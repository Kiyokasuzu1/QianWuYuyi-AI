# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_adapters.py

Phase 5.0-D2 Step 2: 业务 Adapter Skeleton 测试。

覆盖:
- MemoryAdapter / GrowthAdapter / EmotionAdapter / PersonalityAdapter
- is_available() 在 Skeleton 阶段默认 False(无 target_resolver)
- describe() / health() 返回结构
- 事件便捷方法(emit_should_consolidate / should_propose / should_decay / should_sync)
- BaseAdapter 通用: emit() 计数、_safe_call 错误隔离
- target_resolver 注入后可正确解析
- Adapter 协议(duck-typed)满足
- Adapter 事件 payload 默认 source=owner
"""
import unittest

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.adapters.emotion_adapter import (
    EmotionAdapter,
    build_default_emotion_adapter,
)
from src.runtime.integration.adapters.growth_adapter import (
    GrowthAdapter,
    build_default_growth_adapter,
)
from src.runtime.integration.adapters.memory_adapter import (
    MemoryAdapter,
    build_default_memory_adapter,
)
from src.runtime.integration.adapters.personality_adapter import (
    PersonalityAdapter,
    build_default_personality_adapter,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    IntegrationEvent,
)


class TestMemoryAdapter(unittest.TestCase):
    def test_default_construct(self):
        a = MemoryAdapter()
        self.assertEqual(a.name, "memory_adapter")
        self.assertEqual(a.owner, "memory")

    def test_skeleton_unavailable(self):
        a = MemoryAdapter()
        self.assertFalse(a.is_available())

    def test_target_resolver_returns_object(self):
        sentinel = object()
        a = MemoryAdapter(target_resolver=lambda: sentinel)
        self.assertTrue(a.is_available())
        self.assertIs(a.get_target(), sentinel)

    def test_target_resolver_returns_none(self):
        a = MemoryAdapter(target_resolver=lambda: None)
        self.assertFalse(a.is_available())
        self.assertIsNone(a.get_target())

    def test_target_resolver_raises(self):
        def bad():
            raise RuntimeError("nope")

        a = MemoryAdapter(target_resolver=bad)
        self.assertFalse(a.is_available())
        self.assertGreaterEqual(a.error_count, 1)
        self.assertIn("nope", a.last_error)

    def test_emit_should_consolidate_no_emitter(self):
        a = MemoryAdapter()
        event = a.emit_should_consolidate(payload={"k": 1})
        self.assertEqual(event.event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(event.payload, {"k": 1})
        self.assertEqual(event.source, "memory")
        self.assertEqual(a.emitted_count, 1)

    def test_emit_with_emitter(self):
        received = []
        a = MemoryAdapter(event_emitter=lambda e: received.append(e))
        event = a.emit_should_consolidate()
        self.assertEqual(len(received), 1)
        self.assertIs(received[0], event)

    def test_emit_with_failing_emitter(self):
        def bad(e):
            raise RuntimeError("emit boom")

        a = MemoryAdapter(event_emitter=bad)
        event = a.emit_should_consolidate()
        # 不抛错
        self.assertEqual(event.event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertGreaterEqual(a.error_count, 1)

    def test_health(self):
        a = MemoryAdapter()
        h = a.health()
        self.assertEqual(h["name"], "memory_adapter")
        self.assertTrue(h["skeleton"])
        self.assertEqual(h["target_kind"], "memory_system")

    def test_factory(self):
        a = build_default_memory_adapter()
        self.assertIsInstance(a, MemoryAdapter)


class TestGrowthAdapter(unittest.TestCase):
    def test_default(self):
        a = GrowthAdapter()
        self.assertEqual(a.name, "growth_adapter")
        self.assertEqual(a.owner, "growth")
        self.assertFalse(a.is_available())

    def test_emit_should_propose(self):
        a = GrowthAdapter()
        event = a.emit_should_propose(payload={"x": 1})
        self.assertEqual(event.event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)
        self.assertEqual(event.source, "growth")
        self.assertEqual(a.emitted_count, 1)

    def test_health(self):
        a = GrowthAdapter()
        h = a.health()
        self.assertEqual(h["target_kind"], "growth_loop")

    def test_factory(self):
        a = build_default_growth_adapter()
        self.assertIsInstance(a, GrowthAdapter)


class TestEmotionAdapter(unittest.TestCase):
    def test_default(self):
        a = EmotionAdapter()
        self.assertEqual(a.name, "emotion_adapter")
        self.assertEqual(a.owner, "emotion")
        self.assertFalse(a.is_available())

    def test_emit_should_decay(self):
        a = EmotionAdapter()
        event = a.emit_should_decay()
        self.assertEqual(event.event_type, INTEGRATION_EMOTION_SHOULD_DECAY)
        self.assertEqual(event.source, "emotion")

    def test_health(self):
        a = EmotionAdapter()
        h = a.health()
        self.assertEqual(h["target_kind"], "emotion_manager")

    def test_factory(self):
        a = build_default_emotion_adapter()
        self.assertIsInstance(a, EmotionAdapter)


class TestPersonalityAdapter(unittest.TestCase):
    def test_default(self):
        a = PersonalityAdapter()
        self.assertEqual(a.name, "personality_adapter")
        self.assertEqual(a.owner, "personality")
        self.assertFalse(a.is_available())

    def test_emit_should_sync(self):
        a = PersonalityAdapter()
        event = a.emit_should_sync(payload={"drift": 0.1})
        self.assertEqual(event.event_type, INTEGRATION_PERSONALITY_SHOULD_SYNC)
        self.assertEqual(event.source, "personality")
        self.assertEqual(event.payload, {"drift": 0.1})

    def test_health(self):
        a = PersonalityAdapter()
        h = a.health()
        self.assertEqual(h["target_kind"], "personality_resolver")

    def test_factory(self):
        a = build_default_personality_adapter()
        self.assertIsInstance(a, PersonalityAdapter)


class TestBaseAdapterSafeCall(unittest.TestCase):
    def test_safe_call_ok(self):
        a = BaseAdapter(name="t", owner="o")

        def f(x, y):
            return x + y

        self.assertEqual(a._safe_call(f, 1, 2), 3)
        self.assertEqual(a.call_count, 1)
        self.assertEqual(a.error_count, 0)

    def test_safe_call_error(self):
        a = BaseAdapter(name="t", owner="o")

        def f():
            raise ValueError("oops")

        self.assertIsNone(a._safe_call(f))
        self.assertEqual(a.call_count, 1)
        self.assertEqual(a.error_count, 1)
        self.assertIn("oops", a.last_error)

    def test_safe_call_not_callable(self):
        a = BaseAdapter(name="t", owner="o")
        self.assertIsNone(a._safe_call(None))  # type: ignore[arg-type]

    def test_describe(self):
        a = BaseAdapter(name="t", owner="o")
        d = a.describe()
        self.assertEqual(d["name"], "t")
        self.assertEqual(d["owner"], "o")
        self.assertFalse(d["available"])
        self.assertEqual(d["call_count"], 0)
        self.assertEqual(d["error_count"], 0)

    def test_make_event_default_source(self):
        a = BaseAdapter(name="t", owner="owner_x")
        e = a.make_event("some.event")
        self.assertEqual(e.source, "owner_x")


class TestAdapterProtocol(unittest.TestCase):
    def test_memory_satisfies_protocol(self):
        from src.runtime.integration.adapters.base import Adapter

        a = MemoryAdapter()
        self.assertIsInstance(a, Adapter)

    def test_growth_satisfies_protocol(self):
        from src.runtime.integration.adapters.base import Adapter

        self.assertIsInstance(GrowthAdapter(), Adapter)

    def test_emotion_satisfies_protocol(self):
        from src.runtime.integration.adapters.base import Adapter

        self.assertIsInstance(EmotionAdapter(), Adapter)

    def test_personality_satisfies_protocol(self):
        from src.runtime.integration.adapters.base import Adapter

        self.assertIsInstance(PersonalityAdapter(), Adapter)


class TestEmitOnlyAcceptsIntegrationEvent(unittest.TestCase):
    def test_emit_with_non_event(self):
        a = MemoryAdapter()
        before = a.emitted_count
        a.emit("not an event")  # type: ignore[arg-type]
        # 计数不增加
        self.assertEqual(a.emitted_count, before)


if __name__ == "__main__":
    unittest.main()
