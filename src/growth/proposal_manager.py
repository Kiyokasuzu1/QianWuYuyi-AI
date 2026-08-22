"""
Phase B.1.2 — ProposalManager 完整实现

替换原 skeleton。

职责：
- create_proposal(): 从 source_event + 评估元数据生成 GrowthProposal，持久化，发布事件
- accept_proposal(): 接受 proposal，调用 PersonalityAdapter 生成 GrowthRecord，写入
  PersonalityGrowthHistory，更新 status=accepted
- reject_proposal(): 拒绝 proposal，记录原因，status=rejected
- apply_proposal(): 真正应用到 SelfModel（accepted → applied），需通过 PersonalityAdapter
- list_proposals(): 列出 proposal，支持 status 过滤
- dedupe: 重复 source_event_id + fingerprint 自动跳过（或合并）

关键约束（Phase B.1.2 + B.1.5）：
- 默认 auto_accept_enabled = False（不绕过人工/系统审核）
- 全部 status 走 {pending, accepted, rejected, applied}
- 所有异常被 try/except 隔离，不影响 Runtime
- 不直接修改 Personality 文件，只通过 PersonalityAdapter 走标准路径
- 完整生命周期日志：
    [proposal_created]
    [proposal_stored]
    [proposal_accepted]
    [proposal_rejected]
    [proposal_applied]
    [self_model_updated]
"""
from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime

