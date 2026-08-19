# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_integration.py

Phase 5.5 Integration: GrowthProposalAdapter → GovernanceProvider 集成测试

目标：
- 验证 Admin 提交的 Type A Proposal 经 GrowthProposalAdapter 镜像为 Type B
- 验证 ApprovalManager 可通过 MirrorBackedAdapter 读取 Type B 镜像
- 验证 Approve / Reject 流程可走通（Admin → Type A → Type B 镜像 → ApprovalManager）
- 验证镜像集成是可选的（默认 disabled → Phase 5.4 行为不变）
- 验证 RuntimeCore 引用在集成前后保持稳定
- 验证镜像追踪 metadata 正确写入 Type A

强约束：
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState 实例
- 不修改 RuntimeCore / Orchestrator
- 镜像操作失败不影响主流程（best-effort）
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock RuntimeProvider（与 test_admin_governance 一致）
# =====================================================================

class MockRuntimeProvider:
    """测试用 Mock RuntimeProvider，不创建任何 Authority 实例。"""

    def __init__(self):
        self._personality_data = {
            "available": True,
            "current": {
                "开放性": 0.75,
                "严谨性": 0.65,
                "外向性": 0.55,
                "宜人性": 0.85,
                "神经质": 0.30,
            },
            "state": {
                "total_growth": 0.42,
                "maturity": 0.38,
                "self_awareness": 0.55,
                "empathy": 0.60,
                "stability": 0.72,
            },
            "self_model": {"linked_to_growth": True},
        }
        self._memory_data = {
            "available": True,
            "total_count": 5,
            "user_id": "user_test",
            "important_count": 1,
            "recent": [
                {
                    "id": "mem_001",
                    "type": "identity",
                    "content": "用户喜欢古典音乐",
                    "importance": 0.85,
                    "source_event_id": "evt_001",
                    "user_id": "user_test",
                },
            ],
        }
        self._growth_data = {
            "available": True,
            "shared_with_resolver": True,
            "metrics": {
                "total_growth": 0.42,
                "growth_score": 0.50,
            },
        }

    def get_status(self):
        return {"online": True, "runtime": {"initialized": True, "is_running": True}}

    def get_authority_status(self):
        return {
            "memory_store": True,
            "vector_memory": True,
            "emotion_manager": True,
            "personality_resolver": True,
            "self_model_store": True,
            "growth_state": True,
        }

    def get_personality_summary(self):
        return self._personality_data

    def get_memory_summary(self):
        return self._memory_data

    def get_growth_summary(self):
        return self._growth_data

    def get_emotion_summary(self):
        return {"available": True, "current": "平静", "intensity": 0.5, "recent": []}


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tmp_storage_dir():
    """临时 ProposalStorage 目录"""
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_integration_test_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def tmp_mirror_dir():
    """临时 MirrorStorage 目录"""
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_mirror_test_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def mock_provider():
    return MockRuntimeProvider()


@pytest.fixture
def governance_provider_with_mirror(mock_provider, tmp_storage_dir, tmp_mirror_dir):
    """带镜像集成的 GovernanceProvider"""
    from src.growth.proposal.storage import ProposalStorage
    from src.admin.governance_provider import GovernanceProvider
    from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
    from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration

    storage = ProposalStorage(data_dir=tmp_storage_dir)
    mirror = GrowthProposalMirrorStorage(storage_path=str(Path(tmp_mirror_dir) / "mirror.json"))
    integration = GovernanceMirrorIntegration(mirror_storage=mirror, enabled=True)
    return GovernanceProvider(
        runtime_provider=mock_provider,
        proposal_storage=storage,
        mirror_integration=integration,
    )


