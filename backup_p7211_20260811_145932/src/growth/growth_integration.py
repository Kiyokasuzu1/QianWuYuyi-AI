"""
Phase B.1 — GrowthIntegrationService

职责：
整合 Event → GrowthProposal → PersonalityAdapter → PersonalityGrowthHistory → SelfModel
的完整链路。是整个 Growth Pipeline 的运行时统一入口。

调用链：
    Event
       ↓
    GrowthIntegrationService.process_event()
       ↓
    [event]
    [growth_evaluation]
    [proposal_created]
    [proposal_stored]
       ↓
    ProposalManager (等待 review)
       ↓
    [proposal_accepted]
       ↓
    PersonalityAdapter → PersonalityGrowthHistory.add()
       ↓
    [proposal_applied]
       ↓
    PersonalityAdapter.apply_proposal() (TraitState)
       ↓
    [self_model_updated]
       ↓
    SelfModelStore.update() 写入 growth_history.records

设计原则（Phase B.1.5 安全规则）：
1. 低置信度（< 0.8）不能进入人格成长（被 ProposalManager 拦截）
2. 单次事件不能改变核心人格（delta 受 MAX_SINGLE_EVENT_DELTA 限制）
3. 必须有 evidence（无 evidence 不创建 proposal）
4. 重复成长事件自动合并（fingerprint 去重）
5. 冲突成长自动标记 needs_review

关键约束：
- 不破坏现有 Memory / Growth System
- 不直接修改 Personality（所有修改都走 PersonalityAdapter）
- 所有异常被 try/except 隔离，不影响 Runtime
- 完整生命周期日志（log + 可选 audit）
- 单测可独立运行
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from src.contracts import growth_schema, audit_schema
from src.growth.proposal_manager import ProposalManager
from src.growth.proposal_store import ProposalStore
from src.personality.personality_adapter import PersonalityAdapter
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.growth_history_bridge import GrowthHistoryBridge

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


# Phase B.1.5 #1: 置信度门槛
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

# Phase B.1.5 #2: 单次事件最大 delta（与 growth_evaluator.MAX_SINGLE_EVENT_DELTA 保持一致）
DEFAULT_MAX_SINGLE_EVENT_DELTA = 0.05


class GrowthIntegrationService:
    """
    完整 Growth Pipeline 集成服务。

    输入：source_event（dict） + evaluator_output（dict）
    输出：proposal + apply result + growth_history view
    """

    def __init__(
        self,
        proposal_manager: Optional[ProposalManager] = None,
        personality_adapter: Optional[PersonalityAdapter] = None,
        growth_history: Optional[PersonalityGrowthHistory] = None,
        growth_history_bridge: Optional[GrowthHistoryBridge] = None,
        self_model_store: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.growth_history = growth_history or PersonalityGrowthHistory()
        self.growth_history_bridge = growth_history_bridge or GrowthHistoryBridge(
            growth_history=self.growth_history,
        )
        self.personality_adapter = personality_adapter or PersonalityAdapter()
        self.proposal_manager = proposal_manager or ProposalManager(
            personality_adapter=self.personality_adapter,
            growth_history=self.growth_history,
            config={
                "auto_accept_enabled": self.config.get("auto_accept_enabled", False),
                "confidence_threshold": self.config.get(
                    "confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD,
                ),
            },
        )
        self.self_model_store = self_model_store

        # Phase B.1.5: 安全规则配置
        self.confidence_threshold: float = float(
            self.config.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD),
        )
        self.max_single_event_delta: float = float(
            self.config.get("max_single_event_delta", DEFAULT_MAX_SINGLE_EVENT_DELTA),
        )
        self.audit = self.config.get("audit")

    # ============================================================
    # 主入口: process_event
    # ============================================================
    def process_event(
        self,
        source_event: Dict[str, Any],
        evaluator_output: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        处理一个 source_event,经过完整 Growth Pipeline 流程。

        Args:
            source_event: 触发事件 {"id": ..., "type": ..., ...}
            evaluator_output: GrowthEvaluator 输出
                {
                    "proposed_changes": [ChangeItem, ...],
                    "confidence": float,
                    "evidence_ids": [str, ...],
                    "growth_level": str,
                    "reason": str,
                    "narrative": str,
                    "action_scope": str (personality/memory/...),
                }

        Returns:
            {
                "pipeline_state": "created" / "deduped" / "rejected_low_confidence"
                                  / "rejected_no_evidence" / "auto_accepted"
                                  / "needs_review" / "error",
                "proposal_id": str | None,
                "growth_record_id": str | None,
                "applied": bool,
                "growth_history_view": Dict | None,
                "reasons": [str, ...],
            }
        """
        result: Dict[str, Any] = {
            "pipeline_state": "",
            "proposal_id": None,
            "growth_record_id": None,
            "applied": False,
            "growth_history_view": None,
            "reasons": [],
        }

        try:
            # ============================================================
            # [event] — 接收到 source_event
            # ============================================================
            logger.info(
                "[event] id=%s type=%s",
                source_event.get("id", ""),
                source_event.get("type", ""),
            )

            # ============================================================
            # [growth_evaluation] — 评估阶段
            # ============================================================
            confidence = float(evaluator_output.get("confidence", 0.0) or 0.0)
            evidence_ids = list(evaluator_output.get("evidence_ids") or [])
            proposed_changes = list(evaluator_output.get("proposed_changes") or [])
            evaluator_meta = {
                k: v for k, v in (evaluator_output or {}).items()
                if k not in ("proposed_changes", "confidence", "evidence_ids")
            }

            logger.info(
                "[growth_evaluation] confidence=%.3f evidence=%d changes=%d level=%s",
                confidence,
                len(evidence_ids),
                len(proposed_changes),
                evaluator_meta.get("growth_level", ""),
            )

            # Phase B.1.5 #2: 单次事件最大 delta 限制
            # 浮点精度容差：避免 0.55 - 0.5 = 0.050000000000000044 误判
            _DELTA_EPSILON = 1e-9
            oversized = []
            for ci in proposed_changes:
                try:
                    before = float(ci.before) if ci.before is not None else 0.0
                    after = float(ci.after) if ci.after is not None else 0.0
                    if abs(after - before) > self.max_single_event_delta + _DELTA_EPSILON:
                        oversized.append(ci.path)
                except Exception:
                    continue
            if oversized:
                logger.warning(
                    "[growth_delta_too_large] paths=%s max=%.4f",
                    ", ".join(oversized),
                    self.max_single_event_delta,
                )
                result["pipeline_state"] = "needs_review"
                result["reasons"].append(
                    f"delta_oversized: {oversized}",
                )
                # 创建 proposal 但标记 needs_review
                create_result = self.proposal_manager.create_proposal(
                    source_event=source_event,
                    proposed_changes=proposed_changes,
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    evaluator_meta=evaluator_meta,
                )
                if create_result.get("proposal") is not None:
                    self.proposal_manager.mark_needs_review(
                        proposal_id=create_result["proposal"].id,
                        reason=f"delta_oversized: {oversized}",
                    )
                    result["proposal_id"] = create_result["proposal"].id
                return result

            # ============================================================
            # [proposal_created] + [proposal_stored]
            # 通过 ProposalManager 创建并持久化
            # ============================================================
            create_result = self.proposal_manager.create_proposal(
                source_event=source_event,
                proposed_changes=proposed_changes,
                confidence=confidence,
                evidence_ids=evidence_ids,
                evaluator_meta=evaluator_meta,
            )

            pipeline_state = create_result.get("status", "error")
            result["pipeline_state"] = pipeline_state
            result["reasons"].append(create_result.get("reason", ""))

            if pipeline_state == "deduped":
                # 重复：不创建新 proposal,返回 existing_id
                result["proposal_id"] = create_result.get("existing_id")
                logger.info(
                    "[pipeline_deduped] existing_id=%s",
                    result["proposal_id"],
                )
                return result

            if pipeline_state in ("rejected_low_confidence", "rejected_no_evidence", "store_failed"):
                logger.info(
                    "[pipeline_rejected] state=%s reason=%s",
                    pipeline_state,
                    result["reasons"][-1],
                )
                return result

            proposal = create_result.get("proposal")
            if proposal is None:
                result["pipeline_state"] = "error"
                return result

            result["proposal_id"] = proposal.id

            # ============================================================
            # 如果 auto_accept 触发了 accept, get growth_record_id
            # ============================================================
            if pipeline_state == "accepted":
                # auto_accept path
                gr = create_result.get("growth_record")
                if gr:
                    result["growth_record_id"] = gr.get("record_id", "")
                    result["applied"] = False  # accepted 但未 applied
                    result["growth_history_view"] = self.growth_history_bridge.build_growth_history_view()
                    return result

            # ============================================================
            # 默认: proposal 处于 pending, 不自动 accept
            # 如需继续 apply, 调用方显式 accept_proposal + apply_proposal
            # ============================================================
            return result

        except Exception as e:
            logger.exception("[pipeline_error] %s", e)
            result["pipeline_state"] = "error"
            result["reasons"].append(f"exception: {e}")
            # 隔离：不影响 Runtime
            return result

    # ============================================================
    # review_proposal: 显式 accept/reject
    # ============================================================
    def accept_proposal(self, proposal_id: str, actor: str = "user") -> Dict[str, Any]:
        """显式 accept 一个 proposal, 并写入 PersonalityGrowthHistory。"""
        try:
            r = self.proposal_manager.accept_proposal(
                proposal_id=proposal_id,
                actor=actor,
            )
            if r.get("status") == "accepted":
                # ============================================================
                # [self_model_updated] — 同步 SelfModel.growth_history
                # ============================================================
                self._refresh_self_model()
            return r
        except Exception as e:
            logger.exception("[accept_failed] %s", e)
            return {"status": "error", "reason": str(e)}

    def reject_proposal(
        self,
        proposal_id: str,
        actor: str = "user",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return self.proposal_manager.reject_proposal(
                proposal_id=proposal_id,
                actor=actor,
                reason=reason,
            )
        except Exception as e:
            logger.exception("[reject_failed] %s", e)
            return {"status": "error", "reason": str(e)}

    def apply_proposal(self, proposal_id: str, actor: str = "user") -> Dict[str, Any]:
        """apply = accept + apply_proposal（一次性完成两步）"""
        try:
            # 先 accept（如果未 accepted）
            current = self.proposal_manager.get_proposal(proposal_id)
            if current is None:
                return {"status": "not_found"}
            if current.status == "pending":
                acc = self.proposal_manager.accept_proposal(proposal_id, actor=actor)
                if acc.get("status") not in ("accepted", "already_accepted"):
                    return acc
            # 再 apply
            r = self.proposal_manager.apply_proposal(proposal_id, actor=actor)
            if r.get("status") == "applied":
                self._refresh_self_model()
            return r
        except Exception as e:
            logger.exception("[apply_failed] %s", e)
            return {"status": "error", "reason": str(e)}

    # ============================================================
    # 查询
    # ============================================================
    def get_growth_history_view(self) -> Dict[str, Any]:
        return self.growth_history_bridge.build_growth_history_view()

    def recent_growth_events(self, n: int = 5):
        return self.growth_history_bridge.recent_growth_events(n)

    def growth_count(self) -> int:
        return self.growth_history_bridge.growth_count()

    def important_changes(self, min_confidence: float = 0.8):
        return self.growth_history_bridge.important_changes(min_confidence)

    def list_proposals(self, status: Optional[str] = None, limit: int = 50):
        return self.proposal_manager.list_proposals(status=status, limit=limit)

    def get_proposal(self, proposal_id: str):
        return self.proposal_manager.get_proposal(proposal_id)

    # ============================================================
    # _refresh_self_model
    # ============================================================
    def _refresh_self_model(self) -> None:
        """[self_model_updated] 触发 SelfModel 重新生成并写入 growth_history。"""
        if self.self_model_store is None:
            return
        try:
            # 构建 growth_history view
            view = self.growth_history_bridge.build_growth_history_view()
            # 写入 SelfModelStore（如有 set_growth_history_view 方法则调用）
            if hasattr(self.self_model_store, "set_growth_history_view"):
                self.self_model_store.set_growth_history_view(view)
            elif hasattr(self.self_model_store, "update_growth_history"):
                self.self_model_store.update_growth_history(view)
            # 触发完整 update（如提供了 history + trait_states）
            if hasattr(self.self_model_store, "should_update") and hasattr(self.self_model_store, "update"):
                if self.self_model_store.should_update(self.growth_history):
                    trait_states = self.config.get("trait_states", {})
                    self.self_model_store.update(self.growth_history, trait_states)
        except Exception as e:
            logger.warning("[self_model_updated_failed] %s", e)
# ============================================================
    # Phase 4.0-R2.5.1: accept_experience
    # ============================================================
    def accept_experience(
        self,
        record: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Bridge 调用的 Growth 入口：接收一条 memory record，自己走完 Growth Pipeline 并产出 pending Proposal。

        Phase 4.0-R2.5.1 设计原因：
            ExperienceBridge 只做三通道分流（Event Router），不执行业务管道。
            所以 normalize→validate→match→evaluate→proposed_changes→process_event
            这一整套 Growth 内部流程由 GrowthIntegrationService 自己封装完成。

        Bridge（调用方）的职责："这条经历应该进入 Growth。"
        本方法（Growth 职责）："好的，我自己处理怎么成长。"

        Args:
            record: MemoryStore.get_by_id() 返回的完整记录 dict。
                要求字段：id, content, user_id, role, timestamp, importance(float), metadata(dict)
                其中 content 必须非空；role 应为 'user'（Bridge 在分流时应该已校验，此方法再做一次防御校验）。

        Returns:
            Dict，与 process_event() 返回结构完全一致：
            {
              "pipeline_state": str,   # created / deduped / rejected_low_confidence / ...
              "proposal_id": str|None, # 仅当状态是 created / deduped / accepted 时非空
              "proposal": GrowthProposal|dict|None,  # 原始 proposal 对象（Bridge 审计用）
              "growth_record_id": str|None,
              "applied": bool,        # R2.5.1 永远为 False（auto_accept=False；永远不 apply personality）
              "growth_history_view": dict|None,
              "reasons": List[str],
            }

        红线（R2.5.1 坚决遵守）：
            - 永远不能让 PersonalityState 发生变化
            - 永远不能让 GrowthState.apply() 被调用
            - 永远不让 RelationshipState 被修改
        实现方式：ProposalManager 通过 self.config auto_accept_enabled=False 传递；
                PersonalityAdapter 虽然被构造，不影响数据，因为 proposal 停在 pending。
        """
        try:
            # 防御性校验：record 必须足够完整
            content = str(record.get("content") or "").strip()
            memory_id = str(record.get("id") or "")
            user_id = str(record.get("user_id") or "")
            role = str(record.get("role") or "")
            if not content or not memory_id:
                return {
                    "pipeline_state": "rejected_no_evidence",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": [
                        "accept_experience: record 不满足最小契约"
                        f" (content={len(content)}chars, memory_id={memory_id!r})"
                    ],
                }
            if role != "user":
                # Bridge 应在此之前过滤 assistant 角色。这里再防御一层。
                return {
                    "pipeline_state": "rejected_no_evidence",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": [f"accept_experience: role={role!r} 不是 user，跳过"],
                }

            # Step 1: 构造 Extractor 等价事件骨架
            importance_val = float(record.get("importance") or 0.5)
            raw_event: Dict[str, Any] = {
                "event": f"用户经历: {content[:20]}...",
                "topic": content[:30],
                "event_type": "conversation",  # 默认值，等 Normalizer 重写
                "importance": importance_val,
                "evidence": [
                    {
                        "text": content,
                        "role": role,
                        "source_index": 0,
                        "memory_id": memory_id,
                    }
                ],
                "source_ids": [memory_id],
            }

            # Step 2: Normalize（顺带产出 category_id / meaning / canonical_topic）
            from src.growth.event_normalizer import EventNormalizer

            _normalizer = EventNormalizer()
            normalized_list = _normalizer.normalize([raw_event])
            if not normalized_list:
                return {
                    "pipeline_state": "rejected_no_evidence",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": ["accept_experience: Normalizer 返回空（丢弃）"],
                }
            normalized = normalized_list[0]

            # Step 3: Validate
            from src.growth.event_validator import EventValidator

            _validator = EventValidator()
            if not _validator.should_keep(normalized):
                _decision = _validator.decide(normalized) or ("discard", 0.0, "validator")
                return {
                    "pipeline_state": "rejected_no_evidence",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": [f"accept_experience: Validator discard: {_decision}"],
                }

            # Step 4: Match 历史
            from src.growth.event_history_matcher import EventHistoryMatcher

            _matcher = EventHistoryMatcher()
            canonical_topic = normalized.get("canonical_topic") or normalized.get("topic", "")
            ev_type = normalized.get("event_type", "")
            history = _matcher.get_history(canonical_topic, ev_type)

            # Step 4.5: ✨ GrowthEligibilityFilter — Phase 4.0 R2.5.2-A
            #   "这条经历现在值得进入 Evaluator 吗？"
            #   - 仅判断 频率 / 稳定性 / 证据量 / 观察窗口 四个时机维度
            #   - 不做语义方向判断（Evaluator 才做）
            #   - 不直接写 Personality / GrowthState / RelationshipState
            from src.growth.growth_eligibility_filter import (
                GrowthEligibilityFilter,
                emit_eligibility_audit,
            )

            _eligibility = GrowthEligibilityFilter()
            eligibility_decision = _eligibility.evaluate(record, normalized, history)
            # 审计（完全失败隔离）
            emit_eligibility_audit(eligibility_decision, user_id=user_id)

            if not eligibility_decision.get("eligible"):
                return {
                    "pipeline_state": "rejected_growth_eligibility",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": [
                        "accept_experience: GrowthEligibilityFilter.blocked: "
                        f"rule={eligibility_decision.get('rule')} "
                        f"freq={eligibility_decision.get('frequency_score')} "
                        f"stab={eligibility_decision.get('stability_score')} "
                        f"ev={eligibility_decision.get('evidence_count')} "
                        f"state={eligibility_decision.get('observation_state')}"
                    ],
                    "eligibility_decision": eligibility_decision,
                }

            # Step 5: Evaluate
            from src.growth.growth_evaluator import GrowthEvaluator

            _evaluator = GrowthEvaluator()
            evaluator_output = _evaluator.evaluate(normalized, history) or {}

            # Step 5.5: ✨ TransitionAnalyzer — Phase 4.0 R2.5.2-C
            #   只判断「这属于什么兴趣转变」，绝不直接改 Personality / 产出 ChangeItem
            #   - reinforce / new_interest_emerging → 挂在 evaluator_meta.transition_analysis
            #     继续走正常 Evaluator → ChangeItem 链路
            #   - gradual_transition（矛盾）: proposal 创好后再 mark_needs_review，
            #     交给 R2.5.3 Approval 阶段决定要不要 apply（避免直接覆盖）
            #   - 任何异常隔离：失败时不阻塞正常链路（transition_analysis=None）
            transition_analysis: Dict[str, Any] | None = None
            try:
                from src.growth.transition_analyzer import TransitionAnalyzer

                _transition_analyzer = TransitionAnalyzer()
                _tr_proposal = _transition_analyzer.analyze(
                    normalized_event=normalized,
                    history_events=history,
                    evaluator_output=evaluator_output,
                )
                if _tr_proposal is not None:
                    transition_analysis = dict(_tr_proposal)
            except Exception as exc:  # noqa: BLE001
                try:
                    logger.warning(
                        "[GrowthIntegrationService.accept_experience] transition analyzer 失败(已隔离): %s",
                        exc,
                    )
                except Exception:  # noqa: BLE001
                    pass
                transition_analysis = None

            # 如果 Evaluator 明确不允许成长，则返回 rejected_low_confidence
            if evaluator_output.get("growth_allowed") is False:
                return {
                    "pipeline_state": "rejected_low_confidence",
                    "proposal_id": None,
                    "proposal": None,
                    "growth_record_id": None,
                    "applied": False,
                    "growth_history_view": None,
                    "reasons": [
                        "accept_experience: Evaluator growth_allowed=False; "
                        f"level={evaluator_output.get('growth_level')} "
                        f"confidence={evaluator_output.get('confidence')}"
                    ],
                }

            # Step 6: 把 evaluator_output 转成 process_event() 所需结构
            from src.contracts.growth_schema import ChangeItem

            target_candidates: List[str] = list(evaluator_output.get("target_candidates") or [])
            applied_delta = float(evaluator_output.get("applied_delta") or 0.0)
            proposed_changes: List[Any] = []
            if target_candidates and applied_delta > 0:
                for candidate in target_candidates:
                    path = f"personality.{candidate}"
                    reason = (
                        f"growth_bridge: growth_level={evaluator_output.get('growth_level')} "
                        f"signal={evaluator_output.get('growth_signal')} "
                        f"confidence={evaluator_output.get('confidence')}"
                    )
                    try:
                        ci = ChangeItem(path=path, before=0.0, after=applied_delta, reason=reason)
                    except TypeError:
                        ci = {
                            "path": path,
                            "before": 0.0,
                            "after": applied_delta,
                            "reason": reason,
                        }
                    proposed_changes.append(ci)
            evidence_ids: List[str] = list(dict.fromkeys(
                [str(x.get("memory_id") or "")
                 for x in (normalized.get("evidence") or [])
                 if x.get("memory_id")]
            )) or [memory_id]

            evaluator_output_for_process: Dict[str, Any] = dict(evaluator_output)
            evaluator_output_for_process["proposed_changes"] = proposed_changes
            evaluator_output_for_process["evidence_ids"] = evidence_ids
            # Step 5.5 transition 结果挂在 evaluator_meta（process_event 会把除 proposed_changes/
            # confidence/evidence_ids 之外的所有字段全部拷贝进 evaluator_meta）
            if transition_analysis:
                evaluator_output_for_process["transition_analysis"] = transition_analysis
                evaluator_output_for_process["transition_proposal_mode"] = transition_analysis.get("proposal_mode")
            # Step 7: 构造 source_event 并调用 process_event()
            source_event: Dict[str, Any] = {
                "event_id": f"evt_{memory_id}",
                "event_type": str(normalized.get("event_type") or "memory"),
                "topic": str(normalized.get("canonical_topic")
                             or normalized.get("topic") or ""),
                "user_id": user_id,
                "timestamp": str(record.get("timestamp") or ""),
                "raw_content": content,
                "memory_id": memory_id,
                "source": "experience_bridge",
            }

            process_result = self.process_event(source_event, evaluator_output_for_process)
            # 如果本次是 gradual_transition 且真的创建了 proposal，手动 mark_needs_review，
            # 把「矛盾兴趣迁移，必须 Approval 之后才能 apply」的语义注入进去。
            if (
                transition_analysis
                and transition_analysis.get("proposal_mode") == "gradual_transition"
            ):
                pid = process_result.get("proposal_id")
                if pid:
                    try:
                        self.proposal_manager.mark_needs_review(
                            proposal_id=pid,
                            reason=(
                                "interest_transition_gradual: "
                                + transition_analysis.get("reason_text", "")
                            ),
                        )
                        process_result["reasons"].append(
                            "transition_gradual_marked_needs_review"
                        )
                    except Exception as exc:  # noqa: BLE001
                        try:
                            logger.warning(
                                "[accept_experience] mark_needs_review(transition_gradual) 失败(已隔离): %s",
                                exc,
                            )
                        except Exception:  # noqa: BLE001
                            pass
            # TC-11 Gate: 任何 transition_analysis（reinforce / emerging / gradual）一旦成功产出，
            # 都在 reasons 里留下 audit trail，便于测试和日志追踪。
            if transition_analysis:
                try:
                    process_result["reasons"].append("transition_analysis_attached")
                except Exception:  # noqa: BLE001
                    pass
            # ============================================================
            # Step 8: Approval（Phase 4.0 R2.5.3: Evolution Governance Layer）
            # 红线：
            #   - 只产生 ApprovalDecision + 修改 proposal.status ∈ {approved/rejected/deferred/under_review}
            #   - 绝对不调 accept_proposal()/apply_proposal()/PersonalityAdapter
            #   - applied 永远保持 False（Apply 留给 R2.5.4 Personality Evolution Pipeline）
            # ============================================================
            pid = process_result.get("proposal_id")
            if pid:
                try:
                    from src.approval.approval_manager import ApprovalManager, ApprovalPolicy
                    _approval_raw_cfg = (self.config or {}).get("approval") if isinstance(self.config, dict) else None
                    _approval_raw_cfg = _approval_raw_cfg if isinstance(_approval_raw_cfg, dict) else {}
                    _policy_cfg: Dict[str, Any] = {**_approval_raw_cfg}
                    _approval = ApprovalManager(
                        policy=ApprovalPolicy(config=_policy_cfg),
                        proposal_manager=self.proposal_manager,
                    )
                    _approval_res = _approval.govern_proposal(pid)
                    if _approval_res.get("governed"):
                        process_result["reasons"].append("approval_decision_applied")
                        d = _approval_res.get("decision")
                        if d == "approved":
                            process_result["reasons"].append("approval_result_approved")
                        elif d == "rejected":
                            process_result["reasons"].append("approval_result_rejected")
                        elif d == "deferred":
                            process_result["reasons"].append("approval_result_deferred")
                        if _approval_res.get("proposal_status") == "under_review":
                            process_result["reasons"].append("approval_result_under_review")
                        # 审计元数据：把 decision_id 塞给外层返回
                        if _approval_res.get("decision_id"):
                            process_result["approval_decision_id"] = _approval_res["decision_id"]
                            process_result["approval_decision"] = d
                            process_result["approval_reasons"] = list(_approval_res.get("reasons") or [])
                    elif _approval_res.get("isolated"):
                        process_result["reasons"].append("approval_govern_crash_isolated")
                    else:
                        # proposal_manager/proposal 缺失；状态迁移不匹配（from_states 限制）等 → 不阻塞
                        try:
                            for r in (_approval_res.get("reasons") or []):
                                process_result["reasons"].append(f"approval_skipped: {r}")
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as exc:  # noqa: BLE001
                    try:
                        logger.warning(
                            "[accept_experience] Step8 Approval 异常(已隔离): proposal=%s error=%s",
                            pid, exc,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    process_result["reasons"].append("approval_step_outer_crash_isolated")

            # ============================================================
            # Step 9: Evolution Pipeline（Phase 4.0 R2.5.4: 人格演化执行）
            # 红线：
            #   - 只有 Approval decision=approved 时才执行
            #   - applied=True 仅在 EvolutionPipeline 成功执行后
            #   - 异常 100% 隔离，不阻塞 accept_experience
            # ============================================================
            _approval_decision_val = process_result.get("approval_decision")
            _approval_decision_id = process_result.get("approval_decision_id")
            if (
                _approval_decision_val == "approved"
                and _approval_decision_id
                and pid
            ):
                try:
                    from src.personality.evolution_pipeline import EvolutionPipeline
                    _evo = EvolutionPipeline()
                    # 从 proposal_manager 获取最新的 proposal 对象（status 已被 Approval 改为 approved）
                    _proposal_obj = self.proposal_manager.get_proposal(pid)
                    if _proposal_obj is not None:
                        _approval_decision_dict = {
                            "id": _approval_decision_id,
                            "decision": "approved",
                            "reasons": list(process_result.get("approval_reasons") or []),
                        }
                        _evo_res = _evo.execute(_proposal_obj, _approval_decision_dict)
                        if _evo_res.get("executed"):
                            process_result["applied"] = True
                            process_result["reasons"].append("evolution_pipeline_executed")
                            process_result["evolution_record_id"] = _evo_res.get("evolution_record_id")
                            process_result["evolution_applied_traits"] = _evo_res.get("applied_traits", {})
                            if _evo_res.get("blocked_identity"):
                                process_result["reasons"].append("evolution_identity_blocked")
                                process_result["evolution_blocked_identity"] = _evo_res["blocked_identity"]
                            if not _evo_res.get("identity_continuity_ok"):
                                process_result["reasons"].append("evolution_identity_continuity_violation")
                        else:
                            process_result["reasons"].append("evolution_pipeline_not_executed")
                            if _evo_res.get("error"):
                                process_result["reasons"].append(f"evolution_error: {_evo_res['error']}")
                    else:
                        process_result["reasons"].append("evolution_skipped_proposal_not_found")
                except Exception as exc:  # noqa: BLE001
                    try:
                        logger.warning(
                            "[accept_experience] Step9 Evolution 异常(已隔离): proposal=%s error=%s",
                            pid, exc,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    process_result["reasons"].append("evolution_step_crash_isolated")

            # R2.5.1 语义：applied 默认 False；只有 Step 9 EvolutionPipeline 成功才 True
            # （上面 Step 9 已经在成功时设 applied=True，这里不需要再强制 False）
            return process_result

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "[accept_experience_failed] record=%s exc=%s",
                record.get("id"),
                exc,
            )
            return {
                "pipeline_state": "error",
                "proposal_id": None,
                "proposal": None,
                "growth_record_id": None,
                "applied": False,
                "growth_history_view": None,
                "reasons": [f"accept_experience exception: {exc!r}"],
            }

    # ============================================================
    # Phase 4.0-R2.5.1: accept_experience_static（Bridge 懒调用工厂）
    # ============================================================
    @classmethod
    def accept_experience_static(
        cls,
        record: Dict[str, Any],
        *,
        auto_accept_enabled: bool = False,
        confidence_threshold: float = 0.8,
    ) -> Dict[str, Any]:
        """Bridge 懒调用入口：用默认配置实例化后调用 accept_experience。

        关键安全配置（R2.5.1 永远不能打开 auto_accept）：
            auto_accept_enabled = False（默认）
            confidence_threshold = 0.8（默认）
        Bridge 调用时必须显式传 auto_accept_enabled=False（双重保险）。
        """
        service = cls(
            config={
                "auto_accept_enabled": bool(auto_accept_enabled),
                "confidence_threshold": float(confidence_threshold),
            }
        )
        return service.accept_experience(record)
