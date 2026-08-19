"""
Phase 3.5.25: Long Term Memory Architecture Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ConsolidatedMemory:
    memory_id: str = field(default_factory=lambda: f"ltm_{uuid.uuid4().hex[:10]}")
    memory_type: str = ""   # episodic / semantic / identity / relationship / emotional
    canonical_key: str = ""
    content: str = ""
    source_ids: List[str] = field(default_factory=list)
    truth: float = 0.0
    reinforcement_count: int = 1
    decay_score: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryConflictRecord:
    conflict_id: str = field(default_factory=lambda: f"mcf_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    conflict_type: str = ""    # preference_conflict / identity_conflict / semantic_conflict
    subject: str = ""
    candidate_ids: List[str] = field(default_factory=list)
    chosen_id: str = ""
    resolution_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryConsolidationReport:
    report_id: str = field(default_factory=lambda: f"mcr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    episodic_memories: List[Dict[str, Any]] = field(default_factory=list)
    semantic_memories: List[Dict[str, Any]] = field(default_factory=list)
    identity_memories: List[Dict[str, Any]] = field(default_factory=list)
    relationship_memories: List[Dict[str, Any]] = field(default_factory=list)
    emotional_memories: List[Dict[str, Any]] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LongTermMemorySnapshot:
    snapshot_id: str = field(default_factory=lambda: f"ltms_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_reports: int = 0
    total_conflicts: int = 0
    last_report_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
