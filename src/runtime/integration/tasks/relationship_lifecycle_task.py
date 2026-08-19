# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/relationship_lifecycle_task.py

Phase 5.0-D2 Step 3: Relationship Lifecycle Task。

职责:
- 把 RelationshipAdapter 接入 Lifecycle Core
- 周期性触发"应触发 relationship evaluate"事件
- 不调用 RelationshipManager 任何业务方法
- 不实现主动行为

约束:
- 通过 RelationshipAdapter 通信
- 不修改 Relationship 子系统源码
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
    IntegrationEvent,
)
from src.runtime.integration.tasks.base_integration_task import BaseIntegrationTask
from src.runtime.integration.tasks.relationship_adapter import RelationshipAdapter


logger = logging.getLogger(__name__)


# ============================================================
# RelationshipLifecycleTask
# ============================================================
class RelationshipLifecycleTask(BaseIntegrationTask):
    """Relationship Lifecycle Task(Skeleton 阶段:仅触发事件)。

    - task_id:  relationship_lifecycle_task
    - owner:    relationship
    - priority: 5(默认,与 Memory 一档)
    - condition: AfterInterval(600)(10 分钟一次,可在构造时覆盖)
    """

    DEFAULT_TASK_ID = "relationship_lifecycle_task"
    DEFAULT_OWNER = "relationship"
    DEFAULT_PRIORITY = 5
    DEFAULT_INTERVAL_SECONDS = 600.0

    def __init__(
        self,
        adapter: Optional[RelationshipAdapter] = None,
        *,
        interval_seconds: Optional[float] = None,
        priority: Optional[int] = None,
    ) -> None:
        if adapter is None:
            adapter = RelationshipAdapter()
        super().__init__(
            task_id=self.DEFAULT_TASK_ID,
            owner=self.DEFAULT_OWNER,
            adapter=adapter,
            display_name="Relationship Lifecycle Task",
            priority=int(priority) if priority is not None else self.DEFAULT_PRIORITY,
            interval_seconds=float(
                interval_seconds
                if interval_seconds is not None
                else self.DEFAULT_INTERVAL_SECONDS
            ),
        )

    def _do_execute(self, context: Any) -> IntegrationEvent:
        """调用 adapter 触发 should_evaluate 事件。

        不读取任何业务状态,仅基于 task_id 生成可追溯事件。
        """
        event = self._adapter.emit_should_evaluate(
            payload={
                "task_id": self.task_id,
                "tick": getattr(context, "tick", None) if context else None,
            },
            related_ids=[self.task_id],
        )
        return event

    @property
    def triggered_event_type(self) -> str:
        return INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE
