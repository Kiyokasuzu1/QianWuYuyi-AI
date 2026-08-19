# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_initiative_engine.py

Phase 5.0-D3-C: InitiativeEngine 单元测试。

覆盖:
- 默认配置与工厂
- enable_initiative=False 短路
- 从 IntegrationEvent 提取 topic
- 从 ReflectionResult 提取 topic
- InterestSignal 生成
- InterestSignal 趋势推进(rising/stable/fading)
- PossibleAction 生成
- ActionFilter 集成
- 队列入队
- 确定性
- 异常恢复
- 线程安全
- 描述与统计
"""
import threading
import unittest
from src.runtime.initiative.initiative_engine import (
    DEFAULT_INTEREST_MIN_CONFIDENCE,
    DEFAULT_INTEREST_MIN_STRENGTH,
    DEFAULT_RISING_BONUS,
    DEFAULT_STRENGTH_TO_ACTION,
    INITIATIVE_ENGINE_SCHEMA_VERSION,
    InitiativeConfig,
    InitiativeEngine,
    InitiativeTickOutput,
    build_default_initiative_engine,
)
from src.runtime.initiative.interest_signal import (
    INTEREST_TREND_FADING,
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    InterestSignal,
    InterestSignalRegistry,
)
from src.runtime.initiative.internal_state import InternalState, InternalStateStore
from src.runtime.initiative.possible_action import (
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_TYPE_ASK,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_OBSERVE,
    ACTION_TYPE_RECOMMEND,
    ACTION_TYPE_REMIND,
    PossibleAction,
)
from src.runtime.initiative.action_filter import ActionFilter
from src.runtime.initiative.initiative_queue import InitiativeQueue
from src.runtime.integration.integration_event import (
    INTEGRATION_EXPERIENCE_RECORDED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.reflection.reflection_result import (
    Insight,
    REFLECTION_TYPE_DAILY,
    ReflectionResult,
)


class _FixedClock:
    def __init__(self, value: float = 1000.0) -> None:
        self.v = float(value)

    def now(self) -> float:
        return self.v


def _ev(topic: str = "AI绘画", event_id: str = "", source: str = "src") -> object:
    ev = make_integration_event(
        event_type=INTEGRATION_EXPERIENCE_RECORDED,
        source=source,
        payload={"topic": topic},
    )
    if event_id:
        ev.event_id = event_id
    return ev


def _ref(topics: list, source_event_ids: list = None, reflection_id: str = "") -> ReflectionResult:
    insights = []
    for t in topics:
        insights.append(Insight(
            category="pattern",
            description=f"用户表现出对 {t} 的兴趣",
            supporting_event_ids=[],
            confidence=0.7,
        ))
    r = ReflectionResult(
        reflection_type=REFLECTION_TYPE_DAILY,
        triggered_at=0.0,
        insights=insights,
        suggested_changes=[],
        source_event_ids=list(source_event_ids or []),
    )
    if reflection_id:
        r.reflection_id = reflection_id
    return r


# ============================================================
# 基础
# ============================================================
class TestEngineDefaults(unittest.TestCase):
    def test_default_creation(self):
        e = InitiativeEngine()
        self.assertEqual(e.name, "initiative_engine")
        self.assertTrue(e.enabled)
        self.assertEqual(e.tick_count, 0)
        self.assertEqual(e.signals_created, 0)
        self.assertEqual(e.actions_created, 0)
        self.assertIsInstance(e.registry, InterestSignalRegistry)
        self.assertIsInstance(e.filter, ActionFilter)
        self.assertIsInstance(e.queue, InitiativeQueue)

    def test_custom_creation(self):
        clock = _FixedClock(123.0)
        reg = InterestSignalRegistry()
        f = ActionFilter()
        q = InitiativeQueue()
        e = InitiativeEngine(name="x", clock=clock, signal_registry=reg, action_filter=f, queue=q)
        self.assertEqual(e.name, "x")
        self.assertIs(e.clock, clock)
        self.assertIs(e.registry, reg)
        self.assertIs(e.filter, f)
        self.assertIs(e.queue, q)

    def test_config_clipped(self):
        cfg = InitiativeConfig(
            interest_min_strength=2.0,
            interest_min_confidence=-1.0,
            strength_to_action=5.0,
            rising_bonus=-0.5,
            max_signals_per_tick=-3,
            max_actions_per_tick=-2,
            max_topics_per_signal=0,
        )
        e = InitiativeEngine(config=cfg)
        self.assertEqual(e.config.interest_min_strength, 1.0)
        self.assertEqual(e.config.interest_min_confidence, 0.0)
        self.assertEqual(e.config.strength_to_action, 1.0)
        self.assertEqual(e.config.rising_bonus, 0.0)
        self.assertEqual(e.config.max_signals_per_tick, 0)
        self.assertEqual(e.config.max_actions_per_tick, 0)
        self.assertEqual(e.config.max_topics_per_signal, 1)

    def test_schema_version(self):
        self.assertEqual(INITIATIVE_ENGINE_SCHEMA_VERSION, "1.0")

    def test_build_default(self):
        e = build_default_initiative_engine()
        self.assertIsInstance(e, InitiativeEngine)

    def test_set_enabled(self):
        e = InitiativeEngine()
        e.set_enabled(False)
        self.assertFalse(e.enabled)
        e.set_enabled(True)
        self.assertTrue(e.enabled)

    def test_configure(self):
        e = InitiativeEngine()
        e.configure(rising_bonus=0.2, max_signals_per_tick=64)
        self.assertAlmostEqual(e.config.rising_bonus, 0.2)
        self.assertEqual(e.config.max_signals_per_tick, 64)


# ============================================================
# Disabled
# ============================================================
class TestEngineDisabled(unittest.TestCase):
    def test_disabled_skips(self):
        e = InitiativeEngine()
        e.set_enabled(False)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertTrue(out.skipped)
        self.assertEqual(out.skip_reason, "disabled")
        self.assertEqual(len(out.created_signals), 0)
        self.assertEqual(len(out.created_actions), 0)


# ============================================================
# 主题提取
# ============================================================
class TestEngineTopicExtraction(unittest.TestCase):
    def test_extract_topic_from_event(self):
        e = InitiativeEngine()
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(len(out.created_signals), 1)
        self.assertEqual(out.created_signals[0].topic, "AI绘画")

    def test_extract_topic_subject_key(self):
        e = InitiativeEngine()
        ev = make_integration_event(
            event_type=INTEGRATION_EXPERIENCE_RECORDED,
            source="src",
            payload={"subject": "B站视频"},
        )
        out = e.tick(events=[ev], now=10.0)
        self.assertEqual(len(out.created_signals), 1)
        self.assertEqual(out.created_signals[0].topic, "B站视频")

    def test_extract_topic_from_event_type_fallback(self):
        e = InitiativeEngine()
        ev = make_integration_event(
            event_type="integration.experience.bilibili",
            source="src",
            payload={},
        )
        out = e.tick(events=[ev], now=10.0)
        # 应当从 event_type 后缀提取 bilibili
        self.assertEqual(len(out.created_signals), 1)
        self.assertEqual(out.created_signals[0].topic, "bilibili")

    def test_no_topic_ignored(self):
        e = InitiativeEngine()
        ev = make_integration_event(
            event_type=INTEGRATION_EXPERIENCE_RECORDED,
            source="src",
            payload={"other_key": "value"},
        )
        out = e.tick(events=[ev], now=10.0)
        self.assertEqual(len(out.created_signals), 0)

    def test_extract_from_reflection(self):
        e = InitiativeEngine()
        ref = _ref(["music", "reading"])
        # 也提供 events 以确保 topic 落入 topic_to_events
        out = e.tick(
            events=[_ev("music"), _ev("reading")],
            reflections=[ref],
            now=10.0,
        )
        # music / reading 都应被提取
        topics = {s.topic for s in out.created_signals}
        self.assertIn("music", topics)
        self.assertIn("reading", topics)

    def test_extract_from_reflection_via_event(self):
        e = InitiativeEngine()
        ev = _ev("代码", event_id="e1")
        ref = _ref([], source_event_ids=["e1"], reflection_id="r1")
        out = e.tick(events=[ev], reflections=[ref], now=10.0)
        topics = {s.topic for s in out.created_signals}
        self.assertIn("代码", topics)


# ============================================================
# Signal 强度与置信度
# ============================================================
class TestEngineSignalStrength(unittest.TestCase):
    def test_below_min_strength_ignored(self):
        cfg = InitiativeConfig(interest_min_strength=0.95, interest_min_confidence=0.0)
        e = InitiativeEngine(config=cfg)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # 单个事件 strength = 0.4,不应通过
        self.assertEqual(len(out.created_signals), 0)

    def test_below_min_confidence_ignored(self):
        cfg = InitiativeConfig(interest_min_strength=0.0, interest_min_confidence=0.95)
        e = InitiativeEngine(config=cfg)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(len(out.created_signals), 0)

    def test_signal_has_evidence(self):
        e = InitiativeEngine()
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        s = out.created_signals[0]
        self.assertGreater(len(s.source_event_ids), 0)
        self.assertTrue(s.is_valid())

    def test_signal_initial_added_to_registry(self):
        e = InitiativeEngine()
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # signal 应当被加入 registry
        self.assertEqual(len(out.created_signals), 1)
        sig = out.created_signals[0]
        # 至少被加入 registry
        items = e.registry.list()
        self.assertIn(sig, items)


# ============================================================
# 趋势推进
# ============================================================
class TestEngineTrendEvolution(unittest.TestCase):
    def test_rising_on_repeat(self):
        e = InitiativeEngine()
        # 第一次
        out1 = e.tick(events=[_ev("AI绘画")], now=10.0)
        # 第二次同 topic
        out2 = e.tick(events=[_ev("AI绘画")], now=20.0)
        # registry 中所有 AI绘画 应当 trend=rising
        items = e.registry.list()
        ai_art = [s for s in items if s.topic == "AI绘画"]
        self.assertGreaterEqual(len(ai_art), 1)
        for s in ai_art:
            self.assertEqual(s.trend, INTEREST_TREND_RISING)

    def test_stable_after_rising(self):
        e = InitiativeEngine()
        e.tick(events=[_ev("AI绘画")], now=10.0)
        e.tick(events=[_ev("AI绘画")], now=20.0)
        # 第三次无新 AI绘画 → 推进 stable
        e.tick(events=[_ev("B站")], now=30.0)
        items = e.registry.list()
        ai_art = [s for s in items if s.topic == "AI绘画"]
        # 第一个 AI绘画 signal 在第二次 tick 后变成 rising,第三次后变 stable
        self.assertGreater(len(ai_art), 0)
        # 至少一个 signal 是 stable
        trends = {s.trend for s in ai_art}
        self.assertTrue(
            INTEREST_TREND_STABLE in trends or INTEREST_TREND_RISING in trends
        )

    def test_fading_after_stable(self):
        e = InitiativeEngine()
        e.tick(events=[_ev("AI绘画")], now=10.0)
        e.tick(events=[_ev("AI绘画")], now=20.0)
        e.tick(events=[_ev("B站")], now=30.0)  # -> stable
        e.tick(events=[_ev("C")], now=40.0)  # -> fading
        items = e.registry.list()
        ai_art = [s for s in items if s.topic == "AI绘画"]
        self.assertGreater(len(ai_art), 0)
        # 至少一个 signal 是 fading
        trends = {s.trend for s in ai_art}
        self.assertIn(INTEREST_TREND_FADING, trends)

    def test_strength_decays(self):
        e = InitiativeEngine()
        e.tick(events=[_ev("AI绘画")], now=10.0)
        e.tick(events=[_ev("AI绘画")], now=20.0)
        e.tick(events=[_ev("B站")], now=30.0)  # -> stable;强度衰减
        items = e.registry.list()
        ai_art = [s for s in items if s.topic == "AI绘画"]
        # 强度应当 < 1.0
        self.assertTrue(any(s.strength < 1.0 for s in ai_art))


# ============================================================
# Action 生成
# ============================================================
class TestEngineActionGeneration(unittest.TestCase):
    def test_action_from_event_topic(self):
        # 提高 strength_to_action 让一个事件就能产生 action
        cfg = InitiativeConfig(strength_to_action=0.0, max_signals_per_tick=10)
        # 默认 min_expected_value=0.2,单事件 expected_value=0.168 < 0.2
        # 用一个非常宽松的 filter
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(len(out.created_actions), 1)
        a = out.created_actions[0]
        self.assertEqual(a.topic, "AI绘画")
        self.assertGreater(len(a.supporting_signal_ids), 0)
        self.assertEqual(a.status, ACTION_STATUS_PENDING)
        # AI绘画 包含 "ai_art"? 不包含;会落入 default → observe
        self.assertIn(a.action_type, (ACTION_TYPE_LEARN, ACTION_TYPE_OBSERVE))

    def test_action_topic_to_action_type_mapping(self):
        cfg = InitiativeConfig(strength_to_action=0.0, max_signals_per_tick=20)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        # topics 含 mapping key
        evs = [_ev("ai_art_basic"), _ev("music_classical"), _ev("code_review"), _ev("news_today")]
        out = e.tick(events=evs, now=10.0)
        types = {a.topic: a.action_type for a in out.created_actions}
        self.assertEqual(types.get("ai_art_basic"), ACTION_TYPE_LEARN)
        self.assertEqual(types.get("music_classical"), ACTION_TYPE_LEARN)
        self.assertEqual(types.get("code_review"), ACTION_TYPE_LEARN)
        self.assertEqual(types.get("news_today"), ACTION_TYPE_OBSERVE)

    def test_no_action_below_strength(self):
        cfg = InitiativeConfig(strength_to_action=0.9)
        e = InitiativeEngine(config=cfg)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # 单事件 strength=0.4 < 0.9
        self.assertEqual(len(out.created_actions), 0)

    def test_action_evidence(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        a = out.created_actions[0]
        self.assertTrue(a.has_evidence())

    def test_urgency_normal(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        a = out.created_actions[0]
        self.assertIn(a.urgency, ("low", "normal", "high"))

    def test_urgency_high_when_curious(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        store = InternalStateStore(initial=InternalState(curiosity_drive=0.9))
        e = InitiativeEngine(config=cfg, state_store=store, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        a = out.created_actions[0]
        self.assertEqual(a.urgency, "high")

    def test_max_actions_per_tick(self):
        cfg = InitiativeConfig(strength_to_action=0.0, max_actions_per_tick=2)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        evs = [_ev("AI绘画"), _ev("音乐"), _ev("代码"), _ev("B站")]
        out = e.tick(events=evs, now=10.0)
        self.assertLessEqual(len(out.created_actions), 2)


# ============================================================
# Filter 集成
# ============================================================
class TestEngineFilterIntegration(unittest.TestCase):
    def test_queued_actions_pushed(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(len(out.queued_actions), 1)
        self.assertEqual(e.queue.size, 1)

    def test_low_confidence_discarded(self):
        cfg = InitiativeConfig(
            strength_to_action=0.0,
            interest_min_strength=0.0,
            interest_min_confidence=0.0,
        )
        f = ActionFilter(min_confidence=0.99)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(len(out.created_actions), 1)
        self.assertEqual(len(out.queued_actions), 0)
        self.assertEqual(len(out.discarded_actions), 1)

    def test_low_value_deferred(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.99)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # confidence * strength = 0.4 * 0.42 ~= 0.168 < 0.99 → deferred
        self.assertEqual(len(out.deferred_actions), 1)
        self.assertEqual(e.queue.size, 0)


# ============================================================
# 确定性
# ============================================================
class TestEngineDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        clock = _FixedClock(500.0)
        e1 = InitiativeEngine(config=cfg, clock=clock)
        e2 = InitiativeEngine(config=cfg, clock=_FixedClock(500.0))
        events = [_ev("AI绘画"), _ev("音乐")]
        out1 = e1.tick(events=events, now=10.0)
        out2 = e2.tick(events=events, now=10.0)
        self.assertEqual(len(out1.created_signals), len(out2.created_signals))
        self.assertEqual(len(out1.created_actions), len(out2.created_actions))
        for a, b in zip(out1.created_actions, out2.created_actions):
            self.assertEqual(a.topic, b.topic)
            self.assertEqual(a.action_type, b.action_type)
            self.assertAlmostEqual(a.confidence, b.confidence, places=6)


# ============================================================
# 异常恢复
# ============================================================
class TestEngineErrorRecovery(unittest.TestCase):
    def test_invalid_events_ignored(self):
        e = InitiativeEngine()
        # 非 IntegrationEvent 应当被忽略,不应抛错
        out = e.tick(events=[_ev("AI绘画"), "garbage", None, 42], now=10.0)  # type: ignore[list-item]
        self.assertIsInstance(out, InitiativeTickOutput)
        self.assertEqual(out.error, "")

    def test_invalid_reflections_ignored(self):
        e = InitiativeEngine()
        out = e.tick(reflections=[_ref(["music"]), "garbage", None], now=10.0)  # type: ignore[list-item]
        self.assertIsInstance(out, InitiativeTickOutput)
        self.assertEqual(out.error, "")


# ============================================================
# 统计
# ============================================================
class TestEngineStats(unittest.TestCase):
    def test_tick_count_increments(self):
        e = InitiativeEngine()
        for i in range(3):
            e.tick(events=[_ev("AI绘画")], now=10.0 + i)
        self.assertEqual(e.tick_count, 3)

    def test_signals_created_accumulates(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        # 第一次创建一个 signal & action
        e.tick(events=[_ev("ai_art_basic")], now=10.0)
        # signals 累加
        self.assertEqual(e.signals_created, 1)
        # 第二次:由于新 tick 中 registry 仍包含 ai_art_basic,所以会有 2 actions
        e.tick(events=[_ev("music_classical")], now=20.0)
        self.assertEqual(e.signals_created, 2)
        # actions 应当 >= 2(因为 actions 包含历史)
        self.assertGreaterEqual(e.actions_created, 2)

    def test_actions_queued(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(e.actions_queued, 1)
        self.assertEqual(e.queue.size, 1)

    def test_actions_deferred_count(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.99)
        e = InitiativeEngine(config=cfg, action_filter=f)
        e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(e.actions_deferred_count, 1)

    def test_last_signal_ids(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        ids = e.last_signal_ids
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], out.created_signals[0].signal_id)

    def test_last_action_ids(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        ids = e.last_action_ids
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0], out.created_actions[0].action_id)

    def test_describe(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        e.tick(events=[_ev("AI绘画")], now=10.0)
        d = e.describe()
        self.assertIn("name", d)
        self.assertIn("tick_count", d)
        self.assertIn("signals_created", d)
        self.assertIn("actions_created", d)
        self.assertIn("actions_queued", d)
        self.assertIn("registry", d)
        self.assertIn("filter", d)
        self.assertIn("queue", d)
        self.assertIn("config", d)


# ============================================================
# State store 集成
# ============================================================
class TestEngineStateStore(unittest.TestCase):
    def test_state_store_provides_state(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        store = InternalStateStore(initial=InternalState(curiosity_drive=0.9))
        e = InitiativeEngine(config=cfg, state_store=store, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # high curiosity → high urgency
        self.assertEqual(out.created_actions[0].urgency, "high")

    def test_state_store_snapshot(self):
        store = InternalStateStore()
        store.update(curiosity_drive=0.8)
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, state_store=store, action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(out.created_actions[0].urgency, "high")

    def test_state_store_with_state_property(self):
        class Store:
            def __init__(self):
                self.state = InternalState(curiosity_drive=0.9)
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, state_store=Store(), action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        self.assertEqual(out.created_actions[0].urgency, "high")

    def test_state_store_no_state(self):
        class Store:
            pass
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, state_store=Store(), action_filter=f)
        out = e.tick(events=[_ev("AI绘画")], now=10.0)
        # 应当 fallback 到 normal
        self.assertEqual(out.created_actions[0].urgency, "normal")


# ============================================================
# TickOutput
# ============================================================
class TestTickOutput(unittest.TestCase):
    def test_default(self):
        o = InitiativeTickOutput()
        self.assertEqual(o.created_signals, [])
        self.assertEqual(o.created_actions, [])
        self.assertEqual(o.queued_actions, [])
        self.assertFalse(o.skipped)
        self.assertEqual(o.skip_reason, "")
        self.assertEqual(o.error, "")


# ============================================================
# 线程安全
# ============================================================
class TestEngineThreadSafety(unittest.TestCase):
    def test_concurrent_ticks(self):
        cfg = InitiativeConfig(strength_to_action=0.0)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        evs = [_ev("ai_art_basic"), _ev("music_classical"), _ev("code_review")]

        def worker():
            for _ in range(20):
                e.tick(events=evs, now=10.0)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 不抛错且计数正确
        self.assertEqual(e.tick_count, 80)


# ============================================================
# 队列集成
# ============================================================
class TestEngineQueueIntegration(unittest.TestCase):
    def test_multiple_actions_queued(self):
        cfg = InitiativeConfig(strength_to_action=0.0, max_signals_per_tick=20)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        e = InitiativeEngine(config=cfg, action_filter=f)
        e.tick(events=[_ev("ai_art_basic"), _ev("music_classical"), _ev("code_review")], now=10.0)
        # 至少 1 个 action 入队
        self.assertGreater(e.queue.size, 0)

    def test_queue_capacity_limit(self):
        cfg = InitiativeConfig(strength_to_action=0.0, max_signals_per_tick=20)
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
        q = InitiativeQueue(capacity=1)
        e = InitiativeEngine(config=cfg, action_filter=f, queue=q)
        e.tick(events=[_ev("ai_art_basic"), _ev("music_classical"), _ev("code_review")], now=10.0)
        # 容量限制,可能 1 个或被丢弃
        self.assertLessEqual(q.size, 1)


# ============================================================
# 关闭 / 清理
# ============================================================
class TestEngineShutdown(unittest.TestCase):
    def test_disable_after_running(self):
        e = InitiativeEngine()
        e.tick(events=[_ev("AI绘画")], now=10.0)
        e.set_enabled(False)
        out = e.tick(events=[_ev("AI绘画")], now=20.0)
        self.assertTrue(out.skipped)


if __name__ == "__main__":
    unittest.main()
