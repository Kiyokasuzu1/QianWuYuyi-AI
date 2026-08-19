from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
import uuid

from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE, PRIORITY_LEVEL


@dataclass
class GrowthProposal:
    proposal_id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    proposal_type: str = PROPOSAL_TYPE["PERSONALITY"]
    status: str = PROPOSAL_STATUS["PENDING"]

    source: str = ""
    source_event_id: str = ""
    user_id: str = ""

    affected_dimensions: Dict[str, float] = field(default_factory=dict)
    before_state: Dict[str, float] = field(default_factory=dict)
    after_state: Dict[str, float] = field(default_factory=dict)

    confidence: float = 0.0
    reason: str = ""
    evidence: List[str] = field(default_factory=list)

    priority: str = PRIORITY_LEVEL["MEDIUM"]
    reviewer_id: str = ""
    review_comment: str = ""
    reviewed_at: Optional[str] = None

    applied_at: Optional[str] = None
    applied_by: str = ""

    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "timestamp": self.timestamp,
            "proposal_type": self.proposal_type,
            "status": self.status,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "user_id": self.user_id,
            "affected_dimensions": self.affected_dimensions,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": self.evidence,
            "priority": self.priority,
            "reviewer_id": self.reviewer_id,
            "review_comment": self.review_comment,
            "reviewed_at": self.reviewed_at,
            "applied_at": self.applied_at,
            "applied_by": self.applied_by,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthProposal":
        return cls(
            proposal_id=data.get("proposal_id", ""),
            timestamp=data.get("timestamp", ""),
            proposal_type=data.get("proposal_type", PROPOSAL_TYPE["PERSONALITY"]),
            status=data.get("status", PROPOSAL_STATUS["PENDING"]),
            source=data.get("source", ""),
            source_event_id=data.get("source_event_id", ""),
            user_id=data.get("user_id", ""),
            affected_dimensions=data.get("affected_dimensions", {}),
            before_state=data.get("before_state", {}),
            after_state=data.get("after_state", {}),
            confidence=data.get("confidence", 0.0),
            reason=data.get("reason", ""),
            evidence=data.get("evidence", []),
            priority=data.get("priority", PRIORITY_LEVEL["MEDIUM"]),
            reviewer_id=data.get("reviewer_id", ""),
            review_comment=data.get("review_comment", ""),
            reviewed_at=data.get("reviewed_at"),
            applied_at=data.get("applied_at"),
            applied_by=data.get("applied_by", ""),
            expires_at=data.get("expires_at"),
            metadata=data.get("metadata", {}),
        )

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            expire_time = datetime.fromisoformat(self.expires_at)
            return datetime.now() > expire_time
        except Exception:
            return False

    def is_pending(self) -> bool:
        return self.status == PROPOSAL_STATUS["PENDING"] and not self.is_expired()

    def get_total_delta(self) -> float:
        return sum(abs(delta) for delta in self.affected_dimensions.values())