# -*- coding: utf-8 -*-
"""
src/runtime/integration/adapters/memory_adapter.py

Phase 5.0-D2 Step 2: MemoryAdapter 骨架。

职责:
- 隔离 IntegrationLayer 与 MemorySystem 的耦合
- 提供内存系统可用性查询 (is_available)
- 暴露统一事件 emit 入口
- 不调用 MemorySystem 任何业务方法(Skeleton 阶段)

约束:
- 不 import src.memory.**(避免硬依赖)
- 不修改 MemorySystem 源码
- 不调用 LLM / DB / Network
- 仅作为接口隔离层存在
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    IntegrationEvent,
)


logger = logging.getLogger(__name__)


# ============================================================
# MemoryAdapter
# ============================================================
class MemoryAdapter(BaseAdapter):
    """MemorySystem 的薄包装(Skeleton)。

    Skeleton 阶段:
    - 不解析 target(默认 None)
    - 不调用任何 MemorySystem 方法
    - 仅作为 Memory 接入 IntegrationLayer 的占位
    """

    def __init__(
        self,
        *,
        target_resolver: Optional[Callable[[], Optional[Any]]] = None,
        event_emitter: Optional[Callable[[IntegrationEvent], None]] = None,
    ) -> None:
        super().__init__(
            name="memory_adapter",
            owner="memory",
            target_resolver=target_resolver,
            event_emitter=event_emitter,
        )

    # --------------------------------------------------------
    # 事件便捷方法(Skeleton 阶段)
    # --------------------------------------------------------
    def emit_should_consolidate(
        self,
        *,
        payload: Optional[dict] = None,
        related_ids: Optional[list] = None,
    ) -> IntegrationEvent:
        """发出"应触发 memory consolidation"事件(Skeleton 阶段仅生成不投递业务)。"""
        event = self.make_event(
            INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            payload=payload or {},
            related_ids=related_ids or [],
        )
        self.emit(event)
        return event

    def health(self) -> dict:
        """返回 MemoryAdapter 健康状态。"""
        d = self.describe()
        d["target_kind"] = "memory_system"
        d["skeleton"] = True
        return d


# ============================================================
# 工厂
# ============================================================
def build_default_memory_adapter() -> MemoryAdapter:
    """构造默认的 MemoryAdapter(不注入 target_resolver,Skeleton 阶段)。"""
    return MemoryAdapter()
