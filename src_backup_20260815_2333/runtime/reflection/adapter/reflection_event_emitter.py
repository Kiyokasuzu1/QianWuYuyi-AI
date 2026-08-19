# -*- coding: utf-8 -*-
"""
src/runtime/reflection/adapter/reflection_event_emitter.py

Phase 5.0-D3-B: 反思事件发射器。

职责:
- 把 ReflectionResult 转换为 IntegrationEvent
- 提供按类型发射的方法
- 不调用 LLM / DB / Network
- 不直接修改 SelfModel

约束:
- 仅依赖 Python 标准库 + Runtime 内部模块
- 时间全部由 Clock 注入
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.lifecycle.internal.clock import (
    Clock,
    SystemClock,
)
from src.runtime.reflection.reflection_result import (
    ALL_REFLECTION_TYPES,
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    ReflectionResult,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
REFLECTION_EVENT_EMITTER_SCHEMA_VERSION = "1.0"

# 事件类型(与 integration_event.py 保持一致)
EVENT_TYPE_REFLECTION_COMPLETED = "integration.reflection.completed"
EVENT_TYPE_REFLECTION_DAILY_COMPLETED = "integration.reflection.daily_completed"
EVENT_TYPE_REFLECTION_EVENT_COMPLETED = "integration.reflection.event_completed"
EVENT_TYPE_REFLECTION_GROWTH_COMPLETED = "integration.reflection.growth_completed"

ALL_REFLECTION_EVENT_TYPES = (
    EVENT_TYPE_REFLECTION_COMPLETED,
    EVENT_TYPE_REFLECTION_DAILY_COMPLETED,
    EVENT_TYPE_REFLECTION_EVENT_COMPLETED,
    EVENT_TYPE_REFLECTION_GROWTH_COMPLETED,
)

# Reflection type -> event type 映射
REFLECTION_TYPE_TO_EVENT_TYPE: Dict[str, str] = {
    REFLECTION_TYPE_DAILY: EVENT_TYPE_REFLECTION_DAILY_COMPLETED,
    REFLECTION_TYPE_EVENT: EVENT_TYPE_REFLECTION_EVENT_COMPLETED,
    REFLECTION_TYPE_GROWTH: EVENT_TYPE_REFLECTION_GROWTH_COMPLETED,
}


def _is_clock_like(obj: Any) -> bool:
    if obj is None:
        return False
    return callable(getattr(obj, "now", None)) and callable(getattr(obj, "monotonic", None))


# ============================================================
# ReflectionEventEmitter
# ============================================================
class ReflectionEventEmitter:
    """反思事件发射器。

    负责把 ReflectionResult 转换为 IntegrationEvent。
    任何业务模块(尤其 SelfModel Adapter)可通过订阅
    REFLECTION_*_COMPLETED 事件来接收反思结果。
    """

    DEFAULT_NAME = "reflection_event_emitter"
    DEFAULT_OWNER = "reflection"
    DEFAULT_SOURCE = "reflection_engine"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        owner: Optional[str] = None,
        clock: Optional[Clock] = None,
        event_emitter: Optional[Any] = None,
    ) -> None:
        self._name = str(name or self.DEFAULT_NAME)
        self._owner = str(owner or self.DEFAULT_OWNER)
        if clock is None:
            clock = SystemClock()
        if not _is_clock_like(clock):
            clock = SystemClock()
        self._clock = clock
        self._event_emitter = event_emitter
        self._lock = threading.RLock()
        self._emitted_count = 0
        self._last_event_id: str = ""
        self._last_reflection_id: str = ""
        self._last_error: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def schema_version(self) -> str:
        return REFLECTION_EVENT_EMITTER_SCHEMA_VERSION

    @property
    def emitted_count(self) -> int:
        with self._lock:
            return self._emitted_count

    @property
    def last_event_id(self) -> str:
        with self._lock:
            return self._last_event_id

    @property
    def last_reflection_id(self) -> str:
        with self._lock:
            return self._last_reflection_id

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 发射
    # --------------------------------------------------------
    def emit(self, result: ReflectionResult) -> List[IntegrationEvent]:
        """把一个 ReflectionResult 发射为 IntegrationEvent 列表。

        - 通用事件: REFLECTION_COMPLETED
        - 类型事件: REFLECTION_<TYPE>_COMPLETED
        """
        if not isinstance(result, ReflectionResult):
            return []
        with self._lock:
            self._last_reflection_id = result.reflection_id
        # 1) 类型特定事件
        specific = self._emit_specific(result)
        # 2) 通用事件
        general = self._emit_general(result)
        out: List[IntegrationEvent] = []
        if isinstance(specific, IntegrationEvent):
            out.append(specific)
        if isinstance(general, IntegrationEvent):
            out.append(general)
        return out

    def emit_many(self, results: List[ReflectionResult]) -> List[IntegrationEvent]:
        """批量发射。"""
        if not results:
            return []
        out: List[IntegrationEvent] = []
        for r in results:
            if not isinstance(r, ReflectionResult):
                continue
            out.extend(self.emit(r))
        return out

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _build_payload(self, result: ReflectionResult) -> Dict[str, Any]:
        return {
            "reflection_id": result.reflection_id,
            "reflection_type": result.reflection_type,
            "source_event_ids": list(result.source_event_ids),
            "insight_count": result.insight_count,
            "suggestion_count": result.suggestion_count,
            "confidence": float(result.confidence),
            "evidence_strength": float(result.evidence_strength),
            "window_start": float(result.window_start),
            "window_end": float(result.window_end),
            "triggered_at": float(result.triggered_at),
        }

    def _emit_specific(self, result: ReflectionResult) -> Optional[IntegrationEvent]:
        et = REFLECTION_TYPE_TO_EVENT_TYPE.get(result.reflection_type)
        if not et:
            return None
        payload = self._build_payload(result)
        return self._do_emit(
            et,
            payload,
            related_ids=[result.reflection_id] + list(result.source_event_ids),
        )

    def _emit_general(self, result: ReflectionResult) -> Optional[IntegrationEvent]:
        payload = self._build_payload(result)
        return self._do_emit(
            EVENT_TYPE_REFLECTION_COMPLETED,
            payload,
            related_ids=[result.reflection_id] + list(result.source_event_ids),
        )

    def _do_emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        related_ids: List[str],
    ) -> Optional[IntegrationEvent]:
        ev = make_integration_event(
            event_type=event_type,
            source=self._owner,
            payload=payload,
            related_ids=related_ids,
        )
        # 投递
        try:
            self._dispatch(ev)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"dispatch 失败: {exc}"
            logger.warning(
                "ReflectionEventEmitter(%s) dispatch 失败(已隔离): %s",
                self._name, exc,
            )
        with self._lock:
            self._emitted_count += 1
            self._last_event_id = ev.event_id
        return ev

    def _dispatch(self, event: IntegrationEvent) -> None:
        """实际投递。"""
        if not isinstance(event, IntegrationEvent):
            return
        emitter = self._event_emitter
        if emitter is None:
            # 无外部 emitter: 仅记录统计(不抛错)
            return
        try:
            emitter(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ReflectionEventEmitter(%s) external emitter failed: %s",
                self._name, exc,
            )

    def set_event_emitter(self, emitter: Optional[Any]) -> None:
        self._event_emitter = emitter

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "owner": self._owner,
                "schema_version": self.schema_version,
                "emitted_count": self._emitted_count,
                "last_event_id": self._last_event_id,
                "last_reflection_id": self._last_reflection_id,
                "last_error": self._last_error,
                "clock_name": getattr(self._clock, "name", "?"),
                "has_external_emitter": self._event_emitter is not None,
            }

    def __repr__(self) -> str:
        return (
            f"ReflectionEventEmitter(name={self._name!r}, "
            f"emitted={self._emitted_count})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_reflection_event_emitter(
    *,
    clock: Optional[Clock] = None,
    event_emitter: Optional[Any] = None,
) -> ReflectionEventEmitter:
    """构造默认 ReflectionEventEmitter。"""
    return ReflectionEventEmitter(clock=clock, event_emitter=event_emitter)


__all__ = [
    "REFLECTION_EVENT_EMITTER_SCHEMA_VERSION",
    "EVENT_TYPE_REFLECTION_COMPLETED",
    "EVENT_TYPE_REFLECTION_DAILY_COMPLETED",
    "EVENT_TYPE_REFLECTION_EVENT_COMPLETED",
    "EVENT_TYPE_REFLECTION_GROWTH_COMPLETED",
    "ALL_REFLECTION_EVENT_TYPES",
    "REFLECTION_TYPE_TO_EVENT_TYPE",
    "ReflectionEventEmitter",
    "build_default_reflection_event_emitter",
]
