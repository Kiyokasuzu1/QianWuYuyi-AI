# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_integration_event.py

Phase 5.0-D2 Step 2: IntegrationEvent 数据契约测试。

覆盖:
- 默认构造
- 字段验证(event_type / severity / source / payload)
- to_dict / from_dict 序列化
- 容错:非 dict payload / 非 list related_ids
- is_valid / is_known_type
- make_integration_event 工厂
- 序列化往返
"""
import unittest

from src.runtime.integration.integration_event import (
    ALL_INTEGRATION_EVENT_TYPES,
    ALL_INTEGRATION_SEVERITIES,
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    INTEGRATION_SEVERITY_INFO,
    IntegrationEvent,
    make_integration_event,
)


class TestIntegrationEventDefaults(unittest.TestCase):
    def test_default_construct(self):
        e = IntegrationEvent()
        self.assertIsNotNone(e.event_id)
        self.assertTrue(e.event_id.startswith("iev_"))
        self.assertEqual(e.event_type, "")
        self.assertEqual(e.severity, INTEGRATION_SEVERITY_INFO)
        self.assertEqual(e.source, "unknown")
        self.assertIsInstance(e.payload, dict)
        self.assertEqual(e.payload, {})
        self.assertIsInstance(e.related_ids, list)
        self.assertEqual(e.related_ids, [])
        self.assertIsInstance(e.metadata, dict)
        self.assertEqual(e.metadata, {})

    def test_event_id_unique(self):
        e1 = IntegrationEvent()
        e2 = IntegrationEvent()
        self.assertNotEqual(e1.event_id, e2.event_id)

    def test_known_event_types_nonempty(self):
        self.assertGreater(len(ALL_INTEGRATION_EVENT_TYPES), 0)
        # 至少包含 9 种(对照设计文档)
        self.assertGreaterEqual(len(ALL_INTEGRATION_EVENT_TYPES), 9)

    def test_severities_listed(self):
        for s in (
            "info",
            "notice",
            "warning",
            "critical",
        ):
            self.assertIn(s, ALL_INTEGRATION_SEVERITIES)


class TestIntegrationEventFields(unittest.TestCase):
    def test_set_event_type(self):
        e = IntegrationEvent(event_type=INTEGRATION_GROWTH_SHOULD_PROPOSE)
        self.assertEqual(e.event_type, INTEGRATION_GROWTH_SHOULD_PROPOSE)

    def test_set_all_fields(self):
        e = IntegrationEvent(
            event_type=INTEGRATION_EMOTION_SHOULD_DECAY,
            source="test",
            severity="warning",
            payload={"k": 1},
            related_ids=["a", "b"],
            metadata={"x": "y"},
        )
        self.assertEqual(e.event_type, INTEGRATION_EMOTION_SHOULD_DECAY)
        self.assertEqual(e.source, "test")
        self.assertEqual(e.severity, "warning")
        self.assertEqual(e.payload, {"k": 1})
        self.assertEqual(e.related_ids, ["a", "b"])
        self.assertEqual(e.metadata, {"x": "y"})

    def test_payload_dict_isolation(self):
        """外部修改传入的 dict 不应影响 event。"""
        p = {"a": 1}
        e = IntegrationEvent(payload=p)
        p["b"] = 2
        self.assertNotIn("b", e.payload)

    def test_related_ids_list_isolation(self):
        r = ["a"]
        e = IntegrationEvent(related_ids=r)
        r.append("b")
        self.assertNotIn("b", e.related_ids)


class TestIntegrationEventSerialization(unittest.TestCase):
    def test_to_dict(self):
        e = IntegrationEvent(
            event_type=INTEGRATION_PERSONALITY_SHOULD_SYNC,
            source="host",
        )
        d = e.to_dict()
        self.assertIn("event_id", d)
        self.assertEqual(d["event_type"], INTEGRATION_PERSONALITY_SHOULD_SYNC)
        self.assertEqual(d["source"], "host")

    def test_from_dict_normal(self):
        e = IntegrationEvent(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source="runtime",
            payload={"k": "v"},
        )
        d = e.to_dict()
        e2 = IntegrationEvent.from_dict(d)
        self.assertEqual(e2.event_id, e.event_id)
        self.assertEqual(e2.event_type, e.event_type)
        self.assertEqual(e2.source, e.source)
        self.assertEqual(e2.payload, e.payload)

    def test_from_dict_missing_fields(self):
        """缺字段也能反序列化(默认值)。"""
        e = IntegrationEvent.from_dict({})
        self.assertIsNotNone(e.event_id)
        self.assertEqual(e.event_type, "")
        self.assertEqual(e.payload, {})

    def test_from_dict_invalid_data(self):
        """非 dict 输入不应抛错。"""
        e = IntegrationEvent.from_dict(None)  # type: ignore[arg-type]
        self.assertIsInstance(e, IntegrationEvent)


class TestIntegrationEventValidation(unittest.TestCase):
    def test_is_valid_no_type(self):
        e = IntegrationEvent()
        self.assertFalse(e.is_valid())

    def test_is_valid_with_type(self):
        e = IntegrationEvent(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertTrue(e.is_valid())

    def test_is_known_type(self):
        e = IntegrationEvent(event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE)
        self.assertTrue(e.is_known_type())

    def test_is_not_known_type(self):
        e = IntegrationEvent(event_type="some.unknown.event")
        self.assertFalse(e.is_known_type())


class TestMakeIntegrationEvent(unittest.TestCase):
    def test_basic(self):
        e = make_integration_event(
            event_type=INTEGRATION_EMOTION_SHOULD_DECAY,
            source="test",
        )
        self.assertEqual(e.event_type, INTEGRATION_EMOTION_SHOULD_DECAY)
        self.assertEqual(e.source, "test")

    def test_with_payload(self):
        e = make_integration_event(
            event_type=INTEGRATION_GROWTH_SHOULD_PROPOSE,
            payload={"a": 1},
            related_ids=["x"],
            severity="warning",
        )
        self.assertEqual(e.payload, {"a": 1})
        self.assertEqual(e.related_ids, ["x"])
        self.assertEqual(e.severity, "warning")

    def test_invalid_payload_becomes_empty(self):
        e = make_integration_event(
            event_type="x",
            payload="not a dict",  # type: ignore[arg-type]
        )
        self.assertEqual(e.payload, {})


class TestIntegrationEventRepr(unittest.TestCase):
    def test_repr(self):
        e = IntegrationEvent(event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE)
        r = repr(e)
        self.assertIn("IntegrationEvent", r)
        self.assertIn(INTEGRATION_LIFECYCLE_TICK_COMPLETE, r)


if __name__ == "__main__":
    unittest.main()
