"""
Phase 3.5.21: Cognitive Loop Verification Schema

用于验证羽依认知闭环是否稳定工作，
不负责驱动真实人格变更。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class DecisionBudget:
    """防止 verification 中反思无限循环。"""

    max_reflections: int = 2
    max_evaluations: int = 1
    max_proposals: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CognitiveLoopReport:
    loop_id: str = field(default_factory=lambda: f"clr_{uuid.uuid4().hex[:10]}")
    start_time: str = field(default_factory=now_iso)
    end_time: str = ""

    experience_created: bool = False
    memory_written: bool = False
    reflection_generated: bool = False
    evaluation_completed: bool = False
    proposal_created: bool = False
    identity_checked: bool = False
    evolution_applied: bool = False

    failure_points: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    input_text: str = ""
    sequence_index: int = 0
    audit: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
