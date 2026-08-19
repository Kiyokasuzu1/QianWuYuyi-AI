# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_record.py

Phase 5.0-D3-D: GoalRecord 目标记录 + ChangeRecord 变化记录。

职责:
- 表达一个目标在某个时间点的状态快照
- 表达目标的状态变化
- 用于 GoalHistory 追踪

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 常量
# ============================================================
GOAL_RECORD_SCHEMA_VERSION = "1.0"

# change type
GOAL_CHANGE_CREATED = "created"
GOAL_CHANGE_UPDATED = "updated"
GOAL_CHANGE_ACTIVATED = "activated"
GOAL_CHANGE_PAUSED = "paused"
GOAL_CHANGE_COMPLETED = "completed"
GOAL_CHANGE_ABANDONED = "abandoned"
GOAL_CHANGE_PROMOTED = "promoted"
ALL_GOAL_CHANGE_TYPES = (
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_UPDATED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_PROMOTED,
)

# 长度限制
MAX_REASON_LEN = 512
MAX_SOURCE_IDS = 64
MAX_DESIRE_IDS = 32


def _new_record_id() -> str:
    return f"rec_{uuid.uuid4().hex[:12]}"


def _new_change_id() -> str:
    return f"chg_{uuid.uuid4().hex[:12]}"


# ============================================================
# GoalRecord
# ============================================================
@dataclass
class GoalRecord:
    """目标在某时间点的快照。

    字段:
    - record_id:           str
    - goal_id:             目标 ID
    - title:               标题
    - description:         描述
    - goal_type:           目标类型
    - status:              状态
    - priority:            优先级
    - importance:          重要性
    - confidence:          置信度
    - source_event_ids:    来源事件
    - source_desire_ids:   来源愿望
    - reason:              原因
    - snapshot_at:         快照时间
    - version:             schema 版本
    """

    record_id: str = field(default_factory=_new_record_id)
    goal_id: str = ""
    title: str = ""
    description: str = ""
    goal_type: str = ""
    status: str = ""
    priority: float = 0.5
    importance: float = 0.5
    confidence: float = 0.5
    source_event_ids: List[str] = field(default_factory=list)
    source_desire_ids: List[str] = field(default_factory=list)
    reason: str = ""
    snapshot_at: float = 0.0
    version: str = GOAL_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.goal_id, str):
            self.goal_id = str(self.goal_id or "")
        if not isinstance(self.title, str):
            self.title = str(self.title or "")
        if not isinstance(self.description, str):
            self.description = str(self.description or "")
        if not isinstance(self.reason, str):
            self.reason = str(self.reason or "")
        if len(self.reason) > MAX_REASON_LEN:
            self.reason = self.reason[:MAX_REASON_LEN]
        # 数值
        try:
            self.priority = float(self.priority)
        except Exception:
            self.priority = 0.5
        try:
            self.importance = float(self.importance)
        except Exception:
            self.importance = 0.5
        try:
            self.confidence = float(self.confidence)
        except Exception:
            self.confidence = 0.5
        # 裁剪
        if self.priority < 0.0:
            self.priority = 0.0
        if self.priority > 1.0:
            self.priority = 1.0
        if self.importance < 0.0:
            self.importance = 0.0
        if self.importance > 1.0:
            self.importance = 1.0
        if self.confidence < 0.0:
            self.confidence = 0.0
        if self.confidence > 1.0:
            self.confidence = 1.0
        # ids
        self.source_event_ids = _clean_id_list(self.source_event_ids, limit=MAX_SOURCE_IDS)
        self.source_desire_ids = _clean_id_list(self.source_desire_ids, limit=MAX_DESIRE_IDS)
        # 时间
        try:
            self.snapshot_at = float(self.snapshot_at)
        except Exception:
            self.snapshot_at = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalRecord":
        if not isinstance(data, dict):
            return cls(goal_id="invalid")
        try:
            priority = float(data.get("priority", 0.5))
        except Exception:
            priority = 0.5
        try:
            importance = float(data.get("importance", 0.5))
        except Exception:
            importance = 0.5
        try:
            confidence = float(data.get("confidence", 0.5))
        except Exception:
            confidence = 0.5
        try:
            snapshot_at = float(data.get("snapshot_at", 0.0))
        except Exception:
            snapshot_at = 0.0
        return cls(
            record_id=str(data.get("record_id") or _new_record_id()),
            goal_id=str(data.get("goal_id", "") or ""),
            title=str(data.get("title", "") or ""),
            description=str(data.get("description", "") or ""),
            goal_type=str(data.get("goal_type", "") or ""),
            status=str(data.get("status", "") or ""),
            priority=priority,
            importance=importance,
            confidence=confidence,
            source_event_ids=_safe_str_list(data.get("source_event_ids"), limit=MAX_SOURCE_IDS),
            source_desire_ids=_safe_str_list(data.get("source_desire_ids"), limit=MAX_DESIRE_IDS),
            reason=str(data.get("reason", "") or ""),
            snapshot_at=snapshot_at,
            version=str(data.get("version", GOAL_RECORD_SCHEMA_VERSION) or GOAL_RECORD_SCHEMA_VERSION),
        )

    def __repr__(self) -> str:
        return (
            f"GoalRecord(id={self.record_id!r}, goal_id={self.goal_id!r}, "
            f"status={self.status!r})"
        )


