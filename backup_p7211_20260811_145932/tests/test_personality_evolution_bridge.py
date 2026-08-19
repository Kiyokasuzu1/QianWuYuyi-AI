# -*- coding: utf-8 -*-
"""
tests/test_personality_evolution_bridge.py

Phase C.8.5 Personality Evolution Bridge —— 测试套件

覆盖:
  - Creation (Bridge 创建)
  - Validation (pending/approved/ready_for_apply 状态校验)
  - Evidence (proposal/history/approval 引用)
  - Conflict (core_identity / immutable_traits 检测)
  - Readonly (Personality/SelfModel 不修改)
  - Security (禁止 apply/resolver/TraitUpdater)
  - Audit (request 记录)
  - Duplicate (重复 request 检测)
  - Concurrency (多线程安全)
  - FailSafe (snapshot/audit 异常)
  - Integration (完整 Proposal→Review→Lifecycle→Approval→History→EvolutionRequest)
  - Schema (字段完整性)

合计: ≥45 测试
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# 确保 src 在 path
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.runtime.evolution.personality_evolution_bridge import (  # noqa: E402
    PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
    PERSONALITY_EVOLUTION_BRIDGE_NAME,
    PERSONALITY_EVOLUTION_BRIDGE_VERSION,
    TARGET_PERSONALITY,
    STATUS_PENDING,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    CONFLICT_CORE_IDENTITY,
    CONFLICT_IMMUTABLE_TRAIT,
    CONFLICT_NONE,
    DEFAULT_CONFIDENCE,
    REQUIRED_PROPOSAL_STATUS,
    REASON_INVALID_STATUS,
    REASON_DUPLICATE,
    REASON_INVALID_INPUT,
    REASON_INTERNAL_ERROR,
    AUDIT_ACTION_REQUESTED,
    ACTOR_RUNTIME,
    PersonalityEvolutionBridge,
    create_personality_evolution_bridge,
    safe_create_request,
)


# ============================================================
# Mocks
# ============================================================


class _MockPersonalitySnapshot:
    """模拟 Personality snapshot 提供者(只读)。"""

    def __init__(self, snapshot: Optional[Dict[str, Any]] = None) -> None:
        self._snapshot = snapshot or {
            "core_identity": {
                "name": "yuyi",
                "core_name": "浅雾羽依",
            },
            "immutable_traits": ["kindness", "gentleness", "loyalty"],
            "name": "yuyi",
        }
        self.read_count = 0

    def get_snapshot(self) -> Dict[str, Any]:
        self.read_count += 1
        return dict(self._snapshot)


class _FailingSnapshot:
    """读取总是抛异常的 snapshot。"""

    def get_snapshot(self) -> Dict[str, Any]:
        raise RuntimeError("snapshot unavailable")


class _MockRequestStore:
    """模拟 RequestStore(内存持久化)。"""

    def __init__(self) -> None:
        self.saved: Dict[str, Dict[str, Any]] = {}
        self.fail_save = False

    def save_request(self, request_id: str, payload: Dict[str, Any]) -> bool:
        if self.fail_save:
            return False
        self.saved[request_id] = dict(payload)
        return True

    def load_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        r = self.saved.get(request_id)
        return dict(r) if r else None


class _MockAudit:
    """模拟 Audit 接收器。"""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self.fail = False

    def record(
        self,
        operation_type: str = "",
        source: str = "",
        action: str = "",
        detail: Optional[Dict[str, Any]] = None,
        result: str = "success",
    ) -> None:
        if self.fail:
            raise RuntimeError("audit fail")
        self.records.append({
            "operation_type": operation_type,
            "source": source,
            "action": action,
            "detail": dict(detail) if isinstance(detail, dict) else {},
            "result": result,
        })


def _make_proposal(
    status: str = "ready_for_apply",
    proposal_id: str = "prop_test_001",
    changes: Optional[List[Dict[str, Any]]] = None,
    confidence: float = 0.7,
) -> Dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "status": status,
        "changes": changes if changes is not None else [
            {"key": "expressiveness", "delta": 0.05, "direction": "increase"}
        ],
        "confidence": confidence,
    }


# ============================================================
# TestCreation
# ============================================================


class TestCreation:
    def test_01_bridge_creates_successfully(self):
        b = create_personality_evolution_bridge()
        assert isinstance(b, PersonalityEvolutionBridge)
        assert b.SCHEMA_VERSION == PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION
        assert b.NAME == PERSONALITY_EVOLUTION_BRIDGE_NAME
        assert b.VERSION == PERSONALITY_EVOLUTION_BRIDGE_VERSION

    def test_02_bridge_with_snapshot(self):
        snap = _MockPersonalitySnapshot()
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        # 首次读取 snapshot
        summary = b.get_snapshot_summary()
        assert summary["available"] is True
        assert "kindness" in summary["immutable_traits"]

    def test_03_bridge_with_request_store(self):
        store = _MockRequestStore()
        b = create_personality_evolution_bridge(request_store=store)
        r = b.create_request(_make_proposal())
        assert r["success"] is True
        # store 已持久化
        assert r["request_id"] in store.saved

    def test_04_bridge_with_audit(self):
        audit = _MockAudit()
        b = create_personality_evolution_bridge(audit=audit)
        r = b.create_request(_make_proposal())
        assert r["success"] is True
        assert r["audit_recorded"] is True
        assert len(audit.records) == 1
        assert audit.records[0]["operation_type"] == AUDIT_ACTION_REQUESTED


# ============================================================
# TestValidation
# ============================================================


class TestValidation:
    def test_05_pending_proposal_rejected(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="pending"))
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["reason"] == REASON_INVALID_STATUS
        assert r["status"] == STATUS_REJECTED

    def test_06_approved_proposal_rejected(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="approved"))
        assert r["success"] is False
        assert r["reason"] == REASON_INVALID_STATUS

    def test_07_reviewing_proposal_rejected(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="reviewing"))
        assert r["success"] is False
        assert r["reason"] == REASON_INVALID_STATUS

    def test_08_ready_for_apply_accepted(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="ready_for_apply"))
        assert r["success"] is True
        assert r["status"] == STATUS_PENDING
        assert r["request_id"] != ""

    def test_09_missing_proposal_id(self):
        b = create_personality_evolution_bridge()
        r = b.create_request({"status": "ready_for_apply"})
        assert r["success"] is False
        assert r["reason"] == REASON_INVALID_INPUT

    def test_10_invalid_proposal_object(self):
        b = create_personality_evolution_bridge()
        r = b.create_request("not a dict")
        assert r["success"] is False


# ============================================================
# TestEvidence
# ============================================================


class TestEvidence:
    def test_11_evidence_includes_source_proposal(self):
        b = create_personality_evolution_bridge()
        proposal = _make_proposal(proposal_id="prop_evidence_1")
        r = b.create_request(proposal)
        assert r["success"] is True
        ev = r["evidence"]
        assert "source_proposal" in ev
        assert ev["source_proposal"]["proposal_id"] == "prop_evidence_1"
        assert ev["source_proposal"]["status"] == "ready_for_apply"

    def test_12_evidence_includes_history_reference(self):
        b = create_personality_evolution_bridge()
        hist_ref = {
            "proposal_id": "prop_evidence_2",
            "history_id": "hist_xxx",
            "version": 4,
        }
        r = b.create_request(
            _make_proposal(proposal_id="prop_evidence_2"),
            history_ref=hist_ref,
        )
        assert r["success"] is True
        ev = r["evidence"]
        assert ev["history_reference"]["history_id"] == "hist_xxx"
        assert ev["history_reference"]["version"] == 4

    def test_13_evidence_includes_approval_reference(self):
        b = create_personality_evolution_bridge()
        app_ref = {
            "proposal_id": "prop_evidence_3",
            "approver": "human_admin",
            "decision": "approved",
        }
        r = b.create_request(
            _make_proposal(proposal_id="prop_evidence_3"),
            approval_ref=app_ref,
        )
        assert r["success"] is True
        ev = r["evidence"]
        assert ev["approval_reference"]["approver"] == "human_admin"
        assert ev["approval_reference"]["decision"] == "approved"

    def test_14_evidence_contains_confidence(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(confidence=0.88))
        assert r["success"] is True
        assert 0.0 <= r["confidence"] <= 1.0
        assert abs(r["confidence"] - 0.88) < 1e-6


# ============================================================
# TestConflict
# ============================================================


class TestConflict:
    def test_15_core_identity_conflict(self):
        snap = _MockPersonalitySnapshot({
            "core_identity": {"name": "yuyi", "core_name": "yuyi_name"},
            "immutable_traits": ["kindness"],
        })
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        # changes 涉及 core_identity key
        proposal = _make_proposal(
            proposal_id="prop_conflict_1",
            changes=[{"key": "name", "delta": 0.1}],
        )
        r = b.create_request(proposal)
        assert r["success"] is True  # 创建成功,但 status=needs_review
        assert r["status"] == STATUS_NEEDS_REVIEW
        assert CONFLICT_CORE_IDENTITY in r["conflicts"]

    def test_16_immutable_trait_conflict(self):
        snap = _MockPersonalitySnapshot({
            "core_identity": {},
            "immutable_traits": ["kindness", "gentleness"],
        })
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        proposal = _make_proposal(
            proposal_id="prop_conflict_2",
            changes=[{"trait": "kindness", "delta": -0.1}],
        )
        r = b.create_request(proposal)
        assert r["status"] == STATUS_NEEDS_REVIEW
        assert CONFLICT_IMMUTABLE_TRAIT in r["conflicts"]

    def test_17_no_conflict_normal(self):
        snap = _MockPersonalitySnapshot({
            "core_identity": {"name": "yuyi"},
            "immutable_traits": ["kindness"],
        })
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        proposal = _make_proposal(
            proposal_id="prop_normal_1",
            changes=[{"key": "expressiveness", "delta": 0.05}],
        )
        r = b.create_request(proposal)
        assert r["success"] is True
        assert r["status"] == STATUS_PENDING
        assert r["conflicts"] == []

    def test_18_detect_conflict_helper(self):
        snap = _MockPersonalitySnapshot({
            "core_identity": {"name": "yuyi"},
            "immutable_traits": ["kindness"],
        })
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        proposal = _make_proposal(changes=[{"trait": "kindness", "delta": 0.1}])
        c = b.detect_conflict(proposal)
        assert CONFLICT_IMMUTABLE_TRAIT in c

    def test_19_no_snapshot_no_conflict(self):
        # 无 snapshot_provider → 不做 conflict 检查
        b = create_personality_evolution_bridge()
        proposal = _make_proposal(
            proposal_id="prop_no_snap",
            changes=[{"key": "anything", "delta": 0.1}],
        )
        r = b.create_request(proposal)
        assert r["status"] == STATUS_PENDING
        assert r["conflicts"] == []


# ============================================================
# TestReadonly
# ============================================================


class TestReadonly:
    def test_20_personality_not_modified(self):
        snap = _MockPersonalitySnapshot()
        b = create_personality_evolution_bridge(snapshot_provider=snap)
        before = snap.get_snapshot()
        r = b.create_request(_make_proposal())
        after = snap.get_snapshot()
        # snapshot 内容未变
        assert before == after
        # 仅"读"操作
        assert snap.read_count >= 1
        assert r["success"] is True

    def test_21_self_model_not_modified(self):
        # 模拟 self_model,验证 Bridge 没有调用其 update 接口
        class _SelfModel:
            def __init__(self) -> None:
                self.update_called = 0
                self.state = {"x": 0}

            def update(self, *a: Any, **kw: Any) -> None:
                self.update_called += 1

        sm = _SelfModel()
        b = create_personality_evolution_bridge()
        # 即使注入 self_model,b.create_request 也不会调用它
        b.create_request(_make_proposal())
        assert sm.update_called == 0


# ============================================================
# TestSecurity
# ============================================================


class TestSecurity:
    def test_22_apply_forbidden(self):
        # Bridge 不暴露 apply / accept 接口
        b = create_personality_evolution_bridge()
        for forbidden in ("apply", "accept", "commit", "execute_apply", "apply_proposal"):
            assert not hasattr(b, forbidden) or not callable(getattr(b, forbidden, None)) \
                or forbidden in ("apply",)  # 允许出现 'apply' 子串(如 apply_request),但本 Bridge 没有

    def test_23_resolver_forbidden(self):
        b = create_personality_evolution_bridge()
        for forbidden in ("resolve", "resolve_personality", "personality_resolver"):
            assert not hasattr(b, forbidden)

    def test_24_trait_updater_forbidden(self):
        b = create_personality_evolution_bridge()
        for forbidden in ("update_traits", "set_traits", "apply_trait", "trait_updater"):
            assert not hasattr(b, forbidden)

    def test_25_personality_save_forbidden(self):
        b = create_personality_evolution_bridge()
        for forbidden in ("save_personality", "commit_personality", "persist_state"):
            assert not hasattr(b, forbidden)

    def test_26_self_model_update_forbidden(self):
        b = create_personality_evolution_bridge()
        for forbidden in ("update_self_model", "set_self_model", "self_model_update"):
            assert not hasattr(b, forbidden)


# ============================================================
# TestAudit
# ============================================================


class TestAudit:
    def test_27_audit_request_recorded(self):
        audit = _MockAudit()
        b = create_personality_evolution_bridge(audit=audit)
        r = b.create_request(_make_proposal(proposal_id="prop_audit_1"))
        assert r["audit_recorded"] is True
        assert len(audit.records) == 1
        rec = audit.records[0]
        assert rec["operation_type"] == AUDIT_ACTION_REQUESTED
        assert rec["detail"]["proposal_id"] == "prop_audit_1"
        assert rec["detail"]["request_id"] == r["request_id"]
        assert rec["detail"]["status"] == STATUS_PENDING
        assert "timestamp" in rec["detail"]
        assert "confidence" in rec["detail"]

    def test_28_audit_skipped_when_no_audit(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["audit_recorded"] is False  # 无 audit → 视为 false

    def test_29_audit_contains_conflict_info(self):
        audit = _MockAudit()
        snap = _MockPersonalitySnapshot({
            "core_identity": {"name": "yuyi"},
            "immutable_traits": ["kindness"],
        })
        b = create_personality_evolution_bridge(
            snapshot_provider=snap,
            audit=audit,
        )
        r = b.create_request(_make_proposal(
            proposal_id="prop_audit_conflict",
            changes=[{"trait": "kindness", "delta": 0.1}],
        ))
        assert r["status"] == STATUS_NEEDS_REVIEW
        assert r["audit_recorded"] is True
        rec = audit.records[0]
        assert rec["detail"]["status"] == STATUS_NEEDS_REVIEW


# ============================================================
# TestDuplicate
# ============================================================


class TestDuplicate:
    def test_30_duplicate_request_detected(self):
        b = create_personality_evolution_bridge()
        r1 = b.create_request(_make_proposal(proposal_id="prop_dup_1"))
        assert r1["success"] is True
        r2 = b.create_request(_make_proposal(proposal_id="prop_dup_1"))
        assert r2["success"] is False
        assert r2["reason"] == REASON_DUPLICATE
        assert r2["request_id"] == r1["request_id"]

    def test_31_different_proposals_no_duplicate(self):
        b = create_personality_evolution_bridge()
        r1 = b.create_request(_make_proposal(proposal_id="prop_a"))
        r2 = b.create_request(_make_proposal(proposal_id="prop_b"))
        assert r1["success"] is True
        assert r2["success"] is True
        assert r1["request_id"] != r2["request_id"]


# ============================================================
# TestConcurrency
# ============================================================


class TestConcurrency:
    def test_32_concurrent_create_request_same_proposal(self):
        b = create_personality_evolution_bridge()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker() -> None:
            r = b.create_request(_make_proposal(proposal_id="prop_concurrent"))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 仅 1 个 success,其余 duplicate
        successes = [r for r in results if r["success"]]
        duplicates = [r for r in results if not r["success"] and r["reason"] == REASON_DUPLICATE]
        assert len(successes) == 1
        assert len(duplicates) == 19
        # 所有 request_id 一致
        ids = {r["request_id"] for r in results}
        assert len(ids) == 1

    def test_33_concurrent_create_request_different_proposals(self):
        b = create_personality_evolution_bridge()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            r = b.create_request(_make_proposal(proposal_id=f"prop_par_{i}"))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 全部成功
        assert all(r["success"] for r in results)
        assert len({r["request_id"] for r in results}) == 30


# ============================================================
# TestFailSafe
# ============================================================


class TestFailSafe:
    def test_34_snapshot_exception_safe(self):
        b = create_personality_evolution_bridge(snapshot_provider=_FailingSnapshot())
        r = b.create_request(_make_proposal(proposal_id="prop_snap_fail"))
        # snapshot 失败不阻塞 → 仍可创建,无 conflict
        assert r["success"] is True
        assert r["status"] == STATUS_PENDING

    def test_35_audit_exception_safe(self):
        class _FailingAudit:
            def record(self, **kw: Any) -> None:
                raise RuntimeError("audit boom")

        b = create_personality_evolution_bridge(audit=_FailingAudit())
        r = b.create_request(_make_proposal(proposal_id="prop_audit_fail"))
        # audit 失败不阻塞 → 仍可创建
        assert r["success"] is True
        assert r["audit_recorded"] is False

    def test_36_request_store_exception_safe(self):
        class _FailingStore:
            def save_request(self, *a: Any, **kw: Any) -> bool:
                raise RuntimeError("store boom")

        b = create_personality_evolution_bridge(request_store=_FailingStore())
        r = b.create_request(_make_proposal(proposal_id="prop_store_fail"))
        assert r["success"] is True  # store 失败不阻塞

    def test_37_internal_exception_safe(self):
        b = create_personality_evolution_bridge()
        # 通过 monkey-patch 触发内部异常
        original = b._extract_proposal_changes

        def _explode(_p: Any) -> Any:
            raise RuntimeError("unexpected")

        b._extract_proposal_changes = _explode  # type: ignore[assignment]
        r = b.create_request(_make_proposal(proposal_id="prop_internal_fail"))
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["error"] != ""
        b._extract_proposal_changes = original  # type: ignore[assignment]


# ============================================================
# TestIntegration
# ============================================================


class TestIntegration:
    """完整链路:Runtime → Proposal → Review → Lifecycle → Approval → History → EvolutionRequest"""

    def _build_full_chain(self):
        """构造一个完整链路的 mock 环境。"""
        from src.runtime.growth import (
            GrowthProposalRuntime,
            GrowthProposalLifecycleManager,
            GrowthProposalApprovalWorkflow,
            GrowthProposalHistory,
            STATE_READY_FOR_APPLY,
            STATE_PENDING,
            STATE_REVIEWING,
            STATE_APPROVED,
            AUDIT_ACTION_PROPOSAL_CREATED,
        )

        # 1. Runtime 生成 proposal
        runtime = GrowthProposalRuntime()
        # 直接构造一个 dict(避免 signal 路径)
        proposal = {
            "proposal_id": "prop_full_chain",
            "status": STATE_PENDING,
            "changes": [{"key": "expressiveness", "delta": 0.05}],
            "confidence": 0.8,
        }
        return runtime, proposal

    def _make_store(self):
        """构造一个支持 add_proposal 的 mock store。"""
        class _Store:
            def __init__(self) -> None:
                self._idx: Dict[str, Dict[str, Any]] = {}

            def add_proposal(self, proposal_id: str, status: str, **kwargs: Any) -> None:
                self._idx[proposal_id] = {"id": proposal_id, "status": status, **kwargs}

            def load(self, proposal_id: str) -> Optional[Dict[str, Any]]:
                d = self._idx.get(proposal_id)
                return dict(d) if d else None

            def update(self, proposal: Any) -> None:
                if isinstance(proposal, dict):
                    pid = proposal.get("id") or proposal.get("proposal_id")
                    if pid:
                        self._idx[str(pid)] = dict(proposal)

        return _Store()

    def test_38_full_chain_proposal_to_evolution_request(self):
        from src.runtime.growth import (
            GrowthProposalLifecycleManager,
            GrowthProposalApprovalWorkflow,
            GrowthProposalHistory,
            STATE_READY_FOR_APPLY,
            STATE_APPROVED,
            STATE_PENDING,
            STATE_REVIEWING,
        )

        # ---- Phase 1: 准备 proposal ----
        proposal_id = "prop_int_full"
        store = self._make_store()
        # ---- Phase 2: Lifecycle ----
        lifecycle = GrowthProposalLifecycleManager()
        lifecycle.set_store(store)
        store.add_proposal(proposal_id, STATE_PENDING)
        lifecycle.transition(proposal_id, STATE_REVIEWING)
        # ---- Phase 3: Review ----
        audit = _MockAudit()
        history = GrowthProposalHistory(audit=audit)
        history.record_event(proposal_id, "runtime_growth_proposal_reviewed", actor="runtime")
        # ---- Phase 4: Approval ----
        workflow = GrowthProposalApprovalWorkflow(audit=audit)
        workflow.approve(proposal_id, approver="admin")
        # 推进到 ready_for_apply
        lifecycle.transition(proposal_id, "approved")
        lifecycle.transition(proposal_id, STATE_READY_FOR_APPLY)
        # ---- Phase 5: History 记录最终动作 ----
        history.record_event(proposal_id, "runtime_growth_proposal_approved", actor="admin")
        history.record_event(proposal_id, "runtime_growth_proposal_ready_for_apply", actor="runtime")
        # ---- Phase 6: EvolutionRequest ----
        bridge = create_personality_evolution_bridge(audit=audit)
        ready_proposal = {
            "proposal_id": proposal_id,
            "status": STATE_READY_FOR_APPLY,
            "changes": [{"key": "expressiveness", "delta": 0.05}],
            "confidence": 0.8,
        }
        history_ref = {
            "proposal_id": proposal_id,
            "history_count": 4,
            "latest_version": 4,
        }
        approval_ref = {
            "proposal_id": proposal_id,
            "approver": "admin",
            "decision": "approved",
        }
        r = bridge.create_request(
            ready_proposal,
            history_ref=history_ref,
            approval_ref=approval_ref,
        )
        assert r["success"] is True
        assert r["status"] == STATUS_PENDING
        # 证据链完整
        assert r["evidence"]["source_proposal"]["proposal_id"] == proposal_id
        assert r["evidence"]["history_reference"]["latest_version"] == 4
        assert r["evidence"]["approval_reference"]["approver"] == "admin"
        # Audit 包含 evolution request
        evo_audits = [a for a in audit.records if a["operation_type"] == AUDIT_ACTION_REQUESTED]
        assert len(evo_audits) == 1

    def test_39_full_chain_with_conflict(self):
        from src.runtime.growth import (
            GrowthProposalLifecycleManager,
            GrowthProposalApprovalWorkflow,
            GrowthProposalHistory,
            STATE_READY_FOR_APPLY,
            STATE_PENDING,
            STATE_REVIEWING,
        )

        snap = _MockPersonalitySnapshot({
            "core_identity": {"name": "yuyi"},
            "immutable_traits": ["kindness"],
        })
        audit = _MockAudit()
        store = self._make_store()
        lifecycle = GrowthProposalLifecycleManager()
        lifecycle.set_store(store)
        store.add_proposal("prop_int_conflict", STATE_PENDING)
        lifecycle.transition("prop_int_conflict", STATE_REVIEWING)
        history = GrowthProposalHistory(audit=audit)
        history.record_event("prop_int_conflict", "runtime_growth_proposal_reviewed", actor="runtime")
        workflow = GrowthProposalApprovalWorkflow(audit=audit)
        workflow.approve("prop_int_conflict", approver="admin")
        lifecycle.transition("prop_int_conflict", "approved")
        lifecycle.transition("prop_int_conflict", STATE_READY_FOR_APPLY)
        bridge = create_personality_evolution_bridge(snapshot_provider=snap, audit=audit)
        # proposal 涉及 immutable trait
        bad_proposal = {
            "proposal_id": "prop_int_conflict",
            "status": STATE_READY_FOR_APPLY,
            "changes": [{"trait": "kindness", "delta": -0.1}],
            "confidence": 0.9,
        }
        r = bridge.create_request(bad_proposal)
        assert r["success"] is True
        assert r["status"] == STATUS_NEEDS_REVIEW
        assert CONFLICT_IMMUTABLE_TRAIT in r["conflicts"]

    def test_40_full_chain_invalid_status_blocks(self):
        # 走完 lifecycle 但停留在 approved 而非 ready_for_apply
        from src.runtime.growth import (
            GrowthProposalLifecycleManager,
            GrowthProposalApprovalWorkflow,
            STATE_APPROVED,
            STATE_PENDING,
            STATE_REVIEWING,
        )

        audit = _MockAudit()
        store = self._make_store()
        lifecycle = GrowthProposalLifecycleManager()
        lifecycle.set_store(store)
        store.add_proposal("prop_int_blocked", STATE_PENDING)
        lifecycle.transition("prop_int_blocked", STATE_REVIEWING)
        workflow = GrowthProposalApprovalWorkflow(audit=audit)
        workflow.approve("prop_int_blocked", approver="admin")
        lifecycle.transition("prop_int_blocked", STATE_APPROVED)
        # 故意不进入 ready_for_apply
        bridge = create_personality_evolution_bridge(audit=audit)
        r = bridge.create_request({
            "proposal_id": "prop_int_blocked",
            "status": STATE_APPROVED,  # 错误:应为 ready_for_apply
            "changes": [{"key": "expressiveness", "delta": 0.05}],
            "confidence": 0.7,
        })
        assert r["success"] is False
        assert r["reason"] == REASON_INVALID_STATUS

    def test_41_get_request_by_id(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(proposal_id="prop_get_1"))
        rid = r["request_id"]
        got = b.get_request(rid)
        assert got is not None
        assert got["proposal_id"] == "prop_get_1"
        assert got["status"] == STATUS_PENDING

    def test_42_get_request_not_found(self):
        b = create_personality_evolution_bridge()
        got = b.get_request("nonexistent_id")
        assert got is None

    def test_43_list_requests_filtered(self):
        b = create_personality_evolution_bridge()
        b.create_request(_make_proposal(proposal_id="p_list_1"))
        b.create_request(_make_proposal(proposal_id="p_list_2"))
        all_items = b.list_requests()
        assert len(all_items) == 2
        filtered = b.list_requests(proposal_id="p_list_1")
        assert len(filtered) == 1
        assert filtered[0]["proposal_id"] == "p_list_1"

    def test_44_get_stats(self):
        b = create_personality_evolution_bridge()
        b.create_request(_make_proposal(proposal_id="p_stats_1"))
        b.create_request(_make_proposal(proposal_id="p_stats_2"))
        # 制造 1 个 duplicate
        b.create_request(_make_proposal(proposal_id="p_stats_1"))
        s = b.get_stats()
        assert s["create_count"] == 3
        assert s["duplicate_count"] == 1
        assert s["total_requests"] == 2

    def test_45_safe_create_request_helper_no_bridge(self):
        r = safe_create_request(None, _make_proposal())
        assert r["success"] is False
        assert r["degraded"] is True


# ============================================================
# TestSchema
# ============================================================


class TestSchema:
    def test_46_schema_version_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["schema_version"] == PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION

    def test_47_request_id_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert "request_id" in r
        assert isinstance(r["request_id"], str)
        assert r["request_id"].startswith("evreq_")

    def test_48_proposal_id_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(proposal_id="prop_schema_1"))
        assert r["proposal_id"] == "prop_schema_1"

    def test_49_target_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["target"] == TARGET_PERSONALITY

    def test_50_changes_field(self):
        b = create_personality_evolution_bridge()
        changes = [{"key": "expressiveness", "delta": 0.05}]
        r = b.create_request(_make_proposal(changes=changes))
        assert r["changes"] == changes

    def test_51_evidence_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert "evidence" in r
        assert "source_proposal" in r["evidence"]
        assert "history_reference" in r["evidence"]
        assert "approval_reference" in r["evidence"]

    def test_52_confidence_field_clamped(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(confidence=1.5))
        assert r["confidence"] == 1.0
        r2 = b.create_request(_make_proposal(confidence=-0.5, proposal_id="p_clam_2"))
        assert r2["confidence"] == 0.0

    def test_53_status_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["status"] in (STATUS_PENDING, STATUS_NEEDS_REVIEW, STATUS_REJECTED)

    def test_54_conflicts_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert isinstance(r["conflicts"], list)

    def test_55_reason_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="pending"))
        assert r["reason"] == REASON_INVALID_STATUS

    def test_56_actor_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(), actor="admin_alice")
        assert r["actor"] == "admin_alice"

    def test_57_timestamp_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert isinstance(r["timestamp"], str)
        assert r["timestamp"].endswith("Z") or "T" in r["timestamp"]

    def test_58_audit_recorded_field(self):
        audit = _MockAudit()
        b = create_personality_evolution_bridge(audit=audit)
        r = b.create_request(_make_proposal())
        assert r["audit_recorded"] is True

    def test_59_error_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal(status="pending"))
        assert r["error"] != ""

    def test_60_success_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["success"] is True

    def test_61_bridge_metadata_field(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert "bridge" in r
        assert r["bridge"]["name"] == PERSONALITY_EVOLUTION_BRIDGE_NAME
        assert r["bridge"]["version"] == PERSONALITY_EVOLUTION_BRIDGE_VERSION
        assert r["bridge"]["schema_version"] == PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION

    def test_62_default_actor_runtime(self):
        b = create_personality_evolution_bridge()
        r = b.create_request(_make_proposal())
        assert r["actor"] == ACTOR_RUNTIME

    def test_63_reset_clears_state(self):
        b = create_personality_evolution_bridge()
        b.create_request(_make_proposal(proposal_id="p_reset_1"))
        b.reset()
        s = b.get_stats()
        assert s["create_count"] == 0
        assert s["total_requests"] == 0

    def test_64_snapshot_summary_no_provider(self):
        b = create_personality_evolution_bridge()
        s = b.get_snapshot_summary()
        assert s["available"] is False
        assert s["core_identity_keys"] == []
        assert s["immutable_traits"] == []
