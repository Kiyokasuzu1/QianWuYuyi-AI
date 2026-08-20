# -*- coding: utf-8 -*-
"""
src/runtime/cognitive_timeline.py

P2.4-B.15 Phase 2 — CognitiveTimeline 最小层（认知时间线基础层）

定位（B.15 Phase 2 审计 §1/§2）：
    ReflectionRecord 目前"创建即丢失"（无磁盘持久化、无 append-only
    store 主循环路径）。本模块建立**纯内存、可注入 writer** 的认知
    事件时间线，把 Reflection 升级为"可审计认知事件"。

边界（任务书红线，禁止越界）：
    - 只负责**记录** TimelineEvent（event_id/trace_id/timestamp/
      event_type/source/domain/evidence_refs/parent_event_id）
    - 禁止写人格 / self_model / memory / relationship
    - 默认不写 data/（零磁盘 I/O）；writer 由调用方显式注入
    - 不 import 任何业务模块（personality / self_model / memory 等）

Flag 契约（B.15 任务书）：
    - _timeline_recording_enabled 默认 False
    - False 时：LifecycleExecutor / Stage 11 行为与 Phase 1 完全一致
    - True 时：只增加 timeline 节点记录与既有事件发布，不产生人格变化

失败语义（fail-open）：
    - append 异常 / writer 异常绝不上抛，只记计数与 last_error
    - Timeline 记录失败绝不能阻断主循环
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TIMELINE_SCHEMA_VERSION = "1.0"
DEFAULT_TIMELINE_CAPACITY = 1000


def _now_iso() -> str:
    # 与 reflection_record.py 保持一致的 ISO UTC 时间格式
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _new_event_id() -> str:
    return f"tl_{uuid.uuid4().hex[:12]}"


# ============================================================
# Flag（默认 False = 不启用，Phase 2 约束）
# ============================================================
_timeline_recording_enabled: bool = False
_timeline: Optional["CognitiveTimeline"] = None


def is_timeline_recording_enabled() -> bool:
    return _timeline_recording_enabled


def set_timeline_recording_enabled(enabled: bool) -> None:
    global _timeline_recording_enabled
    _timeline_recording_enabled = bool(enabled)


def get_timeline() -> "CognitiveTimeline":
    """惰性获取进程内单例（纯内存；测试可用 set_timeline 注入）。"""
    global _timeline
    if _timeline is None:
        _timeline = CognitiveTimeline()
    return _timeline


def set_timeline(timeline: Optional["CognitiveTimeline"]) -> None:
    """替换进程内实例（测试 / 未来灰度接线用）。"""
    global _timeline
    _timeline = timeline


def reset_timeline_state() -> None:
    """恢复默认状态：flag=False + 实例置空（测试用）。"""
    global _timeline, _timeline_recording_enabled
    _timeline = None
    _timeline_recording_enabled = False


# ============================================================
# TimelineEvent（冻结字段契约，B.15 Phase 2 任务书）
# ============================================================
@dataclass
class TimelineEvent:
    """认知时间线节点（纯数据投影，无任何状态写入语义）。

    字段来源（审计 §5）：
    - event_id:        节点唯一 id（reflection 转换时 = "tl_" + reflection_id，
                       保证同一反思重放不产生重复节点）
    - trace_id:        本轮认知轨迹 id（= RuntimeContext.session_id）
    - timestamp:       ISO UTC 时间（来自 ReflectionRecord.timestamp）
    - event_type:      复用既有事件常量（"reflection_completed"）
    - source:          来源（ReflectionRecord.source）
    - domain:          领域（trigger_category / reflection_kind）
    - evidence_refs:   证据引用（source_audit_id + reflection_id + evidence_ids）
    - parent_event_id: 上游节点 id（reflection_started 事件 id，可选）
    """

    event_id: str = field(default_factory=_new_event_id)
    trace_id: str = ""
    timestamp: str = field(default_factory=_now_iso)
    event_type: str = ""
    source: str = ""
    domain: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    parent_event_id: Optional[str] = None

    def __post_init__(self) -> None:
        # 规范化 evidence_refs：只保留非空字符串
        if not isinstance(self.evidence_refs, list):
            try:
                self.evidence_refs = list(self.evidence_refs or [])
            except Exception:  # noqa: BLE001
                self.evidence_refs = []
        self.evidence_refs = [
            str(x) for x in self.evidence_refs if x is not None and str(x) != ""
        ]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TimelineEvent":
        if not isinstance(data, dict):
            raise TypeError(
                f"TimelineEvent.from_dict 需要 dict,实际 {type(data).__name__}"
            )
        return cls(
            event_id=str(data.get("event_id", "") or _new_event_id()),
            trace_id=str(data.get("trace_id", "") or ""),
            timestamp=str(data.get("timestamp", "") or _now_iso()),
            event_type=str(data.get("event_type", "") or ""),
            source=str(data.get("source", "") or ""),
            domain=str(data.get("domain", "") or ""),
            evidence_refs=list(data.get("evidence_refs", []) or []),
            parent_event_id=(
                str(data["parent_event_id"])
                if data.get("parent_event_id")
                else None
            ),
        )


# ============================================================
# ReflectionRecord → TimelineEvent 投影（纯函数，零状态写入）
# ============================================================
def timeline_event_from_reflection(
    record: Dict[str, Any],
    *,
    trace_id: str = "",
    parent_event_id: Optional[str] = None,
) -> TimelineEvent:
    """把 ReflectionRecord.to_dict() 投影为 TimelineEvent（审计 §5 映射）。

    纯函数：只读输入 dict，不触碰任何业务模块状态。
    event_id = "tl_" + reflection_id → 同一反思重复转换得到相同 id，
    由 CognitiveTimeline 幂等去重（"重复 trace_id 不产生重复节点"）。
    """
    from src.contracts.runtime_event_schema import (
        EVENT_REFLECTION_COMPLETED,
    )

    rid = str(record.get("reflection_id", "") or "")
    evidence: List[str] = []
    for key in ("source_audit_id", "reflection_id"):
        val = str(record.get(key, "") or "")
        if val and val not in evidence:
            evidence.append(val)
    for val in (record.get("evidence_ids") or []):
        s = str(val)
        if s and s not in evidence:
            evidence.append(s)
    return TimelineEvent(
        event_id=f"tl_{rid}" if rid else _new_event_id(),
        trace_id=str(trace_id or ""),
        timestamp=str(record.get("timestamp", "") or _now_iso()),
        event_type=EVENT_REFLECTION_COMPLETED,
        source=str(record.get("source", "") or ""),
        domain=str(
            record.get("trigger_category", "")
            or record.get("reflection_kind", "")
            or ""
        ),
        evidence_refs=evidence,
        parent_event_id=parent_event_id,
    )


# ============================================================
# CognitiveTimeline（append-only 内存实现 + 可选注入 writer）
# ============================================================
class CognitiveTimeline:
    """认知时间线（纯内存 append-only + 可选 writer 注入）。

    约束：
    - 默认零磁盘 I/O（writer 未注入时）
    - 容量上限 FIFO 淘汰（默认 1000）
    - 幂等去重：同一 event_id 重复 append 不产生重复节点
    - fail-open：append / writer 异常绝不上抛，不阻断主循环
    """

    def __init__(
        self,
        capacity: int = DEFAULT_TIMELINE_CAPACITY,
        writer: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> None:
        if capacity <= 0:
            capacity = DEFAULT_TIMELINE_CAPACITY
        self.capacity: int = capacity
        self._writer: Optional[Callable[[Dict[str, Any]], Any]] = writer
        self._events: List[TimelineEvent] = []
        self._seen_event_ids: set = set()
        self._dropped_count: int = 0
        self._writer_fail_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(self, event: Optional[TimelineEvent]) -> bool:
        """追加节点（fail-soft；writer 失败不影响内存记录）。"""
        if event is None:
            self._last_error = "append_none_event"
            return False
        if not isinstance(event, TimelineEvent):
            self._last_error = "append_invalid_type"
            return False
        try:
            if event.event_id in self._seen_event_ids:
                return True  # 幂等：同一节点不重复记录
            self._events.append(event)
            self._seen_event_ids.add(event.event_id)
            while len(self._events) > self.capacity:
                oldest = self._events.pop(0)
                self._seen_event_ids.discard(oldest.event_id)
                self._dropped_count += 1
            self._last_error = None
            self._write_to_writer(event)
            return True
        except Exception as exc:  # noqa: BLE001 记录异常绝不阻断主循环
            self._last_error = f"append_failed: {exc}"
            logger.debug("[cognitive_timeline] append 失败（已隔离）: %s", exc)
            return False

    def _write_to_writer(self, event: TimelineEvent) -> None:
        if self._writer is None:
            return
        try:
            self._writer(event.to_dict())
        except Exception as exc:  # noqa: BLE001 fail-open
            self._writer_fail_count += 1
            logger.debug(
                "[cognitive_timeline] writer 失败（已隔离，内存记录保留）: %s",
                exc,
            )

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get_events(self) -> List[TimelineEvent]:
        """全部节点（按追加顺序）。返回副本，调用方无法破坏内部状态。"""
        return list(self._events)

    def get_by_event_id(self, event_id: str) -> Optional[TimelineEvent]:
        if not event_id:
            return None
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    def get_by_trace_id(self, trace_id: str) -> List[TimelineEvent]:
        if not trace_id:
            return []
        return [e for e in self._events if e.trace_id == trace_id]

    def chain(self, trace_id: str) -> List[TimelineEvent]:
        """某 trace 的节点链（按追加顺序 = 认知发生顺序）。"""
        return self.get_by_trace_id(trace_id)

    def count(self) -> int:
        return len(self._events)

    def clear(self) -> int:
        n = len(self._events)
        self._events.clear()
        self._seen_event_ids.clear()
        return n

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def writer_fail_count(self) -> int:
        return self._writer_fail_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "capacity": self.capacity,
            "event_count": self.count(),
            "dropped_count": self._dropped_count,
            "writer_fail_count": self._writer_fail_count,
            "writer_attached": self._writer is not None,
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": TIMELINE_SCHEMA_VERSION,
            "capacity": self.capacity,
            "event_count": self.count(),
            "traces": sorted(
                {e.trace_id for e in self._events if e.trace_id}
            ),
            "last_error": self._last_error,
        }


__all__ = [
    "TimelineEvent",
    "CognitiveTimeline",
    "TIMELINE_SCHEMA_VERSION",
    "DEFAULT_TIMELINE_CAPACITY",
    "is_timeline_recording_enabled",
    "set_timeline_recording_enabled",
    "get_timeline",
    "set_timeline",
    "reset_timeline_state",
    "timeline_event_from_reflection",
]
