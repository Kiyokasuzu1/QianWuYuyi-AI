"""
Phase 3.5.26: Personality Stability System Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class PersonalityStabilityIssue:
    issue_id: str = field(default_factory=lambda: f"psi_{uuid.uuid4().hex[:10]}")
    issue_type: str = ""   # core_value_drift / trait_drift / contradiction_risk / identity_instability
    severity: str = "low"
    description: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PersonalityStabilityReport:
    report_id: str = field(default_factory=lambda: f"psr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    identity_id: str = ""
    drift_score: float = 0.0
    stability_score: float = 1.0
    is_stable: bool = True
    issues: List[Dict[str, Any]] = field(default_factory=list)
    core_value_summary: Dict[str, Any] = field(default_factory=dict)
    contradiction_summary: Dict[str, Any] = field(default_factory=dict)
    trait_summary: Dict[str, Any] = field(default_factory=dict)
    identity_stability_link: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
