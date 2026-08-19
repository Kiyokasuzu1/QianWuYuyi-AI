# -*- coding: utf-8 -*-
"""
src/control/runtime_adapter/__init__.py

Phase C.10.6.1 — Runtime Control Adapter

提供 Runtime 访问 ControlState 的只读接口。
禁止修改 ControlState;只能查询。
"""

from .runtime_adapter import (
    RuntimeControlAdapter,
    RuntimeMode,
    get_runtime_control_adapter,
    reset_runtime_control_adapter_for_testing,
)

__all__ = [
    "RuntimeControlAdapter",
    "RuntimeMode",
    "get_runtime_control_adapter",
    "reset_runtime_control_adapter_for_testing",
]
