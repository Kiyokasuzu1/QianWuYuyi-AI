# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/apply_service.py

Phase C.10.10 — Policy Apply Service

本文件实现:
- PolicyApplyConfig      apply 配置(auto_apply_enabled 等)
- PolicyApplyService     将 approved proposal 应用到 ThrottleRegistry / RuntimeBudget
- ApplyOutcome / PolicyApplyResult

约束:
- 不直接修改业务模块
- 只能调整 ThrottleRegistry 的 interval / throttle / cooldown
- 只能调整 RuntimeBudget 的 daily_cost_limit / daily_tokens_limit /
  daily_llm_calls_limit / per_module_cost_limit / module_cost
- 任何异常 → fail-soft,失败时返回 outcome="failed"

Safe Auto 模式:
- 允许 throttle.interval 调整(任何幅度)
- 允许 throttle.throttle 调整,但变化不超过 20%
- 允许 budget 增加限制(只能增加限额)
- 禁止 throttle.cooldown_seconds(任何调整都需要 manual)
- 禁止修改 ControlState
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..feedback.proposal import (
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_DAILY_LLM_CALLS,
    PARAM_BUDGET_DAILY_TOKENS,
    PARAM_BUDGET_MODULE_COST,
    PARAM_BUDGET_PER_MODULE_COST,
    PARAM_THROTTLE_COOLDOWN,
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
    PolicyAdjustmentProposal,
)

logger = logging.getLogger(__name__)


# ============================================================
# 允许的 apply 参数
# ============================================================
ALLOWED_THROTTLE_PARAMETERS = frozenset({
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
    PARAM_THROTTLE_COOLDOWN,
})
ALLOWED_BUDGET_PARAMETERS = frozenset({
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_DAILY_TOKENS,
    PARAM_BUDGET_DAILY_LLM_CALLS,
    PARAM_BUDGET_PER_MODULE_COST,
    PARAM_BUDGET_MODULE_COST,
})
ALL_APPLY_PARAMETERS = ALLOWED_THROTTLE_PARAMETERS | ALLOWED_BUDGET_PARAMETERS

# safe_auto 模式下允许的参数(更严格)
SAFE_AUTO_THROTTLE_PARAMETERS = frozenset({
    PARAM_THROTTLE_INTERVAL,
    PARAM_THROTTLE_THROTTLE,
})
SAFE_AUTO_BUDGET_PARAMETERS = frozenset({
    PARAM_BUDGET_DAILY_COST,
    PARAM_BUDGET_DAILY_TOKENS,
    PARAM_BUDGET_DAILY_LLM_CALLS,
    PARAM_BUDGET_PER_MODULE_COST,
})

DEFAULT_MAX_THROTTLE_DELTA = 0.20  # 20%


# ============================================================
# ApplyOutcome
# ============================================================
APPLY_OUTCOME_SUCCESS = "success"
APPLY_OUTCOME_FAILED = "failed"
APPLY_OUTCOME_REJECTED = "rejected"  # 被 safe_auto 模式拒绝


# ============================================================
# PolicyApplyResult
# ============================================================
@dataclass
class PolicyApplyResult:
    """单条 apply 的结果。

    字段:
    - outcome:        success / failed / rejected
    - proposal_id:    对应的 proposal_id
    - module:         模块名
    - parameter:      参数名
    - old_value:      apply 前的值
    - new_value:      apply 后的值(可能 = old_value 当 outcome=failed)
    - error:          错误信息
    - snapshot_id:    apply 前 snapshot 的 ID(可回滚)
    - timestamp:      时间戳
    """

    outcome: str = APPLY_OUTCOME_FAILED
    proposal_id: str = ""
    module: str = ""
    parameter: str = ""
    old_value: Any = None
    new_value: Any = None
    error: str = ""
    snapshot_id: str = ""
    timestamp: float = 0.0

    @property
    def success(self) -> bool:
        return self.outcome == APPLY_OUTCOME_SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "outcome": str(self.outcome),
                "proposal_id": str(self.proposal_id),
                "module": str(self.module),
                "parameter": str(self.parameter),
                "old_value": self.old_value,
                "new_value": self.new_value,
                "error": str(self.error),
                "snapshot_id": str(self.snapshot_id),
                "timestamp": float(self.timestamp),
                "success": bool(self.success),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApplyResult.to_dict 异常(已隔离): %s", exc)
            return {
                "outcome": APPLY_OUTCOME_FAILED,
                "proposal_id": "",
                "module": "",
                "parameter": "",
                "old_value": None,
                "new_value": None,
                "error": "to_dict_failed",
                "snapshot_id": "",
                "timestamp": 0.0,
                "success": False,
            }


