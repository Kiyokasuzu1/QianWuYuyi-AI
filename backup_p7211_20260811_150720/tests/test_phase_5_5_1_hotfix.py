# -*- coding: utf-8 -*-
"""
tests/test_phase_5_5_1_hotfix.py

Phase 5.5.1 Hotfix: Mirror 层 Authority 边界修复测试套件

覆盖：
  [1] Proposal Traceability: source_proposal_id 字段关联
  [2] Mirror Authority 修正: MirrorReadOnlyAdapter 只读；MirrorBackedAdapter deprecated
  [3] Memory Action 隔离: action_scope 路由；memory 类型不进 PersonalityAdapter
  [4] Mirror Failure Handling: 失败写入 retry queue + Type A metadata
  [5] Personality Apply Protection: path 白名单 + 非法路径拒绝
  [6] State Machine 收敛: 非法状态转换被禁止

强约束：
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState 实例
- 不修改 RuntimeCore / Orchestrator
- 测试隔离：每个测试使用临时目录
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

# ============================================================
# 路径设置
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# [6] State Machine 收敛测试
# ============================================================

class TestStateMachines(unittest.TestCase):
    """[6] 状态机转换规则测试"""

    def test_01_type_a_states_complete(self):
        """Type A 应包含 5 个状态"""
        from src.growth.state_machines import TYPE_A_STATES
        self.assertEqual(
            TYPE_A_STATES,
            frozenset({"pending", "approved", "rejected", "applied", "cancelled"}),
        )

    def test_02_type_b_states_complete(self):
        """Type B 应包含 3 个状态"""
        from src.growth.state_machines import TYPE_B_STATES
        self.assertEqual(
            TYPE_B_STATES,
            frozenset({"proposed", "approved", "rejected"}),
        )

    def test_03_type_a_pending_to_approved_valid(self):
        """pending → approved 是合法的"""
        from src.growth.state_machines import validate_type_a_transition
        self.assertTrue(validate_type_a_transition("pending", "approved"))

    def test_04_type_a_pending_to_rejected_valid(self):
        """pending → rejected 是合法的"""
        from src.growth.state_machines import validate_type_a_transition
        self.assertTrue(validate_type_a_transition("pending", "rejected"))

    def test_05_type_a_approved_to_pending_invalid(self):
        """approved → pending 必须禁止（用户要求）"""
        from src.growth.state_machines import (
            validate_type_a_transition, IllegalStateTransition,
        )
        with self.assertRaises(IllegalStateTransition):
            validate_type_a_transition("approved", "pending")

    def test_06_type_a_rejected_to_approved_invalid(self):
        """rejected → approved 必须禁止（用户要求）"""
        from src.growth.state_machines import (
            validate_type_a_transition, IllegalStateTransition,
        )
        with self.assertRaises(IllegalStateTransition):
            validate_type_a_transition("rejected", "approved")

    def test_07_type_a_rejected_is_terminal(self):
        """rejected 是终态，不能转出"""
        from src.growth.state_machines import (
            validate_type_a_transition, IllegalStateTransition,
        )
        for to_state in ("pending", "approved", "applied", "cancelled"):
            with self.assertRaises(IllegalStateTransition):
                validate_type_a_transition("rejected", to_state)

    def test_08_type_a_applied_is_terminal(self):
        """applied 是终态，不能转出"""
        from src.growth.state_machines import (
            validate_type_a_transition, IllegalStateTransition,
        )
        for to_state in ("pending", "approved", "rejected", "cancelled"):
            with self.assertRaises(IllegalStateTransition):
                validate_type_a_transition("applied", to_state)

    def test_09_type_a_unknown_state_raises(self):
        """未知状态抛异常"""
        from src.growth.state_machines import (
            validate_type_a_transition, IllegalStateTransition,
        )
        with self.assertRaises(IllegalStateTransition):
            validate_type_a_transition("bogus", "approved")

    def test_10_type_b_proposed_to_approved_valid(self):
        """Type B proposed → approved 是合法的"""
        from src.growth.state_machines import validate_type_b_transition
        self.assertTrue(validate_type_b_transition("proposed", "approved"))

    def test_11_type_b_approved_to_proposed_invalid(self):
        """Type B approved → proposed 必须禁止"""
        from src.growth.state_machines import (
            validate_type_b_transition, IllegalStateTransition,
        )
        with self.assertRaises(IllegalStateTransition):
            validate_type_b_transition("approved", "proposed")

    def test_12_type_b_rejected_is_terminal(self):
        """Type B rejected 是终态"""
        from src.growth.state_machines import (
            validate_type_b_transition, IllegalStateTransition,
        )
        with self.assertRaises(IllegalStateTransition):
            validate_type_b_transition("rejected", "approved")

    def test_13_is_terminal_state(self):
        """is_terminal_state 应正确识别终态"""
        from src.growth.state_machines import is_terminal_state
        self.assertTrue(is_terminal_state("rejected", "type_a"))
        self.assertTrue(is_terminal_state("applied", "type_a"))
        self.assertTrue(is_terminal_state("cancelled", "type_a"))
        self.assertFalse(is_terminal_state("pending", "type_a"))
        self.assertTrue(is_terminal_state("approved", "type_b"))
        self.assertTrue(is_terminal_state("rejected", "type_b"))
        self.assertFalse(is_terminal_state("proposed", "type_b"))

    def test_14_same_state_is_idempotent(self):
        """同状态转换应视为合法（幂等）"""
        from src.growth.state_machines import validate_type_a_transition
        self.assertTrue(validate_type_a_transition("pending", "pending"))
        self.assertTrue(validate_type_a_transition("approved", "approved"))

    def test_15_get_state_machine_info(self):
        """get_state_machine_info 返回完整信息"""
        from src.growth.state_machines import get_state_machine_info
        info = get_state_machine_info()
        self.assertIn("type_a", info)
        self.assertIn("type_b", info)
        self.assertIn("action_scopes", info)
        self.assertIn("pending", info["type_a"]["states"])
        self.assertIn("proposed", info["type_b"]["states"])
        self.assertIn("personality", info["action_scopes"])
        self.assertIn("memory", info["action_scopes"])

    def test_16_action_scopes_complete(self):
        """action_scopes 应包含所有 scope"""
        from src.growth.state_machines import ACTION_SCOPES
        self.assertIn("personality", ACTION_SCOPES)
        self.assertIn("memory", ACTION_SCOPES)
        self.assertIn("relationship", ACTION_SCOPES)
        self.assertIn("self_model", ACTION_SCOPES)


# ============================================================
# [4] RetryQueue 测试
# ============================================================

class TestRetryQueue(unittest.TestCase):
    """[4] Mirror Failure Handling - RetryQueue 测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_retry_queue_")
        self.storage_path = os.path.join(self.tmpdir, "retry.json")
        from src.growth.sync.retry_queue import RetryQueue
        self.queue = RetryQueue(storage_path=self.storage_path)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_add_failure_creates_record(self):
        """add_failure 应创建 RetryRecord"""
        rec = self.queue.add_failure(
            proposal_id="prop_001",
            operation="mirror",
            error_message="test error",
            payload={"x": 1},
        )
        self.assertTrue(rec.record_id.startswith("retry_"))
        self.assertEqual(rec.proposal_id, "prop_001")
        self.assertEqual(rec.operation, "mirror")
        self.assertEqual(rec.error_message, "test error")
        self.assertEqual(rec.payload, {"x": 1})
        self.assertEqual(rec.status, "pending")
        self.assertEqual(rec.retry_count, 0)

    def test_02_mark_success(self):
        """mark_success 应将记录标记为 success"""
        rec = self.queue.add_failure(
            proposal_id="prop_002",
            operation="mirror",
            error_message="err",
        )
        ok = self.queue.mark_success(rec.record_id)
        self.assertTrue(ok)
        all_recs = self.queue.get_all()
        self.assertEqual(len(all_recs), 1)
        self.assertEqual(all_recs[0].status, "success")

    def test_03_mark_retry_increments_count(self):
        """mark_retry 应增加 retry_count"""
        rec = self.queue.add_failure(
            proposal_id="prop_003",
            operation="sync_status",
            error_message="err",
        )
        r = self.queue.mark_retry(rec.record_id, error_message="retry err 1")
        self.assertEqual(r.retry_count, 1)
        self.assertEqual(r.error_message, "retry err 1")
        r2 = self.queue.mark_retry(rec.record_id, error_message="retry err 2")
        self.assertEqual(r2.retry_count, 2)
        r3 = self.queue.mark_retry(rec.record_id, error_message="retry err 3")
        # 达到 max_retry=3 后变 abandoned
        self.assertEqual(r3.status, "abandoned")

    def test_04_get_pending_filters_status(self):
        """get_pending 应过滤 status=pending"""
        rec1 = self.queue.add_failure("p1", "mirror", "e1")
        rec2 = self.queue.add_failure("p2", "mirror", "e2")
        self.queue.mark_success(rec1.record_id)
        pending = self.queue.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].proposal_id, "p2")

    def test_05_abandon_marks_status(self):
        """abandon 应将记录标记为 abandoned"""
        rec = self.queue.add_failure("p4", "mirror", "e4")
        ok = self.queue.abandon(rec.record_id)
        self.assertTrue(ok)
        all_recs = self.queue.get_all()
        self.assertEqual(all_recs[0].status, "abandoned")

    def test_06_count_methods(self):
        """count / count_by_stats 应正确"""
        self.queue.add_failure("p5", "mirror", "e5")
        self.queue.add_failure("p6", "mirror", "e6")
        rec = self.queue.add_failure("p7", "mirror", "e7")
        self.queue.mark_success(rec.record_id)
        self.assertEqual(self.queue.count(), 3)
        self.assertEqual(self.queue.count_by_status("pending"), 2)
        self.assertEqual(self.queue.count_by_status("success"), 1)

    def test_07_persistence(self):
        """记录应持久化到文件"""
        rec = self.queue.add_failure("p8", "mirror", "e8")
        # 重新构造实例
        from src.growth.sync.retry_queue import RetryQueue
        queue2 = RetryQueue(storage_path=self.storage_path)
        loaded = queue2.get_all()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].record_id, rec.record_id)


