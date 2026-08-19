# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_strategies.py

Phase 5.0-D3-B: DailyReflection / EventReflection / GrowthReflection 单元测试。
"""
import unittest
from typing import List

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.reflection.daily_reflection import (
    DEFAULT_DAILY_WINDOW_SECONDS,
    PREFERENCE_FREQUENCY_THRESHOLD,
    DailyReflection,
)
from src.runtime.reflection.event_reflection import EventReflection
from src.runtime.reflection.growth_reflection import (
    DEFAULT_GROWTH_WINDOW_SECONDS,
    MIN_GROWTH_EVENTS,
    GrowthReflection,
)
from src.runtime.reflection.reflection_result import (
    INSIGHT_CATEGORY_CONCERN,
    INSIGHT_CATEGORY_GROWTH,
    INSIGHT_CATEGORY_PATTERN,
    INSIGHT_CATEGORY_PREFERENCE,
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


def _ev(et: str, ts: float, topic: str = "", trait: str = "", severity: str = "info", payload: dict = None) -> IntegrationEvent:
    p = dict(payload or {})
    if topic:
        p["topic"] = topic
    if trait:
        p["trait"] = trait
    return make_integration_event(
        event_type=et,
        source="test",
        severity=severity,
        payload=p,
    )


# ============================================================
# DailyReflection
# ============================================================
class TestDailyReflection(unittest.TestCase):
    def test_default_creation(self):
        d = DailyReflection()
        self.assertEqual(d.name, "daily_reflection")
        self.assertEqual(d.reflection_type, REFLECTION_TYPE_DAILY)
        self.assertEqual(d.window_seconds, DEFAULT_DAILY_WINDOW_SECONDS)
        self.assertEqual(d.preference_threshold, PREFERENCE_FREQUENCY_THRESHOLD)

    def test_custom_thresholds(self):
        d = DailyReflection(preference_threshold=2, pattern_threshold=4)
        self.assertEqual(d.preference_threshold, 2)
        self.assertEqual(d.pattern_threshold, 4)

    def test_invalid_thresholds(self):
        d = DailyReflection(preference_threshold=0, pattern_threshold=-1)
        self.assertEqual(d.preference_threshold, 1)
        self.assertEqual(d.pattern_threshold, 1)

    def test_should_run_empty(self):
        d = DailyReflection()
        ctx = ReflectionContext(clock_now=100, events=[])
        self.assertFalse(d.should_run(ctx))

    def test_should_run_with_events(self):
        d = DailyReflection()
        ctx = ReflectionContext(clock_now=100, events=[_ev("a", 100.0)])
        self.assertTrue(d.should_run(ctx))

    def test_reflect_empty(self):
        d = DailyReflection()
        ctx = ReflectionContext(clock_now=100, events=[])
        r = d.reflect(ctx)
        self.assertIsInstance(r, ReflectionResult)
        self.assertEqual(r.insight_count, 0)
        self.assertEqual(r.suggestion_count, 0)

    def test_reflect_invalid_context(self):
        d = DailyReflection()
        r = d.reflect("not a context")  # type: ignore[arg-type]
        self.assertIsInstance(r, ReflectionResult)

    def test_reflect_below_threshold(self):
        d = DailyReflection()
        events = [_ev("a", 100.0)]  # 只 1 个事件
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        self.assertEqual(r.insight_count, 0)

    def test_reflect_pattern_insight(self):
        d = DailyReflection(pattern_threshold=3, preference_threshold=2)
        events = [_ev("a", 100.0) for _ in range(5)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        self.assertGreater(r.insight_count, 0)
        # 至少 1 个 pattern insight
        cats = [i.category for i in r.insights]
        self.assertIn(INSIGHT_CATEGORY_PATTERN, cats)

    def test_reflect_preference_insight(self):
        d = DailyReflection(preference_threshold=2, pattern_threshold=10)
        events = [_ev("a", 100.0) for _ in range(3)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        self.assertGreater(r.insight_count, 0)
        cats = [i.category for i in r.insights]
        self.assertIn(INSIGHT_CATEGORY_PREFERENCE, cats)

    def test_reflect_topic_preference(self):
        d = DailyReflection(preference_threshold=2)
        events = [_ev("x", 100.0, topic="ai_art") for _ in range(3)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        self.assertGreater(r.insight_count, 0)
        # 至少一个 insight 描述 ai_art
        self.assertTrue(any("ai_art" in i.description for i in r.insights))

    def test_reflect_interest_suggestion(self):
        d = DailyReflection(preference_threshold=2)
        events = [_ev("x", 100.0, topic="ai_art") for _ in range(3)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        # 至少一个 interest suggestion
        sug_fields = [s.target_field for s in r.suggested_changes]
        self.assertTrue(any("interest_state.ai_art" in f for f in sug_fields))

    def test_reflect_trait_suggestion(self):
        d = DailyReflection(preference_threshold=2)
        events = [_ev("x", 100.0, trait="curious") for _ in range(3)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        sug_fields = [s.target_field for s in r.suggested_changes]
        self.assertTrue(any("trait_state.curious" in f for f in sug_fields))

    def test_all_suggestions_have_evidence(self):
        d = DailyReflection(preference_threshold=2)
        events = [_ev("x", 100.0, topic="ai_art", trait="curious") for _ in range(3)]
        ctx = ReflectionContext(clock_now=100, events=events)
        r = d.reflect(ctx)
        for s in r.suggested_changes:
            self.assertGreater(len(s.evidence_event_ids), 0)

    def test_window_set(self):
        d = DailyReflection()
        events = [_ev("x", 100.0) for _ in range(3)]
        ctx = ReflectionContext(
            clock_now=200.0,
            window_start=0.0,
            window_end=200.0,
            events=events,
        )
        r = d.reflect(ctx)
        self.assertEqual(r.window_start, 0.0)
        self.assertEqual(r.window_end, 200.0)

    def test_source_event_ids(self):
        d = DailyReflection()
        events = [_ev("x", 100.0 + i) for i in range(3)]
        ctx = ReflectionContext(clock_now=200.0, events=events)
        r = d.reflect(ctx)
        self.assertEqual(len(r.source_event_ids), 3)

    def test_confidence_calc(self):
        d = DailyReflection()
        events = [_ev("x", 100.0 + i) for i in range(20)]
        ctx = ReflectionContext(clock_now=200.0, events=events)
        r = d.reflect(ctx)
        self.assertGreater(r.confidence, 0.0)


# ============================================================
# EventReflection
# ============================================================
class TestEventReflection(unittest.TestCase):
    def test_default_creation(self):
        e = EventReflection()
        self.assertEqual(e.name, "event_reflection")
        self.assertEqual(e.reflection_type, REFLECTION_TYPE_EVENT)

    def test_custom_important_severities(self):
        e = EventReflection(important_severities=["warning"])
        self.assertIn("warning", e.important_severities)

    def test_should_run_empty(self):
        e = EventReflection()
        ctx = ReflectionContext(clock_now=100, events=[])
        self.assertFalse(e.should_run(ctx))

    def test_should_run_with_event(self):
        e = EventReflection()
        ctx = ReflectionContext(clock_now=100, events=[_ev("a", 100.0)])
        self.assertTrue(e.should_run(ctx))

    def test_reflect_empty(self):
        e = EventReflection()
        ctx = ReflectionContext(clock_now=100, events=[])
        r = e.reflect(ctx)
        self.assertEqual(r.insight_count, 0)

    def test_reflect_invalid_context(self):
        e = EventReflection()
        r = e.reflect("not a context")  # type: ignore[arg-type]
        self.assertIsInstance(r, ReflectionResult)

    def test_reflect_info_event(self):
        e = EventReflection()
        ev = _ev("user.input", 100.0, topic="greeting")
        ctx = ReflectionContext(clock_now=100, events=[ev])
        r = e.reflect(ctx)
        self.assertGreater(r.insight_count, 0)
        cats = [i.category for i in r.insights]
        self.assertIn(INSIGHT_CATEGORY_PATTERN, cats)

    def test_reflect_critical_event_has_concern(self):
        e = EventReflection()
        ev = _ev("system.failure", 100.0, severity="critical")
        ctx = ReflectionContext(clock_now=100, events=[ev])
        r = e.reflect(ctx)
        cats = [i.category for i in r.insights]
        self.assertIn(INSIGHT_CATEGORY_CONCERN, cats)

    def test_reflect_warning_event_has_capability_suggestion(self):
        e = EventReflection()
        ev = _ev("user.error", 100.0, severity="warning")
        ctx = ReflectionContext(clock_now=100, events=[ev])
        r = e.reflect(ctx)
        # 至少一个 capability suggestion
        sug_fields = [s.target_field for s in r.suggested_changes]
        self.assertTrue(any("capability_state" in f for f in sug_fields))

    def test_reflect_growth_event(self):
        e = EventReflection()
        ev = _ev("user.level_up", 100.0, topic="learning", severity="notice")
        ctx = ReflectionContext(clock_now=100, events=[ev])
        r = e.reflect(ctx)
        # 包含 growth insight
        cats = [i.category for i in r.insights]
        self.assertIn(INSIGHT_CATEGORY_GROWTH, cats)

    def test_all_suggestions_have_evidence(self):
        e = EventReflection()
        ev = _ev("user.error", 100.0, severity="warning")
        ctx = ReflectionContext(clock_now=100, events=[ev])
        r = e.reflect(ctx)
        for s in r.suggested_changes:
            self.assertGreater(len(s.evidence_event_ids), 0)

    def test_pick_most_severe(self):
        e = EventReflection()
        ev1 = _ev("a", 100.0, severity="info")
        ev2 = _ev("b", 101.0, severity="critical")
        ev3 = _ev("c", 102.0, severity="warning")
        ctx = ReflectionContext(clock_now=200, events=[ev1, ev2, ev3])
        r = e.reflect(ctx)
        # 选最严重的: critical
        self.assertEqual(len(r.insights) > 0, True)
        # 描述应提到 critical
        self.assertTrue(any("critical" in i.description for i in r.insights))

    def test_is_important(self):
        e = EventReflection()
        ev1 = _ev("a", 100.0, severity="info")
        ev2 = _ev("b", 100.0, severity="critical")
        ev3 = _ev("c", 100.0, payload={"important": True})
        self.assertFalse(e.is_important(ev1))
        self.assertTrue(e.is_important(ev2))
        self.assertTrue(e.is_important(ev3))


# ============================================================
# GrowthReflection
# ============================================================
class TestGrowthReflection(unittest.TestCase):
    def test_default_creation(self):
        g = GrowthReflection()
        self.assertEqual(g.name, "growth_reflection")
        self.assertEqual(g.reflection_type, REFLECTION_TYPE_GROWTH)
        self.assertEqual(g.window_seconds, DEFAULT_GROWTH_WINDOW_SECONDS)

    def test_custom_buckets(self):
        g = GrowthReflection(num_buckets=14)
        self.assertEqual(g.num_buckets, 14)

    def test_invalid_buckets(self):
        g = GrowthReflection(num_buckets=0)
        self.assertEqual(g.num_buckets, 2)
        g = GrowthReflection(num_buckets=200)
        self.assertEqual(g.num_buckets, 64)

    def test_should_run_below_min_events(self):
        g = GrowthReflection()
        events = [_ev("a", 100.0 + i) for i in range(3)]
        ctx = ReflectionContext(clock_now=200, events=events)
        self.assertFalse(g.should_run(ctx))

    def test_should_run_short_window(self):
        g = GrowthReflection()
        events = [_ev("a", 100.0 + i) for i in range(20)]
        ctx = ReflectionContext(clock_now=200, events=events)
        # window_seconds = 19 < 7 天
        self.assertFalse(g.should_run(ctx))

    def test_should_run_with_long_window(self):
        g = GrowthReflection()
        events = [_ev("a", 100.0 + i, topic="t") for i in range(20)]
        # 7 天 = 604800
        ctx = ReflectionContext(
            clock_now=200.0,
            window_start=0.0,
            window_end=604800.0,
            events=events,
        )
        self.assertTrue(g.should_run(ctx))

    def test_reflect_empty(self):
        g = GrowthReflection()
        ctx = ReflectionContext(clock_now=100, events=[])
        r = g.reflect(ctx)
        self.assertEqual(r.insight_count, 0)

    def test_reflect_invalid_context(self):
        g = GrowthReflection()
        r = g.reflect("not a context")  # type: ignore[arg-type]
        self.assertIsInstance(r, ReflectionResult)

    def test_reflect_growing_trend(self):
        g = GrowthReflection(num_buckets=4)
        # 在前 2 桶各 1 个,后 2 桶各 10 个 → 增长
        events: List[IntegrationEvent] = []
        for i in range(2):
            events.append(_ev("a", 100.0 + i * 1000, topic="t"))
        for i in range(20):
            events.append(_ev("a", 500000.0 + i * 1000, topic="t"))
        ctx = ReflectionContext(
            clock_now=600000.0,
            window_start=0.0,
            window_end=600000.0,
            events=events,
        )
        r = g.reflect(ctx)
        # 至少有 trend insight
        self.assertGreater(r.insight_count, 0)

    def test_reflect_decaying_trend(self):
        g = GrowthReflection(num_buckets=4)
        events: List[IntegrationEvent] = []
        for i in range(20):
            events.append(_ev("a", 100.0 + i * 1000, topic="t"))
        for i in range(2):
            events.append(_ev("a", 500000.0 + i * 1000, topic="t"))
        ctx = ReflectionContext(
            clock_now=600000.0,
            window_start=0.0,
            window_end=600000.0,
            events=events,
        )
        r = g.reflect(ctx)
        self.assertGreater(r.insight_count, 0)

    def test_all_suggestions_have_evidence(self):
        g = GrowthReflection(num_buckets=4)
        events: List[IntegrationEvent] = []
        for i in range(2):
            events.append(_ev("a", 100.0 + i * 1000, topic="t"))
        for i in range(20):
            events.append(_ev("a", 500000.0 + i * 1000, topic="t"))
        ctx = ReflectionContext(
            clock_now=600000.0,
            window_start=0.0,
            window_end=600000.0,
            events=events,
        )
        r = g.reflect(ctx)
        for s in r.suggested_changes:
            self.assertGreater(len(s.evidence_event_ids), 0)


# ============================================================
# BaseReflectionStrategy
# ============================================================
class TestBaseReflectionStrategy(unittest.TestCase):
    def test_default_name_and_type(self):
        b = BaseReflectionStrategy()
        self.assertEqual(b.name, "base_strategy")
        self.assertEqual(b.reflection_type, "daily")

    def test_should_run_no_events(self):
        b = BaseReflectionStrategy()
        ctx = ReflectionContext(clock_now=100, events=[])
        self.assertFalse(b.should_run(ctx))

    def test_should_run_with_events(self):
        b = BaseReflectionStrategy()
        ctx = ReflectionContext(clock_now=100, events=[_ev("a", 100.0)])
        self.assertTrue(b.should_run(ctx))

    def test_reflect_not_implemented(self):
        b = BaseReflectionStrategy()
        with self.assertRaises(NotImplementedError):
            b.reflect(ReflectionContext())


if __name__ == "__main__":
    unittest.main()
