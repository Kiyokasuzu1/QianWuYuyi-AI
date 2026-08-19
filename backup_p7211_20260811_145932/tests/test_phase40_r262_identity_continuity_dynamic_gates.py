"""
Phase 4.0 — R2.6.2 Gates: Identity Continuity Dynamic

Gates（IC-1 ~ IC-15）：
  IC-1   Report 形状 7 字段 frozen exact + validate_report_shape 校验
  IC-2   全维度正常（anchor ok + no drift + narrative ok）→ is_continuous=True + warnings=[]
  IC-3   identity core anchor 缺失 → identity_anchor_stability↓ + is_continuous=False + warning
  IC-4   core_values 集合不完整 → core_value_preserved=False + warning
  IC-5   anchor 权重漂移 critical → anchor_score↓ + warning
  IC-6   personality massive drift（major_count>=3）→ drift_score↓ + warning + is_continuous=False
  IC-7   large_changes 但 narrative 被 evolution_records 解释 → narrative_gap=0 → continuous=True
  IC-8   large_changes 且无任何 evolution_records 解释 → narrative_gaps → is_continuous=False
  IC-9   drift 大但 anchor 稳定、narrative 连续 → warnings 有但 is_continuous 取决于总分
  IC-10  空输入（None baseline/None current/None sm/None records）→ is_continuous=True（无 break 信息）
  IC-11  异常隔离：evaluate 崩溃 → 返回 is_continuous=False + warning=`dynamic_check_crash_isolated`
  IC-12  AST 扫描：模块不 import forbidden_imports + 不调用 forbidden_calls
  IC-13  输入对象不被修改：baseline_state.traits + current_state.traits + iam.anchors 前后完全一致
  IC-14  personality_drift.stable_traits_changed 和 large_changes 类型正确且 delta 字段存在
  IC-15  version 每次 evaluate 严格递增
"""
from __future__ import annotations

import ast
import inspect
import unittest
from typing import Any, Dict, List

from src.personality.identity_continuity_dynamic import (
    IdentityContinuityDynamicChecker,
    FROZEN_REPORT_KEYS,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_report_shape,
)
from src.personality.identity_anchor import IdentityAnchorManager
from src.personality.personality_state import (
    PersonalityState,
    reset_personality_state,
)
from src.self_model.self_model_builder import SelfModelBuilder
from src.personality.evolution_record import build_evolution_record


# ============================================================
# 辅助：造 evolution records（针对指定 trait）
# ============================================================
def _mk_evo_for(trait: str, *, before: float, after: float, idx: int = 0) -> Dict[str, Any]:
    return build_evolution_record(
        proposal_id=f"prop_ic_{idx}",
        approval_id=f"approval_ic_{idx}",
        change_type="interest_transition" if abs(after - before) > 0.2 else "trait_delta",
        before={f"trait.{trait}": before},
        after={f"trait.{trait}": after},
        reasons=["enough_evidence", "identity_consistent"],
        confidence=0.9,
    )


