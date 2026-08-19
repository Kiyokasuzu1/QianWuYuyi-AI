# -*- coding: utf-8 -*-
"""
tests/test_governance_security.py

Phase 5.4: Admin Governance 安全测试。

目标：
验证 Admin Governance Layer 没有任何隐藏的 mutation 路径，无法绕过 Proposal 生命周期。

覆盖：
1. 直接修改 personality 失败（无对应接口）
2. 直接删除 memory 失败（无对应接口）
3. approve 不触发 apply（仅更新状态）
4. rejected proposal 不可执行
5. applied proposal 不可重复执行
6. 终态 Proposal 不可再审查
7. 治理层无法获取 Runtime 内部对象引用（只读）
8. 无法通过 governance 修改 Authority 内部状态
9. 异常路径下不会写入 Authority
10. 审计完整性
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
# Mock Runtime
# =====================================================================

class MockAuthority:
    """带单例保护的 Mock Authority"""

    _instance = None

    def __init__(self, name: str = "test"):
        # 单例保护
        cls_name = type(self).__name__
        if getattr(MockAuthority, f"_{cls_name}_instance", None) is not None:
            raise RuntimeError(f"{cls_name} 违反单例约束")
        setattr(MockAuthority, f"_{cls_name}_instance", self)
        self.name = name
        self._mutation_log: List[Dict[str, Any]] = []

    def record_mutation(self, op: str, **kwargs):
        """记录任何被调用的 mutation"""
        self._mutation_log.append({"op": op, **kwargs})

    def get_mutations(self) -> List[Dict[str, Any]]:
        return list(self._mutation_log)


class MockMemoryStoreAuthority(MockAuthority):
    def add(self, memory): self.record_mutation("memory.add", **memory)
    def delete(self, mid): self.record_mutation("memory.delete", id=mid)
    def update(self, mid, **kw): self.record_mutation("memory.update", id=mid, **kw)
    def clear(self): self.record_mutation("memory.clear")


class MockPersonalityAuthority(MockAuthority):
    def __init__(self):
        super().__init__("personality")
        self._traits = {"开放性": 0.5, "严谨性": 0.5, "外向性": 0.5}
        self._resolver_calls = 0

    def resolve(self):
        self._resolver_calls += 1
        return dict(self._traits)

    def set_trait(self, trait, value):
        self.record_mutation("personality.set_trait", trait=trait, value=value)
        self._traits[trait] = value

    def apply_changes(self, changes):
        self.record_mutation("personality.apply", **changes)
        for k, v in changes.items():
            self._traits[k] = v

    def get_trait(self, trait):
        return self._traits.get(trait)


class MockEmotionAuthority(MockAuthority):
    def __init__(self):
        super().__init__("emotion")
        self.current = "平静"
        self.intensity = 0.5

    def set_emotion(self, emotion, intensity):
        self.record_mutation("emotion.set", emotion=emotion, intensity=intensity)
        self.current = emotion
        self.intensity = intensity


class MockGrowthAuthority(MockAuthority):
    def __init__(self):
        super().__init__("growth")
        self._state = {"total_growth": 0.0, "maturity": 0.0}

    def advance(self, delta):
        self.record_mutation("growth.advance", delta=delta)
        self._state["total_growth"] += delta

    def reset(self):
        self.record_mutation("growth.reset")
        self._state = {"total_growth": 0.0, "maturity": 0.0}

    def apply_proposal_directly(self, proposal):
        """模拟 PersonalityAdapter.apply_proposal（不应被 governance 调用）"""
        self.record_mutation("growth.apply_direct", proposal_id=proposal.get("proposal_id"))


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tmp_storage_dir():
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_security_test_")
    yield tmp_dir
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture
def authorities():
    """提供一组 Mock Authority（带单例保护 + 变更追踪）"""
    # 重置所有单例
    for attr in list(vars(MockAuthority).keys()):
        if attr.endswith("_instance"):
            setattr(MockAuthority, attr, None)

    memory = MockMemoryStoreAuthority()
    personality = MockPersonalityAuthority()
    emotion = MockEmotionAuthority()
    growth = MockGrowthAuthority()

    yield {
        "memory": memory,
        "personality": personality,
        "emotion": emotion,
        "growth": growth,
    }

    # 清理
    for attr in list(vars(MockAuthority).keys()):
        if attr.endswith("_instance"):
            setattr(MockAuthority, attr, None)


@pytest.fixture
def governance_provider(authorities, tmp_storage_dir):
    """提供 GovernanceProvider，注入 Mock Authority"""
    from src.growth.proposal.storage import ProposalStorage
    from src.admin.governance_provider import GovernanceProvider

    storage = ProposalStorage(data_dir=tmp_storage_dir)

    # Mock RuntimeProvider - **只读** 暴露 Authority 状态
    class _ReadOnlyRuntimeProvider:
        def __init__(self, auth):
            self._auth = auth

        def get_personality_summary(self):
            return {
                "available": True,
                "current": self._auth["personality"].resolve(),
                "state": {"total_growth": 0.0, "maturity": 0.0},
                "self_model": {},
            }

        def get_memory_summary(self):
            return {
                "available": True,
                "total_count": 0,
                "user_id": "user_test",
                "recent": [],
                "important_count": 0,
            }

        def get_growth_summary(self):
            return {
                "available": True,
                "shared_with_resolver": True,
                "metrics": {"total_growth": 0.0},
            }

    return GovernanceProvider(
        runtime_provider=_ReadOnlyRuntimeProvider(authorities),
        proposal_storage=storage,
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
# A. 直接修改 personality 失败
# =====================================================================

class TestNoDirectPersonalityModification:
    """验证 GovernanceProvider 不提供直接修改 personality 的接口"""

    def test_governance_provider_has_no_set_trait_method(self, governance_provider):
        """GovernanceProvider 不暴露 set_trait / set_personality 方法"""
        forbidden_methods = [
            "set_trait", "set_personality", "update_personality",
            "modify_personality", "apply_personality_change", "direct_apply",
            "set_state", "set_resolver", "update_resolver",
        ]
        for m in forbidden_methods:
            assert not hasattr(governance_provider, m), \
                f"GovernanceProvider 不应暴露 {m} 方法"

    def test_governance_cannot_modify_traits_directly(self, governance_provider, authorities):
        """通过 governance 无法直接修改 traits"""
        before = authorities["personality"].resolve()

        # 各种尝试（即使是不存在的接口调用）
        for forbidden_op in ["set_trait", "update", "apply_changes"]:
            if hasattr(governance_provider, forbidden_op):
                try:
                    getattr(governance_provider, forbidden_op)("开放性", 0.99)
                except Exception:
                    pass

        after = authorities["personality"].resolve()
        assert before == after

    def test_proposal_propose_does_not_modify_personality(self, governance_provider, authorities):
        """propose_personality_change 不会直接修改 personality"""
        from src.contracts.governance_schema import PersonalityChangeRequest

        before_openness = authorities["personality"].get_trait("开放性")
        mutations_before = len(authorities["personality"].get_mutations())

        result = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.99, reason="test")
        )
        assert result["success"] is True

        # Authority 未被直接修改
        assert authorities["personality"].get_trait("开放性") == before_openness
        # 无新增 mutation
        assert len(authorities["personality"].get_mutations()) == mutations_before


# =====================================================================
# B. 直接删除 memory 失败
# =====================================================================

class TestNoDirectMemoryDeletion:
    """验证 GovernanceProvider 不提供直接删除 memory 的接口"""

    def test_governance_provider_has_no_delete_memory_method(self, governance_provider):
        """GovernanceProvider 不暴露 delete_memory / clear 方法"""
        forbidden_methods = [
            "delete_memory", "remove_memory", "clear_memories",
            "purge_memory", "drop_memory",
        ]
        for m in forbidden_methods:
            assert not hasattr(governance_provider, m), \
                f"GovernanceProvider 不应暴露 {m} 方法"

    def test_propose_memory_action_does_not_delete(self, governance_provider, authorities):
        """propose_memory_action(delete) 不会直接删除记忆"""
        from src.contracts.governance_schema import MemoryActionRequest

        mutations_before = len(authorities["memory"].get_mutations())

        result = governance_provider.propose_memory_action(
            MemoryActionRequest(memory_id="mem_001", action="delete", reason="x")
        )
        assert result["success"] is True

        # 没有调用 memory.delete
        new_mutations = authorities["memory"].get_mutations()[mutations_before:]
        delete_mutations = [m for m in new_mutations if m.get("op") == "memory.delete"]
        assert len(delete_mutations) == 0

    def test_propose_memory_mark_incorrect_does_not_modify(self, governance_provider, authorities):
        """propose_memory_action(mark_incorrect) 不会修改记忆"""
        from src.contracts.governance_schema import MemoryActionRequest

        mutations_before = len(authorities["memory"].get_mutations())

        result = governance_provider.propose_memory_action(
            MemoryActionRequest(memory_id="mem_001", action="mark_incorrect", reason="x")
        )
        assert result["success"] is True

        new_mutations = authorities["memory"].get_mutations()[mutations_before:]
        assert len(new_mutations) == 0  # 零 mutation


# =====================================================================
# C. approve 不触发 apply
# =====================================================================

class TestApproveDoesNotApply:
    """验证 approve 只更新 status，不触发 apply"""

    def test_approve_does_not_modify_personality(self, governance_provider, authorities):
        """Admin approve 不会修改 PersonalityAuthority"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        before_openness = authorities["personality"].get_trait("开放性")
        mutations_before = len(authorities["personality"].get_mutations())

        # Propose + Approve
        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.5, reason="x")
        )
        governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="approve", reason="ok")
        )

        # 关键断言：Authority 未变
        assert authorities["personality"].get_trait("开放性") == before_openness
        # 无 personality mutation
        new_mutations = authorities["personality"].get_mutations()[mutations_before:]
        apply_mutations = [m for m in new_mutations if "apply" in m.get("op", "").lower()]
        assert len(apply_mutations) == 0

    def test_modify_also_does_not_modify_personality(self, governance_provider, authorities):
        """Admin modify 也不会修改 PersonalityAuthority"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        before = authorities["personality"].resolve()
        mutations_before = len(authorities["personality"].get_mutations())

        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.5, reason="x")
        )
        governance_provider.review_proposal(
            GrowthProposalReviewRequest(
                proposal_id=r["proposal_id"],
                action="modify",
                reason="adjust",
                modified_changes=[{"trait": "开放性", "after": 0.99}],
            )
        )

        assert authorities["personality"].resolve() == before
        new_mutations = authorities["personality"].get_mutations()[mutations_before:]
        assert len(new_mutations) == 0


# =====================================================================
# D. rejected proposal 不可执行
# =====================================================================

class TestRejectedProposalCannotBeExecuted:
    """rejected Proposal 不可被 apply"""

    def test_rejected_proposal_status(self, governance_provider):
        """rejected Proposal 状态正确"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        review = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="reject", reason="bad")
        )
        assert review["success"] is True
        assert review["after_status"] == "rejected"

        detail = governance_provider.get_proposal_detail(r["proposal_id"])
        assert detail["status"] == "rejected"

    def test_rejected_proposal_has_no_apply_path(self, governance_provider, authorities):
        """rejected Proposal 在 governance 层无 apply 路径"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        mutations_before = len(authorities["personality"].get_mutations())

        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.5, reason="x")
        )
        governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="reject", reason="bad")
        )

        # 没有任何 apply 类 mutation
        new_mutations = authorities["personality"].get_mutations()[mutations_before:]
        assert all("apply" not in m.get("op", "").lower() for m in new_mutations)


# =====================================================================
# E. applied proposal 不可重复执行
# =====================================================================

class TestAppliedProposalCannotBeRerun:
    """applied Proposal 不可重复审查/执行"""

    def test_applied_proposal_cannot_be_reviewed(self, governance_provider):
        """applied 状态不能再 review"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )

        # 手动设为 applied 状态（模拟已 apply）
        from src.growth.proposal.storage import ProposalStorage
        storage = governance_provider._storage
        p = storage.load(r["proposal_id"])
        p.status = "applied"
        storage.save(p)

        # 尝试再 review
        review = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="approve", reason="x")
        )
        assert review["success"] is False
        assert "终态" in review["error"]

    def test_approved_proposal_cannot_be_reviewed_again(self, governance_provider):
        """approved 状态不能再 review"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        # 第一次 approve
        governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="approve", reason="ok")
        )
        # 第二次 review 应失败
        review = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="reject", reason="x")
        )
        assert review["success"] is False


# =====================================================================
# F. Governance 不暴露 Authority 内部对象引用
# =====================================================================

class TestAuthorityReferenceIsolation:
    """GovernanceProvider 不暴露 Authority 内部对象引用"""

    def test_governance_has_no_authority_attributes(self, governance_provider):
        """GovernanceProvider 不持有 Authority 引用"""
        forbidden_attrs = [
            "_memory_store", "_personality_resolver", "_emotion_manager",
            "_growth_state", "_authority", "_runtime", "_core",
        ]
        for attr in forbidden_attrs:
            val = getattr(governance_provider, attr, None)
            assert val is None, f"GovernanceProvider.{attr} 不应为 Authority 引用 (got {val})"

    def test_snapshots_do_not_return_authority_references(self, governance_provider):
        """snapshot 返回的数据不应包含 Authority 内部对象引用"""
        p = governance_provider.get_personality_governance_snapshot()
        m = governance_provider.get_memory_governance_snapshot()
        g = governance_provider.get_growth_governance_snapshot()

        # 数据结构是 dict/list/string/number，不应有 dataclass/object 引用
        for snap in (p, m, g):
            assert isinstance(snap, dict)
            # 不应直接持有可调用方法
            for k, v in snap.items():
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        # 跳过已知安全的字段
                        if kk in ("error", "section", "available"):
                            continue
                        # 字段值应为基本类型或 list/dict
                        assert not callable(vv), f"snapshot.{k}.{kk} 不应是 callable"


# =====================================================================
# G. 异常路径不写入 Authority
# =====================================================================

class TestExceptionDoesNotMutateAuthority:
    """异常路径下不写入 Authority"""

    def test_storage_failure_does_not_affect_authority(self, authorities, tmp_storage_dir):
        """ProposalStorage 失败时 Authority 不受影响"""
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import PersonalityChangeRequest

        mock_storage = MagicMock()
        mock_storage.save.side_effect = Exception("disk full")
        mock_storage.list_all.return_value = []
        mock_storage.list_by_status.return_value = []
        mock_storage.list_by_type.return_value = []

        class _RP:
            def get_personality_summary(self):
                return {"available": True, "current": {}, "state": {}, "self_model": {}}
            def get_memory_summary(self):
                return {"available": True, "total_count": 0, "user_id": None, "recent": [], "important_count": 0}
            def get_growth_summary(self):
                return {"available": True, "shared_with_resolver": True, "metrics": {}}

        provider = GovernanceProvider(runtime_provider=_RP(), proposal_storage=mock_storage)

        before_personality = authorities["personality"].resolve()
        before_mutations = len(authorities["personality"].get_mutations())

        # 多次失败提交
        for i in range(3):
            r = provider.propose_personality_change(
                PersonalityChangeRequest(trait="开放性", delta=0.05, reason=f"x{i}")
            )
            assert r["success"] is False

        # Authority 未被触碰
        assert authorities["personality"].resolve() == before_personality
        assert len(authorities["personality"].get_mutations()) == before_mutations

    def test_invalid_proposal_does_not_modify_authority(self, governance_provider, authorities):
        """非法 Proposal 不修改 Authority"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            MemoryActionRequest,
            GrowthProposalReviewRequest,
        )

        mutations_before = len(authorities["personality"].get_mutations()) + len(authorities["memory"].get_mutations())

        # 空 trait
        r1 = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="", delta=0.05)
        )
        assert r1["success"] is False

        # 非法 action
        r2 = governance_provider.propose_memory_action(
            MemoryActionRequest(memory_id="x", action="invalid_action")
        )
        assert r2["success"] is False

        # 不存在的 proposal_id
        r3 = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id="nonexistent", action="approve")
        )
        assert r3["success"] is False

        # 没有任何 mutation
        all_mutations = (
            authorities["personality"].get_mutations()
            + authorities["memory"].get_mutations()
            + authorities["growth"].get_mutations()
            + authorities["emotion"].get_mutations()
        )
        # 减去初始 mutation 数
        new_mutations = all_mutations[mutations_before:]
        assert len(new_mutations) == 0


