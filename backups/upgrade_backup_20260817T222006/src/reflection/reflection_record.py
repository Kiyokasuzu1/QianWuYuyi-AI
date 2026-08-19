"""
反思记录数据模型 v1.2
content 属性包含 self_change / new_beliefs
"""
from __future__ import annotations
from enum import Enum
from typing import List, Dict, Any
from dataclasses import dataclass, field


class ReflectionLevel(Enum):
    OBSERVATION = "observation"
    INSIGHT = "insight"
    BELIEF_CHANGE = "belief_change"
    IDENTITY_CHANGE = "identity_change"


@dataclass
class ReflectionRecord:
    reflection_id: str
    timestamp: str

    source_event_ids: List[str] = field(default_factory=list)
    source_influence_ids: List[str] = field(default_factory=list)

    event_summary: str = ""
    previous_self_view: str = ""
    current_understanding: str = ""

    self_change: List[str] = field(default_factory=list)
    new_beliefs: List[str] = field(default_factory=list)
    causal_chain: List[str] = field(default_factory=list)

    reflection_level: str = ReflectionLevel.OBSERVATION.value
    confidence: float = 0.5
    is_safe: bool = True
    contains_dependency: bool = False
    contains_exaggeration: bool = False

    def __post_init__(self):
        valid_levels = {level.value for level in ReflectionLevel}
        if self.reflection_level not in valid_levels:
            self.reflection_level = ReflectionLevel.OBSERVATION.value
        self.confidence = max(0.0, min(1.0, self.confidence))

    @property
    def content(self) -> str:
        parts = []
        if self.event_summary:
            parts.append(self.event_summary)
        if self.previous_self_view:
            parts.append(f"过去我认为：{self.previous_self_view}")
        if self.current_understanding:
            parts.append(f"现在我理解：{self.current_understanding}")
        if self.self_change:
            parts.append("变化：" + "、".join(self.self_change))
        if self.new_beliefs:
            parts.append("新信念：" + "、".join(self.new_beliefs))
        return "；".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "timestamp": self.timestamp,
            "source_event_ids": self.source_event_ids,
            "source_influence_ids": self.source_influence_ids,
            "event_summary": self.event_summary,
            "previous_self_view": self.previous_self_view,
            "current_understanding": self.current_understanding,
            "self_change": self.self_change,
            "new_beliefs": self.new_beliefs,
            "causal_chain": self.causal_chain,
            "reflection_level": self.reflection_level,
            "confidence": self.confidence,
            "is_safe": self.is_safe,
            "contains_dependency": self.contains_dependency,
            "contains_exaggeration": self.contains_exaggeration,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ReflectionRecord:
        return cls(
            reflection_id=data["reflection_id"],
            timestamp=data["timestamp"],
            source_event_ids=data.get("source_event_ids", []),
            source_influence_ids=data.get("source_influence_ids", []),
            event_summary=data.get("event_summary", ""),
            previous_self_view=data.get("previous_self_view", ""),
            current_understanding=data.get("current_understanding", ""),
            self_change=data.get("self_change", []),
            new_beliefs=data.get("new_beliefs", []),
            causal_chain=data.get("causal_chain", []),
            reflection_level=data.get("reflection_level", ReflectionLevel.OBSERVATION.value),
            confidence=data.get("confidence", 0.5),
            is_safe=data.get("is_safe", True),
            contains_dependency=data.get("contains_dependency", False),
            contains_exaggeration=data.get("contains_exaggeration", False),
        )