# -*- coding: utf-8 -*-
"""
src/runtime/observer/__init__.py

Phase 7.1 Runtime Intelligence —— 观察层包入口。

包级别 API:
    - RuntimeEvent / RuntimeEventType          (runtime_event module)
    - ObservationConfig                        (config module)
    - EventQueue                               (event_queue module)
    - ObservationEventSink / NoOpObservationSink (event_sink module)
    - safe_emit / safe_emit_stage              (pipeline_hooks module)
    - get_event_queue() / get_observation_event_sink()  (进程级单例)
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

# 重导出
from .runtime_event import (
    RUNTIME_EVENT_SCHEMA_VERSION,
    RuntimeEvent,
    RuntimeEventType,
    EVENT_LEVEL_INFO,
    EVENT_LEVEL_WARN,
    EVENT_LEVEL_ERROR,
    EVENT_LEVELS,
)
from .config import ObservationConfig, DEFAULT_PERSISTENCE_DIR
from .event_queue import EventQueue
from .event_sink import ObservationEventSink, NoOpObservationSink
from .pipeline_hooks import safe_emit, safe_emit_stage

# Phase 7.2: Cognitive Trace
from .cognitive_event import (
    COGNITIVE_EVENT_SCHEMA_VERSION,
    CognitiveEvent,
    CognitiveEventType,
)
from .cognitive_hooks import (
    safe_emit_cognitive,
    set_cognitive_context,
    clear_cognitive_context,
    emit_memory_retrieved,
    emit_personality_resolved,
    emit_emotion_updated,
    emit_response_path_decided,
)


__all__ = [
    # —— runtime_event ——
    "RUNTIME_EVENT_SCHEMA_VERSION",
    "RuntimeEvent",
    "RuntimeEventType",
    "EVENT_LEVEL_INFO",
    "EVENT_LEVEL_WARN",
    "EVENT_LEVEL_ERROR",
    "EVENT_LEVELS",
    # —— config ——
    "ObservationConfig",
    "DEFAULT_PERSISTENCE_DIR",
    # —— event_queue ——
    "EventQueue",
    # —— event_sink ——
    "ObservationEventSink",
    "NoOpObservationSink",
    # —— pipeline_hooks ——
    "safe_emit",
    "safe_emit_stage",
    # —— cognitive_event (Phase 7.2) ——
    "COGNITIVE_EVENT_SCHEMA_VERSION",
    "CognitiveEvent",
    "CognitiveEventType",
    # —— cognitive_hooks (Phase 7.2) ——
    "safe_emit_cognitive",
    "set_cognitive_context",
    "clear_cognitive_context",
    "emit_memory_retrieved",
    "emit_personality_resolved",
    "emit_emotion_updated",
    "emit_response_path_decided",
    # —— 单例工厂 ——
    "get_event_queue",
    "get_observation_event_sink",
    "init_observation_layer",
    "reset_observation_singletons_for_tests",
]


# ============================================================
# 进程级单例(api_server 进程内唯一)
# ============================================================
_lock = threading.RLock()
_event_queue_singleton: Optional[EventQueue] = None
_event_sink_singleton: Optional[ObservationEventSink] = None
_init_done: bool = False


def init_observation_layer(
    config: Optional[ObservationConfig] = None,
    project_root: Optional[Any] = None,
) -> None:
    """
    进程级初始化(由 api_server.py 在启动时调用一次)。

    - 若已初始化,直接返回(幂等)
    - 构造 EventQueue + ObservationEventSink 并保存为进程级单例

    Args:
        config:       ObservationConfig(None → 默认配置)
        project_root: 项目根 Path/str,用于解析 persistence_dir(None → CWD)
    """
    global _event_queue_singleton, _event_sink_singleton, _init_done
    with _lock:
        if _init_done:
            return
        cfg: ObservationConfig = (
            config if isinstance(config, ObservationConfig) else ObservationConfig()
        )
        _event_queue_singleton = EventQueue(
            maxlen=cfg.max_history_len,
            subscriber_queue_len=cfg.subscriber_queue_len,
        )
        if cfg.enabled:
            _event_sink_singleton = ObservationEventSink(
                queue=_event_queue_singleton,
                config=cfg,
                project_root=project_root,
            )
        else:
            _event_sink_singleton = NoOpObservationSink()
        _init_done = True


def get_event_queue() -> Optional[EventQueue]:
    """获取进程级 EventQueue 单例(未初始化时返回 None)。"""
    with _lock:
        return _event_queue_singleton


def get_observation_event_sink() -> Optional[ObservationEventSink]:
    """
    获取进程级 ObservationEventSink 单例。

    未初始化时,自动使用默认配置懒初始化(便于测试 + 旧代码兼容)。
    """
    global _event_sink_singleton, _init_done
    with _lock:
        if _event_sink_singleton is None:
            init_observation_layer()
        return _event_sink_singleton


def reset_observation_singletons_for_tests() -> None:
    """
    测试专用: 重置单例状态。

    警告: 生产调用会丢失内存队列 + 订阅者。仅在 pytest fixture 中使用。
    """
    global _event_queue_singleton, _event_sink_singleton, _init_done
    with _lock:
        _event_queue_singleton = None
        _event_sink_singleton = None
        _init_done = False
