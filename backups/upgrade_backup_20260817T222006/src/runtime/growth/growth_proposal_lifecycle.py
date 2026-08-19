# -*- coding: utf-8 -*-
"""
src/runtime/growth/growth_proposal_lifecycle.py

Phase C.8.2 Growth Proposal Lifecycle State Machine —— GrowthProposalLifecycleManager

=========================
目标
=========================
建立 Proposal 状态机的运行时管理层。

负责:
  - Proposal 状态转换
  - Audit 记录(每次 transition)
  - 重复保护(同状态不再 transition)
  - 并发安全(thread-safe)

不负责:
  - 人格修改
  - SelfModel 更新
  - Trait 修改
  - 自动 apply / accept

=========================
状态机(冻结)
=========================
  pending → reviewing
  pending → rejected        (允许从 pending 直接 reject)
  reviewing → approved
  reviewing → rejected
  reviewing → needs_review
  approved → ready_for_apply

终态(不可再 transition):
  rejected
  needs_review       (回到 reviewing 后才能再转换)
  ready_for_apply    (由外部 C.8.5+ 接管)

=========================
硬约束
=========================
禁止调用:
  - PersonalityResolver.resolve()
  - PersonalityAdapter.apply_proposal()
  - TraitStateUpdater.apply()
  - SelfModelStore.update()
  - ProposalManager.apply_proposal() / accept_proposal() / reject_proposal()

禁止 transition:
  - pending → applied
  - rejected → approved
  - applied → pending
  - 任何 backward transition(终态 → 其他)

=========================
异常处理
=========================
transition() 永不抛异常。
任何异常 → 返回:
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

GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION = "1.0"
GROWTH_PROPOSAL_LIFECYCLE_NAME = "growth_proposal_lifecycle"
GROWTH_PROPOSAL_LIFECYCLE_VERSION = "1.0.0"

# 状态常量(冻结)
STATE_PENDING = "pending"
STATE_REVIEWING = "reviewing"
STATE_APPROVED = "approved"
STATE_REJECTED = "rejected"
STATE_NEEDS_REVIEW = "needs_review"
STATE_READY_FOR_APPLY = "ready_for_apply"

# 全部合法状态
ALL_STATES: FrozenSet[str] = frozenset({
    STATE_PENDING,
    STATE_REVIEWING,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_NEEDS_REVIEW,
    STATE_READY_FOR_APPLY,
})

# 状态转换表(冻结,from_state -> set(to_state))
TRANSITIONS: Dict[str, FrozenSet[str]] = {
    STATE_PENDING: frozenset({STATE_REVIEWING, STATE_REJECTED}),
    STATE_REVIEWING: frozenset({STATE_APPROVED, STATE_REJECTED, STATE_NEEDS_REVIEW}),
    STATE_APPROVED: frozenset({STATE_READY_FOR_APPLY}),
    STATE_REJECTED: frozenset(),  # 终态
    STATE_NEEDS_REVIEW: frozenset({STATE_REVIEWING}),  # 只能回到 reviewing
    STATE_READY_FOR_APPLY: frozenset(),  # 终态(由 C.8.5+ 接管)
}

# 终态(不可再 transition)
TERMINAL_STATES: FrozenSet[str] = frozenset({
    STATE_REJECTED,
    STATE_READY_FOR_APPLY,
})

# 初始状态(新建 proposal 的默认状态)
INITIAL_STATE = STATE_PENDING

# Audit
AUDIT_ACTION_TRANSITION = "runtime_growth_proposal_transition"
AUDIT_COMPONENT = "runtime_growth"
AUDIT_ACTOR_RUNTIME = "runtime"
AUDIT_ACTOR_DEFAULT = AUDIT_ACTOR_RUNTIME

# 错误原因
REASON_ALREADY_IN_STATE = "already_in_state"
REASON_INVALID_TRANSITION = "invalid_transition"
REASON_PROPOSAL_NOT_FOUND = "proposal_not_found"
REASON_INVALID_STATE = "invalid_state"
REASON_STORE_ERROR = "store_error"
REASON_AUDIT_ERROR = "audit_error"
REASON_INVALID_INPUT = "invalid_input"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"

# 禁止调用的方法(防误用)
FORBIDDEN_PROPOSAL_MANAGER_METHODS = frozenset({
    "accept_proposal",
    "reject_proposal",
    "apply_proposal",
})


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


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return default
    except Exception:  # noqa: BLE001
        return default


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


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:  # noqa: BLE001
        return []


def _safe_deepcopy(v: Any) -> Any:
    """深拷贝(任何异常都返回原值或空容器)。"""
    try:
        return copy.deepcopy(v)
    except Exception:  # noqa: BLE001
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# 状态机验证
# ============================================================


def is_valid_state(state: str) -> bool:
    """判断 state 是否合法。"""
    return state in ALL_STATES


def is_terminal_state(state: str) -> bool:
    """判断 state 是否为终态。"""
    return state in TERMINAL_STATES


def validate_transition(from_state: str, to_state: str) -> bool:
    """验证状态转换是否合法(返回 bool,不抛异常)。

    规则:
      - from_state / to_state 必须合法
      - from_state == to_state 视为不合法(由 transition 内部处理为 already_in_state)
      - 终态不可再转出
      - 只能按 TRANSITIONS 表的边进行
    """
    if from_state not in ALL_STATES:
        return False
    if to_state not in ALL_STATES:
        return False
    if from_state == to_state:
        return False  # 视为 no-op,留给 transition 内部判断
    allowed = TRANSITIONS.get(from_state, frozenset())
    return to_state in allowed


def get_allowed_transitions(from_state: str) -> List[str]:
    """返回 from_state 的合法目标状态列表(冻结列表的 list 化)。"""
    if from_state not in ALL_STATES:
        return []
    return sorted(TRANSITIONS.get(from_state, frozenset()))


# ============================================================
# Audit 工具
# ============================================================


def _record_audit_safely(
    audit: Any,
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
            "old_state": str(old_state or ""),
            "new_state": str(new_state or ""),
            "actor": str(actor or AUDIT_ACTOR_DEFAULT),
            "timestamp": str(timestamp or _now_iso()),
            "schema_version": GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION,
        }
        if reason:
            detail["reason"] = str(reason)

        # 优先 record()
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=AUDIT_ACTION_TRANSITION,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_TRANSITION,
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
                    operation_type=AUDIT_ACTION_TRANSITION,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_TRANSITION,
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
                    operation_type=AUDIT_ACTION_TRANSITION,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_TRANSITION,
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
    """从 ProposalStore 读取 proposal 的 status(只读)。

    兼容多种接口:
      - store.load(proposal_id) -> GrowthProposal / dict
      - store.get(proposal_id) -> dict
      - store.list() / store.index
    """
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
    """向 ProposalStore 写入 proposal 的新 status(单一写入口)。

    优先 store.update(proposal_obj),否则:
      - 修改 _index 中的 dict 后 store.save()

    行为:
      - 如果 store.update 抛异常,**不**自动回落到 _index 兜底
        (否则会掩盖 store 错误,违反 FailSafe 契约)
      - 仅在 store 不存在 update() 接口时,才回落到 _index 兜底
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
            # 读取原 proposal(只读)
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
                    updated["lifecycle_actor"] = str(actor or AUDIT_ACTOR_DEFAULT)
                    updated["lifecycle_updated_at"] = str(timestamp or _now_iso())
                    if reason:
                        updated["lifecycle_reason"] = str(reason)
                    update_fn(updated)
                    return True
                else:
                    # dataclass-like
                    try:
                        if hasattr(original, "status"):
                            setattr(original, "status", str(new_status))
                        if hasattr(original, "lifecycle_actor"):
                            setattr(original, "lifecycle_actor", str(actor or AUDIT_ACTOR_DEFAULT))
                        if hasattr(original, "lifecycle_updated_at"):
                            setattr(original, "lifecycle_updated_at", str(timestamp or _now_iso()))
                        update_fn(original)
                        return True
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            # store.update 抛异常 → 不回落,直接返回 False
            return False
    # 2) fallback:仅在 store 没有 update() 时,直接更新 _index(不持久化,但保证本进程一致性)
    try:
        index = getattr(store, "_index", None)
        if isinstance(index, dict) and pid in index:
            p = index[pid]
            if isinstance(p, dict):
                p["status"] = str(new_status)
                p["lifecycle_actor"] = str(actor or AUDIT_ACTOR_DEFAULT)
                p["lifecycle_updated_at"] = str(timestamp or _now_iso())
                if reason:
                    p["lifecycle_reason"] = str(reason)
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ============================================================
# GrowthProposalLifecycleManager
# ============================================================


