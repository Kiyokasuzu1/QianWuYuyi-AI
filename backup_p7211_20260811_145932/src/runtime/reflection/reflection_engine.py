# -*- coding: utf-8 -*-
"""
src/runtime/reflection/reflection_engine.py

Phase 5.0-D3-B: Reflection Engine —— 反思编排核心。

职责:
- 编排多个策略(Daily / Event / Growth)
- 决定运行哪个 / 哪些策略
- 收集 Insight / Suggestion
- 写入 ReflectionHistory
- 确定性: 相同输入 → 相同输出
- 异常隔离
- 通过 Clock 注入获取时间

约束:
- 不调用 LLM / DB / Network
- 仅依赖 Python 标准库 + Runtime 内部模块
- 线程安全
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.lifecycle.internal.clock import (
    Clock,
    SystemClock,
)
from src.runtime.reflection.daily_reflection import DailyReflection
from src.runtime.reflection.event_reflection import EventReflection
from src.runtime.reflection.growth_reflection import GrowthReflection
from src.runtime.reflection.reflection_history import (
    DEFAULT_REFLECTION_HISTORY_CAPACITY,
    ReflectionHistory,
    ReflectionRecord,
)
from src.runtime.reflection.reflection_result import (
    ALL_REFLECTION_TYPES,
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    ReflectionResult,
    StateChangeSuggestion,
)
from src.runtime.reflection.reflection_strategy import (
    ReflectionContext,
    ReflectionStrategy,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
REFLECTION_ENGINE_SCHEMA_VERSION = "1.0"

DEFAULT_DAILY_WINDOW_SECONDS = 24 * 60 * 60
DEFAULT_GROWTH_WINDOW_SECONDS = 7 * 24 * 60 * 60


# ============================================================
# 异常
# ============================================================
class ReflectionEngineError(Exception):
    """ReflectionEngine 错误基类。"""


# ============================================================
# ReflectionEngine
# ============================================================
class ReflectionEngine:
    """反思引擎。

    设计:
    - 持有 clock(注入)
    - 持有 strategies(默认: Daily / Event / Growth)
    - 持有 history(默认 128 容量)
    - 线程安全(RLock)
    - 异常隔离
    - 不修改任何 SelfModel 状态
    """

    DEFAULT_NAME = "reflection_engine"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        clock: Optional[Clock] = None,
        strategies: Optional[Sequence[ReflectionStrategy]] = None,
        history: Optional[ReflectionHistory] = None,
        history_capacity: int = DEFAULT_REFLECTION_HISTORY_CAPACITY,
        daily_window_seconds: float = DEFAULT_DAILY_WINDOW_SECONDS,
        growth_window_seconds: float = DEFAULT_GROWTH_WINDOW_SECONDS,
    ) -> None:
        self._name = str(name or self.DEFAULT_NAME)
        # clock
        if clock is None:
            clock = SystemClock()
        if not _is_clock_like(clock):
            raise ReflectionEngineError(
                f"clock 必须是 Clock 实例,实际: {type(clock).__name__}"
            )
        self._clock = clock
        # history
        if history is None:
            history = ReflectionHistory(
                name=f"{self._name}_history",
                capacity=history_capacity,
            )
        if not isinstance(history, ReflectionHistory):
            raise ReflectionEngineError(
                f"history 必须是 ReflectionHistory 实例,实际: {type(history).__name__}"
            )
        self._history = history
        # 窗口
        try:
            self._daily_window = float(daily_window_seconds)
        except Exception:
            self._daily_window = DEFAULT_DAILY_WINDOW_SECONDS
        if self._daily_window <= 0:
            self._daily_window = DEFAULT_DAILY_WINDOW_SECONDS
        try:
            self._growth_window = float(growth_window_seconds)
        except Exception:
            self._growth_window = DEFAULT_GROWTH_WINDOW_SECONDS
        if self._growth_window <= 0:
            self._growth_window = DEFAULT_GROWTH_WINDOW_SECONDS
        # 策略
        self._lock = threading.RLock()
        if strategies is None:
            strategies = [
                DailyReflection(window_seconds=self._daily_window),
                EventReflection(),
                GrowthReflection(window_seconds=self._growth_window),
            ]
        clean_strategies: List[ReflectionStrategy] = []
        for s in strategies:
            if s is None:
                continue
            if not hasattr(s, "reflect") or not callable(getattr(s, "reflect", None)):
                continue
            clean_strategies.append(s)
        self._strategies: List[ReflectionStrategy] = clean_strategies
        # 统计
        self._total_runs = 0
        self._total_results = 0
        self._total_errors = 0
        self._last_result: Optional[ReflectionResult] = None
        self._last_at: float = 0.0
        self._last_error: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def history(self) -> ReflectionHistory:
        return self._history

    @property
    def strategies(self) -> List[ReflectionStrategy]:
        with self._lock:
            return list(self._strategies)

    @property
    def total_runs(self) -> int:
        with self._lock:
            return self._total_runs

    @property
    def total_results(self) -> int:
        with self._lock:
            return self._total_results

    @property
    def total_errors(self) -> int:
        with self._lock:
            return self._total_errors

    @property
    def last_result(self) -> Optional[ReflectionResult]:
        with self._lock:
            return self._last_result

    @property
    def last_at(self) -> float:
        with self._lock:
            return self._last_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return REFLECTION_ENGINE_SCHEMA_VERSION

    @property
    def daily_window_seconds(self) -> float:
        return self._daily_window

    @property
    def growth_window_seconds(self) -> float:
        return self._growth_window

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def add_strategy(self, strategy: ReflectionStrategy) -> bool:
        if strategy is None:
            return False
        if not hasattr(strategy, "reflect") or not callable(getattr(strategy, "reflect", None)):
            return False
        with self._lock:
            self._strategies.append(strategy)
        return True

    def remove_strategy(self, name: str) -> bool:
        target = str(name or "")
        if not target:
            return False
        with self._lock:
            for i, s in enumerate(list(self._strategies)):
                if getattr(s, "name", "") == target:
                    del self._strategies[i]
                    return True
        return False

    def set_clock(self, clock: Clock) -> bool:
        if not _is_clock_like(clock):
            return False
        self._clock = clock
        return True

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def evaluate(self, events: Sequence[IntegrationEvent]) -> List[str]:
        """根据事件评估应触发的策略类型。

        返回应运行的策略名列表(daily / event / growth)。
        """
        chosen: List[str] = []
        if events is None:
            return chosen
        # event 数量 = 0: 全部 skip
        n = sum(1 for e in events if isinstance(e, IntegrationEvent))
        if n == 0:
            return chosen
        # 至少一个事件 → event strategy 可触发
        if any(getattr(s, "reflection_type", "") == REFLECTION_TYPE_EVENT for s in self._strategies):
            chosen.append(REFLECTION_TYPE_EVENT)
        # 时间窗口分析
        if events:
            timestamps = [float(e.timestamp or 0.0) for e in events if isinstance(e, IntegrationEvent)]
            if timestamps:
                window = max(timestamps) - min(timestamps)
                if window >= self._daily_window and any(
                    getattr(s, "reflection_type", "") == REFLECTION_TYPE_DAILY
                    for s in self._strategies
                ):
                    chosen.append(REFLECTION_TYPE_DAILY)
                if window >= self._growth_window and any(
                    getattr(s, "reflection_type", "") == REFLECTION_TYPE_GROWTH
                    for s in self._strategies
                ):
                    chosen.append(REFLECTION_TYPE_GROWTH)
        return chosen

    def should_reflect(
        self,
        events: Sequence[IntegrationEvent],
    ) -> bool:
        """是否有满足条件的策略可运行。"""
        if events is None:
            return False
        n = sum(1 for e in events if isinstance(e, IntegrationEvent))
        if n == 0:
            return False
        for s in self._strategies:
            try:
                ctx = self._build_context(events, strategy=s)
                if s.should_run(ctx):
                    return True
            except Exception:
                continue
        return False

    def reflect(
        self,
        events: Sequence[IntegrationEvent],
        *,
        reflection_type: Optional[str] = None,
    ) -> List[ReflectionResult]:
        """运行所有(或指定)策略,返回结果列表。

        确定性: 相同 events + clock + strategies → 相同结果集。
        """
        if events is None:
            events = []
        # 过滤合法事件
        clean_events: List[IntegrationEvent] = [
            e for e in events if isinstance(e, IntegrationEvent)
        ]
        now = self._clock.now()
        with self._lock:
            self._total_runs += 1
            self._last_at = float(now)

        results: List[ReflectionResult] = []
        for strategy in self._strategies:
            rt = getattr(strategy, "reflection_type", "")
            if reflection_type is not None and rt != reflection_type:
                continue
            try:
                ctx = self._build_context(clean_events, strategy=strategy)
                if not strategy.should_run(ctx):
                    continue
                result = strategy.reflect(ctx)
                if not isinstance(result, ReflectionResult):
                    continue
                # 写入历史
                try:
                    self._history.append_result(result, timestamp=now)
                except Exception:
                    pass
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._total_errors += 1
                    self._last_error = f"{getattr(strategy, 'name', '?')}: {exc}"
                logger.warning(
                    "ReflectionEngine(%s) strategy %s 失败(已隔离): %s",
                    self._name, getattr(strategy, "name", "?"), exc,
                )
        with self._lock:
            self._total_results += len(results)
            if results:
                self._last_result = results[-1]
        return results

    def reflect_one(
        self,
        strategy: ReflectionStrategy,
        events: Sequence[IntegrationEvent],
    ) -> Optional[ReflectionResult]:
        """运行指定单个策略,返回结果(或 None)。"""
        if strategy is None or not isinstance(strategy, object):
            return None
        if not hasattr(strategy, "reflect") or not callable(getattr(strategy, "reflect", None)):
            return None
        clean_events: List[IntegrationEvent] = [
            e for e in (events or []) if isinstance(e, IntegrationEvent)
        ]
        try:
            ctx = self._build_context(clean_events, strategy=strategy)
            if not strategy.should_run(ctx):
                return None
            result = strategy.reflect(ctx)
            if not isinstance(result, ReflectionResult):
                return None
            try:
                self._history.append_result(result, timestamp=self._clock.now())
            except Exception:
                pass
            with self._lock:
                self._total_results += 1
                self._last_result = result
            return result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._total_errors += 1
                self._last_error = f"{getattr(strategy, 'name', '?')}: {exc}"
            return None

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _build_context(
        self,
        events: List[IntegrationEvent],
        *,
        strategy: ReflectionStrategy,
    ) -> ReflectionContext:
        """根据策略构造 ReflectionContext。"""
        if not events:
            now = self._clock.now()
            return ReflectionContext(
                clock_now=now,
                window_start=now,
                window_end=now,
                events=[],
                metadata={"strategy": getattr(strategy, "name", "")},
            )
        try:
            timestamps = [float(e.timestamp or 0.0) for e in events]
        except Exception:
            timestamps = []
        if timestamps:
            ws = min(timestamps)
            we = max(timestamps)
        else:
            ws = we = self._clock.now()
        now = self._clock.now()
        return ReflectionContext(
            clock_now=now,
            window_start=ws,
            window_end=we,
            events=list(events),
            metadata={"strategy": getattr(strategy, "name", "")},
        )

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "schema_version": REFLECTION_ENGINE_SCHEMA_VERSION,
                "clock_name": getattr(self._clock, "name", "?"),
                "strategy_count": len(self._strategies),
                "strategies": [getattr(s, "name", "?") for s in self._strategies],
                "history_size": self._history.size,
                "history_capacity": self._history.capacity,
                "total_runs": self._total_runs,
                "total_results": self._total_results,
                "total_errors": self._total_errors,
                "last_at": self._last_at,
                "last_error": self._last_error,
                "last_result_id": (
                    self._last_result.reflection_id if self._last_result else ""
                ),
                "daily_window_seconds": self._daily_window,
                "growth_window_seconds": self._growth_window,
            }

    def __repr__(self) -> str:
        return (
            f"ReflectionEngine(name={self._name!r}, "
            f"strategies={len(self._strategies)}, "
            f"history={self._history.size}/{self._history.capacity})"
        )


# ============================================================
# 辅助
# ============================================================
def _is_clock_like(obj: Any) -> bool:
    if obj is None:
        return False
    return callable(getattr(obj, "now", None)) and callable(getattr(obj, "monotonic", None))


# ============================================================
# 工厂
# ============================================================
def build_default_reflection_engine(
    *,
    clock: Optional[Clock] = None,
    history_capacity: int = DEFAULT_REFLECTION_HISTORY_CAPACITY,
    daily_window_seconds: float = DEFAULT_DAILY_WINDOW_SECONDS,
    growth_window_seconds: float = DEFAULT_GROWTH_WINDOW_SECONDS,
) -> ReflectionEngine:
    """构造默认 ReflectionEngine。"""
    return ReflectionEngine(
        clock=clock,
        history_capacity=history_capacity,
        daily_window_seconds=daily_window_seconds,
        growth_window_seconds=growth_window_seconds,
    )


__all__ = [
    # 常量
    "REFLECTION_ENGINE_SCHEMA_VERSION",
    "DEFAULT_DAILY_WINDOW_SECONDS",
    "DEFAULT_GROWTH_WINDOW_SECONDS",
    # 异常
    "ReflectionEngineError",
    # 类
    "ReflectionEngine",
    # 工厂
    "build_default_reflection_engine",
]
