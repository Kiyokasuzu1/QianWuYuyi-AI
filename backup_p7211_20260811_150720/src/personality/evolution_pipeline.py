"""
Phase 4.0 — R2.5.4: EvolutionPipeline（人格演化执行管道）

职责：
  把 approved proposal → EvolutionRecord → PersonalityState.apply_evolution() → IdentityContinuity Check

这是 Growth 闭环的最后一厘米：
  R2.5.3 Approval 说「允许改变」
  R2.5.4 EvolutionPipeline 执行「真的成为她的一部分」

红线（EP-1 ~ EP-4）：
  EP-1: 只有 proposal.status == "approved" 才能 apply（rejected/deferred/pending → 必须失败）
  EP-2: 同一 proposal_id 不可重复 apply（幂等保护，在 PersonalityState 内部）
  EP-3: 任何 PersonalityState 变化必须有 proposal_id + approval_id + evolution_record_id
  EP-4: Identity Anchor 二次保护（PersonalityState 内部硬拒 identity.core.*/manifesto.*）

不修改：
  - persona.md / identity.md / 任何磁盘文件
  - 不调 LLM / 不生成回复
  - 不绕过 Approval（只接受已 approved 的 proposal）

生命周期：
  Proposal(status=approved)
      ↓
  EvolutionPipeline.execute(proposal, approval_decision)
      ↓
  build_evolution_record(proposal_id, approval_id, before, after, reasons)
      ↓
  PersonalityState.apply_evolution(record)
      ↓
  IdentityContinuity Check（post-evolution：「改变以后还是不是羽依？」）
      ↓
  返回 EvolutionResult
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.personality.evolution_record import (
    EvolutionRecord,
    build_evolution_record,
    ALLOWED_CHANGE_TYPES,
)
from src.personality.personality_state import (
    PersonalityState,
    get_personality_state,
)

logger = logging.getLogger(__name__)


class EvolutionPipeline:
    """R2.5.4: 人格演化执行管道。"""

    def __init__(
        self,
        *,
        personality_state: Optional[PersonalityState] = None,
    ) -> None:
        self.state = personality_state or get_personality_state()

    # ============================================================
    # 入口：执行一个已 approved 的 proposal
    # ============================================================
    def execute(
        self,
        proposal: Any,
        approval_decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        执行人格演化。

        Args:
            proposal: GrowthProposal 对象（或 duck-typed 对象 with .id/.status/.proposed_changes/.confidence/.evaluator_meta）
            approval_decision: ApprovalDecision dict（必须含 id + decision + reasons）

        Returns:
          {
            "executed": bool,           # 是否成功执行了演化
            "evolution_record_id": str | None,
            "proposal_id": str,
            "approval_id": str,
            "applied_traits": Dict[str, {"before": float, "after": float}],
            "blocked_identity": List[str],
            "identity_continuity_ok": bool,  # post-evolution identity check
            "error": str | None,
            "isolated": bool,           # 异常是否隔离
          }
        """
        try:
            return self._execute_safe(proposal, approval_decision)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[evolution_pipeline_crash_isolated] error=%s", exc)
            return {
                "executed": False,
                "evolution_record_id": None,
                "proposal_id": str(getattr(proposal, "id", "") or ""),
                "approval_id": str(approval_decision.get("id", "") or ""),
                "applied_traits": {},
                "blocked_identity": [],
                "identity_continuity_ok": True,  # 没改就不需要检查
                "error": f"evolution_pipeline_crash: {exc!r}",
                "isolated": True,
            }

    # ============================================================
    # 内部实现
    # ============================================================
    def _execute_safe(
        self,
        proposal: Any,
        approval_decision: Dict[str, Any],
    ) -> Dict[str, Any]:
        proposal_id = str(getattr(proposal, "id", "") or "")
        approval_id = str(approval_decision.get("id", "") or "")
        decision = str(approval_decision.get("decision", "") or "")

        # EP-1: 只有 approved 才能 apply
        proposal_status = str(getattr(proposal, "status", "") or "")
        if proposal_status != "approved":
            return self._fail(
                proposal_id, approval_id,
                f"EP-1: proposal status={proposal_status!r} 不是 'approved'，不允许 apply",
            )

        # EP-1 (补充): ApprovalDecision.decision 也必须是 approved
        if decision != "approved":
            return self._fail(
                proposal_id, approval_id,
                f"EP-1: approval_decision.decision={decision!r} 不是 'approved'",
            )

        # EP-2: 幂等检查（提前检查，避免不必要的 build）
        if self.state.has_proposal_been_applied(proposal_id):
            return self._fail(
                proposal_id, approval_id,
                f"EP-2: proposal_id={proposal_id} 已被应用过，不可重复执行",
            )

        # 提取 before/after 和 change_type
        before_snap, after_snap, change_type = self._extract_changes(proposal)

        if not after_snap:
            return self._fail(
                proposal_id, approval_id,
                "proposal.proposed_changes 无法提取任何 trait 变化",
            )

        # 构建 EvolutionRecord
        reasons = list(approval_decision.get("reasons") or [])
        if not reasons:
            reasons = ["approved_by_governance"]

        try:
            record = build_evolution_record(
                proposal_id=proposal_id,
                approval_id=approval_id,
                change_type=change_type,
                before=before_snap,
                after=after_snap,
                reasons=reasons,
                confidence=float(getattr(proposal, "confidence", 0.0) or 0.0),
            )
        except ValueError as exc:
            return self._fail(proposal_id, approval_id, f"build_evolution_record failed: {exc}")

        # EP-3: record 必须有 proposal_id + approval_id（build_evolution_record 已保证）
        # 执行 apply
        apply_result = self.state.apply_evolution(record)

        # Identity Continuity Check（post-evolution）
        identity_ok = self._identity_continuity_check(apply_result)

        return {
            "executed": bool(apply_result.get("applied")),
            "evolution_record_id": record.get("record_id"),
            "proposal_id": proposal_id,
            "approval_id": approval_id,
            "applied_traits": apply_result.get("affected_traits", {}),
            "blocked_identity": apply_result.get("blocked_identity", []),
            "identity_continuity_ok": identity_ok,
            "error": apply_result.get("error"),
            "isolated": False,
        }

    # ============================================================
    # 从 proposal.proposed_changes 提取 before/after
    # ============================================================
    @staticmethod
    def _extract_changes(proposal: Any) -> tuple:
        """返回 (before_dict, after_dict, change_type)。key 使用完整 path（identity 检查需要完整前缀）。"""
        before: Dict[str, float] = {}
        after: Dict[str, float] = {}
        change_type = "trait_delta"

        # 检查 evaluator_meta 里是否有 transition 信息
        em = dict(getattr(proposal, "evaluator_meta", None) or {})
        if em.get("transition_proposal_mode") == "gradual_transition":
            change_type = "interest_transition"
        elif em.get("transition_proposal_mode") == "new_interest_emerging":
            change_type = "new_trait"

        changes = list(getattr(proposal, "proposed_changes", None) or [])
        for c in changes:
            # ChangeItem dataclass or dict
            if hasattr(c, "__dict__") and not isinstance(c, dict):
                path = str(getattr(c, "path", "") or "")
                c_before = getattr(c, "before", None)
                c_after = getattr(c, "after", None)
            else:
                path = str(c.get("path", "") or "")
                c_before = c.get("before")
                c_after = c.get("after")

            if not path:
                continue

            try:
                before_val = float(c_before) if c_before is not None else 0.5
                after_val = float(c_after) if c_after is not None else before_val
            except Exception:  # noqa: BLE001
                continue

            # 保留完整 path 作为 key（PersonalityState 需要完整前缀做 identity 检查）
            before[path] = round(before_val, 6)
            after[path] = round(after_val, 6)

        return before, after, change_type

    # ============================================================
    # Identity Continuity Check（post-evolution）
    # ============================================================
    @staticmethod
    def _identity_continuity_check(apply_result: Dict[str, Any]) -> bool:
        """
        改变以后还是不是羽依？

        简单检查：
        - 如果有 identity 被拦截 → True（说明保护生效了，没有实际改变 identity）
        - 如果 applied_traits 里有 trait 名以 identity. 开头 → False（不应该发生，但二次保险）
        - 否则 True
        """
        # 如果 identity 被拦截，保护生效 → True
        blocked = apply_result.get("blocked_identity") or []
        if blocked:
            return True  # 保护拦截了，identity 没变

        # 检查 applied 里有没有 identity 路径（不应该发生，但保险）
        applied = apply_result.get("affected_traits") or {}
        for trait_name in applied:
            if str(trait_name).startswith("identity.") or str(trait_name).startswith("manifesto."):
                logger.error(
                    "[identity_continuity_violation] trait=%s 被应用到 PersonalityState！", trait_name,
                )
                return False

        return True

    # ============================================================
    # 辅助
    # ============================================================
    @staticmethod
    def _fail(proposal_id: str, approval_id: str, error: str) -> Dict[str, Any]:
        logger.warning("[evolution_pipeline_failed] proposal=%s error=%s", proposal_id, error)
        return {
            "executed": False,
            "evolution_record_id": None,
            "proposal_id": proposal_id,
            "approval_id": approval_id,
            "applied_traits": {},
            "blocked_identity": [],
            "identity_continuity_ok": True,
            "error": error,
            "isolated": False,
        }
