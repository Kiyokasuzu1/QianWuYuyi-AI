"""
Phase 4.0 — R2.6.3 Gates: SelfReflectionBuilder Implementation

Gates (SRB-1 ~ SRB-16)：
  SRB-1  正常 build 成功：observed_changes + interpreted_causes 条目数匹配
  SRB-2  空输入（None everywhere）安全降级返回合法 SelfReflectionSnapshot（build_degraded）
  SRB-3  EvolutionRecord 映射正确：before / after / delta / trait / change_type / timestamp 与源 record 一致
  SRB-4  proposal_reasons 来自 GrowthProposal.evaluator_meta.reasons（不是硬编）
  SRB-5  approval_reasons 来自 ApprovalDecision.reasons
  SRB-6  identity_alignment.overall_assessment 严格映射 ICR：
          ICR.is_continuous=True → identity_compatible
          ICR.warnings 有 break → identity_break_risk
          ICR.warnings 有 narrative_gap / drift_large（无 break）→ identity_tension_detected
  SRB-7  unresolved_tensions.status 永远 "unresolved"（哪怕 build_degraded 也一样）
  SRB-8  不修改任何输入对象（self_model / icr / evo_recs / proposals_by_id.values / approvals_by_id.values）
  SRB-9  deterministic：同 build 两次，observed_changes / causes（去除 proposal/approval id 不同）/ summary 相同
  SRB-10 AST 扫描 self_reflection_builder 源码：
           无 FORBIDDEN_IMPORTS + 无 FORBIDDEN_CALLS（apply_evolution/create_proposal/accept_proposal/...）
  SRB-11 异常隔离：_build_identity_alignment 抛 RuntimeError → build_degraded；
           shape 合法 + unresolved_tensions 含 degraded
  SRB-12 output shape 永远合法：各种输入组合下 snapshot_dict 通过 validate_self_reflection_shape
  SRB-13 current_self_summary 3 段：origin_bullet 来源于 sm.identity_view.origin；core_values_bullets 来源于 sm.identity_view.core_values；recent_changes_bullets 包含最近变化 trait 名
  SRB-14 cause_tag 白名单：每条 interpreted_causes.cause_tag 仅在 evidence_driven / identity_consistent / transition_protected / under_review / unknown 中取值
  SRB-15 unresolved_tensions.kind ∈ {trait_tension, narrative_gap, identity_mismatch}；且 narrative_gap 在 ICR 有 warnings 对应出现
  SRB-16 version 严格递增（每次 build +1，包括 build_degraded 分支）
"""
from __future__ import annotations

import ast
import copy
import inspect
import unittest
from typing import Any, Dict, List

from src.self_reflection import (
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    build_snapshot,
    validate_self_reflection_shape,
)
from src.self_reflection.self_reflection_builder import SelfReflectionBuilder
from src.self_reflection.self_reflection_snapshot import SelfReflectionSnapshot
from src.self_model.self_model_builder import SelfModelBuilder
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.personality.identity_anchor import IdentityAnchorManager
from src.personality.evolution_record import build_evolution_record
from src.approval.approval_decision import build_approval_decision
from src.contracts.growth_schema import GrowthProposal


# ============================================================
# 辅助：造测试桩
# ============================================================
def _mk_sm():
    reset_personality_state()
    iam = IdentityAnchorManager()
    state = PersonalityState(traits={
        "creativity": 0.72,
        "curiosity": 0.8,
        "empathy": 0.65,
        "independence": 0.8,
        "connection_value": 0.75,
    })
    # 构造 2 条 evo records
    recs = [
        build_evolution_record(
            proposal_id="prop_cr_01", approval_id="appr_cr_01",
            change_type="trait_delta",
            before={"trait.creativity": 0.60},
            after={"trait.creativity": 0.72},
            reasons=["enough_evidence", "identity_consistent"],
            confidence=0.88,
        ),
        build_evolution_record(
            proposal_id="prop_mp_02", approval_id="appr_mp_02",
            change_type="interest_transition",
            before={"trait.empathy": 0.55},
            after={"trait.empathy": 0.65},
            reasons=["identity_consistent", "interest_transition_gradual_requires_observation"],
            confidence=0.80,
        ),
    ]
    b = SelfModelBuilder()
    return b.build(iam, state, recs), state, recs


