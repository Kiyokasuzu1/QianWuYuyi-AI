"""
成长流水线（GrowthPipeline）v0.9

浅雾羽依成长系统 v0.9

Phase 3.8.3-C 更新：
- 移除 _inject_compat_fields()：语义生成职责完全移交 GrowthNarrativeAdapter
- Pipeline 不再负责 meaning/narrative/schema 转换

Phase 3.8.3-B 更新：
- incremental_update() 中新增 Adapter 流程：
  GrowthRecord → GrowthNarrativeAdapter → PersonalityGrowthRecord → PersonalityGrowthHistory

Phase 3.8.2-B 更新：
- incremental_update() 中新增 Proposal 化流程：
  GrowthEvaluator → GrowthProposal → GrowthEngine.apply_proposal()
- apply_proposal() 是纯执行方法，不承担认知职责
- 旧 apply(event) 保留作为 fallback
- GROWTH_MAP 保留作为 ExperienceMeaning 失败时的回退

Phase 3.8.2-A 更新：
- 在 GrowthEvaluator 之前注入 deep_resolve_meaning() 的语义理解结果
- 经历意义随 GrowthEvent 流转到下游
- LLM 调用失败时自动回退到规则映射（无破坏性变更）

Phase 7.1 更新：
- 产生 PersonalityInfluence 影响记录
- 提供 collect_new_influences 供 Orchestrator 同步
- incremental_update 开头清理临时缓存
"""

from typing import Optional, Dict, List, Any
import uuid
import logging
from datetime import datetime

from src.growth.event_extractor import EventExtractor
from src.growth.event_normalizer import EventNormalizer
from src.growth.event_validator import EventValidator
from src.growth.event_history_matcher import EventHistoryMatcher
from src.growth.growth_engine import GrowthEngine
from src.growth.growth_state import GrowthState
from src.growth.event_identity_resolver import resolve_event_identity
from src.growth.growth_evaluator import GrowthEvaluator

# Phase 3.8.2-A：经历意义理解
from src.growth.meaning_resolver import deep_resolve_meaning

# Phase 3.8.2-B：GrowthProposal 权威 schema
from src.contracts.growth_schema import GrowthProposal as ContractProposal, ChangeItem

# P2.3-B.5：Growth Mutation Gateway 迁移层（默认关闭 → 旧路径字节不变）
from src.growth.mutation_adapter import (
    GrowthMutationAdapter,
    attach_governance_linkage,
    is_growth_mutation_gateway_enabled,
)

# P2.6 Phase B-1：治理统一开关（默认关闭 → 旧路径字节不变）
from src.governance.governance_unification import is_governance_unification_enabled
from src.growth.proposal_store import ProposalStore
from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA

# Phase 3.8.3-B：GrowthNarrativeAdapter — GrowthRecord → PersonalityGrowthRecord
from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

from src.personality.personality_resolver import PersonalityResolver
from src.personality.relationship_state import RelationshipState
from src.personality.personality_growth_record import PersonalityGrowthHistory

# Phase 7.1 新增
from src.personality.personality_influence import PersonalityInfluence, InfluenceType

logger = logging.getLogger(__name__)


def _is_growth_governed() -> bool:
    """P2.6 Phase B-1：成长写入治理化开关聚合。

    True 时 incremental_update 的 proposal 路由强制走 MutationGateway，
    legacy 直写（GrowthEngine.apply / apply_proposal）不得作为 fallback 执行。
    任一开关关闭时本函数对旧行为零影响。
    """
    return is_growth_mutation_gateway_enabled() or is_governance_unification_enabled()


