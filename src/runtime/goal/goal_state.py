# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_state.py

Phase 5.0-D3-D: GoalState 长期目标状态。

职责:
- 表达"羽依当前长期关注方向"
- 每个 Goal 必须有 evidence(来源)
- 不执行任何动作,只描述
- 状态机: candidate -> active -> paused -> completed/abandoned

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 常量
# ============================================================
GOAL_STATE_SCHEMA_VERSION = "1.0"

# goal_type
GOAL_TYPE_PERSONAL_GROWTH = "personal_growth"
GOAL_TYPE_LEARNING = "learning"
GOAL_TYPE_CREATIVE = "creative"
GOAL_TYPE_RELATIONSHIP = "relationship"
GOAL_TYPE_EXPLORATION = "exploration"
ALL_GOAL_TYPES = (
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_RELATIONSHIP,
    GOAL_TYPE_EXPLORATION,
)
DEFAULT_GOAL_TYPE = GOAL_TYPE_PERSONAL_GROWTH

# status
GOAL_STATUS_CANDIDATE = "candidate"
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_PAUSED = "paused"
GOAL_STATUS_COMPLETED = "completed"
GOAL_STATUS_ABANDONED = "abandoned"
ALL_GOAL_STATUSES = (
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_PAUSED,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_ABANDONED,
)
DEFAULT_GOAL_STATUS = GOAL_STATUS_CANDIDATE

# 长度限制
MAX_TITLE_LEN = 128
MAX_DESCRIPTION_LEN = 1024
MAX_REASON_LEN = 512
MAX_SOURCE_IDS = 64
MAX_DESIRE_IDS = 32
MAX_METADATA_KEYS = 16


def _new_goal_id() -> str:
    return f"goal_{uuid.uuid4().hex[:12]}"


