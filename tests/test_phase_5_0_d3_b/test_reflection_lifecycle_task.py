# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_lifecycle_task.py

Phase 5.0-D3-B: ReflectionLifecycleTask 单元测试。
"""
import unittest
from typing import Any, List, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_REFLECTION_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask
from src.runtime.integration.tasks.reflection_lifecycle_task import (
    REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION,
    ReflectionLifecycleTask,
)


class _FakeContext:
    """测试用 LifecycleContext。"""
    def __init__(self, tick: int = 0, now: float = 100.0):
        self.tick = tick
        self._now = now
        self.is_cancelled = False

    def now(self) -> float:
        return self._now


def _ev(et: str, ts: float, topic: str = "", severity: str = "info") -> IntegrationEvent:
    p = {}
    if topic:
        p["topic"] = topic
    return make_integration_event(
        event_type=et,
        source="test",
        severity=severity,
        payload=p,
    )


class TestReflectionLifecycleTaskCreation(unittest.TestCase):
    def test_default_creation(self):
        t = ReflectionLifecycleTask()
        self.assertEqual(t.task_id, "reflection_lifecycle_task")
        self.assertEqual(t.owner, "reflection")
        self.assertEqual(t.interval_seconds, 300.0)
        self.assertEqual(t.max_events, 256)

    def test_custom_creation(self):
        t = ReflectionLifecycleTask(interval_seconds=60.0, max_events=10, priority=3)
        self.assertEqual(t.interval_seconds, 60.0)
        self.assertEqual(t.max_events, 10)
        self.assertEqual(t.priority, 3)

    def test_default_adapter(self):
        t = ReflectionLifecycleTask()
        # 应注入一个 ReflectionAdapter
        self.assertIsNotNone(t.reflection_adapter)

    def test_invalid_max_events_clamped(self):
        t = ReflectionLifecycleTask(max_events=-1)
        self.assertEqual(t.max_events, 0)


class TestReflectionLifecycleTaskPushEvents(unittest.TestCase):
    def test_push_events(self):
        t = ReflectionLifecycleTask()
        events = [_ev("a", 100.0 + i) for i in range(3)]
        n = t.push_events(events)
        self.assertEqual(n, 3)
        self.assertEqual(t.pending_count, 3)

    def test_push_events_with_invalid(self):
        t = ReflectionLifecycleTask()
        events = [_ev("a", 100.0), "bad", None, _ev("b", 101.0)]
        n = t.push_events(events)
        self.assertEqual(n, 2)
        self.assertEqual(t.pending_count, 2)

    def test_push_events_truncates(self):
        t = ReflectionLifecycleTask(max_events=3)
        events = [_ev("a", 100.0 + i) for i in range(10)]
        t.push_events(events)
        # 容量限制: 最多 3 个
        self.assertLessEqual(t.pending_count, 3)

    def test_clear_pending(self):
        t = ReflectionLifecycleTask()
        t.push_events([_ev("a", 100.0)])
        n = t.clear_pending()
        self.assertEqual(n, 1)
        self.assertEqual(t.pending_count, 0)


class TestReflectionLifecycleTaskExecute(unittest.TestCase):
    def test_execute_no_events_skip(self):
        t = ReflectionLifecycleTask()
        ctx = _FakeContext(tick=1)
        result_event = t._do_execute(ctx)
        self.assertIsInstance(result_event, IntegrationEvent)
        self.assertEqual(t.tick_skipped_count, 1)
        self.assertEqual(t.tick_reflection_count, 0)
        # 应返回 skip 事件
        self.assertEqual(result_event.payload.get("status"), "skip")

    def test_execute_with_events(self):
        t = ReflectionLifecycleTask()
        ctx = _FakeContext(tick=1)
        t.push_events([_ev("a", 100.0 + i, topic="test") for i in range(3)])
        result_event = t._do_execute(ctx)
        self.assertIsInstance(result_event, IntegrationEvent)
        # 应返回 REFLECTION_COMPLETED
        self.assertEqual(result_event.event_type, INTEGRATION_REFLECTION_COMPLETED)
        self.assertEqual(t.tick_reflection_count, 1)

    def test_execute_payload_contains_required(self):
        t = ReflectionLifecycleTask()
        ctx = _FakeContext(tick=5)
        t.push_events([_ev("a", 100.0 + i, topic="test") for i in range(3)])
        result_event = t._do_execute(ctx)
        p = result_event.payload
        self.assertIn("reflection_id", p)
        self.assertIn("reflection_type", p)
        self.assertIn("source_event_ids", p)
        self.assertIn("insight_count", p)
        self.assertIn("confidence", p)
        self.assertEqual(p["tick"], 5)

    def test_execute_multiple_runs(self):
        t = ReflectionLifecycleTask()
        ctx = _FakeContext(tick=1)
        # 第一次: 有事件 → 完成
        t.push_events([_ev("a", 100.0)])
        t._do_execute(ctx)
        # 第二次: 无事件 → skip
        t._do_execute(ctx)
        self.assertEqual(t.tick_reflection_count, 1)
        self.assertEqual(t.tick_skipped_count, 1)

    def test_execute_returns_integration_event(self):
        t = ReflectionLifecycleTask()
        result_event = t._do_execute(_FakeContext())
        self.assertIsInstance(result_event, IntegrationEvent)


class TestReflectionLifecycleTaskProperties(unittest.TestCase):
    def test_triggered_event_type(self):
        t = ReflectionLifecycleTask()
        self.assertEqual(t.triggered_event_type, INTEGRATION_REFLECTION_COMPLETED)

    def test_schema_version(self):
        t = ReflectionLifecycleTask()
        self.assertEqual(t.schema_version, REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION)

    def test_last_reflection_id_initial(self):
        t = ReflectionLifecycleTask()
        self.assertEqual(t.last_reflection_id, "")

    def test_describe(self):
        t = ReflectionLifecycleTask()
        t.push_events([_ev("a", 100.0)])
        t._do_execute(_FakeContext())
        d = t.describe()
        self.assertEqual(d["tick_reflection_count"], 1)
        self.assertEqual(d["schema_version"], REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION)


class TestReflectionLifecycleTaskInterval(unittest.TestCase):
    def test_interval_300_default(self):
        t = ReflectionLifecycleTask()
        self.assertEqual(t.interval_seconds, 300.0)

    def test_custom_interval(self):
        t = ReflectionLifecycleTask(interval_seconds=120.0)
        self.assertEqual(t.interval_seconds, 120.0)

    def test_invalid_interval_uses_default(self):
        t = ReflectionLifecycleTask(interval_seconds="bad")  # type: ignore[arg-type]
        self.assertEqual(t.interval_seconds, 300.0)


class TestReflectionLifecycleTaskIntegration(unittest.TestCase):
    """集成: Task 走完整 RUN 流程。"""

    def test_full_flow(self):
        t = ReflectionLifecycleTask(interval_seconds=0.0)
        ctx = _FakeContext(tick=1)
        events = [_ev(f"a{i}", 100.0 + i, topic=f"t{i % 2}") for i in range(5)]
        t.push_events(events)
        result_event = t._do_execute(ctx)
        self.assertEqual(result_event.event_type, INTEGRATION_REFLECTION_COMPLETED)
        # 至少产生 insight
        p = result_event.payload
        self.assertGreaterEqual(p["insight_count"], 0)
        # 完成后 pending 已清空
        self.assertEqual(t.pending_count, 0)


if __name__ == "__main__":
    unittest.main()
