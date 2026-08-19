# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_task.py

Phase 5.0-D1: 任务抽象。

职责:
- 定义 LifecycleTask 协议
- 提供 BaseLifecycleTask 默认实现
- 任务异常包装为 LifecycleError
- 任务不保存运行状态(由 Manager 维护 TaskState)
- 任务不调用其他任务
- 任务不依赖业务模块

设计:
- task_id 必须非空、合法字符
- owner_module 标识来源模块
- priority 1-10(默认 5)
- condition: LifecycleCondition 实例(默认 Always)
- budget_ms: 单次执行时间预算(None = 无限制)
- interval_seconds: 调度间隔提示(None = 手动触发)
- max_concurrent: 同任务并发上限(默认 1)
- execute(ctx) -> LifecycleResult | dict | None
"""
from __future__ import annotations

import logging
import re
import time as _time
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import (
    Always,
    Decision,
    LifecycleCondition,
    TaskState,
)
from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
    classify_exception,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)

if TYPE_CHECKING:  # pragma: no cover
    pass


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
_TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+$")
_OWNER_PATTERN = re.compile(r"^[a-zA-Z0-9_.\-]+$")

MIN_PRIORITY = 1
MAX_PRIORITY = 10
DEFAULT_PRIORITY = 5


def _validate_task_id(task_id: Any) -> str:
    if not isinstance(task_id, str):
        raise ValueError(f"task_id 必须是字符串,实际: {type(task_id).__name__}")
    if not task_id or not task_id.strip():
        raise ValueError("task_id 不能为空")
    if not _TASK_ID_PATTERN.match(task_id):
        raise ValueError(
            f"task_id 含非法字符: {task_id!r} (允许: a-z A-Z 0-9 _ . -)"
        )
    return task_id


def _validate_owner(owner: Any) -> str:
    if not isinstance(owner, str):
        raise ValueError(f"owner 必须是字符串,实际: {type(owner).__name__}")
    if not owner or not owner.strip():
        raise ValueError("owner 不能为空")
    if not _OWNER_PATTERN.match(owner):
        raise ValueError(
            f"owner 含非法字符: {owner!r} (允许: a-z A-Z 0-9 _ . -)"
        )
    return owner


# ============================================================
# LifecycleTask Protocol
# ============================================================
@runtime_checkable
class LifecycleTask(Protocol):
    """任务协议(duck-typed)。"""

    @property
    def task_id(self) -> str: ...

    @property
    def owner(self) -> str: ...

    @property
    def priority(self) -> int: ...

    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision: ...

    def execute(
        self,
        context: LifecycleContext,
    ) -> Any: ...

    def on_success(self, result: LifecycleResult) -> None: ...

    def on_failure(self, error: LifecycleError) -> None: ...


# ============================================================
# BaseLifecycleTask
# ============================================================
class BaseLifecycleTask:
    """任务基类。

    子类最少实现 execute();其他方法有合理默认。
    """

    def __init__(
        self,
        task_id: str,
        owner: str,
        *,
        display_name: str = "",
        priority: int = DEFAULT_PRIORITY,
        condition: Optional[LifecycleCondition] = None,
        budget_ms: Optional[int] = None,
        interval_seconds: Optional[float] = None,
        max_concurrent: int = 1,
    ) -> None:
        self._task_id = _validate_task_id(task_id)
        self._owner = _validate_owner(owner)
        self._display_name = str(display_name or task_id)
        try:
            p = int(priority)
        except Exception:
            p = DEFAULT_PRIORITY
        if p < MIN_PRIORITY:
            p = MIN_PRIORITY
        elif p > MAX_PRIORITY:
            p = MAX_PRIORITY
        self._priority = p
        self._condition = condition if condition is not None else Always()
        try:
            self._budget_ms = int(budget_ms) if budget_ms is not None else None
            if self._budget_ms is not None and self._budget_ms <= 0:
                self._budget_ms = None
        except Exception:
            self._budget_ms = None
        try:
            self._interval_seconds = (
                float(interval_seconds) if interval_seconds is not None else None
            )
        except Exception:
            self._interval_seconds = None
        try:
            mc = int(max_concurrent)
        except Exception:
            mc = 1
        if mc < 1:
            mc = 1
        self._max_concurrent = mc

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def condition(self) -> LifecycleCondition:
        return self._condition

    @property
    def budget_ms(self) -> Optional[int]:
        return self._budget_ms

    @property
    def interval_seconds(self) -> Optional[float]:
        return self._interval_seconds

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    # --------------------------------------------------------
    # 决策
    # --------------------------------------------------------
    def should_run(
        self,
        context: LifecycleContext,
        state: TaskState,
    ) -> Decision:
        try:
            d = self._condition.should_run(context, state)
        except Exception as exc:  # noqa: BLE001
            return Decision.skip(reason=f"condition_failed:{exc}")
        if not isinstance(d, Decision):
            return Decision.skip(reason="condition_invalid_return")
        return d

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------
    def execute(
        self,
        context: LifecycleContext,
    ) -> Any:
        """子类必须实现。

        返回值:
        - LifecycleResult: 原样使用
        - dict: 自动包装为 LifecycleResult(由 run_safely 处理)
        - None: 自动包装为 LifecycleResult.success
        - 其他: 包装为 LifecycleResult.success
        """
        raise NotImplementedError(
            f"{type(self).__name__}.execute() 未实现"
        )

    def on_success(self, result: LifecycleResult) -> None:
        """默认 no-op。子类可覆盖。"""
        return None

    def on_failure(self, error: LifecycleError) -> None:
        """默认 no-op。子类可覆盖。"""
        return None

    # --------------------------------------------------------
    # 包装执行
    # --------------------------------------------------------
    def run_safely(
        self,
        context: LifecycleContext,
        *,
        now_fn=None,
    ) -> LifecycleResult:
        """执行任务并包装为 LifecycleResult。

        - 异常自动包装为 LifecycleError
        - 字典/None 返回值自动包装为 LifecycleResult
        - 不抛任何异常(异常隔离)
        - 尊重 context.is_cancelled
        - 尊重 budget_ms(粗略计时,使用 time.monotonic)
        """
        if context is None:
            return LifecycleResult.fatal(
                self._task_id,
                error=LifecycleError.internal("no_context"),
                started_at=0.0,
                ended_at=0.0,
            )
        if context.is_cancelled:
            return LifecycleResult.cancelled(self._task_id, at=context.now())
        now = context.now()
        budget = self._budget_ms
        try:
            ret = self._invoke_with_budget(context, budget)
        except LifecycleError as e:
            ended = context.now()
            status = (
                LifecycleStatus.FAILED
                if e.category
                in (ErrorCategory.TRANSIENT, ErrorCategory.PERMANENT)
                else LifecycleStatus.FAILED
            )
            if e.category == ErrorCategory.TIMEOUT:
                status = LifecycleStatus.TIMEOUT
            elif e.category == ErrorCategory.CANCELLED:
                status = LifecycleStatus.CANCELLED
            r = LifecycleResult(
                task_id=self._task_id,
                status=status,
                started_at=now,
                ended_at=ended,
                error=e,
            )
            try:
                self.on_failure(e)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[LifecycleTask] on_failure 钩子失败(已隔离): %s", exc
                )
            return r
        except BaseException as exc:  # noqa: BLE001
            ended = context.now()
            le = classify_exception(exc)
            r = LifecycleResult(
                task_id=self._task_id,
                status=LifecycleStatus.FAILED,
                started_at=now,
                ended_at=ended,
                error=le,
            )
            try:
                self.on_failure(le)
            except Exception as exc2:  # noqa: BLE001
                logger.warning(
                    "[LifecycleTask] on_failure 钩子失败(已隔离): %s", exc2
                )
            return r

        ended = context.now()
        # 返回值包装
        if isinstance(ret, LifecycleResult):
            r = ret
        elif isinstance(ret, dict):
            r = LifecycleResult(
                task_id=self._task_id,
                status=LifecycleStatus.SUCCESS,
                started_at=now,
                ended_at=ended,
                produced_events=list(ret.get("events") or []),
                metrics=dict(ret.get("metrics") or {}),
                next_recommended_at=ret.get("next_recommended_at"),
            )
        elif ret is None:
            r = LifecycleResult.success(
                self._task_id, started_at=now, ended_at=ended
            )
        else:
            r = LifecycleResult.success(
                self._task_id,
                started_at=now,
                ended_at=ended,
                metrics={"return_type": type(ret).__name__},
            )

        try:
            self.on_success(r)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LifecycleTask] on_success 钩子失败(已隔离): %s", exc
            )
        return r

    def _invoke_with_budget(
        self,
        context: LifecycleContext,
        budget_ms: Optional[int],
    ):
        """执行 execute(),可被 Future 化扩展;此处单线程直接调用。"""
        # 单线程模式:不实现真超时(D1 阶段)
        # 仅对 budget<=0 的极端值,直接 raise TimeoutError 包装
        if budget_ms is not None and budget_ms <= 0:
            raise LifecycleError.timeout("budget_exhausted_before_run")
        return self.execute(context)

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, BaseLifecycleTask):
            return False
        return self._task_id == other._task_id

    def __hash__(self) -> int:
        return hash(self._task_id)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(task_id={self._task_id!r}, "
            f"owner={self._owner!r}, priority={self._priority!r})"
        )


# ============================================================
# 简单 Task 工厂
# ============================================================
class SimpleTask(BaseLifecycleTask):
    """基于 callable 的简单任务(便于测试/动态创建)。

    使用:
        task = SimpleTask(
            task_id="t.demo",
            owner="test",
            action=lambda ctx: {"metrics": {"k": 1}},
        )
    """

    def __init__(
        self,
        task_id: str,
        owner: str,
        action,
        *,
        condition: Optional[LifecycleCondition] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            task_id,
            owner,
            condition=condition,
            **kwargs,
        )
        if not callable(action):
            raise ValueError("SimpleTask.action 必须可调用")
        self._action = action

    def execute(self, context: LifecycleContext) -> Any:
        return self._action(context)


__all__ = [
    "LifecycleTask",
    "BaseLifecycleTask",
    "SimpleTask",
    "MIN_PRIORITY",
    "MAX_PRIORITY",
    "DEFAULT_PRIORITY",
]
