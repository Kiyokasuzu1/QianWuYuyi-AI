# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_self_state.py

Phase 5.0-D3-A: Self Model System —— SelfState / IdentityView / TraitState /
                 CapabilityState / InterestState / ChangeRecord 单元测试
"""
import unittest
from typing import Any, Dict, List

from src.runtime.self_model.self_state import (
    SELF_MODEL_STATE_SCHEMA_VERSION,
    IdentityView,
    TraitState,
    CapabilityState,
    InterestState,
    ChangeRecord,
    SelfState,
    MAX_TRAITS,
    MAX_CAPABILITIES,
    MAX_INTERESTS,
    VALID_TRAIT_DIRECTIONS,
    VALID_CHANGE_KINDS,
    VALID_CHANGE_SOURCES,
    build_default_self_state,
)


# ============================================================
# IdentityView 测试
# ============================================================
class TestIdentityViewBasics(unittest.TestCase):
    def test_default_view(self):
        v = IdentityView.default_view(now=1000.0)
        self.assertEqual(v.name, "yuyi")
        self.assertEqual(v.archetype, "companion_ai")
        self.assertEqual(v.language, "zh-CN")
        self.assertTrue(v.is_valid())
        self.assertEqual(v.created_at, 1000.0)
        self.assertEqual(v.updated_at, 1000.0)

    def test_to_dict_contains_schema(self):
        v = IdentityView.default_view(now=0.0)
        d = v.to_dict()
        self.assertIn("schema_version", d)
        self.assertEqual(d["schema_version"], SELF_MODEL_STATE_SCHEMA_VERSION)
        self.assertIn("name", d)
        self.assertIn("core_values", d)

    def test_is_valid_when_name_empty(self):
        v = IdentityView(name="", archetype="x", identity_id="abc")
        self.assertFalse(v.is_valid())

    def test_with_updated_name(self):
        v = IdentityView.default_view(now=1000.0)
        v2 = v.with_updated(now=2000.0, name="newname")
        self.assertEqual(v2.name, "newname")
        self.assertEqual(v2.updated_at, 2000.0)
        # 原对象不变
        self.assertEqual(v.name, "yuyi")
        self.assertEqual(v.updated_at, 1000.0)

    def test_with_updated_core_values(self):
        v = IdentityView.default_view(now=0.0)
        v2 = v.with_updated(now=0.0, core_values=("a", "b", "c"))
        self.assertEqual(v2.core_values, ("a", "b", "c"))

    def test_with_updated_attributes(self):
        v = IdentityView.default_view(now=0.0)
        v2 = v.with_updated(now=0.0, attributes={"k": "v"})
        self.assertEqual(v2.attributes, {"k": "v"})

    def test_attributes_clipped(self):
        v = IdentityView(attributes={f"k{i}": "v" for i in range(100)})
        self.assertLessEqual(len(v.attributes), 32)

    def test_core_values_dedupe(self):
        v = IdentityView(core_values=("a", "a", "b", "a"))
        self.assertEqual(v.core_values, ("a", "b"))

    def test_name_clipped_long(self):
        v = IdentityView(name="x" * 1000)
        self.assertLessEqual(len(v.name), 64)


# ============================================================
# TraitState 测试
# ============================================================
class TestTraitStateBasics(unittest.TestCase):
    def test_default_trait(self):
        t = TraitState(name="curiosity")
        self.assertTrue(t.is_valid())
        self.assertEqual(t.direction, "stable")
        self.assertEqual(t.current_value, 0.5)

    def test_invalid_direction_becomes_stable(self):
        t = TraitState(name="curiosity", direction="invalid")
        self.assertEqual(t.direction, "stable")

    def test_value_clipped(self):
        t = TraitState(name="curiosity", current_value=2.0)
        self.assertEqual(t.current_value, 1.0)
        t2 = TraitState(name="curiosity", current_value=-0.5)
        self.assertEqual(t2.current_value, 0.0)

    def test_value_nan_becomes_zero(self):
        t = TraitState(name="curiosity", current_value=float("nan"))
        self.assertEqual(t.current_value, 0.0)

    def test_evidence_dedup(self):
        t = TraitState(evidence_event_ids=["a", "b", "a", "c"])
        self.assertEqual(t.evidence_event_ids, ["a", "b", "c"])

    def test_evidence_from_string(self):
        t = TraitState(evidence_event_ids="single_evidence")
        self.assertEqual(t.evidence_event_ids, ["single_evidence"])

    def test_evidence_from_invalid(self):
        t = TraitState(evidence_event_ids=42)
        self.assertEqual(t.evidence_event_ids, ["42"])

    def test_with_value_increase_direction(self):
        t = TraitState(name="x", current_value=0.3, direction="stable")
        t2 = t.with_value(now=1000.0, new_value=0.6)
        self.assertEqual(t2.direction, "rising")
        self.assertEqual(t2.current_value, 0.6)
        self.assertEqual(t2.last_updated, 1000.0)

    def test_with_value_decrease_direction(self):
        t = TraitState(name="x", current_value=0.6)
        t2 = t.with_value(now=1000.0, new_value=0.3)
        self.assertEqual(t2.direction, "falling")

    def test_with_value_evidence_merges(self):
        t = TraitState(evidence_event_ids=["a", "b"])
        t2 = t.with_value(now=1000.0, new_value=0.5, new_evidence=["b", "c"])
        self.assertIn("a", t2.evidence_event_ids)
        self.assertIn("b", t2.evidence_event_ids)
        self.assertIn("c", t2.evidence_event_ids)

    def test_with_value_clipped_evidence(self):
        t = TraitState(evidence_event_ids=[f"e{i}" for i in range(70)])
        t2 = t.with_value(now=0.0, new_value=0.5, new_evidence=[f"new{i}" for i in range(70)])
        self.assertLessEqual(len(t2.evidence_event_ids), 64)


# ============================================================
# CapabilityState 测试
# ============================================================
class TestCapabilityStateBasics(unittest.TestCase):
    def test_default(self):
        c = CapabilityState(name="speak")
        self.assertTrue(c.is_valid())
        self.assertTrue(c.enabled)
        self.assertEqual(c.proficiency, 0.5)

    def test_with_update_mark_used(self):
        c = CapabilityState(name="x", last_used=0.0)
        c2 = c.with_update(now=1000.0, mark_used=True)
        self.assertEqual(c2.last_used, 1000.0)
        self.assertEqual(c.last_used, 0.0)  # 原对象不变

    def test_with_update_enabled(self):
        c = CapabilityState(enabled=True)
        c2 = c.with_update(now=0.0, enabled=False)
        self.assertFalse(c2.enabled)
        self.assertTrue(c.enabled)

    def test_with_update_evidence_merges(self):
        c = CapabilityState(evidence_event_ids=["a"])
        c2 = c.with_update(now=0.0, new_evidence=["b"])
        self.assertEqual(set(c2.evidence_event_ids), {"a", "b"})

    def test_invalid_proficiency_clipped(self):
        c = CapabilityState(proficiency=2.0)
        self.assertEqual(c.proficiency, 1.0)


# ============================================================
# InterestState 测试
# ============================================================
class TestInterestStateBasics(unittest.TestCase):
    def test_default(self):
        i = InterestState(name="music")
        self.assertTrue(i.is_valid())
        self.assertEqual(i.level, 0.5)
        self.assertEqual(i.decay_rate, 0.0)

    def test_with_level_explicit(self):
        i = InterestState(name="x", level=0.3)
        i2 = i.with_level(now=0.0, new_level=0.7)
        self.assertEqual(i2.level, 0.7)

    def test_with_level_boost(self):
        i = InterestState(level=0.3)
        i2 = i.with_level(now=0.0, boost=0.2)
        self.assertEqual(i2.level, 0.5)

    def test_with_level_boost_clipped(self):
        i = InterestState(level=0.9)
        i2 = i.with_level(now=0.0, boost=0.5)
        self.assertEqual(i2.level, 1.0)

    def test_decay(self):
        i = InterestState(level=0.5, decay_rate=0.01)
        i2 = i.decay(now=1000.0, delta_seconds=10.0)
        self.assertAlmostEqual(i2.level, 0.4, places=4)
        # 原对象不变
        self.assertEqual(i.level, 0.5)

    def test_decay_no_change_when_delta_zero(self):
        i = InterestState(level=0.5, decay_rate=0.1)
        i2 = i.decay(now=0.0, delta_seconds=0.0)
        self.assertEqual(i2.level, 0.5)

    def test_decay_clamped_to_zero(self):
        i = InterestState(level=0.1, decay_rate=0.5)
        i2 = i.decay(now=0.0, delta_seconds=10.0)
        self.assertEqual(i2.level, 0.0)

    def test_tags_dedupe(self):
        i = InterestState(tags=("a", "b", "a", "c"))
        self.assertEqual(i.tags, ("a", "b", "c"))


# ============================================================
# ChangeRecord 测试
# ============================================================
class TestChangeRecordBasics(unittest.TestCase):
    def test_default(self):
        r = ChangeRecord(target_id="t1", target_name="x")
        self.assertTrue(r.is_valid())
        self.assertFalse(r.has_evidence())
        self.assertIn(r.change_kind, VALID_CHANGE_KINDS)

    def test_invalid_kind_becomes_trait_update(self):
        r = ChangeRecord(change_kind="not_a_kind", target_id="t1", target_name="x")
        self.assertEqual(r.change_kind, "trait_update")

    def test_invalid_source_becomes_unknown(self):
        r = ChangeRecord(change_source="not_a_source", target_id="t1", target_name="x")
        self.assertEqual(r.change_source, "unknown")

    def test_evidence_required_marker(self):
        r = ChangeRecord(evidence_event_ids=["e1"], target_id="t1", target_name="x")
        self.assertTrue(r.has_evidence())

    def test_to_dict_schema(self):
        r = ChangeRecord(target_id="t1", target_name="x", evidence_event_ids=["e1"])
        d = r.to_dict()
        self.assertIn("schema_version", d)
        self.assertEqual(d["schema_version"], SELF_MODEL_STATE_SCHEMA_VERSION)


# ============================================================
# SelfState 测试
# ============================================================
class TestSelfStateBasics(unittest.TestCase):
    def test_empty(self):
        s = SelfState.empty(now=1000.0)
        self.assertEqual(s.trait_count, 0)
        self.assertEqual(s.capability_count, 0)
        self.assertEqual(s.interest_count, 0)
        self.assertEqual(s.created_at, 1000.0)
        self.assertEqual(s.last_refreshed, 1000.0)

    def test_build_default(self):
        s = build_default_self_state(now=500.0)
        self.assertEqual(s.identity.name, "yuyi")
        self.assertEqual(s.created_at, 500.0)

    def test_with_trait(self):
        s = SelfState.empty(now=0.0)
        t = TraitState(name="curiosity", current_value=0.8, trait_id="trt_1")
        s2 = s.with_trait(now=100.0, trait=t)
        self.assertEqual(s2.trait_count, 1)
        # 原对象不变
        self.assertEqual(s.trait_count, 0)
        # 新对象的 trait 已存入
        self.assertIn("curiosity", s2.traits)
        self.assertEqual(s2.last_refreshed, 100.0)

    def test_with_capability(self):
        s = SelfState.empty(now=0.0)
        c = CapabilityState(name="speak", enabled=True, capability_id="cap_1")
        s2 = s.with_capability(now=200.0, capability=c)
        self.assertEqual(s2.capability_count, 1)
        self.assertEqual(s2.enabled_capability_count, 1)

    def test_with_interest(self):
        s = SelfState.empty(now=0.0)
        i = InterestState(name="music", level=0.6, interest_id="int_1")
        s2 = s.with_interest(now=300.0, interest=i)
        self.assertEqual(s2.interest_count, 1)

    def test_with_removed(self):
        s = SelfState.empty(now=0.0)
        s = s.with_trait(now=100.0, trait=TraitState(name="t1", trait_id="trt_1"))
        s = s.with_trait(now=200.0, trait=TraitState(name="t2", trait_id="trt_2"))
        s2 = s.with_removed(now=300.0, trait="t1")
        self.assertEqual(s2.trait_count, 1)
        self.assertIn("t2", s2.traits)
        self.assertNotIn("t1", s2.traits)

    def test_with_interest_decay(self):
        s = SelfState.empty(now=0.0)
        s = s.with_interest(
            now=0.0,
            interest=InterestState(name="music", level=0.5, decay_rate=0.01, interest_id="int_1"),
        )
        s2 = s.with_interest_decay(now=10.0, delta_seconds=10.0)
        new = s2.interests["music"]
        self.assertLess(new.level, 0.5)

    def test_with_refresh(self):
        s = SelfState.empty(now=0.0)
        s2 = s.with_refresh(now=999.0)
        self.assertEqual(s2.last_refreshed, 999.0)

    def test_with_identity(self):
        s = SelfState.empty(now=0.0)
        nv = IdentityView.default_view(now=100.0).with_updated(now=100.0, name="newname")
        s2 = s.with_identity(now=100.0, new_view=nv)
        self.assertEqual(s2.identity.name, "newname")
        self.assertEqual(s.identity.name, "yuyi")

    def test_with_trait_capacity_protection(self):
        s = SelfState.empty(now=0.0)
        # 插入超量 traits(>MAX_TRAITS)
        for i in range(MAX_TRAITS + 5):
            s = s.with_trait(
                now=float(i),
                trait=TraitState(name=f"t{i}", trait_id=f"trt_{i}"),
            )
        self.assertLessEqual(s.trait_count, MAX_TRAITS)

    def test_health_check(self):
        s = SelfState.empty(now=0.0)
        hc = s.health_check()
        self.assertTrue(hc["healthy"])
        self.assertEqual(hc["trait_count"], 0)

    def test_snapshot_dict(self):
        s = SelfState.empty(now=0.0)
        s = s.with_trait(now=10.0, trait=TraitState(name="t1", trait_id="trt_1"))
        d = s.snapshot_dict()
        self.assertIn("identity", d)
        self.assertIn("traits", d)
        self.assertIn("counters", d)
        self.assertEqual(d["counters"]["traits"], 1)

    def test_total_evidence_count(self):
        s = SelfState.empty(now=0.0)
        t = TraitState(name="t1", evidence_event_ids=["e1", "e2"], trait_id="trt_1")
        s2 = s.with_trait(now=0.0, trait=t)
        self.assertEqual(s2.total_evidence_count, 2)

    def test_repr(self):
        s = SelfState.empty(now=0.0)
        r = repr(s)
        self.assertIn("SelfState", r)
        self.assertIn("yuyi", r)


if __name__ == "__main__":
    unittest.main()
