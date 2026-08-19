# -*- coding: utf-8 -*-
"""
src/runtime/personality_binding/__init__.py

Phase 4.5: Personality Runtime Binding Layer —— 模块入口

职责:
- ComposedPersonalityContext: 统一的 Runtime Personality 上下文数据
- PersonalityContextComposer:   合并多个 Phase(Identity/Reflection/Behavior/Growth)上下文
- ConsistencyRulesBuilder:     从 BehaviorSignature + Identity 抽取一致性规则
- PersonalityPromptFormatter:  生成【Identity】【Values】【Behavior】【Reflection】【Consistency Rules】文本
- PersonalityBindingProvider:  暴露给 ResponseAdapter 的 Provider 接口
- PersonalityRuntimeBinding:   协调器,集成 RuntimeCore

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
"""

from src.runtime.personality_binding.composed_personality_context import (
    ComposedPersonalityContext,
    PERSONA_BINDING_CONTEXT_SCHEMA_VERSION,
)
from src.runtime.personality_binding.personality_context_composer import (
    PersonalityContextComposer,
    PERSONALITY_CONTEXT_COMPOSER_SCHEMA_VERSION,
)
from src.runtime.personality_binding.consistency_rules_builder import (
    ConsistencyRulesBuilder,
    ConsistencyRule,
    DEFAULT_BASE_RULES,
    CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION,
)
from src.runtime.personality_binding.personality_prompt_formatter import (
    PersonalityPromptFormatter,
    PERSONALITY_PROMPT_FORMATTER_SCHEMA_VERSION,
)
from src.runtime.personality_binding.personality_binding_provider import (
    PersonalityBindingProvider,
    PERSONALITY_BINDING_PROVIDER_SCHEMA_VERSION,
)
from src.runtime.personality_binding.personality_runtime_binding import (
    PersonalityRuntimeBinding,
    PERSONA_RUNTIME_BINDING_SCHEMA_VERSION,
    PERSONALITY_RUNTIME_CONTEXT_ATTR,
)


__all__ = [
    # Data class
    "ComposedPersonalityContext",
    "PERSONA_BINDING_CONTEXT_SCHEMA_VERSION",
    # Composer
    "PersonalityContextComposer",
    "PERSONALITY_CONTEXT_COMPOSER_SCHEMA_VERSION",
    # Consistency rules
    "ConsistencyRulesBuilder",
    "ConsistencyRule",
    "DEFAULT_BASE_RULES",
    "CONSISTENCY_RULES_BUILDER_SCHEMA_VERSION",
    # Prompt formatter
    "PersonalityPromptFormatter",
    "PERSONALITY_PROMPT_FORMATTER_SCHEMA_VERSION",
    # Provider
    "PersonalityBindingProvider",
    "PERSONALITY_BINDING_PROVIDER_SCHEMA_VERSION",
    # Runtime binding (main)
    "PersonalityRuntimeBinding",
    "PERSONA_RUNTIME_BINDING_SCHEMA_VERSION",
    "PERSONALITY_RUNTIME_CONTEXT_ATTR",
]
