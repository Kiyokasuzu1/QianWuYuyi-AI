# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_event_emitter.py

Phase 5.0-D1 Step 3: EventEmitter 测试。

覆盖:
- 构造与默认属性
- on / once / off / off_by_id
- 事件发射与历史
- 错误隔离
- 线程安全
- 订阅者数量统计
- 关闭/上下文管理
- 边界场景(空订阅、容量上限)
"""
from __future__ import annotations

import threading
import unittest

from src.runtime.lifecycle.internal.event_emitter import (
    DEFAULT_HISTORY_CAPACITY,
    EVENT_LIFECYCLE_STARTED,
    EVENT_TASK_FAILED,
    EVENT_TASK_FINISHED,
    EVENT_TASK_REGISTERED,
    EventEmitter,
    EventEmitterError,
    LifecycleEvent,
)


class TestEventDataclass(unittest.TestCase):
    def test_basic_construction(self):
        ev = LifecycleEvent("test.event", {"k": 1}, source="s", sequence=1, timestamp=10.0)
        self.assertEqual(ev.event_type, "test.event")
        self.assertEqual(ev.payload, {"k": 1})
        self.assertEqual(ev.source, "s")
        self.assertEqual(ev.sequence, 1)
        self.assertEqual(ev.timestamp, 10.0)

    def test_empty_event_type_raises(self):
        with self.assertRaises(ValueError):
            LifecycleEvent("")

    def test_non_string_event_type_raises(self):
        with self.assertRaises(ValueError):
            LifecycleEvent(123)  # type: ignore[arg-type]

    def test_payload_defaults_to_empty_dict(self):
        ev = LifecycleEvent("x")
        self.assertEqual(ev.payload, {})

    def test_payload_is_copied(self):
        src = {"a": 1}
        ev = LifecycleEvent("x", src)
        src["a"] = 999
        self.assertEqual(ev.payload["a"], 1)

    def test_non_dict_payload_becomes_empty(self):
        ev = LifecycleEvent("x", "not_a_dict")  # type: ignore[arg-type]
        self.assertEqual(ev.payload, {})

    def test_to_dict(self):
        ev = LifecycleEvent("x", {"k": "v"}, source="s", sequence=2, timestamp=5.0)
        d = ev.to_dict()
        self.assertEqual(d["event_type"], "x")
        self.assertEqual(d["payload"], {"k": "v"})
        self.assertEqual(d["source"], "s")
        self.assertEqual(d["sequence"], 2)
        self.assertEqual(d["timestamp"], 5.0)

    def test_repr(self):
        ev = LifecycleEvent("t", sequence=1)
        self.assertIn("t", repr(ev))

    def test_equality_and_hash(self):
        a = LifecycleEvent("t", source="s", sequence=1)
        b = LifecycleEvent("t", source="s", sequence=1)
        c = LifecycleEvent("t", source="s", sequence=2)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(hash(a), hash(b))


class TestConstruction(unittest.TestCase):
    def test_default(self):
        e = EventEmitter()
        self.assertEqual(e.history_capacity, DEFAULT_HISTORY_CAPACITY)
        self.assertEqual(e.history_size(), 0)
        self.assertEqual(e.total_emitted, 0)
        self.assertEqual(e.emit_failures, 0)
        self.assertFalse(e.is_closed)

    def test_custom_name(self):
        e = EventEmitter(name="emitter_x")
        self.assertEqual(e.name, "emitter_x")
        self.assertIn("emitter_x", repr(e))

    def test_custom_history_capacity(self):
        e = EventEmitter(history_capacity=5)
        self.assertEqual(e.history_capacity, 5)

    def test_history_capacity_clamped(self):
        e = EventEmitter(history_capacity=99999999)
        self.assertLessEqual(e.history_capacity, 10000)

    def test_history_capacity_zero_disables(self):
        e = EventEmitter(history_capacity=0)
        self.assertEqual(e.history_size(), 0)
        e.emit("t")
        self.assertEqual(e.history_size(), 0)

    def test_negative_history_capacity_becomes_zero(self):
        e = EventEmitter(history_capacity=-5)
        self.assertEqual(e.history_capacity, 0)


class TestSubscribe(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter()

    def test_on_returns_id(self):
        sid = self.emitter.on("e", lambda ev: None)
        self.assertIsInstance(sid, int)
        self.assertGreater(sid, 0)

    def test_on_invalid_event_type_raises(self):
        with self.assertRaises(ValueError):
            self.emitter.on("", lambda ev: None)
        with self.assertRaises(ValueError):
            self.emitter.on(123, lambda ev: None)  # type: ignore[arg-type]

    def test_on_non_callable_raises(self):
        with self.assertRaises(ValueError):
            self.emitter.on("e", "not_callable")  # type: ignore[arg-type]

    def test_subscriber_count_total(self):
        self.emitter.on("e1", lambda ev: None)
        self.emitter.on("e1", lambda ev: None)
        self.emitter.on("e2", lambda ev: None)
        self.assertEqual(self.emitter.subscriber_count(), 3)
        self.assertEqual(self.emitter.subscriber_count("e1"), 2)
        self.assertEqual(self.emitter.subscriber_count("e2"), 1)
        self.assertEqual(self.emitter.subscriber_count("e3"), 0)

    def test_event_types(self):
        self.emitter.on("e1", lambda ev: None)
        self.emitter.on("e2", lambda ev: None)
        self.assertEqual(self.emitter.event_types(), ["e1", "e2"])

    def test_unique_sub_ids(self):
        ids = [self.emitter.on("e", lambda ev: None) for _ in range(10)]
        self.assertEqual(len(set(ids)), 10)


class TestEmit(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter(history_capacity=10)

    def test_basic_emit(self):
        called = []

        def h(ev):
            called.append(ev)

        self.emitter.on("e", h)
        success, errors = self.emitter.emit("e", {"x": 1}, source="s", timestamp=1.0)
        self.assertEqual(success, 1)
        self.assertEqual(errors, 0)
        self.assertEqual(len(called), 1)
        self.assertEqual(called[0].payload, {"x": 1})
        self.assertEqual(called[0].source, "s")
        self.assertEqual(self.emitter.total_emitted, 1)

    def test_emit_no_subscribers(self):
        success, errors = self.emitter.emit("e", {"x": 1})
        self.assertEqual(success, 0)
        self.assertEqual(errors, 0)
        self.assertEqual(self.emitter.total_emitted, 1)

    def test_emit_invalid_event_type(self):
        with self.assertRaises(ValueError):
            self.emitter.emit("")
        with self.assertRaises(ValueError):
            self.emitter.emit(123)  # type: ignore[arg-type]

    def test_emit_history(self):
        self.emitter.emit("e1", {"i": 1})
        self.emitter.emit("e2", {"i": 2})
        self.emitter.emit("e1", {"i": 3})
        hist = self.emitter.history()
        self.assertEqual(len(hist), 3)
        self.assertEqual([e.event_type for e in hist], ["e1", "e2", "e1"])

    def test_emit_sequence_monotonic(self):
        ids = []
        for i in range(5):
            self.emitter.emit("e", {"i": i})
        hist = self.emitter.history()
        ids = [e.sequence for e in hist]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 5)

    def test_emit_to_specific_subscriber(self):
        a_calls, b_calls = [], []

        def a(ev):
            a_calls.append(ev)

        def b(ev):
            b_calls.append(ev)

        self.emitter.on("a_type", a)
        self.emitter.on("b_type", b)
        self.emitter.emit("a_type")
        self.assertEqual(len(a_calls), 1)
        self.assertEqual(len(b_calls), 0)


class TestErrorIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter(history_capacity=10)

    def test_handler_exception_does_not_propagate(self):
        results = []

        def bad(ev):
            raise RuntimeError("boom")

        def good(ev):
            results.append("good")

        self.emitter.on("e", bad)
        self.emitter.on("e", good)
        success, errors = self.emitter.emit("e")
        self.assertEqual(success, 1)
        self.assertEqual(errors, 1)
        self.assertEqual(results, ["good"])
        self.assertEqual(self.emitter.emit_failures, 1)

    def test_all_handlers_fail(self):
        def bad1(ev):
            raise RuntimeError("1")

        def bad2(ev):
            raise ValueError("2")

        self.emitter.on("e", bad1)
        self.emitter.on("e", bad2)
        success, errors = self.emitter.emit("e")
        self.assertEqual(success, 0)
        self.assertEqual(errors, 2)
        self.assertEqual(self.emitter.emit_failures, 2)

    def test_handler_called_even_if_previous_failed(self):
        order = []

        def bad(ev):
            order.append("bad")
            raise RuntimeError()

        def good(ev):
            order.append("good")

        self.emitter.on("e", bad)
        self.emitter.on("e", good)
        self.emitter.emit("e")
        self.assertEqual(order, ["bad", "good"])


class TestUnsubscribe(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter()

    def test_off_specific(self):
        called = []

        def h(ev):
            called.append(ev)

        self.emitter.on("e", h)
        self.emitter.off("e", h)
        self.emitter.emit("e")
        self.assertEqual(called, [])
        self.assertEqual(self.emitter.subscriber_count(), 0)

    def test_off_by_id(self):
        def h(ev):
            pass

        sid = self.emitter.on("e", h)
        self.assertTrue(self.emitter.off_by_id(sid))
        self.assertEqual(self.emitter.subscriber_count(), 0)

    def test_off_by_id_missing(self):
        self.assertFalse(self.emitter.off_by_id(99999))

    def test_off_all_of_type(self):
        self.emitter.on("e", lambda ev: None)
        self.emitter.on("e", lambda ev: None)
        n = self.emitter.off("e")
        self.assertEqual(n, 2)
        self.assertEqual(self.emitter.subscriber_count(), 0)

    def test_off_unknown_type(self):
        self.assertEqual(self.emitter.off("nonexistent"), 0)

    def test_off_invalid_event_type(self):
        with self.assertRaises(ValueError):
            self.emitter.off("")

    def test_clear_subscribers_all(self):
        self.emitter.on("e1", lambda ev: None)
        self.emitter.on("e2", lambda ev: None)
        n = self.emitter.clear_subscribers()
        self.assertEqual(n, 2)
        self.assertEqual(self.emitter.subscriber_count(), 0)

    def test_clear_subscribers_specific(self):
        self.emitter.on("e1", lambda ev: None)
        self.emitter.on("e2", lambda ev: None)
        n = self.emitter.clear_subscribers("e1")
        self.assertEqual(n, 1)
        self.assertEqual(self.emitter.subscriber_count(), 1)


class TestOnce(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter()

    def test_once_triggered_once(self):
        calls = []

        def h(ev):
            calls.append(ev)

        self.emitter.once("e", h)
        self.emitter.emit("e")
        self.emitter.emit("e")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.emitter.subscriber_count(), 0)

    def test_once_does_not_interfere_with_persistent(self):
        calls_p, calls_o = [], []

        def persistent(ev):
            calls_p.append(ev)

        def one(ev):
            calls_o.append(ev)

        self.emitter.on("e", persistent)
        self.emitter.once("e", one)
        self.emitter.emit("e")
        self.emitter.emit("e")
        self.assertEqual(len(calls_p), 2)
        self.assertEqual(len(calls_o), 1)


class TestHistory(unittest.TestCase):
    def setUp(self) -> None:
        self.emitter = EventEmitter(history_capacity=5)

    def test_history_capacity_overflow(self):
        for i in range(10):
            self.emitter.emit("e", {"i": i})
        self.assertEqual(self.emitter.history_size(), 5)
        # 最近 5 条
        hist = self.emitter.history()
        self.assertEqual([e.payload["i"] for e in hist], [5, 6, 7, 8, 9])

    def test_history_filter_by_type(self):
        for i in range(3):
            self.emitter.emit("e1", {"i": i})
        for i in range(2):
            self.emitter.emit("e2", {"i": i})
        e1_hist = self.emitter.history(event_type="e1")
        self.assertEqual(len(e1_hist), 3)
        self.assertTrue(all(e.event_type == "e1" for e in e1_hist))

    def test_history_with_limit(self):
        for i in range(5):
            self.emitter.emit("e", {"i": i})
        hist = self.emitter.history(limit=2)
        self.assertEqual(len(hist), 2)
        self.assertEqual([e.payload["i"] for e in hist], [3, 4])

    def test_history_limit_zero(self):
        self.emitter.emit("e")
        self.assertEqual(self.emitter.history(limit=0), [])

    def test_history_limit_negative(self):
        self.emitter.emit("e")
        self.assertEqual(self.emitter.history(limit=-1), [])

    def test_clear_history(self):
        for _ in range(3):
            self.emitter.emit("e")
        n = self.emitter.clear_history()
        self.assertEqual(n, 3)
        self.assertEqual(self.emitter.history_size(), 0)
        # 不影响订阅
        sid = self.emitter.on("e", lambda ev: None)
        self.assertIsInstance(sid, int)


class TestClose(unittest.TestCase):
    def test_close_marks_closed(self):
        e = EventEmitter()
        e.close()
        self.assertTrue(e.is_closed)

    def test_close_clears_subscribers(self):
        e = EventEmitter()
        e.on("e", lambda ev: None)
        e.close()
        self.assertEqual(e.subscriber_count(), 0)

    def test_emit_after_close_raises(self):
        e = EventEmitter()
        e.close()
        with self.assertRaises(EventEmitterError):
            e.emit("e")

    def test_subscribe_after_close_raises(self):
        e = EventEmitter()
        e.close()
        with self.assertRaises(EventEmitterError):
            e.on("e", lambda ev: None)

    def test_context_manager(self):
        with EventEmitter() as e:
            e.on("x", lambda ev: None)
        self.assertTrue(e.is_closed)
        with self.assertRaises(EventEmitterError):
            e.emit("x")


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_emit(self):
        e = EventEmitter(history_capacity=1000)
        counter = [0]
        lock = threading.Lock()

        def h(ev):
            with lock:
                counter[0] += 1

        e.on("e", h)
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            for _ in range(50):
                e.emit("e")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(counter[0], 8 * 50)
        self.assertEqual(e.history_size(), 8 * 50)

    def test_concurrent_subscribe_emit(self):
        e = EventEmitter(history_capacity=1000)
        counter = [0]
        lock = threading.Lock()

        def h(ev):
            with lock:
                counter[0] += 1

        e.on("e", h)

        def subscribe_worker():
            for _ in range(20):
                e.on("e", lambda ev: None)

        def emit_worker():
            for _ in range(20):
                e.emit("e")

        threads = [
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=emit_worker),
            threading.Thread(target=emit_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # counter 只统计 persistent 订阅的命中
        self.assertGreater(counter[0], 0)


class TestStandardEvents(unittest.TestCase):
    def test_standard_event_constants(self):
        from src.runtime.lifecycle.internal.event_emitter import (
            ALL_EVENT_TYPES,
            EVENT_LIFECYCLE_PAUSED,
            EVENT_LIFECYCLE_RESUMED,
            EVENT_LIFECYCLE_STOPPED,
            EVENT_TASK_SKIPPED,
            EVENT_DECISION_MADE,
        )
        self.assertIn(EVENT_TASK_REGISTERED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_TASK_FAILED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_LIFECYCLE_STARTED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_LIFECYCLE_PAUSED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_LIFECYCLE_RESUMED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_LIFECYCLE_STOPPED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_TASK_FINISHED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_TASK_SKIPPED, ALL_EVENT_TYPES)
        self.assertIn(EVENT_DECISION_MADE, ALL_EVENT_TYPES)

    def test_emit_using_standard_event(self):
        e = EventEmitter()
        seen = []
        e.on(EVENT_LIFECYCLE_STARTED, lambda ev: seen.append(ev))
        e.emit(EVENT_LIFECYCLE_STARTED, {"reason": "boot"})
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].payload["reason"], "boot")


class TestReentrantSafety(unittest.TestCase):
    def test_handler_can_subscribe_during_emit(self):
        e = EventEmitter(history_capacity=10)
        order = []

        def h1(ev):
            order.append("h1")
            # 在 handler 中订阅不应死锁
            e.on("e", h2)

        def h2(ev):
            order.append("h2")

        e.on("e", h1)
        e.emit("e")
        # h2 在本次 emit 中不会触发(拷贝快照后才订阅)
        self.assertEqual(order, ["h1"])
        e.emit("e")
        self.assertEqual(order, ["h1", "h1", "h2"])


if __name__ == "__main__":
    unittest.main()
