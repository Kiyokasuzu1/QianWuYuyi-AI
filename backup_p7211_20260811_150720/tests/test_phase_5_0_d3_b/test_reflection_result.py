# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_reflection_result.py

Phase 5.0-D3-B: ReflectionResult / Insight / StateChangeSuggestion 单元测试。
"""
import unittest

from src.runtime.reflection.reflection_result import (
    ALL_INSIGHT_CATEGORIES,
    ALL_REFLECTION_TYPES,
    INSIGHT_CATEGORY_CONCERN,
    INSIGHT_CATEGORY_GROWTH,
    INSIGHT_CATEGORY_PATTERN,
    INSIGHT_CATEGORY_PREFERENCE,
    REFLECTION_RESULT_SCHEMA_VERSION,
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
    build_empty_reflection_result,
)


class TestInsightCreation(unittest.TestCase):
    def test_default_creation(self):
        i = Insight()
        self.assertTrue(i.insight_id)
        self.assertEqual(i.category, INSIGHT_CATEGORY_PATTERN)
        self.assertEqual(i.confidence, 0.5)

    def test_custom_creation(self):
        i = Insight(
            category=INSIGHT_CATEGORY_PREFERENCE,
            description="test",
            supporting_event_ids=["e1", "e2"],
            confidence=0.8,
        )
        self.assertEqual(i.category, INSIGHT_CATEGORY_PREFERENCE)
        self.assertEqual(i.description, "test")
        self.assertEqual(len(i.supporting_event_ids), 2)
        self.assertEqual(i.confidence, 0.8)

    def test_invalid_category_fallback(self):
        i = Insight(category="unknown_cat")
        self.assertEqual(i.category, INSIGHT_CATEGORY_PATTERN)

    def test_confidence_clipping(self):
        i = Insight(confidence=2.0)
        self.assertEqual(i.confidence, 1.0)
        i = Insight(confidence=-1.0)
        self.assertEqual(i.confidence, 0.0)
        i = Insight(confidence="bad")
        self.assertEqual(i.confidence, 0.5)

    def test_evidence_normalization(self):
        i = Insight(supporting_event_ids=["e1", "e1", "e2", None, 123, ""])
        # None / "" / dup 都会被剔除
        self.assertIn("e1", i.supporting_event_ids)
        self.assertIn("e2", i.supporting_event_ids)
        self.assertIn("123", i.supporting_event_ids)
        self.assertEqual(len(i.supporting_event_ids), 3)

    def test_evidence_truncation(self):
        evidence = [f"e{i}" for i in range(100)]
        i = Insight(supporting_event_ids=evidence)
        self.assertLessEqual(len(i.supporting_event_ids), 64)

    def test_description_clipping(self):
        long = "x" * 2000
        i = Insight(description=long)
        self.assertEqual(len(i.description), 512)

    def test_to_dict(self):
        i = Insight(
            category=INSIGHT_CATEGORY_GROWTH,
            description="d",
            supporting_event_ids=["e1"],
            confidence=0.7,
        )
        d = i.to_dict()
        self.assertIn("schema_version", d)
        self.assertEqual(d["schema_version"], REFLECTION_RESULT_SCHEMA_VERSION)
        self.assertEqual(d["category"], INSIGHT_CATEGORY_GROWTH)
        self.assertEqual(d["description"], "d")
        self.assertEqual(d["supporting_event_ids"], ["e1"])
        self.assertEqual(d["confidence"], 0.7)

    def test_has_evidence(self):
        i1 = Insight(supporting_event_ids=[])
        self.assertFalse(i1.has_evidence())
        i2 = Insight(supporting_event_ids=["e1"])
        self.assertTrue(i2.has_evidence())

    def test_is_valid(self):
        i = Insight()
        self.assertTrue(i.is_valid())
        i.insight_id = ""
        self.assertFalse(i.is_valid())

    def test_metadata_truncated(self):
        meta = {f"k{i}": f"v{i}" for i in range(100)}
        i = Insight(metadata=meta)
        self.assertLessEqual(len(i.metadata), 16)


class TestStateChangeSuggestion(unittest.TestCase):
    def test_default_creation(self):
        s = StateChangeSuggestion()
        self.assertEqual(s.target_field, "")
        self.assertEqual(s.delta, 0.0)
        self.assertEqual(s.confidence, 0.5)

    def test_valid_requires_evidence(self):
        s = StateChangeSuggestion(target_field="interest_state.x", evidence_event_ids=["e1"])
        self.assertTrue(s.is_valid())
        s2 = StateChangeSuggestion(target_field="interest_state.x", evidence_event_ids=[])
        self.assertFalse(s2.is_valid())
        s3 = StateChangeSuggestion(target_field="", evidence_event_ids=["e1"])
        self.assertFalse(s3.is_valid())

    def test_delta_clipping(self):
        # delta 是自由浮点
        s = StateChangeSuggestion(delta=1.5)
        self.assertEqual(s.delta, 1.5)
        s = StateChangeSuggestion(delta="bad")
        self.assertEqual(s.delta, 0.0)

    def test_confidence_clipping(self):
        s = StateChangeSuggestion(confidence=10.0, target_field="x", evidence_event_ids=["e"])
        self.assertEqual(s.confidence, 1.0)
        s = StateChangeSuggestion(confidence=-1.0, target_field="x", evidence_event_ids=["e"])
        self.assertEqual(s.confidence, 0.0)

    def test_evidence_truncation(self):
        evidence = [f"e{i}" for i in range(200)]
        s = StateChangeSuggestion(target_field="x", evidence_event_ids=evidence)
        self.assertLessEqual(len(s.evidence_event_ids), 64)

    def test_to_dict(self):
        s = StateChangeSuggestion(
            target_field="interest_state.ai_art",
            delta=0.1,
            reason="r",
            evidence_event_ids=["e1"],
            confidence=0.7,
        )
        d = s.to_dict()
        self.assertEqual(d["target_field"], "interest_state.ai_art")
        self.assertEqual(d["delta"], 0.1)
        self.assertEqual(d["evidence_event_ids"], ["e1"])
        self.assertIn("schema_version", d)

    def test_reason_clipping(self):
        s = StateChangeSuggestion(reason="x" * 2000, target_field="x", evidence_event_ids=["e"])
        self.assertEqual(len(s.reason), 512)

    def test_target_field_clipping(self):
        s = StateChangeSuggestion(target_field="t" * 500, evidence_event_ids=["e"])
        self.assertEqual(len(s.target_field), 128)

    def test_has_evidence(self):
        s = StateChangeSuggestion(target_field="x", evidence_event_ids=["e1"])
        self.assertTrue(s.has_evidence())
        s2 = StateChangeSuggestion(target_field="x", evidence_event_ids=[])
        self.assertFalse(s2.has_evidence())


class TestReflectionResultCreation(unittest.TestCase):
    def test_default_creation(self):
        r = ReflectionResult()
        self.assertTrue(r.reflection_id)
        self.assertEqual(r.reflection_type, REFLECTION_TYPE_DAILY)
        self.assertEqual(r.confidence, 0.5)
        self.assertEqual(r.evidence_strength, 0.5)

    def test_invalid_type_fallback(self):
        r = ReflectionResult(reflection_type="invalid")
        self.assertEqual(r.reflection_type, REFLECTION_TYPE_DAILY)

    def test_window_validation(self):
        r = ReflectionResult(window_start=200, window_end=100)
        self.assertFalse(r.is_valid())

    def test_valid_result(self):
        r = ReflectionResult(window_start=100, window_end=200, source_event_ids=["e1"])
        self.assertTrue(r.is_valid())

    def test_insights_have_evidence_check(self):
        r = ReflectionResult()
        r.insights = [Insight(supporting_event_ids=["e1"])]
        self.assertTrue(r.all_insights_have_evidence())
        r.insights = [Insight(supporting_event_ids=[])]
        self.assertFalse(r.all_insights_have_evidence())

    def test_suggestions_have_evidence_check(self):
        r = ReflectionResult()
        r.suggested_changes = [StateChangeSuggestion(target_field="x", evidence_event_ids=["e1"])]
        self.assertTrue(r.has_evidence())
        r.suggested_changes = [StateChangeSuggestion(target_field="x", evidence_event_ids=[])]
        self.assertFalse(r.has_evidence())

    def test_source_event_ids_dedup(self):
        r = ReflectionResult(source_event_ids=["e1", "e1", "e2", None, 123])
        ids = r.source_event_ids
        self.assertEqual(ids.count("e1"), 1)
        self.assertIn("e2", ids)
        self.assertIn("123", ids)

    def test_source_event_ids_truncation(self):
        ids = [f"e{i}" for i in range(500)]
        r = ReflectionResult(source_event_ids=ids)
        self.assertLessEqual(len(r.source_event_ids), 256)

    def test_insights_truncation(self):
        ins = [Insight(supporting_event_ids=[f"e{i}"]) for i in range(100)]
        r = ReflectionResult(insights=ins)
        self.assertLessEqual(len(r.insights), 64)

    def test_suggestions_truncation(self):
        sugs = [StateChangeSuggestion(target_field=f"x{i}", evidence_event_ids=["e1"]) for i in range(100)]
        r = ReflectionResult(suggested_changes=sugs)
        self.assertLessEqual(len(r.suggested_changes), 64)

    def test_to_dict_roundtrip(self):
        r = ReflectionResult(
            reflection_type=REFLECTION_TYPE_GROWTH,
            triggered_at=100.0,
            window_start=0.0,
            window_end=200.0,
            source_event_ids=["e1", "e2"],
            insights=[Insight(supporting_event_ids=["e1"], category=INSIGHT_CATEGORY_GROWTH)],
            suggested_changes=[StateChangeSuggestion(target_field="x", evidence_event_ids=["e2"])],
            confidence=0.7,
            evidence_strength=0.8,
        )
        d = r.to_dict()
        self.assertIn("schema_version", d)
        r2 = ReflectionResult.from_dict(d)
        self.assertEqual(r2.reflection_id, r.reflection_id)
        self.assertEqual(r2.reflection_type, REFLECTION_TYPE_GROWTH)
        self.assertEqual(r2.source_event_ids, r.source_event_ids)
        self.assertEqual(r2.confidence, r.confidence)
        self.assertEqual(r2.evidence_strength, r.evidence_strength)
        self.assertEqual(len(r2.insights), 1)
        self.assertEqual(len(r2.suggested_changes), 1)
        self.assertEqual(r2.insights[0].category, INSIGHT_CATEGORY_GROWTH)

    def test_from_dict_invalid_returns_default(self):
        r = ReflectionResult.from_dict(None)
        self.assertIsInstance(r, ReflectionResult)
        r = ReflectionResult.from_dict("not a dict")
        self.assertIsInstance(r, ReflectionResult)

    def test_from_dict_handles_missing_insight_fields(self):
        d = {
            "insights": [{"category": "invalid", "confidence": "bad"}],
            "suggested_changes": [{"target_field": "x", "delta": "bad"}],
        }
        r = ReflectionResult.from_dict(d)
        self.assertEqual(len(r.insights), 1)
        self.assertEqual(len(r.suggested_changes), 1)
        self.assertEqual(r.insights[0].category, INSIGHT_CATEGORY_PATTERN)
        self.assertEqual(r.suggested_changes[0].delta, 0.0)

    def test_summary(self):
        r = ReflectionResult(
            source_event_ids=["e1", "e2"],
            insights=[Insight()],
            suggested_changes=[StateChangeSuggestion(target_field="x", evidence_event_ids=["e1"])],
        )
        s = r.summary()
        self.assertEqual(s["source_event_count"], 2)
        self.assertEqual(s["insight_count"], 1)
        self.assertEqual(s["suggestion_count"], 1)

    def test_insight_count_property(self):
        r = ReflectionResult(insights=[Insight() for _ in range(5)])
        self.assertEqual(r.insight_count, 5)

    def test_suggestion_count_property(self):
        r = ReflectionResult(
            suggested_changes=[
                StateChangeSuggestion(target_field=f"x{i}", evidence_event_ids=["e1"])
                for i in range(3)
            ]
        )
        self.assertEqual(r.suggestion_count, 3)

    def test_build_empty(self):
        r = build_empty_reflection_result(reflection_type=REFLECTION_TYPE_EVENT, now=100.0)
        self.assertEqual(r.reflection_type, REFLECTION_TYPE_EVENT)
        self.assertEqual(r.triggered_at, 100.0)


class TestConstants(unittest.TestCase):
    def test_all_reflection_types(self):
        self.assertIn(REFLECTION_TYPE_DAILY, ALL_REFLECTION_TYPES)
        self.assertIn(REFLECTION_TYPE_EVENT, ALL_REFLECTION_TYPES)
        self.assertIn(REFLECTION_TYPE_GROWTH, ALL_REFLECTION_TYPES)

    def test_all_insight_categories(self):
        self.assertIn(INSIGHT_CATEGORY_PATTERN, ALL_INSIGHT_CATEGORIES)
        self.assertIn(INSIGHT_CATEGORY_PREFERENCE, ALL_INSIGHT_CATEGORIES)
        self.assertIn(INSIGHT_CATEGORY_GROWTH, ALL_INSIGHT_CATEGORIES)
        self.assertIn(INSIGHT_CATEGORY_CONCERN, ALL_INSIGHT_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
