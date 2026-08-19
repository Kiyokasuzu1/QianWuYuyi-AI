"""
人格演化记录 (EvolutionRecord) v2.0

职责：
记录一次人格演化的完整信息，包括触发来源、变化详情、
审批决策和追溯原因。作为 TraitState 修改的唯一合法入口。

v1.2 修正：增加 rejected_dimensions 字段
v2.0 (R2.5.4): 增加 proposal_id / approval_id / change_type / before / after / reasons
                冻结 R2.5.4 字段集合，支持完整回放（"为什么羽依现在喜欢 AI 绘画？"）
"""

from typing import TypedDict, Dict, List, Optional
from datetime import datetime, timezone
import uuid


class EvolutionRecord(TypedDict, total=False):
    """人格演化记录"""

    # ---- 标识 ----
    record_id: str                          # 唯一标识
    timestamp: str                          # 记录时间

    # ---- 触发来源 ----
    trigger_candidates: List[str]           # 触发此次审批的 trait_candidates
    source_reflection_id: Optional[str]     # 来源反思记录ID
    source_growth_records: List[str]        # 支撑此决策的成长记录ID列表

    # ---- 变化详情 ----
    trait_changes: Dict[str, Dict[str, float]]  # 各维度的变化详情

    # ---- 审批决策 ----
    approved: bool                          # 是否通过审批
    confidence: float                       # 审批置信度
    decision_reason: str                    # 决策原因摘要
    rejection_reasons: Dict[str, str]       # 每个被拒维度的具体原因
    rejected_dimensions: List[str]          # 被拒绝的维度列表

    # ---- 元数据 ----
    evolution_level: str                    # 演化层级
    requires_validation: bool               # 是否需要后续持续验证

    # ---- R2.5.4 冻结字段（回放 + 审计闭环） ----
    proposal_id: str                        # 关联的 GrowthProposal.id
    approval_id: str                        # 关联的 ApprovalDecision.id
    change_type: str                        # trait_delta / interest_transition / new_trait
    before: Dict[str, float]                # 变化前的 trait 快照（仅 affected traits）
    after: Dict[str, float]                 # 变化后的 trait 快照（仅 affected traits）
    reasons: List[str]                      # 决策原因标签（来自 ApprovalDecision.reasons）


# R2.5.4 冻结字段集合（build_evolution_record 会强制这些字段存在）
R254_FROZEN_KEYS: tuple = (
    "record_id",
    "timestamp",
    "proposal_id",
    "approval_id",
    "change_type",
    "before",
    "after",
    "reasons",
)

ALLOWED_CHANGE_TYPES: tuple = (
    "trait_delta",              # 普通 trait 微调
    "interest_transition",      # 兴趣迁移（来自 R2.5.2-C gradual_transition）
    "new_trait",                # 新兴趣/新维度出现
)


def build_evolution_record(
    *,
    proposal_id: str,
    approval_id: str,
    change_type: str,
    before: Dict[str, float],
    after: Dict[str, float],
    reasons: List[str],
    confidence: float = 0.0,
) -> EvolutionRecord:
    """
    构建 R2.5.4 EvolutionRecord。只做形状校验，不执行任何 side-effect。

    红线：
      - proposal_id / approval_id 必须非空（EP-3: 任何 PersonalityState 叹息必须有溯源）
      - change_type 必须在 ALLOWED_CHANGE_TYPES 里
      - before / after 必须是 dict（允许空 dict，但 key 必须是 str, value 必须是 float）
      - reasons 必须非空（可解释性）
    """
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValueError("proposal_id 必须为非空字符串")
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise ValueError("approval_id 必须为非空字符串（EP-3: 无 approval 的演化不允许）")
    if change_type not in ALLOWED_CHANGE_TYPES:
        raise ValueError(f"change_type={change_type!r} 非法，允许={ALLOWED_CHANGE_TYPES}")
    if not isinstance(reasons, list) or len(reasons) == 0:
        raise ValueError("reasons 必须为非空列表（决策必须可解释）")
    for r in reasons:
        if not isinstance(r, str) or not r.strip():
            raise ValueError(f"reason 条目必须为非空字符串: {r!r}")

    # 归一化 before/after
    def _normalize_traits(d: Dict[str, float]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k, v in (d or {}).items():
            try:
                out[str(k)] = round(float(v), 6)
            except Exception:  # noqa: BLE001
                raise ValueError(f"trait value {v!r} for key {k!r} 不是数值")
        return out

    record: EvolutionRecord = {
        "record_id": f"evo_{uuid.uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "proposal_id": str(proposal_id).strip(),
        "approval_id": str(approval_id).strip(),
        "change_type": change_type,
        "before": _normalize_traits(before),
        "after": _normalize_traits(after),
        "reasons": [str(x).strip() for x in reasons],
        "approved": True,
        "confidence": round(min(max(float(confidence or 0.0), 0.0), 1.0), 4),
        "decision_reason": "; ".join(reasons),
    }

    # 二次形状检查
    missing = [k for k in R254_FROZEN_KEYS if k not in record]
    if missing:
        raise ValueError(f"EvolutionRecord 缺少冻结字段: {missing}")
    return record