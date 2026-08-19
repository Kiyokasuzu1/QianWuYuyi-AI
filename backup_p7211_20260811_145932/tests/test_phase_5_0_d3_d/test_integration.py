# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_integration.py

Phase 5.0-D3-D: 端到端集成测试。

覆盖:
- Reflection -> Initiative -> Desire -> Goal -> Plan 完整链路
- 集成事件(IntegrationEvent)正确产生
- 所有 Goal 有 evidence
- Goal 不执行任何动作
- 线程安全
"""
import unittest
from src.runtime.goal.adapter.goal_adapter import GoalAdapter
from src.runtime.goal.desire import (
    DESIRE_TREND_RISING,
    build_desire,
)
from src.runtime.goal.goal_manager import GoalManager
from src.runtime.goal.goal_state import (
    GOAL_STATUS_CANDIDATE,
    GOAL_TYPE_LEARNING,
    build_candidate_goal,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_GOAL_PLAN_CREATED,
    INTEGRATION_GOAL_UPDATED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.initiative.interest_signal import (
    INTEREST_TREND_RISING,
    InterestSignal,
)
from src.runtime.reflection.reflection_result import (
    Insight,
    REFLECTION_TYPE_DAILY,
    ReflectionResult,
)


def _ev(topic: str, event_id: str = "") -> IntegrationEvent:
    e = make_integration_event(
        event_type="integration.experience.recorded",
        source="test",
        payload={"topic": topic},
    )
    if event_id:
        e.event_id = event_id
    return e


def _ref(topics=None, source_event_ids=None) -> ReflectionResult:
    insights = [
        Insight(
            category="pattern",
            description=f"用户表现出对 {t} 的兴趣",
            supporting_event_ids=[],
            confidence=0.7,
        )
        for t in (topics or [])
    ]
    return ReflectionResult(
        reflection_type=REFLECTION_TYPE_DAILY,
        triggered_at=0.0,
        insights=insights,
        suggested_changes=[],
        source_event_ids=list(source_event_ids or []),
    )


def _sig(topic: str, source_event_ids=None) -> InterestSignal:
    return InterestSignal(
        topic=topic,
        strength=0.8,
        trend=INTEREST_TREND_RISING,
        source_event_ids=list(source_event_ids or []),
    )


def _desire(topic: str = "ai_art", source_event_ids=None):
    return build_desire(
        topic=topic,
        strength=0.85,
        confidence=0.85,
        trend=DESIRE_TREND_RISING,
        supporting_signal_ids=list(source_event_ids or ["e1"]),
    )


class TestFullPipeline(unittest.TestCase):
    def test_reflection_initiative_desire_goal_plan(self):
        """完整链路:Reflection + Initiative -> Desire -> Goal -> Plan。"""
        a = GoalAdapter()
        # 1) Reflection
        reflections = [_ref(topics=["ai_art"], source_event_ids=["e1"])]
        # 2) Interest signals (Initiative 阶段产生)
        signals = [_sig(topic="ai_art", source_event_ids=["e1"])]
        # 3) Integration events
        events = [_ev(topic="ai_art", event_id="e1")]
        # 4) 直接构造 desires(模拟从 Initiative 提升)
        desires = [_desire(topic="ai_art", source_event_ids=["e1"])]
        out = a.tick(
            events=events,
            reflections=reflections,
            signals=signals,
            desires=desires,
            plan=True,
        )
        # 5) 验证
        self.assertIsNotNone(out)
        self.assertEqual(out.desires_added, 1)
        self.assertGreaterEqual(out.goals_created, 1)
        self.assertGreater(out.plans_created, 0)
        # 6) 验证事件
        self.assertGreater(len(out.desire_events), 0)
        self.assertEqual(out.desire_events[0].event_type, INTEGRATION_DESIRE_CREATED)
        self.assertGreater(len(out.goal_events), 0)
        self.assertEqual(out.goal_events[0].event_type, INTEGRATION_GOAL_CREATED)
        self.assertGreater(len(out.plan_events), 0)
        self.assertEqual(out.plan_events[0].event_type, INTEGRATION_GOAL_PLAN_CREATED)

    def test_all_goals_have_evidence(self):
        """所有生成的 Goal 必须有 evidence。"""
        a = GoalAdapter()
        desires = [_desire(topic=f"t{i}", source_event_ids=[f"e{i}"]) for i in range(3)]
        a.tick(desires=desires)
        for g in a.manager.list_goals():
            self.assertTrue(g.has_evidence(), f"goal {g.goal_id} has no evidence")
            self.assertTrue(g.is_valid())

    def test_goal_no_execute(self):
        """Goal 只描述,不执行任何动作。"""
        a = GoalAdapter()
        out = a.tick(desires=[_desire(topic="ai_art", source_event_ids=["e1"])])
        g = a.manager.list_goals()[0]
        # Goal 没有 action / execute 方法
        self.assertFalse(hasattr(g, "execute"))
        self.assertFalse(hasattr(g, "run"))
        # 验证 GoalPlan 也不可执行
        plan = a.manager.list_plans()[0]
        self.assertFalse(hasattr(plan, "execute"))
        self.assertFalse(hasattr(plan, "run"))

    def test_planner_creates_only(self):
        """GoalPlanner 只生成 Plan,不执行。"""
        a = GoalAdapter()
        a.tick(desires=[_desire()])
        plans = a.manager.list_plans()
        self.assertGreater(len(plans), 0)
        for p in plans:
            self.assertTrue(p.has_milestones())


class TestIntegrationMultiTopic(unittest.TestCase):
    def test_multi_topic(self):
        a = GoalAdapter()
        topics = ["ai_art", "code_review", "social_event"]
        desires = [_desire(topic=t, source_event_ids=[f"e_{t}"]) for t in topics]
        out = a.tick(desires=desires)
        self.assertGreaterEqual(out.goals_created, 3)
        # 每个 topic 都有对应 goal
        goal_topics = [g.title for g in a.manager.list_goals()]
        for t in topics:
            found = any(t in gt for gt in goal_topics)
            self.assertTrue(found, f"topic {t} not in goals: {goal_topics}")


class TestIntegrationNoBusinessModuleDeps(unittest.TestCase):
    """约束检查:不依赖业务模块。"""

    def test_no_business_imports(self):
        """Goal 模块不应直接 import 业务模块。"""
        import importlib
        import sys
        # 重新加载
        from src.runtime.goal import (
            goal_state,
            desire,
            goal_record,
            goal_generator,
            goal_planner,
            goal_history,
            goal_manager,
        )
        from src.runtime.goal.adapter import (
            goal_adapter,
            goal_event_emitter,
        )
        modules = [
            goal_state,
            desire,
            goal_record,
            goal_generator,
            goal_planner,
            goal_history,
            goal_manager,
            goal_adapter,
            goal_event_emitter,
        ]
        forbidden = ("src.memory", "src.growth", "src.personality", "src.emotion", "src.relationship")
        for mod in modules:
            name = mod.__name__
            for f in forbidden:
                # 检查模块的 __dict__ 是否有 import 痕迹
                for attr in dir(mod):
                    if attr.startswith("__"):
                        continue
                    v = getattr(mod, attr, None)
                    if hasattr(v, "__module__") and v.__module__ and v.__module__.startswith(f):
                        self.fail(f"module {name} imports from {f}: {v.__module__}.{attr}")


class TestIntegrationLifecycle(unittest.TestCase):
    def test_lifecycle_task_runs(self):
        from src.runtime.integration.tasks.goal_lifecycle_task import (
            GoalLifecycleTask,
        )
        task = GoalLifecycleTask()
        task.push_desires([_desire(topic="ai_art", source_event_ids=["e1"])])

        class _Ctx:
            def __init__(self):
                self.tick = 1
            def now(self):
                return 100.0

        result = task.execute(_Ctx())
        self.assertIsNotNone(result)
        self.assertEqual(task.tick_run_count, 1)

    def test_desires_added_to_manager(self):
        a = GoalAdapter()
        a.tick(desires=[_desire()])
        self.assertEqual(a.manager.desire_count_total, 1)
        # Desire 必须可以在 manager 中找到
        desires = a.manager.list_desires()
        self.assertEqual(len(desires), 1)


class TestIntegrationConstraints(unittest.TestCase):
    def test_all_evidence(self):
        """再次验证 evidence 约束。"""
        a = GoalAdapter()
        a.tick(desires=[_desire(topic=f"t{i}", source_event_ids=[f"e{i}"]) for i in range(5)])
        for g in a.manager.list_goals():
            self.assertTrue(g.has_evidence())

    def test_concurrent_pipeline(self):
        """并发安全:多线程同时 tick。"""
        import threading
        a = GoalAdapter()
        results = []
        lock = threading.Lock()

        def worker(i: int):
            out = a.tick(
                desires=[_desire(topic=f"t{i}", source_event_ids=[f"e_{i}"])],
            )
            with lock:
                results.append(out is not None)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(1 for x in results if x), 10)


if __name__ == "__main__":
    unittest.main()
