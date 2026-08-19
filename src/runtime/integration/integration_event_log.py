# -*- coding: utf-8 -*-
"""
src/runtime/integration/integration_event_log.py

Phase 5.0-D2 Step 4: 内存 IntegrationEvent 收集器。

职责:
- 在 RuntimeIntegrationHost 内部作为"事件容器"
- 接收 EventBridge 投递的 IntegrationEvent
- 提供按类型 / 时间 / 来源过滤的查询接口
- 容量可配置(默认 256),超限采用 FIFO 淘汰
- 线程安全

约束:
- 不直接 import 任何业务模块
- 不调用 LLM / DB / Network
- 仅作为内存数据结构,持久化由 IntegrationEventStore 负责
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.runtime.integration.integration_event import IntegrationEvent


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
DEFAULT_LOG_CAPACITY = 256
MAX_LOG_CAPACITY = 10000


class IntegrationEventLogError(Exception):
    """IntegrationEventLog 错误基类。"""


# ============================================================
# IntegrationEventLog
# ============================================================
class IntegrationEventLog:
    """IntegrationEvent 内存收集器(环形缓冲,FIFO 淘汰)。

    设计:
    - 容量可配置,默认 256,最大 10000
    - 容量为 0 时不保留任何事件(append 仍可调用,但只更新统计)
    - append() 线程安全
    - 提供按类型 / 来源 / 时间范围的过滤接口
    - 异常隔离:append 失败不抛错
    """

    def __init__(
        self,
        *,
        name: str = "integration_event_log",
        capacity: int = DEFAULT_LOG_CAPACITY,
    ) -> None:
        self._name = str(name or "integration_event_log")
        cap = int(capacity or 0)
        if cap < 0:
            cap = 0
        if cap > MAX_LOG_CAPACITY:
            cap = MAX_LOG_CAPACITY
        self._capacity = cap
        self._lock = threading.RLock()
        self._buffer: deque = deque(maxlen=cap) if cap > 0 else deque()
        self._total_appended = 0
        self._total_dropped = 0
        self._last_event_id: str = ""
        self._last_event_type: str = ""
        self._last_event_at: float = 0.0
        self._last_error: str = ""
        self._by_type_count: Dict[str, int] = {}
        self._closed = False

    # --------------------------------------------------------
    # 身份 / 容量
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def total_appended(self) -> int:
        with self._lock:
            return self._total_appended

    @property
    def total_dropped(self) -> int:
        with self._lock:
            return self._total_dropped

    @property
    def last_event_id(self) -> str:
        with self._lock:
            return self._last_event_id

    @property
    def last_event_type(self) -> str:
        with self._lock:
            return self._last_event_type

    @property
    def last_event_at(self) -> float:
        with self._lock:
            return self._last_event_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def __len__(self) -> int:
        return self.size

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(
        self,
        event: Any,
        *,
        silent: bool = True,
    ) -> bool:
        """追加一个事件。

        返回 True 表示已加入缓冲区,False 表示被丢弃或失败。
        异常默认被隔离(silent=True);silent=False 时异常上抛。
        """
        try:
            if not isinstance(event, IntegrationEvent):
                if silent:
                    with self._lock:
                        self._total_dropped += 1
                        self._last_error = "append 收到非 IntegrationEvent"
                    return False
                raise IntegrationEventLogError(
                    f"append 需要 IntegrationEvent,实际: {type(event).__name__}"
                )

            with self._lock:
                if self._closed:
                    self._total_dropped += 1
                    self._last_error = "log 已关闭"
                    return False
                # 容量为 0 时仅统计,不保留
                if self._capacity > 0:
                    self._buffer.append(event)
                self._total_appended += 1
                self._last_event_id = event.event_id
                self._last_event_type = event.event_type
                self._last_event_at = float(getattr(event, "timestamp", 0.0) or 0.0)
                # by_type 计数
                et = str(event.event_type or "")
                self._by_type_count[et] = self._by_type_count.get(et, 0) + 1
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._total_dropped += 1
                self._last_error = f"append 失败: {exc}"
            if silent:
                logger.warning(
                    "IntegrationEventLog(%s) append 失败(已隔离): %s",
                    self._name, exc,
                )
                return False
            raise

    def extend(self, events: Iterable[Any]) -> int:
        """批量追加,返回成功数量。"""
        if events is None:
            return 0
        success = 0
        for ev in events:
            if self.append(ev, silent=True):
                success += 1
        return success

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def events(
        self,
        *,
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        since: Optional[float] = None,
        until: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> List[IntegrationEvent]:
        """返回事件列表(按时间正序,符合条件的事件)。"""
        with self._lock:
            data: Sequence[IntegrationEvent] = list(self._buffer)
        if event_type is not None:
            data = [e for e in data if e.event_type == event_type]
        if source is not None:
            data = [e for e in data if e.source == source]
        if since is not None:
            try:
                s = float(since)
                data = [e for e in data if float(getattr(e, "timestamp", 0.0) or 0.0) >= s]
            except Exception:
                pass
        if until is not None:
            try:
                u = float(until)
                data = [e for e in data if float(getattr(e, "timestamp", 0.0) or 0.0) <= u]
            except Exception:
                pass
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                data = data[-n:]
            except Exception:
                pass
        return list(data)

    def latest(self, limit: int = 10) -> List[IntegrationEvent]:
        """返回最近 N 条事件(时间倒序不会做,保持插入顺序的尾部 N 条)。"""
        with self._lock:
            buf = list(self._buffer)
        try:
            n = int(limit)
        except Exception:
            n = 10
        if n <= 0:
            return []
        return buf[-n:]

    def count_by_type(self, event_type: str) -> int:
        with self._lock:
            return int(self._by_type_count.get(str(event_type or ""), 0))

    def type_counts(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._by_type_count)

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> int:
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
            self._by_type_count.clear()
            return n

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
        return True

    # --------------------------------------------------------
    # 描述 / 调试
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "capacity": self._capacity,
                "size": len(self._buffer),
                "total_appended": self._total_appended,
                "total_dropped": self._total_dropped,
                "last_event_id": self._last_event_id,
                "last_event_type": self._last_event_type,
                "last_event_at": self._last_event_at,
                "last_error": self._last_error,
                "is_closed": self._closed,
                "type_counts": dict(self._by_type_count),
            }

    def __repr__(self) -> str:
        return (
            f"IntegrationEventLog(name={self._name!r}, "
            f"size={self.size}/{self._capacity}, "
            f"appended={self.total_appended})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_event_log(
    *,
    name: str = "default_event_log",
    capacity: int = DEFAULT_LOG_CAPACITY,
) -> IntegrationEventLog:
    """构造默认 IntegrationEventLog。"""
    return IntegrationEventLog(name=name, capacity=capacity)


__all__ = [
    "IntegrationEventLog",
    "IntegrationEventLogError",
    "DEFAULT_LOG_CAPACITY",
    "MAX_LOG_CAPACITY",
    "build_default_event_log",
]
