# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_lifecycle_tasks.py

Phase 5.0-D2 Step 3: 5 个 LifecycleTask(Memory/Emotion/Growth/Personality/Relationship)单元测试。

覆盖:
- BaseIntegrationTask:异常注入非 BaseAdapter 抛错,execution/success/failure 统计,_do_execute 校验
- MemoryLifecycleTask:触发 INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,payload 包含 task_id
- EmotionLifecycleTask:触发 INTEGRATION_EMOTION_SHOULD_DECAY
- GrowthLifecycleTask:触发 INTEGRATION_GROWTH_SHOULD_PROPOSE
- PersonalityLifecycleTask:触发 INTEGRATION_PERSONALITY_SHOULD_SYNC
- RelationshipLifecycleTask:触发 INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE
- 5 个 Task 通过 LifecycleManager tick() 可调度(Skeleton 验证)
- Task 不 import 任何业务模块(由 constants 验证)
"""
import unittest
from typing import Any, List, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
    IntegrationEvent,
)
from src.runtime.integration.tasks import (
    BaseIntegrationTask,
    EmotionLifecycleTask,
    GrowthLifecycleTask,
    IntegrationTaskError,
    MemoryLifecycleTask,
    PersonalityLifecycleTask,
    RelationshipAdapter,
    RelationshipLifecycleTask,
)
from src.runtime.integration.tasks.base_integration_task import (
    BaseIntegrationTask as BaseIntegrationTaskDirect,
)
from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle.internal.clock import SystemClock


# ============================================================
# Test Fixtures
# ============================================================
class _CapturingAdapter:
    """测试用:捕获所有 emit_* 调用产生的事件。"""

    def __init__(self, *, name: str, owner: str) -> None:
        self.name = name
        self.owner = owner
        self.events: List[IntegrationEvent] = []

    def emit(self, event: IntegrationEvent) -> None:
        self.events.append(event)


class _FakeContext:
    """Skeleton Task _do_execute 使用的 context 替身。"""

    def __init__(self, tick: Optional[int] = None) -> None:
        self._tick = tick
        self._now = 0.0

    def now(self) -> float:
        return self._now

    @property
    def tick(self) -> Optional[int]:
        return self._tick


# ============================================================
# BaseIntegrationTask Tests
# ============================================================
class TestBaseIntegrationTaskInit(unittest.TestCase):
    def test_require_base_adapter_instance(self):
        """非 BaseAdapter 实例应抛 IntegrationTaskError。"""
        with self.assertRaises(IntegrationTaskError):
            BaseIntegrationTask(
                task_id="t1",
                owner="memory",
                adapter="not_an_adapter",  # type: ignore[arg-type]
            )

    def test_default_intervals(self):
        """未指定 interval 时使用默认 60s。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        t = MemoryLifecycleTask(adapter=MemoryAdapter())
        self.assertEqual(t.interval_seconds, 300.0)  # Memory 默认 300s
        self.assertEqual(t.task_id, "memory_lifecycle_task")
        self.assertEqual(t.owner, "memory")
        self.assertEqual(t.priority, 6)

    def test_override_interval_and_priority(self):
        """构造时覆盖 interval / priority。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        t = MemoryLifecycleTask(
            adapter=MemoryAdapter(),
            interval_seconds=12.5,
            priority=9,
        )
        self.assertEqual(t.interval_seconds, 12.5)
        self.assertEqual(t.priority, 9)


class TestBaseIntegrationTaskExecute(unittest.TestCase):
    def test_execute_returns_dict_with_event_metrics(self):
        """execute() 返回 dict 包含 events / metrics。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        t = MemoryLifecycleTask(adapter=MemoryAdapter())
        ctx = _FakeContext(tick=7)
        ret = t.execute(ctx)
        self.assertIsInstance(ret, dict)
        self.assertIn("events", ret)
        self.assertEqual(len(ret["events"]), 1)
        m = ret["metrics"]
        self.assertEqual(m["adapter_name"], "memory_adapter")
        self.assertEqual(m["event_type"], INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)

    def test_execute_records_last_event(self):
        """execute() 后 last_event / last_event_type 被记录。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        t = MemoryLifecycleTask(adapter=MemoryAdapter())
        t.execute(_FakeContext(tick=1))
        self.assertIsNotNone(t.last_event)
        self.assertEqual(t.last_event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(t.execution_count, 1)
        self.assertEqual(t.success_count, 0)  # on_success 在 manager 调用 run_safely 后才 +1

    def test_execute_rejects_non_integration_event(self):
        """_do_execute 返回非 IntegrationEvent 应抛 IntegrationTaskError。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        class _BadTask(MemoryLifecycleTask):
            def _do_execute(self, context: Any) -> Any:  # type: ignore[override]
                return "not a IntegrationEvent"

        t = _BadTask(adapter=MemoryAdapter())
        with self.assertRaises(IntegrationTaskError):
            t.execute(_FakeContext())
        self.assertGreaterEqual(t.failure_count, 1)

    def test_execute_propagates_exception(self):
        """_do_execute 抛错时 exception 上抛,failure_count 增加。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        class _BoomTask(MemoryLifecycleTask):
            def _do_execute(self, context: Any) -> IntegrationEvent:
                raise RuntimeError("boom")

        t = _BoomTask(adapter=MemoryAdapter())
        with self.assertRaises(RuntimeError):
            t.execute(_FakeContext())
        self.assertEqual(t.execution_count, 1)
        self.assertGreaterEqual(t.failure_count, 1)
        self.assertIn("boom", t.last_error)


class TestBaseIntegrationTaskHooks(unittest.TestCase):
    def test_on_success_increments(self):
        from src.runtime.lifecycle.lifecycle_result import (
            LifecycleResult,
            LifecycleStatus,
        )

        t = _build_minimal_task()
        r = LifecycleResult.success("t_minimal", started_at=0.0, ended_at=0.0)
        t.on_success(r)
        self.assertEqual(t.success_count, 1)

    def test_on_failure_increments(self):
        from src.runtime.lifecycle.lifecycle_errors import (
            ErrorCategory,
            LifecycleError,
        )

        t = _build_minimal_task()
        e = LifecycleError(ErrorCategory.INTERNAL, "x")
        t.on_failure(e)
        self.assertEqual(t.failure_count, 1)
        self.assertIn("x", t.last_error)

    def test_describe_includes_state(self):
        t = _build_minimal_task()
        d = t.describe()
        self.assertEqual(d["task_id"], "t_minimal")
        self.assertEqual(d["owner"], "memory")
        self.assertIn("execution_count", d)
        self.assertIn("adapter_name", d)


def _build_minimal_task() -> BaseIntegrationTaskDirect:
    """构建一个最简 BaseIntegrationTask 用于钩子测试。"""
    from src.runtime.integration.adapters.memory_adapter import MemoryAdapter

    class _MinimalTask(BaseIntegrationTask):
        def _do_execute(self, context: Any) -> IntegrationEvent:
            return self._adapter.emit_should_consolidate(  # type: ignore[attr-defined]
                payload={"task_id": self.task_id},
                related_ids=[self.task_id],
            )

    return _MinimalTask(
        task_id="t_minimal",
        owner="memory",
        adapter=MemoryAdapter(),
        interval_seconds=10.0,
    )


# ============================================================
# Per-system Task Tests
# ============================================================
class TestMemoryLifecycleTask(unittest.TestCase):
    def test_default_metadata(self):
        t = MemoryLifecycleTask()
        self.assertEqual(t.task_id, "memory_lifecycle_task")
        self.assertEqual(t.owner, "memory")
        self.assertEqual(t.interval_seconds, 300.0)
        self.assertEqual(t.priority, 6)
        self.assertEqual(t.triggered_event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)

    def test_do_execute_emits_event_with_task_id(self):
        t = MemoryLifecycleTask()
        ctx = _FakeContext(tick=11)
        ev = t._do_execute(ctx)
        self.assertIsInstance(ev, IntegrationEvent)
        self.assertEqual(ev.event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(ev.payload.get("task_id"), "memory_lifecycle_task")
        self.assertEqual(ev.payload.get("tick"), 11)
        self.assertIn("memory_lifecycle_task", ev.related_ids)


class TestEmotionLifecycleTask(unittest.TestCase):
    def test_default_metadata(self):
        t = EmotionLifecycleTask()
        self.assertEqual(t.task_id, "emotion_lifecycle_task")
        self.assertEqual(t.owner, "emotion")
        self.assertEqual(t.interval_seconds, 60.0)
        self.assertEqual(t.priority, 7)
        self.assertEqual(t.triggered_event_type, INTEGRATION_EMOTION_SHOULD_DECAY)

    def test_do_execute_emits_decay_event(self):
        t = EmotionLifecycleTask()
        ev = t._do_execute(_FakeContext())
        self.assertEqual(ev.event_type, INTEGRATION_EMOTION_SHOULD_DECAY)
        self.assertEqual(ev.payload.get("task_id"), "emotion_lifecycle_task")


class TestGrowthLifecycleTask(unittest.TestCase):
    def test_default_metadata(self):
        t = GrowthLifecycleTask()
        self.assertEqual(t.task_id, "growth_lifecycle_task")
        self.assertEqual(t.owner, "growth")
        self.assertEqual(t.interval_seconds, 1800.0)
        self.assertEqual(t.priority, 4)
        self.assertEqual(t.triggered_event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)

    def test_do_execute_emits_propose_event(self):
        t = GrowthLifecycleTask()
        ev = t._do_execute(_FakeContext(tick=99))
        self.assertEqual(ev.event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)
        self.assertEqual(ev.payload.get("task_id"), "growth_lifecycle_task")
        self.assertEqual(ev.payload.get("tick"), 99)


class TestPersonalityLifecycleTask(unittest.TestCase):
    def test_default_metadata(self):
        t = PersonalityLifecycleTask()
        self.assertEqual(t.task_id, "personality_lifecycle_task")
        self.assertEqual(t.owner, "personality")
        self.assertEqual(t.interval_seconds, 3600.0)
        self.assertEqual(t.priority, 3)
        self.assertEqual(t.triggered_event_type, INTEGRATION_PERSONALITY_SHOULD_SYNC)

    def test_do_execute_emits_sync_event(self):
        t = PersonalityLifecycleTask()
        ev = t._do_execute(_FakeContext())
        self.assertEqual(ev.event_type, INTEGRATION_PERSONALITY_SHOULD_SYNC)
        self.assertEqual(ev.payload.get("task_id"), "personality_lifecycle_task")


class TestRelationshipAdapterAndTask(unittest.TestCase):
    def test_relationship_adapter_identity(self):
        a = RelationshipAdapter()
        self.assertEqual(a.name, "relationship_adapter")
        self.assertEqual(a.owner, "relationship")
        h = a.health()
        self.assertEqual(h["target_kind"], "relationship_manager")
        self.assertTrue(h["skeleton"])

    def test_relationship_lifecycle_task_metadata(self):
        t = RelationshipLifecycleTask()
        self.assertEqual(t.task_id, "relationship_lifecycle_task")
        self.assertEqual(t.owner, "relationship")
        self.assertEqual(t.interval_seconds, 600.0)
        self.assertEqual(t.priority, 5)
        self.assertEqual(
            t.triggered_event_type,
            INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
        )

    def test_relationship_lifecycle_task_do_execute(self):
        t = RelationshipLifecycleTask()
        ev = t._do_execute(_FakeContext(tick=3))
        self.assertIsInstance(ev, IntegrationEvent)
        self.assertEqual(ev.event_type, INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE)
        self.assertEqual(ev.payload.get("task_id"), "relationship_lifecycle_task")
        self.assertEqual(ev.payload.get("tick"), 3)
        # Adapter 应该记录到 emitted_count
        self.assertEqual(t.adapter.emitted_count, 1)


# ============================================================
# 全部 5 个 Task 都使用真实 LifecycleManager 调度一次
# ============================================================
class TestFiveTasksRegistration(unittest.TestCase):
    def test_register_and_run_all_five_tasks(self):
        """5 个 Task 都应能注册到 LifecycleManager 并执行成功。"""
        from src.runtime.lifecycle.internal.clock import SystemClock

        manager = LifecycleManager(clock=SystemClock())
        manager.register(MemoryLifecycleTask())
        manager.register(EmotionLifecycleTask())
        manager.register(GrowthLifecycleTask())
        manager.register(PersonalityLifecycleTask())
        manager.register(RelationshipLifecycleTask())
        self.assertEqual(manager.task_count(), 5)

        # 启动
        self.assertTrue(manager.start())
        results = manager.tick()
        self.assertEqual(len(results), 5)

        # 全部 SUCCESS
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        for r in results:
            self.assertEqual(r.status, LifecycleStatus.SUCCESS)
            self.assertIn(r.task_id, {
                "memory_lifecycle_task",
                "emotion_lifecycle_task",
                "growth_lifecycle_task",
                "personality_lifecycle_task",
                "relationship_lifecycle_task",
            })

        manager.stop()

    def test_one_task_failure_does_not_block_others(self):
        """一个 Task 抛错时,其他 Task 仍能正常完成。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.lifecycle.internal.clock import SystemClock
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask,
        )

        class _BoomMemoryTask(MemoryLifecycleTask):
            def _do_execute(self, context: Any) -> IntegrationEvent:
                raise RuntimeError("memory boom")

        manager = LifecycleManager(clock=SystemClock())
        manager.register(_BoomMemoryTask(adapter=MemoryAdapter()))
        manager.register(EmotionLifecycleTask())
        manager.register(GrowthLifecycleTask())
        manager.register(PersonalityLifecycleTask())
        manager.register(RelationshipLifecycleTask())

        self.assertTrue(manager.start())
        results = manager.tick()
        self.assertEqual(len(results), 5)
        # 找到 memory 的结果
        mem_result = next(r for r in results if r.task_id == "memory_lifecycle_task")
        emo_result = next(r for r in results if r.task_id == "emotion_lifecycle_task")
        self.assertEqual(mem_result.status, LifecycleStatus.FAILED)
        self.assertEqual(emo_result.status, LifecycleStatus.SUCCESS)
        manager.stop()


