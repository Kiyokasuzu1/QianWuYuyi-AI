# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/goal_lifecycle_task.py

Phase 5.0-D3-D: Goal Lifecycle Task。

职责:
- 把 GoalAdapter 接入 LifecycleManager
- 默认 interval 1800 秒
- 执行 tick(), 触发 GoalAdapter.tick
- 通过 Adapter 发出 DESIRE_CREATED / GOAL_CREATED / GOAL_PLAN_CREATED 事件
- 异常隔离(RUN / SKIP / ERROR)

约束:
- 不调用业务模块
- 不修改 Lifecycle Core
- 时间全部 Clock 注入
- enable_goal = False 时 SKIP
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from src.runtime.goal.adapter.goal_adapter import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_REFLECTIONS,
    DEFAULT_MAX_SIGNALS,
    DEFAULT_MAX_ACTIONS,
    DEFAULT_MAX_DESIRES,
    GoalAdapter,
)
from src.runtime.goal.desire import Desire
from src.runtime.initiative.interest_signal import InterestSignal
from src.runtime.initiative.possible_action import PossibleAction
from src.runtime.integration.integration_event import (
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask
from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.reflection.reflection_result import ReflectionResult


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_LIFECYCLE_TASK_SCHEMA_VERSION = "1.0"


# ============================================================
# GoalLifecycleTask
# ============================================================
class GoalLifecycleTask(BaseIntegrationTask):
    """Goal 周期 Task。

    流程:
    1) tick: 由 LifecycleManager 调度
    2) execute: 调用 adapter.tick(events, reflections, signals, actions, desires)
    3) emit: 通过 adapter 发出 DESIRE_CREATED / GOAL_CREATED / GOAL_PLAN_CREATED
    4) metrics: 上报 desire_count / goal_count / plan_count

    状态:
    - RUN: 有 pending 或外部输入
    - SKIP: 适配器 disabled 或无任何输入
    - ERROR: 异常
    """

    DEFAULT_TASK_ID = "goal_lifecycle_task"
    DEFAULT_OWNER = "goal"
    DEFAULT_PRIORITY = 5
    DEFAULT_INTERVAL_SECONDS = 1800.0  # 30 分钟
    DEFAULT_MAX_EVENTS = DEFAULT_MAX_EVENTS
    DEFAULT_MAX_REFLECTIONS = DEFAULT_MAX_REFLECTIONS
    DEFAULT_MAX_SIGNALS = DEFAULT_MAX_SIGNALS
    DEFAULT_MAX_ACTIONS = DEFAULT_MAX_ACTIONS
    DEFAULT_MAX_DESIRES = DEFAULT_MAX_DESIRES

    def __init__(
        self,
        adapter: Optional[GoalAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
        max_events: Optional[int] = None,
        max_reflections: Optional[int] = None,
        max_signals: Optional[int] = None,
        max_actions: Optional[int] = None,
        max_desires: Optional[int] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        if adapter is None:
            adapter = GoalAdapter()
        # interval 解析
        if interval_seconds is None:
            resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        else:
            try:
                resolved_interval = float(interval_seconds)
            except Exception:
                resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        if resolved_interval < 0:
            resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        try:
            resolved_priority = int(priority) if priority is not None else self.DEFAULT_PRIORITY
        except Exception:
            resolved_priority = self.DEFAULT_PRIORITY
        try:
            resolved_max_events = int(max_events) if max_events is not None else self.DEFAULT_MAX_EVENTS
        except Exception:
            resolved_max_events = self.DEFAULT_MAX_EVENTS
        if resolved_max_events < 0:
            resolved_max_events = 0
        try:
            resolved_max_refs = int(max_reflections) if max_reflections is not None else self.DEFAULT_MAX_REFLECTIONS
        except Exception:
            resolved_max_refs = self.DEFAULT_MAX_REFLECTIONS
        if resolved_max_refs < 0:
            resolved_max_refs = 0
        try:
            resolved_max_sigs = int(max_signals) if max_signals is not None else self.DEFAULT_MAX_SIGNALS
        except Exception:
            resolved_max_sigs = self.DEFAULT_MAX_SIGNALS
        if resolved_max_sigs < 0:
            resolved_max_sigs = 0
        try:
            resolved_max_acts = int(max_actions) if max_actions is not None else self.DEFAULT_MAX_ACTIONS
        except Exception:
            resolved_max_acts = self.DEFAULT_MAX_ACTIONS
        if resolved_max_acts < 0:
            resolved_max_acts = 0
        try:
            resolved_max_des = int(max_desires) if max_desires is not None else self.DEFAULT_MAX_DESIRES
        except Exception:
            resolved_max_des = self.DEFAULT_MAX_DESIRES
        if resolved_max_des < 0:
            resolved_max_des = 0
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Goal Lifecycle Task",
            priority=resolved_priority,
            interval_seconds=resolved_interval,
        )
        self._max_events = resolved_max_events
        self._max_reflections = resolved_max_refs
        self._max_signals = resolved_max_sigs
        self._max_actions = resolved_max_acts
        self._max_desires = resolved_max_des
        self._clock: Clock = clock or SystemClock()
        # 统计
        self._tick_run_count = 0
        self._tick_skipped_count = 0
        self._tick_error_count = 0
        self._last_desire_count = 0
        self._last_goal_count = 0
        self._last_plan_count = 0
        self._last_tick_at = 0.0
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def goal_adapter(self) -> GoalAdapter:
        return self._adapter  # type: ignore[return-value]

    @property
    def max_events(self) -> int:
        return self._max_events

    @property
    def max_reflections(self) -> int:
        return self._max_reflections

    @property
    def max_signals(self) -> int:
        return self._max_signals

    @property
    def max_actions(self) -> int:
        return self._max_actions

    @property
    def max_desires(self) -> int:
        return self._max_desires

    @property
    def tick_run_count(self) -> int:
        with self._lock:
            return self._tick_run_count

    @property
    def tick_skipped_count(self) -> int:
        with self._lock:
            return self._tick_skipped_count

    @property
    def tick_error_count(self) -> int:
        with self._lock:
            return self._tick_error_count

    @property
    def last_desire_count(self) -> int:
        with self._lock:
            return self._last_desire_count

    @property
    def last_goal_count(self) -> int:
        with self._lock:
            return self._last_goal_count

    @property
    def last_plan_count(self) -> int:
        with self._lock:
            return self._last_plan_count

    @property
    def last_tick_at(self) -> float:
        with self._lock:
            return self._last_tick_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return GOAL_LIFECYCLE_TASK_SCHEMA_VERSION

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_GOAL_CREATED

    # --------------------------------------------------------
    # 事件输入
    # --------------------------------------------------------
    def push_events(self, events: List[IntegrationEvent]) -> int:
        return self.goal_adapter.push_events(events)

    def push_reflections(self, reflections: List[ReflectionResult]) -> int:
        return self.goal_adapter.push_reflections(reflections)

    def push_signals(self, signals: List[InterestSignal]) -> int:
        return self.goal_adapter.push_signals(signals)

    def push_actions(self, actions: List[PossibleAction]) -> int:
        return self.goal_adapter.push_actions(actions)

    def push_desires(self, desires: List[Desire]) -> int:
        return self.goal_adapter.push_desires(desires)

    def clear_pending(self) -> int:
        return self.goal_adapter.clear_pending()

    @property
    def pending_event_count(self) -> int:
        return self.goal_adapter.pending_event_count

    @property
    def pending_reflection_count(self) -> int:
        return self.goal_adapter.pending_reflection_count

    @property
    def pending_signal_count(self) -> int:
        return self.goal_adapter.pending_signal_count

    @property
    def pending_action_count(self) -> int:
        return self.goal_adapter.pending_action_count

    @property
    def pending_desire_count(self) -> int:
        return self.goal_adapter.pending_desire_count

    # --------------------------------------------------------
    # 实际执行
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        try:
            now = self._safe_now()
            with self._lock:
                self._last_tick_at = now
            # 评估是否值得 tick
            should = self.goal_adapter.evaluate()
            if not should:
                with self._lock:
                    self._tick_skipped_count += 1
                return self._make_skip_event(context, reason="no_input_or_disabled")
            # 执行
            output = self.goal_adapter.tick()
            if output is None:
                with self._lock:
                    self._tick_error_count += 1
                    self._last_error = "adapter 返回 None"
                return self._make_error_event(context, "adapter_returned_none")
            with self._lock:
                self._tick_run_count += 1
                self._last_desire_count = output.desires_added
                self._last_goal_count = output.goals_created
                self._last_plan_count = output.plans_created
            return self._make_completed_event(context, output)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._tick_error_count += 1
                self._last_error = str(exc)
            return self._make_error_event(context, str(exc))

    # --------------------------------------------------------
    # 事件构造
    # --------------------------------------------------------
    def _make_skip_event(self, context: Any, *, reason: str = "no_input") -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "goal",
                "status": "skip",
                "reason": str(reason)[:256],
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
            metadata={"schema_version": self.schema_version},
        )

    def _make_error_event(self, context: Any, error: str) -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "goal",
                "status": "error",
                "error": str(error)[:512],
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
            metadata={"schema_version": self.schema_version},
        )

    def _make_completed_event(self, context: Any, output: Any) -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_GOAL_CREATED,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "goal",
                "status": "run",
                "desires_added": int(output.desires_added),
                "goals_created": int(output.goals_created),
                "plans_created": int(output.plans_created),
                "candidates": [g.goal_id for g in output.candidates],
                "skipped_count": len(output.skipped),
                "skipped_reason": dict(output.skipped_reason),
                "desire_event_count": len(output.desire_events),
                "goal_event_count": len(output.goal_events),
                "plan_event_count": len(output.plan_events),
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id] + [g.goal_id for g in output.candidates],
            metadata={"schema_version": self.schema_version},
        )

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "schema_version": self.schema_version,
            "tick_run_count": self._tick_run_count,
            "tick_skipped_count": self._tick_skipped_count,
            "tick_error_count": self._tick_error_count,
            "last_desire_count": self._last_desire_count,
            "last_goal_count": self._last_goal_count,
            "last_plan_count": self._last_plan_count,
            "last_tick_at": self._last_tick_at,
            "max_events": self._max_events,
            "max_reflections": self._max_reflections,
            "max_signals": self._max_signals,
            "max_actions": self._max_actions,
            "max_desires": self._max_desires,
            "last_error": self._last_error,
            "adapter": self.goal_adapter.describe(),
        })
        return d

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalLifecycleTask(task_id={self.task_id!r}, "
                f"run={self._tick_run_count}, skip={self._tick_skipped_count}, "
                f"error={self._tick_error_count})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_goal_lifecycle_task() -> GoalLifecycleTask:
    return GoalLifecycleTask()


__all__ = [
    "GOAL_LIFECYCLE_TASK_SCHEMA_VERSION",
    "GoalLifecycleTask",
    "build_default_goal_lifecycle_task",
]
