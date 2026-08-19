# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_queue.py

Phase 5.0-D3-C: InitiativeQueue 候选行动队列。

职责:
- 线程安全 FIFO
- 容量限制
- enqueue / dequeue / peek / remove / history
- 按 priority 降序插入(同优先级内 FIFO)

约束:
- 不依赖业务模块
- 仅 Python 标准库
- 不执行任何动作
"""
from __future__ import annotations

import heapq
import itertools
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.initiative.possible_action import (
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    PossibleAction,
)


# ============================================================
# 常量
# ============================================================
INITIATIVE_QUEUE_SCHEMA_VERSION = "1.0"

DEFAULT_QUEUE_CAPACITY = 128
MAX_QUEUE_CAPACITY = 4096
MAX_HISTORY = 1024


def _safe_id(s: str) -> str:
    if s is None:
        return ""
    return str(s)


# ============================================================
# 队列条目
# ============================================================
@dataclass
class _QueueItem:
    seq: int
    priority: float
    action: PossibleAction

    def __lt__(self, other: "_QueueItem") -> bool:  # 用于 heapq
        # 优先级降序:priority 大的先出 → heapq 是小顶堆,所以反向
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.seq < other.seq


# ============================================================
# 队列
# ============================================================
class InitiativeQueue:
    """线程安全 FIFO 队列(优先级降序,同优先级内 FIFO)。

    特点:
    - 容量限制
    - 仅接收 status=pending 的 PossibleAction
    - 接收时按 priority 降序入堆
    - dequeue 返回最高优先级(同优先级取最早入队)
    - history 记录被 dequeue / remove / 主动 drop 的对象(供未来审计)
    """

    def __init__(
        self,
        *,
        name: str = "initiative_queue",
        capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        self._name = str(name or "initiative_queue")
        try:
            cap = int(capacity)
        except Exception:
            cap = DEFAULT_QUEUE_CAPACITY
        if cap < 0:
            cap = 0
        if cap > MAX_QUEUE_CAPACITY:
            cap = MAX_QUEUE_CAPACITY
        self._capacity = cap
        self._lock = threading.RLock()
        # heap: list of _QueueItem
        self._heap: List[_QueueItem] = []
        # by_id: action_id -> _QueueItem
        self._by_id: Dict[str, _QueueItem] = {}
        # seq 计数器
        self._counter = itertools.count()
        # 统计
        self._enqueued = 0
        self._dequeued = 0
        self._dropped_capacity = 0
        self._dropped_status = 0
        self._removed = 0
        self._last_id: str = ""
        self._last_dequeue_id: str = ""
        self._last_error: str = ""
        self._closed = False
        # history(最近)
        self._history: deque = deque(maxlen=MAX_HISTORY)

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._heap)

    def __len__(self) -> int:
        return self.size

    @property
    def enqueued_count(self) -> int:
        with self._lock:
            return self._enqueued

    @property
    def dequeued_count(self) -> int:
        with self._lock:
            return self._dequeued

    @property
    def dropped_capacity_count(self) -> int:
        with self._lock:
            return self._dropped_capacity

    @property
    def dropped_status_count(self) -> int:
        with self._lock:
            return self._dropped_status

    @property
    def removed_count(self) -> int:
        with self._lock:
            return self._removed

    @property
    def last_id(self) -> str:
        with self._lock:
            return self._last_id

    @property
    def last_dequeue_id(self) -> str:
        with self._lock:
            return self._last_dequeue_id

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def is_full(self) -> bool:
        with self._lock:
            if self._capacity <= 0:
                return False
            return len(self._heap) >= self._capacity

    # --------------------------------------------------------
    # 入队
    # --------------------------------------------------------
    def enqueue(self, action: Any) -> bool:
        """入队一个 PossibleAction。

        - 仅 status=pending 才接收
        - 容量满时,若 capacity>0,丢弃当前;若 capacity=0 仍接收
        """
        if not isinstance(action, PossibleAction):
            with self._lock:
                self._dropped_status += 1
                self._last_error = "enqueue 收到非 PossibleAction"
            return False
        with self._lock:
            if self._closed:
                self._dropped_status += 1
                self._last_error = "queue 已关闭"
                return False
            if action.status != ACTION_STATUS_PENDING:
                self._dropped_status += 1
                self._last_error = f"非 pending 状态被拒绝: {action.status}"
                return False
            if not action.has_evidence():
                self._dropped_status += 1
                self._last_error = "无 evidence 的行动被拒绝"
                return False
            # 容量
            if self._capacity > 0 and len(self._heap) >= self._capacity:
                # 容量满,丢弃(本规则:不替换已有)
                self._dropped_capacity += 1
                self._last_error = "queue 已满"
                return False
            # 加入
            item = _QueueItem(
                seq=next(self._counter),
                priority=float(action.priority),
                action=action,
            )
            heapq.heappush(self._heap, item)
            self._by_id[action.action_id] = item
            self._enqueued += 1
            self._last_id = action.action_id
        return True

    # --------------------------------------------------------
    # 出队
    # --------------------------------------------------------
    def dequeue(self) -> Optional[PossibleAction]:
        """出队优先级最高(同优先级最早入队)的行动。"""
        with self._lock:
            while self._heap:
                item = heapq.heappop(self._heap)
                # 校验
                if not isinstance(item, _QueueItem):
                    continue
                # 同步 by_id
                self._by_id.pop(item.action.action_id, None)
                self._dequeued += 1
                self._last_dequeue_id = item.action.action_id
                self._push_history_locked(item.action, "dequeue")
                return item.action
            return None

    def peek(self) -> Optional[PossibleAction]:
        """查看队首,不出队。"""
        with self._lock:
            # 由于过滤可能产生 stale 元素,peek 也需要清理
            while self._heap:
                item = self._heap[0]
                if not isinstance(item, _QueueItem):
                    heapq.heappop(self._heap)
                    continue
                return item.action
            return None

    # --------------------------------------------------------
    # 删除
    # --------------------------------------------------------
    def remove(self, action_id: str) -> bool:
        """按 action_id 移除(惰性删除:仅从 by_id 移除,出队时跳过)。"""
        if not action_id:
            return False
        aid = _safe_id(action_id)
        with self._lock:
            item = self._by_id.pop(aid, None)
            if item is None:
                return False
            # 标记删除(在堆中惰性)
            # 我们用特殊 action_id 标记
            try:
                item.action.action_id = f"__removed__:{aid}"
            except Exception:
                pass
            self._removed += 1
            self._push_history_locked(item.action, "remove")
        return True

    def discard(self, action_id: str) -> bool:
        """把一个行动标记为 discarded(从队列移除,记录到 history)。"""
        if not action_id:
            return False
        aid = _safe_id(action_id)
        with self._lock:
            item = self._by_id.pop(aid, None)
            if item is None:
                return False
            try:
                item.action.mark_discarded()
                item.action.action_id = f"__discarded__:{aid}"
            except Exception:
                pass
            self._removed += 1
            self._push_history_locked(item.action, "discard")
        return True

    def contains(self, action_id: str) -> bool:
        if not action_id:
            return False
        with self._lock:
            return _safe_id(action_id) in self._by_id

    def get(self, action_id: str) -> Optional[PossibleAction]:
        if not action_id:
            return None
        with self._lock:
            item = self._by_id.get(_safe_id(action_id))
            if item is None:
                return None
            return item.action

    # --------------------------------------------------------
    # 列表
    # --------------------------------------------------------
    def list(self, *, limit: Optional[int] = None) -> List[PossibleAction]:
        """返回当前所有元素(按 priority 降序,FIFO 同序)。"""
        with self._lock:
            data = [it for it in sorted(self._heap) if isinstance(it, _QueueItem)]
        out: List[PossibleAction] = []
        for it in data:
            aid = it.action.action_id
            if not aid.startswith("__") and aid in self._by_id:
                out.append(it.action)
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                out = out[:n]
            except Exception:
                pass
        return out

    def history(self, *, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """返回最近被消费/移除的行动摘要(dequeue/remove/discard)。"""
        with self._lock:
            data = list(self._history)
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                data = data[-n:]
            except Exception:
                pass
        return list(data)

    def _push_history_locked(self, action: PossibleAction, op: str) -> None:
        try:
            self._history.append({
                "op": str(op),
                "action_id": str(action.action_id),
                "action_type": str(action.action_type),
                "topic": str(action.topic),
                "status": str(action.status),
                "priority": float(action.priority),
            })
        except Exception:
            pass

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> int:
        with self._lock:
            n = len(self._heap)
            self._heap.clear()
            self._by_id.clear()
            return n

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
        return True

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "capacity": self._capacity,
                "size": len(self._heap),
                "enqueued": self._enqueued,
                "dequeued": self._dequeued,
                "removed": self._removed,
                "dropped_capacity": self._dropped_capacity,
                "dropped_status": self._dropped_status,
                "last_id": self._last_id,
                "last_dequeue_id": self._last_dequeue_id,
                "is_closed": self._closed,
                "is_full": self.is_full,
                "history_size": len(self._history),
                "last_error": self._last_error,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InitiativeQueue(name={self._name!r}, "
                f"size={len(self._heap)}/{self._capacity}, "
                f"enq={self._enqueued}, deq={self._dequeued})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_initiative_queue(
    *,
    capacity: int = DEFAULT_QUEUE_CAPACITY,
) -> InitiativeQueue:
    return InitiativeQueue(capacity=capacity)


__all__ = [
    # 常量
    "INITIATIVE_QUEUE_SCHEMA_VERSION",
    "DEFAULT_QUEUE_CAPACITY",
    "MAX_QUEUE_CAPACITY",
    # 类
    "InitiativeQueue",
    # 工厂
    "build_default_initiative_queue",
]
