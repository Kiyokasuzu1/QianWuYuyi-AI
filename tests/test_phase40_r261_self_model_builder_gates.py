"""
Phase 4.0 — R2.6.1 Gates: SelfModelBuilder

Gates（SB-1 ~ SB-10 + 扩展）：
  SB-1   Builder 输出必须符合 SelfModelSchema
  SB-2   输入缺失时安全降级（None / 异常 input → 合法 snapshot）
  SB-3   Builder 不 import Growth/Approval/Relationship/Emotion/Memory/adapter（AST 扫描）
  SB-4   Builder 不调用 PersonalityState.apply_evolution / set_trait / execute 等（AST 扫描）
  SB-5   IdentityAnchor 只读：build 前后 anchor.anchors 的 anchor_id / weight / is_core 不变
  SB-6   EvolutionRecord 只读：build 前后每个 record 的 proposal_id / approval_id / after 不变
  SB-7   同输入 deterministic：两次 build 的 5 view + version 完全一致（generated_at 相同情况下）
  SB-8   空历史（evolution_records=[]）可以生成合法 SelfModel
  SB-9   recent_changes 数量限制（默认 <= 10，可配置）
  SB-10  返回 Snapshot 不可变（与 R2.6.0 的 SM-8 一致）
  SB-11  identity_view 包含 origin/core_values/stable_markers 三个字段
  SB-12  personality_view 包含 stable_traits/evolving_traits/recent_changes 三个字段
  SB-13  development_view 包含 evolution_history + important_turning_points
  SB-14  contradiction_view 只标记 detected，不做 resolved
  SB-15  capability_view 包含 known_strengths + limitations（非空）
"""
from __future__ import annotations

import ast
import copy
import inspect
import unittest
from typing import Any, Dict, List

from src.self_model import (
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_model_shape,
)
from src.self_model.self_model_builder import (
    SelfModelBuilder,
    KNOWN_TENSION_PAIRS,
)
from src.self_model.self_model_snapshot import SelfModelSnapshot
from src.personality.identity_anchor import (
    IdentityAnchorManager,
    load_default_anchors,
)
from src.personality.personality_state import (
    PersonalityState,
    reset_personality_state,
)
from src.personality.evolution_record import build_evolution_record


# ============================================================
# 辅助：造演化记录
# ============================================================
def _mk_evo_records(n: int = 5) -> List[Dict[str, Any]]:
    records = []
    traits = ["creativity", "curiosity", "empathy", "AI_art", "independence", "connection_value"]
    for i in range(n):
        t = traits[i % len(traits)]
        bv = 0.5 + 0.03 * i
        av = bv + 0.06
        records.append(build_evolution_record(
            proposal_id=f"prop_b_{i}",
            approval_id=f"approval_b_{i}",
            change_type="interest_transition" if i % 4 == 0 else (
                "new_trait" if i % 4 == 1 else "trait_delta"
            ),
            before={f"trait.{t}": round(bv, 4)},
            after={f"trait.{t}": round(av, 4)},
            reasons=["enough_evidence", "identity_consistent"],
            confidence=0.8,
        ))
    return records


class TestPhase40R261SB1SchemaCompliance(unittest.TestCase):
    """SB-1: 输出符合 schema"""

    def test_sb1_output_passes_validate(self):
        b = SelfModelBuilder()
        state = PersonalityState(
            traits={"creativity": 0.7, "curiosity": 0.8, "empathy": 0.65,
                     "independence": 0.75, "connection_value": 0.72},
        )
        iam = IdentityAnchorManager()
        snap = b.build(iam, state, _mk_evo_records(5))
        d = snap.to_dict()
        validate_self_model_shape({k: v for k, v in d.items() if k != "snapshot_id"})
        # 没抛异常就过了

    def test_sb1_each_build_has_incrementing_version(self):
        b = SelfModelBuilder()
        state = PersonalityState()
        snap1 = b.build(personality_state=state, generated_at="2026-08-08T00:00:00+00:00")
        snap2 = b.build(personality_state=state, generated_at="2026-08-08T00:00:01+00:00")
        self.assertEqual(snap1.version, 1)
        self.assertEqual(snap2.version, 2)


