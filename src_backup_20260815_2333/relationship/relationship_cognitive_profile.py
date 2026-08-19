"""
关系认知档案 (RelationshipCognitiveProfile) — Phase 10.7

职责：
记录羽依对用户互动模式的认知（候选/确认模式）。
与旧版 RelationshipInfluenceProfile 不同，本档案关注“互动模式”而非“人格影响”。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime


@dataclass
class RelationshipCognitiveProfile:
    """羽依对用户互动模式的认知档案"""

    candidate_patterns: List[str] = field(default_factory=list)
    confirmed_patterns: List[str] = field(default_factory=list)
    important_events: List[Dict[str, Any]] = field(default_factory=list)
    first_interaction: str = ""
    total_interactions: int = 0
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_patterns": self.candidate_patterns,
            "confirmed_patterns": self.confirmed_patterns,
            "important_events": self.important_events,
            "first_interaction": self.first_interaction,
            "total_interactions": self.total_interactions,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipCognitiveProfile":
        return cls(
            candidate_patterns=data.get("candidate_patterns", []),
            confirmed_patterns=data.get("confirmed_patterns", []),
            important_events=data.get("important_events", []),
            first_interaction=data.get("first_interaction", ""),
            total_interactions=data.get("total_interactions", 0),
            updated_at=data.get("updated_at", ""),
        )