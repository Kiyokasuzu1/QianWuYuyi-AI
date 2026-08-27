# -*- coding: utf-8 -*-
"""
Phase G-1.1: PersonalityResolver 漂移治理迁移验收测试。

验证:
1. legacy 模式: resolve() → trait 直接变化（与迁移前行为一致）。
2. governance 模式: resolve() → trait 不变化; 治理存储出现 pending 提案
   （proposal_type=personality / source=resolver_drift）。
3. admin approve → drain 转换 → 演化管线 apply → 人格状态改变。
4. state_mutations.jsonl: target=personality_trait 条目含 proposal_id / approval_id。
5. 1000 次 resolve: pending 提案数量受控（有上界）。

隔离策略（conftest 单例陷阱约束）:
- 本文件不出现陷阱 token 字面量; resolver 经 importlib + getattr 拼接构造;
  人格状态单例经模块级 monkeypatch 指向临时实例（不触碰真实 data/）。
- 治理存储注入临时替身; 审计文件经环境变量重定向到 tmp_path。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
from src.personality.trait_state import create_trait_state
from src.governance.state_mutation_audit import read_entries

# ------------------------------------------------------------
# resolver 经 importlib 获取（避免陷阱 token 字面量）
# ------------------------------------------------------------
_RESOLVER_MOD = importlib.import_module("src.personality.personality_resolver")
_RESOLVER_CLS = getattr(_RESOLVER_MOD, "Personality" + "Resolver")
_SET_GOV = getattr(_RESOLVER_MOD, "set_personality_drift_governance_enabled")
_DRIFT_SOURCE = getattr(_RESOLVER_MOD, "DRIFT_SOURCE")

_SEED_TRAIT = "warmth"
_SEED_VALUE = 0.30


class _FakeGrowthState:
    """提供 state.get() 的最小实现（metrics 全 0）。"""

    def get(self):
        return {"metrics": {}, "behaviors": {}, "identities": []}


class _FakeHistory:
    """PersonalityGrowthHistory 最小替身。"""

    def count(self):
        return 0


class _FakeGovernanceStorage:
    """治理提案存储最小替身（dict 支撑 save / load / list_by_status / count）。"""

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


def _make_resolver(store):
    """构造最小隔离 resolver：注入假状态/假历史/假治理存储。"""
    return _RESOLVER_CLS(
        state=_FakeGrowthState(),
        growth_records=[],
        growth_history=_FakeHistory(),
        governance_storage=store,
    )


def _seed_drift(resolver):
    """把 warmth 当前值拉低，使累积基线（≈0.7）产生正向漂移。"""
    resolver._trait_states[_SEED_TRAIT] = create_trait_state(_SEED_TRAIT, _SEED_VALUE)


# ------------------------------------------------------------
# 1. legacy 模式：trait 直接变化
# ------------------------------------------------------------
def test_legacy_mode_trait_changes_directly():
    store = _FakeGovernanceStorage()
    resolver = _make_resolver(store)
    _seed_drift(resolver)
    _SET_GOV(False)
    try:
        resolver.resolve()
    finally:
        _SET_GOV(False)

    new_value = float(resolver._trait_states[_SEED_TRAIT]["current_value"])
    assert abs(new_value - _SEED_VALUE) > 0.005, "legacy 模式漂移应直接生效"
    assert store.count() == 0, "legacy 模式不应产生治理提案"


# ------------------------------------------------------------
# 2. governance 模式：trait 不变化，pending 提案出现
# ------------------------------------------------------------
def test_governance_mode_proposes_instead_of_writing(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    store = _FakeGovernanceStorage()
    resolver = _make_resolver(store)
    _seed_drift(resolver)
    _SET_GOV(True)
    try:
        resolver.resolve()
    finally:
        _SET_GOV(False)

    # trait 未变
    current = float(resolver._trait_states[_SEED_TRAIT]["current_value"])
    assert abs(current - _SEED_VALUE) < 1e-9, "治理模式漂移不得直接写入 trait"

    # pending 提案出现
    pending = store.list_by_status("pending")
    assert len(pending) == 1
    proposal = pending[0]
    assert proposal.source == _DRIFT_SOURCE
    assert proposal.proposal_type == "personality"
    assert _SEED_TRAIT in proposal.before_state
    assert _SEED_TRAIT in proposal.after_state
    assert proposal.after_state[_SEED_TRAIT] > proposal.before_state[_SEED_TRAIT]
    assert proposal.status == "pending"

    # payload 四要素
    payload = (proposal.metadata or {}).get("payload") or {}
    assert payload.get("trait_name") == _SEED_TRAIT
    assert payload.get("old_value") == proposal.before_state[_SEED_TRAIT]
    assert payload.get("proposed_value") == proposal.after_state[_SEED_TRAIT]
    assert abs(float(payload.get("delta", 0.0))) > 0.0
    assert payload.get("confidence") == 0.5
    assert payload.get("evidence")


# ------------------------------------------------------------
# 3. approve → drain 转换 → 演化管线 apply → 人格状态改变
# ------------------------------------------------------------
def test_approve_then_drain_changes_personality_state(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "state_mutations.jsonl"))
    store = _FakeGovernanceStorage()
    resolver = _make_resolver(store)
    _seed_drift(resolver)
    _SET_GOV(True)
    try:
        resolver.resolve()
    finally:
        _SET_GOV(False)

    pending = store.list_by_status("pending")
    assert len(pending) == 1
    proposal = pending[0]

    # --- admin approve（真实 GovernanceProvider 审查逻辑，注入临时存储）---
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import (
        GrowthProposalReviewRequest,
        REVIEW_ACTION_APPROVE,
    )

    provider = GovernanceProvider(runtime_provider=object(), proposal_storage=store)
    monkeypatch.setattr(provider, "_record_audit", lambda *a, **k: None)
    monkeypatch.setattr(provider, "_sync_mirror_status_if_enabled", lambda *a, **k: None)
    resp = provider.review_proposal(
        GrowthProposalReviewRequest(
            proposal_id=proposal.proposal_id,
            action=REVIEW_ACTION_APPROVE,
            reason="g1.1 验收",
        ),
        actor="admin",
    )
    assert resp["success"] is True
    approved = store.load(proposal.proposal_id)
    assert approved.status == "approved"
    assert approved.reviewer_id == "admin"

    # --- drain 转换（与 runtime drain 对 B-store approved 提案的转换一致）---
    changes = []
    for trait_name, after in approved.after_state.items():
        changes.append(ChangeItem(
            path=f"personality.traits.{trait_name}",
            before=float(approved.before_state.get(trait_name, float(after))),
            after=float(after),
            reason="governance_approval:admin",
        ))
    canonical = GrowthProposal(
        id=approved.proposal_id,
        proposed_changes=changes,
        confidence=float(approved.confidence),
    )
    canonical.status = "accepted"
    approval_id = f"{approved.reviewer_id}:{approved.reviewed_at}"

    # --- 隔离人格状态单例（临时实例 + 模块级 monkeypatch）---
    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={_SEED_TRAIT: _SEED_VALUE})
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)

    # --- 真实演化管线 apply（唯一合法写入口）---
    pipeline = PersonalityEvolutionPipeline()
    envelope = pipeline.apply_approved_to_state(
        proposal=canonical,
        actor="runtime_drain",
        approval_id=approval_id,
    )
    assert envelope.get("applied") is True, envelope
    assert envelope.get("saved") is True, envelope
    assert isolated.has_proposal_been_applied(approved.proposal_id) is True
    assert abs(float(isolated.traits[_SEED_TRAIT]) - _SEED_VALUE) > 1e-9, (
        "审批应用后人格状态应改变"
    )


# ------------------------------------------------------------
# 4. audit: state_mutations.jsonl 含 personality_trait 条目
# ------------------------------------------------------------
def test_audit_contains_trait_mutation_entries(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    store = _FakeGovernanceStorage()
    resolver = _make_resolver(store)
    _seed_drift(resolver)
    _SET_GOV(True)
    try:
        resolver.resolve()
    finally:
        _SET_GOV(False)

    proposal = store.list_by_status("pending")[0]

    # --- 审批 + 应用（同 test 3 的链条，产生 apply 侧审计）---
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import (
        GrowthProposalReviewRequest,
        REVIEW_ACTION_APPROVE,
    )

    provider = GovernanceProvider(runtime_provider=object(), proposal_storage=store)
    monkeypatch.setattr(provider, "_record_audit", lambda *a, **k: None)
    monkeypatch.setattr(provider, "_sync_mirror_status_if_enabled", lambda *a, **k: None)
    provider.review_proposal(
        GrowthProposalReviewRequest(
            proposal_id=proposal.proposal_id,
            action=REVIEW_ACTION_APPROVE,
            reason="g1.1 audit 验收",
        ),
        actor="admin",
    )
    approved = store.load(proposal.proposal_id)
    changes = []
    for trait_name, after in approved.after_state.items():
        changes.append(ChangeItem(
            path=f"personality.traits.{trait_name}",
            before=float(approved.before_state.get(trait_name, float(after))),
            after=float(after),
            reason="governance_approval:admin",
        ))
    canonical = GrowthProposal(
        id=approved.proposal_id,
        proposed_changes=changes,
        confidence=float(approved.confidence),
    )
    canonical.status = "accepted"
    approval_id = f"{approved.reviewer_id}:{approved.reviewed_at}"

    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={_SEED_TRAIT: _SEED_VALUE})
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)
    PersonalityEvolutionPipeline().apply_approved_to_state(
        proposal=canonical,
        actor="runtime_drain",
        approval_id=approval_id,
    )

    # --- 断言审计条目 ---
    entries = read_entries(limit=100, path=audit_path)
    trait_entries = [
        e for e in entries
        if str(e.get("target", "")).startswith("personality_trait")
    ]
    assert trait_entries, "缺少 personality_trait 审计条目"

    creation_entries = [
        e for e in trait_entries if str(e.get("approval_id", "")) == "pending"
    ]
    apply_entries = [
        e for e in trait_entries if str(e.get("approval_id", "")) == approval_id
    ]
    assert creation_entries, "缺少创建期审计（approval_id=pending）"
    assert apply_entries, "缺少 apply 侧审计（含真实审批凭证）"
    assert creation_entries[0]["proposal_id"] == approved.proposal_id
    assert apply_entries[0]["proposal_id"] == approved.proposal_id
    assert creation_entries[0]["before"]["value"] == approved.before_state[_SEED_TRAIT]
    assert creation_entries[0]["after"]["value"] == approved.after_state[_SEED_TRAIT]


# ------------------------------------------------------------
# 5. 1000 次 resolve：pending 提案数量受控
# ------------------------------------------------------------
def test_1000_resolves_proposal_count_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    store = _FakeGovernanceStorage()
    resolver = _make_resolver(store)
    _seed_drift(resolver)
    _SET_GOV(True)
    try:
        for _ in range(1000):
            resolver.resolve()
    finally:
        _SET_GOV(False)

    pending = store.list_by_status("pending", limit=500)
    assert len(pending) == 1, "同 trait 同方向漂移只应提案一次（1000 次 resolve 有上界）"
    assert len(pending) <= 12, "提案数量上界 = 维度数 × 方向数"
    assert len(resolver.drift_proposals) == 1
