from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.storage import save_proposal, load_proposal
from src.growth.proposal.constants import (
    PROPOSAL_STATUS,
    PROPOSAL_TYPE,
    AUTO_APPROVE_THRESHOLD,
    PRIORITY_LEVEL,
    PROPOSAL_EXPIRY_HOURS,
)
from src.events.events import EventType, GrowthProposalEvent
from src.events.bus import publish_event


class ProposalReviewer:
    def __init__(self):
        pass

    def should_create_proposal(
        self,
        proposal_type: str,
        affected_dimensions: Dict[str, float],
    ) -> bool:
        for dimension, delta in affected_dimensions.items():
            threshold = AUTO_APPROVE_THRESHOLD.get(dimension, AUTO_APPROVE_THRESHOLD["personality"])
            if abs(delta) > threshold:
                return True
        return False

    def determine_priority(
        self,
        proposal_type: str,
        affected_dimensions: Dict[str, float],
    ) -> str:
        total_delta = sum(abs(d) for d in affected_dimensions.values())

        if total_delta > 0.15:
            return PRIORITY_LEVEL["HIGH"]
        elif total_delta > 0.05:
            return PRIORITY_LEVEL["MEDIUM"]
        else:
            return PRIORITY_LEVEL["LOW"]

    def create_proposal(
        self,
        proposal_type: str,
        affected_dimensions: Dict[str, float],
        before_state: Dict[str, float],
        after_state: Dict[str, float],
        source: str = "",
        source_event_id: str = "",
        user_id: str = "",
        confidence: float = 0.0,
        reason: str = "",
        evidence: List[str] = None,
    ) -> Optional[GrowthProposal]:
        if not self.should_create_proposal(proposal_type, affected_dimensions):
            return None

        # R-1.5.0: 同源去重——同类型同 source_event_id 的非终态提案已存在时
        # 复用（防与 orchestrator 直写路径双提案）
        try:
            from src.growth.proposal.storage import (
                get_proposal_storage,
                find_proposal_same_source,
            )

            if find_proposal_same_source(
                get_proposal_storage(), proposal_type, source_event_id,
            ):
                return None
        except Exception:  # noqa: BLE001
            pass

        expires_at = (datetime.now() + timedelta(hours=PROPOSAL_EXPIRY_HOURS)).isoformat()

        priority = self.determine_priority(proposal_type, affected_dimensions)

        proposal = GrowthProposal(
            proposal_type=proposal_type,
            affected_dimensions=affected_dimensions,
            before_state=before_state,
            after_state=after_state,
            source=source,
            source_event_id=source_event_id,
            user_id=user_id,
            confidence=confidence,
            reason=reason,
            evidence=evidence or [],
            priority=priority,
            expires_at=expires_at,
        )

        save_proposal(proposal)

        event = GrowthProposalEvent(
            proposal_id=proposal.proposal_id,
            proposal_type=proposal_type,
            affected_dimensions=affected_dimensions,
            confidence=confidence,
            reason=reason,
        )
        publish_event(event)

        if priority == PRIORITY_LEVEL["LOW"]:
            self.approve_proposal(proposal.proposal_id, reviewer_id="auto")

        return proposal

    def approve_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        proposal = load_proposal(proposal_id)
        if not proposal or not proposal.is_pending():
            return False

        proposal.status = PROPOSAL_STATUS["APPROVED"]
        proposal.reviewer_id = reviewer_id
        proposal.review_comment = comment
        proposal.reviewed_at = datetime.now().isoformat()

        save_proposal(proposal)

        from src.events.events import YuyiEvent
        event = YuyiEvent(
            event_type=EventType.GROWTH_PROPOSAL_APPROVED,
            source="proposal_reviewer",
            data={"proposal_id": proposal_id, "reviewer_id": reviewer_id},
        )
        publish_event(event)

        return True

    def reject_proposal(
        self,
        proposal_id: str,
        reviewer_id: str = "",
        comment: str = "",
    ) -> bool:
        proposal = load_proposal(proposal_id)
        if not proposal or not proposal.is_pending():
            return False

        proposal.status = PROPOSAL_STATUS["REJECTED"]
        proposal.reviewer_id = reviewer_id
        proposal.review_comment = comment
        proposal.reviewed_at = datetime.now().isoformat()

        save_proposal(proposal)

        from src.events.events import YuyiEvent
        event = YuyiEvent(
            event_type=EventType.GROWTH_PROPOSAL_REJECTED,
            source="proposal_reviewer",
            data={"proposal_id": proposal_id, "reviewer_id": reviewer_id, "comment": comment},
        )
        publish_event(event)

        return True

    def apply_proposal(self, proposal_id: str, applied_by: str = "") -> bool:
        proposal = load_proposal(proposal_id)
        if not proposal or proposal.status != PROPOSAL_STATUS["APPROVED"]:
            return False

        if proposal.proposal_type == PROPOSAL_TYPE["PERSONALITY"]:
            self._apply_personality_change(proposal)
        elif proposal.proposal_type == PROPOSAL_TYPE["RELATIONSHIP"]:
            self._apply_relationship_change(proposal)
        elif proposal.proposal_type == PROPOSAL_TYPE["SELF_MODEL"]:
            self._apply_self_model_change(proposal)

        proposal.status = PROPOSAL_STATUS["APPLIED"]
        proposal.applied_at = datetime.now().isoformat()
        proposal.applied_by = applied_by
        save_proposal(proposal)

        from src.events.events import YuyiEvent
        event = YuyiEvent(
            event_type=EventType.GROWTH_APPLIED,
            source="proposal_reviewer",
            data={"proposal_id": proposal_id, "applied_by": applied_by},
        )
        publish_event(event)

        return True

    def _apply_personality_change(self, proposal: GrowthProposal):
        from src.events.events import PersonalityChangedEvent
        event = PersonalityChangedEvent(
            before_state=proposal.before_state,
            after_state=proposal.after_state,
            reason=proposal.reason,
        )
        publish_event(event)

    def _apply_relationship_change(self, proposal: GrowthProposal):
        from src.events.events import RelationshipChangedEvent
        for dimension, delta in proposal.affected_dimensions.items():
            event = RelationshipChangedEvent(
                user_id=proposal.user_id,
                dimension=dimension,
                old_value=proposal.before_state.get(dimension, 0.0),
                new_value=proposal.after_state.get(dimension, 0.0),
                reason=proposal.reason,
            )
            publish_event(event)

    def _apply_self_model_change(self, proposal: GrowthProposal):
        pass


