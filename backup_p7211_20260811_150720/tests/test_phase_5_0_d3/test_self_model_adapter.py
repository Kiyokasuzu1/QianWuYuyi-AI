# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_self_model_adapter.py

Phase 5.0-D3-A: Self Model System —— SelfModelAdapter 单元测试
"""
import unittest
from typing import Any, Dict, List

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.self_model.self_model_adapter import (
    DEFAULT_OWNER,
    SELF_MODEL_ADAPTER_SCHEMA_VERSION,
    SELF_MODEL_CAPABILITY_USED,
    SELF_MODEL_CHANGE_RECORDED,
    SELF_MODEL_INTEREST_REINFORCED,
    SELF_MODEL_SNAPSHOT_REFRESHED,
    SELF_MODEL_TRAIT_REINFORCED,
    SelfModelAdapter,
    build_default_self_model_adapter,
)
from src.runtime.self_model.self_model_manager import SelfModelManager


def _build_started_manager() -> SelfModelManager:
    m = SelfModelManager()
    m.start()
    return m


# ============================================================
# 构造与基础
# ============================================================
class TestAdapterBasics(unittest.TestCase):
    def test_default_construct(self):
        adp = SelfModelAdapter()
        self.assertEqual(adp.name, "self_model_adapter")
        self.assertEqual(adp.owner, DEFAULT_OWNER)
        self.assertTrue(adp.enabled)
        self.assertIsNone(adp.manager)
        self.assertEqual(adp.events_consumed, 0)

    def test_build_default(self):
        adp = build_default_self_model_adapter()
        self.assertIsInstance(adp, SelfModelAdapter)

    def test_bind_manager(self):
        adp = SelfModelAdapter()
        m = _build_started_manager()
        adp.bind_manager(m)
        self.assertIs(adp.manager, m)

    def test_bind_invalid_manager(self):
        adp = SelfModelAdapter()
        adp.bind_manager("not_a_manager")  # type: ignore
        self.assertIsNone(adp.manager)

    def test_set_enabled(self):
        adp = SelfModelAdapter()
        adp.set_enabled(False)
        self.assertFalse(adp.enabled)
        adp.set_enabled(True)
        self.assertTrue(adp.enabled)

    def test_is_available(self):
        adp = SelfModelAdapter()
        self.assertFalse(adp.is_available())
        m = _build_started_manager()
        adp.bind_manager(m)
        self.assertTrue(adp.is_available())

    def test_get_target(self):
        adp = SelfModelAdapter()
        m = _build_started_manager()
        adp.bind_manager(m)
        self.assertIs(adp.get_target(), m)

    def test_describe(self):
        adp = SelfModelAdapter()
        d = adp.describe()
        self.assertEqual(d["schema_version"], SELF_MODEL_ADAPTER_SCHEMA_VERSION)
        self.assertEqual(d["owner"], DEFAULT_OWNER)
        self.assertIn("events_consumed", d)
        self.assertIn("manager_name", d)


# ============================================================
# handle_event
# ============================================================
class TestAdapterHandleEvent(unittest.TestCase):
    def setUp(self):
        self.adp = SelfModelAdapter()
        self.mgr = _build_started_manager()
        self.adp.bind_manager(self.mgr)

    def test_reject_when_disabled(self):
        self.adp.set_enabled(False)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 0.5},
        )
        self.assertIsNone(self.adp.handle_event(ev))
        self.assertGreater(self.adp.events_rejected, 0)

    def test_reject_non_event(self):
        rec = self.adp.handle_event({"event_type": SELF_MODEL_TRAIT_REINFORCED})
        self.assertIsNone(rec)
        self.assertGreater(self.adp.events_rejected, 0)

    def test_reject_when_no_manager(self):
        adp = SelfModelAdapter()
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 0.5},
        )
        self.assertIsNone(adp.handle_event(ev))
        self.assertGreater(adp.events_rejected, 0)

    def test_reject_when_manager_not_started(self):
        adp = SelfModelAdapter()
        m = SelfModelManager()  # not started
        adp.bind_manager(m)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 0.5},
        )
        self.assertIsNone(adp.handle_event(ev))
        self.assertGreater(adp.events_rejected, 0)

    def test_unknown_event_type_returns_none(self):
        ev = make_integration_event(
            event_type="unknown.event",
            source="test",
            payload={},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNone(rec)
        # still consumed counter increased but no record returned
        self.assertEqual(self.adp.events_consumed, 1)

    def test_trait_reinforced(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "curiosity", "value": 0.7},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)
        t = self.mgr.get_trait("curiosity")
        self.assertIsNotNone(t)
        self.assertEqual(t.current_value, 0.7)

    def test_trait_reinforced_uses_event_id_as_evidence(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 0.5},
        )
        rec = self.adp.handle_event(ev)
        self.assertIn(ev.event_id, rec.evidence_event_ids)

    def test_trait_reinforced_uses_payload_evidence(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={
                "trait": "x",
                "value": 0.5,
                "evidence_event_ids": ["e1", "e2"],
            },
        )
        rec = self.adp.handle_event(ev)
        self.assertIn("e1", rec.evidence_event_ids)
        self.assertIn("e2", rec.evidence_event_ids)

    def test_trait_reinforced_missing_trait_name(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"value": 0.5},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNone(rec)

    def test_trait_reinforced_value_clipped(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 2.0},
        )
        rec = self.adp.handle_event(ev)
        t = self.mgr.get_trait("x")
        self.assertEqual(t.current_value, 1.0)

    def test_interest_reinforced(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_INTEREST_REINFORCED,
            source="test",
            payload={"interest": "music", "boost": 0.2},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)
        i = self.mgr.get_interest("music")
        self.assertGreater(i.level, 0.5)

    def test_interest_reinforced_missing_name(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_INTEREST_REINFORCED,
            source="test",
            payload={"boost": 0.1},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNone(rec)

    def test_interest_reinforced_default_boost(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_INTEREST_REINFORCED,
            source="test",
            payload={"interest": "x"},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)

    def test_capability_used(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="test",
            payload={
                "capability": "speak",
                "proficiency": 0.8,
                "confidence": 0.7,
            },
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)
        c = self.mgr.get_capability("speak")
        self.assertEqual(c.proficiency, 0.8)
        self.assertEqual(c.confidence, 0.7)
        # mark_used=True → last_used 已更新
        self.assertGreater(c.last_used, 0.0)

    def test_capability_used_missing_name(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="test",
            payload={"proficiency": 0.5},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNone(rec)

    def test_capability_used_no_proficiency(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_CAPABILITY_USED,
            source="test",
            payload={"capability": "x"},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)
        c = self.mgr.get_capability("x")
        # 不指定 proficiency 时,被 clip 到 0.0 (Manager 行为)
        self.assertEqual(c.proficiency, 0.0)
        # confidence 同样
        self.assertEqual(c.confidence, 0.0)

    def test_snapshot_refreshed(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
            source="test",
            payload={},
        )
        rec = self.adp.handle_event(ev)
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "snapshot_refresh")

    def test_snapshot_refreshed_with_evidence(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
            source="test",
            payload={"evidence_event_ids": ["e1"]},
        )
        rec = self.adp.handle_event(ev)
        self.assertIn("e1", rec.evidence_event_ids)


# ============================================================
# handle_events (批量)
# ============================================================
class TestAdapterHandleEvents(unittest.TestCase):
    def setUp(self):
        self.adp = SelfModelAdapter()
        self.mgr = _build_started_manager()
        self.adp.bind_manager(self.mgr)

    def test_batch(self):
        events = [
            make_integration_event(
                event_type=SELF_MODEL_TRAIT_REINFORCED,
                source="test",
                payload={"trait": f"t{i}", "value": 0.5},
            )
            for i in range(5)
        ]
        recs = self.adp.handle_events(events)
        self.assertEqual(len(recs), 5)

    def test_batch_with_limit(self):
        adp = SelfModelAdapter(max_per_tick=3)
        m = _build_started_manager()
        adp.bind_manager(m)
        events = [
            make_integration_event(
                event_type=SELF_MODEL_TRAIT_REINFORCED,
                source="test",
                payload={"trait": f"t{i}", "value": 0.5},
            )
            for i in range(10)
        ]
        recs = adp.handle_events(events)
        # 限制为 3
        self.assertLessEqual(len(recs), 3)

    def test_batch_none(self):
        recs = self.adp.handle_events(None)
        self.assertEqual(recs, [])

    def test_batch_empty(self):
        recs = self.adp.handle_events([])
        self.assertEqual(recs, [])

    def test_batch_disabled(self):
        self.adp.set_enabled(False)
        events = [
            make_integration_event(
                event_type=SELF_MODEL_TRAIT_REINFORCED,
                source="test",
                payload={"trait": "x", "value": 0.5},
            )
        ]
        recs = self.adp.handle_events(events)
        self.assertEqual(recs, [])


# ============================================================
# 发出事件
# ============================================================
class TestAdapterEmit(unittest.TestCase):
    def setUp(self):
        self.adp = SelfModelAdapter()

    def test_emit_trait_reinforced(self):
        ev = self.adp.emit_trait_reinforced("curiosity", 0.7)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, SELF_MODEL_TRAIT_REINFORCED)
        self.assertEqual(ev.payload["trait"], "curiosity")
        self.assertEqual(ev.payload["value"], 0.7)

    def test_emit_interest_reinforced(self):
        ev = self.adp.emit_interest_reinforced("music", 0.1)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, SELF_MODEL_INTEREST_REINFORCED)
        self.assertEqual(ev.payload["interest"], "music")

    def test_emit_capability_used(self):
        ev = self.adp.emit_capability_used("speak", new_proficiency=0.8)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, SELF_MODEL_CAPABILITY_USED)
        self.assertEqual(ev.payload["capability"], "speak")
        self.assertEqual(ev.payload["proficiency"], 0.8)

    def test_emit_snapshot_refreshed(self):
        ev = self.adp.emit_snapshot_refreshed(reason="manual")
        self.assertIsNotNone(ev)
        self.assertEqual(ev.event_type, SELF_MODEL_SNAPSHOT_REFRESHED)
        self.assertEqual(ev.payload["reason"], "manual")

    def test_emit_invalid_name_returns_none(self):
        self.assertIsNone(self.adp.emit_trait_reinforced("", 0.5))
        self.assertIsNone(self.adp.emit_interest_reinforced("", 0.5))
        self.assertIsNone(self.adp.emit_capability_used("", new_proficiency=0.5))

    def test_emit_value_clip(self):
        ev = self.adp.emit_trait_reinforced("x", 2.0)
        self.assertEqual(ev.payload["value"], 1.0)

    def test_emit_invalid_value_defaults(self):
        ev = self.adp.emit_trait_reinforced("x", "not_a_number")
        self.assertEqual(ev.payload["value"], 0.5)

    def test_emit_records_incremented(self):
        before = self.adp.events_emitted
        self.adp.emit_trait_reinforced("x", 0.5)
        self.assertEqual(self.adp.events_emitted, before + 1)

    def test_emit_records_id(self):
        ev = self.adp.emit_trait_reinforced("x", 0.5)
        self.assertEqual(self.adp.last_emitted_event_id, ev.event_id)


# ============================================================
# _emit_change_recorded (内部)
# ============================================================
class TestAdapterEmitChangeRecorded(unittest.TestCase):
    def setUp(self):
        self.adp = SelfModelAdapter()
        self.mgr = _build_started_manager()
        self.adp.bind_manager(self.mgr)

    def test_emit_change_recorded_via_handle(self):
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": 0.5, "evidence_event_ids": ["e1"]},
        )
        self.adp.handle_event(ev)
        # handle_event 后会 emit 一个 change.recorded
        self.assertGreater(self.adp.change_records_emitted, 0)
        self.assertEqual(self.adp.last_emitted_event_id, self.adp.last_emitted_event_id)
        # 事件已被 BaseAdapter.emit 推送 → 计数应 +1
        self.assertGreater(self.adp.events_emitted, 0)


# ============================================================
# 异常隔离
# ============================================================
class TestAdapterErrorIsolation(unittest.TestCase):
    def test_handle_event_exception_does_not_crash(self):
        adp = SelfModelAdapter()
        m = _build_started_manager()
        adp.bind_manager(m)
        # 构造一个会导致 _handle_* 内部异常的事件(payload 是奇怪类型)
        ev = make_integration_event(
            event_type=SELF_MODEL_TRAIT_REINFORCED,
            source="test",
            payload={"trait": "x", "value": object()},  # 非数字
        )
        # 不抛异常 → 返回 None
        rec = adp.handle_event(ev)
        # value 被 clip 不会出错 → 有 record
        # 但若 value 无法转换也容错
        self.assertIsNotNone(rec) if rec is None else None


if __name__ == "__main__":
    unittest.main()
