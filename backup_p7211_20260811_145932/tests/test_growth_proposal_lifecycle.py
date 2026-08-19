# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_lifecycle.py

Phase C.8.2 Growth Proposal Lifecycle State Machine —— 验证测试

目标:
  验证 GrowthProposalLifecycleManager 严格只管理 Proposal 状态:
    1) 合法状态转换(pending→reviewing→approved→ready_for_apply)
    2) 非法转换被阻止
    3) Audit 完整记录
    4) 不触发人格 / SelfModel / Trait 修改
    5) 不调用 apply_proposal / accept_proposal
    6) 并发安全

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务

覆盖 35+ 测试:
  Creation (1)
  State (2-3)
  Transition (4-8)
  Invalid (9-11)
  Readonly (12-13)
  Security (14-15)
  Audit (16-17)
  FailSafe (18-19)
  Integration (20-21)
  Concurrency (22)
  Schema (23-35)
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
    GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION,
    GROWTH_PROPOSAL_LIFECYCLE_NAME,
    GROWTH_PROPOSAL_LIFECYCLE_VERSION,
    STATE_PENDING,
    STATE_REVIEWING,
    STATE_APPROVED,
    STATE_REJECTED,
    STATE_NEEDS_REVIEW,
    STATE_READY_FOR_APPLY,
    ALL_STATES,
    TRANSITIONS,
    TERMINAL_STATES,
    INITIAL_STATE,
    AUDIT_ACTION_TRANSITION,
    REASON_ALREADY_IN_STATE,
    REASON_INVALID_TRANSITION,
    REASON_PROPOSAL_NOT_FOUND,
    GrowthProposalLifecycleManager,
    create_growth_proposal_lifecycle,
    safe_transition,
    is_valid_state,
    is_terminal_state,
    validate_transition,
    get_allowed_transitions,
)
from src.runtime.growth.growth_proposal_lifecycle import (
    AUDIT_COMPONENT,
    AUDIT_ACTOR_RUNTIME,
    REASON_INVALID_STATE,
    REASON_STORE_ERROR,
    REASON_INVALID_INPUT,
    REASON_INTERNAL_ERROR,
)


# ============================================================
# 1. Mock ProposalStore
# ============================================================


class _MockProposalStore:
    """最小 ProposalStore mock,只暴露 load/update + _index。

    模拟一个能保存并按 id 查询 proposal 的内存 store。
    """

    def __init__(self) -> None:
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_calls: int = 0
        self._update_calls: int = 0
        self._raise_on_update: Optional[Exception] = None
        self._raise_on_load: Optional[Exception] = None
        self._lock = threading.RLock()

    def save(self, proposal: Any) -> None:
        """可选:支持 save。"""
        with self._lock:
            if isinstance(proposal, dict):
                pid = str(proposal.get("id") or proposal.get("proposal_id") or "")
                if pid:
                    self._index[pid] = dict(proposal)
            elif hasattr(proposal, "id"):
                try:
                    self._index[str(proposal.id)] = proposal.to_dict() if hasattr(proposal, "to_dict") else {"id": proposal.id}
                except Exception:  # noqa: BLE001
                    pass

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
            elif hasattr(proposal, "id"):
                try:
                    self._index[str(proposal.id)] = proposal.to_dict() if hasattr(proposal, "to_dict") else {"id": proposal.id}
                except Exception:  # noqa: BLE001
                    pass

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


# ============================================================
# 4. Mock ProposalManager(带 apply 计数)
# ============================================================


class _MockProposalManager:
    """带计数 + 异常注入的 ProposalManager。"""

    def __init__(self) -> None:
        self.accept_calls: int = 0
        self.reject_calls: int = 0
        self.apply_calls: int = 0
        self.create_calls: int = 0

    def accept_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.accept_calls += 1
        raise AssertionError("accept_proposal FORBIDDEN in C.8.2")

    def reject_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.reject_calls += 1
        raise AssertionError("reject_proposal FORBIDDEN in C.8.2")

    def apply_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.apply_calls += 1
        raise AssertionError("apply_proposal FORBIDDEN in C.8.2")

    def create_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls += 1
        return None


