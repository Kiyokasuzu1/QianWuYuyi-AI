# -*- coding: utf-8 -*-
"""
src/initiative/__init__.py

v1.3 Phase 5.1: Initiative 治理基础设施包(只含 Proposal→Drain→Action→Audit 链)。

红线: 本包不包含任何主动发送/Engine 接线能力; 仅治理骨架。
"""

from src.initiative.initiative_drain import (
    DRAIN_ACTOR,
    LIFECYCLE_STAGE_PRODUCED,
    build_initiative_proposal,
    drain_approved_initiative_proposals,
)
# v1.3 Phase 5.2: Action 执行前安全检查层
from src.initiative.action_safety import (
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_COOLDOWN_SECONDS,
    ALLOWED_ACTION_TYPES,
    ALLOWED_PAYLOAD_KEYS,
    ALLOWED_USER_PREFERENCES,
    DENIED_USER_PREFERENCES,
    SafetyDecision,
    ActionSafetyFilter,
)
# v1.3 Phase 5.3: InitiativeCandidate 生成层(Goal 驱动纯函数)
from src.initiative.initiative_candidate import (
    GENERATOR_VERSION,
    SUPPORTED_ACTION_TYPES,
    DEFAULT_MAX_CANDIDATES,
    generate_candidates,
)
# v1.3 Phase 5.4: Initiative 受治理流水线(默认 off, 双开关)
from src.initiative.initiative_pipeline import (
    PIPELINE_MODES,
    DEFAULT_MODE,
    DEFAULT_DRAIN_LIMIT,
    PIPELINE_SOURCE,
    InitiativePipeline,
)
# v1.3 Phase 5.5: 生产硬化(统一预算 + 可观察性)
from src.initiative.initiative_budget import (
    DEFAULT_PER_GOAL_DAILY_LIMIT,
    DEFAULT_PER_USER_COOLDOWN_SECONDS,
    DEFAULT_GLOBAL_DAILY_LIMIT,
    REASON_PER_GOAL,
    REASON_USER_COOLDOWN,
    REASON_GLOBAL,
    InitiativeBudgetPolicy,
)
from src.initiative.initiative_observability import (
    DEFAULT_LEDGER_PATH,
    SCHEMA_VERSION,
    STAGE_PROPOSAL_CREATED,
    STAGE_ACTION_CREATED,
    STAGE_SAFETY_PASSED,
    STAGE_SAFETY_BLOCKED,
    STAGE_BUDGET_BLOCKED,
    STAGE_DISPATCH_DISABLED,
    STAGE_DISPATCHED,
    InitiativeActionLedger,
    list_initiative_actions,
)

__all__ = [
    "DRAIN_ACTOR",
    "LIFECYCLE_STAGE_PRODUCED",
    "build_initiative_proposal",
    "drain_approved_initiative_proposals",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_COOLDOWN_SECONDS",
    "ALLOWED_ACTION_TYPES",
    "ALLOWED_PAYLOAD_KEYS",
    "ALLOWED_USER_PREFERENCES",
    "DENIED_USER_PREFERENCES",
    "SafetyDecision",
    "ActionSafetyFilter",
    "GENERATOR_VERSION",
    "SUPPORTED_ACTION_TYPES",
    "DEFAULT_MAX_CANDIDATES",
    "generate_candidates",
    "PIPELINE_MODES",
    "DEFAULT_MODE",
    "DEFAULT_DRAIN_LIMIT",
    "PIPELINE_SOURCE",
    "InitiativePipeline",
    "DEFAULT_PER_GOAL_DAILY_LIMIT",
    "DEFAULT_PER_USER_COOLDOWN_SECONDS",
    "DEFAULT_GLOBAL_DAILY_LIMIT",
    "REASON_PER_GOAL",
    "REASON_USER_COOLDOWN",
    "REASON_GLOBAL",
    "InitiativeBudgetPolicy",
    "DEFAULT_LEDGER_PATH",
    "SCHEMA_VERSION",
    "STAGE_PROPOSAL_CREATED",
    "STAGE_ACTION_CREATED",
    "STAGE_SAFETY_PASSED",
    "STAGE_SAFETY_BLOCKED",
    "STAGE_BUDGET_BLOCKED",
    "STAGE_DISPATCH_DISABLED",
    "STAGE_DISPATCHED",
    "InitiativeActionLedger",
    "list_initiative_actions",
]
