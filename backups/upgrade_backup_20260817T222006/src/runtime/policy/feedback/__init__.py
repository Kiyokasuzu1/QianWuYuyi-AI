# -*- coding: utf-8 -*-
"""
src/runtime/policy/feedback/__init__.py

Phase C.10.9 — Adaptive Policy Feedback Loop

本包实现 Runtime Policy 自适应反馈子系统:

- PolicyMetrics / MetricsCollector     feedback_collector.py
- AdaptiveEvaluator / EvaluatorConfig  adaptive_evaluator.py
- PolicyAdjustmentProposal / ProposalStore proposal.py
- PolicyFeedbackEngine                 feedback_engine.py

使用方式:
    from src.runtime.policy.feedback import (
        PolicyFeedbackEngine,
        PolicyMetrics,
        PolicyAdjustmentProposal,
        ProposalStore,
        MetricsCollector,
        AdaptiveEvaluator,
        EvaluatorConfig,
        PolicySnapshot,
        capture_policy_snapshot,
        build_feedback_engine,
    )

    engine = build_feedback_engine(
        throttle_registry=layer.throttle_registry,
        runtime_budget=layer.runtime_budget,
    )
    engine.record_execution("growth", success=True, latency_ms=120, cost=2.0)
    proposals = engine.evaluate(cycle_id="c-123")
"""

from .adaptive_evaluator import (
    AdaptiveEvaluator,
    EvaluatorConfig,
    PolicySnapshot,
    build_evaluator,
    capture_policy_snapshot,
)
from .feedback_collector import (
    MetricsCollector,
    PolicyMetrics,
    build_empty_metrics,
    build_metrics_collector,
)
from .feedback_engine import (
    DEFAULT_MAX_PROPOSALS,
    DEFAULT_PUBLISH_EVENTS,
    PolicyFeedbackEngine,
    build_feedback_engine,
)
from .proposal import (
    ADJUST_DIRECTION_DECREASE,
    ADJUST_DIRECTION_INCREASE,
    ADJUST_DIRECTION_NO_CHANGE,
    ALL_PARAMETERS,
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_DAILY_LLM_CALLS,
    PARAM_BUDGET_DAILY_TOKENS,
    PARAM_BUDGET_MODULE_COST,
    PARAM_BUDGET_PER_MODULE_COST,
    PARAM_THROTTLE_COOLDOWN,
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
    PolicyAdjustmentProposal,
    ProposalStore,
    build_proposal,
)


__all__ = [
    # 指标
    "PolicyMetrics",
    "MetricsCollector",
    "build_metrics_collector",
    "build_empty_metrics",
    # 评估器
    "AdaptiveEvaluator",
    "EvaluatorConfig",
    "PolicySnapshot",
    "build_evaluator",
    "capture_policy_snapshot",
    # Proposal
    "PolicyAdjustmentProposal",
    "ProposalStore",
    "build_proposal",
    "ADJUST_DIRECTION_INCREASE",
    "ADJUST_DIRECTION_DECREASE",
    "ADJUST_DIRECTION_NO_CHANGE",
    "ALL_PARAMETERS",
    "PARAM_THROTTLE_INTERVAL",
    "PARAM_THROTTLE_THROTTLE",
    "PARAM_THROTTLE_COOLDOWN",
    "PARAM_BUDGET_DAILY_LLM_CALLS",
    "PARAM_BUDGET_DAILY_TOKENS",
    "PARAM_BUDGET_DAILY_COST",
    "PARAM_BUDGET_PER_MODULE_COST",
    "PARAM_BUDGET_MODULE_COST",
    # Engine
    "PolicyFeedbackEngine",
    "build_feedback_engine",
    "DEFAULT_MAX_PROPOSALS",
    "DEFAULT_PUBLISH_EVENTS",
]
