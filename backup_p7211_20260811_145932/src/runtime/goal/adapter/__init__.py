# -*- coding: utf-8 -*-
"""
src/runtime/goal/adapter/__init__.py

Phase 5.0-D3-D: Goal Adapter 子包导出。
"""
from src.runtime.goal.adapter.goal_adapter import (
    DEFAULT_GOAL_ADAPTER_OWNER,
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_REFLECTIONS,
    GOAL_ADAPTER_SCHEMA_VERSION,
    GoalAdapter,
    GoalAdapterTickOutput,
    build_default_goal_adapter,
)
from src.runtime.goal.adapter.goal_event_emitter import (
    DEFAULT_EMITTER_SOURCE,
    DEFAULT_GOAL_EMITTER_OWNER,
    GOAL_EVENT_EMITTER_SCHEMA_VERSION,
    GoalEventEmitter,
    build_default_goal_event_emitter,
)


__all__ = [
    # adapter
    "GOAL_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_GOAL_ADAPTER_OWNER",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_REFLECTIONS",
    "GoalAdapter",
    "GoalAdapterTickOutput",
    "build_default_goal_adapter",
    # emitter
    "GOAL_EVENT_EMITTER_SCHEMA_VERSION",
    "DEFAULT_GOAL_EMITTER_OWNER",
    "DEFAULT_EMITTER_SOURCE",
    "GoalEventEmitter",
    "build_default_goal_event_emitter",
]
