# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_manager.py

Phase 5.0-D3-D: GoalManager 目标中心管理。

职责:
- 管理 GoalState / GoalPlan / Desire
- 提供 CRUD:create_goal / update_goal / pause_goal / complete_goal / history
- 线程安全
- ChangeRecord 记录
- Clock 注入

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 不可执行任何动作
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.goal.desire import (
    DESIRE_TREND_NEW,
    DEFAULT_DESIRE_TREND,
    Desire,
    DesireRegistry,
    build_desire,
)
from src.runtime.goal.goal_history import GoalHistory
from src.runtime.goal.goal_planner import (
    GoalPlan,
    GoalPlanner,
    build_default_goal_planner,
)
from src.runtime.goal.goal_record import (
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_PROMOTED,
    GOAL_CHANGE_UPDATED,
    ChangeRecord,
    GoalRecord,
)
from src.runtime.goal.goal_state import (
    ALL_GOAL_STATUSES,
    DEFAULT_GOAL_TYPE,
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_PAUSED,
    GOAL_TYPE_PERSONAL_GROWTH,
    GoalState,
    build_candidate_goal,
    is_valid_goal_transition,
)
from src.runtime.initiative.interest_signal import InterestSignal
from src.runtime.initiative.possible_action import PossibleAction
from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.reflection.reflection_result import ReflectionResult


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_MANAGER_SCHEMA_VERSION = "1.0"
DEFAULT_MAX_GOALS = 1024
DEFAULT_MAX_PLANS = 1024


