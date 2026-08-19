"""
情绪信念 (EmotionBelief) — Phase 9.6 覆盖版
从情绪模式中提取的、可以进入 SelfModel 的自我认知片段。

新增：
- get_merge_key() 方法，用于 Bridge 层去重
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple
from datetime import datetime
import uuid


@dataclass
class EmotionBelief:
    belief_id: str = field(default_factory=lambda: f"eb_{uuid.uuid4().hex[:12]}")
    belief_version: str = "1.0"
    content: str = ""
    emotion: str = ""
    event_type: str = ""
    confidence: float = 0.0
    stability: float = 0.0
    source_pattern_id: str = ""
    evidence_trace_ids: List[str] = field(default_factory=list)
    occurrence_count: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.stability = max(0.0, min(1.0, self.stability))
        self.evidence_trace_ids = list(self.evidence_trace_ids)

    def get_merge_key(self) -> Tuple[str, str]:
        """返回用于去重的合并键，当前使用 event_type + emotion"""
        return (self.event_type, self.emotion)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "belief_id": self.belief_id,
            "belief_version": self.belief_version,
            "content": self.content,
            "emotion": self.emotion,
            "event_type": self.event_type,
            "confidence": self.confidence,
            "stability": self.stability,
            "source_pattern_id": self.source_pattern_id,
            "evidence_trace_ids": self.evidence_trace_ids,
            "occurrence_count": self.occurrence_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionBelief":
        return cls(
            belief_id=data.get("belief_id") or f"eb_{uuid.uuid4().hex[:12]}",
            belief_version=data.get("belief_version", "1.0"),
            content=data.get("content", ""),
            emotion=data.get("emotion", ""),
            event_type=data.get("event_type", ""),
            confidence=data.get("confidence", 0.0),
            stability=data.get("stability", 0.0),
            source_pattern_id=data.get("source_pattern_id", ""),
            evidence_trace_ids=data.get("evidence_trace_ids", []),
            occurrence_count=data.get("occurrence_count", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", data.get("created_at", "")),
        )