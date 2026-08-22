# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/reflection_lifecycle_task.py

Phase 5.0-D3-B: Reflection Lifecycle Task。

职责:
- 把 ReflectionAdapter 接入 Lifecycle Core
- 周期性触发反思
- 通过 Adapter 发出 IntegrationEvent
- 不直接修改 SelfModel
- 不调用业务模块

约束:
- 仅依赖 Runtime 内部模块
- 通过 ReflectionAdapter 通信
- 异常隔离
- 时间全部 Clock 注入
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_REFLECTION_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask
from src.runtime.reflection.adapter.reflection_adapter import ReflectionAdapter
from src.runtime.reflection.reflection_engine import ReflectionEngine
from src.runtime.reflection.reflection_history import ReflectionHistory
from src.runtime.reflection.reflection_result import (
    ALL_REFLECTION_TYPES,
    ReflectionResult,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION = "1.0"


# ============================================================
# ReflectionLifecycleTask
# ============================================================
class ReflectionLifecycleTask(BaseIntegrationTask):
    """Reflection 周期 Task。

    流程:
    1) tick: 由 LifecycleManager 调度
    2) execute: 调用 adapter.reflect(events) 触发反思
    3) emit: 通过 adapter 发出 REFLECTION_COMPLETED 事件
    4) metrics: 上报 insight_count / suggestion_count
    """

    DEFAULT_TASK_ID = "reflection_lifecycle_task"
    DEFAULT_OWNER = "reflection"
    DEFAULT_PRIORITY = 5
    DEFAULT_INTERVAL_SECONDS = 300.0  # 5 分钟
    DEFAULT_MAX_EVENTS = 256

    def __init__(
        self,
        adapter: Optional[ReflectionAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
        max_events: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = ReflectionAdapter()
        # 解析 interval_seconds,非法值回退到默认值
        if interval_seconds is None:
            resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        else:
            try:
                resolved_interval = float(interval_seconds)
            except Exception:
                resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        if resolved_interval < 0:
            resolved_interval = float(self.DEFAULT_INTERVAL_SECONDS)
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Reflection Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=resolved_interval,
        )
        try:
            self._max_events = int(max_events) if max_events is not None else self.DEFAULT_MAX_EVENTS
        except Exception:
            self._max_events = self.DEFAULT_MAX_EVENTS
        if self._max_events < 0:
            self._max_events = 0
        # pending 事件缓冲
        self._pending: list = []
        # 统计
        self._tick_reflection_count: int = 0
        self._tick_skipped_count: int = 0
        self._tick_error_count: int = 0
        self._last_reflection_id: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def reflection_adapter(self) -> Any:
        return self._adapter

    @property
    def max_events(self) -> int:
        return self._max_events

    @property
    def tick_reflection_count(self) -> int:
        return self._tick_reflection_count

    @property
    def tick_skipped_count(self) -> int:
        return self._tick_skipped_count

    @property
    def tick_error_count(self) -> int:
        return self._tick_error_count

    @property
    def last_reflection_id(self) -> str:
        return self._last_reflection_id

    @property
    def schema_version(self) -> str:
        return REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION

    # --------------------------------------------------------
    # 实际执行
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        """执行反思。

        1) 准备一个空事件 placeholder
        2) 通过 adapter 触发 reflect(空事件列表作为占位)
           实际事件应通过 push_events 注入
        """
        # 注意: 真实事件流应由宿主在 execute 前注入
        # 本 Task 维护一个内部 _pending_events buffer
        events = self._drain_pending_events()
        if not events:
            self._tick_skipped_count += 1
            return self._make_skip_event(context)
        try:
            results = self.reflection_adapter.reflect(events)
        except Exception as exc:  # noqa: BLE001
            self._tick_error_count += 1
            return self._make_error_event(context, str(exc))
        if not results:
            self._tick_skipped_count += 1
            return self._make_skip_event(context)
        self._tick_reflection_count += 1
        self._last_reflection_id = results[-1].reflection_id
        return self._make_completed_event(context, results)

    # --------------------------------------------------------
    # 事件注入
    # --------------------------------------------------------
    def push_events(self, events: List[IntegrationEvent]) -> int:
        """注入待处理事件(由宿主在 tick 前调用)。"""
        from src.runtime.integration.integration_event import IntegrationEvent
        if not events:
            return 0
        n = 0
        for e in events:
            if isinstance(e, IntegrationEvent):
                if self._max_events > 0 and len(self._pending) >= self._max_events:
                    # 简单淘汰最早的
                    try:
                        self._pending.pop(0)
                    except Exception:
                        self._pending = self._pending[-self._max_events:]
                self._pending.append(e)
                n += 1
        return n

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def _drain_pending_events(self) -> List[IntegrationEvent]:
        try:
            evs = list(self._pending)
            self._pending = []
            return evs
        except Exception:
            return []

    def clear_pending(self) -> int:
        try:
            n = len(self._pending)
            self._pending = []
            return n
        except Exception:
            return 0

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _make_skip_event(self, context: Any) -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "reflection",
                "status": "skip",
                "reason": "no_events",
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
            metadata={"schema_version": REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION},
        )

    def _make_error_event(self, context: Any, error: str) -> IntegrationEvent:
        return make_integration_event(
            event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "kind": "reflection",
                "status": "error",
                "error": str(error)[:512],
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
            metadata={"schema_version": REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION},
        )

    def _make_completed_event(
        self,
        context: Any,
        results: List[ReflectionResult],
    ) -> IntegrationEvent:
        if not results:
            return self._make_skip_event(context)
        last = results[-1]
        total_insights = sum(r.insight_count for r in results)
        total_suggestions = sum(r.suggestion_count for r in results)
        return make_integration_event(
            event_type=INTEGRATION_REFLECTION_COMPLETED,
            source=self._owner,
            payload={
                "task_id": self.task_id,
                "reflection_id": last.reflection_id,
                "reflection_type": last.reflection_type,
                "source_event_ids": list(last.source_event_ids),
                "insight_count": total_insights,
                "suggestion_count": total_suggestions,
                "confidence": float(last.confidence),
                "evidence_strength": float(last.evidence_strength),
                "tick": getattr(context, "tick", None) if context else None,
                "result_ids": [r.reflection_id for r in results],
                # v1.1 Phase 2.2: 分析内容富化（additive payload, 供宿主转换为
                # growth 经历记录; Reflection 只产分析结果, 不直接修改状态）
                # 防御: 老式/fake ReflectionResult 可能没有 insights/suggested_changes。
                "insights": [
                    str(i.description)
                    for i in (getattr(last, "insights", None) or [])
                ],
                "suggested_changes": [
                    (s.to_dict() if hasattr(s, "to_dict") else str(s))
                    for s in (getattr(last, "suggested_changes", None) or [])
                ],
            },
            related_ids=[last.reflection_id] + list(last.source_event_ids),
            metadata={"schema_version": REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION},
        )

    # 必须存在,父类用它生成完整 LifecycleResult
    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_REFLECTION_COMPLETED

    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "schema_version": REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION,
            "tick_reflection_count": self._tick_reflection_count,
            "tick_skipped_count": self._tick_skipped_count,
            "tick_error_count": self._tick_error_count,
            "pending_count": self.pending_count,
            "max_events": self._max_events,
            "last_reflection_id": self._last_reflection_id,
        })
        return d


__all__ = [
    "REFLECTION_LIFECYCLE_TASK_SCHEMA_VERSION",
    "ReflectionLifecycleTask",
]
