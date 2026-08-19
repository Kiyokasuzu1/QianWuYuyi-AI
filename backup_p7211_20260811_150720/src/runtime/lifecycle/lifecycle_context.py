# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_context.py

Phase 5.0-D1: 任务执行上下文。

职责:
- 封装一次任务执行的环境(时钟、运行状态、快照、事件、取消标志)
- 提供 emit() / log() / check_quota() / is_cancelled() 等统一接口
- 隔离性:每次执行创建新 Context,不跨任务共享

约束:
- 不依赖任何业务模块(Memory/Growth/Personality/Goal/Proactive 等)
- 只通过 EventEmitter 与外部通信
- 不可变语义:snapshot_view / runtime_state 是只读视图
- 线程安全(单次执行串行,实例不跨线程)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, Mapping, Optional

from src.runtime.lifecycle.internal.clock import Clock

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle.internal.event_emitter import EventEmitter


logger = logging.getLogger(__name__)


# ============================================================
# Runtime Lifecycle State (轻量,只读视图)
# ============================================================
class RuntimeLifecycleStateView:
    """Runtime 当前观察状态的只读视图。

    D1 中仅通过 Snapshot 字段填充;不桥接 LongLoop 实时状态。
    """

    __slots__ = (
        "source",
        "last_state",
        "last_boot_mode",
        "last_turn_count",
        "last_checkpoint_count",
        "last_boot_count",
    )

    def __init__(
        self,
        source: str = "unknown",
        last_state: str = "UNKNOWN",
        last_boot_mode: str = "unknown",
        last_turn_count: int = 0,
        last_checkpoint_count: int = 0,
        last_boot_count: int = 0,
    ) -> None:
        self.source = str(source)
        self.last_state = str(last_state)
        self.last_boot_mode = str(last_boot_mode)
        self.last_turn_count = int(last_turn_count or 0)
        self.last_checkpoint_count = int(last_checkpoint_count or 0)
        self.last_boot_count = int(last_boot_count or 0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "last_state": self.last_state,
            "last_boot_mode": self.last_boot_mode,
            "last_turn_count": self.last_turn_count,
            "last_checkpoint_count": self.last_checkpoint_count,
            "last_boot_count": self.last_boot_count,
        }

    def __repr__(self) -> str:
        return (
            f"RuntimeLifecycleStateView(source={self.source!r}, "
            f"last_state={self.last_state!r}, last_boot_mode={self.last_boot_mode!r})"
        )


# ============================================================
# Snapshot View (只读)
# ============================================================
class _ReadOnlyView:
    """只读映射视图。"""

    __slots__ = ("_data",)

    def __init__(self, data: Optional[Mapping[str, Any]] = None) -> None:
        if data is None:
            self._data: Dict[str, Any] = {}
        elif isinstance(data, dict):
            # 深拷贝避免外部修改污染
            self._data = dict(data)
        elif isinstance(data, Mapping):
            try:
                self._data = dict(dict(data))
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"_ReadOnlyView({self._data!r})"

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


