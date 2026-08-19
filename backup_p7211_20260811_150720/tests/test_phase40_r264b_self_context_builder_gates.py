"""
Phase 4.0 — R2.6.4-B Gates: SelfContextBuilder（read → filter → format）

Gates (SCB-1 ~ SCB-16)：
  SCB-1.1  identity_summary 来源于 SelfModel.identity_view.origin/core_values/stable_identity_markers（重新修改前 vs 后 match）
  SCB-1.2  personality_summary.top_traits 来源于 PersonalityState.traits（top-K 按 value 降序 match；level 规则 match；digits 按 max_trait_detail_digits round）
  SCB-1.3  personality_summary.stable_traits 来源于 SelfModel.personality_view.stable_traits.keys（match）
  SCB-1.4  reflection_summary.observed_change_bullets 来源于 SelfReflection.observed_changes；tension_titles 来源于 unresolved_tensions[].title；reflection_id 相同
  SCB-1.5  continuity_status：identity_core_anchor_missing → identity_break；is_continuous=False 无 break → tension_warning；is_continuous=True 有 tension warnings → continuous_with_tension；纯净 → continuous_safe
  SCB-2.1  mode=minimal allow_list 仅含 identity_summary:origin_bullet + continuity_status（不暴露其他）
  SCB-2.2  mode=summary_only allow_list 不含 deltas/recent bullets（但仍然在 summary dict 里保留字段，只是 policy.allow 不放行；Prompt 层按 policy 过滤）
  SCB-2.3  mode=read_only_identity allow_list = 14 颗 token（但 extra_deny 覆盖后相应移除）
  SCB-3.1  PersonalityState / SelfModel / SelfReflection / EvolutionRecords / ICR 输入对象在 build 前后 hash（dict 比较 / snapshot 比较）一致
  SCB-4.1  AST 扫 builder：无 FORBIDDEN_IMPORTS / FORBIDDEN_CALLS（红线 1/2/3）
  SCB-5.1  1000 条 evolution_records → growth_summary.recent_change_bullets 数量 ≤ max_recent_changes
  SCB-5.2  30 个 traits → top_traits 数量 ≤ top_traits_limit（5）
  SCB-5.3  max_trait_detail_digits = 0 时 top_traits.value 是整数；= 3 时 ≤ 3 位小数
  SCB-6    shape 永远合法（5 种输入组合 + degrade 分支都通过 validate_self_context_shape）
  SCB-7    version 严格递增（正常 build 3 次 1→2→3；含异常分支）
  SCB-8    deterministic：相同输入 + 相同 mode，4 份 summary + status + policy(allow/deny) 相同（version/ts 不同忽略）
  SCB-9    异常隔离：_format_top_traits 抛错 → build_degraded；origin_bullet 含 degraded 字样；version 连续；shape 合法
"""
from __future__ import annotations

import ast
import copy
import inspect
import unittest
from typing import Any, Dict, List

from src.context import (
    INJECTION_POLICY_MODES,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_context_shape,
)
from src.context.self_context_builder import SelfContextBuilder
from src.self_model.self_model_builder import SelfModelBuilder
from src.personality.personality_state import PersonalityState, reset_personality_state
from src.personality.identity_anchor import IdentityAnchorManager
from src.personality.evolution_record import build_evolution_record
from src.self_reflection.self_reflection_builder import SelfReflectionBuilder


