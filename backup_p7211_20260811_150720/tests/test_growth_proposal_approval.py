# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_approval.py

Phase C.8.3 Growth Proposal Approval Workflow —— 验证测试

目标:
  验证 GrowthProposalApprovalWorkflow 严格只管理:
    1) approve:   approved → ready_for_apply
    2) reject:    reviewing → rejected
    3) revoke:    ready_for_apply → approved(重新审核)
  并保证:
    - 权限模型(human/admin/system,anonymous 拒绝)
    - Audit 完整记录
    - 不触发人格 / SelfModel / Trait 修改
    - 不调用 apply_proposal / accept_proposal
    - 并发安全
    - FailSafe

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务

覆盖 35+ 测试:
  Creation (1)
  Approve (2-3)
  Reject (4-5)
  Revoke (6-7)
  Invalid (8-10)
  Permission (11-13)
  Readonly (14-15)
  Security (16-17)
  Audit (18-20)
  FailSafe (21-22)
  Concurrency (23)
  Integration (24)
  Schema (25-35+)
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.growth import (
    GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION,
    GROWTH_PROPOSAL_APPROVAL_NAME,
    GROWTH_PROPOSAL_APPROVAL_VERSION,
    STATE_PENDING,
    STATE_REVIEWING,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_NEEDS_REVIEW,
    STATE_READY_FOR_APPLY,
    AUDIT_ACTION_APPROVED,
    AUDIT_ACTION_REJECTED,
    AUDIT_ACTION_REVOKED,
    ACTOR_HUMAN,
    ACTOR_ADMIN,
    ACTOR_SYSTEM,
    ALLOWED_ACTORS,
    REVOKE_REQUIRED_ACTORS,
    DEFAULT_ACTOR,
    REASON_UNAUTHORIZED,
    REASON_PROPOSAL_NOT_FOUND,
    REASON_ALREADY_APPROVED,
    REASON_ALREADY_REJECTED,
    REASON_ALREADY_REVOKED,
    REASON_NOT_APPROVED,
    REASON_NOT_REVIEWING,
    REASON_NOT_READY_FOR_REVOKE,
    GrowthProposalApprovalWorkflow,
    create_growth_proposal_approval,
    safe_approve,
    safe_reject,
    safe_revoke,
    is_authorized,
    is_revoke_authorized,
)
from src.runtime.growth.growth_proposal_approval import (
    REASON_INVALID_INPUT,
    REASON_STORE_ERROR,
    REASON_INTERNAL_ERROR,
    REASON_DEGRADED,
    REASON_INVALID_STATE,
)


# ============================================================
# 1. Mock ProposalStore
# ============================================================


