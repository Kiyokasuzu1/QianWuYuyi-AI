# -*- coding: utf-8 -*-
"""
src/runtime/initiative/action_filter.py

Phase 5.0-D3-C: ActionFilter 行动过滤器。

职责:
- 评估 PossibleAction 候选
- 应用 4 类规则:
  1) 低置信度 → discarded
  2) 低 expected_value → deferred
  3) 重复抑制(24h 内同 topic)
  4) 能量限制

约束:
- 不依赖业务模块
- 仅 Python 标准库
- 不调用 LLM / DB / Network
- 纯规则实现
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.initiative.internal_state import InternalState
from src.runtime.initiative.possible_action import (
    ACTION_EFFORT_HIGH,
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_URGENCY_HIGH,
    ACTION_URGENCY_LOW,
    ACTION_URGENCY_NORMAL,
    PossibleAction,
)


# ============================================================
# 常量
# ============================================================
ACTION_FILTER_SCHEMA_VERSION = "1.0"

DEFAULT_MIN_CONFIDENCE = 0.30  # < 0.3 -> discarded
DEFAULT_MIN_EXPECTED_VALUE = 0.20  # < 0.2 -> deferred
DEFAULT_REPEAT_WINDOW_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_ENERGY_THRESHOLD = 0.25  # 低能量阈值
DEFAULT_REPEAT_PENALTY = 0.5  # 重复 topic 优先级惩罚

EFFORT_NUMERIC = {
    ACTION_EFFORT_LOW: 0.2,
    ACTION_EFFORT_MEDIUM: 0.5,
    ACTION_EFFORT_HIGH: 0.8,
}
URGENCY_NUMERIC = {
    ACTION_URGENCY_LOW: 0.2,
    ACTION_URGENCY_NORMAL: 0.5,
    ACTION_URGENCY_HIGH: 0.8,
}


# ============================================================
# 过滤结果
# ============================================================
@dataclass
class FilterDecision:
    """过滤决策。

    字段:
    - action_id:       行动 ID
    - passed:          是否通过(filtered=pending 时为 True)
    - new_status:      过滤后状态(pending/filtered/deferred/discarded)
    - reason:          决策原因
    - priority:        过滤后优先级
    - rule_applied:    命中的规则(low_confidence/low_value/repeat/energy/none)
    """

    action_id: str = ""
    passed: bool = True
    new_status: str = ACTION_STATUS_PENDING
    reason: str = ""
    priority: float = 0.5
    rule_applied: str = "none"


# ============================================================
# ActionFilter
# ============================================================
class ActionFilter:
    """PossibleAction 过滤器(线程安全)。"""

    def __init__(
        self,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_expected_value: float = DEFAULT_MIN_EXPECTED_VALUE,
        repeat_window_seconds: float = DEFAULT_REPEAT_WINDOW_SECONDS,
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        repeat_penalty: float = DEFAULT_REPEAT_PENALTY,
        name: str = "action_filter",
    ) -> None:
        self._name = str(name or "action_filter")
        self._min_confidence = _clip01(min_confidence, default=DEFAULT_MIN_CONFIDENCE)
        self._min_expected_value = _clip01(min_expected_value, default=DEFAULT_MIN_EXPECTED_VALUE)
        try:
            self._repeat_window = float(repeat_window_seconds)
        except Exception:
            self._repeat_window = DEFAULT_REPEAT_WINDOW_SECONDS
        if self._repeat_window < 0:
            self._repeat_window = 0.0
        self._energy_threshold = _clip01(energy_threshold, default=DEFAULT_ENERGY_THRESHOLD)
        try:
            self._repeat_penalty = float(repeat_penalty)
        except Exception:
            self._repeat_penalty = DEFAULT_REPEAT_PENALTY
        if self._repeat_penalty < 0.0:
            self._repeat_penalty = 0.0
        if self._repeat_penalty > 1.0:
            self._repeat_penalty = 1.0
        self._lock = threading.RLock()
        # 历史(用于重复抑制,topic -> last_seen)
        self._history: Dict[str, float] = {}
        self._history_max = 1024
        # 统计
        self._total_evaluated = 0
        self._total_passed = 0
        self._total_filtered = 0
        self._total_deferred = 0
        self._total_discarded = 0
        self._rule_hits = {
            "low_confidence": 0,
            "low_value": 0,
            "repeat": 0,
            "energy": 0,
            "none": 0,
        }
        self._last_error = ""

    @property
    def name(self) -> str:
        return self._name

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    @property
    def min_expected_value(self) -> float:
        return self._min_expected_value

    @property
    def repeat_window_seconds(self) -> float:
        return self._repeat_window

    @property
    def energy_threshold(self) -> float:
        return self._energy_threshold

    @property
    def repeat_penalty(self) -> float:
        return self._repeat_penalty

    @property
    def total_evaluated(self) -> int:
        with self._lock:
            return self._total_evaluated

    @property
    def total_passed(self) -> int:
        with self._lock:
            return self._total_passed

    @property
    def total_filtered(self) -> int:
        with self._lock:
            return self._total_filtered

    @property
    def total_deferred(self) -> int:
        with self._lock:
            return self._total_deferred

    @property
    def total_discarded(self) -> int:
        with self._lock:
            return self._total_discarded

    @property
    def rule_hits(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._rule_hits)

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def evaluate(
        self,
        action: PossibleAction,
        *,
        state: Optional[InternalState] = None,
        now: Optional[float] = None,
    ) -> FilterDecision:
        """评估一个 PossibleAction。

        返回 FilterDecision(原 action 不被修改)。
        """
        if not isinstance(action, PossibleAction):
            with self._lock:
                self._last_error = "evaluate 收到非 PossibleAction"
            return FilterDecision(
                action_id="",
                passed=False,
                new_status=ACTION_STATUS_DISCARDED,
                reason="invalid_action",
                priority=0.0,
                rule_applied="none",
            )

        try:
            return self._evaluate_inner(action, state=state, now=now)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"evaluate 异常: {exc}"
            return FilterDecision(
                action_id=action.action_id,
                passed=False,
                new_status=ACTION_STATUS_DISCARDED,
                reason="filter_error",
                priority=action.priority,
                rule_applied="none",
            )

    def _evaluate_inner(
        self,
        action: PossibleAction,
        *,
        state: Optional[InternalState],
        now: Optional[float],
    ) -> FilterDecision:
        with self._lock:
            self._total_evaluated += 1

        priority = action.priority
        new_status = ACTION_STATUS_PENDING
        reason = "passed"
        rule = "none"

        # 1) 低置信度
        if action.confidence < self._min_confidence:
            new_status = ACTION_STATUS_DISCARDED
            reason = f"low_confidence({action.confidence:.2f}<{self._min_confidence:.2f})"
            rule = "low_confidence"
            with self._lock:
                self._rule_hits["low_confidence"] = self._rule_hits.get("low_confidence", 0) + 1
            return self._finalize(action, new_status, reason, priority, rule)

        # 2) 低 expected_value
        if action.expected_value < self._min_expected_value:
            new_status = ACTION_STATUS_DEFERRED
            reason = f"low_value({action.expected_value:.2f}<{self._min_expected_value:.2f})"
            rule = "low_value"
            with self._lock:
                self._rule_hits["low_value"] = self._rule_hits.get("low_value", 0) + 1
            return self._finalize(action, new_status, reason, priority, rule)

        # 3) 重复抑制(24h 内同 topic)
        try:
            now_f = float(now) if now is not None else 0.0
        except Exception:
            now_f = 0.0
        if now_f > 0 and self._repeat_window > 0:
            with self._lock:
                last_seen = self._history.get(action.topic, 0.0)
            if last_seen > 0 and (now_f - last_seen) <= self._repeat_window:
                new_priority = priority * (1.0 - self._repeat_penalty)
                if new_priority < 0.0:
                    new_priority = 0.0
                priority = new_priority
                reason = f"repeat_penalty(topic={action.topic!r}, age={now_f - last_seen:.0f}s)"
                rule = "repeat"
                with self._lock:
                    self._rule_hits["repeat"] = self._rule_hits.get("repeat", 0) + 1

        # 4) 能量限制
        if isinstance(state, InternalState) and state.is_low_energy(self._energy_threshold):
            # 高 effort 的行动降级为 filtered
            if action.effort_estimate == ACTION_EFFORT_HIGH:
                new_status = ACTION_STATUS_FILTERED
                reason = (
                    f"energy_too_low(state={state.energy_level:.2f}<{self._energy_threshold:.2f})"
                )
                rule = "energy"
                with self._lock:
                    self._rule_hits["energy"] = self._rule_hits.get("energy", 0) + 1
                return self._finalize(action, new_status, reason, priority, rule)

        # 通过 - 记录历史(用于重复抑制)
        if rule == "none":
            with self._lock:
                self._rule_hits["none"] = self._rule_hits.get("none", 0) + 1
        if new_status == ACTION_STATUS_PENDING and now_f > 0 and action.topic:
            with self._lock:
                self._history[action.topic] = now_f
                if len(self._history) > self._history_max:
                    try:
                        oldest_key = next(iter(self._history))
                        self._history.pop(oldest_key, None)
                    except Exception:
                        pass
        return self._finalize(action, new_status, reason, priority, rule)

    def _finalize(
        self,
        action: PossibleAction,
        new_status: str,
        reason: str,
        priority: float,
        rule: str,
    ) -> FilterDecision:
        passed = new_status == ACTION_STATUS_PENDING
        with self._lock:
            if new_status == ACTION_STATUS_PENDING:
                self._total_passed += 1
            elif new_status == ACTION_STATUS_FILTERED:
                self._total_filtered += 1
            elif new_status == ACTION_STATUS_DEFERRED:
                self._total_deferred += 1
            elif new_status == ACTION_STATUS_DISCARDED:
                self._total_discarded += 1
        return FilterDecision(
            action_id=action.action_id,
            passed=passed,
            new_status=new_status,
            reason=reason,
            priority=priority,
            rule_applied=rule,
        )

    # --------------------------------------------------------
    # 应用决策并更新历史
    # --------------------------------------------------------
    def apply(
        self,
        action: PossibleAction,
        decision: FilterDecision,
        *,
        now: Optional[float] = None,
    ) -> PossibleAction:
        """把决策应用到 action(状态变更 + 优先级更新),并记录到历史。

        返回修改后的 action(同一对象,原地修改)。
        """
        if not isinstance(action, PossibleAction):
            return action
        if not isinstance(decision, FilterDecision):
            return action
        # 应用状态
        try:
            now_f = float(now) if now is not None else 0.0
        except Exception:
            now_f = 0.0
        if decision.new_status == ACTION_STATUS_PENDING:
            action.mark_pending(now_f)
        elif decision.new_status == ACTION_STATUS_FILTERED:
            action.mark_filtered(now_f)
        elif decision.new_status == ACTION_STATUS_DEFERRED:
            action.mark_deferred(now_f)
        elif decision.new_status == ACTION_STATUS_DISCARDED:
            action.mark_discarded(now_f)
        action.update_priority(decision.priority, now=now_f)
        # 记录历史(topic -> now)
        with self._lock:
            if now_f > 0 and action.topic:
                self._history[action.topic] = now_f
                # 限制 history 大小
                if len(self._history) > self._history_max:
                    # 简单淘汰最早
                    try:
                        oldest_key = next(iter(self._history))
                        self._history.pop(oldest_key, None)
                    except Exception:
                        pass
        return action

    def filter_batch(
        self,
        actions: Sequence[PossibleAction],
        *,
        state: Optional[InternalState] = None,
        now: Optional[float] = None,
    ) -> List[FilterDecision]:
        """批量过滤。"""
        decisions: List[FilterDecision] = []
        if actions is None:
            return decisions
        for a in actions:
            decisions.append(self.evaluate(a, state=state, now=now))
        return decisions

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear_history(self) -> int:
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n

    def history_snapshot(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._history)

    def reset_stats(self) -> None:
        with self._lock:
            self._total_evaluated = 0
            self._total_passed = 0
            self._total_filtered = 0
            self._total_deferred = 0
            self._total_discarded = 0
            for k in self._rule_hits:
                self._rule_hits[k] = 0

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "min_confidence": self._min_confidence,
                "min_expected_value": self._min_expected_value,
                "repeat_window_seconds": self._repeat_window,
                "energy_threshold": self._energy_threshold,
                "repeat_penalty": self._repeat_penalty,
                "total_evaluated": self._total_evaluated,
                "total_passed": self._total_passed,
                "total_filtered": self._total_filtered,
                "total_deferred": self._total_deferred,
                "total_discarded": self._total_discarded,
                "rule_hits": dict(self._rule_hits),
                "history_size": len(self._history),
                "last_error": self._last_error,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"ActionFilter(name={self._name!r}, evaluated={self._total_evaluated}, "
                f"passed={self._total_passed}, filtered={self._total_filtered}, "
                f"deferred={self._total_deferred}, discarded={self._total_discarded})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_action_filter() -> ActionFilter:
    return ActionFilter()


# ============================================================
# 工具
# ============================================================
def _clip01(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


__all__ = [
    # 常量
    "ACTION_FILTER_SCHEMA_VERSION",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_MIN_EXPECTED_VALUE",
    "DEFAULT_REPEAT_WINDOW_SECONDS",
    "DEFAULT_ENERGY_THRESHOLD",
    "DEFAULT_REPEAT_PENALTY",
    # 数据类
    "FilterDecision",
    # 类
    "ActionFilter",
    # 工厂
    "build_default_action_filter",
]
