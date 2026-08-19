# -*- coding: utf-8 -*-
"""
src/runtime/reflection/reflection_strategy.py

Phase 5.0-D3-B: Reflection 策略基类。

职责:
- 定义 ReflectionStrategy 协议
- 定义 ReflectionContext(策略运行环境)
- 不实现具体策略,具体由 daily/event/growth 子类实现

约束:
- 不依赖业务模块
- 仅依赖 Python 标准库
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.reflection.reflection_result import ReflectionResult


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
REFLECTION_STRATEGY_SCHEMA_VERSION = "1.0"


# ============================================================
# ReflectionContext
# ============================================================
class ReflectionContext:
    """反思策略的运行环境。

    字段:
    - clock_now:        float         # 当前时间(已注入)
    - window_start:     float         # 窗口起点
    - window_end:       float         # 窗口终点
    - events:           List[IntegrationEvent]  # 窗口内事件
    - metadata:         Dict[str, Any]
    """

    def __init__(
        self,
        *,
        clock_now: float = 0.0,
        window_start: float = 0.0,
        window_end: float = 0.0,
        events: Optional[List[IntegrationEvent]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self._clock_now = float(clock_now)
        except Exception:
            self._clock_now = 0.0
        try:
            self._window_start = float(window_start)
        except Exception:
            self._window_start = self._clock_now
        try:
            self._window_end = float(window_end)
        except Exception:
            self._window_end = self._clock_now
        if not isinstance(events, list):
            try:
                self._events: List[IntegrationEvent] = list(events) if events else []
            except Exception:
                self._events = []
        else:
            self._events = [e for e in events if isinstance(e, IntegrationEvent)]
        if not isinstance(metadata, dict):
            try:
                self._metadata = dict(metadata) if metadata else {}
            except Exception:
                self._metadata = {}
        else:
            self._metadata = dict(metadata)
        if len(self._metadata) > 32:
            keys = list(self._metadata.keys())[:32]
            self._metadata = {k: self._metadata[k] for k in keys}

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def clock_now(self) -> float:
        return self._clock_now

    @property
    def window_start(self) -> float:
        return self._window_start

    @property
    def window_end(self) -> float:
        return self._window_end

    @property
    def events(self) -> List[IntegrationEvent]:
        return list(self._events)

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self._metadata)

    @property
    def event_count(self) -> int:
        return len(self._events)

    def event_ids(self) -> List[str]:
        return [e.event_id for e in self._events if e is not None]

    def window_seconds(self) -> float:
        try:
            return max(0.0, float(self._window_end) - float(self._window_start))
        except Exception:
            return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "clock_now": self._clock_now,
            "window_start": self._window_start,
            "window_end": self._window_end,
            "event_count": self.event_count,
            "window_seconds": self.window_seconds(),
            "metadata": dict(self._metadata),
        }

    def __repr__(self) -> str:
        return (
            f"ReflectionContext(now={self._clock_now}, "
            f"events={self.event_count}, "
            f"window=[{self._window_start}, {self._window_end}])"
        )


# ============================================================
# ReflectionStrategy Protocol
# ============================================================
@runtime_checkable
class ReflectionStrategy(Protocol):
    """反思策略协议(duck-typed)。

    所有具体策略必须实现:
    - name:            str
    - reflection_type: str
    - should_run(ctx) -> bool
    - reflect(ctx) -> ReflectionResult
    """

    @property
    def name(self) -> str: ...

    @property
    def reflection_type(self) -> str: ...

    def should_run(self, context: ReflectionContext) -> bool: ...

    def reflect(self, context: ReflectionContext) -> ReflectionResult: ...


# ============================================================
# BaseReflectionStrategy
# ============================================================
class BaseReflectionStrategy:
    """反思策略基类(默认空实现,子类可覆盖)。

    提供:
    - 名称 / 类型字段
    - 通用 should_run 规则
    - 通用 reflect 包装
    """

    DEFAULT_NAME = "base_strategy"
    DEFAULT_REFLECTION_TYPE = "daily"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        reflection_type: Optional[str] = None,
    ) -> None:
        self._name = str(name or self.DEFAULT_NAME)
        self._reflection_type = str(reflection_type or self.DEFAULT_REFLECTION_TYPE)

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def reflection_type(self) -> str:
        return self._reflection_type

    # --------------------------------------------------------
    # 决策
    # --------------------------------------------------------
    def should_run(self, context: ReflectionContext) -> bool:
        """默认: 至少 1 个事件才运行。"""
        if not isinstance(context, ReflectionContext):
            return False
        return context.event_count > 0

    # --------------------------------------------------------
    # 反思
    # --------------------------------------------------------
    def reflect(self, context: ReflectionContext) -> ReflectionResult:
        """子类必须实现。"""
        raise NotImplementedError(
            f"{type(self).__name__}.reflect() 未实现"
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "reflection_type": self._reflection_type,
            "schema_version": REFLECTION_STRATEGY_SCHEMA_VERSION,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self._name!r}, "
            f"type={self._reflection_type!r})"
        )


__all__ = [
    # 常量
    "REFLECTION_STRATEGY_SCHEMA_VERSION",
    # 类
    "ReflectionContext",
    "ReflectionStrategy",
    "BaseReflectionStrategy",
]
