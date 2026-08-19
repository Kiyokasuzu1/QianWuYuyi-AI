# -*- coding: utf-8 -*-
"""
src/runtime/observer/runtime_event.py

Phase 7.1 Runtime Intelligence —— RuntimeEvent 数据结构
与 RuntimeEventType 命名空间枚举。

设计要点:
- schema_version: 事件 schema 版本号,便于未来扩展时的 JSONL 解析
- RuntimeEventType: 使用 runtime.xxx 命名空间,避免与未来
  growth/memory/emotion/initiative 事件类型混淆
- dataclass + frozenset,不可变语义,事件创建后禁止修改
- 零额外依赖,仅 stdlib(dataclasses, uuid, datetime)
"""
from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ============================================================
# Schema 版本(冻结 v1.0)
# ============================================================
RUNTIME_EVENT_SCHEMA_VERSION = "1.0"


# ============================================================
# 事件类型命名空间枚举(Phase 7.1 —— 仅 Pipeline 级事件)
#
# 命名规则: runtime.<domain>.<action>
# Phase 7.2 以后扩展其他模块时分别使用:
#   memory.xxx / growth.xxx / emotion.xxx / initiative.xxx
# ============================================================
class RuntimeEventType:
    """Runtime 领域事件类型(命名空间: runtime.*)。"""

    # 用户输入 → Pipeline 入口
    USER_MESSAGE_RECEIVED = "runtime.user.message.received"

    # Pipeline 执行开始/结束
    PIPELINE_STARTED = "runtime.pipeline.started"
    PIPELINE_FINISHED = "runtime.pipeline.finished"
    PIPELINE_ERROR = "runtime.pipeline.error"

    # 阶段事件(与 RuntimePipeline.run() 内部阶段对应)
    TOKEN_OPTIMIZATION_DONE = "runtime.stage.token_optimization.done"
    RUNTIME_PATH_DONE = "runtime.stage.runtime_path.done"
    ORCHESTRATOR_FALLBACK_TRIGGERED = "runtime.stage.orchestrator_fallback.triggered"

    # 回复发送
    RESPONSE_SENT = "runtime.response.sent"

    # 所有已声明类型列表,便于校验
    _ALL = (
        USER_MESSAGE_RECEIVED,
        PIPELINE_STARTED,
        PIPELINE_FINISHED,
        PIPELINE_ERROR,
        TOKEN_OPTIMIZATION_DONE,
        RUNTIME_PATH_DONE,
        ORCHESTRATOR_FALLBACK_TRIGGERED,
        RESPONSE_SENT,
    )

    @classmethod
    def is_valid(cls, event_type: str) -> bool:
        """校验事件类型是否在已声明的枚举中。"""
        return isinstance(event_type, str) and event_type in cls._ALL


# ============================================================
# 事件等级
# ============================================================
EVENT_LEVEL_INFO = "info"
EVENT_LEVEL_WARN = "warn"
EVENT_LEVEL_ERROR = "error"
EVENT_LEVELS = (EVENT_LEVEL_INFO, EVENT_LEVEL_WARN, EVENT_LEVEL_ERROR)


# ============================================================
# RuntimeEvent 数据结构
# ============================================================
@dataclass
class RuntimeEvent:
    """
    Runtime 观察事件 —— 不可变。

    Attributes:
        event_id:        事件唯一 ID(uuid4 hex, 12 char),用于 SSE id 字段
        event_type:      runtime.xxx 命名空间事件类型
        schema_version:  事件 schema 版本,默认 v1.0
        timestamp:       unix 时间戳(秒,float)
        iso_timestamp:   ISO8601 UTC 字符串
        trace_id:        关联 RuntimeTrace.trace_id
        session_id:      会话标识
        stage:           pipeline 阶段名(空字符串表示非阶段事件)
        level:           info / warn / error
        data:            自由 payload(字典,内容由 event_type 决定)
    """

    event_type: str
    trace_id: str
    session_id: str
    event_id: str = ""
    schema_version: str = RUNTIME_EVENT_SCHEMA_VERSION
    timestamp: float = 0.0
    iso_timestamp: str = ""
    stage: str = ""
    level: str = EVENT_LEVEL_INFO
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = time.time()
        # 填充缺失字段
        if not self.event_id:
            self.event_id = "evt_" + uuid.uuid4().hex[:12]
        if not self.timestamp:
            self.timestamp = now
        if not self.iso_timestamp:
            try:
                self.iso_timestamp = (
                    datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
                    .isoformat()
                )
            except Exception:  # noqa: BLE001
                self.iso_timestamp = ""
        # 规范化 data(必须是 dict)
        if not isinstance(self.data, dict):
            self.data = {"raw": str(self.data)}

    # ------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为纯字典(JSON-serializable)。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuntimeEvent":
        """从字典反序列化(忽略未知字段)。"""
        if not isinstance(d, dict):
            raise TypeError("RuntimeEvent.from_dict 期望 dict")
        known = {
            k: d[k]
            for k in (
                "event_type",
                "trace_id",
                "session_id",
                "event_id",
                "schema_version",
                "timestamp",
                "iso_timestamp",
                "stage",
                "level",
                "data",
            )
            if k in d
        }
        return cls(**known)

    # 便捷访问
    @property
    def is_error(self) -> bool:
        return self.level == EVENT_LEVEL_ERROR

    @property
    def short_type(self) -> str:
        """去掉 runtime. 前缀,用于前端紧凑展示。"""
        return (
            self.event_type[len("runtime.") :]
            if self.event_type.startswith("runtime.")
            else self.event_type
        )


__all__ = [
    "RUNTIME_EVENT_SCHEMA_VERSION",
    "RuntimeEventType",
    "EVENT_LEVEL_INFO",
    "EVENT_LEVEL_WARN",
    "EVENT_LEVEL_ERROR",
    "EVENT_LEVELS",
    "RuntimeEvent",
]
