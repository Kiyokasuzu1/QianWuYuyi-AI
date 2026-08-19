"""
Phase 4.0 — R2.5.3: ApprovalDecision（演化治理决策 冻结形状）

定位：只记录「治理层对某个 GrowthProposal 的决策」，不执行任何人格/状态变更。
本模块不读取 PersonalityState；不调用 apply；不产生任何 change item。

三种 decision（R2.5.3 冻结，不允许扩）：
  1) approved — 三层保护通过，允许进入 R2.5.4 Evolution Pipeline（但本模块不 apply）
  2) rejected — 身份冲突/原则性违反，永久不应用
  3) deferred — 证据不足/观察期不够，暂不决策，保持 proposal 待下次再议

冻结字段（7 个，Gate AP-1 强制 exact check；Phase 3.6.5 兼容：字段追加不在 GrowthProposal 内）：
  id            approval_{hex12}
  proposal_id   被决策的 GrowthProposal.id（必填非空）
  decision      Literal["approved", "rejected", "deferred"]
  reasons       List[str]；必须非空；建议用 ALLOWED_REASON_TAGS 里的标签
  confidence    0~1；治理层自身对决策的把握度（不是 evaluator.confidence）
  created_at    ISO 时间戳（UTC Z；用于审计排序）
  reviewer      Literal["system"]（R2.5.3 只有治理层 system；未来人类 review 再扩字段）

红线 R2.5.3 冻结不得违反：
  1) 不读取 Personality，不调 .apply / .accept / PersonalityAdapter
  2) decision 只允许 approved/rejected/deferred（under_review 是 proposal.status，不是 decision）
  3) reviewer 唯一允许 "system"（R2.5.3 不允许人工输入伪造 decision 已应用）
  4) 字段集合固定，不偷偷加 extra
  5) reasons 至少 1 条（决策必须可解释）
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, TypedDict, Optional
import uuid


ApprovalDecisionType = Literal["approved", "rejected", "deferred"]
ApprovalReviewer = Literal["system"]
DecisionReasonTag = str


class ApprovalDecision(TypedDict, total=False):
    id: str
    proposal_id: str
    decision: ApprovalDecisionType
    reasons: List[DecisionReasonTag]
    confidence: float
    created_at: str
    reviewer: ApprovalReviewer


FROZEN_KEYS: tuple = (
    "id",
    "proposal_id",
    "decision",
    "reasons",
    "confidence",
    "created_at",
    "reviewer",
)

ALLOWED_DECISIONS: tuple = ("approved", "rejected", "deferred")
ALLOWED_REVIEWERS: tuple = ("system",)

# 建议 reason tag（非强制；但 Gates 会验证三层保护产出的 tag 必在下面集合）
ALLOWED_REASON_TAGS: tuple = (
    # Identity 层
    "identity_consistent",
    "identity_violation_rejected",
    # Evidence 层
    "enough_evidence",
    "insufficient_evidence_deferred",
    "confidence_below_threshold_deferred",
    "time_span_too_short_deferred",
    # Conflict 层
    "no_conflict",
    "conflict_detected_under_review",
    "conflict_with_existing_trait_deferred",
    # Transition 保护（R2.5.2-C）
    "interest_transition_gradual_requires_observation",
    # 通用
    "governance_policy_approved",
    "default_defer_safe_because_reasons_missing",
)


def build_approval_decision(
    *,
    proposal_id: str,
    decision: str,
    reasons: List[str],
    confidence: float,
    reviewer: str = "system",
) -> ApprovalDecision:
    """构建 ApprovalDecision。只做形状校验 + 红线防御。不执行任何 side-effect。"""

    # proposal_id 必填非空
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValueError("proposal_id 必须为非空字符串")

    # decision 枚举
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(
            f"decision={decision!r} 非法，允许={ALLOWED_DECISIONS}"
        )

    # reviewer 枚举（R2.5.3 唯一允许 system）
    if reviewer not in ALLOWED_REVIEWERS:
        raise ValueError(
            f"reviewer={reviewer!r} 非法，R2.5.3 允许={ALLOWED_REVIEWERS}"
        )

    # reasons 至少一条；类型必须是 list；元素必须是 str
    if not isinstance(reasons, list) or len(reasons) == 0:
        raise ValueError("reasons 必须为非空字符串列表（决策必须可解释）")
    for r in reasons:
        if not isinstance(r, str) or not r.strip():
            raise ValueError(f"reason 条目必须为非空字符串: {r!r}")

    # confidence 归一化到 0~1
    try:
        c = float(confidence)
    except Exception:  # noqa: BLE001
        raise ValueError(f"confidence={confidence!r} 不是数值")
    c = min(max(c, 0.0), 1.0)

    decision_obj: ApprovalDecision = {
        "id": f"approval_{uuid.uuid4().hex[:12]}",
        "proposal_id": str(proposal_id).strip(),
        "decision": decision,  # type: ignore[typeddict-item]
        "reasons": [str(x).strip() for x in reasons],
        "confidence": round(c, 4),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewer": reviewer,  # type: ignore[typeddict-item]
    }

    # 二次形状 + 红线断言式检查
    missing = [k for k in FROZEN_KEYS if k not in decision_obj]
    if missing:
        raise ValueError(f"ApprovalDecision 缺少冻结字段: {missing}")
    extra = [k for k in decision_obj.keys() if k not in FROZEN_KEYS]
    if extra:
        raise ValueError(f"ApprovalDecision 有未登记字段: {extra}")
    return decision_obj