# ============================================================
# [5] Personality Apply Protection 测试
# ============================================================

class TestPersonalityApplyProtection(unittest.TestCase):
    """[5] Personality Apply Protection - path 白名单验证"""

    def test_01_allowed_paths_accepted(self):
        """白名单内 path 应被接受"""
        from src.personality.personality_adapter import validate_proposal_path
        valid_paths = [
            "self_state.initiative",
            "self_state.social_need",
            "openness",
            "conscientiousness",
            "personality.traits.shyness",
            "开放性",
            "神经质",
        ]
        for p in valid_paths:
            self.assertTrue(
                validate_proposal_path(p),
                f"path {p} 应被接受",
            )

    def test_02_disallowed_paths_rejected(self):
        """白名单外 path 应被拒绝"""
        from src.personality.personality_adapter import (
            validate_proposal_path, PersonalityPathValidationError,
        )
        bad_paths = [
            "self_state.unknown",
            "personality.random_field",
            "system.file",
            "../etc/passwd",
            "arbitrary_path",
            "",
        ]
        for p in bad_paths:
            with self.assertRaises(PersonalityPathValidationError):
                validate_proposal_path(p)

    def test_03_memory_scope_uses_memory_paths(self):
        """memory scope 下 path 必须在 MEMORY_ACTION_PATHS 中"""
        from src.personality.personality_adapter import (
            validate_proposal_path, PersonalityPathValidationError,
        )
        # 合法 memory path
        for p in ("memory.mark_incorrect", "memory.delete", "memory.merge"):
            self.assertTrue(validate_proposal_path(p, action_scope="memory"))
        # 白名单 path 在 memory scope 下应被拒
        with self.assertRaises(PersonalityPathValidationError):
            validate_proposal_path("self_state.initiative", action_scope="memory")
        # 非法 path 在 memory scope 下也应被拒
        with self.assertRaises(PersonalityPathValidationError):
            validate_proposal_path("memory.bogus", action_scope="memory")

    def test_04_validate_proposal_paths_batch(self):
        """validate_proposal_paths 应批量验证"""
        from src.personality.personality_adapter import validate_proposal_paths

        class FakeChangeItem:
            def __init__(self, path):
                self.path = path

        # 全部合法
        cis = [FakeChangeItem("self_state.initiative"), FakeChangeItem("openness")]
        valid, errors = validate_proposal_paths(cis)
        self.assertTrue(valid)
        self.assertEqual(errors, [])

        # 部分非法
        cis = [
            FakeChangeItem("self_state.initiative"),
            FakeChangeItem("bad_path"),
        ]
        valid, errors = validate_proposal_paths(cis)
        self.assertFalse(valid)
        self.assertEqual(len(errors), 1)
        self.assertIn("bad_path", errors[0])

    def test_05_apply_proposal_skips_memory_scope(self):
        """apply_proposal 应短路 memory scope（[3] 隔离）"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        prop = GrowthProposal(
            evaluator_meta={"action_scope": "memory"},
            proposed_changes=[
                ChangeItem(path="memory.mark_incorrect", before=1, after=0),
            ],
        )
        adapter = PersonalityAdapter()
        result = adapter.apply_proposal(prop)
        self.assertFalse(result["applied"])
        self.assertIn("memory_action_skipped", result["note"])

    def test_06_apply_proposal_rejects_illegal_path(self):
        """apply_proposal 应拒绝非白名单 path"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        prop = GrowthProposal(
            evaluator_meta={"action_scope": "personality"},
            proposed_changes=[
                ChangeItem(path="system.file", before=1, after=0),
            ],
        )
        adapter = PersonalityAdapter()
        result = adapter.apply_proposal(prop)
        self.assertFalse(result["applied"])
        self.assertIn("path_validation_failed", result["note"])

    def test_07_apply_proposal_accepts_valid_path(self):
        """apply_proposal 应接受白名单 path"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        prop = GrowthProposal(
            evaluator_meta={"action_scope": "personality"},
            proposed_changes=[
                ChangeItem(path="self_state.initiative", before=0.5, after=0.6),
            ],
        )
        adapter = PersonalityAdapter()
        result = adapter.apply_proposal(prop)
        # 不管是 True 还是 False（取决于 TraitStateUpdater 是否可用），
        # note 都不应是 path_validation_failed
        if not result["applied"]:
            self.assertNotIn("path_validation_failed", result["note"])
            self.assertNotIn("memory_action_skipped", result["note"])


# ============================================================
# [1] Proposal Traceability 测试
# ============================================================

class TestProposalTraceability(unittest.TestCase):
    """[1] Proposal Traceability - source_proposal_id 字段"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_traceability_")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_type_a(self, proposal_id="prop_a_001", **kwargs):
        from src.growth.proposal.proposal import GrowthProposal
        p = GrowthProposal(proposal_id=proposal_id, **kwargs)
        return p

    def test_01_type_b_includes_source_proposal_id(self):
        """Type B 的 evaluator_meta 应包含 source_proposal_id"""
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror,
            enabled=True,
            retry_queue=retry,
        )

        type_a = self._make_type_a(proposal_id="prop_a_001")
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertTrue(result["success"])
        # 检查 evaluator_meta
        mirror_data = mirror.get_proposal(result["mirror_proposal_id"])
        self.assertIsNotNone(mirror_data)
        self.assertIn("evaluator_meta", mirror_data)
        meta = mirror_data["evaluator_meta"]
        self.assertEqual(meta.get("source_proposal_id"), "prop_a_001")
        self.assertEqual(meta.get("mirror_source_proposal_id"), "prop_a_001")
        self.assertTrue(meta.get("mirrored_from_type_a"))

    def test_02_build_type_a_mirror_metadata(self):
        """build_type_a_mirror_metadata 应生成完整追踪字段"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        integration = GovernanceMirrorIntegration(
            mirror_storage=MagicMock(),
            enabled=True,
        )
        result = {
            "success": True,
            "mirror_proposal_id": "prop_b_xxx",
            "mirror_status": "proposed",
            "action_scope": "personality",
        }
        meta = integration.build_type_a_mirror_metadata(result)
        self.assertTrue(meta["mirror_attempted"])
        self.assertTrue(meta["mirror_success"])
        self.assertEqual(meta["mirror_sync_status"], "success")
        self.assertEqual(meta["mirror_proposal_id"], "prop_b_xxx")
        self.assertEqual(meta["mirror_schema"], "type_b")
        self.assertEqual(meta["action_scope"], "personality")

    def test_03_metadata_records_failure(self):
        """失败时 metadata 应记录 mirror_sync_status=failed"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        integration = GovernanceMirrorIntegration(
            mirror_storage=MagicMock(),
            enabled=True,
        )
        result = {
            "success": False,
            "error": "mirror_save_failed",
            "action_scope": "personality",
        }
        meta = integration.build_type_a_mirror_metadata(result)
        self.assertEqual(meta["mirror_sync_status"], "failed")
        self.assertEqual(meta["mirror_error"], "mirror_save_failed")
        self.assertFalse(meta["mirror_success"])

    def test_04_metadata_records_skipped(self):
        """memory scope skipped 时 metadata 应记录跳过原因"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        integration = GovernanceMirrorIntegration(
            mirror_storage=MagicMock(),
            enabled=True,
        )
        result = {
            "skipped": True,
            "success": False,
            "error": "memory_action_skipped",
            "action_scope": "memory",
        }
        meta = integration.build_type_a_mirror_metadata(result)
        self.assertTrue(meta["mirror_attempted"])
        self.assertTrue(meta["mirror_skipped"])
        self.assertEqual(meta["action_scope"], "memory")
        self.assertEqual(meta["mirror_skip_reason"], "memory_action_skipped")


# ============================================================
# [3] Memory Action 隔离测试
# ============================================================

class TestMemoryActionIsolation(unittest.TestCase):
    """[3] Memory Action 隔离 - action_scope 路由"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_memory_iso_")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_type_a(self, proposal_id="prop_a", proposal_type="personality",
                     metadata=None):
        from src.growth.proposal.proposal import GrowthProposal
        return GrowthProposal(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            metadata=metadata or {},
        )

    def test_01_explicit_memory_scope_skips_mirror(self):
        """metadata.action_scope=memory 应跳过镜像"""
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror,
            enabled=True,
            retry_queue=retry,
        )

        type_a = self._make_type_a(
            proposal_id="prop_a_mem",
            metadata={"action_scope": "memory"},
        )
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("action_scope"), "memory")
        self.assertEqual(result.get("error"), "memory_action_skipped")
        # 镜像存储应保持空
        self.assertEqual(mirror.count(), 0)

    def test_02_identity_with_memory_request_kind_detected(self):
        """identity 类型 + request_kind=memory_xxx 应被识别为 memory scope"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        integration = GovernanceMirrorIntegration(
            mirror_storage=MagicMock(),
            enabled=True,
        )
        type_a = self._make_type_a(
            proposal_id="prop_a_id_mem",
            proposal_type="identity",
            metadata={"request_kind": "memory_mark_incorrect"},
        )
        scope = integration._get_action_scope(type_a)
        self.assertEqual(scope, "memory")

    def test_03_default_personality_scope(self):
        """默认 scope 应为 personality"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        integration = GovernanceMirrorIntegration(
            mirror_storage=MagicMock(),
            enabled=True,
        )
        type_a = self._make_type_a(
            proposal_id="prop_a_default",
            proposal_type="personality",
        )
        scope = integration._get_action_scope(type_a)
        self.assertEqual(scope, "personality")

    def test_04_explicit_personality_scope_works(self):
        """显式 action_scope=personality 应执行镜像"""
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror,
            enabled=True,
            retry_queue=retry,
        )

        type_a = self._make_type_a(
            proposal_id="prop_a_p",
            metadata={"action_scope": "personality"},
        )
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertTrue(result["success"])
        self.assertEqual(result["action_scope"], "personality")
        self.assertEqual(mirror.count(), 1)