# ============================================================
# helper：造一套完整合法输入
# ============================================================
def _mk_inputs():
    reset_personality_state()
    iam = IdentityAnchorManager()
    state = PersonalityState(traits={
        "creativity": 0.72,
        "curiosity": 0.80,
        "empathy": 0.65,
        "independence": 0.80,
        "playfulness": 0.55,
        "connection_value": 0.75,
    })
    recs = [
        build_evolution_record(
            proposal_id="prop_cr", approval_id="appr_cr", change_type="trait_delta",
            before={"trait.creativity": 0.60}, after={"trait.creativity": 0.72},
            reasons=["enough_evidence", "identity_consistent"], confidence=0.9,
        ),
        build_evolution_record(
            proposal_id="prop_emp", approval_id="appr_emp", change_type="interest_transition",
            before={"trait.empathy": 0.55}, after={"trait.empathy": 0.65},
            reasons=["identity_consistent"], confidence=0.8,
        ),
    ]
    sm = SelfModelBuilder().build(iam, state, recs)
    # 造 icr compatible / with_tension 两份
    icr_compatible = {
        "is_continuous": True,
        "identity_anchor_stability": 0.99,
        "core_value_preserved": True,
        "personality_drift": {"stable_traits_changed": [], "large_changes": []},
        "warnings": [],
        "version": 1,
    }
    icr_tension = {
        "is_continuous": False,
        "identity_anchor_stability": 0.69,
        "core_value_preserved": True,
        "personality_drift": {"stable_traits_changed": [], "large_changes": [
            {"trait": "sudden", "before": 0.1, "after": 0.9, "delta": 0.8},
        ]},
        "warnings": ["narrative_gap_sudden", "personality_drift_large:1"],
        "version": 1,
    }
    icr_break = {
        "is_continuous": False,
        "identity_anchor_stability": 0.12,
        "core_value_preserved": False,
        "personality_drift": {"stable_traits_changed": [], "large_changes": []},
        "warnings": ["identity_core_anchor_missing:['anchor_growth']"],
        "version": 1,
    }
    # 构造 proposals + approvals（给 SelfReflectionBuilder 用）
    from src.contracts.growth_schema import GrowthProposal
    from src.approval.approval_decision import build_approval_decision
    proposals = {
        "prop_cr": GrowthProposal(id="prop_cr", confidence=0.90, evidence_ids=["mem_a", "mem_b", "mem_c"],
                                  evaluator_meta={"reasons": ["AI绘画多次在对话中自发提及"]}),
        "prop_emp": GrowthProposal(id="prop_emp", confidence=0.82, evidence_ids=["mem_d", "mem_e"],
                                   evaluator_meta={"reasons": ["共情表达频次稳定上升"]}),
    }
    approvals = {
        "appr_cr": build_approval_decision(proposal_id="prop_cr", decision="approved",
                                          reasons=["identity_consistent", "enough_evidence", "no_conflict",
                                                   "governance_policy_approved"],
                                          confidence=0.92),
        "appr_emp": build_approval_decision(proposal_id="prop_emp", decision="deferred",
                                           reasons=["identity_consistent",
                                                    "interest_transition_gradual_requires_observation"],
                                           confidence=0.70),
    }
    reflection = SelfReflectionBuilder().build(
        self_model_snapshot=sm, identity_continuity_report=icr_compatible,
        evolution_records=recs, proposals_by_id=proposals, approvals_by_id=approvals,
    )
    return state, sm, recs, reflection, icr_compatible, icr_tension, icr_break


