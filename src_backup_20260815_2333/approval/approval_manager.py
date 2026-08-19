"""
Phase 4.0 — R2.5.3: ApprovalManager（Evolution Governance Layer 协调器）

职责：
  1) 从 ProposalManager 读取 GrowthProposal → 转换 ProposalSnapshot（只读）
  2) 调 ApprovalPolicy.evaluate 治理三层保护 → PolicyRecommendation
  3) build_approval_decision 生成决策快照
  4) 通过 ProposalManager.update_proposal_status_and_meta 安全更新
       proposal.status ∈ {approved / rejected / deferred / under_review}
       evaluator_meta.approval_decision_id = approval.id

红线（R2.5.3 最关键冻结）：
  ❌ ApprovalManager 绝对不直接调用：
     - ProposalManager.accept_proposal()
     - ProposalManager.apply_proposal()
     - PersonalityAdapter / PersonalityState / trait_state
  ❌ 绝不写 GrowthRecord / 不修改五维关系 / 不写 manifesto
  ✅ 只产生 ApprovalDecision + 修改 proposal.status（给 R2.5.4 Evolution Pipeline 留下钩子）

  应用 → 完全留给 R2.5.4 Personality Evolution Pipeline。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.approval.approval_decision import (
    ApprovalDecision,
    build_approval_decision,
)
from src.approval.approval_policy import (
    ApprovalPolicy,
    PolicyRecommendation,
    ProposalSnapshot,
)

logger = logging.getLogger(__name__)


# 允许 R2.5.3 Approval 修改 proposal 的旧状态集合：
# 默认 GrowthProposal 的 status 可能是 "proposed"（v1.0 默认），以及之前代码写的 "pending" alias。
# 还有 Step 5.5 之前的 "under_review" / "deferred"（如果之前同 proposal 被 defer 过）。
DEFAULT_ALLOWED_FROM_STATES: Tuple[str, ...] = (
    "proposed",       # v1.0 schema 默认
    "pending",        # 用户文档 alias（兼容）
    "under_review",   # 之前 defer 过的可以再次治理
    "deferred",       # 同上
)


class ApprovalDecisionStore:
    """极简内存决策存储（R2.5.3 只要求审计可追溯；不引入外部 DB）。"""

    def __init__(self) -> None:
        self._by_id: Dict[str, ApprovalDecision] = {}
        self._by_proposal: Dict[str, List[str]] = {}

    def save(self, decision: ApprovalDecision) -> ApprovalDecision:
        did = decision.get("id")
        if not did:
            raise ValueError("decision.id 为空")
        self._by_id[did] = decision
        pid = decision.get("proposal_id", "")
        self._by_proposal.setdefault(pid, []).append(did)
        return decision

    def get(self, decision_id: str) -> Optional[ApprovalDecision]:
        return self._by_id.get(decision_id)

    def list_by_proposal(self, proposal_id: str) -> List[ApprovalDecision]:
        return [self._by_id[x] for x in self._by_proposal.get(proposal_id, []) if x in self._by_id]


_DEFAULT_DECISION_STORE: Optional[ApprovalDecisionStore] = None


def get_default_decision_store() -> ApprovalDecisionStore:
    global _DEFAULT_DECISION_STORE
    if _DEFAULT_DECISION_STORE is None:
        _DEFAULT_DECISION_STORE = ApprovalDecisionStore()
    return _DEFAULT_DECISION_STORE


class ApprovalManager:
    """Evolution Governance Layer 协调器：Policy → Decision → 更新 proposal 元数据。"""

    def __init__(
        self,
        *,
        policy: Optional[ApprovalPolicy] = None,
        proposal_manager: Any = None,  # 弱类型；避免循环 import proposal_manager（已在 growth_integration 持有）
        decision_store: Optional[ApprovalDecisionStore] = None,
        allowed_from_states: Optional[List[str]] = None,
    ) -> None:
        self.policy = policy or ApprovalPolicy()
        self.proposal_manager = proposal_manager
        self.decision_store = decision_store or get_default_decision_store()
        self.allowed_from_states: List[str] = list(allowed_from_states or DEFAULT_ALLOWED_FROM_STATES)

    # ============================================================
    # 入口：治理一个 GrowthProposal
    # ============================================================
    def govern_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """
        返回：
          {
            "governed": bool,          # 是否真的完成了治理
            "decision_id": str | None,
            "decision": str | None,    # approved / rejected / deferred
            "proposal_status": str | None,   # 更新后的 proposal.status
            "reasons": List[str],
            "error": str | None,       # 异常时记录
            "isolated": bool,          # 异常是否隔离（True: 不会 propagate 到 accept_experience）
          }
        """
        try:
            return self._govern_safe(proposal_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[approval_govern_crash_isolated] proposal=%s error=%s", proposal_id, exc)
            return {
                "governed": False,
                "decision_id": None,
                "decision": None,
                "proposal_status": None,
                "reasons": [],
                "error": f"approval_crash_isolated: {exc!r}",
                "isolated": True,
            }

    # ============================================================
    # 内部实现
    # ============================================================
    def _govern_safe(self, proposal_id: str) -> Dict[str, Any]:
        if not self.proposal_manager:
            return {
                "governed": False,
                "decision_id": None,
                "decision": None,
                "proposal_status": None,
                "reasons": ["approval_manager_missing_proposal_manager_binding"],
                "error": None,
                "isolated": False,
            }

        proposal = self.proposal_manager.get_proposal(proposal_id)
        if proposal is None:
            return {
                "governed": False,
                "decision_id": None,
                "decision": None,
                "proposal_status": None,
                "reasons": [f"proposal_not_found: {proposal_id}"],
                "error": None,
                "isolated": False,
            }

        snap = self._to_snapshot(proposal)
        recommendation: PolicyRecommendation = self.policy.evaluate(snap)

        # 构建 ApprovalDecision
        decision_obj: ApprovalDecision = build_approval_decision(
            proposal_id=proposal.id,
            decision=recommendation.decision,
            reasons=list(recommendation.reasons),
            confidence=float(recommendation.decision_confidence or 0.0),
            reviewer="system",
        )
        self.decision_store.save(decision_obj)

        # 计算新 status（status_hint 优先；否则按 decision 映射）
        new_status: Optional[str] = None
        if recommendation.status_hint:
            new_status = recommendation.status_hint
        elif recommendation.decision == "approved":
            new_status = "approved"
        elif recommendation.decision == "rejected":
            new_status = "rejected"
        elif recommendation.decision == "deferred":
            new_status = "deferred"

        meta_updates = {"approval_decision_id": decision_obj.get("id")}
        # 也把 reasons / decision / confidence 塞进 evaluator_meta，便于审计 UI 直接读
        meta_updates["approval_decision"] = recommendation.decision
        meta_updates["approval_reasons"] = list(recommendation.reasons)
        meta_updates["approval_confidence"] = float(recommendation.decision_confidence or 0.0)

        # 安全更新
        update_res = self.proposal_manager.update_proposal_status_and_meta(
            proposal.id,
            new_status=new_status,
            meta_updates=meta_updates,
            from_states=list(self.allowed_from_states),
        )
        updated_proposal = update_res.get("proposal") or proposal
        final_status = getattr(updated_proposal, "status", None) or proposal.status

        return {
            "governed": update_res.get("status") == "updated",
            "decision_id": decision_obj.get("id"),
            "decision": recommendation.decision,
            "proposal_status": final_status,
            "reasons": list(recommendation.reasons),
            "error": None,
            "isolated": False,
        }

    # ============================================================
    # GrowthProposal → ProposalSnapshot
    # ============================================================
    @staticmethod
    def _to_snapshot(proposal: Any) -> ProposalSnapshot:
        em = dict(getattr(proposal, "evaluator_meta", None) or {})
        changes: List[Dict[str, Any]] = []
        raw_changes = list(getattr(proposal, "proposed_changes", None) or [])
        for c in raw_changes:
            # GrowthProposal.proposed_changes 是 List[ChangeItem]（dataclass 或 dict）
            if hasattr(c, "__dict__") and not isinstance(c, dict):
                c_dict = {
                    "path": getattr(c, "path", None),
                    "before": getattr(c, "before", None),
                    "after": getattr(c, "after", None),
                    "reason": getattr(c, "reason", None),
                }
            else:
                c_dict = dict(c)
            changes.append(c_dict)
        occurrence = int(em.get("occurrence_count") or 1)
        return ProposalSnapshot(
            proposal_id=str(getattr(proposal, "id", "") or ""),
            evaluator_confidence=float(getattr(proposal, "confidence", 0.0) or 0.0),
            evidence_ids=list(getattr(proposal, "evidence_ids", None) or []),
            proposed_changes=changes,
            evaluator_meta=em,
            needs_review=bool(em.get("needs_review")),
            occurrence_count=occurrence if occurrence >= 1 else 1,
        )
