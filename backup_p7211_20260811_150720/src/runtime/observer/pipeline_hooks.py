# -*- coding: utf-8 -*-
"""
src/runtime/observer/pipeline_hooks.py

Phase 7.1 —— Pipeline 集成辅助函数。

职责:
    提供 Pipeline 钩子调用的便捷封装,确保异常隔离 + 不影响主链路。

    本模块是 RuntimePipeline._safe_emit_event() 的"纯函数版本",
    便于单测与未来复用到其他编排入口(initiative_sender 等)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .event_sink import ObservationEventSink, NoOpObservationSink
from .runtime_event import (
    EVENT_LEVEL_ERROR,
    EVENT_LEVEL_INFO,
    RuntimeEventType,
)

logger = logging.getLogger(__name__)


def safe_emit(
    sink: Optional[ObservationEventSink],
    event_type: str,
    *,
    trace_id: str,
    session_id: str,
    stage: str = "",
    level: str = EVENT_LEVEL_INFO,
    data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    无异常事件发射封装。

    - sink 为 None 时 → No-Op
    - sink 不可用 → No-Op
    - 任何异常 → debug log,绝不冒泡
    """
    if sink is None:
        return
    if isinstance(sink, NoOpObservationSink):
        return
    if not getattr(sink, "enabled", True):
        return
    try:
        sink.emit(
            event_type,
            trace_id=trace_id,
            session_id=session_id,
            stage=stage,
            level=level,
            data=data or {},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("[pipeline_hooks.safe_emit] 失败(已吞掉): %s", exc)


def safe_emit_stage(
    sink: Optional[ObservationEventSink],
    *,
    stage_name: str,
    stage_event_type: str,
    trace_id: str,
    session_id: str,
    duration_ms: Optional[int] = None,
    error: Optional[str] = None,
    extra_data: Optional[Dict[str, Any]] = None,
) -> None:
    """
    阶段完成事件便捷封装(自动带 duration_ms / error / extra_data)。

    用法:
        safe_emit_stage(
            sink,
            stage_name="token_optimization",
            stage_event_type=RuntimeEventType.TOKEN_OPTIMIZATION_DONE,
            trace_id=tid,
            session_id=sid,
            duration_ms=12,
            error=None,
            extra_data={"tokens_before": 1200, "tokens_after": 980},
        )
    """
    level = EVENT_LEVEL_INFO if not error else EVENT_LEVEL_ERROR
    data: Dict[str, Any] = {}
    if duration_ms is not None:
        data["duration_ms"] = int(duration_ms)
    if error:
        data["error"] = str(error)
    if isinstance(extra_data, dict):
        data.update(extra_data)
    safe_emit(
        sink,
        stage_event_type,
        trace_id=trace_id,
        session_id=session_id,
        stage=stage_name,
        level=level,
        data=data,
    )


__all__ = ["safe_emit", "safe_emit_stage"]
