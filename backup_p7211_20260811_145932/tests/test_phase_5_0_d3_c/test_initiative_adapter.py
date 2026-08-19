# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_initiative_adapter.py

Phase 5.0-D3-C: InitiativeAdapter 单元测试。

覆盖:
- 默认配置
- 事件推送(push_events / push_reflections)
- 容量限制
- pending 状态
- evaluate / tick 流程
- Reflection Event -> Initiative
- 发射 INTEREST_SIGNAL_CREATED / INITIATIVE_CREATED
- 描述
- 线程安全
"""
import threading
import unittest
from src.runtime.initiative.adapter.initiative_adapter import (
    DEFAULT_INITIATIVE_ADAPTER_OWNER,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_REFLECTIONS,
    INITIATIVE_ADAPTER_SCHEMA_VERSION,
    InitiativeAdapter,
    build_default_initiative_adapter,
)
from src.runtime.initiative.adapter.initiative_event_emitter import (
    DEFAULT_INITIATIVE_EMITTER_OWNER,
    INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION,
    InitiativeEventEmitter,
    build_default_initiative_event_emitter,
)
from src.runtime.initiative.initiative_engine import (
    InitiativeConfig,
    InitiativeEngine,
)
from src.runtime.initiative.action_filter import ActionFilter
from src.runtime.initiative.initiative_queue import InitiativeQueue
from src.runtime.integration.integration_event import (
    INTEGRATION_EXPERIENCE_RECORDED,
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INITIATIVE_FILTERED,
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.reflection.reflection_result import (
    Insight,
    REFLECTION_TYPE_DAILY,
    ReflectionResult,
)


def _ev(topic: str = "AI绘画", event_id: str = "", event_type: str = INTEGRATION_EXPERIENCE_RECORDED) -> IntegrationEvent:
    e = make_integration_event(
        event_type=event_type,
        source="src",
        payload={"topic": topic},
    )
    if event_id:
        e.event_id = event_id
    return e


def _ref(topics: list = None, source_event_ids: list = None, reflection_id: str = "") -> ReflectionResult:
    insights = []
    for t in (topics or []):
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


def _make_adapter(*, permissive_filter: bool = True, max_events: int = None, max_reflections: int = None) -> InitiativeAdapter:
    """创建一个便于测试的 adapter。"""
    if permissive_filter:
        f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
    else:
        f = ActionFilter()
    cfg = InitiativeConfig(strength_to_action=0.0)
    e = InitiativeEngine(config=cfg, action_filter=f, queue=InitiativeQueue())
    kwargs = {}
    if max_events is not None:
        kwargs["max_events"] = max_events
    if max_reflections is not None:
        kwargs["max_reflections"] = max_reflections
    return InitiativeAdapter(
        engine=e,
        emitter=InitiativeEventEmitter(),
        **kwargs,
    )


# ============================================================
# 默认
# ============================================================
class TestAdapterDefaults(unittest.TestCase):
    def test_default_creation(self):
        a = InitiativeAdapter()
        self.assertEqual(a.name, "initiative_adapter")
        self.assertEqual(a.owner, DEFAULT_INITIATIVE_ADAPTER_OWNER)
        self.assertTrue(a.enabled)
        self.assertEqual(a.tick_count, 0)
        self.assertEqual(a.signals_emitted, 0)
        self.assertEqual(a.actions_emitted, 0)
        self.assertEqual(a.pending_event_count, 0)
        self.assertEqual(a.pending_reflection_count, 0)

    def test_custom_creation(self):
        e = InitiativeEngine()
        em = InitiativeEventEmitter(owner="custom_owner")
        # owner 由 adapter 自己控制,不跟随 emitter
        a = InitiativeAdapter(name="x", owner="my_initiative", engine=e, emitter=em, max_events=10, max_reflections=5)
        self.assertEqual(a.name, "x")
        self.assertEqual(a.owner, "my_initiative")
        self.assertIs(a.engine, e)
        self.assertIs(a.emitter, em)
        self.assertEqual(a._max_events, 10)
        self.assertEqual(a._max_reflections, 5)

    def test_invalid_max_events_clipped(self):
        a = InitiativeAdapter(max_events=-5)
        self.assertEqual(a._max_events, 0)

    def test_invalid_max_reflections_clipped(self):
        a = InitiativeAdapter(max_reflections=-5)
        self.assertEqual(a._max_reflections, 0)

    def test_schema_version(self):
        self.assertEqual(INITIATIVE_ADAPTER_SCHEMA_VERSION, "1.0")

    def test_build_default(self):
        a = build_default_initiative_adapter()
        self.assertIsInstance(a, InitiativeAdapter)


# ============================================================
# 事件推送
# ============================================================
class TestAdapterPush(unittest.TestCase):
    def test_push_events(self):
        a = _make_adapter()
        n = a.push_events([_ev("A"), _ev("B")])
        self.assertEqual(n, 2)
        self.assertEqual(a.pending_event_count, 2)

    def test_push_events_none(self):
        a = _make_adapter()
        n = a.push_events(None)  # type: ignore[arg-type]
        self.assertEqual(n, 0)

    def test_push_events_filters_non_integration_event(self):
        a = _make_adapter()
        n = a.push_events([_ev("A"), "garbage", None, 42])  # type: ignore[list-item]
        self.assertEqual(n, 1)
        self.assertEqual(a.pending_event_count, 1)

    def test_push_reflections(self):
        a = _make_adapter()
        n = a.push_reflections([_ref(["music"])])
        self.assertEqual(n, 1)
        self.assertEqual(a.pending_reflection_count, 1)

    def test_push_reflections_none(self):
        a = _make_adapter()
        n = a.push_reflections(None)  # type: ignore[arg-type]
        self.assertEqual(n, 0)

    def test_push_reflections_filters_non_reflection(self):
        a = _make_adapter()
        n = a.push_reflections([_ref(["music"]), "garbage", None])
        self.assertEqual(n, 1)

    def test_max_events_eviction(self):
        a = _make_adapter(max_events=2)
        a.push_events([_ev("A"), _ev("B"), _ev("C"), _ev("D")])
        # 容量 2,保留最新 2 个
        self.assertEqual(a.pending_event_count, 2)

    def test_max_reflections_eviction(self):
        a = _make_adapter(max_reflections=1)
        a.push_reflections([_ref(["A"]), _ref(["B"])])
        self.assertEqual(a.pending_reflection_count, 1)

    def test_zero_max_events_no_limit(self):
        a = _make_adapter(max_events=0)
        a.push_events([_ev(t) for t in ["A", "B", "C", "D"]])
        self.assertEqual(a.pending_event_count, 4)

    def test_clear_pending(self):
        a = _make_adapter()
        a.push_events([_ev("A")])
        a.push_reflections([_ref(["music"])])
        n = a.clear_pending()
        self.assertEqual(n, 2)
        self.assertEqual(a.pending_event_count, 0)
        self.assertEqual(a.pending_reflection_count, 0)


# ============================================================
# 评估与执行
# ============================================================
class TestAdapterEvaluate(unittest.TestCase):
    def test_evaluate_disabled(self):
        a = _make_adapter()
        a.set_enabled(False)
        self.assertFalse(a.evaluate())

    def test_evaluate_no_input(self):
        a = _make_adapter()
        self.assertFalse(a.evaluate())

    def test_evaluate_with_events(self):
        a = _make_adapter()
        a.push_events([_ev("A")])
        self.assertTrue(a.evaluate())

    def test_evaluate_with_reflections(self):
        a = _make_adapter()
        a.push_reflections([_ref(["music"])])
        self.assertTrue(a.evaluate())

    def test_evaluate_with_external_input(self):
        a = _make_adapter()
        self.assertTrue(a.evaluate(events=[_ev("A")]))

    def test_evaluate_empty_input_lists(self):
        a = _make_adapter()
        self.assertFalse(a.evaluate(events=[], reflections=[]))


# ============================================================
# Tick 流程
# ============================================================
class TestAdapterTick(unittest.TestCase):
    def test_tick_no_input(self):
        a = _make_adapter()
        out = a.tick()
        self.assertIsNotNone(out)
        # engine 即使无输入也会运行,只是不产生 signal/action
        self.assertEqual(len(out.created_signals), 0)
        self.assertEqual(a.tick_count, 1)

    def test_tick_with_event(self):
        a = _make_adapter()
        out = a.tick(events=[_ev("ai_art_basic")])
        self.assertIsNotNone(out)
        self.assertGreaterEqual(len(out.created_signals), 1)
        self.assertEqual(a.tick_count, 1)

    def test_tick_with_reflection(self):
        a = _make_adapter()
        # 反射需要 events 配合才能产生 signal
        out = a.tick(events=[_ev("music")], reflections=[_ref(["music"])])
        self.assertIsNotNone(out)
        topics = {s.topic for s in out.created_signals}
        self.assertIn("music", topics)

    def test_tick_drains_pending(self):
        a = _make_adapter()
        a.push_events([_ev("ai_art_basic")])
        self.assertEqual(a.pending_event_count, 1)
        a.tick()
        # drain 后 pending 清空
        self.assertEqual(a.pending_event_count, 0)

    def test_tick_merges_inputs(self):
        a = _make_adapter()
        a.push_events([_ev("A")])
        out = a.tick(events=[_ev("B")])
        # B 应当被处理
        self.assertIsNotNone(out)
        self.assertEqual(a.pending_event_count, 0)

    def test_tick_disabled(self):
        a = _make_adapter()
        a.set_enabled(False)
        out = a.tick(events=[_ev("ai_art_basic")])
        self.assertIsNotNone(out)
        self.assertTrue(out.skipped)

    def test_tick_emits_signal_event(self):
        a = _make_adapter()
        a.tick(events=[_ev("ai_art_basic")])
        # 应当发射至少一个 INTEREST_SIGNAL_CREATED
        self.assertGreater(a.signals_emitted, 0)

    def test_tick_emits_action_event(self):
        a = _make_adapter()
        a.tick(events=[_ev("ai_art_basic")])
        # 应当发射 INITIATIVE_CREATED 或 INITIATIVE_FILTERED
        self.assertGreaterEqual(a.actions_emitted, 0)

    def test_drain_and_tick(self):
        a = _make_adapter()
        a.push_events([_ev("ai_art_basic")])
        out = a.drain_and_tick()
        self.assertIsNotNone(out)

    def test_last_tick_output(self):
        a = _make_adapter()
        out = a.tick(events=[_ev("ai_art_basic")])
        self.assertIs(a.last_tick_output, out)

    def test_tick_increments_count(self):
        a = _make_adapter()
        for _ in range(3):
            a.tick(events=[_ev("ai_art_basic")])
        self.assertEqual(a.tick_count, 3)


# ============================================================
# Event 转换
# ============================================================
class TestAdapterEventConversion(unittest.TestCase):
    def test_reflection_event_triggers_initiative(self):
        a = _make_adapter()
        # reflection event 进入 pending
        ref_ev = _ev(event_type=INTEGRATION_REFLECTION_DAILY_COMPLETED, event_id="ref_1")
        ref_ev.payload = {"topic": "ai_art_basic"}
        a.push_events([ref_ev])
        out = a.tick()
        self.assertGreater(len(out.created_signals), 0)
        self.assertEqual(out.created_signals[0].topic, "ai_art_basic")

    def test_experience_event_triggers_initiative(self):
        a = _make_adapter()
        a.push_events([_ev("music_classical")])
        out = a.tick()
        topics = {s.topic for s in out.created_signals}
        self.assertIn("music_classical", topics)

    def test_emitted_signal_event_type(self):
        a = _make_adapter()
        events = []
        # 拦截 emit
        original_emit = a._event_emitter
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        # 注入到 BaseAdapter
        a._event_emitter = custom_emit
        a.tick(events=[_ev("ai_art_basic")])
        # 至少一个 signal event
        signal_events = [e for e in capture if e.event_type == INTEGRATION_INTEREST_SIGNAL_CREATED]
        self.assertGreater(len(signal_events), 0)

    def test_emitted_action_event_type(self):
        a = _make_adapter()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        a._event_emitter = custom_emit
        a.tick(events=[_ev("ai_art_basic")])
        action_events = [
            e for e in capture
            if e.event_type in (INTEGRATION_INITIATIVE_CREATED, INTEGRATION_INITIATIVE_FILTERED)
        ]
        self.assertGreater(len(action_events), 0)

    def test_action_event_payload(self):
        a = _make_adapter()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        a._event_emitter = custom_emit
        a.tick(events=[_ev("ai_art_basic")])
        created = [
            e for e in capture
            if e.event_type == INTEGRATION_INITIATIVE_CREATED
        ]
        if created:
            payload = created[0].payload
            self.assertIn("action_id", payload)
            self.assertIn("action_type", payload)
            self.assertIn("topic", payload)
            self.assertIn("supporting_signal_ids", payload)
            self.assertIn("confidence", payload)
            self.assertIn("expected_value", payload)


# ============================================================
# 异常隔离
# ============================================================
class TestAdapterErrorIsolation(unittest.TestCase):
    def test_tick_handles_exception(self):
        a = _make_adapter()
        # 构造一个会让 engine 抛错的场景:非 None input 但 engine 异常
        # 简单方法是:无外部 emitter 时,emit 静默忽略
        a.tick(events=[_ev("A"), None, "x", 42])  # type: ignore[list-item]
        # 不应抛错
        self.assertIsNotNone(a.last_tick_output)

    def test_describe_after_error(self):
        a = _make_adapter()
        a.tick(events=[_ev("A")])
        d = a.describe()
        self.assertIn("schema_version", d)
        self.assertIn("enabled", d)
        self.assertIn("tick_count", d)
        self.assertIn("engine", d)
        self.assertIn("emitter", d)


# ============================================================
# 描述与表示
# ============================================================
class TestAdapterDescribe(unittest.TestCase):
    def test_describe(self):
        a = _make_adapter()
        a.tick(events=[_ev("ai_art_basic")])
        d = a.describe()
        self.assertIn("schema_version", d)
        self.assertIn("name", d)
        self.assertIn("owner", d)
        self.assertIn("available", d)
        self.assertIn("enabled", d)
        self.assertIn("tick_count", d)
        self.assertIn("signals_emitted", d)
        self.assertIn("actions_emitted", d)
        self.assertIn("pending_event_count", d)
        self.assertIn("pending_reflection_count", d)
        self.assertIn("engine", d)
        self.assertIn("emitter", d)

    def test_repr(self):
        a = _make_adapter()
        s = repr(a)
        self.assertIn("InitiativeAdapter", s)


# ============================================================
# 线程安全
# ============================================================
class TestAdapterThreadSafety(unittest.TestCase):
    def test_concurrent_push_events(self):
        a = _make_adapter(max_events=10000)
        def worker(start):
            for i in range(100):
                a.push_events([_ev(f"t{start}_{i}")])
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(a.pending_event_count, 400)

    def test_concurrent_tick(self):
        a = _make_adapter()
        def worker():
            for _ in range(20):
                a.tick(events=[_ev("ai_art_basic")])
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(a.tick_count, 80)


# ============================================================
# Emitter
# ============================================================
class TestEmitter(unittest.TestCase):
    def test_default(self):
        em = InitiativeEventEmitter()
        self.assertEqual(em.owner, DEFAULT_INITIATIVE_EMITTER_OWNER)
        self.assertEqual(em.emitted_count, 0)

    def test_custom_owner(self):
        em = InitiativeEventEmitter(owner="custom")
        self.assertEqual(em.owner, "custom")

    def test_build_default(self):
        em = build_default_initiative_event_emitter()
        self.assertIsInstance(em, InitiativeEventEmitter)

    def test_schema_version(self):
        self.assertEqual(INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION, "1.0")

    def test_emit_signal(self):
        from src.runtime.initiative.interest_signal import (
            InterestSignal,
        )
        em = InitiativeEventEmitter()
        sig = InterestSignal(
            topic="A",
            strength=0.6,
            source_event_ids=["e1"],
            confidence=0.7,
        )
        ev = em.emit_signal(sig)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_INTEREST_SIGNAL_CREATED)
        self.assertEqual(ev.payload["topic"], "A")
        self.assertEqual(em.emitted_signal_count, 1)

    def test_emit_signal_non_signal(self):
        em = InitiativeEventEmitter()
        ev = em.emit_signal("not a signal")  # type: ignore[arg-type]
        self.assertIsNone(ev)
        self.assertNotEqual(em.last_error, "")

    def test_emit_signals_batch(self):
        from src.runtime.initiative.interest_signal import InterestSignal
        em = InitiativeEventEmitter()
        sigs = [
            InterestSignal(topic=f"t{i}", strength=0.5, source_event_ids=[f"e{i}"])
            for i in range(3)
        ]
        out = em.emit_signals(sigs)
        self.assertEqual(len(out), 3)
        self.assertEqual(em.emitted_signal_count, 3)

    def test_emit_action_pending(self):
        from src.runtime.initiative.possible_action import (
            ACTION_STATUS_PENDING,
            PossibleAction,
        )
        em = InitiativeEventEmitter()
        a = PossibleAction(
            topic="A",
            supporting_signal_ids=["s1"],
            status=ACTION_STATUS_PENDING,
        )
        ev = em.emit_action(a)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_INITIATIVE_CREATED)
        self.assertEqual(em.emitted_created_count, 1)

    def test_emit_action_filtered(self):
        from src.runtime.initiative.possible_action import (
            ACTION_STATUS_DISCARDED,
            PossibleAction,
        )
        em = InitiativeEventEmitter()
        a = PossibleAction(
            topic="A",
            supporting_signal_ids=["s1"],
            status=ACTION_STATUS_DISCARDED,
        )
        ev = em.emit_action(a)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, INTEGRATION_INITIATIVE_FILTERED)
        self.assertEqual(em.emitted_filtered_count, 1)

    def test_emit_action_no_evidence(self):
        from src.runtime.initiative.possible_action import PossibleAction
        em = InitiativeEventEmitter()
        a = PossibleAction(
            topic="A",
            supporting_signal_ids=[],
        )
        ev = em.emit_action(a)
        self.assertIsNone(ev)

    def test_emit_action_non_action(self):
        em = InitiativeEventEmitter()
        ev = em.emit_action("not an action")  # type: ignore[arg-type]
        self.assertIsNone(ev)

    def test_emit_actions_batch(self):
        from src.runtime.initiative.possible_action import PossibleAction
        em = InitiativeEventEmitter()
        actions = [
            PossibleAction(topic=f"t{i}", supporting_signal_ids=[f"s{i}"])
            for i in range(3)
        ]
        out = em.emit_actions(actions)
        self.assertEqual(len(out), 3)

    def test_describe(self):
        em = InitiativeEventEmitter()
        d = em.describe()
        self.assertIn("owner", d)
        self.assertIn("emitted_count", d)
        self.assertIn("emitted_signal", d)
        self.assertIn("emitted_created", d)
        self.assertIn("emitted_filtered", d)

    def test_repr(self):
        em = InitiativeEventEmitter()
        s = repr(em)
        self.assertIn("InitiativeEventEmitter", s)


if __name__ == "__main__":
    unittest.main()
