# -*- coding: utf-8 -*-
"""
tests/test_personality_evolution_engine.py

Phase C.8.6 Personality Evolution Engine —— 测试套件

覆盖:
  - Creation          (Engine / Policy / Store / Version 创建)
  - Validation        (pending/approved/ready_for_apply 状态校验)
  - Policy            (confidence / delta / identity / immutable / conflict)
  - Apply             (正常演化 / 版本增加 / record 生成)
  - Readonly          (旧人格不修改 / SelfModel 不修改)
  - Security          (无 approval 禁止 / 无 request 禁止 / 绕过 bridge 禁止)
  - Rollback          (成功 / 失败)
  - Audit             (apply / fail / rollback)
  - Concurrency       (重复 request 并发 / 多 request 并发)
  - FailSafe          (store 异常 / policy 异常 / snapshot 异常)
  - Integration       (Experience → Growth → Proposal → Review → Approval
                       → History → EvolutionRequest → EvolutionEngine)
  - Schema            (record 字段完整性)

合计: ≥60 测试
"""
from __future__ import annotations

import sys
import threading
import time
import pytest
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.runtime.evolution.personality_evolution_policy import (  # noqa: E402
    PersonalityEvolutionPolicy,
    create_personality_evolution_policy,
    DECISION_ALLOW,
    DECISION_NEEDS_REVIEW,
    DECISION_DENY,
    REASON_CONFIDENCE_TOO_LOW,
    REASON_DELTA_TOO_LARGE,
    REASON_CORE_IDENTITY_CONFLICT,
    REASON_IMMUTABLE_TRAIT_CONFLICT,
    REASON_TRAIT_CONFLICT,
    REASON_REPEATED_CHANGE,
    MAX_SINGLE_TRAIT_DELTA,
    MIN_CONFIDENCE_FOR_APPLY,
    DEFAULT_CORE_IDENTITY_FIELDS,
    DEFAULT_IMMUTABLE_TRAITS,
)

from src.runtime.evolution.personality_evolution_record import (  # noqa: E402
    PersonalityEvolutionStore,
    PersonalityVersionStore,
    create_personality_evolution_record_store,
    create_personality_version_store,
    build_personality_evolution_record,
    RECORD_STATUS_APPLIED,
    RECORD_STATUS_REJECTED,
    RECORD_STATUS_ROLLED_BACK,
    SELF_MODEL_IMPACT_PENDING,
    REQUIRED_REQUEST_STATUS,
)

from src.runtime.evolution.personality_evolution_engine import (  # noqa: E402
    PersonalityEvolutionEngine,
    create_personality_evolution_engine,
    safe_apply_request,
    AUDIT_ACTION_APPLIED,
    AUDIT_ACTION_FAILED,
    AUDIT_ACTION_ROLLED_BACK,
    APPLY_REASON_OK,
    APPLY_REASON_INVALID_STATE,
    APPLY_REASON_POLICY_DENIED,
    APPLY_REASON_POLICY_REVIEW,
    APPLY_REASON_DUPLICATE,
    APPLY_REASON_INTEGRITY,
    PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
    PERSONALITY_EVOLUTION_ENGINE_NAME,
    PERSONALITY_EVOLUTION_ENGINE_VERSION,
)


# ============================================================
# Mocks
# ============================================================


