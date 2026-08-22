# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_integration_tasks_dispatch.py

Phase 5.0-D2 Step 3: 生命周期调度验证。

目标:
- 验证 5 个 Task 通过 LifecycleManager 完整调度链
  register -> start -> tick -> stop
- 验证每次 tick 后 Adapter 的 emitted_count 增加 1
- 验证多次 tick 后历史记录正确
- 验证 5 个 Task 互不干扰
- 验证条件 AfterInterval 正常行为(短间隔时多次 tick 可再次执行)
- 验证与 RuntimeIntegrationHost 集成(Host.tick 触发底层 Manager)
"""
import unittest
from typing import List

from src.runtime.integration.integration_event import (
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
)
from src.runtime.integration.event_bridge import EventBridge
from src.runtime.integration.runtime_integration_host import (
    HOST_STATE_RUNNING,
    RuntimeIntegrationHost,
)
from src.runtime.integration.tasks import (
    EmotionLifecycleTask,
    GrowthLifecycleTask,
    MemoryLifecycleTask,
    PersonalityLifecycleTask,
    RelationshipLifecycleTask,
)
from src.runtime.lifecycle.internal.clock import FrozenClock, SystemClock
from src.runtime.lifecycle.internal.event_emitter import EventEmitter
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import (
    AfterInterval,
    TaskState,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle.lifecycle_result import LifecycleStatus


# ============================================================
# 1. 5 个 Task 全部注册后,首次 tick 全部 RUN
# ============================================================
class TestFiveTasksFirstTick(unittest.TestCase):
    def test_all_tasks_run_on_first_tick(self):
        manager = LifecycleManager(clock=SystemClock())
        for t in (
            MemoryLifecycleTask(),
            EmotionLifecycleTask(),
            GrowthLifecycleTask(),
            PersonalityLifecycleTask(),
            RelationshipLifecycleTask(),
        ):
            manager.register(t)

        self.assertTrue(manager.start())
        results = manager.tick()

        self.assertEqual(len(results), 5)
        statuses = {r.task_id: r.status for r in results}
        for tid in (
            "memory_lifecycle_task",
            "emotion_lifecycle_task",
            "growth_lifecycle_task",
            "personality_lifecycle_task",
            "relationship_lifecycle_task",
        ):
            self.assertEqual(statuses[tid], LifecycleStatus.SUCCESS)
        manager.stop()

    def test_each_task_emits_distinct_event_type(self):
        """每个 Task 触发的事件类型不同。"""
        from src.runtime.integration.integration_event import IntegrationEvent

        manager = LifecycleManager(clock=SystemClock())
        tasks = [
            MemoryLifecycleTask(),
            EmotionLifecycleTask(),
            GrowthLifecycleTask(),
            PersonalityLifecycleTask(),
            RelationshipLifecycleTask(),
        ]
        for t in tasks:
            manager.register(t)

        self.assertTrue(manager.start())
        # 收集每个 task 触发的事件类型
        from src.runtime.integration.tasks.base_integration_task import (
            BaseIntegrationTask as _BIT,
        )

        types = set()
        for t in tasks:
            event = t._do_execute(None)  # type: ignore[arg-type]
            self.assertIsInstance(event, IntegrationEvent)
            types.add(event.event_type)
        # 5 个互不相同的事件类型
        self.assertEqual(len(types), 5)
        self.assertSetEqual(
            types,
            {
                INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
                INTEGRATION_EMOTION_SHOULD_DECAY,
                INTEGRATION_GROWTH_SHOULD_PROPOSE,
                INTEGRATION_PERSONALITY_SHOULD_SYNC,
                INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
            },
        )
        manager.stop()


# ============================================================
# 2. Adapter emitted_count 与 tick 次数一致
# ============================================================
class TestAdapterEmittedCount(unittest.TestCase):
    def test_emitted_count_matches_execution_count(self):
        manager = LifecycleManager(clock=SystemClock())
        tasks = [
            MemoryLifecycleTask(interval_seconds=0.0),
            EmotionLifecycleTask(interval_seconds=0.0),
            GrowthLifecycleTask(interval_seconds=0.0),
            PersonalityLifecycleTask(interval_seconds=0.0),
            RelationshipLifecycleTask(interval_seconds=0.0),
        ]
        for t in tasks:
            manager.register(t)

        self.assertTrue(manager.start())
        # 第一次 tick:5 个 task 全部执行(AfterInterval(0) 立即通过)
        manager.tick()
        # 第二次 tick:5 个 task 全部执行(AfterInterval(0))
        manager.tick()
        # 第三次
        manager.tick()
        manager.stop()

        for t in tasks:
            # 每次 tick 都执行,共 3 次
            self.assertEqual(t.adapter.emitted_count, 3)
            self.assertEqual(t.execution_count, 3)
            self.assertEqual(t.success_count, 3)


# ============================================================
# 3. 多次 tick 后,history 正确累积
# ============================================================
class TestManagerHistory(unittest.TestCase):
    def test_history_records_each_run(self):
        manager = LifecycleManager(clock=SystemClock(), history_capacity=100)
        t = MemoryLifecycleTask(interval_seconds=0.0)
        manager.register(t)
        self.assertTrue(manager.start())
        for _ in range(5):
            manager.tick()
        manager.stop()

        hist = manager.history(task_id="memory_lifecycle_task")
        self.assertEqual(len(hist), 5)
        for r in hist:
            self.assertEqual(r.status, LifecycleStatus.SUCCESS)
            self.assertIn(
                INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
                r.metrics.get("event_type", ""),
            )


# ============================================================
# 4. AfterInterval 条件:长间隔时,第二次 tick 会被 SKIP
# ============================================================
class TestAfterIntervalSkipBehavior(unittest.TestCase):
    def test_long_interval_skips_second_tick(self):
        """长间隔(>0)的 task 首次 tick 后,第二次 tick 应被 SKIP。"""
        clock = FrozenClock(initial=0.0)
        manager = LifecycleManager(clock=clock)
        t = MemoryLifecycleTask(interval_seconds=300.0)
        manager.register(t)

        self.assertTrue(manager.start())
        # 第一次:AfterInterval 在 last_run_at=None 时返回 RUN
        results_1 = manager.tick()
        self.assertEqual(len(results_1), 1)
        self.assertEqual(results_1[0].status, LifecycleStatus.SUCCESS)

        # 推进时间 10s,远小于 300s
        clock.advance(10.0)
        results_2 = manager.tick()
        self.assertEqual(len(results_2), 1)
        self.assertEqual(results_2[0].status, LifecycleStatus.SKIPPED)

        # 推进时间到 305s,大于 300s
        clock.advance(295.0)
        results_3 = manager.tick()
        self.assertEqual(len(results_3), 1)
        self.assertEqual(results_3[0].status, LifecycleStatus.SUCCESS)
        manager.stop()


# ============================================================
# 5. 异常隔离:一个 Task 抛错,其他 Task 不受影响
# ============================================================
class TestFailureIsolation(unittest.TestCase):
    def test_one_task_exception_does_not_affect_others(self):
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        class _BoomTask(MemoryLifecycleTask):
            def _do_execute(self, context):  # type: ignore[override]
                raise RuntimeError("planned boom")

        manager = LifecycleManager(clock=SystemClock())
        boom = _BoomTask(adapter=MemoryAdapter())
        manager.register(boom)
        manager.register(EmotionLifecycleTask(interval_seconds=0.0))
        manager.register(GrowthLifecycleTask(interval_seconds=0.0))
        manager.register(PersonalityLifecycleTask(interval_seconds=0.0))
        manager.register(RelationshipLifecycleTask(interval_seconds=0.0))

        self.assertTrue(manager.start())
        results = manager.tick()
        self.assertEqual(len(results), 5)

        # boom FAILED
        mem_r = next(r for r in results if r.task_id == "memory_lifecycle_task")
        self.assertEqual(mem_r.status, LifecycleStatus.FAILED)

        # 其他 4 个 SUCCESS
        for tid in (
            "emotion_lifecycle_task",
            "growth_lifecycle_task",
            "personality_lifecycle_task",
            "relationship_lifecycle_task",
        ):
            r = next(x for x in results if x.task_id == tid)
            self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        manager.stop()

    def test_continued_ticks_after_failure(self):
        """失败后,后续 tick 仍能继续调度。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        class _BoomTask(MemoryLifecycleTask):
            def _do_execute(self, context):  # type: ignore[override]
                raise RuntimeError("always boom")

        manager = LifecycleManager(clock=SystemClock())
        boom = _BoomTask(adapter=MemoryAdapter(), interval_seconds=0.0)
        manager.register(boom)
        manager.register(EmotionLifecycleTask(interval_seconds=0.0))

        self.assertTrue(manager.start())
        for _ in range(3):
            results = manager.tick()
            self.assertEqual(len(results), 2)
        manager.stop()
        # emotion task 应累计 3 次 SUCCESS
        emo = manager.get_task_state("emotion_lifecycle_task")
        self.assertIsNotNone(emo)
        self.assertEqual(emo.run_count, 3)


