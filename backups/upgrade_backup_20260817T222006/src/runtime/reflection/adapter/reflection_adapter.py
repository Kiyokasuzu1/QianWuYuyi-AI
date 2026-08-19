# -*- coding: utf-8 -*-
"""
src/runtime/reflection/adapter/reflection_adapter.py

Phase 5.0-D3-B: Reflection Adapter —— IntegrationEvent ↔ ReflectionEngine 桥接。

职责:
- 接收 IntegrationEvent(任意类型)
- 通过 evaluate() 判断是否触发反思
- 通过 reflect() 调用 ReflectionEngine
- 通过 emit_result() 输出 REFLECTION_COMPLETED 事件
- 不直接修改 SelfModel
- 不调用业务模块

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 仅依赖 Python 标准库 + Runtime 内部模块
- 线程安全
- 异常隔离
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.lifecycle.internal.clock import (
    Clock,
    SystemClock,
)
from src.runtime.reflection.adapter.reflection_event_emitter import (
    EVENT_TYPE_REFLECTION_COMPLETED,
    EVENT_TYPE_REFLECTION_DAILY_COMPLETED,
    EVENT_TYPE_REFLECTION_EVENT_COMPLETED,
    EVENT_TYPE_REFLECTION_GROWTH_COMPLETED,
    ReflectionEventEmitter,
)
from src.runtime.reflection.reflection_engine import (
    DEFAULT_DAILY_WINDOW_SECONDS,
    DEFAULT_GROWTH_WINDOW_SECONDS,
    ReflectionEngine,
    build_default_reflection_engine,
)
from src.runtime.reflection.reflection_result import (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    ReflectionResult,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
REFLECTION_ADAPTER_SCHEMA_VERSION = "1.0"
DEFAULT_REFLECTION_ADAPTER_OWNER = "reflection"


def _is_clock_like(obj: Any) -> bool:
    if obj is None:
        return False
    return callable(getattr(obj, "now", None)) and callable(getattr(obj, "monotonic", None))


# ============================================================
# ReflectionAdapter
# ============================================================
class ReflectionAdapter(BaseAdapter):
    """ReflectionAdapter。

    字段:
    - engine:    ReflectionEngine
    - emitter:   ReflectionEventEmitter
    - enabled:   bool
    - max_per_call: int  # 每次 reflect() 处理的最大事件数
    """

    def __init__(
        self,
        *,
        name: str = "reflection_adapter",
        owner: str = DEFAULT_REFLECTION_ADAPTER_OWNER,
        engine: Optional[ReflectionEngine] = None,
        emitter: Optional[ReflectionEventEmitter] = None,
        clock: Optional[Clock] = None,
        event_emitter: Optional[Any] = None,
        enabled: bool = True,
        max_per_call: int = 256,
    ) -> None:
        super().__init__(
            name=name,
            owner=owner,
            target_resolver=(lambda: engine),
            event_emitter=event_emitter,
        )
        # clock
        if clock is None:
            clock = SystemClock()
        if not _is_clock_like(clock):
            clock = SystemClock()
        self._clock = clock
        # engine
        if engine is None:
            engine = build_default_reflection_engine(clock=clock)
        self._engine: ReflectionEngine = engine
        # emitter
        if emitter is None:
            emitter = ReflectionEventEmitter(clock=clock, event_emitter=event_emitter)
        else:
            # 注入 clock
            try:
                emitter._clock = clock  # type: ignore[attr-defined]
            except Exception:
                pass
        self._emitter: ReflectionEventEmitter = emitter
        self._enabled: bool = bool(enabled)
        self._max_per_call: int = max(0, int(max_per_call or 0))
        # 统计
        self._lock = threading.RLock()
        self._events_consumed: int = 0
        self._events_rejected: int = 0
        self._reflection_runs: int = 0
        self._results_emitted: int = 0
        self._last_consumed_event_id: str = ""
        self._last_reflection_id: str = ""
        self._last_error: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def engine(self) -> ReflectionEngine:
        return self._engine

    @property
    def emitter(self) -> ReflectionEventEmitter:
        return self._emitter

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    @property
    def events_consumed(self) -> int:
        with self._lock:
            return self._events_consumed

    @property
    def events_rejected(self) -> int:
        with self._lock:
            return self._events_rejected

    @property
    def reflection_runs(self) -> int:
        with self._lock:
            return self._reflection_runs

    @property
    def results_emitted(self) -> int:
        with self._lock:
            return self._results_emitted

    @property
    def last_consumed_event_id(self) -> str:
        with self._lock:
            return self._last_consumed_event_id

    @property
    def last_reflection_id(self) -> str:
        with self._lock:
            return self._last_reflection_id

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return REFLECTION_ADAPTER_SCHEMA_VERSION

    # --------------------------------------------------------
    # 核心 API
    # --------------------------------------------------------
    def evaluate(self, events: Sequence[IntegrationEvent]) -> List[str]:
        """评估应触发的策略类型。"""
        if not self._enabled:
            return []
        try:
            return self._engine.evaluate(events or [])
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"evaluate 失败: {exc}"
            return []

    def reflect(
        self,
        events: Sequence[IntegrationEvent],
        *,
        reflection_type: Optional[str] = None,
    ) -> List[ReflectionResult]:
        """运行引擎,并把结果转换为 IntegrationEvent 发出。"""
        if not self._enabled:
            return []
        # 截断
        clean_events: List[IntegrationEvent] = [
            e for e in (events or []) if isinstance(e, IntegrationEvent)
        ]
        if self._max_per_call > 0 and len(clean_events) > self._max_per_call:
            clean_events = clean_events[-self._max_per_call:]
        try:
            results = self._engine.reflect(clean_events, reflection_type=reflection_type)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"reflect 失败: {exc}"
            return []
        # 发送事件
        for r in results:
            try:
                self.emit_result(r)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = f"emit_result 失败: {exc}"
        with self._lock:
            self._reflection_runs += 1
            if results:
                self._last_reflection_id = results[-1].reflection_id
        return results

    def emit_result(self, result: ReflectionResult) -> List[IntegrationEvent]:
        """把 ReflectionResult 转换为 IntegrationEvent 发出。"""
        if not isinstance(result, ReflectionResult):
            return []
        try:
            events = self._emitter.emit(result)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emitter.emit 失败: {exc}"
            return []
        # 投递: 通过 BaseAdapter.emit
        for ev in events:
            try:
                if isinstance(ev, IntegrationEvent):
                    self.emit(ev)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._last_error = f"BaseAdapter.emit 失败: {exc}"
        with self._lock:
            self._results_emitted += len(events)
        return events

    # --------------------------------------------------------
    # Adapter 协议
    # --------------------------------------------------------
    def handle_event(self, event: Any) -> Optional[ReflectionResult]:
        """处理单个 IntegrationEvent(用于 EventBridge 投递)。"""
        if not self._enabled:
            with self._lock:
                self._events_rejected += 1
            return None
        if not isinstance(event, IntegrationEvent):
            with self._lock:
                self._events_rejected += 1
            return None
        with self._lock:
            self._events_consumed += 1
            self._last_consumed_event_id = event.event_id
        # 单事件不足以触发 daily / growth,但可以触发 event 反思
        try:
            results = self._engine.reflect([event])
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"handle_event reflect 失败: {exc}"
            return None
        if not results:
            return None
        # 发出事件
        for r in results:
            try:
                self.emit_result(r)
            except Exception:
                pass
        with self._lock:
            self._reflection_runs += 1
            self._last_reflection_id = results[-1].reflection_id
        return results[-1]

    def handle_events(
        self,
        events: Sequence[Any],
    ) -> List[ReflectionResult]:
        """批量处理,返回 ReflectionResult 列表。"""
        if not self._enabled:
            return []
        if not events:
            return []
        try:
            return self.reflect(events)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"handle_events 失败: {exc}"
            return []

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    def is_available(self) -> bool:
        return self._enabled and self._engine is not None

    def get_target(self) -> Optional[Any]:
        return self._engine

    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d.update({
            "schema_version": REFLECTION_ADAPTER_SCHEMA_VERSION,
            "enabled": self._enabled,
            "engine_name": self._engine.name if self._engine else None,
            "emitter_name": self._emitter.name if self._emitter else None,
            "events_consumed": self._events_consumed,
            "events_rejected": self._events_rejected,
            "reflection_runs": self._reflection_runs,
            "results_emitted": self._results_emitted,
            "last_consumed_event_id": self._last_consumed_event_id,
            "last_reflection_id": self._last_reflection_id,
            "max_per_call": self._max_per_call,
        })
        return d

    def __repr__(self) -> str:
        return (
            f"ReflectionAdapter(name={self._name!r}, enabled={self._enabled}, "
            f"engine={self._engine.name if self._engine else None!r}, "
            f"runs={self._reflection_runs}, "
            f"emitted={self._results_emitted})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_reflection_adapter(
    *,
    clock: Optional[Clock] = None,
    event_emitter: Optional[Any] = None,
    engine: Optional[ReflectionEngine] = None,
    enabled: bool = True,
) -> ReflectionAdapter:
    """构造默认 ReflectionAdapter。"""
    return ReflectionAdapter(
        clock=clock,
        event_emitter=event_emitter,
        engine=engine,
        enabled=enabled,
    )


__all__ = [
    "REFLECTION_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_REFLECTION_ADAPTER_OWNER",
    "ReflectionAdapter",
    "build_default_reflection_adapter",
]
