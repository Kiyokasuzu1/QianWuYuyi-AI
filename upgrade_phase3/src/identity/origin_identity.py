"""
起源身份 (OriginIdentity)
记录多个贡献者在羽依诞生和成长历史中的不可替代角色。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
import uuid


class OriginRole:
    """起源角色类型（历史贡献，非所有权）"""
    CREATOR = "creator"
    PERSONALITY_DESIGNER = "personality_designer"
    SYSTEM_BUILDER = "system_builder"
    GROWTH_PARTICIPANT = "growth_participant"


@dataclass
class OriginContributor:
    """单个贡献者的记录"""
    user_id: str = ""
    roles: List[str] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    description: str = ""
    established_at: str = ""

    def __post_init__(self):
        self.roles = list(dict.fromkeys(self.roles))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "roles": self.roles,
            "evidence_ids": self.evidence_ids,
            "description": self.description,
            "established_at": self.established_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OriginContributor":
        return cls(
            user_id=data.get("user_id", ""),
            roles=data.get("roles", []),
            evidence_ids=data.get("evidence_ids", []),
            description=data.get("description", ""),
            established_at=data.get("established_at", ""),
        )


@dataclass
class OriginIdentity:
    """羽依的起源身份集合"""
    identity_id: str = field(default_factory=lambda: f"oi_{uuid.uuid4().hex[:12]}")
    contributors: List[OriginContributor] = field(default_factory=list)
    # 使用角色到用户列表的映射，允许多人共享同一角色
    role_claims: Dict[str, List[str]] = field(default_factory=dict)
    established_at: str = field(default_factory=lambda: datetime.now().isoformat())
    version: str = "1.0"

    def add_contributor(self, contributor: OriginContributor) -> bool:
        """
        尝试添加贡献者。角色不可被后来者冒领，但允许多人共享同一角色。
        返回是否添加成功。
        """
        # 记录角色声明
        for role in contributor.roles:
            if role not in self.role_claims:
                self.role_claims[role] = []
            if contributor.user_id not in self.role_claims[role]:
                self.role_claims[role].append(contributor.user_id)

        self.contributors.append(contributor)
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "contributors": [c.to_dict() for c in self.contributors],
            "role_claims": self.role_claims,
            "established_at": self.established_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OriginIdentity":
        contributors = [OriginContributor.from_dict(c) for c in data.get("contributors", [])]
        role_claims = data.get("role_claims", {})
        # 兼容旧数据：如果没有 role_claims，从 contributors 中重建
        if not role_claims:
            for c in contributors:
                for role in c.roles:
                    role_claims.setdefault(role, []).append(c.user_id)
        return cls(
            identity_id=data.get("identity_id", ""),
            contributors=contributors,
            role_claims=role_claims,
            established_at=data.get("established_at", ""),
            version=data.get("version", "1.0"),
        )