def _mk_proposals():
    return {
        "prop_cr_01": GrowthProposal(
            id="prop_cr_01",
            confidence=0.90,
            evidence_ids=["mem_a", "mem_b", "mem_c"],
            evaluator_meta={
                "reasons": ["AI绘画多次在对话中自发提及", "高置信度相关经历验证"],
            },
        ),
        "prop_mp_02": GrowthProposal(
            id="prop_mp_02",
            confidence=0.82,
            evidence_ids=["mem_d", "mem_e"],
            evaluator_meta={
                "reasons": ["共情表达频次稳定上升", "transition_gradual_marked_needs_review"],
            },
        ),
    }


def _mk_approvals():
    return {
        "appr_cr_01": build_approval_decision(
            proposal_id="prop_cr_01", decision="approved",
            reasons=["identity_consistent", "enough_evidence", "no_conflict", "governance_policy_approved"],
            confidence=0.92,
        ),
        "appr_mp_02": build_approval_decision(
            proposal_id="prop_mp_02", decision="deferred",
            reasons=[
                "identity_consistent",
                "interest_transition_gradual_requires_observation",
                "conflict_detected_under_review",
            ],
            confidence=0.70,
        ),
    }


def _mk_icr_compatible() -> Dict[str, Any]:
    return {
        "is_continuous": True,
        "identity_anchor_stability": 0.98,
        "core_value_preserved": True,
        "personality_drift": {
            "stable_traits_changed": [],
            "large_changes": [],
        },
        "warnings": [],
        "version": 1,
        "generated_at": "2026-08-08T00:00:00+00:00",
    }


def _mk_icr_with_gaps_and_tension() -> Dict[str, Any]:
    return {
        "is_continuous": False,
        "identity_anchor_stability": 0.70,
        "core_value_preserved": True,
        "personality_drift": {
            "stable_traits_changed": [],
            "large_changes": [
                {"trait": "sudden", "before": 0.1, "after": 0.9, "delta": 0.8},
            ],
        },
        "warnings": ["narrative_gap_sudden", "narrative_gaps_total:1/1",
                    "personality_drift_large:1"],
        "version": 1,
        "generated_at": "2026-08-08T00:00:00+00:00",
    }


class TestPhase40R263SRB1BuildOK(unittest.TestCase):
    """SRB-1 正常 build：observed/causes 数量匹配"""

    def test_srb1_build_ok(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        snap = b.build(
            self_model_snapshot=sm,
            identity_continuity_report=_mk_icr_compatible(),
            evolution_records=recs,
            proposals_by_id=_mk_proposals(),
            approvals_by_id=_mk_approvals(),
        )
        self.assertIsInstance(snap, SelfReflectionSnapshot)
        # 每条 evo record 有 1 个 after entry → observed 2 条
        self.assertEqual(len(snap.observed_changes), 2)
        # 每条 observed 对应 1 条 cause
        self.assertEqual(len(snap.interpreted_causes), 2)
        # shape 必须合法（build 已经 validate 过，但这里再 double check via validate）
        validate_self_reflection_shape(snap.to_dict())


class TestPhase40R263SRB2EmptyDegrade(unittest.TestCase):
    """SRB-2 空输入安全降级"""

    def test_srb2_none_inputs_return_snapshot(self):
        b = SelfReflectionBuilder()
        snap = b.build()
        self.assertIsInstance(snap, SelfReflectionSnapshot)
        # shape 仍然合法
        validate_self_reflection_shape(snap.to_dict())

    def test_srb2_none_inputs_snapshot_still_readonly_frozen(self):
        b = SelfReflectionBuilder()
        snap = b.build()
        with self.assertRaises(AttributeError):
            snap._version = 999  # type: ignore[misc]


class TestPhase40R263SRB3EvoMapping(unittest.TestCase):
    """SRB-3 EvolutionRecord 映射正确"""

    def test_srb3_creativity_mapping(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs,
                       proposals_by_id=_mk_proposals(), approvals_by_id=_mk_approvals())
        cr = next(x for x in snap.observed_changes if x["trait"] == "creativity")
        self.assertEqual(cr["before"], 0.6)
        self.assertEqual(cr["after"], 0.72)
        self.assertAlmostEqual(cr["delta"], 0.12)
        self.assertEqual(cr["change_type"], "trait_delta")
        # empathy: interest_transition
        mp = next(x for x in snap.observed_changes if x["trait"] == "empathy")
        self.assertEqual(mp["change_type"], "interest_transition")
        self.assertAlmostEqual(mp["delta"], 0.10)


class TestPhase40R263SRB4SRB5ProposalApprovalReasons(unittest.TestCase):
    """SRB-4 proposal reasons; SRB-5 approval reasons"""

    def test_srb4_srb5_reasons_come_from_inputs(self):
        sm, _, recs = _mk_sm()
        proposals = _mk_proposals()
        approvals = _mk_approvals()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs,
                       proposals_by_id=proposals, approvals_by_id=approvals)
        cr_cause = next(c for c in snap.interpreted_causes if c["proposal_id"] == "prop_cr_01")
        # SRB-4
        for pr in proposals["prop_cr_01"].evaluator_meta["reasons"]:
            self.assertIn(pr, cr_cause["proposal_reasons"])
        # SRB-5
        for ar in approvals["appr_cr_01"]["reasons"]:
            self.assertIn(ar, cr_cause["approval_reasons"])

    def test_srb4_srb5_no_proposal_approval_cause_unknown(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        # 不传 proposals_by_id / approvals_by_id：proposal_id / approval_id 仍能从 evo record 读出
        # 但 proposal_reasons / approval_reasons 为空，cause_tag=unknown
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs)
        for cause in snap.interpreted_causes:
            self.assertEqual(cause["cause_tag"], "unknown")
            self.assertEqual(cause["proposal_reasons"], [])
            self.assertEqual(cause["approval_reasons"], [])
            self.assertEqual(cause["evidence_summary"], "无可追溯成长提案与审批记录")


