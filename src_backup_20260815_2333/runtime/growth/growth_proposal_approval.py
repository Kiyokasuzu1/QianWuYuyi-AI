# -*- coding: utf-8 -*-
"""
src/runtime/growth/growth_proposal_approval.py

Phase C.8.3 Growth Proposal Approval Workflow —— GrowthProposalApprovalWorkflow

=========================
目标
=========================
建立 Proposal 最终批准流程管理(Workflow 层)。

负责:
  - approve(proposal_id, approver):       approved → ready_for_apply
  - reject(proposal_id, reason, rejector): reviewing → rejected
  - revoke_approval(...):                  ready_for_apply → approved(重新审核)

不负责:
  - 人格修改
  - SelfModel 更新
  - Trait 修改
  - 自动 apply
  - Proposal 内容修改(只改 lifecycle state)

=========================
权限模型
=========================
允许: human / admin / system
禁止: anonymous / 其他
默认 actor: human

revoke_approval 权限更严(需要 admin)。

=========================
状态前置条件
=========================
approve:       当前状态必须 == "approved"
reject:        当前状态必须 == "reviewing"
revoke_approval: 当前状态必须 == "ready_for_apply"

=========================
审计
=========================
每次操作记录:
  - runtime_growth_proposal_approved
  - runtime_growth_proposal_rejected
  - runtime_growth_proposal_revoked

字段:
  proposal_id, action, old_state, new_state, actor, timestamp, reason

=========================
硬约束
=========================
禁止调用:
  - PersonalityResolver.resolve()
  - PersonalityAdapter.apply_proposal()
  - TraitStateUpdater.apply()
  - SelfModelStore.update()
  - ProposalManager.apply_proposal() / accept_proposal() / reject_proposal()
  - 任何人格系统

=========================
异常处理
=========================
所有方法永不抛异常,任何异常 → 返回:
  {
    "success": false,
    "degraded": true,
    "error": "..."
  }
Runtime cycle 不崩溃。
"""
from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION = "1.0"
GROWTH_PROPOSAL_APPROVAL_NAME = "growth_proposal_approval"
GROWTH_PROPOSAL_APPROVAL_VERSION = "1.0.0"

# 状态常量(从 lifecycle 复用)
STATE_PENDING = "pending"
STATE_REVIEWING = "reviewing"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_NEEDS_REVIEW = "needs_review"
STATE_READY_FOR_APPLY = "ready_for_apply"

# Audit action
AUDIT_ACTION_APPROVED = "runtime_growth_proposal_approved"
AUDIT_ACTION_REJECTED = "runtime_growth_proposal_rejected"
AUDIT_ACTION_REVOKED = "runtime_growth_proposal_revoked"
AUDIT_COMPONENT = "runtime_growth"

# 权限
ACTOR_HUMAN = "human"
ACTOR_ADMIN = "admin"
ACTOR_SYSTEM = "system"
ALLOWED_ACTORS: FrozenSet[str] = frozenset({ACTOR_HUMAN, ACTOR_ADMIN, ACTOR_SYSTEM})
DEFAULT_ACTOR = ACTOR_HUMAN

# revoke 需要更高权限
REVOKE_REQUIRED_ACTORS: FrozenSet[str] = frozenset({ACTOR_ADMIN, ACTOR_SYSTEM})

# 错误原因
REASON_UNAUTHORIZED = "unauthorized"
REASON_INVALID_STATE = "invalid_state"
REASON_PROPOSAL_NOT_FOUND = "proposal_not_found"
REASON_ALREADY_APPROVED = "already_approved"
REASON_ALREADY_REJECTED = "already_rejected"
REASON_ALREADY_REVOKED = "already_revoked"
REASON_NOT_APPROVED = "not_approved"
REASON_NOT_REVIEWING = "not_reviewing"
REASON_NOT_READY_FOR_REVOKE = "not_ready_for_revoke"
REASON_INVALID_INPUT = "invalid_input"
REASON_STORE_ERROR = "store_error"
REASON_AUDIT_ERROR = "audit_error"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"


# ============================================================
# 时间工具
# ============================================================


