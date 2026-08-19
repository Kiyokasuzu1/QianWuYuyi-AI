# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_record.py

Phase 5.0-D3-D: GoalRecord / ChangeRecord 单元测试。

覆盖:
- GoalRecord: 创建, to_dict/from_dict, 序列化
- ChangeRecord: 创建, 状态变化类型校验, 序列化
- build_record_from_goal 工厂
- build_change_record 工厂
"""
import unittest
from src.runtime.goal.goal_record import (
    ALL_GOAL_CHANGE_TYPES,
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_PROMOTED,
    GOAL_CHANGE_UPDATED,
    GOAL_RECORD_SCHEMA_VERSION,
    ChangeRecord,
    GoalRecord,
    build_change_record,
    build_record_from_goal,
)
from src.runtime.goal.goal_state import (
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GoalState,
    build_candidate_goal,
)


class TestGoalRecord(unittest.TestCase):
    def test_default_construction(self):
        r = GoalRecord()
        self.assertTrue(r.record_id.startswith("rec_"))
        self.assertEqual(r.version, GOAL_RECORD_SCHEMA_VERSION)
        self.assertEqual(r.goal_id, "")

    def test_to_dict_roundtrip(self):
        r = GoalRecord(
            goal_id="g1",
            title="t",
            description="d",
            goal_type="learning",
            status="active",
            priority=0.6,
            importance=0.7,
            confidence=0.5,
            source_event_ids=["e1"],
            source_desire_ids=["d1"],
            reason="r",
            snapshot_at=100.0,
        )
        d = r.to_dict()
        r2 = GoalRecord.from_dict(d)
        self.assertEqual(r2.goal_id, r.goal_id)
        self.assertEqual(r2.title, r.title)
        self.assertEqual(r2.priority, r.priority)
        self.assertEqual(r2.snapshot_at, r.snapshot_at)

    def test_from_dict_invalid(self):
        r = GoalRecord.from_dict("not a dict")
        self.assertEqual(r.goal_id, "invalid")

    def test_from_dict_partial(self):
        r = GoalRecord.from_dict({"goal_id": "g"})
        self.assertEqual(r.goal_id, "g")

    def test_priority_clipping(self):
        r = GoalRecord(priority=2.0)
        self.assertEqual(r.priority, 1.0)
        r2 = GoalRecord(priority=-1.0)
        self.assertEqual(r2.priority, 0.0)

    def test_invalid_priority(self):
        r = GoalRecord(priority="bogus")
        self.assertEqual(r.priority, 0.5)

    def test_reason_clipping(self):
        r = GoalRecord(reason="r" * 1000)
        self.assertLessEqual(len(r.reason), 512)

    def test_repr(self):
        r = GoalRecord(goal_id="g1")
        rp = repr(r)
        self.assertIn("GoalRecord", rp)
        self.assertIn("g1", rp)

    def test_build_record_from_goal(self):
        g = build_candidate_goal(
            title="t",
            description="d",
            goal_type="learning",
            source_event_ids=["e1"],
            source_desire_ids=["des1"],
            importance=0.6,
            confidence=0.7,
            now=10.0,
        )
        r = build_record_from_goal(g, now=20.0)
        self.assertEqual(r.goal_id, g.goal_id)
        self.assertEqual(r.title, "t")
        self.assertEqual(r.snapshot_at, 20.0)
        self.assertEqual(r.goal_type, "learning")
        self.assertEqual(r.status, GOAL_STATUS_CANDIDATE)


class TestChangeRecord(unittest.TestCase):
    def test_default_construction(self):
        c = ChangeRecord()
        self.assertTrue(c.change_id.startswith("chg_"))
        self.assertEqual(c.change_type, GOAL_CHANGE_CREATED)
        self.assertEqual(c.version, GOAL_RECORD_SCHEMA_VERSION)

    def test_invalid_change_type_fallback(self):
        c = ChangeRecord(change_type="bogus")
        self.assertEqual(c.change_type, GOAL_CHANGE_CREATED)

    def test_state_dict_validity(self):
        c = ChangeRecord(
            goal_id="g1",
            change_type=GOAL_CHANGE_ACTIVATED,
            old_state={"status": "candidate"},
            new_state={"status": "active"},
        )
        self.assertEqual(c.old_state, {"status": "candidate"})
        self.assertEqual(c.new_state, {"status": "active"})

    def test_invalid_state(self):
        c = ChangeRecord(
            goal_id="g1",
            change_type=GOAL_CHANGE_CREATED,
            old_state="not a dict",
        )
        # 不应该是 dict(应该被处理掉)
        self.assertFalse(isinstance(c.old_state, str) and c.old_state == "not a dict")

    def test_to_dict_roundtrip(self):
        c = ChangeRecord(
            goal_id="g1",
            change_type=GOAL_CHANGE_UPDATED,
            old_state={"x": 1},
            new_state={"x": 2},
            source_event_ids=["e1"],
            source_desire_ids=["d1"],
            reason="r",
            timestamp=100.0,
        )
        d = c.to_dict()
        c2 = ChangeRecord.from_dict(d)
        self.assertEqual(c2.goal_id, "g1")
        self.assertEqual(c2.change_type, GOAL_CHANGE_UPDATED)
        self.assertEqual(c2.old_state, {"x": 1})
        self.assertEqual(c2.new_state, {"x": 2})

    def test_from_dict_invalid(self):
        c = ChangeRecord.from_dict("not a dict")
        self.assertEqual(c.goal_id, "invalid")

    def test_is_creation(self):
        c = ChangeRecord(change_type=GOAL_CHANGE_CREATED)
        self.assertTrue(c.is_creation())

    def test_is_completion(self):
        c = ChangeRecord(change_type=GOAL_CHANGE_COMPLETED)
        self.assertTrue(c.is_completion())

    def test_is_status_change(self):
        for ct in (GOAL_CHANGE_ACTIVATED, GOAL_CHANGE_PAUSED, GOAL_CHANGE_COMPLETED, GOAL_CHANGE_ABANDONED):
            c = ChangeRecord(change_type=ct)
            self.assertTrue(c.is_status_change())
        c2 = ChangeRecord(change_type=GOAL_CHANGE_CREATED)
        self.assertFalse(c2.is_status_change())

    def test_repr(self):
        c = ChangeRecord(goal_id="g1", change_type=GOAL_CHANGE_CREATED)
        rp = repr(c)
        self.assertIn("ChangeRecord", rp)
        self.assertIn("g1", rp)

    def test_all_change_types_known(self):
        for t in (GOAL_CHANGE_CREATED, GOAL_CHANGE_UPDATED, GOAL_CHANGE_ACTIVATED,
                  GOAL_CHANGE_PAUSED, GOAL_CHANGE_COMPLETED, GOAL_CHANGE_ABANDONED,
                  GOAL_CHANGE_PROMOTED):
            self.assertIn(t, ALL_GOAL_CHANGE_TYPES)

    def test_build_change_record(self):
        c = build_change_record(
            goal_id="g1",
            change_type=GOAL_CHANGE_UPDATED,
            old_state={"x": 1},
            new_state={"x": 2},
            source_event_ids=["e1"],
            source_desire_ids=["d1"],
            reason="r",
            now=100.0,
        )
        self.assertEqual(c.goal_id, "g1")
        self.assertEqual(c.change_type, GOAL_CHANGE_UPDATED)
        self.assertEqual(c.timestamp, 100.0)
        self.assertEqual(c.reason, "r")
        self.assertIn("e1", c.source_event_ids)
        self.assertIn("d1", c.source_desire_ids)

    def test_reason_clipping(self):
        c = ChangeRecord(reason="r" * 1000)
        self.assertLessEqual(len(c.reason), 512)

    def test_timestamp_invalid(self):
        c = ChangeRecord(timestamp="bogus")
        self.assertEqual(c.timestamp, 0.0)


if __name__ == "__main__":
    unittest.main()
