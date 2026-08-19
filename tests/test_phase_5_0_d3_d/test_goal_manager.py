# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_manager.py

Phase 5.0-D3-D: GoalManager 单元测试。

覆盖:
- 创建与配置
- Desire CRUD
- Goal CRUD (create, update, activate, pause, complete, abandon)
- Plan 管理
- History (FIFO, replay, count)
- 并发
- enable/disable
"""
import threading
import unittest
from src.runtime.goal.desire import (
    DESIRE_TREND_RISING,
    Desire,
    build_desire,
)
from src.runtime.goal.goal_manager import (
    DEFAULT_MAX_GOALS,
    DEFAULT_MAX_PLANS,
    GOAL_MANAGER_SCHEMA_VERSION,
    GoalManager,
    build_default_goal_manager,
)
from src.runtime.goal.goal_planner import GoalPlan
from src.runtime.goal.goal_record import (
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_UPDATED,
)
from src.runtime.goal.goal_state import (
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_PAUSED,
    GoalState,
    build_candidate_goal,
)


class TestGoalManagerBasic(unittest.TestCase):
    def test_default(self):
        m = build_default_goal_manager()
        self.assertEqual(m.goal_count, 0)
        self.assertEqual(m.plan_count, 0)
        self.assertEqual(m.created_count, 0)
        self.assertEqual(m.enabled, True)
        self.assertEqual(m.schema_version, GOAL_MANAGER_SCHEMA_VERSION)

    def test_set_enabled(self):
        m = GoalManager()
        m.set_enabled(False)
        self.assertFalse(m.enabled)
        m.set_enabled(True)
        self.assertTrue(m.enabled)

    def test_invalid_max_goals(self):
        m = GoalManager(max_goals="bogus")
        # 初始时 goal_count 应为 0
        self.assertEqual(m.goal_count, 0)
        # 应回退到默认值,可以正常创建
        m.create_goal(build_candidate_goal(title="t", source_event_ids=["e1"]))
        self.assertEqual(m.goal_count, 1)


class TestGoalManagerDesire(unittest.TestCase):
    def test_add_desire(self):
        m = GoalManager()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        self.assertTrue(m.add_desire(d))
        self.assertEqual(m.desire_count_total, 1)
        self.assertEqual(m.desire_registry.size, 1)

    def test_add_desire_no_evidence(self):
        m = GoalManager()
        d = Desire(topic="t")
        self.assertFalse(m.add_desire(d))

    def test_add_desire_invalid(self):
        m = GoalManager()
        self.assertFalse(m.add_desire("not desire"))
        self.assertFalse(m.add_desire(None))

    def test_get_desire(self):
        m = GoalManager()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        m.add_desire(d)
        self.assertEqual(m.get_desire(d.desire_id), d)
        self.assertIsNone(m.get_desire("missing"))
        self.assertIsNone(m.get_desire(""))

    def test_list_desires(self):
        m = GoalManager()
        for i in range(3):
            d = build_desire(topic=f"t{i}", supporting_signal_ids=[f"s{i}"])
            m.add_desire(d)
        self.assertEqual(len(m.list_desires()), 3)

    def test_find_desires_by_topic(self):
        m = GoalManager()
        d1 = build_desire(topic="t1", supporting_signal_ids=["a"])
        d2 = build_desire(topic="t1", supporting_signal_ids=["b"])
        m.add_desire(d1)
        m.add_desire(d2)
        self.assertEqual(len(m.find_desires_by_topic("t1")), 2)
        self.assertEqual(len(m.find_desires_by_topic("missing")), 0)
        self.assertEqual(len(m.find_desires_by_topic("")), 0)

    def test_remove_desire(self):
        m = GoalManager()
        d = build_desire(topic="t", supporting_signal_ids=["s1"])
        m.add_desire(d)
        self.assertTrue(m.remove_desire(d.desire_id))
        self.assertIsNone(m.get_desire(d.desire_id))
        self.assertFalse(m.remove_desire("missing"))


class TestGoalManagerGoalCRUD(unittest.TestCase):
    def test_create_goal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"], source_desire_ids=["d1"])
        result = m.create_goal(g)
        self.assertEqual(result.status, GOAL_STATUS_CANDIDATE)
        self.assertEqual(m.goal_count, 1)
        self.assertEqual(m.created_count, 1)
        self.assertEqual(m.goal_count_total, 1)

    def test_create_goal_invalid(self):
        m = GoalManager()
        g = GoalState(title="")  # invalid
        m.create_goal(g)
        self.assertEqual(m.goal_count, 0)

    def test_create_goal_wrong_status(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        g.status = GOAL_STATUS_ACTIVE
        m.create_goal(g)
        self.assertEqual(m.goal_count, 0)

    def test_create_goal_creates_plan(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g, plan=True)
        self.assertEqual(m.plan_count, 1)
        self.assertEqual(m.plan_count_total, 1)

    def test_create_goal_without_plan(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g, plan=False)
        self.assertEqual(m.plan_count, 0)

    def test_create_goal_disabled(self):
        m = GoalManager(enabled=False)
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        self.assertEqual(m.goal_count, 0)

    def test_get_goal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        self.assertEqual(m.get_goal(g.goal_id), g)
        self.assertIsNone(m.get_goal("missing"))
        self.assertIsNone(m.get_goal(""))

    def test_list_goals_filter_status(self):
        m = GoalManager()
        g1 = build_candidate_goal(title="t1", source_event_ids=["e1"])
        g2 = build_candidate_goal(title="t2", source_event_ids=["e2"])
        m.create_goal(g1)
        m.create_goal(g2)
        m.activate_goal(g1.goal_id)
        self.assertEqual(len(m.list_goals(status=GOAL_STATUS_CANDIDATE)), 1)
        self.assertEqual(len(m.list_goals(status=GOAL_STATUS_ACTIVE)), 1)
        self.assertEqual(len(m.list_goals()), 2)

    def test_list_goals_filter_type(self):
        from src.runtime.goal.goal_state import GOAL_TYPE_LEARNING, GOAL_TYPE_CREATIVE
        m = GoalManager()
        g1 = build_candidate_goal(title="t1", goal_type=GOAL_TYPE_LEARNING, source_event_ids=["e1"])
        g2 = build_candidate_goal(title="t2", goal_type=GOAL_TYPE_CREATIVE, source_event_ids=["e2"])
        m.create_goal(g1)
        m.create_goal(g2)
        self.assertEqual(len(m.list_goals(goal_type=GOAL_TYPE_LEARNING)), 1)
        self.assertEqual(len(m.list_goals(goal_type=GOAL_TYPE_CREATIVE)), 1)

    def test_list_goals_invalid_status(self):
        m = GoalManager()
        self.assertEqual(len(m.list_goals(status="bogus")), 0)

    def test_remove_goal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        self.assertTrue(m.remove_goal(g.goal_id))
        self.assertEqual(m.goal_count, 0)
        self.assertFalse(m.remove_goal(g.goal_id))
        self.assertFalse(m.remove_goal("missing"))


class TestGoalManagerUpdate(unittest.TestCase):
    def test_update_goal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        updated = m.update_goal(
            g.goal_id,
            title="new",
            description="new desc",
            priority=0.8,
            importance=0.7,
            reason="r",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.title, "new")
        self.assertEqual(updated.description, "new desc")
        self.assertAlmostEqual(updated.priority, 0.8)
        self.assertAlmostEqual(updated.importance, 0.7)
        self.assertEqual(updated.reason, "r")
        self.assertEqual(m.updated_count, 1)

    def test_update_goal_missing(self):
        m = GoalManager()
        self.assertIsNone(m.update_goal("missing", title="x"))

    def test_update_goal_terminal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        m.complete_goal(g.goal_id)
        self.assertIsNone(m.update_goal(g.goal_id, title="new"))


class TestGoalManagerTransitions(unittest.TestCase):
    def _make_active(self) -> GoalState:
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        return g

    def test_activate(self):
        g = self._make_active()
        self.assertEqual(g.status, GOAL_STATUS_ACTIVE)
        self.assertEqual(g.updated_at > 0, True)

    def test_activate_idempotent(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        self.assertTrue(m.activate_goal(g.goal_id))
        self.assertEqual(g.status, GOAL_STATUS_ACTIVE)

    def test_pause(self):
        g = self._make_active()
        m = GoalManager()
        m.activate_goal(g.goal_id)  # already active
        # 重新创建
        g2 = build_candidate_goal(title="t2", source_event_ids=["e1"])
        m.create_goal(g2)
        m.activate_goal(g2.goal_id)
        self.assertTrue(m.pause_goal(g2.goal_id))
        self.assertEqual(g2.status, GOAL_STATUS_PAUSED)

    def test_complete_from_active(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        self.assertTrue(m.complete_goal(g.goal_id))
        self.assertEqual(g.status, GOAL_STATUS_COMPLETED)
        self.assertEqual(m.completed_count, 1)

    def test_abandon(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        self.assertTrue(m.abandon_goal(g.goal_id, reason="放弃"))
        self.assertEqual(g.status, GOAL_STATUS_ABANDONED)
        self.assertEqual(m.abandoned_count, 1)

    def test_paused_to_completed(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        m.pause_goal(g.goal_id)
        self.assertTrue(m.complete_goal(g.goal_id))
        self.assertEqual(g.status, GOAL_STATUS_COMPLETED)

    def test_completed_to_activate_fails(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        m.complete_goal(g.goal_id)
        self.assertFalse(m.activate_goal(g.goal_id))

    def test_completed_to_abandon_fails(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        m.complete_goal(g.goal_id)
        self.assertFalse(m.abandon_goal(g.goal_id))

    def test_transition_missing(self):
        m = GoalManager()
        self.assertFalse(m.activate_goal("missing"))
        self.assertFalse(m.pause_goal("missing"))
        self.assertFalse(m.complete_goal("missing"))
        self.assertFalse(m.abandon_goal("missing"))


class TestGoalManagerPlan(unittest.TestCase):
    def test_get_plan(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g, plan=True)
        plans = m.list_plans()
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        self.assertEqual(plan.goal_id, g.goal_id)
        self.assertEqual(m.get_plan(plan.plan_id), plan)
        self.assertIsNone(m.get_plan("missing"))

    def test_get_plans_for_goal(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g, plan=True)
        self.assertEqual(len(m.get_plans_for_goal(g.goal_id)), 1)
        self.assertEqual(len(m.get_plans_for_goal("missing")), 0)
        self.assertEqual(len(m.get_plans_for_goal("")), 0)

    def test_create_plan_explicit(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g, plan=False)
        plan = m.create_plan(g)
        self.assertIsNotNone(plan)
        self.assertEqual(plan.goal_id, g.goal_id)


class TestGoalManagerHistory(unittest.TestCase):
    def test_history(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        m.pause_goal(g.goal_id)
        h = m.history()
        self.assertGreaterEqual(len(h), 3)
        # 第一条是 created
        self.assertEqual(h[0].change_type, GOAL_CHANGE_CREATED)

    def test_history_for_goal(self):
        m = GoalManager()
        g1 = build_candidate_goal(title="t1", source_event_ids=["e1"])
        g2 = build_candidate_goal(title="t2", source_event_ids=["e2"])
        m.create_goal(g1)
        m.create_goal(g2)
        m.activate_goal(g1.goal_id)
        h1 = m.history_for(g1.goal_id)
        self.assertEqual(len(h1), 2)

    def test_snapshots(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        m.activate_goal(g.goal_id)
        snaps = m.snapshots()
        self.assertGreaterEqual(len(snaps), 2)


class TestGoalManagerConcurrency(unittest.TestCase):
    def test_concurrent_create(self):
        m = GoalManager(max_goals=1000)
        results = []
        lock = threading.Lock()

        def worker(i: int):
            g = build_candidate_goal(title=f"t{i}", source_event_ids=[f"e{i}"])
            r = m.create_goal(g, plan=False)
            with lock:
                results.append(r is not None and r.goal_id is not None)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(1 for x in results if x), 50)

    def test_concurrent_transitions(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        results = []
        lock = threading.Lock()

        def worker():
            ok = m.activate_goal(g.goal_id)
            with lock:
                results.append(ok)
        ts = [threading.Thread(target=worker) for _ in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(1 for x in results if x), 20)


class TestGoalManagerDescribe(unittest.TestCase):
    def test_describe(self):
        m = GoalManager()
        g = build_candidate_goal(title="t", source_event_ids=["e1"])
        m.create_goal(g)
        d = m.describe()
        self.assertEqual(d["name"], "goal_manager")
        self.assertTrue(d["enabled"])
        self.assertEqual(d["goal_count"], 1)
        self.assertIn("planner", d)
        self.assertIn("history", d)

    def test_repr(self):
        m = GoalManager()
        r = repr(m)
        self.assertIn("GoalManager", r)


if __name__ == "__main__":
    unittest.main()
