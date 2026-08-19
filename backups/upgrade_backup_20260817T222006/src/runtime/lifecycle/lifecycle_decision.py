# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_decision.py

Phase 5.0-D1: 决策模型。

设计:
- Decision: 不可变数据类(verdict / reason / confidence / defer_until)
- Verdict: 枚举(RUN / SKIP / DEFER)
- LifecycleCondition: Protocol(should_run(ctx, state) -> Decision)
- 8 种基础条件原语:
    Always / Never / AfterInterval / AfterFirstRun /
    SnapshotBootCount / And / Or / Not / Predicate

约束:
- 不依赖业务模块
- 决策必须可重现(同 Context + TaskState 必得同 Decision)
- 条件原语异常隔离(包装为 SKIP,reason 记录)
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from src.runtime.lifecycle.lifecycle_context import LifecycleContext


logger = logging.getLogger(__name__)


# ============================================================
# 决策类型
# ============================================================
class Verdict(str, enum.Enum):
    """决策结果。"""

    RUN = "RUN"
    SKIP = "SKIP"
    DEFER = "DEFER"


# ============================================================
# Decision
# ============================================================
@dataclass(slots=True, frozen=True)
class Decision:
    """决策结果(不可变)。"""

    verdict: Verdict
    reason: str = ""
    confidence: float = 1.0
    defer_until: Optional[float] = None

    def __post_init__(self) -> None:
        # 类型规范化
        if not isinstance(self.verdict, Verdict):
            try:
                object.__setattr__(self, "verdict", Verdict(str(self.verdict)))
            except (ValueError, KeyError):
                raise ValueError(f"Decision.verdict 非法: {self.verdict!r}")
        try:
            object.__setattr__(self, "reason", str(self.reason or ""))
        except Exception:
            object.__setattr__(self, "reason", "")
        try:
            conf = float(self.confidence)
            if conf < 0.0:
                conf = 0.0
            elif conf > 1.0:
                conf = 1.0
            object.__setattr__(self, "confidence", conf)
        except Exception:
            object.__setattr__(self, "confidence", 1.0)
        if self.defer_until is not None:
            try:
                object.__setattr__(self, "defer_until", float(self.defer_until))
            except Exception:
                object.__setattr__(self, "defer_until", None)

    # --------------------------------------------------------
    # 便利构造
    # --------------------------------------------------------
    @classmethod
    def run(cls, reason: str = "", confidence: float = 1.0) -> "Decision":
        return cls(verdict=Verdict.RUN, reason=reason, confidence=confidence)

    @classmethod
    def skip(cls, reason: str = "", confidence: float = 1.0) -> "Decision":
        return cls(verdict=Verdict.SKIP, reason=reason, confidence=confidence)

    @classmethod
    def defer(
        cls,
        until: float,
        reason: str = "",
        confidence: float = 1.0,
    ) -> "Decision":
        return cls(
            verdict=Verdict.DEFER,
            reason=reason,
            confidence=confidence,
            defer_until=until,
        )

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    @property
    def is_run(self) -> bool:
        return self.verdict == Verdict.RUN

    @property
    def is_skip(self) -> bool:
        return self.verdict == Verdict.SKIP

    @property
    def is_defer(self) -> bool:
        return self.verdict == Verdict.DEFER

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "defer_until": self.defer_until,
        }


# ============================================================
# TaskState (用于决策,生命周期状态)
# ============================================================
class TaskState:
    """任务运行状态(由 Manager 维护)。

    决策条件可读取(只读):
    - last_run_at: 上次执行时间(epoch seconds)
    - run_count:   执行次数
    - failure_count: 失败次数
    - skip_count:  跳过次数
    - last_result: 上次结果
    """

    __slots__ = (
        "task_id",
        "last_run_at",
        "last_ended_at",
        "run_count",
        "failure_count",
        "skip_count",
        "last_result",
        "last_error",
    )

    def __init__(
        self,
        task_id: str = "",
        last_run_at: Optional[float] = None,
        last_ended_at: Optional[float] = None,
        run_count: int = 0,
        failure_count: int = 0,
        skip_count: int = 0,
        last_result: Any = None,
        last_error: Any = None,
    ) -> None:
        self.task_id = str(task_id)
        self.last_run_at = last_run_at
        self.last_ended_at = last_ended_at
        self.run_count = int(run_count or 0)
        self.failure_count = int(failure_count or 0)
        self.skip_count = int(skip_count or 0)
        self.last_result = last_result
        self.last_error = last_error

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "last_run_at": self.last_run_at,
            "last_ended_at": self.last_ended_at,
            "run_count": self.run_count,
            "failure_count": self.failure_count,
            "skip_count": self.skip_count,
        }


