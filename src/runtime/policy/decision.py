# -*- coding: utf-8 -*-
"""
src/runtime/policy/decision.py

Phase C.10.7 — Runtime Policy Decision

RuntimeDecision 是 PolicyEngine 评估后的统一输出数据结构。

字段:
- module:  被评估的模块名
- allowed: 是否允许执行
- reason:  决策原因(短文本,可空)
- mode:    当前运行模式(normal / safe / maintenance / unknown)
- readonly:是否只读(允许基础查询但禁止副作用)
- throttle:节流倍率(1.0 = 不限流,< 1.0 表示减速,0 = 完全停止副作用)
- metadata:附加元数据(规则名 / 命中条件 / 审计字段等)

设计目标:
- 完全 dataclass 化,不可变
- 提供 to_dict() 便于写入 audit / event / debug
- 提供 allow() / deny() / readonly() 工厂方法,避免散落字面量
- 与 C.10.5/C.10.6 的 ControlState / ControlContext 共享命名约定

Phase C.10.7 强约束:
- 此文件不依赖任何业务模块
- 此文件不依赖 Desktop
- 异常隔离:任何 to_dict() 失败必须返回空 dict 而不是抛错
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 模式常量
# ============================================================
# 与 src/control/runtime/control_provider.py 中的 RuntimeControlMode 保持一致
# 这里重复定义避免 policy 包反向依赖 control 包
DECISION_MODE_NORMAL = "normal"
DECISION_MODE_SAFE = "safe"
DECISION_MODE_MAINTENANCE = "maintenance"
DECISION_MODE_UNKNOWN = "unknown"

VALID_MODES = frozenset(
    {DECISION_MODE_NORMAL, DECISION_MODE_SAFE, DECISION_MODE_MAINTENANCE, DECISION_MODE_UNKNOWN}
)


# ============================================================
# RuntimeDecision
# ============================================================
@dataclass
class RuntimeDecision:
    """Runtime Policy Engine 输出的统一决策结构。

    由 PolicyEngine.evaluate(module, context) 返回,
    Runtime 根据 decision.allowed / decision.readonly / decision.throttle
    决定如何执行模块。

    Phase C.10.8 扩展字段:
    - cooldown:            冷却剩余时间(秒);> 0 时禁止模块执行
    - execution_interval:  模块两次执行的最小间隔(cycle 数)
    - budget_cost:         本次执行预计消耗的预算(用于 RuntimeBudget 校验)
    """

    module: str = ""
    allowed: bool = True
    reason: str = ""
    mode: str = DECISION_MODE_NORMAL
    readonly: bool = False
    throttle: float = 1.0
    cooldown: float = 0.0
    execution_interval: int = 0
    budget_cost: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    # --------------------------------------------------------
    # 工厂方法
    # --------------------------------------------------------
    @classmethod
    def allow(
        cls,
        module: str = "",
        reason: str = "",
        mode: str = DECISION_MODE_NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        throttle: float = 1.0,
        cooldown: float = 0.0,
        execution_interval: int = 0,
        budget_cost: float = 0.0,
    ) -> "RuntimeDecision":
        """构造一个"允许执行"决策。"""
        return cls(
            module=str(module or ""),
            allowed=True,
            reason=str(reason or ""),
            mode=_normalize_mode(mode),
            readonly=False,
            throttle=_safe_float(throttle, 1.0),
            cooldown=max(0.0, _safe_float(cooldown, 0.0)),
            execution_interval=max(0, _safe_int(execution_interval, 0)),
            budget_cost=max(0.0, _safe_float(budget_cost, 0.0)),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def deny(
        cls,
        module: str = "",
        reason: str = "",
        mode: str = DECISION_MODE_NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        throttle: float = 0.0,
        cooldown: float = 0.0,
        execution_interval: int = 0,
        budget_cost: float = 0.0,
    ) -> "RuntimeDecision":
        """构造一个"禁止执行"决策。"""
        return cls(
            module=str(module or ""),
            allowed=False,
            reason=str(reason or ""),
            mode=_normalize_mode(mode),
            readonly=False,
            throttle=_safe_float(throttle, 0.0),
            cooldown=max(0.0, _safe_float(cooldown, 0.0)),
            execution_interval=max(0, _safe_int(execution_interval, 0)),
            budget_cost=max(0.0, _safe_float(budget_cost, 0.0)),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def readonly_decision(
        cls,
        module: str = "",
        reason: str = "",
        mode: str = DECISION_MODE_NORMAL,
        metadata: Optional[Dict[str, Any]] = None,
        throttle: float = 0.5,
        cooldown: float = 0.0,
        execution_interval: int = 0,
        budget_cost: float = 0.0,
    ) -> "RuntimeDecision":
        """构造一个"只读"决策(允许查询/读取,禁止副作用)。"""
        return cls(
            module=str(module or ""),
            allowed=True,
            reason=str(reason or ""),
            mode=_normalize_mode(mode),
            readonly=True,
            throttle=_safe_float(throttle, 0.5),  # 只读模式默认半速
            cooldown=max(0.0, _safe_float(cooldown, 0.0)),
            execution_interval=max(0, _safe_int(execution_interval, 0)),
            budget_cost=max(0.0, _safe_float(budget_cost, 0.0)),
            metadata=dict(metadata or {}),
        )

    # --------------------------------------------------------
    # 序列化 / 派生属性
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict(写入 audit / event / debug)。"""
        try:
            return {
                "module": str(self.module or ""),
                "allowed": bool(self.allowed),
                "reason": str(self.reason or ""),
                "mode": str(self.mode or DECISION_MODE_NORMAL),
                "readonly": bool(self.readonly),
                "throttle": float(self.throttle) if self.throttle is not None else 1.0,
                "cooldown": float(self.cooldown) if self.cooldown is not None else 0.0,
                "execution_interval": int(self.execution_interval) if self.execution_interval is not None else 0,
                "budget_cost": float(self.budget_cost) if self.budget_cost is not None else 0.0,
                "metadata": dict(self.metadata or {}),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("RuntimeDecision.to_dict 异常(已隔离): %s", exc)
            return {
                "module": "",
                "allowed": True,
                "reason": "",
                "mode": DECISION_MODE_UNKNOWN,
                "readonly": False,
                "throttle": 1.0,
                "cooldown": 0.0,
                "execution_interval": 0,
                "budget_cost": 0.0,
                "metadata": {},
            }

    @property
    def is_denied(self) -> bool:
        """是否被禁止。"""
        return not bool(self.allowed)

    @property
    def is_readonly(self) -> bool:
        """是否只读。"""
        return bool(self.readonly) and bool(self.allowed)

    @property
    def is_normal_mode(self) -> bool:
        return str(self.mode or "") == DECISION_MODE_NORMAL

    @property
    def is_safe_mode(self) -> bool:
        return str(self.mode or "") == DECISION_MODE_SAFE

    @property
    def is_maintenance_mode(self) -> bool:
        return str(self.mode or "") == DECISION_MODE_MAINTENANCE

    def with_throttle(self, throttle: float) -> "RuntimeDecision":
        """返回一个新 decision,throttle 被覆盖(用于链式微调)。"""
        try:
            t = float(throttle)
        except Exception:  # noqa: BLE001
            t = 1.0
        return RuntimeDecision(
            module=str(self.module or ""),
            allowed=bool(self.allowed),
            reason=str(self.reason or ""),
            mode=str(self.mode or DECISION_MODE_NORMAL),
            readonly=bool(self.readonly),
            throttle=t,
            cooldown=float(self.cooldown) if self.cooldown is not None else 0.0,
            execution_interval=int(self.execution_interval) if self.execution_interval is not None else 0,
            budget_cost=float(self.budget_cost) if self.budget_cost is not None else 0.0,
            metadata=dict(self.metadata or {}),
        )

    def with_metadata(self, key: str, value: Any) -> "RuntimeDecision":
        """返回一个新 decision,在 metadata 中写入一个键值对(用于补充审计信息)。"""
        meta = dict(self.metadata or {})
        try:
            meta[str(key)] = value
        except Exception:  # noqa: BLE001
            pass
        return RuntimeDecision(
            module=str(self.module or ""),
            allowed=bool(self.allowed),
            reason=str(self.reason or ""),
            mode=str(self.mode or DECISION_MODE_NORMAL),
            readonly=bool(self.readonly),
            throttle=float(self.throttle) if self.throttle is not None else 1.0,
            cooldown=float(self.cooldown) if self.cooldown is not None else 0.0,
            execution_interval=int(self.execution_interval) if self.execution_interval is not None else 0,
            budget_cost=float(self.budget_cost) if self.budget_cost is not None else 0.0,
            metadata=meta,
        )

    def with_cooldown(self, cooldown: float) -> "RuntimeDecision":
        """返回一个新 decision,cooldown 被覆盖。"""
        try:
            c = max(0.0, float(cooldown))
        except Exception:  # noqa: BLE001
            c = 0.0
        return RuntimeDecision(
            module=str(self.module or ""),
            allowed=bool(self.allowed),
            reason=str(self.reason or ""),
            mode=str(self.mode or DECISION_MODE_NORMAL),
            readonly=bool(self.readonly),
            throttle=float(self.throttle) if self.throttle is not None else 1.0,
            cooldown=c,
            execution_interval=int(self.execution_interval) if self.execution_interval is not None else 0,
            budget_cost=float(self.budget_cost) if self.budget_cost is not None else 0.0,
            metadata=dict(self.metadata or {}),
        )

    def with_execution_interval(self, interval: int) -> "RuntimeDecision":
        """返回一个新 decision,execution_interval 被覆盖。"""
        try:
            i = max(0, int(interval))
        except Exception:  # noqa: BLE001
            i = 0
        return RuntimeDecision(
            module=str(self.module or ""),
            allowed=bool(self.allowed),
            reason=str(self.reason or ""),
            mode=str(self.mode or DECISION_MODE_NORMAL),
            readonly=bool(self.readonly),
            throttle=float(self.throttle) if self.throttle is not None else 1.0,
            cooldown=float(self.cooldown) if self.cooldown is not None else 0.0,
            execution_interval=i,
            budget_cost=float(self.budget_cost) if self.budget_cost is not None else 0.0,
            metadata=dict(self.metadata or {}),
        )

    def with_budget_cost(self, cost: float) -> "RuntimeDecision":
        """返回一个新 decision,budget_cost 被覆盖。"""
        try:
            bc = max(0.0, float(cost))
        except Exception:  # noqa: BLE001
            bc = 0.0
        return RuntimeDecision(
            module=str(self.module or ""),
            allowed=bool(self.allowed),
            reason=str(self.reason or ""),
            mode=str(self.mode or DECISION_MODE_NORMAL),
            readonly=bool(self.readonly),
            throttle=float(self.throttle) if self.throttle is not None else 1.0,
            cooldown=float(self.cooldown) if self.cooldown is not None else 0.0,
            execution_interval=int(self.execution_interval) if self.execution_interval is not None else 0,
            budget_cost=bc,
            metadata=dict(self.metadata or {}),
        )

    @property
    def is_in_cooldown(self) -> bool:
        """是否仍处于冷却中(> 0)。"""
        try:
            return float(self.cooldown or 0.0) > 0.0
        except Exception:  # noqa: BLE001
            return False

    @property
    def is_throttled(self) -> bool:
        """是否被节流(0 < throttle < 1.0)。"""
        try:
            t = float(self.throttle) if self.throttle is not None else 1.0
            return 0.0 < t < 1.0
        except Exception:  # noqa: BLE001
            return False

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RuntimeDecision(module={self.module!r}, allowed={self.allowed}, "
            f"mode={self.mode!r}, readonly={self.readonly}, throttle={self.throttle}, "
            f"cooldown={self.cooldown}, interval={self.execution_interval}, cost={self.budget_cost})"
        )


# ============================================================
# 工具函数
# ============================================================
def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:  # noqa: BLE001
        return default


def _normalize_mode(mode: Any) -> str:
    """规范化 mode 字符串,无效值回退到 unknown。"""
    if mode is None:
        return DECISION_MODE_UNKNOWN
    s = str(mode).strip().lower()
    if s in VALID_MODES:
        return s
    return DECISION_MODE_UNKNOWN


def default_allow_decision(module: str = "") -> RuntimeDecision:
    """构造一个 fail-soft 默认允许决策(PolicyEngine 异常时返回)。"""
    return RuntimeDecision.allow(
        module=module,
        reason="default_allow_fallback",
        mode=DECISION_MODE_NORMAL,
        metadata={"fallback": True},
    )


def default_throttle_decision(
    module: str = "",
    throttle: float = 1.0,
    cooldown: float = 0.0,
    execution_interval: int = 0,
    reason: str = "throttle_fallback",
) -> RuntimeDecision:
    """构造一个 fail-soft 节流决策(throttle/budget 异常时返回)。"""
    return RuntimeDecision.allow(
        module=module,
        reason=reason,
        mode=DECISION_MODE_NORMAL,
        throttle=throttle,
        cooldown=cooldown,
        execution_interval=execution_interval,
        metadata={"fallback": True, "throttle_layer": True},
    )
