# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/vision_registry.py

Phase 4.2.0: Vision Adapter Registry

职责:
- 管理多个 VisionAdapter 实例
- 提供批量 attach / detach / health_check / analyze
- Runtime 只依赖本注册表,不直接接触具体 Adapter

约束:
- 不 import openai / qwen-vl / llava / yolo / clip SDK
- 不 import Runtime / Memory / Emotion / Personality
- 异常隔离:单个 Adapter 失败不中断 Registry
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.runtime.perception.observation import Observation
from src.runtime.perception.vision.vision_adapter import VisionAdapter
from src.runtime.perception.vision.vision_result import VisionResult


VISION_ADAPTER_REGISTRY_SCHEMA_VERSION = "1.0"


class VisionAdapterRegistry:
    """Vision Adapter 注册表(Phase 4.2.0 v1.0)。

    职责:
    - 接收多个 VisionAdapter
    - 提供按 name 索引
    - 批量 attach / detach / health_check
    - 批量 analyze (产出 VisionResult 列表)

    依赖:
    - 仅依赖 VisionAdapter 抽象类
    - 不接触具体模型 / Provider SDK
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, VisionAdapter] = {}

    def register(self, adapter: VisionAdapter) -> None:
        """注册一个 Adapter。"""
        if not isinstance(adapter, VisionAdapter):
            raise TypeError(
                f"Adapter must be VisionAdapter, got {type(adapter).__name__}"
            )
        if adapter.name in self._adapters:
            raise ValueError(
                f"VisionAdapter {adapter.name!r} already registered"
            )
        self._adapters[adapter.name] = adapter

    def unregister(self, name: str) -> Optional[VisionAdapter]:
        return self._adapters.pop(name, None)

    def get(self, name: str) -> Optional[VisionAdapter]:
        return self._adapters.get(name)

    def all(self) -> List[VisionAdapter]:
        return list(self._adapters.values())

    def __len__(self) -> int:
        return len(self._adapters)

    def __contains__(self, name: str) -> bool:
        return name in self._adapters

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def attach_all(self) -> None:
        for a in self._adapters.values():
            try:
                a.attach()
            except Exception:  # noqa: BLE001
                pass

    def detach_all(self) -> None:
        for a in self._adapters.values():
            try:
                a.detach()
            except Exception:  # noqa: BLE001
                pass

    def health_check_all(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}
        for name, a in self._adapters.items():
            try:
                results[name] = a.health_check()
            except Exception as exc:  # noqa: BLE001
                results[name] = {
                    "healthy": False,
                    "name": name,
                    "error": str(exc),
                }
        return results

    # --------------------------------------------------------
    # 核心:批量分析
    # --------------------------------------------------------
    def analyze_all(
        self,
        observations: List[Observation],
    ) -> List[VisionResult]:
        """所有已 attached Adapter 对给定 observations 各自分析一次。

        聚合所有 VisionResult 列表,失败/None 静默丢弃。
        """
        results: List[VisionResult] = []
        for adapter in self._adapters.values():
            if not adapter.is_attached:
                continue
            try:
                adapter_results = adapter.describe_batch(observations)
            except Exception:  # noqa: BLE001
                continue
            if adapter_results:
                results.extend(adapter_results)
        return results


__all__ = [
    "VisionAdapterRegistry",
    "VISION_ADAPTER_REGISTRY_SCHEMA_VERSION",
]
