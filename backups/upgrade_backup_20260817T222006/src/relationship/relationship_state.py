"""
当前关系状态快照 (RelationshipState)
描述羽依与用户之间当前的关系状态。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime


@dataclass
class RelationshipState:
    familiarity: float = 0.0              # 熟悉度 0~1
    trust: float = 0.0                    # 信任度 0~1
    collaboration: float = 0.0            # 协作度 0~1
    interaction_frequency: float = 0.0    # 互动频率 0~1
    communication_style: List[str] = field(default_factory=list)  # 沟通风格标签
    relationship_stage: str = "initial"   # "initial" / "developing" / "stable" / "deep_collaboration"
    last_interaction_at: str = ""         # 最近一次互动时间
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.familiarity = max(0.0, min(1.0, self.familiarity))
        self.trust = max(0.0, min(1.0, self.trust))
        self.collaboration = max(0.0, min(1.0, self.collaboration))
        self.interaction_frequency = max(0.0, min(1.0, self.interaction_frequency))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "familiarity": self.familiarity,
            "trust": self.trust,
            "collaboration": self.collaboration,
            "interaction_frequency": self.interaction_frequency,
            "communication_style": self.communication_style,
            "relationship_stage": self.relationship_stage,
            "last_interaction_at": self.last_interaction_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipState":
        return cls(
            familiarity=data.get("familiarity", 0.0),
            trust=data.get("trust", 0.0),
            collaboration=data.get("collaboration", 0.0),
            interaction_frequency=data.get("interaction_frequency", 0.0),
            communication_style=data.get("communication_style", []),
            relationship_stage=data.get("relationship_stage", "initial"),
            last_interaction_at=data.get("last_interaction_at", ""),
            updated_at=data.get("updated_at", ""),
        )