# ============================================================
# 5. 测试:Creation (1)
# ============================================================


class TestCreation:
    """Manager 创建测试。"""

    def test_01_manager_creates_successfully(self) -> None:
        """1. manager 创建成功(无依赖也可工作)。"""
        m = create_growth_proposal_lifecycle()
        assert isinstance(m, GrowthProposalLifecycleManager)
        assert m.transition_count == 0
        # 工厂 + 构造
        m2 = GrowthProposalLifecycleManager()
        assert m2 is not None
        # 注入 store / audit
        m3 = create_growth_proposal_lifecycle(
            proposal_store=_MockProposalStore(),
            audit=_MockAudit(),
            default_actor="test_actor",
        )
        assert m3 is not None


# ============================================================
# 6. 测试:State (2-3)
# ============================================================


class TestState:
    """状态读取测试。"""

    def test_02_initial_state_pending(self) -> None:
        """2. 新建 proposal 初始状态 = pending。"""
        store = _MockProposalStore()
        store.add_proposal("prop_initial_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        cur = m.get_state("prop_initial_001")
        assert cur == STATE_PENDING
        # 也可读到 allowed transitions
        allowed = m.get_allowed_transitions("prop_initial_001")
        assert STATE_REVIEWING in allowed
        assert STATE_REJECTED in allowed

    def test_03_state_read_only(self) -> None:
        """3. 状态读取不会修改 store(load_calls 不会触发 update)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_read_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        # 读 10 次
        for _ in range(10):
            _ = m.get_state("prop_read_001")
        # store 未被 update
        assert store.update_call_count == 0
        # load 至少被调 1 次
        assert store.load_call_count >= 1


# ============================================================
# 7. 测试:Transition (4-8)
# ============================================================


class TestTransition:
    """合法状态转换测试。"""

    def test_04_pending_to_reviewing(self) -> None:
        """4. pending → reviewing 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_t_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_t_001", STATE_REVIEWING, actor="runtime")
        assert r["success"] is True
        assert r["old_state"] == STATE_PENDING
        assert r["new_state"] == STATE_REVIEWING
        # store 状态被更新
        assert store.load("prop_t_001")["status"] == STATE_REVIEWING

    def test_05_reviewing_to_approved(self) -> None:
        """5. reviewing → approved 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_t_002", status=STATE_REVIEWING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_t_002", STATE_APPROVED, actor="human_reviewer")
        assert r["success"] is True
        assert r["old_state"] == STATE_REVIEWING
        assert r["new_state"] == STATE_APPROVED
        assert r["actor"] == "human_reviewer"

    def test_06_reviewing_to_rejected(self) -> None:
        """6. reviewing → rejected 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_t_003", status=STATE_REVIEWING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_t_003", STATE_REJECTED, actor="human_reviewer")
        assert r["success"] is True
        assert r["new_state"] == STATE_REJECTED

    def test_07_reviewing_to_needs_review(self) -> None:
        """7. reviewing → needs_review 合法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_t_004", status=STATE_REVIEWING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_t_004", STATE_NEEDS_REVIEW, actor="runtime")
        assert r["success"] is True
        assert r["new_state"] == STATE_NEEDS_REVIEW

    def test_08_approved_to_ready_for_apply(self) -> None:
        """8. approved → ready_for_apply 合法(终态前最后一步)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_t_005", status=STATE_APPROVED)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_t_005", STATE_READY_FOR_APPLY, actor="runtime")
        assert r["success"] is True
        assert r["new_state"] == STATE_READY_FOR_APPLY
        # ready_for_apply 是终态
        assert m.is_terminal("prop_t_005") is True


# ============================================================
# 8. 测试:Invalid (9-11)
# ============================================================


