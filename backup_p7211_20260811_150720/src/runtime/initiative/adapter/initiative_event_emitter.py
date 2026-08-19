# -*- coding: utf-8 -*-
"""
src/runtime/initiative/adapter/initiative_event_emitter.py

Phase 5.0-D3-C: 把 Initiative 产出转成 IntegrationEvent。

职责:
- InterestSignal -> INTEREST_SIGNAL_CREATED
- PossibleAction(pending/filtered/discarded/deferred) -> INITIATIVE_CREATED 或 INITIATIVE_FILTERED
- 严格只发射事件,不执行

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from src.runtime.integration.integration_event import (
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INITIATIVE_FILTERED,
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.initiative.interest_signal import InterestSignal
from src.runtime.initiative.possible_action import (
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    PossibleAction,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION = "1.0"

DEFAULT_INITIATIVE_EMITTER_OWNER = "initiative"
DEFAULT_EMITTER_SOURCE = "initiative_event_emitter"


# ============================================================
# InitiativeEventEmitter
# ============================================================
class InitiativeEventEmitter:
    """Initiative -> IntegrationEvent 转换器。"""

    def __init__(
        self,
        *,
        owner: str = DEFAULT_INITIATIVE_EMITTER_OWNER,
        source: str = DEFAULT_EMITTER_SOURCE,
    ) -> None:
        self._owner = str(owner or DEFAULT_INITIATIVE_EMITTER_OWNER)
        self._source = str(source or DEFAULT_EMITTER_SOURCE)
        self._lock = threading.RLock()
        # 统计
        self._emitted_count = 0
        self._emitted_signal = 0
        self._emitted_created = 0
        self._emitted_filtered = 0
        self._last_event_id = ""
        self._last_error = ""

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def source(self) -> str:
        return self._source

    @property
    def emitted_count(self) -> int:
        with self._lock:
            return self._emitted_count

    @property
    def emitted_signal_count(self) -> int:
        with self._lock:
            return self._emitted_signal

    @property
    def emitted_created_count(self) -> int:
        with self._lock:
            return self._emitted_created

    @property
    def emitted_filtered_count(self) -> int:
        with self._lock:
            return self._emitted_filtered

    @property
    def last_event_id(self) -> str:
        with self._lock:
            return self._last_event_id

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 兴趣信号
    # --------------------------------------------------------
    def emit_signal(self, signal: Any) -> Optional[IntegrationEvent]:
        if not isinstance(signal, InterestSignal):
            with self._lock:
                self._last_error = "emit_signal 收到非 InterestSignal"
            return None
        try:
            ev = make_integration_event(
                event_type=INTEGRATION_INTEREST_SIGNAL_CREATED,
                source=self._owner,
                payload={
                    "signal_id": signal.signal_id,
                    "topic": signal.topic,
                    "trend": signal.trend,
                    "strength": float(signal.strength),
                    "confidence": float(signal.confidence),
                    "source_event_ids": list(signal.source_event_ids),
                    "source_reflection_ids": list(signal.source_reflection_ids),
                    "rationale": signal.rationale,
                },
                related_ids=[signal.signal_id] + list(signal.source_event_ids),
                metadata={"schema_version": INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION},
            )
            with self._lock:
                self._emitted_count += 1
                self._emitted_signal += 1
                self._last_event_id = ev.event_id
            return ev
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emit_signal 异常: {exc}"
            return None

    def emit_signals(self, signals: List[Any]) -> List[IntegrationEvent]:
        out: List[IntegrationEvent] = []
        for s in signals or []:
            ev = self.emit_signal(s)
            if ev is not None:
                out.append(ev)
        return out

    # --------------------------------------------------------
    # 行动
    # --------------------------------------------------------
    def emit_action(self, action: Any) -> Optional[IntegrationEvent]:
        if not isinstance(action, PossibleAction):
            with self._lock:
                self._last_error = "emit_action 收到非 PossibleAction"
            return None
        if not action.has_evidence():
            with self._lock:
                self._last_error = "action 缺少 evidence"
            return None
        try:
            if action.status == ACTION_STATUS_PENDING:
                event_type = INTEGRATION_INITIATIVE_CREATED
                with self._lock:
                    self._emitted_created += 1
            else:
                event_type = INTEGRATION_INITIATIVE_FILTERED
                with self._lock:
                    self._emitted_filtered += 1
            payload = {
                "action_id": action.action_id,
                "action_type": action.action_type,
                "topic": action.topic,
                "status": action.status,
                "urgency": action.urgency,
                "effort_estimate": action.effort_estimate,
                "expected_value": float(action.expected_value),
                "confidence": float(action.confidence),
                "priority": float(action.priority),
                "supporting_signal_ids": list(action.supporting_signal_ids),
                "rationale": action.rationale,
            }
            ev = make_integration_event(
                event_type=event_type,
                source=self._owner,
                payload=payload,
                related_ids=[action.action_id] + list(action.supporting_signal_ids),
                metadata={"schema_version": INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION},
            )
            with self._lock:
                self._emitted_count += 1
                self._last_event_id = ev.event_id
            return ev
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"emit_action 异常: {exc}"
            return None

    def emit_actions(self, actions: List[Any]) -> List[IntegrationEvent]:
        out: List[IntegrationEvent] = []
        for a in actions or []:
            ev = self.emit_action(a)
            if ev is not None:
                out.append(ev)
        return out

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "owner": self._owner,
                "source": self._source,
                "emitted_count": self._emitted_count,
                "emitted_signal": self._emitted_signal,
                "emitted_created": self._emitted_created,
                "emitted_filtered": self._emitted_filtered,
                "last_event_id": self._last_event_id,
                "last_error": self._last_error,
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InitiativeEventEmitter(owner={self._owner!r}, "
                f"emitted={self._emitted_count})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_initiative_event_emitter() -> InitiativeEventEmitter:
    return InitiativeEventEmitter()


__all__ = [
    "INITIATIVE_EVENT_EMITTER_SCHEMA_VERSION",
    "DEFAULT_INITIATIVE_EMITTER_OWNER",
    "DEFAULT_EMITTER_SOURCE",
    "InitiativeEventEmitter",
    "build_default_initiative_event_emitter",
]
