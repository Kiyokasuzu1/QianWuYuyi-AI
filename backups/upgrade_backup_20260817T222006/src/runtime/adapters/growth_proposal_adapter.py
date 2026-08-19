# -*- coding: utf-8 -*-
"""
src/runtime/adapters/growth_proposal_adapter.py

Phase 5.5: GrowthProposalAdapter —— 双 Schema 兼容适配层

目的：
- 桥接两个不兼容的 GrowthProposal 数据类：
  * Type A: src.growth.proposal.proposal.GrowthProposal  (Admin Governance 使用)
  * Type B: src.contracts.growth_schema.GrowthProposal    (Runtime / ApprovalManager 使用)
- 实现 Type A ↔ Type B 双向无损转换
- 不修改 RuntimeCore / Orchestrator / ApprovalManager / PersonalityAdapter
- 不删除任何旧 Schema
- 不创建任何 Authority 实例

强约束：
1. Adapter 本身是纯函数式转换器（无副作用）
2. 不直接 apply 任何变更（保留审批机制）
3. 不绕过 ProposalStorage / ApprovalManager
4. 所有原 Schema 字段在转换中保留在 evaluator_meta / metadata 中

设计原则：
- Type A 主键: proposal_id
- Type B 主键: id
- 状态映射：Type A 有 applied/cancelled 等扩展状态，全部以 evaluator_meta 透传
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 状态映射常量
# ============================================================

# Type A 状态集
TYPE_A_STATUSES = {"pending", "approved", "rejected", "applied", "cancelled"}
# Type B 状态集
TYPE_B_STATUSES = {"proposed", "approved", "rejected"}

# Type A → Type B 状态映射
STATUS_A_TO_B: Dict[str, str] = {
    "pending": "proposed",
    "approved": "approved",
    "rejected": "rejected",
    # applied 在 Type B 中没有直接对应，但语义上更接近 approved
    # 保留在 evaluator_meta.source_status 中以便回写
    "applied": "approved",
    "cancelled": "rejected",
}

# Type B → Type A 状态映射（默认映射；如有 source_status 优先用）
STATUS_B_TO_A: Dict[str, str] = {
    "proposed": "pending",
    "approved": "approved",
    "rejected": "rejected",
}

# 字段透传映射：Type A → evaluator_meta 子键
A_TO_META_KEYS = {
    "source": "source",
    "user_id": "user_id",
    "priority": "priority",
    "expires_at": "expires_at",
    "applied_at": "applied_at",
    "applied_by": "applied_by",
    "reviewer_id": "reviewer_id",
    "review_comment": "review_comment",
    "reviewed_at": "reviewed_at",
    "proposal_type": "proposal_type",
}


class GrowthProposalAdapter:
    """
    双 GrowthProposal Schema 兼容适配器（Phase 5.5）。

    提供：
      - type_a_to_type_b(type_a) -> Type B GrowthProposal
      - type_b_to_type_a(type_b) -> Type A GrowthProposal
      - to_type_b(proposal)      -> 智能识别并转换为 Type B
      - to_type_a(proposal)      -> 智能识别并转换为 Type A
      - build_approval_input(type_a) -> ApprovalManager 可接受的输入

    不持有任何 Authority 引用。
    不直接 apply 任何变更。
    不修改 RuntimeCore / Orchestrator / ApprovalManager。
    """

    # ============================================================
    # 类型识别
    # ============================================================

    @staticmethod
    def detect_type(proposal: Any) -> str:
        """
        智能识别 Proposal 类型。

        判定规则：
        - 有 `proposal_id` 字段且无 `proposed_changes` 字段 → Type A
        - 有 `id` 字段且有 `proposed_changes` 字段 → Type B
        - 其它 → 'unknown'

        Args:
            proposal: 任意 Proposal 实例

        Returns:
            "type_a" / "type_b" / "unknown"
        """
        if proposal is None:
            return "unknown"
        has_proposal_id = hasattr(proposal, "proposal_id")
        has_proposed_changes = hasattr(proposal, "proposed_changes")
        has_id = hasattr(proposal, "id")
        has_evidence_ids = hasattr(proposal, "evidence_ids")

        if has_proposal_id and not has_proposed_changes and not has_evidence_ids:
            return "type_a"
        if has_proposed_changes and (has_evidence_ids or has_id):
            return "type_b"
        if has_proposal_id and has_id and proposal.proposal_id == getattr(proposal, "id", None):
            # 同时有两个字段，但值相同（可能复用）
            return "type_b" if has_proposed_changes else "type_a"
        return "unknown"

    # ============================================================
    # Type A → Type B
    # ============================================================

    @classmethod
    def type_a_to_type_b(cls, type_a_proposal: Any) -> Any:
        """
        将 Type A GrowthProposal 转换为 Type B GrowthProposal。

        字段映射：
            proposal_id           -> id
            affected_dimensions   -> proposed_changes (List[ChangeItem])
            before_state / after_state -> ChangeItem.before / ChangeItem.after
            evidence              -> evidence_ids
            metadata              -> evaluator_meta
            其他 Type A 扩展字段   -> evaluator_meta 子键
            reason                -> evaluator_meta["reason"]

        状态映射：
            pending   -> proposed
            approved  -> approved
            rejected  -> rejected
            applied   -> approved (+ evaluator_meta["source_status"]="applied")
            cancelled -> rejected (+ evaluator_meta["source_status"]="cancelled")

        Args:
            type_a_proposal: src.growth.proposal.proposal.GrowthProposal 实例

        Returns:
            src.contracts.growth_schema.GrowthProposal 实例
        """
        from src.contracts.growth_schema import GrowthProposal as TypeBProposal, ChangeItem

        if type_a_proposal is None:
            raise ValueError("type_a_proposal 不能为 None")

        # 状态映射
        original_status = getattr(type_a_proposal, "status", "pending")
        target_status = STATUS_A_TO_B.get(original_status, "proposed")

        # 构建 evaluator_meta（透传 Type A 扩展字段）
        evaluator_meta: Dict[str, Any] = {
            "adapter": "GrowthProposalAdapter",
            "adapter_version": "phase_5_5",
            "source_schema": "type_a",
            "source_proposal_type": getattr(type_a_proposal, "proposal_type", ""),
        }

        # 透传 reason
        reason = getattr(type_a_proposal, "reason", "") or ""
        if reason:
            evaluator_meta["reason"] = reason

        # 透传 source_status（如果原状态是 applied / cancelled）
        if original_status in ("applied", "cancelled"):
            evaluator_meta["source_status"] = original_status

        # 透传扩展字段到 evaluator_meta
        for a_key, meta_key in A_TO_META_KEYS.items():
            val = getattr(type_a_proposal, a_key, None)
            if val is not None and val != "":
                evaluator_meta[meta_key] = val

        # 合并已有 metadata（如果 Type A proposal 有 metadata）
        existing_metadata = getattr(type_a_proposal, "metadata", {}) or {}
        if existing_metadata:
            # 不覆盖已设置的 evaluator_meta 字段
            for k, v in existing_metadata.items():
                if k not in evaluator_meta:
                    evaluator_meta[k] = v

        # 构建 proposed_changes
        proposed_changes: List[ChangeItem] = []
        affected_dimensions = getattr(type_a_proposal, "affected_dimensions", {}) or {}
        before_state = getattr(type_a_proposal, "before_state", {}) or {}
        after_state = getattr(type_a_proposal, "after_state", {}) or {}

        for dim, delta in affected_dimensions.items():
            before_val = before_state.get(dim)
            after_val = after_state.get(dim)
            # 优先使用 after_state；若缺失但有 before + delta，计算
            if after_val is None and before_val is not None:
                try:
                    after_val = float(before_val) + float(delta)
                except (TypeError, ValueError):
                    after_val = None
            proposed_changes.append(ChangeItem(
                path=dim,
                before=before_val,
                after=after_val,
                reason=reason or None,
            ))

        # 透传 evidence
        evidence_ids: List[str] = list(getattr(type_a_proposal, "evidence", []) or [])

        # 构建 Type B
        type_b = TypeBProposal(
            id=getattr(type_a_proposal, "proposal_id", ""),
            source_event_id=getattr(type_a_proposal, "source_event_id", "") or None,
            proposed_changes=proposed_changes,
            confidence=float(getattr(type_a_proposal, "confidence", 0.0) or 0.0),
            evidence_ids=evidence_ids,
            evaluator_meta=evaluator_meta,
            timestamp=getattr(type_a_proposal, "timestamp", "") or "",
            status=target_status,
        )

        logger.debug(
            f"[GrowthProposalAdapter] Type A → Type B: "
            f"{getattr(type_a_proposal, 'proposal_id', '?')} ({original_status} → {target_status})"
        )

        return type_b

    # ============================================================
    # Type B → Type A
    # ============================================================

    @classmethod
    def type_b_to_type_a(cls, type_b_proposal: Any) -> Any:
        """
        将 Type B GrowthProposal 转换为 Type A GrowthProposal。

        字段映射：
            id                       -> proposal_id
            proposed_changes         -> affected_dimensions / before_state / after_state
            evidence_ids             -> evidence
            evaluator_meta           -> metadata（保留 source 字段）
            evaluator_meta 中透传字段 -> Type A 顶层字段（如 source, user_id 等）

        状态映射：
            proposed  -> pending
            approved  -> approved
            rejected  -> rejected
            若 evaluator_meta["source_status"] == "applied"  -> applied
            若 evaluator_meta["source_status"] == "cancelled" -> cancelled

        Args:
            type_b_proposal: src.contracts.growth_schema.GrowthProposal 实例

        Returns:
            src.growth.proposal.proposal.GrowthProposal 实例
        """
        from src.growth.proposal.proposal import GrowthProposal as TypeAProposal
        from src.growth.proposal.constants import (
            PROPOSAL_TYPE,
            PROPOSAL_STATUS,
            PRIORITY_LEVEL,
        )

        if type_b_proposal is None:
            raise ValueError("type_b_proposal 不能为 None")

        # 状态映射
        original_status = getattr(type_b_proposal, "status", "proposed")
        target_status = STATUS_B_TO_A.get(original_status, "pending")

        # 透传 source_status（如果存在）
        evaluator_meta = getattr(type_b_proposal, "evaluator_meta", {}) or {}
        source_status = evaluator_meta.get("source_status")
        if source_status in ("applied", "cancelled"):
            target_status = source_status

        # 从 proposed_changes 重建 affected_dimensions / before_state / after_state
        proposed_changes = getattr(type_b_proposal, "proposed_changes", []) or []
        affected_dimensions: Dict[str, float] = {}
        before_state: Dict[str, float] = {}
        after_state: Dict[str, float] = {}

        for ci in proposed_changes:
            path = getattr(ci, "path", "")
            if not path:
                continue
            before = getattr(ci, "before", None)
            after = getattr(ci, "after", None)
            if after is not None:
                try:
                    after_state[path] = float(after)
                    if before is not None:
                        try:
                            delta = float(after) - float(before)
                            affected_dimensions[path] = delta
                            before_state[path] = float(before)
                        except (TypeError, ValueError):
                            affected_dimensions[path] = float(after)
                    else:
                        affected_dimensions[path] = float(after)
                except (TypeError, ValueError):
                    pass
            elif before is not None:
                try:
                    before_state[path] = float(before)
                except (TypeError, ValueError):
                    pass

        # 提取 evidence
        evidence = list(getattr(type_b_proposal, "evidence_ids", []) or [])

        # 提取 metadata：排除已知的 Type A 字段映射
        known_meta_keys = set(A_TO_META_KEYS.values()) | {
            "adapter", "adapter_version", "source_schema",
            "source_proposal_type", "reason", "source_status",
        }
        metadata: Dict[str, Any] = {
            k: v for k, v in evaluator_meta.items()
            if k not in known_meta_keys
        }
        # 在 metadata 中保留 adapter 来源标记
        metadata["_converted_from"] = "type_b"
        metadata["_adapter"] = "GrowthProposalAdapter"
        metadata["_adapter_version"] = "phase_5_5"

        # 提取 Type A 顶层字段
        source = evaluator_meta.get("source", "")
        user_id = evaluator_meta.get("user_id", "")
        priority = evaluator_meta.get("priority", PRIORITY_LEVEL["MEDIUM"])
        expires_at = evaluator_meta.get("expires_at")
        applied_at = evaluator_meta.get("applied_at")
        applied_by = evaluator_meta.get("applied_by", "")
        reviewer_id = evaluator_meta.get("reviewer_id", "")
        review_comment = evaluator_meta.get("review_comment", "")
        reviewed_at = evaluator_meta.get("reviewed_at")
        proposal_type = evaluator_meta.get(
            "source_proposal_type",
            evaluator_meta.get("proposal_type", PROPOSAL_TYPE["PERSONALITY"])
        )

        # 提取 reason（evaluator_meta 中）
        reason = evaluator_meta.get("reason", "")

        # 构建 Type A
        type_a = TypeAProposal(
            proposal_id=getattr(type_b_proposal, "id", ""),
            timestamp=getattr(type_b_proposal, "timestamp", "") or "",
            proposal_type=proposal_type,
            status=target_status,
            source=source,
            source_event_id=getattr(type_b_proposal, "source_event_id", "") or "",
            user_id=user_id,
            affected_dimensions=affected_dimensions,
            before_state=before_state,
            after_state=after_state,
            confidence=float(getattr(type_b_proposal, "confidence", 0.0) or 0.0),
            reason=reason,
            evidence=evidence,
            priority=priority,
            reviewer_id=reviewer_id,
            review_comment=review_comment,
            reviewed_at=reviewed_at,
            applied_at=applied_at,
            applied_by=applied_by,
            expires_at=expires_at,
            metadata=metadata,
        )

        logger.debug(
            f"[GrowthProposalAdapter] Type B → Type A: "
            f"{getattr(type_b_proposal, 'id', '?')} ({original_status} → {target_status})"
        )

        return type_a

    # ============================================================
    # 智能转换
    # ============================================================

    @classmethod
    def to_type_b(cls, proposal: Any) -> Any:
        """
        智能转换为 Type B。
        - Type A → type_a_to_type_b
        - Type B → 原样返回
        - 其它 → 抛出 ValueError
        """
        t = cls.detect_type(proposal)
        if t == "type_b":
            return proposal
        if t == "type_a":
            return cls.type_a_to_type_b(proposal)
        raise ValueError(f"无法识别 Proposal 类型: {type(proposal)}")

    @classmethod
    def to_type_a(cls, proposal: Any) -> Any:
        """
        智能转换为 Type A。
        - Type B → type_b_to_type_a
        - Type A → 原样返回
        - 其它 → 抛出 ValueError
        """
        t = cls.detect_type(proposal)
        if t == "type_a":
            return proposal
        if t == "type_b":
            return cls.type_b_to_type_a(proposal)
        raise ValueError(f"无法识别 Proposal 类型: {type(proposal)}")

    # ============================================================
    # 构造 ApprovalManager 可接受的输入
    # ============================================================

    @classmethod
    def build_approval_input(cls, type_a_proposal: Any) -> Any:
        """
        构造 ApprovalManager 可接受的输入（Type B GrowthProposal）。

        这就是 Phase 5.5 的核心价值：
        Admin 提交的 Type A Proposal 经此转换后，
        可以被 ApprovalManager 接受并最终 apply 到 PersonalityResolver。

        Args:
            type_a_proposal: Admin 提交的 Type A GrowthProposal

        Returns:
            Type B GrowthProposal（可直接交给 ApprovalManager）
        """
        return cls.type_a_to_type_b(type_a_proposal)

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def get_status_mapping_info() -> Dict[str, Dict[str, str]]:
        """
        返回状态映射表，用于文档和调试。
        """
        return {
            "type_a_to_type_b": STATUS_A_TO_B,
            "type_b_to_type_a": STATUS_B_TO_A,
            "type_a_statuses": {s: s for s in sorted(TYPE_A_STATUSES)},
            "type_b_statuses": {s: s for s in sorted(TYPE_B_STATUSES)},
        }

    @staticmethod
    def get_field_mapping_info() -> Dict[str, Any]:
        """
        返回字段映射表，用于文档和调试。
        """
        return {
            "type_a_to_type_b": {
                "proposal_id": "id",
                "affected_dimensions": "proposed_changes[].path (with before/after)",
                "before_state": "proposed_changes[].before",
                "after_state": "proposed_changes[].after",
                "evidence": "evidence_ids",
                "metadata": "evaluator_meta (merged)",
                "source": "evaluator_meta.source",
                "user_id": "evaluator_meta.user_id",
                "priority": "evaluator_meta.priority",
                "expires_at": "evaluator_meta.expires_at",
                "applied_at": "evaluator_meta.applied_at",
                "applied_by": "evaluator_meta.applied_by",
                "reviewer_id": "evaluator_meta.reviewer_id",
                "review_comment": "evaluator_meta.review_comment",
                "reviewed_at": "evaluator_meta.reviewed_at",
                "proposal_type": "evaluator_meta.source_proposal_type",
                "reason": "evaluator_meta.reason",
            },
            "type_b_to_type_a": {
                "id": "proposal_id",
                "proposed_changes": "affected_dimensions + before_state + after_state",
                "evidence_ids": "evidence",
                "evaluator_meta (subset)": "metadata",
                "evaluator_meta.source": "source",
                "evaluator_meta.user_id": "user_id",
                "evaluator_meta.priority": "priority",
                "evaluator_meta.expires_at": "expires_at",
                "evaluator_meta.applied_at": "applied_at",
                "evaluator_meta.applied_by": "applied_by",
                "evaluator_meta.reviewer_id": "reviewer_id",
                "evaluator_meta.review_comment": "review_comment",
                "evaluator_meta.reviewed_at": "reviewed_at",
                "evaluator_meta.source_proposal_type": "proposal_type",
                "evaluator_meta.reason": "reason",
                "evaluator_meta.source_status (applied/cancelled)": "status (overrides)",
            },
        }