# ============================================================
# [2] Mirror Authority 修正测试
# ============================================================

class TestMirrorAuthority(unittest.TestCase):
    """[2] Mirror Authority 修正 - MirrorReadOnlyAdapter / deprecated"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_mirror_auth_")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_readonly_adapter_exposes_only_read_methods(self):
        """MirrorReadOnlyAdapter 不应有 accept/reject/update_proposal"""
        from src.runtime.adapters.growth_proposal_mirror import MirrorReadOnlyAdapter
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        adapter = MirrorReadOnlyAdapter(mirror)
        # 应该有读方法
        self.assertTrue(hasattr(adapter, "get_proposal"))
        self.assertTrue(hasattr(adapter, "list_proposals"))
        # 不应该有写方法
        self.assertFalse(hasattr(adapter, "accept_proposal"))
        self.assertFalse(hasattr(adapter, "reject_proposal"))
        self.assertFalse(hasattr(adapter, "update_proposal"))

    def test_02_readonly_get_proposal_returns_none_when_empty(self):
        """MirrorReadOnlyAdapter.get_proposal 在不存在时返回 None"""
        from src.runtime.adapters.growth_proposal_mirror import (
            MirrorReadOnlyAdapter, GrowthProposalMirrorStorage,
        )
        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        adapter = MirrorReadOnlyAdapter(mirror)
        self.assertIsNone(adapter.get_proposal("nonexistent"))

    def test_03_backed_adapter_marked_deprecated(self):
        """MirrorBackedAdapter 应被标记为 deprecated"""
        from src.runtime.adapters.growth_proposal_mirror import MirrorBackedAdapter
        # docstring 应包含 deprecated 标记
        self.assertIn("deprecated", MirrorBackedAdapter.__doc__.lower())

    def test_04_sync_status_uses_status_via_governance(self):
        """sync_status 应只通过 GovernanceProvider 同步状态（由 integration 保证）"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.proposal.proposal import GrowthProposal

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=os.path.join(self.tmpdir, "retry.json"))
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror,
            enabled=True,
            retry_queue=retry,
        )
        # 1. 先镜像
        type_a = GrowthProposal(proposal_id="prop_a_sync")
        r1 = integration.mirror_type_a_to_type_b(type_a)
        self.assertTrue(r1["success"])
        mirror_id = r1["mirror_proposal_id"]

        # 2. 通过 sync_status 同步（不是通过 MirrorBackedAdapter）
        r2 = integration.sync_status(
            type_a, "approved",
            reviewer_id="admin_001", review_comment="ok"
        )
        self.assertTrue(r2["success"])
        # 验证镜像状态已更新
        d = mirror.get_proposal(mirror_id)
        self.assertEqual(d["status"], "approved")
        meta = d.get("evaluator_meta", {})
        self.assertEqual(meta.get("source_status"), "approved")
        self.assertEqual(meta.get("reviewer_id"), "admin_001")

    def test_05_mirror_storage_only_saves(self):
        """MirrorStorage 只负责保存，不应被外部视为 Authority"""
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        # 验证 MirrorStorage 没有 Authority 相关属性
        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        self.assertFalse(hasattr(mirror, "approval_manager"))
        self.assertFalse(hasattr(mirror, "personality_resolver"))
        self.assertFalse(hasattr(mirror, "runtime_core"))