# ============================================================
# LifecycleContext
# ============================================================
class LifecycleContext:
    """任务执行上下文。

    每次任务执行前由 LifecycleManager 创建;不跨任务共享。
    """

    __slots__ = (
        "_clock",
        "_runtime_state",
        "_snapshot_view",
        "_emitter",
        "_task_id",
        "_cancelled",
        "_destroyed",
        "_quota_used",
        "_quota_max",
        "_lock",
    )

    def __init__(
        self,
        *,
        clock: Clock,
        runtime_state: Optional[RuntimeLifecycleStateView] = None,
        snapshot_view: Optional[Mapping[str, Any]] = None,
        emitter: Optional["EventEmitter"] = None,
        task_id: str = "",
        quota_max: int = 0,
    ) -> None:
        """构造 Context。

        参数:
        - clock: 时钟抽象
        - runtime_state: Runtime 当前观察状态
        - snapshot_view: RuntimeSnapshot 的只读视图
        - emitter: 事件总线(用于 emit)
        - task_id: 当前任务 ID
        - quota_max: 单次执行可 emit 的事件上限;0 = 不限
        """
        if clock is None:
            raise ValueError("LifecycleContext 需要 clock")
        self._lock = threading.RLock()
        self._clock = clock
        self._runtime_state = runtime_state or RuntimeLifecycleStateView()
        self._snapshot_view = _ReadOnlyView(snapshot_view)
        self._emitter = emitter
        self._task_id = str(task_id)
        self._cancelled = False
        self._destroyed = False
        self._quota_used = 0
        self._quota_max = max(0, int(quota_max or 0))

    # --------------------------------------------------------
    # 时钟
    # --------------------------------------------------------
    @property
    def clock(self) -> Clock:
        return self._clock

    def now(self) -> float:
        return self._clock.now()

    # --------------------------------------------------------
    # Runtime 状态
    # --------------------------------------------------------
    @property
    def runtime_state(self) -> RuntimeLifecycleStateView:
        return self._runtime_state

    # --------------------------------------------------------
    # Snapshot 视图
    # --------------------------------------------------------
    @property
    def snapshot_view(self) -> _ReadOnlyView:
        return self._snapshot_view

    # --------------------------------------------------------
    # 事件
    # --------------------------------------------------------
    @property
    def emitter(self) -> Optional["EventEmitter"]:
        return self._emitter

    def emit(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """发出事件。

        返回:
        - event_id: 成功
        - None: 失败(emitter 不可用 / 超过 quota / 异常)
        """
        if not isinstance(event_type, str) or not event_type:
            return None
        if not isinstance(payload, dict):
            return None
        with self._lock:
            if self._destroyed:
                return None
            if self._cancelled:
                return None
            if self._quota_max > 0 and self._quota_used >= self._quota_max:
                logger.warning(
                    "[LifecycleContext] 任务 %s emit quota 已满,丢弃事件: %s",
                    self._task_id,
                    event_type,
                )
                return None
            self._quota_used += 1
        if self._emitter is None:
            return None
        try:
            # 注入 task_id 到 payload
            try:
                enriched = dict(payload)
            except Exception:
                enriched = {}
            enriched.setdefault("task_id", self._task_id)
            return self._emitter.emit(event_type, enriched)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LifecycleContext] emit 失败(已隔离): %s", exc
            )
            return None

    # --------------------------------------------------------
    # 日志
    # --------------------------------------------------------
    def log(
        self,
        level: str,
        message: str,
        **fields: Any,
    ) -> None:
        """统一日志(写到 logger,不污染主流程)。"""
        try:
            log_level = {
                "DEBUG": logging.DEBUG,
                "INFO": logging.INFO,
                "WARNING": logging.WARNING,
                "ERROR": logging.ERROR,
                "CRITICAL": logging.CRITICAL,
            }.get(str(level).upper(), logging.INFO)
        except Exception:
            log_level = logging.INFO
        try:
            extra = {"task_id": self._task_id}
            extra.update(fields)
            logger.log(log_level, "[%s] %s", self._task_id, str(message), extra=extra)
        except Exception:  # noqa: BLE001
            try:
                logger.log(log_level, "[%s] %s", self._task_id, str(message))
            except Exception:
                pass

    # --------------------------------------------------------
    # 配额
    # --------------------------------------------------------
    def check_quota(self) -> bool:
        """检查是否还有 emit 配额。

        - quota_max = 0: 无限,总是 True
        - 否则: 已用 < max 时 True
        """
        with self._lock:
            if self._quota_max <= 0:
                return True
            return self._quota_used < self._quota_max

    @property
    def quota_used(self) -> int:
        with self._lock:
            return self._quota_used

    @property
    def quota_max(self) -> int:
        return self._quota_max

    # --------------------------------------------------------
    # 取消
    # --------------------------------------------------------
    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        """由 LifecycleManager 在 stop() 时调用,通知任务中断。"""
        with self._lock:
            self._cancelled = True

    def cancelled(self) -> bool:
        """is_cancelled 的别名(duck-typed)。"""
        return self.is_cancelled

    # --------------------------------------------------------
    # 销毁
    # --------------------------------------------------------
    @property
    def destroyed(self) -> bool:
        with self._lock:
            return self._destroyed

    def destroy(self) -> None:
        """销毁 Context(不再可用)。

        销毁后:
        - emit() 返回 None
        - cancel() 仍可调用(幂等)
        - 任何属性访问仍可读取
        """
        with self._lock:
            self._destroyed = True
            self._cancelled = True

    # --------------------------------------------------------
    # 上下文管理器
    # --------------------------------------------------------
    def __enter__(self) -> "LifecycleContext":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.destroy()

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    @property
    def task_id(self) -> str:
        return self._task_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self._task_id,
            "now": self._clock.now(),
            "runtime_state": self._runtime_state.to_dict(),
            "snapshot_view": self._snapshot_view.to_dict(),
            "is_cancelled": self.is_cancelled,
            "quota_used": self.quota_used,
            "quota_max": self.quota_max,
        }

    def __repr__(self) -> str:
        return (
            f"LifecycleContext(task_id={self._task_id!r}, "
            f"cancelled={self._cancelled!r}, "
            f"quota_used={self._quota_used!r}/{self._quota_max!r})"
        )


# ============================================================
# 工厂
# ============================================================
def build_context(
    *,
    clock: Clock,
    runtime_state: Optional[RuntimeLifecycleStateView] = None,
    snapshot_view: Optional[Mapping[str, Any]] = None,
    emitter: Optional["EventEmitter"] = None,
    task_id: str = "",
    quota_max: int = 0,
) -> LifecycleContext:
    """便捷工厂。"""
    return LifecycleContext(
        clock=clock,
        runtime_state=runtime_state,
        snapshot_view=snapshot_view,
        emitter=emitter,
        task_id=task_id,
        quota_max=quota_max,
    )


__all__ = [
    "LifecycleContext",
    "RuntimeLifecycleStateView",
    "build_context",
]
