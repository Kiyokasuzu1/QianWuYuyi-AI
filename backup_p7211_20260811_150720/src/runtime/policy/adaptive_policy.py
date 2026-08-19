# -*- coding: utf-8 -*-
"""
src/runtime/policy/adaptive_policy.py

Phase C.10.8 — Adaptive Policy Layer

AdaptivePolicyLayer 是一站式封装,把 Throttle + Budget + Base Policy 组合起来,
对 PolicyEngine 透明地提供:
- 自动注册 ThrottleRule 和 BudgetRule
- 提供"模块执行后"的状态更新接口(tick / charge)
- 暴露 snapshot / stats 供 Runtime 集成

设计原则:
- 不修改 PolicyEngine 内部,只在外部包一层
- 不引入第三方依赖
- 完全可独立测试(不需要 Runtime)
- 异常全部隔离
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from .budget import (
    BudgetLedger,
    BudgetRule,
    BudgetSnapshot,
    RuntimeBudget,
    build_default_budget,
    build_default_budget_rule,
)
from .decision import (
    DECISION_MODE_NORMAL,
    RuntimeDecision,
    default_allow_decision,
    default_throttle_decision,
)
from .policy_context import PolicyContext
from .policy_engine import PolicyEngine
from .policy_rule import (
    AlwaysAllowRule,
    BasePolicyRule,
    DisabledModuleRule,
    MaintenanceRule,
    PolicyRule,
    SafeModeRule,
    build_default_rules,
)
from .throttle import (
    ThrottleRegistry,
    ThrottleRule,
    ThrottleState,
    build_default_throttle_rule,
    build_throttle_registry,
)

logger = logging.getLogger(__name__)


# ============================================================
# AdaptivePolicyLayer
# ============================================================
class AdaptivePolicyLayer:
    """组合 Throttle + Budget + Base Policy 的自适应策略层。

    责任:
    1. 持有 ThrottleRegistry / RuntimeBudget / PolicyEngine
    2. 默认注册 ThrottleRule(priority=200) 和 BudgetRule(priority=100)
    3. 提供 on_module_executed / on_module_skipped 接口供 Runtime 调用
    4. 提供 snapshot / stats 接口供监控/Admin 读取
    5. 任何方法异常隔离

    使用方式:
        layer = AdaptivePolicyLayer.build_default()
        decision = layer.evaluate("memory", context=ctx)
        if decision.allowed:
            layer.on_module_executed("memory", cost=decision.budget_cost, tokens=100, cycle_id=ctx.cycle_id)
    """

    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        throttle_registry: Optional[ThrottleRegistry] = None,
        runtime_budget: Optional[RuntimeBudget] = None,
        throttle_rule: Optional[ThrottleRule] = None,
        budget_rule: Optional[BudgetRule] = None,
        add_base_rules: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        # 基础 PolicyEngine(若未提供则新建一个空的,再追加规则)
        self._engine = policy_engine if policy_engine is not None else PolicyEngine(
            rules=[],
            use_default_rules=False,
        )
        # Throttle
        self._throttle_registry = throttle_registry if throttle_registry is not None else ThrottleRegistry()
        self._throttle_rule = throttle_rule if throttle_rule is not None else ThrottleRule(
            registry=self._throttle_registry,
        )
        # Budget
        self._runtime_budget = runtime_budget if runtime_budget is not None else RuntimeBudget()
        self._budget_rule = budget_rule if budget_rule is not None else BudgetRule(
            budget=self._runtime_budget,
        )
        # 注册到 engine
        if add_base_rules:
            self._register_base_rules()
        self._register_adaptive_rules()

    # --------------------------------------------------------
    # 构造器
    # --------------------------------------------------------
    @classmethod
    def build_default(
        cls,
        intervals: Optional[Dict[str, int]] = None,
        throttles: Optional[Dict[str, float]] = None,
        cooldowns: Optional[Dict[str, float]] = None,
        module_costs: Optional[Dict[str, float]] = None,
        llm_calls_limit: int = 5000,
        tokens_limit: int = 2_000_000,
        cost_limit: float = 100.0,
        add_base_rules: bool = True,
    ) -> "AdaptivePolicyLayer":
        """使用默认配置构造 AdaptivePolicyLayer。"""
        registry = build_throttle_registry(
            intervals=intervals,
            throttles=throttles,
            cooldowns=cooldowns,
        )
        budget = build_default_budget(
            module_costs=module_costs,
            llm_calls_limit=llm_calls_limit,
            tokens_limit=tokens_limit,
            cost_limit=cost_limit,
        )
        return cls(
            throttle_registry=registry,
            runtime_budget=budget,
            add_base_rules=add_base_rules,
        )

    @classmethod
    def from_engine(
        cls,
        policy_engine: PolicyEngine,
        add_base_rules: bool = False,
    ) -> "AdaptivePolicyLayer":
        """从已存在的 PolicyEngine 构造(只追加 throttle/budget 规则)。"""
        return cls(
            policy_engine=policy_engine,
            add_base_rules=add_base_rules,
        )

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _register_base_rules(self) -> None:
        try:
            existing_names = {
                getattr(r, "name", type(r).__name__)
                for r in self._engine.get_rules()
            }
            for rule in build_default_rules():
                name = getattr(rule, "name", type(rule).__name__)
                if name not in existing_names:
                    self._engine.add_rule(rule)
                    existing_names.add(name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer._register_base_rules 异常: %s", exc)

    def _register_adaptive_rules(self) -> None:
        try:
            existing_names = {
                getattr(r, "name", type(r).__name__)
                for r in self._engine.get_rules()
            }
            for rule in (self._throttle_rule, self._budget_rule):
                name = getattr(rule, "name", type(rule).__name__)
                if name not in existing_names:
                    self._engine.add_rule(rule)
                    existing_names.add(name)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer._register_adaptive_rules 异常: %s", exc)

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def engine(self) -> PolicyEngine:
        return self._engine

    @property
    def throttle_registry(self) -> ThrottleRegistry:
        return self._throttle_registry

    @property
    def runtime_budget(self) -> RuntimeBudget:
        return self._runtime_budget

    @property
    def throttle_rule(self) -> ThrottleRule:
        return self._throttle_rule

    @property
    def budget_rule(self) -> BudgetRule:
        return self._budget_rule

    # --------------------------------------------------------
    # 核心:评估 + 后置记录
    # --------------------------------------------------------
    def evaluate(
        self,
        module: str,
        context: Optional[PolicyContext] = None,
    ) -> RuntimeDecision:
        """评估模块执行策略。

        任何异常 → fail-soft:allow + throttle=1.0
        """
        try:
            return self._engine.evaluate(str(module or ""), context=context)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer.evaluate 异常(已隔离): %s", exc)
            return default_throttle_decision(module=str(module or ""))

    def on_module_executed(
        self,
        module: str,
        cost: float = 0.0,
        tokens: int = 0,
        cycle_id: str = "",
        llm_calls: int = 1,
        now: Optional[float] = None,
    ) -> bool:
        """记录模块已执行(throttle tick + budget charge)。

        返回 True 表示已扣费成功 / False 表示 budget 不足(被拒绝)。
        """
        try:
            # 1) throttle tick
            try:
                self._throttle_registry.tick(
                    module=str(module or ""),
                    cycle_id=str(cycle_id or ""),
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_module_executed tick 异常: %s", exc)
            # 2) budget charge(若 cost 为 0 则用 estimated cost)
            try:
                if cost <= 0:
                    cost = self._runtime_budget.estimate_cost(str(module or ""))
                return bool(self._runtime_budget.charge(
                    module=str(module or ""),
                    cost=float(cost),
                    tokens=int(tokens),
                    llm_calls=int(llm_calls),
                ))
            except Exception as exc:  # noqa: BLE001
                logger.debug("on_module_executed charge 异常: %s", exc)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_module_executed 异常(已隔离): %s", exc)
            return True

    def on_module_skipped(
        self,
        module: str,
        reason: str = "",
    ) -> None:
        """记录模块被跳过(仅更新统计)。"""
        try:
            self._throttle_registry.record_skip(
                module=str(module or ""),
                reason=str(reason or ""),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("on_module_skipped 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 状态/快照
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        try:
            return {
                "policy_engine": self._engine.to_dict() if hasattr(self._engine, "to_dict") else {},
                "throttle_registry": self._throttle_registry.snapshot(),
                "budget": self._runtime_budget.snapshot().to_dict(),
                "throttle_stats": self._throttle_registry.stats(),
                "budget_stats": self._runtime_budget.stats(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer.snapshot 异常(已隔离): %s", exc)
            return {
                "policy_engine": {},
                "throttle_registry": {},
                "budget": {},
                "throttle_stats": {},
                "budget_stats": {},
            }

    def stats(self) -> Dict[str, Any]:
        try:
            return {
                "engine_stats": self._engine.get_stats(),
                "throttle_stats": self._throttle_registry.stats(),
                "budget_stats": self._runtime_budget.stats(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer.stats 异常(已隔离): %s", exc)
            return {
                "engine_stats": {},
                "throttle_stats": {},
                "budget_stats": {},
            }

    def health_check(self) -> Dict[str, Any]:
        try:
            return {
                "ok": True,
                "engine": self._engine.health_check() if hasattr(self._engine, "health_check") else {},
                "throttle": self._throttle_registry.stats(),
                "budget": self._runtime_budget.stats(),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer.health_check 异常(已隔离): %s", exc)
            return {"ok": False, "error": str(exc)}

    def reset(self) -> None:
        """测试用:重置 throttle 统计 / budget 累计。"""
        try:
            self._throttle_registry.reset_stats()
            self._runtime_budget.reset_daily()
        except Exception as exc:  # noqa: BLE001
            logger.debug("AdaptivePolicyLayer.reset 异常(已隔离): %s", exc)
