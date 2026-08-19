"""
Phase 4.0 R2.6.4-C.1 PromptContext Contract Gates (PRC-1~PRC-18)

PromptContext 是 Context Assembly 契约层（不是 Prompt 文本）。
冻结 9 顶层 + 8 子结构 + context_trace 审计字段。
红线：不 modify 任何状态 / 不产 GrowthCandidate / 不接 LLM。
"""

from __future__ import annotations

import ast
import copy
import inspect
from typing import Any, Dict, List

import pytest

from src.context.prompt_context_schema import (
    ASSEMBLY_MODE_WHITELIST,
    CONTEXT_TRACE_FIELDS,
    EMOTION_CONTEXT_FIELDS,
    EMOTION_MOOD_WHITELIST,
    FORBIDDEN_CALLS,
    FORBIDDEN_IMPORTS,
    FROZEN_PROMPT_CONTEXT_KEYS,
    MEMORY_CONTEXT_FIELDS,
    RELATIONSHIP_CONTEXT_FIELDS,
    RELATIONSHIP_TIER_WHITELIST,
    SYSTEM_IDENTITY_FIELDS,
    SYSTEM_ROLE_WHITELIST,
    TASK_CONTEXT_FIELDS,
    TASK_SCENARIO_WHITELIST,
    TRUST_LEVEL_WHITELIST,
    USER_CONTEXT_FIELDS,
    USER_CONVERSATION_ROLE_WHITELIST,
    create_empty_prompt_context,
    validate_prompt_context_shape,
)


def _valid_base() -> Dict[str, Any]:
    return create_empty_prompt_context()


# ─────────────────────────────────────────────────────────
# PRC-1: 顶层形状校验（缺字段必抛）
# ─────────────────────────────────────────────────────────
class TestPRC1ShapeMissingField:
    def test_missing_top_level_field_raises(self) -> None:
        for key in FROZEN_PROMPT_CONTEXT_KEYS:
            ctx = _valid_base()
            del ctx[key]
            with pytest.raises(ValueError, match=key):
                validate_prompt_context_shape(ctx)

    def test_not_dict_raises(self) -> None:
        with pytest.raises(ValueError, match="必须为 dict"):
            validate_prompt_context_shape(("list", "is", "wrong"))  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────
# PRC-2: 额外字段必抛
# ─────────────────────────────────────────────────────────
class TestPRC2ShapeNoExtraField:
    def test_extra_top_level_field_raises(self) -> None:
        ctx = _valid_base()
        ctx["response_decision"] = "用户喜欢猫"
        with pytest.raises(ValueError, match="未冻结额外字段"):
            validate_prompt_context_shape(ctx)

    def test_extra_sub_field_raises(self) -> None:
        ctx = _valid_base()
        ctx["system_identity"]["birthday"] = "2025-01-01"
        with pytest.raises(ValueError, match="system_identity.*未冻结字段"):
            validate_prompt_context_shape(ctx)

    def test_sub_field_keys_match_contract_exactly(self) -> None:
        ctx = _valid_base()
        for sub, fields in (
            (ctx["system_identity"], SYSTEM_IDENTITY_FIELDS),
            (ctx["user_context"], USER_CONTEXT_FIELDS),
            (ctx["memory_context"], MEMORY_CONTEXT_FIELDS),
            (ctx["emotion_context"], EMOTION_CONTEXT_FIELDS),
            (ctx["relationship_context"], RELATIONSHIP_CONTEXT_FIELDS),
            (ctx["task_context"], TASK_CONTEXT_FIELDS),
            (ctx["context_trace"], CONTEXT_TRACE_FIELDS),
        ):
            assert tuple(sub.keys()) == tuple(fields), f"子对象键顺序/数量不符合冻结契约: {sub.keys()} != {fields}"


