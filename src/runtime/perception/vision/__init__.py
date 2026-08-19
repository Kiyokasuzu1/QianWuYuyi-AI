# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/__init__.py

Phase 4.2.0: Vision Adapter Layer 入口。

提供:
- VisionAdapter          抽象基类
- VisionAdapterRegistry  Adapter 注册表
- VisionResult           视觉分析结果数据模型
- VisionProvider         模型提供方抽象
- MockVisionProvider     架构验证用 mock provider
- vision_result_to_fact  VisionResult → Fact 转换函数
- vision_results_to_facts 批量转换

依赖方向:
    Runtime
      ↓
    VisionAdapterRegistry
      ↓
    VisionAdapter (abstract)
      ↓
    VisionProvider (abstract)
      ↓
    (OpenAI / Qwen / LLaVA / Mock)

本阶段不接入任何真实 Vision SDK。
"""
from __future__ import annotations

from src.runtime.perception.vision.vision_result import (
    VisionResult,
    VISION_RESULT_SCHEMA_VERSION,
)
from src.runtime.perception.vision.vision_adapter import (
    VisionAdapter,
    VISION_ADAPTER_SCHEMA_VERSION,
)
from src.runtime.perception.vision.vision_registry import (
    VisionAdapterRegistry,
    VISION_ADAPTER_REGISTRY_SCHEMA_VERSION,
)
from src.runtime.perception.vision.fact_builder import (
    vision_result_to_fact,
    vision_results_to_facts,
    VISION_FACT_BUILDER_SCHEMA_VERSION,
)
from src.runtime.perception.vision.providers import (
    VisionProvider,
    VISION_PROVIDER_SCHEMA_VERSION,
    MockVisionProvider,
    MOCK_VISION_PROVIDER_SCHEMA_VERSION,
)

__all__ = [
    # 数据模型
    "VisionResult",
    "VISION_RESULT_SCHEMA_VERSION",
    # Adapter 抽象
    "VisionAdapter",
    "VISION_ADAPTER_SCHEMA_VERSION",
    # Registry
    "VisionAdapterRegistry",
    "VISION_ADAPTER_REGISTRY_SCHEMA_VERSION",
    # Provider 抽象
    "VisionProvider",
    "VISION_PROVIDER_SCHEMA_VERSION",
    "MockVisionProvider",
    "MOCK_VISION_PROVIDER_SCHEMA_VERSION",
    # Fact 转换
    "vision_result_to_fact",
    "vision_results_to_facts",
    "VISION_FACT_BUILDER_SCHEMA_VERSION",
]
