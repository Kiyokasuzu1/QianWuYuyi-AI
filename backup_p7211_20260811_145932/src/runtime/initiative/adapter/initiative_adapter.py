# -*- coding: utf-8 -*-
"""
src/runtime/initiative/adapter/initiative_adapter.py

Phase 5.0-D3-C: InitiativeAdapter。

职责:
- 桥接 IntegrationEvent <-> InitiativeEngine
- 接收 Reflection 事件 / Experience 事件
- 触发 tick
- 通过 InitiativeEventEmitter 发射 IntegrationEvent
- 不直接读取业务模块

约束:
- 不依赖业务模块(memory/growth/personality/emotion/relationship)
- 仅依赖 Runtime 内部模块(integration / reflection / initiative / lifecycle)
- 异常隔离
- enable_initiative = False 短路
"""
from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional, Sequence

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    INTEGRATION_REFLECTION_GROWTH_COMPLETED,
    IntegrationEvent,
)
from src.runtime.initiative.adapter.initiative_event_emitter import (
    DEFAULT_INITIATIVE_EMITTER_OWNER,
    InitiativeEventEmitter,
)
from src.runtime.initiative.initiative_engine import (
    INITIATIVE_ENGINE_SCHEMA_VERSION,
    InitiativeEngine,
    InitiativeTickOutput,
)
from src.runtime.initiative.possible_action import (
    ACTION_STATUS_PENDING,
)
from src.runtime.lifecycle.internal.clock import Clock, SystemClock


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
INITIATIVE_ADAPTER_SCHEMA_VERSION = "1.0"

DEFAULT_INITIATIVE_ADAPTER_OWNER = DEFAULT_INITIATIVE_EMITTER_OWNER  # "initiative"
DEFAULT_MAX_EVENTS = 256
DEFAULT_MAX_REFLECTIONS = 64


