"""
事件总线

模块间通过事件总线通信，
管理面板订阅事件以实时展示系统状态。

设计原则：
- 同步发布（简单可靠，适合当前规模）
- 支持通配符订阅（module.*）
- 事件带时间戳和 request_id 可追溯
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """事件对象"""
    event_type: str                                # 事件类型，如 "module.dead"、"config.changed"
    payload: Dict[str, Any] = field(default_factory=dict)  # 事件数据
    request_id: str = ""                          # 追踪 ID
    timestamp: float = 0.0                        # 时间戳
    source: str = ""                              # 事件来源（模块名）

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.request_id:
            self.request_id = uuid.uuid4().hex[:12]


class EventBus:
    """
    事件总线 — 全局单例

    支持：
    - emit(event_type, payload)           发布事件
    - subscribe(event_pattern, callback)  订阅事件（支持通配符）
    - unsubscribe(event_pattern, callback) 取消订阅
    """

    _instance: Optional[EventBus] = None
    _lock = threading.Lock()

    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Event], None]]] = {}
        self._event_history: List[Event] = []
        self._max_history = 1000
        self._emit_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> EventBus:
        """获取全局单例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（仅测试用）"""
        with cls._lock:
            cls._instance = None

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None,
             source: str = "", request_id: str = "") -> Event:
        """
        发布事件

        Args:
            event_type: 事件类型（点分隔，如 "module.started"）
            payload: 事件数据
            source: 事件来源
            request_id: 追踪 ID（自动生成可不填）

        Returns:
            发布的事件对象
        """
        event = Event(
            event_type=event_type,
            payload=payload or {},
            source=source,
            request_id=request_id,
        )

        # 记录历史
        with self._emit_lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

        # 通知订阅者
        matched = self._find_matched_subscribers(event_type)
        for callback in matched:
            try:
                callback(event)
            except Exception as e:
                logger.warning(f"事件回调执行失败 [{event_type}]: {e}")

        return event

    def subscribe(self, event_pattern: str,
                  callback: Callable[[Event], None]) -> str:
        """
        订阅事件

        Args:
            event_pattern: 事件类型模式（支持通配符，如 "module.*"、"*.error"）
            callback: 回调函数，接收 Event 对象

        Returns:
            订阅 ID（用于取消订阅）
        """
        import hashlib
        sub_id = hashlib.md5(f"{event_pattern}:{id(callback)}".encode()).hexdigest()[:8]

        if event_pattern not in self._subscribers:
            self._subscribers[event_pattern] = []
        self._subscribers[event_pattern].append(callback)

        logger.debug(f"订阅事件: {event_pattern} (sub_id={sub_id})")
        return sub_id

    def unsubscribe(self, event_pattern: str,
                    callback: Callable[[Event], None]) -> bool:
        """
        取消订阅

        Args:
            event_pattern: 事件类型模式
            callback: 回调函数

        Returns:
            是否成功取消
        """
        if event_pattern not in self._subscribers:
            return False

        try:
            self._subscribers[event_pattern].remove(callback)
            if not self._subscribers[event_pattern]:
                del self._subscribers[event_pattern]
            return True
        except ValueError:
            return False

    def get_history(self, event_type: Optional[str] = None,
                    limit: int = 100) -> List[Event]:
        """
        获取事件历史

        Args:
            event_type: 按事件类型过滤（支持通配符）
            limit: 最多返回条数

        Returns:
            事件列表（按时间倒序）
        """
        with self._emit_lock:
            history = list(reversed(self._event_history))

        if event_type:
            history = [e for e in history
                       if fnmatch.fnmatch(e.event_type, event_type)]

        return history[:limit]

    def _find_matched_subscribers(self, event_type: str) -> List[Callable[[Event], None]]:
        """查找所有匹配的订阅者"""
        matched: List[Callable[[Event], None]] = []
        for pattern, callbacks in self._subscribers.items():
            if fnmatch.fnmatch(event_type, pattern):
                matched.extend(callbacks)
        return matched


def emit_event(event_type: str, payload: Optional[Dict[str, Any]] = None,
               source: str = "", request_id: str = "") -> Event:
    """便捷函数：发布事件"""
    bus = EventBus.get_instance()
    return bus.emit(event_type, payload=payload, source=source, request_id=request_id)


def on_event(event_pattern: str, callback: Callable[[Event], None]) -> str:
    """便捷函数：订阅事件"""
    bus = EventBus.get_instance()
    return bus.subscribe(event_pattern, callback)
