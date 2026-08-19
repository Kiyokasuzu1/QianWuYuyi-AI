"""
Phase 3.5.10: Identity Anchor System 测试

覆盖：
 1. load_default_anchors() 默认锚点加载（来源于 identity.md）
 2. IdentityAnchorManager（快照 / 比较 / 完整性 / 变化建议应用）
 3. validate_anchor_integrity（权重偏移 / 约束违反 / 缺失检测 / SelfModel 一致性）
 4. AnchorChangeProposal 审批机制
 5. RuntimeCore 接入（默认关闭 / 启用 / get / refresh / integrity_report）
 6. 完整链路：Persona Origin → IdentityAnchor → SelfModel → IdentityContinuity

约束验证：
- 不修改 Persona 文档
- 不替代 Personality System
- 不自动限制成长（仅提供参考）
- 所有变化必须可审计
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.identity_anchor_schema import (
    AnchorSource,
    AnchorConstraint,
    IdentityAnchor,
    AnchorSnapshot,
    AnchorChangeProposal,
    AnchorWeightChange,
    AnchorDeviation,
    AnchorIntegrityReport,
)
from src.personality.identity_anchor import (
    load_default_anchors,
    IdentityAnchorManager,
)
from src.personality.self_model_manager import SelfModelManager
from src.personality.trait_state import create_trait_state
from src.personality.identity_continuity import IdentityContinuityChecker
from src.contracts.identity_schema import IdentitySnapshot


# ============================================================
# 1. load_default_anchors 测试
# ============================================================

class TestLoadDefaultAnchors(unittest.TestCase):
    def test_01_loads_five_core_anchors(self):
        anchors = load_default_anchors()
        self.assertEqual(len(anchors), 5)
        names = {a.name for a in anchors}
        self.assertIn("Authenticity", names)
        self.assertIn("Growth", names)
        self.assertIn("Independence", names)
        self.assertIn("MemoryConnection", names)
        self.assertIn("CreatorRelationship", names)

    def test_02_all_anchors_are_core(self):
        anchors = load_default_anchors()
        for a in anchors:
            self.assertTrue(a.is_core)

    def test_03_source_is_identity_doc(self):
        anchors = load_default_anchors()
        for a in anchors:
            self.assertEqual(a.source.source_type, "identity_doc")
            self.assertEqual(a.source.source_id, "docs/identity.md")

    def test_04_anchors_have_constraints(self):
        anchors = load_default_anchors()
        for a in anchors:
            self.assertGreaterEqual(len(a.constraints), 1)

    def test_05_anchors_have_related_traits(self):
        anchors = load_default_anchors()
        for a in anchors:
            self.assertGreaterEqual(len(a.related_traits), 1)
            self.assertGreaterEqual(len(a.related_core_values), 1)

    def test_06_weights_are_distinct(self):
        anchors = load_default_anchors()
        weights = [a.weight for a in anchors]
        self.assertEqual(len(weights), len(set(weights)))  # 各不相同
        # Authenticity 最高
        auth = next(a for a in anchors if a.name == "Authenticity")
        self.assertGreaterEqual(auth.weight, 0.9)

    def test_07_original_weight_equals_weight(self):
        anchors = load_default_anchors()
        for a in anchors:
            self.assertEqual(a.weight, a.original_weight)


# ============================================================
# 2. IdentityAnchorManager 测试
# ============================================================

class TestIdentityAnchorManager(unittest.TestCase):
    def setUp(self):
        self.mgr = IdentityAnchorManager()

    def test_01_initializes_with_default_anchors(self):
        self.assertEqual(len(self.mgr.anchors), 5)
        self.assertEqual(len(self.mgr.get_core_anchors()), 5)

    def test_02_get_anchor_by_id(self):
        a = self.mgr.get_anchor("anchor_authenticity")
        self.assertIsNotNone(a)
        self.assertEqual(a.name, "Authenticity")
        self.assertEqual(a.display_name, "真实性")

    def test_03_get_anchor_not_found(self):
        self.assertIsNone(self.mgr.get_anchor("nonexistent"))

    def test_04_create_snapshot(self):
        snap = self.mgr.create_anchor_snapshot()
        self.assertEqual(snap.total_anchors, 5)
        self.assertEqual(snap.core_anchors_count, 5)
        self.assertGreater(snap.total_weight, 0)
        self.assertGreater(snap.avg_weight, 0)
        self.assertGreater(len(snap.anchors), 0)
        # 快照存入历史
        self.assertEqual(len(self.mgr._snapshots), 1)

    def test_05_snapshot_history_capped(self):
        for _ in range(120):
            self.mgr.create_anchor_snapshot()
        self.assertLessEqual(len(self.mgr._snapshots), 100)


# ============================================================
# 3. compare_anchor_changes 测试
# ============================================================

class TestCompareAnchorChanges(unittest.TestCase):
    def setUp(self):
        self.mgr = IdentityAnchorManager()

    def test_01_no_changes_between_identical(self):
        snap1 = self.mgr.create_anchor_snapshot()
        snap2 = self.mgr.create_anchor_snapshot()
        proposal = self.mgr.compare_anchor_changes(snap1, snap2)
        self.assertEqual(len(proposal.weight_changes), 0)

    def test_02_detects_weight_change(self):
        snap1 = self.mgr.create_anchor_snapshot()
        # 手动修改权重
        self.mgr.anchors["anchor_authenticity"].weight = 0.85
        snap2 = self.mgr.create_anchor_snapshot()
        proposal = self.mgr.compare_anchor_changes(snap1, snap2)
        self.assertEqual(len(proposal.weight_changes), 1)
        wc = proposal.weight_changes[0]
        self.assertEqual(wc.anchor_id, "anchor_authenticity")
        self.assertAlmostEqual(wc.delta, -0.10, places=4)

    def test_03_integrity_impact_negative_on_large_drift(self):
        snap1 = self.mgr.create_anchor_snapshot()
        self.mgr.anchors["anchor_growth"].weight = 0.60  # -0.30
        snap2 = self.mgr.create_anchor_snapshot()
        proposal = self.mgr.compare_anchor_changes(snap1, snap2)
        self.assertEqual(proposal.integrity_impact, "negative")

    def test_04_detects_added_anchor(self):
        snap1 = self.mgr.create_anchor_snapshot()
        # 添加新锚点
        new_anchor = IdentityAnchor(
            anchor_id="anchor_custom",
            name="Custom",
            display_name="自定义",
            weight=0.5,
            is_core=False,
        )
        self.mgr.anchors["anchor_custom"] = new_anchor
        snap2 = self.mgr.create_anchor_snapshot()
        proposal = self.mgr.compare_anchor_changes(snap1, snap2)
        self.assertTrue(any("新增锚点" in n for n in proposal.notes))


# ============================================================
# 4. validate_anchor_integrity 测试
# ============================================================

class TestValidateAnchorIntegrity(unittest.TestCase):
    def setUp(self):
        self.mgr = IdentityAnchorManager()

    def test_01_intact_on_default(self):
        report = self.mgr.validate_anchor_integrity()
        self.assertTrue(report.is_intact)
        self.assertGreaterEqual(report.integrity_score, 0.9)
        self.assertEqual(len(report.deviations), 0)

    def test_02_weight_drift_warning(self):
        self.mgr.anchors["anchor_authenticity"].weight = 0.83  # drift = -0.12
        report = self.mgr.validate_anchor_integrity()
        # drift >= 0.10 → low severity deviation
        drifts = [d for d in report.deviations if d.deviation_type == "weight_drift"]
        self.assertGreaterEqual(len(drifts), 1)
        self.assertIn(drifts[0].severity, ["low", "medium"])

    def test_03_weight_drift_critical(self):
        self.mgr.anchors["anchor_growth"].weight = 0.65  # drift = -0.25
        report = self.mgr.validate_anchor_integrity()
        drifts = [d for d in report.deviations if d.deviation_type == "weight_drift"]
        self.assertGreaterEqual(len(drifts), 1)
        self.assertEqual(drifts[0].severity, "medium")  # 0.25 >= 0.20 → medium

    def test_04_weight_drift_max_exceeded(self):
        self.mgr.anchors["anchor_independence"].weight = 0.50  # drift = -0.38
        report = self.mgr.validate_anchor_integrity()
        drifts = [d for d in report.deviations if d.deviation_type == "weight_drift"]
        self.assertGreaterEqual(len(drifts), 1)
        self.assertEqual(drifts[0].severity, "high")  # >= 0.30 → high

    def test_05_missing_core_anchor(self):
        del self.mgr.anchors["anchor_memory"]
        report = self.mgr.validate_anchor_integrity()
        missing = [d for d in report.deviations if d.deviation_type == "missing_anchor"]
        self.assertGreaterEqual(len(missing), 1)
        self.assertEqual(missing[0].anchor_id, "anchor_memory")

    def test_06_self_model_alignment(self):
        # 构造一个 SelfModel snapshot，包含锚点关联的核心价值观和特质
        sm_snap = {
            "core_values_summary": [
                {"id": "honesty", "name": "真诚", "weight": 0.85},
                {"id": "growth", "name": "成长", "weight": 0.80},
                {"id": "autonomy", "name": "自主探索", "weight": 0.75},
                {"id": "empathy", "name": "共情", "weight": 0.78},
                {"id": "kindness", "name": "温柔", "weight": 0.70},
            ],
            "top_traits": [
                {"trait": "honesty", "value": 0.8},
                {"trait": "self_confidence", "value": 0.6},
                {"trait": "self_expression", "value": 0.5},
                {"trait": "curiosity", "value": 0.6},
                {"trait": "openness", "value": 0.5},
                {"trait": "initiative", "value": 0.55},
                {"trait": "autonomy", "value": 0.7},
                {"trait": "sensitivity", "value": 0.5},
                {"trait": "patience", "value": 0.45},
                {"trait": "empathy", "value": 0.65},
                {"trait": "kindness", "value": 0.6},
            ],
        }
        report = self.mgr.validate_anchor_integrity(
            self_model_snapshot=sm_snap
        )
        # 所有关联维度都在 → alignment 高
        self.assertGreaterEqual(report.self_model_alignment, 0.8)

    def test_07_self_model_misalignment(self):
        # SelfModel 缺少很多关联维度
        sm_snap = {
            "core_values_summary": [{"id": "honesty", "name": "真诚", "weight": 0.85}],
            "top_traits": [{"trait": "honesty", "value": 0.8}],
        }
        report = self.mgr.validate_anchor_integrity(
            self_model_snapshot=sm_snap
        )
        self.assertLess(report.self_model_alignment, 1.0)

    def test_08_integrity_report_summary(self):
        report = self.mgr.validate_anchor_integrity()
        summary = report.summary()
        self.assertIn("INTACT", summary)
        self.assertIn("score=", summary)


# ============================================================
# 5. AnchorChangeProposal 审批机制测试
# ============================================================

class TestAnchorChangeProposal(unittest.TestCase):
    def setUp(self):
        self.mgr = IdentityAnchorManager()

    def test_01_unapproved_proposal_not_applied(self):
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(
                    anchor_id="anchor_authenticity",
                    old_weight=0.95,
                    new_weight=0.90,
                    delta=-0.05,
                ),
            ],
        )
        result = self.mgr.apply_change_proposal(proposal)
        self.assertFalse(result)
        # 权重未变
        self.assertAlmostEqual(self.mgr.anchors["anchor_authenticity"].weight, 0.95)

    def test_02_approved_proposal_applied(self):
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(
                    anchor_id="anchor_growth",
                    old_weight=0.90,
                    new_weight=0.92,
                    delta=0.02,
                ),
            ],
            approved=True,
        )
        result = self.mgr.apply_change_proposal(proposal)
        self.assertTrue(result)
        self.assertAlmostEqual(self.mgr.anchors["anchor_growth"].weight, 0.92)
        # 版本增加
        self.assertGreater(self.mgr.anchors["anchor_growth"].version, 1)
        # proposal 标记为 applied
        self.assertTrue(proposal.applied)

    def test_03_double_apply_returns_false(self):
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(anchor_id="anchor_growth", delta=0.01),
            ],
            approved=True,
        )
        self.mgr.apply_change_proposal(proposal)
        result = self.mgr.apply_change_proposal(proposal)
        self.assertFalse(result)

    def test_04_weight_clamped_to_max_drift(self):
        # 尝试偏移超过 MAX_ALLOWED (0.30)
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(
                    anchor_id="anchor_authenticity",
                    old_weight=0.95,
                    new_weight=0.50,  # delta = -0.45，远超 0.30
                    delta=-0.45,
                ),
            ],
            approved=True,
        )
        self.mgr.apply_change_proposal(proposal)
        # 实际权重应被限制在 original_weight - 0.30 = 0.65
        self.assertAlmostEqual(self.mgr.anchors["anchor_authenticity"].weight, 0.65, places=2)

    def test_05_proposal_audited(self):
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(anchor_id="anchor_growth", delta=0.01),
            ],
            approved=True,
        )
        self.mgr.apply_change_proposal(proposal)
        proposals = self.mgr.get_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].proposal_id, proposal.proposal_id)


# ============================================================
# 6. RuntimeCore 接入测试
# ============================================================

class TestRuntimeIdentityAnchor(unittest.TestCase):
    def _make_config(self, tmp: str, enabled: bool = True) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "identity_anchor_enabled": enabled,
            "memory_store_path": os.path.join(tmp, "mem_anchor.json"),
            "growth_proposals_path": os.path.join(tmp, "gp_anchor.json"),
            "state_file": os.path.join(tmp, "rt_anchor.json"),
            "tick_interval_seconds": 1,
        }

    def test_01_disabled_by_default(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.identity_anchor_manager)
        self.assertIsNone(rc.get_identity_anchor())
        self.assertIsNone(rc.refresh_identity_anchor())

    def test_02_enabled_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=True))
            self.assertIsNotNone(rc.identity_anchor_manager)

    def test_03_get_identity_anchor_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.get_identity_anchor()
            self.assertIsNotNone(result)
            self.assertEqual(result["total"], 5)
            self.assertEqual(result["core_count"], 5)

    def test_04_get_identity_anchor_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.get_identity_anchor(anchor_id="anchor_authenticity")
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "Authenticity")

    def test_05_refresh_identity_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.refresh_identity_anchor()
            self.assertIsNotNone(result)
            self.assertEqual(result["total_anchors"], 5)
            self.assertIn("anchors", result)

    def test_06_get_anchor_integrity_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.get_anchor_integrity_report(use_self_model=False)
            self.assertIsNotNone(result)
            self.assertTrue(result["is_intact"])
            self.assertGreaterEqual(result["integrity_score"], 0.9)

    def test_07_list_anchor_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.refresh_identity_anchor()
            rc.refresh_identity_anchor()
            snaps = rc.list_anchor_snapshots(limit=10)
            self.assertEqual(len(snaps), 2)

    def test_08_get_anchor_manager_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            state = rc.get_anchor_manager_state()
            self.assertIsNotNone(state)
            self.assertIn("anchors", state)
            self.assertEqual(len(state["anchors"]), 5)


# ============================================================
# 7. 完整链路：Persona Origin → IdentityAnchor → SelfModel → IdentityContinuity
# ============================================================

class TestFullAnchorLifecycle(unittest.TestCase):
    def test_01_persona_to_anchor_to_selfmodel_to_continuity(self):
        """
        完整链路：
        1. Persona Origin (identity.md) → 加载 IdentityAnchor
        2. IdentityAnchor → 构建 SelfModel（关联锚点）
        3. SelfModel 变化 → IdentityContinuity 检测
        4. AnchorIntegrity 验证锚点是否保持稳定
        """
        # 1. Persona Origin → IdentityAnchor
        anchors = load_default_anchors()
        self.assertEqual(len(anchors), 5)
        anchor_mgr = IdentityAnchorManager(anchors)
        anchor_snap_1 = anchor_mgr.create_anchor_snapshot()
        self.assertEqual(anchor_snap_1.total_anchors, 5)

        # 2. IdentityAnchor → SelfModel（通过 trait_states 关联）
        sm_mgr = SelfModelManager("si_anchor_test")
        sm_mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.7),
            "honesty": create_trait_state("honesty", 0.8),
            "autonomy": create_trait_state("autonomy", 0.75),
            "curiosity": create_trait_state("curiosity", 0.6),
            "self_confidence": create_trait_state("self_confidence", 0.65),
            "self_expression": create_trait_state("self_expression", 0.55),
            "empathy": create_trait_state("empathy", 0.72),
            "kindness": create_trait_state("kindness", 0.68),
        })
        sm_snap_1 = sm_mgr.snapshot()
        self.assertGreaterEqual(sm_snap_1["stable_traits_count"], 5)

        # 3. Anchor Integrity 验证（SelfModel 与锚点一致）
        integrity_1 = anchor_mgr.validate_anchor_integrity(
            self_model_snapshot=sm_snap_1
        )
        self.assertTrue(integrity_1.is_intact)
        # SelfModel 中有 honesty / autonomy / empathy / kindness → alignment 较高
        self.assertGreater(integrity_1.self_model_alignment, 0.5)

        # 4. SelfModel 变化（成长 → 微调）
        sm_mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.72),
            "honesty": create_trait_state("honesty", 0.82),
            "autonomy": create_trait_state("autonomy", 0.76),
            "curiosity": create_trait_state("curiosity", 0.62),
            "self_confidence": create_trait_state("self_confidence", 0.67),
            "self_expression": create_trait_state("self_expression", 0.57),
            "empathy": create_trait_state("empathy", 0.74),
            "kindness": create_trait_state("kindness", 0.70),
        })
        sm_snap_2 = sm_mgr.snapshot()

        # 5. IdentityContinuity 检测
        checker = IdentityContinuityChecker()
        before_isnap = IdentitySnapshot.from_self_model_snapshot(sm_snap_1)
        after_isnap = IdentitySnapshot.from_self_model_snapshot(sm_snap_2)
        continuity_report = checker.generate_change_report(before_isnap, after_isnap)
        self.assertTrue(continuity_report.is_continuous)
        self.assertGreater(continuity_report.continuity_score, 0.8)

        # 6. Anchor Integrity 再次验证（锚点本身不变，只是 SelfModel 变了）
        anchor_snap_2 = anchor_mgr.create_anchor_snapshot()
        integrity_2 = anchor_mgr.validate_anchor_integrity(
            self_model_snapshot=sm_snap_2
        )
        # 锚点权重未变 → 仍然 intact
        self.assertTrue(integrity_2.is_intact)
        self.assertEqual(len(integrity_2.deviations), 0)

        # 7. 锚点权重变化 → 检测偏离
        anchor_mgr.anchors["anchor_authenticity"].weight = 0.80  # drift = -0.15
        integrity_3 = anchor_mgr.validate_anchor_integrity(self_model_snapshot=None)
        drifts = [d for d in integrity_3.deviations if d.deviation_type == "weight_drift"]
        self.assertGreaterEqual(len(drifts), 1)

        # 8. 审批并应用变化建议
        proposal = AnchorChangeProposal(
            weight_changes=[
                AnchorWeightChange(
                    anchor_id="anchor_growth",
                    delta=0.02,
                ),
            ],
            approved=True,
        )
        ok = anchor_mgr.apply_change_proposal(proposal)
        self.assertTrue(ok)
        self.assertAlmostEqual(
            anchor_mgr.anchors["anchor_growth"].weight,
            0.92,  # 0.90 + 0.02
            places=2,
        )

    def test_02_runtime_full_anchor_lifecycle(self):
        """
        Runtime 集成完整测试：
        IdentityAnchor + SelfModel + IdentityContinuity 三层联动
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "identity_anchor_enabled": True,
                "identity_continuity_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_full_anchor.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_full_anchor.json"),
                "state_file": os.path.join(tmp, "rt_full_anchor.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 1. 验证所有系统已初始化
            self.assertIsNotNone(rc.identity_anchor_manager)
            self.assertIsNotNone(rc.self_model_manager)
            self.assertIsNotNone(rc.identity_continuity_checker)

            # 2. 刷新 SelfModel
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.7),
                "honesty": create_trait_state("honesty", 0.8),
                "autonomy": create_trait_state("autonomy", 0.75),
            })

            # 3. 刷新 IdentityAnchor
            anchor_snap = rc.refresh_identity_anchor()
            self.assertIsNotNone(anchor_snap)
            self.assertEqual(anchor_snap["total_anchors"], 5)
            # SelfModel 版本应关联
            self.assertGreater(anchor_snap["self_model_version"], 0)

            # 4. 获取 Anchor Integrity Report（关联 SelfModel）
            integrity = rc.get_anchor_integrity_report(use_self_model=True)
            self.assertIsNotNone(integrity)
            self.assertTrue(integrity["is_intact"])

            # 5. 刷新 IdentityContinuity
            cont_result = rc.refresh_identity_continuity(force=True)
            self.assertIsNotNone(cont_result)

            # 6. 验证不修改 Persona / TraitState
            # （锚点权重未变，因为没有任何 proposal 被应用）
            anchor_state = rc.get_anchor_manager_state()
            for aid, a in anchor_state["anchors"].items():
                self.assertAlmostEqual(a["weight"], a["original_weight"])

            # 7. 锚点列表可获取
            all_anchors = rc.get_identity_anchor()
            self.assertEqual(all_anchors["total"], 5)

            # 8. 单个锚点详情
            auth = rc.get_identity_anchor(anchor_id="anchor_authenticity")
            self.assertEqual(auth["name"], "Authenticity")
            self.assertTrue(auth["is_core"])
            self.assertGreaterEqual(len(auth["constraints"]), 1)


if __name__ == "__main__":
    unittest.main()
