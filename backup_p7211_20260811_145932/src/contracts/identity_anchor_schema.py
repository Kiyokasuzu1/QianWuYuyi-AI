"""
Phase 3.5.10: Identity Anchor Schema

身份锚点（Identity Anchor）是羽依长期身份核心层的结构化表示。

设计原则：
- 锚点来源于 identity.md 中的核心原则（Authenticity / Growth / Independence / Memory Connection / Creator Relationship）
- 锚点不是「限制成长的规则」，而是「身份稳定参考点」
- 锚点不修改 Persona 文档、不修改 TraitState、不替代 Personality System
- 所有变化必须可审计（AnchorChangeProposal → 审批 → 应用）
- 不接入 LLM，全结构化

核心结构：
- AnchorSource: 锚点来源标记（identity.md / growth_milestone / reflection）
- IdentityAnchor: 单个身份锚点（原则 + 权重 + 约束条件 + 来源）
- AnchorSnapshot: 某时刻所有锚点的快照
- AnchorChangeProposal: 锚点变化建议（不自动应用，需审批）
- AnchorIntegrityReport: 锚点完整性报告（检测锚点是否偏离）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. AnchorSource: 锚点来源
# ============================================================

@dataclass
class AnchorSource:
    """记录锚点的来源信息（可审计）"""
    source_type: str = "identity_doc"   # identity_doc / growth_milestone / reflection / external
    source_id: str = ""                  # identity.md / gr_xxx / ins_xxx
    source_section: str = ""             # 文档章节名（如 "Authenticity"）
    timestamp: str = field(default_factory=now_iso)
    confidence: float = 1.0             # identity_doc 来源 = 1.0（最高）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 2. IdentityAnchor: 单个身份锚点
# ============================================================

@dataclass
class AnchorConstraint:
    """
    锚点约束条件。

    类型：
    - must_maintain: 该维度必须在一定范围内维持
    - must_not_violate: 该维度不能被违反
    - should_preserve: 建议保持（非强制）
    """
    constraint_type: str = "should_preserve"  # must_maintain / must_not_violate / should_preserve
    dimension: str = ""       # 约束维度（如 "authenticity_score" / "growth_openness"）
    min_value: float = 0.0     # 最小值（must_maintain 用）
    max_value: float = 1.0     # 最大值
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentityAnchor:
    """
    单个身份锚点。

    代表 identity.md 中的一个核心原则。
    锚点有权重（表示在身份核心中的相对重要性），
    但权重变化仅作为「参考」，不自动限制成长。
    """
    anchor_id: str = field(default_factory=lambda: f"anchor_{uuid.uuid4().hex[:8]}")
    name: str = ""                     # 锚点名称（如 "Authenticity"）
    display_name: str = ""             # 中文显示名（如 "真实性"）
    description: str = ""              # 结构化描述
    principle: str = ""                # 核心原则（一句话）

    # 权重（0~1），表示在身份核心中的相对重要性
    weight: float = 0.8
    original_weight: float = 0.8       # 原始权重（来自 identity.md，不可低于此值的某比例）

    # 来源
    source: AnchorSource = field(default_factory=AnchorSource)

    # 约束条件列表
    constraints: List[AnchorConstraint] = field(default_factory=list)

    # 关联维度（该锚点影响哪些 SelfModel 维度）
    related_traits: List[str] = field(default_factory=list)       # 如 ["warmth", "honesty"]
    related_core_values: List[str] = field(default_factory=list)  # 如 ["empathy", "honesty"]

    # 审计
    created_at: str = field(default_factory=now_iso)
    last_modified: str = field(default_factory=now_iso)
    version: int = 1

    # 是否为核心锚点（不可移除）
    is_core: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "principle": self.principle,
            "weight": round(self.weight, 4),
            "original_weight": round(self.original_weight, 4),
            "source": self.source.to_dict(),
            "constraints": [c.to_dict() for c in self.constraints],
            "related_traits": list(self.related_traits),
            "related_core_values": list(self.related_core_values),
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "version": self.version,
            "is_core": self.is_core,
        }


# ============================================================
# 3. AnchorSnapshot: 某时刻所有锚点的快照
# ============================================================

@dataclass
class AnchorSnapshot:
    """
    身份锚点快照 —— 某一时刻所有锚点的状态汇总。

    用于审计和连续性比较。
    """
    snapshot_id: str = field(default_factory=lambda: f"asnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # 锚点摘要（仅包含可比较的核心字段）
    anchors: List[Dict[str, Any]] = field(default_factory=list)

    # 统计
    total_anchors: int = 0
    core_anchors_count: int = 0
    total_weight: float = 0.0
    avg_weight: float = 0.0

    # 来源标记
    source: str = ""  # "identity_anchor_manager" / "external"

    # 关联的 SelfModel 版本（可选）
    self_model_version: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "anchors": list(self.anchors),
            "total_anchors": self.total_anchors,
            "core_anchors_count": self.core_anchors_count,
            "total_weight": round(self.total_weight, 4),
            "avg_weight": round(self.avg_weight, 4),
            "source": self.source,
            "self_model_version": self.self_model_version,
        }


# ============================================================
# 4. AnchorChangeProposal: 锚点变化建议
# ============================================================

@dataclass
class AnchorWeightChange:
    """单个锚点的权重变化"""
    anchor_id: str = ""
    anchor_name: str = ""
    old_weight: float = 0.0
    new_weight: float = 0.0
    delta: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnchorChangeProposal:
    """
    锚点变化建议。

    不自动应用，需要外部审批。
    可来源：
    - GrowthRecord 累积（milestone 类型触发权重微调建议）
    - ReflectionInsight（检测到身份偏移）
    - IdentityContinuity 报告（检测到断裂后的修复建议）
    """
    proposal_id: str = field(default_factory=lambda: f"acp_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # 来源
    source_type: str = ""   # growth_record / reflection_insight / continuity_report / external
    source_id: str = ""

    # 变化内容
    weight_changes: List[AnchorWeightChange] = field(default_factory=list)
    add_constraints: Dict[str, List[AnchorConstraint]] = field(default_factory=dict)  # anchor_id → constraints
    notes: List[str] = field(default_factory=list)

    # 评估
    confidence: float = 0.0
    requires_approval: bool = True   # 始终需要审批
    approved: bool = False
    applied: bool = False

    # 完整性影响评估
    integrity_impact: str = "neutral"  # positive / neutral / negative

    def summary(self) -> str:
        parts = []
        if self.weight_changes:
            parts.append(f"{len(self.weight_changes)} weight_changes")
        if self.add_constraints:
            parts.append(f"{len(self.add_constraints)} constraint_additions")
        return ", ".join(parts) or "no changes"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "timestamp": self.timestamp,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "weight_changes": [c.to_dict() for c in self.weight_changes],
            "add_constraints": {
                k: [c.to_dict() for c in v]
                for k, v in self.add_constraints.items()
            },
            "notes": list(self.notes),
            "confidence": round(self.confidence, 4),
            "requires_approval": self.requires_approval,
            "approved": self.approved,
            "applied": self.applied,
            "integrity_impact": self.integrity_impact,
            "summary": self.summary(),
        }


# ============================================================
# 5. AnchorIntegrityReport: 锚点完整性报告
# ============================================================

@dataclass
class AnchorDeviation:
    """单个锚点的偏离记录"""
    anchor_id: str = ""
    anchor_name: str = ""
    deviation_type: str = ""     # weight_drift / constraint_violation / missing_anchor
    severity: str = "low"        # low / medium / high
    description: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AnchorIntegrityReport:
    """
    锚点完整性报告。

    检测：
    - 锚点权重是否偏离原始值过多
    - 约束条件是否被违反
    - 核心锚点是否缺失
    - SelfModel 是否与锚点一致
    """
    report_id: str = field(default_factory=lambda: f"air_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # 完整性分数（0~1）
    integrity_score: float = 1.0
    is_intact: bool = True        # score >= threshold
    threshold: float = 0.8

    # 偏离列表
    deviations: List[AnchorDeviation] = field(default_factory=list)

    # 锚点摘要
    anchor_count: int = 0
    core_anchor_count: int = 0
    avg_weight_drift: float = 0.0     # 平均权重偏移
    max_weight_drift: float = 0.0     # 最大权重偏移

    # 与 SelfModel 的一致性
    self_model_alignment: float = 1.0  # 0~1，锚点与 SelfModel 的一致程度

    # 关联快照
    anchor_snapshot_id: str = ""

    # 注释
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "INTACT" if self.is_intact else "DEVIATED"
        return (
            f"[{status}] score={self.integrity_score:.3f} "
            f"deviations={len(self.deviations)} "
            f"max_drift={self.max_weight_drift:.4f} "
            f"alignment={self.self_model_alignment:.3f}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "integrity_score": round(self.integrity_score, 4),
            "is_intact": self.is_intact,
            "threshold": self.threshold,
            "deviations": [d.to_dict() for d in self.deviations],
            "anchor_count": self.anchor_count,
            "core_anchor_count": self.core_anchor_count,
            "avg_weight_drift": round(self.avg_weight_drift, 4),
            "max_weight_drift": round(self.max_weight_drift, 4),
            "self_model_alignment": round(self.self_model_alignment, 4),
            "anchor_snapshot_id": self.anchor_snapshot_id,
            "notes": list(self.notes),
            "summary": self.summary(),
        }
