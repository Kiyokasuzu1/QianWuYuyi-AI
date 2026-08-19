"""
关系影响画像 (RelationshipInfluenceProfile) — Phase 7 重命名版

职责：
管理某个用户对羽依成长产生的所有真实影响记录。
为 RelationalExpressionAuditor 提供事实依据。
"""

from typing import Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from src.personality.personality_influence import PersonalityInfluence


@dataclass
class RelationshipInfluenceProfile:
    """某用户对羽依的影响画像。"""

    user_id: str
    relationship_start: str

    influences: List[PersonalityInfluence] = field(default_factory=list)
    last_updated: str = ""

    def add_influence(self, influence: PersonalityInfluence):
        self.influences.append(influence)
        self.last_updated = datetime.now().isoformat()

    def get_influences_by_dimension(self, dimension: str) -> List[PersonalityInfluence]:
        return [i for i in self.influences if i.affected_dimension == dimension]

    @property
    def unique_dimensions(self) -> List[str]:
        return list(set(i.affected_dimension for i in self.influences))

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "relationship_start": self.relationship_start,
            "influence_count": len(self.influences),
            "unique_dimensions": self.unique_dimensions,
            "last_updated": self.last_updated,
            "influences": [i.to_dict() for i in self.influences],
        }