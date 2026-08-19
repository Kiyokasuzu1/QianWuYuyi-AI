"""
Phase 3.5.12: Reflection Evaluation Layer 测试

覆盖：
 1. Schema 结构（ReflectionEvaluation / EvaluationDimension / EvaluationHistoryEntry / EvaluatorSnapshot）
 2. ReflectionEvaluator - ReflectionInsight 评估
 3. ReflectionEvaluator - GrowthRecord 评估
 4. ReflectionEvaluator - IdentityChange + ContinuityReport 评估
 5. ReflectionEvaluator - RelationshipState 评估
 6. 综合评分与推荐结论（accept/reject/defer/revisit）
 7. 风险标记（identity_misalignment / instability / contradiction_risk 等）
 8. 历史与快照（审计能力）
 9. RuntimeCore 接入（默认关闭 / 启用 / evaluate_reflection / 便捷方法）
10. 完整链路：Reflection → Evaluation → 不自动接受 Proposal

约束验证：
- 不修改 Persona / TraitState
- 不自动接受 GrowthProposal
- 所有评估行为可审计
- 不调用 LLM
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.reflection_evaluation_schema import (
    ALL_DIMENSIONS,
    DIMENSION_NOVELTY,
    DIMENSION_RELEVANCE,
    DIMENSION_CONSISTENCY,
    DIMENSION_STABILITY,
    DIMENSION_ALIGNMENT,
    RECOMMENDATION_ACCEPT,
    RECOMMENDATION_REJECT,
    RECOMMENDATION_DEFER,
    RECOMMENDATION_REVISIT,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_GROWTH_RECORD,
    SOURCE_IDENTITY_CHANGE,
    SOURCE_RELATIONSHIP,
    EvaluationDimension,
    EvaluationHistoryEntry,
    EvaluatorSnapshot,
    ReflectionEvaluation,
)
from src.runtime.reflection_evaluator import (
    ReflectionEvaluator,
    EvaluatorConfig,
)


# ============================================================
# 1. Schema 结构测试
# ============================================================

class TestEvaluationSchema(unittest.TestCase):
    def test_01_evaluation_dimension_defaults(self):
        d = EvaluationDimension()
        self.assertEqual(d.score, 0.0)
        self.assertEqual(d.weight, 1.0)

    def test_02_reflection_evaluation_defaults(self):
        ev = ReflectionEvaluation()
        self.assertTrue(ev.evaluation_id.startswith("rev_"))
        self.assertEqual(ev.recommendation, "")
        self.assertEqual(ev.dimensions, [])
        self.assertEqual(ev.risk_flags, [])

    def test_03_history_entry_defaults(self):
        h = EvaluationHistoryEntry()
        self.assertEqual(h.overall_score, 0.0)
        self.assertEqual(h.risk_flags_count, 0)

    def test_04_snapshot_defaults(self):
        s = EvaluatorSnapshot()
        self.assertTrue(s.snapshot_id.startswith("esnap_"))
        self.assertEqual(s.total_evaluations, 0)

    def test_05_evaluation_to_dict(self):
        ev = ReflectionEvaluation(
            source_type=SOURCE_REFLECTION_INSIGHT,
            source_id="ins_001",
            overall_score=0.75,
            recommendation=RECOMMENDATION_ACCEPT,
            risk_flags=["low_evidence"],
        )
        d = ev.to_dict()
        self.assertEqual(d["source_type"], SOURCE_REFLECTION_INSIGHT)
        self.assertEqual(d["overall_score"], 0.75)
        self.assertEqual(d["recommendation"], RECOMMENDATION_ACCEPT)
        self.assertIn("low_evidence", d["risk_flags"])

    def test_06_evaluation_summary(self):
        ev = ReflectionEvaluation(
            source_type=SOURCE_GROWTH_RECORD,
            source_id="rec_001",
            overall_score=0.5,
            recommendation=RECOMMENDATION_DEFER,
            risk_flags=["low_evidence", "instability"],
        )
        s = ev.summary()
        self.assertIn("DEFER", s)
        self.assertIn("risks=2", s)

    def test_07_all_dimensions_constant(self):
        self.assertEqual(len(ALL_DIMENSIONS), 5)
        self.assertIn(DIMENSION_NOVELTY, ALL_DIMENSIONS)
        self.assertIn(DIMENSION_ALIGNMENT, ALL_DIMENSIONS)


# ============================================================
# 2. ReflectionInsight 评估
# ============================================================

class TestEvaluateInsight(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def _make_insight(self, **kwargs) -> Dict[str, Any]:
        defaults = {
            "insight_id": "ins_test_001",
            "insight_type": "pattern",
            "summary": "测试洞察",
            "confidence": 0.7,
            "pattern_frequency": 3,
            "experience_ids": ["e1", "e2", "e3", "e4"],
            "suggested_adjustments": ["adjust_1"],
        }
        defaults.update(kwargs)
        return defaults

    def test_01_basic_evaluation(self):
        insight = self._make_insight()
        ev = self.evaluator.evaluate_insight(insight)
        self.assertEqual(ev.source_type, SOURCE_REFLECTION_INSIGHT)
        self.assertEqual(ev.source_id, "ins_test_001")
        self.assertEqual(len(ev.dimensions), 5)
        self.assertGreater(ev.overall_score, 0.0)
        self.assertIn(ev.recommendation, [
            RECOMMENDATION_ACCEPT,
            RECOMMENDATION_REJECT,
            RECOMMENDATION_DEFER,
            RECOMMENDATION_REVISIT,
        ])

    def test_02_high_confidence_accept(self):
        insight = self._make_insight(
            confidence=0.9,
            pattern_frequency=4,
            experience_ids=["e1", "e2", "e3", "e4", "e5"],
            suggested_adjustments=["a1", "a2"],
        )
        ev = self.evaluator.evaluate_insight(insight)
        self.assertGreaterEqual(ev.overall_score, 0.5)
        # 高置信 + 多经验 → 至少不是 reject
        self.assertNotEqual(ev.recommendation, RECOMMENDATION_REJECT)

    def test_03_low_confidence_reject_or_defer(self):
        insight = self._make_insight(
            confidence=0.1,
            pattern_frequency=0,
            experience_ids=[],
            suggested_adjustments=[],
        )
        ev = self.evaluator.evaluate_insight(insight)
        # 低置信 + 无证据 → reject 或 defer
        self.assertIn(ev.recommendation, [RECOMMENDATION_REJECT, RECOMMENDATION_DEFER])

    def test_04_problem_type_novelty(self):
        insight = self._make_insight(insight_type="problem")
        ev = self.evaluator.evaluate_insight(insight)
        novelty_dim = next(d for d in ev.dimensions if d.name == DIMENSION_NOVELTY)
        self.assertGreaterEqual(novelty_dim.score, 0.7)

    def test_05_high_frequency_low_novelty(self):
        insight = self._make_insight(pattern_frequency=10)
        ev = self.evaluator.evaluate_insight(insight)
        novelty_dim = next(d for d in ev.dimensions if d.name == DIMENSION_NOVELTY)
        self.assertLessEqual(novelty_dim.score, 0.3)


# ============================================================
# 3. GrowthRecord 评估
# ============================================================

class TestEvaluateGrowthRecord(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def _make_record(self, **kwargs) -> Dict[str, Any]:
        defaults = {
            "record_id": "rec_test_001",
            "growth_signal": "test_signal",
            "source_type": "preference",
            "growth_level": "preference",
            "affected_dimensions": {"warmth": 0.02},
            "confidence": 0.6,
            "reason": "test",
        }
        defaults.update(kwargs)
        return defaults

    def test_01_basic_evaluation(self):
        rec = self._make_record()
        ev = self.evaluator.evaluate_growth_record(rec)
        self.assertEqual(ev.source_type, SOURCE_GROWTH_RECORD)
        self.assertEqual(ev.source_id, "rec_test_001")
        self.assertEqual(len(ev.dimensions), 5)

    def test_02_milestone_high_novelty(self):
        rec = self._make_record(source_type="milestone")
        ev = self.evaluator.evaluate_growth_record(rec)
        novelty_dim = next(d for d in ev.dimensions if d.name == DIMENSION_NOVELTY)
        self.assertGreaterEqual(novelty_dim.score, 0.8)

    def test_03_trait_level_lower_alignment(self):
        rec = self._make_record(growth_level="trait")
        ev = self.evaluator.evaluate_growth_record(rec)
        alignment_dim = next(d for d in ev.dimensions if d.name == DIMENSION_ALIGNMENT)
        self.assertLessEqual(alignment_dim.score, 0.5)

    def test_04_large_delta_magnitude_risk(self):
        rec = self._make_record(
            affected_dimensions={"warmth": 0.3},
            growth_level="trait",
        )
        ev = self.evaluator.evaluate_growth_record(rec)
        # 0.3 > 0.15 → 应触发 magnitude_risk
        self.assertIn("magnitude_risk", ev.risk_flags)


# ============================================================
# 4. IdentityChange + ContinuityReport 评估
# ============================================================

class TestEvaluateIdentityChange(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def _make_change(self, **kwargs) -> Dict[str, Any]:
        defaults = {
            "changes": [
                {"trait": "warmth", "old_value": 0.6, "new_value": 0.62, "delta": 0.02},
            ],
            "core_value_changes": [],
            "added_traits": [],
            "removed_traits": [],
            "added_core_values": [],
            "removed_core_values": [],
        }
        defaults.update(kwargs)
        return defaults

    def _make_report(self, **kwargs) -> Dict[str, Any]:
        defaults = {
            "continuity_score": 0.9,
            "is_continuous": True,
            "conflicts": [],
        }
        defaults.update(kwargs)
        return defaults

    def test_01_basic_evaluation(self):
        change = self._make_change()
        report = self._make_report()
        ev = self.evaluator.evaluate_identity_change(change, report)
        self.assertEqual(ev.source_type, SOURCE_IDENTITY_CHANGE)
        self.assertEqual(len(ev.dimensions), 5)

    def test_02_broken_continuity_revisit(self):
        change = self._make_change()
        report = self._make_report(
            continuity_score=0.3,
            is_continuous=False,
        )
        ev = self.evaluator.evaluate_identity_change(change, report)
        # 连续性断裂 → alignment 低 → revisit
        self.assertEqual(ev.recommendation, RECOMMENDATION_REVISIT)
        self.assertIn("identity_misalignment", ev.risk_flags)

    def test_03_conflicts_lower_alignment(self):
        change = self._make_change()
        report = self._make_report(
            continuity_score=0.7,
            is_continuous=True,
            conflicts=[{"conflict_type": "trait_reversal"}],
        )
        ev = self.evaluator.evaluate_identity_change(change, report)
        alignment_dim = next(d for d in ev.dimensions if d.name == DIMENSION_ALIGNMENT)
        self.assertLessEqual(alignment_dim.score, 0.4)

    def test_04_large_trait_delta_magnitude_risk(self):
        change = self._make_change(
            changes=[
                {"trait": "warmth", "old_value": 0.6, "new_value": 0.9, "delta": 0.3},
            ],
        )
        report = self._make_report()
        ev = self.evaluator.evaluate_identity_change(change, report)
        self.assertIn("magnitude_risk", ev.risk_flags)

    def test_05_removed_traits_alignment_risk(self):
        change = self._make_change(removed_traits=["curiosity"])
        report = self._make_report()
        ev = self.evaluator.evaluate_identity_change(change, report)
        alignment_dim = next(d for d in ev.dimensions if d.name == DIMENSION_ALIGNMENT)
        self.assertLessEqual(alignment_dim.score, 0.5)

    def test_06_without_report(self):
        change = self._make_change()
        ev = self.evaluator.evaluate_identity_change(change, report=None)
        # 无 report 时默认连续性 1.0
        self.assertEqual(ev.source_type, SOURCE_IDENTITY_CHANGE)


# ============================================================
# 5. RelationshipState 评估
# ============================================================

class TestEvaluateRelationship(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def _make_state(self, **kwargs) -> Dict[str, Any]:
        defaults = {
            "familiarity": 0.5,
            "trust": 0.6,
            "collaboration": 0.4,
            "interaction_frequency": 0.5,
            "relationship_stage": "developing",
        }
        defaults.update(kwargs)
        return defaults

    def test_01_basic_evaluation(self):
        state = self._make_state()
        ev = self.evaluator.evaluate_relationship(state)
        self.assertEqual(ev.source_type, SOURCE_RELATIONSHIP)
        self.assertEqual(len(ev.dimensions), 5)

    def test_02_high_trust_high_consistency(self):
        state = self._make_state(trust=0.9)
        ev = self.evaluator.evaluate_relationship(state)
        consistency_dim = next(d for d in ev.dimensions if d.name == DIMENSION_CONSISTENCY)
        self.assertGreaterEqual(consistency_dim.score, 0.9)

    def test_03_low_trust_low_consistency(self):
        state = self._make_state(trust=0.1)
        ev = self.evaluator.evaluate_relationship(state)
        consistency_dim = next(d for d in ev.dimensions if d.name == DIMENSION_CONSISTENCY)
        self.assertLessEqual(consistency_dim.score, 0.2)

    def test_04_initial_stage_high_novelty(self):
        state = self._make_state(relationship_stage="initial")
        ev = self.evaluator.evaluate_relationship(state)
        novelty_dim = next(d for d in ev.dimensions if d.name == DIMENSION_NOVELTY)
        self.assertGreaterEqual(novelty_dim.score, 0.7)


# ============================================================
# 6. 统一入口 + 推荐结论边界
# ============================================================

class TestUnifiedEvaluateAndRecommendation(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def test_01_unified_insight(self):
        insight = {
            "insight_id": "ins_u_001",
            "insight_type": "pattern",
            "confidence": 0.7,
            "pattern_frequency": 3,
            "experience_ids": ["e1", "e2"],
        }
        ev = self.evaluator.evaluate(SOURCE_REFLECTION_INSIGHT, insight)
        self.assertEqual(ev.source_type, SOURCE_REFLECTION_INSIGHT)

    def test_02_unified_growth_record(self):
        rec = {
            "record_id": "rec_u_001",
            "growth_signal": "test",
            "source_type": "preference",
            "growth_level": "preference",
            "affected_dimensions": {"warmth": 0.02},
            "confidence": 0.6,
        }
        ev = self.evaluator.evaluate(SOURCE_GROWTH_RECORD, rec)
        self.assertEqual(ev.source_type, SOURCE_GROWTH_RECORD)

    def test_03_unified_identity_change_with_report(self):
        change = {"changes": [], "core_value_changes": []}
        report = {"continuity_score": 0.9, "is_continuous": True, "conflicts": []}
        ev = self.evaluator.evaluate(
            SOURCE_IDENTITY_CHANGE, change, report=report
        )
        self.assertEqual(ev.source_type, SOURCE_IDENTITY_CHANGE)

    def test_04_unified_relationship(self):
        state = {
            "familiarity": 0.5,
            "trust": 0.6,
            "collaboration": 0.4,
            "interaction_frequency": 0.5,
            "relationship_stage": "stable",
        }
        ev = self.evaluator.evaluate(SOURCE_RELATIONSHIP, state)
        self.assertEqual(ev.source_type, SOURCE_RELATIONSHIP)

    def test_05_unknown_source_type(self):
        ev = self.evaluator.evaluate("unknown_type", {})
        self.assertEqual(ev.overall_score, 0.0)
        self.assertEqual(ev.recommendation, RECOMMENDATION_REJECT)

    def test_06_accept_threshold_boundary(self):
        # 构造高分 insight
        insight = {
            "insight_id": "ins_high",
            "insight_type": "improvement",
            "confidence": 0.95,
            "pattern_frequency": 4,
            "experience_ids": ["e1", "e2", "e3", "e4", "e5"],
            "suggested_adjustments": ["a1", "a2"],
        }
        ev = self.evaluator.evaluate_insight(insight)
        # 高分应推荐 accept
        self.assertEqual(ev.recommendation, RECOMMENDATION_ACCEPT)

    def test_07_reject_threshold_boundary(self):
        # 构造低分 insight
        insight = {
            "insight_id": "ins_low",
            "insight_type": "pattern",
            "confidence": 0.05,
            "pattern_frequency": 0,
            "experience_ids": [],
            "suggested_adjustments": [],
        }
        ev = self.evaluator.evaluate_insight(insight)
        self.assertEqual(ev.recommendation, RECOMMENDATION_REJECT)


# ============================================================
# 7. 风险标记
# ============================================================

class TestRiskFlags(unittest.TestCase):
    def setUp(self):
        self.evaluator = ReflectionEvaluator()

    def test_01_low_evidence_flag(self):
        insight = {
            "insight_id": "ins_le",
            "insight_type": "pattern",
            "confidence": 0.7,
            "pattern_frequency": 0,
            "experience_ids": [],
        }
        ev = self.evaluator.evaluate_insight(insight)
        self.assertIn("low_evidence", ev.risk_flags)

    def test_02_instability_flag(self):
        rec = {
            "record_id": "rec_inst",
            "growth_signal": "test",
            "source_type": "creation",
            "growth_level": "context",
            "affected_dimensions": {"warmth": 0.5},
            "confidence": 0.3,
        }
        ev = self.evaluator.evaluate_growth_record(rec)
        # 大幅变化 → stability 低
        self.assertIn("instability", ev.risk_flags)

    def test_03_contradiction_risk_flag(self):
        insight = {
            "insight_id": "ins_cr",
            "insight_type": "pattern",
            "confidence": 0.1,
            "pattern_frequency": 0,
            "experience_ids": [],
        }
        ev = self.evaluator.evaluate_insight(insight)
        self.assertIn("contradiction_risk", ev.risk_flags)

    def test_04_value_risk_flag(self):
        change = {
            "changes": [],
            "core_value_changes": [
                {"value_id": "cv1", "delta": 0.15},
            ],
            "added_traits": [],
            "removed_traits": [],
            "added_core_values": [],
            "removed_core_values": [],
        }
        report = {"continuity_score": 0.9, "is_continuous": True, "conflicts": []}
        ev = self.evaluator.evaluate_identity_change(change, report)
        # 0.15 > 0.10 → value_risk
        self.assertIn("value_risk", ev.risk_flags)


# ============================================================
# 8. 历史与快照（审计）
# ============================================================

class TestHistoryAndSnapshot(unittest.TestCase):
    def test_01_history_recorded(self):
        evaluator = ReflectionEvaluator()
        insight = {"insight_id": "i1", "insight_type": "pattern", "confidence": 0.7}
        evaluator.evaluate_insight(insight)
        evaluator.evaluate_insight(insight)
        history = evaluator.get_history(limit=10)
        self.assertEqual(len(history), 2)

    def test_02_latest_evaluation(self):
        evaluator = ReflectionEvaluator()
        insight = {"insight_id": "i_latest", "insight_type": "pattern", "confidence": 0.7}
        evaluator.evaluate_insight(insight)
        latest = evaluator.get_latest_evaluation()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.source_id, "i_latest")

    def test_03_snapshot_stats(self):
        evaluator = ReflectionEvaluator()
        for i in range(5):
            evaluator.evaluate_insight({
                "insight_id": f"i_{i}",
                "insight_type": "pattern",
                "confidence": 0.7,
                "pattern_frequency": 3,
                "experience_ids": ["e1", "e2", "e3"],
            })
        snap = evaluator.get_snapshot()
        self.assertEqual(snap.total_evaluations, 5)
        # 至少有一些 accept 或 defer
        total = snap.total_accept + snap.total_reject + snap.total_defer + snap.total_revisit
        self.assertEqual(total, 5)

    def test_04_clear_history(self):
        evaluator = ReflectionEvaluator()
        evaluator.evaluate_insight({"insight_id": "i1", "insight_type": "pattern"})
        n = evaluator.clear_history()
        self.assertEqual(n, 1)
        self.assertEqual(len(evaluator.get_history()), 0)

    def test_05_history_capped(self):
        cfg = EvaluatorConfig(max_history=5)
        evaluator = ReflectionEvaluator(config=cfg)
        for i in range(10):
            evaluator.evaluate_insight({"insight_id": f"i_{i}", "insight_type": "pattern"})
        self.assertLessEqual(len(evaluator._history), 5)

    def test_06_history_newest_first(self):
        evaluator = ReflectionEvaluator()
        evaluator.evaluate_insight({"insight_id": "first", "insight_type": "pattern"})
        evaluator.evaluate_insight({"insight_id": "second", "insight_type": "pattern"})
        history = evaluator.get_history(limit=10)
        # 最新在前
        self.assertEqual(history[0].source_id, "second")
        self.assertEqual(history[1].source_id, "first")


# ============================================================
# 9. RuntimeCore 接入测试
# ============================================================

class TestRuntimeEvaluatorIntegration(unittest.TestCase):
    def _make_config(self, tmp: str, enabled: bool = True) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "reflection_evaluator_enabled": enabled,
            "ev_accept_threshold": 0.6,
            "ev_reject_threshold": 0.3,
            "memory_store_path": os.path.join(tmp, "mem_ev.json"),
            "growth_proposals_path": os.path.join(tmp, "gp_ev.json"),
            "state_file": os.path.join(tmp, "rt_ev.json"),
            "tick_interval_seconds": 1,
            "reflection_min_experiences": 2,
        }

    def test_01_disabled_by_default(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.reflection_evaluator)
        self.assertIsNone(rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT))
        self.assertIsNone(rc.evaluate_last_insight())
        self.assertEqual(rc.get_evaluation_history(), [])
        self.assertIsNone(rc.get_evaluation_snapshot())

    def test_02_enabled_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=True))
            self.assertIsNotNone(rc.reflection_evaluator)

    def test_03_evaluate_insight(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            insight = {
                "insight_id": "ins_rt_001",
                "insight_type": "pattern",
                "summary": "测试",
                "confidence": 0.8,
                "pattern_frequency": 3,
                "experience_ids": ["e1", "e2", "e3"],
            }
            result = rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            self.assertIsNotNone(result)
            self.assertEqual(result["source_type"], SOURCE_REFLECTION_INSIGHT)
            self.assertEqual(result["source_id"], "ins_rt_001")

    def test_04_evaluate_growth_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rec = {
                "record_id": "rec_rt_001",
                "growth_signal": "test",
                "source_type": "preference",
                "growth_level": "preference",
                "affected_dimensions": {"warmth": 0.02},
                "confidence": 0.7,
            }
            result = rc.evaluate_reflection(SOURCE_GROWTH_RECORD, rec)
            self.assertIsNotNone(result)
            self.assertEqual(result["source_id"], "rec_rt_001")

    def test_05_evaluate_identity_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            change = {
                "changes": [{"trait": "warmth", "delta": 0.02}],
                "core_value_changes": [],
                "added_traits": [],
                "removed_traits": [],
                "added_core_values": [],
                "removed_core_values": [],
            }
            report = {"continuity_score": 0.9, "is_continuous": True, "conflicts": []}
            result = rc.evaluate_reflection(
                SOURCE_IDENTITY_CHANGE, change, report=report
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["source_type"], SOURCE_IDENTITY_CHANGE)

    def test_06_evaluate_relationship(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            state = {
                "familiarity": 0.5,
                "trust": 0.6,
                "collaboration": 0.4,
                "interaction_frequency": 0.5,
                "relationship_stage": "stable",
            }
            result = rc.evaluate_reflection(SOURCE_RELATIONSHIP, state)
            self.assertIsNotNone(result)
            self.assertEqual(result["source_type"], SOURCE_RELATIONSHIP)

    def test_07_evaluation_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            insight = {"insight_id": "i1", "insight_type": "pattern", "confidence": 0.7}
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            history = rc.get_evaluation_history(limit=10)
            self.assertEqual(len(history), 2)

    def test_08_evaluation_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            insight = {"insight_id": "i1", "insight_type": "pattern", "confidence": 0.7}
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            snap = rc.get_evaluation_snapshot()
            self.assertIsNotNone(snap)
            self.assertEqual(snap["total_evaluations"], 1)

    def test_09_clear_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            insight = {"insight_id": "i1", "insight_type": "pattern", "confidence": 0.7}
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            n = rc.clear_evaluation_history()
            self.assertEqual(n, 1)
            self.assertEqual(len(rc.get_evaluation_history()), 0)

    def test_10_latest_evaluation(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            insight = {"insight_id": "i_latest", "insight_type": "pattern", "confidence": 0.7}
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, insight)
            latest = rc.get_latest_evaluation()
            self.assertIsNotNone(latest)
            self.assertEqual(latest["source_id"], "i_latest")

    def test_11_unknown_source_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.evaluate_reflection("unknown_type", {})
            self.assertIsNone(result)


# ============================================================
# 10. 完整链路测试
#    Reflection → Evaluation → 不自动接受 Proposal
# ============================================================

class TestFullEvaluationLifecycle(unittest.TestCase):
    def test_01_reflect_then_evaluate(self):
        """
        完整链路：
        1. 注入经验 → 反思生成 Insight
        2. 评估 Insight → 获得 ReflectionEvaluation
        3. 验证不自动接受 GrowthProposal（仍为 proposed）
        4. 评估历史可审计
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_full.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_full.json"),
                "state_file": os.path.join(tmp, "rt_full.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
            }
            rc = RuntimeCore(config=config)

            # 1. 注入经验
            from src.contracts.experience_schema import RuntimeExperience, ActionResult
            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"e_ev_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hi{i}"}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(
                        action_id=f"act_ev_{i}",
                        success=True,
                        response_received=(i % 2 == 0),
                    ),
                    self_state_before={"initiative": 0.7},
                    self_state_after={"initiative": 0.68},
                    duration_ms=100.0,
                )
                rc.experience_builder._buffer.append(exp)

            # 2. 反思
            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)

            # 3. 评估
            result = rc.evaluate_last_insight()
            self.assertIsNotNone(result)
            self.assertEqual(result["source_type"], SOURCE_REFLECTION_INSIGHT)
            self.assertGreater(result["overall_score"], 0.0)
            self.assertIn(result["recommendation"], [
                RECOMMENDATION_ACCEPT,
                RECOMMENDATION_REJECT,
                RECOMMENDATION_DEFER,
                RECOMMENDATION_REVISIT,
            ])

            # 4. 验证不自动接受 GrowthProposal
            proposals = rc.get_growth_proposals(status="proposed", limit=20)
            accepted = rc.get_growth_proposals(status="accepted", limit=20)
            self.assertEqual(len(accepted), 0)

            # 5. 评估历史可审计
            # Phase 3.5.13 起，reflect_on_experiences 在 bridge 启用时会先自动评估一次，
            # evaluate_last_insight 再显式评估一次，因此这里至少有 2 条历史。
            history = rc.get_evaluation_history(limit=10)
            self.assertGreaterEqual(len(history), 2)
            self.assertEqual(history[0]["source_type"], SOURCE_REFLECTION_INSIGHT)

    def test_02_multi_source_evaluation_audit(self):
        """
        多来源评估的审计能力：
        - 评估 Insight
        - 评估 GrowthRecord
        - 评估 RelationshipState
        - 历史记录 3 条
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_multi.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_multi.json"),
                "state_file": os.path.join(tmp, "rt_multi.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
            }
            rc = RuntimeCore(config=config)

            # 1. 评估 Insight
            rc.evaluate_reflection(SOURCE_REFLECTION_INSIGHT, {
                "insight_id": "i_multi",
                "insight_type": "pattern",
                "confidence": 0.7,
                "pattern_frequency": 3,
                "experience_ids": ["e1", "e2"],
            })

            # 2. 评估 GrowthRecord
            rc.evaluate_reflection(SOURCE_GROWTH_RECORD, {
                "record_id": "r_multi",
                "growth_signal": "test",
                "source_type": "preference",
                "growth_level": "preference",
                "affected_dimensions": {"warmth": 0.02},
                "confidence": 0.7,
            })

            # 3. 评估 RelationshipState
            rc.evaluate_reflection(SOURCE_RELATIONSHIP, {
                "familiarity": 0.5,
                "trust": 0.6,
                "collaboration": 0.4,
                "interaction_frequency": 0.5,
                "relationship_stage": "stable",
            })

            # 历史记录 3 条
            history = rc.get_evaluation_history(limit=10)
            self.assertEqual(len(history), 3)
            # 最新在前
            self.assertEqual(history[0]["source_type"], SOURCE_RELATIONSHIP)
            self.assertEqual(history[1]["source_type"], SOURCE_GROWTH_RECORD)
            self.assertEqual(history[2]["source_type"], SOURCE_REFLECTION_INSIGHT)

            # 快照统计
            snap = rc.get_evaluation_snapshot()
            self.assertEqual(snap["total_evaluations"], 3)

    def test_03_evaluator_config_custom_thresholds(self):
        """
        自定义阈值验证：
        - 提高 accept_threshold → 更多 defer/reject
        - 降低 accept_threshold → 更多 accept
        """
        # 高阈值
        cfg_high = EvaluatorConfig(accept_threshold=0.95, reject_threshold=0.05)
        ev_high = ReflectionEvaluator(config=cfg_high)
        insight = {
            "insight_id": "i_cfg",
            "insight_type": "pattern",
            "confidence": 0.7,
            "pattern_frequency": 3,
            "experience_ids": ["e1", "e2", "e3"],
        }
        result_high = ev_high.evaluate_insight(insight)
        # 0.7 < 0.95 → 不应 accept
        self.assertNotEqual(result_high.recommendation, RECOMMENDATION_ACCEPT)

        # 低阈值
        cfg_low = EvaluatorConfig(accept_threshold=0.3, reject_threshold=0.05)
        ev_low = ReflectionEvaluator(config=cfg_low)
        result_low = ev_low.evaluate_insight(insight)
        # 0.7 > 0.3 → 应 accept
        self.assertEqual(result_low.recommendation, RECOMMENDATION_ACCEPT)


if __name__ == "__main__":
    unittest.main()