# ============================================================
# [4] Mirror Failure Handling 集成测试
# ============================================================

class TestMirrorFailureHandling(unittest.TestCase):
    """[4] Mirror Failure Handling - 失败记录 + retry"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_mirror_fail_")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_mirror_save_failure_creates_retry_record(self):
        """save_type_b 失败时应创建 retry record"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.proposal.proposal import GrowthProposal

        # 构造失败的 mirror（用 MagicMock 模拟 save_type_b 返回 False）
        broken_mirror = MagicMock()
        broken_mirror.save_type_b.return_value = False
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=broken_mirror,
            enabled=True,
            retry_queue=retry,
        )

        type_a = GrowthProposal(proposal_id="prop_a_fail")
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertFalse(result["success"])
        self.assertIn("mirror_save_failed", result["error"])
        # 验证 retry queue 增加了记录
        self.assertEqual(retry.count(), 1)
        rec = retry.get_all()[0]
        self.assertEqual(rec.proposal_id, "prop_a_fail")
        self.assertEqual(rec.operation, "mirror")

    def test_02_sync_status_failure_creates_retry_record(self):
        """sync_status 失败时应创建 retry record"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.proposal.proposal import GrowthProposal

        broken_mirror = MagicMock()
        broken_mirror.save_type_b.return_value = True
        broken_mirror.update_status.return_value = False
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=broken_mirror,
            enabled=True,
            retry_queue=retry,
        )

        type_a = GrowthProposal(proposal_id="prop_a_sync_fail")
        # 先镜像
        r1 = integration.mirror_type_a_to_type_b(type_a)
        self.assertTrue(r1["success"])
        # 再 sync_status 失败
        r2 = integration.sync_status(type_a, "approved", reviewer_id="admin_001")
        self.assertFalse(r2["success"])
        self.assertEqual(r2["error"], "mirror_update_failed")
        # retry queue 应有 1 条 sync_status 失败记录
        pending = retry.get_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].operation, "sync_status")

    def test_03_metadata_contains_mirror_sync_status(self):
        """失败时 metadata 应包含 mirror_sync_status=failed"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        broken_mirror = MagicMock()
        broken_mirror.save_type_b.return_value = False
        integration = GovernanceMirrorIntegration(
            mirror_storage=broken_mirror,
            enabled=True,
        )
        type_a = MagicMock()
        type_a.proposal_id = "p1"
        type_a.metadata = {}
        result = integration.mirror_type_a_to_type_b(type_a)
        meta = integration.build_type_a_mirror_metadata(result)
        self.assertEqual(meta["mirror_sync_status"], "failed")
        self.assertIn("mirror_error", meta)
        self.assertNotEqual(meta["mirror_error"], "")

    def test_04_no_silent_fail_even_without_retry_queue(self):
        """即使没有 retry queue，也不应 silent fail（应记录日志 + 返回结果）"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        broken_mirror = MagicMock()
        broken_mirror.save_type_b.return_value = False
        integration = GovernanceMirrorIntegration(
            mirror_storage=broken_mirror,
            enabled=True,
            retry_queue=None,  # 不传 retry queue
        )
        type_a = MagicMock()
        type_a.proposal_id = "p_no_retry"
        type_a.metadata = {}
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertFalse(result["success"])
        # 返回值本身已经表达了失败（非 silent）
        self.assertIn("error", result)
        self.assertNotEqual(result["error"], "")

    def test_05_exception_in_mirror_creates_retry_record(self):
        """镜像过程中异常也应创建 retry record"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.growth.sync.retry_queue import RetryQueue

        class ExplodingMirror:
            def save_type_b(self, *args, **kwargs):
                raise RuntimeError("boom")

        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=ExplodingMirror(),
            enabled=True,
            retry_queue=retry,
        )
        type_a = MagicMock()
        type_a.proposal_id = "p_explode"
        type_a.metadata = {}
        result = integration.mirror_type_a_to_type_b(type_a)
        self.assertFalse(result["success"])
        self.assertIn("exception", result["error"])
        # retry queue 应有记录
        self.assertGreaterEqual(retry.count(), 1)