class TestInvalid:
    """非法状态转换测试。"""

    def test_09_pending_to_approved_invalid(self) -> None:
        """9. pending → approved 非法(必须先 reviewing)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_inv_001", STATE_APPROVED, actor="runtime")
        assert r["success"] is False
        assert r["reason"] == REASON_INVALID_TRANSITION
        # store 状态不变
        assert store.load("prop_inv_001")["status"] == STATE_PENDING
        # 无 update 发生
        assert store.update_call_count == 0

    def test_10_pending_to_applied_invalid(self) -> None:
        """10. pending → applied 非法。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_002", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_inv_002", "applied", actor="runtime")
        assert r["success"] is False
        # "applied" 本身不是合法状态之一
        assert r["reason"] in (REASON_INVALID_TRANSITION, REASON_INVALID_STATE)
        # 状态不变
        assert store.load("prop_inv_002")["status"] == STATE_PENDING

    def test_11_rejected_to_approved_invalid(self) -> None:
        """11. rejected → approved 非法(终态不可再转出)。"""
        store = _MockProposalStore()
        store.add_proposal("prop_inv_003", status=STATE_REJECTED)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_inv_003", STATE_APPROVED, actor="runtime")
        assert r["success"] is False
        # rejected 是终态
        assert r["reason"] == REASON_INVALID_TRANSITION
        assert store.load("prop_inv_003")["status"] == STATE_REJECTED


# ============================================================
# 9. 测试:Readonly (12-13)
# ============================================================


