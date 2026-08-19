# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/runtime_service.py

Phase C.10.2 / C.10.4 —— Runtime Service(远程版本 + 缓存 + 事件)

通过 RemoteProviderBridge 访问 Yuyi Server API。
不直接 import 任何 src.* 模块。

Phase C.10.4 新增:
- 缓存:最近一次成功的 runtime snapshot
- Schema 校验:envelope 不兼容时返回 schema_mismatch
- 事件:RuntimeUpdated / ConnectionChanged

约束(强):
- 不允许 import 任何 src.* 业务模块
- 禁止调用任何写接口
- 失败返回 fallback dict
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from yuyi_desktop.core.cache.remote_snapshot_cache import (
    RemoteSnapshotCache,
    get_remote_snapshot_cache,
)
from yuyi_desktop.core.errors import DesktopError
from yuyi_desktop.core.events.event_bus import (
    EventTypes,
    get_event_bus,
)
from yuyi_desktop.core.remote_provider_bridge import (
    RemoteProviderBridge,
    get_remote_provider_bridge,
)
from yuyi_desktop.core.schema_validator import (
    SchemaValidator,
    get_schema_validator,
)

logger = logging.getLogger(__name__)


CACHE_KEY = "runtime.snapshot"


class RuntimeService:
    """Runtime 数据只读服务(远程 + 缓存 + 事件)。"""

    def __init__(
        self,
        bridge: Optional[RemoteProviderBridge] = None,
        cache: Optional[RemoteSnapshotCache] = None,
        schema_validator: Optional[SchemaValidator] = None,
        event_bus: Optional[Any] = None,
        enable_schema_check: bool = True,
        enable_events: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._bridge = bridge if bridge is not None else get_remote_provider_bridge()
        self._cache = cache if cache is not None else get_remote_snapshot_cache()
        self._schema_validator = (
            schema_validator if schema_validator is not None
            else get_schema_validator()
        )
        self._enable_schema_check = bool(enable_schema_check)
        self._event_bus = event_bus if event_bus is not None else get_event_bus()
        self._enable_events = bool(enable_events)

    # --------------------------------------------------------
    # 旧 API(向后兼容)
    # --------------------------------------------------------
    def get_status_envelope(self) -> Dict[str, Any]:
        """获取 Runtime 状态完整 envelope。"""
        with self._lock:
            return self._bridge.get_runtime_snapshot()

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_runtime_status_data()

    def get_health(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_health_data()

    def get_runtime_status_v2(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_runtime_status_v2_data()

    def get_overview_envelope(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_runtime_overview()

    def get_lifecycle_tasks(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_runtime_tasks_data()

    def get_tick_history(self, limit: int = 20) -> List[Any]:
        with self._lock:
            try:
                n = max(1, min(200, int(limit)))
            except (TypeError, ValueError):
                n = 20
            return self._bridge.get_runtime_ticks_data()[:n]

    def get_overview(self) -> Dict[str, Any]:
        """Dashboard 主页用:整合 runtime_status + lifecycle + ticks。"""
        with self._lock:
            overview_envelope = self.get_overview_envelope()
            overview_data = (
                overview_envelope.get("data", {})
                if isinstance(overview_envelope, dict) else {}
            )
            status_envelope = self.get_status_envelope()
            status = self.get_status()
            v2 = self.get_runtime_status_v2()
            tasks = self.get_lifecycle_tasks()
            ticks = self.get_tick_history(limit=5)
            online = bool(v2.get("online", status.get("online", False)))
            health = v2.get("health", "unknown")
            adapters = v2.get("adapters", {}) if isinstance(v2, dict) else {}
            return {
                "status": status,
                "v2": v2,
                "overview": overview_data if isinstance(overview_data, dict) else {},
                "task_count": len(tasks.get("tasks", []) or []),
                "task_available": bool(
                    tasks.get("available", status_envelope.get("success", False))
                ),
                "recent_tick_count": len(ticks),
                "available": bool(status_envelope.get("success", False)) or bool(
                    overview_envelope.get("success", False)
                ),
                "degraded": bool(status_envelope.get("degraded", False)),
                "online": online,
                "health": health,
                "adapters": adapters if isinstance(adapters, dict) else {},
            }

    # --------------------------------------------------------
    # C.10.4 新 API:缓存 + 事件
    # --------------------------------------------------------
    def refresh(self) -> Dict[str, Any]:
        """
        拉取 Runtime 状态,写入缓存,发布事件。

        Returns:
            envelope。失败时 envelope.success=False;同时会发布 ConnectionChanged 事件。
        """
        with self._lock:
            envelope = self._bridge.get_runtime_snapshot()
            return self._handle_envelope(envelope)

    def get_snapshot_with_cache(self) -> Dict[str, Any]:
        """
        获取 snapshot(优先用 envelope,失败时回退到缓存)。

        Returns:
            {
                "data": ...,            # 实际数据
                "source": "live" | "cache",
                "online": bool,
                "last_update_time": str,
                "is_stale": bool,
                "degraded": bool,
                "offline": bool,        # cache-only(无新鲜数据)
                "schema_version": str,
            }
        """
        with self._lock:
            envelope = self._bridge.get_runtime_snapshot()
            self._handle_envelope(envelope)
            return self._build_view(envelope)

    def get_cached_snapshot(self) -> Optional[Dict[str, Any]]:
        """直接读缓存(可能为 None)。"""
        with self._lock:
            return self._cache.get_data(CACHE_KEY, allow_stale=True)

    def get_snapshot_status(self) -> Dict[str, Any]:
        """获取缓存状态(供 UI 展示)。"""
        with self._lock:
            return self._cache.get_status(CACHE_KEY)

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _handle_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(envelope, dict):
            envelope = {
                "success": False, "data": {}, "error": "envelope_not_dict",
                "degraded": True, "schema_version": "", "latency_ms": 0.0,
            }
        if envelope.get("success", False):
            if self._enable_schema_check:
                result = self._schema_validator.validate(envelope)
                if not result.ok:
                    self._publish(
                        EventTypes.SCHEMA_CHANGED,
                        {
                            "domain": "runtime",
                            "expected": result.expected,
                            "actual": result.actual,
                            "reason": result.reason,
                        },
                    )
                    return {
                        "success": False, "data": {},
                        "error": f"schema_mismatch: {result.reason}",
                        "degraded": True,
                        "schema_version": str(result.actual or ""),
                        "latency_ms": float(envelope.get("latency_ms", 0.0)),
                    }
            latency = float(envelope.get("latency_ms", 0.0) or 0.0)
            self._cache.put_envelope(CACHE_KEY, envelope)
            self._publish(
                EventTypes.RUNTIME_UPDATED,
                {
                    "domain": "runtime",
                    "latency_ms": latency,
                    "cache_key": CACHE_KEY,
                },
            )
        else:
            self._publish(
                EventTypes.CONNECTION_CHANGED,
                {
                    "domain": "runtime",
                    "success": False,
                    "error": str(envelope.get("error", "")),
                },
            )
        return envelope

    def _build_view(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        success = bool(envelope.get("success", False)) if isinstance(envelope, dict) else False
        cache_status = self._cache.get_status(CACHE_KEY)
        if success:
            data = envelope.get("data", {}) if isinstance(envelope, dict) else {}
            return {
                "data": data if isinstance(data, dict) else {},
                "source": "live",
                "online": True,
                "last_update_time": str(envelope.get("timestamp", "")),
                "is_stale": False,
                "degraded": bool(envelope.get("degraded", False)),
                "offline": False,
                "schema_version": str(envelope.get("schema_version", "")),
            }
        # 失败:回退缓存
        cached = self._cache.get_data(CACHE_KEY, allow_stale=True)
        return {
            "data": cached if isinstance(cached, dict) else {},
            "source": "cache" if cached is not None else "empty",
            "online": False,
            "last_update_time": str(cache_status.get("timestamp", "")),
            "is_stale": bool(cache_status.get("is_stale", True)),
            "degraded": True,
            "offline": cached is None,
            "schema_version": str(cache_status.get("schema_version", "")),
        }

    def _publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        if not self._enable_events:
            return
        try:
            self._event_bus.publish_typed(
                event_type=event_type,
                source="service.runtime",
                data=data if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeService: publish 失败: %s", exc)


# 模块级单例
_svc_instance: Optional[RuntimeService] = None
_svc_lock = threading.Lock()


def get_runtime_service() -> RuntimeService:
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = RuntimeService()
    return _svc_instance


def reset_runtime_service_for_testing() -> None:
    global _svc_instance
    with _svc_lock:
        _svc_instance = None


__all__ = [
    "RuntimeService",
    "get_runtime_service",
    "reset_runtime_service_for_testing",
]
