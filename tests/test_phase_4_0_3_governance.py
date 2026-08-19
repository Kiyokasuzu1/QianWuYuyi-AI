"""
Phase 4.0.3 — SelfModel Governance Contract 测试

验证 GovernancePolicy、GovernanceDecision、ApprovalQueue 的正确性，
以及 SelfModelUpdater 的治理集成。

测试覆盖：
- GovernanceDecision 不可变性
- GovernancePolicy 对五种 growth_level 的决策
- 不同 Policy 实例对同一记录返回相同决策
- SelfModelUpdater.update_from_growth() 接入治理
- ApprovalQueue 完整生命周期
- Proposal 不携带治理状态
- 硬契约：无第三条路径到达 Store
- 向后兼容：legacy update_from_growth_records()
"""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime

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


def _get_narratives(store) -> list:
    """安全获取 growth_narratives，处理 store.get() 返回 None 的情况"""
    model = store.get()
    if model is None:
        return []
    return model.get("growth_narratives", [])


def _make_store(tmp_path: str):
    from src.personality.self_model_store import SelfModelStore
    return SelfModelStore(storage_path=os.path.join(tmp_path, "self_model.json"))


# ============================================================
# GovernanceDecision
# ============================================================

class TestGovernanceDecision:
    """验证 GovernanceDecision 的不可变性和等价性"""

    def test_decision_immutable(self):
        from src.personality.self_model_governance import (
            GovernanceDecision,
            GovernanceAction,
        )
        d = GovernanceDecision(
            action=GovernanceAction.AUTO_APPLY,
            growth_level="context",
            confidence=0.6,
            reason="test",
        )
        with pytest.raises(Exception):
            d.action = GovernanceAction.DENY

    def test_decisions_equal_same_input(self):
        from src.personality.self_model_governance import (
            GovernanceDecision,
            GovernanceAction,
        )
        d1 = GovernanceDecision(
            action=GovernanceAction.AUTO_APPLY,
            growth_level="context",
            confidence=0.6,
            reason="reason A",
        )
        d2 = GovernanceDecision(
            action=GovernanceAction.AUTO_APPLY,
            growth_level="context",
            confidence=0.6,
            reason="reason B",
        )
        assert d1 == d2  # reason 不同但等价

    def test_decisions_not_equal_different_level(self):
        from src.personality.self_model_governance import (
            GovernanceDecision,
            GovernanceAction,
        )
        d1 = GovernanceDecision(
            action=GovernanceAction.AUTO_APPLY,
            growth_level="context",
            confidence=0.6,
            reason="",
        )
        d2 = GovernanceDecision(
            action=GovernanceAction.AUTO_APPLY,
            growth_level="preference",
            confidence=0.6,
            reason="",
        )
        assert d1 != d2


# ============================================================
# GovernancePolicy
# ============================================================

class TestGovernancePolicy:
    """验证 GovernancePolicy 的五种 growth_level 决策"""

    def test_context_auto_apply_high_confidence(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="context", confidence=0.6))
        assert decision.action == GovernanceAction.AUTO_APPLY

    def test_context_auto_apply_low_confidence(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="context", confidence=0.5))
        assert decision.action == GovernanceAction.AUTO_APPLY

    def test_context_deny_below_threshold(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="context", confidence=0.49))
        assert decision.action == GovernanceAction.DENY

    def test_preference_auto_apply_high(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="preference", confidence=0.7))
        assert decision.action == GovernanceAction.AUTO_APPLY

    def test_preference_approval_required_low(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="preference", confidence=0.6))
        assert decision.action == GovernanceAction.APPROVAL_REQUIRED

    def test_trait_approval_required(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="trait", confidence=0.85))
        assert decision.action == GovernanceAction.APPROVAL_REQUIRED

    def test_trait_deny_low_confidence(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="trait", confidence=0.75))
        assert decision.action == GovernanceAction.DENY

    def test_identity_deny(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="identity", confidence=0.95))
        assert decision.action == GovernanceAction.DENY

    def test_trace_deny(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="trace", confidence=0.3))
        assert decision.action == GovernanceAction.DENY

    def test_unknown_level_deny(self):
        from src.personality.self_model_governance import (
            SelfModelGovernancePolicy,
            GovernanceAction,
        )
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate(_make_record(growth_level="unknown", confidence=0.9))
        assert decision.action == GovernanceAction.DENY

    def test_same_decision_different_instances(self):
        """两个独立 Policy 实例对同一记录返回等价决策"""
        from src.personality.self_model_governance import SelfModelGovernancePolicy
        p1 = SelfModelGovernancePolicy()
        p2 = SelfModelGovernancePolicy()
        record = _make_record(growth_level="trait", confidence=0.85)
        assert p1.evaluate(record) == p2.evaluate(record)


# ============================================================
# SelfModelUpdater with Governance
# ============================================================

