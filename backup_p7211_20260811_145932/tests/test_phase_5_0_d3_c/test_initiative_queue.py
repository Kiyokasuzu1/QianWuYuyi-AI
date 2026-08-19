# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_initiative_queue.py

Phase 5.0-D3-C: InitiativeQueue 单元测试。

覆盖:
- 默认配置
- 容量限制
- 入队/出队
- FIFO 同优先级
- 优先级降序
- peek / remove / discard / contains / get
- history 记录
- 线程安全并发
- 描述
"""
import threading
import unittest
from src.runtime.initiative.initiative_queue import (
    DEFAULT_QUEUE_CAPACITY,
    INITIATIVE_QUEUE_SCHEMA_VERSION,
    InitiativeQueue,
    MAX_QUEUE_CAPACITY,
    build_default_initiative_queue,
)
from src.runtime.initiative.possible_action import (
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_OBSERVE,
    ACTION_URGENCY_NORMAL,
    PossibleAction,
)


def _act(
    topic: str = "AI绘画",
    *,
    priority: float = 0.5,
    confidence: float = 0.6,
    expected_value: float = 0.6,
    effort: str = ACTION_EFFORT_LOW,
    action_type: str = ACTION_TYPE_OBSERVE,
    status: str = ACTION_STATUS_PENDING,
    supporting: list = None,
) -> PossibleAction:
    sigs = ["sig_1"] if supporting is None else list(supporting)
    return PossibleAction(
        action_type=action_type,
        topic=topic,
        rationale="r",
        supporting_signal_ids=sigs,
        urgency=ACTION_URGENCY_NORMAL,
        effort_estimate=effort,
        expected_value=expected_value,
        confidence=confidence,
        status=status,
        priority=priority,
    )


class TestQueueDefaults(unittest.TestCase):
    def test_default_creation(self):
        q = InitiativeQueue()
        self.assertEqual(q.name, "initiative_queue")
        self.assertEqual(q.capacity, DEFAULT_QUEUE_CAPACITY)
        self.assertEqual(q.size, 0)
        self.assertEqual(len(q), 0)
        self.assertFalse(q.is_full)
        self.assertFalse(q.is_closed)
        self.assertEqual(q.enqueued_count, 0)
        self.assertEqual(q.dequeued_count, 0)

    def test_custom_creation(self):
        q = InitiativeQueue(name="custom", capacity=64)
        self.assertEqual(q.name, "custom")
        self.assertEqual(q.capacity, 64)

    def test_invalid_capacity_clipped(self):
        q = InitiativeQueue(capacity=-1)
        self.assertEqual(q.capacity, 0)
        q2 = InitiativeQueue(capacity=9999999)
        self.assertEqual(q2.capacity, MAX_QUEUE_CAPACITY)

    def test_invalid_name_stringified(self):
        q = InitiativeQueue(name="")
        self.assertEqual(q.name, "initiative_queue")

    def test_schema_version(self):
        self.assertEqual(INITIATIVE_QUEUE_SCHEMA_VERSION, "1.0")

    def test_build_default(self):
        q = build_default_initiative_queue()
        self.assertIsInstance(q, InitiativeQueue)
        self.assertEqual(q.capacity, DEFAULT_QUEUE_CAPACITY)


class TestQueueEnqueue(unittest.TestCase):
    def test_enqueue_pending(self):
        q = InitiativeQueue()
        a = _act()
        ok = q.enqueue(a)
        self.assertTrue(ok)
        self.assertEqual(q.size, 1)
        self.assertEqual(q.enqueued_count, 1)
        self.assertEqual(q.last_id, a.action_id)

    def test_enqueue_non_pending_status(self):
        q = InitiativeQueue()
        a = _act(status=ACTION_STATUS_DISCARDED)
        self.assertFalse(q.enqueue(a))
        self.assertEqual(q.dropped_status_count, 1)
        self.assertEqual(q.size, 0)

    def test_enqueue_filtered_status(self):
        q = InitiativeQueue()
        a = _act(status=ACTION_STATUS_FILTERED)
        self.assertFalse(q.enqueue(a))
        self.assertEqual(q.dropped_status_count, 1)

    def test_enqueue_deferred_status(self):
        q = InitiativeQueue()
        a = _act(status=ACTION_STATUS_DEFERRED)
        self.assertFalse(q.enqueue(a))
        self.assertEqual(q.dropped_status_count, 1)

    def test_enqueue_non_possible_action(self):
        q = InitiativeQueue()
        self.assertFalse(q.enqueue("not an action"))  # type: ignore[arg-type]
        self.assertEqual(q.dropped_status_count, 1)

    def test_enqueue_no_evidence_rejected(self):
        q = InitiativeQueue()
        a = _act(supporting=[])
        self.assertFalse(q.enqueue(a))
        self.assertEqual(q.dropped_status_count, 1)
        self.assertIn("evidence", q.last_error)

    def test_enqueue_to_full_rejected(self):
        q = InitiativeQueue(capacity=2)
        a1 = _act(topic="A")
        a2 = _act(topic="B")
        a3 = _act(topic="C")
        self.assertTrue(q.enqueue(a1))
        self.assertTrue(q.enqueue(a2))
        self.assertFalse(q.enqueue(a3))
        self.assertEqual(q.dropped_capacity_count, 1)
        self.assertEqual(q.size, 2)
        self.assertIn("满", q.last_error)

    def test_enqueue_zero_capacity_always_accepts(self):
        q = InitiativeQueue(capacity=0)
        for i in range(10):
            self.assertTrue(q.enqueue(_act(topic=f"t{i}")))

    def test_enqueue_after_close_rejected(self):
        q = InitiativeQueue()
        q.close()
        self.assertTrue(q.is_closed)
        a = _act()
        self.assertFalse(q.enqueue(a))
        self.assertEqual(q.dropped_status_count, 1)


class TestQueueDequeue(unittest.TestCase):
    def test_dequeue_empty(self):
        q = InitiativeQueue()
        self.assertIsNone(q.dequeue())

    def test_dequeue_returns_action(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        out = q.dequeue()
        self.assertIs(out, a)
        self.assertEqual(q.size, 0)
        self.assertEqual(q.dequeued_count, 1)
        self.assertEqual(q.last_dequeue_id, a.action_id)

    def test_dequeue_priority_order(self):
        q = InitiativeQueue()
        a_low = _act(topic="low", priority=0.3)
        a_hi = _act(topic="hi", priority=0.9)
        a_mid = _act(topic="mid", priority=0.6)
        q.enqueue(a_low)
        q.enqueue(a_hi)
        q.enqueue(a_mid)
        # 优先级降序
        self.assertEqual(q.dequeue().topic, "hi")
        self.assertEqual(q.dequeue().topic, "mid")
        self.assertEqual(q.dequeue().topic, "low")

    def test_dequeue_fifo_same_priority(self):
        q = InitiativeQueue()
        actions = [_act(topic=f"t{i}", priority=0.5) for i in range(5)]
        for a in actions:
            q.enqueue(a)
        for i in range(5):
            self.assertEqual(q.dequeue().topic, f"t{i}")

    def test_dequeue_history_recorded(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        q.dequeue()
        h = q.history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["op"], "dequeue")
        self.assertEqual(h[0]["action_id"], a.action_id)


class TestQueuePeek(unittest.TestCase):
    def test_peek_empty(self):
        q = InitiativeQueue()
        self.assertIsNone(q.peek())

    def test_peek_does_not_remove(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        self.assertIs(q.peek(), a)
        self.assertEqual(q.size, 1)
        self.assertEqual(q.dequeued_count, 0)

    def test_peek_returns_highest_priority(self):
        q = InitiativeQueue()
        q.enqueue(_act(topic="low", priority=0.2))
        q.enqueue(_act(topic="hi", priority=0.9))
        self.assertEqual(q.peek().topic, "hi")


class TestQueueRemove(unittest.TestCase):
    def test_remove_existing(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        self.assertTrue(q.remove(a.action_id))
        self.assertEqual(q.removed_count, 1)
        self.assertFalse(q.contains(a.action_id))

    def test_remove_marks_action_id(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        self.assertTrue(q.remove(a.action_id))
        self.assertEqual(q.removed_count, 1)
        self.assertFalse(q.contains(a.action_id))
        # dequeue 仍可返回该 action,但其 id 已被标记为 __removed__:
        out = q.dequeue()
        self.assertIsNotNone(out)
        self.assertTrue(out.action_id.startswith("__removed__:"))
        # history 应当记录 remove
        h = q.history()
        ops = [item["op"] for item in h]
        self.assertIn("remove", ops)

    def test_remove_nonexistent(self):
        q = InitiativeQueue()
        self.assertFalse(q.remove("nonexistent"))
        self.assertEqual(q.removed_count, 0)

    def test_remove_empty_id(self):
        q = InitiativeQueue()
        self.assertFalse(q.remove(""))

    def test_discard_existing(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        self.assertTrue(q.discard(a.action_id))
        self.assertFalse(q.contains(a.action_id))
        self.assertEqual(q.removed_count, 1)

    def test_discard_nonexistent(self):
        q = InitiativeQueue()
        self.assertFalse(q.discard("nope"))


class TestQueueContainsGet(unittest.TestCase):
    def test_contains(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        self.assertTrue(q.contains(a.action_id))
        self.assertFalse(q.contains("missing"))
        self.assertFalse(q.contains(""))

    def test_get(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        out = q.get(a.action_id)
        self.assertIs(out, a)
        self.assertIsNone(q.get("missing"))
        self.assertIsNone(q.get(""))


class TestQueueList(unittest.TestCase):
    def test_list_priority_order(self):
        q = InitiativeQueue()
        q.enqueue(_act(topic="a", priority=0.3))
        q.enqueue(_act(topic="b", priority=0.7))
        q.enqueue(_act(topic="c", priority=0.5))
        out = q.list()
        self.assertEqual([a.topic for a in out], ["b", "c", "a"])

    def test_list_with_limit(self):
        q = InitiativeQueue()
        for i in range(10):
            q.enqueue(_act(topic=f"t{i}", priority=float(i)))
        out = q.list(limit=3)
        self.assertEqual(len(out), 3)

    def test_list_with_invalid_limit(self):
        q = InitiativeQueue()
        q.enqueue(_act())
        out = q.list(limit=0)
        self.assertEqual(out, [])
        out2 = q.list(limit=-1)
        self.assertEqual(out2, [])

    def test_list_excludes_removed(self):
        q = InitiativeQueue()
        a1 = _act(topic="A")
        a2 = _act(topic="B")
        q.enqueue(a1)
        q.enqueue(a2)
        q.remove(a1.action_id)
        out = q.list()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].topic, "B")


class TestQueueHistory(unittest.TestCase):
    def test_history_empty(self):
        q = InitiativeQueue()
        self.assertEqual(q.history(), [])

    def test_history_with_limit(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        q.dequeue()
        h = q.history(limit=0)
        self.assertEqual(h, [])
        h2 = q.history(limit=5)
        self.assertGreater(len(h2), 0)

    def test_history_remove_recorded(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        q.remove(a.action_id)
        h = q.history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["op"], "remove")

    def test_history_discard_recorded(self):
        q = InitiativeQueue()
        a = _act()
        q.enqueue(a)
        q.discard(a.action_id)
        h = q.history()
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["op"], "discard")


class TestQueueMaintenance(unittest.TestCase):
    def test_clear(self):
        q = InitiativeQueue()
        for i in range(5):
            q.enqueue(_act(topic=f"t{i}"))
        n = q.clear()
        self.assertEqual(n, 5)
        self.assertEqual(q.size, 0)

    def test_close(self):
        q = InitiativeQueue()
        self.assertFalse(q.is_closed)
        self.assertTrue(q.close())
        self.assertTrue(q.is_closed)
        # 第二次返回 False
        self.assertFalse(q.close())

    def test_describe(self):
        q = InitiativeQueue()
        q.enqueue(_act())
        d = q.describe()
        self.assertIn("name", d)
        self.assertIn("size", d)
        self.assertIn("capacity", d)
        self.assertIn("enqueued", d)
        self.assertIn("dequeued", d)
        self.assertEqual(d["enqueued"], 1)
        self.assertEqual(d["size"], 1)

    def test_repr(self):
        q = InitiativeQueue()
        s = repr(q)
        self.assertIn("InitiativeQueue", s)
        self.assertIn("size", s)


class TestQueueThreadSafety(unittest.TestCase):
    def test_concurrent_enqueue(self):
        q = InitiativeQueue(capacity=10000)
        def worker(start):
            for i in range(100):
                q.enqueue(_act(topic=f"t{start}_{i}"))
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(q.enqueued_count, 500)
        self.assertEqual(q.size, 500)

    def test_concurrent_dequeue(self):
        q = InitiativeQueue(capacity=10000)
        for i in range(500):
            q.enqueue(_act(topic=f"t{i}"))
        out_count = [0]
        lock = threading.Lock()
        def worker():
            for _ in range(50):
                a = q.dequeue()
                if a is not None:
                    with lock:
                        out_count[0] += 1
        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(out_count[0], 500)
        self.assertEqual(q.dequeued_count, 500)

    def test_concurrent_mixed(self):
        q = InitiativeQueue(capacity=10000)
        def producer():
            for i in range(100):
                q.enqueue(_act(topic=f"p{i}"))
        def consumer():
            for _ in range(100):
                q.dequeue()
        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=producer))
            threads.append(threading.Thread(target=consumer))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # enqueued 应当为 300
        self.assertEqual(q.enqueued_count, 300)
        # dequeued 应当为 300
        self.assertEqual(q.dequeued_count, 300)

    def test_concurrent_remove(self):
        q = InitiativeQueue(capacity=10000)
        items = []
        for i in range(50):
            a = _act(topic=f"t{i}")
            q.enqueue(a)
            items.append(a)
        def worker(idx):
            for i in range(idx, len(items), 2):
                q.remove(items[i].action_id)
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 所有偶数下标的应被移除
        self.assertEqual(q.removed_count, 50)


class TestQueueActionTypes(unittest.TestCase):
    def test_enqueue_learn_action(self):
        q = InitiativeQueue()
        a = PossibleAction(
            action_type=ACTION_TYPE_LEARN,
            topic="ML",
            rationale="r",
            supporting_signal_ids=["s1"],
            effort_estimate=ACTION_EFFORT_MEDIUM,
            expected_value=0.5,
            confidence=0.5,
        )
        self.assertTrue(q.enqueue(a))
        self.assertEqual(q.dequeue().action_type, ACTION_TYPE_LEARN)

    def test_evidence_required(self):
        q = InitiativeQueue()
        # 无 evidence 应被拒绝
        a = PossibleAction(
            action_type=ACTION_TYPE_OBSERVE,
            topic="X",
            rationale="r",
            supporting_signal_ids=[],
            expected_value=0.5,
            confidence=0.5,
        )
        self.assertFalse(q.enqueue(a))


if __name__ == "__main__":
    unittest.main()
