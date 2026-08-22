# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_runtime_integration_host.py

Phase 5.0-D2 Step 2: RuntimeIntegrationHost Skeleton 测试。

覆盖:
- 默认构造与 4 个默认 Adapter 注册
- 状态机:CREATED -> STARTING -> RUNNING -> STOPPING -> STOPPED
- 重复 start 幂等
- start 后 stop 成功
- 注入 LifecycleManager:start/stop 联动
- 注入 LifecycleManager 抛错:状态转 FAILED
- publish_business_event / publish_integration_event
- tick:无 LifecycleManager 时仍可 tick(返回 [] 并发事件)
- tick:LifecycleManager 抛错不致命
- status() / health_check() 返回结构
- register_adapter:扩展 Adapter
- get_adapter:按 name 查找
- 不可在 STOPPED 后再次 start
"""
import unittest
from typing import Any, List

from src.runtime.integration.event_bridge import EventBridge
from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.runtime_integration_host import (
    HOST_STATE_CREATED,
    HOST_STATE_FAILED,
    HOST_STATE_RUNNING,
    HOST_STATE_STARTING,
    HOST_STATE_STOPPED,
    HOST_STATE_STOPPING,
    RuntimeIntegrationHost,
    build_default_host,
)


class FakeLifecycleManager:
    """Skeleton 测试用:模拟 LifecycleManager。"""

    def __init__(self, *, fail_start: bool = False, fail_stop: bool = False, fail_tick: bool = False) -> None:
        self._started = False
        self._stopped = False
        self._fail_start = fail_start
        self._fail_stop = fail_stop
        self._fail_tick = fail_tick
        self._tick_count = 0
        self._results: List[Any] = []

    def start(self) -> bool:
        if self._fail_start:
            raise RuntimeError("start boom")
        self._started = True
        return True

    def stop(self) -> bool:
        if self._fail_stop:
            raise RuntimeError("stop boom")
        self._stopped = True
        return True

    def tick(self) -> List[Any]:
        if self._fail_tick:
            raise RuntimeError("tick boom")
        self._tick_count += 1
        # 模拟返回 1 个 LifecycleResult
        return [f"result_{self._tick_count}"]


class TestHostDefaults(unittest.TestCase):
    def test_default_construct(self):
        h = RuntimeIntegrationHost()
        self.assertEqual(h.name, "runtime_integration_host")
        self.assertEqual(h.state, HOST_STATE_CREATED)
        self.assertEqual(h.tick_count, 0)
        self.assertIsNone(h.started_at)
        self.assertIsNone(h.stopped_at)

    def test_default_adapters_registered(self):
        h = RuntimeIntegrationHost()
        names = sorted(h.adapters.keys())
        self.assertIn("memory_adapter", names)
        self.assertIn("growth_adapter", names)
        self.assertIn("emotion_adapter", names)
        self.assertIn("personality_adapter", names)

    def test_get_adapter(self):
        h = RuntimeIntegrationHost()
        a = h.get_adapter("memory_adapter")
        self.assertIsNotNone(a)
        self.assertEqual(a.name, "memory_adapter")

    def test_get_adapter_missing(self):
        h = RuntimeIntegrationHost()
        self.assertIsNone(h.get_adapter("not_exist"))

    def test_default_event_bridge(self):
        h = RuntimeIntegrationHost()
        self.assertIsInstance(h.event_bridge, EventBridge)

    def test_factory(self):
        h = build_default_host(name="my_host")
        self.assertEqual(h.name, "my_host")


class TestHostLifecycle(unittest.TestCase):
    def test_start_no_lifecycle_manager(self):
        h = RuntimeIntegrationHost()
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)
        self.assertIsNotNone(h.started_at)

    def test_idempotent_start(self):
        h = RuntimeIntegrationHost()
        self.assertTrue(h.start())
        self.assertTrue(h.start())
        self.assertEqual(h.state, HOST_STATE_RUNNING)

    def test_stop_from_created(self):
        h = RuntimeIntegrationHost()
        self.assertTrue(h.stop())
        self.assertEqual(h.state, HOST_STATE_STOPPED)

    def test_stop_from_running(self):
        h = RuntimeIntegrationHost()
        h.start()
        self.assertTrue(h.stop())
        self.assertEqual(h.state, HOST_STATE_STOPPED)
        self.assertIsNotNone(h.stopped_at)

    def test_idempotent_stop(self):
        h = RuntimeIntegrationHost()
        h.stop()
        self.assertTrue(h.stop())
        self.assertEqual(h.state, HOST_STATE_STOPPED)

    def test_cannot_restart_after_stop(self):
        h = RuntimeIntegrationHost()
        h.start()
        h.stop()
        self.assertFalse(h.start())
        # 仍处于 STOPPED
        self.assertEqual(h.state, HOST_STATE_STOPPED)

    def test_start_with_lifecycle_manager(self):
        lm = FakeLifecycleManager()
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        self.assertTrue(h.start())
        self.assertTrue(lm._started)
        self.assertEqual(h.state, HOST_STATE_RUNNING)

    def test_start_lifecycle_manager_fails(self):
        lm = FakeLifecycleManager(fail_start=True)
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        self.assertFalse(h.start())
        self.assertEqual(h.state, HOST_STATE_FAILED)
        self.assertIn("LifecycleManager", h.last_error)

    def test_stop_lifecycle_manager_fails(self):
        lm = FakeLifecycleManager(fail_stop=True)
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        h.start()
        # stop 不抛错,但记录 last_error
        self.assertTrue(h.stop())
        self.assertEqual(h.state, HOST_STATE_STOPPED)
        self.assertIn("LifecycleManager", h.last_error)


class TestHostTick(unittest.TestCase):
    def test_tick_no_lifecycle_manager(self):
        """Step 4:默认 host 自动创建 manager + 5 task,首次 tick 全部 SUCCESS。"""
        h = RuntimeIntegrationHost()
        h.start()
        results = h.tick()
        self.assertEqual(len(results), 7)
        self.assertEqual(h.tick_count, 1)
        # 应该发出 tick_complete
        self.assertEqual(h.event_bridge.published_count, 1)
        self.assertEqual(
            h.event_bridge.last_published_type,
            INTEGRATION_LIFECYCLE_TICK_COMPLETE,
        )

    def test_tick_disabled_auto_loop(self):
        """当显式禁用 auto_create_manager + auto_register_tasks,tick 应返回 []。"""
        h = RuntimeIntegrationHost(
            auto_create_manager=False,
            auto_register_tasks=False,
        )
        h.start()
        results = h.tick()
        self.assertEqual(results, [])
        self.assertEqual(h.tick_count, 1)
        # tick_complete 仍发出
        self.assertEqual(h.event_bridge.published_count, 1)

    def test_tick_with_lifecycle_manager(self):
        lm = FakeLifecycleManager()
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        h.start()
        results = h.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(lm._tick_count, 1)
        self.assertEqual(h.tick_count, 1)

    def test_tick_multiple(self):
        lm = FakeLifecycleManager()
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        h.start()
        for _ in range(3):
            h.tick()
        self.assertEqual(h.tick_count, 3)
        self.assertEqual(lm._tick_count, 3)
        # 3 次 tick_complete 事件
        self.assertGreaterEqual(h.event_bridge.published_count, 3)

    def test_tick_when_not_running(self):
        h = RuntimeIntegrationHost()
        # 不 start,直接 tick 应返回空
        results = h.tick()
        self.assertEqual(results, [])
        self.assertEqual(h.tick_count, 0)

    def test_tick_lifecycle_manager_raises(self):
        lm = FakeLifecycleManager(fail_tick=True)
        h = RuntimeIntegrationHost(lifecycle_manager=lm)
        h.start()
        # tick 不应抛错
        results = h.tick()
        self.assertEqual(results, [])
        # tick_count 仍递增(在锁内)
        self.assertEqual(h.tick_count, 1)
        self.assertIn("tick", h.last_error)


class TestHostPublish(unittest.TestCase):
    def test_publish_business_event(self):
        received: List[IntegrationEvent] = []
        eb = EventBridge(integration_to_lifecycle=lambda e: received.append(e))
        h = RuntimeIntegrationHost(event_bridge=eb)
        result = h.publish_business_event(
            make_integration_event(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(received), 1)

    def test_publish_integration_event(self):
        received: List[IntegrationEvent] = []
        eb = EventBridge(integration_to_lifecycle=lambda e: received.append(e))
        h = RuntimeIntegrationHost(event_bridge=eb)
        e = make_integration_event(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        result = h.publish_integration_event(e)
        self.assertIs(result, e)
        self.assertEqual(len(received), 1)


class TestHostAdapters(unittest.TestCase):
    def test_register_extra_adapter(self):
        h = RuntimeIntegrationHost()
        before = len(h.adapters)
        from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
        from src.runtime.integration.adapters.growth_adapter import GrowthAdapter

        # 注入同名 adapter 会覆盖,注入新 adapter 才增加
        h.register_adapter(GrowthAdapter())
        self.assertEqual(len(h.adapters), before)  # 名字冲突覆盖
        # 注入到新名字
        from src.runtime.integration.adapters.base import BaseAdapter

        class _ExtraAdapter(BaseAdapter):
            pass

        h.register_adapter(_ExtraAdapter(name="extra_adapter", owner="custom"))
        self.assertEqual(len(h.adapters), before + 1)

    def test_register_invalid_adapter_ignored(self):
        h = RuntimeIntegrationHost()
        before = len(h.adapters)
        h.register_adapter("not an adapter")  # type: ignore[arg-type]
        self.assertEqual(len(h.adapters), before)


class TestHostStatus(unittest.TestCase):
    def test_status(self):
        h = RuntimeIntegrationHost()
        s = h.status()
        self.assertEqual(s["name"], "runtime_integration_host")
        self.assertEqual(s["state"], HOST_STATE_CREATED)
        self.assertFalse(s["has_lifecycle_manager"])
        self.assertIn("adapters", s)
        self.assertIn("event_bridge", s)
        for name in ("memory_adapter", "growth_adapter", "emotion_adapter", "personality_adapter"):
            self.assertIn(name, s["adapters"])

    def test_health_check(self):
        h = RuntimeIntegrationHost()
        h.start()
        hc = h.health_check()
        self.assertEqual(hc["state"], HOST_STATE_RUNNING)
        self.assertIn("memory_adapter", hc["adapters"])
        # event_bridge 是独立字段,不在 adapters 字典内
        self.assertIn("event_bridge", hc)
        self.assertIn("error_count", hc["event_bridge"])


class TestHostRepr(unittest.TestCase):
    def test_repr(self):
        h = RuntimeIntegrationHost(name="t")
        r = repr(h)
        self.assertIn("RuntimeIntegrationHost", r)
        self.assertIn("t", r)


if __name__ == "__main__":
    unittest.main()
