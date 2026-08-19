# -*- coding: utf-8 -*-
"""
src/runtime/context/__init__.py

Phase C.10.6 — Runtime Context 子包

包含 Runtime 在 cycle 内使用的各种 Context 包装对象。
同时保持向后兼容:从 src.runtime.context 直接 import RuntimeContext 仍然有效。

向后兼容:
    from src.runtime.context import RuntimeContext  # OK
    from src.runtime.context import RuntimeControlContext  # OK (Phase C.10.6)
"""

# 向后兼容:从原 src/runtime/context.py 迁移过来的 RuntimeContext
from .runtime_context import (
    RuntimeContext,
    RUNTIME_CONTEXT_SCHEMA_VERSION,
)

# Phase C.10.6 新增:Runtime Control Context
from .control_context import (
    RuntimeControlContext,
    ControlStateProvider,
    NullControlStateProvider,
    create_control_context,
    get_default_control_context,
    reset_default_control_context_for_testing,
)

__all__ = [
    # 向后兼容
    "RuntimeContext",
    "RUNTIME_CONTEXT_SCHEMA_VERSION",
    # Phase C.10.6
    "RuntimeControlContext",
    "ControlStateProvider",
    "NullControlStateProvider",
    "create_control_context",
    "get_default_control_context",
    "reset_default_control_context_for_testing",
]