# ============================================================
# LifecycleCondition Protocol
# ============================================================
@runtime_checkable
class LifecycleCondition(Protocol):
    """决策条件接口。"""

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        ...


# ============================================================
# 基础条件: Always / Never
# ============================================================
class Always:
    """总执行。"""

    __slots__ = ("reason",)

    def __init__(self, reason: str = "always") -> None:
        self.reason = str(reason or "always")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        return Decision.run(self.reason)

    def __repr__(self) -> str:
        return f"Always(reason={self.reason!r})"


class Never:
    """永不执行。"""

    __slots__ = ("reason",)

    def __init__(self, reason: str = "never") -> None:
        self.reason = str(reason or "never")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        return Decision.skip(self.reason)

    def __repr__(self) -> str:
        return f"Never(reason={self.reason!r})"


# ============================================================
# AfterInterval
# ============================================================
class AfterInterval:
    """距上次执行 ≥ N 秒后才允许 RUN。"""

    __slots__ = ("interval", "default_reason")

    def __init__(self, interval: float, default_reason: str = "") -> None:
        try:
            self.interval = float(interval)
        except Exception:
            self.interval = 0.0
        self.default_reason = str(default_reason or "interval_not_elapsed")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        now = context.clock.now()
        if state.last_run_at is None:
            return Decision.run(reason="first_run", confidence=1.0)
        try:
            elapsed = now - float(state.last_run_at)
        except Exception:
            return Decision.run(reason="elapsed_invalid", confidence=0.5)
        if elapsed >= self.interval:
            return Decision.run(
                reason=f"interval_elapsed:{elapsed:.1f}s",
                confidence=1.0,
            )
        return Decision.skip(
            reason=f"{self.default_reason}:elapsed={elapsed:.1f}s<{self.interval:.1f}s",
            confidence=1.0,
        )

    def __repr__(self) -> str:
        return f"AfterInterval(interval={self.interval!r})"


# ============================================================
# AfterFirstRun
# ============================================================
class AfterFirstRun:
    """只在 first run 之后才 RUN。"""

    __slots__ = ("inner", "reason")

    def __init__(
        self,
        inner: Optional[LifecycleCondition] = None,
        reason: str = "first_run_not_done",
    ) -> None:
        self.inner = inner
        self.reason = str(reason or "first_run_not_done")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        if state.last_run_at is None:
            return Decision.skip(reason=self.reason)
        if self.inner is None:
            return Decision.run(reason="first_run_done")
        try:
            return self.inner.should_run(context, state)
        except Exception as exc:  # noqa: BLE001
            return Decision.skip(reason=f"inner_failed:{exc}")

    def __repr__(self) -> str:
        return f"AfterFirstRun(inner={self.inner!r})"


# ============================================================
# SnapshotBootCount
# ============================================================
class SnapshotBootCount:
    """boot_count 满足条件才 RUN。

    使用方式:
        SnapshotBootCount(min=2)        # boot_count >= 2
        SnapshotBootCount(max=5)        # boot_count <= 5
        SnapshotBootCount(min=2, max=10)  # 区间
    """

    __slots__ = ("min", "max", "default_reason")

    def __init__(
        self,
        min: Optional[int] = None,
        max: Optional[int] = None,
        default_reason: str = "boot_count_out_of_range",
    ) -> None:
        self.min = int(min) if min is not None else None
        self.max = int(max) if max is not None else None
        self.default_reason = str(default_reason or "boot_count_out_of_range")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        try:
            boot = int(context.snapshot_view.get("boot_count", 0) or 0)
        except Exception:
            boot = 0
        if self.min is not None and boot < self.min:
            return Decision.skip(
                reason=f"{self.default_reason}:boot={boot}<{self.min}",
            )
        if self.max is not None and boot > self.max:
            return Decision.skip(
                reason=f"{self.default_reason}:boot={boot}>{self.max}",
            )
        return Decision.run(reason=f"boot_count_ok:{boot}")

    def __repr__(self) -> str:
        return f"SnapshotBootCount(min={self.min!r}, max={self.max!r})"


# ============================================================
# And / Or / Not
# ============================================================
class And:
    """所有子条件 RUN 才 RUN;任一 SKIP 则 SKIP;任一 DEFER 则 DEFER。

    子节点可以是 LifecycleCondition 或 Decision 实例(后者直接使用)。
    """

    __slots__ = ("conditions",)

    def __init__(self, *conditions: Any) -> None:
        self.conditions = list(conditions)

    def _evaluate_child(self, child: Any, context, state) -> Decision:
        if isinstance(child, Decision):
            return child
        if isinstance(child, LifecycleCondition) or hasattr(child, "should_run"):
            try:
                return child.should_run(context, state)
            except Exception as exc:  # noqa: BLE001
                return Decision.skip(reason=f"and:child_failed:{exc}")
        return Decision.skip(reason=f"and:not_a_condition:{type(child).__name__}")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        if not self.conditions:
            return Decision.run(reason="and:empty")
        min_conf = 1.0
        last_reason = ""
        for c in self.conditions:
            d = self._evaluate_child(c, context, state)
            if d.is_skip:
                return d
            if d.is_defer:
                return d
            min_conf = min(min_conf, d.confidence)
            last_reason = d.reason
        return Decision.run(reason=last_reason, confidence=min_conf)

    def __repr__(self) -> str:
        return f"And({len(self.conditions)} children)"


