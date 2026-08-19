"""
Phase 4.0 — R2.5.3: ApprovalPolicy（三层治理保护）

Evolution Governance Layer 的纯策略层：
  输入: proposal snapshot（只读；包含 proposed_changes / confidence / evidence_ids / evaluator_meta）
  输出: PolicyRecommendation（decision + reasons + decision_confidence + status_hint）

注意：本模块绝不读取 PersonalityState / SelfModel / trait_state / PersonalityAdapter。
      也绝不写入任何东西（只做纯函数计算）。

三层顺序（一旦触发高优先级层直接返回，不再评估下游）：
  1) Identity Anchor  → 违反身份 → decision=rejected（硬拒绝，不可 override）
  2) Evidence        → 证据不足 → decision=deferred（可观察，下次再议）
  3) Conflict        → 与现状冲突 → decision=deferred + status_hint=under_review

三层全过 → decision=approved + reasons=(identity_consistent, enough_evidence, no_conflict)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.approval.approval_decision import (
    ApprovalDecisionType,
    DecisionReasonTag,
)


# ============================================================
# 1. Identity Layer 最小可审计规则（Phase R2.5.3 冻结）
#    不写任何 "羽依应该是什么数值" 的代码，只禁止 "羽依核心身份被 Growth 修改"。
#    若 change.path 以任意前缀匹配 FORBIDDEN_IDENTITY_PATH_PREFIXES，直接 rejected。
# ============================================================
FORBIDDEN_IDENTITY_PATH_PREFIXES: Tuple[str, ...] = (
    "identity.core.",         # 核心身份（羽依/千羽语义锚点/名字等）
    "identity.origin.",       # 出生/原初设定（Phase 3.6.4 冻结不可变）
    "manifesto.",             # 宣言/红线原则（绝对不允许 Growth 触碰）
)


# ============================================================
# 2. Evidence Layer 阈值（Phase R2.5.3 默认值；可通过 ApprovalPolicy(config=...) 覆盖）
# ============================================================
DEFAULT_MIN_EVIDENCE_COUNT: int = 3
DEFAULT_MIN_EVALUATOR_CONFIDENCE: float = 0.75
DEFAULT_MIN_OCCURRENCE_COUNT: int = 2


# ============================================================
# 3. Conflict Layer 阈值（默认：|delta| >= 0.15 AND before/after 跨越 0.5 视为倒转）
# ============================================================
DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD: float = 0.15
DEFAULT_CONFLICT_REVERSAL_MIDPOINT: float = 0.5


@dataclass
class PolicyRecommendation:
    """三层保护的输出（纯值对象）。ApprovalManager 会据此 build_approval_decision。"""
    decision: ApprovalDecisionType
    reasons: List[DecisionReasonTag] = field(default_factory=list)
    decision_confidence: float = 0.0
    status_hint: Optional[str] = None  # 可选：提示 proposal.status 应该改成什么（under_review）

    def add_reason(self, tag: DecisionReasonTag) -> None:
        if tag not in self.reasons:
            self.reasons.append(tag)


@dataclass
class ProposalSnapshot:
    """proposal 的只读快照（从 GrowthProposal 提取；避免 policy 依赖 proposal_manager 结构）。"""
    proposal_id: str
    evaluator_confidence: float
    evidence_ids: List[str]
    proposed_changes: List[Dict[str, Any]]   # 每项 {path, before, after, reason}
    evaluator_meta: Dict[str, Any]
    needs_review: bool = False
    occurrence_count: int = 1


class ApprovalPolicy:
    """纯函数治理策略。"""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self.min_evidence_count: int = int(cfg.get(
            "min_evidence_count", DEFAULT_MIN_EVIDENCE_COUNT,
        ))
        self.min_evaluator_confidence: float = float(cfg.get(
            "min_evaluator_confidence", DEFAULT_MIN_EVALUATOR_CONFIDENCE,
        ))
        self.min_occurrence_count: int = int(cfg.get(
            "min_occurrence_count", DEFAULT_MIN_OCCURRENCE_COUNT,
        ))
        self.conflict_reversal_delta: float = float(cfg.get(
            "conflict_reversal_delta_threshold", DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD,
        ))
        self.conflict_reversal_midpoint: float = float(cfg.get(
            "conflict_reversal_midpoint", DEFAULT_CONFLICT_REVERSAL_MIDPOINT,
        ))

    # ============================================================
    # 公共入口
    # ============================================================
    def evaluate(self, snap: ProposalSnapshot) -> PolicyRecommendation:
        """按三层顺序评估。"""
        # Layer 1: Identity Anchor
        rec = self._identity_anchor(snap)
        if rec is not None:
            return rec

        # Layer 2: Evidence
        rec = self._evidence(snap)
        if rec is not None:
            return rec

        # Layer 3: Conflict
        rec = self._conflict(snap)
        if rec is not None:
            return rec

        # 三层通过 → approved
        approved = PolicyRecommendation(
            decision="approved",
            decision_confidence=0.9,
        )
        approved.add_reason("identity_consistent")
        approved.add_reason("enough_evidence")
        approved.add_reason("no_conflict")
        approved.add_reason("governance_policy_approved")
        return approved

    # ============================================================
    # Layer 1: Identity Anchor（硬拒绝）
    # ============================================================
    def _identity_anchor(self, snap: ProposalSnapshot) -> Optional[PolicyRecommendation]:
        for change in snap.proposed_changes or []:
            path = str(change.get("path", "") or "")
            for prefix in FORBIDDEN_IDENTITY_PATH_PREFIXES:
                if path.startswith(prefix):
                    rec = PolicyRecommendation(
                        decision="rejected",
                        decision_confidence=0.98,
                    )
                    rec.add_reason("identity_violation_rejected")
                    return rec
        return None

    # ============================================================
    # Layer 2: Evidence（证据不足 → deferred，下次再议）
    # ============================================================
    def _evidence(self, snap: ProposalSnapshot) -> Optional[PolicyRecommendation]:
        reasons: List[DecisionReasonTag] = []
        enough = True

        evidence_count = len(set(snap.evidence_ids or []))
        if evidence_count < self.min_evidence_count:
            reasons.append("insufficient_evidence_deferred")
            enough = False

        if float(snap.evaluator_confidence or 0.0) < self.min_evaluator_confidence:
            reasons.append("confidence_below_threshold_deferred")
            enough = False

        occurrence = int(snap.occurrence_count or 0) or 1
        if occurrence < self.min_occurrence_count:
            reasons.append("time_span_too_short_deferred")
            enough = False

        # R2.5.2-C gradual_transition 需要再观察一段时间
        em = snap.evaluator_meta or {}
        if em.get("transition_proposal_mode") == "gradual_transition":
            reasons.append("interest_transition_gradual_requires_observation")
            enough = False

        if enough:
            return None
        return PolicyRecommendation(
            decision="deferred",
            reasons=reasons,
            decision_confidence=0.8,
        )

    # ============================================================
    # Layer 3: Conflict（倒转/needs_review → deferred + under_review）
    # ============================================================
    def _conflict(self, snap: ProposalSnapshot) -> Optional[PolicyRecommendation]:
        reasons: List[DecisionReasonTag] = []
        status_hint: Optional[str] = None

        # 冲突1: evaluator_meta.needs_review 已经 True（来自 Step 5.5 transition 或 delta_oversized）
        if snap.needs_review:
            reasons.append("conflict_detected_under_review")
            status_hint = "under_review"

        # 冲突2: 任意一个 change 跨越 midpoint 且 delta 绝对值超阈值
        for change in snap.proposed_changes or []:
            try:
                before = float(change.get("before") if change.get("before") is not None else 0.5)
                after = float(change.get("after") if change.get("after") is not None else before)
            except Exception:  # noqa: BLE001
                continue
            delta = after - before
            cross_midpoint = (
                (before < self.conflict_reversal_midpoint <= after)
                or (before > self.conflict_reversal_midpoint >= after)
            )
            if cross_midpoint and abs(delta) >= self.conflict_reversal_delta:
                reasons.append("conflict_with_existing_trait_deferred")
                status_hint = "under_review"
                break

        if not reasons:
            return None
        # Conflict 层 → deferred（不是 rejected；冲突可以观察缓解或下次再议）
        return PolicyRecommendation(
            decision="deferred",
            reasons=reasons,
            decision_confidence=0.75,
            status_hint=status_hint,
        )