class GrowthPipeline:

    def __init__(
        self,
        event_memory=None,
        memory_store=None,
        user_id="366648462",
        relationship_state: Optional[RelationshipState] = None,
        growth_state: Optional[GrowthState] = None,
        growth_history: Optional[PersonalityGrowthHistory] = None,
        # P2.3-B.5: 治理模式下 NEED_REVIEW / DEFER 的提案落盘 store。
        # 默认 None → 旧路径不创建任何 store；仅网关开启且需要落盘时惰性创建。
        proposal_store: Optional[Any] = None,
    ):
        # 事件处理
        self.extractor = EventExtractor()
        self.normalizer = EventNormalizer()
        self.validator = EventValidator()
        self.matcher = EventHistoryMatcher()

        # 成长核心（Phase 4.3.3：支持外部注入 GrowthState）
        self.growth_engine = GrowthEngine(state=growth_state)

        # 成长评估器
        self.evaluator = GrowthEvaluator()

        # 成长记录存储（Phase 2.4 GrowthHistory Writer 收口）：
        # - 外部注入的 growth_history 优先（运行时权威实例，多入口共享）；
        # - 未注入时从 RuntimeContext（RuntimeBridge → PersonalityResolver）取共享实例；
        # - 二者都不可用时才回退旧硬编码默认（仅向后兼容）。
        self.growth_records = (
            growth_history
            if growth_history is not None
            else self._resolve_shared_growth_history()
        )

        # Phase 3.8.3-B：GrowthRecord → PersonalityGrowthRecord 适配器
        self.narrative_adapter = GrowthNarrativeAdapter()

        # Phase 7.1：临时影响记录缓存
        self.new_influences = []

        # 关系系统
        self.relationship_state = (
            relationship_state or RelationshipState()
        )

        # 人格系统
        self.resolver = PersonalityResolver(
            state=self.growth_engine.state,
            relationship_state=self.relationship_state
        )

        # 外部依赖
        self.store = memory_store
        self.event_memory = event_memory
        self.target_user_id = user_id

        # P2.3-B.5: Growth Mutation Gateway 迁移层（默认关闭 → 旧行为字节不变；
        # 开关开启后 incremental_update 的直写改经 MutationRequest → Gateway）
        self._mutation_adapter = GrowthMutationAdapter()
        self._proposal_store = proposal_store

        self.matcher.set_growth_state(self.growth_engine.state)

    # =================================================
    # P2.3-B.5：治理可观测（旧模式恒为空）
    # =================================================
    @property
    def mutation_outcomes(self):
        """治理模式下每次 route 的裁决留痕（旧模式恒为空）。"""
        return self._mutation_adapter.outcomes

    @property
    def deferred_requests(self):
        """DEFER 延迟队列槽位（旧模式恒为空）。"""
        return self._mutation_adapter.deferred

    @staticmethod
    def _resolve_shared_growth_history() -> PersonalityGrowthHistory:
        """Phase 2.4: 从 RuntimeContext 复用权威 PersonalityGrowthHistory。

        RuntimeCore 构造 PersonalityResolver 时注入持久化 history 并保留
        可写引用 `_growth_history_store`。所有 GrowthPipeline 共享该实例，
        避免多实例对 personality_growth_history.json 的丢失更新。

        Bridge 不可用 / 共享实例无落盘路径时，回退旧硬编码默认
        （旧行为保留，仅向后兼容）。
        """
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge

            _resolver = get_runtime_bridge().get_personality_resolver()
            _shared = getattr(_resolver, "_growth_history_store", None)
            if _shared is None:
                _shared = getattr(_resolver, "growth_history", None)
            if (
                _shared is not None
                and getattr(_shared, "_storage_path", None) is not None
            ):
                return _shared
        except Exception:
            pass
        return PersonalityGrowthHistory(
            storage_path=PersonalityGrowthHistory.DEFAULT_STORAGE_PATH
        )

    # =================================================
    # Phase 3.8.2-A：经历意义解析
    # =================================================
    def _resolve_experience_meaning(self, event: Dict) -> Optional[Dict]:
        """为事件解析经历意义。

        调用 deep_resolve_meaning() 进行 LLM 语义理解。
        失败时返回 None，调用方应回退到规则映射。

        Returns:
            ExperienceMeaning.to_dict() 的结果，或 None（失败时）
        """
        try:
            meaning = deep_resolve_meaning(
                event=event,
                current_growth_state=self.growth_engine.state.to_dict()
                if hasattr(self.growth_engine.state, "to_dict")
                else None,
            )
            if meaning.fallback_used:
                # 规则 fallback 不提升语义质量，返回 None 让 evaluator 用旧逻辑
                return None
            return meaning.to_dict()
        except Exception:
            # 静默失败，不影响主链路
            return None

    # =================================================
    # Phase 3.8.2-B：从评估结果构建 GrowthProposal
    # =================================================
    def _snapshot_state_reader(self, path: str) -> Optional[dict]:
        """T1-B：只读唯一状态源（GrowthState metrics / PersonalityState traits）。

        未知路径 → None → capture fail-closed（提案生成失败）。
        """
        gs = getattr(self, "growth_state", None)
        if gs is not None:
            try:
                if path in (gs._state.get("metrics") or {}):
                    return {"value": gs.get_metric(path), "source": "growth_state.json"}
            except Exception:  # noqa: BLE001
                pass
        try:
            from src.personality.personality_state import get_personality_state
            ps = get_personality_state()
            if path in (ps.traits or {}):
                return {"value": ps.traits[path], "source": "personality_state.json"}
        except Exception:  # noqa: BLE001
            pass
        return None

    def _build_experience_trace(self, event: Dict) -> Optional[dict]:
        """T2-1-P0：从真实事件证据固化 ExperienceTrace（事实来源）。

        source_memory_ids 只取真实记忆引用（ev.source_id）；
        无真实来源 → None（fail-closed：无来源不允许生成提案）。
        禁止 LLM 补充 / 禁止 meaning 类字段。
        """
        try:
            from src.growth.experience_trace import (
                append_trace, make_trace, new_experience_id,
            )
            source_memory_ids = [str(ev.get("source_id")) for ev in event.get("evidence", [])
                                 if ev.get("source_id")]
            if not source_memory_ids:
                return None
            summary = str(event.get("event_id") or "")[:100] or "pipeline 经历摘要"
            tid = new_experience_id()
            tr = make_trace(tid, source_memory_ids, "runtime_pipeline",
                            summary, "growth_pipeline", "system_rule")
            if tr is None or not append_trace(tr):
                return None
            return tr
        except Exception:  # noqa: BLE001
            return None

    def _build_proposal_from_evaluated(self, evaluated: Dict, event: Dict) -> Optional[ContractProposal]:
        """从 GrowthEvaluator 的评估结果构建 GrowthProposal（权威 schema）。

        将 target_candidates + applied_delta 转换为 ChangeItem 列表。
        必须有 evidence 才能创建 proposal。
        """
        target_candidates = evaluated.get("target_candidates", [])
        applied_delta = evaluated.get("applied_delta", 0.0)
        confidence = evaluated.get("confidence", 0.5)
        growth_signal = evaluated.get("growth_signal", "")
        experience_meaning = evaluated.get("experience_meaning")

        if not target_candidates or applied_delta <= 0.0:
            return None

        # 构建证据ID列表
        evidence_ids = []
        for ev in event.get("evidence", []):
            eid = ev.get("id") or ev.get("source_id") or str(uuid.uuid4().hex[:8])
            evidence_ids.append(eid)
        if not evidence_ids:
            evidence_ids.append(f"ev_{event.get('event_id', 'unknown')}")

        # 构建 ChangeItem 列表
        changes = []
        primary_dim = target_candidates[0]
        primary_val = round(applied_delta, 4)
        changes.append(ChangeItem(
            path=primary_dim,
            before=None,
            after=primary_val,
            reason=f"growth_signal={growth_signal}, confidence={confidence:.2f}",
        ))

        # 次要维度（如果有）
        if len(target_candidates) > 1:
            secondary_dim = target_candidates[1]
            secondary_val = round(applied_delta * 0.5, 4)
            changes.append(ChangeItem(
                path=secondary_dim,
                before=None,
                after=secondary_val,
                reason=f"secondary dimension from {growth_signal}",
            ))

        # 构建 evaluator_meta（含 meaning 信息）
        # T2-1-P0：注入 pattern 字段（validator 规则 3/4 依赖；无频率数据时诚实用 1）
        evaluator_meta = {
            "growth_signal": growth_signal,
            "growth_level": evaluated.get("growth_level", ""),
            "growth_domain": evaluated.get("growth_domain", ""),
            "pattern_detected": str(evaluated.get("pattern_detected")
                                    or growth_signal or "growth_pipeline"),
            "pattern_frequency": int(evaluated.get("pattern_frequency") or 1),
            "_governance_origin": "GrowthPipeline.incremental_update",
            "_source_schema": "growth_evaluator",
        }
        if experience_meaning:
            evaluator_meta["experience_meaning"] = {
                "surface": experience_meaning.get("surface_meaning", ""),
                "deeper": experience_meaning.get("deeper_significance", ""),
                "confidence": experience_meaning.get("confidence", 0),
            }

        # T1-B：生成阶段冻结 before_snapshot（被动捕获，只读唯一状态源）
        # fail-closed：任一 affected path 不可证明 → 提案生成失败
        from src.growth.before_snapshot import capture_snapshots
        snapshots = capture_snapshots([c.path for c in changes], self._snapshot_state_reader)
        if snapshots is None:
            logging.getLogger(__name__).warning(
                "[GrowthPipeline] before_snapshot 捕获失败（状态不可证明）——提案生成失败")
            return None
        snap_by_path = {s["path"]: s for s in snapshots}
        for c in changes:
            c.before = snap_by_path.get(c.path, {}).get("old_value")

        # T2-1-P0：ExperienceTrace 固化（先于 proposal 绑定；无真实来源 → fail-closed）
        trace = self._build_experience_trace(event)
        if trace is None:
            logging.getLogger(__name__).warning(
                "[GrowthPipeline] ExperienceTrace 固化失败（无真实 memory 来源）——提案生成失败")
            return None
        evidence_trace_ids = [trace["experience_id"]]
        # 兼容：旧字段 evidence_ids 同步（历史 schema 读取不受影响）
        if evidence_ids and evidence_trace_ids:
            evidence_trace_ids = list(dict.fromkeys(evidence_trace_ids + evidence_ids[:0]))

        return ContractProposal(
            source_event_id=event.get("event_id", ""),
            proposed_changes=changes,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evidence_trace_ids=evidence_trace_ids,
            evaluator_meta=evaluator_meta,
            status="proposed",
            before_snapshot=snapshots,
        )

    # =================================================
    # P2.3-B.5：Proposal → MutationRequest → Gateway 治理路径
    # =================================================
    def _build_evidence_from_event(self, event: Dict, proposal: ContractProposal) -> List[Dict]:
        """事件 + proposal 证据 → evidence 列表（供 EvidenceCheck 判定类型）。

        来源类型：事件 evidence 保留原类型；source_ids（用户消息来源）
        记为 user_behavior（SOURCE_RELIABILITY 最高可信来源）；
        proposal.evidence_ids 记为 proposal_evidence。
        """
        evidence: List[Dict] = []
        seen = set()
        for ev in event.get("evidence") or []:
            eid = str(ev.get("id") or ev.get("source_id") or "")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            evidence.append({"ref": eid, "type": ev.get("type") or "user_behavior"})
        for sid in event.get("source_ids") or []:
            if not sid or sid in seen:
                continue
            seen.add(sid)
            evidence.append({"ref": str(sid), "type": "user_behavior"})
        for eid in getattr(proposal, "evidence_ids", None) or []:
            if not eid or eid in seen:
                continue
            seen.add(eid)
            evidence.append({"ref": str(eid), "type": "proposal_evidence"})
        return evidence

    def _park_proposal_for_review(
        self,
        proposal: ContractProposal,
        outcomes: List[Dict],
        verdict: str,
    ) -> bool:
        """NEED_REVIEW / DEFER：保存 proposal（status=pending）等待审核。

        - 治理链接键写入 evaluator_meta._governance（Phase 4 契约：
          所有新产生 proposal 必须带 mutation_id/request_id/trace_id/evidence）
        - 不触发 accept/apply/adapter（仅落盘，无任何执行副作用）
        - store 未注入时惰性创建默认 ProposalStore（仅治理模式下发生，
          旧路径不产生 data/proposals 副作用）
        返回是否成功落盘（失败被隔离，不影响主链路）。
        """
        store = self._proposal_store
        if store is None:
            store = ProposalStore()
            self._proposal_store = store
        proposal.status = "pending"  # ProposalStore VALID_STATUSES 白名单内的待审核态
        first = outcomes[0] if outcomes else {}
        attach_governance_linkage(
            proposal,
            mutation_id=first.get("mutation_id"),
            request_id=first.get("request_id"),
            trace_id=first.get("trace_id"),
            evidence=[
                {
                    "mutation_id": o.get("mutation_id"),
                    "request_id": o.get("request_id"),
                    "trace_id": o.get("trace_id"),
                    "target_path": o.get("target_path"),
                    "decision": o.get("decision"),
                    "reason": o.get("reason"),
                    "audit_reference": o.get("audit_reference"),
                }
                for o in outcomes
            ],
            decision=verdict,
        )
        try:
            store.save(proposal)
            logger.info(
                "[P2.3-B.5] growth proposal parked: id=%s verdict=%s path=%s",
                getattr(proposal, "id", ""),
                verdict,
                str(store.path),
            )
            return True
        except Exception as exc:  # noqa: BLE001 落盘失败隔离
            logger.warning("[P2.3-B.5] park proposal 落盘失败（已隔离）: %s", exc)
            return False

    def _route_proposal_through_gateway(
        self,
        proposal: ContractProposal,
        event: Dict,
    ) -> Dict:
        """治理路径（growth_mutation_gateway_enabled=True 时启用）。

        每个 ChangeItem → 一个 MutationRequest（target_domain="growth"，
        actor_identity="growth_system"）→ Gateway 五道检查：
          - ACCEPT      → 进入现有 Apply Adapter（GrowthEngine.apply_proposal，
                          仅含 ACCEPT 的 ChangeItem）
          - REJECT      → 该变更被丢弃，不改变状态
          - NEED_REVIEW → 整个 proposal 持久化为 pending 等待审核，本轮不 apply
          - DEFER       → 整个 proposal 持久化 + 进入延迟队列，本轮不 apply

        proposed_change 的 before/after/delta 与 GrowthEngine.apply_proposal 的
        加权截断语义一致（weighted = min(after * confidence, 0.01)），保证
        网关评估的就是执行件实际会做的变更。

        返回与 GrowthEngine.apply_proposal 兼容的结果 dict（status 键）。
        所有裁决留痕在 self._mutation_adapter.outcomes。
        """
        changes = list(getattr(proposal, "proposed_changes", None) or [])
        if not changes:
            return {"status": "rejected", "reason": "no_changes"}

        state = self.growth_engine.state
        confidence = float(getattr(proposal, "confidence", 0.5) or 0.5)
        evidence = self._build_evidence_from_event(event, proposal)
        event_id = event.get("event_id", "")

        outcomes = []
        accepted_changes = []
        for ci in changes:
            dim = str(getattr(ci, "path", "") or "")
            raw_after = float(getattr(ci, "after", None) or 0.0)
            target_path = f"growth.metrics.{dim}"
            before_val = float(state.get_metric(dim))
            weighted = min(raw_after * confidence, MAX_SINGLE_EVENT_DELTA)
            after_val = round(before_val + weighted, 6)

            mutation_id = f"mut_{uuid.uuid4().hex[:12]}"
            request = self._mutation_adapter.build_request(
                source_event={
                    "event_id": event_id,
                    "type": event.get("event_type", ""),
                    "canonical_topic": event.get(
                        "canonical_topic", event.get("topic", "")
                    ),
                    "occurrence_count": event.get("occurrence_count", 1),
                    "confidence": confidence,
                },
                target_path=target_path,
                proposed_change={
                    "path": target_path,
                    "before": before_val,
                    "after": after_val,
                    "delta": round(weighted, 6),
                    "confidence": confidence,
                },
                evidence=evidence,
                context_snapshot={
                    "request_id": f"req_{mutation_id[4:]}",
                    "trace_id": f"trace_{mutation_id[4:]}",
                    "source": "growth_pipeline.incremental_update",
                },
                risk_level="low",
                mutation_id=mutation_id,
            )

            def _apply_route(req, decision):  # noqa: ANN001
                # ACCEPT 执行件：单 ChangeItem 经现有 Apply Adapter 执行
                single = ContractProposal(
                    source_event_id=getattr(proposal, "source_event_id", ""),
                    proposed_changes=[ci],
                    confidence=confidence,
                    evidence_ids=list(getattr(proposal, "evidence_ids", None) or []),
                    evaluator_meta=dict(
                        getattr(proposal, "evaluator_meta", None) or {}
                    ),
                    status="proposed",
                )
                result = self.growth_engine.apply_proposal(single)
                return result.get("status") == "applied"

            envelope = self._mutation_adapter.route(request, apply_route=_apply_route)
            if envelope.get("decision") == "ACCEPT":
                accepted_changes.append((ci, envelope, request))
            outcomes.append(envelope)

        verdicts = {o.get("decision") for o in outcomes}

        if "NEED_REVIEW" in verdicts:
            saved = self._park_proposal_for_review(proposal, outcomes, "NEED_REVIEW")
            return {
                "status": "needs_review",
                "proposal_id": getattr(proposal, "id", ""),
                "reason": "至少一个变更需要审核，proposal 已保存等待审核",
                "saved": saved,
                "before": {},
                "delta": {},
            }
        if "DEFER" in verdicts:
            saved = self._park_proposal_for_review(proposal, outcomes, "DEFER")
            return {
                "status": "deferred",
                "proposal_id": getattr(proposal, "id", ""),
                "reason": "至少一个变更被暂缓，proposal 已保存并进入延迟队列",
                "saved": saved,
                "before": {},
                "delta": {},
            }
        if not accepted_changes:
            return {
                "status": "rejected",
                "proposal_id": getattr(proposal, "id", ""),
                "reason": "all_changes_rejected_by_gateway",
                "before": {},
                "delta": {},
            }

        # 全部变更 ACCEPT（或 ACCEPT+REJECT 混合，REJECT 项已丢弃）：
        # 现有 Apply Adapter 已在 apply_route 中逐项执行，这里合并 before/delta
        #（取请求快照值——apply 已发生，事后读 state 会拿到 after）
        before_merged = {}
        delta_merged = {}
        for _ci, envelope, request in accepted_changes:
            if not envelope.get("applied"):
                continue
            dim = str(getattr(_ci, "path", "") or "")
            pc = request.proposed_change
            before_merged[dim] = pc.get("before")
            delta_merged[dim] = pc.get("delta")
        if not delta_merged:
            return {
                "status": "declined",
                "proposal_id": getattr(proposal, "id", ""),
                "reason": "apply_adapter_declined_all_changes",
                "before": {},
                "delta": {},
            }
        return {
            "status": "applied",
            "proposal_id": getattr(proposal, "id", ""),
            "mode": "proposal",
            "before": before_merged,
            "delta": delta_merged,
        }

    # =================================================
    # 增量成长更新（实时聊天入口）
    # =================================================
    def incremental_update(self, user_message: str):
        """单次聊天后的快速成长入口"""

        # Phase 7.1：每次调用清理临时缓存
        self.new_influences.clear()

        try:
            events = self.extractor.extract_from_text(user_message)

            if not events:
                return {
                    "events": [],
                    "personality": self.resolver.resolve(),
                    "growth_records": [],
                }

            events = self.normalizer.normalize(events)
            events = self.validator.validate(events)

            if not events:
                return {
                    "events": [],
                    "personality": self.resolver.resolve(),
                    "growth_records": [],
                }

            for e in events:
                resolve_event_identity(e)

            events = self.matcher.track(events, False)

            applied = []
            new_records = []

            for event in events:
                apply_flag = event.get("metadata", {}).get("validator_apply", True)
                if not apply_flag:
                    continue

                # 成长资格评估
                canonical_topic = event.get("canonical_topic", event.get("topic", ""))
                event_type = event.get("event_type", "")
                history_events = self.matcher.get_history(canonical_topic, event_type)

                evaluated = self.evaluator.evaluate(
                    event, history_events,
                    experience_meaning=self._resolve_experience_meaning(event),
                )

                # P2.3-B.5: 治理裁决标记（仅网关开启且 proposal 生成时为 True，
                # 用于抑制 legacy apply(event) fallback，防止绕过 Gateway 直写）
                gateway_handled = False

                if evaluated.get("growth_allowed", False):
                    # Phase 3.8.2-B：Proposal 化流程
                    proposal_applied = False
                    # 1. 从评估结果构建 GrowthProposal
                    proposal = self._build_proposal_from_evaluated(evaluated, event)
                    if proposal is not None:
                        if _is_growth_governed():
                            # P2.3-B.5 / P2.6 Phase B-1: 治理路径 ——
                            # MutationRequest → Gateway → ACCEPT 才进入现有
                            # Apply Adapter（REJECT 不改状态 / NEED_REVIEW 存提案待审 /
                            # DEFER 进延迟队列）。统一开关开启时即使 B.5 开关关闭
                            # 也强制走网关，legacy 直写不再作为 fallback。
                            gateway_handled = True
                            proposal_result = self._route_proposal_through_gateway(
                                proposal, event
                            )
                        else:
                            # 2. 旧路径：调用纯执行方法 apply_proposal()
                            proposal_result = self.growth_engine.apply_proposal(proposal)
                        if proposal_result.get("status") == "applied":
                            proposal_applied = True
                            applied.append({
                                **event,
                                "growth_mode": "proposal",
                                "proposal_id": proposal_result.get("proposal_id"),
                                "delta": proposal_result.get("delta"),
                            })

                    # 3. 同时生成 GrowthRecord（兼容 PersonalityResolver）
                    # P2.6 Phase B-1（F1 门控）：统一治理模式下仅当 proposal
                    # ACCEPT 且 mutation 已成功（proposal_applied=True）才追加，
                    # 防止人格向量经 resolver 绕过治理漂移；
                    # A 态 / 仅 B.5 网关态保持旧行为（无条件追加）。
                    if not is_governance_unification_enabled() or proposal_applied:
                        record = self.growth_engine.apply_evaluated(evaluated)
                        if record:
                            # 注入 proposal 信息
                            record["proposal_id"] = proposal.id if proposal else None
                            # Phase 3.8.3-B：通过 Adapter 转换为 PersonalityGrowthRecord
                            personality_record = self.narrative_adapter.convert(record)
                            self.growth_records.add(personality_record)
                            new_records.append(record)
                else:
                    proposal_applied = False

                # Phase 3.8.5 Step 5：根据 Proposal 结果选择路径
                # - Proposal 成功：跳过 apply() 的 GrowthState 更新，只做 Relationship + Influence
                # - Proposal 失败/未生成：legacy apply() 作为 fallback
                if proposal_applied:
                    # Proposal 路径：GrowthState 已由 apply_proposal() 更新
                    # 不再调用 apply(event)，避免双重 GrowthState 更新
                    # 关系更新和影响生成均委托给 GrowthEngine 统一入口
                    if event.get("is_first_occurrence", True):
                        self.growth_engine.apply_relationship(event, self.relationship_state)
                    self.new_influences.extend(
                        self.growth_engine.generate_personality_influence(event, proposal_result)
                    )
                else:
                    # P2.3-B.5: 治理模式已裁决且未 ACCEPT → 不进入 legacy apply()
                    #（否则等于绕过 Gateway 直写 GrowthState）
                    if is_growth_mutation_gateway_enabled() and gateway_handled:
                        logger.info(
                            "[P2.3-B.5] growth mutation 未 ACCEPT，跳过 legacy "
                            "apply/关系/影响（裁决见 mutation_outcomes）"
                        )
                        result = {"status": "governed_no_apply"}
                    elif is_governance_unification_enabled():
                        # P2.6 Phase B-1：统一治理模式 fail-closed——
                        # proposal 未生成 / 未 ACCEPT / 未进入 gateway 时，
                        # 一律禁止 legacy apply(event) 直写 growth_state
                        logger.info(
                            "[P2.6-B1] 统一治理模式：growth 直写被抑制 "
                            "(proposal_applied=%s, gateway_handled=%s)",
                            proposal_applied, gateway_handled,
                        )
                        result = {
                            "status": "deferred",
                            "reason": "governance_unification_no_apply",
                            "proposal_id": None,
                        }
                    else:
                        # Fallback 路径：legacy apply(event) 完整执行
                        result = self.growth_engine.apply(event)
                    if result.get("status") == "applied":
                        if result.get("mode") == "first":
                            self.growth_engine.apply_relationship(event, self.relationship_state)
                        applied.append({
                            **event,
                            "growth_mode": result.get("mode")
                        })
                        self.new_influences.extend(
                            self.growth_engine.generate_personality_influence(event, result)
                        )

                if self.store and event.get("source_ids"):
                    try:
                        self.store.mark_processed_batch(event["source_ids"])
                    except AttributeError:
                        pass

            return {
                "events": applied,
                "personality": self.resolver.resolve(),
                "growth_records": new_records,
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"⚠️ 增量成长失败: {e}")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

    # =================================================
    # Phase 7.1：收集本轮新产生的影响记录
    # =================================================
    def collect_new_influences(self):
        """收集本轮新产生的影响记录，供 Orchestrator 同步到 RelationshipProfile"""
        result = self.new_influences[:]
        self.new_influences.clear()
        return result

    # =================================================
    # Phase 7.1：计算影响记录的可信度
    # =================================================
    def _calculate_influence_confidence(self, event, result) -> float:
        """基于证据链计算影响记录的可信度"""
        confidence = 0.5
        if event.get("validation_status") == "confirmed":
            confidence += 0.3
        if len(event.get("source_ids", [])) > 1:
            confidence += 0.1
        if result.get("status") == "applied":
            confidence += 0.1
        deltas = result.get("delta", {}).values()
        if deltas:
            max_delta = max(abs(v) for v in deltas)
            if max_delta > 0.05:
                confidence += 0.05
        return min(confidence, 1.0)

    # =================================================
    # Phase 3.8.5 Step 5：PersonalityInfluence 生成（兼容包装器，委托至 GrowthEngine）
    # =================================================
    def _record_influence(self, event, result):
        """人格影响生成（兼容包装器，委托至 GrowthEngine.generate_personality_influence()）"""
        influences = self.growth_engine.generate_personality_influence(event, result)
        self.new_influences.extend(influences)

    # =================================================
    # 关系更新（内部方法，Phase 3.8.5 已委托至 GrowthEngine）
    # =================================================
    def _update_relationship(self, event):
        """关系更新（兼容包装器，委托至 GrowthEngine.apply_relationship()）"""
        self.growth_engine.apply_relationship(event, self.relationship_state)

    # =================================================
    # 完整整合（批量处理）
    # =================================================
    def run_full_consolidation(self, limit=None, force_first_run=False):
        print("📂 开始成长整理")

        try:
            events = self.extractor.extract(limit)
        except Exception as e:
            print(f"❌ 事件提取失败: {e}")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        if not events:
            print("⚠️ 没有提取到事件")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        print(f"📝 原始事件 {len(events)} 个")
        events = self.normalizer.normalize(events)
        print(f"🧹 标准化完成 {len(events)} 个")

        before = len(events)
        events = self.validator.validate(events)
        print(f"🔍 验证后 {len(events)} 个 (过滤 {before - len(events)} 个)")

        if not events:
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        for e in events:
            resolve_event_identity(e)

        events = self.matcher.track(events, force_first_run)

        applied = 0
        processed = []
        new_records = []

        for event in events:
            try:
                if not event.get("is_first_occurrence", True):
                    continue

                apply_flag = event.get("metadata", {}).get("validator_apply", True)
                if not apply_flag:
                    print(f"⏭️ 跳过 event {event.get('event_id', '')} (validator_apply=False)")
                    continue

                canonical_topic = event.get("canonical_topic", event.get("topic", ""))
                event_type = event.get("event_type", "")
                history_events = self.matcher.get_history(canonical_topic, event_type)

                evaluated = self.evaluator.evaluate(
                    event, history_events,
                    experience_meaning=self._resolve_experience_meaning(event),
                )
                # P2.6 Phase B-1（F1 门控）：统一治理模式下不产生 growth_records
                # 追加（无提案无 ACCEPT），防止 resolver 人格影响漂移；A 态不变
                if evaluated.get("growth_allowed", False) and not is_governance_unification_enabled():
                    record = self.growth_engine.apply_evaluated(evaluated)
                    if record:
                        # Phase 3.8.3-B：通过 Adapter 转换为 PersonalityGrowthRecord
                        personality_record = self.narrative_adapter.convert(record)
                        self.growth_records.add(personality_record)
                        new_records.append(record)

                if is_governance_unification_enabled():
                    # P2.6 Phase B-1：run_full_consolidation 无 gateway 路由，
                    # 统一治理模式下禁止 direct apply（fail-closed，返回 deferred）
                    result = {
                        "status": "deferred",
                        "reason": "governance_unification_no_apply",
                        "proposal_id": None,
                    }
                else:
                    result = self.growth_engine.apply(event)

                if result.get("status") == "applied":
                    applied += 1
                    processed.extend(event.get("source_ids", []))
                    self.growth_engine.apply_relationship(event, self.relationship_state)
                    self.new_influences.extend(
                        self.growth_engine.generate_personality_influence(event, result)
                    )

            except Exception as e:
                print(f"⚠️ 成长事件失败: {event.get('topic')}, {e}")

        print(f"🌱 应用成长事件 {applied} 个")
        personality = self.resolver.resolve()

        if self.store and processed:
            self.store.mark_processed_batch(processed)

        if self.event_memory:
            self.event_memory.refresh()

        return {
            "events": events,
            "personality": personality,
            "growth_records": new_records,
        }

    def get_current_personality(self):
        return self.resolver.resolve()

    @property
    def state(self):
        return self.growth_engine.state