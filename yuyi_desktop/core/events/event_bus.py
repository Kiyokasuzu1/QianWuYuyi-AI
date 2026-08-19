# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/events/event_bus.py

Phase C.10.4.5 —— Desktop Event Bus

一个轻量级、线程安全的事件总线。

设计目标:
- 替代 UI 主动轮询
- Service 在数据更新时 publish,UI subscribe 后自动收到通知
- 不依赖 PySide6 / Qt(保持 core 层纯净)
- 任何订阅者异常不会影响其他订阅者或发布者
- 保留最近 N 条事件历史(供测试 / 调试 / UI 初次加载)

事件类型(标准):
- RuntimeUpdated
- MemoryUpdated
- PersonalityUpdated
- GrowthUpdated
- InitiativeUpdated
- ConnectionChanged
- SchemaChanged
- DataInvalidated
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 标准事件类型
# ============================================================
class EventTypes:
    """标准事件类型常量。"""

    # 数据更新
    RUNTIME_UPDATED = "RuntimeUpdated"
    MEMORY_UPDATED = "MemoryUpdated"
    PERSONALITY_UPDATED = "PersonalityUpdated"
    GROWTH_UPDATED = "GrowthUpdated"
    INITIATIVE_UPDATED = "InitiativeUpdated"
    SELFMODEL_UPDATED = "SelfModelUpdated"
    LIFE_UPDATED = "LifeUpdated"

    # 连接 / 协议
    CONNECTION_CHANGED = "ConnectionChanged"
    SCHEMA_CHANGED = "SchemaChanged"

    # 缓存 / 数据
    DATA_INVALIDATED = "DataInvalidated"
    CACHE_HIT = "CacheHit"
    CACHE_MISS = "CacheMiss"


# ============================================================
# 事件数据
# ============================================================
@dataclass
class DesktopEvent:
    """
    标准事件对象。

    字段:
        event_id:   唯一 ID
        event_type: 事件类型
        timestamp:  ISO8601 UTC
        source:     事件来源(模块名 / "service.runtime" 等)
        data:       事件携带数据(只读快照)
        correlation_id: 关联 ID(可选,例如同一次刷新的多个事件)
    """

    event_type: str
    source: str = ""
    data: Optional[Dict[str, Any]] = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": str(self.event_type),
            "source": str(self.source or ""),
            "data": dict(self.data) if isinstance(self.data, dict) else {},
            "timestamp": str(self.timestamp),
            "correlation_id": str(self.correlation_id or ""),
        }


# ============================================================
# 订阅记录(内部)
# ============================================================
@dataclass
class _Subscription:
    sub_id: str
    event_type: str
    handler: Callable[[DesktopEvent], None]
    once: bool = False
    created_at: float = 0.0


