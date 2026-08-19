from typing import Callable, Dict, List, Optional, Any
from src.events.events import YuyiEvent, EventType


class EventBus:
    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}
        self._global_handlers: List[Callable] = []

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: Callable):
        if handler not in self._global_handlers:
            self._global_handlers.append(handler)

    def unsubscribe(self, event_type: str, handler: Callable):
        if event_type in self._handlers and handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    def unsubscribe_all(self, handler: Callable):
        if handler in self._global_handlers:
            self._global_handlers.remove(handler)
        for handlers in self._handlers.values():
            if handler in handlers:
                handlers.remove(handler)

    def publish(self, event: YuyiEvent) -> List[Any]:
        results = []

        for handler in self._global_handlers:
            try:
                results.append(handler(event))
            except Exception as e:
                print(f"[EventBus] 全局处理器执行失败: {e}")

        if event.event_type in self._handlers:
            for handler in self._handlers[event.event_type]:
                try:
                    results.append(handler(event))
                except Exception as e:
                    print(f"[EventBus] 事件 {event.event_type} 处理器执行失败: {e}")

        return results

    def has_subscribers(self, event_type: str) -> bool:
        return event_type in self._handlers and len(self._handlers[event_type]) > 0

    def get_subscriber_count(self, event_type: Optional[str] = None) -> int:
        if event_type is None:
            return sum(len(handlers) for handlers in self._handlers.values()) + len(self._global_handlers)
        return len(self._handlers.get(event_type, []))


_global_bus = None


def get_event_bus() -> EventBus:
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus()
    return _global_bus


def publish_event(event: YuyiEvent):
    return get_event_bus().publish(event)


def subscribe_event(event_type: str, handler: Callable):
    get_event_bus().subscribe(event_type, handler)


def subscribe_all_events(handler: Callable):
    get_event_bus().subscribe_all(handler)