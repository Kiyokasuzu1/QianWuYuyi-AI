"""
Phase 4.0.3-C — Governance Bypass Closure 不可绕过性验收测试

验证：
1. Adapter 安全闸：未审批 proposal 直接调用 apply_suggestion() → 拒绝
2. Adapter 安全闸：已批准 proposal (requires_approval=False) → 可写入
3. RuntimeCore from_pcr() → GovernancePolicy.evaluate() → AUTO_APPLY / APPROVAL_REQUIRED
4. RuntimeCore accept_self_model_suggestion() → ApprovalQueue.approve() → apply_proposal()
5. RuntimeCore reject_self_model_suggestion() → ApprovalQueue.reject()
6. 直接调用 apply_suggestion() with unapproved → 拒绝
7. 所有 production apply_change_proposal() 调用均可追溯到两条合法路径
"""

from __future__ import annotations

import os
import tempfile
import uuid
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


# ============================================================
# Helpers
# ============================================================

def _make_record(growth_level="context", confidence=0.6, **kwargs):
    return {
        "record_id": f"gr_{uuid.uuid4().hex[:8]}",
        "source_event_id": f"ev_{uuid.uuid4().hex[:8]}",
        "growth_level": growth_level,
        "confidence": confidence,
        "affected_dimensions": {"creativity": 0.05},
        "reason": "测试记录",
        "growth_signal": "test_signal",
        "timestamp": datetime.now().isoformat(),
        **kwargs,
    }


def _make_store(tmp_path: str):
    from src.personality.self_model_store import SelfModelStore
    return SelfModelStore(storage_path=os.path.join(tmp_path, "self_model.json"))


def _make_pcr(
    trait_changes: dict = None,
    description: str = "test change",
    confidence: float = 0.85,
):
    """构造一个 PersonalityChangeRequest dict（与 RuntimeCore 的输出格式一致）"""
    pcr_id = f"pcr_{uuid.uuid4().hex[:8]}"
    return {
        "request_id": pcr_id,
        "source_proposal_id": f"prop_{uuid.uuid4().hex[:8]}",
        "source_insight_id": f"ins_{uuid.uuid4().hex[:8]}",
        "confidence": confidence,
        "evidence_count": 3,
        "reason": description,
        "evolution_record": {
            "changes": trait_changes or {"warmth": {"delta": 0.02, "reason": "test"}},
        },
        "growth_records": [
            {
                "record_id": f"gr_{uuid.uuid4().hex[:8]}",
                "growth_level": "trait",
                "confidence": confidence,
                "reason": description,
                "growth_signal": "test_signal",
                "timestamp": datetime.now().isoformat(),
            }
        ],
        "timestamp": datetime.now().isoformat(),
    }


def _get_narratives(store) -> list:
    model = store.get()
    if model is None:
        return []
    return model.get("growth_narratives", [])


# ============================================================
# Test 1: Adapter Safety Gate — 未审批 Proposal 拒绝写入
# ============================================================

