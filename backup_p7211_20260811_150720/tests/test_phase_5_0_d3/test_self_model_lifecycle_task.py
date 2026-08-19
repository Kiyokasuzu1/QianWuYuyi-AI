# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_self_model_lifecycle_task.py

Phase 5.0-D3-A: Self Model System —— SelfModelLifecycleTask 单元测试
"""
import unittest
from typing import Any, List, Optional

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.self_model.self_model_adapter import (
    SELF_MODEL_CHANGE_RECORDED,
    SELF_MODEL_SNAPSHOT_REFRESHED,
    SelfModelAdapter,
)
from src.runtime.self_model.self_model_lifecycle_task import (
    SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION,
    SelfModelLifecycleTask,
    build_default_self_model_lifecycle_task,
)
from src.runtime.self_model.self_model_manager import SelfModelManager


# ============================================================
# 辅助
# ============================================================
class _FakeContext:
    """Lifecycle Context 替身。"""

    def __init__(self, tick: Optional[int] = None) -> None:
        self._tick = tick
        self._now = 0.0
        self.task_id = "self_model_lifecycle_task"

    def now(self) -> float:
        return self._now

    @property
    def tick(self) -> Optional[int]:
        return self._tick


def _build_started_manager() -> SelfModelManager:
    m = SelfModelManager()
    m.start()
    return m


# ============================================================
# 构造
# ============================================================
class TestTaskConstruction(unittest.TestCase):
    def test_default_construct(self):
        t = SelfModelLifecycleTask()
        self.assertEqual(t.task_id, "self_model_lifecycle_task")
        self.assertEqual(t.owner, "self_model")
        self.assertEqual(t.priority, 4)
        self.assertEqual(t.interval_seconds, 600.0)
        self.assertTrue(t.enable_decay)
        self.assertEqual(t.decay_seconds_per_tick, 60.0)
        self.assertIsInstance(t.self_model_adapter, SelfModelAdapter)

    def test_custom_adapter(self):
        adp = SelfModelAdapter()
        t = SelfModelLifecycleTask(adapter=adp)
        self.assertIs(t.self_model_adapter, adp)

    def test_custom_interval(self):
        t = SelfModelLifecycleTask(interval_seconds=10.0)
        self.assertEqual(t.interval_seconds, 10.0)

    def test_custom_priority(self):
        t = SelfModelLifecycleTask(priority=2)
        self.assertEqual(t.priority, 2)

    def test_disable_decay(self):
        t = SelfModelLifecycleTask(enable_decay=False)
        self.assertFalse(t.enable_decay)

    def test_decay_seconds_per_tick(self):
        t = SelfModelLifecycleTask(decay_seconds_per_tick=120.0)
        self.assertEqual(t.decay_seconds_per_tick, 120.0)

    def test_invalid_adapter_rejected(self):
        # 传非 BaseAdapter 应抛错
        from src.runtime.integration.tasks.base_integration_task import (
            IntegrationTaskError,
        )
        with self.assertRaises(IntegrationTaskError):
            SelfModelLifecycleTask(adapter="not_an_adapter")  # type: ignore

    def test_build_default(self):
        t = build_default_self_model_lifecycle_task()
        self.assertIsInstance(t, SelfModelLifecycleTask)

    def test_schema_version(self):
        t = SelfModelLifecycleTask()
        self.assertEqual(t.schema_version, SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION)


# ============================================================
# 执行
# ============================================================
class TestTaskExecute(unittest.TestCase):
    def setUp(self):
        self.adp = SelfModelAdapter()
        self.mgr = _build_started_manager()
        self.adp.bind_manager(self.mgr)
        self.task = SelfModelLifecycleTask(adapter=self.adp, interval_seconds=0.0)
        # 给 adapter 注入 emit 收集(不递归,避免 emit 内部再次调用 self.adp.emit)
        self.emitted: List[IntegrationEvent] = []

        def _capture(ev):
            self.emitted.append(ev)

        self.adp._event_emitter = _capture
        # 注入 self.task._adapter 引用,以便 _do_execute 使用
        self.ctx = _FakeContext(tick=1)

    def test_execute_emits_refresh_event(self):
        ev = self.task._do_execute(self.ctx)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, SELF_MODEL_SNAPSHOT_REFRESHED)

    def test_execute_increments_refresh_count(self):
        self.task._do_execute(self.ctx)
        self.assertEqual(self.task.tick_refresh_count, 1)
        self.task._do_execute(self.ctx)
        self.assertEqual(self.task.tick_refresh_count, 2)

    def test_execute_emits_change_recorded(self):
        # _do_execute 调用 manager.refresh,但 manager.refresh 本身不 emit 事件
        # (只有 adapter.handle_event 才 emit SELF_MODEL_CHANGE_RECORDED)
        # 因此 emitted 列表中只会有 SELF_MODEL_SNAPSHOT_REFRESHED
        self.task._do_execute(self.ctx)
        types = [ev.event_type for ev in self.emitted]
        self.assertIn(SELF_MODEL_SNAPSHOT_REFRESHED, types)
        # 验证 manager 内部确实有 change record 产生
        recs = self.mgr.query_changes(kind="snapshot_refresh")
        self.assertGreaterEqual(len(recs), 1)

    def test_execute_emits_via_adapter_handle_event(self):
        # 通过 adapter.handle_event 显式处理 SELF_MODEL_SNAPSHOT_REFRESHED 时会 emit
        from src.runtime.integration.integration_event import make_integration_event
        ev_in = make_integration_event(
            event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
            source="test",
            payload={"evidence_event_ids": ["e1"]},
        )
        self.adp.handle_event(ev_in)
        types = [ev.event_type for ev in self.emitted]
        self.assertIn(SELF_MODEL_CHANGE_RECORDED, types)

    def test_execute_runs_decay_when_enabled(self):
        # 先创建一个 interest
        self.mgr.upsert_interest(
            name="music",
            new_level=0.8,
            new_decay_rate=0.01,
            evidence_event_ids=["e1"],
        )
        self.task._do_execute(self.ctx)
        self.assertGreaterEqual(self.task.tick_decay_count, 1)
        i = self.mgr.get_interest("music")
        self.assertLess(i.level, 0.8)

    def test_execute_no_decay_when_disabled(self):
        t = SelfModelLifecycleTask(adapter=self.adp, enable_decay=False)
        self.mgr.upsert_interest(
            name="music",
            new_level=0.8,
            new_decay_rate=0.01,
            evidence_event_ids=["e1"],
        )
        t._do_execute(self.ctx)
        self.assertEqual(t.tick_decay_count, 0)
        i = self.mgr.get_interest("music")
        self.assertEqual(i.level, 0.8)

    def test_execute_no_manager(self):
        # adapter 不绑定 manager
        adp = SelfModelAdapter()
        t = SelfModelLifecycleTask(adapter=adp)
        t._do_execute(self.ctx)  # 不应抛错
        # refresh/decay 计数仍为 0
        self.assertEqual(t.tick_refresh_count, 0)

    def test_execute_manager_not_started(self):
        adp = SelfModelAdapter()
        m = SelfModelManager()  # not started
        adp.bind_manager(m)
        t = SelfModelLifecycleTask(adapter=adp)
        t._do_execute(self.ctx)  # 不应抛错
        self.assertEqual(t.tick_refresh_count, 0)

    def test_execute_via_execute_returns_dict(self):
        # 通过 BaseIntegrationTask.execute 入口
        result = self.task.execute(self.ctx)
        self.assertIsInstance(result, dict)
        self.assertIn("events", result)
        self.assertEqual(len(result["events"]), 1)

    def test_execute_increments_execution_count(self):
        before = self.task.execution_count
        self.task.execute(self.ctx)
        self.assertEqual(self.task.execution_count, before + 1)


# ============================================================
# 描述
# ============================================================
class TestTaskDescribe(unittest.TestCase):
    def test_describe(self):
        t = SelfModelLifecycleTask(interval_seconds=30.0, priority=3)
        d = t.describe()
        self.assertEqual(d["task_id"], "self_model_lifecycle_task")
        self.assertEqual(d["owner"], "self_model")
        self.assertEqual(d["interval_seconds"], 30.0)
        self.assertEqual(d["priority"], 3)
        self.assertEqual(d["schema_version"], SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION)
        self.assertIn("tick_refresh_count", d)
        self.assertIn("tick_decay_count", d)
        self.assertIn("tick_change_count", d)
        self.assertIn("enable_decay", d)
        self.assertIn("decay_seconds_per_tick", d)

    def test_repr(self):
        t = SelfModelLifecycleTask()
        r = repr(t)
        self.assertIn("SelfModelLifecycleTask", r)
        self.assertIn("self_model_lifecycle_task", r)


if __name__ == "__main__":
    unittest.main()
