"""
起源验证器 (OriginVerifier) — Phase 11 最终版
根据不同起源角色应用不同的验证规则。
"""
import copy
from src.identity.origin_event import OriginEvent, OriginEventStatus
from src.identity.origin_identity import OriginRole


class OriginVerifier:
    # 不同角色有不同的验证门槛
    ROLE_REQUIREMENTS = {
        OriginRole.CREATOR: {
            "min_evidence_count": 3,
            "min_confidence": 0.65,
        },
        OriginRole.PERSONALITY_DESIGNER: {
            "min_evidence_count": 2,
            "min_confidence": 0.60,
        },
        OriginRole.SYSTEM_BUILDER: {
            "min_evidence_count": 2,
            "min_confidence": 0.60,
        },
        OriginRole.GROWTH_PARTICIPANT: {
            "min_evidence_count": 3,
            "min_confidence": 0.55,
        },
    }

    # 默认门槛（未知角色使用）
    DEFAULT_REQUIREMENTS = {
        "min_evidence_count": 3,
        "min_confidence": 0.65,
    }

    def verify(self, event: OriginEvent, existing_evidence_count: int = 0) -> OriginEvent:
        """
        验证候选事件。根据不同角色的要求，判断是否通过。
        返回更新后的事件（不修改原对象）。
        """
        verified = copy.deepcopy(event)

        if not verified.potential_roles:
            verified.status = OriginEventStatus.REJECTED
            return verified

        # 取所有候选角色中最严格的要求
        max_evidence = 0
        max_confidence = 0.0
        for role in verified.potential_roles:
            reqs = self.ROLE_REQUIREMENTS.get(role, self.DEFAULT_REQUIREMENTS)
            if reqs["min_evidence_count"] > max_evidence:
                max_evidence = reqs["min_evidence_count"]
            if reqs["min_confidence"] > max_confidence:
                max_confidence = reqs["min_confidence"]

        # 证据数量检查
        total_evidence = len(verified.evidence_ids) + existing_evidence_count
        if total_evidence < max_evidence:
            verified.status = OriginEventStatus.REJECTED
            verified.confidence = max(verified.confidence, 0.1)
            return verified

        # 提升置信度（基于证据数量）
        evidence_bonus = min(0.5, total_evidence * 0.1)
        verified.confidence = min(1.0, verified.confidence + evidence_bonus)

        if verified.confidence >= max_confidence:
            verified.status = OriginEventStatus.VERIFIED
        else:
            verified.status = OriginEventStatus.REJECTED

        return verified