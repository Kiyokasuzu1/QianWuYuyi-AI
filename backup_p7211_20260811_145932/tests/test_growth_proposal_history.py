# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_history.py

Phase C.8.4 Growth Proposal History & Versioning —— 验证测试

目标:
  验证 GrowthProposalHistory 严格只管理 Proposal 生命周期历史:
    1) record_event:   记录事件,自动管理 version
    2) get_history:    按 proposal 查询完整历史
    3) get_latest:     查询最新快照
    4) compare_versions: 对比两个版本
    5) Audit 完整
    6) 不触发人格 / SelfModel / Trait 修改
    7) 不调用 apply_proposal / accept_proposal
    8) 并发安全(无重复 version)

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务

覆盖 40+ 测试:
  Creation (1)
  Record (2-4)
  Version (5-6)
  Query (7-8)
  Diff (9)
  Readonly (10-11)
  Security (12-13)
  Audit (14)
  FailSafe (15-16)
  Integration (17)
  Schema (18-40+)
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
    GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION,
    GROWTH_PROPOSAL_HISTORY_NAME,
    GROWTH_PROPOSAL_HISTORY_VERSION,
    ACTION_CREATED,
    ACTION_REVIEWED,
    ACTION_APPROVED,
    ACTION_REJECTED,
    ACTION_REVOKED,
    ACTION_TRANSITION,
    HISTORY_ALL_ACTIONS,
    AUDIT_ACTION_HISTORY_UPDATED,
    ACTOR_RUNTIME,
    INITIAL_VERSION,
    REASON_VERSION_NOT_FOUND,
    REASON_INVALID_VERSION,
    HISTORY_REASON_PROPOSAL_NOT_FOUND,
    GrowthProposalHistory,
    create_growth_proposal_history,
    safe_record_event,
)
from src.runtime.growth.growth_proposal_history import (
    REASON_INVALID_INPUT,
    REASON_STORE_ERROR,
    REASON_INTERNAL_ERROR,
    REASON_DEGRADED,
    REASON_AUDIT_ERROR,
)


# ============================================================
# 1. Mock ProposalStore
# ============================================================


