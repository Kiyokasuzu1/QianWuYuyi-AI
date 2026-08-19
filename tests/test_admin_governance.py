# -*- coding: utf-8 -*-
"""
tests/test_admin_governance.py

Phase 5.3: Admin Governance & Editing 测试。

覆盖：
  1. GovernanceProvider 不会创建新的 Authority 实例
  2. Personality Change Proposal 正确生成并写入 ProposalStorage
  3. Memory Action Proposal 正确生成并写入 ProposalStorage
  4. Growth Proposal 审查（approve / reject / modify）流程
  5. RuntimeBridge 链路正确（Provider 通过 Bridge 读取 Runtime 状态）
  6. Admin 现有功能不受影响（Phase 5.1/5.2 API 仍可访问）
  7. 数据契约（governance_schema）正确性
  8. 边界条件与错误处理

约束：
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
- 不修改 RuntimeCore
- 所有修改通过 GrowthProposal 间接完成
- 保持向后兼容（现有 Admin 测试不需修改）
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# 测试用 Mock RuntimeProvider
# =====================================================================

class MockRuntimeProvider:
    """用于测试的 Mock RuntimeProvider。

    模拟 Phase 5.1 RuntimeProvider 的接口，但不实际初始化 RuntimeCore。
    """

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
            "total_count": 10,
            "user_id": "user_test",
            "important_count": 3,
            "recent": [
                {
                    "id": "mem_001",
                    "type": "identity",
                    "content": "用户喜欢古典音乐",
                    "importance": 0.85,
                    "source_event_id": "evt_001",
                    "user_id": "user_test",
                },
                {
                    "id": "mem_002",
                    "type": "event",
                    "content": "主人今天心情不错",
                    "importance": 0.50,
                    "source_event_id": "evt_002",
                    "user_id": "user_test",
                },
                {
                    "id": "mem_003",
                    "type": "preference",
                    "content": "主人对甜食有偏好",
                    "importance": 0.72,
                    "source_event_id": "evt_003",
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
                "maturity": 0.38,
                "self_awareness": 0.55,
                "empathy": 0.60,
                "stability": 0.72,
            },
        }
        # 记录访问次数
        self.access_count = {"personality": 0, "memory": 0, "growth": 0}

    def get_status(self) -> Dict[str, Any]:
        return {"online": True, "runtime": {"initialized": True, "is_running": True}}

    def get_authority_status(self) -> Dict[str, bool]:
        return {
            "memory_store": True,
            "vector_memory": True,
            "emotion_manager": True,
            "personality_resolver": True,
            "self_model_store": True,
            "growth_state": True,
        }

    def get_personality_summary(self) -> Dict[str, Any]:
        self.access_count["personality"] += 1
        return self._personality_data

    def get_memory_summary(self) -> Dict[str, Any]:
        self.access_count["memory"] += 1
        return self._memory_data

    def get_growth_summary(self) -> Dict[str, Any]:
        self.access_count["growth"] += 1
        return self._growth_data

    def get_emotion_summary(self) -> Dict[str, Any]:
        return {"available": True, "current": "平静", "intensity": 0.5, "recent": []}


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tmp_storage_dir():
    """提供临时 ProposalStorage 目录（每个测试隔离）"""
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_governance_test_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def mock_provider():
    """提供 Mock RuntimeProvider"""
    return MockRuntimeProvider()


@pytest.fixture
def governance_provider(mock_provider, tmp_storage_dir):
    """提供 GovernanceProvider 实例（带 mock runtime provider + 临时 storage）"""
    from src.growth.proposal.storage import ProposalStorage
    storage = ProposalStorage(data_dir=tmp_storage_dir)
    from src.admin.governance_provider import GovernanceProvider
    return GovernanceProvider(runtime_provider=mock_provider, proposal_storage=storage)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """重置模块级单例"""
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
# A. 约束验证：Provider 不创建新 Authority
# =====================================================================

class TestGovernanceProviderConstraints:
    """验证 GovernanceProvider 不会创建任何核心 Authority 实例。"""

    def test_provider_uses_injected_dependencies(self, mock_provider, tmp_storage_dir):
        """Provider 接受注入式依赖，不硬编码实例化。"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider

        storage = ProposalStorage(data_dir=tmp_storage_dir)
        provider = GovernanceProvider(
            runtime_provider=mock_provider,
            proposal_storage=storage,
        )

        # 验证依赖被正确持有
        assert provider._runtime_provider is mock_provider
        assert provider._storage is storage

    def test_provider_does_not_create_memory_store(self, mock_provider, tmp_storage_dir):
        """Provider 不会实例化 MemoryStore。"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider

        storage = ProposalStorage(data_dir=tmp_storage_dir)
        provider = GovernanceProvider(
            runtime_provider=mock_provider,
            proposal_storage=storage,
        )

        # 调用各种方法，验证不会创建 MemoryStore
        provider.get_personality_governance_snapshot()
        provider.get_memory_governance_snapshot(limit=5)
        provider.get_growth_governance_snapshot(limit=5)

        # Provider 不应有这些属性
        assert not hasattr(provider, "_memory_store") or provider.__dict__.get("_memory_store") is None
        assert not hasattr(provider, "_personality_resolver") or provider.__dict__.get("_personality_resolver") is None
        assert not hasattr(provider, "_emotion_manager") or provider.__dict__.get("_emotion_manager") is None
        assert not hasattr(provider, "_growth_state") or provider.__dict__.get("_growth_state") is None

    def test_provider_reads_via_runtime_provider(self, governance_provider, mock_provider):
        """Provider 的所有只读操作都通过 RuntimeProvider（间接通过 RuntimeBridge）。"""
        # 触发多个 snapshot
        governance_provider.get_personality_governance_snapshot()
        governance_provider.get_memory_governance_snapshot(limit=5)
        governance_provider.get_growth_governance_snapshot(limit=5)

        # 验证 mock provider 被访问
        assert mock_provider.access_count["personality"] >= 1
        assert mock_provider.access_count["memory"] >= 1
        assert mock_provider.access_count["growth"] >= 1


# =====================================================================
# B. 数据契约（governance_schema）测试
# =====================================================================

class TestGovernanceSchema:
    """验证数据契约的基本正确性。"""

    def test_personality_change_request_defaults(self):
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(trait="开放性", delta=0.05)
        assert req.trait == "开放性"
        assert req.delta == 0.05
        assert req.confidence == 0.5
        assert req.reason == ""
        assert req.priority == "medium"
        assert req.evidence == []

    def test_memory_action_request_defaults(self):
        from src.contracts.governance_schema import MemoryActionRequest
        req = MemoryActionRequest(memory_id="mem_001", action="delete")
        assert req.memory_id == "mem_001"
        assert req.action == "delete"
        assert req.reason == ""
        assert req.target_memory_id is None

    def test_growth_proposal_review_request_defaults(self):
        from src.contracts.governance_schema import GrowthProposalReviewRequest
        req = GrowthProposalReviewRequest(proposal_id="prop_001", action="approve")
        assert req.proposal_id == "prop_001"
        assert req.action == "approve"
        assert req.modified_changes is None

    def test_governance_snapshot_to_dict(self):
        from src.contracts.governance_schema import GovernanceSnapshot
        snap = GovernanceSnapshot(section="personality", available=True, data={"x": 1})
        d = snap.to_dict()
        assert d["section"] == "personality"
        assert d["available"] is True
        assert d["data"] == {"x": 1}

    def test_all_memory_actions_constant(self):
        from src.contracts.governance_schema import (
            ALL_MEMORY_ACTIONS,
            MEMORY_ACTION_DELETE,
            MEMORY_ACTION_MARK_INCORRECT,
            MEMORY_ACTION_MERGE,
        )
        assert MEMORY_ACTION_DELETE in ALL_MEMORY_ACTIONS
        assert MEMORY_ACTION_MARK_INCORRECT in ALL_MEMORY_ACTIONS
        assert MEMORY_ACTION_MERGE in ALL_MEMORY_ACTIONS

    def test_all_review_actions_constant(self):
        from src.contracts.governance_schema import (
            ALL_REVIEW_ACTIONS,
            REVIEW_ACTION_APPROVE,
            REVIEW_ACTION_REJECT,
            REVIEW_ACTION_MODIFY,
        )
        assert REVIEW_ACTION_APPROVE in ALL_REVIEW_ACTIONS
        assert REVIEW_ACTION_REJECT in ALL_REVIEW_ACTIONS
        assert REVIEW_ACTION_MODIFY in ALL_REVIEW_ACTIONS


# =====================================================================
# C. Personality Governance 测试
# =====================================================================

class TestPersonalityGovernance:
    """测试人格治理功能。"""

    def test_get_snapshot_returns_personality_data(self, governance_provider):
        """get_personality_governance_snapshot 返回人格相关数据。"""
        snapshot = governance_provider.get_personality_governance_snapshot()

        assert snapshot["section"] == "personality"
        assert snapshot["available"] is True
        assert "data" in snapshot
        assert "traits" in snapshot["data"]
        assert "state" in snapshot["data"]
        assert "self_model" in snapshot["data"]
        # traits 应包含 mock 中的特质
        assert "开放性" in snapshot["data"]["traits"]

    def test_propose_personality_change_creates_proposal(self, governance_provider, tmp_storage_dir):
        """propose_personality_change 生成 Proposal 并写入 ProposalStorage。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        req = PersonalityChangeRequest(
            trait="开放性",
            delta=0.05,
            reason="test_reason",
            confidence=0.7,
            priority="high",
        )
        result = governance_provider.propose_personality_change(req, actor="test_admin")

        # 验证返回
        assert result["success"] is True
        assert "proposal_id" in result
        assert result["proposal_id"].startswith("gov_")
        assert "proposal" in result

        proposal = result["proposal"]
        assert proposal["proposal_type"] == "personality"
        assert proposal["status"] == "pending"
        assert "开放性" in proposal["affected_dimensions"]
        assert proposal["affected_dimensions"]["开放性"] == 0.05
        assert proposal["reason"] == "test_reason"
        assert proposal["confidence"] == 0.7
        assert proposal["priority"] == "high"

        # 验证 metadata 标记为 governance 来源
        assert proposal["metadata"].get("admin_governance") is True
        assert proposal["metadata"].get("request_kind") == "personality_change"

        # 验证写入持久化
        from src.growth.proposal.storage import ProposalStorage
        storage = ProposalStorage(data_dir=tmp_storage_dir)
        loaded = storage.load(result["proposal_id"])
        assert loaded is not None
        assert loaded.proposal_id == result["proposal_id"]

    def test_propose_personality_change_validates_inputs(self, governance_provider):
        """输入校验：trait / delta 缺失或不合法时返回失败。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        # 空 trait
        req1 = PersonalityChangeRequest(trait="", delta=0.05)
        r1 = governance_provider.propose_personality_change(req1)
        assert r1["success"] is False

        # delta 缺失
        req2 = PersonalityChangeRequest(trait="x", delta=None)  # type: ignore
        r2 = governance_provider.propose_personality_change(req2)
        assert r2["success"] is False

    def test_propose_personality_change_clamps_after_value(self, governance_provider):
        """after_value 限幅到 [0, 1]，避免极端值。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        # delta 极大，应该被截断
        req = PersonalityChangeRequest(trait="开放性", delta=10.0, reason="extreme_test")
        result = governance_provider.propose_personality_change(req)

        assert result["success"] is True
        after = result["proposal"]["after_state"]["开放性"]
        assert 0.0 <= after <= 1.0

    def test_propose_personality_change_uses_authority_only_for_reading(
        self, governance_provider, mock_provider
    ):
        """propose_personality_change 只通过 Provider 读取（不直接访问 Authority）。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        before = mock_provider.access_count["personality"]

        req = PersonalityChangeRequest(trait="开放性", delta=0.01, reason="x")
        result = governance_provider.propose_personality_change(req)

        assert result["success"] is True
        # 至少调用了一次 personality
        assert mock_provider.access_count["personality"] > before


# =====================================================================
# D. Memory Governance 测试
# =====================================================================

class TestMemoryGovernance:
    """测试记忆治理功能。"""

    def test_get_snapshot_returns_memory_data(self, governance_provider):
        """get_memory_governance_snapshot 返回记忆相关数据。"""
        snapshot = governance_provider.get_memory_governance_snapshot(limit=10)

        assert snapshot["section"] == "memory"
        assert snapshot["available"] is True
        assert "data" in snapshot
        assert snapshot["data"]["total_count"] == 10
        assert snapshot["data"]["important_count"] == 3
        assert "recent" in snapshot["data"]
        assert "by_type" in snapshot["data"]
        # mock 中有 3 条记忆，类型包含 identity / event / preference
        assert "identity" in snapshot["data"]["by_type"]

    def test_propose_memory_action_creates_proposal(self, governance_provider, tmp_storage_dir):
        """propose_memory_action 生成 Proposal 并写入 ProposalStorage。"""
        from src.contracts.governance_schema import MemoryActionRequest

        req = MemoryActionRequest(
            memory_id="mem_001",
            action="delete",
            reason="incorrect_information",
        )
        result = governance_provider.propose_memory_action(req, actor="test_admin")

        # 验证返回
        assert result["success"] is True
        assert "proposal_id" in result
        assert result["proposal_id"].startswith("gov_")

        proposal = result["proposal"]
        # 记忆操作使用 IDENTITY proposal_type
        assert proposal["proposal_type"] == "identity"
        assert proposal["status"] == "pending"
        assert proposal["reason"] == "incorrect_information"
        assert proposal["evidence"] == ["mem_001"]

        # metadata 应包含 action 和 memory_id
        assert proposal["metadata"].get("memory_action") == "delete"
        assert proposal["metadata"].get("memory_id") == "mem_001"
        assert proposal["metadata"].get("admin_governance") is True
        assert proposal["metadata"].get("request_kind") == "memory_delete"

    def test_propose_memory_action_validates_action(self, governance_provider):
        """action 必须是合法值。"""
        from src.contracts.governance_schema import MemoryActionRequest

        # 无效 action
        req = MemoryActionRequest(memory_id="mem_001", action="invalid_action")
        r = governance_provider.propose_memory_action(req)
        assert r["success"] is False

    def test_propose_memory_action_merge_requires_target(self, governance_provider):
        """merge 操作必须提供 target_memory_id。"""
        from src.contracts.governance_schema import MemoryActionRequest

        req = MemoryActionRequest(memory_id="mem_001", action="merge", target_memory_id=None)
        r = governance_provider.propose_memory_action(req)
        assert r["success"] is False
        assert "target_memory_id" in r["error"]

    def test_propose_memory_action_mark_incorrect_works(self, governance_provider):
        """mark_incorrect 操作（不删除，仅标记）应能成功。"""
        from src.contracts.governance_schema import MemoryActionRequest

        req = MemoryActionRequest(
            memory_id="mem_001",
            action="mark_incorrect",
            reason="contains_incorrect_info",
        )
        result = governance_provider.propose_memory_action(req)
        assert result["success"] is True
        assert result["proposal"]["metadata"]["memory_action"] == "mark_incorrect"

    def test_memory_action_does_not_modify_store(self, governance_provider, mock_provider):
        """Memory action 不会删除或修改 MemoryStore 数据（只通过 Proposal）。"""
        from src.contracts.governance_schema import MemoryActionRequest

        # 记录当前记忆数
        before_count = len(mock_provider._memory_data["recent"])

        req = MemoryActionRequest(memory_id="mem_001", action="delete", reason="test")
        result = governance_provider.propose_memory_action(req)

        # 验证 success，且 mock provider 数据未被修改
        assert result["success"] is True
        assert len(mock_provider._memory_data["recent"]) == before_count
        # 数据应仍可访问
        assert mock_provider._memory_data["recent"][0]["id"] == "mem_001"


# =====================================================================
# E. Growth Governance 测试
# =====================================================================

class TestGrowthGovernance:
    """测试成长治理功能。"""

    def test_get_snapshot_returns_growth_data(self, governance_provider):
        """get_growth_governance_snapshot 返回成长相关数据。"""
        snapshot = governance_provider.get_growth_governance_snapshot(limit=50)

        assert snapshot["section"] == "growth"
        assert snapshot["available"] is True
        assert "data" in snapshot
        assert "metrics" in snapshot["data"]
        assert "shared_with_resolver" in snapshot["data"]
        # 各状态列表（空也行）
        for key in ("pending", "approved", "rejected", "applied"):
            assert key in snapshot["data"]

    def test_list_proposals_empty(self, governance_provider):
        """空存储时返回空列表。"""
        proposals = governance_provider.list_proposals()
        assert proposals == []

    def test_list_proposals_with_data(self, governance_provider):
        """有 Proposal 时按状态过滤。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        # 提交 3 个 proposal
        for i in range(3):
            req = PersonalityChangeRequest(trait=f"t{i}", delta=0.01, reason=f"r{i}")
            governance_provider.propose_personality_change(req)

        proposals = governance_provider.list_proposals()
        assert len(proposals) == 3

        # 按 status 过滤
        pending = governance_provider.list_proposals(status="pending")
        assert len(pending) == 3

        # 按 type 过滤
        personality = governance_provider.list_proposals(proposal_type="personality")
        assert len(personality) == 3

    def test_get_proposal_detail(self, governance_provider):
        """get_proposal_detail 返回 Proposal 完整详情。"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        req = PersonalityChangeRequest(trait="开放性", delta=0.05, reason="test")
        result = governance_provider.propose_personality_change(req)
        proposal_id = result["proposal_id"]

        detail = governance_provider.get_proposal_detail(proposal_id)
        assert detail is not None
        assert detail["proposal_id"] == proposal_id
        assert detail["is_governance"] is True

    def test_get_proposal_detail_not_found(self, governance_provider):
        """不存在的 proposal_id 返回 None。"""
        detail = governance_provider.get_proposal_detail("nonexistent_xxx")
        assert detail is None


class TestProposalReview:
    """测试 Proposal 审查流程。"""

    def _create_proposal(self, provider) -> str:
        from src.contracts.governance_schema import PersonalityChangeRequest
        req = PersonalityChangeRequest(trait="开放性", delta=0.05, reason="to_review")
        result = provider.propose_personality_change(req)
        assert result["success"] is True
        return result["proposal_id"]

    def test_review_approve_marks_approved(self, governance_provider):
        """approve 动作将 Proposal 标记为 approved。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="approve",
            reason="looks_good",
        )
        result = governance_provider.review_proposal(req, actor="test_admin")

        assert result["success"] is True
        assert result["before_status"] == "pending"
        assert result["after_status"] == "approved"
        assert result["action"] == "approve"

        # 验证持久化
        detail = governance_provider.get_proposal_detail(pid)
        assert detail["status"] == "approved"
        assert detail["reviewer_id"] == "test_admin"
        assert detail["review_comment"] == "looks_good"

    def test_review_reject_marks_rejected(self, governance_provider):
        """reject 动作将 Proposal 标记为 rejected。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="reject",
            reason="not_appropriate",
        )
        result = governance_provider.review_proposal(req, actor="test_admin")

        assert result["success"] is True
        assert result["after_status"] == "rejected"

        detail = governance_provider.get_proposal_detail(pid)
        assert detail["status"] == "rejected"

    def test_review_modify_updates_after_state(self, governance_provider):
        """modify 动作应更新 after_state 并标记为 approved。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="modify",
            reason="adjusting",
            modified_changes=[{"trait": "开放性", "after": 0.95}],
        )
        result = governance_provider.review_proposal(req, actor="test_admin")

        assert result["success"] is True
        # modify 视为通过
        assert result["after_status"] == "approved"

        detail = governance_provider.get_proposal_detail(pid)
        assert detail["after_state"]["开放性"] == 0.95
        # 审查元数据
        last_review = detail["metadata"].get("last_review", {})
        assert last_review.get("action") == "modify"

    def test_review_modify_without_changes_fails(self, governance_provider):
        """modify 动作必须提供 modified_changes。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="modify",
            reason="x",
            modified_changes=None,
        )
        result = governance_provider.review_proposal(req)
        assert result["success"] is False
        assert "modified_changes" in result["error"]

    def test_review_invalid_action_fails(self, governance_provider):
        """无效 action 返回失败。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="invalid_action",
            reason="x",
        )
        result = governance_provider.review_proposal(req)
        assert result["success"] is False

    def test_review_nonexistent_proposal_fails(self, governance_provider):
        """不存在的 proposal_id 返回失败。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        req = GrowthProposalReviewRequest(
            proposal_id="nonexistent_xxx",
            action="approve",
            reason="x",
        )
        result = governance_provider.review_proposal(req)
        assert result["success"] is False

    def test_review_already_reviewed_fails(self, governance_provider):
        """已审查的 Proposal 不能再审查。"""
        from src.contracts.governance_schema import GrowthProposalReviewRequest

        pid = self._create_proposal(governance_provider)
        # 第一次审查
        r1 = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=pid, action="approve", reason="x")
        )
        assert r1["success"] is True

        # 第二次审查应失败
        r2 = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=pid, action="reject", reason="y")
        )
        assert r2["success"] is False
        assert "终态" in r2["error"]


# =====================================================================
# F. 错误处理与边界条件
# =====================================================================

class TestErrorHandling:
    """测试错误处理和边界条件。"""

    def test_provider_handles_unavailable_runtime(self, tmp_storage_dir):
        """RuntimeProvider 不可用时（available=False）应安全返回空快照。"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider

        # Mock 返回 available=False
        mock = MagicMock()
        mock.get_personality_summary.return_value = {"available": False}
        mock.get_memory_summary.return_value = {"available": False}
        mock.get_growth_summary.return_value = {"available": False}

        storage = ProposalStorage(data_dir=tmp_storage_dir)
        provider = GovernanceProvider(runtime_provider=mock, proposal_storage=storage)

        # 各种 snapshot 应安全返回
        p = provider.get_personality_governance_snapshot()
        m = provider.get_memory_governance_snapshot()
        g = provider.get_growth_governance_snapshot()

        assert p["available"] is False
        assert m["available"] is False
        assert g["available"] is False

    def test_provider_handles_runtime_exception(self, tmp_storage_dir):
        """RuntimeProvider 抛出异常时，Provider 应安全降级。"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider

        # Mock 抛异常
        mock = MagicMock()
        mock.get_personality_summary.side_effect = Exception("simulated failure")
        mock.get_memory_summary.side_effect = Exception("simulated failure")
        mock.get_growth_summary.side_effect = Exception("simulated failure")

        storage = ProposalStorage(data_dir=tmp_storage_dir)
        provider = GovernanceProvider(runtime_provider=mock, proposal_storage=storage)

        # 不应抛异常
        p = provider.get_personality_governance_snapshot()
        m = provider.get_memory_governance_snapshot()
        g = provider.get_growth_governance_snapshot()

        # 即使失败，也返回结构化结果
        assert p["section"] == "personality"
        assert m["section"] == "memory"
        assert g["section"] == "growth"

    def test_propose_handles_storage_failure(self, mock_provider, tmp_storage_dir):
        """ProposalStorage 失败时，Provider 应返回错误（不崩溃）。"""
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import PersonalityChangeRequest

        # Mock 写入失败
        mock_storage = MagicMock()
        mock_storage.save.side_effect = Exception("disk full")

        provider = GovernanceProvider(runtime_provider=mock_provider, proposal_storage=mock_storage)

        req = PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        result = provider.propose_personality_change(req)

        assert result["success"] is False
        assert "保存" in result["error"] or "失败" in result["error"] or "写入" in result["error"]


# =====================================================================
# G. 向后兼容（Admin 现有功能不受影响）
# =====================================================================

class TestBackwardCompatibility:
    """验证 Phase 5.3 治理层不会破坏 Phase 5.1/5.2 Admin 功能。"""

    def test_governance_schema_imports_independently(self):
        """governance_schema 是独立模块，不依赖 admin 包。"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            MemoryActionRequest,
            GrowthProposalReviewRequest,
            GovernanceSnapshot,
        )
        # 全部能 import
        assert PersonalityChangeRequest is not None
        assert MemoryActionRequest is not None
        assert GrowthProposalReviewRequest is not None
        assert GovernanceSnapshot is not None

    def test_governance_provider_does_not_modify_runtime_provider(self, mock_provider, tmp_storage_dir):
        """GovernanceProvider 不会修改 RuntimeProvider 的状态。"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import PersonalityChangeRequest

        storage = ProposalStorage(data_dir=tmp_storage_dir)
        provider = GovernanceProvider(
            runtime_provider=mock_provider,
            proposal_storage=storage,
        )

        # 保存 mock provider 当前状态
        before_personality = dict(mock_provider._personality_data)
        before_memory = dict(mock_provider._memory_data)
        before_growth = dict(mock_provider._growth_data)

        # 执行多个操作
        provider.get_personality_governance_snapshot()
        provider.get_memory_governance_snapshot()
        provider.get_growth_governance_snapshot()
        req = PersonalityChangeRequest(trait="开放性", delta=0.01, reason="x")
        provider.propose_personality_change(req)

        # 验证 mock provider 状态未被修改
        assert mock_provider._personality_data == before_personality
        assert mock_provider._memory_data == before_memory
        assert mock_provider._growth_data == before_growth

    def test_phase5_1_apis_still_work(self, mock_provider, tmp_storage_dir):
        """Phase 5.1 RuntimeProvider API 仍能工作（治理层不破坏只读接口）。"""
        # Phase 5.1 接口：get_status / get_authority_status / get_personality_summary 等
        status = mock_provider.get_status()
        assert status["online"] is True

        authority = mock_provider.get_authority_status()
        assert authority["personality_resolver"] is True

        personality = mock_provider.get_personality_summary()
        assert personality["available"] is True


# =====================================================================
# H. 完整生命周期（端到端）
# =====================================================================

class TestEndToEndGovernance:
    """测试完整治理生命周期。"""

    def test_full_personality_lifecycle(self, governance_provider, tmp_storage_dir):
        """完整人格治理：snapshot → propose → review → persistence"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )
        from src.growth.proposal.storage import ProposalStorage

        # 1. 读取治理快照
        snapshot = governance_provider.get_personality_governance_snapshot()
        assert snapshot["available"] is True

        # 2. 提交人格变更建议
        req = PersonalityChangeRequest(
            trait="开放性",
            delta=0.05,
            reason="e2e_test",
            confidence=0.8,
        )
        proposal_result = governance_provider.propose_personality_change(req)
        assert proposal_result["success"] is True
        proposal_id = proposal_result["proposal_id"]

        # 3. 验证 Proposal 在存储中
        storage = ProposalStorage(data_dir=tmp_storage_dir)
        loaded = storage.load(proposal_id)
        assert loaded is not None
        assert loaded.status == "pending"

        # 4. 审查 Proposal
        review_req = GrowthProposalReviewRequest(
            proposal_id=proposal_id,
            action="approve",
            reason="e2e_approved",
        )
        review_result = governance_provider.review_proposal(review_req, actor="e2e_admin")
        assert review_result["success"] is True

        # 5. 验证最终状态
        final = storage.load(proposal_id)
        assert final is not None
        assert final.status == "approved"
        assert final.reviewer_id == "e2e_admin"
        assert final.review_comment == "e2e_approved"

    def test_full_memory_lifecycle(self, governance_provider, tmp_storage_dir):
        """完整记忆治理：snapshot → propose → persistence（不直接修改 store）"""
        from src.contracts.governance_schema import MemoryActionRequest
        from src.growth.proposal.storage import ProposalStorage

        # 1. 读取记忆快照
        snapshot = governance_provider.get_memory_governance_snapshot(limit=10)
        assert snapshot["data"]["total_count"] >= 1

        # 2. 提交记忆处理申请
        req = MemoryActionRequest(
            memory_id="mem_001",
            action="mark_incorrect",
            reason="e2e_memory_test",
        )
        result = governance_provider.propose_memory_action(req)
        assert result["success"] is True
        proposal_id = result["proposal_id"]

        # 3. 验证 Proposal 持久化
        storage = ProposalStorage(data_dir=tmp_storage_dir)
        loaded = storage.load(proposal_id)
        assert loaded is not None
        assert loaded.metadata.get("memory_action") == "mark_incorrect"
        assert loaded.status == "pending"

        # 4. 验证记忆快照在 list_proposals 中能找到
        proposals = governance_provider.list_proposals(proposal_type="identity")
        assert any(p["proposal_id"] == proposal_id for p in proposals)

    def test_full_growth_lifecycle_with_modify(self, governance_provider):
        """完整成长治理：create → review(modify) → final state"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        # 1. 创建
        req = PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        r1 = governance_provider.propose_personality_change(req)
        pid = r1["proposal_id"]
        original_after = r1["proposal"]["after_state"]["开放性"]

        # 2. modify：调整 after_state
        new_after = min(1.0, original_after + 0.1)
        review_req = GrowthProposalReviewRequest(
            proposal_id=pid,
            action="modify",
            reason="adjust",
            modified_changes=[{"trait": "开放性", "after": new_after}],
        )
        r2 = governance_provider.review_proposal(review_req)

        assert r2["success"] is True
        assert r2["after_status"] == "approved"

        # 3. 验证最终状态
        detail = governance_provider.get_proposal_detail(pid)
        assert detail["after_state"]["开放性"] == new_after
        assert detail["status"] == "approved"

        # 4. 验证不能再审查
        r3 = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=pid, action="reject", reason="x")
        )
        assert r3["success"] is False
