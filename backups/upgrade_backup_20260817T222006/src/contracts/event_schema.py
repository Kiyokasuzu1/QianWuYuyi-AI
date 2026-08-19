"""Contracts for QianWuYuyi-AI Phase 3.5.0

This module defines dataclass-based schemas for events.
Keep schemas lightweight and dependency-free (uses built-in dataclasses + typing).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class BaseEvent:
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    type: str = "base"
    source: Optional[str] = None
    timestamp: str = field(default_factory=now_iso)
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseEvent":
        return cls(
            id=data.get("id") or f"evt_{uuid.uuid4().hex[:12]}",
            type=data.get("type", "base"),
            source=data.get("source"),
            timestamp=data.get("timestamp", now_iso()),
            payload=data.get("payload", {}),
            related_ids=data.get("related_ids", []),
            metadata=data.get("metadata", {}),
        )


# Specific (semantic) event types as lightweight subclasses or helpers
@dataclass
class UserMessageEvent(BaseEvent):
    type: str = "UserMessageEvent"


@dataclass
class MemoryCreatedEvent(BaseEvent):
    type: str = "MemoryCreatedEvent"


@dataclass
class EmotionChangedEvent(BaseEvent):
    type: str = "EmotionChangedEvent"


@dataclass
class RelationshipChangedEvent(BaseEvent):
    type: str = "RelationshipChangedEvent"


@dataclass
class GrowthTriggeredEvent(BaseEvent):
    type: str = "GrowthTriggeredEvent"


@dataclass
class ReflectionTriggeredEvent(BaseEvent):
    type: str = "ReflectionTriggeredEvent"


@dataclass
class SystemTickEvent(BaseEvent):
    type: str = "SystemTickEvent"
