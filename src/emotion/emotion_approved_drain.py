# -*- coding: utf-8 -*-
"""
src/emotion/emotion_approved_drain.py

R-1.3.b: Emotion approved 提案消费器（approved → applied 生命周期闭环）。

模板: src/growth/self_model_approved_drain.py（同模式, 独立域）。

职责边界:
- 只消费 B-store（src/growth/proposal/storage.py）status=APPROVED 且
  proposal_type=emotion 的提案（admin 审批即授权）;
- 不生成提案、不修改治理规则、不修改 EmotionState schema;
- metadata["emotion_proposal"] 经字段白名单重建 EmotionChangeProposal
  （禁止全量信任 metadata 注入）;
- 应用: EmotionUpdater.apply（11 维校验）→ repository.save →
  record_state_mutation（component="emotion",
  approval_id=f"{reviewer_id}:{reviewed_at}"）→ 回写 APPLIED;
- apply 失败不得静默标记成功（applied 记录为空 → failed, 保持 APPROVED 供重试）;
- fail-soft: 单提案异常隔离, 不影响其余提案与主链;
- 默认关闭（emotion_drain_enabled=false）, 开启前不改变任何现有行为。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRAIN_ACTOR = "emotion_drain"

# EmotionChangeProposal 构造字段（metadata["emotion_proposal"] 白名单重建）
_ECP_FIELDS = (
    "emotion_dimension",
    "delta",
    "confidence",
    "reason",
    "evidence_ids",
    "proposal_id",
    "source_event_id",
    "created_at",
)


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
    # 延迟导入: 便于测试 monkeypatch storage.get_proposal_storage
    from src.growth.proposal.storage import get_proposal_storage

    return get_proposal_storage()


def _reconstruct_ecp(payload: Dict[str, Any]):
    """metadata 白名单重建 EmotionChangeProposal（字段过滤防御）。"""
    from src.emotion.emotion_change_proposal import EmotionChangeProposal

    if "emotion_dimension" not in payload:
        raise ValueError("missing emotion_dimension")
    if "delta" not in payload:
        raise ValueError("missing delta")
    kwargs = {k: payload[k] for k in _ECP_FIELDS if k in payload}
    return EmotionChangeProposal(**kwargs)


def drain_approved_emotion_proposals(
    *,
    repository: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """消费 B-store 中已 APPROVED 的 emotion 提案。

    返回: enabled/limit/reason + processed/applied/skipped/rejected/failed + details。
    配置关闭时返回 {"enabled": false, "reason": "disabled_by_config", ...}。
    """
    config = config or {}
    enabled = bool(config.get("emotion_drain_enabled", False))
    limit_val = int(config.get("emotion_drain_limit", 3)) if limit is None else int(limit)

    if not enabled:
        return _empty_result(False, limit_val, "disabled_by_config")
    if repository is None:
        return _empty_result(True, limit_val, "missing_repository")
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

    from src.emotion.emotion_state import EmotionState
    from src.emotion.emotion_updater import EmotionUpdater
    from src.governance.state_mutation_audit import record_state_mutation

    updater = EmotionUpdater()

    for proposal in candidates or []:
        pid = str(getattr(proposal, "proposal_id", ""))
        detail: Dict[str, Any] = {"proposal_id": pid}

        if getattr(proposal, "status", None) != PROPOSAL_STATUS["APPROVED"]:
            detail.update(result="skipped", reason="not_approved")
            result["skipped"] += 1
            result["details"].append(detail)
            continue
        if getattr(proposal, "proposal_type", None) != PROPOSAL_TYPE["EMOTION"]:
            detail.update(result="skipped", reason="type_filter")
            result["skipped"] += 1
            result["details"].append(detail)
            continue

        result["processed"] += 1

        metadata = getattr(proposal, "metadata", None) or {}
        payload = metadata.get("emotion_proposal")
        if not isinstance(payload, dict) or not payload:
            detail.update(result="rejected", reason="metadata_missing")
            result["rejected"] += 1
            result["details"].append(detail)
            continue
        try:
            ecp = _reconstruct_ecp(payload)
        except Exception as exc:  # noqa: BLE001
            detail.update(result="rejected", reason=f"payload_invalid:{exc}")
            result["rejected"] += 1
            result["details"].append(detail)
            continue

        # R-1.4: 幂等——该提案已成功应用过（state_mutations 账本含同 proposal_id
        # 的 emotion 应用审计, 多为上轮 APPLIED 回写失败遗留）→ 不重复 apply,
        # 仅补回写账本状态。
        try:
            from src.governance.state_mutation_audit import read_entries

            _existing = read_entries(limit=1000)
            _already = any(
                str(e.get("proposal_id", "")) == pid
                and e.get("component") == "emotion"
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

        try:
            state = repository.load()
        except Exception as exc:  # noqa: BLE001
            detail.update(result="failed", reason=f"load_failed:{exc}")
            result["failed"] += 1
            result["details"].append(detail)
            continue

        try:
            apply_result = updater.apply(
                state,
                [ecp],
                actor=DRAIN_ACTOR,
                source_event_id=str(getattr(proposal, "source_event_id", "") or ""),
            )
        except Exception as exc:  # noqa: BLE001
            detail.update(result="failed", reason=f"apply_error:{exc}")
            result["failed"] += 1
            result["details"].append(detail)
            continue

        if not apply_result.applied:
            detail.update(result="failed", reason="apply_rejected")
            result["failed"] += 1
            result["details"].append(detail)
            continue

        try:
            repository.save(EmotionState.from_dict(apply_result.state_after))
        except Exception as exc:  # noqa: BLE001
            detail.update(result="failed", reason=f"save_failed:{exc}")
            result["failed"] += 1
            result["details"].append(detail)
            continue

        # R-1.3.b: 应用审计（approval_id = reviewer_id:reviewed_at, fail-soft）
        try:
            record_state_mutation(
                component="emotion",
                target="emotion_state.dimensions",
                before=apply_result.state_before,
                after=apply_result.state_after,
                proposal_id=pid,
                approval_id=(
                    f"{getattr(proposal, 'reviewer_id', '') or 'admin'}:"
                    f"{getattr(proposal, 'reviewed_at', '') or 'unknown'}"
                ),
                actor=DRAIN_ACTOR,
                extra={
                    "reviewer_id": str(getattr(proposal, "reviewer_id", "") or ""),
                    "decision": "approve",
                },
            )
        except Exception:  # noqa: BLE001
            pass

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
    "drain_approved_emotion_proposals",
]
