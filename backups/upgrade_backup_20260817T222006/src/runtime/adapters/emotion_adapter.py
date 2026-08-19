# src/runtime/adapters/emotion_adapter.py
"""
EmotionAdapter —— Emotion 模块 Adapter 接口骨架（Phase 3.7.1）

仅定义接口，不实现业务逻辑。
Runtime 通过 EmotionAdapter 访问 Emotion 模块，避免直接 import EmotionManager / EmotionEngine。

依赖：仅 stdlib + 同包 base + RuntimeContext / Event
禁止：import src.emotion.*（业务实现）
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.context import RuntimeContext
from src.runtime.events import Event


class EmotionAdapter(AdapterBase):
    """Emotion 模块 Adapter（v1.0）

    职责：
    - 封装 Emotion 系统的访问
    - 提供统一的情绪分析与更新入口
    - Runtime 不直接依赖 EmotionManager

    接口：
    - analyze(event)   - 分析事件中的情绪信号
    - update(context)  - 基于 RuntimeContext 更新情绪状态
    """

    name: str = "emotion_adapter"
    schema_version: str = "1.0"

    def __init__(self) -> None:
        super().__init__()

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Emotion 模块（设计阶段仅标记状态）。"""
        self._mark_attached()
        self._cache_health(
            {"healthy": True, "name": self.name, "schema_version": self.schema_version}
        )

    def detach(self) -> None:
        """解除接入。"""
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        result = {
            "healthy": self.is_attached,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Emotion 业务接口（接口骨架，不实现）
    # --------------------------------------------------------
    def analyze(self, event: Event) -> Any:
        """分析事件中的情绪信号（设计阶段仅返回 None）。

        Args:
            event: Runtime Event

        Returns:
            情绪分析结果（业务实现时返回 EmotionAnalysis）
        """
        raise NotImplementedError(
            "EmotionAdapter.analyze() 接口未实现（设计阶段）"
        )

    def update(self, context: RuntimeContext) -> Any:
        """基于 RuntimeContext 更新情绪状态（设计阶段仅返回 None）。

        Args:
            context: Runtime 共享上下文

        Returns:
            更新后的情绪状态（业务实现时返回 EmotionState）
        """
        raise NotImplementedError(
            "EmotionAdapter.update() 接口未实现（设计阶段）"
        )
