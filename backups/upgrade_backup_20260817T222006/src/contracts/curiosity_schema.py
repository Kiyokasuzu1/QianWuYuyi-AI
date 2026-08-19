"""
Phase 3.5.30: Curiosity Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class LearningGoal:
    goal_id: str = field(default_factory=lambda: f"lg_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    goal_type: str = ""   # interest_discovery / unknown_exploration / question_generation / contradiction_resolution
    topic: str = ""
    reason: str = ""
    questions: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "proposed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CuriositySnapshot:
    snapshot_id: str = field(default_factory=lambda: f"cur_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_goals: int = 0
    active_topics: List[str] = field(default_factory=list)
    last_goal_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
