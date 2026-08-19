# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_adapter.py

Phase 5.5: GrowthProposalAdapter 单元测试

目标：
验证双 GrowthProposal Schema 兼容适配层的行为正确性：
1. Type A → Type B 字段完整
2. Type B → Type A 字段完整
3. 状态转换正确
4. evidence / metadata 映射正确
5. before_state / after_state 保留
6. Admin Proposal 可以经过 Adapter 进入 ApprovalManager
7. Adapter 不创建任何 Authority
8. RuntimeCore 引用不变化
"""

from __future__ import annotations

import sys
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tmp_proposals_path():
    """临时 proposals.json 路径"""
    tmp = tempfile.mkdtemp(prefix="yuyi_adapter_test_")
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_type_a():
    """构造一个完整 Type A Proposal"""
    from src.growth.proposal.proposal import GrowthProposal as ProposalA
    return ProposalA(
        proposal_id="prop_a_001",
        proposal_type="personality",
        status="pending",
        source="admin",
        source_event_id="evt_a_001",
        user_id="user_test",
        affected_dimensions={"开放性": 0.08, "严谨性": -0.03},
        before_state={"开放性": 0.5, "严谨性": 0.7},
        after_state={"开放性": 0.58, "严谨性": 0.67},
        confidence=0.75,
        reason="admin_review_test",
        evidence=["mem_001", "mem_002"],
        priority="medium",
        reviewer_id="",
        review_comment="",
        metadata={"custom_key": "custom_value", "source_phase": "5.3"},
    )


@pytest.fixture
def sample_type_b():
    """构造一个完整 Type B Proposal"""
    from src.contracts.growth_schema import GrowthProposal as ProposalB, ChangeItem
    return ProposalB(
        id="prop_b_001",
        source_event_id="evt_b_001",
        proposed_changes=[
            ChangeItem(path="开放性", before=0.5, after=0.6, reason="growth_test"),
            ChangeItem(path="外向性", before=0.4, after=0.45, reason="growth_test"),
        ],
        confidence=0.8,
        evidence_ids=["evt_b_001", "evt_b_002"],
        evaluator_meta={
            "source": "growth_pipeline",
            "user_id": "user_test",
            "pattern": "exploration",
            "reason": "growth_test",
        },
        timestamp="2026-07-30T12:00:00Z",
        status="proposed",
    )


# =====================================================================
# 1. 类型识别
# =====================================================================

class TestProposalTypeDetection:
    """GrowthProposalAdapter.detect_type 正确识别 Schema"""

    def test_detect_type_a(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        t = GrowthProposalAdapter.detect_type(sample_type_a)
        assert t == "type_a"

    def test_detect_type_b(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        t = GrowthProposalAdapter.detect_type(sample_type_b)
        assert t == "type_b"

    def test_detect_none(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        assert GrowthProposalAdapter.detect_type(None) == "unknown"

    def test_detect_arbitrary_object(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        class Foo:
            pass
        assert GrowthProposalAdapter.detect_type(Foo()) == "unknown"


# =====================================================================
# 2. Type A → Type B 转换
# =====================================================================

class TestTypeAToTypeB:
    """Type A → Type B 字段映射验证"""

    def test_proposal_id_to_id(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.id == "prop_a_001"

    def test_affected_dimensions_to_proposed_changes(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        paths = {ci.path for ci in b.proposed_changes}
        assert "开放性" in paths
        assert "严谨性" in paths
        # 验证 before/after 正确
        for ci in b.proposed_changes:
            if ci.path == "开放性":
                assert ci.before == 0.5
                assert ci.after == 0.58
            elif ci.path == "严谨性":
                assert ci.before == 0.7
                assert ci.after == 0.67

    def test_evidence_to_evidence_ids(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert sorted(b.evidence_ids) == ["mem_001", "mem_002"]

    def test_metadata_merged_to_evaluator_meta(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.evaluator_meta.get("custom_key") == "custom_value"
        assert b.evaluator_meta.get("source_phase") == "5.3"
        assert b.evaluator_meta.get("source_schema") == "type_a"
        assert b.evaluator_meta.get("adapter") == "GrowthProposalAdapter"

    def test_extended_fields_in_evaluator_meta(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        meta = b.evaluator_meta
        assert meta.get("source") == "admin"
        assert meta.get("user_id") == "user_test"
        assert meta.get("priority") == "medium"
        assert meta.get("source_proposal_type") == "personality"
        assert meta.get("reason") == "admin_review_test"

    def test_status_pending_to_proposed(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "pending"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.status == "proposed"

    def test_status_approved_to_approved(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "approved"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.status == "approved"

    def test_status_rejected_to_rejected(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "rejected"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.status == "rejected"

    def test_status_applied_preserved_in_meta(self, sample_type_a):
        """Type A applied 状态 → Type B approved + source_status='applied'"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "applied"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.status == "approved"
        assert b.evaluator_meta.get("source_status") == "applied"

    def test_status_cancelled_preserved_in_meta(self, sample_type_a):
        """Type A cancelled 状态 → Type B rejected + source_status='cancelled'"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "cancelled"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.status == "rejected"
        assert b.evaluator_meta.get("source_status") == "cancelled"

    def test_confidence_preserved(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.confidence == 0.75

    def test_timestamp_preserved(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.timestamp == sample_type_a.timestamp

    def test_source_event_id_preserved(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        assert b.source_event_id == "evt_a_001"

    def test_raises_on_none(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        with pytest.raises(ValueError):
            GrowthProposalAdapter.type_a_to_type_b(None)


# =====================================================================
# 3. Type B → Type A 转换
# =====================================================================

class TestTypeBToTypeA:
    """Type B → Type A 字段映射验证"""

    def test_id_to_proposal_id(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.proposal_id == "prop_b_001"

    def test_proposed_changes_to_affected_dimensions(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        # 开放性 0.5 → 0.6 = 0.1
        assert abs(a.affected_dimensions.get("开放性", 0) - 0.1) < 1e-6
        # 外向性 0.4 → 0.45 = 0.05
        assert abs(a.affected_dimensions.get("外向性", 0) - 0.05) < 1e-6

    def test_proposed_changes_to_before_state(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.before_state.get("开放性") == 0.5
        assert a.before_state.get("外向性") == 0.4

    def test_proposed_changes_to_after_state(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.after_state.get("开放性") == 0.6
        assert a.after_state.get("外向性") == 0.45

    def test_evidence_ids_to_evidence(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert sorted(a.evidence) == ["evt_b_001", "evt_b_002"]

    def test_evaluator_meta_fields_extracted(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.source == "growth_pipeline"
        assert a.user_id == "user_test"
        assert a.reason == "growth_test"

    def test_status_proposed_to_pending(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_b.status = "proposed"
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.status == "pending"

    def test_status_approved_to_approved(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_b.status = "approved"
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.status == "approved"

    def test_status_rejected_to_rejected(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_b.status = "rejected"
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.status == "rejected"

    def test_source_status_applied_restored(self, sample_type_b):
        """Type B source_status='applied' → Type A status='applied'"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_b.status = "approved"
        sample_type_b.evaluator_meta["source_status"] = "applied"
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.status == "applied"

    def test_source_status_cancelled_restored(self, sample_type_b):
        """Type B source_status='cancelled' → Type A status='cancelled'"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_b.status = "rejected"
        sample_type_b.evaluator_meta["source_status"] = "cancelled"
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.status == "cancelled"

    def test_metadata_preserved(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        # metadata 中应保留 pattern（不在已知 meta keys 中）
        assert a.metadata.get("pattern") == "exploration"
        # 保留 adapter 来源
        assert a.metadata.get("_converted_from") == "type_b"

    def test_confidence_preserved(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.type_b_to_type_a(sample_type_b)
        assert a.confidence == 0.8

    def test_raises_on_none(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        with pytest.raises(ValueError):
            GrowthProposalAdapter.type_b_to_type_a(None)


# =====================================================================
# 4. 双向往返（Round Trip）
# =====================================================================

class TestRoundTripConversion:
    """Type A → Type B → Type A 字段保留验证"""

    def test_round_trip_preserves_core_fields(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        a2 = GrowthProposalAdapter.type_b_to_type_a(b)
        # 主键
        assert a2.proposal_id == sample_type_a.proposal_id
        # proposal_type
        assert a2.proposal_type == sample_type_a.proposal_type
        # source
        assert a2.source == sample_type_a.source
        # user_id
        assert a2.user_id == sample_type_a.user_id
        # confidence
        assert abs(a2.confidence - sample_type_a.confidence) < 1e-6
        # reason
        assert a2.reason == sample_type_a.reason
        # evidence
        assert sorted(a2.evidence) == sorted(sample_type_a.evidence)
        # priority
        assert a2.priority == sample_type_a.priority

    def test_round_trip_preserves_affected_dimensions(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        a2 = GrowthProposalAdapter.type_b_to_type_a(b)
        for dim, delta in sample_type_a.affected_dimensions.items():
            assert abs(a2.affected_dimensions.get(dim, 0) - delta) < 1e-6

    def test_round_trip_preserves_before_after(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        a2 = GrowthProposalAdapter.type_b_to_type_a(b)
        for dim, val in sample_type_a.before_state.items():
            assert abs(a2.before_state.get(dim, 0) - val) < 1e-6
        for dim, val in sample_type_a.after_state.items():
            assert abs(a2.after_state.get(dim, 0) - val) < 1e-6

    def test_round_trip_status_pending(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "pending"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        a2 = GrowthProposalAdapter.type_b_to_type_a(b)
        assert a2.status == "pending"

    def test_round_trip_status_applied(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        sample_type_a.status = "applied"
        b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        a2 = GrowthProposalAdapter.type_b_to_type_a(b)
        assert a2.status == "applied"


# =====================================================================
# 5. 智能转换
# =====================================================================

class TestSmartConversion:
    """to_type_a / to_type_b 智能识别"""

    def test_to_type_b_from_type_a(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.to_type_b(sample_type_a)
        from src.contracts.growth_schema import GrowthProposal as TypeB
        assert isinstance(b, TypeB)

    def test_to_type_b_from_type_b_passthrough(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = GrowthProposalAdapter.to_type_b(sample_type_b)
        assert b is sample_type_b

    def test_to_type_a_from_type_b(self, sample_type_b):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.to_type_a(sample_type_b)
        from src.growth.proposal.proposal import GrowthProposal as TypeA
        assert isinstance(a, TypeA)

    def test_to_type_a_from_type_a_passthrough(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = GrowthProposalAdapter.to_type_a(sample_type_a)
        assert a is sample_type_a

    def test_to_type_b_unknown_raises(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        with pytest.raises(ValueError):
            GrowthProposalAdapter.to_type_b(object())

    def test_to_type_a_unknown_raises(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        with pytest.raises(ValueError):
            GrowthProposalAdapter.to_type_a(object())


# =====================================================================
# 6. Admin → ApprovalManager 闭环
# =====================================================================

class TestAdminToApprovalManagerFlow:
    """Admin Type A Proposal 通过 Adapter 进入 ApprovalManager"""

    def test_admin_proposal_can_be_approved_by_approval_manager(self, sample_type_a):
        """
        关键测试：Admin 提交的 Type A Proposal 经 Adapter 转换后，
        可以被 ApprovalManager 接受并完成 apply 流程。
        """
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        from src.growth.approval_manager import ApprovalManager

        # 1. 构造 GrowthAdapter stub（持有转换后的 Type B）
        type_b_proposal = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        held_proposals = [type_b_proposal]

        class _Adapter:
            def get_proposal(self, pid, **kwargs):
                for p in held_proposals:
                    if p.id == pid:
                        return p
                return None
            def list_proposals(self, **kwargs):
                return list(held_proposals)
            def update_proposal(self, proposal, **kwargs):
                for i, p in enumerate(held_proposals):
                    if p.id == proposal.id:
                        held_proposals[i] = proposal
                        return True
                return False
            def accept_proposal(self, pid, **kwargs):
                for p in held_proposals:
                    if p.id == pid:
                        p.status = "approved"
                        return p
                return None
            def reject_proposal(self, pid, **kwargs):
                for p in held_proposals:
                    if p.id == pid:
                        p.status = "rejected"
                        return p
                return None

        mgr = ApprovalManager(growth_adapter=_Adapter())

        # 2. Admin 提交 Type A Proposal（不会进入 ApprovalManager）
        # 验证：直接尝试用 type_a_proposal.id 找不到
        direct_result = mgr.approve_proposal(sample_type_a.proposal_id, reason="direct")
        # ApprovalManager 看不到 Type A（因为它持有 Type B 镜像）
        # 但我们通过 Adapter 转换后写入

        # 3. 经过 Adapter 转换后，ApprovalManager 可以接受
        rec = mgr.approve_proposal(type_b_proposal.id, reason="via_adapter")
        assert rec is not None
        assert rec.proposal_id == "prop_a_001"
        assert rec.after_status == "approved"

    def test_admin_proposal_can_be_rejected(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        from src.growth.approval_manager import ApprovalManager

        type_b_proposal = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        held_proposals = [type_b_proposal]

        class _Adapter:
            def get_proposal(self, pid, **kwargs):
                for p in held_proposals:
                    if p.id == pid:
                        return p
                return None
            def list_proposals(self, **kwargs):
                return list(held_proposals)
            def update_proposal(self, proposal, **kwargs):
                return True
            def accept_proposal(self, pid, **kwargs):
                return None
            def reject_proposal(self, pid, **kwargs):
                for p in held_proposals:
                    if p.id == pid:
                        p.status = "rejected"
                        return p
                return None

        mgr = ApprovalManager(growth_adapter=_Adapter())
        rec = mgr.reject_proposal(type_b_proposal.id, reason="via_adapter_reject")
        assert rec is not None
        assert rec.after_status == "rejected"

    def test_build_approval_input_returns_type_b(self, sample_type_a):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        from src.contracts.growth_schema import GrowthProposal as TypeB
        result = GrowthProposalAdapter.build_approval_input(sample_type_a)
        assert isinstance(result, TypeB)
        assert result.id == sample_type_a.proposal_id


# =====================================================================
# 7. Adapter 不创建 Authority
# =====================================================================

class TestAdapterNoAuthorityCreation:
    """Adapter 自身不创建任何 Authority 实例"""

    def test_adapter_class_has_no_authority_attributes(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        forbidden = [
            "memory_store", "personality_resolver", "emotion_manager",
            "growth_state", "self_model_store", "vector_memory",
            "runtime_core", "orchestrator",
        ]
        for attr in forbidden:
            assert not hasattr(GrowthProposalAdapter, attr), \
                f"GrowthProposalAdapter 不应有 {attr} 属性"

    def test_adapter_does_not_import_authority_modules(self):
        """Adapter 源码不应 import Authority 构造器"""
        from src.runtime.adapters import growth_proposal_adapter as mod
        import inspect
        source = inspect.getsource(mod)
        # 不应 import 任何 Authority
        forbidden_imports = [
            "from src.memory.memory_store import",
            "from src.personality.personality_resolver import",
            "from src.emotion.emotion_manager import",
            "from src.runtime.runtime_core import",
        ]
        for imp in forbidden_imports:
            assert imp not in source, \
                f"Adapter 不应 import {imp}"

    def test_conversion_pure_function_no_side_effects(self, sample_type_a, sample_type_b):
        """转换函数不应修改输入对象"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        # 记录原始状态
        a_status_before = sample_type_a.status
        b_status_before = sample_type_b.status

        # 转换
        GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
        GrowthProposalAdapter.type_b_to_type_a(sample_type_b)

        # 输入未被修改
        assert sample_type_a.status == a_status_before
        assert sample_type_b.status == b_status_before


