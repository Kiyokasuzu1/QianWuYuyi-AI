"""
RuntimeEventBus —— Runtime 事件总线封装

提供 Runtime 模块对全局 EventBus 的统一访问接口。
不新建独立事件总线，而是封装全局 EventBus，
确保事件类型一致性。

设计原则：
- 复用全局 EventBus（src/events/bus.py）
- 提供类型转换（contracts BaseEvent -> events YuyiEvent）
- 保持 Runtime 模块自包含感
"""

from typing import Any, Callable

from src.events.bus import get_event_bus, publish_event, subscribe_event
from src.events.events import YuyiEvent
from src.contracts.event_schema import BaseEvent


class RuntimeEventBus:
    """
    Runtime 视角的事件总线

    封装全局 EventBus，提供 Runtime 专用接口。
    """

    def __init__(self):
        self._bus = get_event_bus()

    def publish(self, event: Any) -> Any:
        """
        发布事件

        支持 YuyiEvent 和 BaseEvent（contracts）。

        Args:
            event: 事件对象

        Returns:
            发布结果
        """
        if isinstance(event, YuyiEvent):
            return publish_event(event)
        elif isinstance(event, BaseEvent):
            yuyi_event = YuyiEvent(
                event_type=event.type,
                source=event.source or "runtime",
                data=event.to_dict(),
            )
            return publish_event(yuyi_event)
        else:
            raise TypeError(f"Unsupported event type: {type(event)}")

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """
        订阅特定类型事件

        Args:
            event_type: 事件类型
            handler: 处理函数
        """
        subscribe_event(event_type, handler)

    def subscribe_all(self, handler: Callable) -> None:
        """
        订阅所有事件

        Args:
            handler: 处理函数
        """
        self._bus.subscribe_all(handler)