@pytest.fixture
def governance_provider_no_mirror(mock_provider, tmp_storage_dir):
    """无镜像集成的 GovernanceProvider（验证向后兼容）"""
    from src.growth.proposal.storage import ProposalStorage
    from src.admin.governance_provider import GovernanceProvider

    storage = ProposalStorage(data_dir=tmp_storage_dir)
    return GovernanceProvider(
        runtime_provider=mock_provider,
        proposal_storage=storage,
        mirror_integration=None,  # 默认 disabled
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    try:
        from src.admin.governance_provider import reset_governance_provider_for_testing
        reset_governance_provider_for_testing()
    except Exception:
        pass
    yield
    try:
        from src.admin.governance_provider import reset_governance_provider_for_testing
        reset_governance_provider_for_testing()
    except Exception:
        pass


# =====================================================================
# 1. Admin 提交 Type A Proposal → 自动生成 Type B 镜像
# =====================================================================

class TestAdminProposalAutoMirror:
    """验证 Admin 提交 Type A Proposal 后自动生成 Type B 镜像。"""

    def test_personality_proposal_creates_type_b_mirror(
        self, governance_provider_with_mirror, tmp_mirror_dir
    ):
        """Personality Proposal 提交后应有 Type B 镜像。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        req = PersonalityChangeRequest(
            trait="开放性",
            delta=0.1,
            confidence=0.8,
            reason="test_personality_mirror",
            evidence=["evt_001"],
            priority="medium",
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]
        assert pid.startswith("gov_")

        # 验证 Type A 已写入 ProposalStorage
        type_a = governance_provider_with_mirror._storage.load(pid)
        assert type_a is not None
        assert type_a.proposal_id == pid

        # 验证 Type B 镜像已写入
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        mirror_path = Path(tmp_mirror_dir) / "mirror.json"
        assert mirror_path.exists()
        mirror = GrowthProposalMirrorStorage(storage_path=str(mirror_path))
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data is not None
        assert type_b_data["id"] == pid

    def test_memory_proposal_skips_mirror_phase_5_5_1(
        self, governance_provider_with_mirror, tmp_mirror_dir
    ):
        """Phase 5.5.1 Hotfix [3]: Memory Action Proposal 不再被镜像。

        Memory Action 类型 proposal 应当：
        - 被 GovernanceProvider 接受
        - 不进入 Mirror（防止进入 PersonalityAdapter）
        - 不创建无意义 GrowthProposal
        """
        from src.contracts.governance_schema import MemoryActionRequest

        req = MemoryActionRequest(
            memory_id="mem_001",
            action="mark_incorrect",
            reason="test_memory_mirror",
            priority="high",
        )
        result = governance_provider_with_mirror.propose_memory_action(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]

        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        mirror_path = Path(tmp_mirror_dir) / "mirror.json"
        mirror = GrowthProposalMirrorStorage(storage_path=str(mirror_path))
        # Phase 5.5.1 [3]: Memory Action 不再生成 Type B 镜像
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data is None, (
            "Phase 5.5.1 [3] Memory Action 隔离：mirror 中不应存在 Memory Action 镜像"
        )
        # 但 Type A 本身仍应存在
        type_a = governance_provider_with_mirror.get_proposal_detail(pid)
        assert type_a is not None
        # Type A metadata 应记录 mirror_skipped
        meta = (type_a or {}).get("metadata", {}) if isinstance(type_a, dict) else getattr(type_a, "metadata", {}) or {}
        assert meta.get("mirror_skipped") is True or meta.get("mirror_attempted") is True


# =====================================================================
# 2. 镜像字段一致性
# =====================================================================

class TestMirrorFieldConsistency:
    """验证 Type A → Type B 字段映射正确。"""

    def test_mirror_id_matches_type_a_proposal_id(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="严谨性", delta=0.05, confidence=0.7, reason="id_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data["id"] == pid

    def test_mirror_confidence_matches(self, governance_provider_with_mirror):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="外向性", delta=-0.05, confidence=0.65, reason="confidence_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert abs(type_b_data["confidence"] - 0.65) < 1e-6

    def test_mirror_proposed_changes_have_trait(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="宜人性", delta=0.15, confidence=0.85, reason="trait_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        changes = type_b_data["proposed_changes"]
        assert len(changes) >= 1
        paths = [c["path"] for c in changes]
        assert "宜人性" in paths

    def test_mirror_evidence_ids(self, governance_provider_with_mirror):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="神经质",
            delta=-0.1,
            confidence=0.9,
            reason="evidence_test",
            evidence=["evt_001", "evt_002"],
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert "evt_001" in type_b_data["evidence_ids"]
        assert "evt_002" in type_b_data["evidence_ids"]


# =====================================================================
# 3. 镜像追踪 metadata
# =====================================================================

class TestMirrorTrackingMetadata:
    """验证 Type A metadata 中包含 mirror 追踪字段。"""

    def test_type_a_metadata_has_mirror_proposal_id(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.8, reason="meta_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        type_a = governance_provider_with_mirror._storage.load(pid)
        meta = type_a.metadata or {}
        assert meta.get("mirror_attempted") is True
        assert meta.get("mirror_success") is True
        assert meta.get("mirror_proposal_id") == pid
        assert meta.get("mirror_schema") == "type_b"
        assert "mirrored_at" in meta

    def test_type_b_evaluator_meta_has_source_a_info(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.8, reason="source_a_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        meta = type_b_data.get("evaluator_meta", {}) or {}
        assert meta.get("mirrored_from_type_a") is True
        assert meta.get("mirror_source_proposal_id") == pid
        assert "mirrored_at" in meta
        assert meta.get("source_schema") == "type_a"


# =====================================================================
# 4. 向后兼容：mirror_integration=None 时不写入镜像
# =====================================================================

class TestBackwardCompatibility:
    """验证未启用镜像时，GovernanceProvider 行为与 Phase 5.4 一致。"""

    def test_no_mirror_when_disabled(self, governance_provider_no_mirror, tmp_mirror_dir):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.8, reason="compat_test"
        )
        result = governance_provider_no_mirror.propose_personality_change(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]

        # Type A 仍写入 ProposalStorage
        type_a = governance_provider_no_mirror._storage.load(pid)
        assert type_a is not None

        # Type B 镜像不存在（mirror_integration 为 None，未创建）
        assert governance_provider_no_mirror.get_mirror_integration() is None

    def test_dynamic_enable_mirror_via_setter(
        self, governance_provider_no_mirror, tmp_mirror_dir
    ):
        """通过 set_mirror_integration 动态启用镜像。"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage
        from src.runtime.adapters.governance_mirror_integration import GovernanceMirrorIntegration

        # 初始无镜像
        assert governance_provider_no_mirror.get_mirror_integration() is None

        # 动态启用
        mirror = GrowthProposalMirrorStorage(storage_path=str(Path(tmp_mirror_dir) / "m.json"))
        integration = GovernanceMirrorIntegration(mirror_storage=mirror, enabled=True)
        governance_provider_no_mirror.set_mirror_integration(integration)

        # 提交 proposal
        req = PersonalityChangeRequest(
            trait="外向性", delta=0.05, confidence=0.7, reason="dynamic_test"
        )
        result = governance_provider_no_mirror.propose_personality_change(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]

        # 验证镜像已写入
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data is not None
        assert type_b_data["id"] == pid

    def test_dynamic_disable_mirror_via_setter(
        self, governance_provider_with_mirror, tmp_mirror_dir
    ):
        """通过 set_mirror_integration(None) 动态禁用镜像。"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        from src.runtime.adapters.growth_proposal_mirror import GrowthProposalMirrorStorage

        # 验证初始已启用
        assert governance_provider_with_mirror.get_mirror_integration() is not None

        # 禁用
        governance_provider_with_mirror.set_mirror_integration(None)
        assert governance_provider_with_mirror.get_mirror_integration() is None

        # 提交 proposal
        req = PersonalityChangeRequest(
            trait="宜人性", delta=0.1, confidence=0.8, reason="disable_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]

        # 镜像计数应不增加（仅初始一次）
        mirror = GrowthProposalMirrorStorage(storage_path=str(Path(tmp_mirror_dir) / "mirror.json"))
        all_mirrors = mirror.list_proposals(limit=100)
        # 由于之前没提交过 proposal，应为 0
        assert len(all_mirrors) == 0


# =====================================================================
# 5. Approve 流程：Type A → Type B 镜像 → ApprovalManager
# =====================================================================

class TestApproveFlowThroughMirror:
    """验证 Admin approve 后 Type B 镜像可被 ApprovalManager 接受。"""

    def test_admin_review_approve_propagates_to_mirror(
        self, governance_provider_with_mirror
    ):
        """review_proposal(approve) 后 Type B 镜像状态同步为 approved。"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
            REVIEW_ACTION_APPROVE,
        )

        # 1. 提交
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.8, reason="approve_flow"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        # 2. 审查 approve
        review_req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action=REVIEW_ACTION_APPROVE,
            reason="approved_via_admin",
        )
        rev_result = governance_provider_with_mirror.review_proposal(review_req, actor="admin")
        assert rev_result["success"] is True
        assert rev_result["after_status"] == "approved"

        # 3. 验证 Type B 镜像状态已同步
        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data["status"] == "approved"
        meta = type_b_data.get("evaluator_meta", {}) or {}
        assert meta.get("source_status") == "approved"
        assert meta.get("reviewer_id") == "admin"

    def test_approval_manager_can_read_mirror_via_adapter(
        self, governance_provider_with_mirror
    ):
        """ApprovalManager 可通过 MirrorBackedAdapter 读取 Type B 镜像。"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
            REVIEW_ACTION_APPROVE,
        )
        from src.growth.approval_manager import ApprovalManager
        from src.runtime.adapters.growth_proposal_mirror import MirrorBackedAdapter

        # 1. 提交 + 审查
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.8, reason="am_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        review_req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action=REVIEW_ACTION_APPROVE,
            reason="am_test_approve",
        )
        governance_provider_with_mirror.review_proposal(review_req, actor="admin")

        # 2. 通过 MirrorBackedAdapter 构造 ApprovalManager
        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        adapter = MirrorBackedAdapter(mirror)
        # 使用临时 history_path 避免污染
        with tempfile.TemporaryDirectory() as tmp_hist:
            mgr = ApprovalManager(growth_adapter=adapter, history_path=str(Path(tmp_hist) / "h.json"))

            # 3. ApprovalManager 可通过 list_proposals 找到该镜像
            props = mgr.list_proposals = adapter.list_proposals(status="proposed", limit=10)
            # 由于已 approve，这里应该是空
            assert all(p.id != pid for p in props)

            # 4. 但通过 get_proposal 仍可获取（快照）
            from src.contracts.growth_schema import GrowthProposal
            p = adapter.get_proposal(pid)
            assert p is not None
            assert p.id == pid
            assert p.status == "approved"
            # 验证镜像内容正确
            assert len(p.proposed_changes) >= 1


# =====================================================================
# 6. Reject 流程：同步 Type B 状态
# =====================================================================

class TestRejectFlowThroughMirror:
    """验证 reject 流程同步 Type B 状态。"""

    def test_admin_review_reject_propagates_to_mirror(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
            REVIEW_ACTION_REJECT,
        )

        # 1. 提交
        req = PersonalityChangeRequest(
            trait="严谨性", delta=-0.1, confidence=0.4, reason="reject_flow"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        # 2. 审查 reject
        review_req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action=REVIEW_ACTION_REJECT,
            reason="rejected_by_admin",
        )
        rev_result = governance_provider_with_mirror.review_proposal(review_req, actor="admin")
        assert rev_result["success"] is True
        assert rev_result["after_status"] == "rejected"

        # 3. 验证 Type B 镜像状态同步为 rejected
        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data["status"] == "rejected"
        meta = type_b_data.get("evaluator_meta", {}) or {}
        assert meta.get("source_status") == "rejected"


# =====================================================================
# 7. ApprovalManager 端到端：Admin → Adapter → ApprovalManager
# =====================================================================

class TestEndToEndApprovalFlow:
    """端到端验证：Admin 提交 → 镜像 → ApprovalManager approve 完整链路。"""

    def test_admin_proposal_full_cycle_via_mirror(
        self, governance_provider_with_mirror
    ):
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
            REVIEW_ACTION_APPROVE,
        )
        from src.growth.approval_manager import ApprovalManager
        from src.runtime.adapters.growth_proposal_mirror import MirrorBackedAdapter

        # 1. Admin 提交 Type A proposal
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.85, reason="e2e_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        assert result["success"] is True
        pid = result["proposal_id"]

        # 2. 验证 Type B 镜像存在，状态为 proposed
        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data is not None
        assert type_b_data["status"] == "proposed"

        # 3. 构造 ApprovalManager 通过 MirrorBackedAdapter
        adapter = MirrorBackedAdapter(mirror)
        with tempfile.TemporaryDirectory() as tmp_hist:
            mgr = ApprovalManager(growth_adapter=adapter, history_path=str(Path(tmp_hist) / "h.json"))

            # 4. ApprovalManager list_proposals(status="proposed") 可见
            proposed = adapter.list_proposals(status="proposed", limit=10)
            assert any(p.id == pid for p in proposed)

            # 5. ApprovalManager 接受该提案
            record = mgr.approve_proposal(pid, reason="e2e_admin_approve", actor="admin")
            assert record is not None
            assert record.proposal_id == pid
            assert record.after_status == "approved"

            # 6. 验证 mirror 状态已更新
            type_b_data2 = mirror.get_proposal(pid)
            assert type_b_data2["status"] == "approved"
            assert "accepted_at" in type_b_data2

            # 7. 验证审批历史已生成
            history = mgr.get_approval_history(limit=10)
            assert len(history) == 1
            assert history[0].proposal_id == pid
            assert history[0].action == "approve"


# =====================================================================
# 8. RuntimeCore 引用稳定性
# =====================================================================

class TestRuntimeCoreStability:
    """验证镜像集成不影响 RuntimeCore 引用。"""

    def test_mirror_storage_does_not_import_runtime_core(self):
        """MirrorStorage 模块不应 import RuntimeCore。"""
        from src.runtime.adapters import growth_proposal_mirror
        source = Path(growth_proposal_mirror.__file__).read_text(encoding="utf-8")
        forbidden_patterns = [
            "from src.runtime.runtime_core import",
            "import src.runtime.runtime_core",
            "from src.orchestrator import",
            "import src.orchestrator",
            "from src.personality.personality_resolver import",
            "from src.memory.memory_store import",
        ]
        for pat in forbidden_patterns:
            assert pat not in source, f"MirrorStorage 不应 import: {pat}"

    def test_governance_mirror_integration_does_not_import_runtime_core(self):
        """GovernanceMirrorIntegration 模块不应 import RuntimeCore。"""
        from src.runtime.adapters import governance_mirror_integration
        source = Path(governance_mirror_integration.__file__).read_text(encoding="utf-8")
        forbidden_patterns = [
            "from src.runtime.runtime_core import",
            "import src.runtime.runtime_core",
            "from src.orchestrator import",
            "import src.orchestrator",
        ]
        for pat in forbidden_patterns:
            assert pat not in source, f"Integration 不应 import: {pat}"

    def test_governance_provider_mirror_methods_dont_create_authority(
        self, governance_provider_with_mirror
    ):
        """GovernanceProvider 镜像方法不创建任何 Authority 实例。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        # 多次调用镜像方法
        for i in range(3):
            req = PersonalityChangeRequest(
                trait="开放性", delta=0.01, confidence=0.5, reason=f"no_auth_{i}"
            )
            governance_provider_with_mirror.propose_personality_change(req, actor="admin")

        # 验证 Provider 没有这些属性
        provider = governance_provider_with_mirror
        forbidden_attrs = [
            "personality_resolver", "memory_store", "emotion_manager",
            "growth_state", "self_model_store", "vector_memory",
            "runtime_core", "orchestrator",
        ]
        for attr in forbidden_attrs:
            assert not hasattr(provider, attr) or getattr(provider, attr, None) is None, \
                f"Provider 不应有属性: {attr}"


