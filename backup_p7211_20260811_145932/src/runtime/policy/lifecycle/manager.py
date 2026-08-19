# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/manager.py

Phase C.10.10 — Policy Lifecycle Manager (统一入口)

本文件实现:
- PolicyLifecycleManager  完整生命周期管理

状态机:
PENDING
   ↓
APPROVED (人工 / auto_approve)
   ↓
APPLYING (apply 进行中)
   ↓
APPLIED (apply 成功)
   ↓
VERIFIED (verify 成功)

异常:
PENDING → REJECTED
APPROVED/APPLIED → ROLLED_BACK
APPLYING → FAILED (apply 异常)

设计原则:
- 严格走 Proposal → Approval → Apply → Verify → Rollback 闭环
- 默认 auto_apply_enabled=False,显式开启才允许自动 apply
- 任何异常 fail-soft,不阻塞 Runtime
- 全部操作写入 PolicyAuditStore
- 状态切换发布 EventBus 事件
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..feedback.proposal import PolicyAdjustmentProposal, ProposalStore
from .apply_service import (
    APPLY_OUTCOME_FAILED,
    APPLY_OUTCOME_REJECTED,
    APPLY_OUTCOME_SUCCESS,
    PolicyApplyConfig,
    PolicyApplyResult,
    PolicyApplyService,
)
from .approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_CANCELLED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    PolicyApprovalRecord,
    PolicyApprovalStore,
    build_approval_record,
)
from .audit import (
    AUDIT_EVENT_APPLIED,
    AUDIT_EVENT_APPROVED,
    AUDIT_EVENT_FAILED,
    AUDIT_EVENT_REJECTED,
    AUDIT_EVENT_ROLLBACK,
    AUDIT_EVENT_SNAPSHOT,
    AUDIT_EVENT_VERIFIED,
    PolicyAuditStore,
)
from .snapshot import PolicySnapshotManager
from .verifier import (
    VerificationResult,
    PolicyVerifier,
)

logger = logging.getLogger(__name__)


# ============================================================
# 状态机常量
# ============================================================
LIFECYCLE_STATE_PENDING = "pending"
LIFECYCLE_STATE_APPROVED = "approved"
LIFECYCLE_STATE_APPLYING = "applying"
LIFECYCLE_STATE_APPLIED = "applied"
LIFECYCLE_STATE_VERIFIED = "verified"
LIFECYCLE_STATE_FAILED = "failed"
LIFECYCLE_STATE_REJECTED = "rejected"
LIFECYCLE_STATE_ROLLED_BACK = "rolled_back"

LIFECYCLE_TERMINAL_STATES = frozenset({
    LIFECYCLE_STATE_VERIFIED,
    LIFECYCLE_STATE_REJECTED,
    LIFECYCLE_STATE_ROLLED_BACK,
    LIFECYCLE_STATE_FAILED,
})


# ============================================================
# LifecycleState (per proposal)
# ============================================================
@dataclass
class _LifecycleEntry:
    """单条 proposal 的 lifecycle 状态。

    字段:
    - proposal_id
    - state
    - approval_record_id
    - apply_result
    - verify_result
    - snapshot_id
    - updated_at
    - history:  状态切换历史
    """

    proposal_id: str = ""
    state: str = LIFECYCLE_STATE_PENDING
    approval_record_id: str = ""
    apply_result: Optional[PolicyApplyResult] = None
    verify_result: Optional[VerificationResult] = None
    snapshot_id: str = ""
    updated_at: float = 0.0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "proposal_id": str(self.proposal_id),
                "state": str(self.state),
                "approval_record_id": str(self.approval_record_id),
                "apply_result": self.apply_result.to_dict() if self.apply_result else None,
                "verify_result": self.verify_result.to_dict() if self.verify_result else None,
                "snapshot_id": str(self.snapshot_id),
                "updated_at": float(self.updated_at),
                "history": list(self.history or []),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("_LifecycleEntry.to_dict 异常(已隔离): %s", exc)
            return {
                "proposal_id": str(self.proposal_id),
                "state": LIFECYCLE_STATE_PENDING,
                "approval_record_id": "",
                "apply_result": None,
                "verify_result": None,
                "snapshot_id": "",
                "updated_at": 0.0,
                "history": [],
            }


