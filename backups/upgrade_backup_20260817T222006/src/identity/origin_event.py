"""
起源事件 (OriginEvent)
记录一次可能形成起源身份的历史事件。
"""
from dataclasses import dataclass, field
from typing import List
from datetime import datetime


class OriginEventStatus:
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass
class OriginEvent:
    event_id: str = ""
    event_type: str = ""
    description: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    potential_roles: List[str] = field(default_factory=list)
    user_id: str = ""
    confidence: float = 0.0
    status: str = OriginEventStatus.CANDIDATE
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "description": self.description,
            "evidence_ids": self.evidence_ids,
            "potential_roles": self.potential_roles,
            "user_id": self.user_id,
            "confidence": self.confidence,
            "status": self.status,
            "timestamp": self.timestamp,
        }