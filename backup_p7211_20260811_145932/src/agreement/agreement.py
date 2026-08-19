"""
约定 (Agreement)
羽依与用户之间建立的长期不可违背的自我约束。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


class AgreementPriority(Enum):
    IMMUTABLE = 100   # 不可变，永远不能删除或修改
    HIGH = 80         # 高优先级，可修改但需要额外确认
    MEDIUM = 50       # 中优先级，可正常修改


class AgreementCategory(Enum):
    IDENTITY_BOUNDARY = "identity_boundary"
    INTERACTION_STYLE = "interaction_style"
    MEMORY_RULE = "memory_rule"
    VALUE_CONSTRAINT = "value_constraint"
    SAFETY_BOUNDARY = "safety_boundary"


class AgreementStatus:
    ACTIVE = "active"
    FROZEN = "frozen"
    DEPRECATED = "deprecated"


class AgreementSource:
    USER_CONFIRMED = "user_confirmed"
    SYSTEM_DEFINED = "system_defined"
    DEVELOPER_DEFINED = "developer_defined"
    SELF_GENERATED = "self_generated"


@dataclass
class Agreement:
    agreement_id: str = field(default_factory=lambda: f"agr_{uuid.uuid4().hex[:12]}")
    content: str = ""
    category: AgreementCategory = AgreementCategory.IDENTITY_BOUNDARY
    priority: AgreementPriority = AgreementPriority.HIGH
    status: str = AgreementStatus.ACTIVE
    version: int = 1
    source_type: str = AgreementSource.USER_CONFIRMED
    evidence_ids: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def can_modify(self) -> bool:
        """检查此约定是否可以被修改或删除"""
        return self.priority != AgreementPriority.IMMUTABLE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "content": self.content,
            "category": self.category.value,
            "priority": self.priority.name,
            "status": self.status,
            "version": self.version,
            "source_type": self.source_type,
            "evidence_ids": self.evidence_ids,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Agreement":
        priority_str = data.get("priority", "HIGH")
        try:
            priority = AgreementPriority[priority_str]
        except KeyError:
            priority = AgreementPriority.HIGH

        category_str = data.get("category", "identity_boundary")
        try:
            category = AgreementCategory(category_str)
        except ValueError:
            category = AgreementCategory.IDENTITY_BOUNDARY

        return cls(
            agreement_id=data.get("agreement_id", ""),
            content=data.get("content", ""),
            category=category,
            priority=priority,
            status=data.get("status", AgreementStatus.ACTIVE),
            version=data.get("version", 1),
            source_type=data.get("source_type", AgreementSource.USER_CONFIRMED),
            evidence_ids=data.get("evidence_ids", []),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )