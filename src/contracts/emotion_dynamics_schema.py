"""
Phase 3.5.28: Emotion Dynamics Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class EmotionTransitionRecord:
    transition_id: str = field(default_factory=lambda: f"etr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    event_type: str = ""
    source: str = ""
    memory_id: str = ""
    dominant_emotion: str = ""
    mood_after: str = "neutral"
    state_before: Dict[str, Any] = field(default_factory=dict)
    state_after: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EmotionalMemorySummary:
    summary_id: str = field(default_factory=lambda: f"ems_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_traces: int = 0
    dominant_emotions: List[Dict[str, Any]] = field(default_factory=list)
    recent_memory_ids: List[str] = field(default_factory=list)
    patterns: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EmotionDynamicsSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"eds_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_transitions: int = 0
    persistent_mood: str = "neutral"
    mood_streak: int = 0
    last_event_type: str = ""
    emotional_memory_total: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