# ============================================================
# GoalManager
# ============================================================
class GoalManager:
    """目标中心管理器(线程安全)。"""

    def __init__(
        self,
        *,
        max_goals: int = DEFAULT_MAX_GOALS,
        max_plans: int = DEFAULT_MAX_PLANS,
        history_capacity: int = 1024,
        planner: Optional[GoalPlanner] = None,
        history: Optional[GoalHistory] = None,
        desire_registry: Optional[DesireRegistry] = None,
        clock: Optional[Clock] = None,
        enabled: bool = True,
        name: str = "goal_manager",
    ) -> None:
        self._name = str(name or "goal_manager")
        try:
            self._max_goals = max(1, int(max_goals))
        except Exception:
            self._max_goals = DEFAULT_MAX_GOALS
        try:
            self._max_plans = max(1, int(max_plans))
        except Exception:
            self._max_plans = DEFAULT_MAX_PLANS
        self._planner = planner if isinstance(planner, GoalPlanner) else build_default_goal_planner()
        self._history = history if isinstance(history, GoalHistory) else GoalHistory(capacity=history_capacity)
        self._desire_registry = (
            desire_registry if isinstance(desire_registry, DesireRegistry) else DesireRegistry()
        )
        self._clock: Clock = clock or SystemClock()
        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        # 数据
        self._goals: Dict[str, GoalState] = {}
        self._plans: Dict[str, GoalPlan] = {}
        # 统计
        self._goal_count_total = 0
        self._plan_count_total = 0
        self._desire_count_total = 0
        self._created_count = 0
        self._updated_count = 0
        self._activated_count = 0
        self._paused_count = 0
        self._completed_count = 0
        self._abandoned_count = 0
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def schema_version(self) -> str:
        return GOAL_MANAGER_SCHEMA_VERSION

    @property
    def planner(self) -> GoalPlanner:
        return self._planner

    @property
    def history(self) -> GoalHistory:
        return self._history

    @property
    def desire_registry(self) -> DesireRegistry:
        return self._desire_registry

    @property
    def goal_count(self) -> int:
        with self._lock:
            return len(self._goals)

    @property
    def plan_count(self) -> int:
        with self._lock:
            return len(self._plans)

    @property
    def goal_count_total(self) -> int:
        with self._lock:
            return self._goal_count_total

    @property
    def plan_count_total(self) -> int:
        with self._lock:
            return self._plan_count_total

    @property
    def desire_count_total(self) -> int:
        with self._lock:
            return self._desire_count_total

    @property
    def created_count(self) -> int:
        with self._lock:
            return self._created_count

    @property
    def updated_count(self) -> int:
        with self._lock:
            return self._updated_count

    @property
    def activated_count(self) -> int:
        with self._lock:
            return self._activated_count

    @property
    def paused_count(self) -> int:
        with self._lock:
            return self._paused_count

    @property
    def completed_count(self) -> int:
        with self._lock:
            return self._completed_count

    @property
    def abandoned_count(self) -> int:
        with self._lock:
            return self._abandoned_count

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    # --------------------------------------------------------
    # Desire
    # --------------------------------------------------------
    def add_desire(self, desire: Desire) -> bool:
        if not isinstance(desire, Desire):
            return False
        if not desire.has_evidence():
            return False
        ok = self._desire_registry.add(desire)
        if ok:
            with self._lock:
                self._desire_count_total += 1
        return ok

    def get_desire(self, desire_id: str) -> Optional[Desire]:
        return self._desire_registry.get(desire_id)

    def list_desires(self) -> List[Desire]:
        return self._desire_registry.list()

    def find_desires_by_topic(self, topic: str) -> List[Desire]:
        if not topic:
            return []
        return self._desire_registry.find_by_topic(topic)

    def remove_desire(self, desire_id: str) -> bool:
        return self._desire_registry.remove(desire_id)

    # --------------------------------------------------------
    # Goal CRUD
    # --------------------------------------------------------
    def create_goal(
        self,
        goal: GoalState,
        *,
        plan: bool = True,
        now: Optional[float] = None,
    ) -> GoalState:
        """注册一个 GoalState(状态必须是 candidate)。"""
        try:
            if not isinstance(goal, GoalState):
                raise ValueError("goal 不是 GoalState")
            if not goal.is_valid():
                raise ValueError("goal 缺少 evidence 或字段非法")
            if goal.status != GOAL_STATUS_CANDIDATE:
                raise ValueError(f"create_goal 期望 status=candidate,实际 {goal.status}")
            try:
                ts = float(now) if now is not None else self._safe_now()
            except Exception:
                ts = 0.0
            with self._lock:
                if not self._enabled:
                    self._last_error = "manager disabled"
                    return goal
                if len(self._goals) >= self._max_goals:
                    # 简单淘汰
                    try:
                        oldest_key = next(iter(self._goals))
                        self._goals.pop(oldest_key, None)
                    except Exception:
                        pass
                self._goals[goal.goal_id] = goal
                self._created_count += 1
                self._goal_count_total += 1
                # 记录 change
            self._history.record_change(
                goal_id=goal.goal_id,
                change_type=GOAL_CHANGE_CREATED,
                old_state=None,
                new_state=goal.to_dict(),
                source_event_ids=list(goal.source_event_ids or []),
                source_desire_ids=list(goal.source_desire_ids or []),
                reason=goal.reason or "created",
                now=ts,
            )
            self._history.record_snapshot(goal, now=ts)
            # 可选:同步创建 plan
            if plan:
                self.create_plan(goal, now=ts)
            return goal
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"create_goal 异常: {exc}"
            return goal

    def update_goal(
        self,
        goal_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[float] = None,
        importance: Optional[float] = None,
        reason: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[GoalState]:
        try:
            try:
                ts = float(now) if now is not None else self._safe_now()
            except Exception:
                ts = 0.0
            with self._lock:
                g = self._goals.get(goal_id)
                if g is None:
                    return None
                if g.is_terminal():
                    return None
                old_state = g.to_dict()
                if title is not None:
                    g.title = title
                if description is not None:
                    g.description = description
                if priority is not None:
                    g.update_priority(priority, now=ts)
                if importance is not None:
                    g.importance = max(0.0, min(1.0, float(importance)))
                if reason is not None:
                    g.reason = reason
                g.updated_at = ts
                new_state = g.to_dict()
                self._updated_count += 1
            self._history.record_change(
                goal_id=goal_id,
                change_type=GOAL_CHANGE_UPDATED,
                old_state=old_state,
                new_state=new_state,
                source_event_ids=list(g.source_event_ids or []),
                source_desire_ids=list(g.source_desire_ids or []),
                reason=reason or "updated",
                now=ts,
            )
            self._history.record_snapshot(g, now=ts)
            return g
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"update_goal 异常: {exc}"
            return None

    def activate_goal(self, goal_id: str, *, now: Optional[float] = None) -> bool:
        return self._transition_goal(goal_id, GOAL_STATUS_ACTIVE, GOAL_CHANGE_ACTIVATED, now=now)

    def pause_goal(self, goal_id: str, *, now: Optional[float] = None) -> bool:
        return self._transition_goal(goal_id, GOAL_STATUS_PAUSED, GOAL_CHANGE_PAUSED, now=now)

    def complete_goal(self, goal_id: str, *, now: Optional[float] = None) -> bool:
        return self._transition_goal(goal_id, GOAL_STATUS_COMPLETED, GOAL_CHANGE_COMPLETED, now=now)

    def abandon_goal(self, goal_id: str, *, reason: str = "", now: Optional[float] = None) -> bool:
        ok = self._transition_goal(goal_id, GOAL_STATUS_ABANDONED, GOAL_CHANGE_ABANDONED, now=now, reason=reason or "abandoned")
        return ok

    def _transition_goal(
        self,
        goal_id: str,
        target_status: str,
        change_type: str,
        *,
        now: Optional[float] = None,
        reason: str = "",
    ) -> bool:
        try:
            try:
                ts = float(now) if now is not None else self._safe_now()
            except Exception:
                ts = 0.0
            with self._lock:
                g = self._goals.get(goal_id)
                if g is None:
                    return False
                if not is_valid_goal_transition(g.status, target_status):
                    return False
                old_state = g.to_dict()
                # 调用状态方法
                if target_status == GOAL_STATUS_ACTIVE:
                    g.activate(now=ts)
                elif target_status == GOAL_STATUS_PAUSED:
                    g.pause(now=ts)
                elif target_status == GOAL_STATUS_COMPLETED:
                    g.complete(now=ts)
                elif target_status == GOAL_STATUS_ABANDONED:
                    g.abandon(now=ts)
                else:
                    return False
                new_state = g.to_dict()
                # 统计
                if change_type == GOAL_CHANGE_ACTIVATED:
                    self._activated_count += 1
                elif change_type == GOAL_CHANGE_PAUSED:
                    self._paused_count += 1
                elif change_type == GOAL_CHANGE_COMPLETED:
                    self._completed_count += 1
                elif change_type == GOAL_CHANGE_ABANDONED:
                    self._abandoned_count += 1
            self._history.record_change(
                goal_id=goal_id,
                change_type=change_type,
                old_state=old_state,
                new_state=new_state,
                source_event_ids=list(g.source_event_ids or []),
                source_desire_ids=list(g.source_desire_ids or []),
                reason=reason or f"transition to {target_status}",
                now=ts,
            )
            self._history.record_snapshot(g, now=ts)
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"_transition_goal 异常: {exc}"
            return False

    def get_goal(self, goal_id: str) -> Optional[GoalState]:
        with self._lock:
            return self._goals.get(goal_id)

    def list_goals(
        self,
        *,
        status: Optional[str] = None,
        goal_type: Optional[str] = None,
    ) -> List[GoalState]:
        if status is not None and status not in ALL_GOAL_STATUSES:
            return []
        with self._lock:
            data = list(self._goals.values())
        if status is not None:
            data = [g for g in data if g.status == status]
        if goal_type is not None:
            data = [g for g in data if g.goal_type == goal_type]
        return data

    def remove_goal(self, goal_id: str) -> bool:
        with self._lock:
            existed = self._goals.pop(goal_id, None)
        if existed is None:
            return False
        # 不删除 history(snapshot/change 保留)
        return True

    # --------------------------------------------------------
    # Plan
    # --------------------------------------------------------
    def create_plan(self, goal: GoalState, *, now: Optional[float] = None) -> GoalPlan:
        plan = self._planner.plan(goal, now=now)
        with self._lock:
            if len(self._plans) >= self._max_plans:
                try:
                    oldest_key = next(iter(self._plans))
                    self._plans.pop(oldest_key, None)
                except Exception:
                    pass
            self._plans[plan.plan_id] = plan
            self._plan_count_total += 1
        return plan

    def get_plan(self, plan_id: str) -> Optional[GoalPlan]:
        with self._lock:
            return self._plans.get(plan_id)

    def get_plans_for_goal(self, goal_id: str) -> List[GoalPlan]:
        if not goal_id:
            return []
        with self._lock:
            return [p for p in self._plans.values() if p.goal_id == goal_id]

    def list_plans(self) -> List[GoalPlan]:
        with self._lock:
            return list(self._plans.values())

    # --------------------------------------------------------
    # 历史
    # --------------------------------------------------------
    def history(self) -> List[ChangeRecord]:
        return self._history.history()

    def history_for(self, goal_id: str) -> List[ChangeRecord]:
        return self._history.history_for_goal(goal_id)

    def snapshots(self) -> List[GoalRecord]:
        return self._history.snapshots()

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "enabled": self._enabled,
                "schema_version": self.schema_version,
                "goal_count": len(self._goals),
                "plan_count": len(self._plans),
                "desire_size": self._desire_registry.size,
                "goal_count_total": self._goal_count_total,
                "plan_count_total": self._plan_count_total,
                "desire_count_total": self._desire_count_total,
                "created_count": self._created_count,
                "updated_count": self._updated_count,
                "activated_count": self._activated_count,
                "paused_count": self._paused_count,
                "completed_count": self._completed_count,
                "abandoned_count": self._abandoned_count,
                "last_error": self._last_error,
                "planner": self._planner.describe(),
                "history": self._history.describe(),
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalManager(name={self._name!r}, enabled={self._enabled}, "
                f"goals={len(self._goals)}, plans={len(self._plans)})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_goal_manager() -> GoalManager:
    return GoalManager()


__all__ = [
    # 常量
    "GOAL_MANAGER_SCHEMA_VERSION",
    "DEFAULT_MAX_GOALS",
    "DEFAULT_MAX_PLANS",
    # 类
    "GoalManager",
    # 工厂
    "build_default_goal_manager",
]