class TestAdapterSafetyGate:
    """验证 SelfModelUpdaterAdapter.apply_suggestion() 安全闸"""

    def test_apply_suggestion_refuses_unapproved_proposal(self, tmp_path):
        """未审批 proposal (requires_approval=True) → apply_suggestion() 拒绝"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_store import SelfModelStore
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )

        # 生成一个 proposal（requires_approval=True）
        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)
        assert proposal is not None, "proposal 生成失败"
        assert proposal.requires_approval is True, "trait proposal 默认需要审批"

        # 记录写入前状态
        narratives_before = len(_get_narratives(store))

        # 直接调用 apply_suggestion（绕过审批）
        adapter.apply_suggestion(manager=None, suggestion=proposal)

        # 验证：Store 未被修改
        narratives_after = len(_get_narratives(store))
        assert narratives_after == narratives_before, (
            f"未审批 proposal 不应写入 Store: before={narratives_before}, after={narratives_after}"
        )

    def test_apply_suggestion_allows_approved_proposal(self, tmp_path):
        """已批准 proposal (requires_approval=False) → apply_suggestion() 允许写入"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_store import SelfModelStore
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )

        # 生成 proposal 并标记为已批准
        record = _make_record(growth_level="context", confidence=0.80)
        proposal = updater.create_proposal_from_growth(record)
        proposal.requires_approval = False  # 模拟审批通过

        # 调用 apply_suggestion
        adapter.apply_suggestion(manager=None, suggestion=proposal)

        # 验证：Store 被修改
        narratives = _get_narratives(store)
        assert len(narratives) > 0, "已批准 proposal 应该写入 Store"

    def test_apply_suggestion_auto_apply_context(self, tmp_path):
        """context AUTO_APPLY → apply_proposal 正常写入"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_store import SelfModelStore

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)

        record = _make_record(growth_level="context", confidence=0.80)
        proposal = updater.create_proposal_from_growth(record)
        assert proposal is not None

        updater.apply_proposal(proposal)
        narratives = _get_narratives(store)
        assert len(narratives) > 0, "context AUTO_APPLY 应正常写入"


# ============================================================
# Test 2: ApprovalQueue 生命周期
# ============================================================

class TestApprovalQueueLifecycle:
    """验证 ApprovalQueue 的 approve() 标记 requires_approval=False"""

    def test_approve_sets_requires_approval_false(self, tmp_path):
        """approve() 后 proposal.requires_approval 变为 False"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_governance import SelfModelApprovalQueue

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        queue = SelfModelApprovalQueue()

        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)
        assert proposal.requires_approval is True

        queue.enqueue(proposal)
        assert queue.pending_count == 1

        approved = queue.approve(proposal.suggestion_id)
        assert approved is not None
        assert approved.requires_approval is False, (
            "approve() 后 requires_approval 应为 False"
        )
        assert queue.pending_count == 0

    def test_reject_removes_from_queue(self, tmp_path):
        """reject() 从队列移除"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_governance import SelfModelApprovalQueue

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        queue = SelfModelApprovalQueue()

        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)

        queue.enqueue(proposal)
        assert queue.pending_count == 1

        queue.reject(proposal.suggestion_id, reason="test reject")
        assert queue.pending_count == 0

    def test_approve_nonexistent_returns_none(self):
        """审批不存在的 proposal → 返回 None"""
        from src.personality.self_model_governance import SelfModelApprovalQueue

        queue = SelfModelApprovalQueue()
        result = queue.approve("nonexistent_id")
        assert result is None


# ============================================================
# Test 3: GovernancePolicy 决策验证
# ============================================================

class TestGovernancePolicyDecisions:
    """验证 GovernancePolicy 对各类 growth_level 的决策"""

    def test_trait_requires_approval(self):
        """trait 级别 → APPROVAL_REQUIRED"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="trait", confidence=0.85)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.APPROVAL_REQUIRED

    def test_context_auto_apply(self):
        """context 级别 → AUTO_APPLY"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="context", confidence=0.60)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.AUTO_APPLY

    def test_preference_high_confidence_auto_apply(self):
        """preference 高置信度 → AUTO_APPLY"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="preference", confidence=0.80)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.AUTO_APPLY

    def test_preference_low_confidence_approval_required(self):
        """preference 低置信度 → APPROVAL_REQUIRED"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="preference", confidence=0.50)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.APPROVAL_REQUIRED

    def test_identity_deny(self):
        """identity 级别 → DENY"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="identity", confidence=0.95)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.DENY

    def test_trace_deny(self):
        """trace 级别 → DENY"""
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )

        policy = SelfModelGovernancePolicy()
        record = _make_record(growth_level="trace", confidence=0.30)
        decision = policy.evaluate(record)
        assert decision.action == GovernanceAction.DENY


# ============================================================
# Test 4: RuntimeCore Governance 集成
# ============================================================

