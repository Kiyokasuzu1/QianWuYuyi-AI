"""
Phase 4.0 — Context 模块
- R2.6.4-A 新增 SelfContext Contract：
  SelfContext = Prompt 允许读取的自我上下文（权限过滤后的 API 视图，不是 SelfModel 原数据）
  红线 1：不直接控制回复（不拼 Prompt 不接 LLM）
  红线 2：不修改任何状态（read-only → format → return）
  红线 3：人格与自我认知分离（SelfReflection → 背景；改变人格必须走 GrowthProposal→Approval→Evolution）
- R2.6.4-C 新增 PromptContext Contract：
  PromptContext = Response Engine 消费的 Context Assembly 视图
  红线 1：不 modify 任何状态（Personality/Memory/Emotion/Relationship 只读）
  红线 2：不产 GrowthCandidate / Proposal / Reflection / Decision
  红线 3：不调用 LLM / 不拼完整 Prompt（属于 Response Engine）
"""
from src.context.time_window import TimeWindow
from src.context.context_manager import ContextManager, get_context_manager
from src.context.self_context_schema import (
    FROZEN_SELF_CONTEXT_KEYS,
    IDENTITY_SUMMARY_FIELDS,
    PERSONALITY_SUMMARY_FIELDS,
    GROWTH_SUMMARY_FIELDS,
    REFLECTION_SUMMARY_FIELDS,
    CONTINUITY_STATUS_WHITELIST,
    INJECTION_POLICY_MODES,
    INJECTION_POLICY_FIELDS,
    FORBIDDEN_IMPORTS as SC_FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS as SC_FORBIDDEN_CALLS,
    validate_self_context_shape,
    create_empty_self_context,
)
from src.context.prompt_context_schema import (
    FROZEN_PROMPT_CONTEXT_KEYS,
    SYSTEM_IDENTITY_FIELDS,
    USER_CONTEXT_FIELDS,
    MEMORY_CONTEXT_FIELDS,
    EMOTION_CONTEXT_FIELDS,
    RELATIONSHIP_CONTEXT_FIELDS,
    TASK_CONTEXT_FIELDS,
    CONTEXT_TRACE_FIELDS,
    SYSTEM_ROLE_WHITELIST,
    USER_CONVERSATION_ROLE_WHITELIST,
    RELATIONSHIP_TIER_WHITELIST,
    EMOTION_MOOD_WHITELIST,
    TRUST_LEVEL_WHITELIST,
    TASK_SCENARIO_WHITELIST,
    ASSEMBLY_MODE_WHITELIST,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_prompt_context_shape,
    create_empty_prompt_context,
)

__all__ = [
    "TimeWindow",
    "ContextManager",
    "get_context_manager",
    # SelfContext contract
    "FROZEN_SELF_CONTEXT_KEYS",
    "IDENTITY_SUMMARY_FIELDS",
    "PERSONALITY_SUMMARY_FIELDS",
    "GROWTH_SUMMARY_FIELDS",
    "REFLECTION_SUMMARY_FIELDS",
    "CONTINUITY_STATUS_WHITELIST",
    "INJECTION_POLICY_MODES",
    "INJECTION_POLICY_FIELDS",
    "SC_FORBIDDEN_IMPORTS",
    "SC_FORBIDDEN_CALLS",
    "validate_self_context_shape",
    "create_empty_self_context",
    # PromptContext contract
    "FROZEN_PROMPT_CONTEXT_KEYS",
    "SYSTEM_IDENTITY_FIELDS",
    "USER_CONTEXT_FIELDS",
    "MEMORY_CONTEXT_FIELDS",
    "EMOTION_CONTEXT_FIELDS",
    "RELATIONSHIP_CONTEXT_FIELDS",
    "TASK_CONTEXT_FIELDS",
    "CONTEXT_TRACE_FIELDS",
    "SYSTEM_ROLE_WHITELIST",
    "USER_CONVERSATION_ROLE_WHITELIST",
    "RELATIONSHIP_TIER_WHITELIST",
    "EMOTION_MOOD_WHITELIST",
    "TRUST_LEVEL_WHITELIST",
    "TASK_SCENARIO_WHITELIST",
    "ASSEMBLY_MODE_WHITELIST",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_CALLS",
    "validate_prompt_context_shape",
    "create_empty_prompt_context",
]