# ============================================================
# PolicyApplyConfig
# ============================================================
@dataclass
class PolicyApplyConfig:
    """Apply 配置。

    字段:
    - auto_apply_enabled:   是否启用自动 apply(默认 False)
                            - False: 任何 apply 都需要人工 approve
                            - True:  approved proposal 走 apply 闭环
    - safe_auto_mode:       True=仅允许 safe 参数 / False=允许所有参数
    - max_throttle_delta:   safe_auto 模式下 throttle 变化比例上限(默认 0.20)
    - require_snapshot:     apply 前必须先 snapshot(默认 True)
    - require_approval:     apply 前必须有 approval(默认 True)
    - actor:                当前 actor(reviewer / system / runtime)
    """

    auto_apply_enabled: bool = False
    safe_auto_mode: bool = True
    max_throttle_delta: float = DEFAULT_MAX_THROTTLE_DELTA
    require_snapshot: bool = True
    require_approval: bool = True
    actor: str = "runtime"

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "auto_apply_enabled": bool(self.auto_apply_enabled),
                "safe_auto_mode": bool(self.safe_auto_mode),
                "max_throttle_delta": float(self.max_throttle_delta),
                "require_snapshot": bool(self.require_snapshot),
                "require_approval": bool(self.require_approval),
                "actor": str(self.actor),
            }
        except Exception:  # noqa: BLE001
            return {}

    @classmethod
    def manual_mode(cls, actor: str = "human") -> "PolicyApplyConfig":
        """默认 manual 模式(auto_apply=False,safe_auto=True)。"""
        return cls(
            auto_apply_enabled=False,
            safe_auto_mode=True,
            require_snapshot=True,
            require_approval=True,
            actor=actor,
        )

    @classmethod
    def safe_auto_mode(
        cls,
        max_throttle_delta: float = DEFAULT_MAX_THROTTLE_DELTA,
        actor: str = "system",
    ) -> "PolicyApplyConfig":
        """Safe auto 模式(auto_apply=True,但仅允许 safe 参数)。"""
        return cls(
            auto_apply_enabled=True,
            safe_auto_mode=True,
            max_throttle_delta=max_throttle_delta,
            require_snapshot=True,
            require_approval=True,
            actor=actor,
        )


