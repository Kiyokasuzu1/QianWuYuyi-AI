# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_lifecycle_task.py

Phase 5.0-D3-A: Self Model System —— SelfModelLifecycleTask

职责:
- 把 SelfModelAdapter 接入 Lifecycle Core
- 周期性触发"self_model snapshot refresh"事件 + 内部变更
- 触发 interest 自然衰减
- 通过 Adapter 发出 IntegrationEvent,被 EventLog/EventStore 收集

约束:
- 不调用业务模块
- 不修改 Lifecycle Core
- 通过 SelfModelAdapter 通信
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Sequence

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask

from src.runtime.self_model.self_model_adapter import (
    SELF_MODEL_ADAPTER_SCHEMA_VERSION,
    SELF_MODEL_CHANGE_RECORDED,
    SELF_MODEL_SNAPSHOT_REFRESHED,
    SelfModelAdapter,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION = "1.0"


# ============================================================
# SelfModelLifecycleTask
# ============================================================
class SelfModelLifecycleTask(BaseIntegrationTask):
    """SelfModel 周期 Task。

    设计:
    - 通过注入的 SelfModelAdapter 通信
    - 周期触发:
      1) emit SELF_MODEL_SNAPSHOT_REFRESHED 事件
      2) 触发 manager.refresh(产生 ChangeRecord)
      3) 触发 interest 自然衰减(可选)
    - 不依赖具体业务模块
    """

    DEFAULT_TASK_ID = "self_model_lifecycle_task"
    DEFAULT_OWNER = "self_model"
    DEFAULT_PRIORITY = 4
    DEFAULT_INTERVAL_SECONDS = 600.0  # 10 分钟
    DEFAULT_DECAY_SECONDS = 60.0  # 单 tick 推进 60 秒

    def __init__(
        self,
        adapter: Optional[SelfModelAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
        enable_decay: bool = True,
        decay_seconds_per_tick: float = DEFAULT_DECAY_SECONDS,
    ) -> None:
        if adapter is None:
            adapter = SelfModelAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="SelfModel Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )
        self._enable_decay = bool(enable_decay)
        self._decay_seconds_per_tick = float(decay_seconds_per_tick or 0.0)
        # Task 自有统计
        self._tick_refresh_count: int = 0
        self._tick_decay_count: int = 0
        self._tick_change_count: int = 0

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    @property
    def self_model_adapter(self) -> SelfModelAdapter:
        return self._adapter  # type: ignore[return-value]

    @property
    def tick_refresh_count(self) -> int:
        return self._tick_refresh_count

    @property
    def tick_decay_count(self) -> int:
        return self._tick_decay_count

    @property
    def tick_change_count(self) -> int:
        return self._tick_change_count

    @property
    def enable_decay(self) -> bool:
        return self._enable_decay

    @property
    def decay_seconds_per_tick(self) -> float:
        return self._decay_seconds_per_tick

    @property
    def schema_version(self) -> str:
        return SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        """执行一次 self_model tick。

        流程:
        1) emit SELF_MODEL_SNAPSHOT_REFRESHED 事件
        2) 如果 manager 可用,触发 refresh(产生 ChangeRecord)
        3) 如果 enable_decay + manager 可用,触发 interest decay
        4) 返回 SELF_MODEL_CHANGE_RECORDED(或 SELF_MODEL_SNAPSHOT_REFRESHED)事件
        """
        adp: SelfModelAdapter = self._adapter  # type: ignore[assignment]
        # 1) emit refresh event
        tick_id = ""
        try:
            if context is not None and getattr(context, "task_id", None):
                tick_id = str(context.task_id)
        except Exception:
            tick_id = ""
        ev_refresh = adp.emit_snapshot_refreshed(
            reason=f"tick:{self.task_id}",
            evidence_event_ids=[f"tick:{tick_id}"],
        )
        if ev_refresh is None:
            # fallback:直接 make
            ev_refresh = make_integration_event(
                event_type=SELF_MODEL_SNAPSHOT_REFRESHED,
                source=self._owner,
                payload={"task_id": self.task_id},
                related_ids=[self.task_id],
            )
            adp.emit(ev_refresh)

        # 2) 触发 manager refresh
        manager = adp.manager
        if manager is not None and manager.is_started:
            try:
                manager.refresh(
                    evidence_event_ids=[ev_refresh.event_id],
                    change_source="lifecycle_task",
                    reason=f"task:{self.task_id}",
                )
                self._tick_refresh_count += 1
            except Exception as exc:  # noqa: BLE001
                self._record_error(f"manager.refresh failed: {exc}")

            # 3) interest decay
            if self._enable_decay and self._decay_seconds_per_tick > 0:
                try:
                    n = manager.decay_interests(
                        delta_seconds=self._decay_seconds_per_tick,
                        evidence_event_ids=[ev_refresh.event_id],
                        change_source="lifecycle_task",
                        reason=f"decay:tick:{self.task_id}",
                    )
                    self._tick_decay_count += n
                except Exception as exc:  # noqa: BLE001
                    self._record_error(f"decay_interests failed: {exc}")

        # 4) 优先发出 SELF_MODEL_CHANGE_RECORDED(若 adapter 内部已发出,本任务不重复)
        # 这里直接复用 emit 出去的 refresh 事件作为本 tick 标识事件
        return ev_refresh

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _record_error(self, msg: str) -> None:
        # 委托 BaseIntegrationTask 的 last_error
        try:
            self._last_error = str(msg or "")  # type: ignore[attr-defined]
        except Exception:
            pass

    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "schema_version": SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION,
            "tick_refresh_count": self._tick_refresh_count,
            "tick_decay_count": self._tick_decay_count,
            "tick_change_count": self._tick_change_count,
            "enable_decay": self._enable_decay,
            "decay_seconds_per_tick": self._decay_seconds_per_tick,
        })
        return d


# ============================================================
# 工厂
# ============================================================
def build_default_self_model_lifecycle_task(
    *,
    adapter: Optional[SelfModelAdapter] = None,
    interval_seconds: Optional[float] = None,
) -> SelfModelLifecycleTask:
    """构造默认 SelfModelLifecycleTask。"""
    return SelfModelLifecycleTask(
        adapter=adapter,
        interval_seconds=interval_seconds,
    )


__all__ = [
    "SELF_MODEL_LIFECYCLE_TASK_SCHEMA_VERSION",
    "SelfModelLifecycleTask",
    "build_default_self_model_lifecycle_task",
]
