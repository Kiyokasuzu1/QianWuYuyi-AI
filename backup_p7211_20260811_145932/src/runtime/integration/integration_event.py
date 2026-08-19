# -*- coding: utf-8 -*-
"""
src/runtime/integration/integration_event.py

Phase 5.0-D2 Step 2: IntegrationEvent 数据契约。

职责:
- 定义 IntegrationLayer 事件的标准数据结构
- 定义 EventType 常量(与设计文档 §3.2 一致)
- 不连接任何业务事件源 / Lifecycle EventEmitter
- 仅作为 IntegrationLayer 内部事件容器

约束:
- 不依赖业务模块
- 不依赖 Lifecycle Core 任何可变状态
- 仅依赖 Python 标准库
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# ============================================================
# EventType 常量(对齐设计文档 §3.2)
# ============================================================
INTEGRATION_EXPERIENCE_RECORDED = "integration.experience.recorded"
INTEGRATION_EXPERIENCE_IDLE = "integration.experience.idle"
INTEGRATION_MEMORY_SHOULD_CONSOLIDATE = "integration.memory.should_consolidate"
INTEGRATION_EMOTION_SHOULD_DECAY = "integration.emotion.should_decay"
INTEGRATION_GROWTH_SHOULD_PROPOSE = "integration.growth.should_propose"
INTEGRATION_PERSONALITY_SHOULD_SYNC = "integration.personality.should_sync"
INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE = "integration.relationship.should_evaluate"
INTEGRATION_SNAPSHOT_PERSISTED = "integration.snapshot.persisted"
INTEGRATION_LIFECYCLE_TICK_COMPLETE = "integration.lifecycle.tick_complete"

# ============================================================
# Phase 5.0-D3-B: Reflection 相关事件
# ============================================================
INTEGRATION_REFLECTION_COMPLETED = "integration.reflection.completed"
INTEGRATION_REFLECTION_DAILY_COMPLETED = "integration.reflection.daily_completed"
INTEGRATION_REFLECTION_EVENT_COMPLETED = "integration.reflection.event_completed"
INTEGRATION_REFLECTION_GROWTH_COMPLETED = "integration.reflection.growth_completed"

# ============================================================
# Phase 5.0-D3-C: Initiative 相关事件
# ============================================================
INTEGRATION_INTEREST_SIGNAL_CREATED = "integration.interest_signal.created"
INTEGRATION_INITIATIVE_CREATED = "integration.initiative.created"
INTEGRATION_INITIATIVE_FILTERED = "integration.initiative.filtered"

# ============================================================
# Phase 5.0-D3-D: Goal & Desire 相关事件
# ============================================================
INTEGRATION_DESIRE_CREATED = "integration.desire.created"
INTEGRATION_GOAL_CREATED = "integration.goal.created"
INTEGRATION_GOAL_UPDATED = "integration.goal.updated"
INTEGRATION_GOAL_PLAN_CREATED = "integration.goal.plan_created"


ALL_INTEGRATION_EVENT_TYPES = (
    INTEGRATION_EXPERIENCE_RECORDED,
    INTEGRATION_EXPERIENCE_IDLE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    INTEGRATION_EMOTION_SHOULD_DECAY,
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
    INTEGRATION_SNAPSHOT_PERSISTED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    # Phase 5.0-D3-B
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    INTEGRATION_REFLECTION_GROWTH_COMPLETED,
    # Phase 5.0-D3-C
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INITIATIVE_FILTERED,
    # Phase 5.0-D3-D
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_GOAL_UPDATED,
    INTEGRATION_GOAL_PLAN_CREATED,
)


# ============================================================
# 事件严重度(Skeleton 阶段不消费,仅占位)
# ============================================================
INTEGRATION_SEVERITY_INFO = "info"
INTEGRATION_SEVERITY_NOTICE = "notice"
INTEGRATION_SEVERITY_WARNING = "warning"
INTEGRATION_SEVERITY_CRITICAL = "critical"

ALL_INTEGRATION_SEVERITIES = (
    INTEGRATION_SEVERITY_INFO,
    INTEGRATION_SEVERITY_NOTICE,
    INTEGRATION_SEVERITY_WARNING,
    INTEGRATION_SEVERITY_CRITICAL,
)


# ============================================================
# 辅助
# ============================================================
def _gen_event_id() -> str:
    return f"iev_{uuid.uuid4().hex[:12]}"


def _now_ts() -> float:
    return float(time.time())


# ============================================================
# IntegrationEvent
# ============================================================
@dataclass
class IntegrationEvent:
    """IntegrationLayer 事件数据契约。

    字段:
    - event_id     : 全局唯一 ID
    - event_type   : 事件类型(IntegrationEventType 常量之一)
    - severity     : 严重级别(info/notice/warning/critical)
    - source       : 事件来源(如 "yuyi_event_bus" / "runtime" / "lifecycle_manager")
    - timestamp    : 事件产生时间(epoch seconds)
    - payload      : 事件数据
    - related_ids  : 关联 ID 列表
    - metadata     : 元数据
    """

    event_id: str = field(default_factory=_gen_event_id)
    event_type: str = ""
    severity: str = INTEGRATION_SEVERITY_INFO
    source: str = "unknown"
    timestamp: float = field(default_factory=_now_ts)
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: list = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str):
            self.event_type = str(self.event_type or "")
        if not isinstance(self.severity, str):
            self.severity = INTEGRATION_SEVERITY_INFO
        if not isinstance(self.source, str):
            self.source = str(self.source or "unknown")
        if not isinstance(self.timestamp, (int, float)):
            try:
                self.timestamp = float(self.timestamp)
            except Exception:
                self.timestamp = 0.0
        # payload 深拷贝隔离
        if not isinstance(self.payload, dict):
            try:
                self.payload = dict(self.payload) if self.payload else {}
            except Exception:
                self.payload = {}
        else:
            self.payload = dict(self.payload)
        # related_ids 浅拷贝隔离(列表)
        if not isinstance(self.related_ids, list):
            try:
                self.related_ids = list(self.related_ids) if self.related_ids else []
            except Exception:
                self.related_ids = []
        else:
            self.related_ids = list(self.related_ids)
        # metadata 深拷贝隔离
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        else:
            self.metadata = dict(self.metadata)

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntegrationEvent":
        """从 dict 反序列化,容错处理缺失字段。"""
        if not isinstance(data, dict):
            return cls()
        return cls(
            event_id=str(data.get("event_id") or _gen_event_id()),
            event_type=str(data.get("event_type", "") or ""),
            severity=str(data.get("severity", INTEGRATION_SEVERITY_INFO) or INTEGRATION_SEVERITY_INFO),
            source=str(data.get("source", "unknown") or "unknown"),
            timestamp=_safe_float(data.get("timestamp", 0.0)),
            payload=_safe_dict(data.get("payload")),
            related_ids=_safe_list(data.get("related_ids")),
            metadata=_safe_dict(data.get("metadata")),
        )

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def is_valid(self) -> bool:
        """事件是否合法: event_type 非空且在已知类型列表中(可选)。"""
        if not self.event_type:
            return False
        if not isinstance(self.event_type, str):
            return False
        return True

    def is_known_type(self) -> bool:
        """事件类型是否在 ALL_INTEGRATION_EVENT_TYPES 中。"""
        return self.event_type in ALL_INTEGRATION_EVENT_TYPES

    def __repr__(self) -> str:
        return (
            f"IntegrationEvent(type={self.event_type!r}, "
            f"source={self.source!r}, id={self.event_id!r})"
        )


# ============================================================
# 工厂函数(Skeleton 阶段只提供空工厂)
# ============================================================
def make_integration_event(
    event_type: str,
    source: str = "unknown",
    *,
    payload: Optional[Dict[str, Any]] = None,
    related_ids: Optional[list] = None,
    severity: str = INTEGRATION_SEVERITY_INFO,
    metadata: Optional[Dict[str, Any]] = None,
) -> IntegrationEvent:
    """构造 IntegrationEvent 的工厂函数。"""
    return IntegrationEvent(
        event_type=str(event_type or ""),
        source=str(source or "unknown"),
        severity=str(severity or INTEGRATION_SEVERITY_INFO),
        payload=dict(payload) if isinstance(payload, dict) else {},
        related_ids=list(related_ids) if isinstance(related_ids, list) else [],
        metadata=dict(metadata) if isinstance(metadata, dict) else {},
    )


# ============================================================
# 内部辅助
# ============================================================
def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _safe_list(value: Any) -> list:
    if isinstance(value, list):
        return list(value)
    return []