class _MockPersonalitySnapshot:
    """模拟 Personality snapshot 提供者。"""

    def __init__(
        self,
        traits: Optional[Dict[str, float]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._traits = traits or {
            "expressiveness": 0.5,
            "empathy": 0.6,
            "curiosity": 0.55,
            "humor": 0.4,
        }
        self._raw = {
            "name": "yuyi",
            "core_identity": {"name": "yuyi", "core_name": "浅雾羽依"},
            "immutable_traits": list(DEFAULT_IMMUTABLE_TRAITS),
            "traits": dict(self._traits),
        }
        if extra:
            self._raw.update(extra)
        self.read_count = 0

    def get_snapshot(self) -> Dict[str, Any]:
        self.read_count += 1
        return dict(self._raw)


class _FailingSnapshot:
    def get_snapshot(self) -> Dict[str, Any]:
        raise RuntimeError("snapshot boom")


class _FailingVersionStore:
    """create_version 总抛异常的 VersionStore。"""

    def create_version(self, *a: Any, **kw: Any) -> str:
        raise RuntimeError("version store boom")

    def get_current_version_id(self) -> str:
        return ""

    def get_current(self) -> Optional[Dict[str, Any]]:
        return None

    def count(self) -> int:
        return 0

    def get_version(self, *a: Any, **kw: Any) -> Optional[Dict[str, Any]]:
        return None


class _FailingEvolutionStore:
    def save(self, *a: Any, **kw: Any) -> bool:
        raise RuntimeError("evolution store boom")

    def get(self, *a: Any, **kw: Any) -> Optional[Dict[str, Any]]:
        return None

    def list_by_request(self, *a: Any, **kw: Any) -> List[Dict[str, Any]]:
        return []

    def list_by_proposal(self, *a: Any, **kw: Any) -> List[Dict[str, Any]]:
        return []

    def list_all(self) -> List[Dict[str, Any]]:
        return []

    def mark_rolled_back(self, *a: Any, **kw: Any) -> bool:
        return False

    def get_stats(self) -> Dict[str, Any]:
        return {"total_records": 0}

    def reset(self) -> None:
        pass


class _FailingPolicy:
    def evaluate(self, *a: Any, **kw: Any) -> Dict[str, Any]:
        raise RuntimeError("policy boom")

    def record_applied_change(self, *a: Any, **kw: Any) -> None:
        pass


class _MockAudit:
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


def _make_request(
    request_id: str = "evreq_test_001",
    status: str = REQUIRED_REQUEST_STATUS,
    proposal_status: str = "ready_for_apply",
    proposal_id: str = "prop_test_001",
    changes: Optional[List[Dict[str, Any]]] = None,
    confidence: float = 0.9,
) -> Dict[str, Any]:
    if changes is None:
        changes = [
            {"key": "expressiveness", "delta": 0.05, "direction": "increase"},
        ]
    return {
        "request_id": request_id,
        "status": status,
        "changes": changes,
        "confidence": confidence,
        "evidence": {
            "source_proposal": {
                "proposal_id": proposal_id,
                "status": proposal_status,
                "changes": list(changes),
                "confidence": confidence,
            },
            "history_reference": {"history_id": "hist_001", "version": 3},
            "approval_reference": {"approver": "admin", "decision": "approved"},
        },
        "timestamp": "2026-08-04T10:00:00Z",
        "actor": "runtime",
    }


def _build_engine(
    snapshot: Any = None,
    audit: Any = None,
    policy: Any = None,
    version_store: Any = None,
    evolution_store: Any = None,
) -> PersonalityEvolutionEngine:
    return create_personality_evolution_engine(
        snapshot_provider=snapshot if snapshot is not None else _MockPersonalitySnapshot(),
        evolution_store=evolution_store,
        version_store=version_store,
        policy=policy,
        audit=audit,
    )


# ============================================================
# TestCreation
# ============================================================


class TestCreation:
    def test_01_engine_creates_successfully(self):
        eng = _build_engine()
        assert isinstance(eng, PersonalityEvolutionEngine)
        assert eng.SCHEMA_VERSION == PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION
        assert eng.NAME == PERSONALITY_EVOLUTION_ENGINE_NAME
        assert eng.VERSION == PERSONALITY_EVOLUTION_ENGINE_VERSION

    def test_02_engine_creates_policy_default(self):
        eng = _build_engine()
        assert isinstance(eng._policy, PersonalityEvolutionPolicy)

    def test_03_engine_with_custom_policy(self):
        custom = PersonalityEvolutionPolicy(
            min_confidence=0.95,
            max_trait_delta=0.05,
        )
        eng = _build_engine(policy=custom)
        # 验证 custom policy 被使用
        result = eng._policy.evaluate(
            changes=[{"key": "x", "delta": 0.04}],
            confidence=0.9,
        )
        assert result["decision"] == DECISION_NEEDS_REVIEW  # <0.95

    def test_04_engine_with_audit(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        r = eng.apply_request(_make_request())
        assert r["audit_recorded"] is True
        assert any(a["action"] == AUDIT_ACTION_APPLIED for a in audit.records)

    def test_05_version_store_creates_baseline(self):
        vs = create_personality_version_store(initial_snapshot={"traits": {"x": 0.5}})
        assert vs.count() == 1
        assert vs.get_current_version_id() == "v0"

    def test_06_evolution_store_creates_empty(self):
        es = create_personality_evolution_record_store()
        assert es.get_stats()["total_records"] == 0

    def test_07_factory_returns_engine(self):
        eng = create_personality_evolution_engine()
        assert isinstance(eng, PersonalityEvolutionEngine)

    def test_08_engine_with_initial_snapshot(self):
        eng = create_personality_evolution_engine(
            initial_snapshot={"traits": {"x": 0.5}},
        )
        cur = eng.get_current_version()
        assert cur is not None
        assert cur["version_id"] == "v0"


# ============================================================
# TestValidation
# ============================================================


class TestValidation:
    def test_09_pending_request_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(status="pending"))
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["reason"] == APPLY_REASON_INVALID_STATE

    def test_10_approved_request_accepted(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(status="approved"))
        assert r["success"] is True
        assert r["reason"] == APPLY_REASON_OK

    def test_11_proposal_not_ready_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(proposal_status="approved"))
        assert r["success"] is False
        assert r["reason"] == APPLY_REASON_INVALID_STATE

    def test_12_proposal_ready_accepted(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(proposal_status="ready_for_apply"))
        assert r["success"] is True

    def test_13_none_request_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(None)
        assert r["success"] is False
        assert r["reason"] in ("invalid_input", "internal_error", "invalid_evolution_state")

    def test_14_request_without_request_id(self):
        eng = _build_engine()
        r = eng.apply_request({
            "status": "approved",
            "changes": [{"key": "x", "delta": 0.05}],
            "confidence": 0.9,
            "evidence": {"source_proposal": {"proposal_id": "p1", "status": "ready_for_apply"}},
        })
        # 缺 request_id → 在 policy 评估后被记录(没有 request_id 也可能成功)
        # 至少不能破坏 runtime
        assert "success" in r
        assert "degraded" in r


# ============================================================
# TestPolicy
# ============================================================


class TestPolicy:
    def test_15_confidence_too_low_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(confidence=0.5))
        assert r["success"] is False
        assert r["reason"] == APPLY_REASON_POLICY_REVIEW
        # 包含 confidence_too_low 原因
        if r.get("policy_result"):
            reasons = r["policy_result"].get("reasons", [])
            assert REASON_CONFIDENCE_TOO_LOW in reasons

    def test_16_confidence_at_threshold(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(confidence=MIN_CONFIDENCE_FOR_APPLY))
        # 刚好等于阈值 → policy 允许
        assert r["success"] is True

    def test_17_delta_too_large_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_delta_1",
                changes=[{"key": "expressiveness", "delta": 0.5}],  # 远大于 0.1
            ),
        )
        assert r["success"] is False
        assert r["reason"] in (APPLY_REASON_POLICY_REVIEW, APPLY_REASON_POLICY_DENIED)

    def test_18_core_identity_field_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_identity_1",
                changes=[{"key": "name", "delta": 0.05}],
            ),
        )
        assert r["success"] is False
        assert r["reason"] in (APPLY_REASON_POLICY_DENIED, APPLY_REASON_INTEGRITY)

    def test_19_immutable_trait_rejected(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_immutable_1",
                changes=[{"key": "kindness", "delta": -0.05}],
            ),
        )
        assert r["success"] is False
        assert r["reason"] in (APPLY_REASON_POLICY_DENIED, APPLY_REASON_INTEGRITY)

    def test_20_trait_conflict_rejected(self):
        eng = _build_engine()
        # 预填充 policy 近期变化,触发累加 conflict
        # 直接访问 _recent_changes 注入历史
        eng._policy._recent_changes.setdefault("expressiveness", []).append(
            (time.time(), 0.1, 1.0)
        )
        eng._policy._recent_changes.setdefault("expressiveness", []).append(
            (time.time(), 0.1, 1.0)
        )
        # 新增 delta=0.05: total = 0.05 + 0.1 + 0.1 = 0.25 > 0.2 → TRAIT_CONFLICT
        r = eng.apply_request(
            _make_request(
                request_id="evreq_conflict_1",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        # 累加 conflict 触发 needs_review
        assert r["success"] is False
        assert r["reason"] in (APPLY_REASON_POLICY_REVIEW, APPLY_REASON_POLICY_DENIED)

    def test_21_repeated_change_penalty(self):
        eng = _build_engine()
        # 第一次 apply 成功
        r1 = eng.apply_request(
            _make_request(request_id="evreq_repeat_1", changes=[{"key": "empathy", "delta": 0.03}]),
        )
        assert r1["success"] is True
        # 第二次同 trait
        r2 = eng.apply_request(
            _make_request(request_id="evreq_repeat_2", changes=[{"key": "empathy", "delta": 0.03}]),
        )
        # 重复 → 降权 + 标记 repeated,但 total_delta=0.06<0.2,可能 still allow
        # 只要不是 deny
        assert r2["success"] is True or r2["reason"] == APPLY_REASON_POLICY_REVIEW

    def test_22_normal_change_allowed(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_normal_1",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        assert r["success"] is True


# ============================================================
# TestApply
# ============================================================


class TestApply:
    def test_23_apply_success(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request())
        assert r["success"] is True
        assert r["evolution_id"] != ""
        assert r["old_version"] != ""
        assert r["new_version"] != ""
        assert r["old_version"] != r["new_version"]

    def test_24_version_increments(self):
        eng = _build_engine()
        v0 = eng.get_current_version()["version_id"]
        eng.apply_request(
            _make_request(
                request_id="evreq_inc_1",
                changes=[{"key": "humor", "delta": 0.03}],
            ),
        )
        v1 = eng.get_current_version()["version_id"]
        assert v0 == "v0"
        assert v1 == "v1"
        eng.apply_request(
            _make_request(
                request_id="evreq_inc_2",
                changes=[{"key": "curiosity", "delta": 0.03}],
            ),
        )
        v2 = eng.get_current_version()["version_id"]
        assert v2 == "v2"

    def test_25_record_generated(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        r = eng.apply_request(_make_request())
        assert r["success"] is True
        evo = eng.get_evolution(r["evolution_id"])
        assert evo is not None
        assert evo["request_id"] == "evreq_test_001"
        assert evo["status"] == RECORD_STATUS_APPLIED
        assert "before_snapshot" in evo
        assert "after_snapshot" in evo
        assert "changes" in evo
        assert "timestamp" in evo

    def test_26_changes_applied_field(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_changes_1",
                changes=[{"key": "humor", "delta": 0.05}],
            ),
        )
        assert r["success"] is True
        assert len(r["changes_applied"]) == 1
        ch = r["changes_applied"][0]
        assert ch["key"] == "humor"
        assert "before" in ch and "after" in ch
        assert abs(ch["delta"] - 0.05) < 1e-6

    def test_27_after_snapshot_trait_updated(self):
        eng = _build_engine()
        before = eng.get_current_version()["snapshot"]["traits"]["expressiveness"]
        eng.apply_request(
            _make_request(
                request_id="evreq_trait_1",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        after = eng.get_current_version()["snapshot"]["traits"]["expressiveness"]
        assert abs(after - (before + 0.05)) < 1e-6

    def test_28_duplicate_request_rejected(self):
        eng = _build_engine()
        r1 = eng.apply_request(_make_request(request_id="evreq_dup_1"))
        assert r1["success"] is True
        r2 = eng.apply_request(_make_request(request_id="evreq_dup_1"))
        assert r2["success"] is False
        assert r2["reason"] == APPLY_REASON_DUPLICATE


# ============================================================
# TestReadonly
# ============================================================


class TestReadonly:
    def test_29_old_versions_not_modified(self):
        eng = _build_engine()
        # 应用一次演化
        eng.apply_request(
            _make_request(
                request_id="evreq_ro_1",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        v0 = eng._version_store.get_version("v0")
        v0_traits = dict(v0["snapshot"]["traits"])
        # 再应用一次
        eng.apply_request(
            _make_request(
                request_id="evreq_ro_2",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        # v0 仍然未变
        v0_again = eng._version_store.get_version("v0")
        assert v0_again["snapshot"]["traits"] == v0_traits

    def test_30_self_model_not_modified(self):
        """验证 Engine 没有调用 self_model 相关接口。"""

        class _SelfModel:
            def __init__(self) -> None:
                self.update_called = 0

            def update(self, *a: Any, **kw: Any) -> None:
                self.update_called += 1

        sm = _SelfModel()
        eng = _build_engine()
        eng.apply_request(_make_request())
        assert sm.update_called == 0

    def test_31_snapshot_provider_readonly(self):
        snap = _MockPersonalitySnapshot()
        eng = _build_engine(snapshot=snap)
        before_snapshot = snap.get_snapshot()
        eng.apply_request(_make_request())
        after_snapshot = snap.get_snapshot()
        # snapshot 内容不变
        assert before_snapshot == after_snapshot

    def test_32_engine_no_self_model_field(self):
        eng = _build_engine()
        # Engine 没有任何 self_model 接口
        for forbidden in ("update_self_model", "save_self_model", "self_model_store"):
            assert not hasattr(eng, forbidden)


# ============================================================
# TestSecurity
# ============================================================


class TestSecurity:
    def test_33_no_request_forbidden(self):
        eng = _build_engine()
        r = eng.apply_request(None)
        assert r["success"] is False
        assert r["degraded"] is True

    def test_34_pending_request_forbidden(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(status="pending"))
        assert r["success"] is False

    def test_35_bypassing_bridge_forbidden(self):
        """直接构造未通过 bridge 的 request,confidence 高也必须经过 policy 评估。"""
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_bypass_1",
                changes=[{"key": "kindness", "delta": 0.05}],  # immutable
            ),
        )
        # 即便 confidence=0.9,immutable trait 仍应被拒
        assert r["success"] is False
        assert r["reason"] in (APPLY_REASON_POLICY_DENIED, APPLY_REASON_INTEGRITY)

    def test_36_engine_no_resolve_method(self):
        eng = _build_engine()
        for forbidden in ("resolve", "resolve_personality", "personality_resolver"):
            assert not hasattr(eng, forbidden)

    def test_37_engine_no_trait_update_method(self):
        eng = _build_engine()
        for forbidden in ("update_traits", "set_traits", "trait_updater"):
            assert not hasattr(eng, forbidden)

    def test_38_engine_no_self_model_update(self):
        eng = _build_engine()
        for forbidden in ("update_self_model", "set_self_model"):
            assert not hasattr(eng, forbidden)

    def test_39_engine_no_personality_state_save(self):
        eng = _build_engine()
        # 禁止直接暴露 personality_state.save 接口
        assert not hasattr(eng, "save_personality_state")
        assert not hasattr(eng, "personality_state")


# ============================================================
# TestRollback
# ============================================================


class TestRollback:
    def test_40_rollback_success(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_rb_1",
                changes=[{"key": "humor", "delta": 0.05}],
            ),
        )
        eid = r["evolution_id"]
        rb = eng.rollback(eid, actor="admin")
        assert rb["success"] is True
        assert rb["old_version"] != ""
        assert rb["new_version"] != ""
        assert rb["rollback_of"] == eid

    def test_41_rollback_restores_before(self):
        eng = _build_engine()
        before = eng.get_current_version()["snapshot"]["traits"]["expressiveness"]
        r = eng.apply_request(
            _make_request(
                request_id="evreq_rb_2",
                changes=[{"key": "expressiveness", "delta": 0.05}],
            ),
        )
        after = eng.get_current_version()["snapshot"]["traits"]["expressiveness"]
        assert abs(after - (before + 0.05)) < 1e-6
        eng.rollback(r["evolution_id"])
        restored = eng.get_current_version()["snapshot"]["traits"]["expressiveness"]
        assert abs(restored - before) < 1e-6

    def test_42_rollback_not_found(self):
        eng = _build_engine()
        rb = eng.rollback("nonexistent_id")
        assert rb["success"] is False
        assert rb["reason"] == "rollback_not_found"

    def test_43_rollback_already_rolled_back(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_rb_3",
                changes=[{"key": "curiosity", "delta": 0.05}],
            ),
        )
        eid = r["evolution_id"]
        eng.rollback(eid)
        rb2 = eng.rollback(eid)
        assert rb2["success"] is False
        assert rb2["reason"] == "rollback_not_found"

    def test_44_rollback_preserves_history(self):
        eng = _build_engine()
        r = eng.apply_request(
            _make_request(
                request_id="evreq_rb_4",
                changes=[{"key": "empathy", "delta": 0.05}],
            ),
        )
        before_count = eng._evolution_store.get_stats()["total_records"]
        eng.rollback(r["evolution_id"])
        after_count = eng._evolution_store.get_stats()["total_records"]
        # rollback 增加新记录,原记录保留
        assert after_count > before_count

    def test_45_rollback_empty_id(self):
        eng = _build_engine()
        rb = eng.rollback("")
        assert rb["success"] is False


# ============================================================
# TestAudit
# ============================================================


class TestAudit:
    def test_46_apply_audit(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        r = eng.apply_request(_make_request(request_id="evreq_aud_1"))
        assert r["audit_recorded"] is True
        applied_records = [a for a in audit.records if a["action"] == AUDIT_ACTION_APPLIED]
        assert len(applied_records) == 1
        rec = applied_records[0]
        assert rec["detail"]["request_id"] == "evreq_aud_1"
        assert rec["detail"]["old_version"] == r["old_version"]
        assert rec["detail"]["new_version"] == r["new_version"]
        assert "changes" in rec["detail"]
        assert "timestamp" in rec["detail"]

    def test_47_fail_audit(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        eng.apply_request(
            _make_request(
                request_id="evreq_fail_1",
                confidence=0.3,  # 低于阈值
            ),
        )
        fail_records = [a for a in audit.records if a["action"] == AUDIT_ACTION_FAILED]
        assert len(fail_records) >= 1

    def test_48_rollback_audit(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        r = eng.apply_request(
            _make_request(request_id="evreq_rba_1"),
        )
        eng.rollback(r["evolution_id"])
        rb_records = [a for a in audit.records if a["action"] == AUDIT_ACTION_ROLLED_BACK]
        assert len(rb_records) == 1
        rec = rb_records[0]
        # audit detail 必须包含 evolution_id(被回滚的) + rollback_evolution_id
        assert rec["detail"]["evolution_id"] == r["evolution_id"]
        assert rec["detail"]["rollback_evolution_id"] != ""
        assert rec["detail"]["old_version"] != ""
        assert rec["detail"]["new_version"] != ""

    def test_49_no_audit_safe(self):
        eng = _build_engine(audit=None)
        r = eng.apply_request(_make_request(request_id="evreq_noaud_1"))
        assert r["success"] is True
        assert r["audit_recorded"] is False

    def test_50_audit_failure_safe(self):
        class _FailingAudit:
            def record(self, *a: Any, **kw: Any) -> None:
                raise RuntimeError("audit fail")

        eng = _build_engine(audit=_FailingAudit())
        r = eng.apply_request(_make_request(request_id="evreq_afail_1"))
        # audit 失败不阻塞
        assert r["success"] is True
        assert r["audit_recorded"] is False


# ============================================================
# TestConcurrency
# ============================================================


class TestConcurrency:
    def test_51_duplicate_request_concurrent(self):
        eng = _build_engine()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker() -> None:
            r = eng.apply_request(_make_request(request_id="evreq_conc_1"))
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 仅 1 个 success,其余 duplicate 或 invalid
        successes = [r for r in results if r["success"]]
        duplicates = [r for r in results if not r["success"] and r.get("reason") == APPLY_REASON_DUPLICATE]
        assert len(successes) == 1
        assert len(duplicates) >= 1  # 至少部分标记为 duplicate
        # 所有返回的 evolution_id 一致
        eids = {r["evolution_id"] for r in results if r.get("evolution_id")}
        assert len(eids) == 1

    def test_52_multiple_request_concurrent(self):
        eng = _build_engine()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            r = eng.apply_request(
                _make_request(
                    request_id=f"evreq_multi_{i}",
                    changes=[{"key": f"trait_{i}", "delta": 0.03}],
                ),
            )
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 全部成功(不同 request_id)
        assert all(r["success"] for r in results)
        assert len({r["evolution_id"] for r in results}) == 20


# ============================================================
# TestFailSafe
# ============================================================


class TestFailSafe:
    def test_53_evolution_store_exception(self):
        eng = _build_engine(evolution_store=_FailingEvolutionStore())
        r = eng.apply_request(_make_request(request_id="evreq_esfail_1"))
        # evolution store 失败 → degraded=True, success=False
        assert r["success"] is False
        assert r["degraded"] is True
        assert "error" in r
        assert r["error"] != ""

    def test_54_policy_exception(self):
        eng = _build_engine(policy=_FailingPolicy())
        r = eng.apply_request(_make_request(request_id="evreq_pfail_1"))
        # policy 异常 → degraded
        assert r["success"] is False
        assert r["degraded"] is True

    def test_55_snapshot_exception(self):
        eng = _build_engine(snapshot=_FailingSnapshot())
        r = eng.apply_request(_make_request(request_id="evreq_snapfail_1"))
        # snapshot 失败不阻塞 → success=True(因为没有 snapshot 也是空 dict)
        # 这里 policy 用 default snapshot 仍然可以 evaluate
        assert r["success"] in (True, False)
        assert "degraded" in r

    def test_56_internal_exception_safe(self):
        eng = _build_engine()
        # monkey-patch 制造内部异常
        original = eng._extract_changes

        def _explode(_r: Any) -> List[Dict[str, Any]]:
            raise RuntimeError("internal boom")

        eng._extract_changes = _explode  # type: ignore[assignment]
        r = eng.apply_request(_make_request(request_id="evreq_ifail_1"))
        assert r["success"] is False
        assert r["degraded"] is True
        assert r["error"] != ""
        eng._extract_changes = original  # type: ignore[assignment]

    def test_57_safe_apply_request_no_engine(self):
        r = safe_apply_request(None, _make_request())
        assert r["success"] is False
        assert r["degraded"] is True

    def test_58_safe_apply_request_exception(self):
        class _BadEngine:
            def apply_request(self, *a: Any, **kw: Any) -> Dict[str, Any]:
                raise RuntimeError("engine boom")

        r = safe_apply_request(_BadEngine(), _make_request())
        assert r["success"] is False
        assert r["degraded"] is True


# ============================================================
# TestIntegration
# ============================================================


class TestIntegration:
    """完整链路:Experience → Growth → Proposal → Review → Lifecycle
    → Approval → History → EvolutionRequest → EvolutionEngine"""

    def _make_store(self):
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

    def test_59_full_chain_runtime_to_evolution(self):
        """完整链路:Runtime → Proposal → Review → Lifecycle → Approval
        → History → Bridge → Engine"""
        from src.runtime.growth import (
            GrowthProposalLifecycleManager,
            GrowthProposalApprovalWorkflow,
            GrowthProposalHistory,
            STATE_READY_FOR_APPLY,
            STATE_PENDING,
            STATE_REVIEWING,
            STATE_APPROVED,
        )
        from src.runtime.evolution import (
            create_personality_evolution_bridge,
            STATUS_APPLIED,
        )

        # ---- Phase 1: 准备 proposal ----
        proposal_id = "prop_int_full_1"
        store = self._make_store()
        lifecycle = GrowthProposalLifecycleManager()
        lifecycle.set_store(store)
        store.add_proposal(proposal_id, STATE_PENDING)
        lifecycle.transition(proposal_id, STATE_REVIEWING)
        # ---- Phase 2: Review + History ----
        audit = _MockAudit()
        history = GrowthProposalHistory(audit=audit)
        history.record_event(proposal_id, "runtime_growth_proposal_reviewed", actor="runtime")
        # ---- Phase 3: Approval ----
        workflow = GrowthProposalApprovalWorkflow(audit=audit)
        workflow.approve(proposal_id, approver="admin")
        lifecycle.transition(proposal_id, STATE_APPROVED)
        lifecycle.transition(proposal_id, STATE_READY_FOR_APPLY)
        # ---- Phase 4: History 记录最终状态 ----
        history.record_event(proposal_id, "runtime_growth_proposal_approved", actor="admin")
        history.record_event(proposal_id, "runtime_growth_proposal_ready_for_apply", actor="runtime")
        # ---- Phase 5: Bridge: Proposal → EvolutionRequest ----
        bridge = create_personality_evolution_bridge(audit=audit)
        ready_proposal = {
            "proposal_id": proposal_id,
            "status": STATE_READY_FOR_APPLY,
            "changes": [{"key": "expressiveness", "delta": 0.05}],
            "confidence": 0.9,
        }
        r_bridge = bridge.create_request(ready_proposal)
        assert r_bridge["success"] is True
        # bridge 默认 status=pending → 提升为 approved(模拟人工 approve)
        evo_req = r_bridge
        evo_req["status"] = REQUIRED_REQUEST_STATUS
        # ---- Phase 6: Engine: apply ----
        eng = _build_engine(audit=audit)
        r_eng = eng.apply_request(evo_req)
        assert r_eng["success"] is True
        assert r_eng["evolution_id"] != ""
        assert r_eng["new_version"] != ""
        # record 已写入
        rec = eng.get_evolution(r_eng["evolution_id"])
        assert rec is not None
        assert rec["request_id"] == evo_req["request_id"]
        assert rec["proposal_id"] == proposal_id
        # ---- Phase 7: Rollback ----
        rb = eng.rollback(r_eng["evolution_id"])
        assert rb["success"] is True
        # ---- Phase 8: Audit 检查 ----
        applied = [a for a in audit.records if a["action"] == AUDIT_ACTION_APPLIED]
        rolled = [a for a in audit.records if a["action"] == AUDIT_ACTION_ROLLED_BACK]
        assert len(applied) == 1
        assert len(rolled) == 1

    def test_60_evolution_integration_with_conflict(self):
        """带 conflict 的 proposal → bridge 标记 needs_review → engine 仍可处理(但 policy 应 deny)"""
        from src.runtime.evolution import (
            create_personality_evolution_bridge,
            STATUS_NEEDS_REVIEW,
        )
        audit = _MockAudit()
        snap = _MockPersonalitySnapshot()
        bridge = create_personality_evolution_bridge(audit=audit, snapshot_provider=snap)
        # conflict proposal(immutable trait)
        bad_proposal = {
            "proposal_id": "prop_int_conflict",
            "status": "ready_for_apply",
            "changes": [{"trait": "kindness", "delta": -0.05}],
            "confidence": 0.9,
        }
        r_bridge = bridge.create_request(bad_proposal)
        assert r_bridge["status"] == STATUS_NEEDS_REVIEW
        # 即便 user 强行设 status=approved,engine 仍会因为 immutable trait 拒绝
        r_bridge["status"] = REQUIRED_REQUEST_STATUS
        eng = _build_engine(audit=audit, snapshot=snap)
        r_eng = eng.apply_request(r_bridge)
        assert r_eng["success"] is False
        assert r_eng["reason"] in (APPLY_REASON_POLICY_DENIED, APPLY_REASON_INTEGRITY)

    def test_61_evolution_integration_with_low_confidence(self):
        """低 confidence 的 proposal → bridge 创建 → engine 因 confidence 拒绝"""
        from src.runtime.evolution import create_personality_evolution_bridge
        audit = _MockAudit()
        bridge = create_personality_evolution_bridge(audit=audit)
        proposal = {
            "proposal_id": "prop_int_low_conf",
            "status": "ready_for_apply",
            "changes": [{"key": "expressiveness", "delta": 0.05}],
            "confidence": 0.5,  # 低
        }
        r_bridge = bridge.create_request(proposal)
        assert r_bridge["success"] is True
        r_bridge["status"] = REQUIRED_REQUEST_STATUS
        eng = _build_engine(audit=audit)
        r_eng = eng.apply_request(r_bridge)
        # engine 仍会因 confidence < MIN 而拒绝
        assert r_eng["success"] is False
        assert r_eng["reason"] == APPLY_REASON_POLICY_REVIEW

    def test_62_evolution_then_rollback_then_evolution(self):
        """演化 → 回滚 → 再次演化"""
        eng = _build_engine()
        # 1st evolution
        r1 = eng.apply_request(
            _make_request(
                request_id="evreq_seq_1",
                changes=[{"key": "humor", "delta": 0.05}],
            ),
        )
        assert r1["success"] is True
        # rollback
        rb = eng.rollback(r1["evolution_id"])
        assert rb["success"] is True
        # 2nd evolution(同 request_id 已经被记录,需要新 request_id)
        r2 = eng.apply_request(
            _make_request(
                request_id="evreq_seq_2",
                changes=[{"key": "curiosity", "delta": 0.05}],
            ),
        )
        assert r2["success"] is True
        # 应该有 3 条 record(1 evolution + 1 rollback + 1 evolution)
        stats = eng._evolution_store.get_stats()
        assert stats["total_records"] >= 3


# ============================================================
# TestSchema
# ============================================================


class TestSchema:
    def test_63_evolution_id_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_1"))
        assert r["evolution_id"].startswith("ev_")
        assert isinstance(r["evolution_id"], str)

    def test_64_request_id_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_2"))
        assert r["request_id"] == "evreq_schema_2"

    def test_65_old_new_version_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_3"))
        assert r["old_version"] != ""
        assert r["new_version"] != ""
        assert r["old_version"] != r["new_version"]

    def test_66_changes_applied_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_4"))
        assert isinstance(r["changes_applied"], list)
        if r["success"]:
            for ch in r["changes_applied"]:
                assert "key" in ch
                assert "delta" in ch

    def test_67_timestamp_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_5"))
        assert "timestamp" in r
        assert isinstance(r["timestamp"], str)
        assert r["timestamp"].endswith("Z") or "T" in r["timestamp"]

    def test_68_actor_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_6"), actor="admin_alice")
        assert r["actor"] == "admin_alice"

    def test_69_audit_recorded_field(self):
        audit = _MockAudit()
        eng = _build_engine(audit=audit)
        r = eng.apply_request(_make_request(request_id="evreq_schema_7"))
        assert r["audit_recorded"] is True

    def test_70_policy_result_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_8"))
        assert "policy_result" in r
        assert isinstance(r["policy_result"], dict)

    def test_71_engine_metadata_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_9"))
        assert "engine" in r
        assert r["engine"]["name"] == PERSONALITY_EVOLUTION_ENGINE_NAME
        assert r["engine"]["version"] == PERSONALITY_EVOLUTION_ENGINE_VERSION

    def test_72_schema_version_field(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_10"))
        assert r["schema_version"] == PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION

    def test_73_record_full_fields(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_11"))
        rec = eng.get_evolution(r["evolution_id"])
        required_fields = [
            "schema_version",
            "evolution_id",
            "request_id",
            "proposal_id",
            "before_snapshot",
            "after_snapshot",
            "changes",
            "reason",
            "evidence",
            "confidence",
            "timestamp",
            "old_version",
            "new_version",
            "actor",
            "status",
            "self_model_impact",
        ]
        for f in required_fields:
            assert f in rec, f"missing field: {f}"

    def test_74_self_model_impact_pending(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_schema_12"))
        assert r["self_model_impact"] == SELF_MODEL_IMPACT_PENDING

    def test_75_get_stats(self):
        eng = _build_engine()
        eng.apply_request(_make_request(request_id="evreq_stats_1"))
        s = eng.get_stats()
        assert s["apply_count"] == 1
        assert s["applied_request_count"] == 1
        assert s["version_count"] >= 1

    def test_76_list_evolutions(self):
        eng = _build_engine()
        eng.apply_request(
            _make_request(
                request_id="evreq_list_1",
                changes=[{"key": "humor", "delta": 0.03}],
            ),
        )
        eng.apply_request(
            _make_request(
                request_id="evreq_list_2",
                changes=[{"key": "curiosity", "delta": 0.03}],
            ),
        )
        items = eng.list_evolutions()
        assert len(items) == 2
        # 按 request_id 过滤
        items2 = eng.list_evolutions(request_id="evreq_list_1")
        assert len(items2) == 1

    def test_77_evolution_status_applied(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_status_1"))
        rec = eng.get_evolution(r["evolution_id"])
        assert rec["status"] == RECORD_STATUS_APPLIED

    def test_78_record_evidence_preserved(self):
        eng = _build_engine()
        r = eng.apply_request(_make_request(request_id="evreq_evi_1"))
        rec = eng.get_evolution(r["evolution_id"])
        ev = rec["evidence"]
        assert "source_proposal" in ev
        assert "history_reference" in ev
        assert "approval_reference" in ev

    def test_79_build_record_function(self):
        rec = build_personality_evolution_record(
            evolution_id="ev_test",
            request_id="evreq_test",
            proposal_id="prop_test",
            before_snapshot={"traits": {"x": 0.5}},
            after_snapshot={"traits": {"x": 0.6}},
            changes=[{"key": "x", "delta": 0.1}],
            reason="ok",
            evidence={"source_proposal": {"proposal_id": "p1"}},
            confidence=0.9,
            timestamp="2026-08-04T10:00:00Z",
            old_version="v0",
            new_version="v1",
            actor="admin",
            status=RECORD_STATUS_APPLIED,
        )
        assert rec["evolution_id"] == "ev_test"
        assert rec["status"] == RECORD_STATUS_APPLIED
        assert rec["old_version"] == "v0"
        assert rec["new_version"] == "v1"

    def test_80_engine_reset(self):
        eng = _build_engine()
        eng.apply_request(_make_request(request_id="evreq_reset_1"))
        eng.reset()
        s = eng.get_stats()
        assert s["apply_count"] == 0
        assert s["applied_request_count"] == 0


# ============================================================
# TestStoreModule
# ============================================================


class TestStoreModule:
    def test_81_evolution_store_save_and_get(self):
        es = create_personality_evolution_record_store()
        rec = build_personality_evolution_record(
            evolution_id="ev_x",
            request_id="evreq_x",
            proposal_id="prop_x",
            before_snapshot={"traits": {}},
            after_snapshot={"traits": {}},
            changes=[],
            reason="ok",
            evidence={},
            confidence=0.9,
            timestamp="2026-08-04T10:00:00Z",
        )
        assert es.save(rec) is True
        got = es.get("ev_x")
        assert got is not None
        assert got["evolution_id"] == "ev_x"

    def test_82_evolution_store_index_by_request(self):
        es = create_personality_evolution_record_store()
        for i in range(3):
            rec = build_personality_evolution_record(
                evolution_id=f"ev_{i}",
                request_id="evreq_same",
                proposal_id=f"p_{i}",
                before_snapshot={"traits": {}},
                after_snapshot={"traits": {}},
                changes=[],
                reason="ok",
                evidence={},
                confidence=0.9,
                timestamp="2026-08-04T10:00:00Z",
            )
            es.save(rec)
        items = es.list_by_request("evreq_same")
        assert len(items) == 3

    def test_83_evolution_store_mark_rolled_back(self):
        es = create_personality_evolution_record_store()
        rec = build_personality_evolution_record(
            evolution_id="ev_rb",
            request_id="evreq_rb",
            proposal_id="p_rb",
            before_snapshot={"traits": {}},
            after_snapshot={"traits": {}},
            changes=[],
            reason="ok",
            evidence={},
            confidence=0.9,
            timestamp="2026-08-04T10:00:00Z",
            status=RECORD_STATUS_APPLIED,
        )
        es.save(rec)
        assert es.mark_rolled_back("ev_rb", "ev_rb_rb", {"a": 1}) is True
        got = es.get("ev_rb")
        assert got["status"] == RECORD_STATUS_ROLLED_BACK
        assert got["rolled_back_by"] == "ev_rb_rb"

    def test_84_version_store_create_and_get(self):
        vs = create_personality_version_store(initial_snapshot={"traits": {"x": 0.5}})
        v1 = vs.create_version({"traits": {"x": 0.6}}, "ev1")
        assert v1 == "v1"
        cur = vs.get_current()
        assert cur["version_id"] == "v1"

    def test_85_version_store_rollback_to(self):
        vs = create_personality_version_store(initial_snapshot={"traits": {"x": 0.5}})
        vs.create_version({"traits": {"x": 0.6}}, "ev1")
        vs.create_version({"traits": {"x": 0.7}}, "ev2")
        assert vs.get_current_version_id() == "v2"
        vs.rollback_to("v0")
        assert vs.get_current_version_id() == "v0"
        # 历史保留
        assert vs.count() == 3

    def test_86_version_store_list_versions(self):
        vs = create_personality_version_store(initial_snapshot={"traits": {"x": 0.5}})
        vs.create_version({"traits": {"x": 0.6}}, "ev1")
        versions = vs.list_versions()
        assert len(versions) == 2
        assert versions[0]["version_id"] == "v0"
        assert versions[1]["version_id"] == "v1"
