# -*- coding: utf-8 -*-
"""
src/runtime/recovery/__init__.py

Phase C.7.3 Runtime Recovery & Self-Diagnosis —— 模块入口

只读 / 安全的 Runtime 自诊断 + 恢复基础层。
"""
from __future__ import annotations

from .runtime_diagnoser import (
    RUNTIME_DIAGNOSER_SCHEMA_VERSION,
    RuntimeDiagnoser,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    create_runtime_diagnoser,
    safe_diagnose,
)
from .runtime_recovery import (
    RUNTIME_RECOVERY_SCHEMA_VERSION,
    RECOVERY_ACTION_CLEAR_OBSERVER_CACHE,
    RECOVERY_ACTION_REATTACH_ADAPTERS,
    RECOVERY_ACTION_RECHECK_HEALTH,
    RECOVERY_ACTION_RESET_TEMP_STATE,
    RECOVERY_ACTION_MARK_DEGRADED,
    RuntimeRecovery,
    create_runtime_recovery,
    safe_recover,
)

__all__ = [
    # Diagnoser
    "RUNTIME_DIAGNOSER_SCHEMA_VERSION",
    "RuntimeDiagnoser",
    "create_runtime_diagnoser",
    "safe_diagnose",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SEVERITY_HIGH",
    # Recovery
    "RUNTIME_RECOVERY_SCHEMA_VERSION",
    "RuntimeRecovery",
    "create_runtime_recovery",
    "safe_recover",
    "RECOVERY_ACTION_CLEAR_OBSERVER_CACHE",
    "RECOVERY_ACTION_REATTACH_ADAPTERS",
    "RECOVERY_ACTION_RECHECK_HEALTH",
    "RECOVERY_ACTION_RESET_TEMP_STATE",
    "RECOVERY_ACTION_MARK_DEGRADED",
]
