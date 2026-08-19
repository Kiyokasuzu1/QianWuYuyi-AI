"""
Phase 3.5.18: Personality Evolution Pipeline Schema

定义“人格演化执行”阶段的审计结构：
- 输入：已批准的 GrowthProposal（或 ApprovalRecord）
- 输出：EvolutionRecord / TraitState 更新结果 / SelfModelChangeSuggestion（不自动应用）

约束：
- 不自动接受 proposal
- 不绕过审批
- 不修改 Persona 文档
- 所有变化必须可追溯（source + evidence + approval）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class PersonalityEvolutionRecord:
    """一次人格演化执行记录（可审计）。"""

    record_id: str = field(default_factory=lambda: f"per_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    proposal_id: str = ""
    approval_record_id: str = ""
    actor: str = "runtime"

    status: str = "applied"  # applied / blocked / failed
    reason: str = ""

    identity_stability_report_id: str = ""
    identity_stability_snapshot: Dict[str, Any] = field(default_factory=dict)

    evolution_record: Dict[str, Any] = field(default_factory=dict)
    trait_states_before: Dict[str, Any] = field(default_factory=dict)
    trait_states_after: Dict[str, Any] = field(default_factory=dict)

    self_model_suggestions: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PersonalityEvolutionSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"pes_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    total_records: int = 0
    applied: int = 0
    blocked: int = 0
    failed: int = 0
    last_record_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

