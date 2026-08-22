# -*- coding: utf-8 -*-
"""
P2.6 Phase C-1: SelfModel approved 提案消费器（approved → applied 生命周期闭环）。

上游设计：docs/architecture/p2_6_phase_c0_self_model_drain_design.md

职责边界：
- 只消费 B-store（src/growth/proposal/storage.py）中 status=APPROVED 且
  proposal_type=self_model 的提案（admin 审批即授权，本消费器不重复治理评估，
  决策已冻结在 metadata["governance_decision"]）；
- 不生成提案、不修改治理政策 RULES；
- 应用成功唯一判据：apply 后重新读取 SelfModelStore，growth_narratives 出现
  对应 record_id（SelfModelUpdater.apply_proposal / SelfModelStore.apply_change_proposal
  均吞异常、无失败信号，见 C-0 §2.5）；
- fail-soft：单提案异常隔离；失败保持 APPROVED、不回写 APPLIED，供下轮重试
  或人工处理；record_id 幂等去重防重复追加；
- 默认关闭（self_model_drain_enabled=false），开启前不改变任何现有 A 态行为。

禁止：复用 personality drain；修改 personality drain 方法体；
修改 pipeline.py / growth_engine.py / self_model_updater.py / self_model_store.py。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DRAIN_ACTOR = "self_model_drain"

# C-0 §4.2: change_type 白名单（仅叙事追加 + 自我理解更新）
_ALLOWED_CHANGE_TYPES = ("narrative_append", "self_understanding_update")
# C-0 §4.2: change 载荷内禁止出现的身份核心键（越域防护）
_FORBIDDEN_CHANGE_KEYS = ("stable_traits", "current_traits")
_FORBIDDEN_CHANGE_PREFIX = "identity_"

# SelfModelChangeProposal 构造字段（与 metadata["self_model_proposal"] 1:1）
_SELF_MODEL_PROPOSAL_FIELDS = (
    "change_type",
    "target",
    "change",
    "source",
    "timestamp",
    "suggestion_id",
    "requires_approval",
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
    # 延迟导入：与 orchestrator._persist_self_model_governance_proposal 一致，
    # 便于测试通过 monkeypatch storage.get_proposal_storage 注入假实现。
    from src.growth.proposal.storage import get_proposal_storage

    return get_proposal_storage()


def _validate_payload(payload: Any) -> Optional[str]:
    """校验 metadata["self_model_proposal"]。返回 None=通过，否则拒绝原因。"""
    if not isinstance(payload, dict) or not payload:
        return "metadata_missing"
    change_type = payload.get("change_type")
    if change_type not in _ALLOWED_CHANGE_TYPES:
        return f"change_type_forbidden:{change_type}"
    change = payload.get("change")
    if not isinstance(change, dict):
        return "change_payload_invalid"
    for _key in change.keys():
        if _key in _FORBIDDEN_CHANGE_KEYS or _key.startswith(_FORBIDDEN_CHANGE_PREFIX):
            return f"cross_domain_key:{_key}"
    source = payload.get("source")
    growth_id = source.get("growth_id") if isinstance(source, dict) else None
    if not growth_id:
        return "missing_growth_id"
    return None


def _reconstruct_proposal(payload: Dict[str, Any]):
    """由 metadata 载荷重建 SelfModelChangeProposal（字段过滤防御）。"""
    from src.personality.self_model_updater import SelfModelChangeProposal

    kwargs = {_k: payload[_k] for _k in _SELF_MODEL_PROPOSAL_FIELDS if _k in payload}
    return SelfModelChangeProposal(**kwargs)


def _existing_record_ids(store) -> set:
    """读取 SelfModelStore 当前 growth_narratives 的 record_id 集合（幂等键）。"""
    try:
        data = store.get() if store is not None and hasattr(store, "get") else None
        narratives = (data or {}).get("growth_narratives") or []
        return {
            str(_entry.get("record_id", ""))
            for _entry in narratives
            if isinstance(_entry, dict) and _entry.get("record_id")
        }
    except Exception:
        return set()


def _apply(updater, store, sm_proposal) -> None:
    """应用提案：优先 SelfModelUpdater.apply_proposal（任务书指定路径），
    updater 缺失时回退 store.apply_change_proposal。"""
    if updater is not None and hasattr(updater, "apply_proposal"):
        updater.apply_proposal(sm_proposal)
    else:
        store.apply_change_proposal(sm_proposal)


def _mark_applied(storage, proposal) -> bool:
    """把提案账本回写为 APPLIED。回写失败返回 False（apply 已发生，下轮幂等补回写）。"""
    try:
        from src.growth.proposal.constants import PROPOSAL_STATUS

        proposal.status = PROPOSAL_STATUS["APPLIED"]
        proposal.applied_by = DRAIN_ACTOR
        proposal.applied_at = _utc_now_iso()
        storage.save(proposal)
        return True
    except Exception as _write_exc:
        logger.warning("[SelfModelDrain] 账本回写失败（下轮幂等补回写）: %s", _write_exc)
        return False


def drain_approved_self_model_proposals(
    *,
    updater: Optional[Any] = None,
    store: Optional[Any] = None,
    config: Optional[Dict[str, Any]] = None,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """消费 B-store 中已 APPROVED 的 self_model 提案。

    流程（C-0 §2）：
    list_by_status(APPROVED, limit) → 过滤 proposal_type=self_model → validate
    → 重建 SelfModelChangeProposal → updater.apply_proposal() → 重读 store 验证
    record_id 出现 → 成功: status=APPLIED / applied_by=self_model_drain /
    applied_at=ISO UTC；失败: 保持 APPROVED 不回写。

    返回：
        enabled/limit/reason + processed/applied/skipped/rejected/failed + details。
        配置关闭时返回 {"enabled": false, "reason": "disabled_by_config", ...}。
    """
    config = config or {}
    enabled = bool(config.get("self_model_drain_enabled", False))
    limit_val = int(config.get("self_model_drain_limit", 3)) if limit is None else int(limit)

    if not enabled:
        return _empty_result(False, limit_val, "disabled_by_config")
    if limit_val <= 0:
        return _empty_result(True, limit_val, "limit_zero")
    if store is None:
        return _empty_result(True, limit_val, "missing_store")

    result = _empty_result(True, limit_val)

    try:
        storage = _get_storage()
    except Exception as _storage_exc:
        result["reason"] = "storage_unavailable"
        result["failed"] += 1
        result["details"].append(
            {"proposal_id": "", "result": "failed", "reason": f"storage_unavailable:{_storage_exc}"}
        )
        return result

    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

    try:
        candidates: List[Any] = storage.list_by_status(
            PROPOSAL_STATUS["APPROVED"], limit=limit_val
        )
    except Exception as _list_exc:
        result["reason"] = "list_failed"
        result["failed"] += 1
        result["details"].append(
            {"proposal_id": "", "result": "failed", "reason": f"list_failed:{_list_exc}"}
        )
        return result

    existing_ids = _existing_record_ids(store)

    for proposal in candidates or []:
        _pid = str(getattr(proposal, "proposal_id", ""))
        _detail: Dict[str, Any] = {"proposal_id": _pid}

        # 防御：仅处理 APPROVED 状态
        if getattr(proposal, "status", None) != PROPOSAL_STATUS["APPROVED"]:
            _detail.update(result="skipped", reason="not_approved")
            result["skipped"] += 1
            result["details"].append(_detail)
            continue

        # 消费器内过滤：非 self_model 提案跳过（不重复 personality drain 职责）
        if getattr(proposal, "proposal_type", None) != PROPOSAL_TYPE["SELF_MODEL"]:
            _detail.update(result="skipped", reason="type_filter")
            result["skipped"] += 1
            result["details"].append(_detail)
            continue

        result["processed"] += 1

        _metadata = getattr(proposal, "metadata", None) or {}
        _payload = _metadata.get("self_model_proposal")
        _reject_reason = _validate_payload(_payload)
        if _reject_reason:
            _detail.update(result="rejected", reason=_reject_reason)
            result["rejected"] += 1
            result["details"].append(_detail)
            continue

        _growth_id = str(((_payload.get("source") or {}).get("growth_id")))

        # 幂等：record_id 已存在于 growth_narratives → 不重复 apply。
        # 账本仍为 APPROVED 说明上一轮 apply 成功但回写失败 → 仅补回写账本。
        if _growth_id in existing_ids:
            if _mark_applied(storage, proposal):
                _detail.update(result="status_backfill", reason="already_applied_record_id")
                result["applied"] += 1
            else:
                _detail.update(result="failed", reason="status_backfill_failed")
                result["failed"] += 1
            result["details"].append(_detail)
            continue

        try:
            _sm_proposal = _reconstruct_proposal(_payload)
        except Exception as _recon_exc:
            _detail.update(result="rejected", reason=f"payload_invalid:{_recon_exc}")
            result["rejected"] += 1
            result["details"].append(_detail)
            continue

        # 单提案异常隔离（fail-soft）：apply 抛错不影响其余提案与聊天主链
        try:
            _apply(updater, store, _sm_proposal)
        except Exception as _apply_exc:
            _detail.update(result="failed", reason=f"apply_error:{_apply_exc}")
            result["failed"] += 1
            result["details"].append(_detail)
            continue

        # 成功唯一判据：apply 后重读 store，验证 record_id 出现（C-0 §2.5）
        if _growth_id in _existing_record_ids(store):
            # G-1.2: apply 侧 mutation 审计（fail-soft，不阻断账本回写）
            try:
                from src.governance.state_mutation_audit import record_state_mutation

                record_state_mutation(
                    component="self_model",
                    target="self_model",
                    before={"growth_id": _growth_id},
                    after={"applied": True},
                    proposal_id=_pid,
                    approval_id=(
                        f"{getattr(proposal, 'reviewer_id', '') or 'admin'}:"
                        f"{getattr(proposal, 'reviewed_at', '') or 'unknown'}"
                    ),
                    actor=DRAIN_ACTOR,
                )
            except Exception:  # noqa: BLE001
                pass
            if _mark_applied(storage, proposal):
                _detail.update(result="applied")
            else:
                _detail.update(result="applied", reason="status_write_failed")
            result["applied"] += 1
        else:
            _detail.update(result="failed", reason="apply_unverified")
            result["failed"] += 1
        result["details"].append(_detail)

    if result["processed"] or result["rejected"] or result["failed"]:
        logger.info(
            "[SelfModelDrain] actor=%s self_model_drain_processed=%d "
            "self_model_apply_success=%d self_model_apply_rejected=%d "
            "self_model_drain_failed=%d skipped=%d",
            DRAIN_ACTOR,
            result["processed"],
            result["applied"],
            result["rejected"],
            result["failed"],
            result["skipped"],
        )
        print(
            f"[SelfModelDrain] actor={DRAIN_ACTOR} processed={result['processed']} "
            f"applied={result['applied']} skipped={result['skipped']} "
            f"rejected={result['rejected']} failed={result['failed']}"
        )

    return result
