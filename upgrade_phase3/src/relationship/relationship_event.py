"""
关系事件 (RelationshipEvent)
表示一次可能影响关系认知的互动事件。
事件只声明 potential_dimensions 和 signal_strength，不直接决定变化量。

Phase 10.3 修正：confidence → signal_strength（信号强度，非最终置信度）
"""
from dataclasses import dataclass, field
from typing import List, Set, Dict, Any
from datetime import datetime


@dataclass
class RelationshipEvent:
    event_id: str = ""                              # 事件唯一 ID
    event_type: str = ""                            # "collaboration" / "trust_building" / ...
    evidence_ids: List[str] = field(default_factory=list)  # 关联的 Memory ID
    signal_strength: float = 0.0                    # 信号强度 0~1（非最终置信度）
    potential_dimensions: Set[str] = field(default_factory=set)  # 可能影响的维度集合
    description: str = ""                           # 自然语言描述
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.signal_strength = max(0.0, min(1.0, self.signal_strength))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "evidence_ids": self.evidence_ids,
            "signal_strength": self.signal_strength,
            "potential_dimensions": list(self.potential_dimensions),
            "description": self.description,
            "timestamp": self.timestamp,
        }