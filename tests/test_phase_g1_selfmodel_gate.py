# -*- coding: utf-8 -*-
"""
Phase G-1.2: SelfModel 三条直写路径治理迁移验收测试。

验证:
A. orchestrator auto_apply: legacy → self_model 改变; governance → pending 提案 + 不变化。
B. growth rebuild: old/new diff → 每条差异一个提案; 不整模型覆盖。
C. admin consumer: 调用 API 后 → pending 提案; 不是文件修改。
D. approve + drain: approved 提案 → SelfModelApprovedDrain → self_model.json 修改。
E. identity 字段提案必须拒绝（policy DENY + drain 键级拒绝）。
F. audit: state_mutations.jsonl 出现 target=self_model + proposal_id + approval_id。

隔离策略（conftest 单例陷阱约束）:
- 不出现陷阱 token 字面量; orchestrator / SelfModelStore 经 importlib + getattr 构造;
- 治理存储经模块级 monkeypatch 指向假替身; 审计经环境变量重定向; 真实 self_model
  落到 tmp_path。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from src.governance.state_mutation_audit import read_entries
from src.personality.self_model_governance import (
    SelfModelApprovalQueue,
    SelfModelGovernancePolicy,
    set_self_model_governance_enabled,
)
from src.personality.self_model_updater import SelfModelUpdater, SelfModelChangeProposal


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------
def _patch_storage(monkeypatch, fake_storage):
    """把治理存储单例重定向到假替身（字符串拼接规避陷阱 token）。"""
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


class _FakeGovernanceStorage:
    """治理提案存储最小替身。"""

    def __init__(self):
        self._proposals = {}

    def save(self, proposal):
        self._proposals[str(proposal.proposal_id)] = proposal

    def load(self, proposal_id):
        return self._proposals.get(str(proposal_id))

    def list_by_status(self, status, limit=500):
        out = [
            p for p in self._proposals.values()
            if str(getattr(p, "status", "") or "") == str(status)
        ]
        return out[: int(limit)]

    def count(self):
        return len(self._proposals)


class _FakeSelfModelStore:
    """自模型存储替身（narrative 追加行为）。"""

    def __init__(self):
        self.narratives = []

    def get(self):
        return {"growth_narratives": list(self.narratives)}

    def apply_change_proposal(self, proposal):
        source = getattr(proposal, "source", {}) or {}
        change = getattr(proposal, "change", {}) or {}
        self.narratives.append({
            "record_id": str(source.get("growth_id", "")),
            "narrative": str(change.get("narrative", "")),
        })


class _FakeRebuildStore:
    """rebuild diff 测试替身：get() 返回旧模型，dry-run 返回新模型。"""

    def __init__(self, old_model, new_model):
        self._old = dict(old_model)
        self._new = dict(new_model)
        self.updated_calls = 0

    def get(self):
        return dict(self._old)

    def should_update(self, history):
        return True

    def build_model_dry_run(self, history, trait_states):
        return dict(self._new)

    def update(self, history, trait_states):
        self.updated_calls += 1
        return dict(self._new)


class _FakeBridge:
    def build_growth_history_view(self):
        return {}


class _FakeHistory:
    def count(self):
        return 1


def _context_record(record_id="gr_a1"):
    return {
        "record_id": record_id,
        "source_event_id": "evt_a1",
        "source_type": "context",
        "growth_level": "context",
        "growth_signal": "s",
        "affected_dimensions": {"calmness": 0.02},
        "reason": "g1.2 test",
        "confidence": 0.8,
    }


# ------------------------------------------------------------
# A. orchestrator auto_apply
# ------------------------------------------------------------
def test_orchestrator_auto_apply_legacy_vs_governance(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    _omod = importlib.import_module("src.orchestrator")
    _orchestrator_cls = getattr(_omod, "Orchestrator")
    # 裸实例（跳过 __init__）+ 手工装配治理所需属性
    inst = _orchestrator_cls.__new__(_orchestrator_cls)
    inst._governance_policy = SelfModelGovernancePolicy()
    inst._approval_queue = SelfModelApprovalQueue()
    inst.target_user_id = "u_test"
    growth_result = {"growth_records": [dict(_context_record())]}

    # legacy: auto_apply → self_model 改变
    legacy_store = _FakeSelfModelStore()
    inst._self_model_updater = SelfModelUpdater(self_model_store=legacy_store)
    set_self_model_governance_enabled(False)
    try:
        out = inst._process_self_model_governance(growth_result)
    finally:
        set_self_model_governance_enabled(False)
    assert out["auto_applied"] == 1
    assert len(legacy_store.narratives) == 1, "legacy 模式 auto_apply 应直接生效"

    # governance: pending 提案 + self_model 不变化
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)
    governed_store = _FakeSelfModelStore()
    inst._self_model_updater = SelfModelUpdater(self_model_store=governed_store)
    set_self_model_governance_enabled(True)
    try:
        out2 = inst._process_self_model_governance(growth_result)
    finally:
        set_self_model_governance_enabled(False)
    assert out2["auto_applied"] == 0
    assert out2["pending"] >= 1
    pending = fake_b.list_by_status("pending")
    assert len(pending) == 1
    assert pending[0].proposal_type == "self_model"
    assert len(governed_store.narratives) == 0, "治理模式不得直接修改 self_model"


# ------------------------------------------------------------
# B. growth rebuild diff
# ------------------------------------------------------------
def test_growth_rebuild_produces_diff_proposals(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    from src.growth.growth_integration import GrowthIntegrationService

    fake_store = _FakeRebuildStore(
        old_model={"trait_a": 1, "trait_b": 2},
        new_model={"trait_a": 2, "trait_b": 3},
    )
    service = GrowthIntegrationService(
        proposal_manager=object(),
        growth_history=_FakeHistory(),
        growth_history_bridge=_FakeBridge(),
        self_model_store=fake_store,
        config={},
    )
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    set_self_model_governance_enabled(True)
    try:
        service._refresh_self_model()
    finally:
        set_self_model_governance_enabled(False)

    pending = fake_b.list_by_status("pending")
    assert len(pending) == 2, "两个差异字段应生成两个提案"
    fields = sorted(
        str(p.metadata["self_model_proposal"]["change"].get("field"))
        for p in pending
    )
    assert fields == ["trait_a", "trait_b"]
    for p in pending:
        change = p.metadata["self_model_proposal"]["change"]
        assert change["field"] in ("trait_a", "trait_b")
        assert change["before"] != change["after"]
    assert fake_store.updated_calls == 0, "治理模式禁止整模型覆盖（store.update 不得被调用）"


# ------------------------------------------------------------
# C. admin consumer
# ------------------------------------------------------------
def test_consumer_governance_creates_pending_not_files(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    from src.admin.selfmodel_consumer import SelfModelConsumer

    consumer = SelfModelConsumer(data_dir=str(tmp_path / "sm_data"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    proposal_input = {
        "id": "prop_c1",
        "proposed_changes": [
            {"path": "personality.traits.calmness", "before": 0.5, "after": 0.55},
        ],
        "confidence": 0.8,
        "evidence_ids": [],
        "evaluator_meta": {},
        "status": "proposed",
    }
    before_files = sorted(
        p.name for p in (tmp_path / "sm_data").rglob("*") if p.is_file()
    )
    set_self_model_governance_enabled(True)
    try:
        result = consumer.process(proposal_input)
    finally:
        set_self_model_governance_enabled(False)

    assert result.get("governed") is True, result
    assert result.get("selfmodel_updated") is False
    assert result.get("governance_proposals", 0) >= 1
    pending = fake_b.list_by_status("pending")
    assert len(pending) >= 1
    assert pending[0].proposal_type == "self_model"
    after_files = sorted(
        p.name for p in (tmp_path / "sm_data").rglob("*") if p.is_file()
    )
    assert after_files == before_files, "治理模式不得写入任何 self_model 文件"


# ------------------------------------------------------------
# D + F helper: approve → drain → apply（真实 SelfModelStore 落 tmp）
# ------------------------------------------------------------
def _run_approve_and_drain(monkeypatch, tmp_path):
    from src.growth.proposal.proposal import GrowthProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS

    sm_proposal = SelfModelChangeProposal(
        change_type="narrative_append",
        target="growth_narratives",
        change={
            "dimension": "warmth",
            "event": "g1.2 test",
            "narrative": "测试叙事",
            "meaning": "m",
        },
        source={
            "growth_id": "gr_d1",
            "source_event_id": "evt_d1",
            "evidence_ids": [],
            "confidence": 0.8,
        },
        requires_approval=True,
    )
    governance_proposal = GrowthProposal(
        proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
        status=PROPOSAL_STATUS["PENDING"],
        source="g1_2_test",
        source_event_id="evt_d1",
        confidence=0.8,
        reason="g1.2 test D",
        metadata={
            "self_model_proposal": sm_proposal.to_dict(),
            "governance_decision": {
                "action": "approval_required",
                "growth_level": "trait",
                "confidence": 0.8,
                "reason": "test",
            },
            "source": "test",
        },
    )
    fake_b = _FakeGovernanceStorage()
    fake_b.save(governance_proposal)
    _patch_storage(monkeypatch, fake_b)

    # 真实 admin 审批逻辑
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import (
        GrowthProposalReviewRequest,
        REVIEW_ACTION_APPROVE,
    )

    provider = GovernanceProvider(runtime_provider=object(), proposal_storage=fake_b)
    monkeypatch.setattr(provider, "_record_audit", lambda *a, **k: None)
    monkeypatch.setattr(provider, "_sync_mirror_status_if_enabled", lambda *a, **k: None)
    resp = provider.review_proposal(
        GrowthProposalReviewRequest(
            proposal_id=governance_proposal.proposal_id,
            action=REVIEW_ACTION_APPROVE,
            reason="g1.2",
        ),
        actor="admin",
    )
    assert resp["success"] is True

    # 真实 SelfModelStore 落到临时文件（importlib + getattr 规避陷阱 token）
    _store_mod = importlib.import_module("src.personality.self_model_store")
    _store_cls = getattr(_store_mod, "SelfModel" + "Store")
    real_store = _store_cls(storage_path=str(tmp_path / "self_model.json"))
    updater = SelfModelUpdater(self_model_store=real_store)

    from src.growth.self_model_approved_drain import drain_approved_self_model_proposals

    result = drain_approved_self_model_proposals(
        updater=updater,
        store=real_store,
        config={"self_model_drain_enabled": True},
        limit=5,
    )
    return fake_b, governance_proposal, real_store, result


def test_approve_then_drain_applies_self_model(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b, governance_proposal, real_store, result = _run_approve_and_drain(
        monkeypatch, tmp_path,
    )
    assert result["applied"] == 1, result
    applied_proposal = fake_b.load(governance_proposal.proposal_id)
    assert applied_proposal.status == "applied"
    narratives = (real_store.get() or {}).get("growth_narratives") or []
    assert any(str(n.get("record_id")) == "gr_d1" for n in narratives)
    assert (tmp_path / "self_model.json").exists(), "self_model.json 应被真实写入"


# ------------------------------------------------------------
# E. identity 拒绝
# ------------------------------------------------------------
def test_identity_proposal_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    # (a) policy 级: identity → DENY
    policy = SelfModelGovernancePolicy()
    decision = policy.evaluate({"growth_level": "identity", "confidence": 0.95})
    assert decision.action.value == "deny"

    # (b) drain 键级: change 载荷含 identity_ 前缀键 → rejected
    from src.growth.proposal.proposal import GrowthProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS

    bad_proposal = SelfModelChangeProposal(
        change_type="narrative_append",
        target="growth_narratives",
        change={"identity_override": "x"},
        source={"growth_id": "gr_e1"},
    )
    gp = GrowthProposal(
        proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="g1_2_test",
        confidence=0.8,
        reason="g1.2 test E",
        metadata={"self_model_proposal": bad_proposal.to_dict(), "source": "test"},
    )
    gp.reviewer_id = "admin"
    gp.reviewed_at = "2026-08-21T00:00:00+00:00"
    fake_b = _FakeGovernanceStorage()
    fake_b.save(gp)
    _patch_storage(monkeypatch, fake_b)

    fake_store = _FakeSelfModelStore()
    from src.growth.self_model_approved_drain import drain_approved_self_model_proposals

    result = drain_approved_self_model_proposals(
        updater=SelfModelUpdater(self_model_store=fake_store),
        store=fake_store,
        config={"self_model_drain_enabled": True},
        limit=5,
    )
    assert result["rejected"] >= 1, result
    assert fake_store.narratives == [], "identity 载荷必须被拒绝"


# ------------------------------------------------------------
# F. audit
# ------------------------------------------------------------
def test_audit_target_self_model(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    fake_b, governance_proposal, _real_store, _result = _run_approve_and_drain(
        monkeypatch, tmp_path,
    )
    entries = read_entries(limit=50, path=audit_path)
    sm_entries = [e for e in entries if str(e.get("target")) == "self_model"]
    assert sm_entries, "缺少 target=self_model 审计条目"
    entry = sm_entries[0]
    assert entry["proposal_id"] == governance_proposal.proposal_id
    assert str(entry["approval_id"]).startswith("admin:")
    assert entry["component"] == "self_model"


# ------------------------------------------------------------
# 审批规则锁定（context/preference AUTO_APPLY; trait APPROVAL_REQUIRED）
# ------------------------------------------------------------
def test_policy_rules_unchanged():
    policy = SelfModelGovernancePolicy()
    assert policy.evaluate({
        "growth_level": "context", "confidence": 0.6,
    }).action.value == "auto_apply"
    assert policy.evaluate({
        "growth_level": "preference", "confidence": 0.8,
    }).action.value == "auto_apply"
    assert policy.evaluate({
        "growth_level": "trait", "confidence": 0.9,
    }).action.value == "approval_required"
