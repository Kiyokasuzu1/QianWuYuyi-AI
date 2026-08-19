# -*- coding: utf-8 -*-
"""
src/runtime/reflection/adapter/__init__.py

Phase 5.0-D3-B: Reflection Adapter 包入口。
"""
from __future__ import annotations

from src.runtime.reflection.adapter.reflection_adapter import (
    REFLECTION_ADAPTER_SCHEMA_VERSION,
    DEFAULT_REFLECTION_ADAPTER_OWNER,
    ReflectionAdapter,
    build_default_reflection_adapter,
)
from src.runtime.reflection.adapter.reflection_event_emitter import (
    REFLECTION_EVENT_EMITTER_SCHEMA_VERSION,
    ReflectionEventEmitter,
    build_default_reflection_event_emitter,
)

__all__ = [
    "REFLECTION_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_REFLECTION_ADAPTER_OWNER",
    "REFLECTION_EVENT_EMITTER_SCHEMA_VERSION",
    "ReflectionAdapter",
    "ReflectionEventEmitter",
    "build_default_reflection_adapter",
    "build_default_reflection_event_emitter",
]
