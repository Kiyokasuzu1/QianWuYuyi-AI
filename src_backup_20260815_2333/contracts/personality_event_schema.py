"""
Phase 3.5.14: Personality Event Bus Schema

定义人格生命系统的事件契约。

事件分类（5 类）：
- MemoryEvent        : 记忆系统事件
- GrowthEvent        : 成长系统事件
- ReflectionEvent   : 反思系统事件
- IdentityEvent      : 身份系统事件
- RelationshipEvent  : 关系系统事件

设计原则：
- 纯数据契约（dataclass），无副作用
- 可序列化（to_dict / from_dict）
- 向后兼容已有 BaseEvent / YuyiEvent
- 不修改已有事件类型
- 所有事件必须可审计
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 事件分类常量
# ============================================================

CATEGORY_MEMORY = "memory"
CATEGORY_GROWTH = "growth"
CATEGORY_REFLECTION = "reflection"
CATEGORY_IDENTITY = "identity"
CATEGORY_RELATIONSHIP = "relationship"

ALL_CATEGORIES = (
    CATEGORY_MEMORY,
    CATEGORY_GROWTH,
    CATEGORY_REFLECTION,
    CATEGORY_IDENTITY,
    CATEGORY_RELATIONSHIP,
)


# ============================================================
# 事件严重级别
# ============================================================

SEVERITY_INFO = "info"
SEVERITY_NOTICE = "notice"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

ALL_SEVERITIES = (SEVERITY_INFO, SEVERITY_NOTICE, SEVERITY_WARNING, SEVERITY_CRITICAL)


# ============================================================
# 事件类型常量
# ============================================================

# Memory 事件
EVENT_MEMORY_CREATED = "memory.created"
EVENT_MEMORY_UPDATED = "memory.updated"
EVENT_MEMORY_CONSOLIDATED = "memory.consolidated"
EVENT_MEMORY_ACCESSED = "memory.accessed"

# Growth 事件
EVENT_GROWTH_PROPOSAL_CREATED = "growth.proposal_created"
EVENT_GROWTH_PROPOSAL_APPROVED = "growth.proposal_approved"
EVENT_GROWTH_PROPOSAL_REJECTED = "growth.proposal_rejected"
EVENT_GROWTH_PROPOSAL_MODIFIED = "growth.proposal_modified"
EVENT_GROWTH_APPLIED = "growth.applied"
EVENT_GROWTH_ROLLBACK = "growth.rollback"

# Reflection 事件
EVENT_REFLECTION_TRIGGERED = "reflection.triggered"
EVENT_REFLECTION_COMPLETED = "reflection.completed"
EVENT_REFLECTION_INSIGHT_GENERATED = "reflection.insight_generated"
EVENT_REFLECTION_EVALUATED = "reflection.evaluated"

# Identity 事件
EVENT_IDENTITY_ANCHOR_REFRESHED = "identity.anchor_refreshed"
EVENT_IDENTITY_CONTINUITY_CHECKED = "identity.continuity_checked"
EVENT_IDENTITY_SNAPSHOT_CREATED = "identity.snapshot_created"
EVENT_IDENTITY_CHANGE_PROPOSED = "identity.change_proposed"

# Relationship 事件
EVENT_RELATIONSHIP_LEVEL_UPDATED = "relationship.level_updated"
EVENT_RELATIONSHIP_PATTERN_DETECTED = "relationship.pattern_detected"
EVENT_RELATIONSHIP_INTERACTION = "relationship.interaction"
EVENT_RELATIONSHIP_TRUST_CHANGED = "relationship.trust_changed"


# 分类 → 事件类型前缀映射
CATEGORY_TYPE_PREFIX = {
    CATEGORY_MEMORY: "memory.",
    CATEGORY_GROWTH: "growth.",
    CATEGORY_REFLECTION: "reflection.",
    CATEGORY_IDENTITY: "identity.",
    CATEGORY_RELATIONSHIP: "relationship.",
}


def infer_category(event_type: str) -> str:
    """根据事件类型推断分类"""
    for cat, prefix in CATEGORY_TYPE_PREFIX.items():
        if event_type.startswith(prefix):
            return cat
    return "unknown"


# ============================================================
# PersonalityEvent: 统一事件数据结构
# ============================================================

@dataclass
class PersonalityEvent:
    """
    人格生命系统统一事件。

    所有 5 类事件共享此结构，通过 category / event_type 区分。
    """
    event_id: str = field(default_factory=lambda: f"pe_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=now_iso)

    # 事件分类（memory / growth / reflection / identity / relationship）
    category: str = ""

    # 事件类型（如 memory.created / growth.proposal_approved）
    event_type: str = ""

    # 事件来源（模块名）
    source: str = ""

    # 严重级别
    severity: str = SEVERITY_INFO

    # 事件负载（具体数据）
    payload: Dict[str, Any] = field(default_factory=dict)

    # 相关 ID（如 proposal_id / memory_id / reflection_id）
    related_ids: List[str] = field(default_factory=list)

    # 元数据（用于审计/追溯）
    metadata: Dict[str, Any] = field(default_factory=dict)

    # 触发者（actor，谁触发了此事件）
    actor: str = ""

    # 关联的 actor（如审批人）
    related_actor: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonalityEvent":
        return cls(
            event_id=data.get("event_id") or f"pe_{uuid.uuid4().hex[:12]}",
            timestamp=data.get("timestamp") or now_iso(),
            category=data.get("category", ""),
            event_type=data.get("event_type", ""),
            source=data.get("source", ""),
            severity=data.get("severity", SEVERITY_INFO),
            payload=data.get("payload", {}),
            related_ids=data.get("related_ids", []),
            metadata=data.get("metadata", {}),
            actor=data.get("actor", ""),
            related_actor=data.get("related_actor", ""),
        )

    def validate(self) -> bool:
        """基础校验"""
        if not self.category:
            return False
        if not self.event_type:
            return False
        if self.category not in ALL_CATEGORIES and self.category != "unknown":
            return False
        if self.severity not in ALL_SEVERITIES:
            return False
        return True


# ============================================================
# 类型化事件构造器（便捷工厂方法）
# ============================================================

def make_memory_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[List[str]] = None,
    severity: str = SEVERITY_INFO,
    actor: str = "",
) -> PersonalityEvent:
    """构造 Memory 事件"""
    return PersonalityEvent(
        category=CATEGORY_MEMORY,
        event_type=event_type,
        source=source,
        severity=severity,
        payload=payload or {},
        related_ids=related_ids or [],
        actor=actor,
    )


def make_growth_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[List[str]] = None,
    severity: str = SEVERITY_INFO,
    actor: str = "",
    related_actor: str = "",
) -> PersonalityEvent:
    """构造 Growth 事件"""
    return PersonalityEvent(
        category=CATEGORY_GROWTH,
        event_type=event_type,
        source=source,
        severity=severity,
        payload=payload or {},
        related_ids=related_ids or [],
        actor=actor,
        related_actor=related_actor,
    )


def make_reflection_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[List[str]] = None,
    severity: str = SEVERITY_INFO,
    actor: str = "",
) -> PersonalityEvent:
    """构造 Reflection 事件"""
    return PersonalityEvent(
        category=CATEGORY_REFLECTION,
        event_type=event_type,
        source=source,
        severity=severity,
        payload=payload or {},
        related_ids=related_ids or [],
        actor=actor,
    )


def make_identity_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[List[str]] = None,
    severity: str = SEVERITY_INFO,
    actor: str = "",
) -> PersonalityEvent:
    """构造 Identity 事件"""
    return PersonalityEvent(
        category=CATEGORY_IDENTITY,
        event_type=event_type,
        source=source,
        severity=severity,
        payload=payload or {},
        related_ids=related_ids or [],
        actor=actor,
    )


def make_relationship_event(
    event_type: str,
    source: str,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[List[str]] = None,
    severity: str = SEVERITY_INFO,
    actor: str = "",
) -> PersonalityEvent:
    """构造 Relationship 事件"""
    return PersonalityEvent(
        category=CATEGORY_RELATIONSHIP,
        event_type=event_type,
        source=source,
        severity=severity,
        payload=payload or {},
        related_ids=related_ids or [],
        actor=actor,
    )


# ============================================================
# 事件过滤器
# ============================================================

@dataclass
class EventFilter:
    """
    事件过滤器。

    支持按 category / event_type / source / severity / 时间范围过滤。
    """
    categories: Optional[List[str]] = None
    event_types: Optional[List[str]] = None
    sources: Optional[List[str]] = None
    severities: Optional[List[str]] = None
    related_id: Optional[str] = None
    actor: Optional[str] = None
    start_timestamp: Optional[str] = None
    end_timestamp: Optional[str] = None

    def matches(self, event: PersonalityEvent) -> bool:
        """检查事件是否匹配过滤器"""
        if self.categories and event.category not in self.categories:
            return False
        if self.event_types and event.event_type not in self.event_types:
            return False
        if self.sources and event.source not in self.sources:
            return False
        if self.severities and event.severity not in self.severities:
            return False
        if self.related_id and self.related_id not in event.related_ids:
            return False
        if self.actor and event.actor != self.actor:
            return False
        if self.start_timestamp and event.timestamp < self.start_timestamp:
            return False
        if self.end_timestamp and event.timestamp > self.end_timestamp:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 事件订阅者信息
# ============================================================

@dataclass
class SubscriberInfo:
    """订阅者元信息（用于审计/统计）"""
    subscriber_id: str = field(default_factory=lambda: f"sub_{uuid.uuid4().hex[:8]}")
    category: Optional[str] = None       # None 表示订阅所有分类
    event_types: List[str] = field(default_factory=list)  # 空表示订阅所有类型
    handler_name: str = ""               # 处理函数名（用于调试）
    created_at: str = field(default_factory=now_iso)
    call_count: int = 0                  # 已调用次数
    error_count: int = 0                 # 错误次数
    last_called_at: str = ""
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 总线快照
# ============================================================

@dataclass
class EventBusSnapshot:
    """事件总线状态快照"""
    total_events_published: int = 0
    total_subscribers: int = 0
    history_size: int = 0
    history_capacity: int = 0
    events_by_category: Dict[str, int] = field(default_factory=dict)
    events_by_severity: Dict[str, int] = field(default_factory=dict)
    events_by_type: Dict[str, int] = field(default_factory=dict)
    subscriber_infos: List[Dict[str, Any]] = field(default_factory=list)
    last_event_at: str = ""
    last_error: str = ""
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
