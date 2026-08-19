# -*- coding: utf-8 -*-
"""
src/runtime/goal/adapter/goal_event_emitter.py

Phase 5.0-D3-D: Goal Event Emitter。

职责:
- 把 Goal/Desire/Plan 产出转成 IntegrationEvent
- Desire -> INTEGRATION_DESIRE_CREATED
- Goal(created/updated) -> INTEGRATION_GOAL_CREATED / INTEGRATION_GOAL_UPDATED
- Plan -> INTEGRATION_GOAL_PLAN_CREATED
- 严格只发射事件,不执行

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from src.runtime.goal.desire import Desire
from src.runtime.goal.goal_planner import GoalPlan
from src.runtime.goal.goal_state import (
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_COMPLETED,
    GoalState,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_GOAL_PLAN_CREATED,
    INTEGRATION_GOAL_UPDATED,
    IntegrationEvent,
    make_integration_event,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_EVENT_EMITTER_SCHEMA_VERSION = "1.0"

DEFAULT_GOAL_EMITTER_OWNER = "goal"
DEFAULT_EMITTER_SOURCE = "goal_event_emitter"

GOAL_LIFECYCLE_KIND_DESIRE = "desire"
GOAL_LIFECYCLE_KIND_GOAL = "goal"
GOAL_LIFECYCLE_KIND_PLAN = "plan"


# ============================================================
# GoalEventEmitter
# ============================================================
class GoalEventEmitter:
    """Goal/Desire/Plan -> IntegrationEvent 转换器。"""

    def __init__(
        self,
        *,
        owner: str = DEFAULT_GOAL_EMITTER_OWNER,
        source: str = DEFAULT_EMITTER_SOURCE,
    ) -> None:
        self._owner = str(owner or DEFAULT_GOAL_EMITTER_OWNER)
        self._source = str(source or DEFAULT_EMITTER_SOURCE)
        self._lock = threading.RLock()
        # 统计
        self._emitted_count = 0
        self._emitted_desire = 0
        self._emitted_goal = 0
        self._emitted_plan = 0
        self._last_event_id = ""
        self._last_error = ""

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def source(self) -> str:
        return self._source

    @property
    def emitted_count(self) -> int:
        with self._lock:
            return self._emitted_count

    @property
    def emitted_desire_count(self) -> int:
        with self._lock:
            return self._emitted_desire

    @property
    def emitted_goal_count(self) -> int:
        with self._lock:
            return self._emitted_goal

    @property
    def emitted_plan_count(self) -> int:
        with self._lock:
            return self._emitted_plan

    @property
    def last_event_id(self) -> str:
        with self._lock:
            return self._last_event_id

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return GOAL_EVENT_EMITTER_SCHEMA_VERSION

    # --------------------------------------------------------
    # Desire -> Event
    # --------------------------------------------------------
    def emit_desire(self, desire: Any) -> Optional[IntegrationEvent]:
        if not isinstance(desire, Desire):
            with self._lock:
                self._last_error = "emit_desire 收到非 Desire"
            return None
        if not desire.has_evidence():
            with self._lock:
                self._last_error = "desire 缺少 evidence"
            return None
        try:
            ev = make_integration_event(
                event_type=INTEGRATION_DESIRE_CREATED,
                source=self._owner,
                payload={
                    "desire_id": desire.desire_id,
                    "topic": desire.topic,
                    "strength": float(desire.strength),
                    "trend": desire.trend,
                    "reason": desire.reason,
                    "confidence": float(desire.confidence),
                    "supporting_signal_ids": list(desire.supporting_signal_ids),
                    "supporting_reflection_ids": list(desire.supporting_reflection_ids),
                    "created_at": float(desire.created_at),
                },
                related_ids=[desire.desire_id] + list(desire.supporting_signal_ids) + list(desire.supporting_reflection_ids),
                metadata={
                    "schema_version": GOAL_EVENT_EMITTER_SCHEMA_VERSION,
                    "kind": GOAL_LIFECYCLE_KIND_DESIRE,
                },
            )
            with self._lock:
                self._emitted_count += 1
                self._emitted_desire += 1
                self._last_event_id = ev.event_id
            return ev
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emit_desire 异常: {exc}"
            return None

    def emit_desires(self, desires: List[Any]) -> List[IntegrationEvent]:
        out: List[IntegrationEvent] = []
        for d in desires or []:
            ev = self.emit_desire(d)
            if ev is not None:
                out.append(ev)
        return out

    # --------------------------------------------------------
    # Goal -> Event
    # --------------------------------------------------------
    def emit_goal(self, goal: Any, *, lifecycle: str = "created") -> Optional[IntegrationEvent]:
        if not isinstance(goal, GoalState):
            with self._lock:
                self._last_error = "emit_goal 收到非 GoalState"
            return None
        if not goal.has_evidence():
            with self._lock:
                self._last_error = "goal 缺少 evidence"
            return None
        try:
            lc = (lifecycle or "created").lower()
            if lc in ("created", "candidate", "promoted", "regenerated"):
                event_type = INTEGRATION_GOAL_CREATED
            else:
                event_type = INTEGRATION_GOAL_UPDATED
            ev = make_integration_event(
                event_type=event_type,
                source=self._owner,
                payload={
                    "goal_id": goal.goal_id,
                    "title": goal.title,
                    "description": goal.description,
                    "goal_type": goal.goal_type,
                    "status": goal.status,
                    "priority": float(goal.priority),
                    "importance": float(goal.importance),
                    "confidence": float(goal.confidence),
                    "source_event_ids": list(goal.source_event_ids),
                    "source_desire_ids": list(goal.source_desire_ids),
                    "reason": goal.reason,
                    "lifecycle": lc,
                },
                related_ids=[goal.goal_id] + list(goal.source_event_ids) + list(goal.source_desire_ids),
                metadata={
                    "schema_version": GOAL_EVENT_EMITTER_SCHEMA_VERSION,
                    "kind": GOAL_LIFECYCLE_KIND_GOAL,
                },
            )
            with self._lock:
                self._emitted_count += 1
                self._emitted_goal += 1
                self._last_event_id = ev.event_id
            return ev
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emit_goal 异常: {exc}"
            return None

    def emit_goals(self, goals: List[Any], *, lifecycle: str = "created") -> List[IntegrationEvent]:
        out: List[IntegrationEvent] = []
        for g in goals or []:
            ev = self.emit_goal(g, lifecycle=lifecycle)
            if ev is not None:
                out.append(ev)
        return out

    # --------------------------------------------------------
    # Plan -> Event
    # --------------------------------------------------------
    def emit_plan(self, plan: Any) -> Optional[IntegrationEvent]:
        if not isinstance(plan, GoalPlan):
            with self._lock:
                self._last_error = "emit_plan 收到非 GoalPlan"
            return None
        try:
            ev = make_integration_event(
                event_type=INTEGRATION_GOAL_PLAN_CREATED,
                source=self._owner,
                payload={
                    "plan_id": plan.plan_id,
                    "goal_id": plan.goal_id,
                    "title": plan.title,
                    "step_count": len(plan.steps),
                    "progress": float(plan.progress),
                    "step_ids": [s.step_id for s in plan.steps],
                    "step_titles": [s.title for s in plan.steps],
                },
                related_ids=[plan.plan_id, plan.goal_id] if plan.goal_id else [plan.plan_id],
                metadata={
                    "schema_version": GOAL_EVENT_EMITTER_SCHEMA_VERSION,
                    "kind": GOAL_LIFECYCLE_KIND_PLAN,
                },
            )
            with self._lock:
                self._emitted_count += 1
                self._emitted_plan += 1
                self._last_event_id = ev.event_id
            return ev
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emit_plan 异常: {exc}"
            return None

    def emit_plans(self, plans: List[Any]) -> List[IntegrationEvent]:
        out: List[IntegrationEvent] = []
        for p in plans or []:
            ev = self.emit_plan(p)
            if ev is not None:
                out.append(ev)
        return out

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "owner": self._owner,
                "source": self._source,
                "emitted_count": self._emitted_count,
                "emitted_desire": self._emitted_desire,
                "emitted_goal": self._emitted_goal,
                "emitted_plan": self._emitted_plan,
                "last_event_id": self._last_event_id,
                "last_error": self._last_error,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalEventEmitter(owner={self._owner!r}, "
                f"emitted={self._emitted_count})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_goal_event_emitter() -> GoalEventEmitter:
    return GoalEventEmitter()


__all__ = [
    "GOAL_EVENT_EMITTER_SCHEMA_VERSION",
    "DEFAULT_GOAL_EMITTER_OWNER",
    "DEFAULT_EMITTER_SOURCE",
    "GOAL_LIFECYCLE_KIND_DESIRE",
    "GOAL_LIFECYCLE_KIND_GOAL",
    "GOAL_LIFECYCLE_KIND_PLAN",
    "GoalEventEmitter",
    "build_default_goal_event_emitter",
]
