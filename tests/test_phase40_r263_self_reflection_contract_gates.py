"""
Phase 4.0 — R2.6.3 Gates: SelfReflection Contract Freeze

Gates (SR-1 ~ SR-15)：
  SR-1  顶层 9 字段 exact（注意：reflection_id/self_model_version/observed_changes/interpreted_causes/identity_alignment/unresolved_tensions/current_self_summary/generated_at/version = 9）
  SR-2  缺少任一冻结字段 → ValueError
  SR-3  多余顶层字段 → ValueError
  SR-4  self_model_version / version 必须非负整数
  SR-5  identity_alignment.overall_assessment 枚举只允许 3 个值
  SR-6  interpreted_causes[*].cause_tag 枚举只允许 5 个值
  SR-7  unresolved_tensions[*].status 必须等于 "unresolved"（R2.6.3 不自动解决张力）
  SR-8  unresolved_tensions[*].kind 枚举只允许 3 个值
  SR-9  current_self_summary 必须包含 origin/core/recent 三段
  SR-10 AST 扫描 self_reflection 模块 3 文件 FORBIDDEN_IMPORTS 为 0
  SR-11 AST 扫描 self_reflection 模块 3 文件 FORBIDDEN_CALLS 为 0
  SR-12 create_empty_self_reflection() → shape 合法 + identity_compatible
  SR-13 SelfReflectionSnapshot frozen：setattr → AttributeError
  SR-14 Snapshot property/to_dict 返回 deepcopy（外层修改不影响内部）
  SR-15 build_snapshot：非法输入 → ValueError；合法输入 → reflection_id unique per build
"""
from __future__ import annotations

import ast
import copy
import inspect
import unittest

from src.self_reflection import (
    FROZEN_TOP_LEVEL_KEYS,
    OBSERVED_CHANGE_FIELDS,
    CAUSE_FIELDS,
    IDENTITY_ALIGNMENT_FIELDS,
    UNRESOLVED_TENSION_FIELDS,
    CURRENT_SELF_SUMMARY_FIELDS,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_reflection_shape,
    create_empty_self_reflection,
    build_snapshot,
    SelfReflectionSnapshot,
)


# ============================================================
# 辅助：构造一个合法的 populated 快照 dict（用于测试子结构）
# ============================================================
def _mk_populated_reflection_dict() -> dict:
    return {
        "reflection_id": "ref_test_001",
        "self_model_version": 3,
        "observed_changes": [
            {
                "change_id": "evo_01",
                "trait": "creativity",
                "before": 0.6,
                "after": 0.72,
                "delta": 0.12,
                "change_type": "trait_delta",
                "timestamp": "2026-08-01T00:00:00+00:00",
            },
        ],
        "interpreted_causes": [
            {
                "for_change_id": "evo_01",
                "proposal_id": "prop_01",
                "approval_id": "approval_01",
                "proposal_reasons": ["multiple_observations", "high_confidence"],
                "approval_reasons": ["identity_consistent", "enough_evidence"],
                "evidence_summary": "经 5 次经历验证，平均置信度 0.85",
                "cause_tag": "evidence_driven",
            },
        ],
        "identity_alignment": {
            "core_value_check": {"preserved": True, "missing_values": [], "details": "全匹配"},
            "continuity_score": 0.94,
            "overall_assessment": "identity_compatible",
            "assessment_details": "创造倾向增强，符合核心成长价值观",
        },
        "unresolved_tensions": [
            {
                "tension_id": "tn_01",
                "kind": "trait_tension",
                "title": "独立自主但渴望被理解",
                "details": {"a": "independence", "b": "connection_value"},
                "status": "unresolved",
            },
        ],
        "current_self_summary": {
            "origin_bullet": "由清夏铃创造：始终对羽依与自己诚实。",
            "core_values_bullets": ["理解他人", "探索世界", "尊重创造者给予我的初心"],
            "recent_changes_bullets": ["创造倾向增强", "对视觉艺术兴趣提高"],
        },
        "generated_at": "2026-08-08T12:00:00+00:00",
        "version": 1,
    }


class TestPhase40R263SR1ToSR3TopLevel(unittest.TestCase):
    """SR-1 exact 9; SR-2 missing; SR-3 extra"""

    def test_sr1_exact_9_top_level_keys(self):
        self.assertEqual(len(FROZEN_TOP_LEVEL_KEYS), 9)
        self.assertEqual(set(FROZEN_TOP_LEVEL_KEYS), {
            "reflection_id",
            "self_model_version",
            "observed_changes",
            "interpreted_causes",
            "identity_alignment",
            "unresolved_tensions",
            "current_self_summary",
            "generated_at",
            "version",
        })

    def test_sr2_missing_key_raises(self):
        d = create_empty_self_reflection()
        del d["unresolved_tensions"]
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("unresolved_tensions", str(ctx.exception))

    def test_sr3_extra_top_level_raises(self):
        d = create_empty_self_reflection()
        d["prompt_fragment"] = "bad injection"  # R2.6.3 不允许混入 LLM 片段（契约冻结）
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("prompt_fragment", str(ctx.exception))


