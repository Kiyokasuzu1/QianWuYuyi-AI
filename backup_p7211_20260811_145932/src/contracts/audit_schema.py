"""Contracts: Audit schema (dataclasses)
Audit entries record before/after snapshots (or digests), source event ids and evidence memory ids.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class AuditEntry:
    id: str = field(default_factory=lambda: f"audit_{uuid.uuid4().hex[:12]}")
    component: str = ""  # e.g., 'personality', 'emotion', 'relationship', 'growth', 'runtime'
    actor: Optional[str] = None  # module or user who caused the change
    reason: Optional[str] = None
    source_event_id: Optional[str] = None
    evidence_memory_ids: List[str] = field(default_factory=list)
    before: Optional[Any] = None  # optional digest or snapshot (kept generic)
    after: Optional[Any] = None
    timestamp: str = field(default_factory=now_iso)
    version: Optional[str] = None  # schema/version tag

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEntry":
        return cls(
            id=data.get("id") or f"audit_{uuid.uuid4().hex[:12]}",
            component=data.get("component", ""),
            actor=data.get("actor"),
            reason=data.get("reason"),
            source_event_id=data.get("source_event_id"),
            evidence_memory_ids=data.get("evidence_memory_ids", []),
            before=data.get("before"),
            after=data.get("after"),
            timestamp=data.get("timestamp", now_iso()),
            version=data.get("version"),
        )
