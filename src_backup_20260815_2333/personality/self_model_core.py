"""
Self Model Core (Phase 6.1)

统一导出 Phase 6.1 新增的 SelfModel 核心结构。

设计原则：
- 不重写 self_model.py（legacy）
- 不重写 self_model_v3.py（Phase 12.2，未来）
- 仅作为"Phase 6.1 入口"做 re-export

核心结构：
- SelfIdentity: 复用 src.contracts.self_model_schema.SelfIdentity
- SelfBelief:   src.personality.self_belief
- SelfHistory:  src.personality.self_history
- SelfReflection: src.personality.self_reflection

使用示例：
    from src.personality.self_model_core import (
        SelfIdentity, SelfBelief, SelfHistory, SelfReflection,
        ALLOWED_SELF_MODEL_PATHS,
    )
"""
from __future__ import annotations

# ============================================================
# SelfIdentity: 复用 contracts.self_model_schema
# ============================================================
from src.contracts.self_model_schema import (
    SelfIdentity,
    CoreValue,
    StableTrait,
    Preference,
    BehavioralPattern,
    SelfContradiction,
    GrowthHistoryEntry,
    DevelopmentHistoryItem,
    IdentityUnderstanding,
    SelfModelChangeSuggestion,
)

# ============================================================
# SelfBelief
# ============================================================
from src.personality.self_belief import (
    SelfBelief,
    SelfBeliefStore,
    VALID_BELIEF_DOMAINS,
)

# ============================================================
# SelfHistory
# ============================================================
from src.personality.self_history import (
    SelfHistory,
    SelfHistoryEvent,
    SelfHistoryEventType,
    VALID_EVENT_TYPES,
)

# ============================================================
# SelfReflection
# ============================================================
from src.personality.self_reflection import (
    SelfReflectionNote,
    SelfReflectionStore,
    VALID_TRIGGER_SOURCES,
    VALID_REFLECTION_TYPES,
)


# ============================================================
# SelfModel Path Authority（Phase 6.1 核心）
# ============================================================

# 合法前缀（通配符 .* 后的字段名）
ALLOWED_SELF_MODEL_PATHS: frozenset = frozenset({
    "self_model.core_value",
    "self_model.stable_trait",
    "self_model.preference",
    "self_model.behavioral_pattern",
    "self_model.understanding",
})

# 禁止前缀（即使 ALLOWED 也不允许）
FORBIDDEN_SELF_MODEL_PATHS: frozenset = frozenset({
    "self_model.invalid",
    "self_model.system",
    "self_model.runtime",
    "self_model.admin",
    "self_model.bypass",
})


def validate_self_model_path(path: str) -> bool:
    """
    校验 self_model 路径是否合法。

    规则：
    1. 必须在 ALLOWED_SELF_MODEL_PATHS 前缀中
    2. 不能在 FORBIDDEN_SELF_MODEL_PATHS 前缀中
    3. 格式: self_model.<domain>.<field>[.<key>]
    """
    if not isinstance(path, str) or not path.strip():
        return False
    p = path.strip()
    # 禁止前缀优先
    for forbid in FORBIDDEN_SELF_MODEL_PATHS:
        if p.startswith(forbid + ".") or p == forbid:
            return False
    # 必须在白名单前缀
    for allowed in ALLOWED_SELF_MODEL_PATHS:
        if p.startswith(allowed + ".") or p == allowed:
            return True
    return False


__all__ = [
    # Identity
    "SelfIdentity",
    "CoreValue",
    "StableTrait",
    "Preference",
    "BehavioralPattern",
    "SelfContradiction",
    "GrowthHistoryEntry",
    "DevelopmentHistoryItem",
    "IdentityUnderstanding",
    "SelfModelChangeSuggestion",
    # Belief
    "SelfBelief",
    "SelfBeliefStore",
    "VALID_BELIEF_DOMAINS",
    # History
    "SelfHistory",
    "SelfHistoryEvent",
    "SelfHistoryEventType",
    "VALID_EVENT_TYPES",
    # Reflection
    "SelfReflectionNote",
    "SelfReflectionStore",
    "VALID_TRIGGER_SOURCES",
    "VALID_REFLECTION_TYPES",
    # Authority
    "ALLOWED_SELF_MODEL_PATHS",
    "FORBIDDEN_SELF_MODEL_PATHS",
    "validate_self_model_path",
]
