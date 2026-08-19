"""
Phase 4.0 — R2.6.4-A Gates: SelfContext Contract Freeze

Gates (SC-1 ~ SC-18)：
  SC-1  create_empty_self_context 返回 shape 通过 validate
  SC-2  缺 7 冻结字段任意 1 个 → ValueError
  SC-3  新增未登记字段 → ValueError（Contract 阶段禁止扩展）
  SC-4  identity_summary 缺字段 / 类型错误 → ValueError
  SC-5  personality_summary 缺字段 / top_traits.level 非法 / direction 非法 → ValueError
  SC-6  growth_summary 缺字段 / 类型错误 → ValueError
  SC-7  reflection_summary 缺字段 / 类型错误 → ValueError
  SC-8  continuity_status 不在白名单 → ValueError
  SC-9  injection_policy.mode 不在白名单 → ValueError
  SC-10 injection_policy.allow_list / deny_list 含非法 token → ValueError
  SC-11 version < 1 → ValueError；self_model_version/personality_state_version 负数 → 0 被 create_empty 回退
  SC-12 injection_policy.max_trait_detail_digits > 6 → ValueError；负数 → ValueError
  SC-13 AST 扫 self_context_schema.py：无 FORBIDDEN_IMPORTS / FORBIDDEN_CALLS（红线 1/2/3）
  SC-14 合法完整构造（4 份 summary 都填写）→ validate 通过
  SC-15 合同不 import src.self_reflection.self_reflection_builder（SelfContext 与 Builder 隔离）
  SC-16 合同不 import src.personality.personality_state_updater（红线 2）
  SC-17 injection_policy 缺字段 → ValueError
  SC-18 core_values_bullets 或 trait_anchors_present 为空列表时，仍然 validate 通过（允许降级场景）

"""
from __future__ import annotations

import ast
import inspect
import copy
import unittest
from typing import Any, Dict

from src.context import (
    FROZEN_SELF_CONTEXT_KEYS,
    CONTINUITY_STATUS_WHITELIST,
    INJECTION_POLICY_MODES,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_context_shape,
    create_empty_self_context,
)


# ============================================================
# helper：构造一份合法完整 SelfContext
# ============================================================
def _mk_legal() -> Dict[str, Any]:
    ctx = create_empty_self_context(version=3, self_model_version=7, personality_state_version=11, mode="read_only_identity")
    # 填各字段为合法值（不通过 Builder，直接手工填以验证 Contract 层 shape 校验）
    ctx["identity_summary"].update({
        "origin_bullet": "[与创造者的关系] 清夏铃给予的是开始，而未来由经历共同形成",
        "core_values_bullets": ["honesty", "growth", "autonomy", "empathy", "kindness"],
        "trait_anchors_present": ["anchor_creator", "anchor_core_values", "anchor_growth"],
        "self_model_version": 7,
    })
    ctx["personality_summary"].update({
        "top_traits": [
            {"trait": "creativity", "level": "high", "value": 0.72},
            {"trait": "curiosity", "level": "high", "value": 0.80},
            {"trait": "empathy", "level": "medium", "value": 0.65},
        ],
        "stable_traits": ["creativity", "autonomy", "connection_value"],
        "trait_deltas_recent": [
            {"trait": "creativity", "delta": 0.12, "direction": "up"},
            {"trait": "empathy", "delta": 0.10, "direction": "up"},
        ],
        "personality_state_version": 11,
    })
    ctx["growth_summary"].update({
        "recent_change_bullets": ["creativity +0.12 (trait_delta)", "empathy +0.10 (interest_transition)"],
        "recent_interest_transitions": ["int_tr_01"],
        "evolutions_count_last_30": 2,
        "latest_evolution_at": "2026-08-08T08:00:00+00:00",
    })
    ctx["reflection_summary"].update({
        "observed_change_bullets": ["creativity: 增强 0.120（trait_delta）", "empathy: 增强 0.100（interest_transition）"],
        "unresolved_tension_titles": ["探索欲与稳定需求之间的张力"],
        "has_identity_tension": False,
        "reflection_id": "ref_abc123",
    })
    ctx["continuity_status"] = "continuous_with_tension"
    ctx["injection_policy"].update({
        "mode": "read_only_identity",
        "allow_list": [
            "identity_summary:origin_bullet",
            "identity_summary:core_values",
            "personality_summary:top_traits",
            "personality_summary:stable_traits",
            "growth_summary:bullets",
            "reflection_summary:observed",
            "reflection_summary:tensions",
            "continuity_status",
        ],
        "deny_list": [
            "growth_summary:timestamp",
            "reflection_summary:has_tension",
        ],
        "max_recent_changes": 5,
        "max_trait_detail_digits": 3,
    })
    return ctx