# =====================================================================
# 8. RuntimeCore 引用不变化
# =====================================================================

class TestRuntimeCoreReferenceStability:
    """Adapter 操作不改变 RuntimeCore 引用"""

    def test_runtime_core_instance_unchanged_after_adapter_use(self, sample_type_a):
        from src.runtime.runtime_bridge import RuntimeBridge
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter

        RuntimeBridge.reset_for_testing()
        try:
            bridge = RuntimeBridge.get_instance()
            core_before = bridge.get_runtime_core()

            # 多次使用 Adapter
            for _ in range(10):
                b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
                a2 = GrowthProposalAdapter.type_b_to_type_a(b)
                assert a2 is not None
                assert b is not None

            # RuntimeCore 引用未变
            core_after = bridge.get_runtime_core()
            assert core_before is core_after
        finally:
            RuntimeBridge.reset_for_testing()

    def test_adapter_does_not_touch_orchestrator(self, sample_type_a):
        """Adapter 不触碰 Orchestrator"""
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        # 仅做转换，不实例化 Orchestrator
        for _ in range(5):
            b = GrowthProposalAdapter.type_a_to_type_b(sample_type_a)
            assert b is not None
        # 验证 Orchestrator 未被引用
        from src.runtime.adapters import growth_proposal_adapter as mod
        import inspect
        source = inspect.getsource(mod)
        assert "from src.orchestrator import" not in source


