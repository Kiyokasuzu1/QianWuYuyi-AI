"""
证据匹配结果 (EvidenceMatchResult) v1.0

职责：
封装证据匹配的结构化结果，供 ClaimStrengthEvaluator 和 Auditor 使用。
"""

from dataclasses import dataclass, field
from typing import List
from src.personality.personality_influence import PersonalityInfluence


@dataclass
class EvidenceMatchResult:
    """证据匹配的结构化结果"""

    matched: bool                                               # 是否匹配成功
    score: float = 0.0                                          # 综合评分 0~1
    evidences: List[PersonalityInfluence] = field(default_factory=list)  # 匹配到的影响记录
    matched_dimension: str = ""                                 # 匹配的人格维度
    explanation: str = ""                                       # 可解释的匹配说明

    def to_dict(self) -> dict:
        return {
            "matched": self.matched,
            "score": round(self.score, 3),
            "matched_dimension": self.matched_dimension,
            "evidence_count": len(self.evidences),
            "explanation": self.explanation,
        }