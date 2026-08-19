"""
Phase 3.5.29: Self Reflection Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ContradictionItem:
    contradiction_id: str = field(default_factory=lambda: f"ctr_{uuid.uuid4().hex[:10]}")
    contradiction_type: str = ""   # behavior_value / memory_conflict / personality_change / emotional_pattern
    severity: str = "low"
    description: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContradictionReport:
    report_id: str = field(default_factory=lambda: f"crp_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_items: int = 0
    highest_severity: str = "none"
    items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrendItem:
    trend_id: str = field(default_factory=lambda: f"trd_{uuid.uuid4().hex[:10]}")
    trend_type: str = ""  # interest / value / relationship / emotion / personality
    subject: str = ""
    direction: str = "stable"  # increase / decrease / stable / emerging
    strength: float = 0.0
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LongTermPatternReport:
    report_id: str = field(default_factory=lambda: f"ltr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_trends: int = 0
    interest_changes: List[Dict[str, Any]] = field(default_factory=list)
    value_changes: List[Dict[str, Any]] = field(default_factory=list)
    relationship_changes: List[Dict[str, Any]] = field(default_factory=list)
    emotion_changes: List[Dict[str, Any]] = field(default_factory=list)
    personality_changes: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SelfReflectionSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"srs_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_reflections: int = 0
    total_contradictions: int = 0
    total_trends: int = 0
    last_reflection_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
