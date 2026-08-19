# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision_adapter.py

Phase 4.0.0: Vision Adapter —— 接口骨架

职责:
- 提供视觉理解 Adapter 的抽象接口
- 暴露 analyze(observation) 方法,将 Observation 转为 Fact
- Phase 4.0.0: 不实现真实视觉模型
- 后续 Phase 4.2+ 才允许接入真实视觉模型

约束:
- 禁止 import 任何视觉模型 SDK (LLM Vision / 物体检测 / 图像分类)
- 仅 stdlib + 同包抽象接口

后续 Phase 接入建议:
- Phase 4.2.x: 远程视觉 API
- Phase 4.3.x: 本地视觉模型
- Phase 4.4.x: 专用检测模型
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.perception.adapter import (
    PerceptionAdapter,
    observation_to_fact,
    observations_to_facts,
    ALLOWED_OBS_TO_FACT_SOURCES,
)
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
)
from src.runtime.perception.fact_source import FactSource


logger = logging.getLogger(__name__)


VISION_ADAPTER_SCHEMA_VERSION = "1.0"


# Vision 事件常量
VISION_EVENT_ANALYZED = "vision_analyzed"
VISION_EVENT_UNAVAILABLE = "vision_unavailable"
VISION_EVENT_ERROR = "vision_error"


class VisionAdapter(PerceptionAdapter):
    """视觉理解 Adapter 骨架 (Phase 4.0.0 v1.0)。

    字段:
    - name:               str = "vision_adapter"
    - observation_kind:   ObservationKind.CAMERA (默认,也可改为 SCREEN)
    - schema_version:     str = "1.0"

    接口:
    - attach()             - 接入 Runtime
    - detach()             - 解除接入
    - health_check()       - 健康检查
    - analyze(observation) - 将 Observation 转为 Fact 列表
    - observe()            - 默认返回 None (Vision 依赖其他 Adapter 提供 Observation)

    Phase 4.0.0 行为:
    - analyze() 强制 NotImplementedError
    - observe() 强制返回 None (本阶段不主动 capture)
    """

    name: str = "vision_adapter"
    observation_kind: ObservationKind = ObservationKind.CAMERA
    schema_version: str = VISION_ADAPTER_SCHEMA_VERSION

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__()
        self._config: Dict[str, Any] = dict(config or {})
        # Vision 特有状态
        self._model_loaded: bool = False
        self._analyze_count: int = 0
        self._last_analyze_at: Optional[str] = None

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。Phase 4.0.0: 不加载任何模型。"""
        self._model_loaded = False  # Phase 4.0.0 默认未加载
        self._mark_attached()
        logger.debug(
            "VisionAdapter attached (Phase 4.0.0 skeleton; "
            "no real vision model loaded)"
        )

    def detach(self) -> None:
        """解除接入。"""
        self._model_loaded = False
        self._mark_detached()
        logger.debug("VisionAdapter detached")

    def health_check(self) -> Dict[str, Any]:
        """健康检查。"""
        return {
            "healthy": self._model_loaded,
            "name": self.name,
            "schema_version": self.schema_version,
            "observation_kind": self.observation_kind.value,
            "details": {
                "model_loaded": self._model_loaded,
                "analyze_count": self._analyze_count,
                "last_analyze_at": self._last_analyze_at,
                "phase": "4.0.0-skeleton",
            },
        }

    # --------------------------------------------------------
    # 观察接口
    # --------------------------------------------------------
    def observe(self) -> Optional[Observation]:
        """Phase 4.0.0: Vision Adapter 本身不主动 capture。

        Vision 模型是被动分析器,依赖 ScreenObservationAdapter /
        AudioAdapter 等提供 Observation。
        本阶段 observe() 返回 None。
        """
        return None

    def analyze(
        self,
        observation: Observation,
    ) -> List[Any]:
        """将视觉 Observation 转为 Fact 列表。

        Phase 4.0.0: 强制 NotImplementedError。
        后续 Phase 4.2+ 才会接入真实视觉理解能力。

        Args:
            observation: 来自 ScreenObservationAdapter 或其他视觉源的 Observation

        Returns:
            List[Fact]  (Phase 4.0.0 抛异常)
        """
        raise NotImplementedError(
            "VisionAdapter.analyze() is not implemented in Phase 4.0.0; "
            "real vision model will be added in Phase 4.2+"
        )

    def analyze_safe(
        self,
        observation: Observation,
    ) -> List[Any]:
        """analyze() 的安全封装:NotImplementedError 时返回 []。"""
        try:
            return self.analyze(observation)
        except NotImplementedError:
            logger.debug(
                "VisionAdapter.analyze() not implemented in Phase 4.0.0; "
                "returning []"
            )
            return []
        except Exception as exc:
            logger.warning("VisionAdapter.analyze() error: %s", exc)
            return []

    def analyze_batch(
        self,
        observations: List[Observation],
    ) -> List[Any]:
        """批量分析多个 Observation。"""
        facts: List[Any] = []
        for obs in observations:
            new_facts = self.analyze_safe(obs)
            facts.extend(new_facts)
        return facts

    # --------------------------------------------------------
    # 工具:Observation → Fact 直接转换 (不调用视觉模型)
    # --------------------------------------------------------
    def observation_to_fact_direct(
        self,
        observation: Observation,
    ) -> Optional[Any]:
        """直接将视觉 Observation 转 Fact (不调用视觉模型)。

        适用场景:Observation.content 已经是人类可读文本 (e.g. OCR 结果),
        不需要再调用 Vision 模型分析。
        """
        if observation.source not in ALLOWED_OBS_TO_FACT_SOURCES:
            raise ValueError(
                f"Vision observation must have source in "
                f"{sorted(ALLOWED_OBS_TO_FACT_SOURCES, key=str)}, "
                f"got {observation.source!r}"
            )
        return observation_to_fact(observation)


__all__ = [
    "VisionAdapter",
    "VISION_ADAPTER_SCHEMA_VERSION",
    "VISION_EVENT_ANALYZED",
    "VISION_EVENT_UNAVAILABLE",
    "VISION_EVENT_ERROR",
]
