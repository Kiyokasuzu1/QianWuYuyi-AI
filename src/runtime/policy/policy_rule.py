# -*- coding: utf-8 -*-
"""
src/runtime/policy/policy_rule.py

Phase C.10.7 — PolicyRule 协议与内置规则

本文件定义:
- PolicyRule 协议(duck-typing 友好)
- BasePolicyRule 抽象基类(可选继承)
- SafeModeRule(安全模式规则)
- MaintenanceRule(维护模式规则)
- DisabledModuleRule(基于 module_enabled 的规则)
- AlwaysAllowRule(默认通过规则,兜底)

设计原则:
- 每条规则独立、只读,绝不修改 PolicyContext
- 每条规则都有 name + priority + evaluate() 接口
- 规则异常必须被隔离,返回 None 表示"我不关心,交给下一条规则"
- Runtime 不直接 import 这些规则,只通过 PolicyEngine 间接调用

注意:
- 不依赖业务模块
- 不依赖 Desktop
- 不依赖 ControlState(规则只读 PolicyContext,ControlState 已嵌入 context)
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Protocol, runtime_checkable

from .decision import (
    DECISION_MODE_MAINTENANCE,
    DECISION_MODE_NORMAL,
    DECISION_MODE_SAFE,
    DECISION_MODE_UNKNOWN,
    RuntimeDecision,
)
from .policy_context import PolicyContext

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
# Safe Mode 下被禁止的模块
SAFE_MODE_BLOCKED_MODULES = frozenset({"growth", "initiative", "dream"})

# Safe Mode 下被强制只读的模块
SAFE_MODE_READONLY_MODULES = frozenset({"memory", "emotion"})

# Maintenance Mode 下被禁止的模块(仅允许 runtime + 诊断类)
MAINTENANCE_BLOCKED_MODULES = frozenset(
    {
        "memory",
        "emotion",
        "growth",
        "initiative",
        "dream",
        "live2d",
        "perception",
        "vision",
    }
)

# Maintenance Mode 下允许的模块
MAINTENANCE_ALLOWED_MODULES = frozenset(
    {
        "runtime",
        "health",
        "diagnostic",
        "readonly",
    }
)


# ============================================================
# PolicyRule Protocol
# ============================================================
@runtime_checkable
class PolicyRule(Protocol):
    """所有 Policy Rule 必须实现的协议。

    任何对象只要实现了以下方法,就可以被 PolicyEngine 当作规则使用:
    - name:           str       规则名(可读,用于 audit)
    - priority:       int       优先级(数字越大越先执行)
    - evaluate(ctx) -> Optional[RuntimeDecision]
        返回 None    : 规则不命中,交给下一条规则
        返回 Decision: 命中,作为最终决策
    """

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int: ...

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]: ...


# ============================================================
# BasePolicyRule(可选基类)
# ============================================================
class BasePolicyRule:
    """PolicyRule 抽象基类。

    继承后只需实现 evaluate(),name / priority 有默认实现。
    """

    @property
    def name(self) -> str:
        try:
            return str(getattr(self, "_rule_name", "") or type(self).__name__)
        except Exception:  # noqa: BLE001
            return "BasePolicyRule"

    @property
    def priority(self) -> int:
        try:
            return int(getattr(self, "_rule_priority", 0))
        except Exception:  # noqa: BLE001
            return 0

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:  # noqa: D401
        """子类必须实现。返回 None 表示不命中。"""
        return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r} priority={self.priority}>"


# ============================================================
# AlwaysAllowRule
# ============================================================
class AlwaysAllowRule(BasePolicyRule):
    """默认通过规则,作为兜底。

    - priority 最低(0)
    - 任何上下文都返回 allow()
    - 用于保证 PolicyEngine.evaluate 至少有一个非 None 输出
    """

    def __init__(self) -> None:
        self._rule_name = "AlwaysAllowRule"
        self._rule_priority = 0

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            return RuntimeDecision.allow(
                module=str(ctx.module or ""),
                reason="always_allow",
                mode=str(ctx.runtime_mode or DECISION_MODE_NORMAL),
                metadata={"rule": self.name},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("AlwaysAllowRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# SafeModeRule
# ============================================================
class SafeModeRule(BasePolicyRule):
    """安全模式规则。

    行为:
    - 仅在 is_safe_mode=True 时命中
    - 模块在 SAFE_MODE_BLOCKED_MODULES 中 → deny
    - 模块在 SAFE_MODE_READONLY_MODULES 中 → readonly
    - 其他模块 → allow(基础聊天等)
    """

    def __init__(
        self,
        blocked_modules: Optional[List[str]] = None,
        readonly_modules: Optional[List[str]] = None,
    ) -> None:
        self._rule_name = "SafeModeRule"
        self._rule_priority = 1000
        self._blocked = (
            frozenset(blocked_modules)
            if blocked_modules
            else SAFE_MODE_BLOCKED_MODULES
        )
        self._readonly = (
            frozenset(readonly_modules)
            if readonly_modules
            else SAFE_MODE_READONLY_MODULES
        )

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            if not bool(getattr(ctx, "is_safe_mode", False)):
                return None
            module = str(getattr(ctx, "module", "") or "").strip().lower()
            if not module:
                return None
            meta = {"rule": self.name, "mode": "safe"}
            if module in self._blocked:
                return RuntimeDecision.deny(
                    module=module,
                    reason="safe_mode_blocks_module",
                    mode=DECISION_MODE_SAFE,
                    metadata=meta,
                )
            if module in self._readonly:
                return RuntimeDecision.readonly_decision(
                    module=module,
                    reason="safe_mode_readonly_module",
                    mode=DECISION_MODE_SAFE,
                    metadata=meta,
                )
            return RuntimeDecision.allow(
                module=module,
                reason="safe_mode_basic_module",
                mode=DECISION_MODE_SAFE,
                metadata=meta,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("SafeModeRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# MaintenanceRule
# ============================================================
class MaintenanceRule(BasePolicyRule):
    """维护模式规则。

    行为:
    - 仅在 is_maintenance_mode=True 时命中
    - 模块在 MAINTENANCE_ALLOWED_MODULES 中 → allow
    - 其他模块 → deny
    """

    def __init__(
        self,
        allowed_modules: Optional[List[str]] = None,
        blocked_modules: Optional[List[str]] = None,
    ) -> None:
        self._rule_name = "MaintenanceRule"
        self._rule_priority = 2000  # 优先级高于 SafeModeRule(双开时 Maintenance 让步)
        self._allowed = (
            frozenset(allowed_modules)
            if allowed_modules
            else MAINTENANCE_ALLOWED_MODULES
        )
        self._blocked = (
            frozenset(blocked_modules)
            if blocked_modules
            else MAINTENANCE_BLOCKED_MODULES
        )

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            if not bool(getattr(ctx, "is_maintenance_mode", False)):
                return None
            module = str(getattr(ctx, "module", "") or "").strip().lower()
            if not module:
                return None
            meta = {"rule": self.name, "mode": "maintenance"}
            if module in self._allowed or module == "runtime":
                return RuntimeDecision.allow(
                    module=module,
                    reason="maintenance_mode_allowed_module",
                    mode=DECISION_MODE_MAINTENANCE,
                    metadata=meta,
                )
            return RuntimeDecision.deny(
                module=module,
                reason="maintenance_mode_blocks_module",
                mode=DECISION_MODE_MAINTENANCE,
                metadata=meta,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MaintenanceRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# DisabledModuleRule
# ============================================================
class DisabledModuleRule(BasePolicyRule):
    """基于 module_enabled 的规则。

    行为:
    - 读取 ctx.module_enabled
    - module_enabled=False → deny(reason=module_disabled)
    - module_enabled=True/None → 不命中(交给下一条规则)
    """

    def __init__(self) -> None:
        self._rule_name = "DisabledModuleRule"
        self._rule_priority = 500

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            if getattr(ctx, "module_enabled", None) is None:
                return None
            if bool(ctx.module_enabled):
                return None
            module = str(getattr(ctx, "module", "") or "")
            return RuntimeDecision.deny(
                module=module,
                reason="module_disabled_in_control_state",
                mode=str(getattr(ctx, "runtime_mode", DECISION_MODE_NORMAL) or DECISION_MODE_NORMAL),
                metadata={"rule": self.name, "field": f"{module}_enabled"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DisabledModuleRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# 工厂
# ============================================================
def build_default_rules() -> List[PolicyRule]:
    """构造一组默认规则,按优先级从高到低返回。"""
    return [
        MaintenanceRule(),
        SafeModeRule(),
        DisabledModuleRule(),
        AlwaysAllowRule(),
    ]


def safe_call_rule(
    rule: Any,
    ctx: PolicyContext,
) -> Optional[RuntimeDecision]:
    """以 fail-soft 方式执行一条规则,异常时返回 None。"""
    try:
        if rule is None:
            return None
        evaluate = getattr(rule, "evaluate", None)
        if evaluate is None or not callable(evaluate):
            return None
        return evaluate(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "safe_call_rule(%s) 异常(已隔离): %s",
            getattr(rule, "name", type(rule).__name__),
            exc,
        )
        return None
