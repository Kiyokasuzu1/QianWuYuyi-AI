# -*- coding: utf-8 -*-
"""
src/admin/runtime_dashboard_provider.py

Phase 5.0 Dashboard Upgrade —— Runtime Dashboard Provider。

职责:
- 只读提供 Runtime 相关数据给 Dashboard
- 数据源:RuntimeProvider + RuntimeIntegrationHost(可注入)+ RuntimeSnapshot(只读)
- 不修改任何 Runtime 状态
- 不直接 import 任何 src/runtime/** 业务模块
  (通过 RuntimeProvider / RuntimeBridge / EventHub 访问)

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 所有方法严格容错,任何子组件不可用时返回 fallback

API:
- get_runtime_status() -> {initialized, running, uptime, current_state, ...}
- get_lifecycle_tasks() -> [task_view]
- get_tick_history(limit) -> [tick_view]
- get_integration_events(limit) -> [event_view]
- get_live2d_signal() -> {available, expression, motion, reason, timestamp, readonly, fallback, fallback_reason}
  Step 8.4.6: 只读 Live2D Snapshot 联动,数据全部来自 Runtime Snapshot
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from src.admin.dashboard.event_hub import get_dashboard_event_hub

logger = logging.getLogger(__name__)


# ============================================================
# Host 注入(用于测试 + 生产)
# ============================================================
_host_singleton: Dict[str, Any] = {}


def set_runtime_integration_host_for_dashboard(host: Any) -> None:
    """
    注入 RuntimeIntegrationHost(由 api_server.py 在 init 时调用)。

    Dashboard 不允许自行 import / 创建 Host,只能接收注入。
    """
    _host_singleton["host"] = host


def clear_runtime_integration_host_for_dashboard() -> None:
    """测试用:清除 host 注入。"""
    _host_singleton.pop("host", None)


def _get_injected_host() -> Optional[Any]:
    return _host_singleton.get("host")


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _safe_call(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        if fn is None:
            return None
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime_dashboard_provider safe_call 失败: %s", exc)
        return None


# ============================================================
# Provider
# ============================================================
class RuntimeDashboardProvider:
    """
    Runtime Dashboard 只读 Provider。

    注入:
        runtime_provider: 已有 RuntimeProvider(测试时可注入 mock)
        host:             可选,RuntimeIntegrationHost(由 api_server.py 注入)
    """

    def __init__(
        self,
        runtime_provider: Optional[Any] = None,
        host: Optional[Any] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._runtime_provider = runtime_provider
        self._host = host
        # 缓存 host,确保测试时能拿到注入
        if host is not None:
            _host_singleton["host"] = host

    # --------------------------------------------------------
    # 子 Provider 懒加载
    # --------------------------------------------------------
    def _get_runtime_provider(self) -> Optional[Any]:
        with self._lock:
            if self._runtime_provider is not None:
                return self._runtime_provider
            try:
                from src.admin.runtime_provider import get_runtime_provider
                self._runtime_provider = get_runtime_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("RuntimeDashboardProvider: RuntimeProvider 不可用: %s", exc)
                self._runtime_provider = None
            return self._runtime_provider

    def _get_host(self) -> Optional[Any]:
        """获取 Host:优先注入,其次模块级注入。"""
        if self._host is not None:
            return self._host
        return _get_injected_host()

    # --------------------------------------------------------
    # Runtime Status
    # --------------------------------------------------------
    def get_runtime_status(self) -> Dict[str, Any]:
        """
        获取 Runtime 完整状态。

        Returns:
            {
                "initialized": bool,
                "running": bool,
                "uptime": float|None,     # 秒
                "current_state": str|None,
                "online": bool,
                "tick_count": int,
                "bridge_error": str|None,
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        rp = self._get_runtime_provider()
        if rp is None:
            return {
                "initialized": False,
                "running": False,
                "uptime": None,
                "current_state": None,
                "online": False,
                "tick_count": 0,
                "bridge_error": "runtime_provider_unavailable",
                "available": False,
                "fallback": True,
                "fallback_reason": "runtime_provider_unavailable",
            }

        try:
            status = _safe_call(rp.get_status) or {}
            runtime = status.get("runtime", {}) if isinstance(status, dict) else {}
            online = bool(status.get("online", False))
            initialized = bool(runtime.get("initialized", False))
            running = bool(runtime.get("is_running", False))

            # uptime / current_state 优先从 Host 拿
            uptime: Optional[float] = None
            current_state: Optional[str] = None
            tick_count: int = 0
            host = self._get_host()
            if host is not None:
                try:
                    started_at = _safe_get(host, "started_at")
                    if started_at:
                        uptime = max(0.0, float(time.time() - float(started_at)))
                    current_state = _safe_get(host, "state")
                    tick_count = int(_safe_get(host, "tick_count", 0) or 0)
                except Exception:
                    pass

            return {
                "initialized": initialized,
                "running": running,
                "uptime": uptime,
                "current_state": current_state,
                "online": online,
                "tick_count": tick_count,
                "bridge_error": status.get("bridge_error"),
                "available": True,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider.get_runtime_status 异常: %s", exc)
            return {
                "initialized": False,
                "running": False,
                "uptime": None,
                "current_state": None,
                "online": False,
                "tick_count": 0,
                "bridge_error": str(exc),
                "available": False,
                "fallback": True,
                "fallback_reason": f"runtime_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Lifecycle Tasks(来自 Host.adapters)
    # --------------------------------------------------------
    def get_lifecycle_tasks(self) -> Dict[str, Any]:
        """
        获取 Lifecycle Task(adapter) 列表。

        Returns:
            {
                "tasks": [task_view],
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        host = self._get_host()
        if host is None:
            return {
                "tasks": [],
                "available": False,
                "fallback": True,
                "fallback_reason": "integration_host_unavailable",
            }

        try:
            adapters = _safe_get(host, "adapters") or {}
            if not isinstance(adapters, dict):
                adapters = {}

            tasks: List[Dict[str, Any]] = []
            for name, adapter in adapters.items():
                desc = _safe_call(_safe_get(adapter, "describe")) or {}
                if not isinstance(desc, dict):
                    desc = {}
                available = bool(desc.get("available", False))
                error_count = int(desc.get("error_count", 0) or 0)
                emitted_count = int(desc.get("emitted_count", 0) or 0)
                tasks.append({
                    "task_name": str(name),
                    "status": "running" if available else "stopped",
                    "last_tick_event_id": desc.get("last_emit_event_id", "") or "",
                    "emitted_count": emitted_count,
                    "error_count": error_count,
                    "last_error": desc.get("last_error", "") or "",
                    "owner": desc.get("owner", "") or "",
                    "fallback": False,
                })

            return {
                "tasks": tasks,
                "available": True,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider.get_lifecycle_tasks 异常: %s", exc)
            return {
                "tasks": [],
                "available": False,
                "fallback": True,
                "fallback_reason": f"tasks_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Tick History(从 event_log 拉最近事件)
    # --------------------------------------------------------
    def get_tick_history(self, limit: int = 20) -> Dict[str, Any]:
        """
        获取最近 tick(事件)记录。

        Returns:
            {
                "ticks": [event_view],
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            n = 20

        try:
            hub = get_dashboard_event_hub()
            events = hub.poll_recent(limit=n)
        except Exception as exc:  # noqa: BLE001
            return {
                "ticks": [],
                "available": False,
                "fallback": True,
                "fallback_reason": f"hub_error:{type(exc).__name__}",
            }

        return {
            "ticks": events,
            "available": True,
            "fallback": False,
            "fallback_reason": None,
        }

    # --------------------------------------------------------
    # Integration Events(同 tick,但单独语义)
    # --------------------------------------------------------
    def get_integration_events(self, limit: int = 20) -> Dict[str, Any]:
        """与 get_tick_history 同源,独立 endpoint 便于前端订阅。"""
        return self.get_tick_history(limit=limit)

    # --------------------------------------------------------
    # Live2D Signal(Step 8.4.6)—— 只读联动 Runtime Snapshot
    # --------------------------------------------------------
    # Live2D snapshot 默认路径(与 RuntimeSnapshotStore 共享同一 JSON 文件)
    DEFAULT_LIVE2D_SNAPSHOT_PATH = "data/runtime_snapshot.json"

    # 文件大小保护(防巨型 JSON 爆炸)
    _LIVE2D_SNAPSHOT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB

    # Live2D 字段白名单(只读这些字段,严禁注入)
    _LIVE2D_REQUIRED_FIELDS = ("expression", "motion", "reason", "timestamp")
    _LIVE2D_OPTIONAL_FIELDS = ("confidence", "source", "version")

    def get_live2d_signal(self) -> Dict[str, Any]:
        """
        Step 8.4.6: 获取 Live2D 状态信号(只读,全部来自 Runtime Snapshot)。

        数据流:
            Runtime Snapshot (JSON file) → _read_raw_snapshot → 提取 live2d 字段

        行为契约:
        - 严格只读,绝不修改任何 Runtime / Snapshot 状态
        - 绝不调用 emotion / growth / personality 模块
        - 绝不自己推理 Live2D 动作(必须来自 snapshot)
        - 任何异常路径返回 fallback,不抛

        Returns:
            正常:
                {
                    "available": true,
                    "expression": "neutral",
                    "motion": "idle",
                    "reason": "runtime_snapshot",
                    "timestamp": "...",
                    "readonly": true,
                    "fallback": false,
                    "fallback_reason": None,
                }

            Runtime 不存在 / 不可用:
                {
                    "available": false,
                    "fallback": true,
                    "fallback_reason": "runtime_snapshot_unavailable",
                    "expression": None,
                    "motion": None,
                    "reason": None,
                    "timestamp": None,
                    "readonly": True,
                }

            Snapshot 没有 live2d 字段:
                {
                    "available": false,
                    "fallback": True,
                    "fallback_reason": "live2d_signal_not_found",
                    ...
                }

            字段异常:
                {
                    "available": false,
                    "fallback": True,
                    "fallback_reason": "live2d_signal_invalid",
                    ...
                }
        """
        # 1) 读取 raw snapshot
        raw = self._read_raw_snapshot()
        if raw is None:
            return {
                "available": False,
                "fallback": True,
                "fallback_reason": "runtime_snapshot_unavailable",
                "expression": None,
                "motion": None,
                "reason": None,
                "timestamp": None,
                "readonly": True,
            }

        # 2) 提取 live2d 字段
        live2d = raw.get("live2d") if isinstance(raw, dict) else None
        if not isinstance(live2d, dict):
            return {
                "available": False,
                "fallback": True,
                "fallback_reason": "live2d_signal_not_found",
                "expression": None,
                "motion": None,
                "reason": None,
                "timestamp": None,
                "readonly": True,
            }

        # 3) 校验必填字段
        missing = [f for f in self._LIVE2D_REQUIRED_FIELDS if f not in live2d or live2d.get(f) is None]
        if missing:
            return {
                "available": False,
                "fallback": True,
                "fallback_reason": "live2d_signal_invalid",
                "missing_fields": missing,
                "expression": None,
                "motion": None,
                "reason": None,
                "timestamp": None,
                "readonly": True,
            }

        # 4) 提取并清理字段(只取白名单,其他一律忽略)
        try:
            expression = str(live2d.get("expression", ""))
        except Exception:  # noqa: BLE001
            expression = ""
        try:
            motion = str(live2d.get("motion", ""))
        except Exception:  # noqa: BLE001
            motion = ""
        try:
            reason = str(live2d.get("reason", "runtime_snapshot"))
        except Exception:  # noqa: BLE001
            reason = "runtime_snapshot"
        try:
            timestamp = str(live2d.get("timestamp", "") or "")
        except Exception:  # noqa: BLE001
            timestamp = ""

        # 5) 字段值异常兜底(空字符串视为无效)
        if not expression or not motion:
            return {
                "available": False,
                "fallback": True,
                "fallback_reason": "live2d_signal_invalid",
                "expression": None,
                "motion": None,
                "reason": None,
                "timestamp": None,
                "readonly": True,
            }

        return {
            "available": True,
            "expression": expression,
            "motion": motion,
            "reason": reason,
            "timestamp": timestamp,
            "readonly": True,
            "fallback": False,
            "fallback_reason": None,
        }

    def _read_raw_snapshot(self) -> Optional[Dict[str, Any]]:
        """
        读取 Runtime Snapshot 原始 dict(不通过 RuntimeSnapshot 类)。

        设计要点:
        - 不实例化 RuntimeSnapshot,避免受其字段 schema 约束
        - 直接 JSON 解析,允许读取 schema 外字段(如 live2d)
        - 任何异常返回 None,绝不抛
        - 支持测试注入:可通过 set_live2d_snapshot_store_for_dashboard() 覆盖
        """
        # 1) 测试/生产注入
        injected = self._get_live2d_store()
        if injected is not None:
            try:
                getter = getattr(injected, "read_raw", None) or getattr(injected, "load", None)
                if callable(getter):
                    obj = getter()
                    if isinstance(obj, dict):
                        return obj
                    # 如果是 RuntimeSnapshot 之类的对象,尝试 to_dict
                    to_dict = getattr(obj, "to_dict", None)
                    if callable(to_dict):
                        d = to_dict()
                        if isinstance(d, dict):
                            return d
                    return None
            except Exception as exc:  # noqa: BLE001
                logger.debug("RuntimeDashboardProvider: 注入 store 读取失败: %s", exc)
                return None

        # 2) 默认:从磁盘读取 DEFAULT_LIVE2D_SNAPSHOT_PATH
        path = self.DEFAULT_LIVE2D_SNAPSHOT_PATH
        try:
            if not os.path.exists(path):
                return None
            size = os.path.getsize(path)
            if size > self._LIVE2D_SNAPSHOT_MAX_BYTES:
                logger.warning("RuntimeDashboardProvider: snapshot 文件过大 %d > %d", size, self._LIVE2D_SNAPSHOT_MAX_BYTES)
                return None
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider: snapshot 读盘失败: %s", exc)
            return None

        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider: snapshot JSON 解析失败: %s", exc)
            return None

        if not isinstance(obj, dict):
            return None
        return obj

    # --------------------------------------------------------
    # Live2D Snapshot Store 注入(供测试 + 生产)
    # --------------------------------------------------------
    _live2d_store_singleton: Dict[str, Any] = {}

    def _get_live2d_store(self) -> Optional[Any]:
        # 实例级 > 单例级
        with self._lock:
            if getattr(self, "_live2d_store_injected", None) is not None:
                return self._live2d_store_injected
        return self._live2d_store_singleton.get("store")

    def set_live2d_snapshot_store(self, store: Any) -> None:
        """注入 Live2D snapshot 读取器(供测试)。"""
        with self._lock:
            self._live2d_store_injected = store

    # ========================================================
    # Phase 7.0 Dashboard Runtime Center —— 新增方法
    # ========================================================
    # 以下方法从 src.runtime.trace_recorder / status_tracker 的单例读取数据。
    # 这两个模块是"观察层",不是 Runtime Core 业务模块,符合 Dashboard 只读约束。
    # 任何异常返回 fallback,绝不抛出。
    # ========================================================

    def _get_status_tracker(self) -> Optional[Any]:
        """懒加载 RuntimeStatusTracker 单例(Phase 7.0)。"""
        try:
            from src.runtime.status_tracker import get_runtime_status_tracker
            return get_runtime_status_tracker()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider: status_tracker 不可用: %s", exc)
            return None

    def _get_trace_recorder(self) -> Optional[Any]:
        """懒加载 RuntimeTraceRecorder 单例(Phase 7.0)。"""
        try:
            from src.runtime.trace_recorder import get_runtime_trace_recorder
            return get_runtime_trace_recorder()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider: trace_recorder 不可用: %s", exc)
            return None

    def get_runtime_lifecycle_status(self) -> Dict[str, Any]:
        """Phase 7.0: 获取 Runtime 生命周期状态(uptime/task/event/response)。

        数据源: RuntimeStatusTracker 单例(同进程内存)。

        Returns:
            {
                "status": str,             # "running" / "unknown"
                "started_at": str,         # ISO
                "uptime_seconds": float,
                "uptime_human": str,       # "12h 32m 5s"
                "current_task": str,       # "idle" / "processing" / ...
                "last_event": str|None,
                "last_event_at": str|None,
                "last_response_at": str|None,
                "last_response_age_seconds": float|None,
                "last_response_preview": str,
                "last_trace_id": str|None,
                "last_error": str|None,
                "request_count": int,
                "success_count": int,
                "failure_count": int,
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None,
            }
        """
        tracker = self._get_status_tracker()
        if tracker is None:
            return {
                "status": "unknown",
                "available": False,
                "fallback": True,
                "fallback_reason": "status_tracker_unavailable",
            }
        try:
            data = tracker.get_status()
            data["available"] = True
            data["fallback"] = False
            data["fallback_reason"] = None
            return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider.get_runtime_lifecycle_status 异常: %s", exc)
            return {
                "status": "unknown",
                "available": False,
                "fallback": True,
                "fallback_reason": f"tracker_error:{type(exc).__name__}",
            }

    def get_recent_traces(self, limit: int = 20) -> Dict[str, Any]:
        """Phase 7.0: 获取最近 N 条请求链路 trace。

        数据源: RuntimeTraceRecorder 单例(内存缓存)。

        Returns:
            {
                "traces": [trace_record],
                "count": int,
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None,
            }
        """
        recorder = self._get_trace_recorder()
        if recorder is None:
            return {
                "traces": [],
                "count": 0,
                "available": False,
                "fallback": True,
                "fallback_reason": "trace_recorder_unavailable",
            }
        try:
            n = max(1, min(200, int(limit)))
            traces = recorder.get_recent_traces(limit=n)
            return {
                "traces": traces,
                "count": len(traces),
                "available": True,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider.get_recent_traces 异常: %s", exc)
            return {
                "traces": [],
                "count": 0,
                "available": False,
                "fallback": True,
                "fallback_reason": f"recorder_error:{type(exc).__name__}",
            }

    def get_services_status(self) -> Dict[str, Any]:
        """Phase 7.0: 获取 4 个服务(api_server/runtime/initiative_sender/agent_server)状态。

        数据源:
        - api_server: RuntimeStatusTracker(同进程内存)
        - runtime: RuntimeProvider(同进程)
        - initiative_sender + agent_server: data/agent_server_status.json(跨进程文件)

        Returns:
            {
                "api_server": {...},
                "runtime": {...},
                "initiative_sender": {...},
                "agent_server": {...},
                "available": bool,
                "fallback": bool,
                "fallback_reason": str|None,
            }
        """
        try:
            from src.runtime.status_tracker import (
                AgentServerStatusReader,
                get_services_status as _get_services_status,
            )
            tracker = self._get_status_tracker()
            rp = self._get_runtime_provider()
            reader = AgentServerStatusReader()
            return _get_services_status(
                status_tracker=tracker,
                runtime_provider=rp,
                agent_status_reader=reader,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDashboardProvider.get_services_status 异常: %s", exc)
            return {
                "api_server": {"status": "unknown", "fallback": True},
                "runtime": {"status": "unknown", "fallback": True},
                "initiative_sender": {"status": "unknown", "fallback": True},
                "agent_server": {"status": "unknown", "fallback": True},
                "available": False,
                "fallback": True,
                "fallback_reason": f"services_error:{type(exc).__name__}",
            }


# 全局注入(供 api_server.py 在启动时设置)
def set_live2d_snapshot_store_for_dashboard(store: Any) -> None:
    """
    注入 Live2D snapshot 读取器(全局,生产环境用)。
    可传入一个具有 read_raw() 或 load() 方法的对象,返回 dict。
    """
    RuntimeDashboardProvider._live2d_store_singleton["store"] = store


def clear_live2d_snapshot_store_for_dashboard() -> None:
    """测试用:清除 Live2D snapshot store 注入。"""
    RuntimeDashboardProvider._live2d_store_singleton.pop("store", None)


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[RuntimeDashboardProvider] = None
_provider_lock = threading.Lock()


def get_runtime_dashboard_provider() -> RuntimeDashboardProvider:
    """获取 RuntimeDashboardProvider 单例(懒加载)。"""
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = RuntimeDashboardProvider()
    return _provider_instance


def reset_runtime_dashboard_provider_for_testing() -> None:
    """测试用:重置单例 + 清除 host 注入 + 清除 live2d store 注入。"""
    global _provider_instance
    with _provider_lock:
        _provider_instance = None
    clear_runtime_integration_host_for_dashboard()
    clear_live2d_snapshot_store_for_dashboard()


__all__ = [
    "RuntimeDashboardProvider",
    "get_runtime_dashboard_provider",
    "reset_runtime_dashboard_provider_for_testing",
    "set_runtime_integration_host_for_dashboard",
    "clear_runtime_integration_host_for_dashboard",
    "set_live2d_snapshot_store_for_dashboard",
    "clear_live2d_snapshot_store_for_dashboard",
]
