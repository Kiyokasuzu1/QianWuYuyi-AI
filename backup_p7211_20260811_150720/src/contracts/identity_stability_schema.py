"""
Phase 3.5.17: Identity Stability Schema

在已有 IdentityContinuity 与 IdentityAnchor 基础上，
补充一层统一的 Identity Stability Report：

- 身份变化检测（来自 ContinuityReport）
- 连续性评分（来自 ContinuityReport）
- 冲突检测（来自 ContinuityReport / AnchorIntegrityReport）
- 记忆污染检测（Memory pollution signals）
- 最终稳定性评分与审计记录

约束：
- 不修改 Persona 文档
- 不自动接受 GrowthProposal
- 不做不可审计的隐式状态
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class StabilityIssue:
    """
    身份稳定性问题条目。

    issue_type 典型值：
    - continuity_break
    - anchor_deviation
    - value_conflict
    - personality_mutation
    - wrong_growth
    - memory_pollution
    """

    issue_id: str = field(default_factory=lambda: f"isi_{uuid.uuid4().hex[:10]}")
    issue_type: str = ""
    severity: str = "low"  # low / medium / high / critical
    description: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryPollutionSummary:
    """记忆污染检测汇总（不删除，只报告）。"""

    suspicious_count: int = 0
    suspicious_memory_ids: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentityStabilityReport:
    """统一身份稳定性报告。"""

    report_id: str = field(default_factory=lambda: f"isr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    identity_id: str = ""
    before_snapshot_id: str = ""
    after_snapshot_id: str = ""

    continuity_score: float = 1.0
    anchor_intact: bool = True
    stability_score: float = 1.0
    stability_threshold: float = 0.75
    is_stable: bool = True

    continuity_report: Dict[str, Any] = field(default_factory=dict)
    anchor_integrity_report: Dict[str, Any] = field(default_factory=dict)
    memory_pollution: MemoryPollutionSummary = field(default_factory=MemoryPollutionSummary)

    issues: List[StabilityIssue] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    source: str = "identity_stability_engine"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["issues"] = [i.to_dict() for i in self.issues]
        data["memory_pollution"] = self.memory_pollution.to_dict()
        return data


@dataclass
class IdentityStabilitySnapshot:
    """稳定性引擎快照。"""

    snapshot_id: str = field(default_factory=lambda: f"iss_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    total_reports: int = 0
    unstable_reports: int = 0
    critical_issues: int = 0
    last_report_id: str = ""
    last_is_stable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
