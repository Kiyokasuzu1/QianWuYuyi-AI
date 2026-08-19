# src/runtime/events.py
"""
Runtime Event 基类 —— Phase 3.7.0

仅定义事件数据契约，不包含任何业务逻辑。

Event 是 Runtime 与外部世界（UI / IM / Scheduler / 业务模块）
通信的统一单位。

依赖：仅 stdlib + dataclasses + typing
禁止：import src.memory / src.emotion / src.personality / src.growth
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


# ============================================================
# 事件类型常量（与 docs/runtime.md §5.2 对齐）
# ============================================================
EVENT_TYPE_USER_INPUT = "user_input"
EVENT_TYPE_SYSTEM = "system"
EVENT_TYPE_TICK = "tick"
EVENT_TYPE_GROWTH_PROPOSAL = "growth_proposal"
# Phase 4.0.0: 感知观察事件（设计阶段预留）
EVENT_TYPE_PERCEPTION_OBSERVATION = "perception_observation"

# 事件优先级
EVENT_PRIORITY_NORMAL = 0
EVENT_PRIORITY_HIGH = 1
EVENT_PRIORITY_CRITICAL = 2


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class Event:
    """Runtime Event 基类（v1.0）

    字段契约：
    - id           str                # 唯一 ID
    - type         str                # 事件类型（user_input / system / tick / growth_proposal）
    - source       Optional[str]      # 事件来源
    - timestamp    str                # ISO 8601 with Z
    - payload      Dict[str, Any]     # 事件载荷
    - related_ids  List[str]          # 关联 ID 列表
    - priority     int                # 优先级（0=normal, 1=high, 2=critical）
    - metadata     Dict[str, Any]     # 元数据
    """
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    type: str = EVENT_TYPE_USER_INPUT
    source: Optional[str] = None
    timestamp: str = field(default_factory=_now_iso)
    payload: Dict[str, Any] = field(default_factory=dict)
    related_ids: List[str] = field(default_factory=list)
    priority: int = EVENT_PRIORITY_NORMAL
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """反序列化（缺省字段使用默认值）。"""
        return cls(
            id=data.get("id") or f"evt_{uuid.uuid4().hex[:12]}",
            type=data.get("type", EVENT_TYPE_USER_INPUT),
            source=data.get("source"),
            timestamp=data.get("timestamp") or _now_iso(),
            payload=data.get("payload", {}) or {},
            related_ids=data.get("related_ids", []) or [],
            priority=int(data.get("priority", EVENT_PRIORITY_NORMAL)),
            metadata=data.get("metadata", {}) or {},
        )

    def is_high_priority(self) -> bool:
        """是否高优先级（≥ high）。"""
        return self.priority >= EVENT_PRIORITY_HIGH
