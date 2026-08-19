"""
声明强度评估器 (ClaimStrengthEvaluator) v2.0

职责：
根据证据匹配的评分，将声明强度映射为三个等级，
供 RelationalExpressionAuditor 做出最终决策。
"""

from enum import Enum
from src.safety.evidence_match_result import EvidenceMatchResult


class ClaimStrength(Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class ClaimStrengthEvaluator:
    """根据匹配分数评估声明强度"""

    def evaluate(self, result: EvidenceMatchResult) -> ClaimStrength:
        if result.score >= 0.7:
            return ClaimStrength.SUPPORTED
        elif result.score >= 0.4:
            return ClaimStrength.PARTIALLY_SUPPORTED
        return ClaimStrength.UNSUPPORTED