# -*- coding: utf-8 -*-
"""
src/runtime/policy/policy_engine.py

Phase C.10.7 — Runtime Policy Engine 主入口

PolicyEngine 是 Runtime 调用策略的唯一入口。

Runtime 调用方式:
    decision = policy_engine.evaluate(module, context)
    if decision.allowed:
        # 执行模块(根据 decision.readonly / decision.throttle 调整)
    else:
        # 跳过

Engine 职责:
- 维护一组有序的 PolicyRule
- 接收 module + context,按 priority 顺序评估规则
- 第一条返回非 None 的规则胜出,作为最终 decision
- 所有规则都不命中 → 兜底 AlwaysAllowRule
- 任何内部异常 → fail-soft 返回 default_allow_decision

设计原则:
- 不直接修改 Runtime 行为(只决策)
- 不依赖业务模块
- 不依赖 Desktop
- 可注入自定义规则集
- 可注入事件回调(audit / event bus / log)
- 线程安全(RLock)

注意:
- 评估是只读的:PolicyContext 不会被修改
- 评估结果通过回调/事件发布,不在 engine 内做副作用
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from .decision import (
    DECISION_MODE_NORMAL,
    DECISION_MODE_UNKNOWN,
    RuntimeDecision,
    default_allow_decision,
)
from .policy_context import PolicyContext
from .policy_rule import (
    AlwaysAllowRule,
    PolicyRule,
    build_default_rules,
    safe_call_rule,
)

logger = logging.getLogger(__name__)


# ============================================================
# 事件回调类型
# ============================================================
DecisionCallback = Callable[[RuntimeDecision, PolicyContext], None]


# ============================================================
# PolicyEngine
# ============================================================
class PolicyEngine:
    """Runtime 策略引擎(主入口)。

    用法:
        engine = PolicyEngine()
        # 自定义规则
        engine.add_rule(MyRule(), priority=3000)
        # 评估
        decision = engine.evaluate("memory", context)
        if decision.allowed and not decision.readonly:
            memory.execute()
    """

    DEFAULT_PRIORITY_ALLOW = 0
    DEFAULT_PRIORITY_DISABLED = 500
    DEFAULT_PRIORITY_SAFE = 1000
    DEFAULT_PRIORITY_MAINTENANCE = 2000

    def __init__(
        self,
        rules: Optional[List[PolicyRule]] = None,
        use_default_rules: bool = True,
    ) -> None:
        """构造 PolicyEngine。

        - rules:        自定义规则列表(可空)
        - use_default_rules: True 时自动追加默认规则(Maintenance/Safe/Disabled/AlwaysAllow)
        """
        self._lock = threading.RLock()
        # 用 list 维护有序规则(name -> rule)
        self._rules: List[PolicyRule] = []
        # 回调列表(评估完成后调用)
        self._callbacks: List[DecisionCallback] = []
        # 评估计数(用于测试 / 监控)
        self._stats: Dict[str, int] = {
            "evaluate_total": 0,
            "evaluate_allow": 0,
            "evaluate_deny": 0,
            "evaluate_readonly": 0,
            "evaluate_errors": 0,
            "fallback_allow": 0,
        }
        # 注入规则
        if rules:
            for r in rules:
                self._add_rule_internal(r)
        if use_default_rules:
            for r in build_default_rules():
                self._add_rule_internal(r)
        # 排序
        self._sort_rules()

    # --------------------------------------------------------
    # 规则管理
    # --------------------------------------------------------
    def add_rule(self, rule: PolicyRule, priority: Optional[int] = None) -> None:
        """注册一条规则(可指定 priority,覆盖 rule 自身 priority)。"""
        with self._lock:
            self._add_rule_internal(rule, priority=priority)
            self._sort_rules()

    def _add_rule_internal(
        self,
        rule: Any,
        priority: Optional[int] = None,
    ) -> None:
        try:
            if rule is None:
                return
            # 若 priority 显式传入,临时挂到对象上
            if priority is not None:
                try:
                    setattr(rule, "_rule_priority", int(priority))
                except Exception:  # noqa: BLE001
                    pass
            # 去重(同 name + 同 type 视为同一规则)
            for existing in self._rules:
                if (
                    getattr(existing, "name", None) == getattr(rule, "name", None)
                    and type(existing) is type(rule)
                ):
                    return
            self._rules.append(rule)
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyEngine._add_rule_internal 异常(已隔离): %s", exc)

    def remove_rule(self, rule_name: str) -> int:
        """按 name 移除规则,返回移除数量。"""
        removed = 0
        with self._lock:
            kept: List[PolicyRule] = []
            for r in self._rules:
                if getattr(r, "name", "") == rule_name:
                    removed += 1
                    continue
                kept.append(r)
            self._rules = kept
        return removed

    def clear_rules(self) -> None:
        with self._lock:
            self._rules = []

    def get_rules(self) -> List[PolicyRule]:
        """返回当前规则列表(副本,priority 降序)。"""
        with self._lock:
            return list(self._rules)

    def _sort_rules(self) -> None:
        try:
            self._rules.sort(
                key=lambda r: -int(getattr(r, "priority", 0))
            )
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 回调管理
    # --------------------------------------------------------
    def add_callback(self, callback: DecisionCallback) -> None:
        """注册一个评估完成后的回调(callback 异常被隔离)。"""
        with self._lock:
            if callback is not None and callback not in self._callbacks:
                self._callbacks.append(callback)

    def remove_callback(self, callback: DecisionCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _invoke_callbacks(
        self,
        decision: RuntimeDecision,
        ctx: PolicyContext,
    ) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(decision, ctx)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "PolicyEngine callback 异常(已隔离): %s", exc,
                )

    # --------------------------------------------------------
    # 核心:evaluate
    # --------------------------------------------------------
    def evaluate(
        self,
        module: str,
        context: Optional[PolicyContext] = None,
    ) -> RuntimeDecision:
        """评估一个模块的执行策略。

        步骤:
        1. 若 context 为 None,构造一个 minimal PolicyContext
        2. 始终为本次评估构造一个"模块特定"的 context 副本
           (避免 evaluate_all() 中复用同一 context 导致决策污染)
        3. 按 priority 顺序执行规则
        4. 第一条返回非 None 的规则胜出
        5. 所有规则都不命中 → 兜底 allow decision
        6. 任何异常 → fail-soft 返回 default_allow_decision
        7. 更新 stats
        8. 调用 callbacks
        """
        try:
            with self._lock:
                self._stats["evaluate_total"] += 1
                rules = list(self._rules)
            # 构造模块特定的 context 副本(避免 evaluate_all() 中的污染)
            ctx = _build_per_module_context(context, module)
            decision: Optional[RuntimeDecision] = None
            chosen_rule: Optional[PolicyRule] = None
            for rule in rules:
                d = safe_call_rule(rule, ctx)
                if d is not None:
                    decision = d
                    chosen_rule = rule
                    break
            if decision is None:
                # 兜底:用 AlwaysAllowRule 再评估一次
                decision = default_allow_decision(module=str(module or ""))
                decision = decision.with_metadata(
                    "fallback", "no_rule_matched"
                )
                with self._lock:
                    self._stats["fallback_allow"] += 1
            else:
                # 在 decision.metadata 中标注命中的规则名
                try:
                    rule_name = getattr(chosen_rule, "name", type(chosen_rule).__name__) if chosen_rule else "unknown"
                    decision = decision.with_metadata("matched_rule", rule_name)
                except Exception:  # noqa: BLE001
                    pass
            # 更新 stats
            with self._lock:
                try:
                    if decision.allowed and not decision.readonly:
                        self._stats["evaluate_allow"] += 1
                    elif decision.allowed and decision.readonly:
                        self._stats["evaluate_readonly"] += 1
                    else:
                        self._stats["evaluate_deny"] += 1
                except Exception:  # noqa: BLE001
                    pass
            # 触发回调
            self._invoke_callbacks(decision, ctx)
            return decision
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["evaluate_errors"] += 1
            logger.debug("PolicyEngine.evaluate 异常(已隔离): %s", exc)
            return default_allow_decision(module=str(module or ""))

    # --------------------------------------------------------
    # 批量评估
    # --------------------------------------------------------
    def evaluate_all(
        self,
        modules: List[str],
        context: Optional[PolicyContext] = None,
    ) -> Dict[str, RuntimeDecision]:
        """批量评估多个模块,返回 {module: decision}。"""
        result: Dict[str, RuntimeDecision] = {}
        for m in (modules or []):
            try:
                result[str(m)] = self.evaluate(str(m), context=context)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "PolicyEngine.evaluate_all(%s) 异常(已隔离): %s", m, exc,
                )
                result[str(m)] = default_allow_decision(module=str(m))
        return result

    def which_allowed(
        self,
        modules: List[str],
        context: Optional[PolicyContext] = None,
    ) -> List[str]:
        """返回所有 allowed 的模块名。"""
        return [
            m for m, d in self.evaluate_all(modules, context=context).items()
            if d.allowed
        ]

    def which_denied(
        self,
        modules: List[str],
        context: Optional[PolicyContext] = None,
    ) -> List[str]:
        """返回所有 denied 的模块名。"""
        return [
            m for m, d in self.evaluate_all(modules, context=context).items()
            if not d.allowed
        ]

    # --------------------------------------------------------
    # 健康检查 / 状态
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        """返回引擎健康状态。"""
        with self._lock:
            return {
                "rules_count": len(self._rules),
                "callbacks_count": len(self._callbacks),
                "stats": dict(self._stats),
                "rule_names": [
                    getattr(r, "name", type(r).__name__)
                    for r in self._rules
                ],
            }

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._lock:
            self._stats = {
                "evaluate_total": 0,
                "evaluate_allow": 0,
                "evaluate_deny": 0,
                "evaluate_readonly": 0,
                "evaluate_errors": 0,
                "fallback_allow": 0,
            }

    # --------------------------------------------------------
    # 调试
    # --------------------------------------------------------
    def __repr__(self) -> str:  # pragma: no cover
        try:
            with self._lock:
                return (
                    f"<PolicyEngine rules={len(self._rules)} "
                    f"total={self._stats['evaluate_total']}>"
                )
        except Exception:  # noqa: BLE001
            return "<PolicyEngine>"

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "rule_names": [
                    getattr(r, "name", type(r).__name__)
                    for r in self._rules
                ],
                "stats": self.get_stats(),
            }
        except Exception:  # noqa: BLE001
            return {}


# ============================================================
# 模块级单例(可选)
# ============================================================
_default_engine: Optional[PolicyEngine] = None
_default_lock = threading.Lock()


def get_default_policy_engine() -> PolicyEngine:
    """获取默认 PolicyEngine 单例。

    - 第一次调用时构造(使用默认规则集)
    - 后续调用复用同一实例
    - 可通过 reset_default_policy_engine_for_testing() 重置
    """
    global _default_engine
    if _default_engine is None:
        with _default_lock:
            if _default_engine is None:
                _default_engine = PolicyEngine(use_default_rules=True)
    return _default_engine


def reset_default_policy_engine_for_testing(
    engine: Optional[PolicyEngine] = None,
) -> PolicyEngine:
    """测试用:重置默认 PolicyEngine。

    - engine=None:重置为 None,下次 get 时按默认构造
    - engine=<>: 替换为指定实例
    """
    global _default_engine
    with _default_lock:
        if engine is None:
            _default_engine = PolicyEngine(use_default_rules=True)
        else:
            _default_engine = engine
    return _default_engine


# ============================================================
# 内部工具
# ============================================================
def _build_per_module_context(
    base: Optional[PolicyContext],
    module: str,
) -> PolicyContext:
    """为本次评估构造一个"模块特定"的 PolicyContext 副本。

    - 避免 evaluate_all() 复用同一 context 时,后续 module 字段被覆盖
      污染上一次评估结果
    - 保留基础 context 的所有只读字段(control_state / mode / system_info)
    - 始终使用入参 module 作为 ctx.module
    """
    try:
        if base is None:
            return PolicyContext(module=str(module or ""))
        # 浅拷贝 dataclass 实例
        from dataclasses import replace
        new_ctx = replace(
            base,
            module=str(module or ""),
        )
        return new_ctx
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "_build_per_module_context 异常(已隔离,回退 default): %s", exc,
        )
        return PolicyContext(module=str(module or ""))