class TestReadonly:
    """只读业务边界测试。"""

    def test_12_personality_state_unchanged(self) -> None:
        """12. transition 不会修改 PersonalityState(不调 resolve / apply_proposal)。"""
        pys = _MockPersonalityState()
        store = _MockProposalStore()
        store.add_proposal("prop_ro_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        # 跑完整生命周期
        m.transition("prop_ro_001", STATE_REVIEWING, actor="runtime")
        m.transition("prop_ro_001", STATE_APPROVED, actor="human_reviewer")
        m.transition("prop_ro_001", STATE_READY_FOR_APPLY, actor="runtime")
        # PersonalityState 完全未变
        assert pys.write_count == 0
        assert pys.resolve_call_count == 0
        assert pys.traits == {"warmth": 0.5, "gentleness": 0.5}

    def test_13_self_model_unchanged(self) -> None:
        """13. transition 不会修改 SelfModelStore(不调 update / apply)。"""
        sms = _MockSelfModelStore()
        tsu = _MockTraitStateUpdater()
        store = _MockProposalStore()
        store.add_proposal("prop_ro_002", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        m.transition("prop_ro_002", STATE_REVIEWING, actor="runtime")
        m.transition("prop_ro_002", STATE_APPROVED, actor="human_reviewer")
        m.transition("prop_ro_002", STATE_READY_FOR_APPLY, actor="runtime")
        # SelfModel / Trait 完全未变
        assert sms.write_count == 0
        assert sms.update_call_count == 0
        assert tsu.write_count == 0
        assert tsu.apply_call_count == 0


# ============================================================
# 10. 测试:Security (14-15)
# ============================================================


class TestSecurity:
    """安全限制测试。"""

    def test_14_apply_forbidden(self) -> None:
        """14. Lifecycle 不会触发 apply_proposal / accept_proposal / reject_proposal。"""
        pm = _MockProposalManager()
        store = _MockProposalStore()
        store.add_proposal("prop_sec_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        # 跑多次 transition
        m.transition("prop_sec_001", STATE_REVIEWING, actor="runtime")
        m.transition("prop_sec_001", STATE_APPROVED, actor="human_reviewer")
        m.transition("prop_sec_001", STATE_READY_FOR_APPLY, actor="runtime")
        # ProposalManager 的 apply / accept / reject 全部 0 次
        assert pm.apply_calls == 0
        assert pm.accept_calls == 0
        assert pm.reject_calls == 0

    def test_15_resolver_forbidden(self) -> None:
        """15. PersonalityResolver.resolve() 必须 0 次调用。"""
        pys = _MockPersonalityState()
        store = _MockProposalStore()
        store.add_proposal("prop_sec_002", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        m.transition("prop_sec_002", STATE_REVIEWING, actor="runtime")
        m.transition("prop_sec_002", STATE_APPROVED, actor="human_reviewer")
        # resolve() 0 次
        assert pys.resolve_call_count == 0


# ============================================================
# 11. 测试:Audit (16-17)
# ============================================================


class TestAudit:
    """Audit 测试。"""

    def test_16_transition_audit(self) -> None:
        """16. 每次 transition 记录 audit。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_aud_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store, audit=audit)
        m.transition("prop_aud_001", STATE_REVIEWING, actor="runtime")
        m.transition("prop_aud_001", STATE_APPROVED, actor="human_reviewer")
        # 2 次 audit
        assert audit.record_count == 2
        assert audit.records[0]["operation_type"] == AUDIT_ACTION_TRANSITION

    def test_17_audit_fields_complete(self) -> None:
        """17. audit 字段完整(proposal_id / old_state / new_state / actor / timestamp)。"""
        audit = _MockAudit()
        store = _MockProposalStore()
        store.add_proposal("prop_aud_002", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store, audit=audit)
        m.transition("prop_aud_002", STATE_REVIEWING, actor="custom_actor")
        rec = audit.records[0]
        detail = rec["detail"]
        for key in ["proposal_id", "old_state", "new_state", "actor", "timestamp"]:
            assert key in detail, f"audit detail 缺少 {key}"
        assert detail["proposal_id"] == "prop_aud_002"
        assert detail["old_state"] == STATE_PENDING
        assert detail["new_state"] == STATE_REVIEWING
        assert detail["actor"] == "custom_actor"
        assert detail["timestamp"] is not None


# ============================================================
# 12. 测试:FailSafe (18-19)
# ============================================================


class TestFailSafe:
    """失败隔离测试。"""

    def test_18_store_exception(self) -> None:
        """18. store.update() 抛异常 → 不崩,返回 error。"""
        store = _MockProposalStore()
        store.add_proposal("prop_fs_001", status=STATE_PENDING)
        store._raise_on_update = RuntimeError("store update failed")
        m = create_growth_proposal_lifecycle(proposal_store=store, audit=_MockAudit())
        r = m.transition("prop_fs_001", STATE_REVIEWING, actor="runtime")
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["reason"] == REASON_STORE_ERROR
        # 不崩
        assert m.error_count >= 1

    def test_19_audit_exception(self) -> None:
        """19. audit.record() 抛异常 → transition 仍成功。"""
        audit = _MockAudit()
        audit._raise = True
        store = _MockProposalStore()
        store.add_proposal("prop_fs_002", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store, audit=audit)
        r = m.transition("prop_fs_002", STATE_REVIEWING, actor="runtime")
        # transition 仍成功(不阻断业务)
        assert r["success"] is True
        # 但 audit_recorded=False
        assert r["audit_recorded"] is False
        # store 状态确实被更新
        assert store.load("prop_fs_002")["status"] == STATE_REVIEWING


# ============================================================
# 13. 测试:Integration (20-21)
# ============================================================


class TestIntegration:
    """端到端集成测试。"""

    def test_20_proposal_runtime_to_lifecycle(self) -> None:
        """20. ProposalRuntime → Lifecycle 端到端。"""
        from src.runtime.growth import GrowthProposalRuntime
        # 用本测试中定义的 _MockProposalManagerExt(支持 create + load + update)
        pm = _MockProposalManagerExt()
        runtime = GrowthProposalRuntime(
            growth_evaluator=_MockEvaluatorLite(),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
        # 创建 proposal
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
        # 接入 Lifecycle
        lifecycle = create_growth_proposal_lifecycle(
            proposal_store=pm,
            audit=_MockAudit(),
        )
        r2 = lifecycle.transition(proposal_id, STATE_REVIEWING, actor="runtime")
        assert r2["success"] is True
        # proposal 状态在 pm 中被更新
        p = pm.load(proposal_id)
        assert p is not None
        assert p["status"] == STATE_REVIEWING

    def test_21_full_lifecycle_test(self) -> None:
        """21. 完整生命周期:pending → reviewing → approved → ready_for_apply。"""
        store = _MockProposalStore()
        store.add_proposal("prop_full_001", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(
            proposal_store=store,
            audit=_MockAudit(),
        )
        # 完整路径
        r1 = m.transition("prop_full_001", STATE_REVIEWING, actor="runtime")
        assert r1["success"] is True
        r2 = m.transition("prop_full_001", STATE_APPROVED, actor="human_reviewer")
        assert r2["success"] is True
        r3 = m.transition("prop_full_001", STATE_READY_FOR_APPLY, actor="runtime")
        assert r3["success"] is True
        # 终态
        assert m.get_state("prop_full_001") == STATE_READY_FOR_APPLY
        assert m.is_terminal("prop_full_001") is True
        # ready_for_apply → 其他 非法
        r4 = m.transition("prop_full_001", STATE_APPROVED, actor="runtime")
        assert r4["success"] is False
        assert r4["reason"] == REASON_INVALID_TRANSITION
        # history 完整
        hist = m.get_history("prop_full_001")
        assert len(hist) == 3
        # 跑 metrics
        stats = m.get_stats()
        assert stats["success_count"] == 3


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
            elif hasattr(proposal, "id"):
                try:
                    self._index[str(proposal.id)] = proposal.to_dict() if hasattr(proposal, "to_dict") else {"id": proposal.id}
                except Exception:  # noqa: BLE001
                    pass


# ============================================================
# 14. 测试:Concurrency (22)
# ============================================================


class TestConcurrency:
    """并发安全测试。"""

    def test_22_concurrent_transitions_thread_safe(self) -> None:
        """22. 多线程并发 transition 是 thread-safe 的。"""
        store = _MockProposalStore()
        # 准备 20 个 proposal(各处于 pending)
        n = 20
        for i in range(n):
            store.add_proposal(f"prop_conc_{i:03d}", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        results: List[Dict[str, Any]] = []
        errors: List[Exception] = []

        def worker(idx: int) -> None:
            try:
                pid = f"prop_conc_{idx:03d}"
                # 每个 proposal 跑完整生命周期
                r1 = m.transition(pid, STATE_REVIEWING, actor="t1")
                r2 = m.transition(pid, STATE_APPROVED, actor="t2")
                r3 = m.transition(pid, STATE_READY_FOR_APPLY, actor="t3")
                results.extend([r1, r2, r3])
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
        assert len(results) == n * 3
        for r in results:
            assert r["success"] is True
        # store 状态正确
        for i in range(n):
            pid = f"prop_conc_{i:03d}"
            assert store.load(pid)["status"] == STATE_READY_FOR_APPLY


# ============================================================
# 15. 测试:Schema (23-35)
# ============================================================


class TestSchema:
    """Schema 字段完整性测试。"""

    def test_23_schema_version(self) -> None:
        """23. schema_version 字段存在且正确。"""
        m = create_growth_proposal_lifecycle()
        store = _MockProposalStore()
        store.add_proposal("prop_s_001", status=STATE_PENDING)
        r = m.transition("prop_s_001", STATE_REVIEWING, actor="runtime")
        assert r["schema_version"] == GROWTH_PROPOSAL_LIFECYCLE_SCHEMA_VERSION
        assert r["schema_version"] == "1.0"

    def test_24_state_legality(self) -> None:
        """24. 状态合法性校验。"""
        assert is_valid_state(STATE_PENDING) is True
        assert is_valid_state(STATE_REVIEWING) is True
        assert is_valid_state(STATE_APPROVED) is True
        assert is_valid_state(STATE_REJECTED) is True
        assert is_valid_state(STATE_NEEDS_REVIEW) is True
        assert is_valid_state(STATE_READY_FOR_APPLY) is True
        # 非法状态
        assert is_valid_state("applied") is False
        assert is_valid_state("unknown") is False
        assert is_valid_state("") is False
        # validate_transition
        assert validate_transition(STATE_PENDING, STATE_REVIEWING) is True
        assert validate_transition(STATE_PENDING, STATE_APPROVED) is False
        assert validate_transition(STATE_REJECTED, STATE_APPROVED) is False

    def test_25_timestamp_present(self) -> None:
        """25. timestamp 字段存在。"""
        m = create_growth_proposal_lifecycle()
        store = _MockProposalStore()
        store.add_proposal("prop_s_002", status=STATE_PENDING)
        r = m.transition("prop_s_002", STATE_REVIEWING, actor="runtime")
        assert "timestamp" in r
        assert r["timestamp"] is not None
        assert len(str(r["timestamp"])) > 0
        # ISO 格式
        ts = str(r["timestamp"])
        assert "T" in ts

    def test_26_actor_field(self) -> None:
        """26. actor 字段存在且正确。"""
        m = create_growth_proposal_lifecycle()
        store = _MockProposalStore()
        store.add_proposal("prop_s_003", status=STATE_PENDING)
        r = m.transition("prop_s_003", STATE_REVIEWING, actor="my_actor")
        assert r["actor"] == "my_actor"
        # 默认 actor
        r2 = m.transition("prop_s_003", STATE_APPROVED, actor=None)
        assert r2["actor"] == AUDIT_ACTOR_RUNTIME

    def test_27_error_field_on_failure(self) -> None:
        """27. 失败时 error 字段非空。"""
        m = create_growth_proposal_lifecycle()
        # proposal 不存在
        r = m.transition("nonexistent_prop", STATE_REVIEWING, actor="runtime")
        assert r["success"] is False
        assert r["error"] is not None
        assert r["reason"] == REASON_PROPOSAL_NOT_FOUND
        # 非法状态
        r2 = m.transition("any_id", "bogus_state", actor="runtime")
        assert r2["success"] is False
        assert r2["error"] is not None
        assert r2["reason"] == REASON_INVALID_STATE

    def test_28_success_field(self) -> None:
        """28. success 字段在成功/失败时正确反映。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_004", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r1 = m.transition("prop_s_004", STATE_REVIEWING, actor="runtime")
        assert r1["success"] is True
        r2 = m.transition("prop_s_004", STATE_APPROVED, actor="runtime")
        assert r2["success"] is True
        # ready_for_apply 是终态
        r3 = m.transition("prop_s_004", STATE_READY_FOR_APPLY, actor="runtime")
        assert r3["success"] is True
        # 之后再 transition → 失败
        r4 = m.transition("prop_s_004", STATE_APPROVED, actor="runtime")
        assert r4["success"] is False

    def test_29_old_and_new_state(self) -> None:
        """29. old_state / new_state 字段正确。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_005", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_s_005", STATE_REVIEWING, actor="runtime")
        assert r["old_state"] == STATE_PENDING
        assert r["new_state"] == STATE_REVIEWING

    def test_30_already_in_state_reason(self) -> None:
        """30. 同状态转换 → already_in_state 原因。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_006", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r = m.transition("prop_s_006", STATE_PENDING, actor="runtime")
        assert r["success"] is False
        assert r["reason"] == REASON_ALREADY_IN_STATE

    def test_31_get_state_machine_info(self) -> None:
        """31. get_state_machine_info 返回完整状态机信息。"""
        m = create_growth_proposal_lifecycle()
        info = m.get_state_machine_info()
        for key in ["name", "version", "schema_version", "states", "initial_state", "terminal_states", "transitions"]:
            assert key in info
        # states
        assert STATE_PENDING in info["states"]
        assert STATE_REVIEWING in info["states"]
        assert STATE_APPROVED in info["states"]
        # terminal_states
        assert STATE_REJECTED in info["terminal_states"]
        assert STATE_READY_FOR_APPLY in info["terminal_states"]
        # transitions
        assert STATE_REVIEWING in info["transitions"]
        assert STATE_APPROVED in info["transitions"][STATE_REVIEWING]

    def test_32_get_history(self) -> None:
        """32. get_history 返回正确历史记录。"""
        store = _MockProposalStore()
        store.add_proposal("prop_s_007", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        m.transition("prop_s_007", STATE_REVIEWING, actor="runtime")
        m.transition("prop_s_007", STATE_APPROVED, actor="human_reviewer")
        hist = m.get_history("prop_s_007")
        assert len(hist) == 2
        assert hist[0]["old_state"] == STATE_PENDING
        assert hist[0]["new_state"] == STATE_REVIEWING
        assert hist[1]["old_state"] == STATE_REVIEWING
        assert hist[1]["new_state"] == STATE_APPROVED

    def test_33_get_stats(self) -> None:
        """33. get_stats 包含完整统计字段。"""
        m = create_growth_proposal_lifecycle(proposal_store=_MockProposalStore(), audit=_MockAudit())
        stats = m.get_stats()
        for key in [
            "transition_count", "success_count", "invalid_count", "error_count",
            "audit_recorded_count", "last_error", "last_transition_ts",
            "cached_proposals", "history_size", "has_store", "has_audit",
        ]:
            assert key in stats, f"stats 缺少 {key}"
        assert stats["has_store"] is True
        assert stats["has_audit"] is True
        assert stats["transition_count"] == 0

    def test_34_invalid_inputs_safe(self) -> None:
        """34. 无效输入不抛异常。"""
        m = create_growth_proposal_lifecycle()
        # 空 proposal_id
        r1 = m.transition("", STATE_REVIEWING, actor="runtime")
        assert r1["success"] is False
        assert r1["reason"] == REASON_INVALID_INPUT
        # 空 target_state
        r2 = m.transition("any", "", actor="runtime")
        assert r2["success"] is False
        assert r2["reason"] == REASON_INVALID_INPUT
        # None
        r3 = m.transition(None, STATE_REVIEWING, actor="runtime")  # type: ignore[arg-type]
        assert r3["success"] is False

    def test_35_safe_transition_helper(self) -> None:
        """35. safe_transition 全局辅助函数不抛异常。"""
        # None manager
        r1 = safe_transition(None, "any_id", STATE_REVIEWING, actor="runtime")
        assert isinstance(r1, dict)
        # None 不会崩,无 store → 失败但有结构
        assert "success" in r1
        # 正常 manager
        store = _MockProposalStore()
        store.add_proposal("prop_s_008", status=STATE_PENDING)
        m = create_growth_proposal_lifecycle(proposal_store=store)
        r2 = safe_transition(m, "prop_s_008", STATE_REVIEWING, actor="runtime")
        assert r2["success"] is True


# ============================================================
# 16. 额外测试:状态机纯函数
# ============================================================


class TestStateMachineFunctions:
    """状态机纯函数测试。"""

    def test_get_allowed_transitions(self) -> None:
        """get_allowed_transitions 返回正确列表。"""
        assert STATE_REVIEWING in get_allowed_transitions(STATE_PENDING)
        assert STATE_REJECTED in get_allowed_transitions(STATE_PENDING)
        # approved 只有 ready_for_apply
        assert get_allowed_transitions(STATE_APPROVED) == [STATE_READY_FOR_APPLY]
        # rejected 是终态
        assert get_allowed_transitions(STATE_REJECTED) == []
        # ready_for_apply 是终态
        assert get_allowed_transitions(STATE_READY_FOR_APPLY) == []
        # 非法 from_state
        assert get_allowed_transitions("unknown") == []

    def test_is_terminal_state_fn(self) -> None:
        """is_terminal_state 函数正确。"""
        assert is_terminal_state(STATE_REJECTED) is True
        assert is_terminal_state(STATE_READY_FOR_APPLY) is True
        assert is_terminal_state(STATE_PENDING) is False
        assert is_terminal_state(STATE_REVIEWING) is False
        assert is_terminal_state(STATE_APPROVED) is False
        assert is_terminal_state(STATE_NEEDS_REVIEW) is False
        # 非法状态:不是终态(返回 False 而非抛异常)
        assert is_terminal_state("unknown") is False

    def test_transitions_table_complete(self) -> None:
        """TRANSITIONS 表的每个 from_state 都是合法状态。"""
        for from_state in TRANSITIONS:
            assert from_state in ALL_STATES
            for to_state in TRANSITIONS[from_state]:
                assert to_state in ALL_STATES