class TestPhase40R264ASC1Empty(unittest.TestCase):
    """SC-1 空对象通过 validate"""

    def test_sc1_create_empty_validate_ok(self):
        ctx = create_empty_self_context(version=1)
        validate_self_context_shape(ctx)
        self.assertEqual(ctx["version"], 1)
        self.assertEqual(ctx["continuity_status"], "continuous_safe")


class TestPhase40R264ASC2Missing(unittest.TestCase):
    """SC-2 缺冻结字段"""

    def test_sc2_missing_any_frozen_field_raises(self):
        for k in FROZEN_SELF_CONTEXT_KEYS:
            ctx = _mk_legal()
            del ctx[k]
            with self.assertRaises(ValueError, msg=f"删除 {k} 应 raise"):
                validate_self_context_shape(ctx)


class TestPhase40R264ASC3Extra(unittest.TestCase):
    """SC-3 扩展字段拒绝"""

    def test_sc3_extra_key_raises(self):
        ctx = _mk_legal()
        ctx["new_shiny_field_42"] = "whatever"
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC4Identity(unittest.TestCase):
    """SC-4 identity_summary 类型/字段"""

    def test_sc4_missing_field(self):
        ctx = _mk_legal()
        del ctx["identity_summary"]["trait_anchors_present"]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc4_bad_type(self):
        ctx = _mk_legal()
        ctx["identity_summary"]["origin_bullet"] = 123  # 不是 str
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc4_self_model_version_negative_raises(self):
        ctx = _mk_legal()
        ctx["identity_summary"]["self_model_version"] = -1
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC5Personality(unittest.TestCase):
    """SC-5 personality_summary 字段/枚举"""

    def test_sc5_top_traits_level_illegal(self):
        ctx = _mk_legal()
        ctx["personality_summary"]["top_traits"][0]["level"] = "extreme"
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc5_direction_illegal(self):
        ctx = _mk_legal()
        ctx["personality_summary"]["trait_deltas_recent"][0]["direction"] = "bigjump"
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc5_stable_traits_non_str_entry(self):
        ctx = _mk_legal()
        ctx["personality_summary"]["stable_traits"] = ["a", 123]  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC6Growth(unittest.TestCase):
    """SC-6 growth_summary"""

    def test_sc6_negative_count(self):
        ctx = _mk_legal()
        ctx["growth_summary"]["evolutions_count_last_30"] = -5
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc6_bad_latest_at_type(self):
        ctx = _mk_legal()
        ctx["growth_summary"]["latest_evolution_at"] = None  # type: ignore[typeddict-item]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC7Reflection(unittest.TestCase):
    """SC-7 reflection_summary"""

    def test_sc7_has_tension_not_bool(self):
        ctx = _mk_legal()
        ctx["reflection_summary"]["has_identity_tension"] = "yes"  # type: ignore[typeddict-item]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc7_reflection_id_empty(self):
        ctx = _mk_legal()
        ctx["reflection_summary"]["reflection_id"] = "   "
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC8Continuity(unittest.TestCase):
    """SC-8 continuity_status 白名单"""

    def test_sc8_whitelist_members_all_ok(self):
        for st in CONTINUITY_STATUS_WHITELIST:
            ctx = _mk_legal()
            ctx["continuity_status"] = st
            validate_self_context_shape(ctx)

    def test_sc8_invalid_raises(self):
        ctx = _mk_legal()
        ctx["continuity_status"] = "random_break"
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC9PolicyMode(unittest.TestCase):
    """SC-9 policy mode 白名单"""

    def test_sc9_mode_members_all_ok(self):
        for m in INJECTION_POLICY_MODES:
            ctx = _mk_legal()
            ctx["injection_policy"]["mode"] = m
            validate_self_context_shape(ctx)

    def test_sc9_invalid_raises(self):
        ctx = _mk_legal()
        ctx["injection_policy"]["mode"] = "full_control"
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC10PolicyToken(unittest.TestCase):
    """SC-10 allow/deny list token 白名单"""

    def test_sc10_illegal_token_raises(self):
        ctx = _mk_legal()
        ctx["injection_policy"]["allow_list"] = ["identity_summary:secret_field_x"]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc10_illegal_deny_token_raises(self):
        ctx = _mk_legal()
        ctx["injection_policy"]["deny_list"] = ["reflection_summary:solution_fragments"]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC11Versions(unittest.TestCase):
    """SC-11 version 规则"""

    def test_sc11_version_zero_raises(self):
        ctx = _mk_legal()
        ctx["version"] = 0
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc11_create_empty_negative_sm_version_fallback(self):
        ctx = create_empty_self_context(version=1, self_model_version=-5, personality_state_version=-2)
        # 回退到 0（validate 通过）
        validate_self_context_shape(ctx)
        self.assertEqual(ctx["identity_summary"]["self_model_version"], 0)
        self.assertEqual(ctx["personality_summary"]["personality_state_version"], 0)


