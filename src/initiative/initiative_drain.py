# -*- coding: utf-8 -*-
"""
src/initiative/initiative_drain.py

v1.3 Phase 5.1: Initiative 治理基础设施(Proposal → Drain → Action → Audit)。

职责(只做这一件事):
    APPROVED InitiativeProposal(经 Admin Review)
        ↓ 验证 proposal_type=initiative + 载荷完整性 + 来源校验(fail-closed)
        ↓ 幂等检查(state_mutations 账本 component=initiative)
        ↓ 生成 Action(action_type 来自 action_spec, 不 dispatch 不发送)
        ↓ 审计: state_mutation_audit(component=initiative) + action_lifecycle.jsonl
        ↓ 回写提案 APPLIED

红线(本阶段):
- 不产生主动消息(不 dispatch / 不 import sender / 不 import bridge);
- 不接 GoalCandidate / 不读 GoalState(goal_reference 仅作为追溯字段透传);
- 不调用 LLM;
- 不修改人格/记忆/SelfModel/Emotion/Relationship;
- 默认关闭(initiative_proposal_enabled=false) = 与 v1.2.1 完全一致。

复用治理信封: GrowthProposal(proposal_type=initiative), 禁止第二套 Proposal Store。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRAIN_ACTOR = "initiative_drain"

LIFECYCLE_STAGE_PRODUCED = "action_created"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_result(enabled: bool, limit: int, reason: Optional[str] = None) -> Dict[str, Any]:
    return {
        "enabled": enabled,
        "limit": limit,
        "reason": reason,
        "processed": 0,
        "applied": 0,
        "skipped": 0,
        "rejected": 0,
        "failed": 0,
        "actions": [],
        "details": [],
    }


def _get_storage():
    from src.growth.proposal.storage import get_proposal_storage

    return get_proposal_storage()


# ============================================================
# InitiativeProposal 构造(与 goal_proposal 同模式)
# ============================================================
def build_initiative_proposal(
    *,
    initiative_id: str,
    goal_reference: str = "",
    source_refs: List[Dict[str, Any]],
    action_spec: Dict[str, Any],
    confidence: float = 0.5,
    proposal_id: Optional[str] = None,
    source: str = "admin",
    user_id: str = "",
) -> Any:
    """构造 PENDING 的 Initiative 治理提案(恒 PENDING, 绝不自动 APPROVED)。

    metadata 承载: initiative_id / goal_reference / source_refs / action_spec
    / confidence(全部可追溯); 复用 GrowthProposal 信封, 无第二套 Store。
    """
    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
    from src.growth.proposal.proposal import GrowthProposal

    _refs = list(source_refs or [])
    _spec = dict(action_spec or {})
    _evidence: List[str] = []
    for ref in _refs:
        if isinstance(ref, dict):
            _st = str(ref.get("source_type", "") or "")
            _sid = str(ref.get("source_id", "") or "")
            if _st and _sid:
                _evidence.append(f"{_st}:{_sid}")
    return GrowthProposal(
        proposal_id=proposal_id or f"prop_{uuid.uuid4().hex[:8]}",
        proposal_type=PROPOSAL_TYPE["INITIATIVE"],
        status=PROPOSAL_STATUS["PENDING"],
        source=source,
        user_id=user_id,
        confidence=float(confidence or 0.0),
        reason=f"initiative: {str(initiative_id or '')[:128]}",
        evidence=_evidence,
        metadata={
            "initiative_id": str(initiative_id or "").strip(),
            "goal_reference": str(goal_reference or "").strip(),
            "source_refs": _refs,
            "action_spec": _spec,
            "confidence": float(confidence or 0.0),
            "governance_decision": {"action": "approval_required"},
        },
    )


def _extract_payload(proposal: Any) -> Optional[Dict[str, Any]]:
    meta = getattr(proposal, "metadata", None)
    if not isinstance(meta, dict):
        return None
    if not str(meta.get("initiative_id", "") or "").strip():
        return None
    return meta


# ============================================================
# InitiativeDrain
# ============================================================
def drain_approved_initiative_proposals(
    *,
    action_recorder: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """消费 B-store 中 APPROVED 的 initiative 提案 → 生成 Action(不发送)。

    - 默认关闭(initiative_proposal_enabled=false);
    - 幂等: state_mutations 账本含同 proposal_id + component=initiative →
      只补回写 APPLIED, 不重复生成 Action;
    - fail-soft: 单提案异常隔离。
    """
    config = config or {}
    enabled = bool(config.get("initiative_proposal_enabled", False))
    limit_val = int(config.get("initiative_drain_limit", 3)) if limit is None else int(limit)

    if not enabled:
        return _empty_result(False, limit_val, "disabled_by_config")
    if limit_val <= 0:
        return _empty_result(True, limit_val, "limit_zero")

    result = _empty_result(True, limit_val)

    try:
        storage = _get_storage()
    except Exception as exc:  # noqa: BLE001
        result["reason"] = "storage_unavailable"
        result["failed"] += 1
        result["details"].append(
            {"proposal_id": "", "result": "failed", "reason": f"storage_unavailable:{exc}"}
        )
        return result

    from src.governance.state_mutation_audit import read_entries, record_state_mutation
    from src.goal.goal_source_validation import validate_goal_source_refs
    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
    from src.runtime.action_dispatcher import Action

    try:
        candidates: List[Any] = storage.list_by_status(
            PROPOSAL_STATUS["APPROVED"], limit=limit_val
        )
    except Exception as exc:  # noqa: BLE001
        result["reason"] = "list_failed"
        result["failed"] += 1
        result["details"].append(
            {"proposal_id": "", "result": "failed", "reason": f"list_failed:{exc}"}
        )
        return result

    for proposal in candidates or []:
        pid = str(getattr(proposal, "proposal_id", ""))
        detail: Dict[str, Any] = {"proposal_id": pid}

        if getattr(proposal, "status", None) != PROPOSAL_STATUS["APPROVED"]:
            detail.update(result="skipped", reason="not_approved")
            result["skipped"] += 1
            result["details"].append(detail)
            continue
        if getattr(proposal, "proposal_type", None) != PROPOSAL_TYPE["INITIATIVE"]:
            detail.update(result="skipped", reason="type_filter")
            result["skipped"] += 1
            result["details"].append(detail)
            continue

        result["processed"] += 1

        # ---- 载荷完整性 + 来源真实性(fail-closed) ----
        meta = _extract_payload(proposal)
        if meta is None:
            detail.update(result="rejected", reason="metadata_missing")
            result["rejected"] += 1
            result["details"].append(detail)
            continue

        initiative_id = str(meta.get("initiative_id", "") or "").strip()
        goal_reference = str(meta.get("goal_reference", "") or "").strip()
        action_spec = meta.get("action_spec")
        source_refs = meta.get("source_refs")
        if not initiative_id:
            detail.update(result="rejected", reason="missing_initiative_id")
            result["rejected"] += 1
            result["details"].append(detail)
            continue
        if not isinstance(action_spec, dict) or not str(
            action_spec.get("action_type", "") or ""
        ).strip():
            detail.update(result="rejected", reason="missing_action_spec")
            result["rejected"] += 1
            result["details"].append(detail)
            continue
        _src_ok, _src_reason = validate_goal_source_refs(source_refs)
        if not _src_ok:
            detail.update(result="rejected", reason=_src_reason)
            result["rejected"] += 1
            result["details"].append(detail)
            continue

        # ---- 幂等: 账本已含该提案的 initiative 审计 → 只补回写 ----
        try:
            _existing = read_entries(limit=1000)
            _already = any(
                str(e.get("proposal_id", "")) == pid
                and e.get("component") == "initiative"
                for e in _existing
            )
        except Exception:  # noqa: BLE001
            _already = False
        if _already:
            try:
                proposal.status = PROPOSAL_STATUS["APPLIED"]
                proposal.applied_by = DRAIN_ACTOR
                proposal.applied_at = proposal.applied_at or _utc_now_iso()
                storage.save(proposal)
                detail.update(result="status_backfill", reason="already_applied")
                result["applied"] += 1
            except Exception:  # noqa: BLE001
                detail.update(result="failed", reason="status_backfill_failed")
                result["failed"] += 1
            result["details"].append(detail)
            continue

        # ---- 生成 Action(只构造, 不 dispatch 不发送) ----
        # action_type 在 Action.action_type 上, 不进 payload(与 safety 白名单一致)
        _action_payload: Dict[str, Any] = {
            k: v for k, v in dict(action_spec).items() if k != "action_type"
        }
        _action_payload["proposal_id"] = pid
        _action_payload["goal_reference"] = goal_reference
        _action_payload["initiative_id"] = initiative_id
        # v1.3 Phase 5.2: confidence 随 Action 透传(供 ActionSafetyFilter 检查)
        try:
            _action_payload["confidence"] = round(
                min(max(float(meta.get("confidence", 0.0) or 0.0), 0.0), 1.0), 6
            )
        except (TypeError, ValueError):
            _action_payload["confidence"] = 0.0
        action = Action(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=str(action_spec["action_type"]),
            payload=_action_payload,
            reason=f"initiative_proposal:{pid}",
        )

        # ---- 审计①: state_mutation_audit(component=initiative) ----
        _approval_id = (
            f"{getattr(proposal, 'reviewer_id', '') or 'admin'}:"
            f"{getattr(proposal, 'reviewed_at', '') or 'unknown'}"
        )
        try:
            record_state_mutation(
                component="initiative",
                target="proactive_action",
                before={},
                after={
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "goal_reference": goal_reference,
                    "initiative_id": initiative_id,
                },
                proposal_id=pid,
                approval_id=_approval_id,
                actor=DRAIN_ACTOR,
                extra={
                    "reviewer_id": str(getattr(proposal, "reviewer_id", "") or ""),
                    "decision": "approve",
                },
            )
        except Exception:  # noqa: BLE001
            pass

        # ---- 审计②: action_lifecycle.jsonl(新增 proposal_id/goal_reference) ----
        try:
            _recorder = action_recorder or _build_default_recorder()
            _recorder.persist_event(
                action_id=action.action_id,
                lifecycle_id=f"lc_{uuid.uuid4().hex[:8]}",
                stage=LIFECYCLE_STAGE_PRODUCED,
                decision="initiative_drain",
                source=DRAIN_ACTOR,
                proposal_id=pid,
                goal_reference=goal_reference,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativeDrain] action_lifecycle 审计失败(已隔离): %s", exc)

        # ---- 回写 APPLIED ----
        try:
            proposal.status = PROPOSAL_STATUS["APPLIED"]
            proposal.applied_by = DRAIN_ACTOR
            proposal.applied_at = _utc_now_iso()
            storage.save(proposal)
            detail.update(result="applied")
        except Exception:  # noqa: BLE001
            detail.update(result="applied", reason="status_write_failed")
        result["applied"] += 1
        result["actions"].append(action)
        result["details"].append(detail)

    return result


def _build_default_recorder():
    from src.runtime.action_persistence import ActionPersistenceManager

    return ActionPersistenceManager()


__all__ = [
    "DRAIN_ACTOR",
    "LIFECYCLE_STAGE_PRODUCED",
    "build_initiative_proposal",
    "drain_approved_initiative_proposals",
]