# ============================================================
# 数据类
# ============================================================
@dataclass
class GoalState:
    """长期目标状态。

    字段:
    - goal_id:              str
    - title:                简短标题
    - description:          详细描述
    - goal_type:            personal_growth/learning/creative/relationship/exploration
    - status:               candidate/active/paused/completed/abandoned
    - priority:             优先级 [0, 1]
    - importance:           重要性 [0, 1]
    - confidence:           置信度 [0, 1]
    - created_at:           创建时间
    - updated_at:           更新时间
    - completed_at:         完成时间
    - source_event_ids:     来源事件 ID 列表(必须非空,evidence)
    - source_desire_ids:    来源 Desire ID 列表(必须非空,evidence)
    - reason:               触发原因
    - version:              schema 版本
    - metadata:             扩展元数据
    """

    goal_id: str = field(default_factory=_new_goal_id)
    title: str = ""
    description: str = ""
    goal_type: str = DEFAULT_GOAL_TYPE
    status: str = DEFAULT_GOAL_STATUS
    priority: float = 0.5
    importance: float = 0.5
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    completed_at: float = 0.0
    source_event_ids: List[str] = field(default_factory=list)
    source_desire_ids: List[str] = field(default_factory=list)
    reason: str = ""
    version: str = GOAL_STATE_SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # goal_type
        if self.goal_type not in ALL_GOAL_TYPES:
            self.goal_type = DEFAULT_GOAL_TYPE
        # status
        if self.status not in ALL_GOAL_STATUSES:
            self.status = DEFAULT_GOAL_STATUS
        # 字符串裁剪
        if not isinstance(self.title, str):
            self.title = str(self.title or "")
        if len(self.title) > MAX_TITLE_LEN:
            self.title = self.title[:MAX_TITLE_LEN]
        if not isinstance(self.description, str):
            self.description = str(self.description or "")
        if len(self.description) > MAX_DESCRIPTION_LEN:
            self.description = self.description[:MAX_DESCRIPTION_LEN]
        if not isinstance(self.reason, str):
            self.reason = str(self.reason or "")
        if len(self.reason) > MAX_REASON_LEN:
            self.reason = self.reason[:MAX_REASON_LEN]
        # 数值
        self.priority = _clip01(self.priority, default=0.5)
        self.importance = _clip01(self.importance, default=0.5)
        self.confidence = _clip01(self.confidence, default=0.5)
        # source_event_ids
        self.source_event_ids = _clean_id_list(self.source_event_ids, limit=MAX_SOURCE_IDS)
        # source_desire_ids
        self.source_desire_ids = _clean_id_list(self.source_desire_ids, limit=MAX_DESIRE_IDS)
        # 时间戳
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.updated_at = float(self.updated_at)
        except Exception:
            self.updated_at = 0.0
        try:
            self.completed_at = float(self.completed_at)
        except Exception:
            self.completed_at = 0.0
        # metadata
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        if len(self.metadata) > MAX_METADATA_KEYS:
            keys = list(self.metadata.keys())[:MAX_METADATA_KEYS]
            self.metadata = {k: self.metadata[k] for k in keys}

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def has_evidence(self) -> bool:
        """是否拥有 evidence(来源事件或来源愿望)。"""
        return len(self.source_event_ids) > 0 or len(self.source_desire_ids) > 0

    def is_valid(self) -> bool:
        """合法:title 非空,evidence 存在,字段在范围。"""
        if not self.title:
            return False
        if not self.has_evidence():
            return False
        if self.goal_type not in ALL_GOAL_TYPES:
            return False
        if self.status not in ALL_GOAL_STATUSES:
            return False
        if not (0.0 <= self.priority <= 1.0):
            return False
        if not (0.0 <= self.importance <= 1.0):
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        return True

    def is_candidate(self) -> bool:
        return self.status == GOAL_STATUS_CANDIDATE

    def is_active(self) -> bool:
        return self.status == GOAL_STATUS_ACTIVE

    def is_paused(self) -> bool:
        return self.status == GOAL_STATUS_PAUSED

    def is_completed(self) -> bool:
        return self.status == GOAL_STATUS_COMPLETED

    def is_abandoned(self) -> bool:
        return self.status == GOAL_STATUS_ABANDONED

    def is_terminal(self) -> bool:
        """是否处于终止态(completed / abandoned)。"""
        return self.status in (GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED)

    # --------------------------------------------------------
    # 状态转换
    # --------------------------------------------------------
    def activate(self, now: Optional[float] = None) -> bool:
        if self.status == GOAL_STATUS_COMPLETED:
            return False
        if self.status == GOAL_STATUS_ABANDONED:
            return False
        if self.status == GOAL_STATUS_ACTIVE:
            return True
        self.status = GOAL_STATUS_ACTIVE
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0
        return True

    def pause(self, now: Optional[float] = None) -> bool:
        if self.status in (GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED):
            return False
        if self.status == GOAL_STATUS_PAUSED:
            return True
        self.status = GOAL_STATUS_PAUSED
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0
        return True

    def complete(self, now: Optional[float] = None) -> bool:
        if self.status == GOAL_STATUS_COMPLETED:
            return True
        if self.status == GOAL_STATUS_ABANDONED:
            return False
        self.status = GOAL_STATUS_COMPLETED
        if now is not None:
            try:
                self.updated_at = float(now)
                self.completed_at = float(now)
            except Exception:
                self.updated_at = 0.0
                self.completed_at = 0.0
        return True

    def abandon(self, now: Optional[float] = None) -> bool:
        if self.status == GOAL_STATUS_ABANDONED:
            return True
        if self.status == GOAL_STATUS_COMPLETED:
            return False
        self.status = GOAL_STATUS_ABANDONED
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0
        return True

    def update_priority(self, new_priority: float, now: Optional[float] = None) -> None:
        try:
            v = float(new_priority)
        except Exception:
            return
        self.priority = _clip01(v, default=self.priority)
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0

    def add_source_event_ids(self, ids: List[str]) -> None:
        """追加 source_event_ids(去重)。"""
        if not ids:
            return
        existing = set(self.source_event_ids)
        for i in ids:
            s = str(i) if i is not None else ""
            if s and s not in existing:
                self.source_event_ids.append(s)
                existing.add(s)
                if len(self.source_event_ids) >= MAX_SOURCE_IDS:
                    break

    def add_source_desire_ids(self, ids: List[str]) -> None:
        """追加 source_desire_ids(去重)。"""
        if not ids:
            return
        existing = set(self.source_desire_ids)
        for i in ids:
            s = str(i) if i is not None else ""
            if s and s not in existing:
                self.source_desire_ids.append(s)
                existing.add(s)
                if len(self.source_desire_ids) >= MAX_DESIRE_IDS:
                    break

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GoalState":
        if not isinstance(data, dict):
            return cls(title="invalid")
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
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            updated_at = float(data.get("updated_at", 0.0))
        except Exception:
            updated_at = 0.0
        try:
            completed_at = float(data.get("completed_at", 0.0))
        except Exception:
            completed_at = 0.0
        return cls(
            goal_id=str(data.get("goal_id") or _new_goal_id()),
            title=str(data.get("title", "") or ""),
            description=str(data.get("description", "") or ""),
            goal_type=str(data.get("goal_type", DEFAULT_GOAL_TYPE) or DEFAULT_GOAL_TYPE),
            status=str(data.get("status", DEFAULT_GOAL_STATUS) or DEFAULT_GOAL_STATUS),
            priority=priority,
            importance=importance,
            confidence=confidence,
            created_at=created_at,
            updated_at=updated_at,
            completed_at=completed_at,
            source_event_ids=_safe_str_list(data.get("source_event_ids"), limit=MAX_SOURCE_IDS),
            source_desire_ids=_safe_str_list(data.get("source_desire_ids"), limit=MAX_DESIRE_IDS),
            reason=str(data.get("reason", "") or ""),
            version=str(data.get("version", GOAL_STATE_SCHEMA_VERSION) or GOAL_STATE_SCHEMA_VERSION),
            metadata=_safe_dict(data.get("metadata")),
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "goal_type": self.goal_type,
            "status": self.status,
            "priority": self.priority,
            "importance": self.importance,
            "confidence": self.confidence,
            "source_event_count": len(self.source_event_ids),
            "source_desire_count": len(self.source_desire_ids),
            "has_evidence": self.has_evidence(),
        }

    def __repr__(self) -> str:
        return (
            f"GoalState(id={self.goal_id!r}, type={self.goal_type!r}, "
            f"status={self.status!r}, title={self.title[:32]!r}, "
            f"confidence={self.confidence:.2f})"
        )


