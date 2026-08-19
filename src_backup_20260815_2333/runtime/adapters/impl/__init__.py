# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/__init__.py

Phase 3.7.2: Adapter Implementation 聚合导出

- MemoryAdapterImpl
- EmotionAdapterImpl
- GrowthAdapterImpl
- PersonalityAdapterImpl

Runtime 通过 AdapterRegistry 注册这些 Impl；
Impl 内部依赖业务模块（MemoryService / EmotionManager / GrowthEngine / PersonalityResolver），
但 Impl 之间禁止互相 import。
"""
from __future__ import annotations

from importlib import import_module

__all__ = [
    "MemoryAdapterImpl",
    "EmotionAdapterImpl",
    "GrowthAdapterImpl",
    "PersonalityAdapterImpl",
    "ResponseAdapterImpl",  # Phase 3.8.4
]


_EXPORT_MAP = {
    "MemoryAdapterImpl": (
        "src.runtime.adapters.impl.memory_adapter_impl",
        "MemoryAdapterImpl",
    ),
    "EmotionAdapterImpl": (
        "src.runtime.adapters.impl.emotion_adapter_impl",
        "EmotionAdapterImpl",
    ),
    "GrowthAdapterImpl": (
        "src.runtime.adapters.impl.growth_adapter_impl",
        "GrowthAdapterImpl",
    ),
    "PersonalityAdapterImpl": (
        "src.runtime.adapters.impl.personality_adapter_impl",
        "PersonalityAdapterImpl",
    ),
    "ResponseAdapterImpl": (
        "src.runtime.adapters.impl.response_adapter_impl",
        "ResponseAdapterImpl",
    ),
}


def __getattr__(name):
    if name not in _EXPORT_MAP:
        raise AttributeError(
            f"module 'src.runtime.adapters.impl' has no attribute {name!r}"
        )
    module_name, attr_name = _EXPORT_MAP[name]
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