from src.contracts import growth_schema, audit_schema
from src.growth.proposal_store import ProposalStore, compute_fingerprint
from src.personality.personality_adapter import (
    PersonalityAdapter,
    is_approval_record_enforced,
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# 默认置信度门槛（Phase B.1.5 安全规则）
DEFAULT_CONFIDENCE_THRESHOLD = 0.8

# 默认 auto_accept 关闭（必须显式开启才能跳过审核）
DEFAULT_AUTO_ACCEPT_ENABLED = False


class ProposalManager:
    def __init__(
        self,
        store: Optional[ProposalStore] = None,
        personality_adapter: Optional[PersonalityAdapter] = None,
        config: Optional[Dict[str, Any]] = None,
        growth_history: Optional[Any] = None,
        **kwargs: Any,
    ):
        """
        Args:
            store: ProposalStore 实例（缺省时自建）
            personality_adapter: PersonalityAdapter 实例
            config: 配置（auto_accept_enabled, confidence_threshold, ...）
            growth_history: PersonalityGrowthHistory 实例（可选，accept 时写入）
            **kwargs: 兼容旧 API（runtime_context 等）— 已废弃，运行时忽略
        """
        # 兼容旧调用: runtime_context 等已废弃参数
        if kwargs:
            logger.debug(
                "[proposal_manager_init] ignored legacy kwargs: %s",
                list(kwargs.keys()),
            )
        self.store = store or ProposalStore()
        self.personality_adapter = personality_adapter or PersonalityAdapter()
        self.growth_history = growth_history  # Phase B.1.4
        self.config = config or {}

        # Phase B.1.5: 安全规则
        self.auto_accept_enabled: bool = bool(
            self.config.get("auto_accept_enabled", DEFAULT_AUTO_ACCEPT_ENABLED),
        )
        self.confidence_threshold: float = float(
            self.config.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD),
        )

        # 审计 / 事件 / 快照 hook（可选）
        self.audit = self.config.get("audit")
        self.event_bus = self.config.get("event_bus")
        self.snapshot = self.config.get("snapshot")

    # ============================================================
    # create_proposal
    # ============================================================
    def create_proposal(
        self,
        source_event: Dict[str, Any],
        proposed_changes: List[growth_schema.ChangeItem],
        confidence: float,
        evidence_ids: List[str],
        evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        创建一个 GrowthProposal:
        1. 应用安全规则（置信度门槛、重复合并、冲突标记）
        2. 持久化到 ProposalStore
        3. 发布事件 + 审计
        4. 如 auto_accept_enabled 且 confidence > threshold，自动 accept

        Returns:
            dict:
                - status: created / deduped / rejected_low_confidence / needs_review
                - proposal: GrowthProposal | None
                - existing_id: str | None (仅 deduped 时存在)
                - reason: str (拒绝/合并原因)
        """
        evaluator_meta = evaluator_meta or {}

        # ============================================================
        # [event] — 接收到 source_event
        # ============================================================
        logger.info(
            "[proposal_event] source_event_id=%s confidence=%.3f evidence=%d",
            source_event.get("id", ""),
            confidence,
            len(evidence_ids or []),
        )

        # ============================================================
        # Phase B.1.5 #1: 低置信度拒绝
        # ============================================================
        if confidence < self.confidence_threshold:
            logger.info(
                "[proposal_rejected_low_confidence] confidence=%.3f threshold=%.3f",
                confidence,
                self.confidence_threshold,
            )
            self._record_audit(audit_schema.AuditEntry(
                component="growth",
                actor="proposal_manager",
                reason="low_confidence_rejected",
                before={"confidence": confidence},
                after={"threshold": self.confidence_threshold},
            ))
            return {
                "status": "rejected_low_confidence",
                "proposal": None,
                "existing_id": None,
                "reason": f"confidence {confidence:.3f} < threshold {self.confidence_threshold:.3f}",
            }

        # Phase B.1.5 #3: 必须有 evidence
        if not evidence_ids:
            logger.info("[proposal_rejected_no_evidence]")
            return {
                "status": "rejected_no_evidence",
                "proposal": None,
                "existing_id": None,
                "reason": "evidence_ids is empty",
            }

        # 构建 proposal
        proposal = growth_schema.GrowthProposal(
            source_event_id=source_event.get("id") if isinstance(source_event, dict) else None,
            proposed_changes=proposed_changes,
            confidence=float(confidence),
            evidence_ids=list(evidence_ids or []),
            evaluator_meta=evaluator_meta,
            status="pending",
        )

        # ============================================================
        # v1.1 Phase 1: 内容哈希次级键（不改 schema, 存 evaluator_meta）。
        # 事件链（evt_mem_*）与经历链（evt_exp_*）的 source_event_id 前缀不同,
        # 主指纹无法跨链互认; 内容哈希使同一经历在双链间可互认去重。
        # ============================================================
        _content_key = ""
        if isinstance(source_event, dict):
            _content = str(source_event.get("raw_content") or source_event.get("content") or "")
            if _content.strip():
                import hashlib

                _content_key = hashlib.sha1(
                    f"{source_event.get('user_id', '')}|{_content.strip()}".encode("utf-8")
                ).hexdigest()[:16]
                proposal.evaluator_meta = dict(proposal.evaluator_meta or {})
                proposal.evaluator_meta["content_fingerprint"] = _content_key

        # ============================================================
        # Phase B.1.5 #4: 重复合并（dedupe）
        # ============================================================
        # 防御：非 dataclass 输入不应导致 to_dict 崩溃
        try:
            fp = compute_fingerprint(proposal.to_dict())
            existing_id = self.store.exists_similar(
                source_event_id=proposal.source_event_id or "",
                fingerprint=fp,
            )
        except Exception as e:
            logger.warning("[proposal_dedupe_skipped] reason=%s", e)
            existing_id = None
        if existing_id is None and _content_key:
            # 次级键: 同内容哈希的非终态提案（跨链去重）
            try:
                for _p in self.store.list(limit=500):
                    _meta = getattr(_p, "evaluator_meta", None) or {}
                    if (
                        str(_meta.get("content_fingerprint") or "") == _content_key
                        and getattr(_p, "status", "") in ("pending", "proposed", "accepted")
                    ):
                        existing_id = getattr(_p, "id", "")
                        break
            except Exception as e:
                logger.warning("[proposal_content_dedupe_skipped] reason=%s", e)
        if existing_id is not None:
            logger.info(
                "[proposal_deduped] existing_id=%s new_id=%s",
                existing_id,
                proposal.id,
            )
            return {
                "status": "deduped",
                "proposal": None,
                "existing_id": existing_id,
                "reason": f"duplicate of {existing_id}",
            }

        # ============================================================
        # [proposal_created]
        # ============================================================
        logger.info(
            "[proposal_created] id=%s confidence=%.3f changes=%d",
            proposal.id,
            proposal.confidence,
            len(proposal.proposed_changes),
        )

        # ============================================================
        # [proposal_stored]
        # ============================================================
        try:
            self.store.save(proposal)
            logger.info("[proposal_stored] id=%s path=%s", proposal.id, str(self.store.path))
        except Exception as e:
            logger.error("[proposal_stored_failed] id=%s error=%s", proposal.id, e)
            return {
                "status": "store_failed",
                "proposal": proposal,
                "existing_id": None,
                "reason": f"store error: {e}",
            }

        self._record_audit(audit_schema.AuditEntry(
            component="growth",
            actor="proposal_manager",
            reason="proposal_created",
            before={},
            after={
                "proposal_id": proposal.id,
                "confidence": proposal.confidence,
                "evidence_count": len(proposal.evidence_ids),
            },
        ))

        # ============================================================
        # auto_accept: 仅当显式启用且 confidence 显著高于门槛
        # ============================================================
        if self.auto_accept_enabled and proposal.confidence >= self.confidence_threshold:
            logger.info(
                "[proposal_auto_accept] id=%s (auto_accept_enabled=True)",
                proposal.id,
            )
            return self.accept_proposal(
                proposal_id=proposal.id,
                actor="auto_accept",
                auto=True,
            )

        return {
            "status": "created",
            "proposal": proposal,
            "existing_id": None,
            "reason": "proposal stored, awaiting review",
        }

    # ============================================================
    # accept_proposal
    # ============================================================
    def accept_proposal(
        self,
        proposal_id: str,
        actor: str = "system",
        auto: bool = False,
    ) -> Dict[str, Any]:
        """接受 proposal: 调用 PersonalityAdapter 写入 GrowthRecord + PersonalityGrowthHistory。"""
        proposal = self.store.load(proposal_id)
        if proposal is None:
            return {"status": "not_found", "proposal": None, "reason": f"id={proposal_id} not found"}

        if proposal.status in ("accepted", "applied"):
            return {
                "status": "already_accepted",
                "proposal": proposal,
                "reason": f"id={proposal_id} already {proposal.status}",
            }
        if proposal.status == "rejected":
            return {
                "status": "already_rejected",
                "proposal": proposal,
                "reason": f"id={proposal_id} already rejected",
            }

        # ============================================================
        # Phase B.1.3: 调用 PersonalityAdapter 转换 proposal → change request
        # ============================================================
        try:
            change_request = self.personality_adapter.build_change_request(
                proposal=proposal,
                evaluator_meta=proposal.evaluator_meta,
            )
        except Exception as e:
            logger.error("[proposal_accept_failed] id=%s error=%s", proposal_id, e)
            return {"status": "adapter_failed", "proposal": proposal, "reason": str(e)}

        # ============================================================
        # [proposal_accepted]
        # ============================================================
        # G-1.3.3: 生成真实审批凭证并随提案持久化（禁止伪造批准字符串）
        approval_record = {
            "record_id": f"apr_{uuid.uuid4().hex[:10]}",
            "proposal_id": str(proposal_id),
            "reviewer_id": actor,
            "reviewed_at": now_iso(),
            "decision": "approve",
        }
        proposal.status = "accepted"
        proposal.accepted_at = now_iso()
        proposal.evaluator_meta = dict(proposal.evaluator_meta or {})
        proposal.evaluator_meta["approval_record"] = approval_record
        try:
            self.store.update(proposal)
        except Exception as e:
            logger.warning("[proposal_accepted_store_failed] id=%s error=%s (in-memory still ok)", proposal_id, e)

        logger.info(
            "[proposal_accepted] id=%s actor=%s auto=%s",
            proposal.id,
            actor,
            auto,
        )

        # 写入 PersonalityGrowthHistory
        growth_record = self._convert_to_growth_record(proposal, change_request)
        if self.growth_history is not None and growth_record is not None:
            try:
                self.growth_history.add(growth_record)
            except Exception as e:
                logger.warning(
                    "[growth_history_add_failed] id=%s error=%s",
                    proposal.id,
                    e,
                )

        # 写入审计
        self._record_audit(audit_schema.AuditEntry(
            component="growth",
            actor=actor,
            reason="proposal_accepted",
            before={"status": "pending"},
            after={
                "proposal_id": proposal.id,
                "status": "accepted",
                "growth_record_id": (growth_record or {}).get("record_id", ""),
                "approval_record_id": approval_record["record_id"],
            },
        ))

        return {
            "status": "accepted",
            "proposal": proposal,
            "growth_record": growth_record,
            "reason": "accepted and converted to growth_record",
        }

    # ============================================================
    # reject_proposal
    # ============================================================
    def reject_proposal(
        self,
        proposal_id: str,
        actor: str = "system",
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """拒绝 proposal: 标记 rejected, 记录原因。"""
        proposal = self.store.load(proposal_id)
        if proposal is None:
            return {"status": "not_found", "proposal": None, "reason": f"id={proposal_id} not found"}
        if proposal.status in ("accepted", "applied"):
            return {
                "status": "already_accepted",
                "proposal": proposal,
                "reason": "cannot reject an already-accepted proposal",
            }

        # ============================================================
        # [proposal_rejected]
        # ============================================================
        proposal.status = "rejected"
        proposal.rejected_at = now_iso()
        # 记录拒绝原因到 evaluator_meta
        if reason:
            em = dict(proposal.evaluator_meta or {})
            em["rejection_reason"] = reason
            em["rejected_by"] = actor
            proposal.evaluator_meta = em

        try:
            self.store.update(proposal)
        except Exception as e:
            logger.warning("[proposal_rejected_store_failed] id=%s error=%s", proposal_id, e)

        logger.info(
            "[proposal_rejected] id=%s actor=%s reason=%s",
            proposal.id,
            actor,
            reason or "",
        )

        self._record_audit(audit_schema.AuditEntry(
            component="growth",
            actor=actor,
            reason="proposal_rejected",
            before={"status": "pending"},
            after={"proposal_id": proposal.id, "status": "rejected", "reason": reason or ""},
        ))

        return {
            "status": "rejected",
            "proposal": proposal,
            "reason": reason or "rejected by " + actor,
        }

    # ============================================================
    # apply_proposal
    # ============================================================
    def apply_proposal(
        self,
        proposal_id: str,
        actor: str = "system",
    ) -> Dict[str, Any]:
        """应用 proposal: 必须先 accepted, 然后通过 PersonalityAdapter.apply_proposal
        真正写入 TraitState。"""
        proposal = self.store.load(proposal_id)
        if proposal is None:
            return {"status": "not_found", "proposal": None, "reason": "not found"}
        if proposal.status != "accepted":
            return {
                "status": "not_accepted",
                "proposal": proposal,
                "reason": f"proposal status={proposal.status}, must be accepted first",
            }

        # ============================================================
        # [proposal_applied]
        # ============================================================
        # G-1.3.3: 优先传真实审批凭证（accept 阶段随提案持久化）；
        # 无凭证且 enforcement 开启 → 拒绝；否则 legacy fallback（adapter 内部告警）。
        approval_record = (proposal.evaluator_meta or {}).get("approval_record")
        try:
            if approval_record is not None:
                apply_result = self.personality_adapter.apply_proposal(
                    proposal=proposal,
                    actor=actor,
                    approval_record=approval_record,
                )
            else:
                if is_approval_record_enforced():
                    return {
                        "status": "approval_record_required",
                        "proposal": proposal,
                        "reason": "accept 阶段未生成审批凭证, enforcement 开启时拒绝 apply",
                    }
                apply_result = self.personality_adapter.apply_proposal(
                    proposal=proposal,
                    actor=actor,
                    mark_approved=True,  # legacy fallback（adapter 内部 DeprecationWarning）
                )
        except Exception as e:
            logger.error("[proposal_apply_failed] id=%s error=%s", proposal_id, e)
            return {"status": "apply_failed", "proposal": proposal, "reason": str(e)}

        proposal.status = "applied"
        try:
            self.store.update(proposal)
        except Exception as e:
            logger.warning("[proposal_applied_store_failed] id=%s error=%s", proposal_id, e)

        logger.info(
            "[proposal_applied] id=%s actor=%s applied=%s",
            proposal.id,
            actor,
            apply_result.get("applied", False),
        )

        # ============================================================
        # [self_model_updated] — 触发 SelfModelStore 重新生成
        # ============================================================
        if apply_result.get("applied") and self.snapshot is not None:
            try:
                # snapshot 是 SelfModelStore 实例时调用其 update
                if hasattr(self.snapshot, "refresh_from_history"):
                    self.snapshot.refresh_from_history()
            except Exception as e:
                logger.warning("[self_model_updated_failed] error=%s", e)

        self._record_audit(audit_schema.AuditEntry(
            component="growth",
            actor=actor,
            reason="proposal_applied",
            before={"status": "accepted"},
            after={
                "proposal_id": proposal.id,
                "status": "applied",
                "applied": apply_result.get("applied", False),
                "evolution_record_id": apply_result.get("evolution_record_id", ""),
            },
        ))

        return {
            "status": "applied",
            "proposal": proposal,
            "apply_result": apply_result,
            "reason": "applied via PersonalityAdapter",
        }

    # ============================================================
    # list_proposals
    # ============================================================
    def list_proposals(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[growth_schema.GrowthProposal]:
        return self.store.list(status=status, limit=limit, offset=offset)

    def get_proposal(self, proposal_id: str) -> Optional[growth_schema.GrowthProposal]:
        return self.store.load(proposal_id)

    # ============================================================
    # mark_needs_review (Phase B.1.5 #5: 冲突标记)
    # ============================================================
    def mark_needs_review(
        self,
        proposal_id: str,
        reason: str = "conflict_detected",
    ) -> Dict[str, Any]:
        """标记 proposal 为 needs_review（不修改 status，仅追加 meta）。"""
        proposal = self.store.load(proposal_id)
        if proposal is None:
            return {"status": "not_found", "proposal": None}
        em = dict(proposal.evaluator_meta or {})
        em["needs_review"] = True
        em["needs_review_reason"] = reason
        proposal.evaluator_meta = em
        try:
            self.store.update(proposal)
        except Exception as e:
            logger.warning("[mark_needs_review_store_failed] id=%s error=%s", proposal_id, e)
        logger.info("[proposal_needs_review] id=%s reason=%s", proposal.id, reason)
        return {"status": "needs_review_marked", "proposal": proposal}

    # ============================================================
    # update_proposal_status_and_meta（Phase 4.0 R2.5.3：安全元数据更新）
    # ============================================================
    def update_proposal_status_and_meta(
        self,
        proposal_id: str,
        *,
        new_status: Optional[str] = None,
        meta_updates: Optional[Dict[str, Any]] = None,
        from_states: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        只更新 proposal.status + evaluator_meta 合并；**绝对不触发 accept/apply/adapter**。

        R2.5.3 红线：
          - 不调 PersonalityAdapter
          - 不写 GrowthRecord
          - 不修改 accepted_at / rejected_at（除非 meta_updates 里显式塞；但默认 R2.5.3 Approval 只改 status=approved/rejected/deferred/under_review）
        """
        proposal = self.store.load(proposal_id)
        if proposal is None:
            return {"status": "not_found", "proposal": None}
        # from_states 保护：只允许从指定旧状态迁移；不匹配就不修改（防止并发冲突）
        if from_states and proposal.status not in list(from_states):
            logger.warning(
                "[update_proposal_skip_transition] id=%s current_status=%s required_from=%s",
                proposal_id, proposal.status, from_states,
            )
            return {"status": "skipped_transition_mismatch", "proposal": proposal}
        old_status = proposal.status
        if new_status is not None:
            proposal.status = str(new_status)
        if meta_updates:
            em = dict(proposal.evaluator_meta or {})
            em.update({k: v for k, v in meta_updates.items() if v is not None})
            proposal.evaluator_meta = em
        try:
            self.store.update(proposal)
        except Exception as e:
            logger.warning(
                "[update_proposal_store_failed] id=%s error=%s", proposal_id, e,
            )
            return {"status": "store_update_failed", "proposal": proposal}
        logger.info(
            "[proposal_status_updated] id=%s old=%s new=%s",
            proposal_id, old_status, proposal.status,
        )
        return {"status": "updated", "proposal": proposal}

    # ============================================================
    # _convert_to_growth_record: Proposal → PersonalityGrowthRecord
    # ============================================================
    def _convert_to_growth_record(
        self,
        proposal: growth_schema.GrowthProposal,
        change_request: Any,
    ) -> Optional[Dict[str, Any]]:
        """Phase B.1.3: 把 proposal + change_request 转换为 PersonalityGrowthRecord。"""
        # 聚合 affected_dimensions
        affected: List[str] = []
        changes: Dict[str, Dict[str, Any]] = {}

        for ci in (proposal.proposed_changes or []):
            trait = self._extract_trait_name(ci.path)
            if trait == "unknown":
                continue
            affected.append(trait)
            before = ci.before if ci.before is not None else 0.5
            after = ci.after if ci.after is not None else before
            try:
                delta = round(float(after) - float(before), 6)
            except Exception:
                delta = 0.0
            changes[trait] = {
                "before": before,
                "after": after,
                "delta": delta,
                "reason": ci.reason or "",
            }

        if not affected:
            return None

        return {
            "record_id": f"pgr_{proposal.id}",
            "timestamp": now_iso(),
            "trigger_events": list(proposal.evidence_ids or []),
            "changes": changes,
            "affected_dimensions": list(set(affected)),
            "meaning": (proposal.evaluator_meta or {}).get("reason_summary")
                        or (proposal.evaluator_meta or {}).get("meaning")
                        or f"accepted proposal {proposal.id}",
            "narrative": (proposal.evaluator_meta or {}).get("narrative", ""),
            "confidence": float(proposal.confidence),
            "validation_count": 1,
            "growth_level": (proposal.evaluator_meta or {}).get("growth_level", "context"),
            "source_proposal_id": proposal.id,
            "source_event_id": proposal.source_event_id,
        }

    @staticmethod
    def _extract_trait_name(path: Any) -> str:
        if not isinstance(path, str):
            return "unknown"
        parts = path.split(".")
        return parts[-1] if parts else "unknown"

    # ============================================================
    # 审计 / 事件 辅助
    # ============================================================
    def _record_audit(self, entry) -> None:
        if not self.audit:
            return
        try:
            if hasattr(self.audit, "record"):
                self.audit.record(entry)
        except Exception:
            pass

    def _publish_event(self, event) -> None:
        if not self.event_bus:
            return
        try:
            if hasattr(self.event_bus, "publish"):
                self.event_bus.publish(event)
        except Exception:
            pass