_global_reviewer = None


def get_proposal_reviewer() -> ProposalReviewer:
    global _global_reviewer
    if _global_reviewer is None:
        _global_reviewer = ProposalReviewer()
    return _global_reviewer


def create_proposal_from_event(event):
    reviewer = get_proposal_reviewer()

    if event.event_type == EventType.PERSONALITY_CHANGED:
        return reviewer.create_proposal(
            proposal_type=PROPOSAL_TYPE["PERSONALITY"],
            affected_dimensions=_extract_dimensions(event.data),
            before_state=event.data.get("before_state", {}),
            after_state=event.data.get("after_state", {}),
            source=event.source,
            source_event_id=event.event_id,
            user_id=event.data.get("user_id", ""),
            reason=event.data.get("reason", ""),
        )
    elif event.event_type == EventType.RELATIONSHIP_CHANGED:
        # B_STORE_RELATIONSHIP_MIRROR = DISABLED（2026-08-27 P0 冻结）：
        #   每次 RELATIONSHIP_CHANGED 都生成 proposal（无 delta 阈值/无聚合/无 dedup），
        #   生产已积累 178 条关系噪音提案；System C 提案层已冻结、无人消费。
        #   关系状态是 derived 层，不应进入人格治理提案流。
        #   保留 PERSONALITY_CHANGED 等真实 Growth 类型完全不变。
        return None
    return None


def _extract_dimensions(data: Dict[str, Any]) -> Dict[str, float]:
    before = data.get("before_state", {})
    after = data.get("after_state", {})

    dimensions = {}
    for key in set(before.keys()) | set(after.keys()):
        old_val = before.get(key, 0.0)
        new_val = after.get(key, 0.0)
        delta = new_val - old_val
        if abs(delta) > 0.001:
            dimensions[key] = delta
    return dimensions