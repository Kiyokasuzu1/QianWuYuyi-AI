# -*- coding: utf-8 -*-
# src/runtime/self_model/emotion_self_model_adapter.py
"""
Phase 4.2.2: EmotionSelfModelAdapter —— Emotion 数据源 Adapter

职责:
- 桥接 Runtime → EmotionSelfModelAdapter → EmotionAdapter
- 从 EmotionAdapter 提取 emotion_state,生成 SelfModel 输入
- 不直接 import EmotionManager / EmotionEngine

数据流:
    Runtime → EmotionSelfModelAdapter.extract_inputs(ctx)
        → EmotionAdapter.update(ctx) → EmotionState
        → SelfModelFoundation.build({
              "current_state": {"emotion": {...}},
              "extra_entries": [SelfModelEntry(kind="trait_change", ...)],
          })

约束:
- 不修改 Emotion 核心模块
- 不直接 import src.emotion.*
- 仅使用 EmotionAdapter 暴露的接口(update)
- 任何异常被静默吞掉,返回 {}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import SelfModelEntry
from src.runtime.self_model.self_model_source_adapter import (
    SelfModelSourceAdapter,
    SOURCE_TYPE_EMOTION,
)


logger = logging.getLogger(__name__)


EMOTION_SELF_MODEL_ADAPTER_NAME = "emotion_self_model_adapter"


class EmotionSelfModelAdapter(SelfModelSourceAdapter):
    """Emotion 数据源 Adapter(Phase 4.2.2 / v1.0)。

    从 EmotionAdapter 提取 SelfModel 输入:
    - current_state:    Dict[str, Any]   # 整体 emotion state(读时快照)
    - extra_entries:    List[SelfModelEntry]  # 高强度情绪事件标注
    - trait_states:     Dict[str, Dict]  # 情绪参数 → 稳定特质(可选)

    构造参数:
    - emotion_adapter: 任意满足 EmotionAdapter 接口的对象
                       若为 None,extract_inputs 返回 {}
    - high_intensity_threshold: 触发 extra entry 的情绪强度阈值(默认 0.7)
    """

    name: str = EMOTION_SELF_MODEL_ADAPTER_NAME
    schema_version: str = "1.0"

    def __init__(
        self,
        emotion_adapter: Optional[Any] = None,
        high_intensity_threshold: float = 0.7,
    ) -> None:
        super().__init__()
        self._emotion_adapter: Optional[Any] = emotion_adapter
        self._high_intensity_threshold: float = float(
            high_intensity_threshold
        )
        self._last_state: Optional[Any] = None

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_EMOTION

    @property
    def emotion_adapter(self) -> Optional[Any]:
        return self._emotion_adapter

    def set_emotion_adapter(self, emotion_adapter: Optional[Any]) -> None:
        """运行时注入 EmotionAdapter。"""
        self._emotion_adapter = emotion_adapter

    @property
    def last_state(self) -> Optional[Any]:
        return self._last_state

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": self.source_type,
            "has_emotion_adapter": self._emotion_adapter is not None,
        })

    # --------------------------------------------------------
    # 核心:extract_inputs
    # --------------------------------------------------------
    def extract_inputs(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """从 EmotionAdapter / RuntimeContext 提取 SelfModel 结构化输入。

        行为:
        1) 优先尝试 emotion_adapter.update(ctx) 获取最新 emotion state
        2) 回退:从 ctx.emotion_state 读取(已存在的 snapshot)
        3) 把 emotion state 标准化为 Dict
        4) 高强度情绪 → extra_entries (kind="trait_change")
        5) 整体 → current_state["emotion"]

        Args:
            ctx: Runtime 共享上下文

        Returns:
            {
                "current_state": Dict[str, Any],
                "extra_entries": List[SelfModelEntry],
                "trait_states":  Dict[str, Dict],
            }
        """
        if not self.is_attached:
            return {}

        emotion_state: Any = None

        # 1) 尝试调 emotion_adapter.update(ctx)
        # 注:不再内部 try/except —— 让 emotion_adapter 抛出的异常向上传播,
        # 由基类 safe_extract 统一捕获并返回 {}。这样 Registry 才能正确追踪错误。
        if self._emotion_adapter is not None and ctx is not None:
            update_method = getattr(self._emotion_adapter, "update", None)
            if update_method is not None and callable(update_method):
                emotion_state = update_method(ctx)

        # 2) 回退:从 ctx 读取
        if emotion_state is None and ctx is not None:
            try:
                emotion_state = getattr(ctx, "emotion_state", None)
            except Exception:  # noqa: BLE001
                emotion_state = None

        self._last_state = emotion_state
        if emotion_state is None:
            return {}

        norm = self._normalize_state(emotion_state)
        if not isinstance(norm, dict):
            return {}

        # 高强度情绪 → extra entries
        extra_entries: List[SelfModelEntry] = []
        try:
            intensity = float(norm.get("intensity", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            intensity = 0.0
        if intensity >= self._high_intensity_threshold:
            try:
                summary = (
                    f"high_intensity_emotion:"
                    f" {norm.get('dominant_emotion') or norm.get('emotion', '')}"
                    f" ({intensity:.2f})"
                )
                extra_entries.append(SelfModelEntry(
                    kind="trait_change",
                    summary=summary[:200],
                    sources=["emotion_adapter"],
                    confidence=min(1.0, intensity),
                    meta={
                        "source": "emotion",
                        "intensity": intensity,
                        "dominant_emotion": norm.get("dominant_emotion")
                            or norm.get("emotion"),
                    },
                ))
            except Exception:  # noqa: BLE001
                pass

        # 情绪派生 trait(可选): 平静度 / 警觉度
        trait_states: Dict[str, Dict[str, Any]] = {}
        try:
            calmness = float(norm.get("calmness", 0.5) or 0.5)
            trait_states["calmness"] = {
                "current_value": calmness,
                "direction": "stable",
                "stability": 0.4,
                "confidence": 0.5,
                "sources": ["emotion"],
                "last_updated": "",
            }
        except Exception:  # noqa: BLE001
            pass

        return {
            "current_state": {"emotion": dict(norm)},
            "extra_entries": extra_entries,
            "trait_states": trait_states,
        }

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _normalize_state(self, state: Any) -> Optional[Dict[str, Any]]:
        """把 emotion state 各种形态标准化为 Dict。"""
        if isinstance(state, dict):
            return state
        if hasattr(state, "to_dict") and callable(state.to_dict):
            try:
                d = state.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        if hasattr(state, "__dict__"):
            try:
                return dict(state.__dict__)
            except Exception:  # noqa: BLE001
                return None
        return None

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def high_intensity_threshold(self) -> float:
        return self._high_intensity_threshold

    def set_high_intensity_threshold(self, threshold: float) -> None:
        self._high_intensity_threshold = float(threshold)


__all__ = [
    "EmotionSelfModelAdapter",
    "EMOTION_SELF_MODEL_ADAPTER_NAME",
]