class TestPhase40R263SRB6ICRMapping(unittest.TestCase):
    """SRB-6 identity alignment 严格映射 ICR"""

    def test_srb6_is_continuous_true(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs)
        self.assertEqual(snap.identity_alignment["overall_assessment"], "identity_compatible")
        self.assertGreaterEqual(snap.identity_alignment["continuity_score"], 0.9)
        self.assertIs(snap.identity_alignment["core_value_check"]["preserved"], True)

    def test_srb6_warnings_with_break_risk(self):
        sm, _, recs = _mk_sm()
        icr = _mk_icr_compatible()
        icr["is_continuous"] = False
        icr["warnings"] = ["identity_core_anchor_missing:['anchor_growth']", "some_other_warn"]
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                       evolution_records=recs)
        self.assertEqual(snap.identity_alignment["overall_assessment"], "identity_break_risk")

    def test_srb6_gap_tension_no_break(self):
        sm, _, recs = _mk_sm()
        icr = _mk_icr_with_gaps_and_tension()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                       evolution_records=recs)
        self.assertEqual(snap.identity_alignment["overall_assessment"], "identity_tension_detected")


class TestPhase40R263SRB7Unresolved(unittest.TestCase):
    """SRB-7 unresolved_tensions.status 永远 unresolved"""

    def test_srb7_always_unresolved(self):
        sm, _, recs = _mk_sm()
        icr = _mk_icr_with_gaps_and_tension()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                       evolution_records=recs,
                       proposals_by_id=_mk_proposals(), approvals_by_id=_mk_approvals())
        self.assertGreater(len(snap.unresolved_tensions), 0)
        for t in snap.unresolved_tensions:
            self.assertEqual(t["status"], "unresolved")

    def test_srb7_even_degraded_unresolved(self):
        b = SelfReflectionBuilder()
        # degrade: 故意用 mock 强制异常
        import unittest.mock as mock
        with mock.patch.object(
            SelfReflectionBuilder, "_build_identity_alignment",
            side_effect=RuntimeError("boom"),
        ):
            snap = b.build()
        for t in snap.unresolved_tensions:
            self.assertEqual(t["status"], "unresolved")


