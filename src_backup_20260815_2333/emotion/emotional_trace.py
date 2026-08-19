"""
情绪来源轨迹 (EmotionalTrace) — Phase 9.4 v2.1
记录一次情绪变化的来源，通过 memory_id 关联记忆系统。
不保存事件内容，只保存引用。

v9.4 新增：
- event_type 字段，保留原始事件类型用于模式分析
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import uuid


class EmotionCause(Enum):
    USER_INTERACTION = "user_interaction"
    MEMORY_RECALL = "memory_recall"
    ACHIEVEMENT = "achievement"
    SELF_REFLECTION = "self_reflection"
    SYSTEM = "system"


@dataclass
class EmotionalTrace:
    emotion: str                               # 情绪类型标签 (joy, anxiety, curiosity 等)
    cause: EmotionCause                        # 触发原因枚举
    intensity: float                           # 情绪强度 0~1
    event_type: str = ""                       # 原始事件类型，如 user_praise（新增字段）
    memory_id: Optional[str] = None            # 关联的记忆 ID（可为空）
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:12]}")
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self):
        self.intensity = max(0.0, min(1.0, self.intensity))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "emotion": self.emotion,
            "cause": self.cause.value,
            "event_type": self.event_type,
            "intensity": self.intensity,
            "memory_id": self.memory_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionalTrace":
        # 仅当 data 中显式包含 trace_id 时才传入，避免覆盖默认 UUID
        kwargs = {
            "emotion": data["emotion"],
            "cause": EmotionCause(data["cause"]),
            "event_type": data.get("event_type", ""),   # 旧数据无此字段时，默认为空字符串
            "intensity": data.get("intensity", 0.5),
            "memory_id": data.get("memory_id"),
            "created_at": data.get("created_at", ""),
        }
        if "trace_id" in data and data["trace_id"]:
            kwargs["trace_id"] = data["trace_id"]
        return cls(**kwargs)