# -*- coding: utf-8 -*-
"""
P2.6 Phase A — Governance Audit Probe 测试

验证（任务书 §5）：
    1. probe 正常记录（JSONL append-only）
    2. audit 写失败不影响业务（swallow exception）
    3. 旧链行为结果完全一致（apply / apply_proposal / evaluate 返回值不变）
    4. 默认 config 下无治理行为变化（flag 默认 False，探针与该 flag 无关）

conftest 单例陷阱规避：GrowthEngine 通过 getattr 取类 + 伪 GrowthState 桩，
不直接构造任何受管单例。
"""
from __future__ import annotations

import json
import os

import pytest

import src.growth.growth_engine as growth_engine_module
from src.governance.audit_probe import (
    GovernanceAuditProbe,
    get_governance_audit_probe,
    set_governance_audit_probe,
)
from src.governance.governance_unification import (
    is_governance_unification_enabled,
    set_governance_unification_enabled,
)
from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA
from src.personality.self_model_governance import (
    GovernanceAction,
    SelfModelGovernancePolicy,
)


@pytest.fixture(autouse=True)
def _probe_env(tmp_path):
    """每个测试：独立临时审计目录 + flag 复位 + 单例隔离。"""
    probe = GovernanceAuditProbe(audit_dir=str(tmp_path / "audit"))
    set_governance_audit_probe(probe)
    set_governance_unification_enabled(False)
    yield probe
    set_governance_audit_probe(None)
    set_governance_unification_enabled(False)


class _FakeGrowthState:
    """最小 GrowthState 桩：满足 GrowthEngine 写入路径所需接口。"""

    def __init__(self):
        self._data = {"growth_history": [], "metrics": {}}
        self.save_count = 0

    def get(self):
        return self._data

    def get_metric(self, key):
        return self._data.get("metrics", {}).get(key, 0.0)

    def update_metrics(self, delta):
        self._data["metrics"].update(delta)

    def add_milestone(self, event_id, topic):
        pass

    def save(self):
        self.save_count += 1

    def reset(self):
        self._data = {"growth_history": [], "metrics": {}}
        self.save_count = 0


def _make_engine(state):
    engine_cls = getattr(growth_engine_module, "GrowthEngine")
    return engine_cls(state=state)


def _read_lines(probe):
    path = os.path.join(probe.audit_dir, "governance_audit_probe.jsonl")
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ============================================================
# 1. probe 正常记录
# ============================================================
def test_probe_records_jsonl_entry(_probe_env):
    ok = _probe_env.record(
        source_path="legacy_growth_apply",
        domain="growth",
        mutation_type="growth_state_update",
        decision="direct_apply",
        payload_summary={"proposal_id": "p1"},
        triggered_by="legacy_growth_pipeline",
        request_id="",
    )
    assert ok is True

    entries = _read_lines(_probe_env)
    assert len(entries) == 1
    entry = entries[0]
    for key in (
        "timestamp",
        "source_path",
        "domain",
        "mutation_type",
        "decision",
        "payload_summary",
        "triggered_by",
        "request_id",
    ):
        assert key in entry
    assert entry["source_path"] == "legacy_growth_apply"
    assert entry["domain"] == "growth"
    assert entry["decision"] == "direct_apply"
    assert entry["payload_summary"] == {"proposal_id": "p1"}
    assert entry["timestamp"]


def test_probe_append_only_multiple_records(_probe_env):
    _probe_env.record(source_path="a", domain="growth", mutation_type="m", decision="d")
    _probe_env.record(source_path="b", domain="growth", mutation_type="m", decision="d")
    assert len(_read_lines(_probe_env)) == 2


def test_get_probe_singleton():
    set_governance_audit_probe(None)
    p1 = get_governance_audit_probe()
    p2 = get_governance_audit_probe()
    assert p1 is p2


