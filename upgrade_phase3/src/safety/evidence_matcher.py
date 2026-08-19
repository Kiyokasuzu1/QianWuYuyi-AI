"""
证据匹配器 (EvidenceMatcher) v2.0

职责：
将 RelationalClaim 与 RelationshipProfile 进行综合评分匹配。
包含方向匹配、质量优先评分、uniqueness 特殊处理。
"""

from typing import List
from src.safety.relational_claim import RelationalClaim
from src.safety.evidence_match_result import EvidenceMatchResult
from src.relationship.relationship_profile import RelationshipProfile
from src.personality.personality_influence import PersonalityInfluence


class EvidenceMatcher:
    """综合评分匹配器"""

    def match(
        self,
        claim: RelationalClaim,
        profile: RelationshipProfile,
    ) -> EvidenceMatchResult:
        # 防御空数据
        if profile is None or not hasattr(profile, 'influences'):
            return EvidenceMatchResult(
                matched=False,
                explanation="无关系历史数据",
            )

        # uniqueness 类型特殊处理
        if claim.claim_type == "uniqueness":
            return self._match_uniqueness(claim, profile)

        # 按维度筛选
        candidates = profile.influences if claim.target_dimension is None else [
            i for i in profile.influences
            if i.affected_dimension == claim.target_dimension
        ]

        if not candidates:
            return EvidenceMatchResult(
                matched=False,
                explanation=f"未找到相关影响记录{'（维度: ' + claim.target_dimension + '）' if claim.target_dimension else ''}",
            )

        # 方向匹配筛选
        direction_qualified = [
            i for i in candidates
            if self._check_direction(i, claim)
        ]

        # 筛选满足最低门槛的证据
        qualified = [
            i for i in direction_qualified
            if i.get_verified_impact() >= claim.min_verified_impact
            and i.confidence >= claim.min_confidence
        ]

        if len(qualified) < claim.min_evidence_count:
            return EvidenceMatchResult(
                matched=False,
                score=len(qualified) / max(claim.min_evidence_count, 1) * 0.3,
                evidences=qualified,
                matched_dimension=claim.target_dimension or "",
                explanation=f"证据不足：需要至少 {claim.min_evidence_count} 条满足门槛的记录，实际 {len(qualified)} 条",
            )

        # 综合评分：质量优先，数量作为轻微加成
        total_verified = sum(i.get_verified_impact() for i in qualified)
        avg_confidence = sum(i.confidence for i in qualified) / len(qualified)
        quality_score = min(total_verified / max(claim.claim_intensity, 0.1), 1.0) * 0.5
        confidence_score = avg_confidence * 0.3
        quantity_score = min(len(qualified) / claim.min_evidence_count * 0.1, 0.1)

        score = quality_score + confidence_score + quantity_score
        matched = score >= 0.4

        dimension_display = claim.target_dimension or "多个维度"
        explanation = (
            f"找到 {len(qualified)} 条匹配记录（维度: {dimension_display}），"
            f"综合评分: {round(score, 3)}"
        )

        return EvidenceMatchResult(
            matched=matched,
            score=score,
            evidences=qualified,
            matched_dimension=claim.target_dimension or "",
            explanation=explanation,
        )

    def _check_direction(self, influence: PersonalityInfluence, claim: RelationalClaim) -> bool:
        """检查影响方向是否与声明一致"""
        if claim.expected_direction is None:
            return True
        if claim.expected_direction == "increase":
            return influence.delta > 0
        if claim.expected_direction == "decrease":
            return influence.delta < 0
        return True

    def _match_uniqueness(self, claim: RelationalClaim, profile: RelationshipProfile) -> EvidenceMatchResult:
        """uniqueness 声明需要更严格的证据"""
        if len(profile.unique_dimensions) < 2:
            return EvidenceMatchResult(
                matched=False,
                explanation="唯一性声明需要至少影响两个不同人格维度，实际不足",
            )

        # 需要高可信度的多维度影响
        high_confidence = [
            i for i in profile.influences
            if i.confidence >= 0.7 and i.get_verified_impact() >= 0.03
        ]

        unique_dimensions = set(i.affected_dimension for i in high_confidence)

        if len(unique_dimensions) >= 2 and len(high_confidence) >= 3:
            return EvidenceMatchResult(
                matched=True,
                score=0.8,
                evidences=high_confidence,
                matched_dimension="多个维度",
                explanation=f"唯一性声明被 {len(high_confidence)} 条高可信度多维度记录支持",
            )
        elif len(high_confidence) >= 2:
            return EvidenceMatchResult(
                matched=True,
                score=0.5,
                evidences=high_confidence,
                matched_dimension="多个维度",
                explanation="唯一性声明部分被支持，但证据强度不足",
            )
        return EvidenceMatchResult(
            matched=False,
            explanation=f"唯一性声明需要更多高可信度证据，当前仅 {len(high_confidence)} 条",
        )