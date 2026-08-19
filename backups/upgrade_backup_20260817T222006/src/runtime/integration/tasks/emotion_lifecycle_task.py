# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/emotion_lifecycle_task.py

Phase 5.0-D2 Step 3: Emotion Lifecycle Task。

职责：
- 把 EmotionAdapter 接入 Lifecycle Core
- 周期性触发"应触发 emotion decay"事件
- 不调用 EmotionManager 任何业务方法

约束：
- 通过 EmotionAdapter 通信
- 不修改 src/emotion/**
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.integration.adapters.emotion_adapter import EmotionAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_EMOTION_SHOULD_DECAY,
    IntegrationEvent,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask


logger = logging.getLogger(__name__)


# ============================================================
# EmotionLifecycleTask
# ============================================================
class EmotionLifecycleTask(BaseIntegrationTask):
    """Emotion Lifecycle Task(Skeleton 阶段:仅触发事件)。"""

    DEFAULT_TASK_ID = "emotion_lifecycle_task"
    DEFAULT_OWNER = "emotion"
    DEFAULT_PRIORITY = 7
    DEFAULT_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        adapter: Optional[EmotionAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = EmotionAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Emotion Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )

    def _do_execute(self, context: Any) -> IntegrationEvent:
        event = self._adapter.emit_should_decay(
            payload={
                "task_id": self.task_id,
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
        )
        return event

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_EMOTION_SHOULD_DECAY