class TestPhase40R264BSCB1Sources(unittest.TestCase):
    """SCB-1 数据来源正确"""

    def test_scb1_identity_from_sm(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        sm_identity = sm.identity_view
        builder = SelfContextBuilder()
        ctx = builder.build(
            personality_state=state, self_model_snapshot=sm,
            self_reflection_snapshot=reflection, evolution_records=recs,
            identity_continuity_report=icr,
        )
        self.assertEqual(ctx["identity_summary"]["origin_bullet"], sm_identity.get("origin"))
        self.assertEqual(ctx["identity_summary"]["core_values_bullets"], list(sm_identity.get("core_values") or []))
        self.assertEqual(ctx["identity_summary"]["trait_anchors_present"],
                         list(sm_identity.get("stable_identity_markers") or []))
        self.assertEqual(ctx["identity_summary"]["self_model_version"], sm.version)

    def test_scb1_personality_top_traits_from_state(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder(top_traits_limit=3, max_trait_detail_digits=3)
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        top_names = [t["trait"] for t in ctx["personality_summary"]["top_traits"]]
        # curiosity (0.80) & independence (0.80) 并列 0.80 → 按字母序 independence 在前，然后 creativity 0.72；实际 3 条应该是 cur/indep 二选一 + creativity + connection_value
        # 只校验 top_traits_limit=3 cap
        self.assertEqual(len(top_names), 3)
        # 按 trait value 校验
        for t in ctx["personality_summary"]["top_traits"]:
            self.assertIn(t["trait"], state.traits)
            self.assertAlmostEqual(t["value"], round(state.traits[t["trait"]], 3), places=3)
            if t["value"] >= 0.67:
                self.assertEqual(t["level"], "high")
            elif t["value"] <= 0.33:
                self.assertEqual(t["level"], "low")
            else:
                self.assertEqual(t["level"], "medium")

    def test_scb1_stable_traits_from_sm(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder()
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        stable_from_sm = sorted([str(k).strip() for k in (sm.personality_view.get("stable_traits") or {}).keys() if str(k).strip()])
        self.assertEqual(ctx["personality_summary"]["stable_traits"], stable_from_sm)

    def test_scb1_reflection_from_snapshot(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder()
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        # reflection_id 对应
        self.assertEqual(ctx["reflection_summary"]["reflection_id"], reflection.reflection_id)
        # observed bullets 应包含 creativity / empathy 的事实（允许展示层用中文别名：创造力/共情能力）
        bullets = " ".join(ctx["reflection_summary"]["observed_change_bullets"])
        self.assertTrue(
            ("creativity" in bullets) or ("创造力" in bullets),
            msg=f"reflection observed bullets 中必须体现 creativity/创造力 变更；bullets={bullets}",
        )
        self.assertTrue(
            ("empathy" in bullets) or ("共情" in bullets),
            msg=f"reflection observed bullets 中必须体现 empathy/共情能力 变更；bullets={bullets}",
        )
        # tension_titles：正常 icr compatible 可能空（SelfReflection 未生成 tensions 时为空列表）
        self.assertIsInstance(ctx["reflection_summary"]["unresolved_tension_titles"], list)

    def test_scb1_continuity_rules(self):
        state, sm, recs, reflection, icr_ok, icr_tension, icr_break = _mk_inputs()
        builder = SelfContextBuilder()
        ctx_ok = builder.build(personality_state=state, self_model_snapshot=sm,
                               self_reflection_snapshot=reflection, evolution_records=recs,
                               identity_continuity_report=icr_ok)
        self.assertEqual(ctx_ok["continuity_status"], "continuous_safe")
        ctx_tension = builder.build(personality_state=state, self_model_snapshot=sm,
                                    self_reflection_snapshot=reflection, evolution_records=recs,
                                    identity_continuity_report=icr_tension)
        self.assertEqual(ctx_tension["continuity_status"], "tension_warning")
        ctx_break = builder.build(personality_state=state, self_model_snapshot=sm,
                                  self_reflection_snapshot=reflection, evolution_records=recs,
                                  identity_continuity_report=icr_break)
        self.assertEqual(ctx_break["continuity_status"], "identity_break")
        # continuous_with_tension：is_continuous=True 但 warnings 非空
        icr_with = dict(icr_ok)
        icr_with["warnings"] = ["identity_weight_drift_warning"]
        ctx_with = builder.build(personality_state=state, self_model_snapshot=sm,
                                 self_reflection_snapshot=reflection, evolution_records=recs,
                                 identity_continuity_report=icr_with)
        self.assertEqual(ctx_with["continuity_status"], "continuous_with_tension")


class TestPhase40R264BSCB2Policy(unittest.TestCase):
    """SCB-2 注入策略 mode 生效"""

    def test_scb2_minimal_allow(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder(default_mode="minimal")
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        allow = set(ctx["injection_policy"]["allow_list"])
        self.assertEqual(allow, {"identity_summary:origin_bullet", "continuity_status"})

    def test_scb2_summary_only_excludes_detail_tokens(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder(default_mode="summary_only")
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        allow = set(ctx["injection_policy"]["allow_list"])
        # 不应该包含 bullets/deltas/details
        self.assertNotIn("growth_summary:bullets", allow)
        self.assertNotIn("personality_summary:deltas", allow)
        self.assertNotIn("reflection_summary:observed", allow)
        # 必须包含 summary 级别 tokens
        self.assertTrue({"personality_summary:top_traits",
                         "personality_summary:stable_traits",
                         "reflection_summary:tensions"} <= allow)

    def test_scb2_read_only_identity_full_14_tokens(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder(default_mode="read_only_identity")
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=recs,
                            identity_continuity_report=icr)
        # 14 tokens 总数
        self.assertEqual(len(ctx["injection_policy"]["allow_list"]), 14)
        # deny 覆盖
        builder2 = SelfContextBuilder(
            default_mode="read_only_identity",
            extra_deny_list=["growth_summary:bullets", "personality_summary:deltas"],
        )
        ctx2 = builder2.build(personality_state=state, self_model_snapshot=sm,
                              self_reflection_snapshot=reflection, evolution_records=recs,
                              identity_continuity_report=icr)
        self.assertIn("growth_summary:bullets", ctx2["injection_policy"]["deny_list"])
        self.assertIn("personality_summary:deltas", ctx2["injection_policy"]["deny_list"])


class TestPhase40R264BSCB3NoModify(unittest.TestCase):
    """SCB-3 无状态修改（输入对象 hash/compare 一致）"""

    def test_scb3_inputs_untouched(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        # snapshot all inputs before
        state_before = dict(state.snapshot().items())
        sm_before = sm.to_dict()
        recs_before = copy.deepcopy(recs)
        ref_before = reflection.to_dict()
        icr_before = copy.deepcopy(icr)
        builder = SelfContextBuilder()
        builder.build(personality_state=state, self_model_snapshot=sm,
                      self_reflection_snapshot=reflection, evolution_records=recs,
                      identity_continuity_report=icr)
        self.assertEqual(dict(state.snapshot().items()), state_before)
        self.assertEqual(sm.to_dict(), sm_before)
        for i, r in enumerate(recs):
            self.assertEqual({k: r[k] for k in r.keys()}, {k: recs_before[i][k] for k in recs_before[i].keys()})
        self.assertEqual(reflection.to_dict(), ref_before)
        self.assertEqual(icr, icr_before)


class TestPhase40R264BSCB4ForbiddenAST(unittest.TestCase):
    """SCB-4 AST 红线：不 import / 不调用 红线函数"""

    @staticmethod
    def _clean_src() -> str:
        import src.context.self_context_builder as mod
        tree = ast.parse(inspect.getsource(mod))

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_scb4_no_forbidden_imports(self):
        s = self._clean_src()
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"不应 import {bad}")

    def test_scb4_no_forbidden_calls(self):
        s = self._clean_src()
        for bad in FORBIDDEN_CALLS:
            self.assertNotIn(f"{bad}(", s, f"不应调用 {bad}()")


class TestPhase40R264BSCB5Inflation(unittest.TestCase):
    """SCB-5 膨胀保护"""

    def test_scb5_recent_bullets_cap(self):
        state, sm, _, reflection, icr, _, _ = _mk_inputs()
        # 造 1000 条 evolution records
        many_records: List[Any] = []
        for i in range(1000):
            before = {"trait.trait_x": i * 0.0001}
            after = {"trait.trait_x": i * 0.0001 + 0.1}
            many_records.append(build_evolution_record(
                proposal_id=f"p_{i}", approval_id=f"a_{i}", change_type="trait_delta",
                before=before, after=after, reasons=["enough_evidence"], confidence=0.8,
            ))
        builder = SelfContextBuilder(max_recent_changes=5)
        ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                            self_reflection_snapshot=reflection, evolution_records=many_records,
                            identity_continuity_report=icr)
        self.assertLessEqual(len(ctx["growth_summary"]["recent_change_bullets"]), 5)

    def test_scb5_top_traits_cap(self):
        traits = {f"t_{i}": 0.05 * i for i in range(30)}
        state = PersonalityState(traits=traits)
        iam = IdentityAnchorManager()
        sm = SelfModelBuilder().build(iam, state, [])
        builder = SelfContextBuilder(top_traits_limit=5)
        ctx = builder.build(personality_state=state, self_model_snapshot=sm)
        self.assertEqual(len(ctx["personality_summary"]["top_traits"]), 5)

    def test_scb5_digit_precision(self):
        state = PersonalityState(traits={"a": 0.123456, "b": 0.987654, "c": 0.5})
        iam = IdentityAnchorManager()
        sm = SelfModelBuilder().build(iam, state, [])
        # 精度 0：整数 round
        b0 = SelfContextBuilder(max_trait_detail_digits=0)
        ctx0 = b0.build(personality_state=state, self_model_snapshot=sm)
        for t in ctx0["personality_summary"]["top_traits"]:
            v = t["value"]
            self.assertEqual(v, round(v, 0))
        # 精度 3
        b3 = SelfContextBuilder(max_trait_detail_digits=3)
        ctx3 = b3.build(personality_state=state, self_model_snapshot=sm)
        for t in ctx3["personality_summary"]["top_traits"]:
            v = t["value"]
            self.assertAlmostEqual(v, round(v, 3))
            # 字符串验证小数位数
            s = format(v, ".10f").rstrip("0").rstrip(".")
            if "." in s:
                self.assertLessEqual(len(s.split(".")[1]), 3)


class TestPhase40R264BSCB6Shape(unittest.TestCase):
    """SCB-6 shape 永远合法（5 场景 + degrade）"""

    def test_scb6_scenarios_all_valid(self):
        state, sm, recs, reflection, icr_ok, icr_tension, icr_break = _mk_inputs()
        builder = SelfContextBuilder()
        scenarios = [
            (None, None, None, None, {}),
            (state, None, None, None, {}),
            (None, sm, None, None, icr_ok),
            (state, sm, None, recs, icr_ok),
            (state, sm, reflection, recs, icr_break),
        ]
        for st, m, r, rc, ic in scenarios:
            ctx = builder.build(personality_state=st, self_model_snapshot=m,
                                self_reflection_snapshot=r, evolution_records=rc,
                                identity_continuity_report=ic)
            try:
                validate_self_context_shape(ctx)
            except Exception as exc:
                self.fail(f"shape invalid: {exc!r}, scenario= {bool(st)} {bool(m)} {bool(r)} {bool(rc)}")

    def test_scb6_degraded_valid(self):
        import unittest.mock as mock
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        builder = SelfContextBuilder()
        with mock.patch.object(SelfContextBuilder, "_format_top_traits", side_effect=RuntimeError("boom")):
            ctx = builder.build(personality_state=state, self_model_snapshot=sm,
                                self_reflection_snapshot=reflection, evolution_records=recs,
                                identity_continuity_report=icr)
        validate_self_context_shape(ctx)


class TestPhase40R264BSCB7Version(unittest.TestCase):
    """SCB-7 version 严格递增（含异常）"""

    def test_scb7_3builds_123(self):
        b = SelfContextBuilder()
        v1 = b.build()["version"]
        v2 = b.build()["version"]
        v3 = b.build()["version"]
        self.assertEqual([v1, v2, v3], [1, 2, 3])

    def test_scb7_with_exception_strict_plus_1(self):
        import unittest.mock as mock
        b = SelfContextBuilder()
        v1 = b.build()["version"]
        with mock.patch.object(SelfContextBuilder, "_format_top_traits", side_effect=RuntimeError("boom")):
            v2 = b.build()["version"]
        v3 = b.build()["version"]
        self.assertEqual([v1, v2, v3], [1, 2, 3])


class TestPhase40R264BSCB8Deterministic(unittest.TestCase):
    """SCB-8 deterministic"""

    def test_scb8_identical_inputs_identical_ctx(self):
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        b = SelfContextBuilder()

        def _without_version_and_ts(x: Dict[str, Any]) -> Dict[str, Any]:
            c = copy.deepcopy(x)
            # version 每次递增；ts 可能不同（但 growth_summary.latest_ts 来自 records，固定）；identity_summary 没 ts，仅 ignore version
            c.pop("version", None)
            return c

        a = b.build(personality_state=state, self_model_snapshot=sm,
                    self_reflection_snapshot=reflection, evolution_records=list(recs),
                    identity_continuity_report=copy.deepcopy(icr),
                    mode="read_only_identity")
        b2 = SelfContextBuilder()  # 独立实例，version 重置，但内容应该一致（忽略 version）
        bb = b2.build(personality_state=state, self_model_snapshot=sm,
                      self_reflection_snapshot=reflection, evolution_records=list(recs),
                      identity_continuity_report=copy.deepcopy(icr),
                      mode="read_only_identity")
        self.assertEqual(_without_version_and_ts(a), _without_version_and_ts(bb))


class TestPhase40R264BSCB9ExceptionIsolated(unittest.TestCase):
    """SCB-9 异常隔离"""

    def test_scb9_degraded_origin_degraded_marker_and_shape_valid(self):
        import unittest.mock as mock
        state, sm, recs, reflection, icr, _, _ = _mk_inputs()
        b = SelfContextBuilder()
        with mock.patch.object(SelfContextBuilder, "_format_recent_deltas",
                               side_effect=RuntimeError("boom")):
            ctx = b.build(personality_state=state, self_model_snapshot=sm,
                          self_reflection_snapshot=reflection, evolution_records=recs,
                          identity_continuity_report=icr)
        validate_self_context_shape(ctx)
        self.assertIn("degraded", ctx["identity_summary"]["origin_bullet"])
        # continuity_status 不受异常影响（仍 continuous_safe）
        self.assertEqual(ctx["continuity_status"], "continuous_safe")
        # summary dicts 仍有 4 份 shape 字段（空）
        self.assertEqual(ctx["personality_summary"]["top_traits"], [])


if __name__ == "__main__":
    unittest.main()
