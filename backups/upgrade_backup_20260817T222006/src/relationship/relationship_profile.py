"""
长期关系档案 (RelationshipProfile) — Phase 10.1

职责：
记录羽依与用户互动历史的长期总结，关注的是“互动模式认知”，
而不是“用户事件对人格的影响”。

与旧版 `RelationshipProfile` (v1.2) 的区别：
- 旧版：记录 PersonalityInfluence，用于成长系统和关系审核
- 新版：记录互动模式和关键事件，用于关系认知和沟通策略调整

两个档案在不同阶段可以并存，新版档案是 Phase 10 关系系统的核心数据结构。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime


@dataclass
class RelationshipProfile:
    """羽依与用户的关系档案（互动模式认知）"""

    # 互动模式
    candidate_patterns: List[str] = field(default_factory=list)   # 待验证的互动模式
    confirmed_patterns: List[str] = field(default_factory=list)   # 已确认的互动模式

    # 关键事件
    important_events: List[Dict[str, Any]] = field(default_factory=list)  # 关键关系事件

    # 时间统计
    first_interaction: str = ""          # 首次互动时间
    total_interactions: int = 0          # 总互动次数

    # 元信息
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
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipProfile":
        return cls(
            candidate_patterns=data.get("candidate_patterns", []),
            confirmed_patterns=data.get("confirmed_patterns", []),
            important_events=data.get("important_events", []),
            first_interaction=data.get("first_interaction", ""),
            total_interactions=data.get("total_interactions", 0),
            updated_at=data.get("updated_at", ""),
        )