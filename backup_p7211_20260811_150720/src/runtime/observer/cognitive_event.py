# -*- coding: utf-8 -*-
"""
src/runtime/observer/cognitive_event.py

Phase 7.2 Cognitive Trace —— CognitiveEvent 数据结构
与 CognitiveEventType 命名空间枚举。

设计要点:
- schema_version: 事件 schema 版本号, 1.0 冻结
- CognitiveEventType: 使用 cognitive.xxx 命名空间
- event_id 前缀 evt_cog_ 区分 Runtime 事件
- 与 RuntimeEvent 结构对齐, 但独立 schema_version
- 零额外依赖, 仅 stdlib
"""
from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict


# ============================================================
# Schema 版本(冻结 v1.0)
# ============================================================
COGNITIVE_EVENT_SCHEMA_VERSION = "1.0"


# ============================================================
# 事件类型命名空间枚举(Phase 7.2 —— cognitive.* 命名空间)
#
# 命名规则: cognitive.{subsystem}.{action_past_tense}
# 全部用"过去式"强调已经发生的事实, 避免暗示"应该发生"
# ============================================================
class CognitiveEventType:
    """Cognitive Trace 事件类型(命名空间: cognitive.*)。"""

    # === Phase 7.2.1 第一批 ===
    MEMORY_RETRIEVED = "cognitive.memory.retrieved"
    PERSONALITY_RESOLVED = "cognitive.personality.resolved"
    EMOTION_UPDATED = "cognitive.emotion.updated"

    # 最终生成路径(在 RuntimePipeline 内直接发射)
    RESPONSE_PATH_DECIDED = "cognitive.response.path_decided"

    # === Phase 7.2.2 第二批(预留, 本阶段不实现) ===
    GROWTH_EVALUATED = "cognitive.growth.evaluated"

    # === Phase 7.2.3 第三批(预留, 本阶段不实现) ===
    SELF_MODEL_LOADED = "cognitive.self_model.loaded"

    # 所有已声明类型列表
    _ALL = (
        MEMORY_RETRIEVED,
        PERSONALITY_RESOLVED,
        EMOTION_UPDATED,
        RESPONSE_PATH_DECIDED,
        GROWTH_EVALUATED,
        SELF_MODEL_LOADED,
    )

    @classmethod
    def is_valid(cls, event_type: str) -> bool:
        """校验事件类型是否在已声明的枚举中。"""
        return isinstance(event_type, str) and event_type in cls._ALL


# ============================================================
# CognitiveEvent 数据结构
# ============================================================
@dataclass
class CognitiveEvent:
    """
    Cognitive Trace 单条事件(决策链审计级)。

    与 RuntimeEvent 结构对齐, 但:
    - event_id 前缀 evt_cog_
    - 独立 schema_version
    - 有 subsystem 字段(前端 timeline 颜色用)

    Attributes:
        event_id:        事件唯一 ID, 前缀 evt_cog_
        event_type:      cognitive.xxx 命名空间事件类型
        schema_version:  事件 schema 版本, 默认 v1.0
        timestamp:       unix 时间戳(秒, float)
        iso_timestamp:   ISO8601 UTC 字符串
        trace_id:        关联 RuntimePipeline.lifecycle_id
        session_id:      会话标识
        subsystem:       memory / personality / emotion / growth / self_model / response
        level:           info / warn / error
        data:            摘要 payload(白名单由 subsystem helper 保证)
    """

    event_type: str
    trace_id: str
    session_id: str
    event_id: str = ""
    schema_version: str = COGNITIVE_EVENT_SCHEMA_VERSION
    timestamp: float = 0.0
    iso_timestamp: str = ""
    subsystem: str = ""
    level: str = "info"
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = time.time()
        if not self.event_id:
            self.event_id = "evt_cog_" + uuid.uuid4().hex[:12]
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
        if not isinstance(self.data, dict):
            self.data = {"raw": str(self.data)}

    # ------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为纯字典(JSON-serializable)。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CognitiveEvent":
        """从字典反序列化(忽略未知字段)。"""
        if not isinstance(d, dict):
            raise TypeError("CognitiveEvent.from_dict 期望 dict")
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
                "subsystem",
                "level",
                "data",
            )
            if k in d
        }
        return cls(**known)

    # 便捷访问
    @property
    def is_error(self) -> bool:
        return self.level == "error"

    @property
    def short_type(self) -> str:
        """去掉 cognitive. 前缀, 用于前端紧凑展示。"""
        return (
            self.event_type[len("cognitive."):]
            if self.event_type.startswith("cognitive.")
            else self.event_type
        )


__all__ = [
    "COGNITIVE_EVENT_SCHEMA_VERSION",
    "CognitiveEventType",
    "CognitiveEvent",
]
