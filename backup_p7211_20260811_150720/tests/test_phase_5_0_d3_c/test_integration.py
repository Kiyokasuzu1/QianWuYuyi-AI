# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_integration.py

Phase 5.0-D3-C: Initiative System 端到端集成测试。

覆盖:
- Reflection Event → InterestSignal → PossibleAction → Queue
- 全链路: 输入 events → adapter → engine → queue
- 多次 tick 状态演进
- 启用/禁用 Initiative
- 异常隔离
- 端到端 IntegrationEvent 验证
"""
import unittest
from src.runtime.initiative.adapter.initiative_adapter import InitiativeAdapter
from src.runtime.initiative.adapter.initiative_event_emitter import InitiativeEventEmitter
from src.runtime.initiative.initiative_engine import (
    InitiativeConfig,
    InitiativeEngine,
)
from src.runtime.initiative.action_filter import ActionFilter
from src.runtime.initiative.initiative_queue import InitiativeQueue
from src.runtime.initiative.interest_signal import (
    INTEREST_TREND_FADING,
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
)
from src.runtime.initiative.possible_action import (
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_EXPERIENCE_RECORDED,
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INITIATIVE_FILTERED,
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.initiative_lifecycle_task import (
    InitiativeLifecycleTask,
)
from src.runtime.reflection.reflection_result import (
    Insight,
    REFLECTION_TYPE_DAILY,
    ReflectionResult,
)


def _ev(topic: str = "ai_art_basic", event_id: str = "", event_type: str = INTEGRATION_EXPERIENCE_RECORDED) -> IntegrationEvent:
    e = make_integration_event(
        event_type=event_type,
        source="src",
        payload={"topic": topic},
    )
    if event_id:
        e.event_id = event_id
    return e


def _ref(topics: list = None, source_event_ids: list = None) -> ReflectionResult:
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


class _Ctx:
    def __init__(self, tick: int = 1):
        self.tick = tick

    def now(self) -> float:
        return 100.0


def _build_pipeline(*, min_conf: float = 0.0, min_value: float = 0.0):
    """构造一个完整的 pipeline。"""
    f = ActionFilter(min_confidence=min_conf, min_expected_value=min_value)
    cfg = InitiativeConfig(strength_to_action=0.0)
    queue = InitiativeQueue()
    engine = InitiativeEngine(config=cfg, action_filter=f, queue=queue)
    emitter = InitiativeEventEmitter()
    adapter = InitiativeAdapter(engine=engine, emitter=emitter)
    return {
        "engine": engine,
        "filter": f,
        "queue": queue,
        "emitter": emitter,
        "adapter": adapter,
    }


# ============================================================
# 全链路
# ============================================================
class TestPipelineE2E(unittest.TestCase):
    def test_event_to_queue(self):
        p = _build_pipeline()
        out = p["adapter"].tick(events=[_ev("ai_art_basic")])
        # InterestSignal 应当被生成
        self.assertGreater(len(out.created_signals), 0)
        # PossibleAction 应当被生成
        self.assertGreater(len(out.created_actions), 0)
        # pending actions 应当入队
        self.assertGreater(p["queue"].size, 0)

    def test_reflection_to_queue(self):
        p = _build_pipeline()
        # 提供 reflection 关联 event
        out = p["adapter"].tick(
            events=[_ev("music_classical")],
            reflections=[_ref(["music_classical"])],
        )
        # music_classical 应当在 signals 中
        topics = {s.topic for s in out.created_signals}
        self.assertIn("music_classical", topics)
        # 队列至少 1 个
        self.assertGreater(p["queue"].size, 0)

    def test_event_to_emit_signal(self):
        p = _build_pipeline()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        # 注入 emitter
        p["adapter"]._event_emitter = custom_emit
        p["adapter"].tick(events=[_ev("ai_art_basic")])
        # 至少 1 个 INTEREST_SIGNAL_CREATED
        sig_evs = [e for e in capture if e.event_type == INTEGRATION_INTEREST_SIGNAL_CREATED]
        self.assertGreater(len(sig_evs), 0)

    def test_event_to_emit_action(self):
        p = _build_pipeline()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        p["adapter"]._event_emitter = custom_emit
        p["adapter"].tick(events=[_ev("ai_art_basic")])
        # 至少 1 个 INITIATIVE_CREATED 或 INITIATIVE_FILTERED
        action_evs = [
            e for e in capture
            if e.event_type in (INTEGRATION_INITIATIVE_CREATED, INTEGRATION_INITIATIVE_FILTERED)
        ]
        self.assertGreater(len(action_evs), 0)

    def test_signal_id_chains_to_action(self):
        p = _build_pipeline()
        out = p["adapter"].tick(events=[_ev("ai_art_basic")])
        # signal 应当被 action 引用
        for action in out.created_actions:
            self.assertGreater(len(action.supporting_signal_ids), 0)
        # 至少 1 个 signal id
        signal_ids = {s.signal_id for s in out.created_signals}
        self.assertGreater(len(signal_ids), 0)


# ============================================================
# 多 tick 演进
# ============================================================
class TestPipelineEvolution(unittest.TestCase):
    def test_rising_stable_fading(self):
        p = _build_pipeline()
        # 第 1 次:new
        p["adapter"].tick(events=[_ev("ai_art_basic")], now=10.0)
        items1 = p["engine"].registry.list()
        # 第二次:rising
        p["adapter"].tick(events=[_ev("ai_art_basic")], now=20.0)
        # 第三次:不同 topic → stable
        p["adapter"].tick(events=[_ev("music")], now=30.0)
        items3 = p["engine"].registry.list()
        ai_art = [s for s in items3 if s.topic == "ai_art_basic"]
        # 至少 1 个 ai_art
        self.assertGreater(len(ai_art), 0)
        # 第三次后:趋势应当是 stable 或 rising
        trends = {s.trend for s in ai_art}
        self.assertTrue(
            INTEREST_TREND_STABLE in trends or INTEREST_TREND_RISING in trends
        )

    def test_repeat_suppression(self):
        # 自定义 filter 启用重复抑制
        f = ActionFilter(
            min_confidence=0.0,
            min_expected_value=0.0,
            repeat_window_seconds=100.0,
            repeat_penalty=0.5,
        )
        cfg = InitiativeConfig(strength_to_action=0.0)
        queue = InitiativeQueue()
        engine = InitiativeEngine(config=cfg, action_filter=f, queue=queue)
        adapter = InitiativeAdapter(engine=engine, emitter=InitiativeEventEmitter())
        # 第一次
        out1 = adapter.tick(events=[_ev("ai_art_basic")], now=10.0)
        # 第二次:同 topic
        out2 = adapter.tick(events=[_ev("ai_art_basic")], now=20.0)
        # 第二次的决策可能有 repeat_penalty
        # 至少 1 个 decision 包含 repeat rule
        rules = {d.rule_applied for d in out2.decisions}
        # 应当观察到 repeat rule(可能)
        self.assertIn("repeat", rules)


# ============================================================
# 启用 / 禁用
# ============================================================
class TestEnableDisable(unittest.TestCase):
    def test_disabled_skips_engine(self):
        p = _build_pipeline()
        p["adapter"].set_enabled(False)
        out = p["adapter"].tick(events=[_ev("ai_art_basic")])
        self.assertTrue(out.skipped)
        self.assertEqual(p["queue"].size, 0)

    def test_disabled_no_signals(self):
        p = _build_pipeline()
        p["adapter"].set_enabled(False)
        out = p["adapter"].tick(events=[_ev("ai_art_basic")])
        self.assertEqual(len(out.created_signals), 0)

    def test_re_enable(self):
        p = _build_pipeline()
        p["adapter"].set_enabled(False)
        p["adapter"].set_enabled(True)
        out = p["adapter"].tick(events=[_ev("ai_art_basic")])
        self.assertFalse(out.skipped)
        self.assertGreater(len(out.created_signals), 0)


# ============================================================
# 异常隔离
# ============================================================
class TestExceptionIsolation(unittest.TestCase):
    def test_adapter_handles_garbage_input(self):
        p = _build_pipeline()
        out = p["adapter"].tick(events=[_ev("A"), None, "x", 42])  # type: ignore[list-item]
        # 不应抛错
        self.assertIsNotNone(out)
        # 至少 A 应当被处理
        topics = {s.topic for s in out.created_signals}
        self.assertIn("A", topics)

    def test_adapter_handles_garbage_reflection(self):
        p = _build_pipeline()
        out = p["adapter"].tick(
            events=[_ev("A")],
            reflections=[_ref(["A"]), "garbage", None],
        )
        self.assertIsNotNone(out)
        topics = {s.topic for s in out.created_signals}
        self.assertIn("A", topics)


# ============================================================
# IntegrationEvent payload 验证
# ============================================================
class TestEventPayload(unittest.TestCase):
    def test_signal_event_payload(self):
        p = _build_pipeline()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        p["adapter"]._event_emitter = custom_emit
        p["adapter"].tick(events=[_ev("ai_art_basic")])
        sig_evs = [e for e in capture if e.event_type == INTEGRATION_INTEREST_SIGNAL_CREATED]
        if sig_evs:
            payload = sig_evs[0].payload
            self.assertIn("signal_id", payload)
            self.assertIn("topic", payload)
            self.assertIn("trend", payload)
            self.assertIn("strength", payload)
            self.assertIn("confidence", payload)
            self.assertIn("source_event_ids", payload)

    def test_initiative_created_event_payload(self):
        p = _build_pipeline()
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        p["adapter"]._event_emitter = custom_emit
        p["adapter"].tick(events=[_ev("ai_art_basic")])
        created = [e for e in capture if e.event_type == INTEGRATION_INITIATIVE_CREATED]
        if created:
            payload = created[0].payload
            self.assertIn("action_id", payload)
            self.assertIn("action_type", payload)
            self.assertIn("topic", payload)
            self.assertIn("supporting_signal_ids", payload)
            self.assertIn("confidence", payload)
            self.assertIn("expected_value", payload)
            self.assertIn("priority", payload)
            self.assertIn("urgency", payload)
            self.assertIn("effort_estimate", payload)

    def test_initiative_filtered_event_payload(self):
        # 使用严格 filter 让 action 被过滤
        f = ActionFilter(min_confidence=0.99, min_expected_value=0.0)
        cfg = InitiativeConfig(strength_to_action=0.0)
        engine = InitiativeEngine(config=cfg, action_filter=f, queue=InitiativeQueue())
        adapter = InitiativeAdapter(engine=engine, emitter=InitiativeEventEmitter())
        capture = []

        def custom_emit(ev):
            capture.append(ev)
        adapter._event_emitter = custom_emit
        adapter.tick(events=[_ev("ai_art_basic")])
        # 由于 confidence 0.42 < 0.99,action 应当被 discarded
        filtered = [e for e in capture if e.event_type == INTEGRATION_INITIATIVE_FILTERED]
        self.assertGreater(len(filtered), 0)


# ============================================================
# LifecycleTask 集成
# ============================================================
class TestLifecycleTaskIntegration(unittest.TestCase):
    def test_task_with_adapter(self):
        p = _build_pipeline()
        task = InitiativeLifecycleTask(adapter=p["adapter"])
        task.push_events([_ev("ai_art_basic")])
        ctx = _Ctx()
        result = task.execute(ctx)
        # 应当 RUN
        self.assertEqual(task.tick_run_count, 1)
        # last_event 应当是 INITIATIVE_CREATED
        self.assertEqual(task.last_event.event_type, INTEGRATION_INITIATIVE_CREATED)

    def test_task_skip_when_disabled(self):
        p = _build_pipeline()
        task = InitiativeLifecycleTask(adapter=p["adapter"])
        task.initiative_adapter.set_enabled(False)
        ctx = _Ctx()
        task.execute(ctx)
        self.assertEqual(task.tick_skipped_count, 1)

    def test_task_skip_no_input(self):
        p = _build_pipeline()
        task = InitiativeLifecycleTask(adapter=p["adapter"])
        ctx = _Ctx()
        task.execute(ctx)
        self.assertEqual(task.tick_skipped_count, 1)
        # last event 应当是 LIFECYCLE_TICK_COMPLETE
        self.assertEqual(task.last_event.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        self.assertEqual(task.last_event.payload.get("status"), "skip")


# ============================================================
# 多次 tick
# ============================================================
class TestMultiTick(unittest.TestCase):
    def test_multiple_ticks_accumulate(self):
        p = _build_pipeline()
        for i in range(5):
            p["adapter"].tick(events=[_ev(f"topic_{i}")], now=10.0 + i)
        # engine 应当执行 5 次
        self.assertEqual(p["engine"].tick_count, 5)
        # 5 个不同的 topic 应当有 signal
        topics = {s.topic for s in p["engine"].registry.list()}
        for i in range(5):
            self.assertIn(f"topic_{i}", topics)

    def test_repeat_topic_multiple_signals(self):
        p = _build_pipeline()
        # 多次同 topic
        for i in range(3):
            p["adapter"].tick(events=[_ev("ai_art_basic")], now=10.0 + i)
        # 每次都会创建新 signal
        ai_art_signals = [
            s for s in p["engine"].registry.list() if s.topic == "ai_art_basic"
        ]
        self.assertEqual(len(ai_art_signals), 3)


# ============================================================
# 确定性
# ============================================================
class TestDeterminism(unittest.TestCase):
    def test_same_input_same_signals(self):
        p1 = _build_pipeline()
        p2 = _build_pipeline()
        events = [_ev("ai_art_basic"), _ev("music")]
        out1 = p1["adapter"].tick(events=events, now=10.0)
        out2 = p2["adapter"].tick(events=events, now=10.0)
        # topics 应当一致
        t1 = {s.topic for s in out1.created_signals}
        t2 = {s.topic for s in out2.created_signals}
        self.assertEqual(t1, t2)


if __name__ == "__main__":
    unittest.main()
