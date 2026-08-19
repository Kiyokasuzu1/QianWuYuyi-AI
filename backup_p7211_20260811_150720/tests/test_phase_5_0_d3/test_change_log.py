# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_change_log.py

Phase 5.0-D3-A: Self Model System —— ChangeLog 单元测试
"""
import unittest
import threading
from typing import Any, Dict, List

from src.runtime.self_model.change_log import (
    ChangeLog,
    ChangeLogError,
    DEFAULT_CHANGE_LOG_CAPACITY,
    MAX_CHANGE_LOG_CAPACITY,
    build_default_change_log,
)
from src.runtime.self_model.self_state import (
    ChangeRecord,
    IdentityView,
    TraitState,
)


def _make_record(
    *,
    change_id: str = "",
    change_kind: str = "trait_update",
    change_source: str = "lifecycle_task",
    target: str = "trait",
    target_id: str = "t1",
    target_name: str = "t1",
    evidence: List[str] = None,
    timestamp: float = 1000.0,
) -> ChangeRecord:
    """构造一个有效的 ChangeRecord(便于测试)。"""
    return ChangeRecord(
        change_kind=change_kind,
        change_source=change_source,
        target=target,
        target_id=target_id,
        target_name=target_name,
        before={"x": 1},
        after={"x": 2},
        evidence_event_ids=list(evidence or ["e1"]),
        timestamp=timestamp,
        reason="test",
        metadata={},
    )


# ============================================================
# ChangeLog 基础
# ============================================================
class TestChangeLogBasics(unittest.TestCase):
    def test_default_construct(self):
        log = ChangeLog()
        self.assertEqual(log.name, "self_model_change_log")
        self.assertEqual(log.capacity, DEFAULT_CHANGE_LOG_CAPACITY)
        self.assertEqual(log.size, 0)
        self.assertFalse(log.is_closed)
        self.assertTrue(log.is_empty())

    def test_custom_name(self):
        log = ChangeLog(name="custom")
        self.assertEqual(log.name, "custom")

    def test_capacity_clipped(self):
        log = ChangeLog(capacity=-1)
        self.assertEqual(log.capacity, 0)
        log2 = ChangeLog(capacity=MAX_CHANGE_LOG_CAPACITY + 100)
        self.assertEqual(log2.capacity, MAX_CHANGE_LOG_CAPACITY)

    def test_build_default(self):
        log = build_default_change_log()
        self.assertIsInstance(log, ChangeLog)


# ============================================================
# append / extend
# ============================================================
class TestChangeLogAppend(unittest.TestCase):
    def test_append_valid(self):
        log = ChangeLog()
        r = _make_record()
        self.assertTrue(log.append(r))
        self.assertEqual(log.size, 1)
        self.assertEqual(log.total_appended, 1)

    def test_append_invalid_type(self):
        log = ChangeLog()
        self.assertFalse(log.append("not_a_record"))  # silent
        self.assertEqual(log.total_dropped, 1)
        self.assertNotEqual(log.last_error, "")

    def test_append_invalid_type_raises(self):
        log = ChangeLog()
        with self.assertRaises(ChangeLogError):
            log.append("not_a_record", silent=False)

    def test_append_invalid_record(self):
        log = ChangeLog()
        # 缺 target_id → is_valid() 为 False
        r = ChangeRecord(target="trait", target_id="", target_name="x")
        self.assertFalse(log.append(r))
        self.assertEqual(log.total_dropped, 1)

    def test_append_after_close_rejected(self):
        log = ChangeLog()
        log.close()
        r = _make_record()
        self.assertFalse(log.append(r))

    def test_extend_batch(self):
        log = ChangeLog()
        records = [_make_record(target_id=f"t{i}") for i in range(5)]
        n = log.extend(records)
        self.assertEqual(n, 5)
        self.assertEqual(log.size, 5)

    def test_extend_with_invalid(self):
        log = ChangeLog()
        records = [_make_record(), "invalid", _make_record(target_id="t2")]
        n = log.extend(records)
        self.assertEqual(n, 2)
        self.assertEqual(log.total_dropped, 1)

    def test_extend_none(self):
        log = ChangeLog()
        self.assertEqual(log.extend(None), 0)


# ============================================================
# capacity / eviction
# ============================================================
class TestChangeLogCapacity(unittest.TestCase):
    def test_fifo_eviction(self):
        log = ChangeLog(capacity=3)
        for i in range(5):
            log.append(_make_record(target_id=f"t{i}", timestamp=float(i)))
        self.assertEqual(log.size, 3)
        self.assertGreater(log.total_evicted, 0)
        # 最早的两条应被淘汰
        recs = log.records()
        ids = [r.target_id for r in recs]
        self.assertNotIn("t0", ids)
        self.assertNotIn("t1", ids)
        self.assertIn("t4", ids)

    def test_unlimited_capacity(self):
        log = ChangeLog(capacity=0)
        for i in range(10):
            log.append(_make_record(target_id=f"t{i}"))
        self.assertEqual(log.size, 10)


# ============================================================
# 查询
# ============================================================
class TestChangeLogQuery(unittest.TestCase):
    def setUp(self):
        self.log = ChangeLog()
        self.log.append(_make_record(change_kind="trait_update", target="trait", target_id="trt_1", evidence=["e1"], timestamp=100.0))
        self.log.append(_make_record(change_kind="interest_update", target="interest", target_id="int_1", evidence=["e2"], timestamp=200.0))
        self.log.append(_make_record(change_kind="capability_update", target="capability", target_id="cap_1", evidence=["e1", "e3"], timestamp=300.0))
        self.log.append(_make_record(change_kind="reset", target="self_state", target_id="ss_1", evidence=["e4"], timestamp=400.0))

    def test_records(self):
        recs = self.log.records()
        self.assertEqual(len(recs), 4)

    def test_latest_order(self):
        latest = self.log.latest(limit=2)
        self.assertEqual(len(latest), 2)
        # 最新在前
        self.assertEqual(latest[0].timestamp, 400.0)
        self.assertEqual(latest[1].timestamp, 300.0)

    def test_latest_limit_zero(self):
        self.assertEqual(self.log.latest(0), [])

    def test_by_target(self):
        recs = self.log.by_target("trait")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].target_id, "trt_1")

    def test_by_target_id(self):
        recs = self.log.by_target_id("interest", "int_1")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].target_name, "t1")  # default name in fixture

    def test_by_kind(self):
        recs = self.log.by_kind("trait_update")
        self.assertEqual(len(recs), 1)
        recs2 = self.log.by_kind("not_a_kind")
        self.assertEqual(recs2, [])

    def test_by_source(self):
        recs = self.log.by_source("lifecycle_task")
        self.assertEqual(len(recs), 4)
        recs2 = self.log.by_source("not_a_source")
        self.assertEqual(recs2, [])

    def test_by_evidence(self):
        recs = self.log.by_evidence("e1")
        self.assertEqual(len(recs), 2)
        recs2 = self.log.by_evidence("not_found")
        self.assertEqual(recs2, [])

    def test_by_evidence_empty(self):
        self.assertEqual(self.log.by_evidence(""), [])

    def test_contains_evidence(self):
        self.assertTrue(self.log.contains_evidence("e1"))
        self.assertFalse(self.log.contains_evidence("missing"))
        self.assertFalse(self.log.contains_evidence(""))

    def test_since(self):
        recs = self.log.since(300.0)
        self.assertEqual(len(recs), 2)

    def test_between(self):
        recs = self.log.between(200.0, 300.0)
        self.assertEqual(len(recs), 2)

    def test_query_combine(self):
        recs = self.log.query(target="trait", kind="trait_update")
        self.assertEqual(len(recs), 1)
        recs2 = self.log.query(evidence="e1", target="capability")
        self.assertEqual(len(recs2), 1)

    def test_query_limit(self):
        recs = self.log.query(limit=2)
        self.assertEqual(len(recs), 2)
        # 默认取最后 2 条
        self.assertEqual(recs[0].target_id, "cap_1")
        self.assertEqual(recs[1].target_id, "ss_1")

    def test_count_by_target(self):
        self.assertEqual(self.log.count_by_target("trait"), 1)
        self.assertEqual(self.log.count_by_target("missing"), 0)

    def test_count_by_kind(self):
        self.assertEqual(self.log.count_by_kind("trait_update"), 1)
        self.assertEqual(self.log.count_by_kind("bogus"), 0)

    def test_count_by_evidence(self):
        self.assertEqual(self.log.count_by_evidence("e1"), 2)


# ============================================================
# 维护
# ============================================================
class TestChangeLogMaintenance(unittest.TestCase):
    def test_clear(self):
        log = ChangeLog()
        log.append(_make_record())
        log.append(_make_record())
        n = log.clear()
        self.assertEqual(n, 2)
        self.assertEqual(log.size, 0)

    def test_close(self):
        log = ChangeLog()
        log.close()
        self.assertTrue(log.is_closed)

    def test_describe(self):
        log = ChangeLog(name="d")
        log.append(_make_record())
        d = log.describe()
        self.assertEqual(d["name"], "d")
        self.assertEqual(d["size"], 1)
        self.assertIn("schema_version", d)

    def test_to_dict(self):
        log = ChangeLog()
        log.append(_make_record())
        d = log.to_dict()
        self.assertIn("records", d)
        self.assertEqual(len(d["records"]), 1)

    def test_last_appended(self):
        log = ChangeLog()
        r = _make_record()
        log.append(r)
        self.assertEqual(log.last_appended_id, r.change_id)
        self.assertEqual(log.last_appended_at, r.timestamp)


# ============================================================
# 线程安全
# ============================================================
class TestChangeLogThreadSafety(unittest.TestCase):
    def test_concurrent_append(self):
        log = ChangeLog(capacity=0)  # unlimited
        n_threads = 8
        n_per_thread = 50

        def worker(tid: int):
            for i in range(n_per_thread):
                log.append(_make_record(target_id=f"t{tid}_{i}", timestamp=float(tid * 1000 + i)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(log.size, n_threads * n_per_thread)
        self.assertEqual(log.total_appended, n_threads * n_per_thread)


if __name__ == "__main__":
    unittest.main()
