# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/memory_service.py

Phase C.10.2 / C.10.4 —— Memory Service(远程版本 + 缓存 + 事件)

通过 RemoteProviderBridge 访问 Yuyi Server API。
不直接 import 任何 src.* 模块。

约束(强):
- 不允许 import 任何 src.* 业务模块
- 禁止调用任何 Memory 写接口
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


CACHE_KEY = "memory.snapshot"


class MemoryService:
    """Memory 数据只读服务(远程 + 缓存 + 事件)。"""

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
    # 旧 API
    # --------------------------------------------------------
    def get_summary_envelope(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_memory_snapshot()

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_memory_snapshot_data()

    def get_overview_v2(self) -> Dict[str, Any]:
        with self._lock:
            return self._bridge.get_memory_overview_v2_data()

    def list_recent(self, limit: int = 20) -> List[Any]:
        with self._lock:
            try:
                n = max(1, min(200, int(limit)))
            except (TypeError, ValueError):
                n = 20
            return self._bridge.get_memory_recent_data()[:n]

    def get_overview(self) -> Dict[str, Any]:
        with self._lock:
            v2 = self.get_overview_v2()
            envelope = self.get_summary_envelope()
            summary = self.get_summary()
            total = int(
                v2.get("total_count")
                or summary.get("total_count", summary.get("total", 0))
                or 0
            )
            important = int(
                v2.get("important_count")
                or summary.get("important_count", summary.get("important", 0))
                or 0
            )
            return {
                "total": total,
                "important": important,
                "v2": v2,
                "available": bool(envelope.get("success", False)) or bool(v2.get("available", False)),
                "degraded": bool(envelope.get("degraded", False)),
            }

    # --------------------------------------------------------
    # C.10.4:缓存 + 事件
    # --------------------------------------------------------
    def refresh(self) -> Dict[str, Any]:
        with self._lock:
            envelope = self._bridge.get_memory_snapshot()
            return self._handle_envelope(envelope)

    def get_snapshot_with_cache(self) -> Dict[str, Any]:
        with self._lock:
            envelope = self._bridge.get_memory_snapshot()
            self._handle_envelope(envelope)
            return self._build_view(envelope)

    def get_cached_snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._cache.get_data(CACHE_KEY, allow_stale=True)

    def get_snapshot_status(self) -> Dict[str, Any]:
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
                            "domain": "memory",
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
                EventTypes.MEMORY_UPDATED,
                {
                    "domain": "memory",
                    "latency_ms": latency,
                    "cache_key": CACHE_KEY,
                },
            )
        else:
            self._publish(
                EventTypes.CONNECTION_CHANGED,
                {
                    "domain": "memory",
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
                source="service.memory",
                data=data if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MemoryService: publish 失败: %s", exc)


_svc_instance: Optional[MemoryService] = None
_svc_lock = threading.Lock()


def get_memory_service() -> MemoryService:
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = MemoryService()
    return _svc_instance


def reset_memory_service_for_testing() -> None:
    global _svc_instance
    with _svc_lock:
        _svc_instance = None


__all__ = [
    "MemoryService",
    "get_memory_service",
    "reset_memory_service_for_testing",
]
