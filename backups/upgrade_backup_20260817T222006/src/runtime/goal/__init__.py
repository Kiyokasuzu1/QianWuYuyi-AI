# -*- coding: utf-8 -*-
"""
src/runtime/goal/__init__.py

Phase 5.0-D3-D: Goal & Desire System 子包导出。

导出:
- GoalState
- Desire / DesireRegistry
- GoalRecord / ChangeRecord
- GoalGenerator
- GoalPlanner
- GoalHistory
- GoalManager
- Adapter (GoalAdapter / GoalEventEmitter)
- LifecycleTask (GoalLifecycleTask)
"""
from src.runtime.goal.desire import (
    ALL_DESIRE_TRENDS,
    DESIRE_SCHEMA_VERSION,
    DESIRE_TREND_FADING,
    DESIRE_TREND_NEW,
    DESIRE_TREND_RISING,
    DESIRE_TREND_STABLE,
    DEFAULT_DESIRE_TREND,
    Desire,
    DesireRegistry,
    build_desire,
)
from src.runtime.goal.goal_generator import (
    DEFAULT_DESIRE_MIN_CONFIDENCE,
    DEFAULT_DESIRE_MIN_STRENGTH,
    DEFAULT_MAX_CANDIDATES_PER_TICK,
    DEFAULT_REFLECTION_MIN_COUNT,
    DEFAULT_SIGNAL_MIN_COUNT,
    GOAL_GENERATOR_SCHEMA_VERSION,
    GoalGenerator,
    GoalGeneratorConfig,
    GoalGeneratorInput,
    GoalGeneratorOutput,
    build_default_goal_generator,
)
from src.runtime.goal.goal_history import (
    DEFAULT_HISTORY_CAPACITY,
    GOAL_HISTORY_SCHEMA_VERSION,
    GoalHistory,
    build_default_goal_history,
)
from src.runtime.goal.goal_manager import (
    DEFAULT_MAX_GOALS,
    DEFAULT_MAX_PLANS,
    GOAL_MANAGER_SCHEMA_VERSION,
    GoalManager,
    build_default_goal_manager,
)
from src.runtime.goal.goal_planner import (
    DEFAULT_MAX_MILESTONES,
    GOAL_PLAN_SCHEMA_VERSION,
    GoalPlan,
    GoalPlanner,
    Milestone,
    build_default_goal_planner,
)
from src.runtime.goal.goal_record import (
    ALL_GOAL_CHANGE_TYPES,
    GOAL_CHANGE_ABANDONED,
    GOAL_CHANGE_ACTIVATED,
    GOAL_CHANGE_COMPLETED,
    GOAL_CHANGE_CREATED,
    GOAL_CHANGE_PAUSED,
    GOAL_CHANGE_PROMOTED,
    GOAL_CHANGE_UPDATED,
    GOAL_RECORD_SCHEMA_VERSION,
    ChangeRecord,
    GoalRecord,
    build_change_record,
    build_record_from_goal,
)
from src.runtime.goal.goal_state import (
    ALL_GOAL_STATUSES,
    ALL_GOAL_TYPES,
    GOAL_STATE_SCHEMA_VERSION,
    GOAL_STATUS_ABANDONED,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_PAUSED,
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    VALID_GOAL_TRANSITIONS,
    GoalState,
    build_candidate_goal,
    is_valid_goal_transition,
)


__all__ = [
    # goal_state
    "GOAL_STATE_SCHEMA_VERSION",
    "ALL_GOAL_TYPES",
    "ALL_GOAL_STATUSES",
    "GOAL_TYPE_PERSONAL_GROWTH",
    "GOAL_TYPE_LEARNING",
    "GOAL_TYPE_CREATIVE",
    "GOAL_TYPE_RELATIONSHIP",
    "GOAL_TYPE_EXPLORATION",
    "GOAL_STATUS_CANDIDATE",
    "GOAL_STATUS_ACTIVE",
    "GOAL_STATUS_PAUSED",
    "GOAL_STATUS_COMPLETED",
    "GOAL_STATUS_ABANDONED",
    "VALID_GOAL_TRANSITIONS",
    "GoalState",
    "build_candidate_goal",
    "is_valid_goal_transition",
    # desire
    "DESIRE_SCHEMA_VERSION",
    "ALL_DESIRE_TRENDS",
    "DESIRE_TREND_NEW",
    "DESIRE_TREND_RISING",
    "DESIRE_TREND_STABLE",
    "DESIRE_TREND_FADING",
    "DEFAULT_DESIRE_TREND",
    "Desire",
    "DesireRegistry",
    "build_desire",
    # goal_record
    "GOAL_RECORD_SCHEMA_VERSION",
    "ALL_GOAL_CHANGE_TYPES",
    "GOAL_CHANGE_CREATED",
    "GOAL_CHANGE_UPDATED",
    "GOAL_CHANGE_ACTIVATED",
    "GOAL_CHANGE_PAUSED",
    "GOAL_CHANGE_COMPLETED",
    "GOAL_CHANGE_ABANDONED",
    "GOAL_CHANGE_PROMOTED",
    "GoalRecord",
    "ChangeRecord",
    "build_record_from_goal",
    "build_change_record",
    # goal_generator
    "GOAL_GENERATOR_SCHEMA_VERSION",
    "DEFAULT_DESIRE_MIN_STRENGTH",
    "DEFAULT_DESIRE_MIN_CONFIDENCE",
    "DEFAULT_REFLECTION_MIN_COUNT",
    "DEFAULT_SIGNAL_MIN_COUNT",
    "DEFAULT_MAX_CANDIDATES_PER_TICK",
    "GoalGeneratorConfig",
    "GoalGeneratorInput",
    "GoalGeneratorOutput",
    "GoalGenerator",
    "build_default_goal_generator",
    # goal_planner
    "GOAL_PLAN_SCHEMA_VERSION",
    "DEFAULT_MAX_MILESTONES",
    "Milestone",
    "GoalPlan",
    "GoalPlanner",
    "build_default_goal_planner",
    # goal_history
    "GOAL_HISTORY_SCHEMA_VERSION",
    "DEFAULT_HISTORY_CAPACITY",
    "GoalHistory",
    "build_default_goal_history",
    # goal_manager
    "GOAL_MANAGER_SCHEMA_VERSION",
    "DEFAULT_MAX_GOALS",
    "DEFAULT_MAX_PLANS",
    "GoalManager",
    "build_default_goal_manager",
]
