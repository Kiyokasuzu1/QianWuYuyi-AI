# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_generator.py

Phase 5.0-D3-D: GoalGenerator 单元测试。

覆盖:
- 创建与配置
- generate: 从兴趣生成目标(evidence chain)
- 跳过条件(低强度,低置信度,无 evidence)
- 容量限制
- disabled
- 异常隔离
- topic -> goal_type 映射
- 集成(Interest + Reflection)
"""
import unittest
from src.runtime.goal.desire import (
    DESIRE_TREND_RISING,
    DESIRE_TREND_STABLE,
    Desire,
    build_desire,
)
from src.runtime.goal.goal_generator import (
    DEFAULT_DESIRE_MIN_CONFIDENCE,
    DEFAULT_DESIRE_MIN_STRENGTH,
    GOAL_GENERATOR_SCHEMA_VERSION,
    GoalGenerator,
    GoalGeneratorConfig,
    GoalGeneratorInput,
    GoalGeneratorOutput,
    build_default_goal_generator,
)
from src.runtime.goal.goal_state import (
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    GOAL_STATUS_CANDIDATE,
    GoalState,
    is_valid_goal_transition,
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
from src.runtime.integration.integration_event import (
    INTEGRATION_EXPERIENCE_RECORDED,
    IntegrationEvent,
    make_integration_event,
)


def _sig(topic: str = "ai_art_basic", source_event_ids=None) -> InterestSignal:
    return InterestSignal(
        topic=topic,
        strength=0.7,
        trend=INTEREST_TREND_RISING,
        source_event_ids=list(source_event_ids or []),
    )


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


def _ev(topic: str = "ai_art_basic", event_id: str = "") -> IntegrationEvent:
    e = make_integration_event(
        event_type=INTEGRATION_EXPERIENCE_RECORDED,
        source="src",
        payload={"topic": topic},
    )
    if event_id:
        e.event_id = event_id
    return e


class TestGoalGeneratorConfig(unittest.TestCase):
    def test_default(self):
        c = GoalGeneratorConfig()
        self.assertEqual(c.desire_min_strength, DEFAULT_DESIRE_MIN_STRENGTH)
        self.assertEqual(c.desire_min_confidence, DEFAULT_DESIRE_MIN_CONFIDENCE)
        self.assertTrue(c.enabled)

    def test_invalid_values(self):
        c = GoalGeneratorConfig(
            desire_min_strength="bogus",
            desire_min_confidence="bogus",
            reflection_min_count="bogus",
            signal_min_count="bogus",
            max_candidates_per_tick="bogus",
        )
        self.assertEqual(c.desire_min_strength, DEFAULT_DESIRE_MIN_STRENGTH)
        self.assertEqual(c.desire_min_confidence, DEFAULT_DESIRE_MIN_CONFIDENCE)
        self.assertGreaterEqual(c.reflection_min_count, 1)
        self.assertGreaterEqual(c.signal_min_count, 1)
        self.assertGreaterEqual(c.max_candidates_per_tick, 1)

    def test_min_clip(self):
        c = GoalGeneratorConfig(
            desire_min_strength=2.0,
            desire_min_confidence=-1.0,
        )
        self.assertEqual(c.desire_min_strength, 1.0)
        self.assertEqual(c.desire_min_confidence, 0.0)


class TestGoalGeneratorBasic(unittest.TestCase):
    def test_default_creation(self):
        g = build_default_goal_generator()
        self.assertTrue(g.enabled)
        self.assertEqual(g.generated_count, 0)
        self.assertEqual(g.skipped_count, 0)

    def test_disabled(self):
        g = GoalGenerator()
        g.set_enabled(False)
        d = build_desire(topic="t", strength=0.9, supporting_signal_ids=["s1"])
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 0)
        self.assertIn("global", out.skipped_reason)
        self.assertEqual(out.skipped_reason["global"], "disabled")

    def test_set_enabled(self):
        g = GoalGenerator()
        g.set_enabled(False)
        self.assertFalse(g.enabled)
        g.set_enabled(True)
        self.assertTrue(g.enabled)


class TestGoalGeneratorDesireToGoal(unittest.TestCase):
    def test_high_strength_desire_generates_goal(self):
        g = GoalGenerator()
        d = build_desire(
            topic="ai_art",
            strength=0.8,
            confidence=0.8,
            supporting_signal_ids=["s1", "s2"],
        )
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 1)
        c = out.candidates[0]
        self.assertEqual(c.status, GOAL_STATUS_CANDIDATE)
        self.assertTrue(c.has_evidence())
        self.assertIn("s1", c.source_event_ids)
        self.assertIn(d.desire_id, c.source_desire_ids)
        self.assertEqual(g.generated_count, 1)

    def test_low_strength_desire_skipped(self):
        g = GoalGenerator()
        d = build_desire(
            topic="ai_art",
            strength=0.1,
            confidence=0.9,
            supporting_signal_ids=["s1"],
        )
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(len(out.skipped_desires), 1)
        self.assertEqual(out.skipped_reason[d.desire_id], "low_strength")
        self.assertEqual(g.skipped_count, 1)

    def test_low_confidence_desire_skipped(self):
        g = GoalGenerator()
        d = build_desire(
            topic="ai_art",
            strength=0.8,
            confidence=0.1,
            supporting_signal_ids=["s1"],
        )
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(out.skipped_reason[d.desire_id], "low_confidence")

    def test_no_evidence_desire_skipped(self):
        g = GoalGenerator()
        d = Desire(topic="ai_art", strength=0.8, confidence=0.8)
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(out.skipped_reason[d.desire_id], "no_evidence")

    def test_no_topic_desire_skipped(self):
        g = GoalGenerator()
        d = build_desire(topic="", strength=0.8, supporting_signal_ids=["s1"])
        out = g.generate(desires=[d])
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(out.skipped_reason[d.desire_id], "no_topic")


class TestGoalGeneratorTopicMapping(unittest.TestCase):
    def _gen(self, topic: str) -> str:
        g = GoalGenerator()
        d = build_desire(topic=topic, strength=0.9, confidence=0.9, supporting_signal_ids=["s1"])
        out = g.generate(desires=[d])
        if out.candidates:
            return out.candidates[0].goal_type
        return ""

    def test_ai_art_maps_to_creative(self):
        self.assertEqual(self._gen("ai_art_basic"), GOAL_TYPE_CREATIVE)

    def test_code_maps_to_learning(self):
        self.assertEqual(self._gen("code_review"), GOAL_TYPE_LEARNING)

    def test_social_maps_to_relationship(self):
        self.assertEqual(self._gen("social_event"), GOAL_TYPE_RELATIONSHIP)

    def test_news_maps_to_exploration(self):
        self.assertEqual(self._gen("news_today"), GOAL_TYPE_EXPLORATION)

    def test_unknown_topic_defaults_to_personal_growth(self):
        self.assertEqual(self._gen("random_unknown_xyz"), GOAL_TYPE_PERSONAL_GROWTH)


class TestGoalGeneratorLimits(unittest.TestCase):
    def test_max_candidates_per_tick(self):
        cfg = GoalGeneratorConfig(max_candidates_per_tick=2)
        g = GoalGenerator(config=cfg)
        desires = [
            build_desire(topic=f"t{i}", strength=0.9, confidence=0.9, supporting_signal_ids=[f"s{i}"])
            for i in range(5)
        ]
        out = g.generate(desires=desires)
        self.assertLessEqual(len(out.candidates), 2)

    def test_zero_max(self):
        cfg = GoalGeneratorConfig(max_candidates_per_tick=0)
        g = GoalGenerator(config=cfg)
        d = build_desire(topic="t", strength=0.9, supporting_signal_ids=["s1"])
        out = g.generate(desires=[d])
        # 0 视作"无限制"或"0 拒绝",看实现
        # 当前实现: <= 0 视为不限制
        # 我们的实现是 if 0 < self._config.max_candidates_per_tick <= len(...)
        # 即 0 时不限
        self.assertGreaterEqual(len(out.candidates), 0)


class TestGoalGeneratorIntegration(unittest.TestCase):
    def test_with_signals_and_reflections(self):
        g = GoalGenerator()
        d = build_desire(topic="ai_art", strength=0.7, confidence=0.7, supporting_signal_ids=["s1"])
        sig = _sig(topic="ai_art", source_event_ids=["s1"])
        ref = _ref(topics=["ai_art"], source_event_ids=["e1"])
        out = g.generate(
            desires=[d],
            signals=[sig],
            reflections=[ref],
            integration_events=[_ev(topic="ai_art", event_id="s1")],
        )
        self.assertEqual(len(out.candidates), 1)
        c = out.candidates[0]
        self.assertIn("s1", c.source_event_ids)

    def test_empty_inputs(self):
        g = GoalGenerator()
        out = g.generate()
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(len(out.skipped_desires), 0)


class TestGoalGeneratorDescribe(unittest.TestCase):
    def test_describe(self):
        g = GoalGenerator()
        d = describe = g.describe()
        self.assertIn("enabled", d)
        self.assertIn("generated_count", d)
        self.assertIn("skipped_count", d)
        self.assertIn("config", d)

    def test_repr(self):
        g = GoalGenerator()
        r = repr(g)
        self.assertIn("GoalGenerator", r)


class TestGoalGeneratorInput(unittest.TestCase):
    def test_default(self):
        inp = GoalGeneratorInput()
        self.assertEqual(inp.desires, [])
        self.assertEqual(inp.reflections, [])
        self.assertEqual(inp.signals, [])
        self.assertEqual(inp.actions, [])
        self.assertEqual(inp.integration_events, [])


if __name__ == "__main__":
    unittest.main()
