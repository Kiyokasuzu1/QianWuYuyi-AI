# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_integration_event_log.py

Phase 5.0-D2 Step 4: IntegrationEventLog 单元测试。

覆盖:
- 默认构造与容量配置
- 容量边界(0 / 1 / 最大)
- append() 接受 IntegrationEvent,拒绝非 IntegrationEvent
- append() 线程安全
- 容量超限 FIFO 淘汰
- extend() 批量追加
- 事件查询:按 event_type / source / since / until / limit
- latest() 返回最近 N 条
- count_by_type / type_counts
- clear() / close()
- describe() / __repr__
"""
import unittest
import threading
import time
from typing import List

from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_EMOTION_SHOULD_DECAY,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.integration_event_log import (
    DEFAULT_LOG_CAPACITY,
    IntegrationEventLog,
    IntegrationEventLogError,
    MAX_LOG_CAPACITY,
    build_default_event_log,
)


# ============================================================
# Fixtures
# ============================================================
def _make_event(
    event_type: str = INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    source: str = "test_source",
    payload: dict = None,
) -> IntegrationEvent:
    return make_integration_event(
        event_type=event_type,
        source=source,
        payload=payload or {},
    )


# ============================================================
# 默认构造
# ============================================================
class TestEventLogDefaults(unittest.TestCase):
    def test_default_construct(self):
        log = IntegrationEventLog()
        self.assertEqual(log.name, "integration_event_log")
        self.assertEqual(log.capacity, DEFAULT_LOG_CAPACITY)
        self.assertEqual(log.size, 0)
        self.assertEqual(log.total_appended, 0)
        self.assertEqual(log.total_dropped, 0)
        self.assertFalse(log.is_closed)

    def test_default_capacity_is_256(self):
        log = IntegrationEventLog()
        self.assertEqual(log.capacity, 256)

    def test_custom_name(self):
        log = IntegrationEventLog(name="custom_log")
        self.assertEqual(log.name, "custom_log")

    def test_factory(self):
        log = build_default_event_log(name="f", capacity=64)
        self.assertEqual(log.name, "f")
        self.assertEqual(log.capacity, 64)


# ============================================================
# 容量边界
# ============================================================
class TestEventLogCapacity(unittest.TestCase):
    def test_capacity_zero(self):
        """capacity=0:append 仍工作,但 buffer 为空(只统计)。"""
        log = IntegrationEventLog(capacity=0)
        self.assertEqual(log.capacity, 0)
        log.append(_make_event())
        self.assertEqual(log.size, 0)
        self.assertEqual(log.total_appended, 1)

    def test_capacity_one(self):
        log = IntegrationEventLog(capacity=1)
        log.append(_make_event())
        log.append(_make_event())
        self.assertEqual(log.size, 1)
        self.assertEqual(log.total_appended, 2)

    def test_capacity_clamped_to_max(self):
        log = IntegrationEventLog(capacity=MAX_LOG_CAPACITY + 1000)
        self.assertEqual(log.capacity, MAX_LOG_CAPACITY)

    def test_capacity_negative_clamped_to_zero(self):
        log = IntegrationEventLog(capacity=-5)
        self.assertEqual(log.capacity, 0)

    def test_fifo_eviction(self):
        """容量超限后,旧事件被 FIFO 淘汰。"""
        log = IntegrationEventLog(capacity=3)
        ev_a = _make_event(payload={"k": "A"})
        ev_b = _make_event(payload={"k": "B"})
        ev_c = _make_event(payload={"k": "C"})
        ev_d = _make_event(payload={"k": "D"})
        log.append(ev_a)
        log.append(ev_b)
        log.append(ev_c)
        log.append(ev_d)
        # size 仍是 3
        self.assertEqual(log.size, 3)
        # total_appended 是 4
        self.assertEqual(log.total_appended, 4)
        # 最后剩下的是 B/C/D
        events = log.events()
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1].payload.get("k"), "D")


# ============================================================
# append / extend
# ============================================================
class TestEventLogAppend(unittest.TestCase):
    def test_append_integration_event(self):
        log = IntegrationEventLog()
        ev = _make_event()
        ok = log.append(ev)
        self.assertTrue(ok)
        self.assertEqual(log.size, 1)
        self.assertEqual(log.total_appended, 1)
        self.assertEqual(log.last_event_id, ev.event_id)
        self.assertEqual(log.last_event_type, ev.event_type)

    def test_append_non_event_silent_returns_false(self):
        log = IntegrationEventLog()
        ok = log.append("not an event")
        self.assertFalse(ok)
        self.assertEqual(log.total_appended, 0)
        self.assertEqual(log.total_dropped, 1)
        self.assertIn("IntegrationEvent", log.last_error)

    def test_append_non_event_silent_false_raises(self):
        log = IntegrationEventLog()
        with self.assertRaises(IntegrationEventLogError):
            log.append(123, silent=False)  # type: ignore[arg-type]

    def test_append_after_close_dropped(self):
        log = IntegrationEventLog()
        log.close()
        ok = log.append(_make_event())
        self.assertFalse(ok)
        self.assertEqual(log.total_appended, 0)
        self.assertEqual(log.total_dropped, 1)

    def test_extend_batch(self):
        log = IntegrationEventLog()
        events = [_make_event(payload={"i": i}) for i in range(5)]
        n = log.extend(events)
        self.assertEqual(n, 5)
        self.assertEqual(log.size, 5)
        self.assertEqual(log.total_appended, 5)

    def test_extend_empty(self):
        log = IntegrationEventLog()
        n = log.extend([])
        self.assertEqual(n, 0)

    def test_extend_skips_invalid(self):
        log = IntegrationEventLog()
        events = [
            _make_event(payload={"i": 0}),
            "invalid",
            _make_event(payload={"i": 1}),
        ]
        n = log.extend(events)
        self.assertEqual(n, 2)
        self.assertEqual(log.size, 2)


# ============================================================
# 查询
# ============================================================
class TestEventLogQuery(unittest.TestCase):
    def setUp(self):
        self.log = IntegrationEventLog()
        self.log.append(_make_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
        ))
        time.sleep(0.01)
        self.log.append(_make_event(
            event_type=INTEGRATION_EMOTION_SHOULD_DECAY,
            source="emotion",
        ))
        time.sleep(0.01)
        self.log.append(_make_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
        ))
        time.sleep(0.01)
        self.log.append(_make_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source="host",
        ))

    def test_events_all(self):
        events = self.log.events()
        self.assertEqual(len(events), 4)

    def test_filter_by_type(self):
        events = self.log.events(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(len(events), 2)
        for e in events:
            self.assertEqual(e.event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)

    def test_filter_by_source(self):
        events = self.log.events(source="memory")
        self.assertEqual(len(events), 2)
        for e in events:
            self.assertEqual(e.source, "memory")

    def test_filter_by_type_and_source(self):
        events = self.log.events(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
        )
        self.assertEqual(len(events), 2)

    def test_filter_by_since(self):
        all_events = self.log.events()
        mid_ts = all_events[1].timestamp
        events = self.log.events(since=mid_ts)
        self.assertEqual(len(events), 3)

    def test_filter_by_until(self):
        all_events = self.log.events()
        mid_ts = all_events[1].timestamp
        events = self.log.events(until=mid_ts)
        self.assertEqual(len(events), 2)

    def test_filter_by_limit(self):
        events = self.log.events(limit=2)
        self.assertEqual(len(events), 2)
        # 取最后 2 条
        self.assertEqual(events[0], self.log.events()[2])

    def test_filter_limit_zero(self):
        events = self.log.events(limit=0)
        self.assertEqual(events, [])

    def test_latest_default(self):
        events = self.log.latest()
        self.assertEqual(len(events), 4)
        self.assertEqual(events[-1].event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)

    def test_latest_with_n(self):
        events = self.log.latest(2)
        self.assertEqual(len(events), 2)
        # 取最后 2 条
        self.assertEqual(events[0], self.log.events()[2])

    def test_count_by_type(self):
        self.assertEqual(
            self.log.count_by_type(INTEGRATION_MEMORY_SHOULD_CONSOLIDATE), 2
        )
        self.assertEqual(
            self.log.count_by_type(INTEGRATION_EMOTION_SHOULD_DECAY), 1
        )
        self.assertEqual(self.log.count_by_type("not_existing"), 0)

    def test_type_counts(self):
        counts = self.log.type_counts()
        self.assertEqual(counts[INTEGRATION_MEMORY_SHOULD_CONSOLIDATE], 2)
        self.assertEqual(counts[INTEGRATION_EMOTION_SHOULD_DECAY], 1)
        self.assertEqual(counts[INTEGRATION_LIFECYCLE_TICK_COMPLETE], 1)


# ============================================================
# 维护
# ============================================================
class TestEventLogMaintenance(unittest.TestCase):
    def test_clear(self):
        log = IntegrationEventLog()
        log.append(_make_event())
        log.append(_make_event())
        n = log.clear()
        self.assertEqual(n, 2)
        self.assertEqual(log.size, 0)
        self.assertEqual(log.type_counts(), {})

    def test_close(self):
        log = IntegrationEventLog()
        self.assertFalse(log.is_closed)
        result = log.close()
        self.assertTrue(result)
        self.assertTrue(log.is_closed)

    def test_close_idempotent(self):
        log = IntegrationEventLog()
        self.assertTrue(log.close())
        self.assertFalse(log.close())  # 第二次返回 False

    def test_len(self):
        log = IntegrationEventLog()
        log.append(_make_event())
        log.append(_make_event())
        self.assertEqual(len(log), 2)


# ============================================================
# 描述
# ============================================================
class TestEventLogDescribe(unittest.TestCase):
    def test_describe(self):
        log = IntegrationEventLog(name="desc_log", capacity=10)
        log.append(_make_event())
        d = log.describe()
        self.assertEqual(d["name"], "desc_log")
        self.assertEqual(d["capacity"], 10)
        self.assertEqual(d["size"], 1)
        self.assertEqual(d["total_appended"], 1)
        self.assertEqual(d["total_dropped"], 0)
        self.assertIn("type_counts", d)
        self.assertIn("is_closed", d)
        self.assertIn("last_event_type", d)
        self.assertIn("last_error", d)

    def test_repr(self):
        log = IntegrationEventLog(name="r")
        r = repr(log)
        self.assertIn("IntegrationEventLog", r)
        self.assertIn("r", r)
        self.assertIn("size=0", r)


# ============================================================
# 线程安全
# ============================================================
class TestEventLogThreadSafety(unittest.TestCase):
    def test_concurrent_append(self):
        """多线程并发 append,不应崩溃或丢失计数。"""
        log = IntegrationEventLog(capacity=1000)
        n_threads = 5
        n_per_thread = 50

        def worker(tid: int):
            for i in range(n_per_thread):
                log.append(_make_event(
                    event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
                    source=f"t{tid}",
                    payload={"tid": tid, "i": i},
                ))

        threads = []
        for tid in range(n_threads):
            t = threading.Thread(target=worker, args=(tid,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(log.total_appended, n_threads * n_per_thread)
        # 容量 1000 大于 250
        self.assertEqual(log.size, n_threads * n_per_thread)


# ============================================================
# 与 IntegrationEvent 集成
# ============================================================
class TestEventLogIntegrationEvent(unittest.TestCase):
    def test_append_via_make_integration_event(self):
        log = IntegrationEventLog()
        ev = make_integration_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
            payload={"x": 1},
            related_ids=["id1"],
        )
        log.append(ev)
        events = log.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, ev.event_id)
        self.assertEqual(events[0].payload.get("x"), 1)
        self.assertEqual(events[0].related_ids, ["id1"])


if __name__ == "__main__":
    unittest.main()