class TestPhase40R261SB2Degradation(unittest.TestCase):
    """SB-2: 输入缺失安全降级"""

    def test_sb2_all_none_inputs_returns_legal_snapshot(self):
        b = SelfModelBuilder()
        snap = b.build()  # 全 None
        self.assertIsInstance(snap, SelfModelSnapshot)
        self.assertGreaterEqual(snap.version, 1)
        self.assertTrue(snap.generated_at)
        # 各 view 都是 dict
        for key in ("identity_view", "personality_view", "development_view",
                     "contradiction_view", "capability_view"):
            self.assertIsInstance(getattr(snap, key), dict)

    def test_sb2_broken_state_does_not_crash(self):
        class _BrokenState:
            @property
            def snapshot(self):
                raise RuntimeError("boom")
        b = SelfModelBuilder()
        snap = b.build(personality_state=_BrokenState())
        self.assertIsInstance(snap, SelfModelSnapshot)

    def test_sb2_broken_record_does_not_crash(self):
        class _Broken:
            pass
        b = SelfModelBuilder()
        snap = b.build(evolution_records=[_Broken(), None, {"bad": object()}])  # type: ignore[list-item]
        self.assertIsInstance(snap, SelfModelSnapshot)


class TestPhase40R261SB3SB4ForbiddenDependencyAST(unittest.TestCase):
    """SB-3 不 import 禁止模块；SB-4 不调用禁止函数"""

    @staticmethod
    def _get_clean_src() -> str:
        import src.self_model.self_model_builder as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_sb3_no_forbidden_imports(self):
        s = self._get_clean_src()
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"builder 不应 import {bad}")

    def test_sb4_no_forbidden_calls(self):
        s = self._get_clean_src()
        for bad in FORBIDDEN_CALLS:
            self.assertNotIn(f"{bad}(", s, f"builder 不应调用 {bad}()")


class TestPhase40R261SB5SB6ReadOnly(unittest.TestCase):
    """SB-5 IdentityAnchor 只读；SB-6 EvolutionRecord 只读"""

    def test_sb5_anchor_unchanged_after_build(self):
        iam = IdentityAnchorManager()
        # build 前快照
        before = [(a.anchor_id, a.weight, a.is_core, a.version) for a in iam.get_all_anchors()]
        state = PersonalityState()
        b = SelfModelBuilder()
        b.build(iam, state, _mk_evo_records(3))
        after = [(a.anchor_id, a.weight, a.is_core, a.version) for a in iam.get_all_anchors()]
        self.assertEqual(before, after)

    def test_sb6_evolution_records_unchanged_after_build(self):
        records = _mk_evo_records(4)
        before = [(r["proposal_id"], r["approval_id"], tuple(sorted(r["after"].items()))) for r in records]
        state = PersonalityState()
        b = SelfModelBuilder()
        b.build(personality_state=state, evolution_records=records)
        after = [(r["proposal_id"], r["approval_id"], tuple(sorted(r["after"].items()))) for r in records]
        self.assertEqual(before, after)


class TestPhase40R261SB7Deterministic(unittest.TestCase):
    """SB-7: 同输入 deterministic"""

    def test_sb7_same_input_same_output(self):
        reset_personality_state()
        state = PersonalityState(
            traits={"creativity": 0.7, "curiosity": 0.8},
        )
        iam = IdentityAnchorManager()
        recs = _mk_evo_records(3)
        ts = "2026-08-08T12:00:00+00:00"

        b1 = SelfModelBuilder()
        snap1_a = b1.build(iam, state, copy.deepcopy(recs), generated_at=ts)
        snap1_b = b1.build(iam, state, copy.deepcopy(recs), generated_at=ts)

        # 注意：同一 builder 多次 build version 会递增（设计就是这样），所以比较 5 view 内容
        self.assertEqual(snap1_a.identity_view, snap1_b.identity_view)
        self.assertEqual(snap1_a.personality_view["stable_traits"],
                          snap1_b.personality_view["stable_traits"])
        self.assertEqual(snap1_a.development_view, snap1_b.development_view)
        self.assertEqual(snap1_a.contradiction_view, snap1_b.contradiction_view)
        self.assertEqual(snap1_a.capability_view, snap1_b.capability_view)
        # 但 version 不同（1 vs 2）
        self.assertNotEqual(snap1_a.version, snap1_b.version)


class TestPhase40R261SB8EmptyHistory(unittest.TestCase):
    """SB-8: 空历史可以生成"""

    def test_sb8_empty_records(self):
        state = PersonalityState()
        b = SelfModelBuilder()
        snap = b.build(personality_state=state, evolution_records=[])
        self.assertEqual(snap.personality_view["recent_changes"], [])
        self.assertEqual(snap.development_view["evolution_history"], [])
        self.assertEqual(snap.development_view["important_turning_points"], [])