# ============================================================
# 2. audit 写失败不影响业务
# ============================================================
def test_probe_write_failure_swallowed(tmp_path):
    blocker = tmp_path / "audit_blocker"
    blocker.write_text("i am a file")
    probe = GovernanceAuditProbe(audit_dir=str(blocker / "sub"))
    ok = probe.record(source_path="x", domain="growth", mutation_type="m", decision="d")
    assert ok is False  # 静默吞掉，绝不抛出


# ============================================================
# 3. 旧链行为结果完全一致 + P2/P2b 观察点
# ============================================================
def test_apply_proposal_records_and_behavior_unchanged(_probe_env):
    state = _FakeGrowthState()
    engine = _make_engine(state)
    proposal = {
        "id": "p_phase_a",
        "source_event_id": "e1",
        "proposed_changes": [{"path": "curiosity", "after": 0.05}],
        "confidence": 0.9,
        "evidence_ids": [],
    }

    result = engine.apply_proposal(proposal)

    # 旧链行为完全一致
    assert result["status"] == "applied"
    assert result["mode"] == "proposal"
    assert result["proposal_id"] == "p_phase_a"
    assert result["before"] == {"curiosity": 0.0}
    expected_delta = round(min(0.05 * 0.9, MAX_SINGLE_EVENT_DELTA), 4)
    assert result["delta"]["curiosity"] == expected_delta
    assert state._data["metrics"]["curiosity"] == pytest.approx(expected_delta)
    assert state.save_count == 1

    # P2 观察点：legacy_growth_apply / direct_apply
    entries = _read_lines(_probe_env)
    assert len(entries) == 1
    assert entries[0]["source_path"] == "legacy_growth_apply"
    assert entries[0]["domain"] == "growth"
    assert entries[0]["decision"] == "direct_apply"
    assert entries[0]["payload_summary"]["proposal_id"] == "p_phase_a"
    assert entries[0]["payload_summary"]["dimensions"] == ["curiosity"]


def test_apply_proposal_rejected_path_records_nothing(_probe_env):
    """被拒绝的 proposal（无 changes）不产生审计记录——未发生写入。"""
    state = _FakeGrowthState()
    engine = _make_engine(state)
    result = engine.apply_proposal({"id": "p_rej", "proposed_changes": []})
    assert result["status"] == "rejected"
    assert state.save_count == 0
    assert not os.path.exists(
        os.path.join(_probe_env.audit_dir, "governance_audit_probe.jsonl")
    )


def test_apply_event_records_and_behavior_unchanged(_probe_env, monkeypatch):
    monkeypatch.setattr(
        growth_engine_module,
        "resolve_meaning",
        lambda event: event.get("meaning", "unknown"),
    )
    state = _FakeGrowthState()
    engine = _make_engine(state)
    event = {
        "event_id": "e_apply",
        "event_type": "companionship",
        "event_identity": "companionship::t",
        "topic": "t",
        "meaning": "companionship",
        "importance": 0.5,
        "is_first_occurrence": True,
    }

    result = engine.apply(event)

    # 旧链行为完全一致
    assert result["status"] == "applied"
    assert result["mode"] == "first"
    assert result["meaning"] == "companionship"
    assert result["delta"]
    assert state._data["metrics"]
    assert state.save_count == 1

    # P2b 观察点：legacy_growth_event_apply
    entries = _read_lines(_probe_env)
    assert len(entries) == 1
    assert entries[0]["source_path"] == "legacy_growth_event_apply"
    assert entries[0]["domain"] == "growth"
    assert entries[0]["decision"] == "direct_apply"
    assert entries[0]["payload_summary"]["event_id"] == "e_apply"
    assert entries[0]["payload_summary"]["mode"] == "first"


