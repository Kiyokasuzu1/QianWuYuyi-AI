# -*- coding: utf-8 -*-
"""
src/control/runtime/__init__.py

Phase C.10.6 — Yuyi Runtime Control Provider (Server 端)

本包为 Runtime 提供"只读"访问 ControlState 的能力。
- 不持有任何"修改 ControlState"的方法
- 不依赖 Desktop / 不依赖 yuyi_desktop
- 唯一依赖: src/control/state/control_state.py
"""

from .control_provider import (
    RuntimeControlProvider,
    RuntimeControlMode,
    ModuleCheckResult,
    get_runtime_control_provider,
    reset_runtime_control_provider_for_testing,
)

__all__ = [
    "RuntimeControlProvider",
    "RuntimeControlMode",
    "ModuleCheckResult",
    "get_runtime_control_provider",
    "reset_runtime_control_provider_for_testing",
]
