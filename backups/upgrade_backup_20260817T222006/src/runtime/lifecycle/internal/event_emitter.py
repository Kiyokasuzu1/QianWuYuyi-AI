# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/internal/event_emitter.py

Phase 5.0-D1: 生命周期事件发射器。

职责:
- 提供同步事件订阅/发射能力
- 线程安全
- 错误隔离:单个订阅者抛错不影响其他订阅者
- 事件历史(有容量上限)
- 支持 once / on / off / clear

约束:
- 不依赖任何业务模块
- 不调用 LLM / 数据库 / 第三方服务
- 不连接 RuntimeBootstrap / LongLoop / Orchestrator
- 仅作为 Lifecycle 内部事件总线
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 事件类型常量(Lifecycle 内部)
EVENT_TASK_REGISTERED = "lifecycle.task.registered"
EVENT_TASK_UNREGISTERED = "lifecycle.task.unregistered"
EVENT_TASK_STARTED = "lifecycle.task.started"
EVENT_TASK_FINISHED = "lifecycle.task.finished"
EVENT_TASK_FAILED = "lifecycle.task.failed"
EVENT_TASK_SKIPPED = "lifecycle.task.skipped"
EVENT_LIFECYCLE_STARTED = "lifecycle.started"
EVENT_LIFECYCLE_PAUSED = "lifecycle.paused"
EVENT_LIFECYCLE_RESUMED = "lifecycle.resumed"
EVENT_LIFECYCLE_STOPPED = "lifecycle.stopped"
EVENT_DECISION_MADE = "lifecycle.decision.made"

DEFAULT_HISTORY_CAPACITY = 256
MAX_HISTORY_CAPACITY = 10000

# 默认事件类型列表
ALL_EVENT_TYPES: Tuple[str, ...] = (
    EVENT_TASK_REGISTERED,
    EVENT_TASK_UNREGISTERED,
    EVENT_TASK_STARTED,
    EVENT_TASK_FINISHED,
    EVENT_TASK_FAILED,
    EVENT_TASK_SKIPPED,
    EVENT_LIFECYCLE_STARTED,
    EVENT_LIFECYCLE_PAUSED,
    EVENT_LIFECYCLE_RESUMED,
    EVENT_LIFECYCLE_STOPPED,
    EVENT_DECISION_MADE,
)


# ============================================================
# 异常
# ============================================================
class EventEmitterError(Exception):
    """EventEmitter 内部错误基类。"""


# ============================================================
# Event
# ============================================================
class LifecycleEvent:
    """单个事件的不可变表示。"""

    __slots__ = (
        "event_type",
        "payload",
        "timestamp",
        "source",
        "sequence",
    )

    def __init__(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timestamp: Optional[float] = None,
        source: str = "unknown",
        sequence: int = 0,
    ) -> None:
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type 必须为非空字符串")
        self.event_type = event_type
        self.payload: Dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
        self.timestamp = float(timestamp) if timestamp is not None else 0.0
        self.source = str(source or "unknown")
        self.sequence = int(sequence or 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "source": self.source,
            "sequence": self.sequence,
        }

    def __repr__(self) -> str:
        return (
            f"LifecycleEvent(type={self.event_type!r}, "
            f"source={self.source!r}, seq={self.sequence}, ts={self.timestamp})"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, LifecycleEvent):
            return NotImplemented
        return (
            self.event_type == other.event_type
            and self.sequence == other.sequence
            and self.source == other.source
        )

    def __hash__(self) -> int:
        return hash((self.event_type, self.sequence, self.source))


# ============================================================
# 订阅记录
# ============================================================
class _Subscription:
    """内部订阅记录(避免外部直接依赖)。"""

    __slots__ = ("event_type", "handler", "once", "id")

    def __init__(self, event_type: str, handler: Callable[..., Any], once: bool, sub_id: int) -> None:
        self.event_type = event_type
        self.handler = handler
        self.once = bool(once)
        self.id = int(sub_id)