# ============================================================
# 集成：端到端测试（不创建 Authority）
# ============================================================

class TestPhase551EndToEnd(unittest.TestCase):
    """Phase 5.5.1 Hotfix 端到端集成测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_e2e_551_")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_full_flow_personality_proposal(self):
        """完整流程：personality proposal → mirror → sync_status → audit-ready"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.runtime.adapters.growth_proposal_mirror import (
            GrowthProposalMirrorStorage, MirrorReadOnlyAdapter,
        )
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.proposal.proposal import GrowthProposal

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror, enabled=True, retry_queue=retry,
        )

        # 1. Admin 提交 Type A
        type_a = GrowthProposal(
            proposal_id="prop_a_e2e_001",
            proposal_type="personality",
            metadata={"action_scope": "personality"},
        )
        # 2. 镜像
        r1 = integration.mirror_type_a_to_type_b(type_a)
        self.assertTrue(r1["success"])
        # 3. ApprovalManager 通过 sync_status 批准
        r2 = integration.sync_status(type_a, "approved", reviewer_id="admin_001", review_comment="ok")
        self.assertTrue(r2["success"])
        # 4. 用 MirrorReadOnlyAdapter 读取（验证只读）
        adapter = MirrorReadOnlyAdapter(mirror)
        proposals = adapter.list_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "approved")
        # 5. metadata 中应有 traceability 字段
        meta = proposals[0].evaluator_meta
        self.assertEqual(meta.get("source_proposal_id"), "prop_a_e2e_001")
        # 6. retry queue 应为空
        self.assertEqual(retry.count(), 0)

    def test_02_full_flow_memory_proposal_isolated(self):
        """完整流程：memory proposal → skip mirror → 不进入 PersonalityAdapter"""
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration
        from src.runtime.adapters.growth_proposal_mirror import (
            GrowthProposalMirrorStorage, MirrorReadOnlyAdapter,
        )
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.proposal.proposal import GrowthProposal
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal as TypeB, ChangeItem

        mirror = GrowthProposalMirrorStorage(storage_path=self.mirror_path)
        retry = RetryQueue(storage_path=self.retry_path)
        integration = GovernanceMirrorIntegration(
            mirror_storage=mirror, enabled=True, retry_queue=retry,
        )

        # 1. Admin 提交 memory 类型 Type A
        type_a = GrowthProposal(
            proposal_id="prop_a_mem_001",
            proposal_type="identity",
            metadata={"action_scope": "memory", "request_kind": "memory_mark_incorrect"},
        )
        # 2. 镜像应被跳过
        r1 = integration.mirror_type_a_to_type_b(type_a)
        self.assertFalse(r1["success"])
        self.assertTrue(r1.get("skipped"))
        # 3. 即使手动构造 Type B 并尝试进入 PersonalityAdapter，也应被短路
        type_b = TypeB(
            evaluator_meta={"action_scope": "memory", "source_proposal_id": "prop_a_mem_001"},
            proposed_changes=[ChangeItem(path="memory.mark_incorrect", before=1, after=0)],
        )
        adapter = PersonalityAdapter()
        result = adapter.apply_proposal(type_b)
        self.assertFalse(result["applied"])
        self.assertIn("memory_action_skipped", result["note"])
        # 4. Mirror 保持空
        self.assertEqual(mirror.count(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
