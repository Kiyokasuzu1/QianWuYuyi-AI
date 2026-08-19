# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/vision_adapter.py

Phase 4.2.0: Vision Adapter 抽象层

职责:
- 定义 Vision 适配器抽象接口(describe / analyze / etc.)
- 隐藏具体 Vision 模型;Runtime 只依赖本抽象类
- 接收 Observation,返回 VisionResult
- 内部委托 VisionProvider

约束:
- 不 import openai / qwen-vl / llava / yolo / clip SDK
- 不 import Runtime / Memory / Emotion / Personality
- 不修改 Observation / Fact / RuntimeContext schema
- 异常隔离:所有错误静默降级

设计原则:
- Adapter 不知道具体模型;模型由 Provider 注入
- Adapter 接收 Observation,处理后输出 VisionResult
- Adapter 负责"接收/分发/聚合",Provider 负责"理解"
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.runtime.perception.fact import Fact
from src.runtime.perception.observation import Observation
from src.runtime.perception.vision.providers.base import VisionProvider
from src.runtime.perception.vision.vision_result import VisionResult


VISION_ADAPTER_SCHEMA_VERSION = "1.0"


class VisionAdapter(ABC):
    """Vision Adapter 抽象基类(Phase 4.2.0 v1.0)。

    字段:
    - name:               str                          # Adapter 名
    - schema_version:     str = "1.0"

    子类必须实现:
    - describe()         - 接收 Observation,返回 List[VisionResult]
    - health_check()     - 健康检查

    关键接口:
    - describe(observation)  →  List[VisionResult]
        默认实现:委托 provider.analyze()
    - to_facts(results, observations) → List[Fact]
        通过 fact_builder.vision_result_to_fact 转换
    """

    name: str = "vision_adapter"
    schema_version: str = VISION_ADAPTER_SCHEMA_VERSION

    def __init__(self) -> None:
        self._provider: Optional[VisionProvider] = None
        self._attached: bool = False

    # --------------------------------------------------------
    # Provider 注入
    # --------------------------------------------------------
    def set_provider(self, provider: VisionProvider) -> None:
        """注入 VisionProvider。

        必须在 attach() 之前调用。
        """
        if not isinstance(provider, VisionProvider):
            raise TypeError(
                f"provider must be VisionProvider, got {type(provider).__name__}"
            )
        self._provider = provider

    def get_provider(self) -> Optional[VisionProvider]:
        return self._provider

    @property
    def is_attached(self) -> bool:
        return self._attached

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。

        默认实现:若 provider 已注入,则 provider.attach()。
        子类可扩展(例如加载本地模型权重)。
        """
        if self._provider is not None:
            try:
                self._provider.attach()
            except Exception:  # noqa: BLE001
                pass
        self._attached = True

    def detach(self) -> None:
        """解除接入。"""
        try:
            if self._provider is not None:
                self._provider.detach()
        except Exception:  # noqa: BLE001
            pass
        self._attached = False

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """健康检查。"""
        raise NotImplementedError(
            f"{self.__class__.__name__}.health_check() 未实现"
        )

    # --------------------------------------------------------
    # 核心:描述(可选子类自定义;默认走 provider)
    # --------------------------------------------------------
    def describe(
        self,
        observation: Observation,
    ) -> List[VisionResult]:
        """对一次 Observation 执行视觉理解,返回 0/1 个 VisionResult。

        默认实现:委托 provider.analyze()。
        子类可覆盖以实现多 provider 路由 / 模型选择 / 多模态融合等。
        """
        if observation is None or self._provider is None:
            return []
        if not self._attached:
            return []
        try:
            result = self._provider.analyze(observation)
        except Exception:  # noqa: BLE001
            return []
        if result is None:
            return []
        return [result]

    def describe_batch(
        self,
        observations: List[Observation],
    ) -> List[VisionResult]:
        """批量 describe。"""
        results: List[VisionResult] = []
        for o in observations or []:
            try:
                results.extend(self.describe(o))
            except Exception:  # noqa: BLE001
                continue
        return results

    # --------------------------------------------------------
    # 便利:VisionResult → Fact
    # --------------------------------------------------------
    def to_facts(
        self,
        results: List[VisionResult],
        observations: Optional[List[Observation]] = None,
    ) -> List[Fact]:
        """将本 Adapter 产出的 VisionResult 转换为 Fact。

        委托 fact_builder.vision_results_to_facts。
        """
        # 延迟 import 避免循环依赖
        from src.runtime.perception.vision.fact_builder import (
            vision_results_to_facts,
        )
        return vision_results_to_facts(results, observations)


__all__ = [
    "VisionAdapter",
    "VISION_ADAPTER_SCHEMA_VERSION",
]