# ============================================================
# 业务模块依赖检查(Task 文件不 import 业务模块)
# ============================================================
class TestNoBusinessModuleImport(unittest.TestCase):
    """Task 模块不导入 src.memory / src.growth / src.personality / src.emotion / src.relationship。"""

    FORBIDDEN_MODULES = (
        "src.memory",
        "src.growth",
        "src.personality",
        "src.emotion",
        "src.relationship",
    )

    def _check_file(self, rel_path: str) -> None:
        import os
        import re

        path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "src", "runtime", "integration", "tasks",
            rel_path,
        )
        path = os.path.abspath(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # 仅检查真正的 import 语句
        import_pattern = re.compile(
            r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)",
            re.MULTILINE,
        )
        for m in import_pattern.finditer(content):
            mod = m.group(1) or m.group(2) or ""
            mod = mod.strip()
            for forbidden in self.FORBIDDEN_MODULES:
                if mod == forbidden or mod.startswith(forbidden + "."):
                    self.fail(
                        f"{rel_path} 含禁止 import: '{mod}' "
                        f"(禁止引用 {forbidden} 业务模块)"
                    )

    def test_memory_task_no_business_import(self):
        self._check_file("memory_lifecycle_task.py")

    def test_emotion_task_no_business_import(self):
        self._check_file("emotion_lifecycle_task.py")

    def test_growth_task_no_business_import(self):
        self._check_file("growth_lifecycle_task.py")

    def test_personality_task_no_business_import(self):
        self._check_file("personality_lifecycle_task.py")

    def test_relationship_task_no_business_import(self):
        self._check_file("relationship_lifecycle_task.py")

    def test_relationship_adapter_no_business_import(self):
        self._check_file("relationship_adapter.py")


if __name__ == "__main__":
    unittest.main()
