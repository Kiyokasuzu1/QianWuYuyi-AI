# -*- coding: utf-8 -*-
"""
src/goal/goal_approved_drain.py

v1.3 Agency Phase 1 Task 3: Goal approved 提案消费器
(approved → GoalState active 生命周期闭环)。

模板: src/relationship/relationship_approved_drain.py(同模式, 独立域)。

职责边界:
- 只消费 B-store(src/growth/proposal/storage.py)status=APPROVED 且
  proposal_type=goal 的提案(admin 审批即授权);
- apply 目标 = GoalStateStore(行为倾向域), append status=active 记录;
  不写 personality / self_model / emotion / relationship 任何状态;
- 来源真实性 fail-closed: goal 载荷缺 source_refs / 条目不合法 → 拒绝,
  提案保持 APPROVED(可重新审批/修正), 绝不产生无来源 Goal;
- 幂等: ① state_mutations 账本已含同 proposal_id + component=goal →
  只补回写 APPLIED, 不重复 apply; ② GoalState 已 active(apply 成功但
  审计丢失的崩溃窗口)→ 补审计 + 补回写, 不重复 append;
- fail-soft: 单提案异常隔离; 默认关闭(goal_drain_enabled=false)。

禁止:
- 不调用 LLM / InitiativeBridge / 消息发送;
- 不触发 Goal 完成 → 成长记录连接(Phase 2+ 事项);
- 不接入聊天上下文(Phase 1 不接线 RuntimeCore 主循环)。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRAIN_ACTOR = "goal_drain"


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
        "details": [],
    }


def _get_storage():
    from src.growth.proposal.storage import get_proposal_storage

    return get_proposal_storage()


def drain_approved_goal_proposals(
    *,
    goal_store: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """消费 B-store 中已 APPROVED 的 goal 提案, 应用到 GoalState(active)。"""
    config = config or {}
    enabled = bool(config.get("goal_drain_enabled", False))
    limit_val = int(config.get("goal_drain_limit", 3)) if limit is None else int(limit)

    if not enabled:
        return _empty_result(False, limit_val, "disabled_by_config")
    if goal_store is None:
        return _empty_result(True, limit_val, "missing_goal_store")
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

    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
    from src.goal.goal_proposal import extract_goal_payload
    from src.goal.goal_source_validation import validate_goal_source_refs
    from src.goal.goal_state import GOAL_STATUS
    from src.governance.state_mutation_audit import read_entries, record_state_mutation

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
        if getattr(proposal, "proposal_type", None) != PROPOSAL_TYPE["GOAL"]:
            detail.update(result="skipped", reason="type_filter")
            result["skipped"] += 1
            result["details"].append(detail)
            continue

        result["processed"] += 1

        # ---- 载荷提取 + 来源真实性 fail-closed 校验 ----
        payload = extract_goal_payload(proposal)
        if payload is None:
            detail.update(result="rejected", reason="metadata_missing")
            result["rejected"] += 1
            result["details"].append(detail)
            continue

        goal_id = str(payload.get("goal_id", "") or "").strip()
        description = str(payload.get("description", "") or "").strip()
        source_refs = payload.get("source_refs")
        if not goal_id:
            detail.update(result="rejected", reason="missing_goal_id")
            result["rejected"] += 1
            result["details"].append(detail)
            continue
        if not description:
            detail.update(result="rejected", reason="missing_description")
            result["rejected"] += 1
            result["details"].append(detail)
            continue
        _src_ok, _src_reason = validate_goal_source_refs(source_refs)
        if not _src_ok:
            detail.update(result="rejected", reason=_src_reason)
            result["rejected"] += 1
            result["details"].append(detail)
            continue

        _priority = str(payload.get("priority") or "medium")
        if _priority not in ("low", "medium", "high"):
            _priority = "medium"
        try:
            _confidence = round(
                min(max(float(payload.get("confidence", 0.0) or 0.0), 0.0), 1.0), 6
            )
        except (TypeError, ValueError):
            _confidence = 0.0

        _approval_id = (
            f"{getattr(proposal, 'reviewer_id', '') or 'admin'}:"
            f"{getattr(proposal, 'reviewed_at', '') or 'unknown'}"
        )
        _extra = {
            "reviewer_id": str(getattr(proposal, "reviewer_id", "") or ""),
            "decision": "approve",
        }

        # ---- 幂等①: 账本已含该提案的 goal 应用审计(apply+audit 已完成,
        # 仅回写丢失)→ 补回写 APPLIED, 不重复 apply ----
        try:
            _existing = read_entries(limit=1000)
            _already = any(
                str(e.get("proposal_id", "")) == pid and e.get("component") == "goal"
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

        # ---- 幂等②: GoalState 已 active(apply 成功但审计丢失的崩溃窗口)
        # → 补审计 + 补回写, 不重复 append ----
        try:
            _current = goal_store.get(goal_id)
        except Exception:  # noqa: BLE001
            _current = None
        if isinstance(_current, dict) and _current.get("status") == GOAL_STATUS["ACTIVE"]:
            try:
                record_state_mutation(
                    component="goal",
                    target="goal_state",
                    before=dict(_current),
                    after=dict(_current),
                    proposal_id=pid,
                    approval_id=_approval_id,
                    actor=DRAIN_ACTOR,
                    extra=dict(_extra, recover=True),
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                proposal.status = PROPOSAL_STATUS["APPLIED"]
                proposal.applied_by = DRAIN_ACTOR
                proposal.applied_at = proposal.applied_at or _utc_now_iso()
                storage.save(proposal)
                detail.update(result="goal_already_active", reason="already_applied")
                result["applied"] += 1
            except Exception:  # noqa: BLE001
                detail.update(result="failed", reason="status_backfill_failed")
                result["failed"] += 1
            result["details"].append(detail)
            continue

        # ---- apply: GoalState append active 记录 ----
        try:
            _ok = goal_store.append_state(
                goal_id=goal_id,
                status=GOAL_STATUS["ACTIVE"],
                description=description,
                source_refs=list(source_refs),
                priority=_priority,
                confidence=_confidence,
                proposal_id=pid,
                # v1.3 Phase 2: reason 来自已审批提案, 随状态落盘, 供 GoalResolver 展示
                reason=str(getattr(proposal, "reason", "") or ""),
            )
            _persisted = goal_store.get(goal_id) if _ok else None
            if not isinstance(_persisted, dict) or _persisted.get("status") != GOAL_STATUS["ACTIVE"]:
                detail.update(result="failed", reason="apply_unverified")
                result["failed"] += 1
                result["details"].append(detail)
                continue
        except Exception as exc:  # noqa: BLE001
            detail.update(result="failed", reason=f"apply_failed:{exc}")
            result["failed"] += 1
            result["details"].append(detail)
            continue

        # ---- 审计(尽力而为, 失败不阻断; 已由幂等②兜底) ----
        try:
            record_state_mutation(
                component="goal",
                target="goal_state",
                before={},
                after={"goal_id": goal_id, "status": GOAL_STATUS["ACTIVE"]},
                proposal_id=pid,
                approval_id=_approval_id,
                actor=DRAIN_ACTOR,
                extra=_extra,
            )
        except Exception:  # noqa: BLE001
            pass

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
        result["details"].append(detail)

    return result


__all__ = [
    "DRAIN_ACTOR",
    "drain_approved_goal_proposals",
]