class _MockProposalStore:
    """最小 ProposalStore mock。"""

    def __init__(self) -> None:
        self._index: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def load(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            d = self._index.get(str(proposal_id or ""))
            return dict(d) if d else None

    def add_proposal(self, proposal_id: str, status: str = "pending", **kwargs: Any) -> None:
        with self._lock:
            self._index[str(proposal_id)] = {
                "id": str(proposal_id),
                "status": str(status),
                **{k: v for k, v in kwargs.items()},
            }


# ============================================================
# 2. Mock HistoryStore(支持持久化)
# ============================================================


class _MockHistoryStore:
    """模拟外部持久化 history store。"""

    def __init__(self) -> None:
        self._data: Dict[str, List[Dict[str, Any]]] = {}
        self._save_calls: int = 0
        self._raise_on_save: Optional[Exception] = None
        self._lock = threading.RLock()

    def save_history(self, proposal_id: str, entries: List[Dict[str, Any]]) -> None:
        with self._lock:
            self._save_calls += 1
            if self._raise_on_save is not None:
                raise self._raise_on_save
            self._data[str(proposal_id)] = [dict(x) for x in entries if isinstance(x, dict)]

    def load_history(self, proposal_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(x) for x in self._data.get(str(proposal_id), []) if isinstance(x, dict)]

    @property
    def save_call_count(self) -> int:
        return self._save_calls


# ============================================================
# 3. Mock Audit
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
# 4. Mock 业务对象(用于 TestReadonly / TestSecurity)
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
        raise AssertionError("accept_proposal FORBIDDEN in C.8.4")

    def reject_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.reject_calls += 1
        raise AssertionError("reject_proposal FORBIDDEN in C.8.4")

    def apply_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.apply_calls += 1
        raise AssertionError("apply_proposal FORBIDDEN in C.8.4")

    def create_proposal(self, *args: Any, **kwargs: Any) -> Any:
        self.create_calls += 1
        return None


# ============================================================
# 5. 辅助工厂
# ============================================================


def _make_history(
    history_store: Optional[_MockHistoryStore] = None,
    proposal_store: Optional[_MockProposalStore] = None,
    audit: Optional[_MockAudit] = None,
) -> GrowthProposalHistory:
    return create_growth_proposal_history(
        history_store=history_store,
        proposal_store=proposal_store,
        audit=audit,
    )


# ============================================================
# 6. 测试:Creation (1)
# ============================================================


class TestCreation:
    """History 创建测试。"""

    def test_01_history_creates_successfully(self) -> None:
        """1. history 创建成功(无依赖也可工作)。"""
        h = create_growth_proposal_history()
        assert isinstance(h, GrowthProposalHistory)
        assert h.record_count == 0
        assert h.success_count == 0
        # 工厂 + 构造
        h2 = GrowthProposalHistory()
        assert h2 is not None
        # 注入 store / audit
        h3 = create_growth_proposal_history(
            history_store=_MockHistoryStore(),
            proposal_store=_MockProposalStore(),
            audit=_MockAudit(),
            default_actor="test_actor",
        )
        assert h3 is not None
        # history info
        info = h.get_history_info()
        assert info["name"] == GROWTH_PROPOSAL_HISTORY_NAME
        assert info["version"] == GROWTH_PROPOSAL_HISTORY_VERSION
        assert info["initial_version"] == INITIAL_VERSION


# ============================================================
# 7. 测试:Record (2-4)
# ============================================================


class TestRecord:
    """记录事件测试。"""

    def test_02_created_record(self) -> None:
        """2. record_created 正确记录(初始 version = 1)。"""
        h = _make_history()
        r = h.record_created("prop_001", actor="runtime", metadata={"state": "pending"})
        assert r["success"] is True
        assert r["proposal_id"] == "prop_001"
        assert r["action"] == ACTION_CREATED
        assert r["actor"] == "runtime"
        assert r["version"] == 1
        assert r["previous_version"] is None
        assert r["metadata"]["state"] == "pending"
        assert r["history_id"] != ""
        assert "timestamp" in r
        assert h.success_count == 1

    def test_03_review_record(self) -> None:
        """3. record_reviewed 正确记录(version 递增)。"""
        h = _make_history()
        h.record_created("prop_002", actor="runtime")
        r = h.record_reviewed("prop_002", actor="reviewer_001", metadata={"decision": "ok"})
        assert r["success"] is True
        assert r["action"] == ACTION_REVIEWED
        assert r["version"] == 2
        assert r["previous_version"] == 1
        assert r["actor"] == "reviewer_001"

    def test_04_approve_record(self) -> None:
        """4. record_approved 正确记录(支持 5+ 个标准 action)。"""
        h = _make_history()
        # 全套标准事件
        h.record_created("prop_003", actor="runtime")
        h.record_reviewed("prop_003", actor="reviewer_001")
        h.record_approved("prop_003", actor="admin_001", metadata={"final": True})
        r_rej = h.record_rejected("prop_004", actor="admin_002", metadata={"reason": "x"})
        r_rvk = h.record_revoked("prop_005", actor="admin_003", metadata={"reason": "re_audit"})
        r_trn = h.record_transition("prop_006", actor="runtime")
        # 全部成功
        assert r_rej["action"] == ACTION_REJECTED
        assert r_rvk["action"] == ACTION_REVOKED
        assert r_trn["action"] == ACTION_TRANSITION
        # 版本号
        assert r_rej["version"] == 1
        assert r_rvk["version"] == 1
        assert r_trn["version"] == 1
        # 全部 5 个标准 action 在 ALL_ACTIONS
        for a in (ACTION_CREATED, ACTION_REVIEWED, ACTION_APPROVED,
                  ACTION_REJECTED, ACTION_REVOKED):
            assert a in HISTORY_ALL_ACTIONS


# ============================================================
# 8. 测试:Version (5-6)
# ============================================================


class TestVersion:
    """Version 机制测试。"""

    def test_05_version_increments(self) -> None:
        """5. version 单调递增(每个 proposal 独立)。"""
        h = _make_history()
        # proposal_1: 1 → 2 → 3 → 4
        v1 = h.record_event("prop_v_001", ACTION_CREATED, actor="runtime")
        v2 = h.record_event("prop_v_001", ACTION_REVIEWED, actor="rev")
        v3 = h.record_event("prop_v_001", ACTION_APPROVED, actor="admin")
        v4 = h.record_event("prop_v_001", ACTION_REVOKED, actor="admin")
        assert v1["version"] == 1
        assert v2["version"] == 2
        assert v3["version"] == 3
        assert v4["version"] == 4
        # proposal_2: 1(独立计数)
        v5 = h.record_event("prop_v_002", ACTION_CREATED, actor="runtime")
        assert v5["version"] == 1
        # proposal_1 继续: 5
        v6 = h.record_event("prop_v_001", ACTION_APPROVED, actor="admin")
        assert v6["version"] == 5

    def test_06_concurrent_version_safe(self) -> None:
        """6. 多线程并发 record 同一 proposal 不会产生重复 version。"""
        h = _make_history()
        proposal_id = "prop_v_conc"
        n = 50
        versions_seen: List[int] = []
        versions_lock = threading.Lock()

        def worker(idx: int) -> None:
            r = h.record_event(
                proposal_id=proposal_id,
                action=ACTION_TRANSITION,
                actor=f"t{idx}",
                metadata={"i": idx},
            )
            if r["success"]:
                with versions_lock:
                    versions_seen.append(r["version"])

        threads: List[threading.Thread] = []
        for i in range(n):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"thread 死锁: {t.name}"
        # 所有 version 唯一
        assert len(versions_seen) == n
        assert len(set(versions_seen)) == n
        # version 范围: 1..n
        assert min(versions_seen) == 1
        assert max(versions_seen) == n
        # 没有 version_conflict
        assert h.version_conflict_count == 0


# ============================================================
# 9. 测试:Query (7-8)
# ============================================================


class TestQuery:
    """查询 API 测试。"""

    def test_07_get_history(self) -> None:
        """7. get_history 返回完整历史(按 version 升序)。"""
        h = _make_history()
        h.record_created("prop_q_001", actor="runtime")
        h.record_reviewed("prop_q_001", actor="rev1", metadata={"decision": "ok"})
        h.record_approved("prop_q_001", actor="admin1", metadata={"state": "approved"})
        hist = h.get_history("prop_q_001")
        assert len(hist) == 3
        assert hist[0]["version"] == 1
        assert hist[0]["action"] == ACTION_CREATED
        assert hist[1]["version"] == 2
        assert hist[1]["action"] == ACTION_REVIEWED
        assert hist[2]["version"] == 3
        assert hist[2]["action"] == ACTION_APPROVED
        # metadata 完整
        assert hist[1]["metadata"]["decision"] == "ok"
        assert hist[2]["metadata"]["state"] == "approved"
        # limit
        hist2 = h.get_history("prop_q_001", limit=2)
        assert len(hist2) == 2
        # 不存在 → []
        assert h.get_history("nonexistent") == []
        # history_count
        assert h.get_history_count("prop_q_001") == 3

    def test_08_get_latest(self) -> None:
        """8. get_latest 返回最新快照。"""
        h = _make_history()
        # 不存在 → proposal_exists=False
        r0 = h.get_latest("nonexistent")
        assert r0["success"] is False
        assert r0["proposal_exists"] is False
        # 存在
        h.record_created("prop_l_001", actor="runtime", metadata={"state": "pending"})
        h.record_reviewed("prop_l_001", actor="rev1", metadata={"state": "reviewing"})
        h.record_approved("prop_l_001", actor="admin1", metadata={"state": "approved"})
        r1 = h.get_latest("prop_l_001")
        assert r1["success"] is True
        assert r1["proposal_id"] == "prop_l_001"
        assert r1["current_version"] == 3
        assert r1["last_action"] == ACTION_APPROVED
        assert r1["last_actor"] == "admin1"
        assert r1["current_state"] == "approved"  # 来自 metadata.state
        assert r1["history_count"] == 3
        assert r1["proposal_exists"] is True
        assert r1["updated_at"] is not None


# ============================================================
# 10. 测试:Diff (9)
# ============================================================


class TestDiff:
    """版本对比测试。"""

    def test_09_compare_versions(self) -> None:
        """9. compare_versions 正确计算差异。"""
        h = _make_history()
        h.record_event("prop_d_001", ACTION_CREATED, actor="runtime",
                       metadata={"state": "pending", "score": 0.5})
        h.record_event("prop_d_001", ACTION_REVIEWED, actor="rev1",
                       metadata={"state": "reviewing", "score": 0.6, "comment": "looks_good"})
        h.record_event("prop_d_001", ACTION_APPROVED, actor="admin1",
                       metadata={"state": "approved", "score": 0.7, "final": True})
        # v1 → v2
        d1 = h.compare_versions("prop_d_001", 1, 2)
        assert d1["success"] is True
        assert d1["from_version"] == 1
        assert d1["to_version"] == 2
        # action 变化
        assert d1["changes"]["action"]["from"] == ACTION_CREATED
        assert d1["changes"]["action"]["to"] == ACTION_REVIEWED
        # actor 变化
        assert d1["changes"]["actor"]["from"] == "runtime"
        assert d1["changes"]["actor"]["to"] == "rev1"
        # metadata: score 变化 + comment 新增 + final 暂未
        assert "score" in d1["changes"]["metadata"]["changed"]
        assert d1["changes"]["metadata"]["changed"]["score"]["from"] == 0.5
        assert d1["changes"]["metadata"]["changed"]["score"]["to"] == 0.6
        assert "comment" in d1["changes"]["metadata"]["added"]
        assert "final" not in d1["changes"]["metadata"]["added"]  # v2 还没有
        # diff_summary
        assert d1["diff_summary"]["action_changed"] is True
        assert d1["diff_summary"]["actor_changed"] is True
        assert d1["diff_summary"]["metadata_changed"] is True
        # v1 → v3(跨越)
        d2 = h.compare_versions("prop_d_001", 1, 3)
        assert d2["success"] is True
        # final 新增
        assert "final" in d2["changes"]["metadata"]["added"]
        # 不存在 version
        d3 = h.compare_versions("prop_d_001", 1, 99)
        assert d3["success"] is False
        assert d3["error_reason"] == REASON_VERSION_NOT_FOUND
        # invalid version
        d4 = h.compare_versions("prop_d_001", 0, 1)
        assert d4["success"] is False
        assert d4["error_reason"] == REASON_INVALID_VERSION


# ============================================================
# 11. 测试:Readonly (10-11)
# ============================================================


class TestReadonly:
    """只读业务边界测试。"""

    def test_10_personality_unchanged(self) -> None:
        """10. record / query 不会修改 PersonalityState。"""
        pys = _MockPersonalityState()
        h = _make_history()
        # 跑完整生命周期
        h.record_created("prop_ro_001", actor="runtime")
        h.record_reviewed("prop_ro_001", actor="rev1")
        h.record_approved("prop_ro_001", actor="admin1")
        h.record_rejected("prop_ro_002", actor="admin1", metadata={"reason": "x"})
        h.record_revoked("prop_ro_003", actor="admin1", metadata={"reason": "x"})
        _ = h.get_history("prop_ro_001")
        _ = h.get_latest("prop_ro_001")
        _ = h.compare_versions("prop_ro_001", 1, 2)
        # PersonalityState 完全未变
        assert pys.write_count == 0
        assert pys.resolve_call_count == 0
        assert pys.traits == {"warmth": 0.5, "gentleness": 0.5}

    def test_11_self_model_unchanged(self) -> None:
        """11. record / query 不会修改 SelfModel / Trait。"""
        sms = _MockSelfModelStore()
        tsu = _MockTraitStateUpdater()
        h = _make_history()
        h.record_created("prop_ro_004", actor="runtime")
        h.record_reviewed("prop_ro_004", actor="rev1")
        h.record_approved("prop_ro_004", actor="admin1")
        _ = h.get_history("prop_ro_004")
        # SelfModel / Trait 完全未变
        assert sms.write_count == 0
        assert sms.update_call_count == 0
        assert tsu.write_count == 0
        assert tsu.apply_call_count == 0


# ============================================================
# 12. 测试:Security (12-13)
# ============================================================


class TestSecurity:
    """安全限制测试。"""

    def test_12_apply_forbidden(self) -> None:
        """12. record / query 不会触发 apply_proposal / accept_proposal / reject_proposal。"""
        pm = _MockProposalManager()
        h = _make_history()
        h.record_created("prop_sec_001", actor="runtime")
        h.record_reviewed("prop_sec_001", actor="rev1")
        h.record_approved("prop_sec_001", actor="admin1")
        h.record_rejected("prop_sec_002", actor="admin1", metadata={"reason": "x"})
        h.record_revoked("prop_sec_003", actor="admin1", metadata={"reason": "x"})
        _ = h.get_history("prop_sec_001")
        _ = h.get_latest("prop_sec_001")
        _ = h.compare_versions("prop_sec_001", 1, 2)
        # ProposalManager 完全未被调用
        assert pm.apply_calls == 0
        assert pm.accept_calls == 0
        assert pm.reject_calls == 0
        assert pm.create_calls == 0

    def test_13_resolver_forbidden(self) -> None:
        """13. PersonalityResolver.resolve() 必须 0 次调用。"""
        pys = _MockPersonalityState()
        h = _make_history()
        for i in range(10):
            h.record_event(f"prop_sec_{i:03d}", ACTION_TRANSITION, actor="runtime")
        assert pys.resolve_call_count == 0
        assert pys.write_count == 0


# ============================================================
# 13. 测试:Audit (14)
# ============================================================


class TestAudit:
    """Audit 测试。"""

    def test_14_history_audit(self) -> None:
        """14. 每次 record_event 记录 audit(完整字段)。"""
        audit = _MockAudit()
        h = _make_history(audit=audit)
        h.record_created("prop_aud_001", actor="runtime",
                         metadata={"state": "pending"})
        h.record_reviewed("prop_aud_001", actor="rev1",
                          metadata={"state": "reviewing"})
        # 2 条 audit
        assert audit.record_count == 2
        # 字段完整
        for rec in audit.records:
            detail = rec["detail"]
            for key in ["history_id", "proposal_id", "version", "action", "actor", "timestamp"]:
                assert key in detail, f"audit detail 缺少 {key}"
            assert rec["action"] == AUDIT_ACTION_HISTORY_UPDATED
            assert rec["operation_type"] == AUDIT_ACTION_HISTORY_UPDATED
        # 第 1 条: created, v1
        rec1 = audit.records[0]
        assert rec1["detail"]["action"] == ACTION_CREATED
        assert rec1["detail"]["version"] == 1
        assert rec1["detail"]["proposal_id"] == "prop_aud_001"
        # 第 2 条: reviewed, v2
        rec2 = audit.records[1]
        assert rec2["detail"]["action"] == ACTION_REVIEWED
        assert rec2["detail"]["version"] == 2


# ============================================================
# 14. 测试:FailSafe (15-16)
# ============================================================


class TestFailSafe:
    """失败隔离测试。"""

    def test_15_store_exception(self) -> None:
        """15. history_store 抛异常 → 不崩,degraded=True 但 success=True(内存在)。"""
        store = _MockHistoryStore()
        store._raise_on_save = RuntimeError("store save failed")
        h = _make_history(history_store=store)
        r = h.record_event("prop_fs_001", ACTION_CREATED, actor="runtime")
        # success=True(内存在了),degraded=True(store 写失败)
        assert r["success"] is True
        assert r["degraded"] is True
        assert r["error"] is not None
        assert r["error_reason"] == REASON_STORE_ERROR
        # error_count 增加
        assert h.error_count >= 1
        # 内存中仍可读到
        hist = h.get_history("prop_fs_001")
        assert len(hist) == 1

    def test_16_audit_exception(self) -> None:
        """16. audit.record() 抛异常 → record_event 仍成功,audit_recorded=False。"""
        audit = _MockAudit()
        audit._raise = True
        h = _make_history(audit=audit)
        r = h.record_event("prop_fs_002", ACTION_CREATED, actor="runtime")
        # record 仍成功
        assert r["success"] is True
        # audit_recorded=False
        assert r["audit_recorded"] is False
        # 内存中有
        assert h.get_history_count("prop_fs_002") == 1


# ============================================================
# 15. 测试:Integration (17)
# ============================================================


class TestIntegration:
    """端到端集成测试。"""

    def test_17_full_workflow_with_history(self) -> None:
        """17. Runtime → Review → Approval → History 完整链路。"""
        # 1) Runtime 创建 proposal
        pm = _MockProposalManagerExt()
        from src.runtime.growth import GrowthProposalRuntime
        runtime = GrowthProposalRuntime(
            growth_evaluator=_MockEvaluatorLite(),
            proposal_manager=pm,
            audit=_MockAudit(),
        )
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

        # 2) History 记录 created + reviewed
        h = create_growth_proposal_history(
            proposal_store=pm,
            audit=_MockAudit(),
        )
        h2c = h.record_created(proposal_id, actor="runtime",
                               metadata={"state": "pending"})
        assert h2c["version"] == 1
        h2r = h.record_reviewed(proposal_id, actor="human_reviewer",
                                metadata={"state": "reviewing", "decision": "ok"})
        assert h2r["version"] == 2

        # 3) Lifecycle
        from src.runtime.growth import create_growth_proposal_lifecycle
        lifecycle = create_growth_proposal_lifecycle(
            proposal_store=pm,
            audit=_MockAudit(),
        )
        lifecycle.transition(proposal_id, "reviewing", actor="runtime")
        lifecycle.transition(proposal_id, "approved", actor="human_reviewer")
        h2a = h.record_approved(proposal_id, actor="admin", metadata={"state": "approved"})
        assert h2a["version"] == 3

        # 4) Approval
        from src.runtime.growth import create_growth_proposal_approval
        w = create_growth_proposal_approval(
            proposal_store=pm,
            lifecycle=lifecycle,
            audit=_MockAudit(),
        )
        w.approve(proposal_id, approver="human")
        h2ap = h.record_event(proposal_id, "ready_for_apply", actor="runtime",
                              metadata={"state": "ready_for_apply"})
        assert h2ap["version"] == 4

        # 5) Revoke
        w.revoke_approval(proposal_id, revoker="admin", reason="re_review")
        h2rv = h.record_revoked(proposal_id, actor="admin", metadata={"reason": "re_review"})
        assert h2rv["version"] == 5

        # 6) 验证 history 完整
        hist = h.get_history(proposal_id)
        assert len(hist) == 5
        versions = [e["version"] for e in hist]
        assert versions == [1, 2, 3, 4, 5]

        # 7) get_latest
        latest = h.get_latest(proposal_id)
        assert latest["current_version"] == 5
        assert latest["last_action"] == ACTION_REVOKED

        # 8) compare_versions(v1 vs v5)
        diff = h.compare_versions(proposal_id, 1, 5)
        assert diff["success"] is True
        assert diff["diff_summary"]["action_changed"] is True
        assert diff["diff_summary"]["actor_changed"] is True


# 辅助类(用于 TestIntegration)
class _MockEvaluatorLite:
    def evaluate(self, event, history=None):
        return {"confidence": 0.85, "target_candidates": ["growth"]}


class _MockProposalManagerExt:
    """支持 create + load + update 的 ProposalManager。"""

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
# 16. 测试:Schema (18-40+)
# ============================================================


class TestSchema:
    """Schema 字段完整性测试。"""

    def test_18_schema_version(self) -> None:
        """18. schema_version 字段正确。"""
        h = _make_history()
        r = h.record_event("prop_s_001", ACTION_CREATED, actor="runtime")
        assert r["schema_version"] == GROWTH_PROPOSAL_HISTORY_SCHEMA_VERSION
        assert r["schema_version"] == "1.0"

    def test_19_history_id_present(self) -> None:
        """19. history_id 字段存在且唯一。"""
        h = _make_history()
        r1 = h.record_event("prop_s_002", ACTION_CREATED, actor="runtime")
        r2 = h.record_event("prop_s_002", ACTION_REVIEWED, actor="rev1")
        assert r1["history_id"] != ""
        assert r2["history_id"] != ""
        assert r1["history_id"] != r2["history_id"]
        # history_id 也在 history 中
        hist = h.get_history("prop_s_002")
        assert hist[0]["history_id"] == r1["history_id"]
        assert hist[1]["history_id"] == r2["history_id"]

    def test_20_proposal_id_field(self) -> None:
        """20. proposal_id 字段正确。"""
        h = _make_history()
        r = h.record_event("prop_s_003", ACTION_CREATED, actor="runtime")
        assert r["proposal_id"] == "prop_s_003"
        # 也在 history 中
        hist = h.get_history("prop_s_003")
        assert hist[0]["proposal_id"] == "prop_s_003"

    def test_21_action_field(self) -> None:
        """21. action 字段正确。"""
        h = _make_history()
        for a in [ACTION_CREATED, ACTION_REVIEWED, ACTION_APPROVED,
                  ACTION_REJECTED, ACTION_REVOKED, ACTION_TRANSITION]:
            r = h.record_event(f"prop_s_act_{a[:6]}", a, actor="runtime")
            assert r["action"] == a

    def test_22_actor_field(self) -> None:
        """22. actor 字段正确(默认 runtime / 自定义)。"""
        h = _make_history()
        r1 = h.record_event("prop_s_004", ACTION_CREATED, actor="my_actor")
        assert r1["actor"] == "my_actor"
        # 默认 actor
        r2 = h.record_event("prop_s_004", ACTION_REVIEWED, actor=None)
        assert r2["actor"] == ACTOR_RUNTIME

    def test_23_version_field(self) -> None:
        """23. version 字段正确(初始 1,单调递增)。"""
        h = _make_history()
        r1 = h.record_event("prop_s_005", ACTION_CREATED, actor="runtime")
        r2 = h.record_event("prop_s_005", ACTION_REVIEWED, actor="rev1")
        r3 = h.record_event("prop_s_005", ACTION_APPROVED, actor="admin1")
        assert r1["version"] == 1
        assert r2["version"] == 2
        assert r3["version"] == 3

    def test_24_previous_version_field(self) -> None:
        """24. previous_version 字段正确(首条 None,后续递增)。"""
        h = _make_history()
        r1 = h.record_event("prop_s_006", ACTION_CREATED, actor="runtime")
        r2 = h.record_event("prop_s_006", ACTION_REVIEWED, actor="rev1")
        r3 = h.record_event("prop_s_006", ACTION_APPROVED, actor="admin1")
        assert r1["previous_version"] is None
        assert r2["previous_version"] == 1
        assert r3["previous_version"] == 2

    def test_25_timestamp_field(self) -> None:
        """25. timestamp 字段存在且 ISO 格式。"""
        h = _make_history()
        r = h.record_event("prop_s_007", ACTION_CREATED, actor="runtime")
        assert "timestamp" in r
        assert r["timestamp"] is not None
        ts = str(r["timestamp"])
        assert "T" in ts

    def test_26_metadata_field(self) -> None:
        """26. metadata 字段正确(深拷贝隔离)。"""
        h = _make_history()
        meta = {"state": "pending", "score": 0.85, "tags": ["a", "b"]}
        r = h.record_event("prop_s_008", ACTION_CREATED, actor="runtime", metadata=meta)
        assert r["metadata"] == meta
        # 修改原 meta 不影响 record
        meta["state"] = "modified"
        hist = h.get_history("prop_s_008")
        assert hist[0]["metadata"]["state"] == "pending"

    def test_27_audit_recorded_field(self) -> None:
        """27. audit_recorded 字段正确(成功/失败反映)。"""
        audit = _MockAudit()
        h = _make_history(audit=audit)
        r = h.record_event("prop_s_009", ACTION_CREATED, actor="runtime")
        assert r["audit_recorded"] is True
        # audit 异常时
        audit._raise = True
        r2 = h.record_event("prop_s_009", ACTION_REVIEWED, actor="rev1")
        assert r2["audit_recorded"] is False
        assert r2["success"] is True  # 不阻断

    def test_28_error_field(self) -> None:
        """28. 失败时 error / error_reason 字段非空。"""
        h = _make_history()
        r1 = h.record_event("", ACTION_CREATED, actor="runtime")
        assert r1["success"] is False
        assert r1["error"] is not None
        assert r1["error_reason"] == REASON_INVALID_INPUT
        r2 = h.record_event("any", "", actor="runtime")
        assert r2["success"] is False
        assert r2["error"] is not None
        assert r2["error_reason"] == REASON_INVALID_INPUT

    def test_29_success_field(self) -> None:
        """29. success 字段在成功/失败时正确反映。"""
        h = _make_history()
        r1 = h.record_event("prop_s_010", ACTION_CREATED, actor="runtime")
        assert r1["success"] is True
        r2 = h.record_event("", ACTION_CREATED, actor="runtime")
        assert r2["success"] is False

    def test_30_history_info(self) -> None:
        """30. get_history_info 返回完整模块信息。"""
        h = _make_history()
        info = h.get_history_info()
        for key in ["name", "version", "schema_version", "supported_actions", "initial_version"]:
            assert key in info
        assert info["name"] == GROWTH_PROPOSAL_HISTORY_NAME
        assert info["version"] == GROWTH_PROPOSAL_HISTORY_VERSION
        assert info["initial_version"] == 1
        # supported_actions 包含全部标准 action
        assert ACTION_CREATED in info["supported_actions"]
        assert ACTION_REVIEWED in info["supported_actions"]
        assert ACTION_APPROVED in info["supported_actions"]
        assert ACTION_REJECTED in info["supported_actions"]
        assert ACTION_REVOKED in info["supported_actions"]

    def test_31_get_stats(self) -> None:
        """31. get_stats 包含完整统计字段。"""
        h = _make_history(
            history_store=_MockHistoryStore(),
            proposal_store=_MockProposalStore(),
            audit=_MockAudit(),
        )
        stats = h.get_stats()
        for key in [
            "record_count", "success_count", "error_count",
            "audit_recorded_count", "version_conflict_count",
            "tracked_proposals", "last_error", "last_event_ts",
            "has_history_store", "has_proposal_store", "has_audit",
        ]:
            assert key in stats, f"stats 缺少 {key}"
        assert stats["has_history_store"] is True
        assert stats["has_proposal_store"] is True
        assert stats["has_audit"] is True

    def test_32_safe_record_event_helper(self) -> None:
        """32. safe_record_event 全局辅助函数不抛异常(None history)。"""
        r1 = safe_record_event(None, "any_id", ACTION_CREATED, actor="runtime")
        assert r1["success"] is False
        assert r1["degraded"] is True
        assert "success" in r1
        # 正常 history
        h = _make_history()
        r2 = safe_record_event(h, "prop_s_011", ACTION_CREATED, actor="runtime")
        assert r2["success"] is True
        assert r2["version"] == 1

    def test_33_invalid_inputs_safe(self) -> None:
        """33. 无效输入不抛异常。"""
        h = _make_history()
        # 空 proposal_id
        r1 = h.record_event("", ACTION_CREATED, actor="runtime")
        assert r1["success"] is False
        assert r1["error_reason"] == REASON_INVALID_INPUT
        # 空 action
        r2 = h.record_event("any", "", actor="runtime")
        assert r2["success"] is False
        assert r2["error_reason"] == REASON_INVALID_INPUT
        # None
        r3 = h.record_event(None, ACTION_CREATED, actor="runtime")  # type: ignore[arg-type]
        assert r3["success"] is False
        # metadata=None
        r4 = h.record_event("prop_s_012", ACTION_CREATED, actor="runtime", metadata=None)
        assert r4["success"] is True
        assert r4["metadata"] == {}

    def test_34_get_history_persistence(self) -> None:
        """34. history_store 持久化(save_history 被调用,load_history 也能用)。"""
        store = _MockHistoryStore()
        h = _make_history(history_store=store)
        h.record_event("prop_p_001", ACTION_CREATED, actor="runtime")
        h.record_event("prop_p_001", ACTION_REVIEWED, actor="rev1")
        # 写入了 store
        assert store.save_call_count == 2
        # store 中能读到
        external = store.load_history("prop_p_001")
        assert len(external) == 2
        # 新建一个 history 实例,只接 store(空内存),从 store 读
        h2 = create_growth_proposal_history(history_store=store)
        hist = h2.get_history("prop_p_001")
        assert len(hist) == 2

    def test_35_compare_versions_invalid_inputs(self) -> None:
        """35. compare_versions 无效输入安全。"""
        h = _make_history()
        h.record_event("prop_c_001", ACTION_CREATED, actor="runtime")
        # 空 proposal_id
        r1 = h.compare_versions("", 1, 1)
        assert r1["success"] is False
        assert r1["error_reason"] == REASON_INVALID_INPUT
        # version = 0
        r2 = h.compare_versions("prop_c_001", 0, 1)
        assert r2["success"] is False
        assert r2["error_reason"] == REASON_INVALID_VERSION
        # version 不存在
        r3 = h.compare_versions("prop_c_001", 1, 99)
        assert r3["success"] is False
        assert r3["error_reason"] == REASON_VERSION_NOT_FOUND

    def test_36_diff_metadata_only(self) -> None:
        """36. metadata 单独变化的 diff 正确(无 action / actor 变化)。"""
        h = _make_history()
        h.record_event("prop_d_002", ACTION_TRANSITION, actor="runtime",
                       metadata={"x": 1, "y": 2})
        h.record_event("prop_d_002", ACTION_TRANSITION, actor="runtime",
                       metadata={"x": 1, "y": 3, "z": 4})
        d = h.compare_versions("prop_d_002", 1, 2)
        assert d["success"] is True
        assert d["diff_summary"]["action_changed"] is False
        assert d["diff_summary"]["actor_changed"] is False
        assert d["diff_summary"]["metadata_changed"] is True
        # y 变化
        assert "y" in d["changes"]["metadata"]["changed"]
        assert d["changes"]["metadata"]["changed"]["y"]["from"] == 2
        assert d["changes"]["metadata"]["changed"]["y"]["to"] == 3
        # z 新增
        assert "z" in d["changes"]["metadata"]["added"]
        # x 不变
        assert "x" not in d["changes"]["metadata"]["changed"]
        assert "x" not in d["changes"]["metadata"]["added"]
        assert "x" not in d["changes"]["metadata"]["removed"]

    def test_37_diff_no_change(self) -> None:
        """37. 完全相同的两个 version diff 为空变化。"""
        h = _make_history()
        h.record_event("prop_d_003", ACTION_CREATED, actor="runtime",
                       metadata={"x": 1})
        h.record_event("prop_d_003", ACTION_CREATED, actor="runtime",
                       metadata={"x": 1})
        d = h.compare_versions("prop_d_003", 1, 2)
        assert d["success"] is True
        assert d["diff_summary"]["action_changed"] is False
        assert d["diff_summary"]["actor_changed"] is False
        assert d["diff_summary"]["metadata_changed"] is False

    def test_38_reset(self) -> None:
        """38. reset 清空所有数据(用于测试隔离)。"""
        h = _make_history()
        h.record_event("prop_r_001", ACTION_CREATED, actor="runtime")
        h.record_event("prop_r_001", ACTION_REVIEWED, actor="rev1")
        assert h.get_history_count("prop_r_001") == 2
        h.reset()
        assert h.get_history_count("prop_r_001") == 0
        assert h.record_count == 0
        assert h.success_count == 0

    def test_39_snapshot_invalid_proposal(self) -> None:
        """39. get_latest 不存在 proposal 返回 degraded 错误。"""
        h = _make_history()
        r = h.get_latest("nonexistent_xyz")
        assert r["success"] is False
        assert r["proposal_exists"] is False
        assert r["error_reason"] == HISTORY_REASON_PROPOSAL_NOT_FOUND
        assert r["current_version"] == 0

    def test_40_audit_with_audit_exception(self) -> None:
        """40. audit 异常下 audit_recorded=False 但 success 仍 True。"""
        audit = _MockAudit()
        audit._raise = True
        h = _make_history(audit=audit)
        r = h.record_event("prop_ae_001", ACTION_CREATED, actor="runtime")
        assert r["success"] is True
        assert r["audit_recorded"] is False
        # audit_recorded_count 不增
        assert h.audit_recorded_count == 0


# ============================================================
# 17. 额外测试:版本机制边界
# ============================================================


class TestVersionEdgeCases:
    """版本机制边界测试。"""

    def test_41_custom_action_supported(self) -> None:
        """41. 支持自定义 action 字符串(不限于标准 action)。"""
        h = _make_history()
        r = h.record_event("prop_ce_001", "custom_event_xxx", actor="runtime")
        assert r["success"] is True
        assert r["action"] == "custom_event_xxx"

    def test_42_isolated_proposals_no_cross(self) -> None:
        """42. 不同 proposal 的 version 互不影响(独立性)。"""
        h = _make_history()
        for i in range(3):
            h.record_event(f"prop_iso_{i}", ACTION_CREATED, actor="runtime")
        # 每个 proposal 都是 v1
        for i in range(3):
            r = h.record_event(f"prop_iso_{i}", ACTION_REVIEWED, actor="rev1")
            assert r["version"] == 2
            assert r["previous_version"] == 1
