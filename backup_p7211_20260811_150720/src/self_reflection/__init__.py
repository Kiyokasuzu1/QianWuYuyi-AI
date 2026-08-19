"""
Phase 4.0 — R2.6.3: SelfReflection Module

SelfReflection = 自我解释层（不是总结器 / 不是日记）。

红线：
  1. ❌ 不修改 PersonalityState
  2. ❌ 不产生 GrowthProposal
  3. ❌ 不决定未来人格
  4. ❌ 不覆盖 IdentityAnchor
  5. ✅ 只解释已有变化（只读）
"""
from src.self_reflection.self_reflection_schema import (
    FROZEN_TOP_LEVEL_KEYS,
    OBSERVED_CHANGE_FIELDS,
    CAUSE_FIELDS,
    IDENTITY_ALIGNMENT_FIELDS,
    UNRESOLVED_TENSION_FIELDS,
    CURRENT_SELF_SUMMARY_FIELDS,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    validate_self_reflection_shape,
    create_empty_self_reflection,
)
from src.self_reflection.self_reflection_snapshot import (
    SelfReflectionSnapshot,
    build_snapshot,
)

__all__ = [
    "FROZEN_TOP_LEVEL_KEYS",
    "OBSERVED_CHANGE_FIELDS",
    "CAUSE_FIELDS",
    "IDENTITY_ALIGNMENT_FIELDS",
    "UNRESOLVED_TENSION_FIELDS",
    "CURRENT_SELF_SUMMARY_FIELDS",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_CALLS",
    "validate_self_reflection_shape",
    "create_empty_self_reflection",
    "SelfReflectionSnapshot",
    "build_snapshot",
]