class Or:
    """任一子条件 RUN 即 RUN;全 SKIP 才 SKIP。"""

    __slots__ = ("conditions",)

    def __init__(self, *conditions: Any) -> None:
        self.conditions = list(conditions)

    def _evaluate_child(self, child: Any, context, state) -> Decision:
        if isinstance(child, Decision):
            return child
        if isinstance(child, LifecycleCondition) or hasattr(child, "should_run"):
            try:
                return child.should_run(context, state)
            except Exception as exc:  # noqa: BLE001
                return Decision.skip(reason=f"or:child_failed:{exc}")
        return Decision.skip(reason=f"or:not_a_condition:{type(child).__name__}")

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        if not self.conditions:
            return Decision.skip(reason="or:empty")
        max_conf = 0.0
        any_ran = False
        last_skip_reason = ""
        for c in self.conditions:
            d = self._evaluate_child(c, context, state)
            if d.is_run:
                any_ran = True
                max_conf = max(max_conf, d.confidence)
            elif d.is_defer:
                return d
            else:
                last_skip_reason = d.reason
        if any_ran:
            return Decision.run(reason="or:matched", confidence=max_conf)
        return Decision.skip(reason=f"or:all_skipped:{last_skip_reason}")

    def __repr__(self) -> str:
        return f"Or({len(self.conditions)} children)"


class Not:
    """反转子条件: 子条件 SKIP → RUN; 子条件 RUN → SKIP; 子条件 DEFER → DEFER。"""

    __slots__ = ("inner",)

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        if isinstance(self.inner, Decision):
            d = self.inner
        elif isinstance(self.inner, LifecycleCondition) or hasattr(
            self.inner, "should_run"
        ):
            try:
                d = self.inner.should_run(context, state)
            except Exception as exc:  # noqa: BLE001
                return Decision.skip(reason=f"not:inner_failed:{exc}")
        else:
            return Decision.skip(reason=f"not:not_a_condition:{type(self.inner).__name__}")
        if d.is_run:
            return Decision.skip(reason=f"not:reverted:{d.reason}")
        if d.is_defer:
            return d
        return Decision.run(reason=f"not:matched:{d.reason}")

    def __repr__(self) -> str:
        return f"Not(inner={self.inner!r})"


# ============================================================
# Predicate
# ============================================================
class Predicate:
    """自定义谓词条件。

    使用:
        Predicate(lambda ctx, state: Decision.run("ok"))
    """

    __slots__ = ("fn", "name", "isolated")

    def __init__(
        self,
        fn: Callable[[LifecycleContext, TaskState], Decision],
        *,
        name: str = "predicate",
        isolated: bool = True,
    ) -> None:
        self.fn = fn
        self.name = str(name or "predicate")
        self.isolated = bool(isolated)

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        try:
            d = self.fn(context, state)
        except Exception as exc:  # noqa: BLE001
            return Decision.skip(reason=f"{self.name}:exception:{exc}")
        if not isinstance(d, Decision):
            return Decision.skip(reason=f"{self.name}:not_decision")
        return d

    def __repr__(self) -> str:
        return f"Predicate(name={self.name!r})"


# ============================================================
# DecisionEngine
# ============================================================
class DecisionEngine:
    """决策引擎:调用 condition.should_run,处理异常隔离与默认行为。"""

    __slots__ = ("_default_on_exception",)

    def __init__(
        self,
        default_on_exception: Verdict = Verdict.SKIP,
    ) -> None:
        self._default_on_exception = default_on_exception

    def evaluate(
        self,
        condition: LifecycleCondition,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        if not hasattr(condition, "should_run"):
            return Decision.skip(reason="condition_not_callable")
        try:
            d = condition.should_run(context, state)
        except Exception as exc:  # noqa: BLE001
            return Decision.skip(reason=f"engine:exception:{exc}")
        if not isinstance(d, Decision):
            return Decision.skip(reason="engine:invalid_decision")
        return d


__all__ = [
    "Verdict",
    "Decision",
    "TaskState",
    "LifecycleCondition",
    "Always",
    "Never",
    "AfterInterval",
    "AfterFirstRun",
    "SnapshotBootCount",
    "And",
    "Or",
    "Not",
    "Predicate",
    "DecisionEngine",
]