def _now_iso() -> str:
    """ISO 8601 UTC timestamp(fail-soft)"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return "1970-01-01T00:00:00Z"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        return bool(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return default
    except Exception:  # noqa: BLE001
        return default


def _safe_deepcopy(v: Any) -> Any:
    try:
        return copy.deepcopy(v)
    except Exception:  # noqa: BLE001
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# 权限工具
# ============================================================


def is_authorized(actor: str) -> bool:
    """判断 actor 是否在允许列表中。"""
    return str(actor or "") in ALLOWED_ACTORS


def is_revoke_authorized(actor: str) -> bool:
    """判断 actor 是否允许 revoke(更高权限)。"""
    return str(actor or "") in REVOKE_REQUIRED_ACTORS


# ============================================================
# Audit 工具
# ============================================================


def _record_audit_safely(
    audit: Any,
    action: str,
    proposal_id: str,
    old_state: str,
    new_state: str,
    actor: str,
    timestamp: str,
    reason: Optional[str] = None,
) -> bool:
    """安全地记录 audit(任何异常都吸收)。"""
    try:
        if audit is None:
            return False
        detail: Dict[str, Any] = {
            "proposal_id": str(proposal_id or ""),
            "action": str(action or ""),
            "old_state": str(old_state or ""),
            "new_state": str(new_state or ""),
            "actor": str(actor or DEFAULT_ACTOR),
            "timestamp": str(timestamp or _now_iso()),
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
        }
        if reason:
            detail["reason"] = str(reason)

        # 优先 record()
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=detail,
                    result="success",
                )
                return True
            except Exception:  # noqa: BLE001
                pass
        # fallback:save_audit_record
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=detail,
                    result="success",
                )
                audit.save_audit_record(rec)
                return True
            except Exception:  # noqa: BLE001
                pass
        # fallback:record_audit_log
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=detail,
                    result="success",
                )
                return True
            except Exception:  # noqa: BLE001
                pass
        return False
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# ProposalStore 工具
# ============================================================


def _read_proposal_status(store: Any, proposal_id: str) -> Optional[str]:
    """从 ProposalStore 读取 proposal 的 status(只读)。"""
    if store is None:
        return None
    pid = str(proposal_id or "")
    if not pid:
        return None
    # 1) store.load
    try:
        load_fn = getattr(store, "load", None)
        if callable(load_fn):
            try:
                p = load_fn(pid)
                if p is None:
                    return None
                if isinstance(p, dict):
                    s = _safe_str(_safe_get(p, "status", ""), "")
                    if s:
                        return s
                else:
                    s = getattr(p, "status", None)
                    if s:
                        return str(s)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # 2) store.get
    try:
        get_fn = getattr(store, "get", None)
        if callable(get_fn):
            try:
                p = get_fn(pid)
                if isinstance(p, dict):
                    s = _safe_str(_safe_get(p, "status", ""), "")
                    if s:
                        return s
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    # 3) fallback:遍历 _index
    try:
        index = getattr(store, "_index", None)
        if isinstance(index, dict):
            p = index.get(pid)
            if isinstance(p, dict):
                s = _safe_str(_safe_get(p, "status", ""), "")
                if s:
                    return s
    except Exception:  # noqa: BLE001
        pass
    return None


def _write_proposal_status(
    store: Any,
    proposal_id: str,
    new_status: str,
    actor: str,
    timestamp: str,
    reason: Optional[str] = None,
) -> bool:
    """向 ProposalStore 写入 proposal 的新 status(单一写入口,带 exception 透传)。

    用于 revoke_approval 这种绕过 lifecycle TRANSITIONS 的场景。
    行为:
      - store.update 抛异常 → 不回落,直接返回 False
    """
    if store is None:
        return False
    pid = str(proposal_id or "")
    if not pid:
        return False
    # 1) 优先 store.update(GrowthProposal / dict)
    update_fn = getattr(store, "update", None)
    if callable(update_fn):
        try:
            load_fn = getattr(store, "load", None)
            original = None
            if callable(load_fn):
                try:
                    original = load_fn(pid)
                except Exception:  # noqa: BLE001
                    original = None
            if original is not None:
                if isinstance(original, dict):
                    updated = dict(original)
                    updated["status"] = str(new_status)
                    updated["approval_actor"] = str(actor or DEFAULT_ACTOR)
                    updated["approval_updated_at"] = str(timestamp or _now_iso())
                    if reason:
                        updated["approval_reason"] = str(reason)
                    update_fn(updated)
                    return True
                else:
                    try:
                        if hasattr(original, "status"):
                            setattr(original, "status", str(new_status))
                        if hasattr(original, "approval_actor"):
                            setattr(original, "approval_actor", str(actor or DEFAULT_ACTOR))
                        if hasattr(original, "approval_updated_at"):
                            setattr(original, "approval_updated_at", str(timestamp or _now_iso()))
                        update_fn(original)
                        return True
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            return False
    # 2) fallback:仅在 store 没有 update() 时,直接更新 _index
    try:
        index = getattr(store, "_index", None)
        if isinstance(index, dict) and pid in index:
            p = index[pid]
            if isinstance(p, dict):
                p["status"] = str(new_status)
                p["approval_actor"] = str(actor or DEFAULT_ACTOR)
                p["approval_updated_at"] = str(timestamp or _now_iso())
                if reason:
                    p["approval_reason"] = str(reason)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ============================================================
# GrowthProposalApprovalWorkflow
# ============================================================


class GrowthProposalApprovalWorkflow:
    """
    Growth Proposal 正式批准流程管理器 (Phase C.8.3 / v1.0)

    严格 Workflow 层,只管理:
      - approve(proposal_id, approver):          approved → ready_for_apply
      - reject(proposal_id, reason, rejector):   reviewing → rejected
      - revoke_approval(...):                    ready_for_apply → approved

    严禁:
      - 触发任何 apply / accept 到人格层
      - 修改 Personality / SelfModel / Trait
      - 修改 Proposal 内容(只改 status 字段)
      - 任何 anonymous 操作

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "success": bool,
        "degraded": bool,
        "proposal_id": str,
        "action": "approved" | "rejected" | "revoked",
        "previous_state": str,
        "new_state": str,
        "approver": str,                # actor(approve / reject)
        "reject_reason": Optional[str],
        "reason": Optional[str],
        "approved": bool,               # 仅 approve 返回
        "error": Optional[str],
        "timestamp": str,
        "audit_recorded": bool,
        "approval": {
          "name": "growth_proposal_approval",
          "version": "1.0.0",
          "schema_version": "1.0",
        }
      }
    """

    SCHEMA_VERSION = GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION
    NAME = GROWTH_PROPOSAL_APPROVAL_NAME
    VERSION = GROWTH_PROPOSAL_APPROVAL_VERSION

    def __init__(
        self,
        proposal_store: Any = None,
        lifecycle: Any = None,
        audit: Any = None,
        default_approver: str = DEFAULT_ACTOR,
    ) -> None:
        """
        Args:
            proposal_store: ProposalStore 实例
            lifecycle: GrowthProposalLifecycleManager 实例(用于 approve/reject)
                       如果为 None,workflow 会创建自己的 lifecycle(如果 store 不为 None)
            audit: Audit 写入对象(可选)
            default_approver: 默认 approver(仅当 caller 未传时生效)
        """
        self._store = proposal_store
        self._audit = audit
        self._default_approver = str(default_approver or DEFAULT_ACTOR)
        self._lock = threading.RLock()

        # 优先使用外部 lifecycle,否则在有 store 的情况下创建一个内部 lifecycle
        if lifecycle is not None:
            self._lifecycle = lifecycle
        elif proposal_store is not None:
            try:
                from .growth_proposal_lifecycle import GrowthProposalLifecycleManager
                self._lifecycle = GrowthProposalLifecycleManager(
                    proposal_store=proposal_store,
                    audit=audit,
                )
            except Exception:  # noqa: BLE001
                self._lifecycle = None
        else:
            self._lifecycle = None

        # 内部统计
        self._approve_count: int = 0
        self._reject_count: int = 0
        self._revoke_count: int = 0
        self._unauthorized_count: int = 0
        self._error_count: int = 0
        self._audit_recorded_count: int = 0
        self._last_error: Optional[str] = None
        self._last_action_ts: Optional[str] = None
        self._history: List[Dict[str, Any]] = []
        self._max_history: int = 50

    # --------------------------------------------------------
    # 依赖注入
    # --------------------------------------------------------

    def set_store(self, store: Any) -> None:
        with self._lock:
            self._store = store
            # 如果 lifecycle 之前是 None,重新创建
            if self._lifecycle is None and store is not None:
                try:
                    from .growth_proposal_lifecycle import GrowthProposalLifecycleManager
                    self._lifecycle = GrowthProposalLifecycleManager(
                        proposal_store=store,
                        audit=self._audit,
                    )
                except Exception:  # noqa: BLE001
                    pass

    def set_audit(self, audit: Any) -> None:
        with self._lock:
            self._audit = audit
            if self._lifecycle is not None and hasattr(self._lifecycle, "set_audit"):
                try:
                    self._lifecycle.set_audit(audit)
                except Exception:  # noqa: BLE001
                    pass

    def set_lifecycle(self, lifecycle: Any) -> None:
        with self._lock:
            self._lifecycle = lifecycle

    def attach(
        self,
        store: Any = None,
        audit: Any = None,
        lifecycle: Any = None,
    ) -> bool:
        with self._lock:
            if store is not None:
                self._store = store
            if audit is not None:
                self._audit = audit
            if lifecycle is not None:
                self._lifecycle = lifecycle
        return True

    # --------------------------------------------------------
    # 1) approve
    # --------------------------------------------------------

    def approve(
        self,
        proposal_id: str,
        approver: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        批准 Proposal。
        规则:
          - 当前状态必须 == "approved"
          - 转换: approved → ready_for_apply
          - 权限: human / admin / system(默认 human)

        永不抛异常。
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                act = _safe_str(approver, self._default_approver) or self._default_approver
                ts = _now_iso()

                # 1) 输入校验
                if not pid:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        action="approved",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error="proposal_id_empty",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_INVALID_INPUT,
                    )

                # 2) 权限检查
                if not is_authorized(act):
                    self._unauthorized_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="approved",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error=f"unauthorized: {act}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_UNAUTHORIZED,
                    )

                # 3) 读取当前状态
                current_state = self._read_state(pid)
                if current_state is None:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="approved",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error=f"proposal_not_found: {pid}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_PROPOSAL_NOT_FOUND,
                    )

                # 4) 状态前置检查
                if current_state != STATE_APPROVED:
                    # 特定错误
                    if current_state == STATE_READY_FOR_APPLY:
                        err_reason = REASON_ALREADY_APPROVED
                    elif current_state == STATE_REJECTED:
                        err_reason = REASON_ALREADY_REJECTED
                    else:
                        err_reason = REASON_NOT_APPROVED
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="approved",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error=f"invalid_state: {current_state} (need 'approved')",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=err_reason,
                    )

                # 5) 直接写 store(workflow 自管理 state 转换,避免 lifecycle cache 失效问题)
                try:
                    write_ok = _write_proposal_status(
                        self._store,
                        pid,
                        STATE_READY_FOR_APPLY,
                        act,
                        ts,
                        reason or "approve",
                    )
                except Exception as exc:  # noqa: BLE001
                    self._last_error = repr(exc)
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="approved",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error=f"store_write_failed: {repr(exc)}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                if not write_ok:
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="approved",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=reason,
                        approved=False,
                        error="store_write_failed",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                # 6) 记录 audit
                audit_ok = _record_audit_safely(
                    audit=self._audit,
                    action=AUDIT_ACTION_APPROVED,
                    proposal_id=pid,
                    old_state=current_state,
                    new_state=STATE_READY_FOR_APPLY,
                    actor=act,
                    timestamp=ts,
                    reason=reason,
                )
                if audit_ok:
                    self._audit_recorded_count += 1

                # 7) 更新统计
                self._approve_count += 1
                self._last_action_ts = ts
                self._last_error = None
                self._push_history({
                    "action": "approved",
                    "proposal_id": pid,
                    "old_state": current_state,
                    "new_state": STATE_READY_FOR_APPLY,
                    "actor": act,
                    "reason": reason,
                    "timestamp": ts,
                    "audit_recorded": bool(audit_ok),
                })

                # 8) 成功
                return self._build_report(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    action="approved",
                    previous_state=current_state,
                    new_state=STATE_READY_FOR_APPLY,
                    approver=act,
                    reject_reason=None,
                    reason=reason,
                    approved=True,
                    error=None,
                    audit_recorded=audit_ok,
                    timestamp=ts,
                    error_reason=None,
                )

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_3] approve 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                action="approved",
                previous_state="",
                new_state="",
                approver=_safe_str(approver, self._default_approver),
                reject_reason=None,
                reason=reason,
                approved=False,
                error=repr(exc),
                audit_recorded=False,
                timestamp=_now_iso(),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # 2) reject
    # --------------------------------------------------------

    def reject(
        self,
        proposal_id: str,
        reason: str,
        rejector: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        拒绝 Proposal。
        规则:
          - 当前状态必须 == "reviewing"
          - 转换: reviewing → rejected
          - 权限: human / admin / system(默认 human)
          - reason 必须非空(用于 audit)

        永不抛异常。
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                act = _safe_str(rejector, self._default_approver) or self._default_approver
                rsn = _safe_str(reason, "")
                ts = _now_iso()

                # 1) 输入校验
                if not pid:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        action="rejected",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error="proposal_id_empty",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_INVALID_INPUT,
                    )
                if not rsn:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="rejected",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error="reason_empty",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_INVALID_INPUT,
                    )

                # 2) 权限检查
                if not is_authorized(act):
                    self._unauthorized_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="rejected",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error=f"unauthorized: {act}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_UNAUTHORIZED,
                    )

                # 3) 读取当前状态
                current_state = self._read_state(pid)
                if current_state is None:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="rejected",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error=f"proposal_not_found: {pid}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_PROPOSAL_NOT_FOUND,
                    )

                # 4) 状态前置检查
                if current_state != STATE_REVIEWING:
                    err_reason = REASON_NOT_REVIEWING
                    if current_state == STATE_REJECTED:
                        err_reason = REASON_ALREADY_REJECTED
                    elif current_state == STATE_READY_FOR_APPLY:
                        err_reason = REASON_ALREADY_APPROVED
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="rejected",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error=f"invalid_state: {current_state} (need 'reviewing')",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=err_reason,
                    )

                # 5) 直接写 store(workflow 自管理 state 转换)
                try:
                    write_ok = _write_proposal_status(
                        self._store,
                        pid,
                        STATE_REJECTED,
                        act,
                        ts,
                        rsn,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._last_error = repr(exc)
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="rejected",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error=f"store_write_failed: {repr(exc)}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                if not write_ok:
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="rejected",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=rsn,
                        reason=rsn,
                        approved=False,
                        error="store_write_failed",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                # 6) 记录 audit
                audit_ok = _record_audit_safely(
                    audit=self._audit,
                    action=AUDIT_ACTION_REJECTED,
                    proposal_id=pid,
                    old_state=current_state,
                    new_state=STATE_REJECTED,
                    actor=act,
                    timestamp=ts,
                    reason=rsn,
                )
                if audit_ok:
                    self._audit_recorded_count += 1

                # 7) 更新统计
                self._reject_count += 1
                self._last_action_ts = ts
                self._last_error = None
                self._push_history({
                    "action": "rejected",
                    "proposal_id": pid,
                    "old_state": current_state,
                    "new_state": STATE_REJECTED,
                    "actor": act,
                    "reason": rsn,
                    "timestamp": ts,
                    "audit_recorded": bool(audit_ok),
                })

                # 8) 成功
                return self._build_report(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    action="rejected",
                    previous_state=current_state,
                    new_state=STATE_REJECTED,
                    approver=act,
                    reject_reason=rsn,
                    reason=rsn,
                    approved=False,
                    error=None,
                    audit_recorded=audit_ok,
                    timestamp=ts,
                    error_reason=None,
                )

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_3] reject 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                action="rejected",
                previous_state="",
                new_state="",
                approver=_safe_str(rejector, self._default_approver),
                reject_reason=_safe_str(reason, ""),
                reason=_safe_str(reason, ""),
                approved=False,
                error=repr(exc),
                audit_recorded=False,
                timestamp=_now_iso(),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # 3) revoke_approval
    # --------------------------------------------------------

    def revoke_approval(
        self,
        proposal_id: str,
        revoker: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        撤销已 ready_for_apply 的批准(回到 approved 重新审核)。
        规则:
          - 当前状态必须 == "ready_for_apply"
          - 转换: ready_for_apply → approved
          - 权限: admin / system(revoke 是高影响操作)
          - 不直接修改人格,只改 lifecycle state

        永不抛异常。
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                act = _safe_str(revoker, self._default_approver) or self._default_approver
                rsn = _safe_str(reason, "revoke_approval")
                ts = _now_iso()

                # 1) 输入校验
                if not pid:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        action="revoked",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error="proposal_id_empty",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_INVALID_INPUT,
                    )

                # 2) 权限检查(revoke 更高权限要求)
                if not is_revoke_authorized(act):
                    self._unauthorized_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="revoked",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error=f"unauthorized: {act} (revoke requires admin/system)",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_UNAUTHORIZED,
                    )

                # 3) 读取当前状态
                current_state = self._read_state(pid)
                if current_state is None:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="revoked",
                        previous_state="",
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error=f"proposal_not_found: {pid}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_PROPOSAL_NOT_FOUND,
                    )

                # 4) 状态前置检查
                if current_state != STATE_READY_FOR_APPLY:
                    if current_state == STATE_APPROVED:
                        err_reason = REASON_ALREADY_REVOKED
                    else:
                        err_reason = REASON_NOT_READY_FOR_REVOKE
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        action="revoked",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error=f"invalid_state: {current_state} (need 'ready_for_apply')",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=err_reason,
                    )

                # 5) 直接写 store(revoke 是 workflow 级别的合法操作,绕过 lifecycle TRANSITIONS)
                try:
                    write_ok = _write_proposal_status(
                        self._store,
                        pid,
                        STATE_APPROVED,
                        act,
                        ts,
                        rsn,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._last_error = repr(exc)
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="revoked",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error=f"store_write_failed: {repr(exc)}",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                if not write_ok:
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        action="revoked",
                        previous_state=current_state,
                        new_state="",
                        approver=act,
                        reject_reason=None,
                        reason=rsn,
                        approved=False,
                        error="store_write_failed",
                        audit_recorded=False,
                        timestamp=ts,
                        error_reason=REASON_STORE_ERROR,
                    )

                # 6) 记录 audit
                audit_ok = _record_audit_safely(
                    audit=self._audit,
                    action=AUDIT_ACTION_REVOKED,
                    proposal_id=pid,
                    old_state=current_state,
                    new_state=STATE_APPROVED,
                    actor=act,
                    timestamp=ts,
                    reason=rsn,
                )
                if audit_ok:
                    self._audit_recorded_count += 1

                # 7) 更新统计
                self._revoke_count += 1
                self._last_action_ts = ts
                self._last_error = None
                self._push_history({
                    "action": "revoked",
                    "proposal_id": pid,
                    "old_state": current_state,
                    "new_state": STATE_APPROVED,
                    "actor": act,
                    "reason": rsn,
                    "timestamp": ts,
                    "audit_recorded": bool(audit_ok),
                })

                # 8) 成功
                return self._build_report(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    action="revoked",
                    previous_state=current_state,
                    new_state=STATE_APPROVED,
                    approver=act,
                    reject_reason=None,
                    reason=rsn,
                    approved=False,
                    error=None,
                    audit_recorded=audit_ok,
                    timestamp=ts,
                    error_reason=None,
                )

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_3] revoke_approval 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                action="revoked",
                previous_state="",
                new_state="",
                approver=_safe_str(revoker, self._default_approver),
                reject_reason=None,
                reason=_safe_str(reason, ""),
                approved=False,
                error=repr(exc),
                audit_recorded=False,
                timestamp=_now_iso(),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # 只读访问
    # --------------------------------------------------------

    def get_state(self, proposal_id: str) -> Optional[str]:
        """读取 proposal 当前状态(只读)。"""
        try:
            return self._read_state(proposal_id)
        except Exception:  # noqa: BLE001
            return None

    def get_history(
        self,
        proposal_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """读取本实例内的 action 历史(只读)。"""
        with self._lock:
            if proposal_id is None:
                return list(self._history[-limit:])
            return [
                h for h in self._history
                if h.get("proposal_id") == str(proposal_id)
            ][-limit:]

    def can_approve(self, proposal_id: str) -> bool:
        """判断 proposal 是否可以 approve(state == approved)。"""
        try:
            return self._read_state(proposal_id) == STATE_APPROVED
        except Exception:  # noqa: BLE001
            return False

    def can_reject(self, proposal_id: str) -> bool:
        """判断 proposal 是否可以 reject(state == reviewing)。"""
        try:
            return self._read_state(proposal_id) == STATE_REVIEWING
        except Exception:  # noqa: BLE001
            return False

    def can_revoke(self, proposal_id: str) -> bool:
        """判断 proposal 是否可以 revoke(state == ready_for_apply)。"""
        try:
            return self._read_state(proposal_id) == STATE_READY_FOR_APPLY
        except Exception:  # noqa: BLE001
            return False

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _read_state(self, proposal_id: str) -> Optional[str]:
        """读取 proposal 当前状态(从 store 直接读,绕开 lifecycle 缓存以保证一致性)。

        原因:
          - revoke_approval 会绕过 lifecycle TRANSITIONS 直接写 store
          - 如果依赖 lifecycle 的缓存,revoke 之后缓存会过时
          - 直接读 store 保证 approval workflow 看到一致状态
        """
        return _read_proposal_status(self._store, proposal_id)

    def _push_history(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._history.append(entry)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

    def _build_report(
        self,
        success: bool,
        degraded: bool,
        proposal_id: str,
        action: str,
        previous_state: str,
        new_state: str,
        approver: str,
        reject_reason: Optional[str],
        reason: Optional[str],
        approved: bool,
        error: Optional[str],
        audit_recorded: bool,
        timestamp: str,
        error_reason: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "proposal_id": str(proposal_id or ""),
            "action": str(action or ""),
            "previous_state": str(previous_state or ""),
            "new_state": str(new_state or ""),
            "approver": str(approver or self._default_approver),
            "reject_reason": reject_reason,
            "reason": reason,
            "approved": bool(approved),
            "error": error,
            "error_reason": error_reason,
            "audit_recorded": bool(audit_recorded),
            "timestamp": str(timestamp or _now_iso()),
            "approval": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    # --------------------------------------------------------
    # 统计
    # --------------------------------------------------------

    @property
    def approve_count(self) -> int:
        with self._lock:
            return int(self._approve_count)

    @property
    def reject_count(self) -> int:
        with self._lock:
            return int(self._reject_count)

    @property
    def revoke_count(self) -> int:
        with self._lock:
            return int(self._revoke_count)

    @property
    def unauthorized_count(self) -> int:
        with self._lock:
            return int(self._unauthorized_count)

    @property
    def error_count(self) -> int:
        with self._lock:
            return int(self._error_count)

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "approve_count": int(self._approve_count),
                "reject_count": int(self._reject_count),
                "revoke_count": int(self._revoke_count),
                "unauthorized_count": int(self._unauthorized_count),
                "error_count": int(self._error_count),
                "audit_recorded_count": int(self._audit_recorded_count),
                "last_error": self._last_error,
                "last_action_ts": self._last_action_ts,
                "history_size": len(self._history),
                "has_store": self._store is not None,
                "has_audit": self._audit is not None,
                "has_lifecycle": self._lifecycle is not None,
            }

    def get_workflow_info(self) -> Dict[str, Any]:
        """导出 workflow 元信息(用于诊断)。"""
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "schema_version": self.SCHEMA_VERSION,
            "allowed_actors": sorted(ALLOWED_ACTORS),
            "revoke_required_actors": sorted(REVOKE_REQUIRED_ACTORS),
            "default_approver": self._default_approver,
            "actions": ["approve", "reject", "revoke_approval"],
            "audit_actions": [
                AUDIT_ACTION_APPROVED,
                AUDIT_ACTION_REJECTED,
                AUDIT_ACTION_REVOKED,
            ],
        }


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_growth_proposal_approval(
    proposal_store: Any = None,
    lifecycle: Any = None,
    audit: Any = None,
    default_approver: str = DEFAULT_ACTOR,
) -> GrowthProposalApprovalWorkflow:
    """工厂函数:创建一个 GrowthProposalApprovalWorkflow。"""
    return GrowthProposalApprovalWorkflow(
        proposal_store=proposal_store,
        lifecycle=lifecycle,
        audit=audit,
        default_approver=default_approver,
    )


def safe_approve(
    workflow: Optional[GrowthProposalApprovalWorkflow],
    proposal_id: str,
    approver: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """全局安全 approve(任何异常都吸收)"""
    if workflow is None:
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "approved",
            "previous_state": "",
            "new_state": "",
            "approver": str(approver or DEFAULT_ACTOR),
            "reject_reason": None,
            "reason": reason,
            "approved": False,
            "error": "workflow_none",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }
    try:
        return workflow.approve(proposal_id, approver=approver, reason=reason) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "approved",
            "previous_state": "",
            "new_state": "",
            "approver": str(approver or DEFAULT_ACTOR),
            "reject_reason": None,
            "reason": reason,
            "approved": False,
            "error": "safe_approve_exception",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }


def safe_reject(
    workflow: Optional[GrowthProposalApprovalWorkflow],
    proposal_id: str,
    reason: str,
    rejector: Optional[str] = None,
) -> Dict[str, Any]:
    """全局安全 reject(任何异常都吸收)"""
    if workflow is None:
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "rejected",
            "previous_state": "",
            "new_state": "",
            "approver": str(rejector or DEFAULT_ACTOR),
            "reject_reason": str(reason or ""),
            "reason": str(reason or ""),
            "approved": False,
            "error": "workflow_none",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }
    try:
        return workflow.reject(proposal_id, reason, rejector=rejector) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "rejected",
            "previous_state": "",
            "new_state": "",
            "approver": str(rejector or DEFAULT_ACTOR),
            "reject_reason": str(reason or ""),
            "reason": str(reason or ""),
            "approved": False,
            "error": "safe_reject_exception",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }


def safe_revoke(
    workflow: Optional[GrowthProposalApprovalWorkflow],
    proposal_id: str,
    revoker: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """全局安全 revoke_approval(任何异常都吸收)"""
    if workflow is None:
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "revoked",
            "previous_state": "",
            "new_state": "",
            "approver": str(revoker or DEFAULT_ACTOR),
            "reject_reason": None,
            "reason": reason,
            "approved": False,
            "error": "workflow_none",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }
    try:
        return workflow.revoke_approval(proposal_id, revoker=revoker, reason=reason) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "action": "revoked",
            "previous_state": "",
            "new_state": "",
            "approver": str(revoker or DEFAULT_ACTOR),
            "reject_reason": None,
            "reason": reason,
            "approved": False,
            "error": "safe_revoke_exception",
            "error_reason": REASON_DEGRADED,
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "approval": {
                "name": GROWTH_PROPOSAL_APPROVAL_NAME,
                "version": GROWTH_PROPOSAL_APPROVAL_VERSION,
                "schema_version": GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
            },
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION",
    "GROWTH_PROPOSAL_APPROVAL_NAME",
    "GROWTH_PROPOSAL_APPROVAL_VERSION",
    "STATE_PENDING",
    "STATE_REVIEWING",
    "STATE_APPROVED",
    "STATE_REJECTED",
    "STATE_NEEDS_REVIEW",
    "STATE_READY_FOR_APPLY",
    "AUDIT_ACTION_APPROVED",
    "AUDIT_ACTION_REJECTED",
    "AUDIT_ACTION_REVOKED",
    "AUDIT_COMPONENT",
    "ACTOR_HUMAN",
    "ACTOR_ADMIN",
    "ACTOR_SYSTEM",
    "ALLOWED_ACTORS",
    "REVOKE_REQUIRED_ACTORS",
    "DEFAULT_ACTOR",
    "REASON_UNAUTHORIZED",
    "REASON_INVALID_STATE",
    "REASON_PROPOSAL_NOT_FOUND",
    "REASON_ALREADY_APPROVED",
    "REASON_ALREADY_REJECTED",
    "REASON_ALREADY_REVOKED",
    "REASON_NOT_APPROVED",
    "REASON_NOT_REVIEWING",
    "REASON_NOT_READY_FOR_REVOKE",
    "REASON_INVALID_INPUT",
    "REASON_STORE_ERROR",
    "REASON_AUDIT_ERROR",
    "REASON_INTERNAL_ERROR",
    "REASON_DEGRADED",
    "is_authorized",
    "is_revoke_authorized",
    "GrowthProposalApprovalWorkflow",
    "create_growth_proposal_approval",
    "safe_approve",
    "safe_reject",
    "safe_revoke",
]
