"""
Phase 3.5.24: Autonomous Scheduler Schema
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class SchedulerTaskAudit:
    task_id: str = field(default_factory=lambda: f"ast_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    task_type: str = ""   # memory_maintenance / reflection / identity_check / growth_evaluation
    action: str = ""      # run / skip / hold
    reason: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    executed: bool = False
    success: bool = True
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutonomousSchedulerSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"ass_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    total_tasks: int = 0
    executed_tasks: int = 0
    failed_tasks: int = 0
    last_task_type: str = ""
    last_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
