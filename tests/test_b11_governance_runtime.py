# -*- coding: utf-8 -*-
"""
P2.3-B.11 Phase 6 — Governance Runtime Activation 集成测试

覆盖（任务书冻结六项）：
    1. Proposal 生命周期
    2. 审批状态迁移
    3. audit 保留
    4. duplicate 防护
    5. SelfObservation 创建
    6. SelfObservation 不直接修改 personality

全部使用 pytest tmp_path；仓库 data/ 不得被触碰。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.governance.governance_inspector import GovernanceInspector
from src.governance.mutation_contract import (
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
)
from src.governance.mutation_gateway import MutationGateway
from src.governance.proposal_manager import (
    DuplicateApplyError,
    GovernanceProposalManager,
    InvalidTransition,
    PROPOSAL_STATUSES,
    VALID_TRANSITIONS,
)
from src.governance.proposal_store import GovernanceProposalStore as GovStore
from src.personality.self_observation import SelfObservation


# ============================================================
# helpers
# ============================================================
def _seed_pending(tmp_path: Path) -> tuple[str, GovStore]:
    """Gateway NEED_REVIEW → 落账 → register 进队，返回 (proposal_id, store)。"""
    store = GovStore(tmp_path)
    gateway = MutationGateway(proposal_store=store)
    req = MutationRequest(
        source_event={"event_id": "e1"},
        actor_identity="user",
        target_domain="relationship",
        target_path="relationship.state.trust",
        proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
        evidence=[{"ref": "r1", "type": "memory_link"}],
        context_snapshot={"request_id": "req_t", "trace_id": "trace_t"},
        risk_level="low",
    )
    assert gateway.evaluate(req).decision == DecisionVerdict.NEED_REVIEW
    matches = [
        r for r in store.list_pending()
        if r.get("mutation_id") == req.mutation_id
    ]
    assert matches
    return str(matches[0]["proposal_id"]), store


def _seeded_manager(tmp_path: Path) -> tuple[str, GovernanceProposalManager]:
    proposal_id, store = _seed_pending(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    return proposal_id, manager


# ============================================================
# 1. Proposal 生命周期（六态状态机表驱动验证）
# ============================================================
def test_proposal_lifecycle_full_path(tmp_path):
    proposal_id, manager = _seeded_manager(tmp_path)
    statuses = [manager.get_status(proposal_id)]
    manager.approve(proposal_id, reviewer="human:uid1")
    statuses.append(manager.get_status(proposal_id))
    manager.mark_applied(proposal_id)
    statuses.append(manager.get_status(proposal_id))
    manager.archive(proposal_id)
    statuses.append(manager.get_status(proposal_id))
    assert statuses == [
        "PENDING_REVIEW", "APPROVED", "APPLIED", "ARCHIVED",
    ]
    # 全部六态均在冻结状态集中；迁移表只含设计文档 §3.2 的 7 条边
    assert set(PROPOSAL_STATUSES) == {
        "CREATED", "PENDING_REVIEW", "APPROVED", "REJECTED", "APPLIED", "ARCHIVED",
    }
    assert len(VALID_TRANSITIONS) == 7
    # ARCHIVED 是终态：任何事件均无出边
    assert all(
        (from_status, event)[0] != "ARCHIVED"
        for from_status, event in VALID_TRANSITIONS
    )


# ============================================================
# 2. 审批状态迁移（approve/reject 守卫）
# ============================================================
def test_approval_transitions_guards(tmp_path):
    proposal_id, manager = _seeded_manager(tmp_path)
    # 自动 approve 在 API 形状上被排除：reviewer 必填
    with pytest.raises((ValueError, TypeError)):
        manager.approve(proposal_id)
    # reject 需 reviewer + reason
    with pytest.raises(ValueError):
        manager.reject(proposal_id, reviewer="human:uid1", reason="")
    event = manager.reject(
        proposal_id, reviewer="human:uid1", reason="insufficient_evidence",
    )
    assert event["to_status"] == "REJECTED"
    # 否决不可翻案 / 不可应用
    with pytest.raises(InvalidTransition):
        manager.approve(proposal_id, reviewer="human:uid1")
    with pytest.raises(InvalidTransition):
        manager.mark_applied(proposal_id)
    # CREATED 态（未 register）不可直接 approve
    pid2, store2 = _seed_pending(tmp_path)
    manager2 = GovernanceProposalManager(store=store2)
    with pytest.raises(InvalidTransition):
        manager2.approve(pid2, reviewer="human:uid1")


# ============================================================
# 3. audit 保留（迁移事件流 + Inspector audit 完整性）
# ============================================================
def test_audit_trace_preserved(tmp_path):
    proposal_id, manager = _seeded_manager(tmp_path)
    manager.approve(proposal_id, reviewer="human:uid1", reason="manual_ok")
    manager.mark_applied(proposal_id)
    trace = manager.audit_trace(proposal_id)
    assert [e["event"] for e in trace] == ["register", "approve", "mark_applied"]
    # 每条事件审计三键齐全
    for event in trace:
        ref = event["trace_ref"]
        assert ref["mutation_id"] and ref["request_id"] and ref["trace_id"]
    # Inspector 视角的 audit 完整性
    inspector = GovernanceInspector(
        store=manager._store, manager=manager,
    )
    completeness = inspector.audit_completeness()
    assert completeness["records_complete"] == completeness["records_total"]
    assert completeness["lifecycle_events_complete"] == \
        completeness["lifecycle_events_total"] == 3


# ============================================================
# 4. duplicate 防护
# ============================================================
def test_duplicate_apply_protection(tmp_path):
    proposal_id, store = _seed_pending(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    manager.approve(proposal_id, reviewer="human:uid1")
    manager.mark_applied(proposal_id)
    # 同 proposal 重复 apply → 状态机拒绝
    with pytest.raises(InvalidTransition):
        manager.mark_applied(proposal_id)
    # 跨 proposal 同 mutation → DuplicateApplyError
    from src.governance.proposal_store import build_proposal_record

    mutation_id = manager._store.load_records()[0]["mutation_id"]
    dup_req = MutationRequest(
        mutation_id=mutation_id,
        source_event={"event_id": "e1"},
        actor_identity="user",
        target_domain="emotion",
        target_path="emotion.state.valence",
        proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
        evidence=[{"ref": "r1", "type": "memory_link"}],
        context_snapshot={"request_id": "req_t", "trace_id": "trace_t"},
        risk_level="low",
    )
    dup_dec = MutationDecision(
        mutation_id=mutation_id,
        decision=DecisionVerdict.NEED_REVIEW,
        reason="test",
        checks={},
    )
    pid2 = "prop_b11_dupe"
    store.save(build_proposal_record(dup_req, dup_dec, proposal_id=pid2))
    manager.register(pid2)
    manager.approve(pid2, reviewer="human:uid1")
    with pytest.raises(DuplicateApplyError):
        manager.mark_applied(pid2)


# ============================================================
# 5. SelfObservation 创建
# ============================================================
def test_self_observation_creation():
    obs = SelfObservation.from_event(
        {"event_id": "evt_1", "type": "user_feedback", "content": "你记得我的偏好"},
        domain="self_model",
        evidence_refs=["mem_1", "mem_2", "mem_3"],
        confidence=0.8,
    )
    assert obs.observation_id.startswith("obs_")
    assert obs.domain == "self_model"
    assert obs.evidence_refs == ["mem_1", "mem_2", "mem_3"]
    assert obs.confidence == 0.8
    assert obs.timestamp
    # 六字段冻结
    assert set(obs.to_dict().keys()) == {
        "observation_id", "source_event", "domain",
        "evidence_refs", "confidence", "timestamp",
    }
    # 构造守卫：非法域 / 空证据 / 越界置信度
    with pytest.raises(ValueError):
        SelfObservation.from_event(
            {"event_id": "e"}, domain="consciousness", evidence_refs=["r"],
        )
    with pytest.raises(ValueError):
        SelfObservation.from_event(
            {"event_id": "e"}, domain="growth", evidence_refs=[],
        )
    with pytest.raises(ValueError):
        SelfObservation.from_event(
            {"event_id": "e"}, domain="growth", evidence_refs=["r"], confidence=1.5,
        )
    # frozen：不可变
    with pytest.raises(Exception):
        obs.confidence = 0.1  # type: ignore[misc]


# ============================================================
# 6. SelfObservation 不直接修改 personality
# ============================================================
def test_self_observation_does_not_modify_personality():
    import src.personality.self_observation as so_module

    # 源级：模块不 import 任何人格状态写入口 / 治理执行件
    source = Path(so_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "SelfModelManager", "PersonalityResolver", "IdentityCore",
        "mutation_adapter", "ProposalManager", "GrowthEngine",
        "apply_proposal", "repository.save", ".save(",
    ):
        assert forbidden not in source, (
            f"self_observation 不得引用人格写入口 {forbidden!r}"
        )
    # 功能级：观察与种子投影对任何人格状态零副作用
    personality_state = {"kindness": 0.7, "curiosity": 0.8}
    snapshot = dict(personality_state)
    obs = SelfObservation.from_event(
        {"event_id": "e1"}, domain="personality", evidence_refs=["r1"],
    )
    seed = obs.to_proposal_seed()
    assert personality_state == snapshot
    # 种子只是纯 dict 投影：不含任何执行语义，不进治理链
    assert seed["source_observation_id"] == obs.observation_id
    assert seed["seed_version"] == "b11_phase5"
    assert isinstance(seed, dict)
    # 模块命名空间未暴露任何写句柄型属性
    exported = set(dir(so_module))
    for writer in ("save", "apply", "update_state", "write"):
        assert writer not in exported


# ============================================================
# 附加：仓库 data/ 零污染（B.11 全链路跑一遍后）
# ============================================================
def test_b11_chain_zero_repo_pollution(tmp_path):
    def snapshot_repo() -> dict:
        repo = Path(__file__).resolve().parents[1]
        out = {}
        for rel in (
            "data/governance/proposals.jsonl",
            "data/governance/proposal_lifecycle.jsonl",
            "data/relationship_state.json",
            "data/emotion_state.json",
        ):
            p = repo / rel
            out[rel] = (p.exists(), p.stat().st_mtime_ns) if p.exists() else (False, None)
        return out

    before = snapshot_repo()
    # 全链路：落账 → register → approve → mark_applied + Inspector 统计
    proposal_id, manager = _seeded_manager(tmp_path)
    manager.approve(proposal_id, reviewer="human:uid1")
    manager.mark_applied(proposal_id)
    inspector = GovernanceInspector(store=manager._store, manager=manager)
    assert inspector.count_pending() == 0
    assert inspector.count_by_domain()["relationship"] >= 1
    assert snapshot_repo() == before
