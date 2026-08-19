# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/__init__.py

Phase C.10.10 — Policy Proposal Lifecycle & Controlled Apply

本包实现 Policy 提案的完整生命周期管理:

- PolicyApprovalRecord / PolicyApprovalStore  人工/自动审核记录(append-only)
- PolicyApplyConfig                           Apply 配置(默认关闭 auto_apply)
- PolicyApplyService                          Apply 控制器(调用 ThrottleRegistry / RuntimeBudget)
- PolicySnapshotManager                       Apply 前 Snapshot / 回滚
- PolicyVerifier / VerificationResult         Apply 后验证
- PolicyLifecycleManager                      统一入口(approve → snapshot → apply → verify → mark)
- PolicyAuditRecord / PolicyAuditStore        审计记录(append-only)
- PolicyLifecycleState / Constants            状态机常量

使用方式:
    from src.runtime.policy.lifecycle import (
        PolicyLifecycleManager,
        PolicyApplyConfig,
        build_default_lifecycle_manager,
    )

    manager = build_default_lifecycle_manager(
        proposal_store=feedback_engine.store,
        throttle_registry=layer.throttle_registry,
        runtime_budget=layer.runtime_budget,
        publisher=publish_event,
    )
    manager.approve(proposal_id, reviewer="admin")
    manager.apply(proposal_id)
    manager.verify(proposal_id)
    manager.rollback(proposal_id, reason="verification_failed")

设计原则:
- 严格走 Proposal → Approval → Apply → Verify → Rollback 闭环
- 默认 auto_apply_enabled=False,需显式开启
- 任何异常 fail-soft,不阻塞 Runtime
- 不修改任何业务模块,只调整 ThrottleRegistry / RuntimeBudget 参数
"""

from .apply_service import (
    DEFAULT_MAX_THROTTLE_DELTA,
    APPLY_OUTCOME_FAILED,
    APPLY_OUTCOME_REJECTED,
    APPLY_OUTCOME_SUCCESS,
    PolicyApplyConfig,
    PolicyApplyResult,
    PolicyApplyService,
    build_default_apply_config,
    build_default_apply_service,
)
from .approval import (
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_CANCELLED,
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_REJECTED,
    PolicyApprovalRecord,
    PolicyApprovalStore,
    build_default_approval_store,
    build_approval_record,
)
from .audit import (
    AUDIT_EVENT_APPLIED,
    AUDIT_EVENT_APPROVED,
    AUDIT_EVENT_CREATED,
    AUDIT_EVENT_FAILED,
    AUDIT_EVENT_REJECTED,
    AUDIT_EVENT_ROLLBACK,
    AUDIT_EVENT_SNAPSHOT,
    AUDIT_EVENT_VERIFIED,
    PolicyAuditRecord,
    PolicyAuditStore,
    build_audit_record,
    build_default_audit_store,
)
from .manager import (
    PolicyLifecycleManager,
    build_default_lifecycle_manager,
)
from .snapshot import (
    PolicySnapshotManager,
    SnapshotDiff,
    ThrottleSnapshot,
    build_default_snapshot_manager,
    build_throttle_snapshot,
)
from .verifier import (
    DEFAULT_VERIFY_DELAY,
    VerificationResult,
    PolicyVerifier,
    build_default_verifier,
)

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


__all__ = [
    # 状态机
    "LIFECYCLE_STATE_PENDING",
    "LIFECYCLE_STATE_APPROVED",
    "LIFECYCLE_STATE_APPLYING",
    "LIFECYCLE_STATE_APPLIED",
    "LIFECYCLE_STATE_VERIFIED",
    "LIFECYCLE_STATE_FAILED",
    "LIFECYCLE_STATE_REJECTED",
    "LIFECYCLE_STATE_ROLLED_BACK",
    "LIFECYCLE_TERMINAL_STATES",
    # Approval
    "APPROVAL_STATUS_PENDING",
    "APPROVAL_STATUS_APPROVED",
    "APPROVAL_STATUS_REJECTED",
    "APPROVAL_STATUS_CANCELLED",
    "PolicyApprovalRecord",
    "PolicyApprovalStore",
    "build_approval_record",
    "build_default_approval_store",
    # Audit
    "AUDIT_EVENT_CREATED",
    "AUDIT_EVENT_APPROVED",
    "AUDIT_EVENT_REJECTED",
    "AUDIT_EVENT_SNAPSHOT",
    "AUDIT_EVENT_APPLIED",
    "AUDIT_EVENT_VERIFIED",
    "AUDIT_EVENT_FAILED",
    "AUDIT_EVENT_ROLLBACK",
    "PolicyAuditRecord",
    "PolicyAuditStore",
    "build_audit_record",
    "build_default_audit_store",
    # Snapshot
    "ThrottleSnapshot",
    "SnapshotDiff",
    "PolicySnapshotManager",
    "build_throttle_snapshot",
    "build_default_snapshot_manager",
    # Apply
    "APPLY_OUTCOME_SUCCESS",
    "APPLY_OUTCOME_FAILED",
    "APPLY_OUTCOME_REJECTED",
    "PolicyApplyResult",
    "PolicyApplyConfig",
    "PolicyApplyService",
    "DEFAULT_MAX_THROTTLE_DELTA",
    "build_default_apply_config",
    "build_default_apply_service",
    # Verifier
    "VerificationResult",
    "PolicyVerifier",
    "DEFAULT_VERIFY_DELAY",
    "build_default_verifier",
    # Manager
    "PolicyLifecycleManager",
    "build_default_lifecycle_manager",
]
