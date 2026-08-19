# -*- coding: utf-8 -*-
"""
src/control/registry/__init__.py

Phase C.10.5.2 — Module Registry
"""

from .module_registry import (
    ModuleInfo,
    ModuleRegistry,
    get_module_registry,
    reset_module_registry_for_testing,
    BUILTIN_MODULES,
)

__all__ = [
    "ModuleInfo",
    "ModuleRegistry",
    "get_module_registry",
    "reset_module_registry_for_testing",
    "BUILTIN_MODULES",
]
