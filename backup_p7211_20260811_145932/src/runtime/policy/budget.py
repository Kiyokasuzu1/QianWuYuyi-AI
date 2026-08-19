# -*- coding: utf-8 -*-
"""
src/runtime/policy/budget.py

Phase C.10.8 — Runtime Resource Budget

本文件实现 Runtime 资源预算管理:

- BudgetSnapshot          某一时刻的预算快照(已用 / 剩余 / 总额)
- BudgetLedger            集中管理所有模块的预算消耗(LLM calls / tokens / cost)
- RuntimeBudget           顶层预算控制器,管理每日 LLM 调用 / 模块执行成本 / token 预算
- BudgetRule              内置 PolicyRule:基于 RuntimeBudget 给出预算决策

设计原则:
- RuntimeBudget 不依赖具体业务模块(只接受 LLM 调用数 / 估算 cost)
- 预算不足时,BudgetRule 返回 RuntimeDecision(allowed=False, reason="budget_exceeded")
- 任何异常时,返回 None(fail-soft,交给下一条规则)
- RuntimeBudget 可独立于 PolicyEngine 单独使用(供 API / Admin 读取)

注意:
- 不引入第三方依赖(datetime / threading 即可)
- 默认预算值保守;实际部署可通过 set_limit() 调整
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .decision import (
    DECISION_MODE_NORMAL,
    RuntimeDecision,
)
from .policy_context import PolicyContext
from .policy_rule import BasePolicyRule

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================
DEFAULT_DAILY_LLM_CALL_LIMIT = 5000
DEFAULT_DAILY_TOKEN_LIMIT = 2_000_000
DEFAULT_DAILY_COST_LIMIT = 100.0
DEFAULT_PER_MODULE_COST_LIMIT = 50.0

# 默认模块成本(每次执行)
DEFAULT_MODULE_COSTS: Dict[str, float] = {
    "growth": 2.0,
    "initiative": 3.0,
    "dream": 5.0,
    "memory": 0.1,
    "emotion": 0.05,
    "self_reflection": 1.5,
    "perception": 0.5,
}

# BudgetRule 默认 priority(在 ThrottleRule 之后,AlwaysAllow 之前)
DEFAULT_BUDGET_PRIORITY = 100


# ============================================================
# BudgetSnapshot
# ============================================================
@dataclass
class BudgetSnapshot:
    """某一时刻的预算快照。"""

    daily_llm_calls: int = 0
    daily_llm_calls_limit: int = DEFAULT_DAILY_LLM_CALL_LIMIT
    daily_tokens: int = 0
    daily_tokens_limit: int = DEFAULT_DAILY_TOKEN_LIMIT
    daily_cost: float = 0.0
    daily_cost_limit: float = DEFAULT_DAILY_COST_LIMIT
    day_started_at: float = 0.0
    per_module_cost: Dict[str, float] = field(default_factory=dict)
    per_module_cost_limit: float = DEFAULT_PER_MODULE_COST_LIMIT

    def is_exceeded(self) -> bool:
        try:
            if int(self.daily_llm_calls) >= int(self.daily_llm_calls_limit):
                return True
            if int(self.daily_tokens) >= int(self.daily_tokens_limit):
                return True
            if float(self.daily_cost) >= float(self.daily_cost_limit):
                return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def remaining_llm_calls(self) -> int:
        try:
            return max(0, int(self.daily_llm_calls_limit) - int(self.daily_llm_calls))
        except Exception:  # noqa: BLE001
            return 0

    def remaining_tokens(self) -> int:
        try:
            return max(0, int(self.daily_tokens_limit) - int(self.daily_tokens))
        except Exception:  # noqa: BLE001
            return 0

    def remaining_cost(self) -> float:
        try:
            return max(0.0, float(self.daily_cost_limit) - float(self.daily_cost))
        except Exception:  # noqa: BLE001
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "daily_llm_calls": int(self.daily_llm_calls),
                "daily_llm_calls_limit": int(self.daily_llm_calls_limit),
                "daily_tokens": int(self.daily_tokens),
                "daily_tokens_limit": int(self.daily_tokens_limit),
                "daily_cost": float(self.daily_cost),
                "daily_cost_limit": float(self.daily_cost_limit),
                "day_started_at": float(self.day_started_at),
                "per_module_cost": dict(self.per_module_cost or {}),
                "per_module_cost_limit": float(self.per_module_cost_limit),
                "exceeded": bool(self.is_exceeded()),
            }
        except Exception:  # noqa: BLE001
            return {
                "daily_llm_calls": 0,
                "daily_llm_calls_limit": DEFAULT_DAILY_LLM_CALL_LIMIT,
                "daily_tokens": 0,
                "daily_tokens_limit": DEFAULT_DAILY_TOKEN_LIMIT,
                "daily_cost": 0.0,
                "daily_cost_limit": DEFAULT_DAILY_COST_LIMIT,
                "day_started_at": 0.0,
                "per_module_cost": {},
                "per_module_cost_limit": DEFAULT_PER_MODULE_COST_LIMIT,
                "exceeded": False,
            }


# ============================================================
# BudgetLedger
# ============================================================
class BudgetLedger:
    """预算账本:管理所有计数 / 累计。

    线程安全;提供 consume / reset / snapshot 接口。
    """

    def __init__(
        self,
        llm_calls_limit: int = DEFAULT_DAILY_LLM_CALL_LIMIT,
        tokens_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
        cost_limit: float = DEFAULT_DAILY_COST_LIMIT,
        per_module_cost_limit: float = DEFAULT_PER_MODULE_COST_LIMIT,
    ) -> None:
        self._lock = threading.RLock()
        self._llm_calls_limit = max(0, int(llm_calls_limit))
        self._tokens_limit = max(0, int(tokens_limit))
        self._cost_limit = max(0.0, float(cost_limit))
        self._per_module_cost_limit = max(0.0, float(per_module_cost_limit))
        self._llm_calls = 0
        self._tokens = 0
        self._cost = 0.0
        self._per_module_cost: Dict[str, float] = {}
        self._day_started_at = time.time()
        self._history: List[Dict[str, Any]] = []
        self._total_consume_calls = 0
        self._total_rejected_calls = 0

    # --------------------------------------------------------
    # 限额调整
    # --------------------------------------------------------
    def set_limits(
        self,
        llm_calls_limit: Optional[int] = None,
        tokens_limit: Optional[int] = None,
        cost_limit: Optional[float] = None,
        per_module_cost_limit: Optional[float] = None,
    ) -> None:
        with self._lock:
            if llm_calls_limit is not None:
                self._llm_calls_limit = max(0, int(llm_calls_limit))
            if tokens_limit is not None:
                self._tokens_limit = max(0, int(tokens_limit))
            if cost_limit is not None:
                self._cost_limit = max(0.0, float(cost_limit))
            if per_module_cost_limit is not None:
                self._per_module_cost_limit = max(0.0, float(per_module_cost_limit))

    def reset_daily(self, now: Optional[float] = None) -> None:
        with self._lock:
            self._llm_calls = 0
            self._tokens = 0
            self._cost = 0.0
            self._per_module_cost.clear()
            self._day_started_at = float(now if now is not None else time.time())

    def reset_all(self) -> None:
        with self._lock:
            self.reset_daily()
            self._history.clear()
            self._total_consume_calls = 0
            self._total_rejected_calls = 0

    # --------------------------------------------------------
    # 消耗
    # --------------------------------------------------------
    def can_consume(
        self,
        module: str = "",
        cost: float = 0.0,
        tokens: int = 0,
    ) -> tuple:
        """检查是否还能消耗给定资源。

        返回: (ok: bool, reason: str)
        - reason: "ok" / "llm_calls_exceeded" / "tokens_exceeded" / "cost_exceeded" / "module_cost_exceeded"

        行为说明:
        - 调用时不传 args 表示"任意 1 次 LLM 调用";若已无任何 headroom,返回 False
        - 调用时传 args,会校验 current + request <= limit
        """
        try:
            with self._lock:
                # 第一轮:检查"已用尽"的情况(避免 0-request 误判为 ok)
                if (
                    self._llm_calls_limit > 0
                    and self._llm_calls >= self._llm_calls_limit
                ):
                    return False, "llm_calls_exceeded"
                if (
                    self._tokens_limit > 0
                    and self._tokens >= self._tokens_limit
                ):
                    return False, "tokens_exceeded"
                if (
                    self._cost_limit > 0
                    and self._cost >= self._cost_limit
                ):
                    return False, "cost_exceeded"
                # 第二轮:检查请求是否能容纳
                if self._llm_calls + 1 > self._llm_calls_limit:
                    return False, "llm_calls_exceeded"
                if self._tokens + max(0, int(tokens)) > self._tokens_limit:
                    return False, "tokens_exceeded"
                if self._cost + max(0.0, float(cost)) > self._cost_limit:
                    return False, "cost_exceeded"
                if module and self._per_module_cost_limit > 0:
                    module_used = float(self._per_module_cost.get(str(module), 0.0))
                    if module_used + max(0.0, float(cost)) > self._per_module_cost_limit:
                        return False, "module_cost_exceeded"
                return True, "ok"
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetLedger.can_consume 异常(已隔离): %s", exc)
            return True, "ok"  # fail-soft:允许

    def consume(
        self,
        module: str = "",
        cost: float = 0.0,
        tokens: int = 0,
        llm_calls: int = 1,
        now: Optional[float] = None,
    ) -> bool:
        """尝试消耗预算,成功返回 True,失败返回 False(不扣除)。"""
        try:
            with self._lock:
                ok, reason = self.can_consume(
                    module=module,
                    cost=cost,
                    tokens=tokens,
                )
                if not ok:
                    self._total_rejected_calls += 1
                    return False
                self._llm_calls += max(0, int(llm_calls))
                self._tokens += max(0, int(tokens))
                self._cost += max(0.0, float(cost))
                if module:
                    prev = float(self._per_module_cost.get(str(module), 0.0))
                    self._per_module_cost[str(module)] = prev + max(0.0, float(cost))
                self._total_consume_calls += 1
                # 记录历史(只保留最近 100 条)
                self._history.append({
                    "module": str(module or ""),
                    "cost": float(cost),
                    "tokens": int(tokens),
                    "llm_calls": int(llm_calls),
                    "ts": float(now if now is not None else time.time()),
                })
                if len(self._history) > 100:
                    self._history = self._history[-100:]
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetLedger.consume 异常(已隔离): %s", exc)
            return True  # fail-soft:放行

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            return BudgetSnapshot(
                daily_llm_calls=self._llm_calls,
                daily_llm_calls_limit=self._llm_calls_limit,
                daily_tokens=self._tokens,
                daily_tokens_limit=self._tokens_limit,
                daily_cost=self._cost,
                daily_cost_limit=self._cost_limit,
                day_started_at=self._day_started_at,
                per_module_cost=dict(self._per_module_cost),
                per_module_cost_limit=self._per_module_cost_limit,
            )

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "llm_calls_used": int(self._llm_calls),
                "llm_calls_limit": int(self._llm_calls_limit),
                "tokens_used": int(self._tokens),
                "tokens_limit": int(self._tokens_limit),
                "cost_used": int(self._cost * 1000),  # 转为 int(milli-units) 避免精度
                "cost_limit": int(self._cost_limit * 1000),
                "consume_calls": int(self._total_consume_calls),
                "rejected_calls": int(self._total_rejected_calls),
            }


# ============================================================
# RuntimeBudget
# ============================================================
class RuntimeBudget:
    """Runtime 资源预算顶层控制器。

    持有:
    - ledger:       BudgetLedger(实际账本)
    - module_costs: 模块成本表(每次执行的估算 cost)

    提供:
    - estimate_cost(module)            → 估算单次执行成本
    - check(module, cost, tokens)      → 检查预算
    - charge(module, cost, tokens)     → 扣除预算
    - snapshot()                       → 当前预算快照
    """

    def __init__(
        self,
        ledger: Optional[BudgetLedger] = None,
        module_costs: Optional[Dict[str, float]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._ledger = ledger if ledger is not None else BudgetLedger()
        self._module_costs: Dict[str, float] = {}
        for module, cost in (module_costs or DEFAULT_MODULE_COSTS).items():
            self._module_costs[str(module)] = max(0.0, float(cost))

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    def set_module_cost(self, module: str, cost: float) -> None:
        with self._lock:
            self._module_costs[str(module or "")] = max(0.0, float(cost))

    def get_module_cost(self, module: str) -> float:
        with self._lock:
            return float(self._module_costs.get(str(module or ""), 0.0))

    def estimate_cost(self, module: str) -> float:
        try:
            return self.get_module_cost(module)
        except Exception:  # noqa: BLE001
            return 0.0

    def check(
        self,
        module: str = "",
        cost: float = 0.0,
        tokens: int = 0,
    ) -> tuple:
        try:
            return self._ledger.can_consume(
                module=module,
                cost=cost,
                tokens=tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeBudget.check 异常(已隔离): %s", exc)
            return True, "ok"

    def charge(
        self,
        module: str = "",
        cost: float = 0.0,
        tokens: int = 0,
        llm_calls: int = 1,
    ) -> bool:
        try:
            return self._ledger.consume(
                module=module,
                cost=cost,
                tokens=tokens,
                llm_calls=llm_calls,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeBudget.charge 异常(已隔离): %s", exc)
            return True

    def reset_daily(self) -> None:
        try:
            self._ledger.reset_daily()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeBudget.reset_daily 异常(已隔离): %s", exc)

    def snapshot(self) -> BudgetSnapshot:
        try:
            return self._ledger.snapshot()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeBudget.snapshot 异常(已隔离): %s", exc)
            return BudgetSnapshot()

    def stats(self) -> Dict[str, int]:
        try:
            return self._ledger.stats()
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeBudget.stats 异常(已隔离): %s", exc)
            return {}

    def is_budget_exceeded(self) -> bool:
        try:
            return self.snapshot().is_exceeded()
        except Exception:  # noqa: BLE001
            return False


# ============================================================
# BudgetRule
# ============================================================
class BudgetRule(BasePolicyRule):
    """基于 RuntimeBudget 的预算规则。

    行为:
    - 估算 module 成本(从 RuntimeBudget.module_costs 读取)
    - 若预算不足 → deny(reason=budget_exceeded, budget_cost=estimated)
    - 若预算充足 → allow(reason=budget_ok, budget_cost=estimated, throttle=1.0)
    - 任何异常 → 返回 None(fail-soft,交给下一条规则)

    默认 priority = 100(在 ThrottleRule=200 之后,AlwaysAllow=0 之前)
    """

    def __init__(
        self,
        budget: Optional[RuntimeBudget] = None,
        priority: int = DEFAULT_BUDGET_PRIORITY,
    ) -> None:
        self._rule_name = "BudgetRule"
        self._rule_priority = int(priority)
        self._budget = budget if budget is not None else RuntimeBudget()

    @property
    def budget(self) -> RuntimeBudget:
        return self._budget

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            if ctx is None:
                return None
            module = str(getattr(ctx, "module", "") or "").strip().lower()
            if not module:
                return None
            # 估算成本
            cost = self._budget.estimate_cost(module)
            # 检查预算
            ok, reason = self._budget.check(module=module, cost=cost)
            meta = {
                "rule": self.name,
                "module": module,
                "estimated_cost": float(cost),
            }
            if not ok:
                meta["reject_reason"] = str(reason)
                return RuntimeDecision.deny(
                    module=module,
                    reason="budget_exceeded",
                    mode=DECISION_MODE_NORMAL,
                    throttle=0.0,
                    budget_cost=float(cost),
                    metadata=meta,
                )
            return RuntimeDecision.allow(
                module=module,
                reason="budget_ok",
                mode=DECISION_MODE_NORMAL,
                throttle=1.0,
                budget_cost=float(cost),
                metadata=meta,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# 工厂
# ============================================================
def build_default_budget(
    module_costs: Optional[Dict[str, float]] = None,
    llm_calls_limit: int = DEFAULT_DAILY_LLM_CALL_LIMIT,
    tokens_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
    cost_limit: float = DEFAULT_DAILY_COST_LIMIT,
) -> RuntimeBudget:
    """构造默认的 RuntimeBudget。"""
    ledger = BudgetLedger(
        llm_calls_limit=llm_calls_limit,
        tokens_limit=tokens_limit,
        cost_limit=cost_limit,
    )
    return RuntimeBudget(ledger=ledger, module_costs=module_costs)


def build_default_budget_rule(
    budget: Optional[RuntimeBudget] = None,
    priority: int = DEFAULT_BUDGET_PRIORITY,
) -> BudgetRule:
    """构造默认的 BudgetRule。"""
    return BudgetRule(
        budget=budget if budget is not None else build_default_budget(),
        priority=priority,
    )
