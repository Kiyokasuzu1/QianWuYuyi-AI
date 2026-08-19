# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/service_base.py

Phase C.10.4 —— Cached Service Base

所有 Service 继承此基类,获得统一的能力:
- 远程数据拉取(RemoteProviderBridge / ApiClient)
- 本地缓存(写入/读取,服务器不可达时回退)
- Schema 校验(失败阻断进入业务)
- 事件发布(更新时通知 UI)

数据流:
    Server Data
       ↓
    ApiClient / RemoteProviderBridge
       ↓
    Schema 校验
       ↓
    缓存写入
       ↓
    事件发布
       ↓
    返回数据 / 缓存回退

约束(强):
- 不允许 import 任何 src.* 业务模块
- 失败可缓存,严禁伪造数据
- 缓存只读(返回 deep copy)
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from yuyi_desktop.core.cache.remote_snapshot_cache import (
    RemoteSnapshotCache,
    get_remote_snapshot_cache,
)
from yuyi_desktop.core.errors import (
    DesktopError,
    ERROR_SCHEMA,
)
from yuyi_desktop.core.events.event_bus import (
    DesktopEvent,
    EventBus,
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


# 业务模块 -> 事件类型 映射
_EVENT_TYPE_BY_DOMAIN: Dict[str, str] = {
    "runtime": EventTypes.RUNTIME_UPDATED,
    "memory": EventTypes.MEMORY_UPDATED,
    "personality": EventTypes.PERSONALITY_UPDATED,
    "selfmodel": EventTypes.SELFMODEL_UPDATED,
    "growth": EventTypes.GROWTH_UPDATED,
    "initiative": EventTypes.INITIATIVE_UPDATED,
    "life": EventTypes.LIFE_UPDATED,
}


class CachedServiceBase:
    """
    Service 基类:cache + schema + event。

    子类需:
    - 指定 domain = "runtime" / "memory" / "personality" / "growth" / "initiative" 等
    - 实现 _fetch_remote() 返回 envelope(由 RemoteProviderBridge 提供)
    - (可选)重写 _extract_data(envelope) 自定义 data 提取
    """

    domain: str = "base"  # 子类必须覆盖

    def __init__(
        self,
        bridge: Optional[RemoteProviderBridge] = None,
        cache: Optional[RemoteSnapshotCache] = None,
        schema_validator: Optional[SchemaValidator] = None,
        event_bus: Optional[EventBus] = None,
        enable_schema_check: bool = True,
        enable_events: bool = True,
    ) -> None:
        self._lock = None  # 由具体子类用 RLock
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
    # 子类可覆盖
    # --------------------------------------------------------
    def _fetch_remote(self) -> Dict[str, Any]:
        """
        调用 RemoteProviderBridge 拉取数据,返回 envelope。
        子类必须实现。
        """
        raise NotImplementedError(
            f"{type(self).__name__}._fetch_remote() 必须被子类实现"
        )

    def _cache_key(self) -> str:
        """默认使用 domain 作为 cache key。"""
        return f"{self.domain}.snapshot"

    def _event_type(self) -> str:
        return _EVENT_TYPE_BY_DOMAIN.get(
            self.domain, EventTypes.RUNTIME_UPDATED
        )

    def _source(self) -> str:
        return f"service.{self.domain}"

    # --------------------------------------------------------
    # 公共:fetch / cached_get
    # --------------------------------------------------------
    def fetch(self) -> Dict[str, Any]:
        """
        拉取数据:

        1. 远端拉取(envelope)
        2. Schema 校验
        3. 成功:写入缓存 + 发布事件
        4. 失败:返回 envelope(供调用方根据 success 字段处理)
        """
        envelope = self._fetch_remote()
        if not isinstance(envelope, dict):
            envelope = {
                "success": False,
                "data": {},
                "error": "envelope_not_dict",
                "degraded": True,
                "schema_version": "",
                "latency_ms": 0.0,
            }
        if envelope.get("success", False):
            # Schema 校验
            if self._enable_schema_check:
                result = self._schema_validator.validate(envelope)
                if not result.ok:
                    logger.warning(
                        "CachedServiceBase[%s]: schema mismatch expected=%s actual=%s reason=%s",
                        self.domain,
                        result.expected,
                        result.actual,
                        result.reason,
                    )
                    err_envelope = {
                        "success": False,
                        "data": {},
                        "error": f"schema_mismatch: {result.reason}",
                        "degraded": True,
                        "schema_version": str(result.actual or ""),
                        "latency_ms": float(envelope.get("latency_ms", 0.0)),
                    }
                    self._publish_event(
                        event_type=EventTypes.SCHEMA_CHANGED,
                        data={
                            "domain": self.domain,
                            "expected": result.expected,
                            "actual": result.actual,
                            "reason": result.reason,
                        },
                    )
                    return err_envelope
            # 写入缓存
            latency = float(envelope.get("latency_ms", 0.0) or 0.0)
            self._cache.put_envelope(self._cache_key(), envelope)
            # 发布更新事件
            self._publish_event(
                event_type=self._event_type(),
                data={
                    "domain": self.domain,
                    "latency_ms": latency,
                    "cache_key": self._cache_key(),
                },
            )
        else:
            # 失败:发布连接变化事件(只让 UI 知道)
            self._publish_event(
                event_type=EventTypes.CONNECTION_CHANGED,
                data={
                    "domain": self.domain,
                    "success": False,
                    "error": str(envelope.get("error", "")),
                },
            )
        return envelope

    def get_cached(self) -> Optional[Dict[str, Any]]:
        """
        获取最近一次成功的数据(可能已过期)。
        返回 None 表示无任何缓存。
        """
        return self._cache.get_data(self._cache_key(), allow_stale=True)

    def get_status(self) -> Dict[str, Any]:
        """
        获取缓存状态(供 UI 展示):
        - online / offline
        - last_update_time
        - age_seconds
        - is_stale
        """
        return self._cache.get_status(self._cache_key())

    # --------------------------------------------------------
    # 事件发布
    # --------------------------------------------------------
    def _publish_event(
        self,
        event_type: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._enable_events:
            return
        try:
            self._event_bus.publish_typed(
                event_type=str(event_type or ""),
                source=self._source(),
                data=data if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "CachedServiceBase[%s]: publish_event 失败: %s",
                self.domain, exc,
            )


__all__ = [
    "CachedServiceBase",
]
