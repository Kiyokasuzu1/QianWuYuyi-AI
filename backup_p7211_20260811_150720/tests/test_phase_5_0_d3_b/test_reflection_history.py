# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_history.py

Phase 5.0-D3-B: ReflectionHistory / ReflectionRecord 单元测试。
"""
import threading
import time
import unittest

from src.runtime.reflection.reflection_history import (
    DEFAULT_REFLECTION_HISTORY_CAPACITY,
    MAX_REFLECTION_HISTORY_CAPACITY,
    REFLECTION_HISTORY_SCHEMA_VERSION,
    ReflectionHistory,
    ReflectionHistoryError,
    ReflectionRecord,
    build_default_reflection_history,
)
from src.runtime.reflection.reflection_result import (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
    build_empty_reflection_result,
)


def _make_result(reflection_id: str = "ref_1", rtype: str = REFLECTION_TYPE_DAILY) -> ReflectionResult:
    r = build_empty_reflection_result(reflection_type=rtype, now=100.0)
    r.reflection_id = reflection_id
    r.insights = [Insight(supporting_event_ids=["e1"])]
    r.suggested_changes = [
        StateChangeSuggestion(target_field="x", evidence_event_ids=["e1"], delta=0.1)
    ]
    r.source_event_ids = ["e1", "e2"]
    return r


class TestReflectionRecord(unittest.TestCase):
    def test_default_creation(self):
        rec = ReflectionRecord()
        self.assertEqual(rec.reflection_id, "")
        self.assertEqual(rec.type, "daily")

    def test_invalid_type_fallback(self):
        rec = ReflectionRecord(type="invalid")
        self.assertEqual(rec.type, "daily")

    def test_confidence_clipping(self):
        rec = ReflectionRecord(confidence=2.0)
        self.assertEqual(rec.confidence, 1.0)
        rec = ReflectionRecord(confidence=-1.0)
        self.assertEqual(rec.confidence, 0.0)
        rec = ReflectionRecord(confidence="bad")
        self.assertEqual(rec.confidence, 0.5)

    def test_source_event_ids_dedup(self):
        rec = ReflectionRecord(source_event_ids=["e1", "e1", "e2", None, 123])
        self.assertEqual(rec.source_event_ids.count("e1"), 1)
        self.assertIn("e2", rec.source_event_ids)
        self.assertIn("123", rec.source_event_ids)

    def test_from_result(self):
        r = _make_result()
        rec = ReflectionRecord.from_result(r, timestamp=200.0)
        self.assertEqual(rec.reflection_id, "ref_1")
        self.assertEqual(rec.type, REFLECTION_TYPE_DAILY)
        self.assertEqual(rec.timestamp, 200.0)
        self.assertEqual(rec.source_event_ids, ["e1", "e2"])
        self.assertEqual(rec.insight_count, 1)
        self.assertEqual(rec.suggestion_count, 1)

    def test_to_dict(self):
        rec = ReflectionRecord(reflection_id="r1", type="event", confidence=0.7)
        d = rec.to_dict()
        self.assertEqual(d["reflection_id"], "r1")
        self.assertEqual(d["schema_version"], REFLECTION_HISTORY_SCHEMA_VERSION)

    def test_is_valid(self):
        rec = ReflectionRecord()
        self.assertFalse(rec.is_valid())
        rec.reflection_id = "r1"
        self.assertTrue(rec.is_valid())


class TestReflectionHistory(unittest.TestCase):
    def test_default_creation(self):
        h = ReflectionHistory()
        self.assertEqual(h.capacity, DEFAULT_REFLECTION_HISTORY_CAPACITY)
        self.assertEqual(h.size, 0)
        self.assertFalse(h.is_closed)

    def test_custom_capacity(self):
        h = ReflectionHistory(capacity=10)
        self.assertEqual(h.capacity, 10)

    def test_capacity_clamping_max(self):
        h = ReflectionHistory(capacity=MAX_REFLECTION_HISTORY_CAPACITY + 100)
        self.assertEqual(h.capacity, MAX_REFLECTION_HISTORY_CAPACITY)

    def test_capacity_clamping_min(self):
        h = ReflectionHistory(capacity=-5)
        self.assertEqual(h.capacity, 0)

    def test_append_record(self):
        h = ReflectionHistory(capacity=10)
        rec = ReflectionRecord(reflection_id="r1")
        self.assertTrue(h.append(rec))
        self.assertEqual(h.size, 1)
        self.assertEqual(h.total_appended, 1)

    def test_append_invalid_record(self):
        h = ReflectionHistory(capacity=10)
        self.assertFalse(h.append("not a record"))
        self.assertEqual(h.total_dropped, 1)

    def test_append_raise_when_silent_false(self):
        h = ReflectionHistory(capacity=10)
        with self.assertRaises(ReflectionHistoryError):
            h.append("not a record", silent=False)

    def test_append_result(self):
        h = ReflectionHistory(capacity=10)
        r = _make_result()
        self.assertTrue(h.append_result(r))
        self.assertEqual(h.size, 1)
        self.assertEqual(h.last_id, "ref_1")

    def test_append_invalid_result(self):
        h = ReflectionHistory(capacity=10)
        self.assertFalse(h.append_result("not a result"))
        self.assertEqual(h.size, 0)

    def test_extend(self):
        h = ReflectionHistory(capacity=10)
        records = [ReflectionRecord(reflection_id=f"r{i}") for i in range(5)]
        self.assertEqual(h.extend(records), 5)
        self.assertEqual(h.size, 5)

    def test_extend_with_invalid(self):
        h = ReflectionHistory(capacity=10)
        records = [ReflectionRecord(reflection_id="r1"), "bad", ReflectionRecord(reflection_id="r2")]
        success = h.extend(records)
        self.assertEqual(success, 2)

    def test_fifo_eviction(self):
        h = ReflectionHistory(capacity=3)
        for i in range(5):
            h.append(ReflectionRecord(reflection_id=f"r{i}"))
        self.assertEqual(h.size, 3)
        ids = [r.reflection_id for r in h.list()]
        # r0, r1 已被淘汰
        self.assertEqual(ids, ["r2", "r3", "r4"])

    def test_capacity_zero_no_retention(self):
        h = ReflectionHistory(capacity=0)
        h.append(ReflectionRecord(reflection_id="r1"))
        self.assertEqual(h.size, 0)
        self.assertEqual(h.total_appended, 1)

    def test_get_by_id(self):
        h = ReflectionHistory(capacity=10)
        h.append(ReflectionRecord(reflection_id="r1"))
        h.append(ReflectionRecord(reflection_id="r2"))
        r = h.get("r2")
        self.assertIsNotNone(r)
        self.assertEqual(r.reflection_id, "r2")
        self.assertIsNone(h.get("notfound"))

    def test_get_empty_id(self):
        h = ReflectionHistory(capacity=10)
        self.assertIsNone(h.get(""))

    def test_list_all(self):
        h = ReflectionHistory(capacity=10)
        for i in range(3):
            h.append(ReflectionRecord(reflection_id=f"r{i}"))
        self.assertEqual(len(h.list()), 3)

    def test_list_with_limit(self):
        h = ReflectionHistory(capacity=10)
        for i in range(5):
            h.append(ReflectionRecord(reflection_id=f"r{i}"))
        self.assertEqual(len(h.list(limit=2)), 2)
        self.assertEqual(len(h.list(limit=0)), 0)

    def test_latest(self):
        h = ReflectionHistory(capacity=10)
        for i in range(5):
            h.append(ReflectionRecord(reflection_id=f"r{i}"))
        latest = h.latest(2)
        self.assertEqual(len(latest), 2)
        self.assertEqual(latest[-1].reflection_id, "r4")

    def test_latest_invalid_limit(self):
        h = ReflectionHistory(capacity=10)
        h.append(ReflectionRecord(reflection_id="r1"))
        self.assertEqual(h.latest(0), [])
        self.assertEqual(h.latest("bad"), [])  # type: ignore[arg-type]

    def test_count_by_type(self):
        h = ReflectionHistory(capacity=10)
        h.append(ReflectionRecord(reflection_id="r1", type="daily"))
        h.append(ReflectionRecord(reflection_id="r2", type="daily"))
        h.append(ReflectionRecord(reflection_id="r3", type="event"))
        self.assertEqual(h.count_by_type("daily"), 2)
        self.assertEqual(h.count_by_type("event"), 1)
        self.assertEqual(h.count_by_type("invalid"), 0)

    def test_type_counts(self):
        h = ReflectionHistory(capacity=10)
        h.append(ReflectionRecord(reflection_id="r1", type="daily"))
        h.append(ReflectionRecord(reflection_id="r2", type="event"))
        h.append(ReflectionRecord(reflection_id="r3", type="growth"))
        c = h.type_counts()
        self.assertEqual(c["daily"], 1)
        self.assertEqual(c["event"], 1)
        self.assertEqual(c["growth"], 1)

    def test_clear(self):
        h = ReflectionHistory(capacity=10)
        for i in range(3):
            h.append(ReflectionRecord(reflection_id=f"r{i}"))
        n = h.clear()
        self.assertEqual(n, 3)
        self.assertEqual(h.size, 0)

    def test_close(self):
        h = ReflectionHistory(capacity=10)
        self.assertTrue(h.close())
        self.assertTrue(h.is_closed)
        # close 后再 close: 幂等
        self.assertFalse(h.close())
        # close 后 append 被拒绝
        self.assertFalse(h.append(ReflectionRecord(reflection_id="r1")))
        self.assertEqual(h.total_dropped, 1)

    def test_describe(self):
        h = ReflectionHistory(capacity=10)
        h.append(ReflectionRecord(reflection_id="r1", type="event"))
        d = h.describe()
        self.assertEqual(d["size"], 1)
        self.assertEqual(d["last_id"], "r1")
        self.assertEqual(d["type_counts"]["event"], 1)
        self.assertEqual(d["schema_version"], REFLECTION_HISTORY_SCHEMA_VERSION) \
            if "schema_version" in d else True  # 该字段不要求

    def test_len(self):
        h = ReflectionHistory(capacity=10)
        self.assertEqual(len(h), 0)
        h.append(ReflectionRecord(reflection_id="r1"))
        self.assertEqual(len(h), 1)


class TestReflectionHistoryThreadSafety(unittest.TestCase):
    def test_concurrent_append(self):
        h = ReflectionHistory(capacity=1000)
        threads = []
        for i in range(10):
            t = threading.Thread(
                target=lambda idx=i: [h.append(ReflectionRecord(reflection_id=f"r-{idx}-{j}")) for j in range(50)]
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(h.size, 500)
        self.assertEqual(h.total_appended, 500)

    def test_concurrent_read_write(self):
        h = ReflectionHistory(capacity=100)
        stop = threading.Event()
        errors = []

        def writer():
            try:
                for i in range(200):
                    h.append(ReflectionRecord(reflection_id=f"r{i}"))
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(100):
                    h.list()
                    h.latest(5)
                    h.count_by_type("daily")
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(3):
            threads.append(threading.Thread(target=writer))
            threads.append(threading.Thread(target=reader))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


class TestFactory(unittest.TestCase):
    def test_build_default(self):
        h = build_default_reflection_history()
        self.assertIsInstance(h, ReflectionHistory)
        self.assertEqual(h.capacity, DEFAULT_REFLECTION_HISTORY_CAPACITY)


if __name__ == "__main__":
    unittest.main()
