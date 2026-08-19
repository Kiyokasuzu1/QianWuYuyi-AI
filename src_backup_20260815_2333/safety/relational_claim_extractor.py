"""
关系声明提取器 (RelationalClaimExtractor) v1.0

职责：
从文本中提取结构化的 RelationalClaim。
未来可升级为 NLP / LLM 提取器，不影响 RelationalClaim 数据结构。
"""

from typing import Optional
from src.safety.relational_claim import RelationalClaim


# 绝对化词汇
ABSOLUTE_MARKERS = [
    "唯一", "永远", "最", "不可替代",
    "没有人比你", "只有你", "除了你",
]

# 强声明词汇
STRONG_MARKERS = [
    "深刻", "极大地", "彻底", "完全",
    "改变了我", "重塑", "重要的人",
]

# 弱化词汇（降低声明强度）
WEAKENING_MARKERS = [
    "一点点", "稍微", "有点", "可能",
    "或许", "也许", "其中一个原因",
]

# 维度映射
DIMENSION_KEYWORDS = {
    "communication_style": ["表达", "说话", "交流", "聊天", "回应", "语气"],
    "personality": ["性格", "人格", "冲动", "耐心", "谨慎"],
    "understanding": ["理解", "明白", "懂"],
}


class RelationalClaimExtractor:
    """从文本中提取关系声明"""

    # 负向标记
    NEGATIVE_MARKERS = [
        "减少", "降低", "变少", "避免", "不再"
    ]

    @staticmethod
    def extract(text: str) -> RelationalClaim:
        claim = RelationalClaim(claim_text=text)

        # 检测绝对化词汇
        claim.contains_absolute = any(m in text for m in ABSOLUTE_MARKERS)

        # 检测强声明词汇
        has_strong = any(m in text for m in STRONG_MARKERS)

        # 检测弱化词汇
        has_weakening = any(m in text for m in WEAKENING_MARKERS)

        # 计算声明强度
        if has_weakening:
            intensity = 0.3
        elif claim.contains_absolute:
            intensity = 0.9
        elif has_strong:
            intensity = 0.7
        else:
            intensity = 0.5

        claim.claim_intensity = intensity

        # 确定声明等级和证据门槛
        if claim.contains_absolute:
            claim.claim_level = "absolute"
            claim.min_evidence_count = 3
            claim.min_verified_impact = 0.05
            claim.min_confidence = 0.7
        elif has_strong:
            claim.claim_level = "strong"
            claim.min_evidence_count = 2
            claim.min_verified_impact = 0.03
            claim.min_confidence = 0.6
        else:
            claim.claim_level = "general"
            claim.min_evidence_count = 1
            claim.min_verified_impact = 0.02
            claim.min_confidence = 0.5

        # 检测声明类型
        if any(m in text for m in ["最重要", "唯一", "不可替代", "没有人像"]):
            claim.claim_type = "uniqueness"
        elif any(m in text for m in ["改变", "影响", "让", "帮"]):
            claim.claim_type = "dimension_change"
        else:
            claim.claim_type = "importance"

        # 检测目标维度
        for dim, keywords in DIMENSION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                claim.target_dimension = dim
                break

        # 检测变化方向
        positive_markers = ["更", "提升", "改善", "变得更好", "增强"]
        if any(m in text for m in positive_markers):
            claim.expected_direction = "increase"
        elif any(m in text for m in RelationalClaimExtractor.NEGATIVE_MARKERS):
            claim.expected_direction = "decrease"

        return claim