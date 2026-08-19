"""
Phase 4.0 — R2.6.0: SelfModel Module

SelfModel = 对自身状态的理解层（Self Knowledge Layer）

数据流（严格单向）：
  PersonalityState → SelfModelBuilder → SelfModelSnapshot
  EvolutionRecord  → SelfModelBuilder → SelfModelSnapshot
  IdentityAnchor   → SelfModelBuilder → SelfModelSnapshot

红线：
  - SelfModel 不反向修改 PersonalityState / EvolutionRecord / IdentityAnchor
  - SelfModel 不接入 LLM / 不生成回复
  - SelfModel 不存储 emotion / relationship / raw memory
  - SelfModel 不触发 growth / approval / evolution pipeline

导出：
  - SelfModel (TypedDict)
  - SelfModelSnapshot (不可变快照类)
  - FROZEN_TOP_LEVEL_KEYS / SOURCE_MAP / FORBIDDEN_IMPORTS / FORBIDDEN_CALLS
  - validate_self_model_shape / create_empty_self_model / build_snapshot
"""
from src.self_model.self_model_schema import (
    SelfModel,
    IdentityView,
    PersonalityView,
    DevelopmentView,
    ContradictionView,
    CapabilityView,
    FROZEN_TOP_LEVEL_KEYS,
    SOURCE_MAP,
    FORBIDDEN_IMPORTS,
    FORBIDDEN_CALLS,
    DEFAULT_CONTRADICTION_THRESHOLD,
    DEFAULT_STABLE_TRAIT_THRESHOLD,
    DEFAULT_EVOLVING_TRAIT_THRESHOLD,
    DEFAULT_RECENT_CHANGES_LIMIT,
    validate_self_model_shape,
    create_empty_self_model,
)
from src.self_model.self_model_snapshot import (
    SelfModelSnapshot,
    build_snapshot,
)

__all__ = [
    "SelfModel",
    "IdentityView",
    "PersonalityView",
    "DevelopmentView",
    "ContradictionView",
    "CapabilityView",
    "SelfModelSnapshot",
    "FROZEN_TOP_LEVEL_KEYS",
    "SOURCE_MAP",
    "FORBIDDEN_IMPORTS",
    "FORBIDDEN_CALLS",
    "DEFAULT_CONTRADICTION_THRESHOLD",
    "DEFAULT_STABLE_TRAIT_THRESHOLD",
    "DEFAULT_EVOLVING_TRAIT_THRESHOLD",
    "DEFAULT_RECENT_CHANGES_LIMIT",
    "validate_self_model_shape",
    "create_empty_self_model",
    "build_snapshot",
]
