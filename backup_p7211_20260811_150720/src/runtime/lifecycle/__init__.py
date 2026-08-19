# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/__init__.py

Phase 5.0-D1: Lifecycle 包入口(Step 1 部分)。
"""
from src.runtime.lifecycle.internal.clock import (
    Clock,
    ClockError,
    FrozenClock,
    FrozenClockError,
    MockClock,
    SystemClock,
)
from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
    classify_exception,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)
from src.runtime.lifecycle.lifecycle_state import (
    LifecycleState,
    LifecycleStateMachine,
)


__all__ = [
    # clock
    "Clock",
    "ClockError",
    "FrozenClock",
    "FrozenClockError",
    "MockClock",
    "SystemClock",
    # errors
    "ErrorCategory",
    "LifecycleError",
    "classify_exception",
    # result
    "LifecycleResult",
    "LifecycleStatus",
    # state
    "LifecycleState",
    "LifecycleStateMachine",
]
