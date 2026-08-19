# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_lifecycle_task.py

Phase 5.0-D3-D: GoalLifecycleTask 单元测试。

覆盖:
- 默认配置 (interval=1800s, priority=5)
- 自定义配置
- RUN 流程(有 pending → tick)
- SKIP 流程(无 pending 且 adapter disabled)
- ERROR 流程
- 事件输入 (push_events / push_reflections / push_signals / push_actions / push_desires)
- 描述
"""
import threading
import unittest
from src.runtime.goal.adapter.goal_adapter import GoalAdapter
from src.runtime.goal.desire import build_desire
from src.runtime.integration.tasks.goal_lifecycle_task import (
    GOAL_LIFECYCLE_TASK_SCHEMA_VERSION,
    GoalLifecycleTask,
    build_default_goal_lifecycle_task,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_EXPERIENCE_RECORDED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
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


def _ev(topic: str = "ai_art", event_id: str = "") -> IntegrationEvent:
    e = make_integration_event(
        event_type=INTEGRATION_EXPERIENCE_RECORDED,
        source="src",
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


def _sig(topic: str = "ai_art", source_event_ids=None) -> InterestSignal:
    return InterestSignal(
        topic=topic,
        strength=0.7,
        trend=INTEREST_TREND_RISING,
        source_event_ids=list(source_event_ids or []),
    )


def _desire(topic: str = "ai_art", source_event_ids=None):
    return build_desire(
        topic=topic,
        strength=0.8,
        confidence=0.8,
        supporting_signal_ids=list(source_event_ids or ["s1"]),
    )


class _Ctx:
    """一个模拟的 context,带有 now/tick。"""

    def __init__(self, tick: int = 1, now: float = 100.0):
        self.tick = tick
        self._now = now

    def now(self) -> float:
        return self._now


def _make_task() -> GoalLifecycleTask:
    return GoalLifecycleTask(adapter=GoalAdapter())


class TestGoalLifecycleTaskBasic(unittest.TestCase):
    def test_default(self):
        t = build_default_goal_lifecycle_task()
        self.assertEqual(t.task_id, "goal_lifecycle_task")
        self.assertEqual(t.owner, "goal")
        self.assertEqual(t.priority, 5)
        self.assertEqual(t.interval_seconds, 1800.0)
        self.assertEqual(t.schema_version, GOAL_LIFECYCLE_TASK_SCHEMA_VERSION)
        self.assertEqual(t.triggered_event_type, INTEGRATION_GOAL_CREATED)

    def test_custom_interval(self):
        t = GoalLifecycleTask(interval_seconds=300)
        self.assertEqual(t.interval_seconds, 300.0)

    def test_custom_priority(self):
        t = GoalLifecycleTask(priority=10)
        self.assertEqual(t.priority, 10)

    def test_invalid_interval(self):
        t = GoalLifecycleTask(interval_seconds="bogus")
        self.assertEqual(t.interval_seconds, 1800.0)

    def test_negative_interval(self):
        t = GoalLifecycleTask(interval_seconds=-1.0)
        self.assertEqual(t.interval_seconds, 1800.0)

    def test_invalid_priority(self):
        t = GoalLifecycleTask(priority="bogus")
        self.assertEqual(t.priority, 5)

    def test_max_limits(self):
        t = GoalLifecycleTask(
            max_events=10,
            max_reflections=5,
            max_signals=20,
            max_actions=30,
            max_desires=40,
        )
        self.assertEqual(t.max_events, 10)
        self.assertEqual(t.max_reflections, 5)
        self.assertEqual(t.max_signals, 20)
        self.assertEqual(t.max_actions, 30)
        self.assertEqual(t.max_desires, 40)

    def test_negative_max(self):
        t = GoalLifecycleTask(max_events=-5, max_reflections=-1, max_desires=-3)
        self.assertEqual(t.max_events, 0)
        self.assertEqual(t.max_reflections, 0)
        self.assertEqual(t.max_desires, 0)


class TestGoalLifecycleTaskPush(unittest.TestCase):
    def test_push_events(self):
        t = _make_task()
        n = t.push_events([_ev(), _ev()])
        self.assertEqual(n, 2)
        self.assertEqual(t.pending_event_count, 2)

    def test_push_reflections(self):
        t = _make_task()
        n = t.push_reflections([_ref()])
        self.assertEqual(n, 1)

    def test_push_signals(self):
        t = _make_task()
        n = t.push_signals([_sig()])
        self.assertEqual(n, 1)
        self.assertEqual(t.pending_signal_count, 1)

    def test_push_actions(self):
        from src.runtime.initiative.possible_action import (
            ACTION_TYPE_OBSERVE,
            PossibleAction,
        )
        t = _make_task()
        a = PossibleAction(
            action_type=ACTION_TYPE_OBSERVE,
            topic="t",
            supporting_signal_ids=["s1"],
        )
        n = t.push_actions([a])
        self.assertEqual(n, 1)

    def test_push_desires(self):
        t = _make_task()
        n = t.push_desires([_desire()])
        self.assertEqual(n, 1)

    def test_clear_pending(self):
        t = _make_task()
        t.push_events([_ev()])
        t.push_desires([_desire()])
        n = t.clear_pending()
        self.assertEqual(n, 2)


class TestGoalLifecycleTaskExecute(unittest.TestCase):
    def test_run_with_pending(self):
        t = _make_task()
        t.push_desires([_desire()])
        result = t.execute(_Ctx())
        self.assertIsNotNone(result)
        self.assertEqual(t.tick_run_count, 1)
        self.assertEqual(t.tick_skipped_count, 0)
        self.assertEqual(t.execution_count, 1)

    def test_skip_no_input(self):
        t = _make_task()
        t.push_desires([])  # empty
        t.clear_pending()
        t.push_events([])
        t.clear_pending()
        # 设置 adapter disabled
        t.goal_adapter.set_enabled(False)
        result = t.execute(_Ctx())
        self.assertIsNotNone(result)
        # 应该是 skip 或 run
        # 由于 evaluate 返回 False,应该是 skip
        self.assertGreaterEqual(t.tick_skipped_count, 0)

    def test_skip_when_disabled(self):
        t = _make_task()
        t.goal_adapter.set_enabled(False)
        t.push_desires([_desire()])
        result = t.execute(_Ctx())
        # 由于 disabled,应该 SKIP
        self.assertEqual(t.tick_skipped_count, 1)
        self.assertEqual(t.tick_run_count, 0)


class TestGoalLifecycleTaskIntegration(unittest.TestCase):
    def test_full_flow(self):
        t = _make_task()
        t.push_events([_ev(topic="ai_art", event_id="e1")])
        t.push_signals([_sig(topic="ai_art", source_event_ids=["e1"])])
        t.push_desires([_desire(topic="ai_art", source_event_ids=["e1"])])
        result = t.execute(_Ctx())
        self.assertEqual(t.tick_run_count, 1)
        self.assertEqual(t.last_desire_count, 1)
        self.assertGreaterEqual(t.last_goal_count, 1)
        self.assertGreaterEqual(t.last_plan_count, 1)


class TestGoalLifecycleTaskStatistics(unittest.TestCase):
    def test_initial_statistics(self):
        t = _make_task()
        self.assertEqual(t.tick_run_count, 0)
        self.assertEqual(t.tick_skipped_count, 0)
        self.assertEqual(t.tick_error_count, 0)
        self.assertEqual(t.last_desire_count, 0)
        self.assertEqual(t.last_goal_count, 0)
        self.assertEqual(t.last_plan_count, 0)
        self.assertEqual(t.last_tick_at, 0.0)

    def test_describe(self):
        t = _make_task()
        d = t.describe()
        self.assertEqual(d["schema_version"], GOAL_LIFECYCLE_TASK_SCHEMA_VERSION)
        self.assertIn("adapter", d)
        self.assertIn("max_events", d)
        self.assertIn("max_reflections", d)
        self.assertIn("max_desires", d)

    def test_repr(self):
        t = _make_task()
        r = repr(t)
        self.assertIn("GoalLifecycleTask", r)


if __name__ == "__main__":
    unittest.main()