# ============================================================
# InitiativeAdapter
# ============================================================
class InitiativeAdapter(BaseAdapter):
    """InitiativeEngine 与 IntegrationLayer 的桥接。"""

    def __init__(
        self,
        *,
        name: str = "initiative_adapter",
        owner: str = DEFAULT_INITIATIVE_ADAPTER_OWNER,
        engine: Optional[InitiativeEngine] = None,
        emitter: Optional[InitiativeEventEmitter] = None,
        clock: Optional[Clock] = None,
        max_events: Optional[int] = None,
        max_reflections: Optional[int] = None,
    ) -> None:
        self._engine = engine if isinstance(engine, InitiativeEngine) else InitiativeEngine()
        self._emitter = emitter if isinstance(emitter, InitiativeEventEmitter) else InitiativeEventEmitter()
        self._clock: Clock = clock or SystemClock()
        # 限制
        try:
            self._max_events = int(max_events) if max_events is not None else DEFAULT_MAX_EVENTS
        except Exception:
            self._max_events = DEFAULT_MAX_EVENTS
        if self._max_events < 0:
            self._max_events = 0
        try:
            self._max_reflections = int(max_reflections) if max_reflections is not None else DEFAULT_MAX_REFLECTIONS
        except Exception:
            self._max_reflections = DEFAULT_MAX_REFLECTIONS
        if self._max_reflections < 0:
            self._max_reflections = 0
        # 注入 emitter 的 owner 到 adapter owner(保持一致)
        super().__init__(
            name=str(name or "initiative_adapter"),
            owner=str(owner or DEFAULT_INITIATIVE_ADAPTER_OWNER),
        )
        # pending
        self._pending_events: list = []
        self._pending_reflections: list = []
        # 统计
        self._tick_count = 0
        self._signals_emitted = 0
        self._actions_emitted = 0
        self._last_tick_output: Optional[InitiativeTickOutput] = None
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def engine(self) -> InitiativeEngine:
        return self._engine

    @property
    def emitter(self) -> InitiativeEventEmitter:
        return self._emitter

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def tick_count(self) -> int:
        with self._lock:
            return self._tick_count

    @property
    def signals_emitted(self) -> int:
        with self._lock:
            return self._signals_emitted

    @property
    def actions_emitted(self) -> int:
        with self._lock:
            return self._actions_emitted

    @property
    def last_tick_output(self) -> Optional[InitiativeTickOutput]:
        with self._lock:
            return self._last_tick_output

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return INITIATIVE_ADAPTER_SCHEMA_VERSION

    @property
    def enabled(self) -> bool:
        return self._engine.enabled

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        self._engine.set_enabled(enabled)

    # --------------------------------------------------------
    # 事件输入
    # --------------------------------------------------------
    def push_events(self, events: Sequence[IntegrationEvent]) -> int:
        if events is None:
            return 0
        n = 0
        for e in events:
            if isinstance(e, IntegrationEvent):
                if self._max_events > 0 and len(self._pending_events) >= self._max_events:
                    # 简单淘汰最早
                    try:
                        self._pending_events.pop(0)
                    except Exception:
                        self._pending_events = self._pending_events[-self._max_events:]
                self._pending_events.append(e)
                n += 1
        return n

    def push_reflections(self, reflections: Sequence[Any]) -> int:
        if reflections is None:
            return 0
        # 避免循环 import:用 duck-typing,不强制类型
        from src.runtime.reflection.reflection_result import ReflectionResult
        n = 0
        for r in reflections:
            if isinstance(r, ReflectionResult):
                if self._max_reflections > 0 and len(self._pending_reflections) >= self._max_reflections:
                    try:
                        self._pending_reflections.pop(0)
                    except Exception:
                        self._pending_reflections = self._pending_reflections[-self._max_reflections:]
                self._pending_reflections.append(r)
                n += 1
        return n

    def clear_pending(self) -> int:
        with self._lock:
            ne = len(self._pending_events)
            nr = len(self._pending_reflections)
            self._pending_events = []
            self._pending_reflections = []
        return ne + nr

    @property
    def pending_event_count(self) -> int:
        with self._lock:
            return len(self._pending_events)

    @property
    def pending_reflection_count(self) -> int:
        with self._lock:
            return len(self._pending_reflections)

    # --------------------------------------------------------
    # BaseAdapter 接口
    # --------------------------------------------------------
    def is_available(self) -> bool:
        """Adapter 可用:engine 存在。"""
        return isinstance(self._engine, InitiativeEngine)

    def get_target(self) -> Any:
        return self._engine

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------
    def evaluate(
        self,
        events: Optional[Sequence[IntegrationEvent]] = None,
        reflections: Optional[Sequence[Any]] = None,
    ) -> bool:
        """评估是否值得执行 tick。

        规则:
        - engine 关闭 -> False
        - 没有任何 pending 且外部未提供输入 -> False
        """
        if not self._engine.enabled:
            return False
        with self._lock:
            has_pending = bool(self._pending_events) or bool(self._pending_reflections)
        has_input = bool(events) or bool(reflections)
        if not has_pending and not has_input:
            return False
        return True

    def tick(
        self,
        events: Optional[Sequence[IntegrationEvent]] = None,
        reflections: Optional[Sequence[Any]] = None,
        *,
        now: Optional[float] = None,
    ) -> Optional[InitiativeTickOutput]:
        """执行一次 tick。

        流程:
        1) drain pending
        2) 合并传入 events / reflections
        3) engine.tick(...)
        4) 发射 IntegrationEvent
        5) 统计
        """
        try:
            with self._lock:
                self._tick_count += 1
            # drain
            pending_evs = list(self._pending_events)
            pending_refs = list(self._pending_reflections)
            with self._lock:
                self._pending_events = []
                self._pending_reflections = []
            merged_events: List[IntegrationEvent] = list(pending_evs)
            if events:
                merged_events.extend(list(events))
            merged_refs = list(pending_refs)
            if reflections:
                merged_refs.extend(list(reflections))
            try:
                if now is not None:
                    now_f = float(now)
                else:
                    now_f = self._safe_now()
            except Exception:
                now_f = self._safe_now()
            output = self._engine.tick(
                events=merged_events,
                reflections=merged_refs,
                now=now_f,
            )
            # 发射
            sig_events = self._emitter.emit_signals(output.created_signals)
            pending_actions = [a for a in output.created_actions if a.status == ACTION_STATUS_PENDING]
            other_actions = [a for a in output.created_actions if a.status != ACTION_STATUS_PENDING]
            action_events = self._emitter.emit_actions(pending_actions + other_actions)
            with self._lock:
                self._signals_emitted += len(sig_events)
                self._actions_emitted += len(action_events)
                self._last_tick_output = output
            for ev in sig_events + action_events:
                try:
                    self.emit(ev)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._last_error = f"emit 失败: {exc}"
            return output
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"tick 异常: {exc}"
            logger.warning("InitiativeAdapter.tick 失败: %s", exc)
            return None

    # --------------------------------------------------------
    # 便利
    # --------------------------------------------------------
    def drain_and_tick(self) -> Optional[InitiativeTickOutput]:
        """只使用 pending 数据 tick。"""
        return self.tick()

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "tick_count": self.tick_count,
            "signals_emitted": self.signals_emitted,
            "actions_emitted": self.actions_emitted,
            "pending_event_count": self.pending_event_count,
            "pending_reflection_count": self.pending_reflection_count,
            "engine": self._engine.describe(),
            "emitter": self._emitter.describe(),
            "last_error": self.last_error,
        })
        return d

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InitiativeAdapter(name={self._name!r}, "
                f"enabled={self.enabled}, ticks={self._tick_count}, "
                f"signals={self._signals_emitted}, actions={self._actions_emitted})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_initiative_adapter() -> InitiativeAdapter:
    return InitiativeAdapter()


# 重新导出 constant 以便统一引用
__all__ = [
    "INITIATIVE_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_INITIATIVE_ADAPTER_OWNER",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_REFLECTIONS",
    "InitiativeAdapter",
    "build_default_initiative_adapter",
]