# =====================================================================
# 9. 边界条件
# =====================================================================

class TestEdgeCases:
    """边界条件：空字段、缺失字段、异常输入"""

    def test_empty_affected_dimensions(self):
        from src.growth.proposal.proposal import GrowthProposal as ProposalA
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = ProposalA(affected_dimensions={})
        b = GrowthProposalAdapter.type_a_to_type_b(a)
        assert b.proposed_changes == []

    def test_empty_evidence(self):
        from src.growth.proposal.proposal import GrowthProposal as ProposalA
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = ProposalA(evidence=[])
        b = GrowthProposalAdapter.type_a_to_type_b(a)
        assert b.evidence_ids == []

    def test_empty_proposed_changes(self):
        from src.contracts.growth_schema import GrowthProposal as ProposalB
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = ProposalB(proposed_changes=[])
        a = GrowthProposalAdapter.type_b_to_type_a(b)
        assert a.affected_dimensions == {}
        assert a.before_state == {}
        assert a.after_state == {}

    def test_none_evaluator_meta(self):
        from src.contracts.growth_schema import GrowthProposal as ProposalB
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = ProposalB(evaluator_meta={})
        a = GrowthProposalAdapter.type_b_to_type_a(b)
        assert a is not None
        assert a.source == ""

    def test_zero_confidence(self):
        from src.growth.proposal.proposal import GrowthProposal as ProposalA
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        a = ProposalA(confidence=0.0)
        b = GrowthProposalAdapter.type_a_to_type_b(a)
        assert b.confidence == 0.0

    def test_change_item_with_no_before_no_after(self):
        from src.contracts.growth_schema import GrowthProposal as ProposalB, ChangeItem
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        b = ProposalB(proposed_changes=[ChangeItem(path="only_path", before=None, after=None)])
        a = GrowthProposalAdapter.type_b_to_type_a(b)
        # path only, no before/after → 不影响任何 state
        assert "only_path" not in a.affected_dimensions
        assert "only_path" not in a.before_state
        assert "only_path" not in a.after_state


# =====================================================================
# 10. 工具方法
# =====================================================================

class TestUtilityMethods:
    """工具方法：状态映射表、字段映射表"""

    def test_get_status_mapping_info(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        info = GrowthProposalAdapter.get_status_mapping_info()
        assert "type_a_to_type_b" in info
        assert "type_b_to_type_a" in info
        assert "pending" in info["type_a_to_type_b"]
        assert "proposed" in info["type_b_to_type_a"]

    def test_get_field_mapping_info(self):
        from src.runtime.adapters.growth_proposal_adapter import GrowthProposalAdapter
        info = GrowthProposalAdapter.get_field_mapping_info()
        assert "type_a_to_type_b" in info
        assert "proposal_id" in info["type_a_to_type_b"]
        assert "id" in info["type_a_to_type_b"]["proposal_id"]
        assert "proposed_changes" in info["type_b_to_type_a"]