class TestPhase40R263SR4ToSR8SubShapeEnums(unittest.TestCase):
    """SR-4 version; SR-5 identity_alignment enum; SR-6 cause_tag; SR-7 status=unresolved; SR-8 kind"""

    def test_sr4_version_must_be_non_neg_int(self):
        d = create_empty_self_reflection()
        d["self_model_version"] = -1
        with self.assertRaises(ValueError):
            validate_self_reflection_shape(d)
        d = create_empty_self_reflection()
        d["version"] = "bad"  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            validate_self_reflection_shape(d)
        d = create_empty_self_reflection()
        d["reflection_id"] = ""
        with self.assertRaises(ValueError):
            validate_self_reflection_shape(d)

    def test_sr5_identity_alignment_overall_assessment_enum(self):
        d = _mk_populated_reflection_dict()
        for valid in ("identity_compatible", "identity_tension_detected", "identity_break_risk"):
            d["identity_alignment"]["overall_assessment"] = valid
            validate_self_reflection_shape(d)  # ok
        d["identity_alignment"]["overall_assessment"] = "fully_overwritten"
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("overall_assessment", str(ctx.exception))

    def test_sr6_cause_tag_enum(self):
        d = _mk_populated_reflection_dict()
        for valid in ("evidence_driven", "identity_consistent", "transition_protected", "under_review", "unknown"):
            d["interpreted_causes"][0]["cause_tag"] = valid
            validate_self_reflection_shape(d)
        d["interpreted_causes"][0]["cause_tag"] = "random_tag"
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("cause_tag", str(ctx.exception))

    def test_sr7_tension_status_must_be_unresolved(self):
        d = _mk_populated_reflection_dict()
        d["unresolved_tensions"][0]["status"] = "resolved"
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("unresolved", str(ctx.exception))

    def test_sr8_tension_kind_enum(self):
        d = _mk_populated_reflection_dict()
        for valid in ("trait_tension", "narrative_gap", "identity_mismatch"):
            d["unresolved_tensions"][0]["kind"] = valid
            validate_self_reflection_shape(d)
        d["unresolved_tensions"][0]["kind"] = "magic"
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("kind", str(ctx.exception))


class TestPhase40R263SR9SummaryShape(unittest.TestCase):
    """SR-9: current_self_summary origin/core/recent 3 段"""

    def test_sr9_three_sections_required(self):
        self.assertEqual(set(CURRENT_SELF_SUMMARY_FIELDS), {
            "origin_bullet", "core_values_bullets", "recent_changes_bullets",
        })
        d = create_empty_self_reflection()
        # missing origin
        del d["current_self_summary"]["origin_bullet"]
        with self.assertRaises(ValueError) as ctx:
            validate_self_reflection_shape(d)
        self.assertIn("origin_bullet", str(ctx.exception))

    def test_sr9_types(self):
        d = _mk_populated_reflection_dict()
        validate_self_reflection_shape(d)
        # Wrong types
        d["current_self_summary"]["origin_bullet"] = 123  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            validate_self_reflection_shape(d)
        d = _mk_populated_reflection_dict()
        d["current_self_summary"]["core_values_bullets"] = ""  # type: ignore[assignment]
        with self.assertRaises(ValueError):
            validate_self_reflection_shape(d)


class TestPhase40R263SR10SR11ForbiddenAST(unittest.TestCase):
    """SR-10/11: AST 扫描 3 文件无 FORBIDDEN_IMPORTS/CALLS"""

    @staticmethod
    def _clean_src(mod_name: str) -> str:
        mod = __import__(mod_name, fromlist=["_"])
        src = inspect.getsource(mod)
        tree = ast.parse(src)

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_sr10_no_forbidden_imports_all_3(self):
        for mod_name in (
            "src.self_reflection.self_reflection_schema",
            "src.self_reflection.self_reflection_snapshot",
            "src.self_reflection",
        ):
            s = self._clean_src(mod_name)
            for bad in FORBIDDEN_IMPORTS:
                # 注意：identity_continuity_dynamic_schema FORBIDDEN_IMPORTS 包含 "src.personality.personality_state" /
                # "src.personality.identity_anchor" — 这是"不 import 这些模块"(通过参数传实例)。
                # 但 SR-10 检查的是 self_reflection module，FORBIDDEN_IMPORTS 已经把这两个也列为禁止。
                self.assertNotIn(bad, s, f"{mod_name} 不应 import {bad}")

    def test_sr11_no_forbidden_calls_all_3(self):
        for mod_name in (
            "src.self_reflection.self_reflection_schema",
            "src.self_reflection.self_reflection_snapshot",
            "src.self_reflection",
        ):
            s = self._clean_src(mod_name)
            for bad in FORBIDDEN_CALLS:
                self.assertNotIn(f"{bad}(", s, f"{mod_name} 不应调用 {bad}()")


