from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
import uuid


@dataclass
class YuyiEvent:
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    event_type: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "data": self.data,
            "metadata": self.metadata,
        }


class EventType:
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_RESPONDED = "message.responded"

    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"

    PERSONALITY_CHANGED = "personality.changed"
    TRAIT_UPDATED = "trait.updated"

    EMOTION_CHANGED = "emotion.changed"

    RELATIONSHIP_CHANGED = "relationship.changed"
    RELATIONSHIP_LEVEL_UPDATED = "relationship.level_updated"

    GROWTH_EVENT_DETECTED = "growth.event_detected"
    GROWTH_PROPOSAL_CREATED = "growth.proposal_created"
    GROWTH_PROPOSAL_APPROVED = "growth.proposal_approved"
    GROWTH_PROPOSAL_REJECTED = "growth.proposal_rejected"
    GROWTH_APPLIED = "growth.applied"

    AUDIT_LOG_RECORDED = "audit.log_recorded"


@dataclass
class MessageReceivedEvent(YuyiEvent):
    event_type: str = EventType.MESSAGE_RECEIVED
    user_id: str = ""
    content: str = ""


@dataclass
class MessageRespondedEvent(YuyiEvent):
    event_type: str = EventType.MESSAGE_RESPONDED
    user_id: str = ""
    content: str = ""
    response_time_ms: int = 0


@dataclass
class MemoryCreatedEvent(YuyiEvent):
    event_type: str = EventType.MEMORY_CREATED
    memory_id: str = ""
    user_id: str = ""
    content: str = ""


@dataclass
class PersonalityChangedEvent(YuyiEvent):
    event_type: str = EventType.PERSONALITY_CHANGED
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class RelationshipChangedEvent(YuyiEvent):
    event_type: str = EventType.RELATIONSHIP_CHANGED
    user_id: str = ""
    dimension: str = ""
    old_value: float = 0.0
    new_value: float = 0.0
    reason: str = ""


@dataclass
class GrowthProposalEvent(YuyiEvent):
    event_type: str = EventType.GROWTH_PROPOSAL_CREATED
    proposal_id: str = ""
    proposal_type: str = ""
    affected_dimensions: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""