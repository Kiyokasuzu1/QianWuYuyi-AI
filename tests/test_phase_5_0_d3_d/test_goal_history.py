# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_history.py

Phase 5.0-D3-D: GoalHistory 单元测试。

覆盖:
- 创建 (capacity, 默认)
- record_change / record_snapshot
- FIFO 容量限制
- 按 goal_id 查询
- replay
- 统计
- 线程安全
"""
import threading
import unittest
from src.runtime.goal.goal_history import (
    DEFAULT_HISTORY_CAPACITY,
    GOAL_HISTORY_SCHEMA_VERSION,
    GoalHistory,
    build_default_goal_history,
)
from src.runtime.goal.goal_record import (
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_UPDATED,
    ChangeRecord,
    GoalRecord,
    build_change_record,
    build_record_from_goal,
)
from src.runtime.goal.goal_state import (
    GOAL_STATUS_CANDIDATE,
    GoalState,
    build_candidate_goal,
)


class TestGoalHistoryBasic(unittest.TestCase):
    def test_default(self):
        h = build_default_goal_history()
        self.assertEqual(h.capacity, DEFAULT_HISTORY_CAPACITY)
        self.assertEqual(h.size, 0)
        self.assertEqual(h.snapshot_size, 0)

    def test_custom_capacity(self):
        h = GoalHistory(capacity=10)
        self.assertEqual(h.capacity, 10)

    def test_invalid_capacity(self):
        h = GoalHistory(capacity="bogus")
        self.assertEqual(h.capacity, DEFAULT_HISTORY_CAPACITY)

    def test_record_change(self):
        h = GoalHistory()
        c = h.record_change(
            goal_id="g1",
            change_type=GOAL_CHANGE_CREATED,
            new_state={"title": "t"},
            source_event_ids=["e1"],
            source_desire_ids=["d1"],
            reason="created",
            now=10.0,
        )
        self.assertEqual(c.goal_id, "g1")
        self.assertEqual(c.change_type, GOAL_CHANGE_CREATED)
        self.assertEqual(h.size, 1)
        self.assertEqual(h.added_change_count, 1)

    def test_record_change_invalid_type(self):
        h = GoalHistory()
        c = h.record_change(
            goal_id="g1",
            change_type="bogus",
        )
        self.assertIsNone(c)
        self.assertEqual(h.size, 0)

    def test_record_snapshot(self):
        h = GoalHistory()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        r = h.record_snapshot(g, now=10.0)
        self.assertIsNotNone(r)
        self.assertEqual(r.goal_id, g.goal_id)
        self.assertEqual(h.snapshot_size, 1)
        self.assertEqual(h.added_snapshot_count, 1)


class TestGoalHistoryFIFO(unittest.TestCase):
    def test_change_overflow(self):
        h = GoalHistory(capacity=3)
        for i in range(5):
            h.record_change(
                goal_id=f"g{i}",
                change_type=GOAL_CHANGE_CREATED,
                now=float(i),
            )
        self.assertEqual(h.size, 3)
        self.assertEqual(h.added_change_count, 5)
        self.assertEqual(h.overflow_count, 2)

    def test_snapshot_overflow(self):
        h = GoalHistory(capacity=3)
        for i in range(5):
            g = build_candidate_goal(title=f"t{i}", source_event_ids=[f"e{i}"])
            h.record_snapshot(g, now=float(i))
        self.assertEqual(h.snapshot_size, 3)
        self.assertEqual(h.added_snapshot_count, 5)
        self.assertEqual(h.overflow_count, 2)

    def test_order_preserved(self):
        h = GoalHistory(capacity=5)
        for i in range(3):
            h.record_change(
                goal_id=f"g{i}",
                change_type=GOAL_CHANGE_CREATED,
                now=float(i),
            )
        history = h.history()
        self.assertEqual(history[0].goal_id, "g0")
        self.assertEqual(history[1].goal_id, "g1")
        self.assertEqual(history[2].goal_id, "g2")


class TestGoalHistoryQuery(unittest.TestCase):
    def test_history_for_goal(self):
        h = GoalHistory()
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g2", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_ACTIVATED)
        h.record_change(goal_id="g3", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_COMPLETED)
        g1_changes = h.history_for_goal("g1")
        self.assertEqual(len(g1_changes), 3)
        for c in g1_changes:
            self.assertEqual(c.goal_id, "g1")

    def test_history_for_missing(self):
        h = GoalHistory()
        self.assertEqual(len(h.history_for_goal("missing")), 0)
        self.assertEqual(len(h.history_for_goal("")), 0)

    def test_replay(self):
        h = GoalHistory()
        for i in range(3):
            h.record_change(goal_id=f"g{i}", change_type=GOAL_CHANGE_CREATED, now=float(i))
        replay = h.replay()
        self.assertEqual(len(replay), 3)

    def test_replay_reverse(self):
        h = GoalHistory()
        for i in range(3):
            h.record_change(goal_id=f"g{i}", change_type=GOAL_CHANGE_CREATED, now=float(i))
        replay = h.replay(reverse=True)
        self.assertEqual(replay[0].goal_id, "g2")
        self.assertEqual(replay[2].goal_id, "g0")

    def test_replay_filter(self):
        h = GoalHistory()
        for i in range(3):
            h.record_change(goal_id=f"g{i}", change_type=GOAL_CHANGE_CREATED, now=float(i))
        replay = h.replay(goal_id="g1")
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0].goal_id, "g1")


class TestGoalHistoryStatistics(unittest.TestCase):
    def test_count_by_type(self):
        h = GoalHistory()
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_ACTIVATED)
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_ACTIVATED)
        h.record_change(goal_id="g2", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g2", change_type=GOAL_CHANGE_COMPLETED)
        self.assertEqual(h.count_by_type(GOAL_CHANGE_CREATED), 2)
        self.assertEqual(h.count_by_type(GOAL_CHANGE_ACTIVATED), 2)
        self.assertEqual(h.count_by_type(GOAL_CHANGE_COMPLETED), 1)

    def test_count_for_goal(self):
        h = GoalHistory()
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_ACTIVATED)
        h.record_change(goal_id="g2", change_type=GOAL_CHANGE_CREATED)
        self.assertEqual(h.count_for_goal("g1"), 2)
        self.assertEqual(h.count_for_goal("g2"), 1)
        self.assertEqual(h.count_for_goal(""), 0)

    def test_clear(self):
        h = GoalHistory()
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        h.record_change(goal_id="g2", change_type=GOAL_CHANGE_CREATED)
        n = h.clear()
        self.assertEqual(n, 2)
        self.assertEqual(h.size, 0)
        self.assertEqual(h.snapshot_size, 0)

    def test_describe(self):
        h = GoalHistory()
        h.record_change(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        desc = h.describe()
        self.assertEqual(desc["size"], 1)
        self.assertEqual(desc["added_change_count"], 1)
        self.assertIn("type_counts", desc)

    def test_repr(self):
        h = GoalHistory()
        r = repr(h)
        self.assertIn("GoalHistory", r)


class TestGoalHistoryConcurrency(unittest.TestCase):
    def test_concurrent_record_change(self):
        h = GoalHistory(capacity=10000)
        results = []
        lock = threading.Lock()

        def worker(i: int):
            c = h.record_change(
                goal_id=f"g{i}",
                change_type=GOAL_CHANGE_CREATED,
                now=float(i),
            )
            if c is not None:
                with lock:
                    results.append(c.change_id)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(results), 100)


if __name__ == "__main__":
    unittest.main()
