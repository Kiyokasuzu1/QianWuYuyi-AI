"""
Phase 3.5.23: Runtime Event Bus Wrapper

基于现有 core.event_bus 做一层标准化包装：
- 发布统一标准事件
- 自动兼容 legacy alias
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.event_bus import EventBus
from src.contracts.runtime_event_schema import (
    RuntimeDomainEvent,
    LEGACY_EVENT_ALIASES,
)


class RuntimeEventBus:
    def __init__(self, event_bus: Optional[EventBus] = None):
        self._event_bus = event_bus or EventBus.get_instance()

    def emit(
        self,
        event_type: str,
        *,
        source: str = "",
        source_id: str = "",
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        emit_legacy_aliases: bool = True,
    ) -> RuntimeDomainEvent:
        event = RuntimeDomainEvent(
            event_type=event_type,
            source=source,
            source_id=source_id,
            payload=dict(payload or {}),
            related_ids=list(related_ids or []),
        )
        event_payload = event.to_dict()
        self._event_bus.emit(
            event_type=event_type,
            payload=event_payload,
            source=source,
            request_id=event.event_id,
        )
        if emit_legacy_aliases:
            for alias in LEGACY_EVENT_ALIASES.get(event_type, []):
                self._event_bus.emit(
                    event_type=alias,
                    payload=event_payload,
                    source=source,
                    request_id=event.event_id,
                )
        return event

    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        return [
            {
                "event_type": e.event_type,
                "payload": e.payload,
                "source": e.source,
                "request_id": e.request_id,
                "timestamp": e.timestamp,
            }
            for e in self._event_bus.get_history(event_type=event_type, limit=limit)
        ]
