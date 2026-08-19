# -*- coding: utf-8 -*-
"""
src/runtime/integration/adapters/growth_adapter.py

Phase 5.0-D2 Step 2: GrowthAdapter 骨架。

职责:
- 隔离 IntegrationLayer 与 GrowthLoop 的耦合
- 提供成长系统可用性查询 (is_available)
- 暴露统一事件 emit 入口
- 不调用 GrowthLoop 任何业务方法(Skeleton 阶段)

约束:
- 不 import src.growth.**(避免硬依赖)
- 不修改 GrowthLoop 源码
- 不调用 LLM / DB / Network
- 仅作为接口隔离层存在
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_GROWTH_SHOULD_PROPOSE,
    IntegrationEvent,
)


logger = logging.getLogger(__name__)


# ============================================================
# GrowthAdapter
# ============================================================
class GrowthAdapter(BaseAdapter):
    """GrowthLoop 的薄包装(Skeleton)。"""

    def __init__(
        self,
        *,
        target_resolver: Optional[Callable[[], Optional[Any]]] = None,
        event_emitter: Optional[Callable[[IntegrationEvent], None]] = None,
    ) -> None:
        super().__init__(
            name="growth_adapter",
            owner="growth",
            target_resolver=target_resolver,
            event_emitter=event_emitter,
        )

    # --------------------------------------------------------
    # 事件便捷方法
    # --------------------------------------------------------
    def emit_should_propose(
        self,
        *,
        payload: Optional[dict] = None,
        related_ids: Optional[list] = None,
    ) -> IntegrationEvent:
        """发出"应触发 growth proposal"事件(Skeleton 阶段仅生成不投递业务)。"""
        event = self.make_event(
            INTEGRATION_GROWTH_SHOULD_PROPOSE,
            payload=payload or {},
            related_ids=related_ids or [],
        )
        self.emit(event)
        return event

    def health(self) -> dict:
        """返回 GrowthAdapter 健康状态。"""
        d = self.describe()
        d["target_kind"] = "growth_loop"
        d["skeleton"] = True
        return d


# ============================================================
# 工厂
# ============================================================
def build_default_growth_adapter() -> GrowthAdapter:
    """构造默认的 GrowthAdapter。"""
    return GrowthAdapter()
