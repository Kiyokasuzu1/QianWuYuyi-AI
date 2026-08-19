"""
Phase 3.5.13: Reflection → Growth Integration Schema

定义 ReflectionEvaluation 到 GrowthProposal 候选生成阶段的契约。

职责：
- 保留 ReflectionInsight → ReflectionEvaluation → GrowthProposal 的证据链
- 记录桥接层的决策结果（生成 / 拒绝 / 复用）
- 为审计、回放和后续审批提供稳定结构

约束：
- 不自动应用 GrowthProposal
- 不修改 Persona / Personality 定义
- 所有关键决策必须包含 timestamp / source / audit data
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


BRIDGE_DECISION_CREATED = "proposal_created"
BRIDGE_DECISION_REJECTED = "rejected"
BRIDGE_DECISION_REUSED = "reused"
BRIDGE_DECISION_SKIPPED = "skipped"

ALL_BRIDGE_DECISIONS = [
    BRIDGE_DECISION_CREATED,
    BRIDGE_DECISION_REJECTED,
    BRIDGE_DECISION_REUSED,
    BRIDGE_DECISION_SKIPPED,
]


@dataclass
class EvidenceLink:
    """证据链中的单个节点。"""

    evidence_type: str = ""
    evidence_id: str = ""
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "evidence_id": self.evidence_id,
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }


@dataclass
class ReflectionGrowthRecord:
    """
    Reflection → Growth 桥接记录。

    一次记录对应一次桥接决策：
    - 根据 ReflectionInsight + ReflectionEvaluation + IdentityStatus
    - 判断是否应生成 proposal candidate
    - 记录 proposal_id 与拒绝原因
    """

    record_id: str = field(default_factory=lambda: f"rgr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    insight_id: str = ""
    evaluation_id: str = ""
    source_summary: str = ""

    decision: str = BRIDGE_DECISION_SKIPPED
    decision_reason: str = ""

    proposal_ids: List[str] = field(default_factory=list)
    risk_flags: List[str] = field(default_factory=list)

    identity_status: Dict[str, Any] = field(default_factory=dict)
    evidence_chain: List[EvidenceLink] = field(default_factory=list)

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "insight_id": self.insight_id,
            "evaluation_id": self.evaluation_id,
            "source_summary": self.source_summary,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "proposal_ids": list(self.proposal_ids),
            "risk_flags": list(self.risk_flags),
            "identity_status": dict(self.identity_status),
            "evidence_chain": [item.to_dict() for item in self.evidence_chain],
            "metadata": dict(self.metadata),
        }


@dataclass
class ReflectionGrowthSnapshot:
    """桥接层运行快照。"""

    snapshot_id: str = field(default_factory=lambda: f"rgs_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    enabled: bool = False
    total_records: int = 0
    total_created: int = 0
    total_rejected: int = 0
    total_reused: int = 0
    total_skipped: int = 0

    last_record_id: str = ""
    last_decision: str = ""
    last_insight_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
