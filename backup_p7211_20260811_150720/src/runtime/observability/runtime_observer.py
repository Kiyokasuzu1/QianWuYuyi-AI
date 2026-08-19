# -*- coding: utf-8 -*-
"""
src/runtime/observability/runtime_observer.py

Phase C.7.2 Runtime Observability & Audit Dashboard —— RuntimeObserver

职责:
  - 统一暴露 Runtime 当前可观测状态(只读)
  - 聚合:
      * 所有 Adapter 健康状态
      * 最近 Cycle 执行情况
      * 最近错误
      * 降级状态
      * 生命周期统计
      * Audit Chain 状态
  - 提供统一 schema 的 snapshot 接口

=========================
重要原则(硬约束)
=========================
这是观察层。
只读。
禁止任何业务状态修改。

严禁调用:
  - GrowthEngine.apply() / GrowthState.save() / ProposalManager.create_proposal()
  - PersonalityResolver.resolve() / TraitStateUpdater.apply()
  - 任何 Adapter.process_cycle()
  - 任何 persistence 写入方法

严禁修改:
  - src/growth/**
  - src/personality/**
  - src/runtime/self_model/**
  - Memory/Emotion/Relationship/Growth Adapter 业务逻辑
  - Runtime Cycle 执行流程
  - config.yaml
  - 不得改变 process_event() / adapter 执行顺序 / fail-soft 机制

不修改:
  - RuntimeCycleOrchestrator 主体(只调用其只读方法)
  - CycleHistoryIndex 主体
  - ActionPersistenceManager 主体

允许调用(全部只读):
  - orchestrator.health_check() / snapshot() / is_degraded
  - orchestrator.list_adapters() / list_cycles() / get_context() / list_recent_cycles()
  - adapter.health_check() / adapter.snapshot()
  - CycleHistoryIndex.list() / count() / get_stats()
  - persistence.is_degraded / persistence.get_recent_actions() / persistence.get_stats()

异常处理:
  - 任何组件异常 → Observer 内部吞掉,返回 degraded snapshot
  - 不允许 Observer 自身抛出
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

RUNTIME_OBSERVER_SCHEMA_VERSION = "1.0"
RUNTIME_OBSERVER_NAME = "runtime_observer"
RUNTIME_OBSERVER_VERSION = "1.0.0"

# 5 个标准 adapter(用于 schema 完整性)
STANDARD_ADAPTER_NAMES: List[str] = [
    "memory",
    "emotion",
    "personality",
    "relationship",
    "growth",
]

# 最近错误回看窗口
DEFAULT_RECENT_ERRORS_LIMIT = 10
# 最近 cycles 读取窗口
DEFAULT_RECENT_CYCLES_LIMIT = 20


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
            return str(time.time())


# ============================================================
# 内部辅助
# ============================================================


def _safe_call_dict(fn: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """安全调用一个返回 dict 的方法(任何异常返回空 dict)"""
    try:
        if fn is None or not callable(fn):
            return {}
        result = fn(*args, **kwargs)
        if isinstance(result, dict):
            return dict(result)
        return {}
    except Exception:  # noqa: BLE001
        return {}


def _safe_call_list(fn: Any, *args: Any, **kwargs: Any) -> List[Any]:
    """安全调用一个返回 list 的方法(任何异常返回空 list)"""
    try:
        if fn is None or not callable(fn):
            return []
        result = fn(*args, **kwargs)
        if isinstance(result, list):
            return list(result)
        return []
    except Exception:  # noqa: BLE001
        return []


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    """安全获取属性(任何异常返回 default)"""
    try:
        if obj is None:
            return default
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001
        return default


# ============================================================
# RuntimeObserver
# ============================================================


class RuntimeObserver:
    """
    Runtime 可观测层(Phase C.7.2 / v1.0)

    只读视图:
      - 聚合 RuntimeCycleOrchestrator 的 health_check() / snapshot()
      - 遍历所有 adapter 调 health_check() / snapshot()
      - 读取 CycleHistoryIndex 统计
      - 读取 ActionPersistenceManager 只读属性
      - 全部异常 fail-soft,绝不抛出

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "runtime": {
            "name": "RuntimeCycleOrchestrator",
            "healthy": bool,
            "degraded": bool,
            "enabled": bool,
            "closed": bool,
            "version": str,
            "cycle_count": int,
            "completed_count": int,
            "failed_count": int,
            "consecutive_failures": int,
        },
        "adapters": {
            "memory":     {"healthy": bool, "status": str, "name": str, ...},
            "emotion":    {...},
            "personality": {...},
            "relationship": {...},
            "growth":     {...},
        },
        "cycles": {
            "total": int,
            "completed": int,
            "failed": int,
            "success_rate": float,        # 0.0 - 1.0
            "in_memory": int,
            "latest_cycle_id": Optional[str],
            "latest_timestamp": Optional[str],
        },
        "errors": {
            "last_error": Optional[str],
            "consecutive_failures": int,
            "recent_errors": List[Dict],
        },
        "persistence": {
            "available": bool,
            "degraded": bool,
            "write_count": int,
            "write_error_count": int,
        },
        "audit": {
            "available": bool,
            "latest_cycle_id": Optional[str],
            "latest_timestamp": Optional[str],
        },
        "timestamp": str,
        "observer": {
            "name": str,
            "version": str,
            "schema_version": str,
        },
      }

    使用方式:
      observer = RuntimeObserver(orchestrator=orch)
      snap = observer.snapshot()  # 永远不抛异常
    """

    SCHEMA_VERSION = RUNTIME_OBSERVER_SCHEMA_VERSION
    NAME = RUNTIME_OBSERVER_NAME
    VERSION = RUNTIME_OBSERVER_VERSION

    def __init__(
        self,
        orchestrator: Any = None,           # RuntimeCycleOrchestrator
        history_index: Any = None,           # CycleHistoryIndex(可选,缺省从 orchestrator 取)
        persistence: Any = None,             # ActionPersistenceManager(可选,缺省从 orchestrator 取)
        recent_errors_limit: int = DEFAULT_RECENT_ERRORS_LIMIT,
        recent_cycles_limit: int = DEFAULT_RECENT_CYCLES_LIMIT,
    ) -> None:
        self._orchestrator = orchestrator
        self._history_index = history_index
        self._persistence = persistence
        self._recent_errors_limit = max(1, int(recent_errors_limit or DEFAULT_RECENT_ERRORS_LIMIT))
        self._recent_cycles_limit = max(1, int(recent_cycles_limit or DEFAULT_RECENT_CYCLES_LIMIT))
        self._lock = threading.RLock()
        # 内部统计
        self._snapshot_count: int = 0
        self._last_snapshot_ts: Optional[str] = None
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 依赖解析(惰性)
    # --------------------------------------------------------

    def _get_orchestrator(self) -> Any:
        return self._orchestrator

    def _get_history_index(self) -> Any:
        """获取 CycleHistoryIndex(优先用注入,缺省从 orchestrator 取)"""
        if self._history_index is not None:
            return self._history_index
        orch = self._get_orchestrator()
        if orch is None:
            return None
        # orchestrator._history 可能是私有属性,通过反射安全获取
        return _safe_getattr(orch, "_history", None)

    def _get_persistence(self) -> Any:
        """获取 ActionPersistenceManager(优先用注入,缺省从 orchestrator 取)"""
        if self._persistence is not None:
            return self._persistence
        orch = self._get_orchestrator()
        if orch is None:
            return None
        return _safe_getattr(orch, "persistence", None)

    def _get_adapters_list(self) -> List[Dict[str, Any]]:
        """从 orchestrator 获取已注册 adapter 列表(只读快照)"""
        orch = self._get_orchestrator()
        if orch is None:
            return []
        # 优先用 list_adapters(若有);否则 fallback 到 _adapters.list()
        lst_fn = _safe_getattr(orch, "list_adapters", None)
        if callable(lst_fn):
            try:
                result = lst_fn()
                if isinstance(result, list):
                    return list(result)
            except Exception:  # noqa: BLE001
                pass
        # fallback
        adapters_obj = _safe_getattr(orch, "_adapters", None)
        if adapters_obj is not None:
            list_fn = _safe_getattr(adapters_obj, "list", None)
            if callable(list_fn):
                try:
                    result = list_fn()
                    if isinstance(result, list):
                        return list(result)
                except Exception:  # noqa: BLE001
                    pass
        return []

    def _get_runtime_health(self) -> Dict[str, Any]:
        """安全获取 orchestrator.health_check() 结果"""
        orch = self._get_orchestrator()
        if orch is None:
            return {
                "healthy": False,
                "degraded": True,
                "enabled": False,
                "closed": False,
                "version": "",
                "error": "orchestrator_not_attached",
            }
        return _safe_call_dict(_safe_getattr(orch, "health_check", None))

    def _get_runtime_snapshot(self) -> Dict[str, Any]:
        """安全获取 orchestrator.snapshot() 结果"""
        orch = self._get_orchestrator()
        if orch is None:
            return {"error": "orchestrator_not_attached"}
        return _safe_call_dict(_safe_getattr(orch, "snapshot", None))

    # --------------------------------------------------------
    # Adapter 状态读取(只读)
    # --------------------------------------------------------

    def _read_adapter(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """读取单个 adapter 的健康/快照(只读,fail-soft)。

        兼容两种 health 格式(Phase C.7.1 contract):
          - {"healthy": bool}
          - {"status": "healthy" | "degraded"}
        """
        out: Dict[str, Any] = {
            "name": str(item.get("name", "") or ""),
            "healthy": False,
            "status": "unknown",
            "attached": False,
            "available": True,
            "error": None,
        }
        try:
            name = str(item.get("name", "") or "")
            # item 已包含 health(由 orchestrator._adapters.list() 提供)
            # 但 C.7.2 observer 优先直接调 adapter 自身的 health_check()(更准确)
            adapter_obj = item.get("adapter") or item.get("instance")
            if adapter_obj is None:
                # 退化:用 item 里的 health 字段
                raw_health = item.get("health", {}) or {}
                if isinstance(raw_health, dict):
                    out["healthy"] = _extract_healthy(raw_health)
                    out["status"] = _extract_status(raw_health)
            else:
                # 调 adapter.health_check()(只读)
                hc = _safe_call_dict(_safe_getattr(adapter_obj, "health_check", None))
                out["healthy"] = _extract_healthy(hc)
                out["status"] = _extract_status(hc)
                # 调 adapter.snapshot()(只读)
                snap = _safe_call_dict(_safe_getattr(adapter_obj, "snapshot", None))
                if isinstance(snap, dict):
                    out["snapshot"] = snap
                    out["attached"] = bool(snap.get("attached", False))
            # 兜底:attached 来自 item
            if not out["attached"]:
                out["attached"] = bool(item.get("attached", False))
            return out
        except Exception as exc:  # noqa: BLE001
            out["available"] = False
            out["error"] = repr(exc)
            return out

    # --------------------------------------------------------
    # Cycle History 读取(只读)
    # --------------------------------------------------------

    def _read_cycles(self) -> Dict[str, Any]:
        """读取 cycle 历史统计(只读)"""
        out: Dict[str, Any] = {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "success_rate": 0.0,
            "in_memory": 0,
            "latest_cycle_id": None,
            "latest_timestamp": None,
        }
        try:
            orch = self._get_orchestrator()
            if orch is not None:
                out["total"] = int(_safe_getattr(orch, "cycle_count", 0) or 0)
                out["completed"] = int(_safe_getattr(orch, "completed_count", 0) or 0)
                out["failed"] = int(_safe_getattr(orch, "failed_count", 0) or 0)
            # history in_memory
            hist = self._get_history_index()
            if hist is not None:
                count_fn = _safe_getattr(hist, "count", None)
                if callable(count_fn):
                    out["in_memory"] = int(count_fn() or 0)
                # 读最近 cycles(只读)
                list_fn = _safe_getattr(hist, "list", None)
                if callable(list_fn):
                    try:
                        recent = list_fn(limit=self._recent_cycles_limit)
                        if isinstance(recent, list) and recent:
                            latest = recent[0]
                            out["latest_cycle_id"] = str(_safe_getattr(latest, "cycle_id", "") or "")
                            out["latest_timestamp"] = str(_safe_getattr(latest, "timestamp", "") or "")
                    except Exception:  # noqa: BLE001
                        pass
            # success_rate
            total = int(out["total"] or 0)
            completed = int(out["completed"] or 0)
            if total > 0:
                try:
                    out["success_rate"] = round(float(completed) / float(total), 4)
                except Exception:  # noqa: BLE001
                    out["success_rate"] = 0.0
            return out
        except Exception:  # noqa: BLE001
            return out

    def _read_recent_errors(self) -> List[Dict[str, Any]]:
        """读取最近的错误列表(从 history 中提取,只读)"""
        errors: List[Dict[str, Any]] = []
        try:
            hist = self._get_history_index()
            if hist is None:
                return errors
            list_fn = _safe_getattr(hist, "list", None)
            if not callable(list_fn):
                return errors
            recent = list_fn(limit=self._recent_cycles_limit)
            if not isinstance(recent, list):
                return errors
            for ctx in recent:
                try:
                    err_count = int(_safe_getattr(ctx, "error_count", 0) or 0)
                    if err_count <= 0:
                        continue
                    degraded = _safe_getattr(ctx, "degraded_adapters", []) or []
                    if not isinstance(degraded, list):
                        degraded = list(degraded)
                    snap = _safe_call_dict(_safe_getattr(ctx, "snapshot", None))
                    errors.append({
                        "cycle_id": str(_safe_getattr(ctx, "cycle_id", "") or ""),
                        "session_id": str(_safe_getattr(ctx, "session_id", "") or ""),
                        "timestamp": str(_safe_getattr(ctx, "timestamp", "") or ""),
                        "error_count": int(err_count),
                        "degraded_adapters": list(degraded),
                    })
                    if len(errors) >= self._recent_errors_limit:
                        break
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        return errors

    # --------------------------------------------------------
    # Persistence 状态(只读)
    # --------------------------------------------------------

    def _read_persistence(self) -> Dict[str, Any]:
        """读取 persistence 状态(只读)"""
        out: Dict[str, Any] = {
            "available": False,
            "degraded": False,
            "write_count": 0,
            "write_error_count": 0,
        }
        try:
            pers = self._get_persistence()
            if pers is None:
                out["available"] = False
                return out
            out["available"] = True
            out["degraded"] = bool(_safe_getattr(pers, "is_degraded", False))
            out["write_count"] = int(_safe_getattr(pers, "write_count", 0) or 0)
            out["write_error_count"] = int(
                _safe_getattr(pers, "write_error_count", 0) or 0
            )
            return out
        except Exception:  # noqa: BLE001
            return out

    # --------------------------------------------------------
    # Audit Chain 状态(只读)
    # --------------------------------------------------------

    def _read_audit(self) -> Dict[str, Any]:
        """读取 audit chain 状态(只读)。

        C.7.2 范围:不引入新的 audit 模块,仅消费
          - persistence.get_recent_actions()(若可用)
          - history 内的 cycle 元数据(已有)
        """
        out: Dict[str, Any] = {
            "available": False,
            "latest_cycle_id": None,
            "latest_timestamp": None,
        }
        try:
            # 1) 优先从 persistence.get_recent_actions() 读(只读)
            pers = self._get_persistence()
            if pers is not None:
                get_recent = _safe_getattr(pers, "get_recent_actions", None)
                if callable(get_recent):
                    try:
                        recents = get_recent(limit=1)
                        if isinstance(recents, list) and recents:
                            latest = recents[0]
                            if isinstance(latest, dict):
                                out["latest_cycle_id"] = str(latest.get("action_id", "") or "")
                                out["available"] = True
                                ts = latest.get("ts", 0.0)
                                if ts:
                                    try:
                                        out["latest_timestamp"] = _format_ts(float(ts))
                                    except Exception:  # noqa: BLE001
                                        out["latest_timestamp"] = str(ts)
                    except Exception:  # noqa: BLE001
                        pass
            # 2) fallback:从 history 取
            if not out["latest_cycle_id"]:
                hist = self._get_history_index()
                if hist is not None:
                    list_fn = _safe_getattr(hist, "list", None)
                    if callable(list_fn):
                        try:
                            recent = list_fn(limit=1)
                            if isinstance(recent, list) and recent:
                                latest = recent[0]
                                out["latest_cycle_id"] = str(
                                    _safe_getattr(latest, "cycle_id", "") or ""
                                )
                                out["latest_timestamp"] = str(
                                    _safe_getattr(latest, "timestamp", "") or ""
                                )
                                out["available"] = True
                        except Exception:  # noqa: BLE001
                            pass
            return out
        except Exception:  # noqa: BLE001
            return out

    # --------------------------------------------------------
    # 主入口:snapshot
    # --------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """生成 Runtime 可观测快照(只读,绝不抛异常)。

        任何组件异常都被内部 try/except 隔离,整体降级为 degraded + error。
        """
        try:
            with self._lock:
                # 1) runtime 整体健康
                rt_health = self._get_runtime_health()
                rt_snap = self._get_runtime_snapshot()
                # 若两者都为空(orchestrator.health_check() 和 snapshot() 都失败),
                # 视为 runtime 完全不可用,标记 degraded。
                rt_unavailable = (
                    not rt_health and not rt_snap
                ) or bool(rt_health.get("error")) or bool(rt_snap.get("error"))
                runtime: Dict[str, Any] = {
                    "name": str(
                        rt_health.get("name", "")
                        or rt_snap.get("name", "")
                        or "RuntimeCycleOrchestrator"
                    ),
                    "version": str(
                        rt_health.get("version", "") or rt_snap.get("version", "") or ""
                    ),
                    "schema_version": str(
                        rt_health.get("schema_version", "")
                        or rt_snap.get("schema_version", "")
                        or ""
                    ),
                    "healthy": False if rt_unavailable else bool(
                        rt_health.get("healthy", rt_snap.get("healthy", False))
                    ),
                    "degraded": True if rt_unavailable else bool(
                        rt_health.get("degraded", rt_snap.get("degraded", False))
                    ),
                    "enabled": bool(
                        rt_health.get("enabled", rt_snap.get("enabled", False))
                    ),
                    "closed": bool(
                        rt_health.get("closed", rt_snap.get("closed", False))
                    ),
                    "cycle_count": int(
                        rt_health.get("cycle_count", rt_snap.get("cycle_count", 0)) or 0
                    ),
                    "completed_count": int(
                        rt_health.get("completed_count", rt_snap.get("completed_count", 0))
                        or 0
                    ),
                    "failed_count": int(
                        rt_health.get("failed_count", rt_snap.get("failed_count", 0))
                        or 0
                    ),
                    "consecutive_failures": int(
                        rt_health.get(
                            "consecutive_failures",
                            rt_snap.get("consecutive_failures", 0),
                        )
                        or 0
                    ),
                }
                if rt_unavailable:
                    runtime["error"] = "orchestrator_unavailable"

                # 2) adapters 健康聚合
                adapters_out: Dict[str, Any] = {}
                for std_name in STANDARD_ADAPTER_NAMES:
                    adapters_out[std_name] = {
                        "name": std_name,
                        "healthy": False,
                        "status": "not_registered",
                        "attached": False,
                        "available": False,
                    }
                # 遍历 orchestrator 实际 adapter
                for item in self._get_adapters_list():
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "") or "").strip().lower()
                    if not name:
                        continue
                    if name not in adapters_out:
                        adapters_out[name] = {
                            "name": name,
                            "healthy": False,
                            "status": "unknown",
                            "attached": False,
                            "available": True,
                        }
                    adapters_out[name] = self._read_adapter(item)

                # 3) cycles 统计
                cycles = self._read_cycles()

                # 4) errors
                rt_last_error = str(
                    rt_health.get("last_error", "") or rt_snap.get("last_error", "") or ""
                ) or None
                recent_errors = self._read_recent_errors()
                errors_block: Dict[str, Any] = {
                    "last_error": rt_last_error,
                    "consecutive_failures": runtime["consecutive_failures"],
                    "recent_errors": recent_errors,
                }

                # 5) persistence
                persistence_block = self._read_persistence()

                # 6) audit
                audit_block = self._read_audit()
                # audit.latest_cycle_id 优先采用 history 的最新 cycle
                if not audit_block.get("latest_cycle_id") and cycles.get("latest_cycle_id"):
                    audit_block["latest_cycle_id"] = cycles["latest_cycle_id"]
                if not audit_block.get("latest_timestamp") and cycles.get("latest_timestamp"):
                    audit_block["latest_timestamp"] = cycles["latest_timestamp"]
                    audit_block["available"] = True

                # 7) 组装最终 snapshot
                snap: Dict[str, Any] = {
                    "schema_version": self.SCHEMA_VERSION,
                    "runtime": runtime,
                    "adapters": adapters_out,
                    "cycles": cycles,
                    "errors": errors_block,
                    "persistence": persistence_block,
                    "audit": audit_block,
                    "timestamp": _now_iso(),
                    "observer": {
                        "name": self.NAME,
                        "version": self.VERSION,
                        "schema_version": self.SCHEMA_VERSION,
                    },
                }

                # 8) 内部统计
                self._snapshot_count += 1
                self._last_snapshot_ts = snap["timestamp"]
                self._last_error = None
                return snap
        except Exception as exc:  # noqa: BLE001
            # 兜底:任何未被捕获的异常也降级返回
            with self._lock:
                self._last_error = repr(exc)
            logger.debug(f"[phase_c7_2] observer.snapshot 异常(已隔离): {exc}")
            return {
                "schema_version": self.SCHEMA_VERSION,
                "runtime": {
                    "name": "RuntimeCycleOrchestrator",
                    "healthy": False,
                    "degraded": True,
                    "enabled": False,
                    "closed": False,
                    "error": repr(exc),
                },
                "adapters": {},
                "cycles": {
                    "total": 0,
                    "completed": 0,
                    "failed": 0,
                    "success_rate": 0.0,
                    "in_memory": 0,
                    "latest_cycle_id": None,
                    "latest_timestamp": None,
                },
                "errors": {
                    "last_error": repr(exc),
                    "consecutive_failures": 0,
                    "recent_errors": [],
                },
                "persistence": {
                    "available": False,
                    "degraded": False,
                    "write_count": 0,
                    "write_error_count": 0,
                },
                "audit": {
                    "available": False,
                    "latest_cycle_id": None,
                    "latest_timestamp": None,
                },
                "timestamp": _now_iso(),
                "observer": {
                    "name": self.NAME,
                    "version": self.VERSION,
                    "schema_version": self.SCHEMA_VERSION,
                },
            }

    # --------------------------------------------------------
    # 状态查询(只读)
    # --------------------------------------------------------

    @property
    def snapshot_count(self) -> int:
        with self._lock:
            return int(self._snapshot_count)

    @property
    def last_snapshot_ts(self) -> Optional[str]:
        with self._lock:
            return self._last_snapshot_ts

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def is_healthy(self) -> bool:
        """Observer 自身是否健康(最近一次 snapshot 无异常)。"""
        with self._lock:
            return self._last_error is None

    def get_stats(self) -> Dict[str, Any]:
        """Observer 自身统计(用于自检/调试)"""
        with self._lock:
            return {
                "snapshot_count": int(self._snapshot_count),
                "last_snapshot_ts": self._last_snapshot_ts,
                "last_error": self._last_error,
                "has_orchestrator": self._orchestrator is not None,
                "has_history_index": self._history_index is not None,
                "has_persistence": self._persistence is not None,
            }


# ============================================================
# 内部辅助:health 字段归一化(与 C.7.1 contract 一致)
# ============================================================


def _extract_healthy(health: Any) -> bool:
    """从 health dict 中提取 healthy(bool)。"""
    try:
        if not isinstance(health, dict):
            return False
        # 优先 "healthy" 字段
        if "healthy" in health:
            return bool(health["healthy"])
        # 退化 "status" 字段
        status = str(health.get("status", "") or "").strip().lower()
        return status == "healthy"
    except Exception:  # noqa: BLE001
        return False


def _extract_status(health: Any) -> str:
    """从 health dict 中提取 status(str)。"""
    try:
        if not isinstance(health, dict):
            return "unknown"
        # 优先 "status" 字段
        if "status" in health:
            return str(health.get("status", "") or "unknown")
        # 退化 "healthy" 字段
        if "healthy" in health:
            return "healthy" if bool(health["healthy"]) else "degraded"
        return "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _format_ts(ts: float) -> str:
    """将 float epoch 转为 ISO 8601 UTC(fail-soft)"""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    except Exception:  # noqa: BLE001
        return str(ts)


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_runtime_observer(
    orchestrator: Any = None,
    history_index: Any = None,
    persistence: Any = None,
    recent_errors_limit: int = DEFAULT_RECENT_ERRORS_LIMIT,
    recent_cycles_limit: int = DEFAULT_RECENT_CYCLES_LIMIT,
) -> RuntimeObserver:
    """工厂函数:创建一个 RuntimeObserver。"""
    return RuntimeObserver(
        orchestrator=orchestrator,
        history_index=history_index,
        persistence=persistence,
        recent_errors_limit=recent_errors_limit,
        recent_cycles_limit=recent_cycles_limit,
    )


def safe_get_runtime_observer_summary(
    observer: Optional["RuntimeObserver"],
) -> Dict[str, Any]:
    """全局安全 summary(任何异常都吸收)"""
    empty: Dict[str, Any] = {
        "schema_version": RUNTIME_OBSERVER_SCHEMA_VERSION,
        "runtime": {
            "name": "RuntimeCycleOrchestrator",
            "healthy": False,
            "degraded": True,
            "enabled": False,
        },
        "adapters": {},
        "cycles": {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "success_rate": 0.0,
            "in_memory": 0,
        },
        "errors": {"last_error": None, "recent_errors": []},
        "persistence": {"available": False, "degraded": False},
        "audit": {"available": False},
        "timestamp": _now_iso(),
        "observer": {
            "name": RUNTIME_OBSERVER_NAME,
            "version": RUNTIME_OBSERVER_VERSION,
            "schema_version": RUNTIME_OBSERVER_SCHEMA_VERSION,
        },
    }
    if observer is None:
        return empty
    try:
        return observer.snapshot() or empty
    except Exception:  # noqa: BLE001
        return empty


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "RUNTIME_OBSERVER_SCHEMA_VERSION",
    "RUNTIME_OBSERVER_NAME",
    "RUNTIME_OBSERVER_VERSION",
    "STANDARD_ADAPTER_NAMES",
    "RuntimeObserver",
    "create_runtime_observer",
    "safe_get_runtime_observer_summary",
    "_extract_healthy",
    "_extract_status",
    "_now_iso",
]
