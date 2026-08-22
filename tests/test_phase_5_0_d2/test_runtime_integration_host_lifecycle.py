# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_runtime_integration_host_lifecycle.py

Phase 5.0-D2 Step 4: RuntimeIntegrationHost 生命周期闭环测试。

目标:
- 验证 RuntimeIntegrationHost 完成"启动 → 感知 → 调度 → 执行 → 产生事件 → 保存状态 → 下一轮循环"
- 验证默认行为:auto-create manager + auto-register 5 tasks
- 验证 tick 闭环:5 个 task 全部 SUCCESS,事件进入 EventLog + EventStore
- 验证多次 tick 累积效果
- 验证 lifecycle 状态机(start / stop / restart 行为)
- 验证健康检查 / status 完整性

不修改:
- src/memory/**
- src/growth/**
- src/personality/**
- src/emotion/**
- src/relationship/**
- src/runtime/lifecycle/**
- src/orchestrator/**
"""
import os
import tempfile
import unittest
import shutil
from typing import List

from src.runtime.integration.integration_event import (
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.integration_event_log import IntegrationEventLog
from src.runtime.integration.integration_event_store import IntegrationEventStore
from src.runtime.integration.runtime_integration_host import (
    HOST_STATE_CREATED,
    HOST_STATE_FAILED,
    HOST_STATE_RUNNING,
    HOST_STATE_STOPPED,
    RuntimeIntegrationHost,
    build_default_host,
)
from src.runtime.lifecycle.internal.clock import SystemClock
from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle.lifecycle_result import LifecycleStatus


# ============================================================
# 1. 默认闭环:start -> tick -> tick_complete
# ============================================================
class TestDefaultLifecycle(unittest.TestCase):
    def test_default_construct(self):
        """默认构造: state=CREATED, 5 个 adapter 已注册, lifecycle_manager=None。"""
        h = RuntimeIntegrationHost()
        self.assertEqual(h.state, HOST_STATE_CREATED)
        self.assertIsNone(h.lifecycle_manager)
        self.assertIsNotNone(h.event_log)
        self.assertIsNotNone(h.event_store)
        # 5 个 adapter
        self.assertIn("memory_adapter", h.adapters)
        self.assertIn("emotion_adapter", h.adapters)
        self.assertIn("growth_adapter", h.adapters)
        self.assertIn("personality_adapter", h.adapters)
        self.assertIn("relationship_adapter", h.adapters)

    def test_start_creates_manager_and_registers_tasks(self):
        """start 应自动创建 LifecycleManager 并注册 6 个 Task（Phase F +emotion_reflection）。"""
        h = RuntimeIntegrationHost()
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        # manager 已自动创建
        self.assertIsNotNone(h.lifecycle_manager)
        # 6 个 task 已注册（D-2 的 5 个 + Phase F 情绪反思）
        self.assertEqual(len(h.registered_task_ids), 7)
        for tid in (
            "memory_lifecycle_task",
            "emotion_lifecycle_task",
            "growth_lifecycle_task",
            "personality_lifecycle_task",
            "relationship_lifecycle_task",
            "emotion.reflection",
        ):
            self.assertIn(tid, h.registered_task_ids)

    def test_first_tick_runs_all_five_tasks(self):
        """首次 tick: 6 个 task 全部 SUCCESS,产生 6 个 IntegrationEvent + 1 tick_complete。"""
        h = RuntimeIntegrationHost()
        self.assertTrue(h.start())
        results = h.tick()
        self.assertEqual(len(results), 7)
        for r in results:
            self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        # 5 个 task 事件 + 1 tick_complete = 6 个 IntegrationEvent
        self.assertGreaterEqual(h.event_log.total_appended, 6)
        # bridge 至少发布 1 个(tick_complete)
        self.assertGreaterEqual(h.event_bridge.published_count, 1)
        # 最后发布的是 tick_complete
        self.assertEqual(
            h.event_bridge.last_published_type,
            INTEGRATION_LIFECYCLE_TICK_COMPLETE,
        )

    def test_first_tick_event_types_in_log(self):
        """EventLog 包含 5 个 task 事件类型 + 1 个 tick_complete。"""
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        log_types = set()
        for ev in h.event_log.events():
            log_types.add(ev.event_type)
        # 应包含所有 5 个 task 事件 + tick_complete
        self.assertIn(INTEGRATION_MEMORY_SHOULD_CONSOLIDATE, log_types)
        self.assertIn(INTEGRATION_EMOTION_SHOULD_DECAY, log_types)
        self.assertIn(INTEGRATION_GROWTH_SHOULD_PROPOSE, log_types)
        self.assertIn(INTEGRATION_PERSONALITY_SHOULD_SYNC, log_types)
        self.assertIn(INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE, log_types)
        self.assertIn(INTEGRATION_LIFECYCLE_TICK_COMPLETE, log_types)

    def test_persistence_persists_events(self):
        """tick 事件应持久化到 EventStore。

        默认 intervals 较长(300s/60s/...):
        - 第 1 次 tick: 5 个 task 事件 + 1 tick_complete = 6 个写入
        - 第 2 次 tick: 5 个 task 被 AfterInterval 跳过,只有 1 tick_complete = 1 个写入
        - 共 7 个写入
        """
        tmpdir = tempfile.mkdtemp(prefix="d2_step4_persist_")
        try:
            store_path = os.path.join(tmpdir, "events.jsonl")
            h = RuntimeIntegrationHost(
                event_store_path=store_path,
                enable_persistence=True,
            )
            h.start()
            h.tick()
            h.tick()
            # 文件应存在且有内容
            self.assertTrue(os.path.exists(store_path))
            with open(store_path, "r", encoding="utf-8") as f:
                lines = [l for l in f if l.strip()]
            # 6 + 1 = 7
            self.assertEqual(h.event_store.write_count, 7)
            self.assertEqual(len(lines), 7)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_persistence_disabled(self):
        """enable_persistence=False 时 EventStore 不应写入。"""
        tmpdir = tempfile.mkdtemp(prefix="d2_step4_nopersist_")
        try:
            store_path = os.path.join(tmpdir, "events.jsonl")
            h = RuntimeIntegrationHost(
                event_store_path=store_path,
                enable_persistence=False,
            )
            h.start()
            h.tick()
            # store 未启用,write_count 仍为 0
            self.assertEqual(h.event_store.write_count, 0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 2. 多次 tick 闭环
# ============================================================
class TestMultiTickLoop(unittest.TestCase):
    def test_three_ticks_accumulate_events(self):
        """3 次 tick: 第 1 次 6 个事件,后续每次只 1 个 tick_complete(AfterInterval 跳过 task)。"""
        h = RuntimeIntegrationHost()
        h.start()
        for _ in range(3):
            h.tick()
        self.assertEqual(h.tick_count, 3)
        # 6 + 1 + 1 = 8
        self.assertEqual(h.event_log.total_appended, 8)
        # 3 个 tick_complete 事件
        self.assertEqual(
            h.event_log.count_by_type(INTEGRATION_LIFECYCLE_TICK_COMPLETE), 3
        )
        # 5 个 task 事件各 1 次(只在第 1 次 tick)
        for evt_type in (
            INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            INTEGRATION_EMOTION_SHOULD_DECAY,
            INTEGRATION_GROWTH_SHOULD_PROPOSE,
            INTEGRATION_PERSONALITY_SHOULD_SYNC,
            INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
        ):
            self.assertEqual(h.event_log.count_by_type(evt_type), 1)

    def test_tick_count_increments(self):
        h = RuntimeIntegrationHost()
        h.start()
        for i in range(1, 6):
            h.tick()
            self.assertEqual(h.tick_count, i)

    def test_event_log_size_grows(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        s1 = h.event_log.size
        h.tick()
        s2 = h.event_log.size
        self.assertGreater(s2, s1)


# ============================================================
# 3. 显式禁用自动行为
# ============================================================
class TestDisabledAutoBehavior(unittest.TestCase):
    def test_disabled_auto_create_manager(self):
        """auto_create_manager=False + auto_register_tasks=False: manager 为 None。"""
        h = RuntimeIntegrationHost(
            auto_create_manager=False,
            auto_register_tasks=False,
        )
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        self.assertIsNone(h.lifecycle_manager)
        # tick() 仍可调用,但无结果
        results = h.tick()
        self.assertEqual(results, [])
        self.assertEqual(h.tick_count, 1)
        # tick_complete 仍发出
        self.assertEqual(h.event_bridge.published_count, 1)
        self.assertEqual(
            h.event_bridge.last_published_type,
            INTEGRATION_LIFECYCLE_TICK_COMPLETE,
        )

    def test_disabled_register_tasks(self):
        """auto_create_manager=True + auto_register_tasks=False: manager 创建但不注册 task。"""
        h = RuntimeIntegrationHost(
            auto_create_manager=True,
            auto_register_tasks=False,
        )
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        self.assertIsNotNone(h.lifecycle_manager)
        # 没注册任何 task
        self.assertEqual(h.lifecycle_manager.task_count(), 0)
        results = h.tick()
        # 无 task,无 LifecycleResult
        self.assertEqual(results, [])


# ============================================================
# 4. 外部注入的 LifecycleManager
# ============================================================
class TestExternalLifecycleManager(unittest.TestCase):
    def test_use_injected_manager(self):
        """外部注入 LifecycleManager: start 后 tick 使用外部 manager。"""
        manager = LifecycleManager(clock=SystemClock())
        # 禁用 auto_register_tasks,确保 manager 内部不会有 host 注入的 5 task
        h = RuntimeIntegrationHost(
            lifecycle_manager=manager,
            auto_register_tasks=False,
        )
        self.assertTrue(h.start())
        self.assertIs(h.lifecycle_manager, manager)
        results = h.tick()
        # 外部 manager 无注册 task,无 LifecycleResult
        self.assertEqual(results, [])

    def test_injected_manager_keeps_its_tasks(self):
        """外部 manager 已注册 task 时,host 不会重复注册。"""
        from src.runtime.integration.tasks import MemoryLifecycleTask

        manager = LifecycleManager(clock=SystemClock())
        manager.register(MemoryLifecycleTask())
        h = RuntimeIntegrationHost(
            lifecycle_manager=manager,
            auto_register_tasks=True,  # 试图自动注册
        )
        self.assertTrue(h.start())
        # 不会重复注册(已存在)
        # 但其余 4 个 task + Phase F emotion_reflection 会被加入
        # 实际上 register_default_tasks 会跳过已注册的,然后注册未注册的
        # 所以总数应为 6
        self.assertEqual(manager.task_count(), 7)


# ============================================================
# 5. 状态机
# ============================================================
class TestHostStateMachine(unittest.TestCase):
    def test_full_lifecycle(self):
        h = RuntimeIntegrationHost()
        self.assertEqual(h.state, HOST_STATE_CREATED)
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        self.assertTrue(h.stop())
        self.assertEqual(h.state, HOST_STATE_STOPPED)
        self.assertIsNotNone(h.started_at)
        self.assertIsNotNone(h.stopped_at)

    def test_cannot_restart_after_stop(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.stop()
        # 再次 start 应失败
        self.assertFalse(h.start())
        self.assertEqual(h.state, HOST_STATE_STOPPED)

    def test_idempotent_start(self):
        h = RuntimeIntegrationHost()
        self.assertTrue(h.start())
        self.assertTrue(h.start())  # 重复 start 幂等
        self.assertEqual(h.state, HOST_STATE_RUNNING)

    def test_idempotent_stop(self):
        h = RuntimeIntegrationHost()
        h.start()
        self.assertTrue(h.stop())
        self.assertTrue(h.stop())  # 重复 stop 幂等
        self.assertEqual(h.state, HOST_STATE_STOPPED)

    def test_stop_closes_log_and_store(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        self.assertTrue(h.stop())
        self.assertTrue(h.event_log.is_closed)
        self.assertTrue(h.event_store.is_closed)

    def test_tick_after_stop_returns_empty(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        h.stop()
        # stop 后 tick 不应工作
        results = h.tick()
        self.assertEqual(results, [])
        # tick_count 不变(因为 tick 在 not_running 状态不递增)
        self.assertEqual(h.tick_count, 1)


# ============================================================
# 6. Status & Health Check
# ============================================================
class TestStatusAndHealth(unittest.TestCase):
    def test_status_after_full_loop(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        h.tick()
        s = h.status()
        self.assertEqual(s["state"], HOST_STATE_RUNNING)
        self.assertEqual(s["tick_count"], 2)
        self.assertTrue(s["has_lifecycle_manager"])
        self.assertEqual(len(s["registered_task_ids"]), 7)
        self.assertIn("event_log", s)
        self.assertIn("event_store", s)
        self.assertIn("adapters", s)
        # 5 个 adapter
        self.assertEqual(len(s["adapters"]), 5)

    def test_health_check_after_full_loop(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        hc = h.health_check()
        self.assertEqual(hc["state"], HOST_STATE_RUNNING)
        self.assertEqual(hc["tick_count"], 1)
        self.assertIn("adapters", hc)
        # 5 个 adapter
        self.assertEqual(len(hc["adapters"]), 5)
        # event_bridge 字段独立
        self.assertIn("event_bridge", hc)
        self.assertIn("event_log", hc)
        self.assertIn("event_store", hc)
        # lifecycle_manager 字段
        self.assertIn("lifecycle_manager", hc)
        self.assertIsNotNone(hc["lifecycle_manager"])

    def test_status_event_log_describe(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        s = h.status()
        log_d = s["event_log"]
        self.assertEqual(log_d["size"], 6)
        self.assertEqual(log_d["total_appended"], 6)

    def test_status_event_store_describe(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        s = h.status()
        store_d = s["event_store"]
        self.assertEqual(store_d["write_count"], 6)
        self.assertEqual(store_d["write_failures"], 0)
        self.assertTrue(store_d["exists"])


# ============================================================
# 7. Adapter 事件注入
# ============================================================
class TestAdapterEventInjection(unittest.TestCase):
    def test_adapter_emits_collected_to_event_log(self):
        """Task 通过 Adapter emit 的事件应被 EventLog 收集。"""
        h = RuntimeIntegrationHost()
        h.start()
        h.tick()
        # 5 个 adapter 都应有 emitted_count >= 1
        for name, adp in h.adapters.items():
            self.assertGreaterEqual(
                adp.emitted_count, 1,
                f"adapter {name} 应该有 emitted_count >= 1",
            )

    def test_inject_log_to_adapters_only_in_start(self):
        """start 之前调用 tick 不应注入 log(因为还没 start)。"""
        h = RuntimeIntegrationHost()
        # 不 start,直接 tick
        results = h.tick()
        self.assertEqual(results, [])


# ============================================================
# 8. publish 入口与持久化
# ============================================================
class TestPublishAndPersist(unittest.TestCase):
    def test_publish_integration_event_collected_and_persisted(self):
        """publish_integration_event 进入 EventLog + EventStore。"""
        tmpdir = tempfile.mkdtemp(prefix="d2_step4_pub_")
        try:
            store_path = os.path.join(tmpdir, "events.jsonl")
            h = RuntimeIntegrationHost(
                event_store_path=store_path,
                enable_persistence=True,
            )
            h.start()
            ev = make_integration_event(
                event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
                source="external",
            )
            result = h.publish_integration_event(ev)
            self.assertIs(result, ev)
            # EventLog 收集
            self.assertIn(ev.event_id, [e.event_id for e in h.event_log.events()])
            # EventStore 持久化
            self.assertEqual(h.event_store.write_count, 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_publish_business_event(self):
        h = RuntimeIntegrationHost()
        h.start()
        ev = make_integration_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
        )
        result = h.publish_business_event(ev)
        self.assertIsNotNone(result)
        # EventLog 收集
        self.assertEqual(h.event_log.total_appended, 1)


# ============================================================
# 9. EventLog 容量上限闭环
# ============================================================
class TestEventLogCapacityInLoop(unittest.TestCase):
    def test_event_log_caps_old_events(self):
        """EventLog 容量小时,FIFO 淘汰旧事件。"""
        h = RuntimeIntegrationHost(event_log_capacity=3)
        h.start()
        # 第一次 tick: 6 个事件写入 log,只保留最后 3 个
        h.tick()
        # size 不超过 capacity
        self.assertLessEqual(h.event_log.size, 3)
        # 但 total_appended 累加
        self.assertEqual(h.event_log.total_appended, 6)


# ============================================================
# 10. 工厂
# ============================================================
class TestFactory(unittest.TestCase):
    def test_build_default_host(self):
        h = build_default_host(name="factory_host")
        self.assertEqual(h.name, "factory_host")
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        results = h.tick()
        self.assertEqual(len(results), 7)
        h.stop()


# ============================================================
# 11. 复用注入的 EventLog / EventStore
# ============================================================
class TestReuseEventLogAndStore(unittest.TestCase):
    def test_inject_event_log(self):
        """注入自定义 EventLog。"""
        log = IntegrationEventLog(name="my_log", capacity=10)
        h = RuntimeIntegrationHost(event_log=log)
        h.start()
        h.tick()
        # host 使用注入的 log
        self.assertIs(h.event_log, log)
        self.assertEqual(log.total_appended, 6)

    def test_inject_event_store(self):
        """注入自定义 EventStore。"""
        tmpdir = tempfile.mkdtemp(prefix="d2_step4_inject_store_")
        try:
            store_path = os.path.join(tmpdir, "events.jsonl")
            store = IntegrationEventStore(path=store_path, name="my_store")
            h = RuntimeIntegrationHost(event_store=store)
            h.start()
            h.tick()
            self.assertIs(h.event_store, store)
            self.assertEqual(store.write_count, 6)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ============================================================
# 12. 失败隔离
# ============================================================
class TestFailureIsolation(unittest.TestCase):
    def test_one_task_failure_does_not_break_loop(self):
        """一个 Task 失败不应破坏整个 loop(其他 task 仍 SUCCESS)。"""
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.tasks import (
            EmotionLifecycleTask,
            GrowthLifecycleTask,
            MemoryLifecycleTask,
            PersonalityLifecycleTask,
            RelationshipLifecycleTask,
        )
        from src.runtime.integration.tasks.memory_lifecycle_task import (
            MemoryLifecycleTask as MLT,
        )
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        class _BoomMemoryTask(MLT):
            def _do_execute(self, context):
                raise RuntimeError("memory boom")

        # 自定义 host:注入会 boom 的 memory task
        manager = LifecycleManager(clock=SystemClock())
        manager.register(_BoomMemoryTask(adapter=MemoryAdapter(), interval_seconds=0.0))
        manager.register(EmotionLifecycleTask(interval_seconds=0.0))
        manager.register(GrowthLifecycleTask(interval_seconds=0.0))
        manager.register(PersonalityLifecycleTask(interval_seconds=0.0))
        manager.register(RelationshipLifecycleTask(interval_seconds=0.0))

        h = RuntimeIntegrationHost(
            lifecycle_manager=manager,
            auto_register_tasks=False,  # 不要重复注册
        )
        self.assertTrue(h.start())
        results = h.tick()
        # 5 个结果(memory FAILED,其他 SUCCESS)
        self.assertEqual(len(results), 5)
        statuses = {r.task_id: r.status for r in results}
        self.assertEqual(statuses["memory_lifecycle_task"], LifecycleStatus.FAILED)
        self.assertEqual(statuses["emotion_lifecycle_task"], LifecycleStatus.SUCCESS)
        # tick_complete 仍发出
        self.assertEqual(
            h.event_bridge.last_published_type,
            INTEGRATION_LIFECYCLE_TICK_COMPLETE,
        )


if __name__ == "__main__":
    unittest.main()