class GrowthProposalLifecycleManager:
    """
    Growth Proposal 生命周期状态机管理器 (Phase C.8.2 / v1.0)

    严格只管理 Proposal 状态:
      - transition(proposal_id, target_state, actor): 状态转换
      - get_state(proposal_id): 读取当前状态(只读)
      - get_allowed_transitions(proposal_id): 获取合法目标状态

    严禁:
      - 触发 apply / accept / reject(到人格层)
      - 修改 Personality / SelfModel / Trait
      - 修改 Proposal 内容(只改 status 字段)

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "success": bool,
        "degraded": bool,
        "proposal_id": str,
        "old_state": str,
        "new_state": str,
        "actual_state": str,            # 转换后实际状态
        "target_state": str,            # 请求的目标状态
        "actor": str,
        "reason": Optional[str],        # 失败原因
        "error": Optional[str],         # 顶层异常
        "audit_recorded": bool,
        "timestamp": str,
        "lifecycle": {
          "name": "growth_proposal_lifecycle",
          "version": "1.0.0",
          "schema_version": "1.0",
        }
      }
    """

    SCHEMA_VERSION = GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION
    NAME = GROWTH_PROPOSAL_LIFECYCLE_NAME
    VERSION = GROWTH_PROPOSAL_LIFECYCLE_VERSION

    def __init__(
        self,
        proposal_store: Any = None,
        audit: Any = None,
        default_actor: str = AUDIT_ACTOR_RUNTIME,
    ) -> None:
        """
        Args:
            proposal_store: ProposalStore 实例(可选;None 时仍可记录 audit)
            audit: Audit 写入对象(可选)
            default_actor: 默认 actor(仅当 caller 未传时生效)
        """
        self._store = proposal_store
        self._audit = audit
        self._default_actor = str(default_actor or AUDIT_ACTOR_RUNTIME)
        self._lock = threading.RLock()
        # 内部统计
        self._transition_count: int = 0
        self._success_count: int = 0
        self._invalid_count: int = 0
        self._error_count: int = 0
        self._audit_recorded_count: int = 0
        self._last_error: Optional[str] = None
        self._last_transition_ts: Optional[str] = None
        # 内部 transition 历史(只读,最近 50 条)
        self._history: List[Dict[str, Any]] = []
        self._max_history: int = 50
        # 内部状态缓存(proposal_id -> state)以减少对 store 的访问
        # 这是一个性能优化,**不作为唯一事实来源**(写仍以 store 为准)
        self._state_cache: Dict[str, str] = {}

    # --------------------------------------------------------
    # 依赖注入
    # --------------------------------------------------------

    def set_store(self, store: Any) -> None:
        with self._lock:
            self._store = store

    def set_audit(self, audit: Any) -> None:
        with self._lock:
            self._audit = audit

    def attach(self, store: Any = None, audit: Any = None) -> bool:
        with self._lock:
            if store is not None:
                self._store = store
            if audit is not None:
                self._audit = audit
        return True

    # --------------------------------------------------------
    # 主入口:transition
    # --------------------------------------------------------

    def transition(
        self,
        proposal_id: str,
        target_state: str,
        actor: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行一次状态转换(永不抛异常)。

        Args:
            proposal_id: proposal ID
            target_state: 目标状态
            actor: 操作者(默认 "runtime")
            reason: 可选原因

        Returns:
            dict(见 Schema 契约)
        """
        try:
            with self._lock:
                self._transition_count += 1
                pid = _safe_str(proposal_id, "")
                tgt = _safe_str(target_state, "")
                act = _safe_str(actor, self._default_actor) or self._default_actor
                ts = _now_iso()

                # 1) 输入校验
                if not pid:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        old_state="",
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_INVALID_INPUT,
                        error="proposal_id_empty",
                        audit_recorded=False,
                        timestamp=ts,
                    )
                if not tgt:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state="",
                        new_state="",
                        target_state="",
                        actor=act,
                        reason=REASON_INVALID_INPUT,
                        error="target_state_empty",
                        audit_recorded=False,
                        timestamp=ts,
                    )
                if tgt not in ALL_STATES:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state="",
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_INVALID_STATE,
                        error=f"unknown_state: {tgt}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 2) 读取当前状态
                try:
                    current_state = self._read_state(pid)
                except Exception as exc:  # noqa: BLE001
                    self._last_error = repr(exc)
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        old_state="",
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_STORE_ERROR,
                        error=f"read_state_failed: {repr(exc)}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 3) 未找到 proposal
                if current_state is None:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state="",
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_PROPOSAL_NOT_FOUND,
                        error=f"proposal_not_found: {pid}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 4) 当前状态非法(不在 ALL_STATES 中)
                if current_state not in ALL_STATES:
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_INVALID_STATE,
                        error=f"current_state_invalid: {current_state}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 5) 同状态 → already_in_state
                if current_state == tgt:
                    self._invalid_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state=current_state,
                        target_state=tgt,
                        actor=act,
                        reason=REASON_ALREADY_IN_STATE,
                        error=f"already_in_state: {current_state}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 6) 终态不可再转出
                if current_state in TERMINAL_STATES:
                    self._invalid_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_INVALID_TRANSITION,
                        error=f"terminal_state: {current_state}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 7) 验证转换合法性
                if not validate_transition(current_state, tgt):
                    self._invalid_count += 1
                    return self._build_report(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_INVALID_TRANSITION,
                        error=f"invalid_transition: {current_state} -> {tgt}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 8) 执行写(单一写入口:store.update / _index 兜底)
                try:
                    write_ok = self._write_state(pid, tgt, act, ts, reason)
                except Exception as exc:  # noqa: BLE001
                    self._last_error = repr(exc)
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_STORE_ERROR,
                        error=f"write_state_failed: {repr(exc)}",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                if not write_ok:
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        proposal_id=pid,
                        old_state=current_state,
                        new_state="",
                        target_state=tgt,
                        actor=act,
                        reason=REASON_STORE_ERROR,
                        error="store_write_failed",
                        audit_recorded=False,
                        timestamp=ts,
                    )

                # 9) 写 audit(失败也允许 transition 成功)
                audit_ok = _record_audit_safely(
                    self._audit,
                    proposal_id=pid,
                    old_state=current_state,
                    new_state=tgt,
                    actor=act,
                    timestamp=ts,
                    reason=reason,
                )
                if audit_ok:
                    with self._lock:
                        self._audit_recorded_count += 1

                # 10) 更新缓存
                with self._lock:
                    self._state_cache[pid] = tgt
                    self._success_count += 1
                    self._last_transition_ts = ts
                    self._last_error = None
                    self._push_history({
                        "proposal_id": pid,
                        "old_state": current_state,
                        "new_state": tgt,
                        "actor": act,
                        "reason": reason,
                        "timestamp": ts,
                        "audit_recorded": bool(audit_ok),
                    })

                # 11) 返回成功
                return self._build_report(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    old_state=current_state,
                    new_state=tgt,
                    target_state=tgt,
                    actor=act,
                    reason=reason,
                    error=None,
                    audit_recorded=audit_ok,
                    timestamp=ts,
                )

        except Exception as exc:  # noqa: BLE001
            # 兜底
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_2] transition 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                old_state="",
                new_state="",
                target_state=_safe_str(target_state, ""),
                actor=_safe_str(actor, self._default_actor),
                reason=REASON_INTERNAL_ERROR,
                error=repr(exc),
                audit_recorded=False,
                timestamp=_now_iso(),
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

    def get_allowed_transitions(self, proposal_id: str) -> List[str]:
        """获取 proposal 当前的合法目标状态列表。"""
        try:
            cur = self._read_state(proposal_id)
            if cur is None:
                return []
            return get_allowed_transitions(cur)
        except Exception:  # noqa: BLE001
            return []

    def is_terminal(self, proposal_id: str) -> bool:
        """判断 proposal 当前是否处于终态。"""
        try:
            cur = self._read_state(proposal_id)
            if cur is None:
                return False
            return is_terminal_state(cur)
        except Exception:  # noqa: BLE001
            return False

    def get_history(self, proposal_id: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        """读取本实例内的 transition 历史(只读)。"""
        with self._lock:
            if proposal_id is None:
                return list(self._history[-limit:])
            return [
                h for h in self._history
                if h.get("proposal_id") == str(proposal_id)
            ][-limit:]

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _read_state(self, proposal_id: str) -> Optional[str]:
        """读取 proposal 当前状态(优先缓存,fallback 到 store)。"""
        pid = str(proposal_id or "")
        if not pid:
            return None
        # 1) 缓存
        with self._lock:
            cached = self._state_cache.get(pid)
        if cached is not None:
            return cached
        # 2) store
        s = _read_proposal_status(self._store, pid)
        if s:
            with self._lock:
                self._state_cache[pid] = s
        return s

    def _write_state(
        self,
        proposal_id: str,
        new_state: str,
        actor: str,
        timestamp: str,
        reason: Optional[str],
    ) -> bool:
        """写 proposal 状态(单一写入口)。"""
        return _write_proposal_status(
            self._store,
            proposal_id,
            new_state,
            actor,
            timestamp,
            reason,
        )

    def _push_history(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._history.append(entry)
            if len(self._history) > self._max_history:
                # 保留最近 max_history 条
                self._history = self._history[-self._max_history:]

    def _build_report(
        self,
        success: bool,
        degraded: bool,
        proposal_id: str,
        old_state: str,
        new_state: str,
        target_state: str,
        actor: str,
        reason: Optional[str],
        error: Optional[str],
        audit_recorded: bool,
        timestamp: str,
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "proposal_id": str(proposal_id or ""),
            "old_state": str(old_state or ""),
            "new_state": str(new_state or ""),
            "target_state": str(target_state or ""),
            "actor": str(actor or self._default_actor),
            "reason": reason,
            "error": error,
            "audit_recorded": bool(audit_recorded),
            "timestamp": str(timestamp or _now_iso()),
            "lifecycle": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    # --------------------------------------------------------
    # 状态查询(只读)
    # --------------------------------------------------------

    @property
    def transition_count(self) -> int:
        with self._lock:
            return int(self._transition_count)

    @property
    def success_count(self) -> int:
        with self._lock:
            return int(self._success_count)

    @property
    def invalid_count(self) -> int:
        with self._lock:
            return int(self._invalid_count)

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
                "transition_count": int(self._transition_count),
                "success_count": int(self._success_count),
                "invalid_count": int(self._invalid_count),
                "error_count": int(self._error_count),
                "audit_recorded_count": int(self._audit_recorded_count),
                "last_error": self._last_error,
                "last_transition_ts": self._last_transition_ts,
                "cached_proposals": len(self._state_cache),
                "history_size": len(self._history),
                "has_store": self._store is not None,
                "has_audit": self._audit is not None,
            }

    def get_state_machine_info(self) -> Dict[str, Any]:
        """导出状态机元信息(用于诊断)。"""
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "schema_version": self.SCHEMA_VERSION,
            "states": sorted(ALL_STATES),
            "initial_state": INITIAL_STATE,
            "terminal_states": sorted(TERMINAL_STATES),
            "transitions": {
                k: sorted(v) for k, v in TRANSITIONS.items()
            },
        }


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_growth_proposal_lifecycle(
    proposal_store: Any = None,
    audit: Any = None,
    default_actor: str = AUDIT_ACTOR_RUNTIME,
) -> GrowthProposalLifecycleManager:
    """工厂函数:创建一个 GrowthProposalLifecycleManager。"""
    return GrowthProposalLifecycleManager(
        proposal_store=proposal_store,
        audit=audit,
        default_actor=default_actor,
    )


def safe_transition(
    manager: Optional[GrowthProposalLifecycleManager],
    proposal_id: str,
    target_state: str,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """全局安全 transition(任何异常都吸收)"""
    if manager is None:
        manager = create_growth_proposal_lifecycle()
    try:
        return manager.transition(proposal_id, target_state, actor=actor, reason=reason) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "proposal_id": str(proposal_id or ""),
            "old_state": "",
            "new_state": "",
            "target_state": str(target_state or ""),
            "actor": str(actor or AUDIT_ACTOR_DEFAULT),
            "reason": REASON_DEGRADED,
            "error": "safe_transition_exception",
            "audit_recorded": False,
            "timestamp": _now_iso(),
            "lifecycle": {
                "name": GROWTH_PROPOSAL_LIFECYCLE_NAME,
                "version": GROWTH_PROPOSAL_LIFECYCLE_VERSION,
                "schema_version": GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION,
            },
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION",
    "GROWTH_PROPOSAL_LIFECYCLE_NAME",
    "GROWTH_PROPOSAL_LIFECYCLE_VERSION",
    "STATE_PENDING",
    "STATE_REVIEWING",
    "STATE_APPROVED",
    "STATE_REJECTED",
    "STATE_NEEDS_REVIEW",
    "STATE_READY_FOR_APPLY",
    "ALL_STATES",
    "TRANSITIONS",
    "TERMINAL_STATES",
    "INITIAL_STATE",
    "AUDIT_ACTION_TRANSITION",
    "AUDIT_COMPONENT",
    "AUDIT_ACTOR_RUNTIME",
    "AUDIT_ACTOR_DEFAULT",
    "REASON_ALREADY_IN_STATE",
    "REASON_INVALID_TRANSITION",
    "REASON_PROPOSAL_NOT_FOUND",
    "REASON_INVALID_STATE",
    "REASON_STORE_ERROR",
    "REASON_AUDIT_ERROR",
    "REASON_INVALID_INPUT",
    "REASON_INTERNAL_ERROR",
    "REASON_DEGRADED",
    "FORBIDDEN_PROPOSAL_MANAGER_METHODS",
    "is_valid_state",
    "is_terminal_state",
    "validate_transition",
    "get_allowed_transitions",
    "GrowthProposalLifecycleManager",
    "create_growth_proposal_lifecycle",
    "safe_transition",
    "_record_audit_safely",
    "_read_proposal_status",
    "_write_proposal_status",
]
