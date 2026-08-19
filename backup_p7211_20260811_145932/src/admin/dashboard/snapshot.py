# -*- coding: utf-8 -*-
"""
src/admin/dashboard/snapshot.py

Phase 5.0 Dashboard Upgrade —— YuyiDashboardSnapshot 统一快照契约。

职责:
- 定义 Dashboard 完整快照的数据结构
- 提供 to_dict 序列化辅助
- 严格只读(无业务逻辑)
- 不调用任何业务模块

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 字段全部为只读 dataclass
- 所有字段允许为空(None 或 {} 或 []),由 Provider 负责填入真实数据或标记 fallback
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


SNAPSHOT_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _empty_dict() -> Dict[str, Any]:
    return {}


def _empty_list() -> List[Any]:
    return []


@dataclass
class RuntimeStateView:
    """RuntimeCore 状态视图。"""
    online: bool = False
    initialized: bool = False
    is_running: bool = False
    authority: Dict[str, bool] = field(default_factory=_empty_dict)
    error: Optional[str] = None


@dataclass
class IdentityView:
    """身份视图。"""
    name: str = ""
    anchor: str = ""
    stable: bool = False
    raw: Dict[str, Any] = field(default_factory=_empty_dict)


@dataclass
class EmotionView:
    """情绪视图。"""
    primary: str = "unknown"
    valence: float = 0.0
    arousal: float = 0.0
    intensity: float = 0.0
    recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class MemorySummaryView:
    """记忆摘要视图。"""
    total: int = 0
    important: int = 0
    recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class GrowthSummaryView:
    """成长摘要视图。"""
    stage: str = "unknown"
    score: float = 0.0
    pending_proposals: int = 0
    approved: int = 0
    rejected: int = 0


@dataclass
class RelationshipSummaryView:
    """关系摘要视图。"""
    trust: float = 0.0
    familiarity: float = 0.0
    level: str = "unknown"
    milestones: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class GoalStateView:
    """目标状态视图。"""
    active_count: int = 0
    completed_count: int = 0
    current: Optional[Dict[str, Any]] = None
    recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class InitiativeStateView:
    """主动行为状态视图。"""
    interest_count: int = 0
    possible_action_count: int = 0
    filtered_count: int = 0
    recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class ReflectionSummaryView:
    """反思摘要视图。"""
    total: int = 0
    today: int = 0
    recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class BodyStateView:
    """Body System 状态视图(Phase 5.0-E 预留,本阶段为空)。"""
    observation_count: int = 0
    action_proposal_count: int = 0
    permission_level: str = "unknown"
    executor_recent: List[Dict[str, Any]] = field(default_factory=_empty_list)


@dataclass
class HealthView:
    """整体健康视图。"""
    status: str = "unknown"
    score: int = 0
    issues: List[str] = field(default_factory=_empty_list)


@dataclass
class YuyiDashboardSnapshot:
    """
    Dashboard 完整快照。

    所有字段允许为空,Provider 负责填入真实数据。
    当 Provider 无法获取真实数据时,应在 data 旁标注 fallback=true(由 Router 层处理)。
    """
    runtime_state: RuntimeStateView = field(default_factory=RuntimeStateView)
    identity: IdentityView = field(default_factory=IdentityView)
    emotion: EmotionView = field(default_factory=EmotionView)
    memory_summary: MemorySummaryView = field(default_factory=MemorySummaryView)
    growth_summary: GrowthSummaryView = field(default_factory=GrowthSummaryView)
    relationship_summary: RelationshipSummaryView = field(default_factory=RelationshipSummaryView)
    goal_state: GoalStateView = field(default_factory=GoalStateView)
    initiative_state: InitiativeStateView = field(default_factory=InitiativeStateView)
    reflection_summary: ReflectionSummaryView = field(default_factory=ReflectionSummaryView)
    body_state: BodyStateView = field(default_factory=BodyStateView)
    health: HealthView = field(default_factory=HealthView)
    timestamp: str = field(default_factory=_now_iso)
    schema_version: str = SNAPSHOT_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict。"""
        return asdict(self)

    @classmethod
    def empty(cls) -> "YuyiDashboardSnapshot":
        """构造空快照(用于 fallback 场景)。"""
        return cls()


__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "RuntimeStateView",
    "IdentityView",
    "EmotionView",
    "MemorySummaryView",
    "GrowthSummaryView",
    "RelationshipSummaryView",
    "GoalStateView",
    "InitiativeStateView",
    "ReflectionSummaryView",
    "BodyStateView",
    "HealthView",
    "YuyiDashboardSnapshot",
]
