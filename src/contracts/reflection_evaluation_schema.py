"""
Phase 3.5.12: Reflection Evaluation Layer Schema

定义反思价值评估层的契约数据结构。

职责：
- 在 Reflection → Growth 之间增加结构化判断层
- 不引入 LLM，所有评分基于规则与数值指标
- 输出 ReflectionEvaluation，用于决定 Proposal 是否值得进入人工审批

约束：
- 不自动接受 GrowthProposal（仅产出评估结论，不修改任何 Proposal 状态）
- 不修改 Persona / TraitState
- 所有评估行为可审计（保留历史）

核心结构：
- EvaluationDimension: 单个评估维度（novelty/relevance/consistency/stability/alignment）
- ReflectionEvaluation: 完整评估结果
- EvaluationHistoryEntry: 审计历史条目
- EvaluatorSnapshot: 评估器快照
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. 评估维度定义
# ============================================================

# 维度枚举
DIMENSION_NOVELTY = "novelty"            # 新颖性：是否提供新信息
DIMENSION_RELEVANCE = "relevance"        # 相关性：与当前身份/状态的关联度
DIMENSION_CONSISTENCY = "consistency"    # 一致性：与已有记忆/特质的一致程度
DIMENSION_STABILITY = "stability"        # 稳定性：是否为偶发噪声
DIMENSION_ALIGNMENT = "alignment"       # 身份对齐度：是否符合核心身份原则

ALL_DIMENSIONS: List[str] = [
    DIMENSION_NOVELTY,
    DIMENSION_RELEVANCE,
    DIMENSION_CONSISTENCY,
    DIMENSION_STABILITY,
    DIMENSION_ALIGNMENT,
]

# 推荐结论枚举
RECOMMENDATION_ACCEPT = "accept"      # 建议接受（仍需人工最终确认）
RECOMMENDATION_REJECT = "reject"      # 建议拒绝
RECOMMENDATION_DEFER = "defer"        # 暂缓，等待更多证据
RECOMMENDATION_REVISIT = "revisit"    # 需要重新审视

# 来源类型枚举
SOURCE_REFLECTION_INSIGHT = "reflection_insight"
SOURCE_GROWTH_RECORD = "growth_record"
SOURCE_IDENTITY_CHANGE = "identity_change"
SOURCE_RELATIONSHIP = "relationship"


@dataclass
class EvaluationDimension:
    """
    单个评估维度。

    每个维度有 0~1 的分数和理由。
    """
    name: str = ""              # 维度名（见 ALL_DIMENSIONS）
    score: float = 0.0          # 0~1，1 = 最优
    weight: float = 1.0         # 该维度在综合分中的权重
    reason: str = ""            # 评分理由
    evidence: List[str] = field(default_factory=list)  # 支撑证据

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "weight": round(self.weight, 4),
            "reason": self.reason,
            "evidence": list(self.evidence),
        }


# ============================================================
# 2. ReflectionEvaluation: 完整评估结果
# ============================================================

@dataclass
class ReflectionEvaluation:
    """
    反思评估结果。

    输入来源：ReflectionInsight / GrowthRecord / IdentityChange / RelationshipState
    输出：结构化的维度评分 + 综合分 + 推荐结论。

    不修改任何外部状态，仅产出评估意见。
    """
    evaluation_id: str = field(default_factory=lambda: f"rev_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # --- 来源信息 ---
    source_type: str = ""        # reflection_insight / growth_record / identity_change / relationship
    source_id: str = ""         # 来源对象的 ID
    source_summary: str = ""    # 来源对象摘要（便于审计时无需回查原对象）

    # --- 维度评分 ---
    dimensions: List[EvaluationDimension] = field(default_factory=list)

    # --- 综合评分 ---
    overall_score: float = 0.0   # 0~1，加权平均
    confidence: float = 0.0       # 评估本身的可信度（基于证据数等）

    # --- 推荐结论 ---
    recommendation: str = ""     # accept / reject / defer / revisit
    recommendation_reason: str = ""

    # --- 风险标记 ---
    risk_flags: List[str] = field(default_factory=list)   # 如 ["identity_break_risk", "low_evidence"]
    notes: List[str] = field(default_factory=list)

    # --- 元数据 ---
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "evaluation_id": self.evaluation_id,
            "timestamp": self.timestamp,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_summary": self.source_summary,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "overall_score": round(self.overall_score, 4),
            "confidence": round(self.confidence, 4),
            "recommendation": self.recommendation,
            "recommendation_reason": self.recommendation_reason,
            "risk_flags": list(self.risk_flags),
            "notes": list(self.notes),
            "metadata": dict(self.metadata),
        }

    def summary(self) -> str:
        n_risks = len(self.risk_flags)
        return (
            f"[{self.recommendation.upper()}] score={self.overall_score:.3f} "
            f"conf={self.confidence:.3f} risks={n_risks} "
            f"source={self.source_type}:{self.source_id}"
        )


# ============================================================
# 3. EvaluationHistoryEntry: 审计历史条目
# ============================================================

@dataclass
class EvaluationHistoryEntry:
    """
    评估历史条目（精简版，用于审计）。

    保留核心字段，不包含完整的 dimensions 列表，避免历史膨胀。
    """
    evaluation_id: str = ""
    timestamp: str = field(default_factory=now_iso)
    source_type: str = ""
    source_id: str = ""
    overall_score: float = 0.0
    recommendation: str = ""
    risk_flags_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 4. EvaluatorSnapshot: 评估器快照
# ============================================================

@dataclass
class EvaluatorSnapshot:
    """
    评估器状态快照（用于审计和调试）。
    """
    snapshot_id: str = field(default_factory=lambda: f"esnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    enabled: bool = False
    total_evaluations: int = 0
    total_accept: int = 0
    total_reject: int = 0
    total_defer: int = 0
    total_revisit: int = 0

    last_evaluation_id: str = ""
    last_recommendation: str = ""
    last_overall_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
