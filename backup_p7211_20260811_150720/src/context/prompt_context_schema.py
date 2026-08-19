"""
Phase 4.0 — R2.6.4-C PromptContext Contract Frozen Schema

PromptContext 是 Prompt Builder 生成回复时消费的 "Context Assembly View"。
它本身不决定说什么，只负责把 6 份上下文按冻结结构装配成可审计对象。

冻结契约：
1. 顶层 9 字段（顺序、类型、含义都冻结）：
   system_identity | self_context | user_context | memory_context |
   emotion_context | relationship_context | task_context | context_trace | version
2. self_context 必须是通过 validate_self_context_shape 的合法 SelfContext（不允许
   直接塞 PersonalityState / EvolutionRecord / GrowthProposal 等内部对象）。
3. PromptContext 只做 "读取 → 合并 → 格式化 → 返回"，红线：
   - 不修改任何状态（Personality / Memory / Emotion / Relationship 只读）
   - 不产生 GrowthCandidate / Proposal / Reflection / Decision
   - 不调用 LLM / 拼接完整 Prompt（那属于 Response Engine）
   - 不决定人格 / 不写入 Memory / 不 apply_evolution
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.context.self_context_schema import validate_self_context_shape

# ─────────────────────────────────────────────────────────
# 1. 冻结顶层键
# ─────────────────────────────────────────────────────────
FROZEN_PROMPT_CONTEXT_KEYS: Tuple[str, ...] = (
    "system_identity",
    "self_context",
    "user_context",
    "memory_context",
    "emotion_context",
    "relationship_context",
    "task_context",
    "context_trace",
    "version",
)

# Phase 7.2.1.1-identity-stabilization:
#   允许在冻结键之外额外出现的"可选扩展键"白名单。
#   用于向后兼容（RuntimeController 会塞 origin_identity / turn_cfg / user_name）。
#   注意：这里的键不会变成 required，即使不出现在 dict 里也 OK。
ALLOWED_OPTIONAL_EXTRA_KEYS: Tuple[str, ...] = (
    "origin_identity",  # 新增：起源身份摘要（OriginFacade.get_user_identity 返回）
    "turn_cfg",         # 新增：RuntimeController 把整份 turn_cfg 透传给 Renderer
    "user_id",          # 新增：直接传 user_id（无 turn_cfg 场景也可用）
    "user_name",        # 新增：直接传 user_name（无 turn_cfg 场景也可用）
)

SYSTEM_IDENTITY_FIELDS: Tuple[str, ...] = (
    "name",
    "system_role",
    "anchor_markers",
    "build_tag",
)
USER_CONTEXT_FIELDS: Tuple[str, ...] = (
    "user_id",
    "display_name",
    "conversation_role",
    "relationship_tier",
    "preferred_name",
)
MEMORY_CONTEXT_FIELDS: Tuple[str, ...] = (
    "context_memories",
    "memory_count",
    "recall_scope",
)
EMOTION_CONTEXT_FIELDS: Tuple[str, ...] = (
    "emotion_mood",
    "intensity",
    "context_note",
)
RELATIONSHIP_CONTEXT_FIELDS: Tuple[str, ...] = (
    "closeness_score",
    "dynamic_traits",
    "trust_level",
)
TASK_CONTEXT_FIELDS: Tuple[str, ...] = (
    "turn_number",
    "session_id",
    "current_topic",
    "scenario",
)
CONTEXT_TRACE_FIELDS: Tuple[str, ...] = (
    "self_context_version",
    "self_context_continuity",
    "self_context_injection_mode",
    "memory_count",
    "memory_scope",
    "emotion_source",
    "relationship_trust_level",
    "build_version",
    "assembly_mode",
)

# ─────────────────────────────────────────────────────────
# 2. 白名单枚举（字符串值域锁死）
# ─────────────────────────────────────────────────────────
SYSTEM_ROLE_WHITELIST: Tuple[str, ...] = (
    "companion_ai",
    "assistant_ai",
)
USER_CONVERSATION_ROLE_WHITELIST: Tuple[str, ...] = (
    "user",
    "system",
    "observer",
)
RELATIONSHIP_TIER_WHITELIST: Tuple[str, ...] = (
    "stranger",
    "acquaintance",
    "friend",
    "confidant",
    "unknown",
)
EMOTION_MOOD_WHITELIST: Tuple[str, ...] = (
    "neutral",
    "calm",
    "happy",
    "excited",
    "thoughtful",
    "sad",
    "worried",
    "curious",
    "warm",
)
TRUST_LEVEL_WHITELIST: Tuple[str, ...] = (
    "low",
    "medium",
    "high",
    "unknown",
)
TASK_SCENARIO_WHITELIST: Tuple[str, ...] = (
    "casual_chat",
    "question_answer",
    "creative_collaboration",
    "emotional_support",
    "planning",
    "unknown",
)
ASSEMBLY_MODE_WHITELIST: Tuple[str, ...] = (
    "standard",
    "minimal",
    "identity_only",
    "degraded",
)

# ─────────────────────────────────────────────────────────
# 3. 红线：PromptContext 永不接触的模块与调用
# ─────────────────────────────────────────────────────────
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    "openai",
    "anthropic",
    "src.personality.personality_state_updater",
    "src.growth.eligibility_checker",
    "src.growth.growth_evaluator",
    "src.growth.approval_engine",
    "src.growth.evolution_integration",
    "src.cognition.self_reflection_builder",
    "src.cognition.reflection_engine",
    "src.context.self_context_builder",  # Schema 自己不 import Builder；Builder 才组装
    "src.memory.memory_write",
    "src.emotion.emotion_mutator",
    "src.emotion.emotion_engine",
    "src.response.response_engine",
)

FORBIDDEN_CALLS: Tuple[str, ...] = (
    # Prompt 不能修改人格/成长/记忆/情绪/关系
    "apply_evolution",
    "update_personality",
    "commit_personality",
    "save_memory",
    "write_memory",
    "modify_emotion",
    "update_emotion",
    "set_emotion",
    "update_relationship",
    # Prompt 不能产出 Growth / Reflection 信号
    "create_proposal",
    "build_growth_candidate",
    "generate_proposal",
    "evaluate_proposal",
    "generate_reflection",
    "build_reflection",
    # Prompt 不拼 completion / 不做 reply 决策
    "build_prompt",
    "generate",
    "completion",
    "chat",
    "respond",
    "create_response",
    "llm",
)


# ─────────────────────────────────────────────────────────
# 4. 子结构校验工具
# ─────────────────────────────────────────────────────────
def _require_dict(value: Any, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"PromptContext 字段 {field_name} 必须为 dict/Mapping，实际 {type(value).__name__}")


def _require_subfields(value: Mapping[str, Any], field_name: str, required_fields: Iterable[str]) -> None:
    missing = [k for k in required_fields if k not in value]
    if missing:
        raise ValueError(f"PromptContext 子对象 {field_name} 缺少字段: {missing}")
    extra = [k for k in value.keys() if k not in tuple(required_fields)]
    if extra:
        raise ValueError(f"PromptContext 子对象 {field_name} 存在未冻结字段: {extra}")


def _require_enum(value: Any, field_name: str, whitelist: Iterable[str]) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise ValueError(f"PromptContext 字段 {field_name} 必须为 str，实际 {type(value).__name__}")
    if value not in tuple(whitelist):
        raise ValueError(f"PromptContext 字段 {field_name}={value!r} 不在白名单 {tuple(whitelist)}")


def _require_unit(value: Any, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)):
        raise ValueError(f"PromptContext 字段 {field_name} 必须为数值，实际 {type(value).__name__}")
    if isinstance(value, bool):
        raise ValueError(f"PromptContext 字段 {field_name} 不能为 bool")
    if not (0.0 <= float(value) <= 1.0):
        raise ValueError(f"PromptContext 字段 {field_name}={value} 必须在 [0,1] 区间")


# ─────────────────────────────────────────────────────────
# 5. 顶层 shape 校验
# ─────────────────────────────────────────────────────────
def validate_prompt_context_shape(ctx: Dict[str, Any]) -> None:
    """校验 PromptContext 冻结形状。抛出 ValueError 描述首个冲突。"""
    if not isinstance(ctx, dict):
        raise ValueError(f"PromptContext 必须为 dict，实际 {type(ctx).__name__}")
    missing = [k for k in FROZEN_PROMPT_CONTEXT_KEYS if k not in ctx]
    if missing:
        raise ValueError(f"PromptContext 缺少冻结字段: {missing}")
    # Phase 7.2.1.1: 允许白名单里的"可选扩展键"通过（不变成 required）
    extra = [
        k for k in ctx.keys()
        if k not in FROZEN_PROMPT_CONTEXT_KEYS and k not in ALLOWED_OPTIONAL_EXTRA_KEYS
    ]
    if extra:
        raise ValueError(f"PromptContext 存在未冻结额外字段: {extra}")

    # 1) system_identity
    si = ctx["system_identity"]
    _require_dict(si, "system_identity")
    _require_subfields(si, "system_identity", SYSTEM_IDENTITY_FIELDS)
    if not isinstance(si["name"], str) or not si["name"]:
        raise ValueError("system_identity.name 必须为非空 str")
    _require_enum(si["system_role"], "system_identity.system_role", SYSTEM_ROLE_WHITELIST)
    if not isinstance(si["anchor_markers"], list) or not all(isinstance(x, str) for x in si["anchor_markers"]):
        raise ValueError("system_identity.anchor_markers 必须为 list[str]")
    if not isinstance(si["build_tag"], str) or not si["build_tag"]:
        raise ValueError("system_identity.build_tag 必须为非空 str")

    # 2) self_context（必须是 SelfContext 合法快照）
    sc = ctx["self_context"]
    validate_self_context_shape(sc)

    # 3) user_context
    uc = ctx["user_context"]
    _require_dict(uc, "user_context")
    _require_subfields(uc, "user_context", USER_CONTEXT_FIELDS)
    if uc["user_id"] is not None and not isinstance(uc["user_id"], str):
        raise ValueError("user_context.user_id 必须为 str 或 None")
    if uc["display_name"] is not None and not isinstance(uc["display_name"], str):
        raise ValueError("user_context.display_name 必须为 str 或 None")
    _require_enum(uc["conversation_role"], "user_context.conversation_role", USER_CONVERSATION_ROLE_WHITELIST)
    _require_enum(uc["relationship_tier"], "user_context.relationship_tier", RELATIONSHIP_TIER_WHITELIST)
    if uc["preferred_name"] is not None and not isinstance(uc["preferred_name"], str):
        raise ValueError("user_context.preferred_name 必须为 str 或 None")

    # 4) memory_context
    mc = ctx["memory_context"]
    _require_dict(mc, "memory_context")
    _require_subfields(mc, "memory_context", MEMORY_CONTEXT_FIELDS)
    if not isinstance(mc["context_memories"], list) or not all(isinstance(x, str) for x in mc["context_memories"]):
        raise ValueError("memory_context.context_memories 必须为 list[str]")
    if not isinstance(mc["memory_count"], int) or isinstance(mc["memory_count"], bool) or mc["memory_count"] < 0:
        raise ValueError("memory_context.memory_count 必须为 >=0 int")
    if mc["recall_scope"] is not None and not isinstance(mc["recall_scope"], str):
        raise ValueError("memory_context.recall_scope 必须为 str 或 None")

    # 5) emotion_context
    ec = ctx["emotion_context"]
    _require_dict(ec, "emotion_context")
    _require_subfields(ec, "emotion_context", EMOTION_CONTEXT_FIELDS)
    _require_enum(ec["emotion_mood"], "emotion_context.emotion_mood", EMOTION_MOOD_WHITELIST)
    _require_unit(ec["intensity"], "emotion_context.intensity")
    if ec["context_note"] is not None and not isinstance(ec["context_note"], str):
        raise ValueError("emotion_context.context_note 必须为 str 或 None")

    # 6) relationship_context
    rc = ctx["relationship_context"]
    _require_dict(rc, "relationship_context")
    _require_subfields(rc, "relationship_context", RELATIONSHIP_CONTEXT_FIELDS)
    _require_unit(rc["closeness_score"], "relationship_context.closeness_score")
    if not isinstance(rc["dynamic_traits"], list) or not all(isinstance(x, str) for x in rc["dynamic_traits"]):
        raise ValueError("relationship_context.dynamic_traits 必须为 list[str]")
    _require_enum(rc["trust_level"], "relationship_context.trust_level", TRUST_LEVEL_WHITELIST)

    # 7) task_context
    tc = ctx["task_context"]
    _require_dict(tc, "task_context")
    _require_subfields(tc, "task_context", TASK_CONTEXT_FIELDS)
    if not isinstance(tc["turn_number"], int) or isinstance(tc["turn_number"], bool) or tc["turn_number"] < 0:
        raise ValueError("task_context.turn_number 必须为 >=0 int")
    if tc["session_id"] is not None and not isinstance(tc["session_id"], str):
        raise ValueError("task_context.session_id 必须为 str 或 None")
    if tc["current_topic"] is not None and not isinstance(tc["current_topic"], str):
        raise ValueError("task_context.current_topic 必须为 str 或 None")
    _require_enum(tc["scenario"], "task_context.scenario", TASK_SCENARIO_WHITELIST)

    # 8) context_trace（审计轨迹）
    ct = ctx["context_trace"]
    _require_dict(ct, "context_trace")
    _require_subfields(ct, "context_trace", CONTEXT_TRACE_FIELDS)
    if not isinstance(ct["self_context_version"], int) or isinstance(ct["self_context_version"], bool) or ct["self_context_version"] < 1:
        raise ValueError("context_trace.self_context_version 必须为 >=1 int")
    _require_enum(ct["self_context_continuity"], "context_trace.self_context_continuity", ("continuous_safe", "continuous_with_tension", "tension_warning", "identity_break"))
    if ct["self_context_injection_mode"] is not None and ct["self_context_injection_mode"] not in ("minimal", "summary_only", "read_only_identity"):
        raise ValueError(f"context_trace.self_context_injection_mode={ct['self_context_injection_mode']!r} 不在白名单")
    if not isinstance(ct["memory_count"], int) or isinstance(ct["memory_count"], bool) or ct["memory_count"] < 0:
        raise ValueError("context_trace.memory_count 必须为 >=0 int")
    if ct["memory_scope"] is not None and not isinstance(ct["memory_scope"], str):
        raise ValueError("context_trace.memory_scope 必须为 str 或 None")
    if not isinstance(ct["emotion_source"], str) or not ct["emotion_source"]:
        raise ValueError("context_trace.emotion_source 必须为非空 str")
    _require_enum(ct["relationship_trust_level"], "context_trace.relationship_trust_level", TRUST_LEVEL_WHITELIST)
    if not isinstance(ct["build_version"], int) or isinstance(ct["build_version"], bool) or ct["build_version"] < 1:
        raise ValueError("context_trace.build_version 必须为 >=1 int")
    _require_enum(ct["assembly_mode"], "context_trace.assembly_mode", ASSEMBLY_MODE_WHITELIST)

    # 9) version
    v = ctx["version"]
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise ValueError(f"PromptContext.version 必须为 >=1 int，实际 {v!r}")


# ─────────────────────────────────────────────────────────
# 6. 空构造：degraded / minimal 场景可复用
# ─────────────────────────────────────────────────────────
def create_empty_prompt_context(
    *,
    name: str = "羽依",
    build_tag: str = "phase40-r264c1",
    version: int = 1,
    assembly_mode: str = "standard",
) -> Dict[str, Any]:
    """返回 shape 合法的空 PromptContext（默认值安全）。

    说明：self_context 也必须合法，所以内部复用 create_empty_self_context(version=1)。
    """
    from src.context.self_context_schema import create_empty_self_context

    empty_self = create_empty_self_context(version=1)
    ctx: Dict[str, Any] = {
        "system_identity": {
            "name": name,
            "system_role": "companion_ai",
            "anchor_markers": [],
            "build_tag": build_tag,
        },
        "self_context": empty_self,
        "user_context": {
            "user_id": None,
            "display_name": None,
            "conversation_role": "user",
            "relationship_tier": "unknown",
            "preferred_name": None,
        },
        "memory_context": {
            "context_memories": [],
            "memory_count": 0,
            "recall_scope": None,
        },
        "emotion_context": {
            "emotion_mood": "neutral",
            "intensity": 0.5,
            "context_note": None,
        },
        "relationship_context": {
            "closeness_score": 0.0,
            "dynamic_traits": [],
            "trust_level": "unknown",
        },
        "task_context": {
            "turn_number": 0,
            "session_id": None,
            "current_topic": None,
            "scenario": "unknown",
        },
        "context_trace": {
            "self_context_version": empty_self["version"],
            "self_context_continuity": empty_self["continuity_status"],
            "self_context_injection_mode": empty_self["injection_policy"]["mode"],
            "memory_count": 0,
            "memory_scope": None,
            "emotion_source": "emotion_state:default_neutral",
            "relationship_trust_level": "unknown",
            "build_version": version,
            "assembly_mode": assembly_mode,
        },
        "version": version,
    }
    validate_prompt_context_shape(ctx)
    return ctx
