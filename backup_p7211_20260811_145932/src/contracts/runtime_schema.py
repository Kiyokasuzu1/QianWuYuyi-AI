"""
Runtime Contracts —— Runtime 模块的 Schema 定义

为 RuntimeCore、SelfState、WorldState、Decision、Action
提供序列化/反序列化的契约。

设计原则：
- 使用 dataclass，轻量无依赖
- 与 src/contracts/event_schema.py 风格一致
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SelfStateSchema:
    """自身状态 Schema"""
    energy: float = 0.7
    mood: str = "平静"
    curiosity: float = 0.5
    social_need: float = 0.4
    focus: float = 0.6
    trust: float = 0.5
    initiative: float = 0.3
    last_updated: float = 0.0


@dataclass
class WorldStateSchema:
    """世界状态 Schema"""
    current_time: str = ""
    user_status: str = "unknown"
    environment: Dict[str, Any] = field(default_factory=dict)
    self_state: SelfStateSchema = field(default_factory=SelfStateSchema)
    active_tasks: List[Dict[str, Any]] = field(default_factory=list)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    last_action: Optional[Dict[str, Any]] = None


@dataclass
class DecisionSchema:
    """决策 Schema"""
    action_type: str = ""
    priority: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.5


@dataclass
class ActionSchema:
    """行动 Schema"""
    action_id: str = ""
    action_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    status: str = "pending"
    result: Optional[Any] = None


@dataclass
class RuntimeSnapshotSchema:
    """运行时快照 Schema"""
    world_state: WorldStateSchema = field(default_factory=WorldStateSchema)
    self_state: SelfStateSchema = field(default_factory=SelfStateSchema)
    last_tick: float = 0.0
    is_running: bool = False