# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/__init__.py

Phase 4.3: Self Reflection Layer —— 模块入口
Phase 4.7: Self Model Reflection & Consistency Validation Layer
          (扩展 SelfModelReflectionEngine / ContradictionDetector / ConsistencyChecker)

职责:
- 基于 GrowthAuditRecord 生成 evidence-based 的 ReflectionRecord (Phase 4.3)
- 保存长期反思历史 (Phase 4.3)
- 为未来 Runtime 回复提供可选的 self reflection context (Phase 4.3)
- SelfModel 一致性验证 (Phase 4.7)
- 字段冲突检测 (Phase 4.7)
- 一致性检查报告 (Phase 4.7)

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
- reflection 不依赖下层持久化模块 (Phase 4.6) / identity_binding
- 下层持久化模块 (Phase 4.6) 不依赖 reflection
- evolution 不依赖 reflection
"""

from src.runtime.self_model.reflection.reflection_record import (
    ReflectionRecord,
    REFLECTION_RECORD_SCHEMA_VERSION,
    ReflectionPriority,
    ReflectionKind,
    # Phase 4.7
    ReflectionType,
    ConflictType,
)
from src.runtime.self_model.reflection.reflection_store import (
    ReflectionStore,
    REFLECTION_STORE_SCHEMA_VERSION,
    DEFAULT_REFLECTION_STORE_LIMIT,
)
from src.runtime.self_model.reflection.reflection_engine import (
    ReflectionEngine,
    REFLECTION_ENGINE_SCHEMA_VERSION,
    # Phase 4.7
    SelfModelReflectionEngine,
    SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION,
)
from src.runtime.self_model.reflection.reflection_context_provider import (
    SelfReflectionContextProvider,
    SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION,
    DEFAULT_MAX_RECENT_REFLECTIONS,
)
from src.runtime.self_model.reflection.contradiction_detector import (
    ContradictionDetector,
    ContradictionRecord,
    CONTRADICTION_DETECTOR_SCHEMA_VERSION,
    DEFAULT_VALUE_CONFLICT_THRESHOLD,
    DEFAULT_TRAIT_CONFLICT_THRESHOLD,
    DEFAULT_PREFERENCE_OPPOSITE_THRESHOLD,
    DEFAULT_BEHAVIOR_CONFLICT_THRESHOLD,
    IMMUTABLE_IDENTITY_FIELDS,
)
from src.runtime.self_model.reflection.consistency_checker import (
    ConsistencyChecker,
    ConsistencyReport,
    CONSISTENCY_CHECKER_SCHEMA_VERSION,
    DEFAULT_CONSISTENCY_THRESHOLD,
    DEFAULT_DRIFT_WINDOW,
    DEFAULT_DRIFT_COUNT_THRESHOLD,
    DEFAULT_TRAIT_DRIFT_THRESHOLD,
    DEFAULT_VALUE_DRIFT_THRESHOLD,
    VALID_SCHEMA_VERSIONS,
)


__all__ = [
    # 数据
    "ReflectionRecord",
    "REFLECTION_RECORD_SCHEMA_VERSION",
    "ReflectionPriority",
    "ReflectionKind",
    # Store
    "ReflectionStore",
    "REFLECTION_STORE_SCHEMA_VERSION",
    "DEFAULT_REFLECTION_STORE_LIMIT",
    # Engine (Phase 4.3 audit-driven)
    "ReflectionEngine",
    "REFLECTION_ENGINE_SCHEMA_VERSION",
    # Provider
    "SelfReflectionContextProvider",
    "SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION",
    "DEFAULT_MAX_RECENT_REFLECTIONS",
    # Phase 4.7: 一致性验证
    "ReflectionType",
    "ConflictType",
    "SelfModelReflectionEngine",
    "SELF_MODEL_REFLECTION_ENGINE_SCHEMA_VERSION",
    "ContradictionDetector",
    "ContradictionRecord",
    "CONTRADICTION_DETECTOR_SCHEMA_VERSION",
    "DEFAULT_VALUE_CONFLICT_THRESHOLD",
    "DEFAULT_TRAIT_CONFLICT_THRESHOLD",
    "DEFAULT_PREFERENCE_OPPOSITE_THRESHOLD",
    "DEFAULT_BEHAVIOR_CONFLICT_THRESHOLD",
    "IMMUTABLE_IDENTITY_FIELDS",
    "ConsistencyChecker",
    "ConsistencyReport",
    "CONSISTENCY_CHECKER_SCHEMA_VERSION",
    "DEFAULT_CONSISTENCY_THRESHOLD",
    "DEFAULT_DRIFT_WINDOW",
    "DEFAULT_DRIFT_COUNT_THRESHOLD",
    "DEFAULT_TRAIT_DRIFT_THRESHOLD",
    "DEFAULT_VALUE_DRIFT_THRESHOLD",
    "VALID_SCHEMA_VERSIONS",
]
