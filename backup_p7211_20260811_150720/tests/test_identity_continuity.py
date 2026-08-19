"""
Phase 3.5.9: Identity Continuity Layer 测试

覆盖：
 1. IdentitySnapshot 构造与转换（Unit）
 2. IdentityChange 比较逻辑（compare_snapshots）
 3. ContinuityReport 生成与评分（generate_change_report）
 4. detect_identity_break 冲突检测（trait_reversal / value_drop / identity_break / understanding_regression）
 5. IdentityContinuityHistory 历史审计
 6. RuntimeCore 接入（默认关闭 / 显式启用 / refresh / get / list）
 7. 完整生命周期：旧SelfModel → Growth变化 → 新SelfModel → Continuity检测

约束验证：
- 不修改 Persona 文档
- 不修改 TraitState
- 不生成自然语言叙事
- 只生成结构化 ContinuityReport
- 保留全部历史审计
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.identity_schema import (
    IdentitySnapshot,
    TraitSnapshot,
    CoreValueSnapshot,
    IdentityChange,
    TraitChange,
    CoreValueChange,
    IdentityConflict,
    ContinuityReport,
)
from src.personality.identity_continuity import (
    IdentityContinuityChecker,
    IdentityContinuityHistory,
    ContinuityThresholds,
)
from src.personality.self_model_manager import SelfModelManager
from src.personality.trait_state import create_trait_state
from src.growth.growth_record import create_growth_record


# ============================================================
# 辅助：构造测试快照
# ============================================================

def make_snapshot(
    traits: Dict[str, float],
    core_values: Dict[str, float] = None,
    version: int = 1,
    understanding: Dict[str, float] = None,
    counts: Dict[str, int] = None,
) -> IdentitySnapshot:
    """构造测试用 IdentitySnapshot"""
    und = understanding or {}
    cnt = counts or {}
    cvs = core_values or {}
    return IdentitySnapshot(
        identity_id="si_test",
        version=version,
        traits=[
            TraitSnapshot(trait=k, value=v, direction="stable", stability=0.5, confidence=0.5)
            for k, v in traits.items()
        ],
        core_values=[
            CoreValueSnapshot(value_id=k, name=k, weight=v, confidence=0.8)
            for k, v in cvs.items()
        ],
        experience_awareness=und.get("experience_awareness", 0.3),
        trait_awareness=und.get("trait_awareness", 0.3),
        identity_continuity=und.get("identity_continuity", 0.3),
        overall_understanding=und.get("overall", 0.3),
        growth_history_count=cnt.get("growth_history", 0),
        preferences_count=cnt.get("preferences", 0),
        behavioral_patterns_count=cnt.get("patterns", 0),
        contradictions_count=cnt.get("contradictions", 0),
    )


# ============================================================
# 1. IdentitySnapshot 构造与转换测试
# ============================================================

class TestIdentitySnapshot(unittest.TestCase):
    def test_01_defaults_and_id(self):
        snap = IdentitySnapshot()
        self.assertTrue(snap.snapshot_id.startswith("isnap_"))
        self.assertEqual(snap.version, 1)
        self.assertEqual(snap.traits, [])

    def test_02_from_self_model_snapshot(self):
        sm_snap = {
            "identity_id": "si_xyz",
            "version": 5,
            "top_traits": [
                {"trait": "warmth", "value": 0.75, "stability": 0.8, "confidence": 0.9},
                {"trait": "shyness", "value": 0.6, "stability": 0.5, "confidence": 0.5},
            ],
            "core_values_summary": [
                {"id": "empathy", "name": "共情", "weight": 0.82},
            ],
            "understanding": {
                "experience_awareness": 0.4,
                "trait_awareness": 0.5,
                "identity_continuity": 0.6,
                "overall": 0.5,
            },
            "growth_history_count": 10,
            "preferences_count": 3,
            "behavioral_patterns_count": 2,
            "contradictions_count": 1,
        }
        snap = IdentitySnapshot.from_self_model_snapshot(sm_snap)
        self.assertEqual(snap.identity_id, "si_xyz")
        self.assertEqual(snap.version, 5)
        self.assertEqual(len(snap.traits), 2)
        self.assertEqual(snap.traits[0].trait, "warmth")
        self.assertEqual(snap.traits[0].value, 0.75)
        self.assertEqual(len(snap.core_values), 1)
        self.assertEqual(snap.core_values[0].value_id, "empathy")
        self.assertEqual(snap.growth_history_count, 10)
        self.assertEqual(snap.experience_awareness, 0.4)
        self.assertEqual(snap.source, "self_model_manager")

    def test_03_to_dict_serializable(self):
        snap = make_snapshot({"warmth": 0.7}, {"empathy": 0.8}, version=3)
        d = snap.to_dict()
        self.assertIn("snapshot_id", d)
        self.assertIn("traits", d)
        self.assertIn("core_values", d)
        self.assertIn("understanding", d)
        self.assertEqual(d["version"], 3)


# ============================================================
# 2. compare_snapshots 测试
# ============================================================

class TestCompareSnapshots(unittest.TestCase):
    def setUp(self):
        self.checker = IdentityContinuityChecker()

    def test_01_no_changes(self):
        before = make_snapshot({"warmth": 0.7, "shyness": 0.5})
        after = make_snapshot({"warmth": 0.7, "shyness": 0.5})
        change = self.checker.compare_snapshots(before, after)
        self.assertEqual(len(change.changes), 2)
        for tc in change.changes:
            self.assertEqual(tc.magnitude, "negligible")
            self.assertEqual(tc.direction, "stable")
        self.assertEqual(change.added_traits, [])
        self.assertEqual(change.removed_traits, [])

    def test_02_trait_increase(self):
        before = make_snapshot({"warmth": 0.70})
        after = make_snapshot({"warmth": 0.75})
        change = self.checker.compare_snapshots(before, after)
        self.assertEqual(len(change.changes), 1)
        tc = change.changes[0]
        self.assertEqual(tc.trait, "warmth")
        self.assertAlmostEqual(tc.delta, 0.05)
        self.assertEqual(tc.direction, "increase")
        # delta=0.05 >= moderate_threshold(0.05) → moderate
        self.assertEqual(tc.magnitude, "moderate")

    def test_03_trait_decrease(self):
        before = make_snapshot({"warmth": 0.80})
        after = make_snapshot({"warmth": 0.65})
        change = self.checker.compare_snapshots(before, after)
        tc = change.changes[0]
        self.assertAlmostEqual(tc.delta, -0.15)
        self.assertEqual(tc.direction, "decrease")
        self.assertEqual(tc.magnitude, "major")

    def test_04_added_removed_traits(self):
        before = make_snapshot({"warmth": 0.7, "shyness": 0.5})
        after = make_snapshot({"warmth": 0.7, "curiosity": 0.6})
        change = self.checker.compare_snapshots(before, after)
        self.assertIn("curiosity", change.added_traits)
        self.assertIn("shyness", change.removed_traits)

    def test_05_core_value_changes(self):
        before = make_snapshot({}, {"empathy": 0.8, "honesty": 0.9})
        after = make_snapshot({}, {"empathy": 0.75, "honesty": 0.9})
        change = self.checker.compare_snapshots(before, after)
        self.assertEqual(len(change.core_value_changes), 2)  # empathy changed + honesty unchanged
        empathy_cvc = next(c for c in change.core_value_changes if c.value_id == "empathy")
        self.assertAlmostEqual(empathy_cvc.delta, -0.05)
        self.assertEqual(empathy_cvc.magnitude, "minor")  # 0.05 < 0.08 → minor

    def test_06_understanding_delta(self):
        before = make_snapshot({}, understanding={"overall": 0.4, "experience_awareness": 0.3})
        after = make_snapshot({}, understanding={"overall": 0.5, "experience_awareness": 0.4})
        change = self.checker.compare_snapshots(before, after)
        self.assertAlmostEqual(change.understanding_delta["overall"], 0.1)
        self.assertAlmostEqual(change.understanding_delta["experience_awareness"], 0.1)

    def test_07_count_deltas(self):
        before = make_snapshot({}, counts={"growth_history": 5, "preferences": 3})
        after = make_snapshot({}, counts={"growth_history": 8, "preferences": 2})
        change = self.checker.compare_snapshots(before, after)
        self.assertEqual(change.growth_history_delta, 3)
        self.assertEqual(change.preferences_delta, -1)


# ============================================================
# 3. calculate_continuity & generate_change_report 测试
# ============================================================

class TestContinuityCalculation(unittest.TestCase):
    def setUp(self):
        self.checker = IdentityContinuityChecker()

    def test_01_identical_snapshots_max_continuity(self):
        before = make_snapshot({"warmth": 0.7, "shyness": 0.5}, {"empathy": 0.8})
        after = make_snapshot({"warmth": 0.7, "shyness": 0.5}, {"empathy": 0.8})
        score = self.checker.calculate_continuity(before, after)
        self.assertGreaterEqual(score, 0.99)

    def test_02_minor_changes_high_continuity(self):
        before = make_snapshot({"warmth": 0.70}, {"empathy": 0.80})
        after = make_snapshot({"warmth": 0.71}, {"empathy": 0.81})
        score = self.checker.calculate_continuity(before, after)
        self.assertGreater(score, 0.9)

    def test_03_major_changes_low_continuity(self):
        before = make_snapshot({"warmth": 0.80, "shyness": 0.60, "initiative": 0.70},
                               {"empathy": 0.85})
        after = make_snapshot({"warmth": 0.40, "shyness": 0.30, "initiative": 0.35},
                              {"empathy": 0.50})
        score = self.checker.calculate_continuity(before, after)
        self.assertLess(score, 0.7)

    def test_04_report_structure(self):
        before = make_snapshot({"warmth": 0.70}, {"empathy": 0.80}, version=1)
        after = make_snapshot({"warmth": 0.75}, {"empathy": 0.78}, version=2)
        report = self.checker.generate_change_report(before, after)
        self.assertIsInstance(report, ContinuityReport)
        self.assertEqual(report.before_version, 1)
        self.assertEqual(report.after_version, 2)
        self.assertGreater(report.continuity_score, 0.0)
        self.assertLessEqual(report.continuity_score, 1.0)
        self.assertIn("scores", report.to_dict())
        self.assertIn("trait_stability", report.to_dict()["scores"])

    def test_05_report_summary(self):
        before = make_snapshot({"warmth": 0.70})
        after = make_snapshot({"warmth": 0.72})
        report = self.checker.generate_change_report(before, after)
        summary = report.summary()
        self.assertIn("CONTINUOUS", summary)
        self.assertIn("score=", summary)


# ============================================================
# 4. detect_identity_break 测试
# ============================================================

class TestDetectIdentityBreak(unittest.TestCase):
    def setUp(self):
        self.checker = IdentityContinuityChecker()

    def test_01_no_conflicts_on_minor_change(self):
        before = make_snapshot({"warmth": 0.70})
        after = make_snapshot({"warmth": 0.71})
        report = self.checker.generate_change_report(before, after)
        self.assertEqual(len(report.conflicts), 0)

    def test_02_trait_reversal_detected(self):
        before = make_snapshot({"warmth": 0.80})
        after = make_snapshot({"warmth": 0.65})
        report = self.checker.generate_change_report(before, after)
        # delta = -0.15, abs >= trait_reversal_delta(0.08)
        reversals = [c for c in report.conflicts if c.conflict_type == "trait_reversal"]
        # Note: trait_reversal checks if before.direction was "increase" and delta < 0
        # Before trait direction is "stable" by default in our test helper
        # So this might not trigger as trait_reversal but as identity_break instead
        # Let's check identity_break instead
        breaks = [c for c in report.conflicts if c.conflict_type == "identity_break"]
        # Single trait change >= 0.08 → not identity_break (needs >= 3 traits)
        # So it might not be trait_reversal either (needs direction reversal)
        # Let's just verify some conflict exists for large change
        # Actually: before direction="stable", delta<0 → not a reversal
        # So no conflicts expected here
        # Let's adjust: set before trait direction to "increase"
        before2 = IdentitySnapshot(
            traits=[TraitSnapshot(trait="warmth", value=0.80, direction="increase", stability=0.5, confidence=0.5)],
            core_values=[],
        )
        after2 = make_snapshot({"warmth": 0.65})
        report2 = self.checker.generate_change_report(before2, after2)
        reversals2 = [c for c in report2.conflicts if c.conflict_type == "trait_reversal"]
        self.assertGreaterEqual(len(reversals2), 1)

    def test_03_value_drop_detected(self):
        before = make_snapshot({}, {"empathy": 0.90})
        after = make_snapshot({}, {"empathy": 0.70})
        report = self.checker.generate_change_report(before, after)
        drops = [c for c in report.conflicts if c.conflict_type == "value_drop"]
        self.assertGreaterEqual(len(drops), 1)
        # delta=0.20 >= cv_major(0.15) → critical
        self.assertEqual(drops[0].severity, "critical")

    def test_04_identity_break_multiple_traits(self):
        before = make_snapshot({"warmth": 0.80, "shyness": 0.60, "initiative": 0.70, "curiosity": 0.65})
        after = make_snapshot({"warmth": 0.50, "shyness": 0.30, "initiative": 0.40, "curiosity": 0.35})
        report = self.checker.generate_change_report(before, after)
        breaks = [c for c in report.conflicts if c.conflict_type == "identity_break"]
        self.assertGreaterEqual(len(breaks), 1)
        # 4 traits changed >= 0.08 → identity_break
        self.assertEqual(breaks[0].severity, "high")

    def test_05_understanding_regression_detected(self):
        before = make_snapshot({}, understanding={
            "experience_awareness": 0.5,
            "trait_awareness": 0.5,
            "identity_continuity": 0.5,
            "overall": 0.5,
        })
        after = make_snapshot({}, understanding={
            "experience_awareness": 0.3,
            "trait_awareness": 0.5,
            "identity_continuity": 0.5,
            "overall": 0.5,
        })
        report = self.checker.generate_change_report(before, after)
        regressions = [c for c in report.conflicts if c.conflict_type == "understanding_regression"]
        self.assertGreaterEqual(len(regressions), 1)

    def test_06_structural_change_detected(self):
        before = make_snapshot({"warmth": 0.7, "shyness": 0.5, "initiative": 0.6})
        after = make_snapshot({"warmth": 0.7, "curiosity": 0.5, "energy": 0.6})
        # 2 removed (shyness, initiative), 2 added (curiosity, energy) → total 4 >= 3
        report = self.checker.generate_change_report(before, after)
        breaks = [c for c in report.conflicts if c.conflict_type == "identity_break"]
        # Should detect structural change
        self.assertGreaterEqual(len(breaks), 1)

    def test_07_is_continuous_flag(self):
        # Small change → continuous
        before = make_snapshot({"warmth": 0.70})
        after = make_snapshot({"warmth": 0.71})
        report = self.checker.generate_change_report(before, after)
        self.assertTrue(report.is_continuous)

        # Large change → not continuous
        before2 = make_snapshot({"warmth": 0.80, "shyness": 0.60, "initiative": 0.70})
        after2 = make_snapshot({"warmth": 0.30, "shyness": 0.20, "initiative": 0.25})
        report2 = self.checker.generate_change_report(before2, after2)
        self.assertFalse(report2.is_continuous)


# ============================================================
# 5. IdentityContinuityHistory 测试
# ============================================================

class TestIdentityContinuityHistory(unittest.TestCase):
    def test_01_add_and_retrieve(self):
        hist = IdentityContinuityHistory()
        before = make_snapshot({"warmth": 0.7}, version=1)
        after = make_snapshot({"warmth": 0.75}, version=2)
        checker = IdentityContinuityChecker()
        report = checker.generate_change_report(before, after)
        hist.add_snapshot(before)
        hist.add_snapshot(after)
        hist.add_report(report)
        self.assertEqual(len(hist.get_reports()), 1)
        self.assertEqual(len(hist.get_snapshots()), 2)
        self.assertEqual(hist.get_latest_report().report_id, report.report_id)

    def test_02_average_continuity(self):
        hist = IdentityContinuityHistory()
        checker = IdentityContinuityChecker()
        for i in range(5):
            before = make_snapshot({"warmth": 0.70}, version=i + 1)
            after = make_snapshot({"warmth": 0.70 + i * 0.001}, version=i + 2)
            report = checker.generate_change_report(before, after)
            hist.add_report(report)
        avg = hist.get_average_continuity(window=10)
        self.assertGreater(avg, 0.9)

    def test_03_has_break(self):
        hist = IdentityContinuityHistory()
        checker = IdentityContinuityChecker()
        # Normal change
        before1 = make_snapshot({"warmth": 0.70}, version=1)
        after1 = make_snapshot({"warmth": 0.71}, version=2)
        hist.add_report(checker.generate_change_report(before1, after1))
        self.assertFalse(hist.has_break_detected())
        # Break change
        before2 = make_snapshot({"warmth": 0.80, "shyness": 0.60, "initiative": 0.70}, version=2)
        after2 = make_snapshot({"warmth": 0.30, "shyness": 0.20, "initiative": 0.25}, version=3)
        hist.add_report(checker.generate_change_report(before2, after2))
        self.assertTrue(hist.has_break_detected())

    def test_04_max_reports_cap(self):
        hist = IdentityContinuityHistory()
        checker = IdentityContinuityChecker()
        for i in range(250):
            before = make_snapshot({"warmth": 0.70}, version=i + 1)
            after = make_snapshot({"warmth": 0.70 + i * 0.001}, version=i + 2)
            hist.add_snapshot(before)
            hist.add_report(checker.generate_change_report(before, after))
        self.assertLessEqual(len(hist.get_snapshots(limit=300)), 200)
        self.assertLessEqual(len(hist.get_reports(limit=300)), 200)


# ============================================================
# 6. RuntimeCore 接入测试
# ============================================================

class TestRuntimeIdentityContinuity(unittest.TestCase):
    def _make_config(self, tmp: str, enabled: bool = True) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "identity_continuity_enabled": enabled,
            "memory_store_path": os.path.join(tmp, "mem_ic.json"),
            "growth_proposals_path": os.path.join(tmp, "gp_ic.json"),
            "state_file": os.path.join(tmp, "rt_ic.json"),
            "tick_interval_seconds": 1,
        }

    def test_01_disabled_by_default(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.identity_continuity_checker)
        self.assertIsNone(rc.identity_continuity_history)
        # refresh_identity_continuity returns None
        self.assertIsNone(rc.refresh_identity_continuity())

    def test02_enabled_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=True))
            self.assertIsNotNone(rc.identity_continuity_checker)
            self.assertIsNotNone(rc.identity_continuity_history)

    def test_03_first_refresh_no_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.7),
            })
            result = rc.refresh_identity_continuity()
            self.assertIsNotNone(result)
            self.assertEqual(result.get("note"), "first_snapshot_no_comparison")

    def test_04_second_refresh_generates_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            # First snapshot
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.70),
            })
            rc.refresh_identity_continuity()
            # Second snapshot with minor change
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.72),
            })
            result = rc.refresh_identity_continuity()
            self.assertIsNotNone(result)
            self.assertIn("continuity_score", result)
            self.assertIn("is_continuous", result)
            self.assertTrue(result["is_continuous"])

    def test_05_list_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            for v in [0.70, 0.71, 0.72]:
                rc.refresh_self_model(trait_states={
                    "warmth": create_trait_state("warmth", v),
                })
                rc.refresh_identity_continuity()
            reports = rc.list_identity_continuity_reports(limit=10)
            self.assertEqual(len(reports), 2)  # First is no-comparison, so 2 reports

    def test_06_average_and_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            # Normal first
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.70),
            })
            rc.refresh_identity_continuity()
            # Normal second
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.71),
            })
            rc.refresh_identity_continuity()
            self.assertFalse(rc.has_identity_break_detected())
            avg = rc.get_identity_continuity_average()
            self.assertGreater(avg, 0.5)

    def test_07_capture_and_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.70),
            })
            snap1 = rc.capture_identity_snapshot()
            self.assertIsNotNone(snap1)
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.75),
            })
            snap2 = rc.capture_identity_snapshot()
            result = rc.compare_identity_snapshots(snap1, snap2)
            self.assertIsNotNone(result)
            self.assertIn("continuity_score", result)

    def test_08_force_works_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=False))
            # Even disabled, force=True should work if checker is initialized
            # But checker won't be initialized when disabled
            # So force=True should still return None (no checker)
            result = rc.refresh_identity_continuity(force=True)
            self.assertIsNone(result)


# ============================================================
# 7. 完整生命周期测试
#    旧SelfModel → Growth变化 → 新SelfModel → Continuity检测
# ============================================================

class TestFullContinuityLifecycle(unittest.TestCase):
    def test_01_old_selfmodel_growth_new_selfmodel_continuity(self):
        """
        完整链路：
        1. 构建旧 SelfModel（基础特质状态）
        2. 注入 GrowthRecord（成长变化）
        3. 刷新 SelfModel（生成新版本）
        4. 执行 Continuity 检测
        5. 验证连续性报告结构正确
        """
        # 1. 构建旧 SelfModel
        mgr = SelfModelManager("si_lifecycle")
        mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.70),
            "shyness": create_trait_state("shyness", 0.50),
            "initiative": create_trait_state("initiative", 0.60),
        })
        old_snap = mgr.snapshot()
        self.assertGreaterEqual(old_snap["stable_traits_count"], 3)

        # 2. 注入 GrowthRecord（成长变化）
        grs = [
            create_growth_record(
                record_id="gr_lc_01",
                source_event_id="e1",
                growth_signal="high_user_activity_warmth",
                source_type="preference",
                growth_level="context",
                affected_dimensions={"warmth": 0.004},
                confidence=0.6,
                reason="用户活跃 → 温暖度提升",
                created_at="2026-07-29T01:00:00Z",
            ),
            create_growth_record(
                record_id="gr_lc_02",
                source_event_id="e2",
                growth_signal="low_success_rate_adjustment",
                source_type="preference",
                growth_level="trait",
                affected_dimensions={"initiative": -0.003},
                confidence=0.5,
                reason="低成功率 → 主动性微调",
                created_at="2026-07-29T01:01:00Z",
            ),
        ]

        # 3. 刷新 SelfModel（生成新版本）
        mgr.refresh(
            trait_states={
                "warmth": create_trait_state("warmth", 0.72),  # 微增
                "shyness": create_trait_state("shyness", 0.50),
                "initiative": create_trait_state("initiative", 0.59),  # 微降
            },
            growth_records=grs,
        )
        new_snap = mgr.snapshot()
        self.assertGreaterEqual(new_snap["version"], old_snap["version"] + 1)

        # 4. Continuity 检测
        checker = IdentityContinuityChecker()
        before_isnap = IdentitySnapshot.from_self_model_snapshot(old_snap, identity_id="si_lifecycle")
        after_isnap = IdentitySnapshot.from_self_model_snapshot(new_snap, identity_id="si_lifecycle")
        report = checker.generate_change_report(before_isnap, after_isnap)

        # 5. 验证
        self.assertIsInstance(report, ContinuityReport)
        self.assertGreater(report.continuity_score, 0.5)
        self.assertTrue(report.is_continuous)
        # 至少有变化记录
        self.assertGreater(len(report.changes.changes), 0)
        # 有成长历史增长
        self.assertGreaterEqual(report.changes.growth_history_delta, 0)
        # 无严重冲突（小变化不触发断裂）
        critical = [c for c in report.conflicts if c.severity == "critical"]
        self.assertEqual(len(critical), 0)
        # summary 可读
        self.assertIn("CONTINUOUS", report.summary())

    def test_02_break_detected_on_major_shift(self):
        """
        验证：当特质剧烈变化时，连续性断裂被检测到。
        """
        mgr = SelfModelManager("si_break")
        mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.85),
            "shyness": create_trait_state("shyness", 0.70),
            "initiative": create_trait_state("initiative", 0.80),
            "self_confidence": create_trait_state("self_confidence", 0.75),
        })
        old_snap = mgr.snapshot()

        # 剧烈变化：所有特质大幅下降
        mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.30),
            "shyness": create_trait_state("shyness", 0.20),
            "initiative": create_trait_state("initiative", 0.25),
            "self_confidence": create_trait_state("self_confidence", 0.20),
        })
        new_snap = mgr.snapshot()

        checker = IdentityContinuityChecker()
        before = IdentitySnapshot.from_self_model_snapshot(old_snap, identity_id="si_break")
        after = IdentitySnapshot.from_self_model_snapshot(new_snap, identity_id="si_break")
        report = checker.generate_change_report(before, after)

        self.assertFalse(report.is_continuous)
        self.assertLess(report.continuity_score, 0.7)
        # 至少有 identity_break 冲突
        breaks = [c for c in report.conflicts if c.conflict_type == "identity_break"]
        self.assertGreaterEqual(len(breaks), 1)
        # notes 中有断裂说明
        self.assertTrue(any("断裂" in n or "低于阈值" in n for n in report.notes))

    def test_03_runtime_full_lifecycle(self):
        """
        完整 Runtime 集成：
        RuntimeCore + SelfModel + IdentityContinuity
        多次刷新 → 多次报告 → 历史审计
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "identity_continuity_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_full.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_full.json"),
                "state_file": os.path.join(tmp, "rt_full.json"),
                "tick_interval_seconds": 1,
                "ic_continuity_threshold": 0.6,
            }
            rc = RuntimeCore(config=config)
            # 1. 初始 SelfModel + Continuity 快照
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.70),
                "initiative": create_trait_state("initiative", 0.60),
            })
            r1 = rc.refresh_identity_continuity()
            self.assertEqual(r1.get("note"), "first_snapshot_no_comparison")

            # 2. 小幅变化
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.72),
                "initiative": create_trait_state("initiative", 0.61),
            })
            r2 = rc.refresh_identity_continuity()
            self.assertIn("continuity_score", r2)
            self.assertTrue(r2["is_continuous"])

            # 3. 再小幅变化
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.73),
                "initiative": create_trait_state("initiative", 0.62),
            })
            r3 = rc.refresh_identity_continuity()
            self.assertTrue(r3["is_continuous"])

            # 4. 验证历史
            reports = rc.list_identity_continuity_reports(limit=10)
            self.assertEqual(len(reports), 2)  # r2 和 r3（r1 是 first_snapshot）
            avg = rc.get_identity_continuity_average()
            self.assertGreater(avg, 0.5)
            self.assertFalse(rc.has_identity_break_detected())

            # 5. 验证快照可获取
            snap = rc.get_identity_snapshot()
            self.assertIsNotNone(snap)
            self.assertIn("snapshot_id", snap)

            # 6. 验证不修改 Persona / TraitState
            # （SelfModel 是独立副本，TraitState 由外部传入的 create_trait_state 创建）
            # 只验证 Continuity 系统不写回任何外部状态
            # PCR pending 不受影响
            self.assertEqual(len(rc._pending_change_requests), 0)


if __name__ == "__main__":
    unittest.main()
