# -*- coding: utf-8 -*-
"""
src/runtime/perception/vision/providers/__init__.py

Phase 4.2.0: Vision Providers 包入口。

提供:
- VisionProvider        抽象基类
- MockVisionProvider    用于架构验证

后续 Phase 将加入(本阶段不实现):
- OpenAIVisionProvider
- QwenVLProvider
- LLaVAProvider
- LocalVisionProvider
"""
from __future__ import annotations

from src.runtime.perception.vision.providers.base import (
    VisionProvider,
    VISION_PROVIDER_SCHEMA_VERSION,
)
from src.runtime.perception.vision.providers.mock_provider import (
    MockVisionProvider,
    MOCK_VISION_PROVIDER_SCHEMA_VERSION,
)

__all__ = [
    "VisionProvider",
    "VISION_PROVIDER_SCHEMA_VERSION",
    "MockVisionProvider",
    "MOCK_VISION_PROVIDER_SCHEMA_VERSION",
]
