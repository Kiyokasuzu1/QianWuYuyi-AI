# -*- coding: utf-8 -*-
"""
src/runtime/perception/impl/__init__.py

Phase 4.1.0: Perception Adapter Impl 入口

提供真实感知能力的实现层（lazy import,允许运行期缺失依赖）。

本子包内的 Adapter 必须遵守以下约束：
1. 真实感知能力的 import 必须是 lazy / optional
2. Runtime / 其他模块不直接 import 本子包;通过 PerceptionAdapterRegistry 注册
3. 每个 Adapter 内部必须自带 fallback 逻辑
   - 库未安装时: health_check 返回 unavailable,observe 返回 None
   - 权限/环境不足时: 同上
4. 不向 Runtime 暴露任何"感知实现细节",只暴露 PerceptionAdapter 接口
"""
from __future__ import annotations

from src.runtime.perception.impl.screen_capture_adapter import (
    ScreenCaptureAdapter,
    SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION,
)

__all__ = [
    "ScreenCaptureAdapter",
    "SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION",
]
