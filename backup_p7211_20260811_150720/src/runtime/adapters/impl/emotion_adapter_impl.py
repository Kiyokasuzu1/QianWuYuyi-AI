# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/emotion_adapter_impl.py

Phase 3.7.2: EmotionAdapterImpl —— Emotion 模块 Adapter 实现

职责：
- 桥接 Runtime Adapter 接口（EmotionAdapter）到 EmotionManager
- 内部依赖：src.emotion.emotion_manager.EmotionManager
- 不修改 EmotionManager

Runtime → EmotionAdapter → EmotionAdapterImpl → EmotionManager

约束：
- Event 转为 EmotionEvent（type/intensity/description/source）
- analyze() 走 EmotionEventDetector / 自定义规则生成情绪分析结果
- update()  调 EmotionManager.update()（应用时间衰减）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.adapters.emotion_adapter import EmotionAdapter
from src.runtime.context import RuntimeContext
from src.runtime.events import Event

logger = logging.getLogger(__name__)


# 简单的 Event → EmotionEvent 适配:用 type 决定 intensity
_DEFAULT_INTENSITY_MAP: Dict[str, float] = {
    "user_input": 0.4,
    "user_praise": 0.8,
    "user_conflict": 0.9,
    "achievement": 0.7,
    "system": 0.2,
    "tick": 0.1,
}


class EmotionAdapterImpl(EmotionAdapter):
    """Emotion 模块 Adapter 实现（Phase 3.7.2 / v1.0）

    桥接 Runtime 抽象接口到现有 EmotionManager。
    """

    name: str = "emotion_adapter_impl"
    schema_version: str = "1.0"

    def __init__(self, emotion_manager: Optional[Any] = None) -> None:
        super().__init__()
        self._manager: Optional[Any] = emotion_manager

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """注入 EmotionManager（默认自建）。"""
        if self._manager is None:
            try:
                from src.emotion.emotion_manager import EmotionManager
                self._manager = EmotionManager()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "EmotionAdapterImpl.attach() 自建 EmotionManager 失败: %s", exc
                )
                self._manager = None
        self._mark_attached()
        self._cache_health({
            "healthy": self._manager is not None,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入（不关闭业务实例,只清空引用）。"""
        self._manager = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        healthy = self.is_attached and self._manager is not None
        result = {
            "healthy": healthy,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Emotion 业务接口实现
    # --------------------------------------------------------
    def analyze(self, event: Event) -> Any:
        """分析事件中的情绪信号。

        1) 把 Event 转换为 EmotionEvent
        2) 调 EmotionManager.process_event 让情绪状态更新
        3) 返回 dict 包含 emotion_event / process_result / intensity
        """
        if not self.is_attached or self._manager is None:
            return {"intensity": 0.0, "reason": "not_attached"}

        try:
            from src.emotion.emotion_event import EmotionEvent

            event_type = getattr(event, "type", "user_input") or "user_input"
            intensity = _DEFAULT_INTENSITY_MAP.get(
                event_type,
                float(getattr(event, "priority", 0)) * 0.5,
            )
            description = ""
            payload = getattr(event, "payload", None)
            if isinstance(payload, dict):
                description = str(payload.get("text", "") or payload.get("description", ""))

            emotion_event = EmotionEvent(
                event_type=event_type,
                intensity=float(intensity),
                description=description,
                source="runtime",
            )

            process_result = self._manager.process_event(emotion_event)
            return {
                "intensity": float(intensity),
                "event_type": event_type,
                "process_result": process_result,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("EmotionAdapterImpl.analyze() 失败: %s", exc)
            return {"intensity": 0.0, "reason": str(exc)}

    def update(self, context: RuntimeContext) -> Any:
        """基于 RuntimeContext 更新情绪状态（应用时间衰减）。"""
        if not self.is_attached or self._manager is None:
            return {"updated": False, "reason": "not_attached"}

        try:
            self._manager.update()
            ctx = self._manager.get_context(influence=0.3)
            return {
                "updated": True,
                "context": ctx,
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("EmotionAdapterImpl.update() 失败: %s", exc)
            return {"updated": False, "reason": str(exc)}