# ============================================================
# ChangeRecord
# ============================================================
@dataclass
class ChangeRecord:
    """目标状态变化记录。

    字段:
    - change_id:        str
    - goal_id:          目标 ID
    - change_type:      变化类型
    - old_state:        旧状态快照(可空)
    - new_state:        新状态快照
    - source_event_ids: 触发的事件 ID
    - source_desire_ids:触发的 Desire ID
    - reason:           变化原因
    - timestamp:        时间戳
    - version:          schema 版本
    """

    change_id: str = field(default_factory=_new_change_id)
    goal_id: str = ""
    change_type: str = GOAL_CHANGE_CREATED
    old_state: Optional[Dict[str, Any]] = None
    new_state: Optional[Dict[str, Any]] = None
    source_event_ids: List[str] = field(default_factory=list)
    source_desire_ids: List[str] = field(default_factory=list)
    reason: str = ""
    timestamp: float = 0.0
    version: str = GOAL_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.change_type not in ALL_GOAL_CHANGE_TYPES:
            self.change_type = GOAL_CHANGE_CREATED
        if not isinstance(self.goal_id, str):
            self.goal_id = str(self.goal_id or "")
        if not isinstance(self.reason, str):
            self.reason = str(self.reason or "")
        if len(self.reason) > MAX_REASON_LEN:
            self.reason = self.reason[:MAX_REASON_LEN]
        # old/new state
        if self.old_state is not None and not isinstance(self.old_state, dict):
            try:
                self.old_state = dict(self.old_state)
            except Exception:
                self.old_state = None
        if self.new_state is not None and not isinstance(self.new_state, dict):
            try:
                self.new_state = dict(self.new_state)
            except Exception:
                self.new_state = None
        # ids
        self.source_event_ids = _clean_id_list(self.source_event_ids, limit=MAX_SOURCE_IDS)
        self.source_desire_ids = _clean_id_list(self.source_desire_ids, limit=MAX_DESIRE_IDS)
        try:
            self.timestamp = float(self.timestamp)
        except Exception:
            self.timestamp = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeRecord":
        if not isinstance(data, dict):
            return cls(goal_id="invalid")
        try:
            timestamp = float(data.get("timestamp", 0.0))
        except Exception:
            timestamp = 0.0
        return cls(
            change_id=str(data.get("change_id") or _new_change_id()),
            goal_id=str(data.get("goal_id", "") or ""),
            change_type=str(data.get("change_type", GOAL_CHANGE_CREATED) or GOAL_CHANGE_CREATED),
            old_state=data.get("old_state"),
            new_state=data.get("new_state"),
            source_event_ids=_safe_str_list(data.get("source_event_ids"), limit=MAX_SOURCE_IDS),
            source_desire_ids=_safe_str_list(data.get("source_desire_ids"), limit=MAX_DESIRE_IDS),
            reason=str(data.get("reason", "") or ""),
            timestamp=timestamp,
            version=str(data.get("version", GOAL_RECORD_SCHEMA_VERSION) or GOAL_RECORD_SCHEMA_VERSION),
        )

    def is_creation(self) -> bool:
        return self.change_type == GOAL_CHANGE_CREATED

    def is_completion(self) -> bool:
        return self.change_type == GOAL_CHANGE_COMPLETED

    def is_status_change(self) -> bool:
        return self.change_type in (
            GOAL_CHANGE_ACTIVATED,
            GOAL_CHANGE_PAUSED,
            GOAL_CHANGE_COMPLETED,
            GOAL_CHANGE_ABANDONED,
        )

    def __repr__(self) -> str:
        return (
            f"ChangeRecord(id={self.change_id!r}, goal_id={self.goal_id!r}, "
            f"type={self.change_type!r})"
        )