# ============================================================
# Event Bus
# ============================================================
class EventBus:
    """
    桌面端事件总线(线程安全)。

    典型用法:
        bus = get_event_bus()
        bus.subscribe(EventTypes.RUNTIME_UPDATED, my_handler)
        ...
        bus.publish(DesktopEvent(
            event_type=EventTypes.RUNTIME_UPDATED,
            source="service.runtime",
            data={"snapshot": ...}
        ))
    """

    DEFAULT_HISTORY_LIMIT = 200

    def __init__(self, history_limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        self._lock = threading.RLock()
        self._history_limit = max(0, int(history_limit))
        self._history: List[DesktopEvent] = []
        self._subs: Dict[str, List[_Subscription]] = {}
        self._stats = {
            "total_published": 0,
            "total_delivered": 0,
            "total_handler_errors": 0,
            "total_subscribes": 0,
            "total_unsubscribes": 0,
        }
        self._wildcard_subs: List[_Subscription] = []

    # --------------------------------------------------------
    # 发布
    # --------------------------------------------------------
    def publish(
        self,
        event: DesktopEvent,
    ) -> int:
        """
        发布一个事件。

        Returns:
            实际投递次数(成功 + 失败)。
        """
        if not isinstance(event, DesktopEvent):
            logger.debug("EventBus.publish: 忽略非 DesktopEvent: %r", event)
            return 0

        with self._lock:
            self._stats["total_published"] += 1
            # 写入历史
            if self._history_limit > 0:
                self._history.append(event)
                # 修剪
                if len(self._history) > self._history_limit:
                    self._history = self._history[-self._history_limit:]

            # 收集订阅者
            subs = list(self._subs.get(event.event_type, []))
            wildcards = list(self._wildcard_subs)

        delivered = 0
        errors = 0
        all_subs = subs + wildcards
        for sub in all_subs:
            try:
                sub.handler(event)
                delivered += 1
                self._stats["total_delivered"] += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self._stats["total_handler_errors"] += 1
                logger.warning(
                    "EventBus: handler 异常 type=%s sub_id=%s err=%s",
                    event.event_type,
                    sub.sub_id,
                    exc,
                )
            # 处理 once
            if sub.once:
                self.unsubscribe(sub.sub_id)

        return delivered

    def publish_typed(
        self,
        event_type: str,
        source: str = "",
        data: Optional[Dict[str, Any]] = None,
        correlation_id: str = "",
    ) -> int:
        """
        便捷发布:直接传 type/source/data。
        """
        return self.publish(
            DesktopEvent(
                event_type=str(event_type or ""),
                source=str(source or ""),
                data=data if isinstance(data, dict) else {},
                correlation_id=str(correlation_id or ""),
            )
        )

    # --------------------------------------------------------
    # 订阅
    # --------------------------------------------------------
    def subscribe(
        self,
        event_type: str,
        handler: Callable[[DesktopEvent], None],
        once: bool = False,
    ) -> str:
        """
        订阅事件。

        Returns:
            subscription_id(可用于 unsubscribe)。
        """
        if not event_type or not isinstance(event_type, str):
            raise ValueError("event_type 必须是非空字符串")
        if handler is None or not callable(handler):
            raise ValueError("handler 必须可调用")
        sub_id = f"sub-{uuid.uuid4()}"
        sub = _Subscription(
            sub_id=sub_id,
            event_type=str(event_type),
            handler=handler,
            once=bool(once),
            created_at=time.monotonic(),
        )
        with self._lock:
            self._stats["total_subscribes"] += 1
            if event_type == "*":
                self._wildcard_subs.append(sub)
            else:
                self._subs.setdefault(str(event_type), []).append(sub)
        return sub_id

    def subscribe_once(
        self,
        event_type: str,
        handler: Callable[[DesktopEvent], None],
    ) -> str:
        """仅触发一次的订阅。"""
        return self.subscribe(event_type, handler, once=True)

    def unsubscribe(self, sub_id: str) -> bool:
        """
        取消订阅。

        Returns:
            是否成功移除。
        """
        if not sub_id:
            return False
        with self._lock:
            self._stats["total_unsubscribes"] += 1
            for event_type, subs in list(self._subs.items()):
                for sub in subs:
                    if sub.sub_id == sub_id:
                        subs.remove(sub)
                        return True
            for sub in list(self._wildcard_subs):
                if sub.sub_id == sub_id:
                    self._wildcard_subs.remove(sub)
                    return True
        return False

    def unsubscribe_all(self, event_type: Optional[str] = None) -> int:
        """
        取消所有订阅(指定 event_type 或全部)。
        """
        removed = 0
        with self._lock:
            if event_type is None:
                for k in list(self._subs.keys()):
                    removed += len(self._subs[k])
                    self._subs[k] = []
                removed += len(self._wildcard_subs)
                self._wildcard_subs = []
            else:
                subs = self._subs.get(str(event_type), [])
                removed = len(subs)
                self._subs[str(event_type)] = []
        return removed

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get_history(
        self,
        event_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[DesktopEvent]:
        """
        获取最近事件历史(浅拷贝)。

        Args:
            event_type: 仅返回该类型(可选)
            limit: 最多返回条数
        """
        with self._lock:
            history = list(self._history)
        if event_type:
            history = [e for e in history if e.event_type == event_type]
        if limit > 0 and len(history) > limit:
            history = history[-limit:]
        return history

    def get_recent_by_type(
        self,
        event_type: str,
        limit: int = 1,
    ) -> List[DesktopEvent]:
        """便捷:按类型获取最近 N 条。"""
        return self.get_history(event_type=event_type, limit=limit)

    def has_listeners(self, event_type: str) -> bool:
        with self._lock:
            return (
                len(self._subs.get(event_type, [])) > 0
                or len(self._wildcard_subs) > 0
            )

    def listener_count(self, event_type: str) -> int:
        with self._lock:
            n = len(self._subs.get(event_type, []))
            if event_type == "*":
                n += len(self._wildcard_subs)
            elif self._wildcard_subs:
                n += len(self._wildcard_subs)
            return n

    def clear_history(self) -> int:
        with self._lock:
            n = len(self._history)
            self._history = []
            return n

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    def subscriber_summary(self) -> Dict[str, int]:
        with self._lock:
            result: Dict[str, int] = {}
            for k, v in self._subs.items():
                result[k] = len(v)
            if self._wildcard_subs:
                result["*"] = len(self._wildcard_subs)
            return result


# ============================================================
# 模块级单例
# ============================================================
_bus_instance: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """获取 EventBus 单例(懒加载)。"""
    global _bus_instance
    if _bus_instance is None:
        with _bus_lock:
            if _bus_instance is None:
                _bus_instance = EventBus()
    return _bus_instance


def reset_event_bus_for_testing() -> EventBus:
    """测试用:重置单例。"""
    global _bus_instance
    with _bus_lock:
        _bus_instance = EventBus()
    return _bus_instance


# ============================================================
# 模块级便捷函数
# ============================================================
def subscribe(
    event_type: str,
    handler: Callable[[DesktopEvent], None],
    once: bool = False,
) -> str:
    """模块级便捷订阅。"""
    return get_event_bus().subscribe(event_type, handler, once=once)


def unsubscribe(sub_id: str) -> bool:
    """模块级便捷取消订阅。"""
    return get_event_bus().unsubscribe(sub_id)


__all__ = [
    "DesktopEvent",
    "EventBus",
    "EventTypes",
    "get_event_bus",
    "reset_event_bus_for_testing",
    "subscribe",
    "unsubscribe",
]
