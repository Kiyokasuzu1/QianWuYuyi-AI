# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/providers/base.py

Phase 4.2.0: Vision Provider 抽象基类

职责:
- 定义 Vision 模型提供方必须实现的接口
- 让 VisionAdapter 知道如何调用任何 Vision 模型
- 真实模型(OpenAI / Qwen / LLaVA)后续 Phase 接入;本阶段只定义契约

约束:
- 不 import openai / qwen-vl / llava / yolo / clip SDK
- 不在 __init__ 中加载任何模型
- analyze() 必须异常隔离
- 不修改 Fact / Observation / VisionResult schema
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.runtime.perception.vision.vision_result import VisionResult
from src.runtime.perception.observation import Observation


VISION_PROVIDER_SCHEMA_VERSION = "1.0"


class VisionProvider(ABC):
    """Vision 模型提供方抽象基类(Phase 4.2.0 v1.0)。

    字段:
    - provider_name:    str                          # 提供方标识(用于日志/审计/路由)
    - schema_version:   str = "1.0"

    子类必须实现:
    - analyze()            - 对一次观察(或其数据载体)进行视觉理解
    - health_check()       - 健康检查

    重要:
    - analyze() 禁止修改传入的 observation
    - analyze() 失败必须返回 None,不得抛异常
    - 本类不感知 OpenAI / Qwen / LLaVA 任何 SDK
    """

    provider_name: str = "vision_provider"
    schema_version: str = VISION_PROVIDER_SCHEMA_VERSION

    def __init__(self) -> None:
        self._attached: bool = False

    @property
    def is_attached(self) -> bool:
        return self._attached

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """接入 Runtime。子类可覆盖以加载模型/网络客户端。"""
        self._attached = True

    def detach(self) -> None:
        """解除接入。子类可覆盖以释放资源。"""
        self._attached = False

    @abstractmethod
    def analyze(
        self,
        observation: Observation,
    ) -> Optional[VisionResult]:
        """对一次 Observation 执行视觉理解,返回 VisionResult 或 None。

        输入:
        - observation:    一次感知读数(必须 available=True,kind in (SCREEN, CAMERA))

        返回:
        - VisionResult:   成功
        - None:           失败 / observation 不可用 / 提供方未 attach

        约束:
        - 不修改 observation
        - 失败必须静默返回 None
        - 不抛异常
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.analyze() 未实现"
        )

    @abstractmethod
    def health_check(self) -> Dict[str, Any]:
        """健康检查。

        Returns:
            {
                "healthy": bool,
                "provider_name": str,
                "schema_version": str,
                "details": Dict[str, Any],   # 可选
            }
        """
        raise NotImplementedError(
            f"{self.__class__.__name__}.health_check() 未实现"
        )

    # --------------------------------------------------------
    # 批量便利
    # --------------------------------------------------------
    def analyze_batch(
        self,
        observations: List[Observation],
    ) -> List[VisionResult]:
        """批量 analyze。失败/None 会被静默丢弃。"""
        results: List[VisionResult] = []
        for obs in observations or []:
            try:
                res = self.analyze(obs)
                if res is not None:
                    results.append(res)
            except Exception:  # noqa: BLE001
                continue
        return results


__all__ = [
    "VisionProvider",
    "VISION_PROVIDER_SCHEMA_VERSION",
]