# =====================================================================
# 9. 边界条件
# =====================================================================

class TestEdgeCases:
    """边界条件与错误处理。"""

    def test_mirror_storage_isolated_from_proposal_storage(
        self, governance_provider_with_mirror
    ):
        """MirrorStorage 物理隔离于 ProposalStorage。"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(
            trait="开放性", delta=0.1, confidence=0.5, reason="isolation_test"
        )
        result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
        pid = result["proposal_id"]

        # Type A 在 ProposalStorage
        type_a = governance_provider_with_mirror._storage.load(pid)
        assert type_a is not None

        # Type B 在 MirrorStorage（独立）
        mirror = governance_provider_with_mirror.get_mirror_integration().get_mirror_storage()
        type_b_data = mirror.get_proposal(pid)
        assert type_b_data is not None

        # 删除 Type A 不影响 Type B
        governance_provider_with_mirror._storage.delete(pid)
        type_a2 = governance_provider_with_mirror._storage.load(pid)
        assert type_a2 is None
        type_b2 = mirror.get_proposal(pid)
        assert type_b2 is not None  # 仍然存在

    def test_mirror_storage_preserves_unique_ids(
        self, governance_provider_with_mirror
    ):
        """多次提交生成不同的 proposal id。"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        ids = []
        for i in range(3):
            req = PersonalityChangeRequest(
                trait="开放性", delta=0.01, confidence=0.5, reason=f"unique_{i}"
            )
            result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
            ids.append(result["proposal_id"])
        assert len(set(ids)) == 3

    def test_failed_mirror_does_not_block_proposal_creation(
        self, governance_provider_with_mirror
    ):
        """镜像失败不应阻塞 proposal 创建。"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        from unittest.mock import patch

        # 模拟镜像抛出异常
        with patch.object(
            governance_provider_with_mirror._mirror_integration,
            "mirror_type_a_to_type_b",
            side_effect=Exception("simulated_mirror_failure"),
        ):
            req = PersonalityChangeRequest(
                trait="开放性", delta=0.1, confidence=0.5, reason="mirror_fail_test"
            )
            result = governance_provider_with_mirror.propose_personality_change(req, actor="admin")
            # 主流程应仍成功
            assert result["success"] is True
            assert result["proposal_id"].startswith("gov_")