# ============================================================
# 状态转换合法性
# ============================================================
VALID_GOAL_TRANSITIONS = {
    GOAL_STATUS_CANDIDATE: {GOAL_STATUS_CANDIDATE, GOAL_STATUS_ACTIVE, GOAL_STATUS_PAUSED, GOAL_STATUS_ABANDONED},
    GOAL_STATUS_ACTIVE: {GOAL_STATUS_ACTIVE, GOAL_STATUS_PAUSED, GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED},
    GOAL_STATUS_PAUSED: {GOAL_STATUS_PAUSED, GOAL_STATUS_ACTIVE, GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED},
    GOAL_STATUS_COMPLETED: {GOAL_STATUS_COMPLETED},
    GOAL_STATUS_ABANDONED: {GOAL_STATUS_ABANDONED},
}


def is_valid_goal_transition(from_status: str, to_status: str) -> bool:
    if from_status not in VALID_GOAL_TRANSITIONS:
        return False
    return to_status in VALID_GOAL_TRANSITIONS[from_status]


# ============================================================
# 工厂
# ============================================================
def build_candidate_goal(
    *,
    title: str,
    description: str = "",
    goal_type: str = DEFAULT_GOAL_TYPE,
    source_event_ids: List[str] = None,
    source_desire_ids: List[str] = None,
    reason: str = "",
    importance: float = 0.5,
    confidence: float = 0.5,
    now: Optional[float] = None,
) -> GoalState:
    """构造一个候选状态的目标。"""
    g = GoalState(
        title=title,
        description=description,
        goal_type=goal_type,
        status=GOAL_STATUS_CANDIDATE,
        source_event_ids=list(source_event_ids or []),
        source_desire_ids=list(source_desire_ids or []),
        reason=reason,
        importance=importance,
        confidence=confidence,
    )
    try:
        if now is not None:
            g.created_at = float(now)
            g.updated_at = float(now)
    except Exception:
        pass
    return g


# ============================================================
# 工具
# ============================================================
def _clip01(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


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


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): value[k] for k in list(value.keys())[:MAX_METADATA_KEYS]}
    return {}


__all__ = [
    # 常量
    "GOAL_STATE_SCHEMA_VERSION",
    "ALL_GOAL_TYPES",
    "ALL_GOAL_STATUSES",
    # goal_type
    "GOAL_TYPE_PERSONAL_GROWTH",
    "GOAL_TYPE_LEARNING",
    "GOAL_TYPE_CREATIVE",
    "GOAL_TYPE_RELATIONSHIP",
    "GOAL_TYPE_EXPLORATION",
    # status
    "GOAL_STATUS_CANDIDATE",
    "GOAL_STATUS_ACTIVE",
    "GOAL_STATUS_PAUSED",
    "GOAL_STATUS_COMPLETED",
    "GOAL_STATUS_ABANDONED",
    # 数据类
    "GoalState",
    # 状态转换
    "VALID_GOAL_TRANSITIONS",
    "is_valid_goal_transition",
    # 工厂
    "build_candidate_goal",
]
