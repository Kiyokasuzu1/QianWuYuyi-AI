"""
Phase 3.5.27: Relationship Intelligence Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class RelationshipInteractionRecord:
    interaction_id: str = field(default_factory=lambda: f"rir_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    message_summary: str = ""
    event_type: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    emotion_tag: str = ""
    trust_delta: float = 0.0
    familiarity_delta: float = 0.0
    collaboration_delta: float = 0.0
    stage_after: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipEmotionalPattern:
    pattern_id: str = field(default_factory=lambda: f"rep_{uuid.uuid4().hex[:10]}")
    emotion_tag: str = ""
    count: int = 0
    last_seen: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SharedExperienceRecord:
    experience_id: str = field(default_factory=lambda: f"ser_{uuid.uuid4().hex[:10]}")
    topic: str = ""
    description: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    confidence: float = 0.0
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipMilestoneRecord:
    milestone_id: str = field(default_factory=lambda: f"rmr_{uuid.uuid4().hex[:10]}")
    milestone_type: str = ""
    title: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    evidence_ids: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RelationshipModelSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"rms_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_interactions: int = 0
    total_shared_experiences: int = 0
    total_milestones: int = 0
    total_trust_changes: int = 0
    top_emotions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
