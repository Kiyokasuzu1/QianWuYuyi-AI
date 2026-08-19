# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/memory_lifecycle_task.py

Phase 5.0-D2 Step 3: Memory Lifecycle Task。

职责：
- 把 MemoryAdapter 接入 Lifecycle Core
- 周期性触发"应触发 memory consolidation"事件
- 不调用 MemorySystem 任何业务方法
- 不实现主动行为

约束：
- 通过 MemoryAdapter 通信
- 不修改 src/memory/**
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.integration.adapters.memory_adapter import MemoryAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    IntegrationEvent,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask


logger = logging.getLogger(__name__)


# ============================================================
# MemoryLifecycleTask
# ============================================================
class MemoryLifecycleTask(BaseIntegrationTask):
    """Memory Lifecycle Task(Skeleton 阶段:仅触发事件)。

    - task_id:  memory_lifecycle_task
    - owner:    memory
    - priority: 6(默认)
    - condition: AfterInterval(300)(5 分钟一次,可在构造时覆盖)
    """

    DEFAULT_TASK_ID = "memory_lifecycle_task"
    DEFAULT_OWNER = "memory"
    DEFAULT_PRIORITY = 6
    DEFAULT_INTERVAL_SECONDS = 300.0

    def __init__(
        self,
        adapter: Optional[MemoryAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = MemoryAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Memory Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )

    # --------------------------------------------------------
    # 实际执行(子类实现)
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        """调用 adapter 触发 should_consolidate 事件。

        不读取任何业务状态,仅基于 task_id 生成可追溯事件。
        """
        event = self._adapter.emit_should_consolidate(
            payload={
                "task_id": self.task_id,
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
        )
        return event

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_MEMORY_SHOULD_CONSOLIDATE
