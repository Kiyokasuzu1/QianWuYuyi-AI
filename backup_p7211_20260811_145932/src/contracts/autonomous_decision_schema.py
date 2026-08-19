"""
Phase 3.5.19: Autonomous Decision Layer Schema

定义运行时自主判断的结构化审计记录：
- 什么时候反思
- 什么时候学习（生成 proposal）
- 什么时候保持稳定（不触发反思）

约束：
- 不调用 LLM
- 不自动接受 GrowthProposal
- 所有决策必须可审计
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class AutonomousDecisionRecord:
    record_id: str = field(default_factory=lambda: f"adr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    decision_type: str = ""  # reflection / stability / learning / hold
    action: str = ""         # trigger_reflection / refresh_stability / hold_stable / skip
    reason: str = ""

    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    executed: bool = False
    success: bool = True
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutonomousDecisionSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"ads_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    total_records: int = 0
    total_executed: int = 0
    total_failed: int = 0
    last_action: str = ""
    last_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

