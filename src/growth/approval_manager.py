"""
Phase 3.5.13: Growth Approval Layer
Phase 6.0: Runtime Growth Integration

职责：
- 封装 GrowthAdapter，为 GrowthProposal 提供人工审批生命周期管理
- 记录每一次审批操作（approve / reject / modify）的完整审计记录
- 不自动接受 Proposal（所有状态流转需显式人工触发）
- 不修改 Personality System（仅管理 Proposal 状态，不直接写人格数据）
- Phase 6.0 接入 ProposalLifecycleManager：所有审批流转都走统一生命周期

设计原则：
- 审批管理器是 GrowthAdapter 之上的审计层，不替换底层存储
- 每次操作生成 ApprovalRecord，保留 before/after 状态快照
- modify 操作支持修改 ChangeItem 内容后批准
- 历史记录可独立持久化（JSON 文件），便于审计回溯
- 生命周期记录与审计记录解耦，互不干扰
- lifecycle_manager 为可选参数，缺省时审批仍可工作（向后兼容 Phase 3.5.13）

约束：
- 不修改 GrowthProposal 的核心字段定义
- 不修改 Personality System
- 不自动接受任何 Proposal
"""

from __future__ import annotations

import inspect
import json
import logging
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.growth_schema import GrowthProposal, ChangeItem
from src.contracts.growth_approval_schema import (
    ACTION_APPROVE,
    ACTION_REJECT,
    ACTION_MODIFY,
    ApprovalRecord,
    ApprovalHistory,
    ApprovalSnapshot,
    ChangeDiff,
    now_iso,
)

logger = logging.getLogger(__name__)

# Phase 6.0: 延迟导入避免硬依赖
try:
    from src.growth.lifecycle_manager import (
        ProposalLifecycleManager,
        LifecycleState,
        IllegalLifecycleTransition,
    )
    _LIFECYCLE_AVAILABLE = True
except Exception:  # pragma: no cover
    ProposalLifecycleManager = None  # type: ignore
    LifecycleState = None  # type: ignore
    IllegalLifecycleTransition = None  # type: ignore
    _LIFECYCLE_AVAILABLE = False


