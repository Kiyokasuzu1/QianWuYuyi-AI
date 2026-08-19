# -*- coding: utf-8 -*-
"""
src/runtime/policy/feedback/adaptive_evaluator.py

Phase C.10.9 — Adaptive Evaluator

本文件实现基于 PolicyMetrics 的自适应评估器:

- AdaptiveEvaluator
    根据 PolicyMetrics + 当前 Policy 快照(只读),生成 PolicyAdjustmentProposal 列表。

    评估策略(均为启发式,阈值可在构造时调整):
    1. 高成功率 + 低成本 + 高频 → 建议降低 interval(允许更频繁)
    2. 高失败率 → 建议增加 interval(降低频率)
    3. 高成本 + 高频 → 建议增加 interval 或降低 throttle
    4. 高 throttle 命中率 → 建议增加 interval
    5. 高 budget 拒绝率 → 建议降低 throttle / 减少 module_cost
    6. 低利用率(执行数 < 阈值) → 不生成建议

设计原则:
- 不修改任何 Policy 状态(set / set_limit 等被禁止)
- 只读 access ThrottleRegistry / RuntimeBudget 用于推断 old_value
- 输出 append-only PolicyAdjustmentProposal
- 任何异常 → fail-soft,返回空列表
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .feedback_collector import PolicyMetrics
from .proposal import (
    ADJUST_DIRECTION_DECREASE,
    ADJUST_DIRECTION_INCREASE,
    ADJUST_DIRECTION_NO_CHANGE,
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_MODULE_COST,
    PARAM_BUDGET_PER_MODULE_COST,
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
    PolicyAdjustmentProposal,
    ProposalStore,
    build_proposal,
)

logger = logging.getLogger(__name__)


# ============================================================
# 默认阈值
# ============================================================
DEFAULT_MIN_EXECUTIONS_FOR_PROPOSAL = 10
DEFAULT_HIGH_SUCCESS_RATE = 0.95
DEFAULT_LOW_FAILURE_RATE = 0.05
DEFAULT_HIGH_FAILURE_RATE = 0.20
DEFAULT_HIGH_DENY_RATE = 0.30
DEFAULT_HIGH_THROTTLE_HIT_RATE = 0.20
DEFAULT_HIGH_BUDGET_REJECT_RATE = 0.10
DEFAULT_HIGH_COST_THRESHOLD = 50.0
DEFAULT_LOW_UTILIZATION_THRESHOLD = 5

# Interval 调整步长
DEFAULT_INTERVAL_DECREASE_FACTOR = 0.8  # 当前 interval * 0.8(允许更频繁)
DEFAULT_INTERVAL_INCREASE_FACTOR = 1.5  # 当前 interval * 1.5(降低频率)
DEFAULT_INTERVAL_MIN = 1
DEFAULT_INTERVAL_MAX = 10000

# Throttle 调整步长
DEFAULT_THROTTLE_INCREASE_STEP = 0.1  # 提升 throttle 倍率
DEFAULT_THROTTLE_DECREASE_STEP = 0.1
DEFAULT_THROTTLE_MIN = 0.0
DEFAULT_THROTTLE_MAX = 1.0


# ============================================================
# EvaluatorConfig
# ============================================================
@dataclass
class EvaluatorConfig:
    """自适应评估器的阈值配置。

    字段:
    - min_executions_for_proposal:   触发建议的最小执行数
    - high_success_rate:             "高成功率"阈值
    - low_failure_rate:              "低失败率"阈值
    - high_failure_rate:             "高失败率"阈值(触发 interval↑)
    - high_deny_rate:                "高拒绝率"阈值
    - high_throttle_hit_rate:        "高 throttle 命中率"阈值
    - high_budget_reject_rate:       "高 budget 拒绝率"阈值
    - high_cost_threshold:           "高成本"阈值
    - low_utilization_threshold:     "低利用率"阈值
    - interval_decrease_factor:      降低 interval 的乘数
    - interval_increase_factor:      增加 interval 的乘数
    - interval_min / interval_max:   interval 边界
    - throttle_increase_step:        throttle 上调步长
    - throttle_decrease_step:        throttle 下调步长
    - throttle_min / throttle_max:   throttle 边界
    """

    min_executions_for_proposal: int = DEFAULT_MIN_EXECUTIONS_FOR_PROPOSAL
    high_success_rate: float = DEFAULT_HIGH_SUCCESS_RATE
    low_failure_rate: float = DEFAULT_LOW_FAILURE_RATE
    high_failure_rate: float = DEFAULT_HIGH_FAILURE_RATE
    high_deny_rate: float = DEFAULT_HIGH_DENY_RATE
    high_throttle_hit_rate: float = DEFAULT_HIGH_THROTTLE_HIT_RATE
    high_budget_reject_rate: float = DEFAULT_HIGH_BUDGET_REJECT_RATE
    high_cost_threshold: float = DEFAULT_HIGH_COST_THRESHOLD
    low_utilization_threshold: int = DEFAULT_LOW_UTILIZATION_THRESHOLD
    interval_decrease_factor: float = DEFAULT_INTERVAL_DECREASE_FACTOR
    interval_increase_factor: float = DEFAULT_INTERVAL_INCREASE_FACTOR
    interval_min: int = DEFAULT_INTERVAL_MIN
    interval_max: int = DEFAULT_INTERVAL_MAX
    throttle_increase_step: float = DEFAULT_THROTTLE_INCREASE_STEP
    throttle_decrease_step: float = DEFAULT_THROTTLE_DECREASE_STEP
    throttle_min: float = DEFAULT_THROTTLE_MIN
    throttle_max: float = DEFAULT_THROTTLE_MAX

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "min_executions_for_proposal": int(self.min_executions_for_proposal),
                "high_success_rate": float(self.high_success_rate),
                "low_failure_rate": float(self.low_failure_rate),
                "high_failure_rate": float(self.high_failure_rate),
                "high_deny_rate": float(self.high_deny_rate),
                "high_throttle_hit_rate": float(self.high_throttle_hit_rate),
                "high_budget_reject_rate": float(self.high_budget_reject_rate),
                "high_cost_threshold": float(self.high_cost_threshold),
                "low_utilization_threshold": int(self.low_utilization_threshold),
                "interval_decrease_factor": float(self.interval_decrease_factor),
                "interval_increase_factor": float(self.interval_increase_factor),
                "interval_min": int(self.interval_min),
                "interval_max": int(self.interval_max),
                "throttle_increase_step": float(self.throttle_increase_step),
                "throttle_decrease_step": float(self.throttle_decrease_step),
                "throttle_min": float(self.throttle_min),
                "throttle_max": float(self.throttle_max),
            }
        except Exception:  # noqa: BLE001
            return {}

    @classmethod
    def default(cls) -> "EvaluatorConfig":
        return cls()


# ============================================================
# PolicySnapshot
# ============================================================
@dataclass
class PolicySnapshot:
    """当前 Policy 状态快照(只读),供 evaluator 推断 old_value。

    字段(全部可选,evaluator 仅读取存在的字段):
    - module_intervals:   {module: interval}
    - module_throttles:   {module: throttle}
    - module_cooldowns:   {module: cooldown_seconds}
    - module_costs:       {module: cost}
    - budget_daily_cost_limit: float
    - budget_per_module_cost_limit: float
    """

    module_intervals: Dict[str, int] = field(default_factory=dict)
    module_throttles: Dict[str, float] = field(default_factory=dict)
    module_cooldowns: Dict[str, float] = field(default_factory=dict)
    module_costs: Dict[str, float] = field(default_factory=dict)
    budget_daily_cost_limit: float = 0.0
    budget_per_module_cost_limit: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "module_intervals": dict(self.module_intervals or {}),
                "module_throttles": dict(self.module_throttles or {}),
                "module_cooldowns": dict(self.module_cooldowns or {}),
                "module_costs": dict(self.module_costs or {}),
                "budget_daily_cost_limit": float(self.budget_daily_cost_limit or 0.0),
                "budget_per_module_cost_limit": float(self.budget_per_module_cost_limit or 0.0),
            }
        except Exception:  # noqa: BLE001
            return {}


# ============================================================
# AdaptiveEvaluator
# ============================================================
class AdaptiveEvaluator:
    """基于 PolicyMetrics + PolicySnapshot 评估并生成 PolicyAdjustmentProposal。

    用法:
        evaluator = AdaptiveEvaluator()
        proposals = evaluator.evaluate(
            metrics=collector.collect_all(),
            policy_snapshot=snapshot_fn(),
            cycle_id="c-1",
        )
        store.append_many(proposals)
    """

    def __init__(self, config: Optional[EvaluatorConfig] = None) -> None:
        self._lock = threading.RLock()
        self._config = config or EvaluatorConfig.default()
        # 评估统计
        self._stats: Dict[str, int] = {
            "evaluate_total": 0,
            "proposals_generated": 0,
            "modules_evaluated": 0,
            "modules_skipped_low_utilization": 0,
            "errors": 0,
        }

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def config(self) -> EvaluatorConfig:
        return self._config

    def get_stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return dict(self._stats)
        except Exception:  # noqa: BLE001
            return {}

    def reset_stats(self) -> None:
        try:
            with self._lock:
                self._stats = {
                    "evaluate_total": 0,
                    "proposals_generated": 0,
                    "modules_evaluated": 0,
                    "modules_skipped_low_utilization": 0,
                    "errors": 0,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptiveEvaluator.reset_stats 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def evaluate(
        self,
        metrics: Optional[Dict[str, PolicyMetrics]] = None,
        policy_snapshot: Optional[PolicySnapshot] = None,
        cycle_id: str = "",
    ) -> List[PolicyAdjustmentProposal]:
        """根据 metrics + 当前 policy snapshot 生成 proposal 列表。

        返回空列表表示"无可调整建议"。
        任何异常 → fail-soft 返回空列表。
        """
        try:
            with self._lock:
                self._stats["evaluate_total"] += 1
            metrics = metrics or {}
            snap = policy_snapshot or PolicySnapshot()
            cycle_id = str(cycle_id or "")

            proposals: List[PolicyAdjustmentProposal] = []
            for module, m in metrics.items():
                try:
                    if not isinstance(m, PolicyMetrics):
                        continue
                    p_list = self._evaluate_module(
                        metrics=m,
                        snap=snap,
                        cycle_id=cycle_id,
                    )
                    if p_list:
                        proposals.extend(p_list)
                        with self._lock:
                            self._stats["modules_evaluated"] += 1
                    else:
                        with self._lock:
                            self._stats["modules_skipped_low_utilization"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "AdaptiveEvaluator._evaluate_module(%s) 异常(已隔离): %s",
                        module, exc,
                    )
            with self._lock:
                self._stats["proposals_generated"] += len(proposals)
            return proposals
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("AdaptiveEvaluator.evaluate 异常(已隔离): %s", exc)
            return []

    def _evaluate_module(
        self,
        metrics: PolicyMetrics,
        snap: PolicySnapshot,
        cycle_id: str = "",
    ) -> List[PolicyAdjustmentProposal]:
        """评估单个模块。"""
        proposals: List[PolicyAdjustmentProposal] = []
        try:
            # 1. 低利用率 → 跳过(样本不足)
            if metrics.execute_count < self._config.min_executions_for_proposal:
                return proposals

            module = metrics.module
            old_interval = int(snap.module_intervals.get(module, 0) or 0)
            old_throttle = float(snap.module_throttles.get(module, 1.0) or 1.0)
            old_cost = float(snap.module_costs.get(module, 0.0) or 0.0)

            success_rate = metrics.success_rate
            failure_rate = metrics.failure_rate
            deny_count = metrics.deny_count
            throttle_hit = metrics.throttle_hit_count
            budget_reject = metrics.budget_reject_count

            # 计算拒绝率(以 evaluate_count + deny_count 为分母,粗略估计)
            total_observed = max(1, metrics.execute_count + deny_count)
            throttle_rate = throttle_hit / total_observed
            budget_rate = budget_reject / total_observed

            # 计算 avg_cost(若未显式设置 avg_cost,根据 total_cost/execute_count 推算)
            try:
                avg_cost = float(metrics.avg_cost or 0.0)
                if avg_cost <= 0.0 and int(metrics.execute_count or 0) > 0:
                    avg_cost = float(metrics.total_cost or 0.0) / float(metrics.execute_count)
            except Exception:
                avg_cost = 0.0

            evidence = metrics.to_dict()

            # 2. 高失败率 → 建议增加 interval(降低频率)
            if failure_rate >= self._config.high_failure_rate and old_interval > 0:
                new_interval = self._clamp_interval(
                    int(round(old_interval * self._config.interval_increase_factor))
                )
                if new_interval != old_interval:
                    confidence = min(0.95, failure_rate)
                    proposals.append(build_proposal(
                        module=module,
                        parameter=PARAM_THROTTLE_INTERVAL,
                        old_value=old_interval,
                        suggested_value=new_interval,
                        reason=(
                            f"failure_rate={failure_rate:.2%} >= "
                            f"{self._config.high_failure_rate:.2%}; "
                            "建议降低执行频率"
                        ),
                        confidence=confidence,
                        evidence=evidence,
                        cycle_id=cycle_id,
                        metadata={"trigger": "high_failure_rate"},
                    ))

            # 3. 高 throttle 命中率 → 建议增加 interval
            if throttle_rate >= self._config.high_throttle_hit_rate and old_interval > 0:
                new_interval = self._clamp_interval(
                    int(round(old_interval * self._config.interval_increase_factor))
                )
                if new_interval != old_interval:
                    confidence = min(0.95, throttle_rate + 0.1)
                    proposals.append(build_proposal(
                        module=module,
                        parameter=PARAM_THROTTLE_INTERVAL,
                        old_value=old_interval,
                        suggested_value=new_interval,
                        reason=(
                            f"throttle_hit_rate={throttle_rate:.2%} >= "
                            f"{self._config.high_throttle_hit_rate:.2%}; "
                            "建议降低执行频率"
                        ),
                        confidence=confidence,
                        evidence=evidence,
                        cycle_id=cycle_id,
                        metadata={"trigger": "high_throttle_hit_rate"},
                    ))

            # 4. 高 budget 拒绝率 → 建议降低 module_cost 或增加 interval
            if budget_rate >= self._config.high_budget_reject_rate:
                # 4a. 建议增加 interval(同步)
                if old_interval > 0:
                    new_interval = self._clamp_interval(
                        int(round(old_interval * self._config.interval_increase_factor))
                    )
                    if new_interval != old_interval:
                        proposals.append(build_proposal(
                            module=module,
                            parameter=PARAM_THROTTLE_INTERVAL,
                            old_value=old_interval,
                            suggested_value=new_interval,
                            reason=(
                                f"budget_reject_rate={budget_rate:.2%} >= "
                                f"{self._config.high_budget_reject_rate:.2%}; "
                                "建议降低执行频率以释放预算"
                            ),
                            confidence=min(0.95, budget_rate + 0.15),
                            evidence=evidence,
                            cycle_id=cycle_id,
                            metadata={"trigger": "high_budget_reject_rate"},
                        ))
                # 4b. 建议降低 module_cost(若 old_cost > 0)
                if old_cost > 0:
                    new_cost = max(0.0, old_cost * 0.7)  # 砍 30%
                    if new_cost != old_cost:
                        proposals.append(build_proposal(
                            module=module,
                            parameter=PARAM_BUDGET_MODULE_COST,
                            old_value=old_cost,
                            suggested_value=new_cost,
                            reason=(
                                f"budget_reject_rate={budget_rate:.2%} >= "
                                f"{self._config.high_budget_reject_rate:.2%}; "
                                "建议降低单次执行成本"
                            ),
                            confidence=min(0.95, budget_rate + 0.2),
                            evidence=evidence,
                            cycle_id=cycle_id,
                            metadata={"trigger": "high_budget_reject_rate_cost"},
                        ))

            # 5. 高成功率 + 低成本 + 高频 → 建议降低 interval(允许更频繁)
            if (
                success_rate >= self._config.high_success_rate
                and failure_rate <= self._config.low_failure_rate
                and avg_cost < self._config.high_cost_threshold
                and metrics.execute_count >= self._config.min_executions_for_proposal * 2
                and old_interval > 0
            ):
                new_interval = self._clamp_interval(
                    int(round(old_interval * self._config.interval_decrease_factor))
                )
                if new_interval != old_interval and new_interval >= self._config.interval_min:
                    proposals.append(build_proposal(
                        module=module,
                        parameter=PARAM_THROTTLE_INTERVAL,
                        old_value=old_interval,
                        suggested_value=new_interval,
                        reason=(
                            f"success_rate={success_rate:.2%} high, "
                            f"avg_cost={avg_cost:.2f} low; "
                            "建议提高执行频率"
                        ),
                        confidence=min(0.9, success_rate * 0.8),
                        evidence=evidence,
                        cycle_id=cycle_id,
                        metadata={"trigger": "high_success_low_cost"},
                    ))

            # 6. 极高 cost → 建议降低 throttle(降频)
            if avg_cost > self._config.high_cost_threshold and old_throttle > 0:
                new_throttle = self._clamp_throttle(
                    old_throttle - self._config.throttle_decrease_step
                )
                if new_throttle != old_throttle:
                    proposals.append(build_proposal(
                        module=module,
                        parameter=PARAM_THROTTLE_THROTTLE,
                        old_value=old_throttle,
                        suggested_value=new_throttle,
                        reason=(
                            f"avg_cost={avg_cost:.2f} > "
                            f"{self._config.high_cost_threshold}; "
                            "建议降低 throttle 倍率"
                        ),
                        confidence=min(0.85, avg_cost / 100.0),
                        evidence=evidence,
                        cycle_id=cycle_id,
                        metadata={"trigger": "high_avg_cost"},
                    ))

            return proposals
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptiveEvaluator._evaluate_module 异常(已隔离): %s", exc)
            return []

    def _clamp_interval(self, value: int) -> int:
        try:
            v = int(value)
        except Exception:  # noqa: BLE001
            return self._config.interval_min
        return max(self._config.interval_min, min(self._config.interval_max, v))

    def _clamp_throttle(self, value: float) -> float:
        try:
            v = float(value)
        except Exception:  # noqa: BLE001
            return self._config.throttle_min
        return max(self._config.throttle_min, min(self._config.throttle_max, v))


# ============================================================
# 工具
# ============================================================
def capture_policy_snapshot(
    throttle_registry: Any = None,
    runtime_budget: Any = None,
) -> PolicySnapshot:
    """从 ThrottleRegistry + RuntimeBudget 捕获当前 Policy 状态(只读)。

    - throttle_registry: ThrottleRegistry 实例(或 None)
    - runtime_budget:    RuntimeBudget 实例(或 None)
    返回 PolicySnapshot;任何访问异常 → 返回空 snapshot。
    """
    snap = PolicySnapshot()
    try:
        if throttle_registry is not None:
            try:
                registry_snap = throttle_registry.snapshot() or {}
            except Exception:  # noqa: BLE001
                registry_snap = {}
            for module, state in registry_snap.items():
                try:
                    snap.module_intervals[str(module)] = int(
                        state.get("interval", 0) or 0
                    )
                    snap.module_throttles[str(module)] = float(
                        state.get("throttle", 1.0) or 1.0
                    )
                    snap.module_cooldowns[str(module)] = float(
                        state.get("cooldown_seconds", 0.0) or 0.0
                    )
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.debug("capture_policy_snapshot(throttle) 异常(已隔离): %s", exc)
    try:
        if runtime_budget is not None:
            try:
                # module_costs
                get_module_cost = getattr(runtime_budget, "get_module_cost", None)
                if callable(get_module_cost):
                    # 尝试从 _module_costs 私有字段读
                    private = getattr(runtime_budget, "_module_costs", None)
                    if isinstance(private, dict):
                        for m, c in private.items():
                            snap.module_costs[str(m)] = float(c or 0.0)
                # budget limits
                budget_snap = runtime_budget.snapshot() if hasattr(runtime_budget, "snapshot") else None
                if budget_snap is not None:
                    if hasattr(budget_snap, "daily_cost_limit"):
                        snap.budget_daily_cost_limit = float(
                            getattr(budget_snap, "daily_cost_limit", 0.0) or 0.0
                        )
                    if hasattr(budget_snap, "per_module_cost_limit"):
                        snap.budget_per_module_cost_limit = float(
                            getattr(budget_snap, "per_module_cost_limit", 0.0) or 0.0
                        )
            except Exception as exc:  # noqa: BLE001
                logger.debug("capture_policy_snapshot(budget) 异常(已隔离): %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.debug("capture_policy_snapshot 异常(已隔离): %s", exc)
    return snap


def build_evaluator(config: Optional[EvaluatorConfig] = None) -> AdaptiveEvaluator:
    """构造一个 AdaptiveEvaluator(默认配置)。"""
    return AdaptiveEvaluator(config=config or EvaluatorConfig.default())
