# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_state.py

Phase 5.0-D3-D: GoalState 单元测试。

覆盖:
- 创建 (default, factory, from_dict)
- 状态变化 (activate, pause, complete, abandon, update_priority)
- 序列化 (to_dict, from_dict, summary)
- 校验 (has_evidence, is_valid, is_terminal)
- 状态机合法性
- 异常容错
"""
import unittest
from src.runtime.goal.goal_state import (
    ALL_GOAL_STATUSES,
    ALL_GOAL_TYPES,
    GOAL_STATE_SCHEMA_VERSION,
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_PAUSED,
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    VALID_GOAL_TRANSITIONS,
    GoalState,
    build_candidate_goal,
    is_valid_goal_transition,
)


class TestGoalStateBasic(unittest.TestCase):
    def test_default_construction(self):
        g = GoalState()
        self.assertTrue(g.goal_id.startswith("goal_"))
        self.assertEqual(g.status, GOAL_STATUS_CANDIDATE)
        self.assertEqual(g.goal_type, GOAL_TYPE_PERSONAL_GROWTH)
        self.assertEqual(g.version, GOAL_STATE_SCHEMA_VERSION)
        self.assertFalse(g.has_evidence())

    def test_factory_candidate(self):
        g = build_candidate_goal(
            title="探索 AI 艺术",
            source_event_ids=["ev1", "ev2"],
            source_desire_ids=["des1"],
            reason="测试",
        )
        self.assertEqual(g.status, GOAL_STATUS_CANDIDATE)
        self.assertEqual(g.title, "探索 AI 艺术")
        self.assertIn("ev1", g.source_event_ids)
        self.assertIn("des1", g.source_desire_ids)
        self.assertTrue(g.has_evidence())
        self.assertTrue(g.is_valid())
        self.assertTrue(g.is_candidate())
        self.assertFalse(g.is_terminal())

    def test_invalid_goal_no_title(self):
        g = build_candidate_goal(
            title="",
            source_event_ids=["ev1"],
        )
        self.assertFalse(g.is_valid())

    def test_invalid_goal_no_evidence(self):
        g = GoalState(title="some")
        self.assertFalse(g.has_evidence())
        self.assertFalse(g.is_valid())

    def test_invalid_goal_type_fallback(self):
        g = GoalState(title="x", goal_type="bogus", source_event_ids=["a"])
        self.assertEqual(g.goal_type, GOAL_TYPE_PERSONAL_GROWTH)

    def test_invalid_status_fallback(self):
        g = GoalState(title="x", status="bogus", source_event_ids=["a"])
        self.assertEqual(g.status, GOAL_STATUS_CANDIDATE)


class TestGoalStateTransitions(unittest.TestCase):
    def _make(self) -> GoalState:
        return build_candidate_goal(
            title="t",
            source_event_ids=["ev1"],
            source_desire_ids=["des1"],
        )

    def test_activate_from_candidate(self):
        g = self._make()
        self.assertTrue(g.activate(now=1.0))
        self.assertEqual(g.status, GOAL_STATUS_ACTIVE)
        self.assertEqual(g.updated_at, 1.0)
        self.assertTrue(g.is_active())

    def test_activate_idempotent(self):
        g = self._make()
        g.activate(now=1.0)
        self.assertTrue(g.activate(now=2.0))
        self.assertEqual(g.status, GOAL_STATUS_ACTIVE)

    def test_activate_from_completed_fails(self):
        g = self._make()
        g.activate(now=1.0)
        g.complete(now=2.0)
        self.assertFalse(g.activate(now=3.0))
        self.assertEqual(g.status, GOAL_STATUS_COMPLETED)

    def test_activate_from_abandoned_fails(self):
        g = self._make()
        g.abandon(now=1.0)
        self.assertFalse(g.activate(now=2.0))

    def test_pause_from_active(self):
        g = self._make()
        g.activate(now=1.0)
        self.assertTrue(g.pause(now=2.0))
        self.assertEqual(g.status, GOAL_STATUS_PAUSED)

    def test_pause_from_paused_idempotent(self):
        g = self._make()
        g.pause(now=1.0)
        self.assertTrue(g.pause(now=2.0))

    def test_complete_from_active(self):
        g = self._make()
        g.activate(now=1.0)
        self.assertTrue(g.complete(now=2.0))
        self.assertEqual(g.status, GOAL_STATUS_COMPLETED)
        self.assertEqual(g.completed_at, 2.0)
        self.assertTrue(g.is_completed())
        self.assertTrue(g.is_terminal())

    def test_complete_from_paused(self):
        g = self._make()
        g.pause(now=1.0)
        self.assertTrue(g.complete(now=2.0))
        self.assertEqual(g.status, GOAL_STATUS_COMPLETED)

    def test_complete_from_abandoned_fails(self):
        g = self._make()
        g.abandon(now=1.0)
        self.assertFalse(g.complete(now=2.0))

    def test_complete_idempotent(self):
        g = self._make()
        g.activate(now=1.0)
        g.complete(now=2.0)
        self.assertTrue(g.complete(now=3.0))

    def test_abandon_from_candidate(self):
        g = self._make()
        self.assertTrue(g.abandon(now=1.0))
        self.assertEqual(g.status, GOAL_STATUS_ABANDONED)
        self.assertTrue(g.is_abandoned())
        self.assertTrue(g.is_terminal())

    def test_abandon_idempotent(self):
        g = self._make()
        g.abandon(now=1.0)
        self.assertTrue(g.abandon(now=2.0))

    def test_abandon_from_completed_fails(self):
        g = self._make()
        g.activate(now=1.0)
        g.complete(now=2.0)
        self.assertFalse(g.abandon(now=3.0))

    def test_valid_transition_table(self):
        self.assertTrue(is_valid_goal_transition(GOAL_STATUS_CANDIDATE, GOAL_STATUS_ACTIVE))
        self.assertTrue(is_valid_goal_transition(GOAL_STATUS_CANDIDATE, GOAL_STATUS_PAUSED))
        self.assertFalse(is_valid_goal_transition(GOAL_STATUS_CANDIDATE, GOAL_STATUS_COMPLETED))
        self.assertFalse(is_valid_goal_transition(GOAL_STATUS_COMPLETED, GOAL_STATUS_ACTIVE))
        self.assertFalse(is_valid_goal_transition("bogus", GOAL_STATUS_ACTIVE))

    def test_all_statuses_covered(self):
        for s in (GOAL_STATUS_CANDIDATE, GOAL_STATUS_ACTIVE, GOAL_STATUS_PAUSED,
                  GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED):
            self.assertIn(s, ALL_GOAL_STATUSES)

    def test_all_types_covered(self):
        for t in (GOAL_TYPE_PERSONAL_GROWTH, GOAL_TYPE_LEARNING, GOAL_TYPE_CREATIVE,
                  GOAL_TYPE_RELATIONSHIP, GOAL_TYPE_EXPLORATION):
            self.assertIn(t, ALL_GOAL_TYPES)


class TestGoalStatePriority(unittest.TestCase):
    def _make(self) -> GoalState:
        return build_candidate_goal(
            title="t",
            source_event_ids=["ev1"],
        )

    def test_update_priority_normal(self):
        g = self._make()
        g.update_priority(0.7, now=1.0)
        self.assertAlmostEqual(g.priority, 0.7)

    def test_update_priority_clip_high(self):
        g = self._make()
        g.update_priority(2.0, now=1.0)
        self.assertEqual(g.priority, 1.0)

    def test_update_priority_clip_low(self):
        g = self._make()
        g.update_priority(-1.0, now=1.0)
        self.assertEqual(g.priority, 0.0)

    def test_update_priority_invalid_value(self):
        g = self._make()
        g.update_priority("bogus", now=1.0)
        self.assertEqual(g.priority, 0.5)


class TestGoalStateSourceIds(unittest.TestCase):
    def test_add_source_event_ids(self):
        g = GoalState(title="x", source_event_ids=["a"])
        g.add_source_event_ids(["b", "a", "c"])
        self.assertEqual(g.source_event_ids, ["a", "b", "c"])

    def test_add_source_desire_ids(self):
        g = GoalState(title="x", source_event_ids=["a"], source_desire_ids=["x"])
        g.add_source_desire_ids(["y", "x", "z"])
        self.assertEqual(g.source_desire_ids, ["x", "y", "z"])

    def test_add_empty_list(self):
        g = GoalState(title="x", source_event_ids=["a"])
        g.add_source_event_ids([])
        self.assertEqual(g.source_event_ids, ["a"])


class TestGoalStateSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        g = build_candidate_goal(
            title="t",
            description="d",
            goal_type=GOAL_TYPE_LEARNING,
            source_event_ids=["e1"],
            source_desire_ids=["des1"],
            importance=0.6,
            confidence=0.7,
            now=10.0,
        )
        d = g.to_dict()
        g2 = GoalState.from_dict(d)
        self.assertEqual(g2.goal_id, g.goal_id)
        self.assertEqual(g2.title, g.title)
        self.assertEqual(g2.goal_type, g.goal_type)
        self.assertEqual(g2.source_event_ids, g.source_event_ids)
        self.assertEqual(g2.source_desire_ids, g.source_desire_ids)
        self.assertAlmostEqual(g2.importance, g.importance)
        self.assertAlmostEqual(g2.confidence, g.confidence)
        self.assertEqual(g2.created_at, g.created_at)

    def test_from_dict_invalid(self):
        g = GoalState.from_dict("not a dict")
        self.assertEqual(g.title, "invalid")

    def test_from_dict_partial(self):
        g = GoalState.from_dict({"title": "p", "source_event_ids": ["a"]})
        self.assertEqual(g.title, "p")
        self.assertIn("a", g.source_event_ids)

    def test_summary(self):
        g = build_candidate_goal(
            title="t",
            source_event_ids=["e1", "e2"],
            source_desire_ids=["d1"],
        )
        s = g.summary()
        self.assertEqual(s["title"], "t")
        self.assertEqual(s["source_event_count"], 2)
        self.assertEqual(s["source_desire_count"], 1)
        self.assertTrue(s["has_evidence"])

    def test_repr(self):
        g = build_candidate_goal(title="t", source_event_ids=["a"])
        r = repr(g)
        self.assertIn("GoalState", r)
        self.assertIn("t", r)


class TestGoalStateFields(unittest.TestCase):
    def test_title_clipping(self):
        g = GoalState(title="x" * 5000, source_event_ids=["a"])
        self.assertLessEqual(len(g.title), 128)

    def test_description_clipping(self):
        g = GoalState(title="x", description="d" * 5000, source_event_ids=["a"])
        self.assertLessEqual(len(g.description), 1024)

    def test_reason_clipping(self):
        g = GoalState(title="x", reason="r" * 5000, source_event_ids=["a"])
        self.assertLessEqual(len(g.reason), 512)

    def test_priority_clipping(self):
        g = GoalState(title="x", priority=2.0, source_event_ids=["a"])
        self.assertEqual(g.priority, 1.0)
        g2 = GoalState(title="x", priority=-1.0, source_event_ids=["a"])
        self.assertEqual(g2.priority, 0.0)

    def test_metadata_size_limit(self):
        meta = {f"k{i}": i for i in range(50)}
        g = GoalState(title="x", source_event_ids=["a"], metadata=meta)
        self.assertLessEqual(len(g.metadata), 16)


if __name__ == "__main__":
    unittest.main()
