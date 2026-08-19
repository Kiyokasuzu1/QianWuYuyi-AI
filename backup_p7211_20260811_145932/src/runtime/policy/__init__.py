# -*- coding: utf-8 -*-
"""
src/runtime/policy/__init__.py

Phase C.10.7 — Runtime Policy Engine
Phase C.10.8 — Adaptive Policy & Throttle Layer

提供 Runtime 与 ControlState 之间的"策略决策层":

- RuntimeDecision      统一决策输出
- PolicyContext        评估上下文
- PolicyRule           规则协议
- PolicyEngine         评估入口
- AlwaysAllowRule / SafeModeRule / MaintenanceRule / DisabledModuleRule
                       内置基础规则
- ThrottleState / ThrottleRegistry / ThrottleRule
                       节流与执行间隔控制
- BudgetLedger / RuntimeBudget / BudgetRule
                       资源预算与限制
- AdaptivePolicyLayer  Throttle + Budget + PolicyEngine 一站式封装

使用方式:
    from src.runtime.policy import (
        PolicyEngine,
        PolicyContext,
        RuntimeDecision,
        AdaptivePolicyLayer,
        ThrottleRegistry,
        RuntimeBudget,
    )

    layer = AdaptivePolicyLayer.build_default()
    decision = layer.evaluate("growth", context=ctx)
    if decision.allowed:
        layer.on_module_executed("growth", cost=decision.budget_cost)
"""

from .decision import (
    DECISION_MODE_MAINTENANCE,
    DECISION_MODE_NORMAL,
    DECISION_MODE_SAFE,
    DECISION_MODE_UNKNOWN,
    VALID_MODES,
    RuntimeDecision,
    _normalize_mode,
    default_allow_decision,
    default_throttle_decision,
)
from .policy_context import (
    POLICY_CONTEXT_SCHEMA_VERSION,
    PolicyContext,
)
from .policy_engine import (
    PolicyEngine,
    get_default_policy_engine,
    reset_default_policy_engine_for_testing,
)
from .policy_rule import (
    AlwaysAllowRule,
    BasePolicyRule,
    DisabledModuleRule,
    MaintenanceRule,
    PolicyRule,
    SAFE_MODE_BLOCKED_MODULES,
    SAFE_MODE_READONLY_MODULES,
    MAINTENANCE_ALLOWED_MODULES,
    MAINTENANCE_BLOCKED_MODULES,
    SafeModeRule,
    build_default_rules,
    safe_call_rule,
)

# Phase C.10.8 — Throttle
from .throttle import (
    DEFAULT_MODULE_COOLDOWNS,
    DEFAULT_MODULE_INTERVALS,
    DEFAULT_MODULE_THROTTLES,
    DEFAULT_THROTTLE_PRIORITY,
    ThrottleRegistry,
    ThrottleRule,
    ThrottleState,
    build_default_throttle_rule,
    build_throttle_registry,
)

# Phase C.10.8 — Budget
from .budget import (
    DEFAULT_BUDGET_PRIORITY,
    DEFAULT_DAILY_COST_LIMIT,
    DEFAULT_DAILY_LLM_CALL_LIMIT,
    DEFAULT_DAILY_TOKEN_LIMIT,
    DEFAULT_MODULE_COSTS,
    BudgetLedger,
    BudgetRule,
    BudgetSnapshot,
    RuntimeBudget,
    build_default_budget,
    build_default_budget_rule,
)

# Phase C.10.8 — Adaptive Layer
from .adaptive_policy import AdaptivePolicyLayer

__all__ = [
    # 决策
    "RuntimeDecision",
    "default_allow_decision",
    "default_throttle_decision",
    "_normalize_mode",
    "DECISION_MODE_NORMAL",
    "DECISION_MODE_SAFE",
    "DECISION_MODE_MAINTENANCE",
    "DECISION_MODE_UNKNOWN",
    "VALID_MODES",
    # 上下文
    "PolicyContext",
    "POLICY_CONTEXT_SCHEMA_VERSION",
    # 规则
    "PolicyRule",
    "BasePolicyRule",
    "AlwaysAllowRule",
    "SafeModeRule",
    "MaintenanceRule",
    "DisabledModuleRule",
    "SAFE_MODE_BLOCKED_MODULES",
    "SAFE_MODE_READONLY_MODULES",
    "MAINTENANCE_ALLOWED_MODULES",
    "MAINTENANCE_BLOCKED_MODULES",
    "build_default_rules",
    "safe_call_rule",
    # 引擎
    "PolicyEngine",
    "get_default_policy_engine",
    "reset_default_policy_engine_for_testing",
    # Throttle (C.10.8)
    "ThrottleState",
    "ThrottleRegistry",
    "ThrottleRule",
    "build_throttle_registry",
    "build_default_throttle_rule",
    "DEFAULT_MODULE_INTERVALS",
    "DEFAULT_MODULE_THROTTLES",
    "DEFAULT_MODULE_COOLDOWNS",
    "DEFAULT_THROTTLE_PRIORITY",
    # Budget (C.10.8)
    "BudgetSnapshot",
    "BudgetLedger",
    "RuntimeBudget",
    "BudgetRule",
    "build_default_budget",
    "build_default_budget_rule",
    "DEFAULT_DAILY_LLM_CALL_LIMIT",
    "DEFAULT_DAILY_TOKEN_LIMIT",
    "DEFAULT_DAILY_COST_LIMIT",
    "DEFAULT_MODULE_COSTS",
    "DEFAULT_BUDGET_PRIORITY",
    # Adaptive Layer (C.10.8)
    "AdaptivePolicyLayer",
]