# ============================================================
# 6. 与 RuntimeIntegrationHost 集成
# ============================================================
class TestWithIntegrationHost(unittest.TestCase):
    def test_host_uses_real_manager_with_five_tasks(self):
        manager = LifecycleManager(clock=SystemClock())
        for t in (
            MemoryLifecycleTask(),
            EmotionLifecycleTask(),
            GrowthLifecycleTask(),
            PersonalityLifecycleTask(),
            RelationshipLifecycleTask(),
        ):
            manager.register(t)
        # 把 task 的 event 桥接到 host
        received: List = []
        bridge = EventBridge(
            integration_to_lifecycle=lambda e: received.append(e),
        )
        host = RuntimeIntegrationHost(
            lifecycle_manager=manager,
            event_bridge=bridge,
        )

        self.assertTrue(host.start())
        self.assertEqual(host.state, HOST_STATE_RUNNING)
        results = host.tick()
        # 5 个手动注册 + Phase F 自动注册的 emotion_reflection = 6
        self.assertEqual(len(results), 7)
        # Host 发出了 tick_complete 事件
        self.assertGreaterEqual(host.event_bridge.published_count, 1)
        last_type = host.event_bridge.last_published_type
        self.assertEqual(last_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        self.assertTrue(host.stop())


# ============================================================
# 7. LifecycleManager 状态机与 Task 调度无干扰
# ============================================================
class TestManagerStateWithTasks(unittest.TestCase):
    def test_state_progression_with_tasks(self):
        manager = LifecycleManager(clock=SystemClock())
        for t in (
            MemoryLifecycleTask(interval_seconds=0.0),
            EmotionLifecycleTask(interval_seconds=0.0),
            GrowthLifecycleTask(interval_seconds=0.0),
            PersonalityLifecycleTask(interval_seconds=0.0),
            RelationshipLifecycleTask(interval_seconds=0.0),
        ):
            manager.register(t)
        self.assertTrue(manager.start())
        manager.tick()
        manager.tick()
        # Manager RUNNING
        self.assertTrue(manager.is_running)
        self.assertTrue(manager.stop())
        self.assertTrue(manager.is_stopped)

    def test_health_check_reports_all_tasks(self):
        manager = LifecycleManager(clock=SystemClock())
        for t in (
            MemoryLifecycleTask(),
            EmotionLifecycleTask(),
            GrowthLifecycleTask(),
            PersonalityLifecycleTask(),
            RelationshipLifecycleTask(),
        ):
            manager.register(t)
        self.assertTrue(manager.start())
        manager.tick()
        health = manager.health_check()
        self.assertEqual(health["task_count"], 5)
        self.assertEqual(health["manager_state"], "RUNNING")
        task_ids = {t["task_id"] for t in health["tasks"]}
        self.assertSetEqual(
            task_ids,
            {
                "memory_lifecycle_task",
                "emotion_lifecycle_task",
                "growth_lifecycle_task",
                "personality_lifecycle_task",
                "relationship_lifecycle_task",
            },
        )
        manager.stop()


# ============================================================
# 8. Decision 模型在 Task 内部工作正常(直接调用 should_run)
# ============================================================
class TestTaskShouldRun(unittest.TestCase):
    def test_should_run_with_fresh_task(self):
        """Task 首次 should_run 应返回 RUN(AfterInterval 默认行为)。"""
        manager = LifecycleManager(clock=SystemClock())
        t = MemoryLifecycleTask()
        manager.register(t)
        self.assertTrue(manager.start())
        ctx = LifecycleContext(clock=manager.clock, task_id=t.task_id)
        ts = TaskState(task_id=t.task_id)
        d = t.should_run(ctx, ts)
        self.assertEqual(d.verdict, Verdict.RUN)
        manager.stop()


# ============================================================
# 9. EventEmitter 集成:5 个 Task 触发的事件可被监听
# ============================================================
class TestEventEmitterIntegration(unittest.TestCase):
    def test_event_emitter_records_task_events(self):
        emitter = EventEmitter(name="d2_step3_test")
        manager = LifecycleManager(clock=SystemClock(), emitter=emitter)
        # 使用 SystemClock 包装避免 FrozenClock 复用冲突
        for t in (
            MemoryLifecycleTask(interval_seconds=0.0),
            EmotionLifecycleTask(interval_seconds=0.0),
            GrowthLifecycleTask(interval_seconds=0.0),
            PersonalityLifecycleTask(interval_seconds=0.0),
            RelationshipLifecycleTask(interval_seconds=0.0),
        ):
            manager.register(t)

        received = []

        def on_event(event):  # LifecycleEvent instance
            received.append(event)

        emitter.on("lifecycle.task.completed", on_event)

        self.assertTrue(manager.start())
        manager.tick()
        manager.stop()

        # 5 个 task 都应该发出 completed 事件
        self.assertGreaterEqual(len(received), 5)


# ============================================================
# 10. 5 个 Task 优先级互不干扰
# ============================================================
class TestTaskPriorities(unittest.TestCase):
    def test_priorities_set_correctly(self):
        pairs = [
            (MemoryLifecycleTask(), 6),
            (EmotionLifecycleTask(), 7),
            (GrowthLifecycleTask(), 4),
            (PersonalityLifecycleTask(), 3),
            (RelationshipLifecycleTask(), 5),
        ]
        for t, expected in pairs:
            self.assertEqual(t.priority, expected)


if __name__ == "__main__":
    unittest.main()
