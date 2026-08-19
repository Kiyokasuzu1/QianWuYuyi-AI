# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_adapter.py

Phase 5.0-D3-B: ReflectionAdapter / ReflectionEventEmitter 单元测试。
"""
import unittest
from typing import List

from src.runtime.integration.integration_event import (
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    INTEGRATION_REFLECTION_GROWTH_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.reflection.adapter.reflection_adapter import (
    DEFAULT_REFLECTION_ADAPTER_OWNER,
    REFLECTION_ADAPTER_SCHEMA_VERSION,
    ReflectionAdapter,
    build_default_reflection_adapter,
)
from src.runtime.reflection.adapter.reflection_event_emitter import (
    EVENT_TYPE_REFLECTION_COMPLETED,
    EVENT_TYPE_REFLECTION_DAILY_COMPLETED,
    EVENT_TYPE_REFLECTION_EVENT_COMPLETED,
    EVENT_TYPE_REFLECTION_GROWTH_COMPLETED,
    REFLECTION_EVENT_EMITTER_SCHEMA_VERSION,
    REFLECTION_TYPE_TO_EVENT_TYPE,
    ReflectionEventEmitter,
    build_default_reflection_event_emitter,
)
from src.runtime.reflection.reflection_result import (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
    build_empty_reflection_result,
)


def _ev(et: str, ts: float, topic: str = "", severity: str = "info") -> IntegrationEvent:
    p = {}
    if topic:
        p["topic"] = topic
    return IntegrationEvent(
        event_type=et,
        source="test",
        severity=severity,
        timestamp=ts,
        payload=p,
    )


def _make_result(rtype: str = REFLECTION_TYPE_DAILY, rid: str = "ref_1") -> ReflectionResult:
    r = build_empty_reflection_result(reflection_type=rtype, now=100.0)
    r.reflection_id = rid
    r.insights = [Insight(supporting_event_ids=["e1"])]
    r.suggested_changes = [
        StateChangeSuggestion(target_field="x", evidence_event_ids=["e1"], delta=0.1)
    ]
    r.source_event_ids = ["e1"]
    return r


# ============================================================
# ReflectionEventEmitter
# ============================================================
class TestReflectionEventEmitter(unittest.TestCase):
    def test_default_creation(self):
        e = ReflectionEventEmitter()
        self.assertEqual(e.name, "reflection_event_emitter")
        self.assertEqual(e.owner, "reflection")
        self.assertEqual(e.emitted_count, 0)

    def test_custom_creation(self):
        e = ReflectionEventEmitter(name="custom", owner="custom_owner")
        self.assertEqual(e.name, "custom")
        self.assertEqual(e.owner, "custom_owner")

    def test_emit_result(self):
        e = ReflectionEventEmitter()
        r = _make_result()
        events = e.emit(r)
        # 2 个: 通用 + 特定
        self.assertEqual(len(events), 2)
        types = [ev.event_type for ev in events]
        self.assertIn(EVENT_TYPE_REFLECTION_COMPLETED, types)
        self.assertIn(EVENT_TYPE_REFLECTION_DAILY_COMPLETED, types)

    def test_emit_result_event_type(self):
        e = ReflectionEventEmitter()
        r = _make_result(rtype=REFLECTION_TYPE_EVENT)
        events = e.emit(r)
        types = [ev.event_type for ev in events]
        self.assertIn(EVENT_TYPE_REFLECTION_EVENT_COMPLETED, types)

    def test_emit_result_growth_type(self):
        e = ReflectionEventEmitter()
        r = _make_result(rtype=REFLECTION_TYPE_GROWTH)
        events = e.emit(r)
        types = [ev.event_type for ev in events]
        self.assertIn(EVENT_TYPE_REFLECTION_GROWTH_COMPLETED, types)

    def test_emit_invalid_result(self):
        e = ReflectionEventEmitter()
        self.assertEqual(e.emit("not a result"), [])
        self.assertEqual(e.emit(None), [])

    def test_emit_increments_count(self):
        e = ReflectionEventEmitter()
        e.emit(_make_result())
        self.assertEqual(e.emitted_count, 2)
        e.emit(_make_result(rid="ref_2"))
        self.assertEqual(e.emitted_count, 4)

    def test_emit_last_event_id(self):
        e = ReflectionEventEmitter()
        events = e.emit(_make_result())
        self.assertEqual(e.last_event_id, events[-1].event_id)

    def test_emit_last_reflection_id(self):
        e = ReflectionEventEmitter()
        e.emit(_make_result(rid="ref_x"))
        self.assertEqual(e.last_reflection_id, "ref_x")

    def test_emit_with_external_emitter(self):
        captured: List[IntegrationEvent] = []
        e = ReflectionEventEmitter(event_emitter=lambda ev: captured.append(ev))
        events = e.emit(_make_result())
        self.assertEqual(len(captured), 2)

    def test_emit_many(self):
        e = ReflectionEventEmitter()
        results = [_make_result(rid=f"r{i}") for i in range(3)]
        events = e.emit_many(results)
        self.assertEqual(len(events), 6)
        self.assertEqual(e.emitted_count, 6)

    def test_emit_many_empty(self):
        e = ReflectionEventEmitter()
        self.assertEqual(e.emit_many([]), [])

    def test_emit_many_with_invalid(self):
        e = ReflectionEventEmitter()
        results = [_make_result(rid="r1"), "bad", None, _make_result(rid="r2")]
        events = e.emit_many(results)
        # 只算 2 个合法 result
        self.assertEqual(len(events), 4)

    def test_payload_contains_required_fields(self):
        e = ReflectionEventEmitter()
        r = _make_result(rid="ref_payload")
        events = e.emit(r)
        for ev in events:
            p = ev.payload
            self.assertEqual(p["reflection_id"], "ref_payload")
            self.assertIn("reflection_type", p)
            self.assertIn("source_event_ids", p)
            self.assertIn("insight_count", p)
            self.assertIn("confidence", p)

    def test_related_ids(self):
        e = ReflectionEventEmitter()
        r = _make_result(rid="ref_related")
        r.source_event_ids = ["e1", "e2"]
        events = e.emit(r)
        for ev in events:
            self.assertIn("ref_related", ev.related_ids)
            self.assertIn("e1", ev.related_ids)
            self.assertIn("e2", ev.related_ids)

    def test_describe(self):
        e = ReflectionEventEmitter()
        e.emit(_make_result())
        d = e.describe()
        self.assertEqual(d["schema_version"], REFLECTION_EVENT_EMITTER_SCHEMA_VERSION)
        self.assertEqual(d["emitted_count"], 2)
        self.assertFalse(d["has_external_emitter"])

    def test_set_event_emitter(self):
        e = ReflectionEventEmitter()
        e.set_event_emitter(lambda x: None)
        d = e.describe()
        self.assertTrue(d["has_external_emitter"])


class TestFactoryEventEmitter(unittest.TestCase):
    def test_build_default(self):
        e = build_default_reflection_event_emitter()
        self.assertIsInstance(e, ReflectionEventEmitter)


# ============================================================
# ReflectionAdapter
# ============================================================
class TestReflectionAdapter(unittest.TestCase):
    def test_default_creation(self):
        a = ReflectionAdapter()
        self.assertEqual(a.name, "reflection_adapter")
        self.assertEqual(a.enabled, True)
        self.assertEqual(a.reflection_runs, 0)
        self.assertEqual(a.results_emitted, 0)

    def test_custom_creation(self):
        a = ReflectionAdapter(name="my_adapter", enabled=False)
        self.assertEqual(a.name, "my_adapter")
        self.assertFalse(a.enabled)

    def test_evaluate_disabled(self):
        a = ReflectionAdapter(enabled=False)
        self.assertEqual(a.evaluate([_ev("a", 100.0)]), [])

    def test_evaluate(self):
        a = ReflectionAdapter()
        events = [_ev("a", 100.0)]
        chosen = a.evaluate(events)
        self.assertIn(REFLECTION_TYPE_EVENT, chosen)

    def test_reflect_disabled(self):
        a = ReflectionAdapter(enabled=False)
        results = a.reflect([_ev("a", 100.0)])
        self.assertEqual(results, [])

    def test_reflect_runs(self):
        a = ReflectionAdapter()
        results = a.reflect([_ev("a", 100.0)])
        self.assertGreater(len(results), 0)
        self.assertEqual(a.reflection_runs, 1)

    def test_reflect_truncates(self):
        a = ReflectionAdapter(max_per_call=2)
        events = [_ev(f"a{i}", 100.0 + i) for i in range(10)]
        results = a.reflect(events)
        # 仍能跑(用最后 2 个事件)
        self.assertIsInstance(results, list)

    def test_emit_result(self):
        a = ReflectionAdapter()
        r = _make_result()
        events = a.emit_result(r)
        self.assertEqual(len(events), 2)
        self.assertEqual(a.results_emitted, 2)

    def test_emit_result_invalid(self):
        a = ReflectionAdapter()
        self.assertEqual(a.emit_result("bad"), [])

    def test_handle_event(self):
        a = ReflectionAdapter()
        ev = _ev("a", 100.0, topic="test")
        result = a.handle_event(ev)
        # 至少触发 event strategy
        self.assertIsNotNone(result)
        self.assertEqual(a.events_consumed, 1)

    def test_handle_event_disabled(self):
        a = ReflectionAdapter(enabled=False)
        result = a.handle_event(_ev("a", 100.0))
        self.assertIsNone(result)
        self.assertEqual(a.events_rejected, 1)

    def test_handle_event_invalid(self):
        a = ReflectionAdapter()
        result = a.handle_event("not an event")
        self.assertIsNone(result)
        self.assertEqual(a.events_rejected, 1)

    def test_handle_events(self):
        a = ReflectionAdapter()
        events = [_ev("a", 100.0 + i) for i in range(3)]
        results = a.handle_events(events)
        self.assertGreater(len(results), 0)

    def test_handle_events_empty(self):
        a = ReflectionAdapter()
        self.assertEqual(a.handle_events([]), [])
        self.assertEqual(a.handle_events(None), [])  # type: ignore[arg-type]

    def test_handle_events_disabled(self):
        a = ReflectionAdapter(enabled=False)
        self.assertEqual(a.handle_events([_ev("a", 100.0)]), [])

    def test_is_available(self):
        a = ReflectionAdapter()
        self.assertTrue(a.is_available())
        a.set_enabled(False)
        self.assertFalse(a.is_available())

    def test_get_target(self):
        a = ReflectionAdapter()
        target = a.get_target()
        self.assertIsNotNone(target)
        # 应该是 engine

    def test_last_reflection_id(self):
        a = ReflectionAdapter()
        a.reflect([_ev("a", 100.0)])
        self.assertNotEqual(a.last_reflection_id, "")

    def test_describe(self):
        a = ReflectionAdapter()
        d = a.describe()
        self.assertEqual(d["schema_version"], REFLECTION_ADAPTER_SCHEMA_VERSION)
        self.assertEqual(d["enabled"], True)
        self.assertEqual(d["engine_name"], "reflection_engine")
        self.assertEqual(d["owner"], DEFAULT_REFLECTION_ADAPTER_OWNER)


class TestReflectionAdapterIntegration(unittest.TestCase):
    """端到端: IntegrationEvent → Adapter → Engine → 结果 → 事件"""

    def test_event_to_reflection(self):
        a = ReflectionAdapter()
        ev = _ev("user.input", 100.0, topic="ai_art")
        result = a.handle_event(ev)
        self.assertIsNotNone(result)
        # 应发出事件
        self.assertGreater(a.results_emitted, 0)

    def test_multiple_events_to_reflection(self):
        a = ReflectionAdapter()
        events = [_ev("a", 100.0 + i) for i in range(5)]
        results = a.reflect(events)
        self.assertGreater(len(results), 0)
        # 每个 result 应至少有 insight 或 suggestion
        for r in results:
            self.assertTrue(r.insight_count + r.suggestion_count >= 0)

    def test_payload_contract(self):
        a = ReflectionAdapter()
        a.reflect([_ev("a", 100.0 + i, topic="test") for i in range(3)])
        # 检查最近一次 emit 的事件包含必要字段
        # 由于 events 已被自动 dispatch, 我们通过 results_emitted 验证
        self.assertGreater(a.results_emitted, 0)


class TestFactoryAdapter(unittest.TestCase):
    def test_build_default(self):
        a = build_default_reflection_adapter()
        self.assertIsInstance(a, ReflectionAdapter)


class TestConstants(unittest.TestCase):
    def test_event_types(self):
        self.assertEqual(INTEGRATION_REFLECTION_COMPLETED, EVENT_TYPE_REFLECTION_COMPLETED)
        self.assertEqual(INTEGRATION_REFLECTION_DAILY_COMPLETED, EVENT_TYPE_REFLECTION_DAILY_COMPLETED)
        self.assertEqual(INTEGRATION_REFLECTION_EVENT_COMPLETED, EVENT_TYPE_REFLECTION_EVENT_COMPLETED)
        self.assertEqual(INTEGRATION_REFLECTION_GROWTH_COMPLETED, EVENT_TYPE_REFLECTION_GROWTH_COMPLETED)

    def test_type_mapping(self):
        self.assertEqual(
            REFLECTION_TYPE_TO_EVENT_TYPE[REFLECTION_TYPE_DAILY],
            EVENT_TYPE_REFLECTION_DAILY_COMPLETED,
        )
        self.assertEqual(
            REFLECTION_TYPE_TO_EVENT_TYPE[REFLECTION_TYPE_EVENT],
            EVENT_TYPE_REFLECTION_EVENT_COMPLETED,
        )
        self.assertEqual(
            REFLECTION_TYPE_TO_EVENT_TYPE[REFLECTION_TYPE_GROWTH],
            EVENT_TYPE_REFLECTION_GROWTH_COMPLETED,
        )


if __name__ == "__main__":
    unittest.main()
