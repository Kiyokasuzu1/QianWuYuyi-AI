# -*- coding: utf-8 -*-
"""
src/runtime/initiative/possible_action.py

Phase 5.0-D3-C: PossibleAction 候选行动。

职责:
- 表达"羽依考虑采取的一个行动候选"
- 绝不执行,只描述
- 必须有 evidence 来源(supporting_signal_ids)
- 状态机:pending -> filtered/deferred/discarded

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 不执行任何动作
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 常量
# ============================================================
POSSIBLE_ACTION_SCHEMA_VERSION = "1.0"

# action_type
ACTION_TYPE_OBSERVE = "observe"
ACTION_TYPE_LEARN = "learn"
ACTION_TYPE_ASK = "ask"
ACTION_TYPE_RECOMMEND = "recommend"
ACTION_TYPE_REMIND = "remind"
ALL_ACTION_TYPES = (
    ACTION_TYPE_OBSERVE,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_ASK,
    ACTION_TYPE_RECOMMEND,
    ACTION_TYPE_REMIND,
)
DEFAULT_ACTION_TYPE = ACTION_TYPE_OBSERVE

# status
ACTION_STATUS_PENDING = "pending"
ACTION_STATUS_FILTERED = "filtered"
ACTION_STATUS_DEFERRED = "deferred"
ACTION_STATUS_DISCARDED = "discarded"
ALL_ACTION_STATUSES = (
    ACTION_STATUS_PENDING,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
)
DEFAULT_ACTION_STATUS = ACTION_STATUS_PENDING

# urgency
ACTION_URGENCY_LOW = "low"
ACTION_URGENCY_NORMAL = "normal"
ACTION_URGENCY_HIGH = "high"
ALL_ACTION_URGENCIES = (
    ACTION_URGENCY_LOW,
    ACTION_URGENCY_NORMAL,
    ACTION_URGENCY_HIGH,
)
DEFAULT_ACTION_URGENCY = ACTION_URGENCY_NORMAL

# effort
ACTION_EFFORT_LOW = "low"
ACTION_EFFORT_MEDIUM = "medium"
ACTION_EFFORT_HIGH = "high"
ALL_ACTION_EFFORTS = (
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_EFFORT_HIGH,
)
DEFAULT_ACTION_EFFORT = ACTION_EFFORT_LOW

# 长度限制
MAX_TOPIC_LEN = 128
MAX_RATIONALE_LEN = 512
MAX_SIGNAL_IDS = 64
MAX_METADATA_KEYS = 16


def _new_action_id() -> str:
    import uuid
    return f"act_{uuid.uuid4().hex[:12]}"


# ============================================================
# 数据类
# ============================================================
@dataclass
class PossibleAction:
    """候选行动。

    字段:
    - action_id:              str
    - action_type:            observe/learn/ask/recommend/remind
    - topic:                  主题
    - rationale:              触发原因
    - supporting_signal_ids:  支撑信号 ID 列表(必须非空)
    - urgency:                low/normal/high
    - effort_estimate:        low/medium/high
    - expected_value:         期望价值 [0, 1]
    - confidence:             置信度 [0, 1]
    - status:                 pending/filtered/deferred/discarded
    - priority:               优先级 [0, 1](filter 后的综合分)
    - created_at:             创建时间
    - updated_at:             更新时间
    - version:                schema 版本
    - metadata:               扩展元数据
    """

    action_id: str = field(default_factory=_new_action_id)
    action_type: str = DEFAULT_ACTION_TYPE
    topic: str = ""
    rationale: str = ""
    supporting_signal_ids: List[str] = field(default_factory=list)
    urgency: str = DEFAULT_ACTION_URGENCY
    effort_estimate: str = DEFAULT_ACTION_EFFORT
    expected_value: float = 0.5
    confidence: float = 0.5
    status: str = DEFAULT_ACTION_STATUS
    priority: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    version: str = POSSIBLE_ACTION_SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # action_type
        if self.action_type not in ALL_ACTION_TYPES:
            self.action_type = DEFAULT_ACTION_TYPE
        # status
        if self.status not in ALL_ACTION_STATUSES:
            self.status = DEFAULT_ACTION_STATUS
        # urgency
        if self.urgency not in ALL_ACTION_URGENCIES:
            self.urgency = DEFAULT_ACTION_URGENCY
        # effort_estimate
        if self.effort_estimate not in ALL_ACTION_EFFORTS:
            self.effort_estimate = DEFAULT_ACTION_EFFORT
        # 字符串裁剪
        if not isinstance(self.topic, str):
            self.topic = str(self.topic or "")
        if len(self.topic) > MAX_TOPIC_LEN:
            self.topic = self.topic[:MAX_TOPIC_LEN]
        if not isinstance(self.rationale, str):
            self.rationale = str(self.rationale or "")
        if len(self.rationale) > MAX_RATIONALE_LEN:
            self.rationale = self.rationale[:MAX_RATIONALE_LEN]
        # 数值
        self.expected_value = _clip01(self.expected_value, default=0.5)
        self.confidence = _clip01(self.confidence, default=0.5)
        self.priority = _clip01(self.priority, default=0.5)
        # supporting_signal_ids
        self.supporting_signal_ids = _clean_id_list(self.supporting_signal_ids, limit=MAX_SIGNAL_IDS)
        # 时间戳
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.updated_at = float(self.updated_at)
        except Exception:
            self.updated_at = 0.0
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
        """是否拥有支撑信号(evidence)。"""
        return len(self.supporting_signal_ids) > 0

    def is_valid(self) -> bool:
        """合法:topic 非空,有 evidence,字段在范围。"""
        if not self.topic:
            return False
        if not self.has_evidence():
            return False
        if self.action_type not in ALL_ACTION_TYPES:
            return False
        if self.status not in ALL_ACTION_STATUSES:
            return False
        if not (0.0 <= self.expected_value <= 1.0):
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        return True

    def is_pending(self) -> bool:
        return self.status == ACTION_STATUS_PENDING

    def is_filtered(self) -> bool:
        return self.status == ACTION_STATUS_FILTERED

    def is_deferred(self) -> bool:
        return self.status == ACTION_STATUS_DEFERRED

    def is_discarded(self) -> bool:
        return self.status == ACTION_STATUS_DISCARDED

    def is_actionable(self) -> bool:
        """是否值得被未来执行系统消费(pending 且通过 filter)。"""
        return self.status == ACTION_STATUS_PENDING

    # --------------------------------------------------------
    # 状态转换
    # --------------------------------------------------------
    def _set_status(self, new_status: str, now: Optional[float] = None) -> bool:
        if new_status not in ALL_ACTION_STATUSES:
            return False
        if new_status == self.status:
            return True
        self.status = new_status
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0
        return True

    def mark_filtered(self, now: Optional[float] = None) -> bool:
        return self._set_status(ACTION_STATUS_FILTERED, now=now)

    def mark_deferred(self, now: Optional[float] = None) -> bool:
        return self._set_status(ACTION_STATUS_DEFERRED, now=now)

    def mark_discarded(self, now: Optional[float] = None) -> bool:
        return self._set_status(ACTION_STATUS_DISCARDED, now=now)

    def mark_pending(self, now: Optional[float] = None) -> bool:
        return self._set_status(ACTION_STATUS_PENDING, now=now)

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

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PossibleAction":
        if not isinstance(data, dict):
            return cls(topic="invalid")
        try:
            expected_value = float(data.get("expected_value", 0.5))
        except Exception:
            expected_value = 0.5
        try:
            confidence = float(data.get("confidence", 0.5))
        except Exception:
            confidence = 0.5
        try:
            priority = float(data.get("priority", 0.5))
        except Exception:
            priority = 0.5
        try:
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            updated_at = float(data.get("updated_at", 0.0))
        except Exception:
            updated_at = 0.0
        return cls(
            action_id=str(data.get("action_id") or _new_action_id()),
            action_type=str(data.get("action_type", DEFAULT_ACTION_TYPE) or DEFAULT_ACTION_TYPE),
            topic=str(data.get("topic", "") or ""),
            rationale=str(data.get("rationale", "") or ""),
            supporting_signal_ids=_safe_str_list(data.get("supporting_signal_ids")),
            urgency=str(data.get("urgency", DEFAULT_ACTION_URGENCY) or DEFAULT_ACTION_URGENCY),
            effort_estimate=str(data.get("effort_estimate", DEFAULT_ACTION_EFFORT) or DEFAULT_ACTION_EFFORT),
            expected_value=expected_value,
            confidence=confidence,
            status=str(data.get("status", DEFAULT_ACTION_STATUS) or DEFAULT_ACTION_STATUS),
            priority=priority,
            created_at=created_at,
            updated_at=updated_at,
            version=str(data.get("version", POSSIBLE_ACTION_SCHEMA_VERSION) or POSSIBLE_ACTION_SCHEMA_VERSION),
            metadata=_safe_dict(data.get("metadata")),
        )

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "topic": self.topic,
            "status": self.status,
            "urgency": self.urgency,
            "effort_estimate": self.effort_estimate,
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "priority": self.priority,
            "supporting_signal_count": len(self.supporting_signal_ids),
        }

    def __repr__(self) -> str:
        return (
            f"PossibleAction(id={self.action_id!r}, type={self.action_type!r}, "
            f"topic={self.topic!r}, status={self.status!r}, confidence={self.confidence:.2f})"
        )


# ============================================================
# 状态转换合法性辅助
# ============================================================
VALID_TRANSITIONS = {
    ACTION_STATUS_PENDING: {ACTION_STATUS_PENDING, ACTION_STATUS_FILTERED, ACTION_STATUS_DEFERRED, ACTION_STATUS_DISCARDED},
    ACTION_STATUS_FILTERED: {ACTION_STATUS_FILTERED, ACTION_STATUS_PENDING, ACTION_STATUS_DISCARDED},
    ACTION_STATUS_DEFERRED: {ACTION_STATUS_DEFERRED, ACTION_STATUS_PENDING, ACTION_STATUS_DISCARDED},
    ACTION_STATUS_DISCARDED: {ACTION_STATUS_DISCARDED},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    if from_status not in VALID_TRANSITIONS:
        return False
    return to_status in VALID_TRANSITIONS[from_status]


# ============================================================
# 工厂
# ============================================================
def build_pending_observation(
    *,
    topic: str,
    supporting_signal_ids: List[str],
    rationale: str = "",
    confidence: float = 0.5,
    expected_value: float = 0.5,
    now: Optional[float] = None,
) -> PossibleAction:
    """构造一个 observe 类候选行动。"""
    a = PossibleAction(
        action_type=ACTION_TYPE_OBSERVE,
        topic=topic,
        rationale=rationale or "候选观察",
        supporting_signal_ids=list(supporting_signal_ids or []),
        effort_estimate=ACTION_EFFORT_LOW,
        expected_value=expected_value,
        confidence=confidence,
    )
    try:
        if now is not None:
            a.created_at = float(now)
            a.updated_at = float(now)
    except Exception:
        pass
    return a


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


def _safe_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return _clean_id_list(value, limit=MAX_SIGNAL_IDS)
    return []


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): value[k] for k in list(value.keys())[:MAX_METADATA_KEYS]}
    return {}


__all__ = [
    # 常量
    "POSSIBLE_ACTION_SCHEMA_VERSION",
    "ALL_ACTION_TYPES",
    "ALL_ACTION_STATUSES",
    "ALL_ACTION_URGENCIES",
    "ALL_ACTION_EFFORTS",
    # action type
    "ACTION_TYPE_OBSERVE",
    "ACTION_TYPE_LEARN",
    "ACTION_TYPE_ASK",
    "ACTION_TYPE_RECOMMEND",
    "ACTION_TYPE_REMIND",
    # status
    "ACTION_STATUS_PENDING",
    "ACTION_STATUS_FILTERED",
    "ACTION_STATUS_DEFERRED",
    "ACTION_STATUS_DISCARDED",
    # urgency
    "ACTION_URGENCY_LOW",
    "ACTION_URGENCY_NORMAL",
    "ACTION_URGENCY_HIGH",
    # effort
    "ACTION_EFFORT_LOW",
    "ACTION_EFFORT_MEDIUM",
    "ACTION_EFFORT_HIGH",
    # 数据类
    "PossibleAction",
    # 状态转换
    "VALID_TRANSITIONS",
    "is_valid_transition",
    # 工厂
    "build_pending_observation",
]
