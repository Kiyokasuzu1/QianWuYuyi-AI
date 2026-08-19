# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/relationship_adapter.py

Phase 5.0-D2 Step 3: RelationshipAdapter 骨架(为 Relationship 系统补全 Adapter)。

职责:
- 隔离 IntegrationLayer 与 RelationshipManager / RelationshipModel 的耦合
- 提供关系系统可用性查询 (is_available)
- 暴露统一事件 emit 入口
- 不调用 Relationship 任何业务方法(Skeleton 阶段)

约束:
- 不 import 业务子系统(避免硬依赖)
- 不修改 Relationship 任何源码
- 不调用 LLM / DB / Network
- 仅作为接口隔离层存在

注意:
- D2 Step 2 阶段只创建了 4 个 Adapter(Memory/Emotion/Growth/Personality)
- D2 Step 3 阶段为 Relationship 系统补全 Adapter
- 文件位置位于 tasks/ 目录,符合 Step 3 阶段"新增代码只允许位于 src/runtime/integration/tasks/" 的约束
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
    IntegrationEvent,
)


logger = logging.getLogger(__name__)


# ============================================================
# RelationshipAdapter
# ============================================================
class RelationshipAdapter(BaseAdapter):
    """RelationshipManager 的薄包装(Skeleton)。

    Skeleton 阶段:
    - 不解析 target(默认 None)
    - 不调用任何 Relationship 方法
    - 仅作为 Relationship 接入 IntegrationLayer 的占位
    """

    def __init__(
        self,
        *,
        target_resolver: Optional[Callable[[], Optional[Any]]] = None,
        event_emitter: Optional[Callable[[IntegrationEvent], None]] = None,
    ) -> None:
        super().__init__(
            name="relationship_adapter",
            owner="relationship",
            target_resolver=target_resolver,
            event_emitter=event_emitter,
        )

    # --------------------------------------------------------
    # 事件便捷方法(Skeleton 阶段)
    # --------------------------------------------------------
    def emit_should_evaluate(
        self,
        *,
        payload: Optional[dict] = None,
        related_ids: Optional[list] = None,
    ) -> IntegrationEvent:
        """发出"应触发 relationship evaluate"事件(Skeleton 阶段仅生成不投递业务)。"""
        event = self.make_event(
            INTEGRATION_RELATIONSHIP_SHOULD_EVALUATE,
            payload=payload or {},
            related_ids=related_ids or [],
        )
        self.emit(event)
        return event

    def health(self) -> dict:
        """返回 RelationshipAdapter 健康状态。"""
        d = self.describe()
        d["target_kind"] = "relationship_manager"
        d["skeleton"] = True
        return d


# ============================================================
# 工厂
# ============================================================
def build_default_relationship_adapter() -> RelationshipAdapter:
    """构造默认的 RelationshipAdapter(不注入 target_resolver,Skeleton 阶段)。"""
    return RelationshipAdapter()
