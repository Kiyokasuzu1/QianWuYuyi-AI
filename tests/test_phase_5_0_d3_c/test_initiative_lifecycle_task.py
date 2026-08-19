# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_initiative_lifecycle_task.py

Phase 5.0-D3-C: InitiativeLifecycleTask 单元测试。

覆盖:
- 默认配置 (interval=600s, priority=6)
- 自定义配置
- RUN 流程(有 pending → tick)
- SKIP 流程(无 pending 且 engine disabled)
- ERROR 流程
- 事件输入 (push_events / push_reflections)
- 描述
"""
import threading
import unittest
from src.runtime.integration.tasks.initiative_lifecycle_task import (
    INITIATIVE_LIFECYCLE_TASK_SCHEMA_VERSION,
    InitiativeLifecycleTask,
    build_default_initiative_lifecycle_task,
)
from src.runtime.initiative.adapter.initiative_adapter import (
    InitiativeAdapter,
)
from src.runtime.initiative.adapter.initiative_event_emitter import (
    InitiativeEventEmitter,
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
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.reflection.reflection_result import (
    Insight,
    REFLECTION_TYPE_DAILY,
    ReflectionResult,
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
    """一个模拟的 context,带有 now/tick。"""

    def __init__(self, tick: int = 1, now: float = 100.0):
        self.tick = tick
        self._now = now

    def now(self) -> float:
        return self._now


def _make_task() -> InitiativeLifecycleTask:
    """构造一个便于测试的 task。"""
    f = ActionFilter(min_expected_value=0.0, min_confidence=0.0)
    cfg = InitiativeConfig(strength_to_action=0.0)
    e = InitiativeEngine(config=cfg, action_filter=f, queue=InitiativeQueue())
    a = InitiativeAdapter(engine=e, emitter=InitiativeEventEmitter())
    return InitiativeLifecycleTask(adapter=a)


# ============================================================
# 默认配置
# ============================================================
class TestTaskDefaults(unittest.TestCase):
    def test_default_creation(self):
        t = InitiativeLifecycleTask()
        self.assertEqual(t.task_id, "initiative_lifecycle_task")
        self.assertEqual(t.owner, "initiative")
        self.assertEqual(t.priority, 6)
        self.assertEqual(t.interval_seconds, 600.0)
        self.assertIsInstance(t.initiative_adapter, InitiativeAdapter)

    def test_default_creation_no_adapter(self):
        t = InitiativeLifecycleTask()
        self.assertIsNotNone(t.initiative_adapter)
        # 默认 adapter 默认有 engine
        self.assertIsNotNone(t.initiative_adapter.engine)

    def test_custom_interval(self):
        t = InitiativeLifecycleTask(interval_seconds=120.0)
        self.assertEqual(t.interval_seconds, 120.0)

    def test_negative_interval_reset(self):
        t = InitiativeLifecycleTask(interval_seconds=-5.0)
        self.assertEqual(t.interval_seconds, 600.0)

    def test_invalid_interval(self):
        t = InitiativeLifecycleTask(interval_seconds="bad")
        self.assertEqual(t.interval_seconds, 600.0)

    def test_custom_priority(self):
        t = InitiativeLifecycleTask(priority=10)
        self.assertEqual(t.priority, 10)

    def test_invalid_priority(self):
        t = InitiativeLifecycleTask(priority="bad")
        self.assertEqual(t.priority, 6)

    def test_max_events(self):
        t = InitiativeLifecycleTask(max_events=10)
        self.assertEqual(t.max_events, 10)

    def test_negative_max_events(self):
        t = InitiativeLifecycleTask(max_events=-5)
        self.assertEqual(t.max_events, 0)

    def test_max_reflections(self):
        t = InitiativeLifecycleTask(max_reflections=5)
        self.assertEqual(t.max_reflections, 5)

    def test_negative_max_reflections(self):
        t = InitiativeLifecycleTask(max_reflections=-5)
        self.assertEqual(t.max_reflections, 0)

    def test_schema_version(self):
        self.assertEqual(INITIATIVE_LIFECYCLE_TASK_SCHEMA_VERSION, "1.0")

    def test_build_default(self):
        t = build_default_initiative_lifecycle_task()
        self.assertIsInstance(t, InitiativeLifecycleTask)


# ============================================================
# 输入接口
# ============================================================
class TestTaskInputs(unittest.TestCase):
    def test_push_events(self):
        t = _make_task()
        n = t.push_events([_ev("A")])
        self.assertEqual(n, 1)
        self.assertEqual(t.pending_event_count, 1)

    def test_push_reflections(self):
        t = _make_task()
        n = t.push_reflections([_ref(["music"])])
        self.assertEqual(n, 1)
        self.assertEqual(t.pending_reflection_count, 1)

    def test_clear_pending(self):
        t = _make_task()
        t.push_events([_ev("A")])
        t.push_reflections([_ref(["music"])])
        n = t.clear_pending()
        self.assertEqual(n, 2)


# ============================================================
# RUN / SKIP / ERROR
# ============================================================
class TestTaskExecution(unittest.TestCase):
    def test_run_with_pending_event(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        ctx = _Ctx()
        result = t.execute(ctx)
        # execute 返回 dict
        self.assertIn("events", result)
        self.assertIn("metrics", result)
        # RUN 计数
        self.assertEqual(t.tick_run_count, 1)
        self.assertEqual(t.tick_skipped_count, 0)
        self.assertEqual(t.tick_error_count, 0)

    def test_run_with_pending_reflection(self):
        t = _make_task()
        t.push_reflections([_ref(["music"])])
        t.push_events([_ev("music")])  # 配合 event 让 topic 进入 topic_to_events
        ctx = _Ctx()
        t.execute(ctx)
        self.assertGreater(t.tick_run_count, 0)

    def test_skip_no_pending_no_input(self):
        t = _make_task()
        ctx = _Ctx()
        t.execute(ctx)
        self.assertEqual(t.tick_skipped_count, 1)
        self.assertEqual(t.tick_run_count, 0)
        # last event type 应当是 LIFECYCLE_TICK_COMPLETE(skip)
        self.assertEqual(t.last_event.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        self.assertEqual(t.last_event.payload.get("status"), "skip")

    def test_skip_when_disabled(self):
        t = _make_task()
        t.initiative_adapter.set_enabled(False)
        ctx = _Ctx()
        t.execute(ctx)
        self.assertEqual(t.tick_skipped_count, 1)
        self.assertEqual(t.last_event.payload.get("status"), "skip")

    def test_error_recording(self):
        # 构造一个会让 adapter.tick 返回 None 的场景
        t = _make_task()
        def bad_tick(*args, **kwargs):
            return None
        t.initiative_adapter.tick = bad_tick  # type: ignore[assignment]
        # 但 evaluate 仍可能 True(有 pending)
        t.push_events([_ev("A")])
        ctx = _Ctx()
        t.execute(ctx)
        # adapter 返回 None → task 记录 error
        self.assertEqual(t.tick_error_count, 1)
        # last_event 的 status 应当是 error
        self.assertEqual(t.last_event.payload.get("status"), "error")

    def test_execution_count_increments(self):
        t = _make_task()
        for _ in range(3):
            t.execute(_Ctx())
        self.assertEqual(t.execution_count, 3)

    def test_last_signal_count(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        t.execute(_Ctx())
        self.assertGreaterEqual(t.last_signal_count, 1)

    def test_last_action_count(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        t.execute(_Ctx())
        self.assertGreaterEqual(t.last_action_count, 0)

    def test_last_queued_count(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        t.execute(_Ctx())
        self.assertGreaterEqual(t.last_queued_count, 0)


# ============================================================
# Adapter 集成
# ============================================================
class TestTaskAdapterIntegration(unittest.TestCase):
    def test_uses_provided_adapter(self):
        e = InitiativeEngine()
        a = InitiativeAdapter(engine=e)
        t = InitiativeLifecycleTask(adapter=a)
        self.assertIs(t.initiative_adapter, a)

    def test_default_adapter_engine(self):
        t = InitiativeLifecycleTask()
        self.assertIsInstance(t.initiative_adapter, InitiativeAdapter)
        self.assertIsInstance(t.initiative_adapter.engine, InitiativeEngine)


# ============================================================
# 事件类型
# ============================================================
class TestTaskEventTypes(unittest.TestCase):
    def test_run_emits_initiative_created(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        ctx = _Ctx()
        t.execute(ctx)
        # last_event 应当是 INITIATIVE_CREATED
        self.assertEqual(t.last_event.event_type, INTEGRATION_INITIATIVE_CREATED)
        payload = t.last_event.payload
        self.assertEqual(payload.get("status"), "run")
        self.assertIn("signal_count", payload)
        self.assertIn("action_count", payload)
        self.assertIn("queued_count", payload)

    def test_skip_emits_lifecycle_tick_complete(self):
        t = _make_task()
        t.execute(_Ctx())
        self.assertEqual(t.last_event.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)


# ============================================================
# 描述
# ============================================================
class TestTaskDescribe(unittest.TestCase):
    def test_describe(self):
        t = _make_task()
        t.push_events([_ev("ai_art_basic")])
        t.execute(_Ctx())
        d = t.describe()
        self.assertIn("task_id", d)
        self.assertIn("owner", d)
        self.assertIn("schema_version", d)
        self.assertIn("tick_run_count", d)
        self.assertIn("tick_skipped_count", d)
        self.assertIn("tick_error_count", d)
        self.assertIn("max_events", d)
        self.assertIn("max_reflections", d)
        self.assertIn("adapter", d)

    def test_repr(self):
        t = _make_task()
        s = repr(t)
        self.assertIn("InitiativeLifecycleTask", s)


# ============================================================
# 线程安全
# ============================================================
class TestTaskThreadSafety(unittest.TestCase):
    def test_concurrent_execute(self):
        t = _make_task()
        # 准备 pending
        t.push_events([_ev("ai_art_basic")])

        def worker():
            for _ in range(10):
                t.execute(_Ctx())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        # 40 次执行
        self.assertEqual(t.execution_count, 40)


# ============================================================
# 触发事件类型属性
# ============================================================
class TestTaskTriggeredEventType(unittest.TestCase):
    def test_triggered_event_type(self):
        t = _make_task()
        self.assertEqual(t.triggered_event_type, INTEGRATION_INITIATIVE_CREATED)


if __name__ == "__main__":
    unittest.main()
