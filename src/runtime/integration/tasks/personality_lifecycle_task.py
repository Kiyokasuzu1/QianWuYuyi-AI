# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/personality_lifecycle_task.py

Phase 5.0-D2 Step 3: Personality Lifecycle Task。

职责：
- 把 PersonalityAdapter 接入 Lifecycle Core
- 周期性触发"应触发 personality sync"事件
- 不调用 PersonalityResolver 任何业务方法

约束：
- 通过 PersonalityAdapter 通信
- 不修改 src/personality/**
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.integration.adapters.personality_adapter import PersonalityAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_PERSONALITY_SHOULD_SYNC,
    IntegrationEvent,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask


logger = logging.getLogger(__name__)


# ============================================================
# PersonalityLifecycleTask
# ============================================================
class PersonalityLifecycleTask(BaseIntegrationTask):
    """Personality Lifecycle Task(Skeleton 阶段:仅触发事件)。"""

    DEFAULT_TASK_ID = "personality_lifecycle_task"
    DEFAULT_OWNER = "personality"
    DEFAULT_PRIORITY = 3
    DEFAULT_INTERVAL_SECONDS = 3600.0  # 1 小时

    def __init__(
        self,
        adapter: Optional[PersonalityAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = PersonalityAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Personality Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )

    def _do_execute(self, context: Any) -> IntegrationEvent:
        event = self._adapter.emit_should_sync(
            payload={
                "task_id": self.task_id,
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
        )
        return event

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_PERSONALITY_SHOULD_SYNC
