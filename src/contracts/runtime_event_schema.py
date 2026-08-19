"""
Phase 3.5.23: Runtime Event Schema

统一 Runtime / Cognitive Loop 标准事件。

兼容策略：
- 标准事件名使用下划线风格（用户路线要求）
- 同时提供 legacy alias（点分风格），避免破坏现有 EventBus 订阅方
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


EVENT_EXPERIENCE_CREATED = "experience_created"
EVENT_MEMORY_CREATED = "memory_created"
EVENT_REFLECTION_STARTED = "reflection_started"
EVENT_REFLECTION_COMPLETED = "reflection_completed"
EVENT_EVALUATION_COMPLETED = "evaluation_completed"
EVENT_PROPOSAL_CREATED = "proposal_created"
EVENT_PROPOSAL_APPLIED = "proposal_applied"
EVENT_IDENTITY_CHANGED = "identity_changed"
EVENT_EMOTION_CHANGED = "emotion_changed"
EVENT_RELATIONSHIP_CHANGED = "relationship_changed"


LEGACY_EVENT_ALIASES = {
    EVENT_MEMORY_CREATED: ["memory.created"],
    EVENT_REFLECTION_COMPLETED: ["reflection.completed"],
    EVENT_PROPOSAL_CREATED: ["growth.proposal_created"],
    EVENT_PROPOSAL_APPLIED: ["growth.applied"],
    EVENT_IDENTITY_CHANGED: ["identity.changed"],
    EVENT_EMOTION_CHANGED: ["emotion.changed"],
    EVENT_RELATIONSHIP_CHANGED: ["relationship.changed"],
}


@dataclass
class RuntimeDomainEvent:
    event_id: str = field(default_factory=lambda: f"rde_{uuid.uuid4().hex[:10]}")
    event_type: str = ""
    timestamp: str = field(default_factory=now_iso)
    source: str = ""
    source_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