class TestPhase40R263SRB8InputImmutable(unittest.TestCase):
    """SRB-8 不修改输入对象"""

    def test_srb8_inputs_untouched(self):
        sm, state, recs = _mk_sm()
        proposals = _mk_proposals()
        approvals = _mk_approvals()
        icr = _mk_icr_compatible()
        sm_before = sm.to_dict()
        state_before = dict(state.snapshot().items())
        recs_before = copy.deepcopy(recs)
        prop_before = {pid: p.evaluator_meta for pid, p in proposals.items()}
        appr_before = {aid: copy.deepcopy(a) for aid, a in approvals.items()}
        icr_before = copy.deepcopy(icr)

        b = SelfReflectionBuilder()
        b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                evolution_records=recs,
                proposals_by_id=proposals, approvals_by_id=approvals)

        self.assertEqual(sm.to_dict(), sm_before)
        self.assertEqual(dict(state.snapshot().items()), state_before)
        # recs 深比较（每个 entry）
        for idx, rec in enumerate(recs):
            self.assertEqual({k: rec[k] for k in rec.keys()}, {k: recs_before[idx][k] for k in recs_before[idx].keys()},
                             f"rec {idx} 修改")
        for pid, p in proposals.items():
            self.assertEqual(p.evaluator_meta, prop_before[pid])
        for aid, ad in approvals.items():
            self.assertEqual({k: ad[k] for k in ad.keys()}, {k: appr_before[aid][k] for k in appr_before[aid].keys()})
        self.assertEqual(icr, icr_before)


class TestPhase40R263SRB9Deterministic(unittest.TestCase):
    """SRB-9 同输入下 observed/causes（忽略 proposal/approval 不影响 cause content 因为 ids 相同）+ summary 相同"""

    def test_srb9_2builds_identical_content(self):
        sm, _, recs = _mk_sm()
        proposals = _mk_proposals()
        approvals = _mk_approvals()
        icr = _mk_icr_compatible()
        ts = "2026-08-08T12:00:00+00:00"

        b = SelfReflectionBuilder()
        s1 = b.build(self_model_snapshot=sm, identity_continuity_report=copy.deepcopy(icr),
                     evolution_records=copy.deepcopy(recs),
                     proposals_by_id=copy.deepcopy(proposals),
                     approvals_by_id=copy.deepcopy(approvals),
                     generated_at=ts)
        s2 = b.build(self_model_snapshot=sm, identity_continuity_report=copy.deepcopy(icr),
                     evolution_records=copy.deepcopy(recs),
                     proposals_by_id=copy.deepcopy(proposals),
                     approvals_by_id=copy.deepcopy(approvals),
                     generated_at=ts)
        # observed/causes/summary
        self.assertEqual(s1.observed_changes, s2.observed_changes)
        self.assertEqual(s1.current_self_summary, s2.current_self_summary)
        # 但 version 不同（1 vs 2）→ 证明递增
        self.assertEqual(s1.version, 1)
        self.assertEqual(s2.version, 2)


class TestPhase40R263SRB10ForbiddenAST(unittest.TestCase):
    """SRB-10 AST 扫 builder 代码"""

    @staticmethod
    def _clean_src() -> str:
        import src.self_reflection.self_reflection_builder as mod
        src = inspect.getsource(mod)
        tree = ast.parse(src)

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_srb10_no_forbidden_imports(self):
        s = self._clean_src()
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"不应 import {bad}")

    def test_srb10_no_forbidden_calls(self):
        s = self._clean_src()
        for bad in FORBIDDEN_CALLS:
            self.assertNotIn(f"{bad}(", s, f"不应调用 {bad}()")


class TestPhase40R263SRB11ExceptionIsolated(unittest.TestCase):
    """SRB-11 异常隔离 + degraded 记录"""

    def test_srb11_exception_isolated_and_degraded(self):
        import unittest.mock as mock
        b = SelfReflectionBuilder()
        with mock.patch.object(SelfReflectionBuilder, "_build_observed_changes",
                               side_effect=RuntimeError("boom")):
            snap = b.build()
        self.assertIsInstance(snap, SelfReflectionSnapshot)
        # degraded tension present
        kinds = [t["kind"] for t in snap.unresolved_tensions]
        self.assertIn("identity_mismatch", kinds)
        titles = [t["title"] for t in snap.unresolved_tensions]
        self.assertTrue(any("已隔离" in t or "异常" in t for t in titles), titles)


class TestPhase40R263SRB12ShapeAlwaysValid(unittest.TestCase):
    """SRB-12: 各种输入组合下 shape 永远合法"""

    def test_srb12_combinations(self):
        sm, state, recs = _mk_sm()
        b = SelfReflectionBuilder()
        scenarios = [
            (None, None, None, {}, {}),
            (sm, None, recs, {}, {}),
            (sm, _mk_icr_compatible(), recs, {}, {}),
            (sm, _mk_icr_with_gaps_and_tension(), recs, _mk_proposals(), _mk_approvals()),
            (sm, _mk_icr_compatible(), recs, _mk_proposals(), {}),
        ]
        for (m, ic, rc, ps, as_) in scenarios:
            snap = b.build(
                self_model_snapshot=m,
                identity_continuity_report=ic,
                evolution_records=rc,
                proposals_by_id=ps,
                approvals_by_id=as_,
            )
            try:
                validate_self_reflection_shape(snap.to_dict())
            except Exception as exc:
                self.fail(f"shape invalid: {exc!r}, scenario={m is None, ic is None, rc is None, bool(ps), bool(as_)}")


