# -*- coding: utf-8 -*-
"""
src/growth/self_model_governance_bridge.py

P2.8 Phase D-5.0: SelfModel Governance Bridge（治理桥接消费器）。

解决 D-4.0 遗留问题:
    SelfModel Cycle → GrowthProposal(pending) → 人工审批 → accepted
        ↓
    （原缺口: 谁把 accepted 提案送入合法 SelfModel Evolution 路径）
        ↓
    本 Bridge: accepted/approved canonical 提案 → 重建 SelfModelChangeProposal
    → SelfModelUpdater.apply_proposal → SelfModelStore → self_model.json

单向流（任务书 Step 2.1）:
    允许: Governance(审批结果) → SelfModel Evolution(apply)
    禁止: 反向调用 ProposalManager.accept/approve/reject、
          update_proposal_status_and_meta、任何自动审批

审批证明（任务书 Step 2.2）:
    任何 apply 必须携带 proposal_id + approval_id + approved_by + approved_at
    （提取自 evaluator_meta["approval"] / 顶层同名键, approved_at 回退
    proposal.accepted_at）。缺任一字段 → rejected, 拒绝执行。

幂等（任务书 Step 2.3）:
    第一次: applied; 重复消费同一 proposal: already_applied,
    不再二次改变 self_model（判据: A-store status=applied 或
    growth_id 已存在于 store.growth_narratives, 与 C-1 drain 同一判据）。

审计（任务书 Step 2.4）:
    每次消费至少产生:
    - self_model_proposal_consumed
    - self_model_evolution_applied
    - self_model_evolution_rejected
    条目包含 proposal_id / actor / reason / before / after。
    审计钩子可注入; 未注入时使用模块内置 append-only JSONL
    (DEFAULT_BRIDGE_AUDIT_PATH), 审计不可被绕过。

红线（与 D-3.0/D-4.0 一致）:
- 不自动 approve / accept: 本模块只读取治理结果(status + 审批证明)
- 不绕过 ProposalManager: 审批必须已经发生(accepted/approved 状态)
- 不直接修改 self_model.json: 唯一写路径是 SelfModelUpdater.apply_proposal
  → SelfModelStore.apply_change_proposal
- 不复制 C-1 drain 逻辑: 复用其校验常量 / 重建函数 / 幂等判据
  (self_model_approved_drain 模块只读 import, 单一事实源)
- identity / trace 级提案硬拒绝(镜像 SelfModelGovernancePolicy RULES DENY)

禁止修改: self_model_cycle.py / growth_cycle.py / runtime_core.py /
identity_core.py / personality_evolution_pipeline.py / drain 本体。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts import audit_schema

logger = logging.getLogger(__name__)

BRIDGE_ACTOR = "self_model_governance_bridge"
ACCEPTED_STATES = frozenset({"accepted", "approved"})

AUDIT_REASON_CONSUMED = "self_model_proposal_consumed"
AUDIT_REASON_APPLIED = "self_model_evolution_applied"
AUDIT_REASON_REJECTED = "self_model_evolution_rejected"

DEFAULT_BRIDGE_AUDIT_PATH = "data/audit/self_model_governance_bridge.jsonl"

# 与 SelfModelGovernancePolicy RULES 一致: identity / trace 级禁止
_DENY_GROWTH_LEVELS = frozenset({"identity", "trace"})
_DENY_PROPOSAL_TYPES = frozenset({"identity"})

# SelfModelStore.apply_change_proposal 实际支持的目标
_ALLOWED_TARGETS = frozenset({"growth_narratives", "self_understanding"})

_APPROVAL_FIELDS = ("approval_id", "approved_by", "approved_at")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# 审计写入器（内置默认, 审计不可绕过）
# ============================================================
_AUDIT_LOCKS: Dict[str, threading.RLock] = {}
_AUDIT_LOCKS_GUARD = threading.Lock()


def _audit_lock(path) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _AUDIT_LOCKS_GUARD:
        lock = _AUDIT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _AUDIT_LOCKS[key] = lock
        return lock


class _JsonlAuditWriter:
    """append-only JSONL 审计落盘（与 lifecycle audit writer 同模式）。"""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: Any) -> None:
        try:
            line = json.dumps(
                entry.to_dict() if hasattr(entry, "to_dict") else dict(entry),
                ensure_ascii=False,
                default=str,
            )
            with _audit_lock(self.path):
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SelfModelGovernanceBridge] 审计落盘失败（已隔离）: %s", exc)


def _safe_record(audit: Any, entry: audit_schema.AuditEntry) -> None:
    try:
        if hasattr(audit, "record"):
            audit.record(entry)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SelfModelGovernanceBridge] 审计写入失败（已隔离）: %s", exc)


def _audit_consumed(audit: Any, actor: str, proposal_id: str,
                    decision: str, status: str, reason: str) -> None:
    _safe_record(audit, audit_schema.AuditEntry(
        component="growth",
        actor=actor,
        reason=AUDIT_REASON_CONSUMED,
        after={
            "proposal_id": proposal_id,
            "decision": decision,
            "status": status,
            "reason": reason,
        },
    ))


def _audit_rejected(audit: Any, actor: str, proposal_id: str,
                    reason: str, proof: Optional[Dict[str, Any]]) -> None:
    _safe_record(audit, audit_schema.AuditEntry(
        component="growth",
        actor=actor,
        reason=AUDIT_REASON_REJECTED,
        after={
            "proposal_id": proposal_id,
            "reason": reason,
            "approval": proof,
        },
    ))


def _audit_applied(audit: Any, actor: str, proposal: Any,
                   before_ids: List[str], after_ids: List[str],
                   growth_id: str, proof: Dict[str, Any]) -> None:
    _safe_record(audit, audit_schema.AuditEntry(
        component="growth",
        actor=actor,
        reason=AUDIT_REASON_APPLIED,
        source_event_id=getattr(proposal, "source_event_id", None),
        evidence_memory_ids=list(getattr(proposal, "evidence_ids", None) or []),
        before={"record_ids": sorted(before_ids)},
        after={
            "record_ids": sorted(after_ids),
            "record_id": growth_id,
            "approval": proof,
        },
    ))


# ============================================================
# 审批证明提取（任务书 Step 2.2）
# ============================================================
def extract_approval_proof(proposal: Any) -> Optional[Dict[str, str]]:
    """从 canonical 提案提取审批证明。

    来源优先级: evaluator_meta["approval"] dict → evaluator_meta 顶层同名键;
    approved_at 回退 proposal.accepted_at（ProposalManager.accept_proposal 写入）。

    Returns:
        四字段齐备时返回 {proposal_id, approval_id, approved_by, approved_at};
        任一缺失返回 None（调用方必须拒绝执行）。
    """
    proposal_id = str(getattr(proposal, "id", "") or "").strip()
    em = dict(getattr(proposal, "evaluator_meta", None) or {})
    approval = em.get("approval")
    if not isinstance(approval, dict):
        approval = {}

    def _pick(*keys: str) -> str:
        for key in keys:
            value = approval.get(key) or em.get(key)
            if value:
                return str(value).strip()
        return ""

    approval_id = _pick("approval_id")
    approved_by = _pick("approved_by")
    approved_at = _pick("approved_at") or str(
        getattr(proposal, "accepted_at", "") or ""
    ).strip()

    if not proposal_id or not approval_id or not approved_by or not approved_at:
        return None
    return {
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "approved_by": approved_by,
        "approved_at": approved_at,
    }


def _missing_approval_fields(proposal: Any) -> List[str]:
    """缺失的审批字段名（用于审计 reason, 与 extract_approval_proof 同源）。"""
    proof = extract_approval_proof(proposal)
    if proof is not None:
        return []
    missing = []
    if not str(getattr(proposal, "id", "") or "").strip():
        missing.append("proposal_id")
    em = dict(getattr(proposal, "evaluator_meta", None) or {})
    approval = em.get("approval")
    if not isinstance(approval, dict):
        approval = {}
    has_approved_at = bool(
        (approval.get("approved_at") or em.get("approved_at"))
        or getattr(proposal, "accepted_at", "")
    )
    if not (approval.get("approval_id") or em.get("approval_id")):
        missing.append("approval_id")
    if not (approval.get("approved_by") or em.get("approved_by")):
        missing.append("approved_by")
    if not has_approved_at:
        missing.append("approved_at")
    return missing


# ============================================================
# 载荷/域闸（复用 C-1 drain 的单一事实源, 不复制逻辑）
# ============================================================
def _validate_payload(proposal: Any, em: Dict[str, Any]) -> Optional[str]:
    """校验 self_model_proposal 载荷。返回 None=通过, 否则拒绝原因。"""
    from src.growth.self_model_approved_drain import (
        _ALLOWED_CHANGE_TYPES,
        _FORBIDDEN_CHANGE_KEYS,
        _FORBIDDEN_CHANGE_PREFIX,
    )

    payload = em.get("self_model_proposal")
    if not isinstance(payload, dict) or not payload:
        return "metadata_missing"
    change_type = payload.get("change_type")
    if change_type not in _ALLOWED_CHANGE_TYPES:
        return f"change_type_forbidden:{change_type}"
    target = payload.get("target")
    if target not in _ALLOWED_TARGETS:
        return f"target_forbidden:{target}"
    change = payload.get("change")
    if not isinstance(change, dict):
        return "change_payload_invalid"
    for key in change.keys():
        if key in _FORBIDDEN_CHANGE_KEYS or key.startswith(_FORBIDDEN_CHANGE_PREFIX):
            return f"cross_domain_key:{key}"
    source = payload.get("source")
    growth_id = source.get("growth_id") if isinstance(source, dict) else None
    if not growth_id:
        return "missing_growth_id"
    return None


def _reconstruct_proposal(payload: Dict[str, Any]):
    """由 metadata 载荷重建 SelfModelChangeProposal（复用 C-1 drain 重建逻辑）。"""
    from src.growth.self_model_approved_drain import _reconstruct_proposal as _drain_rebuild

    return _drain_rebuild(payload)


def _existing_record_ids(model_store: Any) -> set:
    """读取当前 growth_narratives 的 record_id 集合（与 C-1 drain 同一幂等判据）。"""
    from src.growth.self_model_approved_drain import _existing_record_ids as _drain_ids

    return _drain_ids(model_store)


# ============================================================
# 核心: 单提案消费
# ============================================================
def consume_self_model_proposal(
    proposal: Any,
    *,
    updater: Any,
    model_store: Any,
    proposal_store: Any = None,
    actor: str = BRIDGE_ACTOR,
    audit: Any = None,
) -> Dict[str, Any]:
    """消费一条 canonical SelfModel 提案（唯一合法 apply 入口）。

    流程（全部闸不可跳过）:
    status ∈ {accepted, approved} → 审批证明四字段 → identity/trace 硬拒绝
    → 载荷校验 → record_id 幂等 → updater.apply_proposal
    → 重读验证 → A-store 回写 applied → 审计

    Returns:
        {proposal_id, status, reason, applied, approval_proof, record_id}
        status ∈ {applied, already_applied, blocked, denied, rejected, not_found}
    """
    audit = audit if audit is not None else _JsonlAuditWriter(DEFAULT_BRIDGE_AUDIT_PATH)

    if proposal is None:
        return {
            "proposal_id": "", "status": "not_found",
            "reason": "proposal_is_none", "applied": False,
            "approval_proof": None, "record_id": "",
        }

    proposal_id = str(getattr(proposal, "id", "") or "")
    result: Dict[str, Any] = {
        "proposal_id": proposal_id,
        "status": "",
        "reason": "",
        "applied": False,
        "approval_proof": None,
        "record_id": "",
    }

    def _finish(status: str, reason: str, **extra: Any) -> Dict[str, Any]:
        result.update(status=status, reason=reason)
        result.update(extra)
        return dict(result)

    # ---- 闸 1: 状态（applied 短路 → 幂等） ----
    status = str(getattr(proposal, "status", "") or "")
    if status == "applied":
        _audit_consumed(audit, actor, proposal_id, "already_applied",
                        status, "store_status_already_applied")
        return _finish("already_applied", "store_status_already_applied")
    if status not in ACCEPTED_STATES:
        reason = f"status_not_accepted:{status}"
        _audit_consumed(audit, actor, proposal_id, "blocked", status, reason)
        _audit_rejected(audit, actor, proposal_id, reason, None)
        return _finish("blocked", reason)

    # ---- 闸 2: 审批证明（缺任一字段 → 拒绝执行） ----
    proof = extract_approval_proof(proposal)
    if proof is None:
        missing = _missing_approval_fields(proposal)
        reason = "missing_approval_proof:" + ",".join(missing)
        _audit_consumed(audit, actor, proposal_id, "rejected", status, reason)
        _audit_rejected(audit, actor, proposal_id, reason, None)
        return _finish("rejected", reason)

    result["approval_proof"] = proof

    # ---- 闸 3: identity / trace 硬拒绝 ----
    em = dict(getattr(proposal, "evaluator_meta", None) or {})
    proposal_type = str(em.get("proposal_type", "") or "")
    growth_level = str(em.get("growth_level", "") or "")
    if proposal_type in _DENY_PROPOSAL_TYPES or growth_level in _DENY_GROWTH_LEVELS:
        reason = f"identity_change_denied:type={proposal_type},level={growth_level}"
        _audit_consumed(audit, actor, proposal_id, "denied", status, reason)
        _audit_rejected(audit, actor, proposal_id, reason, proof)
        return _finish("denied", reason)

    # ---- 闸 4: 载荷校验（复用 C-1 白名单/越域规则） ----
    payload_error = _validate_payload(proposal, em)
    if payload_error:
        decision = "denied" if payload_error.startswith(("cross_domain_key", "target_forbidden")) else "rejected"
        _audit_consumed(audit, actor, proposal_id, decision, status, payload_error)
        _audit_rejected(audit, actor, proposal_id, payload_error, proof)
        return _finish(decision, payload_error)

    payload = em.get("self_model_proposal") or {}
    growth_id = str(((payload.get("source") or {}).get("growth_id") or ""))

    # ---- 闸 5: 幂等（record_id 已存在 → 不重复 apply） ----
    before_ids = _existing_record_ids(model_store)
    if growth_id in before_ids:
        # 账本仍为 accepted/approved 说明上轮 apply 成功但回写失败 → 仅补回写
        if proposal_store is not None:
            proposal.status = "applied"
            em_applied = dict(getattr(proposal, "evaluator_meta", None) or {})
            em_applied["applied_by"] = actor
            em_applied["applied_at"] = _utc_now_iso()
            proposal.evaluator_meta = em_applied
            proposal_store.update(proposal)
        reason = "already_applied_record_id"
        _audit_consumed(audit, actor, proposal_id, "already_applied",
                        status, reason)
        return _finish("already_applied", reason, record_id=growth_id)

    # ---- 闸 6: 唯一合法 apply 入口 ----
    try:
        sm_proposal = _reconstruct_proposal(payload)
    except Exception as exc:  # noqa: BLE001
        reason = f"payload_invalid:{exc}"
        _audit_consumed(audit, actor, proposal_id, "rejected", status, reason)
        _audit_rejected(audit, actor, proposal_id, reason, proof)
        return _finish("rejected", reason)

    # 批准标记: 与 SelfModelApprovalQueue.approve() 同一语义
    sm_proposal.requires_approval = False

    # 不允许反向调用任何审批接口; 唯一副作用入口:
    updater.apply_proposal(sm_proposal)  # 吞异常 → 必须重读验证

    after_ids = _existing_record_ids(model_store)
    if growth_id not in after_ids:
        reason = "apply_unverified"
        _audit_consumed(audit, actor, proposal_id, "rejected", status, reason)
        _audit_rejected(audit, actor, proposal_id, reason, proof)
        return _finish("rejected", reason, record_id=growth_id)

    # ---- 回写 A-store: accepted/approved → applied ----
    if proposal_store is not None:
        proposal.status = "applied"
        em_applied = dict(getattr(proposal, "evaluator_meta", None) or {})
        em_applied["applied_by"] = actor
        em_applied["applied_at"] = _utc_now_iso()
        proposal.evaluator_meta = em_applied
        proposal_store.update(proposal)

    _audit_consumed(audit, actor, proposal_id, "applied", status, "applied_ok")
    _audit_applied(audit, actor, proposal, list(before_ids), list(after_ids),
                   growth_id, proof)
    return _finish("applied", "applied_ok", applied=True, record_id=growth_id)


# ============================================================
# 批量入口: 消费 A-store 中所有已审批 SelfModel 提案
# ============================================================
def consume_accepted_self_model_proposals(
    *,
    proposal_store: Any,
    updater: Any,
    model_store: Any,
    actor: str = BRIDGE_ACTOR,
    audit: Any = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """扫描 A-store 中 accepted/approved 的 self_model 提案并逐个消费。

    只读扫描 + 逐提案消费（fail-soft: 单提案异常隔离在 consume 内部）。
    不修改任何审批状态（不 accept/approve/reject）。
    """
    audit = audit if audit is not None else _JsonlAuditWriter(DEFAULT_BRIDGE_AUDIT_PATH)

    candidates: List[Any] = []
    seen_ids: set = set()
    for status in ACCEPTED_STATES:
        for proposal in proposal_store.list(status=status, limit=limit):
            pid = str(getattr(proposal, "id", "") or "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                candidates.append(proposal)

    details: List[Dict[str, Any]] = []
    summary = {
        "processed": 0, "applied": 0, "already_applied": 0,
        "blocked": 0, "denied": 0, "rejected": 0, "skipped": 0,
        "details": details,
    }

    for proposal in candidates:
        em = dict(getattr(proposal, "evaluator_meta", None) or {})
        if em.get("proposal_type") != "self_model":
            summary["skipped"] += 1
            details.append({
                "proposal_id": getattr(proposal, "id", ""),
                "status": "skipped", "reason": "type_filter",
            })
            continue
        summary["processed"] += 1
        result = consume_self_model_proposal(
            proposal,
            updater=updater,
            model_store=model_store,
            proposal_store=proposal_store,
            actor=actor,
            audit=audit,
        )
        outcome = result.get("status", "")
        if outcome in summary:
            summary[outcome] += 1
        details.append(result)

    return summary


__all__ = [
    "BRIDGE_ACTOR",
    "ACCEPTED_STATES",
    "AUDIT_REASON_CONSUMED",
    "AUDIT_REASON_APPLIED",
    "AUDIT_REASON_REJECTED",
    "DEFAULT_BRIDGE_AUDIT_PATH",
    "extract_approval_proof",
    "consume_self_model_proposal",
    "consume_accepted_self_model_proposals",
]
