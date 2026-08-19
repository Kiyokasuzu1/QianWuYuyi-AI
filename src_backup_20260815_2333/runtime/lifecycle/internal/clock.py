# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/internal/clock.py

Phase 5.0-D1: Clock 抽象。

职责:
- 提供统一的"时间"接口,让所有时间相关测试可冻结、可重现
- 禁止业务代码直接调用 time.time() / time.sleep() (除本文件内部)

设计:
- Clock: Protocol(只声明 now() 接口)
- SystemClock: 默认实现,使用 time 模块
- FrozenClock: 不可逆冻结时钟,测试用
- MockClock: 注入式,可由调用方提供 callable

约束:
- 不依赖任何业务模块
- 不抛异常(冻结态 advance 抛 FrozenClockError)
- 线程安全(基础锁保护)
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Protocol, runtime_checkable


# ============================================================
# Exceptions
# ============================================================
class ClockError(Exception):
    """Clock 抽象层异常基类。"""


class FrozenClockError(ClockError):
    """对已冻结时钟进行修改时抛出。"""


# ============================================================
# Clock Protocol
# ============================================================
@runtime_checkable
class Clock(Protocol):
    """时间抽象接口。

    所有时间相关操作必须通过 Clock 实例,
    禁止业务代码直接调用 time.time()。
    """

    def now(self) -> float:
        """返回当前时间(epoch seconds, UTC)。"""
        ...

    def monotonic(self) -> float:
        """返回单调时钟值(epoch seconds)。用于测量间隔。"""
        ...


# ============================================================
# SystemClock
# ============================================================
class SystemClock:
    """默认系统时钟实现。

    - now(): 使用 time.time()
    - monotonic(): 使用 time.monotonic()
    - 线程安全(time 模块本身线程安全)
    """

    __slots__ = ("_name",)

    def __init__(self, name: str = "system") -> None:
        self._name = str(name)

    @property
    def name(self) -> str:
        return self._name

    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    def __repr__(self) -> str:
        return f"SystemClock(name={self._name!r})"


# ============================================================
# FrozenClock
# ============================================================
class FrozenClock:
    """不可逆冻结时钟(测试用)。

    特性:
    - 初始化时指定起始时间
    - 显式调用 freeze() 后进入"完全不可变"状态
    - 未冻结时可通过 advance(delta) / set(t) 修改
    - 冻结后任何修改抛 FrozenClockError
    - 线程安全(RLock)
    """

    __slots__ = (
        "_initial",
        "_now",
        "_frozen",
        "_lock",
    )

    def __init__(self, initial: float = 0.0, *, frozen: bool = False) -> None:
        """构造 FrozenClock。

        参数:
        - initial: 起始时间(epoch seconds)
        - frozen: 是否立即进入冻结态
        """
        self._lock = threading.RLock()
        self._initial = float(initial)
        self._now = float(initial)
        self._frozen = bool(frozen)

    # --------------------------------------------------------
    # Clock 接口
    # --------------------------------------------------------
    def now(self) -> float:
        with self._lock:
            return self._now

    def monotonic(self) -> float:
        # FrozenClock 不区分 wall/monotonic,行为相同
        with self._lock:
            return self._now

    # --------------------------------------------------------
    # 控制接口
    # --------------------------------------------------------
    def advance(self, delta: float) -> float:
        """推进时间。返回新时间值。

        冻结态下抛 FrozenClockError。
        """
        with self._lock:
            if self._frozen:
                raise FrozenClockError("FrozenClock 已冻结,不能 advance()")
            try:
                d = float(delta)
            except Exception:
                d = 0.0
            self._now = self._now + d
            return self._now

    def set(self, value: float) -> None:
        """设置当前时间。冻结态下抛 FrozenClockError。"""
        with self._lock:
            if self._frozen:
                raise FrozenClockError("FrozenClock 已冻结,不能 set()")
            self._now = float(value)

    def freeze(self) -> None:
        """永久冻结时钟。冻结后只能读取,不能修改。"""
        with self._lock:
            self._frozen = True

    def unfreeze(self) -> None:
        """解除冻结(测试场景用,允许在某些 case 中重新启用)。"""
        with self._lock:
            self._frozen = False

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def is_frozen(self) -> bool:
        with self._lock:
            return self._frozen

    @property
    def initial(self) -> float:
        return self._initial

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"FrozenClock(initial={self._initial!r}, "
                f"now={self._now!r}, frozen={self._frozen!r})"
            )


# ============================================================
# MockClock
# ============================================================
class MockClock:
    """完全由调用方控制的时钟(注入式)。

    使用场景: 单元测试中希望 now() 每次返回不同值,
    或基于状态计算时间。

    特性:
    - 接受 callable: () -> float 作为 now_fn
    - 接受 callable: () -> float 作为 mono_fn(可选,默认同 now)
    - 完全线程安全(callable 自身需保证线程安全)
    """

    __slots__ = ("_now_fn", "_mono_fn", "_name")

    def __init__(
        self,
        now_fn: Optional[Callable[[], float]] = None,
        monotonic_fn: Optional[Callable[[], float]] = None,
        name: str = "mock",
    ) -> None:
        if now_fn is None:
            now_fn = lambda: 0.0
        if monotonic_fn is None:
            monotonic_fn = now_fn
        self._now_fn = now_fn
        self._mono_fn = monotonic_fn
        self._name = str(name)

    def now(self) -> float:
        try:
            return float(self._now_fn() or 0.0)
        except Exception:
            return 0.0

    def monotonic(self) -> float:
        try:
            return float(self._mono_fn() or 0.0)
        except Exception:
            return 0.0

    @property
    def name(self) -> str:
        return self._name

    def __repr__(self) -> str:
        return f"MockClock(name={self._name!r})"


__all__ = [
    "Clock",
    "SystemClock",
    "FrozenClock",
    "MockClock",
    "ClockError",
    "FrozenClockError",
]