# ─────────────────────────────────────────────────────────
# PRC-3: system_identity 枚举与类型
# ─────────────────────────────────────────────────────────
class TestPRC3SystemIdentity:
    def test_role_enum_valid(self) -> None:
        for role in SYSTEM_ROLE_WHITELIST:
            ctx = _valid_base()
            ctx["system_identity"]["system_role"] = role
            validate_prompt_context_shape(ctx)

    def test_role_enum_invalid_raises(self) -> None:
        ctx = _valid_base()
        ctx["system_identity"]["system_role"] = "god_ai"
        with pytest.raises(ValueError, match="system_role"):
            validate_prompt_context_shape(ctx)

    def test_name_empty_raises(self) -> None:
        ctx = _valid_base()
        ctx["system_identity"]["name"] = ""
        with pytest.raises(ValueError, match="name"):
            validate_prompt_context_shape(ctx)

    def test_anchor_markers_list(self) -> None:
        ctx = _valid_base()
        ctx["system_identity"]["anchor_markers"] = "不是列表"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="anchor_markers"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-4: self_context 必须是合法 SelfContext（不绕过 SC 形状校验）
# ─────────────────────────────────────────────────────────
class TestPRC4SelfContextMustBeValid:
    def test_missing_sc_injection_policy_raises(self) -> None:
        ctx = _valid_base()
        # 破坏 SelfContext 形状
        del ctx["self_context"]["injection_policy"]
        with pytest.raises(ValueError, match="缺少冻结字段"):
            validate_prompt_context_shape(ctx)

    def test_raw_personality_state_rejected(self) -> None:
        ctx = _valid_base()
        # 直接塞一个"人格状态模拟"（缺少 SC 结构），必须被 validate_self_context_shape 拒绝
        ctx["self_context"] = {
            "identity_summary": "bad_type_str",
            "personality_summary": {"top_traits": []},
        }
        with pytest.raises(ValueError):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-5: user_context 枚举