# ============================================================
# PolicyLifecycleManager
# ============================================================
class PolicyLifecycleManager:
    """Policy Lifecycle 统一入口。

    用法:
        manager = build_default_lifecycle_manager(
            proposal_store=feedback_engine.store,
            throttle_registry=layer.throttle_registry,
            runtime_budget=layer.runtime_budget,
            publisher=publish_event,
        )
        manager.approve(proposal_id, reviewer="admin")
        manager.apply(proposal_id)
        manager.verify(proposal_id)
        manager.rollback(proposal_id, reason="verify_failed")
    """

    def __init__(
        self,
        proposal_store: Optional[ProposalStore] = None,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        approval_store: Optional[PolicyApprovalStore] = None,
        audit_store: Optional[PolicyAuditStore] = None,
        snapshot_manager: Optional[PolicySnapshotManager] = None,
        apply_service: Optional[PolicyApplyService] = None,
        verifier: Optional[PolicyVerifier] = None,
        apply_config: Optional[PolicyApplyConfig] = None,
        event_publisher: Any = None,
    ) -> None:
        self._lock = threading.RLock()
        self._proposal_store = proposal_store
        self._throttle_registry = throttle_registry
        self._runtime_budget = runtime_budget
        self._approval_store = approval_store or PolicyApprovalStore()
        self._audit_store = audit_store or PolicyAuditStore()
        self._snapshot_manager = snapshot_manager or PolicySnapshotManager()
        self._apply_service = apply_service or PolicyApplyService(
            throttle_registry=throttle_registry,
            runtime_budget=runtime_budget,
            snapshot_manager=self._snapshot_manager,
            config=apply_config or PolicyApplyConfig.manual_mode(),
        )
        self._verifier = verifier or PolicyVerifier(
            throttle_registry=throttle_registry,
        )
        self._event_publisher = event_publisher
        # per-proposal lifecycle state
        self._entries: Dict[str, _LifecycleEntry] = {}
        # 通用统计
        self._stats: Dict[str, int] = {
            "approve_total": 0,
            "reject_total": 0,
            "apply_total": 0,
            "apply_success": 0,
            "apply_failed": 0,
            "verify_total": 0,
            "verify_ok": 0,
            "rollback_total": 0,
            "errors": 0,
        }

    # --------------------------------------------------------
    # 访问器
    # --------------------------------------------------------
    @property
    def proposal_store(self) -> Optional[ProposalStore]:
        return self._proposal_store

    @property
    def approval_store(self) -> PolicyApprovalStore:
        return self._approval_store

    @property
    def audit_store(self) -> PolicyAuditStore:
        return self._audit_store

    @property
    def snapshot_manager(self) -> PolicySnapshotManager:
        return self._snapshot_manager

    @property
    def apply_service(self) -> PolicyApplyService:
        return self._apply_service

    @property
    def verifier(self) -> PolicyVerifier:
        return self._verifier

    def configure(
        self,
        throttle_registry: Any = None,
        runtime_budget: Any = None,
        apply_config: Optional[PolicyApplyConfig] = None,
        event_publisher: Any = None,
    ) -> None:
        with self._lock:
            if throttle_registry is not None:
                self._throttle_registry = throttle_registry
            if runtime_budget is not None:
                self._runtime_budget = runtime_budget
            if event_publisher is not None:
                self._event_publisher = event_publisher
            # 同步给 apply_service / verifier
            self._apply_service.configure(
                throttle_registry=self._throttle_registry,
                runtime_budget=self._runtime_budget,
                snapshot_manager=self._snapshot_manager,
                config=apply_config,
            )
            self._verifier.configure(
                throttle_registry=self._throttle_registry,
            )

    def get_stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return dict(self._stats)
        except Exception:  # noqa: BLE001
            return {}

    def reset_stats(self) -> None:
        try:
            with self._lock:
                for k in self._stats:
                    self._stats[k] = 0
        except Exception:  # noqa: BLE001
            pass

    def get_state(self, proposal_id: str) -> str:
        try:
            with self._lock:
                entry = self._entries.get(str(proposal_id or ""))
                if entry is None:
                    return LIFECYCLE_STATE_PENDING
                return str(entry.state)
        except Exception:  # noqa: BLE001
            return LIFECYCLE_STATE_PENDING

    def get_entry(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._lock:
                entry = self._entries.get(str(proposal_id or ""))
                if entry is None:
                    return None
                return entry.to_dict()
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_entry 异常(已隔离): %s", exc)
            return None

    def list_entries(self) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                return [e.to_dict() for e in self._entries.values()]
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------
    # 辅助:获取 proposal
    # --------------------------------------------------------
    def _get_proposal(self, proposal_id: str) -> Optional[PolicyAdjustmentProposal]:
        try:
            store = self._proposal_store
            if store is None:
                return None
            get_method = getattr(store, "get", None)
            if callable(get_method):
                return get_method(str(proposal_id or ""))
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("_get_proposal 异常(已隔离): %s", exc)
            return None

    def _set_entry_state(
        self,
        proposal_id: str,
        new_state: str,
        audit_event: str = "",
        actor: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            with self._lock:
                entry = self._entries.get(str(proposal_id or ""))
                if entry is None:
                    entry = _LifecycleEntry(proposal_id=str(proposal_id or ""))
                    self._entries[str(proposal_id or "")] = entry
                old_state = entry.state
                entry.state = str(new_state or LIFECYCLE_STATE_PENDING)
                entry.updated_at = float(time.time())
                entry.history.append({
                    "from": old_state,
                    "to": entry.state,
                    "at": entry.updated_at,
                    "actor": actor,
                    "event": audit_event,
                    "metadata": dict(metadata or {}),
                })
                if audit_event:
                    self._audit_store.record(
                        event=audit_event,
                        proposal_id=str(proposal_id or ""),
                        module=(
                            str(metadata.get("module", ""))
                            if metadata else ""
                        ),
                        parameter=(
                            str(metadata.get("parameter", ""))
                            if metadata else ""
                        ),
                        actor=actor or "system",
                        metadata=metadata or {},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_set_entry_state 异常(已隔离): %s", exc)

    # --------------------------------------------------------
    # 事件发布
    # --------------------------------------------------------
    def _publish_event(self, event: Any) -> None:
        try:
            publisher = self._event_publisher
            if publisher is None:
                return
            publisher(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_publish_event 异常(已隔离): %s", exc)

    def _build_event(self, event_type: str, **kwargs: Any) -> Any:
        try:
            from src.events.events import (
                RuntimePolicyAppliedEvent,
                RuntimePolicyProposalApprovedEvent,
                RuntimePolicyRollbackEvent,
                YuyiEvent,
            )
            cls_map: Dict[str, Any] = {
                "approved": RuntimePolicyProposalApprovedEvent,
                "applied": RuntimePolicyAppliedEvent,
                "rollback": RuntimePolicyRollbackEvent,
            }
            cls = cls_map.get(event_type, YuyiEvent)
            try:
                return cls(**kwargs)
            except Exception:
                return YuyiEvent(
                    event_type=f"runtime.policy_{event_type}",
                    source="runtime",
                    data=dict(kwargs or {}),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_build_event 异常(已隔离): %s", exc)
            return None

    # --------------------------------------------------------
    # 1. approve
    # --------------------------------------------------------
    def approve(
        self,
        proposal_id: str,
        reviewer: str = "",
        reason: str = "",
        auto: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """批准一条 proposal。返回 True 表示已批准,False 表示已被 reject / 重复 / 失败。"""
        try:
            with self._lock:
                self._stats["approve_total"] += 1
            pid = str(proposal_id or "")
            if not pid:
                with self._lock:
                    self._stats["errors"] += 1
                return False

            # 检查 proposal 是否存在
            proposal = self._get_proposal(pid)
            if proposal is None:
                self._audit_store.record(
                    event=AUDIT_EVENT_FAILED,
                    proposal_id=pid,
                    actor=reviewer or "system",
                    success=False,
                    error="proposal_not_found",
                )
                with self._lock:
                    self._stats["errors"] += 1
                return False

            # 检查 lifecycle 状态(已 verified / rejected / rolled_back 不可再 approve)
            cur_state = self.get_state(pid)
            if cur_state in LIFECYCLE_TERMINAL_STATES:
                self._audit_store.record(
                    event=AUDIT_EVENT_REJECTED,
                    proposal_id=pid,
                    module=str(proposal.module),
                    parameter=str(proposal.parameter),
                    actor=reviewer or "system",
                    success=False,
                    error=f"already_terminal:{cur_state}",
                )
                with self._lock:
                    self._stats["reject_total"] += 1
                return False

            # 检查 duplicate approve(已有 approved 记录)
            existing = self._approval_store.get_approved_for(pid)
            if existing is not None:
                self._audit_store.record(
                    event=AUDIT_EVENT_REJECTED,
                    proposal_id=pid,
                    module=str(proposal.module),
                    parameter=str(proposal.parameter),
                    actor=reviewer or "system",
                    success=False,
                    error="duplicate_approve",
                )
                with self._lock:
                    self._stats["reject_total"] += 1
                return False

            # 写入 approval
            rec = build_approval_record(
                proposal_id=pid,
                status=APPROVAL_STATUS_APPROVED,
                reviewer=reviewer or (self._apply_service.config.actor or "system"),
                reason=reason or "",
                metadata=dict(metadata or {}),
                auto_approved=bool(auto),
            )
            ok = self._approval_store.append(rec)
            if not ok:
                with self._lock:
                    self._stats["errors"] += 1
                return False

            # 更新 lifecycle
            self._set_entry_state(
                proposal_id=pid,
                new_state=LIFECYCLE_STATE_APPROVED,
                audit_event=AUDIT_EVENT_APPROVED,
                actor=reviewer or "system",
                metadata={
                    "module": str(proposal.module),
                    "parameter": str(proposal.parameter),
                    "reviewer": str(reviewer or ""),
                    "auto": bool(auto),
                    "record_id": str(rec.record_id),
                },
            )

            # 发布事件
            ev = self._build_event(
                "approved",
                proposal_id=pid,
                module=str(proposal.module),
                parameter=str(proposal.parameter),
                reviewer=str(reviewer or ""),
                record_id=str(rec.record_id),
            )
            if ev is not None:
                self._publish_event(ev)

            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("PolicyLifecycleManager.approve 异常(已隔离): %s", exc)
            return False

    # --------------------------------------------------------
    # 1b. reject
    # --------------------------------------------------------
    def reject(
        self,
        proposal_id: str,
        reviewer: str = "",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        try:
            with self._lock:
                self._stats["reject_total"] += 1
            pid = str(proposal_id or "")
            proposal = self._get_proposal(pid)
            if proposal is None:
                self._audit_store.record(
                    event=AUDIT_EVENT_REJECTED,
                    proposal_id=pid,
                    actor=reviewer or "system",
                    success=False,
                    error="proposal_not_found",
                )
                return False

            rec = build_approval_record(
                proposal_id=pid,
                status=APPROVAL_STATUS_REJECTED,
                reviewer=reviewer or "system",
                reason=reason or "",
                metadata=dict(metadata or {}),
                auto_approved=False,
            )
            self._approval_store.append(rec)
            self._set_entry_state(
                proposal_id=pid,
                new_state=LIFECYCLE_STATE_REJECTED,
                audit_event=AUDIT_EVENT_REJECTED,
                actor=reviewer or "system",
                metadata={
                    "module": str(proposal.module),
                    "parameter": str(proposal.parameter),
                    "reviewer": str(reviewer or ""),
                    "reason": str(reason or ""),
                },
            )
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("PolicyLifecycleManager.reject 异常(已隔离): %s", exc)
            return False

    # --------------------------------------------------------
    # 2. apply
    # --------------------------------------------------------
    def apply(
        self,
        proposal_id: str,
        actor: str = "",
        auto: bool = False,
    ) -> PolicyApplyResult:
        try:
            with self._lock:
                self._stats["apply_total"] += 1
            pid = str(proposal_id or "")
            proposal = self._get_proposal(pid)
            if proposal is None:
                with self._lock:
                    self._stats["apply_failed"] += 1
                return PolicyApplyResult(
                    outcome=APPLY_OUTCOME_FAILED,
                    proposal_id=pid,
                    error="proposal_not_found",
                )

            module = str(proposal.module or "")
            parameter = str(proposal.parameter or "")

            # 检查 approval
            if self._apply_service.config.require_approval:
                approved = self._approval_store.get_approved_for(pid)
                if approved is None:
                    with self._lock:
                        self._stats["apply_failed"] += 1
                    self._audit_store.record(
                        event=AUDIT_EVENT_FAILED,
                        proposal_id=pid,
                        module=module,
                        parameter=parameter,
                        actor=actor or "system",
                        success=False,
                        error="approval_required",
                    )
                    return PolicyApplyResult(
                        outcome=APPLY_OUTCOME_REJECTED,
                        proposal_id=pid,
                        module=module,
                        parameter=parameter,
                        old_value=proposal.old_value,
                        new_value=proposal.suggested_value,
                        error="approval_required",
                    )

            # 标记 applying
            self._set_entry_state(
                proposal_id=pid,
                new_state=LIFECYCLE_STATE_APPLYING,
                audit_event=AUDIT_EVENT_SNAPSHOT,
                actor=actor or "system",
                metadata={
                    "module": module,
                    "parameter": parameter,
                },
            )

            # 真正 apply
            result = self._apply_service.apply(proposal)
            # 记录结果
            with self._lock:
                entry = self._entries.get(pid)
                if entry is not None:
                    entry.apply_result = result
                    if result.snapshot_id:
                        entry.snapshot_id = str(result.snapshot_id)

            if result.outcome == APPLY_OUTCOME_SUCCESS:
                with self._lock:
                    self._stats["apply_success"] += 1
                self._set_entry_state(
                    proposal_id=pid,
                    new_state=LIFECYCLE_STATE_APPLIED,
                    audit_event=AUDIT_EVENT_APPLIED,
                    actor=actor or "system",
                    metadata={
                        "module": module,
                        "parameter": parameter,
                        "old_value": result.old_value,
                        "new_value": result.new_value,
                        "snapshot_id": str(result.snapshot_id),
                    },
                )
                ev = self._build_event(
                    "applied",
                    proposal_id=pid,
                    module=module,
                    old_value=result.old_value,
                    new_value=result.new_value,
                    success=True,
                )
                if ev is not None:
                    self._publish_event(ev)
            else:
                with self._lock:
                    self._stats["apply_failed"] += 1
                self._set_entry_state(
                    proposal_id=pid,
                    new_state=LIFECYCLE_STATE_FAILED,
                    audit_event=AUDIT_EVENT_FAILED,
                    actor=actor or "system",
                    metadata={
                        "module": str(proposal.module),
                        "parameter": str(proposal.parameter),
                        "error": str(result.error),
                    },
                )
            return result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["apply_failed"] += 1
                self._stats["errors"] += 1
            logger.debug("PolicyLifecycleManager.apply 异常(已隔离): %s", exc)
            return PolicyApplyResult(
                outcome=APPLY_OUTCOME_FAILED,
                proposal_id=str(proposal_id or ""),
                error=f"apply_exception:{exc}",
            )

    # --------------------------------------------------------
    # 3. verify
    # --------------------------------------------------------
    def verify(
        self,
        proposal_id: str,
        delay_seconds: float = 0.0,
    ) -> VerificationResult:
        try:
            with self._lock:
                self._stats["verify_total"] += 1
            pid = str(proposal_id or "")
            proposal = self._get_proposal(pid)
            if proposal is None:
                with self._lock:
                    self._stats["verify_failed" if False else "verify_total"] += 0
                return VerificationResult(
                    ok=False,
                    proposal_id=pid,
                    message="proposal_not_found",
                )

            # 检查是否已 applied
            cur_state = self.get_state(pid)
            if cur_state not in (LIFECYCLE_STATE_APPLIED, LIFECYCLE_STATE_VERIFIED):
                return VerificationResult(
                    ok=False,
                    proposal_id=pid,
                    message=f"invalid_state_for_verify:{cur_state}",
                )

            result = self._verifier.verify(proposal, delay_seconds=delay_seconds)
            with self._lock:
                entry = self._entries.get(pid)
                if entry is not None:
                    entry.verify_result = result
            if result.ok:
                with self._lock:
                    self._stats["verify_ok"] += 1
                self._set_entry_state(
                    proposal_id=pid,
                    new_state=LIFECYCLE_STATE_VERIFIED,
                    audit_event=AUDIT_EVENT_VERIFIED,
                    actor="verifier",
                    metadata={
                        "module": str(proposal.module),
                        "parameter": str(proposal.parameter),
                        "value_match": bool(result.value_match),
                        "error_rate_ok": bool(result.error_rate_ok),
                    },
                )
            else:
                self._audit_store.record(
                    event=AUDIT_EVENT_FAILED,
                    proposal_id=pid,
                    module=str(proposal.module),
                    parameter=str(proposal.parameter),
                    actor="verifier",
                    success=False,
                    error=f"verify_failed:{result.message}",
                )
            return result
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("PolicyLifecycleManager.verify 异常(已隔离): %s", exc)
            return VerificationResult(
                ok=False,
                proposal_id=str(proposal_id or ""),
                message=f"verify_exception:{exc}",
            )

    # --------------------------------------------------------
    # 4. rollback
    # --------------------------------------------------------
    def rollback(
        self,
        proposal_id: str,
        reason: str = "",
        actor: str = "",
    ) -> bool:
        try:
            with self._lock:
                self._stats["rollback_total"] += 1
            pid = str(proposal_id or "")
            with self._lock:
                entry = self._entries.get(pid)
                snapshot_id = entry.snapshot_id if entry else ""
            ok = self._apply_service.rollback(
                proposal_id=pid,
                snapshot_id=snapshot_id,
            )
            if ok:
                self._set_entry_state(
                    proposal_id=pid,
                    new_state=LIFECYCLE_STATE_ROLLED_BACK,
                    audit_event=AUDIT_EVENT_ROLLBACK,
                    actor=actor or "system",
                    metadata={
                        "reason": str(reason or ""),
                        "snapshot_id": str(snapshot_id),
                    },
                )
                ev = self._build_event(
                    "rollback",
                    proposal_id=pid,
                    reason=str(reason or ""),
                )
                if ev is not None:
                    self._publish_event(ev)
            else:
                self._audit_store.record(
                    event=AUDIT_EVENT_FAILED,
                    proposal_id=pid,
                    actor=actor or "system",
                    success=False,
                    error="rollback_failed",
                )
            return ok
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("PolicyLifecycleManager.rollback 异常(已隔离): %s", exc)
            return False

    # --------------------------------------------------------
    # 一站式:approve → apply → verify
    # --------------------------------------------------------
    def approve_and_apply(
        self,
        proposal_id: str,
        reviewer: str = "system",
        reason: str = "",
        auto_verify: bool = True,
    ) -> Tuple[bool, Optional[VerificationResult]]:
        """一站式批准并应用,可选立即 verify。

        适用于 admin 手动 / safe auto 流程。
        """
        try:
            ok = self.approve(
                proposal_id=proposal_id,
                reviewer=reviewer,
                reason=reason,
            )
            if not ok:
                return False, None
            apply_result = self.apply(
                proposal_id=proposal_id,
                actor=reviewer,
            )
            if not apply_result.success:
                return False, None
            if auto_verify:
                vr = self.verify(proposal_id=proposal_id)
                return True, vr
            return True, None
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._stats["errors"] += 1
            logger.debug("approve_and_apply 异常(已隔离): %s", exc)
            return False, None

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "apply_config": self._apply_service.config.to_dict(),
                    "approval_stats": self._approval_store.stats(),
                    "audit_stats": self._audit_store.stats(),
                    "snapshot_stats": self._snapshot_manager.snapshot(),
                    "apply_stats": self._apply_service.get_stats(),
                    "verifier_stats": self._verifier.get_stats(),
                    "manager_stats": self.get_stats(),
                    "entries": list_entries_snapshot(self._entries),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("snapshot 异常(已隔离): %s", exc)
            return {}


def list_entries_snapshot(entries: Dict[str, _LifecycleEntry]) -> List[Dict[str, Any]]:
    """辅助:把 entries dict 转为 list of dict。"""
    try:
        return [e.to_dict() for e in entries.values()]
    except Exception:  # noqa: BLE001
        return []


# ============================================================
# 工厂
# ============================================================
def build_default_lifecycle_manager(
    proposal_store: Optional[ProposalStore] = None,
    throttle_registry: Any = None,
    runtime_budget: Any = None,
    approval_store: Optional[PolicyApprovalStore] = None,
    audit_store: Optional[PolicyAuditStore] = None,
    snapshot_manager: Optional[PolicySnapshotManager] = None,
    apply_service: Optional[PolicyApplyService] = None,
    verifier: Optional[PolicyVerifier] = None,
    apply_config: Optional[PolicyApplyConfig] = None,
    event_publisher: Any = None,
) -> PolicyLifecycleManager:
    """构造一个默认 PolicyLifecycleManager。"""
    return PolicyLifecycleManager(
        proposal_store=proposal_store,
        throttle_registry=throttle_registry,
        runtime_budget=runtime_budget,
        approval_store=approval_store,
        audit_store=audit_store,
        snapshot_manager=snapshot_manager,
        apply_service=apply_service,
        verifier=verifier,
        apply_config=apply_config,
        event_publisher=event_publisher,
    )
