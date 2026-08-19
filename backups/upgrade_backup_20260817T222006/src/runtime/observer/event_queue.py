# -*- coding: utf-8 -*-
"""
src/runtime/observer/event_queue.py

Phase 7.1 —— EventQueue 进程内有界队列 + 订阅机制。

核心设计:
1. subscribers 使用 dict(sid→queue) 管理,便于后续统计/踢客户端
2. push 时对慢消费者使用 put_nowait(),队列满了直接丢事件,绝
   不阻塞 RuntimePipeline 主链路
3. 支持 Last-Event-ID 断点续传:
   - recent(since_id=xxx) 返回指定 ID 之后的事件
   - 供 SSE 端点响应浏览器 Last-Event-ID 头
4. 订阅者有独立 queue,互不影响消费速度
"""
from __future__ import annotations

import logging
import queue
import threading
import uuid
import collections
from typing import Any, Dict, List, Optional, Tuple

from .runtime_event import RuntimeEvent

logger = logging.getLogger(__name__)


# ============================================================
# EventQueue
# ============================================================
class EventQueue:
    """
    进程内有界事件队列 + 多订阅者广播。

    线程安全(RLock 保护)。

    典型使用:
        eq = EventQueue(maxlen=500)
        eq.push(RuntimeEvent(...))

        sid, q, unsub = eq.subscribe()
        # 消费者端: q.get() / q.get(timeout=15)
        # 用完: unsub()
    """

    def __init__(
        self,
        maxlen: int = 500,
        subscriber_queue_len: int = 100,
    ) -> None:
        self._maxlen = max(1, int(maxlen))
        self._sub_queue_len = max(1, int(subscriber_queue_len))

        self._lock = threading.RLock()
        # 历史环形缓冲(保留最近 N 条,用于 SSE 首次连接回放 + Last-Event-ID)
        self._history: collections.deque = collections.deque(maxlen=self._maxlen)
        # event_id → index 快速定位(便于 since_id 查询)
        # 维护成本低,因为 deque 是 FIFO append,一旦 popLeft 时同步清理即可
        self._id_to_idx: Dict[str, int] = {}
        # subscribers: sid → queue.Queue
        self._subscribers: Dict[str, "queue.Queue[RuntimeEvent]"] = {}

    # --------------------------------------------------------
    # 写入端
    # --------------------------------------------------------
    def push(self, event: RuntimeEvent) -> None:
        """
        推送一条事件 → 历史缓冲 + 所有订阅者。

        线程安全。对任何异常(包括 event 非 RuntimeEvent)都吞掉,
        绝不抛异常到 RuntimePipeline 主链路。
        """
        try:
            if not isinstance(event, RuntimeEvent):
                logger.debug("[EventQueue.push] 跳过非 RuntimeEvent: %r", type(event))
                return
        except Exception:  # noqa: BLE001
            return

        try:
            with self._lock:
                # 保证进入历史的事件 timestamp 严格 > 尾事件,使 deque 顺序与 append 顺序一致
                # (避免 ns 级时间戳相同时 recent(since_id) 判断混乱)
                if self._history:
                    tail_ts = float(getattr(self._history[-1], "timestamp", 0.0))
                    if float(event.timestamp) <= tail_ts:
                        event.timestamp = tail_ts + 1e-9
                # 1) 追加到 history(deque 满了自动丢最左)
                self._history.append(event)
                # 维护 _id_to_idx: 新条目索引是 maxlen-1(如果满了) 或 len-1
                # deque 的索引不是固定的,因此我们只存 event_id→在 deque 中的"当前位置"
                # 为了简化: 不存绝对索引,而是 recent(since_id) 线性扫 deque
                # 这样 _id_to_idx 可以省掉 → 更简单、更少 bug
                # (线性扫 500 条 = 可忽略成本)
                # _id_to_idx 这里留空,不维护

                # 2) 广播到所有订阅者
                if self._subscribers:
                    # 迭代 list() 快照,避免 unsub 修改 dict 时冲突
                    dead_sids: List[str] = []
                    for sid, sub_q in list(self._subscribers.items()):
                        try:
                            sub_q.put_nowait(event)
                        except queue.Full:
                            # 慢消费者: 丢这条事件,不阻塞
                            # 如果连续多次满,后续由 unsub 负责清理
                            continue
                        except Exception:  # noqa: BLE001
                            dead_sids.append(sid)
                    for dead in dead_sids:
                        self._subscribers.pop(dead, None)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[EventQueue.push] 异常(已吞掉): %s", exc)

    # --------------------------------------------------------
    # 读取端 —— 历史查询
    # --------------------------------------------------------
    def recent(
        self,
        limit: int = 20,
        since_id: Optional[str] = None,
    ) -> List[RuntimeEvent]:
        """
        返回最近 N 条事件(历史缓冲内)。

        Args:
            limit:    返回条数上限(被裁剪到 1 ~ maxlen)
            since_id: 可选 Last-Event-ID,仅返回该 ID 之后的事件
                      (不包含 since_id 那条本身,符合 SSE 语义)

        Returns:
            按时间升序(旧→新)排列的事件列表
        """
        try:
            n = max(1, min(int(limit), self._maxlen))
        except (TypeError, ValueError):
            n = 20

        with self._lock:
            items = list(self._history)  # 快照,旧→新

        if not items:
            return []

        # Last-Event-ID 过滤
        if isinstance(since_id, str) and since_id:
            # 从后往前找到 since_id,截取其后
            start_idx = None
            for i in range(len(items) - 1, -1, -1):
                try:
                    if items[i].event_id == since_id:
                        start_idx = i + 1
                        break
                except Exception:  # noqa: BLE001
                    continue
            if start_idx is not None:
                if start_idx >= len(items):
                    return []
                items = items[start_idx:]

        if len(items) <= n:
            return items
        return items[-n:]

    def contains_event_id(self, event_id: str) -> bool:
        """仅用于测试/调试,查询某个 event_id 是否在历史缓冲中。"""
        if not isinstance(event_id, str) or not event_id:
            return False
        with self._lock:
            for ev in self._history:
                try:
                    if ev.event_id == event_id:
                        return True
                except Exception:  # noqa: BLE001
                    continue
        return False

    # --------------------------------------------------------
    # 读取端 —— 订阅
    # --------------------------------------------------------
    def subscribe(
        self,
        queue_len: Optional[int] = None,
    ) -> Tuple[str, "queue.Queue[RuntimeEvent]", Any]:
        """
        订阅实时事件。

        Args:
            queue_len: 订阅者队列容量,None 时使用默认 subscriber_queue_len

        Returns:
            (sid, subscriber_queue, unsubscribe_fn)
                - sid:  订阅者 ID,可用于踢客户端/统计
                - subscriber_queue: queue.Queue,消费者端 .get() 取事件
                - unsubscribe_fn: 无参可调用,调用后取消订阅
        """
        qlen = self._sub_queue_len if queue_len is None else max(1, int(queue_len))
        sid = "sub_" + uuid.uuid4().hex[:12]
        sub_q: "queue.Queue[RuntimeEvent]" = queue.Queue(maxsize=qlen)

        with self._lock:
            self._subscribers[sid] = sub_q

        unsubscribed_flag = {"done": False}

        def unsubscribe() -> None:
            if unsubscribed_flag["done"]:
                return
            unsubscribed_flag["done"] = True
            try:
                with self._lock:
                    self._subscribers.pop(sid, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[EventQueue.unsubscribe] 异常: %s", exc)

        return sid, sub_q, unsubscribe

    # --------------------------------------------------------
    # 订阅者管理(监控/调试)
    # --------------------------------------------------------
    def subscriber_count(self) -> int:
        """当前订阅者数量(线程安全快照)。"""
        with self._lock:
            return len(self._subscribers)

    def subscriber_ids(self) -> List[str]:
        """当前所有订阅者 sid(用于监控)。"""
        with self._lock:
            return list(self._subscribers.keys())

    def history_count(self) -> int:
        """历史缓冲条数。"""
        with self._lock:
            return len(self._history)


__all__ = ["EventQueue"]