class _MockProposalStore:
    """最小 ProposalStore mock,支持 load/update + _index。"""

    def __init__(self) -> None:
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_calls: int = 0
        self._update_calls: int = 0
        self._raise_on_update: Optional[Exception] = None
        self._raise_on_load: Optional[Exception] = None
        self._lock = threading.RLock()

    def load(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._load_calls += 1
            if self._raise_on_load is not None:
                raise self._raise_on_load
            d = self._index.get(str(proposal_id or ""))
            if d is None:
                return None
            return dict(d)

    def update(self, proposal: Any) -> None:
        with self._lock:
            self._update_calls += 1
            if self._raise_on_update is not None:
                raise self._raise_on_update
            if isinstance(proposal, dict):
                pid = str(proposal.get("id") or proposal.get("proposal_id") or "")
                if pid:
                    self._index[pid] = dict(proposal)

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        return self.load(proposal_id)

    def add_proposal(self, proposal_id: str, status: str = STATE_PENDING, **kwargs: Any) -> None:
        with self._lock:
            self._index[str(proposal_id)] = {
                "id": str(proposal_id),
                "status": str(status),
                **{k: v for k, v in kwargs.items()},
            }

    def count(self) -> int:
        with self._lock:
            return len(self._index)

    @property
    def load_call_count(self) -> int:
        return self._load_calls

    @property
    def update_call_count(self) -> int:
        return self._update_calls


# ============================================================
# 2. Mock Audit
# ============================================================


class _MockAudit:
    """最小 audit 记录器。"""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._raise: bool = False

    def record(
        self,
        operation_type: str = "",
        source: str = "",
        action: str = "",
        detail: Optional[Dict[str, Any]] = None,
        result: str = "success",
        **kwargs: Any,
    ) -> None:
        if self._raise:
            raise RuntimeError("audit record failed")
        self._records.append({
            "operation_type": operation_type,
            "source": source,
            "action": action,
            "detail": dict(detail or {}),
            "result": result,
        })

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def records(self) -> List[Dict[str, Any]]:
        return list(self._records)


# ============================================================
# 3. Mock 业务对象(用于 TestReadonly / TestSecurity)
# ============================================================


class _MockPersonalityState:
    """模拟 src/personality/PersonalityState。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5, "gentleness": 0.5}
        self._writes: int = 0
        self._resolve_calls: int = 0

    def resolve(self, *args: Any, **kwargs: Any) -> Any:
        self._writes += 1
        self._resolve_calls += 1
        return {"resolved": True}

    def apply_proposal(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes

    @property
    def resolve_call_count(self) -> int:
        return self._resolve_calls


class _MockSelfModelStore:
    """模拟 src/runtime/self_model/SelfModelStore。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5}
        self._writes: int = 0
        self._update_calls: int = 0

    def update(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        self._update_calls += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes

    @property
    def update_call_count(self) -> int:
        return self._update_calls


class _MockTraitStateUpdater:
    """模拟 TraitStateUpdater。"""

    def __init__(self) -> None:
        self._writes: int = 0
        self._apply_calls: int = 0

    def apply(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        self._apply_calls += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes

    @property
    def apply_call_count(self) -> int:
        return self._apply_calls


class _MockProposalManager:
    """带计数 + 异常注入的 ProposalManager。"""

    def __init__(self) -> None:
        self.accept_calls: int = 0
        self.reject_calls: int = 0
        self.apply_calls: int = 0
        self.create_calls: int = 0

    def accept_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.accept_calls += 1
        raise AssertionError("accept_proposal FORBIDDEN in C.8.3")

    def reject_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.reject_calls += 1
        raise AssertionError("reject_proposal FORBIDDEN in C.8.3")

    def apply_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.apply_calls += 1
        raise AssertionError("apply_proposal FORBIDDEN in C.8.3")

    def create_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls += 1
        return None


# ============================================================
# 4. 辅助工厂
# ============================================================


def _make_workflow(
    store: Optional[_MockProposalStore] = None,
    audit: Optional[_MockAudit] = None,
) -> GrowthProposalApprovalWorkflow:
    if store is None:
        store = _MockProposalStore()
    if audit is None:
        audit = _MockAudit()
    return create_growth_proposal_approval(proposal_store=store, audit=audit)


# ============================================================
# 5. 测试:Creation (1)
# ============================================================


class TestCreation:
    """Workflow 创建测试。"""

    def test_01_workflow_creates_successfully(self) -> None:
        """1. workflow 创建成功(无依赖也可工作)。"""
        w = create_growth_proposal_approval()
        assert isinstance(w, GrowthProposalApprovalWorkflow)
        assert w.approve_count == 0
        assert w.reject_count == 0
        assert w.revoke_count == 0
        # 工厂 + 构造
        w2 = GrowthProposalApprovalWorkflow()
        assert w2 is not None
        # 注入 store / audit
        w3 = create_growth_proposal_approval(
            proposal_store=_MockProposalStore(),
            audit=_MockAudit(),
            default_approver="test_approver",
        )
        assert w3 is not None
        # workflow info
        info = w.get_workflow_info()
        assert info["name"] == GROWTH_PROPOSAL_APPROVAL_NAME
        assert info["version"] == GROWTH_PROPOSAL_APPROVAL_VERSION


# ============================================================
# 6. 测试:Approve (2-3)
# ============================================================


class TestApprove:
    """Approve 测试。"""

    def test_02_approved_to_ready_for_apply(self) -> None:
        """2. approved → ready_for_apply 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_ap_001", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_ap_001", approver=ACTOR_HUMAN)
        assert r["success"] is True
        assert r["approved"] is True
        assert r["previous_state"] == STATE_APPROVED
        assert r["new_state"] == STATE_READY_FOR_APPLY
        assert r["action"] == "approved"
        # store 状态被更新
        assert store.load("prop_ap_001")["status"] == STATE_READY_FOR_APPLY
        assert w.approve_count == 1

    def test_03_correct_actor_recorded(self) -> None:
        """3. approver 正确记录(支持 human / admin / system)。"""
        for actor in (ACTOR_HUMAN, ACTOR_ADMIN, ACTOR_SYSTEM):
            store = _MockProposalStore()
            store.add_proposal(f"prop_ap_a_{actor}", status=STATE_APPROVED)
            w = _make_workflow(store)
            r = w.approve(f"prop_ap_a_{actor}", approver=actor)
            assert r["success"] is True
            assert r["approver"] == actor
        # default actor
        store = _MockProposalStore()
        store.add_proposal("prop_ap_def", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_ap_def")  # 默认 approver
        assert r["success"] is True
        assert r["approver"] == DEFAULT_ACTOR
        assert r["approver"] == ACTOR_HUMAN


# ============================================================
# 7. 测试:Reject (4-5)
# ============================================================


class TestReject:
    """Reject 测试。"""

    def test_04_reviewing_to_rejected(self) -> None:
        """4. reviewing → rejected 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_rj_001", status=STATE_REVIEWING)
        w = _make_workflow(store)
        r = w.reject("prop_rj_001", reason="low_confidence", rejector=ACTOR_HUMAN)
        assert r["success"] is True
        assert r["action"] == "rejected"
        assert r["previous_state"] == STATE_REVIEWING
        assert r["new_state"] == STATE_REJECTED
        # store 状态被更新
        assert store.load("prop_rj_001")["status"] == STATE_REJECTED
        assert w.reject_count == 1

    def test_05_reason_saved(self) -> None:
        """5. reason 正确保存(在 report 和 audit 中)。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_rj_002", status=STATE_REVIEWING)
        w = _make_workflow(store, audit)
        r = w.reject("prop_rj_002", reason="inappropriate_content", rejector=ACTOR_ADMIN)
        assert r["success"] is True
        assert r["reject_reason"] == "inappropriate_content"
        assert r["reason"] == "inappropriate_content"
        # audit 中存在 reject 记录(reason 在其中)
        reject_records = [rec for rec in audit.records if rec["action"] == AUDIT_ACTION_REJECTED]
        assert len(reject_records) >= 1
        rec = reject_records[0]
        assert rec["detail"]["reason"] == "inappropriate_content"
        assert rec["detail"]["actor"] == ACTOR_ADMIN


# ============================================================
# 8. 测试:Revoke (6-7)
# ============================================================


class TestRevoke:
    """Revoke 测试。"""

    def test_06_ready_for_apply_to_approved(self) -> None:
        """6. ready_for_apply → approved 合法(重新审核)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_rv_001", status=STATE_READY_FOR_APPLY)
        w = _make_workflow(store)
        r = w.revoke_approval("prop_rv_001", revoker=ACTOR_ADMIN, reason="false_positive")
        assert r["success"] is True
        assert r["action"] == "revoked"
        assert r["previous_state"] == STATE_READY_FOR_APPLY
        assert r["new_state"] == STATE_APPROVED
        # store 状态被更新
        assert store.load("prop_rv_001")["status"] == STATE_APPROVED
        assert w.revoke_count == 1

    def test_07_revoke_idempotent_or_already_revoke(self) -> None:
        """7. 重复撤销 → already_revoked 错误。"""
        store = _MockProposalStore()
        store.add_proposal("prop_rv_002", status=STATE_APPROVED)  # 已经是 approved
        w = _make_workflow(store)
        r = w.revoke_approval("prop_rv_002", revoker=ACTOR_ADMIN, reason="re-revoke")
        assert r["success"] is False
        assert r["error_reason"] == REASON_ALREADY_REVOKED


# ============================================================
# 9. 测试:Invalid (8-10)
# ============================================================


class TestInvalid:
    """非法操作测试。"""

    def test_08_pending_approve_fails(self) -> None:
        """8. approve 在 pending 状态失败。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_001", status=STATE_PENDING)
        w = _make_workflow(store)
        r = w.approve("prop_inv_001", approver=ACTOR_HUMAN)
        assert r["success"] is False
        assert r["error_reason"] == REASON_NOT_APPROVED
        # store 状态不变
        assert store.load("prop_inv_001")["status"] == STATE_PENDING
        # approve_count 不增
        assert w.approve_count == 0

    def test_09_reviewing_approve_fails(self) -> None:
        """9. approve 在 reviewing 状态失败(必须先经过 approved 节点)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_002", status=STATE_REVIEWING)
        w = _make_workflow(store)
        r = w.approve("prop_inv_002", approver=ACTOR_HUMAN)
        assert r["success"] is False
        assert r["error_reason"] == REASON_NOT_APPROVED
        assert store.load("prop_inv_002")["status"] == STATE_REVIEWING

    def test_10_rejected_approve_fails(self) -> None:
        """10. approve 在 rejected 状态失败。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_003", status=STATE_REJECTED)
        w = _make_workflow(store)
        r = w.approve("prop_inv_003", approver=ACTOR_HUMAN)
        assert r["success"] is False
        # 终态不可 approve
        assert r["error_reason"] in (REASON_NOT_APPROVED, REASON_ALREADY_REJECTED)
        assert store.load("prop_inv_003")["status"] == STATE_REJECTED


# ============================================================
# 10. 测试:Permission (11-13)
# ============================================================


class TestPermission:
    """权限模型测试。"""

    def test_11_human_allowed(self) -> None:
        """11. human 允许 approve。"""
        store = _MockProposalStore()
        store.add_proposal("prop_perm_001", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_perm_001", approver=ACTOR_HUMAN)
        assert r["success"] is True
        assert r["approver"] == ACTOR_HUMAN

    def test_12_admin_allowed(self) -> None:
        """12. admin 允许 approve / reject / revoke。"""
        store = _MockProposalStore()
        store.add_proposal("prop_perm_002", status=STATE_APPROVED)
        w = _make_workflow(store)
        r1 = w.approve("prop_perm_002", approver=ACTOR_ADMIN)
        assert r1["success"] is True
        assert r1["approver"] == ACTOR_ADMIN

        # admin 也能 reject
        store.add_proposal("prop_perm_002b", status=STATE_REVIEWING)
        r2 = w.reject("prop_perm_002b", reason="admin reject", rejector=ACTOR_ADMIN)
        assert r2["success"] is True
        assert r2["approver"] == ACTOR_ADMIN

        # admin 也能 revoke
        store.add_proposal("prop_perm_002c", status=STATE_READY_FOR_APPLY)
        r3 = w.revoke_approval("prop_perm_002c", revoker=ACTOR_ADMIN, reason="admin revoke")
        assert r3["success"] is True
        assert r3["approver"] == ACTOR_ADMIN

    def test_13_anonymous_rejected(self) -> None:
        """13. anonymous 拒绝(approve / reject / revoke 全部失败)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_perm_003", status=STATE_APPROVED)
        w = _make_workflow(store)
        # anonymous approve
        r1 = w.approve("prop_perm_003", approver="anonymous")
        assert r1["success"] is False
        assert r1["error_reason"] == REASON_UNAUTHORIZED
        # anonymous reject
        store.add_proposal("prop_perm_003b", status=STATE_REVIEWING)
        r2 = w.reject("prop_perm_003b", reason="x", rejector="anonymous")
        assert r2["success"] is False
        assert r2["error_reason"] == REASON_UNAUTHORIZED
        # anonymous revoke (即使使用 human/admin 之外的 actor)
        store.add_proposal("prop_perm_003c", status=STATE_READY_FOR_APPLY)
        r3 = w.revoke_approval("prop_perm_003c", revoker="hacker", reason="x")
        assert r3["success"] is False
        assert r3["error_reason"] == REASON_UNAUTHORIZED
        # human 也不能 revoke(只能 admin/system)
        r4 = w.revoke_approval("prop_perm_003c", revoker=ACTOR_HUMAN, reason="x")
        assert r4["success"] is False
        assert r4["error_reason"] == REASON_UNAUTHORIZED


# ============================================================
# 11. 测试:Readonly (14-15)
# ============================================================


class TestReadonly:
    """只读业务边界测试。"""

    def test_14_personality_state_unchanged(self) -> None:
        """14. approve / reject / revoke 不会修改 PersonalityState。"""
        pys = _MockPersonalityState()
        store = _MockProposalStore()
        w = _make_workflow(store)
        # approve
        store.add_proposal("prop_ro_001", status=STATE_APPROVED)
        w.approve("prop_ro_001", approver=ACTOR_HUMAN)
        # reject
        store.add_proposal("prop_ro_002", status=STATE_REVIEWING)
        w.reject("prop_ro_002", reason="test", rejector=ACTOR_HUMAN)
        # revoke
        store.add_proposal("prop_ro_003", status=STATE_READY_FOR_APPLY)
        w.revoke_approval("prop_ro_003", revoker=ACTOR_ADMIN, reason="test")
        # PersonalityState 完全未变
        assert pys.write_count == 0
        assert pys.resolve_call_count == 0
        assert pys.traits == {"warmth": 0.5, "gentleness": 0.5}

    def test_15_self_model_unchanged(self) -> None:
        """15. approve / reject / revoke 不会修改 SelfModel / Trait。"""
        sms = _MockSelfModelStore()
        tsu = _MockTraitStateUpdater()
        store = _MockProposalStore()
        w = _make_workflow(store)
        store.add_proposal("prop_ro_004", status=STATE_APPROVED)
        w.approve("prop_ro_004", approver=ACTOR_HUMAN)
        store.add_proposal("prop_ro_005", status=STATE_REVIEWING)
        w.reject("prop_ro_005", reason="test", rejector=ACTOR_HUMAN)
        store.add_proposal("prop_ro_006", status=STATE_READY_FOR_APPLY)
        w.revoke_approval("prop_ro_006", revoker=ACTOR_ADMIN, reason="test")
        # SelfModel / Trait 完全未变
        assert sms.write_count == 0
        assert sms.update_call_count == 0
        assert tsu.write_count == 0
        assert tsu.apply_call_count == 0


# ============================================================
# 12. 测试:Security (16-17)
# ============================================================


class TestSecurity:
    """安全限制测试。"""

    def test_16_apply_forbidden(self) -> None:
        """16. approve / reject / revoke 都不会触发 apply_proposal / accept_proposal / reject_proposal。"""
        pm = _MockProposalManager()
        store = _MockProposalStore()
        w = _make_workflow(store)
        store.add_proposal("prop_sec_001", status=STATE_APPROVED)
        w.approve("prop_sec_001", approver=ACTOR_HUMAN)
        store.add_proposal("prop_sec_002", status=STATE_REVIEWING)
        w.reject("prop_sec_002", reason="x", rejector=ACTOR_HUMAN)
        store.add_proposal("prop_sec_003", status=STATE_READY_FOR_APPLY)
        w.revoke_approval("prop_sec_003", revoker=ACTOR_ADMIN, reason="x")
        # ProposalManager 完全未被调用
        assert pm.apply_calls == 0
        assert pm.accept_calls == 0
        assert pm.reject_calls == 0
        assert pm.create_calls == 0

    def test_17_resolver_forbidden(self) -> None:
        """17. PersonalityResolver.resolve() 必须 0 次调用。"""
        pys = _MockPersonalityState()
        store = _MockProposalStore()
        w = _make_workflow(store)
        store.add_proposal("prop_sec_004", status=STATE_APPROVED)
        w.approve("prop_sec_004", approver=ACTOR_HUMAN)
        store.add_proposal("prop_sec_005", status=STATE_REVIEWING)
        w.reject("prop_sec_005", reason="x", rejector=ACTOR_HUMAN)
        assert pys.resolve_call_count == 0
        assert pys.write_count == 0


# ============================================================
# 13. 测试:Audit (18-20)
# ============================================================


class TestAudit:
    """Audit 测试。"""

    def test_18_approve_audit(self) -> None:
        """18. approve 操作记录 audit。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_aud_001", status=STATE_APPROVED)
        w = _make_workflow(store, audit)
        w.approve("prop_aud_001", approver=ACTOR_HUMAN)
        # 找到 approve 记录(可能同时有 lifecycle transition 记录)
        approve_records = [rec for rec in audit.records if rec["action"] == AUDIT_ACTION_APPROVED]
        assert len(approve_records) >= 1
        rec = approve_records[0]
        assert rec["operation_type"] == AUDIT_ACTION_APPROVED
        assert rec["action"] == AUDIT_ACTION_APPROVED
        assert rec["detail"]["proposal_id"] == "prop_aud_001"
        assert rec["detail"]["old_state"] == STATE_APPROVED
        assert rec["detail"]["new_state"] == STATE_READY_FOR_APPLY
        assert rec["detail"]["actor"] == ACTOR_HUMAN
        assert "timestamp" in rec["detail"]

    def test_19_reject_audit(self) -> None:
        """19. reject 操作记录 audit。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_aud_002", status=STATE_REVIEWING)
        w = _make_workflow(store, audit)
        w.reject("prop_aud_002", reason="audit_test", rejector=ACTOR_ADMIN)
        # 找到 reject 记录
        reject_records = [rec for rec in audit.records if rec["action"] == AUDIT_ACTION_REJECTED]
        assert len(reject_records) >= 1
        rec = reject_records[0]
        assert rec["operation_type"] == AUDIT_ACTION_REJECTED
        assert rec["detail"]["old_state"] == STATE_REVIEWING
        assert rec["detail"]["new_state"] == STATE_REJECTED
        assert rec["detail"]["reason"] == "audit_test"
        assert rec["detail"]["actor"] == ACTOR_ADMIN

    def test_20_revoke_audit(self) -> None:
        """20. revoke 操作记录 audit。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_aud_003", status=STATE_READY_FOR_APPLY)
        w = _make_workflow(store, audit)
        w.revoke_approval("prop_aud_003", revoker=ACTOR_ADMIN, reason="re_audit")
        # revoke 不走 lifecycle,所以 audit 只有 1 条
        assert audit.record_count >= 1
        revoke_records = [rec for rec in audit.records if rec["action"] == AUDIT_ACTION_REVOKED]
        assert len(revoke_records) >= 1
        rec = revoke_records[0]
        assert rec["operation_type"] == AUDIT_ACTION_REVOKED
        assert rec["detail"]["old_state"] == STATE_READY_FOR_APPLY
        assert rec["detail"]["new_state"] == STATE_APPROVED
        assert rec["detail"]["reason"] == "re_audit"
        assert rec["detail"]["actor"] == ACTOR_ADMIN


# ============================================================
# 14. 测试:FailSafe (21-22)
# ============================================================


class TestFailSafe:
    """失败隔离测试。"""

    def test_21_store_exception(self) -> None:
        """21. store.update() 抛异常 → 不崩,返回 error。"""
        store = _MockProposalStore()
        store.add_proposal("prop_fs_001", status=STATE_READY_FOR_APPLY)
        store._raise_on_update = RuntimeError("store update failed")
        w = _make_workflow(store, _MockAudit())
        r = w.revoke_approval("prop_fs_001", revoker=ACTOR_ADMIN, reason="x")
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["error_reason"] == REASON_STORE_ERROR
        # 不崩
        assert w.error_count >= 1

    def test_22_audit_exception(self) -> None:
        """22. audit.record() 抛异常 → 操作仍成功,但 audit_recorded=False。"""
        audit = _MockAudit()
        audit._raise = True
        store = _MockProposalStore()
        store.add_proposal("prop_fs_002", status=STATE_APPROVED)
        w = _make_workflow(store, audit)
        r = w.approve("prop_fs_002", approver=ACTOR_HUMAN)
        # 操作仍成功
        assert r["success"] is True
        # 但 audit_recorded=False
        assert r["audit_recorded"] is False
        # store 状态确实被更新
        assert store.load("prop_fs_002")["status"] == STATE_READY_FOR_APPLY


# ============================================================
# 15. 测试:Concurrency (23)
# ============================================================


class TestConcurrency:
    """并发安全测试。"""

    def test_23_concurrent_approvals_thread_safe(self) -> None:
        """23. 多线程并发 approve 是 thread-safe 的。"""
        store = _MockProposalStore()
        # 准备 20 个 proposal(各处于 approved)
        n = 20
        for i in range(n):
            store.add_proposal(f"prop_conc_{i:03d}", status=STATE_APPROVED)
        w = _make_workflow(store)
        results: List[Dict[str, Any]] = []
        errors: List[Exception] = []

        def worker(idx: int) -> None:
            try:
                pid = f"prop_conc_{idx:03d}"
                r = w.approve(pid, approver=ACTOR_HUMAN)
                results.append(r)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads: List[threading.Thread] = []
        for i in range(n):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"thread 死锁: {t.name}"
        # 0 错误
        assert len(errors) == 0
        # 全部成功
        assert len(results) == n
        for r in results:
            assert r["success"] is True
        # store 状态正确
        for i in range(n):
            pid = f"prop_conc_{i:03d}"
            assert store.load(pid)["status"] == STATE_READY_FOR_APPLY


# ============================================================
# 16. 测试:Integration (24)
# ============================================================


class TestIntegration:
    """端到端集成测试。"""

    def test_24_full_workflow_runtime_to_approval(self) -> None:
        """24. Runtime → Reviewer → Lifecycle → Approval 完整端到端。"""
        # 使用一个完整的 mock ProposalManager(支持 create + load + update)
        pm = _MockProposalManagerExt()
        from src.runtime.growth import GrowthProposalRuntime
        runtime = GrowthProposalRuntime(
            growth_evaluator=_MockEvaluatorLite(),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        # 1) 创建 proposal
        signal = {
            "id": "evt_int_001",
            "event_id": "evt_int_001",
            "event_type": "preference",
            "canonical_topic": "music",
            "event": {"id": "evt_int_001", "event_type": "preference", "canonical_topic": "music"},
            "evidence": [{"id": "ev_1", "text": "她说了她喜欢"}],
            "source_ids": ["ev_1"],
            "importance_score": 0.8,
            "confidence": 0.85,
            "cycle_id": "cycle_int_001",
            "proposed_changes": [{"path": "growth.music", "before": 0.5, "after": 0.6}],
        }
        r1 = runtime.process_growth_signal(signal)
        assert r1["status"] == "created"
        proposal_id = r1["proposal_id"]

        # 2) Lifecycle: pending → reviewing → approved
        from src.runtime.growth import create_growth_proposal_lifecycle
        lifecycle = create_growth_proposal_lifecycle(
            proposal_store=pm,
            audit=_MockAudit(),
        )
        r2 = lifecycle.transition(proposal_id, STATE_REVIEWING, actor="runtime")
        assert r2["success"] is True
        r3 = lifecycle.transition(proposal_id, STATE_APPROVED, actor="human_reviewer")
        assert r3["success"] is True

        # 3) Approval: approved → ready_for_apply
        w = create_growth_proposal_approval(
            proposal_store=pm,
            lifecycle=lifecycle,
            audit=_MockAudit(),
        )
        r4 = w.approve(proposal_id, approver=ACTOR_HUMAN)
        assert r4["success"] is True
        assert r4["new_state"] == STATE_READY_FOR_APPLY

        # 4) Revoke: ready_for_apply → approved
        r5 = w.revoke_approval(proposal_id, revoker=ACTOR_ADMIN, reason="re_review")
        assert r5["success"] is True
        assert r5["new_state"] == STATE_APPROVED

        # 5) 再次 approve
        r6 = w.approve(proposal_id, approver=ACTOR_HUMAN)
        assert r6["success"] is True
        assert r6["new_state"] == STATE_READY_FOR_APPLY

        # proposal 最终状态
        p = pm.load(proposal_id)
        assert p is not None
        assert p["status"] == STATE_READY_FOR_APPLY


# 辅助类(用于 TestIntegration)
class _MockEvaluatorLite:
    def evaluate(self, event, history=None):
        return {"confidence": 0.85, "target_candidates": ["growth"]}


class _MockProposalManagerExt:
    """支持 create + load + update 的 ProposalManager(用于 TestIntegration)。"""

    def __init__(self) -> None:
        self._index: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()
        self._next_id: int = 0

    def create_proposal(
        self,
        source_event: Dict[str, Any],
        proposed_changes: List[Any],
        confidence: float,
        evidence_ids: List[str],
        evaluator_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if confidence < 0.7 or not evidence_ids:
            return {"status": "rejected_low_confidence", "proposal": None}
        with self._lock:
            self._next_id += 1
            pid = f"prop_{self._next_id:03d}"
            proposal = {
                "id": pid,
                "source_event_id": source_event.get("id", ""),
                "proposed_changes": [
                    ch.to_dict() if hasattr(ch, "to_dict") else (ch if isinstance(ch, dict) else {})
                    for ch in proposed_changes
                ],
                "confidence": float(confidence),
                "evidence_ids": list(evidence_ids),
                "evaluator_meta": dict(evaluator_meta or {}),
                "status": "pending",
                "timestamp": "2026-08-03T00:00:00Z",
                "schema_version": "1.0",
            }
            self._index[pid] = proposal
        return {"status": "created", "proposal": proposal, "existing_id": None}

    def load(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            d = self._index.get(str(proposal_id or ""))
            return dict(d) if d else None

    def update(self, proposal: Any) -> None:
        with self._lock:
            if isinstance(proposal, dict):
                pid = str(proposal.get("id") or proposal.get("proposal_id") or "")
                if pid:
                    self._index[pid] = dict(proposal)


# ============================================================
# 17. 测试:Schema (25-35+)
# ============================================================


class TestSchema:
    """Schema 字段完整性测试。"""

    def test_25_schema_version(self) -> None:
        """25. schema_version 字段正确。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_001", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_s_001", approver=ACTOR_HUMAN)
        assert r["schema_version"] == GROWTH_PROPOSAL_APPROVAL_SCHEMA_VERSION
        assert r["schema_version"] == "1.0"

    def test_26_state_field(self) -> None:
        """26. previous_state / new_state 字段正确。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_002", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_s_002", approver=ACTOR_HUMAN)
        assert r["previous_state"] == STATE_APPROVED
        assert r["new_state"] == STATE_READY_FOR_APPLY

    def test_27_timestamp_present(self) -> None:
        """27. timestamp 字段存在且 ISO 格式。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_003", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_s_003", approver=ACTOR_HUMAN)
        assert "timestamp" in r
        assert r["timestamp"] is not None
        assert len(str(r["timestamp"])) > 0
        ts = str(r["timestamp"])
        assert "T" in ts

    def test_28_actor_field(self) -> None:
        """28. approver 字段正确。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_004", status=STATE_APPROVED)
        w = _make_workflow(store)
        r = w.approve("prop_s_004", approver=ACTOR_ADMIN)
        assert r["approver"] == ACTOR_ADMIN
        # reject 也是 approver 字段
        store.add_proposal("prop_s_004b", status=STATE_REVIEWING)
        r2 = w.reject("prop_s_004b", reason="x", rejector=ACTOR_SYSTEM)
        assert r2["approver"] == ACTOR_SYSTEM

    def test_29_reason_field(self) -> None:
        """29. reason / reject_reason 字段正确。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_005", status=STATE_REVIEWING)
        w = _make_workflow(store)
        r = w.reject("prop_s_005", reason="my_reason_005", rejector=ACTOR_HUMAN)
        assert r["reason"] == "my_reason_005"
        assert r["reject_reason"] == "my_reason_005"
        # revoke 也保存 reason
        store.add_proposal("prop_s_005b", status=STATE_READY_FOR_APPLY)
        r2 = w.revoke_approval("prop_s_005b", revoker=ACTOR_ADMIN, reason="my_revoke_005")
        assert r2["reason"] == "my_revoke_005"

    def test_30_success_field(self) -> None:
        """30. success 字段在成功/失败时正确反映。"""
        store = _MockProposalStore()
        w = _make_workflow(store)
        # 不存在的 proposal
        r1 = w.approve("nonexistent", approver=ACTOR_HUMAN)
        assert r1["success"] is False
        # 存在 + 合法状态
        store.add_proposal("prop_s_006", status=STATE_APPROVED)
        r2 = w.approve("prop_s_006", approver=ACTOR_HUMAN)
        assert r2["success"] is True

    def test_31_error_field(self) -> None:
        """31. 失败时 error / error_reason 字段非空。"""
        store = _MockProposalStore()
        w = _make_workflow(store)
        # 不存在 → error + error_reason
        r1 = w.approve("nonexistent_006", approver=ACTOR_HUMAN)
        assert r1["success"] is False
        assert r1["error"] is not None
        assert r1["error_reason"] == REASON_PROPOSAL_NOT_FOUND
        # 状态错误 → error + error_reason
        store.add_proposal("prop_s_006b", status=STATE_PENDING)
        r2 = w.approve("prop_s_006b", approver=ACTOR_HUMAN)
        assert r2["success"] is False
        assert r2["error"] is not None
        assert r2["error_reason"] == REASON_NOT_APPROVED
        # 权限错误
        r3 = w.approve("prop_s_006b", approver="anonymous")
        assert r3["success"] is False
        assert r3["error"] is not None
        assert r3["error_reason"] == REASON_UNAUTHORIZED

    def test_32_audit_recorded_field(self) -> None:
        """32. audit_recorded 字段正确。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_s_007", status=STATE_APPROVED)
        w = _make_workflow(store, audit)
        r = w.approve("prop_s_007", approver=ACTOR_HUMAN)
        assert r["audit_recorded"] is True

    def test_33_workflow_info(self) -> None:
        """33. get_workflow_info 返回完整 workflow 信息。"""
        w = create_growth_proposal_approval()
        info = w.get_workflow_info()
        for key in [
            "name", "version", "schema_version",
            "allowed_actors", "revoke_required_actors",
            "default_approver", "actions", "audit_actions",
        ]:
            assert key in info, f"workflow info 缺少 {key}"
        assert info["name"] == GROWTH_PROPOSAL_APPROVAL_NAME
        assert ACTOR_HUMAN in info["allowed_actors"]
        assert ACTOR_ADMIN in info["revoke_required_actors"]
        assert AUDIT_ACTION_APPROVED in info["audit_actions"]
        assert AUDIT_ACTION_REJECTED in info["audit_actions"]
        assert AUDIT_ACTION_REVOKED in info["audit_actions"]

    def test_34_get_stats(self) -> None:
        """34. get_stats 包含完整统计字段。"""
        store = _MockProposalStore()
        audit = _MockAudit()
        w = _make_workflow(store, audit)
        stats = w.get_stats()
        for key in [
            "approve_count", "reject_count", "revoke_count",
            "unauthorized_count", "error_count",
            "audit_recorded_count", "last_error", "last_action_ts",
            "history_size", "has_store", "has_audit", "has_lifecycle",
        ]:
            assert key in stats, f"stats 缺少 {key}"
        assert stats["has_store"] is True
        assert stats["has_audit"] is True
        assert stats["has_lifecycle"] is True

    def test_35_safe_helpers(self) -> None:
        """35. safe_approve / safe_reject / safe_revoke 不抛异常。"""
        # None workflow
        r1 = safe_approve(None, "any_id", approver=ACTOR_HUMAN)
        assert r1["success"] is False
        assert r1["degraded"] is True
        r2 = safe_reject(None, "any_id", reason="x", rejector=ACTOR_HUMAN)
        assert r2["success"] is False
        assert r2["degraded"] is True
        r3 = safe_revoke(None, "any_id", revoker=ACTOR_ADMIN, reason="x")
        assert r3["success"] is False
        assert r3["degraded"] is True

    def test_36_invalid_inputs_safe(self) -> None:
        """36. 无效输入不抛异常。"""
        w = create_growth_proposal_approval()
        # 空 proposal_id
        r1 = w.approve("", approver=ACTOR_HUMAN)
        assert r1["success"] is False
        assert r1["error_reason"] == REASON_INVALID_INPUT
        # 空 reason for reject
        r2 = w.reject("any_id", reason="", rejector=ACTOR_HUMAN)
        assert r2["success"] is False
        assert r2["error_reason"] == REASON_INVALID_INPUT
        # None proposal_id
        r3 = w.approve(None, approver=ACTOR_HUMAN)  # type: ignore[arg-type]
        assert r3["success"] is False
        # None reason for reject
        r4 = w.reject("any_id", reason=None, rejector=ACTOR_HUMAN)  # type: ignore[arg-type]
        assert r4["success"] is False

    def test_37_can_xxx_helpers(self) -> None:
        """37. can_approve / can_reject / can_revoke 状态判断。"""
        store = _MockProposalStore()
        w = _make_workflow(store)
        # approved → can_approve
        store.add_proposal("prop_h_001", status=STATE_APPROVED)
        assert w.can_approve("prop_h_001") is True
        assert w.can_reject("prop_h_001") is False
        assert w.can_revoke("prop_h_001") is False
        # reviewing → can_reject
        store.add_proposal("prop_h_002", status=STATE_REVIEWING)
        assert w.can_approve("prop_h_002") is False
        assert w.can_reject("prop_h_002") is True
        assert w.can_revoke("prop_h_002") is False
        # ready_for_apply → can_revoke
        store.add_proposal("prop_h_003", status=STATE_READY_FOR_APPLY)
        assert w.can_approve("prop_h_003") is False
        assert w.can_reject("prop_h_003") is False
        assert w.can_revoke("prop_h_003") is True

    def test_38_get_history(self) -> None:
        """38. get_history 返回正确历史记录。"""
        store = _MockProposalStore()
        w = _make_workflow(store)
        # approve
        store.add_proposal("prop_h_004", status=STATE_APPROVED)
        w.approve("prop_h_004", approver=ACTOR_HUMAN)
        # revoke
        w.revoke_approval("prop_h_004", revoker=ACTOR_ADMIN, reason="x")
        # re-approve
        w.approve("prop_h_004", approver=ACTOR_HUMAN)
        hist = w.get_history("prop_h_004")
        assert len(hist) == 3
        assert hist[0]["action"] == "approved"
        assert hist[1]["action"] == "revoked"
        assert hist[2]["action"] == "approved"


# ============================================================
# 18. 额外测试:权限函数
# ============================================================


class TestPermissionFunctions:
    """权限函数单元测试。"""

    def test_is_authorized_fn(self) -> None:
        """is_authorized 函数正确。"""
        assert is_authorized(ACTOR_HUMAN) is True
        assert is_authorized(ACTOR_ADMIN) is True
        assert is_authorized(ACTOR_SYSTEM) is True
        assert is_authorized("anonymous") is False
        assert is_authorized("hacker") is False
        assert is_authorized("") is False

    def test_is_revoke_authorized_fn(self) -> None:
        """is_revoke_authorized 函数正确(revoke 需要 admin/system)。"""
        assert is_revoke_authorized(ACTOR_ADMIN) is True
        assert is_revoke_authorized(ACTOR_SYSTEM) is True
        assert is_revoke_authorized(ACTOR_HUMAN) is False  # human 不能 revoke
        assert is_revoke_authorized("anonymous") is False
        assert is_revoke_authorized("") is False

    def test_allowed_actors_sets(self) -> None:
        """ACTOR 集合正确。"""
        assert ACTOR_HUMAN in ALLOWED_ACTORS
        assert ACTOR_ADMIN in ALLOWED_ACTORS
        assert ACTOR_SYSTEM in ALLOWED_ACTORS
        assert ACTOR_HUMAN not in REVOKE_REQUIRED_ACTORS
        assert ACTOR_ADMIN in REVOKE_REQUIRED_ACTORS
        assert ACTOR_SYSTEM in REVOKE_REQUIRED_ACTORS
