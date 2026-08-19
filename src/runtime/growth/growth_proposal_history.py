# -*- coding: utf-8 -*-
"""
src/runtime/growth/growth_proposal_history.py

Phase C.8.4 Growth Proposal History & Versioning —— GrowthProposalHistory

=========================
目标
=========================
建立 Proposal Version History 系统。

负责:
  - 记录 Proposal 生命周期事件
  - 自动版本管理(per-proposal 单调递增)
  - 历史查询(get_history / get_latest)
  - 版本对比(compare_versions)
  - Audit 记录

不负责:
  - 人格修改
  - SelfModel 更新
  - Trait 修改
  - 自动 apply
  - 修改 Proposal 内容

=========================
事件类型
=========================
  - runtime_growth_proposal_created
  - runtime_growth_proposal_reviewed
  - runtime_growth_proposal_approved
  - runtime_growth_proposal_rejected
  - runtime_growth_proposal_revoked
  - 任意自定义 action

=========================
Version 机制
=========================
每个 proposal 独立维护版本号:
  - 初始: version = 1
  - 每次 record_event: version += 1
  - 线程安全(RLock):同一 proposal 多线程并发不会产生重复 version

=========================
只读边界
=========================
允许:
  - 读 ProposalStore
  - 读 Lifecycle 状态
  - 写 HistoryStore(本模块)
禁止:
  - 写 Personality
  - 写 SelfModel
  - 写 Trait
  - 调 apply_proposal / accept_proposal / resolve

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
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION = "1.0"
GROWTH_PROPOSAL_HISTORY_NAME = "growth_proposal_history"
GROWTH_PROPOSAL_HISTORY_VERSION = "1.0.0"

# 事件类型(标准化 action 字符串)
ACTION_CREATED = "runtime_growth_proposal_created"
ACTION_REVIEWED = "runtime_growth_proposal_reviewed"
ACTION_APPROVED = "runtime_growth_proposal_approved"
ACTION_REJECTED = "runtime_growth_proposal_rejected"
ACTION_REVOKED = "runtime_growth_proposal_revoked"
ACTION_TRANSITION = "runtime_growth_proposal_transition"  # 通用 transition
ACTION_UPDATED = "runtime_growth_proposal_history_updated"  # 自身 history audit

# 全部合法 action(包含通用 transition / custom)
ALL_ACTIONS: FrozenSet[str] = frozenset({
    ACTION_CREATED,
    ACTION_REVIEWED,
    ACTION_APPROVED,
    ACTION_REJECTED,
    ACTION_REVOKED,
    ACTION_TRANSITION,
    ACTION_UPDATED,
})

# Audit
AUDIT_ACTION_HISTORY_UPDATED = "runtime_growth_proposal_history_updated"
AUDIT_COMPONENT = "runtime_growth"
ACTOR_RUNTIME = "runtime"
ACTOR_DEFAULT = ACTOR_RUNTIME

# 错误原因
REASON_PROPOSAL_NOT_FOUND = "proposal_not_found"
REASON_INVALID_INPUT = "invalid_input"
REASON_VERSION_NOT_FOUND = "version_not_found"
REASON_INVALID_VERSION = "invalid_version"
REASON_STORE_ERROR = "store_error"
REASON_AUDIT_ERROR = "audit_error"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"

# 初始版本号
INITIAL_VERSION = 1


# ============================================================
# 时间 / 工具
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


def _new_history_id() -> str:
    """生成 history_id(UUID 风格,失败时回退到时间戳)。"""
    try:
        return f"hist_{uuid.uuid4().hex[:16]}"
    except Exception:  # noqa: BLE001
        try:
            return f"hist_{int(datetime.now(timezone.utc).timestamp() * 1000):x}"
        except Exception:  # noqa: BLE001
            return "hist_0000000000000000"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
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


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:  # noqa: BLE001
        return {}


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:  # noqa: BLE001
        return []


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
# Audit 工具
# ============================================================


def _record_audit_safely(
    audit: Any,
    history_id: str,
    proposal_id: str,
    version: int,
    action: str,
    actor: str,
    timestamp: str,
) -> bool:
    """安全地记录 audit(任何异常都吸收)。"""
    try:
        if audit is None:
            return False
        detail: Dict[str, Any] = {
            "history_id": str(history_id or ""),
            "proposal_id": str(proposal_id or ""),
            "version": int(version) if version is not None else 0,
            "action": str(action or ""),
            "actor": str(actor or ACTOR_DEFAULT),
            "timestamp": str(timestamp or _now_iso()),
            "schema_version": GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
        }
        # 优先 record()
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=AUDIT_ACTION_HISTORY_UPDATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_HISTORY_UPDATED,
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
                    operation_type=AUDIT_ACTION_HISTORY_UPDATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_HISTORY_UPDATED,
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
                    operation_type=AUDIT_ACTION_HISTORY_UPDATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_HISTORY_UPDATED,
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
# HistoryStore 工具(可选外部持久化)
# ============================================================


def _read_history_from_store(store: Any, proposal_id: str) -> List[Dict[str, Any]]:
    """从外部 store 读取历史(只读,失败返回 [])。"""
    if store is None:
        return []
    pid = _safe_str(proposal_id, "")
    if not pid:
        return []
    try:
        load_fn = getattr(store, "load_history", None) or getattr(store, "get_history", None)
        if callable(load_fn):
            try:
                h = load_fn(pid)
                if isinstance(h, list):
                    return [dict(x) for x in h if isinstance(x, dict)]
                if isinstance(h, dict):
                    return [dict(h)]
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return []


def _write_history_to_store(
    store: Any,
    proposal_id: str,
    history_entries: List[Dict[str, Any]],
) -> bool:
    """向外部 store 写入历史(失败不抛异常,仅返回 bool)。"""
    if store is None:
        return True  # 无 store 视为成功
    pid = _safe_str(proposal_id, "")
    if not pid:
        return False
    try:
        write_fn = getattr(store, "save_history", None) or getattr(store, "set_history", None)
        if callable(write_fn):
            try:
                write_fn(pid, [dict(x) for x in history_entries if isinstance(x, dict)])
                return True
            except Exception:  # noqa: BLE001
                return False
    except Exception:  # noqa: BLE001
        pass
    return False


# ============================================================
# Diff 工具
# ============================================================


def _diff_metadata(
    meta_a: Optional[Dict[str, Any]],
    meta_b: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """比较两个 metadata dict,返回变化。"""
    a = _safe_dict(meta_a)
    b = _safe_dict(meta_b)
    a_keys = set(a.keys())
    b_keys = set(b.keys())
    added = sorted(b_keys - a_keys)
    removed = sorted(a_keys - b_keys)
    common = sorted(a_keys & b_keys)
    changed: Dict[str, Dict[str, Any]] = {}
    for k in common:
        try:
            if a.get(k) != b.get(k):
                changed[k] = {
                    "from": _safe_deepcopy(a.get(k)),
                    "to": _safe_deepcopy(b.get(k)),
                }
        except Exception:  # noqa: BLE001
            changed[k] = {"from": "<uncomparable>", "to": "<uncomparable>"}
    return {
        "added": {k: _safe_deepcopy(b.get(k)) for k in added},
        "removed": {k: _safe_deepcopy(a.get(k)) for k in removed},
        "changed": changed,
    }


# ============================================================
# GrowthProposalHistory
# ============================================================


class GrowthProposalHistory:
    """
    Growth Proposal Version History 管理器 (Phase C.8.4 / v1.0)

    负责:
      - record_event(proposal_id, action, actor, metadata)
      - get_history(proposal_id)
      - get_latest(proposal_id)
      - compare_versions(proposal_id, version_a, version_b)

    严禁:
      - 写 Personality / SelfModel / Trait
      - 调 apply_proposal / accept_proposal
      - 调 PersonalityResolver.resolve()
      - 修改 Proposal 内容

    Schema 契约(冻结于 v1.0):
      record_event 返回:
      {
        "schema_version": "1.0",
        "success": bool,
        "degraded": bool,
        "history_id": str,
        "proposal_id": str,
        "version": int,
        "action": str,
        "actor": str,
        "timestamp": str,
        "metadata": dict,
        "previous_version": Optional[int],
        "audit_recorded": bool,
        "error": Optional[str],
        "history": {
          "name": "growth_proposal_history",
          "version": "1.0.0",
          "schema_version": "1.0",
        }
      }
    """

    SCHEMA_VERSION = GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION
    NAME = GROWTH_PROPOSAL_HISTORY_NAME
    VERSION = GROWTH_PROPOSAL_HISTORY_VERSION

    def __init__(
        self,
        history_store: Any = None,
        proposal_store: Any = None,
        audit: Any = None,
        default_actor: str = ACTOR_DEFAULT,
    ) -> None:
        """
        Args:
            history_store: 可选外部持久化 store
            proposal_store: 可选 ProposalStore(用于读取 proposal 元数据,snapshot 增强)
            audit: 可选 Audit 写入对象
            default_actor: 默认 actor(仅当 caller 未传时生效)
        """
        self._history_store = history_store
        self._proposal_store = proposal_store
        self._audit = audit
        self._default_actor = str(default_actor or ACTOR_DEFAULT)
        self._lock = threading.RLock()

        # 内部存储(per-proposal)
        # proposal_id -> List[history_entry](按 version 升序)
        self._history_index: Dict[str, List[Dict[str, Any]]] = {}
        # proposal_id -> current_version(单调递增)
        self._version_counter: Dict[str, int] = {}

        # 统计
        self._record_count: int = 0
        self._success_count: int = 0
        self._error_count: int = 0
        self._audit_recorded_count: int = 0
        self._version_conflict_count: int = 0
        self._last_error: Optional[str] = None
        self._last_event_ts: Optional[str] = None

    # --------------------------------------------------------
    # 依赖注入
    # --------------------------------------------------------

    def set_history_store(self, store: Any) -> None:
        with self._lock:
            self._history_store = store

    def set_proposal_store(self, store: Any) -> None:
        with self._lock:
            self._proposal_store = store

    def set_audit(self, audit: Any) -> None:
        with self._lock:
            self._audit = audit

    def attach(
        self,
        history_store: Any = None,
        proposal_store: Any = None,
        audit: Any = None,
    ) -> bool:
        with self._lock:
            if history_store is not None:
                self._history_store = history_store
            if proposal_store is not None:
                self._proposal_store = proposal_store
            if audit is not None:
                self._audit = audit
        return True

    # --------------------------------------------------------
    # 主入口:record_event
    # --------------------------------------------------------

    def record_event(
        self,
        proposal_id: str,
        action: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        记录一次事件(永不抛异常)。

        Args:
            proposal_id: proposal ID
            action: 事件类型(必须非空字符串)
            actor: 操作者
            metadata: 可选元数据(深拷贝隔离)

        Returns:
            dict(见 Schema 契约)
        """
        try:
            with self._lock:
                self._record_count += 1
                pid = _safe_str(proposal_id, "")
                act_action = _safe_str(action, "")
                act = _safe_str(actor, self._default_actor) or self._default_actor
                meta = _safe_deepcopy(metadata) if metadata is not None else {}
                ts = _now_iso()

                # 1) 输入校验
                if not pid:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        history_id="",
                        proposal_id="",
                        version=0,
                        previous_version=None,
                        action=act_action,
                        actor=act,
                        timestamp=ts,
                        metadata=meta,
                        audit_recorded=False,
                        error="proposal_id_empty",
                        error_reason=REASON_INVALID_INPUT,
                    )
                if not act_action:
                    return self._build_report(
                        success=False,
                        degraded=False,
                        history_id="",
                        proposal_id=pid,
                        version=0,
                        previous_version=None,
                        action="",
                        actor=act,
                        timestamp=ts,
                        metadata=meta,
                        audit_recorded=False,
                        error="action_empty",
                        error_reason=REASON_INVALID_INPUT,
                    )

                # 2) 计算新 version(RLock 保护下,per-proposal 单调递增)
                prev_version = self._version_counter.get(pid, 0)
                new_version = prev_version + 1

                # 3) 生成 history_id
                hist_id = _new_history_id()

                # 4) 构造 entry
                entry: Dict[str, Any] = {
                    "history_id": hist_id,
                    "proposal_id": pid,
                    "version": int(new_version),
                    "previous_version": int(prev_version) if prev_version > 0 else None,
                    "action": act_action,
                    "actor": act,
                    "timestamp": ts,
                    "metadata": meta,
                    "schema_version": self.SCHEMA_VERSION,
                }

                # 5) 写入内部索引
                if pid not in self._history_index:
                    self._history_index[pid] = []
                self._history_index[pid].append(entry)
                self._version_counter[pid] = new_version

                # 6) 尝试写外部 store(失败也不阻断)
                store_ok = True
                if self._history_store is not None:
                    try:
                        store_ok = _write_history_to_store(
                            self._history_store,
                            pid,
                            self._history_index[pid],
                        )
                    except Exception:  # noqa: BLE001
                        store_ok = False
                if not store_ok:
                    # 写外部 store 失败 → 不回滚内部,标记 degraded
                    self._last_error = "history_store_write_failed"

                # 7) 写 audit(失败也不阻断)
                audit_ok = _record_audit_safely(
                    audit=self._audit,
                    history_id=hist_id,
                    proposal_id=pid,
                    version=new_version,
                    action=act_action,
                    actor=act,
                    timestamp=ts,
                )
                if audit_ok:
                    self._audit_recorded_count += 1

                # 8) 更新统计
                self._success_count += 1
                self._last_event_ts = ts
                if not store_ok:
                    self._error_count += 1
                else:
                    self._last_error = None

                # 9) 返回成功
                return self._build_report(
                    success=True,
                    degraded=not store_ok,
                    history_id=hist_id,
                    proposal_id=pid,
                    version=new_version,
                    previous_version=entry["previous_version"],
                    action=act_action,
                    actor=act,
                    timestamp=ts,
                    metadata=meta,
                    audit_recorded=audit_ok,
                    error=None if store_ok else "history_store_write_failed",
                    error_reason=None if store_ok else REASON_STORE_ERROR,
                )

        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_4] record_event 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                history_id="",
                proposal_id=_safe_str(proposal_id, ""),
                version=0,
                previous_version=None,
                action=_safe_str(action, ""),
                actor=_safe_str(actor, self._default_actor),
                timestamp=_now_iso(),
                metadata={},
                audit_recorded=False,
                error=repr(exc),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # 便捷事件记录
    # --------------------------------------------------------

    def record_created(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录 created 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_CREATED,
            actor=actor,
            metadata=metadata,
        )

    def record_reviewed(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录 reviewed 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_REVIEWED,
            actor=actor,
            metadata=metadata,
        )

    def record_approved(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录 approved 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_APPROVED,
            actor=actor,
            metadata=metadata,
        )

    def record_rejected(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录 rejected 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_REJECTED,
            actor=actor,
            metadata=metadata,
        )

    def record_revoked(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录 revoked 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_REVOKED,
            actor=actor,
            metadata=metadata,
        )

    def record_transition(
        self,
        proposal_id: str,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """记录通用 transition 事件。"""
        return self.record_event(
            proposal_id=proposal_id,
            action=ACTION_TRANSITION,
            actor=actor,
            metadata=metadata,
        )

    # --------------------------------------------------------
    # 查询:get_history
    # --------------------------------------------------------

    def get_history(
        self,
        proposal_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        读取 proposal 完整历史(只读,深拷贝返回)。

        Args:
            proposal_id: proposal ID
            limit: 可选,只返回最近 N 条

        Returns:
            按 version 升序的历史条目列表(proposal 不存在或异常时返回 [])
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                if not pid:
                    return []
                entries = self._history_index.get(pid, [])
                if not entries:
                    # 尝试从外部 store 读取
                    external = _read_history_from_store(self._history_store, pid)
                    if external:
                        return [
                            _safe_deepcopy(e) for e in external
                            if isinstance(e, dict)
                        ]
                    return []
                # 深拷贝
                out = [_safe_deepcopy(e) for e in entries if isinstance(e, dict)]
                if limit is not None and int(limit) > 0:
                    out = out[-int(limit):]
                return out
        except Exception:  # noqa: BLE001
            return []

    def get_history_count(self, proposal_id: str) -> int:
        """返回 proposal 的历史条目数。"""
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                if not pid:
                    return 0
                return len(self._history_index.get(pid, []))
        except Exception:  # noqa: BLE001
            return 0

    # --------------------------------------------------------
    # 查询:get_latest
    # --------------------------------------------------------

    def get_latest(self, proposal_id: str) -> Dict[str, Any]:
        """
        返回 proposal 最新的快照。

        Returns:
            {
              "schema_version": "1.0",
              "success": bool,
              "degraded": bool,
              "proposal_id": str,
              "current_version": int,
              "current_state": str,         # 来自 metadata.state(如可获取)
              "last_action": str,
              "last_actor": str,
              "updated_at": str,
              "history_count": int,
              "proposal_exists": bool,
              "error": Optional[str],
              "history": {...}
            }
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                ts = _now_iso()
                if not pid:
                    return self._build_snapshot(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        current_version=0,
                        current_state="",
                        last_action="",
                        last_actor="",
                        updated_at=ts,
                        history_count=0,
                        proposal_exists=False,
                        error="proposal_id_empty",
                        error_reason=REASON_INVALID_INPUT,
                    )

                entries = self._history_index.get(pid, [])
                if not entries:
                    # 尝试从外部 store
                    external = _read_history_from_store(self._history_store, pid)
                    if external:
                        last = external[-1] if isinstance(external[-1], dict) else {}
                        return self._build_snapshot(
                            success=True,
                            degraded=False,
                            proposal_id=pid,
                            current_version=_safe_int(_safe_get(last, "version", 0)),
                            current_state=_safe_str(
                                _safe_get(_safe_get(last, "metadata", {}), "state", ""),
                                "",
                            ),
                            last_action=_safe_str(_safe_get(last, "action", ""), ""),
                            last_actor=_safe_str(_safe_get(last, "actor", ""), ""),
                            updated_at=_safe_str(_safe_get(last, "timestamp", ts), ts),
                            history_count=len(external),
                            proposal_exists=True,
                            error=None,
                            error_reason=None,
                        )
                    return self._build_snapshot(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        current_version=0,
                        current_state="",
                        last_action="",
                        last_actor="",
                        updated_at=ts,
                        history_count=0,
                        proposal_exists=False,
                        error=f"proposal_not_found: {pid}",
                        error_reason=REASON_PROPOSAL_NOT_FOUND,
                    )

                last = entries[-1]
                last_meta = _safe_get(last, "metadata", {})
                return self._build_snapshot(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    current_version=_safe_int(_safe_get(last, "version", 0)),
                    current_state=_safe_str(_safe_get(last_meta, "state", ""), ""),
                    last_action=_safe_str(_safe_get(last, "action", ""), ""),
                    last_actor=_safe_str(_safe_get(last, "actor", ""), ""),
                    updated_at=_safe_str(_safe_get(last, "timestamp", ts), ts),
                    history_count=len(entries),
                    proposal_exists=True,
                    error=None,
                    error_reason=None,
                )

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c8_4] get_latest 异常(已隔离): {exc}")
            return self._build_snapshot(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                current_version=0,
                current_state="",
                last_action="",
                last_actor="",
                updated_at=_now_iso(),
                history_count=0,
                proposal_exists=False,
                error=repr(exc),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # Diff:compare_versions
    # --------------------------------------------------------

    def compare_versions(
        self,
        proposal_id: str,
        version_a: int,
        version_b: int,
    ) -> Dict[str, Any]:
        """
        对比 proposal 的两个版本。

        Returns:
            {
              "schema_version": "1.0",
              "success": bool,
              "degraded": bool,
              "proposal_id": str,
              "from_version": int,
              "to_version": int,
              "from_entry": dict | None,
              "to_entry": dict | None,
              "changes": {
                "action": {"from": str, "to": str},    # action 变化
                "actor": {"from": str, "to": str},     # actor 变化
                "metadata": {                          # metadata 变化
                  "added": {...},
                  "removed": {...},
                  "changed": {...}
                }
              },
              "diff_summary": {
                "action_changed": bool,
                "actor_changed": bool,
                "metadata_changed": bool
              },
              "error": Optional[str],
              "history": {...}
            }
        """
        try:
            with self._lock:
                pid = _safe_str(proposal_id, "")
                ts = _now_iso()
                if not pid:
                    return self._build_diff(
                        success=False,
                        degraded=False,
                        proposal_id="",
                        from_version=_safe_int(version_a, 0),
                        to_version=_safe_int(version_b, 0),
                        from_entry=None,
                        to_entry=None,
                        changes={},
                        error="proposal_id_empty",
                        error_reason=REASON_INVALID_INPUT,
                    )

                va = _safe_int(version_a, 0)
                vb = _safe_int(version_b, 0)
                if va <= 0 or vb <= 0:
                    return self._build_diff(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        from_version=va,
                        to_version=vb,
                        from_entry=None,
                        to_entry=None,
                        changes={},
                        error=f"invalid_version: a={va}, b={vb}",
                        error_reason=REASON_INVALID_VERSION,
                    )

                entries = self._history_index.get(pid, [])
                # 查 from / to
                entry_a: Optional[Dict[str, Any]] = None
                entry_b: Optional[Dict[str, Any]] = None
                for e in entries:
                    if isinstance(e, dict):
                        ev = _safe_int(_safe_get(e, "version", 0), 0)
                        if ev == va and entry_a is None:
                            entry_a = e
                        if ev == vb and entry_b is None:
                            entry_b = e

                if entry_a is None or entry_b is None:
                    missing = []
                    if entry_a is None:
                        missing.append(f"v{va}")
                    if entry_b is None:
                        missing.append(f"v{vb}")
                    return self._build_diff(
                        success=False,
                        degraded=False,
                        proposal_id=pid,
                        from_version=va,
                        to_version=vb,
                        from_entry=_safe_deepcopy(entry_a) if entry_a else None,
                        to_entry=_safe_deepcopy(entry_b) if entry_b else None,
                        changes={},
                        error=f"version_not_found: {','.join(missing)}",
                        error_reason=REASON_VERSION_NOT_FOUND,
                    )

                # 计算 diff
                a_action = _safe_str(_safe_get(entry_a, "action", ""), "")
                b_action = _safe_str(_safe_get(entry_b, "action", ""), "")
                a_actor = _safe_str(_safe_get(entry_a, "actor", ""), "")
                b_actor = _safe_str(_safe_get(entry_b, "actor", ""), "")
                a_meta = _safe_get(entry_a, "metadata", {})
                b_meta = _safe_get(entry_b, "metadata", {})

                changes: Dict[str, Any] = {
                    "action": {"from": a_action, "to": b_action} if a_action != b_action else {},
                    "actor": {"from": a_actor, "to": b_actor} if a_actor != b_actor else {},
                    "metadata": _diff_metadata(a_meta, b_meta),
                }
                diff_summary = {
                    "action_changed": bool(changes["action"]),
                    "actor_changed": bool(changes["actor"]),
                    "metadata_changed": bool(
                        _safe_get(changes["metadata"], "added", {})
                        or _safe_get(changes["metadata"], "removed", {})
                        or _safe_get(changes["metadata"], "changed", {})
                    ),
                }

                return self._build_diff(
                    success=True,
                    degraded=False,
                    proposal_id=pid,
                    from_version=va,
                    to_version=vb,
                    from_entry=_safe_deepcopy(entry_a),
                    to_entry=_safe_deepcopy(entry_b),
                    changes=changes,
                    error=None,
                    error_reason=None,
                    diff_summary=diff_summary,
                )

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c8_4] compare_versions 异常(已隔离): {exc}")
            return self._build_diff(
                success=False,
                degraded=True,
                proposal_id=_safe_str(proposal_id, ""),
                from_version=_safe_int(version_a, 0),
                to_version=_safe_int(version_b, 0),
                from_entry=None,
                to_entry=None,
                changes={},
                error=repr(exc),
                error_reason=REASON_INTERNAL_ERROR,
            )

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _build_report(
        self,
        success: bool,
        degraded: bool,
        history_id: str,
        proposal_id: str,
        version: int,
        previous_version: Optional[int],
        action: str,
        actor: str,
        timestamp: str,
        metadata: Dict[str, Any],
        audit_recorded: bool,
        error: Optional[str],
        error_reason: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "history_id": str(history_id or ""),
            "proposal_id": str(proposal_id or ""),
            "version": int(version) if version is not None else 0,
            "previous_version": (
                int(previous_version) if isinstance(previous_version, int) else None
            ),
            "action": str(action or ""),
            "actor": str(actor or self._default_actor),
            "timestamp": str(timestamp or _now_iso()),
            "metadata": _safe_deepcopy(metadata) if isinstance(metadata, dict) else {},
            "audit_recorded": bool(audit_recorded),
            "error": error,
            "error_reason": error_reason,
            "history": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    def _build_snapshot(
        self,
        success: bool,
        degraded: bool,
        proposal_id: str,
        current_version: int,
        current_state: str,
        last_action: str,
        last_actor: str,
        updated_at: str,
        history_count: int,
        proposal_exists: bool,
        error: Optional[str],
        error_reason: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "proposal_id": str(proposal_id or ""),
            "current_version": int(current_version) if current_version is not None else 0,
            "current_state": str(current_state or ""),
            "last_action": str(last_action or ""),
            "last_actor": str(last_actor or ""),
            "updated_at": str(updated_at or _now_iso()),
            "history_count": int(history_count) if history_count is not None else 0,
            "proposal_exists": bool(proposal_exists),
            "error": error,
            "error_reason": error_reason,
            "history": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    def _build_diff(
        self,
        success: bool,
        degraded: bool,
        proposal_id: str,
        from_version: int,
        to_version: int,
        from_entry: Optional[Dict[str, Any]],
        to_entry: Optional[Dict[str, Any]],
        changes: Dict[str, Any],
        error: Optional[str],
        error_reason: Optional[str],
        diff_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "proposal_id": str(proposal_id or ""),
            "from_version": int(from_version) if from_version is not None else 0,
            "to_version": int(to_version) if to_version is not None else 0,
            "from_entry": _safe_deepcopy(from_entry) if isinstance(from_entry, dict) else None,
            "to_entry": _safe_deepcopy(to_entry) if isinstance(to_entry, dict) else None,
            "changes": _safe_deepcopy(changes) if isinstance(changes, dict) else {},
            "diff_summary": _safe_deepcopy(diff_summary) if isinstance(diff_summary, dict) else {
                "action_changed": False,
                "actor_changed": False,
                "metadata_changed": False,
            },
            "error": error,
            "error_reason": error_reason,
            "history": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    # --------------------------------------------------------
    # 统计 / 诊断
    # --------------------------------------------------------

    @property
    def record_count(self) -> int:
        with self._lock:
            return int(self._record_count)

    @property
    def success_count(self) -> int:
        with self._lock:
            return int(self._success_count)

    @property
    def error_count(self) -> int:
        with self._lock:
            return int(self._error_count)

    @property
    def audit_recorded_count(self) -> int:
        with self._lock:
            return int(self._audit_recorded_count)

    @property
    def version_conflict_count(self) -> int:
        with self._lock:
            return int(self._version_conflict_count)

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "record_count": int(self._record_count),
                "success_count": int(self._success_count),
                "error_count": int(self._error_count),
                "audit_recorded_count": int(self._audit_recorded_count),
                "version_conflict_count": int(self._version_conflict_count),
                "tracked_proposals": len(self._history_index),
                "last_error": self._last_error,
                "last_event_ts": self._last_event_ts,
                "has_history_store": self._history_store is not None,
                "has_proposal_store": self._proposal_store is not None,
                "has_audit": self._audit is not None,
            }

    def get_history_info(self) -> Dict[str, Any]:
        """导出 history 模块元信息(用于诊断)。"""
        return {
            "name": self.NAME,
            "version": self.VERSION,
            "schema_version": self.SCHEMA_VERSION,
            "supported_actions": sorted(ALL_ACTIONS),
            "initial_version": INITIAL_VERSION,
        }

    def reset(self) -> bool:
        """清空所有历史(仅用于测试/诊断,生产环境慎用)。"""
        with self._lock:
            self._history_index.clear()
            self._version_counter.clear()
            self._record_count = 0
            self._success_count = 0
            self._error_count = 0
            self._audit_recorded_count = 0
            self._version_conflict_count = 0
            self._last_error = None
            self._last_event_ts = None
        return True


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_growth_proposal_history(
    history_store: Any = None,
    proposal_store: Any = None,
    audit: Any = None,
    default_actor: str = ACTOR_DEFAULT,
) -> GrowthProposalHistory:
    """工厂函数:创建一个 GrowthProposalHistory。"""
    return GrowthProposalHistory(
        history_store=history_store,
        proposal_store=proposal_store,
        audit=audit,
        default_actor=default_actor,
    )


def safe_record_event(
    history: Optional[GrowthProposalHistory],
    proposal_id: str,
    action: str,
    actor: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """全局安全 record_event(任何异常都吸收)"""
    if history is None:
        return {
            "schema_version": GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "history_id": "",
            "proposal_id": str(proposal_id or ""),
            "version": 0,
            "previous_version": None,
            "action": str(action or ""),
            "actor": str(actor or ACTOR_DEFAULT),
            "timestamp": _now_iso(),
            "metadata": _safe_deepcopy(metadata) if isinstance(metadata, dict) else {},
            "audit_recorded": False,
            "error": "history_none",
            "error_reason": REASON_DEGRADED,
            "history": {
                "name": GROWTH_PROPOSAL_HISTORY_NAME,
                "version": GROWTH_PROPOSAL_HISTORY_VERSION,
                "schema_version": GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
            },
        }
    try:
        return history.record_event(
            proposal_id=proposal_id,
            action=action,
            actor=actor,
            metadata=metadata,
        ) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "history_id": "",
            "proposal_id": str(proposal_id or ""),
            "version": 0,
            "previous_version": None,
            "action": str(action or ""),
            "actor": str(actor or ACTOR_DEFAULT),
            "timestamp": _now_iso(),
            "metadata": _safe_deepcopy(metadata) if isinstance(metadata, dict) else {},
            "audit_recorded": False,
            "error": "safe_record_exception",
            "error_reason": REASON_DEGRADED,
            "history": {
                "name": GROWTH_PROPOSAL_HISTORY_NAME,
                "version": GROWTH_PROPOSAL_HISTORY_VERSION,
                "schema_version": GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
            },
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION",
    "GROWTH_PROPOSAL_HISTORY_NAME",
    "GROWTH_PROPOSAL_HISTORY_VERSION",
    "ACTION_CREATED",
    "ACTION_REVIEWED",
    "ACTION_APPROVED",
    "ACTION_REJECTED",
    "ACTION_REVOKED",
    "ACTION_TRANSITION",
    "ACTION_UPDATED",
    "ALL_ACTIONS",
    "AUDIT_ACTION_HISTORY_UPDATED",
    "AUDIT_COMPONENT",
    "ACTOR_RUNTIME",
    "ACTOR_DEFAULT",
    "REASON_PROPOSAL_NOT_FOUND",
    "REASON_INVALID_INPUT",
    "REASON_VERSION_NOT_FOUND",
    "REASON_INVALID_VERSION",
    "REASON_STORE_ERROR",
    "REASON_AUDIT_ERROR",
    "REASON_INTERNAL_ERROR",
    "REASON_DEGRADED",
    "INITIAL_VERSION",
    "GrowthProposalHistory",
    "create_growth_proposal_history",
    "safe_record_event",
]