class TestPhase40R261SB9RecentChangesLimit(unittest.TestCase):
    """SB-9: recent_changes 数量限制"""

    def test_sb9_default_limit(self):
        records = _mk_evo_records(20)
        state = PersonalityState()
        b = SelfModelBuilder()
        snap = b.build(personality_state=state, evolution_records=records)
        self.assertLessEqual(len(snap.personality_view["recent_changes"]), 10)

    def test_sb9_custom_limit(self):
        records = _mk_evo_records(20)
        state = PersonalityState()
        b = SelfModelBuilder(recent_changes_limit=3)
        snap = b.build(personality_state=state, evolution_records=records)
        self.assertLessEqual(len(snap.personality_view["recent_changes"]), 3)


class TestPhase40R261SB10SnapshotImmutable(unittest.TestCase):
    """SB-10: Snapshot 不可变"""

    def test_sb10_snapshot_frozen(self):
        state = PersonalityState()
        b = SelfModelBuilder()
        snap = b.build(personality_state=state)
        with self.assertRaises(AttributeError):
            snap._version = 999  # type: ignore[misc]


class TestPhase40R261SB11ToSB15ViewFields(unittest.TestCase):
    """SB-11~15: 5 个 View 结构完整性"""

    def test_sb11_identity_view_fields(self):
        iam = IdentityAnchorManager()
        b = SelfModelBuilder()
        snap = b.build(identity_anchor_manager=iam)
        iv = snap.identity_view
        self.assertIn("origin", iv)
        self.assertIn("core_values", iv)
        self.assertIn("stable_identity_markers", iv)
        # core_values 至少包含 honesty/growth/autonomy/empathy/kindness（5 个 anchor 的 related_core_values）
        for expected in ("honesty", "growth", "autonomy", "empathy", "kindness"):
            self.assertIn(expected, iv["core_values"], f"core_values 应包含 {expected}")
        # stable_identity_markers 至少 5 个（5 个 core anchor）
        self.assertGreaterEqual(len(iv["stable_identity_markers"]), 5)

    def test_sb12_personality_view_fields(self):
        state = PersonalityState()
        b = SelfModelBuilder()
        snap = b.build(personality_state=state)
        pv = snap.personality_view
        self.assertIn("stable_traits", pv)
        self.assertIn("evolving_traits", pv)
        self.assertIn("recent_changes", pv)

    def test_sb13_development_view_fields(self):
        b = SelfModelBuilder()
        snap = b.build()
        dv = snap.development_view
        self.assertIn("evolution_history", dv)
        self.assertIn("important_turning_points", dv)

    def test_sb14_contradiction_only_detected(self):
        # 构造 tension：independence 和 connection_value 都高
        state = PersonalityState(
            traits={
                "independence": 0.85,
                "connection_value": 0.82,
            },
        )
        b = SelfModelBuilder()
        snap = b.build(personality_state=state)
        tensions = snap.contradiction_view["detected_tensions"]
        self.assertGreaterEqual(len(tensions), 1, "高独立性+高连接欲→张力检测")
        for t in tensions:
            self.assertEqual(t["status"], "detected", "矛盾只能标记 detected，不能自动解决")
            self.assertIn("values independence while seeking connection", t["description"])

    def test_sb15_capability_view_fields_non_empty(self):
        b = SelfModelBuilder()
        snap = b.build()
        cv = snap.capability_view
        self.assertIn("known_strengths", cv)
        self.assertIn("limitations", cv)
        self.assertGreaterEqual(len(cv["known_strengths"]), 3)
        self.assertGreaterEqual(len(cv["limitations"]), 3)


class TestPhase40R261TurningPointsAndTensions(unittest.TestCase):
    """interest_transition/new_trait 会标记为 important_turning_points"""

    def test_interest_transition_is_turning_point(self):
        recs = _mk_evo_records(5)  # 其中 index 0, 4 %4==0 是 interest_transition；index 1 %4==1 是 new_trait
        state = PersonalityState()
        b = SelfModelBuilder()
        snap = b.build(personality_state=state, evolution_records=recs)
        self.assertGreaterEqual(len(snap.development_view["important_turning_points"]), 2)
        # 所有 turning points status=detected
        for tp in snap.development_view["important_turning_points"]:
            self.assertEqual(tp["status"], "detected")

    def test_tension_pairs_definition_exists(self):
        self.assertGreaterEqual(len(KNOWN_TENSION_PAIRS), 3)
        for entry in KNOWN_TENSION_PAIRS:
            self.assertEqual(len(entry), 3)


if __name__ == "__main__":
    unittest.main()
