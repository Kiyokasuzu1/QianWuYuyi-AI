# -*- coding: utf-8 -*-
"""P0-1 Growth Fire E2E：经历→提案→审批→应用→Prompt 可见 全链路验收。

真实走生产链路（不 mock Growth 链）：
  MemoryCreatedEvent 语义 → GrowthIntegrationService.accept_experience
  → eligibility → evaluator → proposal → 治理镜像 B-store(pending)
  → GovernanceProvider.review_proposal(approve) → approved
  → drain_approved_growth_proposals → PersonalityState.apply_evolution
  → PersonalityResolver 消费 → prompt 可见

隔离：全部 store/state 注入 tmp_path + save_personality_state 拦截，
不触碰生产 data。覆盖 Gate 1-12 + 白名单回归 + 治理状态机回归。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _mem(mid, content, hours_ago=2, importance=0.8, memory_type="user_milestone"):
    return {
        "id": mid,
        "content": content,
        "timestamp": (datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        "user_id": "366648462",
        "role": "user",
        "importance": importance,
        "source_event_id": "",
        "emotion_tag": "",
        "relationship_id": "366648462",
        "metadata": {"memory_type": memory_type, "source": "runtime_pipeline"},
    }


def _make_service(tmp_path):
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_manager import ProposalManager
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal.storage import ProposalStorage
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    a_store = ProposalStore(str(tmp_path / "a_proposals.jsonl"))
    b_storage = ProposalStorage(str(tmp_path / "b_proposals"))
    pm = ProposalManager(store=a_store)
    gh = PersonalityGrowthHistory(str(tmp_path / "growth_history.json"))
    service = GrowthIntegrationService(
        proposal_manager=pm,
        growth_history=gh,
        config={
            "growth_governance_enabled": True,
            "auto_accept_enabled": False,
            "governance_storage": b_storage,
        },
    )
    return service, b_storage


def _feed_two_observations(service, topic="AI"):
    r1 = _mem("mem_e2e_1", f"我今天完成了一幅{topic}作品", hours_ago=3)
    r2 = _mem("mem_e2e_2", f"刚刚又完成了一幅{topic}作品", hours_ago=1)
    service.accept_experience(r1)
    return service.accept_experience(r2)


def _approve(b_storage, pid, actor="test_admin"):
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import GrowthProposalReviewRequest
    return GovernanceProvider(proposal_storage=b_storage).review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid, action="approve", reason="E2E"),
        actor=actor,
    )


def _make_drain_ctx(tmp_path, monkeypatch, b_storage, name):
    """构造 drain 环境：内存 PersonalityState + pipeline + storage 注入。"""
    from src.personality.personality_state import PersonalityState, get_personality_state
    from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
    from src.runtime.runtime_core import RuntimeCore

    ps = PersonalityState()
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    # 拦截落盘（防写生产 data/personality_state.json）
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True,
    )
    pipeline = PersonalityEvolutionPipeline(
        history_path=str(tmp_path / f"evo_{name}.json"))
    rc = RuntimeCore.__new__(RuntimeCore)
    rc.personality_evolution_pipeline = pipeline
    rc.config = {"growth_apply_drain_enabled": True, "growth_apply_drain_limit": 5}
    monkeypatch.setattr("src.growth.proposal.storage.get_proposal_storage",
                        lambda: b_storage)
    return ps, rc


# ============ Gate 1-4: 提案产生 + 字段完整 + pending ============
def test_gate1_4_proposal_created_with_fields(tmp_path):
    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)

    assert res.get("proposal_id"), f"未产出 proposal: {res.get('pipeline_state')}"
    proposals = b_storage.list_all()
    assert len(proposals) == 1, f"治理 B-store 应有 1 条，实际 {len(proposals)}"
    p = proposals[0]
    # Gate 2: evidence / source_event_id / fingerprint / confidence
    assert p.evidence, f"evidence 缺失: {p}"
    assert p.source_event_id, "source_event_id 缺失"
    assert (p.metadata or {}).get("content_fingerprint"), "fingerprint 缺失"
    assert float(p.confidence or 0) > 0, "confidence 缺失"
    # Gate 3: 默认 pending
    assert p.status == "pending", f"应 pending，实际 {p.status}"
    # Gate 4: pending 不改变人格（accept_experience 红线）
    assert res.get("applied") is False


# ============ Gate 5: 人工审批 → approved ============
def test_gate5_approval_pending_to_approved(tmp_path):
    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")

    r = _approve(b_storage, pid)
    assert r.get("success"), f"审批失败: {r}"
    p = b_storage.load(pid)
    assert p.status == "approved", f"应 approved，实际 {p.status}"
    assert p.reviewed_at, "缺少 reviewed_at"
    assert p.reviewer_id, "缺少 reviewer_id"


# ============ Gate 6-8: drain → personality_state 变化 + 可追溯 ============
def test_gate6_8_drain_applies_to_personality(tmp_path, monkeypatch):
    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    # 取提案目标 trait（before 值）
    proposal_obj = b_storage.load(pid)
    trait = list((proposal_obj.after_state or {}).keys())[0]
    before = (proposal_obj.before_state or {}).get(trait, 0.5)

    _approve(b_storage, pid)
    ps, rc = _make_drain_ctx(tmp_path, monkeypatch, b_storage, "gate6")
    result = rc.drain_approved_growth_proposals()
    assert result.get("applied") == 1, f"drain 未应用: {result}"

    # Gate 7: personality_state 真正变化
    after = ps.traits.get(trait, 0.5)
    assert after != before, f"trait {trait} 未变化: {before} -> {after}"
    # Gate 8: 可追溯（proposal_id 在 applied 集合）
    assert pid in (ps._applied_proposal_ids or set()), f"applied_proposal_ids 缺 {pid}"
    assert ps.version >= 1


# ============ Gate 9: Prompt 可见性（经 apply_approved_to_state 真实链） ============
def test_gate9_prompt_visibility(tmp_path, monkeypatch):
    from src.personality.personality_state import PersonalityState, get_personality_state
    from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    ps = PersonalityState()
    before = ps.traits.get("creativity", 0.5)
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    monkeypatch.setattr(
        "src.personality.personality_state.save_personality_state",
        lambda state=None, path=None: True,
    )

    pipeline = PersonalityEvolutionPipeline(
        history_path=str(tmp_path / "evo_prompt.json"))
    proposal = GrowthProposal(
        id="prop_prompt_e2e",
        proposed_changes=[ChangeItem(
            path="personality.traits.creativity", before=before, after=0.9,
            reason="E2E prompt 可见性")],
        confidence=0.9,
    )
    proposal.status = "accepted"
    envelope = pipeline.apply_approved_to_state(
        proposal=proposal, actor="test", approval_id="admin:2026-08-26")
    assert envelope.get("applied"), f"应用失败: {envelope}"
    # delta 限幅（TraitStateUpdater 防突变）是正确行为：变化发生且方向正确
    applied_val = ps.traits.get("creativity", 0.5)
    assert applied_val > before, f"creativity 应增长: {before} -> {applied_val}"

    # PersonalityResolver 消费 → snapshot 含变化后的 trait
    from src.personality.personality_resolver import PersonalityResolver
    vec = PersonalityResolver().resolve()
    snapshot = vec.get_all() if hasattr(vec, "get_all") else vars(vec)
    text = str(snapshot)
    assert "creativity" in text, f"快照缺 creativity: {text[:300]}"
    assert "personality_state_version" in text, f"快照缺版本: {text[:300]}"


# ============ Gate 10: 重复 drain 幂等 ============
def test_gate10_drain_idempotent(tmp_path, monkeypatch):
    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    _approve(b_storage, pid)
    ps, rc = _make_drain_ctx(tmp_path, monkeypatch, b_storage, "idem")
    r1 = rc.drain_approved_growth_proposals()
    r2 = rc.drain_approved_growth_proposals()
    assert r1.get("applied") == 1, f"首次 drain 应应用: {r1}"
    assert r2.get("applied") == 0, f"重复 drain 应幂等: {r2}"
    # 幂等：提案已标 APPLIED，第二次不再消费（list_by_status(APPROVED) 为空）
    assert b_storage.load(pid).status == "applied"


# ============ Gate 11: rejected 永不改变人格 ============
def test_gate11_rejected_never_applies(tmp_path, monkeypatch):
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import GrowthProposalReviewRequest

    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    GovernanceProvider(proposal_storage=b_storage).review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid, action="reject", reason="E2E 拒绝"),
        actor="test_admin",
    )
    ps, rc = _make_drain_ctx(tmp_path, monkeypatch, b_storage, "rej")
    traits_before = dict(ps.traits)
    result = rc.drain_approved_growth_proposals()
    assert result.get("applied") == 0, f"rejected 不得应用: {result}"
    assert ps.traits == traits_before, "rejected 提案改变了人格"


# ============ 白名单回归：合法 personality target 不再被静默丢弃 ============
def test_metrics_whitelist_personality_target(tmp_path, monkeypatch):
    from src.growth.growth_state import GrowthState
    from src.personality.personality_state import PersonalityState, get_personality_state

    ps = PersonalityState()
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)

    gs = GrowthState(state_path=str(tmp_path / "growth_state.json"))
    # 合法 personality target（personality_state.traits 中的键）
    gs.update_metrics({"creativity": 0.01})
    assert "creativity" in gs._state["metrics"], "合法 personality target 被静默丢弃"
    assert gs._state["metrics"]["creativity"] > 0
    # 未知键仍拒绝
    gs.update_metrics({"totally_unknown_key": 0.5})
    assert "totally_unknown_key" not in gs._state["metrics"], "未知键不应写入"


# ============ 治理状态机回归 ============
def test_governance_state_machine(tmp_path):
    from src.admin.governance_provider import GovernanceProvider
    from src.contracts.governance_schema import GrowthProposalReviewRequest

    service, b_storage = _make_service(tmp_path)
    res = _feed_two_observations(service)
    pid = res.get("governance_proposal_id") or res.get("proposal_id")
    provider = GovernanceProvider(proposal_storage=b_storage)
    # pending → approve
    r = provider.review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid, action="approve", reason="ok"),
        actor="admin")
    assert r.get("success")
    assert b_storage.load(pid).status == "approved"
    # 已 approved 再 reject → 不允许（状态机保护）
    r2 = provider.review_proposal(
        GrowthProposalReviewRequest(proposal_id=pid, action="reject", reason="late"),
        actor="admin")
    assert not r2.get("success"), "已 approved 不应被 reject"


def test_pending_not_applied_without_approval(tmp_path, monkeypatch):
    service, b_storage = _make_service(tmp_path)
    _feed_two_observations(service)  # 产生 pending，不审批
    ps, rc = _make_drain_ctx(tmp_path, monkeypatch, b_storage, "pend")
    traits_before = dict(ps.traits)
    result = rc.drain_approved_growth_proposals()
    assert result.get("applied") == 0, f"pending 不得应用: {result}"
    assert ps.traits == traits_before, "pending 提案改变了人格"