class ApprovalManager:
    """
    GrowthProposal 审批管理器。

    用法：
        manager = ApprovalManager(growth_adapter=adapter)
        # 批准
        record = manager.approve_proposal("prop_xxx", reason="符合身份原则")
        # 拒绝
        record = manager.reject_proposal("prop_yyy", reason="与记忆冲突")
        # 修改后批准
        record = manager.modify_proposal(
            "prop_zzz",
            changes=[{"path": "self_state.initiative", "after": -0.05}],
            reason="降低调整幅度",
        )
        # 查询
        pending = manager.get_pending_proposals()
        history = manager.get_approval_history(limit=20)
    """

    def __init__(
        self,
        growth_adapter: Any,
        history_path: Optional[str] = None,
        lifecycle_manager: Optional[Any] = None,
        apply_hook: Optional[Any] = None,
    ):
        """
        Args:
            growth_adapter: GrowthAdapter 实例（提供 proposal 存储与状态流转）
            history_path: 审批历史持久化路径（可选，不传则仅内存）
            lifecycle_manager: Phase 6.0 — ProposalLifecycleManager 实例（可选）
            apply_hook: Phase 6.0 — PersonalityAdapter.apply_proposal 的回调，
                        签名 (proposal, actor) -> envelope dict。
                        若提供，审批后会自动触发 applying → applied 流转。
        """
        self._adapter = growth_adapter
        self._history_path = Path(history_path) if history_path else None
        if self._history_path:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._history_path.exists():
                self._save_history_to_file(ApprovalHistory())

        self._history: ApprovalHistory = self._load_history_from_file()

        # Phase 6.0: 生命周期管理接入
        self._lifecycle = lifecycle_manager
        self._apply_hook = apply_hook

    # ============================================================
    # 公开接口
    # ============================================================

    def approve_proposal(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "human",
    ) -> Optional[ApprovalRecord]:
        """
        批准 GrowthProposal。

        流程（Phase 6.0 Lifecycle）：
        1. 查找 proposal
        2. 记录操作前快照
        3. Lifecycle: pending → approved
        4. 调用 adapter.accept_proposal() 更新状态
        5. Lifecycle: approved → applying
        6. （可选）通过 apply_hook 触发 PersonalityAdapter.apply_proposal
        7. Lifecycle: applying → applied（或 applying → failed）
        8. 生成 ApprovalRecord 审计记录
        9. 持久化历史

        Returns:
            ApprovalRecord，若 proposal 不存在返回 None
        """
        proposal = self._find_proposal(proposal_id)
        if proposal is None:
            logger.warning(f"approve_proposal: 提案不存在 {proposal_id}")
            return None

        before_status = proposal.status
        before_snapshot = proposal.to_dict()

        # 执行状态流转
        updated = self._adapter.accept_proposal(proposal_id)
        if updated is None:
            logger.error(f"approve_proposal: 状态流转失败 {proposal_id}")
            return None

        after_status = updated.status

        # G-1.3.2: 先构造真实审批凭证（record_id 预生成）——
        # apply_hook 与最终 ApprovalRecord 复用同一 record_id，凭证同源绑定。
        record_id = f"apr_{uuid.uuid4().hex[:10]}"
        approval_evidence = {
            "record_id": record_id,
            "proposal_id": str(proposal_id),
            "reviewer_id": actor,
            "reviewed_at": now_iso(),
            "decision": "approve",
        }

        # Phase 6.0: 生命周期流转 pending → approved → applying → applied/failed
        apply_result = self._run_lifecycle_after_approve(
            updated,
            actor=actor,
            approval_evidence=approval_evidence,
        )

        # 生成审计记录（复用同一 record_id，与 apply 凭证绑定）
        record = ApprovalRecord(
            record_id=record_id,
            proposal_id=proposal_id,
            proposal_snapshot=before_snapshot,
            action=ACTION_APPROVE,
            actor=actor,
            reason=reason or "人工批准",
            before_status=before_status,
            after_status=after_status,
            changes=[],
            metadata={
                "confidence": updated.confidence,
                "evidence_count": len(updated.evidence_ids),
                "phase_6_0": True,
                "apply_result": apply_result or {"applied": False, "skipped": True},
            },
        )

        self._add_record(record)
        logger.info(f"提案已批准: {proposal_id} (reason={reason})")
        return record

    def reject_proposal(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "human",
    ) -> Optional[ApprovalRecord]:
        """
        拒绝 GrowthProposal。

        Phase 6.0: Lifecycle pending → rejected
        """
        proposal = self._find_proposal(proposal_id)
        if proposal is None:
            logger.warning(f"reject_proposal: 提案不存在 {proposal_id}")
            return None

        before_status = proposal.status
        before_snapshot = proposal.to_dict()

        # 执行状态流转
        updated = self._adapter.reject_proposal(proposal_id)
        if updated is None:
            logger.error(f"reject_proposal: 状态流转失败 {proposal_id}")
            return None

        after_status = updated.status

        # Phase 6.0: 生命周期 pending → rejected
        self._run_lifecycle_reject(
            proposal_id=proposal_id,
            before_status=before_status,
            actor=actor,
            reason=reason,
        )

        # 生成审计记录
        record = ApprovalRecord(
            proposal_id=proposal_id,
            proposal_snapshot=before_snapshot,
            action=ACTION_REJECT,
            actor=actor,
            reason=reason or "人工拒绝",
            before_status=before_status,
            after_status=after_status,
            changes=[],
            metadata={
                "confidence": updated.confidence,
                "evidence_count": len(updated.evidence_ids),
                "phase_6_0": True,
            },
        )

        self._add_record(record)
        logger.info(f"提案已拒绝: {proposal_id} (reason={reason})")
        return record

    def modify_proposal(
        self,
        proposal_id: str,
        changes: List[Dict[str, Any]],
        reason: str = "",
        actor: str = "human",
    ) -> Optional[ApprovalRecord]:
        """
        修改 GrowthProposal 的 ChangeItem 后批准。

        流程：
        1. 查找 proposal
        2. 记录修改前的 proposed_changes
        3. 应用修改（更新指定 path 的 after 值）
        4. 调用 adapter.update_proposal() 持久化
        5. 调用 adapter.accept_proposal() 更新状态为 accepted
        6. 生成带 ChangeDiff 的 ApprovalRecord

        Args:
            proposal_id: 提案 ID
            changes: 修改项列表，每项含 path 和 after（可选 before/reason）
            reason: 修改原因
            actor: 操作者

        Returns:
            ApprovalRecord，若 proposal 不存在返回 None
        """
        proposal = self._find_proposal(proposal_id)
        if proposal is None:
            logger.warning(f"modify_proposal: 提案不存在 {proposal_id}")
            return None

        before_status = proposal.status
        before_snapshot = proposal.to_dict()

        # 记录修改差异
        change_diffs: List[ChangeDiff] = []
        change_map: Dict[str, Dict[str, Any]] = {
            c.get("path", ""): c for c in changes if c.get("path")
        }

        # 应用修改到 proposed_changes
        for item in proposal.proposed_changes:
            if item.path in change_map:
                mod = change_map[item.path]
                diff = ChangeDiff(
                    path=item.path,
                    before=item.after,  # 修改前的 after 值
                    after=mod.get("after", item.after),
                    reason=mod.get("reason", reason),
                )
                change_diffs.append(diff)
                # 应用修改
                item.after = mod.get("after", item.after)
                if mod.get("reason"):
                    item.reason = mod.get("reason")

        # 持久化修改后的 proposal
        self._adapter.update_proposal(proposal)

        # 状态流转：修改后批准
        updated = self._adapter.accept_proposal(proposal_id)
        if updated is None:
            logger.error(f"modify_proposal: 修改后状态流转失败 {proposal_id}")
            return None

        after_status = updated.status

        # Phase 6.0: 生命周期 pending → approved → applying → applied/failed
        apply_result = self._run_lifecycle_after_approve(updated, actor=actor)

        # 生成审计记录
        record = ApprovalRecord(
            proposal_id=proposal_id,
            proposal_snapshot=before_snapshot,
            action=ACTION_MODIFY,
            actor=actor,
            reason=reason or "人工修改后批准",
            before_status=before_status,
            after_status=after_status,
            changes=change_diffs,
            metadata={
                "confidence": updated.confidence,
                "evidence_count": len(updated.evidence_ids),
                "modified_paths": [d.path for d in change_diffs],
                "phase_6_0": True,
                "apply_result": apply_result or {"applied": False, "skipped": True},
            },
        )

        self._add_record(record)
        logger.info(
            f"提案已修改并批准: {proposal_id} "
            f"(修改 {len(change_diffs)} 项, reason={reason})"
        )
        return record

    def get_pending_proposals(self, limit: int = 50) -> List[GrowthProposal]:
        """获取待审批的提案（status='proposed'）"""
        return self._adapter.list_proposals(status="proposed", limit=limit)

    def get_approval_history(self, limit: int = 50) -> List[ApprovalRecord]:
        """获取审批历史（最新在前）"""
        return self._history.get_recent(limit=limit)

    def get_proposal_history(self, proposal_id: str) -> List[ApprovalRecord]:
        """获取指定提案的审批历史"""
        return self._history.get_by_proposal(proposal_id)

    def get_snapshot(self) -> ApprovalSnapshot:
        """生成审批管理器快照"""
        last_record = self._history.records[-1] if self._history.records else None
        pending = self.get_pending_proposals(limit=1000)
        return ApprovalSnapshot(
            enabled=True,
            total_records=len(self._history.records),
            total_approvals=self._history.total_approvals,
            total_rejections=self._history.total_rejections,
            total_modifications=self._history.total_modifications,
            pending_count=len(pending),
            last_record_id=last_record.record_id if last_record else "",
            last_action=last_record.action if last_record else "",
        )

    def clear_history(self) -> int:
        """清空审批历史，返回清理数"""
        n = len(self._history.records)
        self._history = ApprovalHistory()
        if self._history_path:
            self._save_history_to_file(self._history)
        return n

    # ============================================================
    # 内部方法
    # ============================================================

    def _find_proposal(self, proposal_id: str) -> Optional[GrowthProposal]:
        """从 adapter 查找 proposal"""
        proposals = self._adapter.list_proposals(limit=10000)
        for p in proposals:
            if p.id == proposal_id:
                return p
        return None

    @staticmethod
    def _call_apply_hook(
        hook: Any,
        proposal: GrowthProposal,
        actor: str,
        approval_evidence: Optional[Dict[str, Any]],
    ) -> Any:
        """G-1.3.2: 优先三参调用 hook(proposal, actor, approval_evidence)；
        旧两参 hook 按签名兼容回退 hook(proposal, actor)。"""
        try:
            signature = inspect.signature(hook)
            positional = [
                p for p in signature.parameters.values()
                if p.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if len(positional) >= 3:
                return hook(proposal, actor, approval_evidence)
        except (TypeError, ValueError):
            pass
        return hook(proposal, actor)

    def _run_lifecycle_after_approve(
        self,
        proposal: GrowthProposal,
        actor: str = "human",
        approval_evidence: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Phase 6.0: 审批通过后执行生命周期与 apply 钩子。

        流程：
        1. pending → approved
        2. approved → applying
        3. apply_hook（若提供）→ envelope
        4. applying → applied（成功）/ failed（失败）

        G-1.3.2: apply_hook 携带 approval_evidence（真实审批凭证），
        旧两参 hook 自动兼容。

        Returns:
            apply envelope dict 或 None（无 hook 时）
        """
        if self._lifecycle is None:
            # 不接入 lifecycle 时的传统行为
            if self._apply_hook is None:
                return None
            # 仅执行 apply
            try:
                envelope = self._call_apply_hook(
                    self._apply_hook, proposal, actor, approval_evidence,
                )
                return envelope if isinstance(envelope, dict) else {"raw": envelope}
            except Exception as e:  # pragma: no cover
                logger.error(f"apply_hook 执行失败: {e}")
                return {"applied": False, "error": str(e)}

        # 接入 lifecycle
        proposal_id = proposal.id
        try:
            self._lifecycle.record_transition(
                proposal_id=proposal_id,
                from_state="pending",
                to_state="approved",
                actor=actor,
                reason="approval_manager.approve",
            )
        except Exception as e:
            logger.warning(f"lifecycle pending→approved 失败（已隔离）: {e}")

        try:
            self._lifecycle.record_transition(
                proposal_id=proposal_id,
                from_state="approved",
                to_state="applying",
                actor=actor,
                reason="approval_manager.applying",
            )
        except Exception as e:
            logger.warning(f"lifecycle approved→applying 失败（已隔离）: {e}")

        # 调用 apply hook（若提供）
        envelope: Optional[Dict[str, Any]] = None
        apply_success = True
        if self._apply_hook is not None:
            try:
                envelope = self._call_apply_hook(
                    self._apply_hook, proposal, actor, approval_evidence,
                )
                if not isinstance(envelope, dict):
                    envelope = {"raw": envelope}
                apply_success = bool(envelope.get("applied", False))
            except Exception as e:
                logger.error(f"apply_hook 异常: {e}")
                envelope = {"applied": False, "error": str(e)}
                apply_success = False
        else:
            envelope = {"applied": False, "skipped": True, "reason": "no_apply_hook"}
            apply_success = False

        # applying → applied / failed
        try:
            self._lifecycle.record_transition(
                proposal_id=proposal_id,
                from_state="applying",
                to_state="applied" if apply_success else "failed",
                actor=actor,
                reason=("apply_success" if apply_success else "apply_failed"),
                metadata={"envelope": envelope or {}},
            )
        except Exception as e:
            logger.warning(f"lifecycle applying→applied/failed 失败（已隔离）: {e}")

        return envelope

    def _run_lifecycle_reject(
        self,
        proposal_id: str,
        before_status: str,
        actor: str,
        reason: str,
    ) -> None:
        """
        Phase 6.0: 拒绝时的生命周期流转 pending → rejected。
        容忍 lifecycle 缺失 / 状态非法，不影响审计。
        """
        if self._lifecycle is None:
            return
        try:
            # lifecycle 内部已对 legacy "proposed" 做了归一化（→ pending），
            # 所以从 before_status 直接转换到 rejected 是合法的。
            # 注意：rejected 是 PENDING 的合法目标，不需要先经过 approved。
            self._lifecycle.record_transition(
                proposal_id=proposal_id,
                from_state=before_status or "proposed",
                to_state="rejected",
                actor=actor,
                reason=reason or "approval_manager.reject",
            )
        except Exception as e:
            logger.warning(f"lifecycle reject 流转失败（已隔离）: {e}")

    def get_lifecycle_state(self, proposal_id: str) -> str:
        """Phase 6.0: 获取 Proposal 当前生命周期状态"""
        if self._lifecycle is None:
            return "disabled"
        try:
            return self._lifecycle.get_state(proposal_id)
        except Exception:
            return "unknown"

    def get_lifecycle_history(self, proposal_id: str, limit: int = 20) -> List[Any]:
        """Phase 6.0: 获取 Proposal 生命周期历史"""
        if self._lifecycle is None:
            return []
        try:
            return self._lifecycle.get_history(proposal_id, limit=limit)
        except Exception:
            return []

    def _add_record(self, record: ApprovalRecord) -> None:
        """添加审计记录并持久化"""
        self._history.add(record)
        if self._history_path:
            self._save_history_to_file(self._history)

    def _load_history_from_file(self) -> ApprovalHistory:
        """从文件加载历史"""
        if not self._history_path or not self._history_path.exists():
            return ApprovalHistory()
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records = []
            for r in data.get("records", []):
                changes = [ChangeDiff(**c) for c in r.get("changes", [])]
                records.append(ApprovalRecord(
                    record_id=r.get("record_id", ""),
                    timestamp=r.get("timestamp", now_iso()),
                    proposal_id=r.get("proposal_id", ""),
                    proposal_snapshot=r.get("proposal_snapshot", {}),
                    action=r.get("action", ""),
                    actor=r.get("actor", "human"),
                    reason=r.get("reason", ""),
                    before_status=r.get("before_status", ""),
                    after_status=r.get("after_status", ""),
                    changes=changes,
                    metadata=r.get("metadata", {}),
                ))
            history = ApprovalHistory(records=records)
            # 重算统计
            history.total_approvals = sum(1 for r in records if r.action == ACTION_APPROVE)
            history.total_rejections = sum(1 for r in records if r.action == ACTION_REJECT)
            history.total_modifications = sum(1 for r in records if r.action == ACTION_MODIFY)
            return history
        except Exception as e:
            logger.error(f"加载审批历史失败: {e}")
            return ApprovalHistory()

    def _save_history_to_file(self, history: ApprovalHistory) -> None:
        """持久化历史到文件"""
        if not self._history_path:
            return
        try:
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump(history.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存审批历史失败: {e}")
