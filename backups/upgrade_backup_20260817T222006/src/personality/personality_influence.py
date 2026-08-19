"""
人格影响记录 (PersonalityInfluence) v1.2

职责：
记录某个事件对羽依人格产生的客观影响。
这是关系真实性系统的数据基础。

设计原则：
- 只记录事实，不表达情感
- 记录“什么特质被改变了多少”
- 与 GrowthEngine 的事件系统打通

v1.2 修正：
- InfluenceType 改为 Enum，与项目风格统一
"""

from typing import Dict, List
from dataclasses import dataclass, field
from enum import Enum


class InfluenceType(Enum):
    """影响类型"""
    POSITIVE_GROWTH = "positive_growth"
    CORRECTION = "correction"
    PREFERENCE_ADJUSTMENT = "preference_adjustment"
    BOUNDARY_LEARNING = "boundary_learning"
    UNDERSTANDING_DEEPEN = "understanding_deepen"


@dataclass
class PersonalityInfluence:
    """
    一条人格影响记录。

    示例：
    事件：清清教羽依不要机械总结
    影响：communication_style 从 0.4 → 0.7
    类型：CORRECTION
    影响幅度：0.3
    可信度：0.85
    """

    influence_id: str                              # 唯一标识
    timestamp: str                                 # 影响发生时间
    source_event_id: str                           # 来源事件ID（关联 GrowthRecord）
    source_event_description: str                  # 事件描述

    # 影响的维度及变化
    affected_dimension: str                        # 被影响的人格维度
    before_value: float                            # 变化前的值
    after_value: float                             # 变化后的值
    delta: float                                   # 变化量

    # 影响性质
    influence_type: InfluenceType = InfluenceType.POSITIVE_GROWTH

    # 影响权重（变化幅度 0~1）
    impact_weight: float = 0.0

    # 可信程度（证据可靠度 0~1）
    confidence: float = 0.5

    # 证据
    evidence: List[str] = field(default_factory=list)

    def get_verified_impact(self) -> float:
        """获取经过置信度加权后的真实影响值"""
        return round(self.impact_weight * self.confidence, 4)

    def to_dict(self) -> Dict:
        return {
            "influence_id": self.influence_id,
            "timestamp": self.timestamp,
            "source_event_id": self.source_event_id,
            "source_event_description": self.source_event_description,
            "affected_dimension": self.affected_dimension,
            "before_value": self.before_value,
            "after_value": self.after_value,
            "delta": self.delta,
            "influence_type": self.influence_type.value,
            "impact_weight": self.impact_weight,
            "confidence": self.confidence,
            "verified_impact": self.get_verified_impact(),
            "evidence": self.evidence,
        }