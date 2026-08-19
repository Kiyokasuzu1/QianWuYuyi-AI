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
"""

from typing import TypedDict, Dict, List


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

    experience_awareness: float     # 经历理解：“我记得发生过什么”
    trait_awareness: float          # 人格理解：“我知道这些经历如何影响我”
    identity_continuity: float      # 身份连续性：“我知道变化后的自己仍然是我”
    overall: float                  # 综合理解水平


class SelfModel(TypedDict, total=False):
    """
    羽依动态自我模型 v2.1
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
    # 更新时间
    # =====================================================

    last_updated: str