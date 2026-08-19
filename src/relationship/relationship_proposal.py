# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段3:RelationshipProposal —— 关系核心候选的治理生命周期。

状态机(禁止跳过任何一环):

    candidate → evaluating → pending_review → accepted → activated
                                             ↘ rejected(终态)

核心安全约束(继承 GrowthProposal 治理思想,但不复制其数据结构):
- 禁止「LLM 认为重要 → 自动成为核心关系」;
- accepted 只能通过 approve(人工审核人, reason) 达成,transition() 永远不能到达;
- rejected 是终态:不可激活、不可再审批;
- 每次成功流转都写审计(audit:action/actor/reason/timestamp/before/after);
- 激活(activated)只代表流程完成,真正的落库动作由外部编排执行,
  本类本身不做任何写盘/写白名单操作。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

PROPOSAL_STATUS = {
    "CANDIDATE": "candidate",
    "EVALUATING": "evaluating",
    "PENDING_REVIEW": "pending_review",
    "ACCEPTED": "accepted",
    "REJECTED": "rejected",
    "ACTIVATED": "activated",
}

# transition() 唯一允许的合法迁移;accepted/rejected/activated 只能
# 经 approve()/reject()/activate() 达成,保证每一环都有审计与语义。
_LEGAL_TRANSITIONS = {
    PROPOSAL_STATUS["CANDIDATE"]: {PROPOSAL_STATUS["EVALUATING"]},
    PROPOSAL_STATUS["EVALUATING"]: {PROPOSAL_STATUS["PENDING_REVIEW"]},
}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


class RelationshipProposal:
    """关系核心候选提案(五态状态机 + 完整审计)。

    字段:
    - source_memory_ids 支撑记忆 id(可追溯)
    - source_user_id    关系对象
    - category          评估分类(relationship_core_candidate / ...)
    - score             评估分数
    - audit             全生命周期审计,每条含 action/actor/reason/timestamp/before/after
    """

    def __init__(
        self,
        proposal_id: Optional[str] = None,
        source_memory_ids: Optional[List[str]] = None,
        source_user_id: str = "",
        category: str = "relationship_core_candidate",
        score: float = 0.0,
        reason: str = "",
        status: Optional[str] = None,
        audit: Optional[List[Dict[str, Any]]] = None,
    ):
        self.proposal_id = str(proposal_id or "") or f"relp_{uuid4().hex[:12]}"
        self.source_memory_ids = _safe_str_list(source_memory_ids)
        self.source_user_id = _safe_str(source_user_id)
        self.category = _safe_str(category, "relationship_core_candidate")
        try:
            self.score = float(score or 0.0)
        except (TypeError, ValueError):
            self.score = 0.0
        self.reason = _safe_str(reason)
        self.status = (
            _safe_str(status, PROPOSAL_STATUS["CANDIDATE"])
            if status in PROPOSAL_STATUS.values()
            else PROPOSAL_STATUS["CANDIDATE"]
        )
        self.audit: List[Dict[str, Any]] = [
            dict(e) for e in audit if isinstance(e, dict)
        ] if isinstance(audit, list) else []
        self.created_at = _now()
        self.updated_at = self.created_at

    # ------------------------------------------------------------
    # 状态机
    # ------------------------------------------------------------

    def transition(self, new_status: str, actor: str = "", reason: str = "") -> bool:
        """内部流转:仅 candidate→evaluating、evaluating→pending_review。

        accepted / rejected / activated 一律拒绝——只能通过 approve/reject/activate。
        """
        if new_status not in _LEGAL_TRANSITIONS.get(self.status, set()):
            return False
        self._record("transition", actor, reason, self.status, new_status)
        self.status = new_status
        return True

    def approve(self, reviewer: str, reason: str = "") -> bool:
        """人工审批通过。必须有审核人,且只能从 pending_review 出发。"""
        if self.status != PROPOSAL_STATUS["PENDING_REVIEW"]:
            return False
        reviewer = _safe_str(reviewer).strip()
        if not reviewer:
            return False
        self._record("approve", reviewer, reason, self.status, PROPOSAL_STATUS["ACCEPTED"])
        self.status = PROPOSAL_STATUS["ACCEPTED"]
        return True

    def reject(self, reviewer: str, reason: str = "") -> bool:
        """人工驳回。必须有审核人,且只能从 pending_review 出发;rejected 为终态。"""
        if self.status != PROPOSAL_STATUS["PENDING_REVIEW"]:
            return False
        reviewer = _safe_str(reviewer).strip()
        if not reviewer:
            return False
        self._record("reject", reviewer, reason, self.status, PROPOSAL_STATUS["REJECTED"])
        self.status = PROPOSAL_STATUS["REJECTED"]
        return True

    def activate(self, actor: str = "", reason: str = "") -> bool:
        """激活标记:只能从 accepted 出发(落库动作由外部编排执行)。"""
        if self.status != PROPOSAL_STATUS["ACCEPTED"]:
            return False
        self._record("activate", actor, reason, self.status, PROPOSAL_STATUS["ACTIVATED"])
        self.status = PROPOSAL_STATUS["ACTIVATED"]
        return True

    # ------------------------------------------------------------
    # 审计与序列化
    # ------------------------------------------------------------

    def _record(self, action: str, actor: str, reason: str,
                before: str, after: str) -> None:
        self.audit.append({
            "action": action,
            "actor": _safe_str(actor),
            "reason": _safe_str(reason),
            "timestamp": _now(),
            "before": before,
            "after": after,
        })
        self.updated_at = self.audit[-1]["timestamp"]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status,
            "source_memory_ids": list(self.source_memory_ids),
            "source_user_id": self.source_user_id,
            "category": self.category,
            "score": self.score,
            "reason": self.reason,
            "audit": [dict(e) for e in self.audit],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipProposal":
        data = data if isinstance(data, dict) else {}
        return cls(
            proposal_id=_safe_str(data.get("proposal_id")),
            source_memory_ids=data.get("source_memory_ids"),
            source_user_id=_safe_str(data.get("source_user_id")),
            category=_safe_str(data.get("category"), "relationship_core_candidate"),
            score=data.get("score") or 0.0,
            reason=_safe_str(data.get("reason")),
            status=_safe_str(data.get("status")),
            audit=data.get("audit"),
        )


__all__ = ["PROPOSAL_STATUS", "RelationshipProposal"]