class TestPhase40R262IC1Shape(unittest.TestCase):
    """IC-1: 7 字段冻结 exact"""

    def test_ic1_frozen_keys_exact_7(self):
        self.assertEqual(len(FROZEN_REPORT_KEYS), 7)
        self.assertEqual(set(FROZEN_REPORT_KEYS), {
            "is_continuous",
            "identity_anchor_stability",
            "core_value_preserved",
            "personality_drift",
            "warnings",
            "version",
            "generated_at",
        })

    def test_ic1_validate_rejects_missing(self):
        rep = {k: v for k, v in zip(FROZEN_REPORT_KEYS, [True, 0.9, True, {"stable_traits_changed": [], "large_changes": []}, [], 0, "now"])}
        del rep["warnings"]
        with self.assertRaises(ValueError):
            validate_report_shape(rep)

    def test_ic1_validate_rejects_extra(self):
        rep = {k: v for k, v in zip(FROZEN_REPORT_KEYS, [True, 0.9, True, {"stable_traits_changed": [], "large_changes": []}, [], 0, "now"])}
        rep["unregistered_score"] = 0.5
        with self.assertRaises(ValueError):
            validate_report_shape(rep)

    def test_ic1_every_report_from_evaluate_passes_validate(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        base = PersonalityState()
        curr = PersonalityState()
        rep = ch.evaluate(
            identity_anchor_manager=iam,
            baseline_state=base,
            current_state=curr,
        )
        validate_report_shape(rep)  # 不抛异常


class TestPhase40R262IC2AllDimOK(unittest.TestCase):
    """IC-2: 全维度正常 → is_continuous=True, no warnings"""

    def test_ic2_all_ok(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        base = PersonalityState(traits={"curiosity": 0.7, "creativity": 0.8, "gentleness": 0.9})
        curr = PersonalityState(traits={"curiosity": 0.71, "creativity": 0.805, "gentleness": 0.898})
        rep = ch.evaluate(identity_anchor_manager=iam, baseline_state=base, current_state=curr)
        self.assertIs(rep["is_continuous"], True)
        self.assertGreaterEqual(rep["identity_anchor_stability"], 0.8)
        self.assertIs(rep["core_value_preserved"], True)
        self.assertEqual(rep["personality_drift"]["large_changes"], [])


class TestPhase40R262IC345AnchorBreak(unittest.TestCase):
    """IC-3 core anchor missing；IC-4 core_values 不完整；IC-5 weight drift critical"""

    def test_ic3_core_anchor_missing(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        # 手动移除 anchor_authenticity
        if "anchor_authenticity" in iam.anchors:
            del iam.anchors["anchor_authenticity"]
        base = PersonalityState()
        curr = PersonalityState()
        rep = ch.evaluate(identity_anchor_manager=iam, baseline_state=base, current_state=curr)
        self.assertIs(rep["is_continuous"], False, "hard break: anchor_score 会被 *0.6")
        self.assertTrue(any("identity_core_anchor_missing" in w for w in rep["warnings"]))
        self.assertLess(rep["identity_anchor_stability"], 0.99)

    def test_ic4_core_values_incomplete(self):
        # 只留 anchor_authenticity → related_core_values 只有 honesty
        anchors_only_honesty = []
        full = list(IdentityAnchorManager().get_all_anchors())
        for a in full:
            if str(getattr(a, "anchor_id", "")) == "anchor_authenticity":
                anchors_only_honesty.append(a)
                break
        iam = IdentityAnchorManager(anchors=anchors_only_honesty)
        ch = IdentityContinuityDynamicChecker()
        rep = ch.evaluate(identity_anchor_manager=iam)
        self.assertIs(rep["core_value_preserved"], False)
        self.assertTrue(any("core_value_missing" in w for w in rep["warnings"]))

    def test_ic5_weight_drift_critical(self):
        iam = IdentityAnchorManager()
        # 把 anchor_growth 权重从 original 0.9 砍到 0.5（delta=0.4，> 0.20 critical）
        a = iam.get_anchor("anchor_growth")
        a.weight = 0.5
        ch = IdentityContinuityDynamicChecker()
        rep = ch.evaluate(identity_anchor_manager=iam)
        self.assertTrue(any("anchor_weight_drift_ge_critical" in w for w in rep["warnings"]))


class TestPhase40R262IC6MassiveDrift(unittest.TestCase):
    """IC-6: massive drift（3+ major）→ is_continuous=False + warning"""

    def test_ic6_massive_3_major_changes(self):
        ch = IdentityContinuityDynamicChecker()
        base = PersonalityState(traits={
            "curiosity": 0.9,
            "creativity": 0.9,
            "gentleness": 0.9,
            "independence": 0.8,
        })
        curr = PersonalityState(traits={
            "curiosity": 0.1,  # delta=-0.8
            "creativity": 0.05,  # delta=-0.85
            "gentleness": 0.05,  # delta=-0.85
            "independence": 0.0,  # delta=-0.8
        })
        rep = ch.evaluate(
            identity_anchor_manager=IdentityAnchorManager(),
            baseline_state=base,
            current_state=curr,
            evolution_records=[],  # narrative 也断
        )
        self.assertEqual(len(rep["personality_drift"]["large_changes"]), 4)
        self.assertTrue(any("personality_drift_massive" in w for w in rep["warnings"]))
        self.assertIs(rep["is_continuous"], False)


class TestPhase40R262IC7IC8Narrative(unittest.TestCase):
    """IC-7 narrative covered → ok；IC-8 narrative gap → False"""

    def test_ic7_large_changes_covered_by_evolution_records(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        base = PersonalityState(traits={"curiosity": 0.7, "AI_art": 0.2})
        curr = PersonalityState(traits={"curiosity": 0.71, "AI_art": 0.55})
        # evolution record 解释 AI_art: 0.2 → 0.55
        recs = [_mk_evo_for("AI_art", before=0.2, after=0.55, idx=7)]
        rep = ch.evaluate(
            identity_anchor_manager=iam,
            baseline_state=base,
            current_state=curr,
            evolution_records=recs,
        )
        has_gap = any("narrative_gap_AI_art" in w for w in rep["warnings"])
        self.assertFalse(has_gap, f"AI_art 应被 evolution record 覆盖，但 warnings={rep['warnings']}")

    def test_ic8_large_changes_no_records_narrative_gap(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        base = PersonalityState(traits={"curiosity": 0.7, "sudden": 0.1})
        curr = PersonalityState(traits={"curiosity": 0.7, "sudden": 0.95})
        # 无 evolution records；current_self_model_snapshot 也没有
        rep = ch.evaluate(
            identity_anchor_manager=iam,
            baseline_state=base,
            current_state=curr,
            evolution_records=[],
        )
        self.assertTrue(any("narrative_gap_sudden" in w for w in rep["warnings"]))
        # sudden 大变化 0.85，没有 narrative → 分数掉很多
        self.assertIs(rep["is_continuous"], False)


class TestPhase40R262IC9DriftyAnchorOk(unittest.TestCase):
    """IC-9: 有 drift 和 warning，但 anchor 稳 + narrative 连续 → continuous 取决于总分"""

    def test_ic9_drift_moderate_but_continuous(self):
        ch = IdentityContinuityDynamicChecker()
        iam = IdentityAnchorManager()
        base = PersonalityState(traits={"creativity": 0.6, "empathy": 0.5})
        curr = PersonalityState(traits={"creativity": 0.72, "empathy": 0.57})
        # evolution recs 解释两者
        recs = [
            _mk_evo_for("creativity", before=0.6, after=0.72, idx=90),
            _mk_evo_for("empathy", before=0.5, after=0.57, idx=91),
        ]
        rep = ch.evaluate(
            identity_anchor_manager=iam,
            baseline_state=base,
            current_state=curr,
            evolution_records=recs,
        )
        # creativity 0.12 delta / empathy 0.07 → 有 stable_traits_changed
        self.assertGreaterEqual(len(rep["personality_drift"]["stable_traits_changed"]), 1)
        # 但总分应 >= 0.70（默认阈值）→ continuous=True
        self.assertIs(rep["is_continuous"], True)
        # 无 narrative gaps
        self.assertFalse(any(w.startswith("narrative_gap_") for w in rep["warnings"]))


class TestPhase40R262IC10EmptyBaseline(unittest.TestCase):
    """IC-10: 空输入（None baseline/current/sm/records）→ is_continuous=True"""

    def test_ic10_none_everywhere(self):
        ch = IdentityContinuityDynamicChecker()
        rep = ch.evaluate()
        self.assertIs(rep["is_continuous"], True)
        self.assertEqual(rep["personality_drift"]["stable_traits_changed"], [])
        self.assertEqual(rep["personality_drift"]["large_changes"], [])


class TestPhase40R262IC11CrashIsolation(unittest.TestCase):
    """IC-11: evaluate 崩溃时异常隔离"""

    def test_ic11_crash_in_anchor_check_isolated(self):
        from unittest.mock import patch
        ch = IdentityContinuityDynamicChecker()
        # 造一个 iam 但在 get_all_anchors 时崩溃
        class _BrokenIAM:
            def get_all_anchors(self):
                raise RuntimeError("boom")
        with patch(
            "src.personality.identity_continuity_dynamic.IdentityContinuityDynamicChecker._check_identity_anchor",
            side_effect=RuntimeError("mocked crash"),
        ):
            rep = ch.evaluate(identity_anchor_manager=_BrokenIAM())
        # 异常隔离：outer try/except 捕获任意崩溃
        self.assertIs(rep["is_continuous"], False)
        self.assertTrue(any("dynamic_check_crash_isolated" in w for w in rep["warnings"]))
        # shape 依然合法
        validate_report_shape(rep)


class TestPhase40R262IC12ForbiddenAST(unittest.TestCase):
    """IC-12: AST 扫描无 forbidden import/call"""

    @staticmethod
    def _clean_src() -> str:
        import src.personality.identity_continuity_dynamic as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_ic12_no_forbidden_imports(self):
        s = self._clean_src()
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"不应 import {bad}")

    def test_ic12_no_forbidden_calls(self):
        s = self._clean_src()
        for bad in FORBIDDEN_CALLS:
            self.assertNotIn(f"{bad}(", s, f"不应调用 {bad}()")


class TestPhase40R262IC13InputImmutable(unittest.TestCase):
    """IC-13: evaluate 不修改任何输入对象"""

    def test_ic13_inputs_untouched(self):
        reset_personality_state()
        iam = IdentityAnchorManager()
        base = PersonalityState(traits={"a": 0.5, "b": 0.6})
        curr = PersonalityState(traits={"a": 0.52, "b": 0.63})
        recs = [_mk_evo_for("a", before=0.5, after=0.52, idx=130)]
        before_iam = [(a.anchor_id, a.weight, a.is_core) for a in iam.get_all_anchors()]
        before_base = dict(base.traits.items())
        before_curr = dict(curr.traits.items())
        before_recs = [(r["proposal_id"], r["approval_id"], tuple(sorted(r["after"].items()))) for r in recs]

        ch = IdentityContinuityDynamicChecker()
        ch.evaluate(
            identity_anchor_manager=iam,
            baseline_state=base,
            current_state=curr,
            evolution_records=recs,
        )

        after_iam = [(a.anchor_id, a.weight, a.is_core) for a in iam.get_all_anchors()]
        after_base = dict(base.traits.items())
        after_curr = dict(curr.traits.items())
        after_recs = [(r["proposal_id"], r["approval_id"], tuple(sorted(r["after"].items()))) for r in recs]
        self.assertEqual(before_iam, after_iam)
        self.assertEqual(before_base, after_base)
        self.assertEqual(before_curr, after_curr)
        self.assertEqual(before_recs, after_recs)


class TestPhase40R262IC14DriftShape(unittest.TestCase):
    """IC-14: stable_traits_changed / large_changes 每一项字段完整"""

    def test_ic14_entry_shape(self):
        ch = IdentityContinuityDynamicChecker()
        base = PersonalityState(traits={"t1": 0.5, "t2": 0.5, "t3": 0.5})
        curr = PersonalityState(traits={"t1": 0.56, "t2": 0.85, "t3": 0.51})
        rep = ch.evaluate(baseline_state=base, current_state=curr)
        for e in rep["personality_drift"]["stable_traits_changed"]:
            self.assertIn("trait", e)
            self.assertIn("before", e)
            self.assertIn("after", e)
            self.assertIn("delta", e)
        for e in rep["personality_drift"]["large_changes"]:
            self.assertIn("trait", e)
            self.assertIn("before", e)
            self.assertIn("after", e)
            self.assertIn("delta", e)
        # t2 delta=0.35 > 0.20 → large_changes 含 t2
        self.assertIn("t2", [e["trait"] for e in rep["personality_drift"]["large_changes"]])


class TestPhase40R262IC15VersionIncrement(unittest.TestCase):
    """IC-15: version 每次 evaluate 严格递增"""

    def test_ic15_version_increments(self):
        ch = IdentityContinuityDynamicChecker()
        r1 = ch.evaluate()
        r2 = ch.evaluate()
        r3 = ch.evaluate()
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 2)
        self.assertEqual(r3["version"], 3)


if __name__ == "__main__":
    unittest.main()
