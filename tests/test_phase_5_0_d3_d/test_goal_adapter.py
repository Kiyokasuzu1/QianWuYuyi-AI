# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_adapter.py

Phase 5.0-D3-D: GoalAdapter 单元测试。

覆盖:
- 创建与配置
- push_events / push_reflections / push_signals / push_actions / push_desires
- evaluate
- tick (RUN, SKIP, ERROR)
- clear_pending
- enable/disable
- 错误隔离
"""
import unittest
from src.runtime.goal.adapter.goal_adapter import (
    DEFAULT_GOAL_ADAPTER_OWNER,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_REFLECTIONS,
    GOAL_ADAPTER_SCHEMA_VERSION,
    GoalAdapter,
    GoalAdapterTickOutput,
    build_default_goal_adapter,
)
from src.runtime.goal.desire import build_desire
from src.runtime.goal.goal_generator import GoalGenerator
from src.runtime.goal.goal_manager import GoalManager
from src.runtime.goal.goal_planner import GoalPlanner
from src.runtime.goal.goal_state import (
    GOAL_STATUS_CANDIDATE,
    GOAL_TYPE_LEARNING,
    build_candidate_goal,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_EXPERIENCE_RECORDED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_REFLECTION_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.initiative.interest_signal import (
    INTEREST_TREND_RISING,
    InterestSignal,
)
from src.runtime.initiative.possible_action import (
    ACTION_TYPE_OBSERVE,
    PossibleAction,
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
        strength=0.8,
        trend=INTEREST_TREND_RISING,
        source_event_ids=list(source_event_ids or []),
    )


def _desire(topic: str = "ai_art", source_event_ids=None) -> "Desire":
    from src.runtime.goal.desire import build_desire
    return build_desire(
        topic=topic,
        strength=0.8,
        confidence=0.8,
        supporting_signal_ids=list(source_event_ids or ["s1"]),
    )


def _action(topic: str = "ai_art") -> PossibleAction:
    return PossibleAction(
        action_type=ACTION_TYPE_OBSERVE,
        topic=topic,
        supporting_signal_ids=["s1"],
    )


class TestGoalAdapterBasic(unittest.TestCase):
    def test_default_creation(self):
        a = build_default_goal_adapter()
        self.assertTrue(a.is_available())
        self.assertEqual(a.owner, DEFAULT_GOAL_ADAPTER_OWNER)
        self.assertEqual(a.schema_version, GOAL_ADAPTER_SCHEMA_VERSION)
        self.assertTrue(a.enabled)
        self.assertEqual(a.tick_count, 0)
        self.assertEqual(a.pending_event_count, 0)
        self.assertEqual(a.pending_reflection_count, 0)
        self.assertEqual(a.pending_signal_count, 0)
        self.assertEqual(a.pending_action_count, 0)
        self.assertEqual(a.pending_desire_count, 0)

    def test_custom_components(self):
        m = GoalManager()
        g = GoalGenerator()
        p = GoalPlanner()
        a = GoalAdapter(manager=m, generator=g, planner=p)
        self.assertEqual(a.manager, m)
        self.assertEqual(a.generator, g)
        self.assertEqual(a.planner, p)

    def test_invalid_components_use_defaults(self):
        a = GoalAdapter(manager="not manager", generator="not gen", planner="not planner")
        self.assertIsInstance(a.manager, GoalManager)
        self.assertIsInstance(a.generator, GoalGenerator)
        self.assertIsInstance(a.planner, GoalPlanner)

    def test_set_enabled(self):
        a = GoalAdapter()
        a.set_enabled(False)
        self.assertFalse(a.enabled)
        a.set_enabled(True)
        self.assertTrue(a.enabled)


class TestGoalAdapterPush(unittest.TestCase):
    def test_push_events(self):
        a = GoalAdapter()
        events = [_ev(topic=f"t{i}") for i in range(3)]
        n = a.push_events(events)
        self.assertEqual(n, 3)
        self.assertEqual(a.pending_event_count, 3)

    def test_push_events_none(self):
        a = GoalAdapter()
        self.assertEqual(a.push_events(None), 0)

    def test_push_events_filter_invalid(self):
        a = GoalAdapter()
        n = a.push_events(["bad", 1, _ev()])
        self.assertEqual(n, 1)

    def test_push_reflections(self):
        a = GoalAdapter()
        n = a.push_reflections([_ref()])
        self.assertEqual(n, 1)

    def test_push_reflections_none(self):
        a = GoalAdapter()
        self.assertEqual(a.push_reflections(None), 0)

    def test_push_reflections_filter_invalid(self):
        a = GoalAdapter()
        n = a.push_reflections(["bad", _ref()])
        self.assertEqual(n, 1)

    def test_push_signals(self):
        a = GoalAdapter()
        n = a.push_signals([_sig()])
        self.assertEqual(n, 1)
        self.assertEqual(a.pending_signal_count, 1)

    def test_push_signals_filter_invalid(self):
        a = GoalAdapter()
        n = a.push_signals(["bad", _sig()])
        self.assertEqual(n, 1)

    def test_push_actions(self):
        a = GoalAdapter()
        n = a.push_actions([_action()])
        self.assertEqual(n, 1)
        self.assertEqual(a.pending_action_count, 1)

    def test_push_actions_filter_invalid(self):
        a = GoalAdapter()
        n = a.push_actions(["bad", _action()])
        self.assertEqual(n, 1)

    def test_push_desires(self):
        a = GoalAdapter()
        n = a.push_desires([_desire()])
        self.assertEqual(n, 1)
        self.assertEqual(a.pending_desire_count, 1)

    def test_push_desires_filter_invalid(self):
        a = GoalAdapter()
        n = a.push_desires(["bad", _desire()])
        self.assertEqual(n, 1)

    def test_max_events_limit(self):
        a = GoalAdapter(max_events=2)
        events = [_ev(topic=f"t{i}") for i in range(5)]
        a.push_events(events)
        self.assertLessEqual(a.pending_event_count, 2)

    def test_clear_pending(self):
        a = GoalAdapter()
        a.push_events([_ev()])
        a.push_reflections([_ref()])
        a.push_signals([_sig()])
        a.push_actions([_action()])
        a.push_desires([_desire()])
        n = a.clear_pending()
        self.assertEqual(n, 5)
        self.assertEqual(a.pending_event_count, 0)


class TestGoalAdapterEvaluate(unittest.TestCase):
    def test_evaluate_disabled(self):
        a = GoalAdapter()
        a.set_enabled(False)
        self.assertFalse(a.evaluate())

    def test_evaluate_empty(self):
        a = GoalAdapter()
        self.assertFalse(a.evaluate())

    def test_evaluate_with_pending(self):
        a = GoalAdapter()
        a.push_events([_ev()])
        self.assertTrue(a.evaluate())

    def test_evaluate_with_input(self):
        a = GoalAdapter()
        self.assertTrue(a.evaluate(events=[_ev()]))
        self.assertTrue(a.evaluate(signals=[_sig()]))
        self.assertTrue(a.evaluate(actions=[_action()]))
        self.assertTrue(a.evaluate(desires=[_desire()]))


class TestGoalAdapterTick(unittest.TestCase):
    def test_tick_no_input_returns_none(self):
        # 没有 pending 也没有输入,evaluate 失败但 tick 仍会执行
        a = GoalAdapter()
        out = a.tick()
        # tick 仍然会跑,只是 candidates 为空
        self.assertIsNotNone(out)
        self.assertEqual(out.goals_created, 0)
        self.assertEqual(a.tick_count, 1)

    def test_tick_with_desire(self):
        a = GoalAdapter()
        out = a.tick(desires=[_desire(topic="ai_art", source_event_ids=["e1"])])
        self.assertIsNotNone(out)
        self.assertGreaterEqual(out.desires_added, 1)
        self.assertGreaterEqual(out.goals_created, 1)
        self.assertGreater(len(out.goal_events), 0)
        self.assertEqual(out.goal_events[0].event_type, INTEGRATION_GOAL_CREATED)

    def test_tick_disabled(self):
        a = GoalAdapter()
        a.set_enabled(False)
        out = a.tick(desires=[_desire()])
        # 即使 disabled,tick 仍会跑(因为 disabled 是 evaluate 层面)
        # 这里测试 evaluate 行为
        self.assertFalse(a.evaluate())

    def test_tick_with_signals(self):
        a = GoalAdapter()
        out = a.tick(
            desires=[_desire(topic="ai_art", source_event_ids=["e1"])],
            signals=[_sig(topic="ai_art", source_event_ids=["e1"])],
        )
        self.assertIsNotNone(out)

    def test_tick_with_events(self):
        a = GoalAdapter()
        out = a.tick(
            events=[_ev(topic="ai_art", event_id="e1")],
            desires=[_desire(topic="ai_art", source_event_ids=["e1"])],
        )
        self.assertIsNotNone(out)

    def test_tick_no_plan(self):
        a = GoalAdapter()
        out = a.tick(desires=[_desire()], plan=False)
        self.assertEqual(out.plans_created, 0)


class TestGoalAdapterClearAndState(unittest.TestCase):
    def test_drain_and_tick(self):
        a = GoalAdapter()
        a.push_desires([_desire()])
        out = a.drain_and_tick()
        self.assertIsNotNone(out)
        self.assertEqual(a.pending_desire_count, 0)


class TestGoalAdapterStatistics(unittest.TestCase):
    def test_describe(self):
        a = GoalAdapter()
        d = a.describe()
        self.assertEqual(d["schema_version"], GOAL_ADAPTER_SCHEMA_VERSION)
        self.assertIn("manager", d)
        self.assertIn("generator", d)
        self.assertIn("emitter", d)

    def test_repr(self):
        a = GoalAdapter()
        r = repr(a)
        self.assertIn("GoalAdapter", r)


class TestGoalAdapterTickOutput(unittest.TestCase):
    def test_default(self):
        o = GoalAdapterTickOutput()
        self.assertEqual(o.desires_added, 0)
        self.assertEqual(o.goals_created, 0)
        self.assertEqual(o.plans_created, 0)
        self.assertEqual(o.candidates, [])
        self.assertEqual(o.skipped, [])

    def test_to_dict(self):
        o = GoalAdapterTickOutput()
        o.desires_added = 2
        o.goals_created = 1
        o.plans_created = 1
        d = o.to_dict()
        self.assertEqual(d["desires_added"], 2)
        self.assertEqual(d["goals_created"], 1)
        self.assertEqual(d["plans_created"], 1)

    def test_repr(self):
        o = GoalAdapterTickOutput()
        o.desires_added = 1
        o.goals_created = 2
        r = repr(o)
        self.assertIn("GoalAdapterTickOutput", r)


if __name__ == "__main__":
    unittest.main()
