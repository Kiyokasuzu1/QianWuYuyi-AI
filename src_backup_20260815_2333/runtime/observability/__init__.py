# -*- coding: utf-8 -*-
"""
src/runtime/observability/__init__.py

Phase C.7.2 Runtime Observability & Audit Dashboard —— 模块入口
"""
from __future__ import annotations

from .runtime_observer import (
    RUNTIME_OBSERVER_SCHEMA_VERSION,
    RuntimeObserver,
    create_runtime_observer,
    safe_get_runtime_observer_summary,
)

__all__ = [
    "RUNTIME_OBSERVER_SCHEMA_VERSION",
    "RuntimeObserver",
    "create_runtime_observer",
    "safe_get_runtime_observer_summary",
]
