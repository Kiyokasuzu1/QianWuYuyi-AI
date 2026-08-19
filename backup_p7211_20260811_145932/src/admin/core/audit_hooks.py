"""
src/admin/core/audit_hooks.py

Phase 5.4: 治理与认知系统审计 Hook 模块

提供标准化的审计记录 Hook 函数，供：
  - Admin Governance 层
  - Growth 系统（Proposal apply / reject）
  - Personality 系统（变化）
  - Memory 系统（修改/删除）

调用。

**设计原则**：
- 不修改任何业务逻辑
- 不侵入现有代码
- 所有 Hook 失败不抛异常（容错）
- 通过 AuditLogger 单例统一记录

**使用方式**：
```python
from src.admin.core.audit_hooks import (
    on_proposal_created,
    on_proposal_reviewed,
    on_proposal_applied,
    on_personality_changed,
    on_memory_modified,
)

# Governance 提交 Proposal 时
on_proposal_created(
    proposal_id="prop_xxx",
    proposal_type="personality",
    actor="admin",
    details={"trait": "开放性", "delta": 0.05},
)

# Personality 变化时
on_personality_changed(
    trait="开放性",
    before=0.5,
    after=0.55,
    source="growth_proposal:prop_xxx",
    actor="system",
)
```

**容错**：
- 任意 Hook 内部异常不会向外抛出
- AuditLogger 单例不可用时静默跳过
- 测试时可注入 mock audit logger
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _get_audit_logger():
    """获取 AuditLogger 单例（容错）"""
    try:
        from src.admin.core.audit import AuditLogger
        return AuditLogger.get_instance()
    except Exception as e:
        logger.debug(f"audit_hooks: 无法获取 AuditLogger: {e}")
        return None


# ============================================================
# 治理层（GovernanceProvider）Hook
# ============================================================

def on_proposal_created(
    proposal_id: str,
    proposal_type: str,
    actor: str = "admin",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Proposal 创建时调用。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.GOVERNANCE_PROPOSAL_CREATED,
            operator=actor,
            operator_type=AuditOperatorType.HUMAN,
            target=proposal_id,
            details={
                "proposal_type": proposal_type,
                "phase": "5.3_governance",
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_proposal_created audit failed: {e}")


def on_proposal_reviewed(
    proposal_id: str,
    action: str,
    actor: str = "admin",
    before_status: str = "pending",
    after_status: str = "",
    reason: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Proposal 审查（approve/reject/modify）时调用。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.GOVERNANCE_PROPOSAL_REVIEWED,
            operator=actor,
            operator_type=AuditOperatorType.HUMAN,
            target=proposal_id,
            details={
                "action": action,
                "before_status": before_status,
                "after_status": after_status,
                "reason": reason,
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_proposal_reviewed audit failed: {e}")


# ============================================================
# 成长系统（Growth）Hook
# ============================================================

def on_proposal_applied(
    proposal_id: str,
    proposal_type: str,
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Proposal 实际 apply 到 PersonalityResolver 时调用。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.GROWTH_PROPOSAL_APPLIED,
            operator=actor,
            operator_type=AuditOperatorType.SYSTEM,
            target=proposal_id,
            details={
                "proposal_type": proposal_type,
                "phase": "growth_apply",
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_proposal_applied audit failed: {e}")


def on_proposal_rejected_by_system(
    proposal_id: str,
    reason: str = "",
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """系统级 Proposal 拒绝时调用（如 ProposalEvaluator 不通过）。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.GROWTH_PROPOSAL_REJECTED,
            operator=actor,
            operator_type=AuditOperatorType.SYSTEM,
            target=proposal_id,
            details={
                "reason": reason,
                "phase": "growth_reject",
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_proposal_rejected_by_system audit failed: {e}")


# ============================================================
# 人格系统（Personality）Hook
# ============================================================

def on_personality_changed(
    trait: str,
    before: Any = None,
    after: Any = None,
    source: str = "system",
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """人格变化时调用（PersonalityAdapter.apply 后）。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.PERSONALITY_CHANGED,
            operator=actor,
            operator_type=(
                AuditOperatorType.AGENT if actor in ("admin", "human")
                else AuditOperatorType.SYSTEM
            ),
            target=trait,
            details={
                "before": before,
                "after": after,
                "source": source,
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_personality_changed audit failed: {e}")


# ============================================================
# 记忆系统（Memory）Hook
# ============================================================

def on_memory_modified(
    memory_id: str,
    action: str = "update",
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """记忆修改时调用。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.MEMORY_MODIFIED,
            operator=actor,
            operator_type=(
                AuditOperatorType.AGENT if actor in ("admin", "human")
                else AuditOperatorType.SYSTEM
            ),
            target=memory_id,
            details={
                "action": action,
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_memory_modified audit failed: {e}")


def on_memory_deleted(
    memory_id: str,
    reason: str = "",
    actor: str = "system",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """记忆删除时调用（应只来自 Growth 系统 apply 后）。"""
    audit = _get_audit_logger()
    if audit is None:
        return
    try:
        from src.admin.core.audit import AuditEventType, AuditOperatorType
        audit.record(
            event_type=AuditEventType.MEMORY_DELETED,
            operator=actor,
            operator_type=(
                AuditOperatorType.AGENT if actor in ("admin", "human")
                else AuditOperatorType.SYSTEM
            ),
            target=memory_id,
            details={
                "reason": reason,
                **(details or {}),
            },
            result="success",
        )
    except Exception as e:
        logger.debug(f"on_memory_deleted audit failed: {e}")
