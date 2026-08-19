# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_engine.py

Phase 5.0-D3-B: ReflectionEngine 单元测试。
"""
import unittest

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.lifecycle.internal.clock import FrozenClock, MockClock
from src.runtime.reflection.daily_reflection import DailyReflection
from src.runtime.reflection.event_reflection import EventReflection
from src.runtime.reflection.growth_reflection import GrowthReflection
from src.runtime.reflection.reflection_engine import (
    DEFAULT_DAILY_WINDOW_SECONDS,
    DEFAULT_GROWTH_WINDOW_SECONDS,
    REFLECTION_ENGINE_SCHEMA_VERSION,
    ReflectionEngine,
    ReflectionEngineError,
    build_default_reflection_engine,
)
from src.runtime.reflection.reflection_history import ReflectionHistory
from src.runtime.reflection.reflection_result import (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
)
from src.runtime.reflection.reflection_strategy import (
    BaseReflectionStrategy,
    ReflectionContext,
)


def _ev(et: str, ts: float, topic: str = "", severity: str = "info") -> IntegrationEvent:
    p = {}
    if topic:
        p["topic"] = topic
    return IntegrationEvent(
        event_type=et,
        source="test",
        severity=severity,
        timestamp=ts,
        payload=p,
    )


class _NullStrategy(BaseReflectionStrategy):
    """不产出任何 insight 的策略(用于测试错误隔离)。"""
    def __init__(self, name: str = "null", rtype: str = "daily"):
        super().__init__(name=name, reflection_type=rtype)
        self._raise = False

    def should_run(self, context):
        return context.event_count > 0

    def reflect(self, context):
        if self._raise:
            raise RuntimeError("strategy failed")
        return ReflectionResult(reflection_type=self._reflection_type)


class _ErrorStrategy(BaseReflectionStrategy):
    """会抛错的策略。"""
    def __init__(self):
        super().__init__(name="error", reflection_type="event")

    def should_run(self, context):
        return context.event_count > 0

    def reflect(self, context):
        raise RuntimeError("intentional error")


class TestReflectionEngineCreation(unittest.TestCase):
    def test_default_creation(self):
        e = ReflectionEngine()
        self.assertEqual(e.name, "reflection_engine")
        self.assertEqual(e.daily_window_seconds, DEFAULT_DAILY_WINDOW_SECONDS)
        self.assertEqual(e.growth_window_seconds, DEFAULT_GROWTH_WINDOW_SECONDS)
        self.assertEqual(len(e.strategies), 3)  # Daily, Event, Growth

    def test_invalid_clock(self):
        with self.assertRaises(ReflectionEngineError):
            ReflectionEngine(clock="not a clock")

    def test_invalid_history(self):
        with self.assertRaises(ReflectionEngineError):
            ReflectionEngine(history="not a history")

    def test_with_frozen_clock(self):
        clk = FrozenClock(initial=100.0, frozen=True)
        e = ReflectionEngine(clock=clk)
        self.assertEqual(e.clock.now(), 100.0)

    def test_with_custom_strategies(self):
        e = ReflectionEngine(strategies=[DailyReflection(), EventReflection()])
        self.assertEqual(len(e.strategies), 2)

    def test_invalid_strategies_filtered(self):
        # 不可调用 reflect 的对象被过滤
        e = ReflectionEngine(strategies=["bad", None, 123, DailyReflection()])
        self.assertEqual(len(e.strategies), 1)

    def test_with_custom_history(self):
        h = ReflectionHistory(capacity=5)
        e = ReflectionEngine(history=h)
        self.assertIs(e.history, h)


class TestReflectionEngineEvaluate(unittest.TestCase):
    def setUp(self):
        self.engine = ReflectionEngine()

    def test_evaluate_no_events(self):
        self.assertEqual(self.engine.evaluate([]), [])

    def test_evaluate_none(self):
        self.assertEqual(self.engine.evaluate(None), [])  # type: ignore[arg-type]

    def test_evaluate_event(self):
        events = [_ev("a", 100.0)]
        chosen = self.engine.evaluate(events)
        self.assertIn(REFLECTION_TYPE_EVENT, chosen)

    def test_evaluate_daily_window(self):
        events = [_ev("a", 0.0), _ev("a", DEFAULT_DAILY_WINDOW_SECONDS)]
        chosen = self.engine.evaluate(events)
        self.assertIn(REFLECTION_TYPE_DAILY, chosen)

    def test_evaluate_growth_window(self):
        events = [
            _ev("a", 0.0),
            _ev("a", DEFAULT_GROWTH_WINDOW_SECONDS),
        ]
        chosen = self.engine.evaluate(events)
        self.assertIn(REFLECTION_TYPE_GROWTH, chosen)

    def test_evaluate_short_window_only_event(self):
        events = [_ev("a", 0.0), _ev("a", 1.0)]
        chosen = self.engine.evaluate(events)
        self.assertIn(REFLECTION_TYPE_EVENT, chosen)
        self.assertNotIn(REFLECTION_TYPE_DAILY, chosen)
        self.assertNotIn(REFLECTION_TYPE_GROWTH, chosen)


class TestReflectionEngineShouldReflect(unittest.TestCase):
    def test_no_events(self):
        e = ReflectionEngine()
        self.assertFalse(e.should_reflect([]))

    def test_with_events(self):
        e = ReflectionEngine()
        self.assertTrue(e.should_reflect([_ev("a", 100.0)]))


class TestReflectionEngineReflect(unittest.TestCase):
    def test_empty_events(self):
        e = ReflectionEngine()
        r = e.reflect([])
        self.assertEqual(r, [])

    def test_single_event_runs_event_strategy(self):
        e = ReflectionEngine()
        results = e.reflect([_ev("a", 100.0)])
        # 至少 1 个结果 (event strategy)
        self.assertGreaterEqual(len(results), 1)
        types = [r.reflection_type for r in results]
        self.assertIn(REFLECTION_TYPE_EVENT, types)

    def test_long_window_runs_daily(self):
        e = ReflectionEngine()
        events = [_ev("a", 100.0 + i * 100) for i in range(20)]
        # 模拟 24h+ 窗口
        events[0] = _ev("a", 0.0)
        events[-1] = _ev("a", DEFAULT_DAILY_WINDOW_SECONDS + 100)
        results = e.reflect(events)
        types = [r.reflection_type for r in results]
        self.assertIn(REFLECTION_TYPE_DAILY, types)

    def test_reflect_specific_type(self):
        e = ReflectionEngine()
        results = e.reflect([_ev("a", 100.0)], reflection_type=REFLECTION_TYPE_EVENT)
        for r in results:
            self.assertEqual(r.reflection_type, REFLECTION_TYPE_EVENT)

    def test_reflect_specific_type_with_no_match(self):
        e = ReflectionEngine()
        results = e.reflect([_ev("a", 100.0)], reflection_type=REFLECTION_TYPE_GROWTH)
        self.assertEqual(results, [])

    def test_total_runs_incremented(self):
        e = ReflectionEngine()
        e.reflect([_ev("a", 100.0)])
        self.assertEqual(e.total_runs, 1)
        e.reflect([_ev("a", 200.0)])
        self.assertEqual(e.total_runs, 2)

    def test_total_results_incremented(self):
        e = ReflectionEngine()
        results = e.reflect([_ev("a", 100.0)])
        self.assertEqual(e.total_results, len(results))

    def test_last_result_set(self):
        e = ReflectionEngine()
        results = e.reflect([_ev("a", 100.0)])
        self.assertIsNotNone(e.last_result)
        self.assertEqual(e.last_result.reflection_id, results[-1].reflection_id)

    def test_history_written(self):
        e = ReflectionEngine()
        e.reflect([_ev("a", 100.0)])
        self.assertGreater(e.history.size, 0)

    def test_deterministic_same_input(self):
        e1 = ReflectionEngine(clock=MockClock(now_fn=lambda: 100.0))
        e2 = ReflectionEngine(clock=MockClock(now_fn=lambda: 100.0))
        events = [_ev("a", 50.0 + i) for i in range(5)]
        r1 = e1.reflect(events)
        r2 = e2.reflect(events)
        self.assertEqual(len(r1), len(r2))
        for a, b in zip(r1, r2):
            self.assertEqual(a.reflection_type, b.reflection_type)
            self.assertEqual(a.confidence, b.confidence)
            self.assertEqual(a.insight_count, b.insight_count)


class TestReflectionEngineErrorIsolation(unittest.TestCase):
    def test_strategy_error_isolated(self):
        e = ReflectionEngine(strategies=[_ErrorStrategy(), DailyReflection()])
        # 不会抛错
        results = e.reflect([_ev("a", 100.0)])
        # 至少 Daily 仍能跑
        # total_errors 应 >= 1
        self.assertGreater(e.total_errors, 0)

    def test_total_errors_incremented(self):
        e = ReflectionEngine(strategies=[_ErrorStrategy()])
        e.reflect([_ev("a", 100.0)])
        self.assertGreaterEqual(e.total_errors, 1)
        self.assertNotEqual(e.last_error, "")


class TestReflectionEngineConfiguration(unittest.TestCase):
    def test_add_strategy(self):
        e = ReflectionEngine()
        before = len(e.strategies)
        self.assertTrue(e.add_strategy(_NullStrategy()))
        self.assertEqual(len(e.strategies), before + 1)

    def test_add_invalid_strategy(self):
        e = ReflectionEngine()
        self.assertFalse(e.add_strategy(None))
        self.assertFalse(e.add_strategy("bad"))

    def test_remove_strategy(self):
        e = ReflectionEngine(strategies=[_NullStrategy("my_strategy")])
        self.assertTrue(e.remove_strategy("my_strategy"))
        self.assertEqual(len(e.strategies), 0)

    def test_remove_nonexistent(self):
        e = ReflectionEngine()
        self.assertFalse(e.remove_strategy("not exist"))
        self.assertFalse(e.remove_strategy(""))

    def test_set_clock(self):
        e = ReflectionEngine()
        new_clk = MockClock(now_fn=lambda: 200.0)
        self.assertTrue(e.set_clock(new_clk))
        self.assertEqual(e.clock.now(), 200.0)

    def test_set_invalid_clock(self):
        e = ReflectionEngine()
        self.assertFalse(e.set_clock("bad"))


class TestReflectionEngineReflectOne(unittest.TestCase):
    def test_reflect_one(self):
        e = ReflectionEngine()
        strat = DailyReflection()
        result = e.reflect_one(strat, [_ev("a", 100.0 + i, topic="t") for i in range(3)])
        # daily 至少 1 个 insight
        self.assertIsInstance(result, ReflectionResult)
        self.assertGreater(result.insight_count, 0)

    def test_reflect_one_none(self):
        e = ReflectionEngine()
        self.assertIsNone(e.reflect_one(None, [_ev("a", 100.0)]))

    def test_reflect_one_no_events(self):
        e = ReflectionEngine()
        strat = DailyReflection(preference_threshold=10)
        result = e.reflect_one(strat, [_ev("a", 100.0)])
        # 事件数 < 阈值,should_run 可能为 True(因为 >=1), 但 insight 可能为 0
        # 此处仅验证返回是合法的 ReflectionResult
        self.assertIsNotNone(result)


class TestReflectionEngineDescribe(unittest.TestCase):
    def test_describe(self):
        e = ReflectionEngine()
        d = e.describe()
        self.assertEqual(d["name"], "reflection_engine")
        self.assertEqual(d["schema_version"], REFLECTION_ENGINE_SCHEMA_VERSION)
        self.assertEqual(d["strategy_count"], 3)


class TestFactory(unittest.TestCase):
    def test_build_default(self):
        e = build_default_reflection_engine()
        self.assertIsInstance(e, ReflectionEngine)
        self.assertEqual(e.history.capacity, 128)


if __name__ == "__main__":
    unittest.main()
