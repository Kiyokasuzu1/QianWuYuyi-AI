"""
WorldState —— 羽依此刻知道什么

与旧的 RuntimeContext（Prompt 组装器）不同，
WorldState 是运行时世界状态快照，
用于决策引擎判断当前应该做什么。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.self_state import SelfState


@dataclass
class WorldState:
    current_time: str = field(default_factory=lambda: datetime.now().isoformat())
    user_status: str = "unknown"
    environment: Dict[str, Any] = field(default_factory=dict)
    self_state: SelfState = field(default_factory=SelfState.default)
    active_tasks: List[Dict[str, Any]] = field(default_factory=list)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    last_action: Optional[Dict[str, Any]] = None

    def update_time(self) -> None:
        """更新时间戳"""
        self.current_time = datetime.now().isoformat()

    def add_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        """记录事件"""
        self.recent_events.append({
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": event_data,
        })
        # 限制事件历史长度
        if len(self.recent_events) > 100:
            self.recent_events = self.recent_events[-100:]

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "current_time": self.current_time,
            "user_status": self.user_status,
            "environment": self.environment,
            "self_state": self.self_state.to_dict(),
            "active_tasks": self.active_tasks,
            "recent_events": self.recent_events,
            "last_action": self.last_action,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorldState":
        """从字典反序列化"""
        return cls(
            current_time=data.get("current_time", datetime.now().isoformat()),
            user_status=data.get("user_status", "unknown"),
            environment=data.get("environment", {}),
            self_state=SelfState.from_dict(data.get("self_state", {})),
            active_tasks=data.get("active_tasks", []),
            recent_events=data.get("recent_events", []),
            last_action=data.get("last_action"),
        )