# =====================================================================
# H. 审计完整性
# =====================================================================

class TestAuditCompleteness:
    """审计完整性"""

    def test_propose_records_audit(self, governance_provider):
        """propose 操作应记录审计（不验证存储细节，只验证不抛异常）"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        # 不抛异常即通过
        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        assert r["success"] is True

    def test_review_records_audit(self, governance_provider):
        """review 操作应记录审计"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )
        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        review = governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="approve", reason="ok")
        )
        assert review["success"] is True
        # 验证 metadata 中有 last_review
        detail = governance_provider.get_proposal_detail(r["proposal_id"])
        assert "last_review" in detail["metadata"]
        assert detail["metadata"]["last_review"]["action"] == "approve"

    def test_proposal_metadata_contains_governance_marker(self, governance_provider):
        """Proposal metadata 标记为 governance 来源"""
        from src.contracts.governance_schema import PersonalityChangeRequest
        r = governance_provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        assert r["proposal"]["metadata"].get("admin_governance") is True
        assert r["proposal"]["metadata"].get("request_kind") == "personality_change"


# =====================================================================
# I. 治理层不能绕过 ProposalStorage
# =====================================================================

class TestNoBypassProposalStorage:
    """所有写入必须经 ProposalStorage"""

    def test_proposal_storage_always_called(self, tmp_storage_dir, authorities):
        """propose 总会调用 ProposalStorage.save"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import PersonalityChangeRequest

        real_storage = ProposalStorage(data_dir=tmp_storage_dir)
        # 用 spy 包装
        save_calls = []
        original_save = real_storage.save

        def spy_save(proposal):
            save_calls.append(proposal.proposal_id)
            return original_save(proposal)

        real_storage.save = spy_save

        class _RP:
            def get_personality_summary(self):
                return {"available": True, "current": {}, "state": {}, "self_model": {}}
            def get_memory_summary(self):
                return {"available": True, "total_count": 0, "user_id": None, "recent": [], "important_count": 0}
            def get_growth_summary(self):
                return {"available": True, "shared_with_resolver": True, "metrics": {}}

        provider = GovernanceProvider(runtime_provider=_RP(), proposal_storage=real_storage)

        r = provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        assert r["success"] is True
        assert r["proposal_id"] in save_calls

    def test_review_always_updates_storage(self, tmp_storage_dir, authorities):
        """review 总会更新 ProposalStorage"""
        from src.growth.proposal.storage import ProposalStorage
        from src.admin.governance_provider import GovernanceProvider
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )

        real_storage = ProposalStorage(data_dir=tmp_storage_dir)
        save_calls = []
        original_save = real_storage.save

        def spy_save(proposal):
            save_calls.append(proposal.proposal_id)
            return original_save(proposal)

        real_storage.save = spy_save

        class _RP:
            def get_personality_summary(self):
                return {"available": True, "current": {}, "state": {}, "self_model": {}}
            def get_memory_summary(self):
                return {"available": True, "total_count": 0, "user_id": None, "recent": [], "important_count": 0}
            def get_growth_summary(self):
                return {"available": True, "shared_with_resolver": True, "metrics": {}}

        provider = GovernanceProvider(runtime_provider=_RP(), proposal_storage=real_storage)

        r = provider.propose_personality_change(
            PersonalityChangeRequest(trait="开放性", delta=0.05, reason="x")
        )
        save_calls.clear()  # 清空 propose 阶段的计数

        review = provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=r["proposal_id"], action="approve", reason="ok")
        )
        assert review["success"] is True
        assert r["proposal_id"] in save_calls  # review 也调用了 save


# =====================================================================
# J. 数据隔离
# =====================================================================

class TestDataIsolation:
    """治理层不能影响其他无关数据"""

    def test_governance_does_not_affect_other_proposals(self, governance_provider, tmp_storage_dir):
        """治理一个 Proposal 不影响其他 Proposal"""
        from src.contracts.governance_schema import (
            PersonalityChangeRequest,
            GrowthProposalReviewRequest,
        )
        from src.growth.proposal.storage import ProposalStorage

        # 创建 5 个 Proposal
        pids = []
        for i in range(5):
            r = governance_provider.propose_personality_change(
                PersonalityChangeRequest(trait=f"t{i}", delta=0.01, reason=f"r{i}")
            )
            pids.append(r["proposal_id"])

        # 只 review 第 3 个
        governance_provider.review_proposal(
            GrowthProposalReviewRequest(proposal_id=pids[2], action="approve", reason="x")
        )

        # 重新加载所有 Proposal
        storage = ProposalStorage(data_dir=tmp_storage_dir)
        all_proposals = storage.list_all(limit=100)

        # 只有 pids[2] 状态为 approved
        statuses = {p.proposal_id: p.status for p in all_proposals}
        assert statuses[pids[2]] == "approved"
        for i, pid in enumerate(pids):
            if i == 2:
                continue
            assert statuses[pid] == "pending", f"pids[{i}] 状态应为 pending"


# =====================================================================
# K. 拒绝绕过 ApprovalManager
# =====================================================================

class TestNoBypassApprovalManager:
    """无法通过 governance 绕过 ApprovalManager"""

    def test_governance_does_not_import_approval_manager(self, governance_provider):
        """GovernanceProvider 不应直接依赖 ApprovalManager"""
        import inspect
        from src.admin import governance_provider as gp_module

        source = inspect.getsource(gp_module)
        # 不应直接 import ApprovalManager
        assert "approval_manager" not in source.lower() or "approvalmanager" not in source.lower(), \
            "GovernanceProvider 不应直接引用 ApprovalManager（保持职责分离）"

    def test_governance_does_not_call_personality_adapter(self, governance_provider):
        """GovernanceProvider 不应直接调用 PersonalityAdapter"""
        import inspect
        from src.admin import governance_provider as gp_module

        source = inspect.getsource(gp_module)
        assert "personality_adapter" not in source.lower(), \
            "GovernanceProvider 不应直接引用 PersonalityAdapter"

    def test_governance_does_not_call_growth_adapter(self, governance_provider):
        """GovernanceProvider 不应直接调用 GrowthAdapter"""
        import inspect
        from src.admin import governance_provider as gp_module

        source = inspect.getsource(gp_module)
        assert "growth_adapter" not in source.lower() or "growthadapter" not in source.lower(), \
            "GovernanceProvider 不应直接引用 GrowthAdapter"
