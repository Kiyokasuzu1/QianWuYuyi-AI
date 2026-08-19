# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_planner.py

Phase 5.0-D3-D: GoalPlanner 单元测试。

覆盖:
- GoalPlanner 创建与配置
- plan() 拆解(各种 goal_type)
- Milestone: 创建, 序列化, 状态
- GoalPlan: 创建, recompute_progress, 序列化
- 限制 (max_milestones)
- 异常隔离
"""
import unittest
from src.runtime.goal.goal_planner import (
    DEFAULT_MAX_MILESTONES,
    GOAL_PLAN_SCHEMA_VERSION,
    GoalPlan,
    GoalPlanner,
    Milestone,
    build_default_goal_planner,
)
from src.runtime.goal.goal_state import (
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    GoalState,
    build_candidate_goal,
)


class TestMilestone(unittest.TestCase):
    def test_default_construction(self):
        m = Milestone()
        self.assertTrue(m.step_id.startswith("stp_"))
        self.assertEqual(m.status, "pending")
        self.assertEqual(m.order, 0)

    def test_invalid_status_fallback(self):
        m = Milestone(status="bogus")
        self.assertEqual(m.status, "pending")

    def test_order_negative_clipped(self):
        m = Milestone(order=-5)
        self.assertEqual(m.order, 0)

    def test_order_invalid(self):
        m = Milestone(order="bogus")
        self.assertEqual(m.order, 0)

    def test_to_dict_roundtrip(self):
        m = Milestone(order=2, title="t", description="d", status="active", created_at=10.0, completed_at=20.0)
        m2 = Milestone.from_dict(m.to_dict())
        self.assertEqual(m2.order, 2)
        self.assertEqual(m2.title, "t")
        self.assertEqual(m2.status, "active")

    def test_from_dict_invalid(self):
        m = Milestone.from_dict("not dict")
        self.assertEqual(m.order, 0)

    def test_mark_completed(self):
        m = Milestone()
        m.mark_completed(now=1.0)
        self.assertEqual(m.status, "completed")
        self.assertEqual(m.completed_at, 1.0)

    def test_mark_active_only_from_pending(self):
        m = Milestone()
        m.mark_active()
        self.assertEqual(m.status, "active")
        # 已 active 的不会变
        m.mark_active()
        self.assertEqual(m.status, "active")

    def test_title_clipping(self):
        m = Milestone(title="t" * 5000)
        self.assertLessEqual(len(m.title), 128)

    def test_description_clipping(self):
        m = Milestone(description="d" * 5000)
        self.assertLessEqual(len(m.description), 512)

    def test_repr(self):
        m = Milestone(order=3, title="Hello")
        r = repr(m)
        self.assertIn("Milestone", r)
        self.assertIn("3", r)


class TestGoalPlan(unittest.TestCase):
    def test_default_construction(self):
        p = GoalPlan()
        self.assertTrue(p.plan_id.startswith("plan_"))
        self.assertEqual(p.version, GOAL_PLAN_SCHEMA_VERSION)
        self.assertEqual(p.progress, 0.0)

    def test_invalid_steps_filtered(self):
        p = GoalPlan(steps=["bad", 1, Milestone(title="ok")])
        self.assertEqual(len(p.steps), 1)
        self.assertEqual(p.steps[0].title, "ok")

    def test_progress_clipping(self):
        p = GoalPlan(progress=2.0)
        self.assertEqual(p.progress, 1.0)
        p2 = GoalPlan(progress=-1.0)
        self.assertEqual(p2.progress, 0.0)

    def test_has_milestones(self):
        p = GoalPlan()
        self.assertFalse(p.has_milestones())
        p.steps.append(Milestone())
        self.assertTrue(p.has_milestones())

    def test_completed_count(self):
        p = GoalPlan()
        m1 = Milestone(status="completed")
        m2 = Milestone(status="pending")
        m3 = Milestone(status="completed")
        p.steps = [m1, m2, m3]
        self.assertEqual(p.completed_count(), 2)

    def test_recompute_progress_empty(self):
        p = GoalPlan()
        p.recompute_progress()
        self.assertEqual(p.progress, 0.0)

    def test_recompute_progress_with_completed(self):
        p = GoalPlan()
        p.steps = [
            Milestone(status="completed"),
            Milestone(status="pending"),
            Milestone(status="completed"),
            Milestone(status="completed"),
        ]
        p.recompute_progress(now=1.0)
        self.assertEqual(p.progress, 0.75)
        self.assertEqual(p.updated_at, 1.0)

    def test_to_dict_roundtrip(self):
        p = GoalPlan(goal_id="g1", title="plan", progress=0.5)
        p.steps = [Milestone(order=0, title="step1")]
        d = p.to_dict()
        p2 = GoalPlan.from_dict(d)
        self.assertEqual(p2.goal_id, "g1")
        self.assertEqual(p2.title, "plan")
        self.assertEqual(p2.progress, 0.5)
        self.assertEqual(len(p2.steps), 1)
        self.assertEqual(p2.steps[0].title, "step1")

    def test_from_dict_invalid(self):
        p = GoalPlan.from_dict("not dict")
        self.assertEqual(p.goal_id, "invalid")

    def test_summary(self):
        p = GoalPlan(goal_id="g1", title="plan", progress=0.25)
        p.steps = [Milestone(status="completed"), Milestone(status="pending")]
        s = p.summary()
        self.assertEqual(s["step_count"], 2)
        self.assertEqual(s["completed_count"], 1)
        self.assertEqual(s["progress"], 0.25)

    def test_repr(self):
        p = GoalPlan(goal_id="g1", progress=0.5)
        r = repr(p)
        self.assertIn("GoalPlan", r)


class TestGoalPlanner(unittest.TestCase):
    def test_default_creation(self):
        p = build_default_goal_planner()
        self.assertEqual(p.max_milestones, DEFAULT_MAX_MILESTONES)
        self.assertEqual(p.plans_created, 0)

    def test_plan_learning(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="学习 AI 绘画", goal_type=GOAL_TYPE_LEARNING, source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        self.assertEqual(plan.goal_id, g.goal_id)
        self.assertTrue(plan.has_milestones())
        self.assertGreater(len(plan.steps), 0)
        self.assertEqual(plan.progress, 0.0)
        self.assertEqual(plan.created_at, 10.0)
        self.assertEqual(p.plans_created, 1)

    def test_plan_creative(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="创作 AI 音乐", goal_type=GOAL_TYPE_CREATIVE, source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertTrue(plan.has_milestones())

    def test_plan_relationship(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="关系维护", goal_type=GOAL_TYPE_RELATIONSHIP, source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertTrue(plan.has_milestones())

    def test_plan_exploration(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="探索未知", goal_type=GOAL_TYPE_EXPLORATION, source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertTrue(plan.has_milestones())

    def test_plan_personal_growth(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="个人成长", goal_type=GOAL_TYPE_PERSONAL_GROWTH, source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertTrue(plan.has_milestones())

    def test_plan_default_type(self):
        p = GoalPlanner()
        g = GoalState(title="t", goal_type="bogus", source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertTrue(plan.has_milestones())

    def test_max_milestones_clamp(self):
        p = GoalPlanner(max_milestones=2)
        g = build_candidate_goal(title="t", goal_type=GOAL_TYPE_LEARNING, source_event_ids=["e1"])
        plan = p.plan(g)
        self.assertLessEqual(len(plan.steps), 2)

    def test_invalid_max_milestones(self):
        p = GoalPlanner(max_milestones="bogus")
        self.assertEqual(p.max_milestones, DEFAULT_MAX_MILESTONES)

    def test_plan_invalid_goal(self):
        p = GoalPlanner()
        plan = p.plan("not a goal")  # type: ignore[arg-type]
        # 容错:返回空 plan
        self.assertEqual(len(plan.steps), 0)

    def test_describe(self):
        p = GoalPlanner()
        d = p.describe()
        self.assertEqual(d["max_milestones"], DEFAULT_MAX_MILESTONES)
        self.assertEqual(d["plans_created"], 0)
        self.assertEqual(d["last_error"], "")

    def test_repr(self):
        p = GoalPlanner(max_milestones=4)
        r = repr(p)
        self.assertIn("GoalPlanner", r)
        self.assertIn("4", r)


if __name__ == "__main__":
    unittest.main()
