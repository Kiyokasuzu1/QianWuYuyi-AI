"""Contracts: State / Snapshot schema (dataclasses)
Snapshot stores component digests and full-state references (for snapshot manager)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ComponentDigest:
    name: str
    digest: Optional[str] = None  # short hash or summary
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    snapshot_id: str = field(default_factory=lambda: f"snap_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=now_iso)
    runtime_status: Dict[str, Any] = field(default_factory=dict)
    personality_digest: Optional[ComponentDigest] = None
    emotion_state: Optional[Dict[str, Any]] = None
    relationship_profiles_digest: Optional[ComponentDigest] = None
    growth_version: Optional[str] = None
    self_model_digest: Optional[ComponentDigest] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.personality_digest:
            d["personality_digest"] = asdict(self.personality_digest)
        if self.relationship_profiles_digest:
            d["relationship_profiles_digest"] = asdict(self.relationship_profiles_digest)
        if self.self_model_digest:
            d["self_model_digest"] = asdict(self.self_model_digest)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Snapshot":
        pd = data.get("personality_digest")
        rpd = data.get("relationship_profiles_digest")
        smd = data.get("self_model_digest")
        return cls(
            snapshot_id=data.get("snapshot_id") or f"snap_{uuid.uuid4().hex[:12]}",
            timestamp=data.get("timestamp") or now_iso(),
            runtime_status=data.get("runtime_status", {}),
            personality_digest=ComponentDigest(**pd) if pd else None,
            emotion_state=data.get("emotion_state"),
            relationship_profiles_digest=ComponentDigest(**rpd) if rpd else None,
            growth_version=data.get("growth_version"),
            self_model_digest=ComponentDigest(**smd) if smd else None,
            notes=data.get("notes"),
        )