class TestPhase40R263SR12Empty(unittest.TestCase):
    """SR-12: create_empty_self_reflection 合法 + identity_compatible"""

    def test_sr12_empty_reflection_valid(self):
        d = create_empty_self_reflection()
        validate_self_reflection_shape(d)  # no raise
        self.assertEqual(d["identity_alignment"]["overall_assessment"], "identity_compatible")
        self.assertTrue(d["reflection_id"].startswith("ref_"))
        self.assertEqual(d["self_model_version"], 0)

    def test_sr12_empty_can_build_snapshot(self):
        d = create_empty_self_reflection()
        snap = build_snapshot(d)
        self.assertIsInstance(snap, SelfReflectionSnapshot)
        self.assertEqual(snap.self_model_version, 0)


class TestPhase40R263SR13SR14SnapshotImmutable(unittest.TestCase):
    """SR-13 frozen; SR-14 deepcopy"""

    def test_sr13_snapshot_frozen(self):
        d = _mk_populated_reflection_dict()
        snap = build_snapshot(d)
        with self.assertRaises(AttributeError):
            snap._version = 999  # type: ignore[misc]

    def test_sr14_deepcopy_property_to_dict(self):
        d = _mk_populated_reflection_dict()
        snap = build_snapshot(d)

        # 1. 改 property 返回的 list element → 内部不受影响
        obs = snap.observed_changes
        obs[0]["after"] = 0.999
        self.assertEqual(snap.observed_changes[0]["after"], 0.72)

        # 2. 改 to_dict → 内部不受影响
        dt = snap.to_dict()
        dt["identity_alignment"]["overall_assessment"] = "identity_break_risk"
        self.assertEqual(snap.identity_alignment["overall_assessment"], "identity_compatible")

        # 3. 改 unresolved_tensions → 内部不受影响
        tens = snap.unresolved_tensions
        tens[0]["kind"] = "magic"  # type: ignore[typeddict-item]
        self.assertEqual(snap.unresolved_tensions[0]["kind"], "trait_tension")


class TestPhase40R263SR15BuildAndUnique(unittest.TestCase):
    """SR-15 build_snapshot 校验严格 + unique id"""

    def test_sr15_build_rejects_invalid(self):
        d = create_empty_self_reflection()
        del d["identity_alignment"]
        with self.assertRaises(ValueError):
            build_snapshot(d)

    def test_sr15_build_populated_passes(self):
        d = _mk_populated_reflection_dict()
        snap = build_snapshot(d)
        self.assertEqual(snap.reflection_id, "ref_test_001")
        self.assertEqual(len(snap.observed_changes), 1)
        self.assertEqual(snap.identity_alignment["overall_assessment"], "identity_compatible")
        self.assertEqual(len(snap.unresolved_tensions), 1)
        self.assertEqual(snap.version, 1)
        self.assertGreater(len(snap.current_self_summary["core_values_bullets"]), 0)

    def test_sr15_unique_id_per_build(self):
        snap1 = build_snapshot(create_empty_self_reflection())
        snap2 = build_snapshot(create_empty_self_reflection())
        self.assertNotEqual(snap1.reflection_id, snap2.reflection_id)


class TestPhase40R263SubShapeFieldCounts(unittest.TestCase):
    """验证子结构 required fields 数量符合冻结"""

    def test_observed_change_has_7_fields(self):
        self.assertEqual(set(OBSERVED_CHANGE_FIELDS), {
            "change_id", "trait", "before", "after", "delta", "change_type", "timestamp",
        })

    def test_cause_has_7_fields(self):
        self.assertEqual(set(CAUSE_FIELDS), {
            "for_change_id", "proposal_id", "approval_id",
            "proposal_reasons", "approval_reasons", "evidence_summary", "cause_tag",
        })

    def test_identity_alignment_has_4_fields(self):
        self.assertEqual(set(IDENTITY_ALIGNMENT_FIELDS), {
            "core_value_check", "continuity_score", "overall_assessment", "assessment_details",
        })

    def test_unresolved_tension_has_5_fields(self):
        self.assertEqual(set(UNRESOLVED_TENSION_FIELDS), {
            "tension_id", "kind", "title", "details", "status",
        })


if __name__ == "__main__":
    unittest.main()
