"""
Phase 3.5.31: Creativity Schema
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class CreativeDirection:
    direction_id: str = field(default_factory=lambda: f"cd_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    theme: str = ""
    source_type: str = ""  # association / emotion / identity / curiosity
    summary: str = ""
    stimulus: str = ""
    associations: List[str] = field(default_factory=list)
    suggested_explorations: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "proposed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CreativitySnapshot:
    snapshot_id: str = field(default_factory=lambda: f"crt_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_directions: int = 0
    active_themes: List[str] = field(default_factory=list)
    last_direction_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