# ============================================================
# 工厂
# ============================================================
def build_record_from_goal(goal: Any, *, now: Optional[float] = None) -> GoalRecord:
    """从 GoalState 构造一个快照记录。"""
    snap_at = now if now is not None else 0.0
    try:
        snap_at = float(snap_at)
    except Exception:
        snap_at = 0.0
    return GoalRecord(
        goal_id=getattr(goal, "goal_id", ""),
        title=getattr(goal, "title", ""),
        description=getattr(goal, "description", ""),
        goal_type=getattr(goal, "goal_type", ""),
        status=getattr(goal, "status", ""),
        priority=getattr(goal, "priority", 0.5),
        importance=getattr(goal, "importance", 0.5),
        confidence=getattr(goal, "confidence", 0.5),
        source_event_ids=list(getattr(goal, "source_event_ids", []) or []),
        source_desire_ids=list(getattr(goal, "source_desire_ids", []) or []),
        reason=getattr(goal, "reason", ""),
        snapshot_at=snap_at,
    )


def build_change_record(
    *,
    goal_id: str,
    change_type: str,
    old_state: Optional[Dict[str, Any]] = None,
    new_state: Optional[Dict[str, Any]] = None,
    source_event_ids: List[str] = None,
    source_desire_ids: List[str] = None,
    reason: str = "",
    now: Optional[float] = None,
) -> ChangeRecord:
    """构造一个变化记录。"""
    ts = now if now is not None else 0.0
    try:
        ts = float(ts)
    except Exception:
        ts = 0.0
    return ChangeRecord(
        goal_id=goal_id,
        change_type=change_type,
        old_state=old_state,
        new_state=new_state,
        source_event_ids=list(source_event_ids or []),
        source_desire_ids=list(source_desire_ids or []),
        reason=reason,
        timestamp=ts,
    )


# ============================================================
# 工具
# ============================================================
def _clean_id_list(value: Any, *, limit: int) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for v in value:
        if v is None:
            continue
        s = str(v)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _safe_str_list(value: Any, *, limit: int) -> List[str]:
    if isinstance(value, list):
        return _clean_id_list(value, limit=limit)
    return []


__all__ = [
    # 常量
    "GOAL_RECORD_SCHEMA_VERSION",
    "ALL_GOAL_CHANGE_TYPES",
    # change type
    "GOAL_CHANGE_CREATED",
    "GOAL_CHANGE_UPDATED",
    "GOAL_CHANGE_ACTIVATED",
    "GOAL_CHANGE_PAUSED",
    "GOAL_CHANGE_COMPLETED",
    "GOAL_CHANGE_ABANDONED",
    "GOAL_CHANGE_PROMOTED",
    # 数据类
    "GoalRecord",
    "ChangeRecord",
    # 工厂
    "build_record_from_goal",
    "build_change_record",
]
