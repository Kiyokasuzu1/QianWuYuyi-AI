"""
Phase 3.5.20: Runtime Final Integration Schema

定义运行时最终集成层的模块注册与健康报告结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class RuntimeModuleRegistration:
    module_id: str = field(default_factory=lambda: f"rim_{uuid.uuid4().hex[:10]}")
    name: str = ""
    category: str = ""
    enabled: bool = False
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeSubsystemStatus:
    enabled: bool = False
    healthy: bool = False
    state: str = "disabled"
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuntimeHealthReport:
    report_id: str = field(default_factory=lambda: f"rhr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    overall_status: str = "unknown"

    memory_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    reflection_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    growth_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    identity_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    personality_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    relationship_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    emotion_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)
    autonomous_status: RuntimeSubsystemStatus = field(default_factory=RuntimeSubsystemStatus)

    registered_modules: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["memory_status"] = self.memory_status.to_dict()
        data["reflection_status"] = self.reflection_status.to_dict()
        data["growth_status"] = self.growth_status.to_dict()
        data["identity_status"] = self.identity_status.to_dict()
        data["personality_status"] = self.personality_status.to_dict()
        data["relationship_status"] = self.relationship_status.to_dict()
        data["emotion_status"] = self.emotion_status.to_dict()
        data["autonomous_status"] = self.autonomous_status.to_dict()
        return data
