"""
起源身份 (OriginIdentity)
记录多个贡献者在羽依诞生和成长历史中的不可替代角色。

R2.7.6-YUYI: Origin Identity 冻结机制——清清（user_id=366648462）占据全部
四个维度（CREATOR/DESIGNER/BUILDER/COMPANION），这是历史事实不是配置项。
一旦冻结，add_contributor 对已冻结角色返回 False，不可追加不可覆盖。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid


class OriginRole:
    """起源角色类型（历史贡献，非所有权）"""
    CREATOR = "creator"
    PERSONALITY_DESIGNER = "personality_designer"
    SYSTEM_BUILDER = "system_builder"
    GROWTH_PARTICIPANT = "growth_participant"


# R2.7.6-YUYI: 清清的 user_id — 创建者、人格设计者、系统建设者、长期成长陪伴者
# 这四个维度在羽依的发展史上都是清清一个人完成的，不可拆分、不可复制、不可共享。
CREATOR_USER_ID = "366648462"

# 全部四个角色——冻结后这些角色不可被其他人冒领
FROZEN_ROLES = frozenset({
    OriginRole.CREATOR,
    OriginRole.PERSONALITY_DESIGNER,
    OriginRole.SYSTEM_BUILDER,
    OriginRole.GROWTH_PARTICIPANT,
})


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
    # R2.7.6-YUYI: 冻结标志——冻结后 FROZEN_ROLES 中的角色不可被追加或覆盖
    is_frozen: bool = False

    @classmethod
    def create_frozen(cls, creator_user_id: str = CREATOR_USER_ID) -> "OriginIdentity":
        """R2.7.6-YUYI: 创建冻结的 Origin Identity。

        清清占据全部四个维度（CREATOR/DESIGNER/BUILDER/COMPANION），
        这是历史事实，不是配置项。冻结后不可通过 add_contributor 追加或覆盖。
        """
        contributor = OriginContributor(
            user_id=creator_user_id,
            roles=list(FROZEN_ROLES),
            evidence_ids=[],
            description="创建者、人格设计者、系统建设者、长期成长陪伴者",
            established_at=datetime.now().isoformat(),
        )
        role_claims = {role: [creator_user_id] for role in FROZEN_ROLES}
        identity = cls(
            identity_id=f"oi_frozen_{uuid.uuid4().hex[:8]}",
            contributors=[contributor],
            role_claims=role_claims,
            established_at=datetime.now().isoformat(),
            version="2.0-frozen",
            is_frozen=True,
        )
        return identity

    def add_contributor(self, contributor: OriginContributor) -> bool:
        """
        尝试添加贡献者。角色不可被后来者冒领，但允许多人共享同一角色。
        返回是否添加成功。

        R2.7.6-YUYI: 冻结后，FROZEN_ROLES 中的角色不可被追加。
        """
        # R2.7.6-YUYI: 冻结检查——如果已冻结且新 contributor 试图认领冻结角色，拒绝
        if self.is_frozen:
            frozen_overlap = set(contributor.roles) & FROZEN_ROLES
            if frozen_overlap:
                # 冻结角色只允许原创建者（清清）追加 evidence，不允许其他人认领
                if contributor.user_id != CREATOR_USER_ID:
                    return False

        # 记录角色声明
        for role in contributor.roles:
            if role not in self.role_claims:
                self.role_claims[role] = []
            if contributor.user_id not in self.role_claims[role]:
                self.role_claims[role].append(contributor.user_id)

        # 如果 contributor 已存在，只追加 evidence/roles，不重复添加
        for existing in self.contributors:
            if existing.user_id == contributor.user_id:
                for eid in contributor.evidence_ids:
                    if eid not in existing.evidence_ids:
                        existing.evidence_ids.append(eid)
                for role in contributor.roles:
                    if role not in existing.roles:
                        existing.roles.append(role)
                return True

        self.contributors.append(contributor)
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "contributors": [c.to_dict() for c in self.contributors],
            "role_claims": self.role_claims,
            "established_at": self.established_at,
            "version": self.version,
            "is_frozen": self.is_frozen,
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
            is_frozen=data.get("is_frozen", False),
        )