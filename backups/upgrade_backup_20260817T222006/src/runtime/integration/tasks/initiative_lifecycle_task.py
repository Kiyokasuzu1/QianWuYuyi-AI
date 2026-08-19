# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/initiative_lifecycle_task.py

Phase 5.0-D3-C: Initiative Lifecycle Task。

职责:
- 把 InitiativeAdapter 接入 LifecycleManager
- 默认 interval 600 秒
- 执行 tick(), 触发 InitiativeAdapter.tick
- 通过 Adapter 发出 INITIATIVE_CREATED / INTEREST_SIGNAL_CREATED 事件
- 异常隔离(RUN / SKIP / ERROR)

约束:
- 不调用业务模块
- 不修改 Lifecycle Core
- 时间全部 Clock 注入
- enable_initiative = False 时 SKIP
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask
from src.runtime.initiative.adapter.initiative_adapter import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_REFLECTIONS,
    InitiativeAdapter,
)
from src.runtime.lifecycle.internal.clock import Clock, SystemClock


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
INITIATIVE_LIFECYCLE_TASK_SCHEMA_VERSION = "1.0"


# ============================================================
# InitiativeLifecycleTask
# ============================================================
class InitiativeLifecycleTask(BaseIntegrationTask):
    """Initiative 周期 Task。

    流程:
    1) tick: 由 LifecycleManager 调度
    2) execute: 调用 adapter.tick(events, reflections)
    3) emit: 通过 adapter 发出 INITIATIVE_CREATED / INTEREST_SIGNAL_CREATED
    4) metrics: 上报 signal_count / action_count / queue_size

    状态:
    - RUN: 有 pending 或外部输入
    - SKIP: 引擎 disabled 或无任何输入
    - ERROR: 异常
    """

    DEFAULT_TASK_ID = "initiative_lifecycle_task"
    DEFAULT_OWNER = "initiative"
    DEFAULT_PRIORITY = 6
    DEFAULT_INTERVAL_SECONDS = 600.0  # 10 分钟
    DEFAULT_MAX_EVENTS = DEFAULT_MAX_EVENTS
    DEFAULT_MAX_REFLECTIONS = DEFAULT_MAX_REFLECTIONS

    def __init__(
        self,
        adapter: Optional[InitiativeAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
        max_events: Optional[int] = None,
        max_reflections: Optional[int] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        if adapter is None:
            adapter = InitiativeAdapter()
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
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Initiative Lifecycle Task",
            priority=resolved_priority,
            interval_seconds=resolved_interval,
        )
        self._max_events = resolved_max_events
        self._max_reflections = resolved_max_refs
        self._clock: Clock = clock or SystemClock()
        # 统计
        self._tick_run_count = 0
        self._tick_skipped_count = 0
        self._tick_error_count = 0
        self._last_signal_count = 0
        self._last_action_count = 0
        self._last_queued_count = 0
        self._last_tick_at = 0.0
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def initiative_adapter(self) -> InitiativeAdapter:
        return self._adapter  # type: ignore[return-value]

    @property
    def max_events(self) -> int:
        return self._max_events

    @property
    def max_reflections(self) -> int:
        return self._max_reflections

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
    def last_signal_count(self) -> int:
        with self._lock:
            return self._last_signal_count

    @property
    def last_action_count(self) -> int:
        with self._lock:
            return self._last_action_count

    @property
    def last_queued_count(self) -> int:
        with self._lock:
            return self._last_queued_count

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
        return INITIATIVE_LIFECYCLE_TASK_SCHEMA_VERSION

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_INITIATIVE_CREATED

    # --------------------------------------------------------
    # 事件输入
    # --------------------------------------------------------
    def push_events(self, events: List[IntegrationEvent]) -> int:
        return self.initiative_adapter.push_events(events)

    def push_reflections(self, reflections: List[Any]) -> int:
        return self.initiative_adapter.push_reflections(reflections)

    def clear_pending(self) -> int:
        return self.initiative_adapter.clear_pending()

    @property
    def pending_event_count(self) -> int:
        return self.initiative_adapter.pending_event_count

    @property
    def pending_reflection_count(self) -> int:
        return self.initiative_adapter.pending_reflection_count

    # --------------------------------------------------------
    # 实际执行
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        try:
            now = self._safe_now()
            with self._lock:
                self._last_tick_at = now
            # 评估是否值得 tick
            should = self.initiative_adapter.evaluate()
            if not should:
                with self._lock:
                    self._tick_skipped_count += 1
                return self._make_skip_event(context, reason="no_input_or_disabled")
            # 执行
            output = self.initiative_adapter.tick()
            if output is None:
                with self._lock:
                    self._tick_error_count += 1
                    self._last_error = "adapter 返回 None"
                return self._make_error_event(context, "adapter_returned_none")
            with self._lock:
                self._tick_run_count += 1
                self._last_signal_count = len(output.created_signals)
                self._last_action_count = len(output.created_actions)
                self._last_queued_count = len(output.queued_actions)
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
                "kind": "initiative",
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
                "kind": "initiative",
                "status": "error",
                "error": str(error)[:512],
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
            metadata={"schema_version": self.schema_version},
        )

    def _make_completed_event(self, context: Any, output: Any) -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_INITIATIVE_CREATED,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "initiative",
                "status": "run",
                "signal_count": int(len(output.created_signals)),
                "action_count": int(len(output.created_actions)),
                "queued_count": int(len(output.queued_actions)),
                "filtered_count": int(len(output.filtered_actions)),
                "deferred_count": int(len(output.deferred_actions)),
                "discarded_count": int(len(output.discarded_actions)),
                "signal_ids": [s.signal_id for s in output.created_signals],
                "action_ids": [a.action_id for a in output.created_actions],
                "queued_action_ids": [a.action_id for a in output.queued_actions],
                "skipped": bool(output.skipped),
                "skip_reason": str(output.skip_reason or ""),
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id] + [a.action_id for a in output.created_actions],
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
            "last_signal_count": self._last_signal_count,
            "last_action_count": self._last_action_count,
            "last_queued_count": self._last_queued_count,
            "last_tick_at": self._last_tick_at,
            "max_events": self._max_events,
            "max_reflections": self._max_reflections,
            "last_error": self._last_error,
            "adapter": self.initiative_adapter.describe(),
        })
        return d

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InitiativeLifecycleTask(task_id={self.task_id!r}, "
                f"run={self._tick_run_count}, skip={self._tick_skipped_count}, "
                f"error={self._tick_error_count})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_initiative_lifecycle_task() -> InitiativeLifecycleTask:
    return InitiativeLifecycleTask()


__all__ = [
    "INITIATIVE_LIFECYCLE_TASK_SCHEMA_VERSION",
    "InitiativeLifecycleTask",
    "build_default_initiative_lifecycle_task",
]