# ============================================================
# EventEmitter
# ============================================================
class EventEmitter:
    """生命周期事件总线。

    设计:
    - on(event_type, handler): 持久订阅
    - once(event_type, handler): 一次性订阅
    - off(event_type, handler): 退订(按 handler 引用匹配)
    - emit(event_type, payload, ...): 同步发射
    - history(): 事件历史(按容量上限)
    - clear_subscribers / clear_history: 重置

    错误隔离:
    - 单个订阅者抛错被捕获,不影响其他订阅者
    - emit 返回 (success_count, error_count)

    线程安全:
    - 订阅和发射均在 RLock 保护下进行
    - 订阅者快照在锁内拷贝,避免迭代期间修改
    """

    def __init__(self, *, history_capacity: int = DEFAULT_HISTORY_CAPACITY, name: str = "emitter") -> None:
        cap = int(history_capacity or 0)
        if cap < 0:
            cap = 0
        if cap > MAX_HISTORY_CAPACITY:
            cap = MAX_HISTORY_CAPACITY
        self._history_capacity = cap
        self._name = str(name or "emitter")

        self._lock = threading.RLock()
        # event_type -> list[_Subscription]
        self._subscribers: Dict[str, List[_Subscription]] = {}
        # 订阅计数(所有事件类型合计,不去重)
        self._total_subscriptions = 0
        # 一次性订阅标记
        self._next_sub_id = 1
        # 序列号(单调递增)
        self._next_sequence = 1
        # 历史
        self._history: Deque[LifecycleEvent] = deque(maxlen=cap) if cap > 0 else deque()
        # 失败计数
        self._emit_failures = 0
        self._total_emitted = 0
        # 是否已销毁
        self._closed = False

    # ------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def history_capacity(self) -> int:
        return self._history_capacity

    @property
    def total_emitted(self) -> int:
        with self._lock:
            return self._total_emitted

    @property
    def emit_failures(self) -> int:
        with self._lock:
            return self._emit_failures

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def subscriber_count(self, event_type: Optional[str] = None) -> int:
        """获取订阅者数量。event_type=None 时返回总数。"""
        with self._lock:
            if event_type is None:
                return self._total_subscriptions
            return len(self._subscribers.get(event_type, ()))

    def history_size(self) -> int:
        with self._lock:
            return len(self._history)

    def event_types(self) -> List[str]:
        """当前已注册订阅的事件类型列表。"""
        with self._lock:
            return sorted(self._subscribers.keys())

    # ------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------
    def on(self, event_type: str, handler: Callable[..., Any]) -> int:
        """注册持久订阅,返回订阅 id。"""
        return self._subscribe(event_type, handler, once=False)

    def once(self, event_type: str, handler: Callable[..., Any]) -> int:
        """注册一次性订阅,触发后自动移除。"""
        return self._subscribe(event_type, handler, once=True)

    def _subscribe(self, event_type: str, handler: Callable[..., Any], *, once: bool) -> int:
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type 必须为非空字符串")
        if not callable(handler):
            raise ValueError("handler 必须可调用")
        with self._lock:
            if self._closed:
                raise EventEmitterError("EventEmitter 已销毁,无法订阅")
            sub_id = self._next_sub_id
            self._next_sub_id += 1
            sub = _Subscription(event_type, handler, once=bool(once), sub_id=sub_id)
            self._subscribers.setdefault(event_type, []).append(sub)
            self._total_subscriptions += 1
            return sub_id

    def off(self, event_type: str, handler: Optional[Callable[..., Any]] = None) -> int:
        """退订。

        - handler=None: 退订该事件类型所有订阅
        - handler 指定: 仅退订匹配引用的订阅

        返回实际退订数量。
        """
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type 必须为非空字符串")
        with self._lock:
            if event_type not in self._subscribers:
                return 0
            subs = self._subscribers[event_type]
            if handler is None:
                removed = len(subs)
                self._subscribers.pop(event_type, None)
                self._total_subscriptions -= removed
                return removed
            kept: List[_Subscription] = []
            removed = 0
            for s in subs:
                if s.handler is handler:
                    removed += 1
                    self._total_subscriptions -= 1
                else:
                    kept.append(s)
            if kept:
                self._subscribers[event_type] = kept
            else:
                self._subscribers.pop(event_type, None)
            return removed

    def off_by_id(self, sub_id: int) -> bool:
        """按订阅 id 退订。"""
        target = int(sub_id)
        with self._lock:
            for event_type, subs in self._subscribers.items():
                kept: List[_Subscription] = []
                removed = False
                for s in subs:
                    if not removed and s.id == target:
                        removed = True
                        self._total_subscriptions -= 1
                    else:
                        kept.append(s)
                if removed:
                    if kept:
                        self._subscribers[event_type] = kept
                    else:
                        self._subscribers.pop(event_type, None)
                    return True
            return False

    def clear_subscribers(self, event_type: Optional[str] = None) -> int:
        """清空订阅。event_type=None 时清空全部。"""
        with self._lock:
            if event_type is None:
                removed = self._total_subscriptions
                self._subscribers.clear()
                self._total_subscriptions = 0
                return removed
            if event_type in self._subscribers:
                removed = len(self._subscribers[event_type])
                self._subscribers.pop(event_type, None)
                self._total_subscriptions -= removed
                return removed
            return 0

    # ------------------------------------------------------------
    # 发射
    # ------------------------------------------------------------
    def emit(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        source: str = "unknown",
        timestamp: Optional[float] = None,
    ) -> Tuple[int, int]:
        """同步发射事件。

        返回 (success_count, error_count)。
        - 一次性订阅触发后自动移除
        - 订阅者抛错被隔离,不影响其他订阅者
        - 事件始终记录到历史(若启用)
        """
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type 必须为非空字符串")

        with self._lock:
            if self._closed:
                raise EventEmitterError("EventEmitter 已销毁,无法发射")
            seq = self._next_sequence
            self._next_sequence += 1
            ts = float(timestamp) if timestamp is not None else 0.0
            event = LifecycleEvent(
                event_type=event_type,
                payload=payload,
                timestamp=ts,
                source=str(source or "unknown"),
                sequence=seq,
            )
            # 记录历史
            if self._history_capacity > 0:
                self._history.append(event)
            # 拷贝订阅者快照
            subs = list(self._subscribers.get(event_type, ()))
            # 一次性订阅标记
            once_ids = [s.id for s in subs if s.once]
            self._total_emitted += 1
        # 在锁外调用,避免订阅者重入死锁
        success = 0
        errors = 0
        for sub in subs:
            try:
                sub.handler(event)
                success += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                with self._lock:
                    self._emit_failures += 1
                logger.warning(
                    "EventEmitter(%s) handler for %s raised: %s",
                    self._name,
                    event_type,
                    exc,
                    exc_info=False,
                )
        # 清理一次性订阅
        if once_ids:
            with self._lock:
                kept = [s for s in self._subscribers.get(event_type, ()) if s.id not in set(once_ids)]
                if kept:
                    self._subscribers[event_type] = kept
                else:
                    self._subscribers.pop(event_type, None)
                self._total_subscriptions -= len(once_ids)
        return success, errors

    # ------------------------------------------------------------
    # 历史
    # ------------------------------------------------------------
    def history(self, event_type: Optional[str] = None, limit: Optional[int] = None) -> List[LifecycleEvent]:
        """获取事件历史(快照)。

        - event_type: 可选,仅返回匹配类型
        - limit: 仅返回最近 N 条
        """
        with self._lock:
            data = list(self._history)
        if event_type is not None:
            data = [e for e in data if e.event_type == event_type]
        if limit is not None:
            n = int(limit)
            if n < 0:
                n = 0
            if n == 0:
                return []
            data = data[-n:]
        return data

    def clear_history(self) -> int:
        """清空历史,返回清空数量。"""
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n

    # ------------------------------------------------------------
    # 销毁
    # ------------------------------------------------------------
    def close(self) -> None:
        """关闭发射器(清空订阅与历史,后续订阅/发射将失败)。"""
        with self._lock:
            self._closed = True
            self._subscribers.clear()
            self._total_subscriptions = 0
            self._history.clear()

    # ------------------------------------------------------------
    # 上下文管理器
    # ------------------------------------------------------------
    def __enter__(self) -> "EventEmitter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # ------------------------------------------------------------
    # 调试
    # ------------------------------------------------------------
    def __repr__(self) -> str:
        with self._lock:
            return (
                f"EventEmitter(name={self._name!r}, "
                f"subs={self._total_subscriptions}, "
                f"emitted={self._total_emitted}, "
                f"history={len(self._history)}/{self._history_capacity})"
            )
