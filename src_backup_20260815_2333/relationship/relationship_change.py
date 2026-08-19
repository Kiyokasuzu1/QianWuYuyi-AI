"""
关系变化记录 (RelationshipChange)
记录一次经过验证的关系状态变化。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
import uuid


@dataclass
class RelationshipChange:
    change_id: str = field(default_factory=lambda: f"rc_{uuid.uuid4().hex[:12]}")
    dimension: str = ""           # 变化的维度：familiarity / trust / collaboration
    previous_value: float = 0.0   # 变化前的值
    new_value: float = 0.0        # 变化后的值
    delta: float = 0.0            # 变化量
    reason: str = ""              # 变化原因
    confidence: float = 0.0       # 这次变化的可信度
    evidence_ids: List[str] = field(default_factory=list)  # 支撑证据
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_id": self.change_id,
            "dimension": self.dimension,
            "previous_value": self.previous_value,
            "new_value": self.new_value,
            "delta": self.delta,
            "reason": self.reason,
            "confidence": self.confidence,
            "evidence_ids": self.evidence_ids,
            "created_at": self.created_at,
        }