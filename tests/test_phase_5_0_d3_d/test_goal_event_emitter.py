# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_event_emitter.py

Phase 5.0-D3-D: GoalEventEmitter 单元测试。

覆盖:
- emit_desire / emit_desires
- emit_goal / emit_goals (created/updated lifecycle)
- emit_plan / emit_plans
- 错误处理 (非 Desire/Goal/Plan, 无 evidence)
- 统计
- 描述
"""
import unittest
from src.runtime.goal.adapter.goal_event_emitter import (
    DEFAULT_EMITTER_SOURCE,
    DEFAULT_GOAL_EMITTER_OWNER,
    GOAL_EVENT_EMITTER_SCHEMA_VERSION,
    GoalEventEmitter,
    build_default_goal_event_emitter,
)
from src.runtime.goal.desire import (
    DESIRE_TREND_RISING,
    build_desire,
)
from src.runtime.goal.goal_planner import GoalPlanner
from src.runtime.goal.goal_state import (
    GOAL_STATUS_CANDIDATE,
    GOAL_TYPE_LEARNING,
    GoalState,
    build_candidate_goal,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_GOAL_PLAN_CREATED,
    INTEGRATION_GOAL_UPDATED,
)


class TestGoalEventEmitterBasic(unittest.TestCase):
    def test_default(self):
        e = build_default_goal_event_emitter()
        self.assertEqual(e.owner, DEFAULT_GOAL_EMITTER_OWNER)
        self.assertEqual(e.source, DEFAULT_EMITTER_SOURCE)
        self.assertEqual(e.schema_version, GOAL_EVENT_EMITTER_SCHEMA_VERSION)

    def test_custom_owner(self):
        e = GoalEventEmitter(owner="custom", source="custom_src")
        self.assertEqual(e.owner, "custom")
        self.assertEqual(e.source, "custom_src")


class TestEmitDesire(unittest.TestCase):
    def test_emit_desire(self):
        e = GoalEventEmitter()
        d = build_desire(
            topic="ai_art",
            strength=0.8,
            trend=DESIRE_TREND_RISING,
            supporting_signal_ids=["s1"],
            supporting_reflection_ids=["r1"],
            reason="r",
        )
        ev = e.emit_desire(d)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_DESIRE_CREATED)
        self.assertEqual(ev.payload["desire_id"], d.desire_id)
        self.assertEqual(ev.payload["topic"], "ai_art")
        self.assertEqual(ev.payload["trend"], DESIRE_TREND_RISING)
        self.assertIn(d.desire_id, ev.related_ids)
        self.assertIn("s1", ev.related_ids)
        self.assertEqual(e.emitted_desire_count, 1)

    def test_emit_desire_non_desire(self):
        e = GoalEventEmitter()
        self.assertIsNone(e.emit_desire("not desire"))
        self.assertIsNone(e.emit_desire(None))
        self.assertEqual(e.emitted_desire_count, 0)

    def test_emit_desire_no_evidence(self):
        e = GoalEventEmitter()
        d = build_desire(topic="t")  # no evidence
        self.assertIsNone(e.emit_desire(d))
        self.assertNotEqual(e.last_error, "")

    def test_emit_desires(self):
        e = GoalEventEmitter()
        d1 = build_desire(topic="t1", supporting_signal_ids=["s1"])
        d2 = build_desire(topic="t2", supporting_signal_ids=["s2"])
        evs = e.emit_desires([d1, d2])
        self.assertEqual(len(evs), 2)
        self.assertEqual(e.emitted_desire_count, 2)


class TestEmitGoal(unittest.TestCase):
    def test_emit_goal_created(self):
        e = GoalEventEmitter()
        g = build_candidate_goal(
            title="t",
            goal_type=GOAL_TYPE_LEARNING,
            source_event_ids=["e1"],
            source_desire_ids=["d1"],
            reason="r",
        )
        ev = e.emit_goal(g, lifecycle="created")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_GOAL_CREATED)
        self.assertEqual(ev.payload["goal_id"], g.goal_id)
        self.assertEqual(ev.payload["goal_type"], GOAL_TYPE_LEARNING)
        self.assertEqual(e.emitted_goal_count, 1)

    def test_emit_goal_updated(self):
        e = GoalEventEmitter()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        ev = e.emit_goal(g, lifecycle="updated")
        self.assertEqual(ev.event_type, INTEGRATION_GOAL_UPDATED)

    def test_emit_goal_non_goal(self):
        e = GoalEventEmitter()
        self.assertIsNone(e.emit_goal("not goal"))
        self.assertIsNone(e.emit_goal(None))

    def test_emit_goal_no_evidence(self):
        e = GoalEventEmitter()
        g = GoalState(title="t")
        self.assertIsNone(e.emit_goal(g))

    def test_emit_goals(self):
        e = GoalEventEmitter()
        g1 = build_candidate_goal(title="t1", source_event_ids=["e1"])
        g2 = build_candidate_goal(title="t2", source_event_ids=["e2"])
        evs = e.emit_goals([g1, g2])
        self.assertEqual(len(evs), 2)
        self.assertEqual(e.emitted_goal_count, 2)


class TestEmitPlan(unittest.TestCase):
    def test_emit_plan(self):
        e = GoalEventEmitter()
        p = GoalPlanner()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        ev = e.emit_plan(plan)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_GOAL_PLAN_CREATED)
        self.assertEqual(ev.payload["plan_id"], plan.plan_id)
        self.assertEqual(ev.payload["goal_id"], plan.goal_id)
        self.assertEqual(ev.payload["step_count"], len(plan.steps))
        self.assertEqual(e.emitted_plan_count, 1)

    def test_emit_plan_non_plan(self):
        e = GoalEventEmitter()
        self.assertIsNone(e.emit_plan("not plan"))
        self.assertIsNone(e.emit_plan(None))

    def test_emit_plans(self):
        e = GoalEventEmitter()
        p = GoalPlanner()
        g1 = build_candidate_goal(title="t1", source_event_ids=["e1"])
        g2 = build_candidate_goal(title="t2", source_event_ids=["e2"])
        evs = e.emit_plans([p.plan(g1), p.plan(g2)])
        self.assertEqual(len(evs), 2)


class TestEmitterStatistics(unittest.TestCase):
    def test_total_count(self):
        e = GoalEventEmitter()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        e.emit_desire(d)
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        e.emit_goal(g)
        self.assertEqual(e.emitted_count, 2)

    def test_last_event_id(self):
        e = GoalEventEmitter()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        ev = e.emit_desire(d)
        self.assertEqual(e.last_event_id, ev.event_id)

    def test_describe(self):
        e = GoalEventEmitter()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        e.emit_desire(d)
        d2 = e.describe()
        self.assertEqual(d2["emitted_count"], 1)
        self.assertEqual(d2["emitted_desire"], 1)
        self.assertNotEqual(d2["last_event_id"], "")

    def test_repr(self):
        e = GoalEventEmitter()
        r = repr(e)
        self.assertIn("GoalEventEmitter", r)


if __name__ == "__main__":
    unittest.main()
