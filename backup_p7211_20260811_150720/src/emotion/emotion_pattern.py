"""
情绪模式 (EmotionPattern) — Phase 9.4 v2.1
记录从历史情绪轨迹中发现的一种规律。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
import uuid


@dataclass
class EmotionPattern:
    pattern_id: str = field(default_factory=lambda: f"pattern_{uuid.uuid4().hex[:12]}")
    pattern_version: str = "1.0"        # 模式版本，用于将来升级兼容
    pattern_type: str = ""              # "trigger_event" / "intensity_range" / "cause_distribution"
    event_type: str = ""                # 关联的事件类型，如 user_praise
    emotion: str = ""                   # 主要情绪类型
    description: str = ""               # 自然语言描述
    confidence: float = 0.0             # 置信度 0~1
    stability: float = 0.0              # 稳定性 0~1
    emotion_distribution: Dict[str, float] = field(default_factory=dict)  # 情绪分布
    evidence_trace_ids: List[str] = field(default_factory=list)
    occurrence_count: int = 0
    last_seen_at: str = ""              # 最近一次出现时间
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.stability = max(0.0, min(1.0, self.stability))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "pattern_type": self.pattern_type,
            "event_type": self.event_type,
            "emotion": self.emotion,
            "description": self.description,
            "confidence": self.confidence,
            "stability": self.stability,
            "emotion_distribution": self.emotion_distribution,
            "evidence_trace_ids": self.evidence_trace_ids,
            "occurrence_count": self.occurrence_count,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionPattern":
        return cls(
            pattern_id=data.get("pattern_id", ""),
            pattern_version=data.get("pattern_version", "1.0"),
            pattern_type=data.get("pattern_type", ""),
            event_type=data.get("event_type", ""),
            emotion=data.get("emotion", ""),
            description=data.get("description", ""),
            confidence=data.get("confidence", 0.0),
            stability=data.get("stability", 0.0),
            emotion_distribution=data.get("emotion_distribution", {}),
            evidence_trace_ids=data.get("evidence_trace_ids", []),
            occurrence_count=data.get("occurrence_count", 0),
            last_seen_at=data.get("last_seen_at", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )