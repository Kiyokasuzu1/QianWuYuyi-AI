# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/growth_lifecycle_task.py

Phase 5.0-D2 Step 3: Growth Lifecycle Task。

职责：
- 把 GrowthAdapter 接入 Lifecycle Core
- 周期性触发"应触发 growth proposal"事件
- 不调用 GrowthLoop 任何业务方法

约束：
- 通过 GrowthAdapter 通信
- 不修改 src/growth/**
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.integration.adapters.growth_adapter import GrowthAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    IntegrationEvent,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask


logger = logging.getLogger(__name__)


# ============================================================
# GrowthLifecycleTask
# ============================================================
class GrowthLifecycleTask(BaseIntegrationTask):
    """Growth Lifecycle Task(Skeleton 阶段:仅触发事件)。"""

    DEFAULT_TASK_ID = "growth_lifecycle_task"
    DEFAULT_OWNER = "growth"
    DEFAULT_PRIORITY = 4
    DEFAULT_INTERVAL_SECONDS = 1800.0  # 30 分钟

    def __init__(
        self,
        adapter: Optional[GrowthAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = GrowthAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Growth Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )

    def _do_execute(self, context: Any) -> IntegrationEvent:
        event = self._adapter.emit_should_propose(
            payload={
                "task_id": self.task_id,
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
        )
        return event

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_GROWTH_SHOULD_PROPOSE
