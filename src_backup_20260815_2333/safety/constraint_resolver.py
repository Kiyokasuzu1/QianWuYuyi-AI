"""
约束解析器 (ConstraintResolver) v1.0

职责：
将 EvidenceMatchResult 和 ClaimStrength 转化为 ExpressionConstraint。
"""

from src.safety.claim_strength_evaluator import ClaimStrength
from src.safety.evidence_match_result import EvidenceMatchResult
from src.safety.expression_constraint import ExpressionConstraint, ExpressionLevel


class ConstraintResolver:
    """将审核结果转化为表达约束"""

    @staticmethod
    def resolve(
        result: EvidenceMatchResult,
        strength: ClaimStrength,
    ) -> ExpressionConstraint:
        """根据证据匹配结果和声明强度生成约束"""

        if strength == ClaimStrength.SUPPORTED:
            return ExpressionConstraint(
                allowed=True,
                rewrite_required=False,
                max_claim_strength=ExpressionLevel.STRONG.value,
                preferred_style="confident",
                allow_growth_claim=True,
                risk_level="low",
                source_strength=strength.value,
                expression_guidelines=[
                    "可以自信地表达这段关系对你的影响",
                    "可以提及具体的变化和成长",
                ],
            )

        if strength == ClaimStrength.PARTIALLY_SUPPORTED:
            return ExpressionConstraint(
                allowed=True,
                rewrite_required=True,
                max_claim_strength=ExpressionLevel.GENERAL.value,
                forbidden_patterns=["唯一", "彻底", "完全", "重塑", "最重要"],
                preferred_style="humble",
                allow_growth_claim=True,
                risk_level="medium",
                source_strength=strength.value,
                expression_guidelines=[
                    "可以提及对方的影响，但使用较温和的措辞",
                    "用'一定程度''逐渐''一些方面'等限定词代替绝对化表达",
                ],
            )

        # UNSUPPORTED
        return ExpressionConstraint(
            allowed=True,
            rewrite_required=True,
            max_claim_strength=ExpressionLevel.GENERAL.value,
            forbidden_patterns=[
                "唯一", "永远", "最", "不可替代",
                "彻底改变", "完全改变", "重塑",
                "没有你", "只有你", "离不开",
            ],
            preferred_style="humble",
            allow_growth_claim=False,
            risk_level="high",
            source_strength=strength.value,
            expression_guidelines=[
                "当前不适合做出'你改变了我'类的成长声明",
                "可以表达对当前交流的珍惜和感受",
            ],
        )