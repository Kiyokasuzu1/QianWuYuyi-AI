"""
Self History (Phase 6.1)

SelfModel 生命周期事件日志。

所有 SelfModel 修改必须通过 SelfModelAdapter 写入 SelfHistory。
SelfHistory 是可审计的、不可变的事件序列。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 事件类型
# ============================================================

class SelfHistoryEventType:
    """
    SelfModel 事件类型（常量集合）。

    命名空间：
    - growth_record_applied
    - reflection_insight_applied
    - pcr_applied
    - self_belief_added
    - self_belief_reinforced
    - self_understanding_updated
    - snapshot_created
    - rollback
    - identity_core_changed
    """
    GROWTH_RECORD_APPLIED = "growth_record_applied"
    REFLECTION_INSIGHT_APPLIED = "reflection_insight_applied"
    PCR_APPLIED = "pcr_applied"
    SELF_BELIEF_ADDED = "self_belief_added"
    SELF_BELIEF_REINFORCED = "self_belief_reinforced"
    SELF_UNDERSTANDING_UPDATED = "self_understanding_updated"
    SNAPSHOT_CREATED = "snapshot_created"
    ROLLBACK = "rollback"
    IDENTITY_CORE_CHANGED = "identity_core_changed"


VALID_EVENT_TYPES: frozenset = frozenset({
    SelfHistoryEventType.GROWTH_RECORD_APPLIED,
    SelfHistoryEventType.REFLECTION_INSIGHT_APPLIED,
    SelfHistoryEventType.PCR_APPLIED,
    SelfHistoryEventType.SELF_BELIEF_ADDED,
    SelfHistoryEventType.SELF_BELIEF_REINFORCED,
    SelfHistoryEventType.SELF_UNDERSTANDING_UPDATED,
    SelfHistoryEventType.SNAPSHOT_CREATED,
    SelfHistoryEventType.ROLLBACK,
    SelfHistoryEventType.IDENTITY_CORE_CHANGED,
})


# ============================================================
# SelfHistoryEvent
# ============================================================

@dataclass
class SelfHistoryEvent:
    """
    SelfModel 生命周期事件（不可变）。

    - event_id: 唯一
    - timestamp: ISO8601
    - event_type: 上述合法值之一
    - source_type: 来源类型（proposal / growth_record / insight / snapshot / manual）
    - source_id: 来源 ID
    - affected_traits: 受影响特质 {trait: delta}
    - affected_beliefs: 受影响 belief_id 列表
    - summary: 自然语言摘要
    - snapshot_before / snapshot_after: 回滚用
    - actor: 触发者（system / human / runtime_pipeline / ...）
    - metadata: 任意附加元数据
    """

    event_id: str = field(default_factory=lambda: f"hevt_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=_now_iso)
    event_type: str = SelfHistoryEventType.PCR_APPLIED
    source_type: str = ""
    source_id: str = ""
    affected_traits: Dict[str, float] = field(default_factory=dict)
    affected_beliefs: List[str] = field(default_factory=list)
    summary: str = ""
    snapshot_before: Optional[Dict[str, Any]] = None
    snapshot_after: Optional[Dict[str, Any]] = None
    actor: str = "system"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.event_type not in VALID_EVENT_TYPES:
            errors.append(f"invalid event_type: {self.event_type}")
        if not isinstance(self.affected_traits, dict):
            errors.append("affected_traits must be dict")
        if not isinstance(self.affected_beliefs, list):
            errors.append("affected_beliefs must be list")
        if not isinstance(self.metadata, dict):
            errors.append("metadata must be dict")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfHistoryEvent":
        return cls(
            event_id=data.get("event_id") or f"hevt_{uuid.uuid4().hex[:10]}",
            timestamp=data.get("timestamp", _now_iso()),
            event_type=data.get("event_type", SelfHistoryEventType.PCR_APPLIED),
            source_type=data.get("source_type", ""),
            source_id=data.get("source_id", ""),
            affected_traits=dict(data.get("affected_traits") or {}),
            affected_beliefs=list(data.get("affected_beliefs") or []),
            summary=data.get("summary", ""),
            snapshot_before=data.get("snapshot_before"),
            snapshot_after=data.get("snapshot_after"),
            actor=data.get("actor", "system"),
            metadata=dict(data.get("metadata") or {}),
        )


# ============================================================
# SelfHistory 容器
# ============================================================

class SelfHistory:
    """
    SelfModel 生命周期事件容器。

    支持：
    - append(event)
    - query(event_type=, source_type=, since=, until=, limit=)
    - latest(n=10)
    - snapshot_at(event_id) → 该事件后的 state_after（若有）
    - rollback_to(event_id) → 返回可被 SelfModelAdapter 应用的 state_after
    - to_dict / load_dict（持久化）
    """

    MAX_EVENTS = 1000  # 内存上限；超出时截断最旧

    def __init__(self) -> None:
        self._events: List[SelfHistoryEvent] = []

    # ============================================================
    # 写入
    # ============================================================

    def append(self, event: SelfHistoryEvent) -> bool:
        if not event.is_valid():
            return False
        self._events.append(event)
        # 截断
        if len(self._events) > self.MAX_EVENTS:
            self._events = self._events[-self.MAX_EVENTS:]
        return True

    # ============================================================
    # 查询
    # ============================================================

    def query(
        self,
        event_type: Optional[str] = None,
        source_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        actor: Optional[str] = None,
        limit: int = 100,
    ) -> List[SelfHistoryEvent]:
        out: List[SelfHistoryEvent] = []
        for e in self._events:
            if event_type and e.event_type != event_type:
                continue
            if source_type and e.source_type != source_type:
                continue
            if since and e.timestamp < since:
                continue
            if until and e.timestamp > until:
                continue
            if actor and e.actor != actor:
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def latest(self, n: int = 10) -> List[SelfHistoryEvent]:
        return self._events[-n:]

    def all(self) -> List[SelfHistoryEvent]:
        return list(self._events)

    def count(self) -> int:
        return len(self._events)

    def get(self, event_id: str) -> Optional[SelfHistoryEvent]:
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    # ============================================================
    # 回滚支持
    # ============================================================

    def snapshot_at(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        返回某事件应用后的 snapshot_after。
        若该事件无 snapshot_after 字段，返回 None。
        """
        e = self.get(event_id)
        if e is None:
            return None
        return e.snapshot_after

    def rollback_to(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        返回可被 SelfModelAdapter 应用的 state。
        等同 snapshot_at(event_id)。
        """
        return self.snapshot_at(event_id)

    def last_snapshot_event(self) -> Optional[SelfHistoryEvent]:
        """返回最后一个 snapshot_created 事件"""
        for e in reversed(self._events):
            if e.event_type == SelfHistoryEventType.SNAPSHOT_CREATED:
                return e
        return None

    # ============================================================
    # 持久化
    # ============================================================

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "count": len(self._events),
            "events": [e.to_dict() for e in self._events],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        self._events = []
        for e in (data.get("events") or []):
            try:
                ev = SelfHistoryEvent.from_dict(e)
                if ev.is_valid():
                    self._events.append(ev)
            except Exception:
                continue
        # 截断
        if len(self._events) > self.MAX_EVENTS:
            self._events = self._events[-self.MAX_EVENTS:]

    def clear(self) -> None:
        self._events.clear()