# ============================================================
# PolicyApplyService
# ============================================================
class PolicyApplyService:
    """Apply 控制器。

    行为:
    - 只调整 ThrottleRegistry / RuntimeBudget 的参数
    - 不修改任何业务模块
    - 不修改 ControlState
    - 任何异常 → fail-soft
    """

    def __init__(
        self,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        snapshot_manager: Any = None,
        config: Optional[PolicyApplyConfig] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._throttle_registry = throttle_registry
        self._runtime_budget = runtime_budget
        self._snapshot_manager = snapshot_manager
        self._config = config or PolicyApplyConfig.manual_mode()
        self._stats: Dict[str, int] = {
            "apply_total": 0,
            "apply_success": 0,
            "apply_failed": 0,
            "apply_rejected": 0,
            "rollback_total": 0,
            "rollback_success": 0,
            "rollback_failed": 0,
        }

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def config(self) -> PolicyApplyConfig:
        return self._config

    @property
    def throttle_registry(self) -> Any:
        return self._throttle_registry

    @property
    def runtime_budget(self) -> Any:
        return self._runtime_budget

    def configure(
        self,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        snapshot_manager: Any = None,
        config: Optional[PolicyApplyConfig] = None,
    ) -> None:
        """运行时注入依赖。"""
        with self._lock:
            if throttle_registry is not None:
                self._throttle_registry = throttle_registry
            if runtime_budget is not None:
                self._runtime_budget = runtime_budget
            if snapshot_manager is not None:
                self._snapshot_manager = snapshot_manager
            if config is not None:
                self._config = config

    def get_stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return dict(self._stats)
        except Exception:  # noqa: BLE001
            return {}

    def reset_stats(self) -> None:
        try:
            with self._lock:
                for k in self._stats:
                    self._stats[k] = 0
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def is_parameter_allowed(
        self,
        parameter: str,
        old_value: Any = None,
        new_value: Any = None,
    ) -> Tuple[bool, str]:
        """检查 parameter 在当前 config 下是否允许 apply。

        返回 (allowed, reason)
        """
        try:
            if not self._config.auto_apply_enabled:
                # 人工模式:所有 ALLOWED 都可(但仍需 approval)
                if parameter in ALL_APPLY_PARAMETERS:
                    return True, "manual_mode"
                return False, f"parameter_not_supported:{parameter}"
            # safe_auto 模式
            if not self._config.safe_auto_mode:
                # full auto 模式:仅禁止 cooldown
                if parameter in ALLOWED_THROTTLE_PARAMETERS and parameter == PARAM_THROTTLE_COOLDOWN:
                    return False, "cooldown_requires_manual"
                if parameter in ALL_APPLY_PARAMETERS:
                    return True, "auto_mode"
                return False, f"parameter_not_supported:{parameter}"
            # safe_auto 模式
            if parameter in SAFE_AUTO_THROTTLE_PARAMETERS:
                if parameter == PARAM_THROTTLE_THROTTLE:
                    # 检查 throttle 变化比例
                    try:
                        old_v = float(old_value if old_value is not None else 1.0)
                        new_v = float(new_value if new_value is not None else 1.0)
                        if old_v <= 0:
                            return True, "safe_auto"
                        delta = abs(new_v - old_v) / max(0.001, old_v)
                        if delta > float(self._config.max_throttle_delta):
                            return False, f"throttle_delta_exceeds:{delta:.2%}"
                    except Exception:  # noqa: BLE001
                        pass
                return True, "safe_auto"
            if parameter in SAFE_AUTO_BUDGET_PARAMETERS:
                # budget 仅允许增加限制(更宽松)
                try:
                    if old_value is not None and new_value is not None:
                        if float(new_value) < float(old_value):
                            return False, "budget_must_increase_in_safe_auto"
                except Exception:  # noqa: BLE001
                    pass
                return True, "safe_auto"
            return False, f"parameter_not_allowed_in_safe_auto:{parameter}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("is_parameter_allowed 异常(已隔离): %s", exc)
            return False, f"check_failed:{exc}"

    # --------------------------------------------------------
    # Apply
    # --------------------------------------------------------
    def apply(
        self,
        proposal: PolicyAdjustmentProposal,
    ) -> PolicyApplyResult:
        """应用一条 proposal 到运行时 Policy。

        流程:
        - 检查 parameter 是否允许
        - 捕获 snapshot(若 require_snapshot)
        - 调用对应 setter
        - 失败 → 尝试回滚
        """
        try:
            with self._lock:
                self._stats["apply_total"] += 1
            if not isinstance(proposal, PolicyAdjustmentProposal):
                with self._lock:
                    self._stats["apply_failed"] += 1
                return PolicyApplyResult(
                    outcome=APPLY_OUTCOME_FAILED,
                    error="invalid_proposal_type",
                )

            # 1. 参数检查
            allowed, reason = self.is_parameter_allowed(
                parameter=str(proposal.parameter),
                old_value=proposal.old_value,
                new_value=proposal.suggested_value,
            )
            if not allowed:
                with self._lock:
                    self._stats["apply_rejected"] += 1
                return PolicyApplyResult(
                    outcome=APPLY_OUTCOME_REJECTED,
                    proposal_id=str(proposal.proposal_id),
                    module=str(proposal.module),
                    parameter=str(proposal.parameter),
                    old_value=proposal.old_value,
                    new_value=proposal.suggested_value,
                    error=reason,
                )

            # 2. snapshot
            snapshot_id = ""
            if self._config.require_snapshot and self._snapshot_manager is not None:
                try:
                    snaps = self._snapshot_manager.create_snapshot(
                        throttle_registry=self._throttle_registry,
                        modules=[str(proposal.module)],
                    )
                    snap = snaps.get(str(proposal.module))
                    if snap is not None:
                        snapshot_id = str(snap.snapshot_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("apply snapshot 异常(已隔离): %s", exc)
                    if self._config.require_snapshot:
                        with self._lock:
                            self._stats["apply_failed"] += 1
                        return PolicyApplyResult(
                            outcome=APPLY_OUTCOME_FAILED,
                            proposal_id=str(proposal.proposal_id),
                            error=f"snapshot_failed:{exc}",
                        )

            # 3. 路由到对应 setter
            param = str(proposal.parameter)
            module = str(proposal.module)
            new_value = proposal.suggested_value
            old_value = proposal.old_value
            try:
                if param in ALLOWED_THROTTLE_PARAMETERS:
                    if self._throttle_registry is None:
                        raise RuntimeError("throttle_registry_not_configured")
                    ok = self._apply_throttle(module, param, new_value, old_value)
                elif param in ALLOWED_BUDGET_PARAMETERS:
                    if self._runtime_budget is None:
                        raise RuntimeError("runtime_budget_not_configured")
                    ok = self._apply_budget(module, param, new_value, old_value)
                else:
                    raise RuntimeError(f"unsupported_parameter:{param}")
                if not ok:
                    raise RuntimeError("setter_returned_false")
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._stats["apply_failed"] += 1
                # 失败时尝试回滚
                self._rollback_from_snapshot(
                    proposal_id=str(proposal.proposal_id),
                    module=module,
                    snapshot_id=snapshot_id,
                )
                return PolicyApplyResult(
                    outcome=APPLY_OUTCOME_FAILED,
                    proposal_id=str(proposal.proposal_id),
                    module=module,
                    parameter=param,
                    old_value=old_value,
                    new_value=old_value,  # 失败 → 维持原值
                    error=f"apply_failed:{exc}",
                    snapshot_id=snapshot_id,
                )

            with self._lock:
                self._stats["apply_success"] += 1
            return PolicyApplyResult(
                outcome=APPLY_OUTCOME_SUCCESS,
                proposal_id=str(proposal.proposal_id),
                module=module,
                parameter=param,
                old_value=old_value,
                new_value=new_value,
                snapshot_id=snapshot_id,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["apply_failed"] += 1
            logger.debug("PolicyApplyService.apply 顶层异常: %s", exc)
            return PolicyApplyResult(
                outcome=APPLY_OUTCOME_FAILED,
                error=f"apply_exception:{exc}",
            )

    # --------------------------------------------------------
    # 私有:Throttle setter
    # --------------------------------------------------------
    def _apply_throttle(
        self,
        module: str,
        param: str,
        new_value: Any,
        old_value: Any,
    ) -> bool:
        try:
            set_method = getattr(self._throttle_registry, "set", None)
            if not callable(set_method):
                return False
            if param == PARAM_THROTTLE_INTERVAL:
                set_method(
                    module=module,
                    interval=max(0, int(new_value)),
                )
            elif param == PARAM_THROTTLE_THROTTLE:
                v = max(0.0, min(1.0, float(new_value)))
                set_method(module=module, throttle=v)
            elif param == PARAM_THROTTLE_COOLDOWN:
                v = max(0.0, float(new_value))
                set_method(module=module, cooldown_seconds=v)
            else:
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("_apply_throttle(%s) 异常(已隔离): %s", param, exc)
            return False

    # --------------------------------------------------------
    # 私有:Budget setter
    # --------------------------------------------------------
    def _apply_budget(
        self,
        module: str,
        param: str,
        new_value: Any,
        old_value: Any,
    ) -> bool:
        try:
            budget = self._runtime_budget
            ledger = getattr(budget, "ledger", None) if budget else None
            if param == PARAM_BUDGET_DAILY_COST:
                # 修改 daily_cost_limit
                if ledger is None or not hasattr(ledger, "_cost_limit"):
                    return False
                with getattr(ledger, "_lock", threading.RLock()):
                    ledger._cost_limit = max(0.0, float(new_value))
                return True
            if param == PARAM_BUDGET_DAILY_TOKENS:
                if ledger is None or not hasattr(ledger, "_tokens_limit"):
                    return False
                with getattr(ledger, "_lock", threading.RLock()):
                    ledger._tokens_limit = max(0, int(new_value))
                return True
            if param == PARAM_BUDGET_DAILY_LLM_CALLS:
                if ledger is None or not hasattr(ledger, "_llm_calls_limit"):
                    return False
                with getattr(ledger, "_lock", threading.RLock()):
                    ledger._llm_calls_limit = max(0, int(new_value))
                return True
            if param == PARAM_BUDGET_PER_MODULE_COST:
                if ledger is None or not hasattr(ledger, "_per_module_cost_limit"):
                    return False
                with getattr(ledger, "_lock", threading.RLock()):
                    ledger._per_module_cost_limit = max(
                        0.0, float(new_value)
                    )
                return True
            if param == PARAM_BUDGET_MODULE_COST:
                if budget is None or not hasattr(budget, "set_module_cost"):
                    return False
                budget.set_module_cost(module=module, cost=float(new_value))
                return True
            return False
        except Exception as exc:  # noqa: BLE001
            logger.debug("_apply_budget(%s) 异常(已隔离): %s", param, exc)
            return False

    # --------------------------------------------------------
    # Rollback
    # --------------------------------------------------------
    def rollback(
        self,
        proposal_id: str = "",
        module: str = "",
        snapshot_id: str = "",
    ) -> bool:
        """根据 snapshot_id 恢复 throttle_registry。"""
        with self._lock:
            self._stats["rollback_total"] += 1
        try:
            if self._snapshot_manager is None or self._throttle_registry is None:
                return False
            # 找到 snapshot
            snap = None
            if snapshot_id:
                snap = self._snapshot_manager.get_snapshot(snapshot_id)
            if snap is None and module:
                # 兜底:从 groups 里找最近一个匹配 module 的 snapshot
                groups = self._snapshot_manager.list_groups()
                for gid in reversed(groups):
                    g = self._snapshot_manager.get_group(gid)
                    if module in g:
                        snap = g[module]
                        break
            if snap is None:
                with self._lock:
                    self._stats["rollback_failed"] += 1
                return False
            ok = self._snapshot_manager.restore_snapshot(
                snapshots={snap.module: snap},
                throttle_registry=self._throttle_registry,
            )
            if ok:
                with self._lock:
                    self._stats["rollback_success"] += 1
            else:
                with self._lock:
                    self._stats["rollback_failed"] += 1
            return ok
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["rollback_failed"] += 1
            logger.debug("PolicyApplyService.rollback 异常: %s", exc)
            return False

    def _rollback_from_snapshot(
        self,
        proposal_id: str = "",
        module: str = "",
        snapshot_id: str = "",
    ) -> None:
        """内部 rollback,不抛错。"""
        try:
            self.rollback(
                proposal_id=proposal_id,
                module=module,
                snapshot_id=snapshot_id,
            )
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# 工厂
# ============================================================
def build_default_apply_config(
    auto_apply_enabled: bool = False,
    safe_auto_mode: bool = True,
    max_throttle_delta: float = DEFAULT_MAX_THROTTLE_DELTA,
    actor: str = "runtime",
) -> PolicyApplyConfig:
    return PolicyApplyConfig(
        auto_apply_enabled=auto_apply_enabled,
        safe_auto_mode=safe_auto_mode,
        max_throttle_delta=max_throttle_delta,
        actor=actor,
    )


def build_default_apply_service(
    throttle_registry: Any = None,
    runtime_budget: Any = None,
    snapshot_manager: Any = None,
    config: Optional[PolicyApplyConfig] = None,
) -> PolicyApplyService:
    return PolicyApplyService(
        throttle_registry=throttle_registry,
        runtime_budget=runtime_budget,
        snapshot_manager=snapshot_manager,
        config=config,
    )
