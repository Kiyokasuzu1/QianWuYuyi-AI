# -*- coding: utf-8 -*-
"""
src/runtime/self_model/identity_binding/__init__.py

Phase 4.4: Self Identity Consistency Layer —— 模块入口

职责:
- IdentityContextBuilder:       从 SelfModelSnapshot 聚合运行时人格上下文
- BehaviorSignature:            行为签名数据 + Provider(从 snapshot 启发式生成)
- PersonalityConsistencyChecker: 一致性检查(identity/value/trait/behavior/safety)
- SelfIdentityRuntime:          协调器,集成 RuntimeCore

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
"""

from src.runtime.self_model.identity_binding.identity_context_builder import (
    IdentityContext,
    IdentityContextBuilder,
    IDENTITY_CONTEXT_SCHEMA_VERSION,
    MAX_RECENT_CHANGES,
    MAX_RECENT_REFLECTIONS,
    RECENT_CHANGE_KINDS,
)
from src.runtime.self_model.identity_binding.behavior_signature import (
    BehaviorPattern,
    BehaviorSignature,
    BehaviorSignatureProvider,
    BEHAVIOR_SIGNATURE_SCHEMA_VERSION,
    DEFAULT_SCENARIOS,
)
from src.runtime.self_model.identity_binding.personality_consistency_checker import (
    ConflictKind,
    ConflictDetail,
    ConsistencyResult,
    PersonalityConsistencyChecker,
    PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION,
)
from src.runtime.self_model.identity_binding.self_identity_runtime import (
    SelfIdentityRuntime,
    SELF_IDENTITY_RUNTIME_SCHEMA_VERSION,
    DEFAULT_SCENARIO,
)


__all__ = [
    # IdentityContext
    "IdentityContext",
    "IdentityContextBuilder",
    "IDENTITY_CONTEXT_SCHEMA_VERSION",
    # BehaviorSignature
    "BehaviorPattern",
    "BehaviorSignature",
    "BehaviorSignatureProvider",
    "BEHAVIOR_SIGNATURE_SCHEMA_VERSION",
    "DEFAULT_SCENARIOS",
    # ConsistencyChecker
    "ConflictKind",
    "ConflictDetail",
    "ConsistencyResult",
    "PersonalityConsistencyChecker",
    "PERSONALITY_CONSISTENCY_CHECKER_SCHEMA_VERSION",
    # SelfIdentityRuntime
    "SelfIdentityRuntime",
    "SELF_IDENTITY_RUNTIME_SCHEMA_VERSION",
    "DEFAULT_SCENARIO",
    # Shared
    "MAX_RECENT_CHANGES",
    "MAX_RECENT_REFLECTIONS",
    "RECENT_CHANGE_KINDS",
]
