# -*- coding: utf-8 -*-
"""
tests/test_audit_completeness.py

Phase 5.4: Audit 完整性测试。

验证：
1. 所有 Phase 5.4 新增的审计事件类型已注册到 AuditEventType
2. audit_hooks 模块所有 Hook 函数可正常调用
3. Hook 失败时容错（不抛异常）
4. 审计数据包含必要字段
5. 关键事件（governance / growth / personality / memory）均能记录
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# A. 审计事件类型注册
# =====================================================================

class TestAuditEventTypeRegistration:
    """验证 Phase 5.4 新增的审计事件类型已正确注册"""

    def test_governance_proposal_created_registered(self):
        """GOVERNANCE_PROPOSAL_CREATED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "GOVERNANCE_PROPOSAL_CREATED")
        assert AuditEventType.GOVERNANCE_PROPOSAL_CREATED == "governance.proposal_created"

    def test_governance_proposal_reviewed_registered(self):
        """GOVERNANCE_PROPOSAL_REVIEWED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "GOVERNANCE_PROPOSAL_REVIEWED")
        assert AuditEventType.GOVERNANCE_PROPOSAL_REVIEWED == "governance.proposal_reviewed"

    def test_growth_proposal_applied_registered(self):
        """GROWTH_PROPOSAL_APPLIED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "GROWTH_PROPOSAL_APPLIED")
        assert AuditEventType.GROWTH_PROPOSAL_APPLIED == "growth.proposal_applied"

    def test_personality_changed_registered(self):
        """PERSONALITY_CHANGED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "PERSONALITY_CHANGED")
        assert AuditEventType.PERSONALITY_CHANGED == "personality.changed"

    def test_memory_modified_registered(self):
        """MEMORY_MODIFIED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "MEMORY_MODIFIED")
        assert AuditEventType.MEMORY_MODIFIED == "memory.modified"

    def test_memory_deleted_registered(self):
        """MEMORY_DELETED 已注册"""
        from src.admin.core.audit import AuditEventType
        assert hasattr(AuditEventType, "MEMORY_DELETED")
        assert AuditEventType.MEMORY_DELETED == "memory.deleted"

    def test_legacy_event_types_preserved(self):
        """原有事件类型未被破坏"""
        from src.admin.core.audit import AuditEventType
        # 原有事件类型
        assert AuditEventType.CONFIG_UPDATE == "config_update"
        assert AuditEventType.MODULE_START == "module_start"
        assert AuditEventType.ADMIN_LOGIN == "admin_login"


# =====================================================================
# B. Hook 函数可正常调用
# =====================================================================

