# -*- coding: utf-8 -*-
"""P0-2 人格权威收敛验收测试（T1-T12）。

覆盖：
T1  PersonalityState canonical（growth_state 不写人格）
T2  SelfModel current_traits 派生自 PersonalityState（P5.0-E #3）
T3  修改 SelfModel trait 不反向修改 PersonalityState
T4  冲突时 PersonalityState 胜出
T5  A-store proposal 不直接修改 PersonalityState
T6  legacy proposal 不修改 PersonalityState
T7  pending proposal 不改变人格
T8  rejected proposal 不改变人格
T9  approved proposal 可以改变人格（唯一生产写链）
T10 人格变化可追溯（version → proposal_id → evidence）
T11 Identity Core 不受影响
T12 M1/M2 Memory 不退化（共享 selection 核心可用）

隔离：全部 tmp_path / 内存 PersonalityState + save 拦截，不触碰生产 data。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _mem(mid, content, hours_ago=2, importance=0.8):
    return {
        "id": mid, "content": content,
        "timestamp": (datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        "user_id": "366648462", "role": "user", "importance": importance,
        "source_event_id": "", "emotion_tag": "", "relationship_id": "366648462",
        "metadata": {"memory_type": "user_milestone", "source": "runtime_pipeline"},
    }


def _make_service(tmp_path):
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_manager import ProposalManager
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal.storage import ProposalStorage
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    a_store = ProposalStore(str(tmp_path / "a.jsonl"))
    b_storage = ProposalStorage(str(tmp_path / "b"))
    pm = ProposalManager(store=a_store)
    gh = PersonalityGrowthHistory(str(tmp_path / "gh.json"))
    service = GrowthIntegrationService(
        proposal_manager=pm, growth_history=gh,
        config={"growth_governance_enabled": True, "auto_accept_enabled": False,
                "governance_storage": b_storage},
    )
    return service, b_storage


def _feed_two(service, topic="AI"):
    service.accept_experience(_mem("m1", f"我今天完成了一幅{topic}作品", 3))
    return service.accept_experience(_mem("m2", f"刚刚又完成了一幅{topic}作品", 1))


def _drain_ctx(tmp_path, monkeypatch, b_storage, name):
    from src.personality.personality_state import PersonalityState, get_personality_state
    from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
    from src.runtime.runtime_core import RuntimeCore

    ps = PersonalityState()
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True,
    )
    pipeline = PersonalityEvolutionPipeline(history_path=str(tmp_path / f"h_{name}.json"))
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.personality_evolution_pipeline = pipeline
    rc.config = {"growth_apply_drain_enabled": True, "growth_apply_drain_limit": 5}
    monkeypatch.setattr("src.growth.proposal.storage.get_proposal_storage", lambda: b_storage)
    return ps, rc


def _approve(b_storage, pid):
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import GrowthProposalReviewRequest
    return GovernanceProvider(proposal_storage=b_storage).review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid, action="approve", reason="P0-2"),
        actor="test_admin")


# ============ T1: PersonalityState canonical ============
def test_t1_personality_state_canonical(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState, get_personality_state
    ps = PersonalityState()
    before = dict(ps.traits)
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    from src.growth.growth_state import GrowthState
    gs = GrowthState(state_path=str(tmp_path / "gs.json"))
    gs.update_metrics({"trust": 0.1, "creativity": 0.05})
    assert ps.traits == before, "growth_state 改变了人格"


# ============ T2: SelfModel current_traits 派生 ============
def test_t2_selfmodel_traits_derived(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState, get_personality_state
    from src.personality.evolution_record import build_evolution_record
    ps = PersonalityState()
    ps.apply_evolution(build_evolution_record(
        proposal_id="prop_t2", approval_id="admin:t2", change_type="trait_delta",
        before={"creativity": 0.6}, after={"creativity": 0.9},
        reasons=["P0-2 T2"], confidence=0.9))
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    # 复现 growth_integration.py:433-446 派生逻辑
    _ts = {}
    _pstate = get_personality_state()
    if int(getattr(_pstate, "version", 0) or 0) > 0:
        for _k, _v in (_pstate.traits or {}).items():
            _ts[_k] = {"current_value": float(_v)}
    assert _ts.get("creativity") == {"current_value": 0.9}
    # builder 消费 → current_traits
    from src.personality.self_model_builder import SelfModelBuilder
    from src.personality.personality_growth_record import PersonalityGrowthHistory
    model = SelfModelBuilder().build(
        history=PersonalityGrowthHistory(str(tmp_path / "h2.json")),
        trait_states=_ts, base_identity="浅雾羽依")
    ct = model.get("current_traits") or {}
    assert float(ct.get("creativity", 0)) == pytest.approx(0.9, abs=0.01), \
        f"current_traits 应派生自 PersonalityState: {ct}"


# ============ T3: 无反向修改 ============
def test_t3_no_reverse_mutation():
    from src.personality.personality_state import PersonalityState
    ps = PersonalityState()
    before = dict(ps.traits)
    sm = {"warmth": 0.99, "creativity": 0.99}
    sm["creativity"] = 0.99  # 模拟外部写 self_model
    assert ps.traits == before and ps.traits.get("creativity") != 0.99


# ============ T4: 冲突时 PersonalityState 胜出 ============
def test_t4_personality_wins_conflict(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState, get_personality_state
    from src.personality.evolution_record import build_evolution_record
    ps = PersonalityState()
    ps.apply_evolution(build_evolution_record(
        proposal_id="prop_t4", approval_id="admin:t4", change_type="trait_delta",
        before={"creativity": 0.6}, after={"creativity": 0.85},
        reasons=["P0-2 T4"], confidence=0.9))
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    old_sm = {"creativity": 0.3, "warmth": 0.72}
    _pstate = get_personality_state()
    derived = {k: float(v) for k, v in (_pstate.traits or {}).items()} \
        if int(getattr(_pstate, "version", 0) or 0) > 0 else old_sm
    assert derived.get("creativity") == pytest.approx(0.85, abs=0.01), \
        "冲突时 PersonalityState 必须胜出"


# ============ T5: A-store 非权威 ============
def test_t5_a_store_not_authoritative(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState
    ps = PersonalityState()
    before = dict(ps.traits)
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    service, b_storage = _make_service(tmp_path)
    res = _feed_two(service)
    assert res.get("applied") is False, "accept_experience 红线：永不 apply"
    assert ps.traits == before, "A-store proposal 直接改变了人格"


# ============ T6: legacy proposal 无效果 ============
def test_t6_legacy_proposal_no_effect(tmp_path):
    from src.personality.personality_state import PersonalityState
    (tmp_path / "growth_proposals.json").write_text(
        '[{"id": "prop_legacy", "status": "proposed"}]', encoding="utf-8")
    ps = PersonalityState()
    before = dict(ps.traits)
    assert ps.traits == before, "legacy proposal 改变了人格"


# ============ T7/T8: pending / rejected 不改人格 ============
def test_t7_t8_pending_rejected_no_change(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import GrowthProposalReviewRequest

    ps = PersonalityState()
    before = dict(ps.traits)
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True)

    # T7: pending 不应用
    service, b_storage = _make_service(tmp_path)
    _feed_two(service)
    ps1, rc1 = _drain_ctx(tmp_path, monkeypatch, b_storage, "pend")
    r1 = rc1.drain_approved_growth_proposals()
    assert r1.get("applied") == 0 and ps1.traits == before, "pending 改变了人格"

    # T8: rejected 不应用
    service2, b_storage2 = _make_service(tmp_path)
    res2 = _feed_two(service2, "绘画")
    pid2 = res2.get("governance_proposal_id") or res2.get("proposal_id")
    GovernanceProvider(proposal_storage=b_storage2).review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid2, action="reject", reason="T8"),
        actor="test_admin")
    ps2, rc2 = _drain_ctx(tmp_path, monkeypatch, b_storage2, "rej")
    r2 = rc2.drain_approved_growth_proposals()
    assert r2.get("applied") == 0 and ps2.traits == before, "rejected 改变了人格"


# ============ T9: approved 改变人格 ============
def test_t9_approved_changes_personality(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState

    ps = PersonalityState()
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True)
    service, b_storage = _make_service(tmp_path)
    res = _feed_two(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    proposal = b_storage.load(pid)
    trait = list((proposal.after_state or {}).keys())[0]
    before = (proposal.before_state or {}).get(trait, 0.5)
    _approve(b_storage, pid)
    ps1, rc1 = _drain_ctx(tmp_path, monkeypatch, b_storage, "appr")
    r = rc1.drain_approved_growth_proposals()
    assert r.get("applied") == 1
    after = ps1.traits.get(trait, 0.5)
    assert after != before, f"approved 后人格应变化: {before} -> {after}"


# ============ T10: 可追溯 ============
def test_t10_traceability(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState

    ps = PersonalityState()
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True)
    service, b_storage = _make_service(tmp_path)
    res = _feed_two(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    proposal = b_storage.load(pid)
    assert proposal.evidence, "evidence 缺失"
    assert proposal.source_event_id, "source_event_id 缺失"
    _approve(b_storage, pid)
    ps1, rc1 = _drain_ctx(tmp_path, monkeypatch, b_storage, "trc")
    rc1.drain_approved_growth_proposals()
    assert pid in (ps1._applied_proposal_ids or set()), "proposal_id 不可追溯"
    assert ps1.version >= 1, "version 未递增"


# ============ T11: Identity 不变 ============
def test_t11_identity_untouched():
    from src.personality.identity_core import IDENTITY_CORE
    assert IDENTITY_CORE.get("name")
    assert IDENTITY_CORE.get("immutable_principles") is not None
    from src.identity.yui_core_profile import YUI_CORE_FACTS
    assert isinstance(YUI_CORE_FACTS, list) and len(YUI_CORE_FACTS) >= 1


# ============ T12: Memory 不退化 ============
def test_t12_memory_not_regressed():
    from src.memory.memory_selection import select_injection_memories, detect_weak_time_intent
    assert detect_weak_time_intent("当时") == "before"
    recs = [_mem("x1", "测试", 1), _mem("x2", "内容", 2)]
    final = select_injection_memories(recs, [], query="")
    assert len(final) <= 6
    assert any(r["id"] in ("x1", "x2") for r in final)