class TestPhase40R264ASC12DigitsCap(unittest.TestCase):
    """SC-12 max_trait_detail_digits 范围限制"""

    def test_sc12_digits_seven_raises(self):
        ctx = _mk_legal()
        ctx["injection_policy"]["max_trait_detail_digits"] = 7
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc12_digits_negative_raises(self):
        ctx = _mk_legal()
        ctx["injection_policy"]["max_trait_detail_digits"] = -1
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC13ForbiddenAST(unittest.TestCase):
    """SC-13 AST 扫 self_context_schema"""

    @staticmethod
    def _clean_src() -> str:
        import src.context.self_context_schema as mod
        tree = ast.parse(inspect.getsource(mod))

        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_sc13_no_forbidden_imports(self):
        s = self._clean_src()
        for bad in FORBIDDEN_IMPORTS:
            self.assertNotIn(bad, s, f"不应 import {bad}")

    def test_sc13_no_forbidden_calls(self):
        s = self._clean_src()
        for bad in FORBIDDEN_CALLS:
            self.assertNotIn(f"{bad}(", s, f"不应调用 {bad}()")


class TestPhase40R264ASC14LegalFull(unittest.TestCase):
    """SC-14 完整合法对象通过 validate"""

    def test_sc14_full_legal_passes(self):
        ctx = _mk_legal()
        validate_self_context_shape(ctx)  # 不 raise 即可


class TestPhase40R264ASC15Separation(unittest.TestCase):
    """SC-15 / SC-16: SelfContext Schema 不直接 import 执行模块（红线 1/2/3）
    用 AST Import / ImportFrom 节点扫描；避免注释/字符串误匹配。
    """

    @staticmethod
    def _ast_imports() -> List[str]:
        import src.context.self_context_schema as mod
        tree = ast.parse(inspect.getsource(mod))
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def test_sc15_no_builder_import(self):
        imps = self._ast_imports()
        for i in imps:
            self.assertNotIn("self_reflection_builder", i, f"不应 import {i}")

    def test_sc16_no_state_updater_import(self):
        imps = self._ast_imports()
        bad_tokens = (
            "personality_state_updater",
            "growth_integration",
            "prompt_builder",
            "response.llm",
            "response.engine",
            "openai",
        )
        for i in imps:
            for bt in bad_tokens:
                self.assertNotIn(bt, i, f"不应 import {i}")


class TestPhase40R264ASC17PolicyMissingField(unittest.TestCase):
    """SC-17 policy 缺字段"""

    def test_sc17_missing_mode(self):
        ctx = _mk_legal()
        del ctx["injection_policy"]["mode"]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)

    def test_sc17_missing_max_recent(self):
        ctx = _mk_legal()
        del ctx["injection_policy"]["max_recent_changes"]
        with self.assertRaises(ValueError):
            validate_self_context_shape(ctx)


class TestPhase40R264ASC18EmptyListsOk(unittest.TestCase):
    """SC-18 空列表仍通过（Contract 允许降级）"""

    def test_sc18_empty_core_values_and_anchors_ok(self):
        ctx = create_empty_self_context(version=1)
        ctx["identity_summary"]["core_values_bullets"] = []
        ctx["identity_summary"]["trait_anchors_present"] = []
        ctx["growth_summary"]["recent_change_bullets"] = []
        ctx["growth_summary"]["recent_interest_transitions"] = []
        ctx["reflection_summary"]["observed_change_bullets"] = []
        ctx["reflection_summary"]["unresolved_tension_titles"] = []
        ctx["injection_policy"]["allow_list"] = []
        ctx["injection_policy"]["deny_list"] = []
        # 空列表不是类型错误，应该通过
        validate_self_context_shape(ctx)


if __name__ == "__main__":
    unittest.main()
