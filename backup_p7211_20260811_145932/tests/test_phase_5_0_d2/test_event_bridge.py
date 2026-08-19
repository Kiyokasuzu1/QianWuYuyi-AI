# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_event_bridge.py

Phase 5.0-D2 Step 2: EventBridge Skeleton 测试。

覆盖:
- 默认构造与状态查询
- 事件名映射(register_mapping / get_mapped_type)
- publish_integration_event:投出到 integration_to_lifecycle
- publish_integration_event:无 emitter 时仍记录指标
- bridge_business_event:IntegrationEvent 透传
- bridge_business_event:dict 输入
- bridge_business_event:对象输入(duck-typed 读取 event_type / payload)
- bridge_business_event:None / 非法输入丢弃
- bridge_business_event:business_to_integration 注入
- error_count 在 emitter 抛错时增加
- dropped_count 在无 emitter 或 None 输入时增加
- 多次桥接的指标累积
"""
import unittest

from src.runtime.integration.event_bridge import (
    BusinessEventToIntegration,
    EventBridge,
    build_default_event_bridge,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    IntegrationEvent,
    make_integration_event,
)


class TestEventBridgeDefaults(unittest.TestCase):
    def test_default_construct(self):
        b = EventBridge()
        self.assertEqual(b.name, "event_bridge")
        self.assertEqual(b.published_count, 0)
        self.assertEqual(b.dropped_count, 0)
        self.assertEqual(b.bridge_count, 0)
        self.assertEqual(b.error_count, 0)

    def test_factory(self):
        b = build_default_event_bridge(name="my_bridge")
        self.assertEqual(b.name, "my_bridge")


class TestEventBridgeMapping(unittest.TestCase):
    def test_register_mapping(self):
        b = EventBridge()
        b.register_mapping("memory.created", INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(
            b.get_mapped_type("memory.created"),
            INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        )

    def test_register_mappings(self):
        b = EventBridge()
        b.register_mappings(
            {
                "a": INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
                "b": INTEGRATION_GROWTH_SHOULD_PROPOSE,
            }
        )
        self.assertEqual(
            b.get_mapped_type("a"),
            INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        )
        self.assertEqual(
            b.get_mapped_type("b"),
            INTEGRATION_GROWTH_SHOULD_PROPOSE,
        )

    def test_unmapped_returns_input(self):
        b = EventBridge()
        self.assertEqual(b.get_mapped_type("foo.bar"), "foo.bar")

    def test_register_mapping_ignores_invalid(self):
        b = EventBridge()
        b.register_mapping("", "x")  # empty source
        b.register_mapping("a", "")  # empty target
        self.assertEqual(b.get_mapped_type("a"), "a")  # unmapped


class TestEventBridgePublishIntegrationEvent(unittest.TestCase):
    def test_publish_with_emitter(self):
        received = []
        b = EventBridge(integration_to_lifecycle=lambda e: received.append(e))
        e = make_integration_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="t",
        )
        result = b.publish_integration_event(e)
        self.assertIs(result, e)
        self.assertEqual(b.published_count, 1)
        self.assertEqual(b.bridge_count, 1)
        self.assertEqual(b.last_published_event_id, e.event_id)
        self.assertEqual(b.last_published_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertEqual(len(received), 1)

    def test_publish_without_emitter(self):
        b = EventBridge()
        e = make_integration_event(event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        result = b.publish_integration_event(e)
        self.assertIs(result, e)
        # 无 emitter 也记录指标
        self.assertEqual(b.published_count, 1)
        self.assertEqual(b.error_count, 0)

    def test_publish_with_failing_emitter(self):
        def bad(e):
            raise RuntimeError("emit boom")

        b = EventBridge(integration_to_lifecycle=bad)
        e = make_integration_event(event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        result = b.publish_integration_event(e)
        self.assertIsNone(result)
        self.assertEqual(b.error_count, 1)
        self.assertEqual(b.dropped_count, 1)
        self.assertIn("emit boom", b.last_error)

    def test_publish_non_event(self):
        b = EventBridge()
        result = b.publish_integration_event("not an event")  # type: ignore[arg-type]
        self.assertIsNone(result)
        self.assertEqual(b.error_count, 1)


class TestEventBridgeBusinessEvent(unittest.TestCase):
    def test_none_event_dropped(self):
        b = EventBridge()
        result = b.bridge_business_event(None)
        self.assertIsNone(result)
        self.assertEqual(b.dropped_count, 1)

    def test_integration_event_passthrough(self):
        received = []
        b = EventBridge(integration_to_lifecycle=lambda e: received.append(e))
        e = make_integration_event(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        result = b.bridge_business_event(e)
        self.assertIs(result, e)
        self.assertEqual(b.published_count, 1)

    def test_dict_input(self):
        b = EventBridge()
        e = make_integration_event(
            event_type=INTEGRATION_GROWTH_SHOULD_PROPOSE,
            source="dict_input",
        )
        result = b.bridge_business_event(e.to_dict())
        # dict 输入被 from_dict 还原
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)
        self.assertEqual(result.source, "dict_input")
        self.assertEqual(b.published_count, 1)

    def test_duck_typed_object(self):
        """支持 duck-typed 业务事件对象。"""
        b = EventBridge()

        class FakeBizEvent:
            event_type = "memory.created"
            payload = {"k": 1}
            source = "fake_biz"
            related_ids = ["r1"]

        result = b.bridge_business_event(FakeBizEvent())
        # 因 register_mapping 未注册,event_type 透传
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, "memory.created")
        self.assertEqual(result.source, "fake_biz")
        self.assertEqual(result.payload, {"k": 1})
        self.assertEqual(result.related_ids, ["r1"])

    def test_duck_typed_with_mapping(self):
        b = EventBridge()
        b.register_mapping("memory.created", INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)

        class FakeBizEvent:
            event_type = "memory.created"
            payload = {"k": 1}
            source = "fake_biz"

        result = b.bridge_business_event(FakeBizEvent())
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)

    def test_custom_converter(self):
        def conv(raw):
            return make_integration_event(
                event_type=INTEGRATION_GROWTH_SHOULD_PROPOSE,
                source="custom",
                payload={"raw_id": id(raw)},
            )

        received = []
        b = EventBridge(
            business_to_integration=conv,
            integration_to_lifecycle=lambda e: received.append(e),
        )
        sentinel = object()
        result = b.bridge_business_event(sentinel)
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)
        self.assertEqual(result.payload, {"raw_id": id(sentinel)})
        self.assertEqual(len(received), 1)

    def test_custom_converter_returns_none(self):
        def conv(raw):
            return None

        b = EventBridge(business_to_integration=conv)
        result = b.bridge_business_event(object())
        self.assertIsNone(result)
        self.assertEqual(b.dropped_count, 1)

    def test_custom_converter_returns_dict(self):
        def conv(raw):
            return make_integration_event(
                event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE
            ).to_dict()

        b = EventBridge(business_to_integration=conv)
        result = b.bridge_business_event(object())
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)

    def test_converter_raises(self):
        def conv(raw):
            raise RuntimeError("convert boom")

        b = EventBridge(business_to_integration=conv)
        result = b.bridge_business_event(object())
        self.assertIsNone(result)
        self.assertEqual(b.error_count, 1)
        self.assertIn("convert boom", b.last_error)

    def test_object_without_event_type_dropped(self):
        b = EventBridge()
        result = b.bridge_business_event(object())
        self.assertIsNone(result)
        self.assertEqual(b.dropped_count, 1)


class TestEventBridgeLifecycleReverse(unittest.TestCase):
    def test_lifecycle_to_integration_converter(self):
        def reverse(raw):
            return make_integration_event(
                event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
                source="lifecycle",
            )

        b = EventBridge(lifecycle_to_integration=reverse)
        result = b.bridge_lifecycle_event("any")
        self.assertIsNotNone(result)
        self.assertEqual(result.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)

    def test_lifecycle_to_integration_not_set(self):
        b = EventBridge()
        result = b.bridge_lifecycle_event("any")
        self.assertIsNone(result)

    def test_lifecycle_to_integration_returns_non_event(self):
        def reverse(raw):
            return "not an event"  # type: ignore[return-value]

        b = EventBridge(lifecycle_to_integration=reverse)
        result = b.bridge_lifecycle_event("any")
        self.assertIsNone(result)

    def test_lifecycle_to_integration_raises(self):
        def reverse(raw):
            raise RuntimeError("x")

        b = EventBridge(lifecycle_to_integration=reverse)
        result = b.bridge_lifecycle_event("any")
        self.assertIsNone(result)
        self.assertEqual(b.error_count, 1)


class TestEventBridgeDescribe(unittest.TestCase):
    def test_describe(self):
        b = EventBridge()
        d = b.describe()
        self.assertEqual(d["name"], "event_bridge")
        self.assertFalse(d["has_business_converter"])
        self.assertFalse(d["has_lifecycle_emitter"])
        self.assertFalse(d["has_lifecycle_converter"])
        self.assertEqual(d["mapping_size"], 0)

    def test_describe_with_injections(self):
        b = EventBridge(
            business_to_integration=lambda r: None,
            integration_to_lifecycle=lambda e: None,
        )
        b.register_mapping("a", "b")
        d = b.describe()
        self.assertTrue(d["has_business_converter"])
        self.assertTrue(d["has_lifecycle_emitter"])
        self.assertEqual(d["mapping_size"], 1)


class TestEventBridgeAccumulatedMetrics(unittest.TestCase):
    def test_metrics_accumulate(self):
        b = EventBridge()
        # 5 个有效
        for i in range(5):
            b.publish_integration_event(
                make_integration_event(event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE)
            )
        # 3 个 None 输入
        for _ in range(3):
            b.bridge_business_event(None)
        self.assertEqual(b.published_count, 5)
        self.assertEqual(b.dropped_count, 3)
        self.assertEqual(b.error_count, 0)


if __name__ == "__main__":
    unittest.main()
