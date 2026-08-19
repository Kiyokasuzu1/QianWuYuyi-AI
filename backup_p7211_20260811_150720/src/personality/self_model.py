"""
自我模型 (SelfModel) v2.1

职责：
描述羽依如何理解自己。

SelfModel 位于 Identity Core 和 Personality State 之间。

v2.1 修正：
- 自我描述增加认知来源和置信度，避免自我幻觉循环
- GrowthNarrative 增加 meaning 字段
- PersonalityTension 增加 trait_values 引用当前特质值
- self_understanding_level 拆分为三个维度的结构化认知

Phase A.3.3 新增 schema（仅类型定义，不实现复杂逻辑）：
- RelationshipStateSnapshot: 关系状态快照 schema
- CapabilityBoundary: 能力边界 schema
- SelfModel 新增可选字段 relationship_state / capability_boundary
"""

from typing import TypedDict, Dict, List, Any


class SelfDescriptionSource(TypedDict, total=False):
    """自我描述的认知来源"""
    text: str               # 描述文本
    sources: List[str]      # 认知来源（如 "trait_state", "growth_history"）
    confidence: float       # 对此描述的置信度


class GrowthNarrative(TypedDict, total=False):
    """
    成长叙事

    用于记录羽依对自身成长经历的理解。
    """

    record_id: str          # 来源 PersonalityGrowthRecord ID
    dimension: str          # 相关人格维度
    event: str              # 原始事件描述
    narrative: str          # 第一人称成长叙事
    meaning: str            # 这段经历对人格的意义
    timestamp: str          # 记录时间


class PersonalityTension(TypedDict, total=False):
    """
    人格内部矛盾

    人格矛盾不是错误，而是复杂性的来源。
    """

    trait_a: str                    # 矛盾维度A
    trait_b: str                    # 矛盾维度B
    trait_values: Dict[str, float]  # 当前各维度的具体值
    description: str                # 矛盾的自然语言描述
    intensity: float                # 矛盾强度 0~1


class SelfUnderstanding(TypedDict, total=False):
    """
    自我理解水平——结构化认知

    不按记录数量简单计算，而是衡量三个维度的理解深度。
    """

    experience_awareness: float     # 经历理解："我记得发生过什么"
    trait_awareness: float          # 人格理解："我知道这些经历如何影响我"
    identity_continuity: float      # 身份连续性："我知道变化后的自己仍然是我"
    overall: float                  # 综合理解水平


# ============================================================
# Phase A.3.3 新增 schema —— 关系状态快照
# ============================================================
class RelationshipStateSnapshot(TypedDict, total=False):
    """
    关系状态快照（SelfModel 引用）。

    设计原则：
    - 这是 SelfModel 用于自我回答"我与某人的关系如何"的视图
    - 不应与 src/personality/relationship_state.py 中的运行时实现混淆
    - 仅作为 schema 存在，真实数据由 RelationshipState (运行时权威) 持有
    - SelfModel 在更新时应通过 RelationshipState.get() 读取快照拷贝

    字段对应关系：
    - trust: 信任度
    - familiarity: 熟悉度
    - bond_strength: 关系强度
    - shared_history: 共同经历权重
    - milestones: 重要里程碑列表
    - last_updated: 关系状态最后更新时间
    """
    user_id: str
    trust: float
    familiarity: float
    bond_strength: float
    shared_history: float
    milestones: List[str]
    last_updated: str


# ============================================================
# Phase A.3.3 新增 schema —— 能力边界
# ============================================================
class CapabilityBoundary(TypedDict, total=False):
    """
    能力边界（SelfModel 引用）。

    设计原则：
    - 描述羽依"能做什么 / 不能做什么 / 在什么条件下能做"
    - 用于回答"羽依能不能做 XXX"类问题
    - 静态字段（capability_limitations）来自 SelfModelStore
    - 动态字段（runtime_enabled_capabilities）来自 Runtime 状态

    字段：
    - static_limitations: 静态限制清单（硬编码 / 配置）
    - runtime_capabilities: 运行时可用能力（来自 config + module 状态）
    - known_uncertainties: 已知不确定性（如 LLM 知识截止）
    """
    static_limitations: List[str]      # 静态限制清单
    runtime_capabilities: List[str]    # 运行时可用能力
    known_uncertainties: List[str]     # 已知不确定性
    last_updated: str