def test_audit_failure_does_not_affect_business(tmp_path, monkeypatch):
    blocker = tmp_path / "audit_blocker"
    blocker.write_text("i am a file")
    set_governance_audit_probe(
        GovernanceAuditProbe(audit_dir=str(blocker / "sub"))
    )
    monkeypatch.setattr(
        growth_engine_module,
        "resolve_meaning",
        lambda event: event.get("meaning", "unknown"),
    )

    state = _FakeGrowthState()
    engine = _make_engine(state)
    proposal = {
        "id": "p_fail",
        "proposed_changes": [{"path": "curiosity", "after": 0.05}],
        "confidence": 0.9,
        "evidence_ids": [],
    }
    result = engine.apply_proposal(proposal)
    assert result["status"] == "applied"  # 审计写失败不影响业务
    assert state.save_count == 1

    event = {
        "event_id": "e_fail",
        "event_type": "companionship",
        "event_identity": "companionship::t",
        "topic": "t",
        "meaning": "companionship",
        "importance": 0.5,
        "is_first_occurrence": True,
    }
    result2 = engine.apply(event)
    assert result2["status"] == "applied"
    assert state.save_count == 2


# ============================================================
# P3 观察点：SelfModelGovernancePolicy 决策记录
# ============================================================
@pytest.mark.parametrize(
    "growth_record, expected_action, expected_threshold",
    [
        ({"growth_level": "context", "confidence": 0.55}, "auto_apply", 0.50),
        ({"growth_level": "preference", "confidence": 0.70}, "auto_apply", 0.65),
        (
            {"growth_level": "preference", "confidence": 0.60},
            "approval_required",
            0.65,
        ),
        ({"growth_level": "trait", "confidence": 0.90}, "approval_required", 0.80),
        ({"growth_level": "identity", "confidence": 0.95}, "deny", 0.90),
    ],
)
def test_policy_decisions_recorded(
    _probe_env, growth_record, expected_action, expected_threshold
):
    policy = SelfModelGovernancePolicy()
    decision = policy.evaluate(dict(growth_record))

    # 决策结果与 Phase A 之前完全一致
    assert decision.action.value == expected_action

    entries = _read_lines(_probe_env)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["source_path"] == "legacy_self_model_policy"
    assert entry["domain"] == "self_model"
    assert entry["decision"] == expected_action
    assert entry["payload_summary"]["growth_level"] == growth_record["growth_level"]
    assert entry["payload_summary"]["confidence"] == growth_record["confidence"]
    assert entry["payload_summary"]["threshold_context"] == expected_threshold


def test_policy_decision_unchanged_with_broken_audit(tmp_path):
    blocker = tmp_path / "audit_blocker"
    blocker.write_text("i am a file")
    set_governance_audit_probe(
        GovernanceAuditProbe(audit_dir=str(blocker / "sub"))
    )

    policy = SelfModelGovernancePolicy()
    decision = policy.evaluate({"growth_level": "preference", "confidence": 0.70})
    assert decision.action == GovernanceAction.AUTO_APPLY
    assert decision.confidence == 0.70


def test_policy_evaluate_equivalence_rule_and_wrapper():
    """evaluate 与纯规则 _evaluate_rule 决策等价（探针只附加记录）。"""
    policy = SelfModelGovernancePolicy()
    for level, conf in (
        ("context", 0.51),
        ("context", 0.49),
        ("preference", 0.66),
        ("trait", 0.85),
        ("identity", 0.99),
        ("unknown_level", 0.5),
    ):
        record = {"growth_level": level, "confidence": conf}
        assert policy.evaluate(record) == policy._evaluate_rule(record)


# ============================================================
# 4. 默认 config 下无治理行为变化
# ============================================================
def test_flag_default_false_and_not_read_by_probe(_probe_env):
    assert is_governance_unification_enabled() is False

    set_governance_unification_enabled(True)
    try:
        assert is_governance_unification_enabled() is True
        # Phase A：探针与该开关无关——开关开启时决策与记录行为不变
        policy = SelfModelGovernancePolicy()
        decision = policy.evaluate({"growth_level": "context", "confidence": 0.55})
        assert decision.action == GovernanceAction.AUTO_APPLY
        entries = _read_lines(_probe_env)
        assert len(entries) == 1
        assert entries[0]["decision"] == "auto_apply"
    finally:
        set_governance_unification_enabled(False)

    assert is_governance_unification_enabled() is False
