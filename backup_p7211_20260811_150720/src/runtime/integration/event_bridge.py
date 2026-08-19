# -*- coding: utf-8 -*-
"""
src/runtime/integration/event_bridge.py

Phase 5.0-D2 Step 2: EventBridge 骨架。

职责:
- 桥接"业务事件源"(Skeleton 阶段:可注入的 callable)→ IntegrationEvent
- 桥接 IntegrationEvent → LifecycleManager 的 EventEmitter
- 维护转换规则(event_type_map)
- 提供 publish_integration_event() 注入入口
- 维护指标(发出去多少、失败多少、丢弃多少)

约束:
- 不直接 import 任何业务事件源
- 不直接 import Lifecycle EventEmitter(通过 callable 注入)
- 不调用 LLM / DB / Network
- 业务事件源可由外部注入,实现完全可测试
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

from src.runtime.integration.integration_event import (
    ALL_INTEGRATION_EVENT_TYPES,
    INTEGRATION_SEVERITY_INFO,
    IntegrationEvent,
    make_integration_event,
)


logger = logging.getLogger(__name__)


# ============================================================
# 桥接器类型
# ============================================================
# 业务事件 → IntegrationEvent 的转换 callable
# 签名: (raw_event) -> Optional[IntegrationEvent] 或 None(不转换)
BusinessEventToIntegration = Callable[[Any], Optional[IntegrationEvent]]

# IntegrationEvent → Lifecycle EventEmitter 的发射 callable
# 签名: (IntegrationEvent) -> None
IntegrationToLifecycleEmitter = Callable[[IntegrationEvent], None]


# ============================================================
# EventBridge
# ============================================================
class EventBridge:
    """业务事件 ↔ IntegrationEvent 桥接器(Skeleton)。

    设计:
    - 注入 business_to_integration:把任意"业务事件对象"转成 IntegrationEvent
    - 注入 integration_to_lifecycle:把 IntegrationEvent 投给 Lifecycle EventEmitter
    - 注入 lifecycle_to_integration:把 Lifecycle 内部事件转为 IntegrationEvent(可选)
    - register_mapping:把 str→str 的事件名映射(legacy 兼容)
    - publish_integration_event:外部直接注入 IntegrationEvent
    - bridge_business_event:接收任意业务事件,经转换后投出
    """

    def __init__(
        self,
        *,
        name: str = "event_bridge",
        business_to_integration: Optional[BusinessEventToIntegration] = None,
        integration_to_lifecycle: Optional[IntegrationToLifecycleEmitter] = None,
        lifecycle_to_integration: Optional[BusinessEventToIntegration] = None,
        event_type_map: Optional[Dict[str, str]] = None,
        known_event_types: Optional[Set[str]] = None,
    ) -> None:
        self._name = str(name or "event_bridge")
        self._business_to_integration = business_to_integration
        self._integration_to_lifecycle = integration_to_lifecycle
        self._lifecycle_to_integration = lifecycle_to_integration

        # 事件名映射(business_event_name -> integration_event_name)
        self._event_type_map: Dict[str, str] = dict(event_type_map or {})
        # 已知 integration 事件类型(用于过滤)
        if known_event_types is not None:
            self._known_event_types: Set[str] = set(known_event_types)
        else:
            self._known_event_types = set(ALL_INTEGRATION_EVENT_TYPES)

        self._lock = threading.RLock()
        # 指标
        self._published_count = 0
        self._dropped_count = 0
        self._bridge_count = 0
        self._error_count = 0
        self._last_published_event_id: str = ""
        self._last_published_type: str = ""
        self._last_bridge_at: float = 0.0
        self._last_error: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def published_count(self) -> int:
        with self._lock:
            return self._published_count

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped_count

    @property
    def bridge_count(self) -> int:
        with self._lock:
            return self._bridge_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def last_published_event_id(self) -> str:
        with self._lock:
            return self._last_published_event_id

    @property
    def last_published_type(self) -> str:
        with self._lock:
            return self._last_published_type

    @property
    def last_bridge_at(self) -> float:
        with self._lock:
            return self._last_bridge_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 映射管理
    # --------------------------------------------------------
    def register_mapping(self, source_event_type: str, target_event_type: str) -> None:
        """注册业务事件名到 IntegrationEvent 名的映射。"""
        if not isinstance(source_event_type, str) or not source_event_type:
            return
        if not isinstance(target_event_type, str) or not target_event_type:
            return
        with self._lock:
            self._event_type_map[source_event_type] = target_event_type

    def register_mappings(self, mapping: Dict[str, str]) -> None:
        """批量注册映射。"""
        if not isinstance(mapping, dict):
            return
        for k, v in mapping.items():
            self.register_mapping(str(k), str(v))

    def get_mapped_type(self, source_event_type: str) -> str:
        """根据 source_event_type 取出 integration 事件名(无映射则原样返回)。"""
        if not isinstance(source_event_type, str):
            return ""
        with self._lock:
            return self._event_type_map.get(source_event_type, source_event_type)

    # --------------------------------------------------------
    # 桥接入口
    # --------------------------------------------------------
    def bridge_business_event(self, raw_event: Any) -> Optional[IntegrationEvent]:
        """接收一个业务事件,经转换后投给 Lifecycle。

        返回实际发出的 IntegrationEvent(若被丢弃/失败则返回 None)。
        """
        if raw_event is None:
            with self._lock:
                self._dropped_count += 1
            return None

        # 1) 业务事件 → IntegrationEvent
        try:
            converted = self._convert_business_event(raw_event)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = f"convert failed: {exc}"
            logger.warning(
                "EventBridge(%s) convert failed: %s", self._name, exc,
            )
            return None

        if converted is None:
            with self._lock:
                self._dropped_count += 1
            return None

        if not isinstance(converted, IntegrationEvent):
            # 转换结果必须是 IntegrationEvent,否则视为失败
            with self._lock:
                self._error_count += 1
                self._last_error = "convert returned non-IntegrationEvent"
                self._dropped_count += 1
            return None

        # 2) 类型过滤:不在已知列表中,可选丢弃(Skeleton 阶段:默认放过)
        # (允许业务侧扩展类型)
        # 3) 投给 Lifecycle EventEmitter
        return self.publish_integration_event(converted)

    def publish_integration_event(
        self, event: IntegrationEvent
    ) -> Optional[IntegrationEvent]:
        """直接注入 IntegrationEvent(Skeleton 阶段:经类型过滤后投给 Lifecycle)。"""
        if not isinstance(event, IntegrationEvent):
            with self._lock:
                self._error_count += 1
                self._last_error = "publish_integration_event 收到非 IntegrationEvent"
            return None

        with self._lock:
            self._bridge_count += 1
            self._last_bridge_at = time.time()

        if self._integration_to_lifecycle is None:
            # Skeleton 阶段允许 None emitter(只统计,不实际投递)
            with self._lock:
                self._published_count += 1
                self._last_published_event_id = event.event_id
                self._last_published_type = event.event_type
            return event

        try:
            self._integration_to_lifecycle(event)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = f"lifecycle emit failed: {exc}"
                self._dropped_count += 1
            logger.warning(
                "EventBridge(%s) lifecycle emit failed: %s", self._name, exc,
            )
            return None

        with self._lock:
            self._published_count += 1
            self._last_published_event_id = event.event_id
            self._last_published_type = event.event_type
        return event

    def bridge_lifecycle_event(self, raw_lifecycle_event: Any) -> Optional[IntegrationEvent]:
        """把 Lifecycle 内部事件反向转为 IntegrationEvent(可选通道)。

        Skeleton 阶段:仅当 lifecycle_to_integration 已注入时才生效。
        """
        if self._lifecycle_to_integration is None:
            return None
        try:
            result = self._lifecycle_to_integration(raw_lifecycle_event)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = f"lifecycle_to_integration failed: {exc}"
            return None
        if not isinstance(result, IntegrationEvent):
            return None
        return result

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _convert_business_event(self, raw_event: Any) -> Optional[IntegrationEvent]:
        """把业务事件转成 IntegrationEvent。"""
        if self._business_to_integration is not None:
            try:
                result = self._business_to_integration(raw_event)
            except Exception as exc:  # noqa: BLE001
                raise exc
            if result is None:
                return None
            if isinstance(result, IntegrationEvent):
                return result
            # 如果返回 dict,尝试转换
            if isinstance(result, dict):
                return IntegrationEvent.from_dict(result)
            return None

        # 默认策略:若 raw_event 已经是 IntegrationEvent,直接返回
        if isinstance(raw_event, IntegrationEvent):
            return raw_event

        # 若是 dict(已有 to_dict),尝试转换
        if isinstance(raw_event, dict):
            try:
                return IntegrationEvent.from_dict(raw_event)
            except Exception:
                return None

        # 若对象有 .event_type + .payload / .data 字段,尝试读取
        et = getattr(raw_event, "event_type", None) or getattr(raw_event, "type", None)
        if not isinstance(et, str) or not et:
            return None

        # 应用事件名映射
        mapped = self.get_mapped_type(et)

        # 读取 payload
        payload: Any = None
        for attr in ("payload", "data"):
            if hasattr(raw_event, attr):
                try:
                    payload = getattr(raw_event, attr)
                    break
                except Exception:
                    payload = None
        if not isinstance(payload, dict):
            payload = {}
        payload = dict(payload)

        # 读取 source
        source = (
            getattr(raw_event, "source", None)
            or getattr(raw_event, "source_id", None)
            or "unknown"
        )
        if not isinstance(source, str):
            source = str(source or "unknown")

        # 读取 related_ids
        related_ids = getattr(raw_event, "related_ids", None)
        if not isinstance(related_ids, list):
            related_ids = []

        return make_integration_event(
            event_type=mapped,
            source=source,
            payload=payload,
            related_ids=related_ids,
            severity=INTEGRATION_SEVERITY_INFO,
        )

    # --------------------------------------------------------
    # 状态 / 调试
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """返回 EventBridge 状态。"""
        with self._lock:
            return {
                "name": self._name,
                "published_count": self._published_count,
                "dropped_count": self._dropped_count,
                "bridge_count": self._bridge_count,
                "error_count": self._error_count,
                "last_published_event_id": self._last_published_event_id,
                "last_published_type": self._last_published_type,
                "last_bridge_at": self._last_bridge_at,
                "last_error": self._last_error,
                "mapping_size": len(self._event_type_map),
                "has_business_converter": self._business_to_integration is not None,
                "has_lifecycle_emitter": self._integration_to_lifecycle is not None,
                "has_lifecycle_converter": self._lifecycle_to_integration is not None,
            }

    def __repr__(self) -> str:
        return (
            f"EventBridge(name={self._name!r}, "
            f"published={self.published_count}, dropped={self.dropped_count})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_event_bridge(
    *,
    name: str = "default_event_bridge",
    business_to_integration: Optional[BusinessEventToIntegration] = None,
    integration_to_lifecycle: Optional[IntegrationToLifecycleEmitter] = None,
) -> EventBridge:
    """构造默认 EventBridge。"""
    return EventBridge(
        name=name,
        business_to_integration=business_to_integration,
        integration_to_lifecycle=integration_to_lifecycle,
    )