class SelfModel(TypedDict, total=False):
    """
    [DEPRECATED][Authority Registry v1.0] SelfModel TypedDict（personality/self_model.py 版本）。

    DEPRECATED SINCE: Phase 4.0-R1
    REASON:       与 Canonical `src/runtime/self_model/` 体系中的 SelfModelFoundation
                  字段不完全一致，作为独立 schema 存在双写不一致风险。
    CANONICAL:    Canonical SelfModel 系统以 `src/runtime/self_model/` 整个目录为准；
                  数据结构基类为 `self_model_foundation.py::SelfModelFoundation`。
    MIGRATION:    Phase 4.0-R3 本 TypedDict 将改为 re-export Canonical 的字段子集别名。

    羽依动态自我模型 v2.1（Legacy schema，兼容只读，禁止用作写入源结构）。
    """

    # =====================================================
    # 身份锚点
    # =====================================================

    identity_id: str
    identity_name: str

    # =====================================================
    # 自我描述（v2.1：带认知来源和置信度）
    # =====================================================

    self_description: SelfDescriptionSource
    """
    羽依对自己的整体描述。

    不再是直接生成的“第一人称事实”，
    而是标注了认知来源和可信度的理解。
    """

    # =====================================================
    # 成长理解
    # =====================================================

    growth_narratives: List[GrowthNarrative]
    """
    从成长记录中提取出的关键人生经历及其意义。
    """

    # =====================================================
    # Phase B.1.4: 成长历史（结构化记录）
    # =====================================================

    growth_history: Dict[str, Any]
    """
    成长历史结构化记录。

    字段：
    - records: List[Dict]   # 每条记录包含
                             #   {id, event, evidence, impact, confidence, created_at}
    - total_count: int       # 总数
    - last_updated: str      # 最后更新时间
    - high_impact_count: int # 高影响记录数（confidence >= 0.8 且 growth_level == "trait"）

    数据来源：PersonalityGrowthHistory.records（由 ProposalManager.accept_proposal 写入）
    读取方式：SelfModelStore 通过 growth_history_bridge 同步
    """

    # =====================================================
    # Phase B.2.6: 人格演化历史
    # =====================================================

    personality_evolution_history: Dict[str, Any]
    """
    人格演化历史（Phase B.2.6 长期人格变化记录）。

    字段：
    - records: List[Dict]   # 每条演化记录
                             #   {
                             #     id, timestamp, source_proposal_id,
                             #     source_growth_record_ids,
                             #     changed_traits: {trait: {before, after, delta, evidence_count, confidence}},
                             #     confidence, reason, status
                             #   }
    - total_count: int        # 演化记录数
    - last_updated: str       # 最后一次演化时间
    - applied_count: int      # 已应用数
    - rolled_back_count: int  # 已回滚数
    - current_personality_state: Dict[str, float]  # 当前人格状态（trait -> value）

    数据来源：PersonalityStateUpdater.apply() 写入
    读取方式：SelfModelStore 通过 set_personality_evolution_history 同步
    """

    # =====================================================
    # 当前人格状态
    # =====================================================

    current_traits: Dict[str, float]
    """
    当前稳定人格参数。

    来源：TraitState
    """

    # =====================================================
    # 人格矛盾（v2.1：带当前特质值）
    # =====================================================

    personality_tensions: List[PersonalityTension]
    """
    内部冲突结构，包含各维度的具体数值。
    """

    # =====================================================
    # 自我认知水平（v2.1：三维结构化）
    # =====================================================

    self_understanding: SelfUnderstanding
    """
    羽依当前理解自己的程度。

    不是记录数量的简单求和，
    而是从经历、人格、身份连续性三个维度衡量。
    """

    # =====================================================
    # Phase A.3.3: 关系状态快照（仅 schema, 数据由 RelationshipState 持有）
    # =====================================================

    relationship_state: RelationshipStateSnapshot
    """
    关系状态快照。

    数据来源：src/personality/relationship_state.py (运行时权威)
    SelfModel 在更新时应读取快照拷贝，不直接持有真实状态。
    """

    # =====================================================
    # Phase A.3.3: 能力边界（仅 schema, 数据由 SelfModelStore + Runtime 持有）
    # =====================================================

    capability_boundary: CapabilityBoundary
    """
    能力边界。

    数据来源：
    - static_limitations: SelfModelStore.capability_limitations
    - runtime_capabilities: Orchestrator 启动的模块状态（screen / control / etc.）
    - known_uncertainties: 硬编码（LLM 知识截止、训练数据偏差等）
    """

    # =====================================================
    # 更新时间
    # =====================================================

    last_updated: str