class TestPhase40R263SRB13Summary3Segments(unittest.TestCase):
    """SRB-13 summary: origin/core/recent 3 段都来自 sm / observed"""

    def test_srb13_three_segments_match_inputs(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs)
        s = snap.current_self_summary
        self.assertIn("清夏铃", s["origin_bullet"])  # 来自 identity anchor_creator.origin (creator→关系描述)
        for cv in ("honesty", "growth", "autonomy", "empathy", "kindness"):
            self.assertIn(cv, s["core_values_bullets"], f"core value {cv} 应来自 SelfModel.identity_view.core_values")
        # recent_change bullet: 至少一条含有 trait 名（creativity 或 empathy）
        bullets = " ".join(s["recent_changes_bullets"])
        self.assertIn("creativity", bullets)
        self.assertIn("empathy", bullets)


class TestPhase40R263SRB14CauseTagWhitelist(unittest.TestCase):
    """SRB-14 cause_tag 只在白名单"""

    _WHITE = {"evidence_driven", "identity_consistent", "transition_protected", "under_review", "unknown"}

    def test_srb14_all_cause_tags_in_white(self):
        sm, _, recs = _mk_sm()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=_mk_icr_compatible(),
                       evolution_records=recs,
                       proposals_by_id=_mk_proposals(), approvals_by_id=_mk_approvals())
        tags = [c["cause_tag"] for c in snap.interpreted_causes]
        # identity_consistent + transition_protected (from appr reason) 都在白名单
        for t in tags:
            self.assertIn(t, self._WHITE, f"cause_tag {t} 非法")
        # 至少一条 transition_protected（第二条 approval 含 interest_transition_gradual_requires_observation）
        self.assertIn("transition_protected", tags)
        # 至少一条 evidence_driven / identity_consistent
        self.assertTrue(any(t in tags for t in ("evidence_driven", "identity_consistent")))


class TestPhase40R263SRB15TensionKinds(unittest.TestCase):
    """SRB-15 tension kind 白名单 + narrative_gap/kind 对应出现"""

    _KINDS = {"trait_tension", "narrative_gap", "identity_mismatch"}

    def test_srb15_kinds_white(self):
        sm, _, recs = _mk_sm()
        icr = _mk_icr_with_gaps_and_tension()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                       evolution_records=recs)
        for t in snap.unresolved_tensions:
            self.assertIn(t["kind"], self._KINDS, f"kind {t['kind']} 非法")

    def test_srb15_narrative_gap_appears_from_icr_warning(self):
        sm, _, recs = _mk_sm()
        icr = _mk_icr_with_gaps_and_tension()
        b = SelfReflectionBuilder()
        snap = b.build(self_model_snapshot=sm, identity_continuity_report=icr,
                       evolution_records=recs)
        kinds = [t["kind"] for t in snap.unresolved_tensions]
        self.assertIn("narrative_gap", kinds)
        # 标题包含 sudden
        titles = [t["title"] for t in snap.unresolved_tensions if t["kind"] == "narrative_gap"]
        self.assertTrue(any("sudden" in t for t in titles), titles)


class TestPhase40R263SRB16VersionIncrement(unittest.TestCase):
    """SRB-16 version 每次 build +1"""

    def test_srb16_version_increments(self):
        b = SelfReflectionBuilder()
        v1 = b.build().version
        v2 = b.build().version
        v3 = b.build().version
        self.assertEqual([v1, v2, v3], [1, 2, 3])

    def test_srb16_degraded_still_increments(self):
        import unittest.mock as mock
        b = SelfReflectionBuilder()
        # 1 正常
        v1 = b.build().version
        # 1 异常隔离
        with mock.patch.object(SelfReflectionBuilder, "_build_observed_changes",
                               side_effect=RuntimeError("boom")):
            v2 = b.build().version
        # 1 正常
        v3 = b.build().version
        self.assertEqual([v1, v2, v3], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
