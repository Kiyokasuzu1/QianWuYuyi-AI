"""
Phase 4.0 — R2.6.0: SelfModel Schema（自我模型数据契约冻结）

定位：
  SelfModel = 对自身状态的理解层（Self Knowledge Layer）
  SelfModel ≠ Personality（不修改 traits）
  SelfModel ≠ Memory（不存储记忆）
  SelfModel ≠ Persona（不修改出生设定）
  SelfModel ≠ Emotion / Relationship（那些有自己的系统，SelfModel 只引用）

数据流（严格单向）：
  PersonalityState → SelfModelBuilder → SelfModelSnapshot
  EvolutionRecord  → SelfModelBuilder → SelfModelSnapshot
  IdentityAnchor   → SelfModelBuilder → SelfModelSnapshot

  ❌ SelfModel 不反向修改 PersonalityState / EvolutionRecord / IdentityAnchor
  ❌ SelfModel 不接入 LLM / 不生成回复
  ❌ SelfModel 不存储 emotion / relationship / raw memory

5 个 View + 2 个元字段（共 7 个顶层 key，R2.6.0 冻结）：
  1) identity_view       — 我是谁？（origin + core_values + stable_identity_markers）
  2) personality_view    — 我现在是什么状态？（stable_traits + evolving_traits + recent_changes）
  3) development_view    — 我经历过什么变化？（evolution_history + important_turning_points）
  4) contradiction_view  — 我有哪些矛盾？（detected_tensions）
  5) capability_view     — 我能做什么？（known_strengths + limitations）
  6) version             — SelfModel 版本（每次 rebuild 递增）
  7) generated_at        — 生成时间戳

来源映射表（SOURCE_MAP）：
  每个 view 字段标注数据来源模块，便于审计和 Gates 验证。
  SelfModel 只从以下模块**读取**：
    - src.personality.personality_state.PersonalityState
    - src.personality.evolution_record.EvolutionRecord
    - src.personality.identity_anchor.IdentityAnchorManager

禁止依赖列表（FORBIDDEN_IMPORTS）：
  SelfModel 模块不得 import 以下模块（AST 扫描验证）：
    - src.personality.personality_adapter（不调 adapter）
    - src.personality.trait_state（不直接改 trait）
    - src.growth.growth_integration（不触发 growth pipeline）
    - src.approval.approval_manager（不触发 approval）
    - src.relationship.*（不直接读关系数据）
    - src.emotion.*（不直接读情绪数据）
    - src.memory.*（不直接读记忆数据）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TypedDict
from datetime import datetime, timezone


# ============================================================
# R2.6.0 冻结：SelfModel 顶层字段（7 个，不多不少）
# ============================================================
FROZEN_TOP_LEVEL_KEYS: Tuple[str, ...] = (
    "identity_view",
    "personality_view",
    "development_view",
    "contradiction_view",
    "capability_view",
    "version",
    "generated_at",
)


# ============================================================
# View 1: identity_view — 我是谁？
# ============================================================
class IdentityView(TypedDict, total=False):
    origin: str                           # 来源描述（如 "由清夏铃创造"）
    core_values: List[str]                # 核心价值观（从 IdentityAnchor 提取）
    stable_identity_markers: List[str]    # 稳定身份标记（不变的锚点名称）


# ============================================================
# View 2: personality_view — 我现在是什么状态？
# ============================================================
class PersonalityView(TypedDict, total=False):
    stable_traits: Dict[str, float]       # 稳定 trait（变化幅度 < 阈值）
    evolving_traits: Dict[str, float]     # 正在演化的 trait（近期有变化）
    recent_changes: List[Dict[str, Any]]  # 最近的变化摘要（从 EvolutionRecord 提取）


# ============================================================
# View 3: development_view — 我经历过什么变化？
# ============================================================
class DevelopmentView(TypedDict, total=False):
    evolution_history: List[Dict[str, Any]]       # 演化历史摘要
    important_turning_points: List[Dict[str, Any]]  # 重要转折点


# ============================================================
# View 4: contradiction_view — 我有哪些矛盾？
# ============================================================
class ContradictionView(TypedDict, total=False):
    detected_tensions: List[Dict[str, Any]]  # 检测到的张力（不消解，只标记）


# ============================================================
# View 5: capability_view — 我能做什么？
# ============================================================
class CapabilityView(TypedDict, total=False):
    known_strengths: List[str]    # 已知优势
    limitations: List[str]        # 已知局限


# ============================================================
# SelfModel 完整结构
# ============================================================
class SelfModel(TypedDict, total=False):
    identity_view: IdentityView
    personality_view: PersonalityView
    development_view: DevelopmentView
    contradiction_view: ContradictionView
    capability_view: CapabilityView
    version: int
    generated_at: str


# ============================================================
# 来源映射表：每个 view 的数据来源
# ============================================================
SOURCE_MAP: Dict[str, Tuple[str, ...]] = {
    "identity_view": (
        "src.personality.identity_anchor.IdentityAnchorManager",
        "src.personality.identity_anchor.load_default_anchors",
    ),
    "personality_view": (
        "src.personality.personality_state.PersonalityState",
        "src.personality.evolution_record.EvolutionRecord",
    ),
    "development_view": (
        "src.personality.evolution_record.EvolutionRecord",
        "src.personality.personality_state.PersonalityState",
    ),
    "contradiction_view": (
        "src.personality.personality_state.PersonalityState",
    ),
    "capability_view": (
        "src.personality.personality_state.PersonalityState",
        "src.personality.identity_anchor.IdentityAnchorManager",
    ),
}


# ============================================================
# 禁止依赖列表：SelfModel 模块不得 import 这些
# ============================================================
FORBIDDEN_IMPORTS: Tuple[str, ...] = (
    # 不反向修改人格
    "src.personality.personality_adapter",
    "src.personality.trait_state",
    # 不触发 growth / approval / evolution
    "src.growth.growth_integration",
    "src.approval.approval_manager",
    "src.personality.evolution_pipeline",
    # 不直接读关系/情绪/记忆（那些有自己的系统，SelfModel 只引用不依赖）
    "src.relationship.relationship_memory",
    "src.relationship.relationship_event",
    "src.emotion",
    "src.memory",
)

# 禁止调用的函数/方法名（AST 扫描检查 call 节点）
FORBIDDEN_CALLS: Tuple[str, ...] = (
    "apply_evolution",        # 不直接改 PersonalityState
    "accept_proposal",
    "apply_proposal",
    "govern_proposal",        # 不触发 approval
    "accept_experience",      # 不触发 growth
    "execute",                # 不触发 evolution pipeline
    "set_trait",              # 不直接设 trait
    "update_traits",
)


# ============================================================
# 矛盾检测阈值（默认值，可在 builder 构造时覆盖）
# ============================================================
DEFAULT_CONTRADICTION_THRESHOLD: float = 0.15   # 两个 trait 值差 >= 0.15 且语义对立时标记
DEFAULT_STABLE_TRAIT_THRESHOLD: float = 0.05     # 近期变化幅度 < 此值 → stable
DEFAULT_EVOLVING_TRAIT_THRESHOLD: float = 0.02   # 近期有变化 >= 此值 → evolving
DEFAULT_RECENT_CHANGES_LIMIT: int = 10           # recent_changes 最多保留条数


# ============================================================
# 验证函数：检查一个 dict 是否符合 SelfModel 冻结形状
# ============================================================
def validate_self_model_shape(model: Dict[str, Any]) -> None:
    """
    验证 SelfModel 的顶层字段是否符合冻结形状。
    不验证 view 内部结构（view 内部允许渐进演化）。

    Raises:
        ValueError: 如果顶层字段不匹配冻结集合
    """
    if not isinstance(model, dict):
        raise ValueError(f"SelfModel 必须是 dict, got {type(model)}")

    missing = [k for k in FROZEN_TOP_LEVEL_KEYS if k not in model]
    if missing:
        raise ValueError(f"SelfModel 缺少冻结字段: {missing}")

    extra = [k for k in model.keys() if k not in FROZEN_TOP_LEVEL_KEYS]
    if extra:
        raise ValueError(f"SelfModel 有未登记的顶层字段: {extra}")

    # version 必须是 int >= 0
    if not isinstance(model["version"], int) or model["version"] < 0:
        raise ValueError(f"version 必须是非负整数, got {model['version']!r}")

    # generated_at 必须是字符串
    if not isinstance(model["generated_at"], str) or not model["generated_at"].strip():
        raise ValueError(f"generated_at 必须是非空字符串, got {model['generated_at']!r}")

    # 每个 view 必须是 dict
    for view_key in ("identity_view", "personality_view", "development_view",
                      "contradiction_view", "capability_view"):
        v = model[view_key]
        if not isinstance(v, dict):
            raise ValueError(f"{view_key} 必须是 dict, got {type(v)}")


# ============================================================
# 构建空 SelfModel（用于初始化或测试）
# ============================================================
def create_empty_self_model() -> SelfModel:
    """创建一个空的 SelfModel（所有 view 为空 dict，version=0）。"""
    return SelfModel(
        identity_view={},
        personality_view={},
        development_view={},
        contradiction_view={},
        capability_view={},
        version=0,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