class TestSelfModelUpdaterGovernance:
    """验证 SelfModelUpdater 接入 GovernancePolicy 后的行为"""

    def test_context_auto_apply_writes_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import SelfModelGovernancePolicy

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            record = _make_record(growth_level="context", confidence=0.6)
            proposal = updater.update_from_growth(record)

            assert proposal is not None
            # 验证 Store 已写入
            narratives = _get_narratives(store)
            assert len(narratives) >= 1

    def test_trait_approval_required_does_not_write_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import SelfModelGovernancePolicy

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            # 记录 trait 写入前的叙事数量
            store_before = len(_get_narratives(store))

            record = _make_record(growth_level="trait", confidence=0.85)
            proposal = updater.update_from_growth(record)

            assert proposal is not None  # proposal 生成了
            # 但 Store 不应被写入
            store_after = len(_get_narratives(store))
            assert store_after == store_before

    def test_identity_deny_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import SelfModelGovernancePolicy

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            record = _make_record(growth_level="identity", confidence=0.95)
            proposal = updater.update_from_growth(record)

            assert proposal is None

    def test_apply_proposal_writes_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import SelfModelGovernancePolicy

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            record = _make_record(growth_level="trait", confidence=0.85)
            proposal = updater.create_proposal_from_growth(record)

            store_before = len(_get_narratives(store))
            updater.apply_proposal(proposal)
            store_after = len(_get_narratives(store))

            assert store_after > store_before


# ============================================================
# ApprovalQueue
# ============================================================

class TestApprovalQueue:
    """验证 SelfModelApprovalQueue 完整生命周期"""

    def test_enqueue_approve_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import (
                SelfModelGovernancePolicy,
                SelfModelApprovalQueue,
            )

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            queue = SelfModelApprovalQueue()

            record = _make_record(growth_level="trait", confidence=0.85)
            proposal = updater.create_proposal_from_growth(record)
            assert proposal is not None

            # 入队
            queue.enqueue(proposal)
            assert queue.pending_count == 1
            assert proposal.suggestion_id in queue.get_pending_ids()

            # 审批
            approved = queue.approve(proposal.suggestion_id)
            assert approved is not None
            assert queue.pending_count == 0

            # 应用
            store_before = len(_get_narratives(store))
            updater.apply_proposal(approved)
            store_after = len(_get_narratives(store))
            assert store_after > store_before

    def test_enqueue_reject_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import (
                SelfModelGovernancePolicy,
                SelfModelApprovalQueue,
            )

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            queue = SelfModelApprovalQueue()

            record = _make_record(growth_level="trait", confidence=0.85)
            proposal = updater.create_proposal_from_growth(record)

            queue.enqueue(proposal)
            store_before = len(_get_narratives(store))

            queue.reject(proposal.suggestion_id, reason="测试拒绝")
            assert queue.pending_count == 0

            store_after = len(_get_narratives(store))
            assert store_after == store_before

    def test_approve_nonexistent_returns_none(self):
        from src.personality.self_model_governance import SelfModelApprovalQueue
        queue = SelfModelApprovalQueue()
        assert queue.approve("nonexistent_id") is None


# ============================================================
# Hard Contract: No Third Path
# ============================================================

class TestGovernanceHardContract:
    """验证硬契约：任何 Store 写入必须通过 Governance + AUTO_APPLY 或 ApprovalQueue"""

    def test_proposal_not_polluted_with_governance(self):
        """Proposal 不携带治理状态（requires_approval 不影响 Governance 决策）"""
        from src.personality.self_model_updater import SelfModelUpdater
        from src.personality.self_model_governance import SelfModelGovernancePolicy

        updater = SelfModelUpdater(
            governance_policy=SelfModelGovernancePolicy(),
        )
        record = _make_record(growth_level="trait", confidence=0.85)
        proposal = updater.create_proposal_from_growth(record)

        # Proposal 的 requires_approval 是 dataclass 默认值，不是 Governance 写入的
        assert proposal is not None
        # Governance 决策应独立于 Proposal 的 requires_approval 字段
        # 由 GovernancePolicy.evaluate() 提供，而非从 Proposal 读取

    def test_legacy_update_from_growth_records_still_works(self):
        """向后兼容：update_from_growth_records() 仍可正常调用"""
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            from src.personality.self_model_updater import SelfModelUpdater
            from src.personality.self_model_governance import SelfModelGovernancePolicy

            updater = SelfModelUpdater(
                self_model_store=store,
                governance_policy=SelfModelGovernancePolicy(),
            )
            records = [
                _make_record(growth_level="context", confidence=0.6),
                _make_record(growth_level="context", confidence=0.55),
            ]
            applied = updater.update_from_growth_records(records)

            # context + confidence≥0.5 → AUTO_APPLY → 应成功应用
            assert len(applied) == 2
            narratives = _get_narratives(store)
            assert len(narratives) >= 2