# src/contracts/growth_schema.py
"""Contracts: Growth Proposal schema (dataclasses)
GrowthProposal encapsulates suggested state changes and evidence references.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ChangeItem:
    path: str
    before: Optional[Any] = None
    after: Optional[Any] = None
    reason: Optional[str] = None


@dataclass
class GrowthProposal:
    id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:12]}")
    source_event_id: Optional[str] = None
    proposed_changes: List[ChangeItem] = field(default_factory=list)
    confidence: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)
    evaluator_meta: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
    status: str = "proposed"
    accepted_at: Optional[str] = None
    rejected_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["proposed_changes"] = [
            asdict(c) for c in self.proposed_changes
        ]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthProposal":
        pcs = data.get("proposed_changes", [])

        change_items = [
            ChangeItem(**c)
            for c in pcs
        ]

        return cls(
            id=data.get("id") or f"prop_{uuid.uuid4().hex[:12]}",
            source_event_id=data.get("source_event_id"),
            proposed_changes=change_items,
            confidence=float(data.get("confidence", 0.0)),
            evidence_ids=data.get("evidence_ids", []),
            evaluator_meta=data.get("evaluator_meta", {}),
            timestamp=data.get("timestamp") or now_iso(),
            status=data.get("status", "proposed"),
            accepted_at=data.get("accepted_at"),
            rejected_at=data.get("rejected_at"),
        )