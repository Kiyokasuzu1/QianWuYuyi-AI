# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_history.py

Phase 5.0-D3-D: GoalHistory 目标变化历史。

职责:
- 记录目标状态变化(创建/更新/激活/暂停/完成/放弃)
- 支持 FIFO 容量限制
- 支持按 goal_id 查询
- 支持 replay

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 线程安全
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Dict, List, Optional

from src.runtime.goal.goal_record import (
    ALL_GOAL_CHANGE_TYPES,
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_PROMOTED,
    GOAL_CHANGE_UPDATED,
    ChangeRecord,
    GoalRecord,
    build_change_record,
    build_record_from_goal,
)


# ============================================================
# 常量
# ============================================================
GOAL_HISTORY_SCHEMA_VERSION = "1.0"
DEFAULT_HISTORY_CAPACITY = 1024


# ============================================================
# GoalHistory
# ============================================================
class GoalHistory:
    """目标变化历史(线程安全,FIFO)。"""

    def __init__(self, *, capacity: int = DEFAULT_HISTORY_CAPACITY) -> None:
        try:
            self._capacity = max(1, int(capacity))
        except Exception:
            self._capacity = DEFAULT_HISTORY_CAPACITY
        self._lock = threading.RLock()
        self._changes: deque = deque(maxlen=self._capacity)
        self._snapshots: deque = deque(maxlen=self._capacity)
        self._index_by_goal: Dict[str, List[int]] = {}
        # 统计
        self._added_changes = 0
        self._added_snapshots = 0
        self._overflow_changes = 0
        self._overflow_snapshots = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._changes)

    @property
    def snapshot_size(self) -> int:
        with self._lock:
            return len(self._snapshots)

    @property
    def added_change_count(self) -> int:
        with self._lock:
            return self._added_changes

    @property
    def added_snapshot_count(self) -> int:
        with self._lock:
            return self._added_snapshots

    @property
    def overflow_count(self) -> int:
        with self._lock:
            return self._overflow_changes + self._overflow_snapshots

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def record_change(
        self,
        *,
        goal_id: str,
        change_type: str,
        old_state: Optional[Dict[str, Any]] = None,
        new_state: Optional[Dict[str, Any]] = None,
        source_event_ids: Optional[List[str]] = None,
        source_desire_ids: Optional[List[str]] = None,
        reason: str = "",
        now: Optional[float] = None,
    ) -> Optional[ChangeRecord]:
        """记录一个变化。

        若 change_type 非法,返回 None。
        """
        if change_type not in ALL_GOAL_CHANGE_TYPES:
            return None
        try:
            ts = float(now) if now is not None else 0.0
        except Exception:
            ts = 0.0
        rec = build_change_record(
            goal_id=str(goal_id or ""),
            change_type=str(change_type or GOAL_CHANGE_CREATED),
            old_state=old_state,
            new_state=new_state,
            source_event_ids=list(source_event_ids or []),
            source_desire_ids=list(source_desire_ids or []),
            reason=str(reason or ""),
            now=ts,
        )
        with self._lock:
            if len(self._changes) >= self._capacity:
                self._overflow_changes += 1
            self._changes.append(rec)
            idx_list = self._index_by_goal.setdefault(rec.goal_id, [])
            idx_list.append(len(self._changes) - 1)
            # 限制索引大小
            if len(idx_list) > 64:
                # 简单丢弃最早
                self._index_by_goal[rec.goal_id] = idx_list[-64:]
            self._added_changes += 1
        return rec

    def record_snapshot(
        self,
        goal: Any,
        *,
        now: Optional[float] = None,
    ) -> GoalRecord:
        """记录一个目标快照。"""
        rec = build_record_from_goal(goal, now=now)
        with self._lock:
            if len(self._snapshots) >= self._capacity:
                self._overflow_snapshots += 1
            self._snapshots.append(rec)
            self._added_snapshots += 1
        return rec

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def history(self, *, limit: Optional[int] = None) -> List[ChangeRecord]:
        with self._lock:
            data = list(self._changes)
        if limit is not None and limit >= 0:
            data = data[-limit:]
        return data

    def snapshots(self, *, limit: Optional[int] = None) -> List[GoalRecord]:
        with self._lock:
            data = list(self._snapshots)
        if limit is not None and limit >= 0:
            data = data[-limit:]
        return data

    def history_for_goal(self, goal_id: str, *, limit: Optional[int] = None) -> List[ChangeRecord]:
        if not goal_id:
            return []
        with self._lock:
            data = [c for c in self._changes if c.goal_id == goal_id]
        if limit is not None and limit >= 0:
            data = data[-limit:]
        return data

    def latest_change_for(self, goal_id: str) -> Optional[ChangeRecord]:
        if not goal_id:
            return None
        with self._lock:
            data = [c for c in self._changes if c.goal_id == goal_id]
        if not data:
            return None
        return data[-1]

    def snapshots_for_goal(self, goal_id: str, *, limit: Optional[int] = None) -> List[GoalRecord]:
        if not goal_id:
            return []
        with self._lock:
            data = [s for s in self._snapshots if s.goal_id == goal_id]
        if limit is not None and limit >= 0:
            data = data[-limit:]
        return data

    def count_by_type(self, change_type: str) -> int:
        if change_type not in ALL_GOAL_CHANGE_TYPES:
            return 0
        with self._lock:
            return sum(1 for c in self._changes if c.change_type == change_type)

    def count_for_goal(self, goal_id: str) -> int:
        if not goal_id:
            return 0
        with self._lock:
            return sum(1 for c in self._changes if c.goal_id == goal_id)

    # --------------------------------------------------------
    # replay
    # --------------------------------------------------------
    def replay(
        self,
        *,
        goal_id: Optional[str] = None,
        reverse: bool = False,
    ) -> List[ChangeRecord]:
        """返回所有变化(可按 goal_id 过滤)。"""
        with self._lock:
            data = list(self._changes)
        if goal_id:
            data = [c for c in data if c.goal_id == goal_id]
        if reverse:
            data.reverse()
        return data

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> int:
        with self._lock:
            n = len(self._changes) + len(self._snapshots)
            self._changes.clear()
            self._snapshots.clear()
            self._index_by_goal.clear()
            return n

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            type_counts: Dict[str, int] = {}
            for c in self._changes:
                type_counts[c.change_type] = type_counts.get(c.change_type, 0) + 1
            return {
                "capacity": self._capacity,
                "size": len(self._changes),
                "snapshot_size": len(self._snapshots),
                "added_change_count": self._added_changes,
                "added_snapshot_count": self._added_snapshots,
                "overflow_count": self._overflow_changes + self._overflow_snapshots,
                "type_counts": type_counts,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalHistory(size={len(self._changes)}, capacity={self._capacity}, "
                f"added={self._added_changes})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_goal_history() -> GoalHistory:
    return GoalHistory()


__all__ = [
    # 常量
    "GOAL_HISTORY_SCHEMA_VERSION",
    "DEFAULT_HISTORY_CAPACITY",
    # 类
    "GoalHistory",
    # 工厂
    "build_default_goal_history",
]