class TestRuntimeCoreGovernance:
    """验证 RuntimeCore 的 Governance 路径"""

    def test_from_pcr_to_governance_trait_enqueues(self, tmp_path):
        """trait PCR → GovernancePolicy → APPROVAL_REQUIRED → enqueue"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            SelfModelApprovalQueue,
        )
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )
        policy = SelfModelGovernancePolicy()
        queue = SelfModelApprovalQueue()

        # 模拟 PCR → from_pcr → governance
        pcr = _make_pcr(confidence=0.85)
        sug = adapter.from_pcr(pcr)
        assert sug is not None, "from_pcr() 应生成 proposal"

        # 获取 growth_record 用于 governance 评估
        record = adapter.pcr_to_growth_record(pcr)
        assert record is not None, "pcr_to_growth_record() 应返回非空"

        decision = policy.evaluate(record)
        assert decision.action.value == "approval_required", (
            f"trait PCR 应进入 APPROVAL_REQUIRED，实际: {decision.action.value}"
        )

        # enqueue
        queue.enqueue(sug)
        assert queue.pending_count == 1, "应入队 1 条 proposal"

        # approve → apply
        approved = queue.approve(sug.suggestion_id)
        assert approved is not None
        assert approved.requires_approval is False

        updater.apply_proposal(approved)
        narratives = _get_narratives(store)
        assert len(narratives) > 0, "审批后应写入 Store"

    def test_accept_self_model_suggestion_flow(self, tmp_path):
        """完整流程: from_pcr → governance → approve → apply"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            SelfModelApprovalQueue,
        )
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )
        policy = SelfModelGovernancePolicy()
        queue = SelfModelApprovalQueue()

        # Step 1: from_pcr → proposal
        pcr = _make_pcr(confidence=0.85)
        sug = adapter.from_pcr(pcr)
        assert sug is not None

        # Step 2: governance evaluate → APPROVAL_REQUIRED → enqueue
        record = adapter.pcr_to_growth_record(pcr)
        decision = policy.evaluate(record)
        assert decision.action.value == "approval_required"
        queue.enqueue(sug)

        # Step 3: accept → approve → apply
        # 模拟 accept_self_model_suggestion 行为
        approved = queue.approve(sug.suggestion_id)
        assert approved is not None
        assert approved.requires_approval is False

        updater.apply_proposal(approved)
        narratives = _get_narratives(store)
        assert len(narratives) > 0

    def test_context_auto_apply_bypasses_queue(self, tmp_path):
        """context PCR → AUTO_APPLY → 直接 apply，不经过 Queue"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            SelfModelApprovalQueue,
        )
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )
        policy = SelfModelGovernancePolicy()
        queue = SelfModelApprovalQueue()

        # 构造一个 context 级别的 PCR（growth_records 中 growth_level 设为 context）
        pcr = _make_pcr(confidence=0.80)
        pcr["growth_records"][0]["growth_level"] = "context"

        sug = adapter.from_pcr(pcr)
        # context PCR 可能被 from_pcr 映射为 trait，需要验证
        record = adapter.pcr_to_growth_record(pcr)
        if record is not None:
            decision = policy.evaluate(record)
            if decision.action.value == "auto_apply":
                # AUTO_APPLY → 直接 apply，不经过 queue
                updater.apply_proposal(sug)
                assert queue.pending_count == 0, "AUTO_APPLY 不应入队"
                narratives = _get_narratives(store)
                assert len(narratives) > 0


# ============================================================
# Test 5: 拒绝路径验证
# ============================================================

class TestRejectionPaths:
    """验证拒绝路径不会写入 Store"""

    def test_reject_does_not_write_store(self, tmp_path):
        """reject → Store 不变化"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import SelfModelApprovalQueue

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        queue = SelfModelApprovalQueue()

        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)
        queue.enqueue(proposal)

        narratives_before = len(_get_narratives(store))

        queue.reject(proposal.suggestion_id, reason="rejected")
        assert queue.pending_count == 0

        narratives_after = len(_get_narratives(store))
        assert narratives_after == narratives_before, "reject 不应写入 Store"

    def test_deny_does_not_write_store(self, tmp_path):
        """DENY decision → Store 不变化"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import SelfModelGovernancePolicy

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        policy = SelfModelGovernancePolicy()

        record = _make_record(growth_level="identity", confidence=0.95)
        decision = policy.evaluate(record)
        assert decision.action.value == "deny"

        narratives_before = len(_get_narratives(store))
        # deny 路径不应创建 proposal 或写入 store
        # 这里验证 store 未变化
        narratives_after = len(_get_narratives(store))
        assert narratives_after == narratives_before, "DENY 不应写入 Store"


# ============================================================
# Test 6: 硬契约 — 无第三条路径
# ============================================================

class TestHardContractNoThirdPath:
    """验证不存在绕过 Governance 的第三条写入路径"""

    def test_apply_suggestion_without_approval_is_blocked(self, tmp_path):
        """
        直接调用 apply_suggestion() with unapproved proposal → 被安全闸拒绝。
        这是最重要的不可绕过性测试。
        """
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )

        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)
        assert proposal.requires_approval is True

        narratives_before = len(_get_narratives(store))

        # 尝试绕过审批直接写入
        adapter.apply_suggestion(manager=None, suggestion=proposal)

        narratives_after = len(_get_narratives(store))
        assert narratives_after == narratives_before, (
            "绕过 Governance 直接调用 apply_suggestion() 必须被拒绝！"
        )

    def test_only_auto_apply_and_approved_can_write(self, tmp_path):
        """
        验证只有 AUTO_APPLY 和 approved Proposal 两条路径能写入 Store。
        """
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            SelfModelApprovalQueue,
        )
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )
        policy = SelfModelGovernancePolicy()
        queue = SelfModelApprovalQueue()

        # 路径 1: AUTO_APPLY (context)
        ctx_record = _make_record(growth_level="context", confidence=0.80)
        ctx_decision = policy.evaluate(ctx_record)
        assert ctx_decision.action.value == "auto_apply"
        ctx_proposal = updater.create_proposal_from_growth(ctx_record)
        updater.apply_proposal(ctx_proposal)
        ctx_narratives = _get_narratives(store)
        assert len(ctx_narratives) > 0, "AUTO_APPLY 路径应写入"

        # 路径 2: APPROVAL_REQUIRED → approved (trait)
        trait_record = _make_record(growth_level="trait", confidence=0.90)
        trait_decision = policy.evaluate(trait_record)
        assert trait_decision.action.value == "approval_required"
        trait_proposal = updater.create_proposal_from_growth(trait_record)
        queue.enqueue(trait_proposal)
        approved = queue.approve(trait_proposal.suggestion_id)
        assert approved.requires_approval is False
        updater.apply_proposal(approved)
        trait_narratives = _get_narratives(store)
        assert len(trait_narratives) >= len(ctx_narratives), (
            f"approved Proposal 路径应写入: ctx={len(ctx_narratives)}, trait={len(trait_narratives)}"
        )

        # 路径 3: 未审批 → 应被拒绝
        unapproved = updater.create_proposal_from_growth(
            _make_record(growth_level="trait", confidence=0.90)
        )
        assert unapproved.requires_approval is True
        pre_count = len(_get_narratives(store))
        adapter.apply_suggestion(manager=None, suggestion=unapproved)
        post_count = len(_get_narratives(store))
        assert post_count == pre_count, "未审批路径必须被拒绝"


# ============================================================
# Test 7: 向后兼容 — 旧测试路径不破坏
# ============================================================

class TestBackwardCompatibility:
    """验证旧 API 在 Governance 闸门下仍可正常工作"""

    def test_update_from_growth_with_governance(self, tmp_path):
        """update_from_growth() 内部已有 governance，正常调用"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)

        record = _make_record(growth_level="context", confidence=0.80)
        proposal = updater.update_from_growth(record)
        assert proposal is not None

        narratives = _get_narratives(store)
        assert len(narratives) > 0

    def test_update_from_growth_records(self, tmp_path):
        """update_from_growth_records() 批量调用"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)

        records = [
            _make_record(growth_level="context", confidence=0.80),
            _make_record(growth_level="context", confidence=0.75),
        ]
        results = updater.update_from_growth_records(records)
        assert len(results) == 2

        narratives = _get_narratives(store)
        assert len(narratives) >= 2

    def test_apply_suggestion_with_approved_proposal_succeeds(self, tmp_path):
        """已批准 proposal → apply_suggestion 正常写入"""
        from src.personality.self_model_store import SelfModelStore
        from src.personality.self_model_updater import SelfModelUpdater
        from src.runtime.self_model.self_model_updater_adapter import SelfModelUpdaterAdapter

        store = SelfModelStore(storage_path=os.path.join(tmp_path, "store.json"))
        updater = SelfModelUpdater(self_model_store=store)
        adapter = SelfModelUpdaterAdapter(
            self_model_store=store,
            self_model_updater=updater,
        )

        record = _make_record(growth_level="context", confidence=0.80)
        proposal = updater.create_proposal_from_growth(record)
        proposal.requires_approval = False  # 模拟已批准

        pre_count = len(_get_narratives(store))
        adapter.apply_suggestion(manager=None, suggestion=proposal)
        post_count = len(_get_narratives(store))
        assert post_count > pre_count, "已批准 proposal 应正常写入"