# ─────────────────────────────────────────────────────────
class TestPRC5UserContext:
    def test_conversation_role_enum_valid(self) -> None:
        for role in USER_CONVERSATION_ROLE_WHITELIST:
            ctx = _valid_base()
            ctx["user_context"]["conversation_role"] = role
            validate_prompt_context_shape(ctx)

    def test_relationship_tier_enum_invalid_raises(self) -> None:
        ctx = _valid_base()
        ctx["user_context"]["relationship_tier"] = "family"
        with pytest.raises(ValueError, match="relationship_tier"):
            validate_prompt_context_shape(ctx)

    def test_preferred_name_none_or_str(self) -> None:
        ctx = _valid_base()
        ctx["user_context"]["preferred_name"] = 123  # type: ignore[assignment]
        with pytest.raises(ValueError, match="preferred_name"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-6: memory_context 类型
# ─────────────────────────────────────────────────────────
class TestPRC6MemoryContext:
    def test_memory_count_non_negative(self) -> None:
        ctx = _valid_base()
        ctx["memory_context"]["memory_count"] = -1
        with pytest.raises(ValueError, match="memory_count"):
            validate_prompt_context_shape(ctx)

    def test_memory_count_bool_raises(self) -> None:
        ctx = _valid_base()
        ctx["memory_context"]["memory_count"] = True  # type: ignore[assignment]
        with pytest.raises(ValueError, match="memory_count"):
            validate_prompt_context_shape(ctx)

    def test_context_memories_not_str_list_raises(self) -> None:
        ctx = _valid_base()
        ctx["memory_context"]["context_memories"] = [1, 2, 3]  # type: ignore[assignment]
        with pytest.raises(ValueError, match="context_memories"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-7: emotion_context 类型 + [0,1]
# ─────────────────────────────────────────────────────────
class TestPRC7EmotionContext:
    def test_mood_enum_invalid_raises(self) -> None:
        ctx = _valid_base()
        ctx["emotion_context"]["emotion_mood"] = "angry_rage_mode"
        with pytest.raises(ValueError, match="emotion_mood"):
            validate_prompt_context_shape(ctx)

    def test_intensity_out_of_range(self) -> None:
        ctx = _valid_base()
        ctx["emotion_context"]["intensity"] = 1.5
        with pytest.raises(ValueError, match="intensity"):
            validate_prompt_context_shape(ctx)
        ctx["emotion_context"]["intensity"] = -0.1
        with pytest.raises(ValueError, match="intensity"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-8: relationship_context
# ─────────────────────────────────────────────────────────
class TestPRC8RelationshipContext:
    def test_trust_level_enum(self) -> None:
        for lvl in TRUST_LEVEL_WHITELIST:
            ctx = _valid_base()
            ctx["relationship_context"]["trust_level"] = lvl
            validate_prompt_context_shape(ctx)

    def test_closeness_out_of_range(self) -> None:
        ctx = _valid_base()
        ctx["relationship_context"]["closeness_score"] = 1.2
        with pytest.raises(ValueError, match="closeness_score"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-9: task_context
# ─────────────────────────────────────────────────────────
class TestPRC9TaskContext:
    def test_scenario_enum_valid(self) -> None:
        for s in TASK_SCENARIO_WHITELIST:
            ctx = _valid_base()
            ctx["task_context"]["scenario"] = s
            validate_prompt_context_shape(ctx)

    def test_turn_number_bool_raises(self) -> None:
        ctx = _valid_base()
        ctx["task_context"]["turn_number"] = False  # type: ignore[assignment]
        with pytest.raises(ValueError, match="turn_number"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-10: context_trace 审计结构完整
# ─────────────────────────────────────────────────────────
class TestPRC10ContextTrace:
    def test_trace_self_context_version_must_be_positive_int(self) -> None:
        ctx = _valid_base()
        ctx["context_trace"]["self_context_version"] = 0
        with pytest.raises(ValueError, match="self_context_version"):
            validate_prompt_context_shape(ctx)

    def test_trace_continuity_must_match_whitelist(self) -> None:
        ctx = _valid_base()
        ctx["context_trace"]["self_context_continuity"] = "super_broken"
        with pytest.raises(ValueError, match="self_context_continuity"):
            validate_prompt_context_shape(ctx)

    def test_trace_build_version_must_be_positive_int(self) -> None:
        ctx = _valid_base()
        ctx["context_trace"]["build_version"] = -5
        with pytest.raises(ValueError, match="build_version"):
            validate_prompt_context_shape(ctx)

    def test_trace_emotion_source_required_nonempty(self) -> None:
        ctx = _valid_base()
        ctx["context_trace"]["emotion_source"] = ""
        with pytest.raises(ValueError, match="emotion_source"):
            validate_prompt_context_shape(ctx)

    def test_assembly_mode_enum(self) -> None:
        ctx = _valid_base()
        ctx["context_trace"]["assembly_mode"] = "full_bias_mode"
        with pytest.raises(ValueError, match="assembly_mode"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-11: version 必须 >=1 int
# ─────────────────────────────────────────────────────────
class TestPRC11Version:
    def test_version_must_be_positive_int(self) -> None:
        ctx = _valid_base()
        ctx["version"] = 0
        with pytest.raises(ValueError, match="version"):
            validate_prompt_context_shape(ctx)

    def test_version_not_float(self) -> None:
        ctx = _valid_base()
        ctx["version"] = 1.5  # type: ignore[assignment]
        with pytest.raises(ValueError, match="version"):
            validate_prompt_context_shape(ctx)


# ─────────────────────────────────────────────────────────
# PRC-12: create_empty_prompt_context 默认值始终合法
# ─────────────────────────────────────────────────────────
class TestPRC12CreateEmpty:
    def test_default_passes_validation(self) -> None:
        ctx = create_empty_prompt_context()
        validate_prompt_context_shape(ctx)

    def test_custom_version_and_tag(self) -> None:
        ctx = create_empty_prompt_context(name="Yuyi-test", build_tag="r264c1", version=9, assembly_mode="identity_only")
        validate_prompt_context_shape(ctx)
        assert ctx["system_identity"]["name"] == "Yuyi-test"
        assert ctx["system_identity"]["build_tag"] == "r264c1"
        assert ctx["version"] == 9
        assert ctx["context_trace"]["assembly_mode"] == "identity_only"

    def test_empty_self_context_inside_pc(self) -> None:
        from src.context.self_context_schema import (
            validate_self_context_shape,
        )

        ctx = create_empty_prompt_context()
        validate_self_context_shape(ctx["self_context"])
        assert ctx["self_context"]["version"] == 1


# ─────────────────────────────────────────────────────────
# PRC-13: validate_prompt_context_shape 纯函数（不修改输入）
# ─────────────────────────────────────────────────────────
class TestPRC13ValidatePure:
    def test_validate_does_not_mutate_input(self) -> None:
        base = _valid_base()
        before = copy.deepcopy(base)
        validate_prompt_context_shape(base)
        assert base == before


# ─────────────────────────────────────────────────────────
# PRC-14: 版本列表顺序严格匹配冻结顺序（避免未来悄悄加字段）
# ─────────────────────────────────────────────────────────
class TestPRC14FieldOrder:
    def test_top_key_order(self) -> None:
        ctx = _valid_base()
        assert tuple(ctx.keys()) == tuple(FROZEN_PROMPT_CONTEXT_KEYS)


# ─────────────────────────────────────────────────────────
# PRC-15: 枚举白名单（所有字符串值域可穷举，不接受任意字符串）
# ─────────────────────────────────────────────────────────
class TestPRC15EnumCoverage:
    def test_all_whitelists_are_tuples_and_nonempty(self) -> None:
        lists = [
            SYSTEM_ROLE_WHITELIST,
            USER_CONVERSATION_ROLE_WHITELIST,
            RELATIONSHIP_TIER_WHITELIST,
            EMOTION_MOOD_WHITELIST,
            TRUST_LEVEL_WHITELIST,
            TASK_SCENARIO_WHITELIST,
            ASSEMBLY_MODE_WHITELIST,
        ]
        for wh in lists:
            assert isinstance(wh, tuple)
            assert len(wh) >= 1
            for item in wh:
                assert isinstance(item, str) and item


# ─────────────────────────────────────────────────────────
# PRC-16: 红线：Schema 文件禁止 import 红线模块
# ─────────────────────────────────────────────────────────
class TestPRC16ForbiddenImportsAST:
    @staticmethod
    def _ast_imports() -> List[str]:
        import src.context.prompt_context_schema as mod

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

    def test_no_forbidden_module_imported(self) -> None:
        imports = set(self._ast_imports())
        bad = [m for m in FORBIDDEN_IMPORTS if m in imports or any(str(i).startswith(m) for i in imports)]
        assert bad == [], f"Schema 中存在红线导入: {bad}"

    def test_allowed_imports_only_expect_context_family(self) -> None:
        imports = self._ast_imports()
        # 只能有 typing / self_context_schema；不能 import openai / growth / personality / emotion / memory / response
        bad = [m for m in imports if any(x in m for x in ("openai", "anthropic", "growth", "memory", "emotion", "response", "personality_state_updater"))]
        assert bad == [], f"发现越过红线模块: {bad}"


# ─────────────────────────────────────────────────────────
# PRC-17: 红线：Schema 函数体不出现 forbidden call 名（不依赖字符串 scan）
# ─────────────────────────────────────────────────────────
class TestPRC17ForbiddenCallsAST:
    @staticmethod
    def _ast_call_names() -> List[str]:
        import src.context.prompt_context_schema as mod

        tree = ast.parse(inspect.getsource(mod))
        calls: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.append(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.append(func.attr)
                # 字符串/注释不算 → AST 天然已忽略
        return calls

    def test_no_forbidden_calls(self) -> None:
        call_names = set(self._ast_call_names())
        bad = [n for n in FORBIDDEN_CALLS if n in call_names]
        assert bad == [], f"Schema 中发现红线调用名: {bad}"


# ─────────────────────────────────────────────────────────
# PRC-18: 红线：FORBIDDEN_IMPORTS / FORBIDDEN_CALLS 覆盖所有关键风险模块
# ─────────────────────────────────────────────────────────
class TestPRC18ForbiddenCoverage:
    def test_forbidden_imports_covers_risky_modules(self) -> None:
        risks = ("openai", "growth", "evolution_integration", "personality_state_updater", "memory_write", "emotion_mutator", "response_engine")
        for r in risks:
            assert any(r in m for m in FORBIDDEN_IMPORTS), f"红线缺失: {r}"

    def test_forbidden_calls_covers_risky_ops(self) -> None:
        risks = ("apply_evolution", "update_personality", "save_memory", "modify_emotion", "create_proposal", "generate_reflection", "build_prompt", "completion", "llm")
        for r in risks:
            assert r in FORBIDDEN_CALLS, f"红线缺失调用: {r}"
