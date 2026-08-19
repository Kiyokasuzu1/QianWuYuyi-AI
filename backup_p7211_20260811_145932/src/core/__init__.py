"""
src.core 包

延迟导入 YuyiCore，避免在仅使用 module_loader / heartbeat 时触发 openai 依赖。
"""

from __future__ import annotations


def __getattr__(name: str):
    if name == "YuyiCore":
        from .yuyi_core import YuyiCore
        return YuyiCore
    raise AttributeError(f"module 'src.core' has no attribute {name}")
