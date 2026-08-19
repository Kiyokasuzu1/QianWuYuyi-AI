# -*- coding: utf-8 -*-
"""
src/runtime/policy/throttle.py

Phase C.10.8 — Runtime Throttle & Interval Control

本文件实现模块级节流与执行间隔控制:

- ThrottleState              单模块节流状态(冷却/间隔/上次 cycle/上次时间)
- ThrottleRegistry           集中管理所有模块的 ThrottleState
- ThrottleRule               内置 PolicyRule:基于 ThrottleRegistry 给出节流决策
- build_throttle_registry    默认 throttle 规则(基于 module 频率表)

设计原则:
- ThrottleState 持有"上次执行 cycle/时间",纯本地状态,不依赖 Runtime
- ThrottleRule 是 PolicyRule 的实现,优先级较低(200) 位于 Maintenance/Safe 之后
- 异常隔离:任何读/写异常时返回 None,不命中,交给下一条规则
- Fail-soft:throttle=1.0 永远被当作"无节流"

使用示例:

    from src.runtime.policy.throttle import (
        ThrottleRule, ThrottleRegistry, build_throttle_registry,
    )

    registry = build_throttle_registry()
    rule = ThrottleRule(registry)
    engine.add_rule(rule, priority=200)

    # 当模块执行后,Runtime 调用:
    registry.tick("growth", cycle_id="c-123")
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
    default_throttle_decision,
)
from .policy_context import PolicyContext
from .policy_rule import BasePolicyRule

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================
# 默认模块执行间隔(cycle 数);> 0 时模块不能连续 cycle 执行
DEFAULT_MODULE_INTERVALS: Dict[str, int] = {
    "growth": 50,
    "initiative": 100,
    "dream": 200,
    "memory_consolidation": 30,
    "self_reflection": 40,
}

# 默认模块执行节流倍率(0~1);1.0 = 不限流
DEFAULT_MODULE_THROTTLES: Dict[str, float] = {
    "growth": 1.0,
    "initiative": 0.5,
    "dream": 0.5,
    "perception": 0.8,
}

# 默认冷却时间(秒):0 = 无冷却
DEFAULT_MODULE_COOLDOWNS: Dict[str, float] = {
    "growth": 0.0,
    "initiative": 0.0,
    "dream": 0.0,
}

# ThrottleRule 默认 priority(在 Maintenance/Safe/Disabled 之后)
DEFAULT_THROTTLE_PRIORITY = 200


# ============================================================
# ThrottleState
# ============================================================
@dataclass
class ThrottleState:
    """单模块的节流状态。

    字段:
    - module:              模块名
    - interval:            两次执行的最小间隔(cycle 数);0 = 无间隔
    - throttle:            节流倍率(1.0 = 不限流,< 1.0 表示降频)
    - cooldown_seconds:    冷却时间(秒),> 0 时禁止执行
    - last_executed_cycle: 上次执行的 cycle_id
    - last_executed_at:    上次执行时间戳(秒)
    - executions:          累计执行次数(用于统计)
    - skipped_cooldown:    累计因冷却跳过的次数
    - skipped_interval:    累计因 interval 跳过的次数
    """

    module: str = ""
    interval: int = 0
    throttle: float = 1.0
    cooldown_seconds: float = 0.0
    last_executed_cycle: str = ""
    last_executed_at: float = 0.0
    executions: int = 0
    skipped_cooldown: int = 0
    skipped_interval: int = 0

    def can_execute(
        self,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> tuple:
        """判断当前 cycle 是否允许执行。

        返回: (can: bool, reason: str, cooldown_remaining: float)
        - can=True, reason="" → 可执行
        - can=False, reason="in_cooldown" → 冷却中
        - can=False, reason="interval_not_reached" → cycle 间隔不足
        """
        try:
            if now is None:
                now = time.time()
            # 检查冷却
            if self.cooldown_seconds > 0 and self.last_executed_at > 0:
                elapsed = max(0.0, now - self.last_executed_at)
                if elapsed < float(self.cooldown_seconds):
                    remaining = max(0.0, float(self.cooldown_seconds) - elapsed)
                    return False, "in_cooldown", remaining
            # 检查 interval(基于 cycle 编号比较)
            if self.interval > 0 and self.last_executed_cycle and cycle_id:
                # 若 cycle_id 相同,说明同一 cycle 不允许重复执行
                if str(cycle_id) == str(self.last_executed_cycle):
                    return False, "interval_not_reached", 0.0
            return True, "", 0.0
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleState.can_execute 异常(已隔离): %s", exc)
            return True, "", 0.0

    def tick(
        self,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        """记录一次执行(更新 last_executed_* / executions)。"""
        try:
            if now is None:
                now = time.time()
            self.last_executed_at = float(now)
            if cycle_id:
                self.last_executed_cycle = str(cycle_id)
            self.executions += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleState.tick 异常(已隔离): %s", exc)

    def record_skip(self, reason: str = "") -> None:
        """记录一次跳过(用于统计)。"""
        try:
            r = str(reason or "")
            if "cooldown" in r:
                self.skipped_cooldown += 1
            elif "interval" in r:
                self.skipped_interval += 1
        except Exception:  # noqa: BLE001
            pass

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "module": str(self.module or ""),
                "interval": int(self.interval),
                "throttle": float(self.throttle),
                "cooldown_seconds": float(self.cooldown_seconds),
                "last_executed_cycle": str(self.last_executed_cycle or ""),
                "last_executed_at": float(self.last_executed_at),
                "executions": int(self.executions),
                "skipped_cooldown": int(self.skipped_cooldown),
                "skipped_interval": int(self.skipped_interval),
            }
        except Exception:  # noqa: BLE001
            return {
                "module": str(self.module or ""),
                "interval": 0,
                "throttle": 1.0,
                "cooldown_seconds": 0.0,
                "last_executed_cycle": "",
                "last_executed_at": 0.0,
                "executions": 0,
                "skipped_cooldown": 0,
                "skipped_interval": 0,
            }


# ============================================================
# ThrottleRegistry
# ============================================================
class ThrottleRegistry:
    """集中管理所有模块的 ThrottleState。

    线程安全(RLock);提供 get / set / tick / reset 接口。
    """

    def __init__(
        self,
        intervals: Optional[Dict[str, int]] = None,
        throttles: Optional[Dict[str, float]] = None,
        cooldowns: Optional[Dict[str, float]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._states: Dict[str, ThrottleState] = {}
        # 应用默认配置
        self._apply_defaults(intervals, throttles, cooldowns)

    def _apply_defaults(
        self,
        intervals: Optional[Dict[str, int]],
        throttles: Optional[Dict[str, float]],
        cooldowns: Optional[Dict[str, float]],
    ) -> None:
        try:
            for module, interval in (intervals or DEFAULT_MODULE_INTERVALS).items():
                self._states[str(module)] = ThrottleState(
                    module=str(module),
                    interval=max(0, int(interval)),
                    throttle=1.0,
                    cooldown_seconds=0.0,
                )
            for module, throttle in (throttles or DEFAULT_MODULE_THROTTLES).items():
                state = self._states.get(str(module))
                if state is None:
                    state = ThrottleState(module=str(module))
                    self._states[str(module)] = state
                state.throttle = max(0.0, min(1.0, float(throttle)))
            for module, cooldown in (cooldowns or DEFAULT_MODULE_COOLDOWNS).items():
                state = self._states.get(str(module))
                if state is None:
                    state = ThrottleState(module=str(module))
                    self._states[str(module)] = state
                state.cooldown_seconds = max(0.0, float(cooldown))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleRegistry._apply_defaults 异常: %s", exc)

    # --------------------------------------------------------
    # CRUD
    # --------------------------------------------------------
    def get(self, module: str, default: bool = True) -> Optional[ThrottleState]:
        with self._lock:
            state = self._states.get(str(module or ""))
            if state is not None:
                return state
            if not default:
                return None
            # 自动创建一个
            state = ThrottleState(module=str(module or ""))
            self._states[str(module or "")] = state
            return state

    def has_module(self, module: str) -> bool:
        """检查模块是否已被注册。"""
        with self._lock:
            return str(module or "") in self._states

    def set(
        self,
        module: str,
        interval: Optional[int] = None,
        throttle: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
    ) -> ThrottleState:
        with self._lock:
            state = self._states.get(str(module or ""))
            if state is None:
                state = ThrottleState(module=str(module or ""))
                self._states[str(module or "")] = state
            if interval is not None:
                state.interval = max(0, int(interval))
            if throttle is not None:
                state.throttle = max(0.0, min(1.0, float(throttle)))
            if cooldown_seconds is not None:
                state.cooldown_seconds = max(0.0, float(cooldown_seconds))
            return state

    def remove(self, module: str) -> bool:
        with self._lock:
            return self._states.pop(str(module or ""), None) is not None

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    def all_modules(self) -> List[str]:
        with self._lock:
            return [str(m) for m in self._states.keys()]

    # --------------------------------------------------------
    # 执行 / 跳过 记录
    # --------------------------------------------------------
    def tick(
        self,
        module: str,
        cycle_id: str = "",
        now: Optional[float] = None,
    ) -> None:
        try:
            state = self.get(module)
            if state is not None:
                state.tick(cycle_id=cycle_id, now=now)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleRegistry.tick 异常(已隔离): %s", exc)

    def record_skip(self, module: str, reason: str = "") -> None:
        try:
            state = self.get(module)
            if state is not None:
                state.record_skip(reason)
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleRegistry.record_skip 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 状态/统计
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                str(m): state.to_dict() for m, state in self._states.items()
            }

    def stats(self) -> Dict[str, int]:
        with self._lock:
            total = 0
            skipped_cooldown = 0
            skipped_interval = 0
            for state in self._states.values():
                total += int(state.executions)
                skipped_cooldown += int(state.skipped_cooldown)
                skipped_interval += int(state.skipped_interval)
            return {
                "modules": len(self._states),
                "executions": total,
                "skipped_cooldown": skipped_cooldown,
                "skipped_interval": skipped_interval,
            }

    def reset_stats(self) -> None:
        with self._lock:
            for state in self._states.values():
                state.executions = 0
                state.skipped_cooldown = 0
                state.skipped_interval = 0


# ============================================================
# ThrottleRule
# ============================================================
class ThrottleRule(BasePolicyRule):
    """基于 ThrottleRegistry 的节流规则。

    行为:
    - 从 ThrottleRegistry 获取该 module 的 ThrottleState
    - 若 in_cooldown → deny(reason=in_cooldown, throttle=0)
    - 若 interval 未到 → deny(reason=interval_not_reached, throttle=0)
    - 否则 → allow(reason=throttle_ok, throttle=state.throttle, cooldown=...)
    - 任何异常 → 返回 None(fail-soft,交给下一条规则)

    默认 priority = 200(Maintenance 2000 / Safe 1000 / Disabled 500 之后,AlwaysAllow 0 之前)
    """

    def __init__(
        self,
        registry: Optional[ThrottleRegistry] = None,
        priority: int = DEFAULT_THROTTLE_PRIORITY,
    ) -> None:
        self._rule_name = "ThrottleRule"
        self._rule_priority = int(priority)
        self._registry = registry if registry is not None else ThrottleRegistry()

    @property
    def registry(self) -> ThrottleRegistry:
        return self._registry

    def evaluate(self, ctx: PolicyContext) -> Optional[RuntimeDecision]:
        try:
            if ctx is None:
                return None
            module = str(getattr(ctx, "module", "") or "").strip().lower()
            if not module:
                return None
            # 只有已注册的模块才参与节流;未注册 → 不命中,交给下一条规则
            if not self._registry.has_module(module):
                return None
            state = self._registry.get(module, default=False)
            if state is None:
                # 未知模块 → 不命中,交给下一条规则
                return None
            cycle_id = str(getattr(ctx, "cycle_id", "") or "")
            can, reason, cooldown_remaining = state.can_execute(cycle_id=cycle_id)
            if not can:
                # 跳过本次
                meta = {"rule": self.name, "module": module, "reason": reason}
                if reason == "in_cooldown":
                    meta["cooldown_remaining"] = float(cooldown_remaining)
                return RuntimeDecision.deny(
                    module=module,
                    reason=reason,
                    mode=DECISION_MODE_NORMAL,
                    throttle=0.0,
                    cooldown=float(cooldown_remaining),
                    execution_interval=int(state.interval),
                    metadata=meta,
                )
            # 可以执行,返回 allow + throttle + interval
            return RuntimeDecision.allow(
                module=module,
                reason="throttle_ok",
                mode=DECISION_MODE_NORMAL,
                throttle=float(state.throttle),
                cooldown=float(state.cooldown_seconds),
                execution_interval=int(state.interval),
                metadata={
                    "rule": self.name,
                    "module": module,
                    "interval": int(state.interval),
                    "throttle": float(state.throttle),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ThrottleRule 异常(已隔离): %s", exc)
            return None


# ============================================================
# 工厂
# ============================================================
def build_throttle_registry(
    intervals: Optional[Dict[str, int]] = None,
    throttles: Optional[Dict[str, float]] = None,
    cooldowns: Optional[Dict[str, float]] = None,
) -> ThrottleRegistry:
    """构造一个 ThrottleRegistry(使用提供的参数或默认配置)。"""
    return ThrottleRegistry(
        intervals=intervals,
        throttles=throttles,
        cooldowns=cooldowns,
    )


def build_default_throttle_rule(
    intervals: Optional[Dict[str, int]] = None,
    throttles: Optional[Dict[str, float]] = None,
    cooldowns: Optional[Dict[str, float]] = None,
    priority: int = DEFAULT_THROTTLE_PRIORITY,
) -> ThrottleRule:
    """构造一个默认的 ThrottleRule(使用 ThrottleRegistry)。"""
    return ThrottleRule(
        registry=build_throttle_registry(
            intervals=intervals,
            throttles=throttles,
            cooldowns=cooldowns,
        ),
        priority=priority,
    )