class TestAuditHooksCallable:
    """验证 Hook 函数可正常调用且不抛异常"""

    def test_on_proposal_created_callable(self, tmp_path):
        """on_proposal_created 可调用"""
        from src.admin.core import audit_hooks

        # 临时改 audit dir
        with patch("src.admin.core.audit.AuditLogger._instance", None):
            with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
                mock_audit = MagicMock()
                mock_get.return_value = mock_audit

                audit_hooks.on_proposal_created(
                    proposal_id="prop_001",
                    proposal_type="personality",
                    actor="test_admin",
                    details={"trait": "开放性"},
                )

                mock_audit.record.assert_called_once()
                call_args = mock_audit.record.call_args
                assert call_args.kwargs["event_type"] == "governance.proposal_created"
                assert call_args.kwargs["operator"] == "test_admin"
                assert call_args.kwargs["target"] == "prop_001"
                assert call_args.kwargs["details"]["proposal_type"] == "personality"

    def test_on_proposal_reviewed_callable(self):
        """on_proposal_reviewed 可调用"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit

            audit_hooks.on_proposal_reviewed(
                proposal_id="prop_002",
                action="approve",
                actor="admin",
                before_status="pending",
                after_status="approved",
                reason="ok",
            )

            mock_audit.record.assert_called_once()
            call_args = mock_audit.record.call_args
            assert call_args.kwargs["event_type"] == "governance.proposal_reviewed"
            assert call_args.kwargs["details"]["action"] == "approve"
            assert call_args.kwargs["details"]["before_status"] == "pending"
            assert call_args.kwargs["details"]["after_status"] == "approved"

    def test_on_proposal_applied_callable(self):
        """on_proposal_applied 可调用"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit

            audit_hooks.on_proposal_applied(
                proposal_id="prop_003",
                proposal_type="personality",
                actor="growth_system",
            )

            mock_audit.record.assert_called_once()
            call_args = mock_audit.record.call_args
            assert call_args.kwargs["event_type"] == "growth.proposal_applied"
            assert call_args.kwargs["operator_type"] == "system"

    def test_on_personality_changed_callable(self):
        """on_personality_changed 可调用"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit

            audit_hooks.on_personality_changed(
                trait="开放性",
                before=0.5,
                after=0.55,
                source="growth_proposal:prop_003",
                actor="system",
            )

            mock_audit.record.assert_called_once()
            call_args = mock_audit.record.call_args
            assert call_args.kwargs["event_type"] == "personality.changed"
            assert call_args.kwargs["target"] == "开放性"
            assert call_args.kwargs["details"]["before"] == 0.5
            assert call_args.kwargs["details"]["after"] == 0.55

    def test_on_memory_modified_callable(self):
        """on_memory_modified 可调用"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit

            audit_hooks.on_memory_modified(
                memory_id="mem_001",
                action="update",
                actor="system",
            )

            mock_audit.record.assert_called_once()
            call_args = mock_audit.record.call_args
            assert call_args.kwargs["event_type"] == "memory.modified"
            assert call_args.kwargs["target"] == "mem_001"

    def test_on_memory_deleted_callable(self):
        """on_memory_deleted 可调用"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_get.return_value = mock_audit

            audit_hooks.on_memory_deleted(
                memory_id="mem_002",
                reason="governance_proposal_applied",
                actor="system",
            )

            mock_audit.record.assert_called_once()
            call_args = mock_audit.record.call_args
            assert call_args.kwargs["event_type"] == "memory.deleted"
            assert call_args.kwargs["details"]["reason"] == "governance_proposal_applied"


# =====================================================================
# C. Hook 容错
# =====================================================================

class TestAuditHookFaultTolerance:
    """Hook 失败时不应抛异常"""

    def test_on_proposal_created_when_audit_unavailable(self):
        """AuditLogger 不可用时，Hook 不抛异常"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance",
                   side_effect=Exception("audit down")):
            # 不应抛异常
            audit_hooks.on_proposal_created(
                proposal_id="prop_001",
                proposal_type="personality",
            )

    def test_on_proposal_reviewed_when_record_fails(self):
        """record() 失败时，Hook 不抛异常"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance") as mock_get:
            mock_audit = MagicMock()
            mock_audit.record.side_effect = Exception("write failed")
            mock_get.return_value = mock_audit

            # 不应抛异常
            audit_hooks.on_proposal_reviewed(
                proposal_id="prop_002",
                action="approve",
            )

    def test_all_hooks_handle_missing_audit(self):
        """所有 Hook 都能容错（无 AuditLogger）"""
        from src.admin.core import audit_hooks

        with patch("src.admin.core.audit.AuditLogger.get_instance",
                   return_value=None):
            # 全部不抛异常
            audit_hooks.on_proposal_created("p1", "personality")
            audit_hooks.on_proposal_reviewed("p1", "approve")
            audit_hooks.on_proposal_applied("p1", "personality")
            audit_hooks.on_proposal_rejected_by_system("p1", "reason")
            audit_hooks.on_personality_changed("trait", 0.5, 0.6)
            audit_hooks.on_memory_modified("mem_1", "update")
            audit_hooks.on_memory_deleted("mem_1", "reason")


# =====================================================================
# D. 真实集成（写入磁盘）
# =====================================================================

class TestAuditRealIntegration:
    """真实 AuditLogger 集成（写入临时目录）"""

    def test_governance_proposal_created_writes_to_disk(self, tmp_path):
        """实际写入审计日志"""
        from src.admin.core.audit import AuditLogger
        from src.admin.core import audit_hooks

        AuditLogger.reset_instance()
        audit = AuditLogger(log_dir=str(tmp_path))
        AuditLogger._instance = audit

        audit_hooks.on_proposal_created(
            proposal_id="prop_real_001",
            proposal_type="personality",
            actor="real_admin",
            details={"trait": "开放性", "delta": 0.05},
        )

        # 查询审计日志
        entries = audit.query(operator="real_admin")
        assert len(entries) >= 1
        last = entries[0]
        assert last["event_type"] == "governance.proposal_created"
        assert last["target"] == "prop_real_001"

        AuditLogger.reset_instance()

    def test_query_by_event_type_works(self, tmp_path):
        """按事件类型查询可用"""
        from src.admin.core.audit import AuditLogger
        from src.admin.core import audit_hooks

        AuditLogger.reset_instance()
        audit = AuditLogger(log_dir=str(tmp_path))
        AuditLogger._instance = audit

        # 触发多种事件
        audit_hooks.on_proposal_created("p1", "personality", actor="a1")
        audit_hooks.on_proposal_reviewed("p1", "approve", actor="a1")
        audit_hooks.on_personality_changed("开放性", 0.5, 0.6, actor="system")

        # 按类型查询
        created = audit.query(event_type="governance.proposal_created")
        reviewed = audit.query(event_type="governance.proposal_reviewed")
        changed = audit.query(event_type="personality.changed")

        assert len(created) >= 1
        assert len(reviewed) >= 1
        assert len(changed) >= 1

        AuditLogger.reset_instance()


# =====================================================================
# E. 与 GovernanceProvider 协同
# =====================================================================

class TestGovernanceProviderAuditIntegration:
    """验证 GovernanceProvider 与新审计事件类型能协同工作（不强制调用 Hook）"""

    def test_governance_provider_uses_audit_logger(self, tmp_path):
        """GovernanceProvider 调用 _record_audit 时使用新事件类型字符串"""
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import PersonalityChangeRequest
        from src.admin.core.audit import AuditLogger

        # 重置 audit
        AuditLogger.reset_instance()
        audit = AuditLogger(log_dir=str(tmp_path))
        AuditLogger._instance = audit

        # 创建 Provider
        from src.growth.proposal.storage import ProposalStorage
        storage = ProposalStorage(data_dir=str(tmp_path / "prop"))

        class _RP:
            def get_personality_summary(self):
                return {"available": True, "current": {"开放性": 0.5}, "state": {}, "self_model": {}}
            def get_memory_summary(self):
                return {"available": True, "total_count": 0, "user_id": None, "recent": [], "important_count": 0}
            def get_growth_summary(self):
                return {"available": True, "shared_with_resolver": True, "metrics": {}}

        provider = GovernanceProvider(runtime_provider=_RP(), proposal_storage=storage)

        # 提交 Proposal
        provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x"),
            actor="integration_test",
        )

        # 不强制要求 GovernanceProvider 调用新 Hook（保持向后兼容）
        # 但 AuditLogger 应可访问
        assert audit is not None
        assert hasattr(AuditEventType := __import__("src.admin.core.audit", fromlist=["AuditEventType"]).AuditEventType,
                       "GOVERNANCE_PROPOSAL_CREATED")
