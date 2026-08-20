# -*- coding: utf-8 -*-
"""
P2.3-B.11 Phase 2 — GovernanceProposalManager 测试

覆盖（任务书 Phase 2）：
    - 创建 proposal（Gateway 落账 → register 进队）
    - pending 查询
    - approve（人工审批接口：reviewer 必填）
    - reject
    - duplicate apply 防护

全部使用 pytest tmp_path；仓库 data/ 不得被触碰。
"""
from __future__ import annotations

from pathlib import Path

import pytest

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
    ProposalNotFoundError,
)
from src.governance.proposal_store import GovernanceProposalStore as GovStore


# ============================================================
# helpers
# ============================================================
def _persisted_proposal(
    tmp_path: Path,
    *,
    domain: str = "relationship",
    path: str = "relationship.state.trust",
    mutation_id: str = "",
) -> tuple[str, GovStore, MutationRequest]:
    """走真实链路：Gateway NEED_REVIEW → ProposalStore 落账，返回 proposal_id。"""
    store = GovStore(tmp_path)
    gateway = MutationGateway(proposal_store=store)
    kwargs = dict(
        source_event={"event_id": "e1"},
        actor_identity="user",
        target_domain=domain,
        target_path=path,
        proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
        evidence=[{"ref": "r1", "type": "memory_link"}],
        context_snapshot={"request_id": "req_t", "trace_id": "trace_t"},
        risk_level="low",
    )
    if mutation_id:
        kwargs["mutation_id"] = mutation_id
    req = MutationRequest(**kwargs)
    decision = gateway.evaluate(req)
    assert decision.decision == DecisionVerdict.NEED_REVIEW
    records = [
        r for r in store.list_pending()
        if r.get("mutation_id") == req.mutation_id
    ]
    assert records, "Gateway 应已落账"
    return str(records[0]["proposal_id"]), store, req


# ============================================================
# 1. 创建 proposal（落账 + register 进队）
# ============================================================
def test_register_moves_created_to_pending_review(tmp_path):
    proposal_id, store, req = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    # 落账即 CREATED
    assert manager.get_status(proposal_id) == "CREATED"
    event = manager.register(proposal_id)
    assert event["from_status"] == "CREATED"
    assert event["to_status"] == "PENDING_REVIEW"
    assert manager.get_status(proposal_id) == "PENDING_REVIEW"
    # 重复 register → 非法迁移（PENDING_REVIEW 无 register 出边）
    with pytest.raises(InvalidTransition):
        manager.register(proposal_id)
    # 未落账的 proposal → ProposalNotFoundError（fail-closed）
    with pytest.raises(ProposalNotFoundError):
        manager.register("prop_nonexistent")


# ============================================================
# 2. pending 查询
# ============================================================
def test_list_by_status_pending(tmp_path):
    proposal_id, store, _ = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    assert manager.list_by_status("PENDING_REVIEW") == []
    manager.register(proposal_id)
    pending = manager.list_by_status("PENDING_REVIEW")
    assert len(pending) == 1
    assert pending[0]["proposal_id"] == proposal_id
    # CREATED 视图：未注册的落账 proposal
    pid2, _, _ = _persisted_proposal(
        tmp_path, domain="emotion", path="emotion.state.valence",
    )
    assert manager.get_status(pid2) == "CREATED"


# ============================================================
# 3. approve（reviewer 必填 = 人工审批接口预留）
# ============================================================
def test_approve_requires_explicit_reviewer(tmp_path):
    proposal_id, store, _ = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    # 无 reviewer → 签名层 TypeError（API 形状排除自动 approve）；
    # 空 reviewer → ValueError（运行时防御）
    with pytest.raises((ValueError, TypeError)):
        manager.approve(proposal_id)
    with pytest.raises(ValueError):
        manager.approve(proposal_id, reviewer="   ")
    event = manager.approve(
        proposal_id, reviewer="human:pending_design", reason="manual_ok",
    )
    assert event["to_status"] == "APPROVED"
    assert event["reviewer"] == "human:pending_design"
    assert manager.get_status(proposal_id) == "APPROVED"


# ============================================================
# 4. reject（终态，不可翻案）
# ============================================================
def test_reject_is_terminal(tmp_path):
    proposal_id, store, _ = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    event = manager.reject(
        proposal_id, reviewer="human:uid1", reason="insufficient_evidence",
    )
    assert event["to_status"] == "REJECTED"
    assert manager.get_status(proposal_id) == "REJECTED"
    # REJECTED → APPROVED 禁止（否决不可翻案）
    with pytest.raises(InvalidTransition):
        manager.approve(proposal_id, reviewer="human:uid1")
    # REJECTED → mark_applied 禁止
    with pytest.raises(InvalidTransition):
        manager.mark_applied(proposal_id)
    # 归档允许
    manager.archive(proposal_id)
    assert manager.get_status(proposal_id) == "ARCHIVED"


# ============================================================
# 5. duplicate apply 防护
# ============================================================
def test_duplicate_apply_blocked(tmp_path):
    proposal_id, store, first_req = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    manager.approve(proposal_id, reviewer="human:uid1")
    event = manager.mark_applied(proposal_id)
    assert event["to_status"] == "APPLIED"
    # 同一 proposal 重复 apply → 状态机拒绝（APPLIED 无 mark_applied 出边）
    with pytest.raises(InvalidTransition):
        manager.mark_applied(proposal_id)
    # 跨 proposal 同 mutation_id → DuplicateApplyError（mutation 维度守卫）：
    # 默认 proposal_id 由 mutation_id 派生（store 幂等跳过），需自定义
    # proposal_id 制造"同 mutation、不同 proposal"的场景
    from src.governance.proposal_store import build_proposal_record

    dup_req = MutationRequest(
        mutation_id=first_req.mutation_id,
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
        mutation_id=dup_req.mutation_id,
        decision=DecisionVerdict.NEED_REVIEW,
        reason="test",
        checks={},
    )
    pid2 = "prop_custom_dupe"
    store.save(build_proposal_record(dup_req, dup_dec, proposal_id=pid2))
    manager.register(pid2)
    manager.approve(pid2, reviewer="human:uid1")
    with pytest.raises(DuplicateApplyError):
        manager.mark_applied(pid2)


# ============================================================
# 6. audit trace 保留
# ============================================================
def test_audit_trace_preserved(tmp_path):
    proposal_id, store, _ = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    manager.approve(proposal_id, reviewer="human:uid1", reason="ok")
    manager.mark_applied(proposal_id)
    trace = manager.audit_trace(proposal_id)
    assert [e["event"] for e in trace] == [
        "register", "approve", "mark_applied",
    ]
    for event in trace:
        assert event["reviewer"] in ("", "human:uid1")
        assert event["actor"] == "proposal_manager"
        assert event["timestamp"]
        ref = event["trace_ref"]
        assert ref["mutation_id"] and ref["request_id"] and ref["trace_id"]


# ============================================================
# 7. 存储边界：append-only + 目录守卫
# ============================================================
def test_lifecycle_append_only_and_data_clean(tmp_path):
    proposal_id, store, _ = _persisted_proposal(tmp_path)
    manager = GovernanceProposalManager(store=store)
    manager.register(proposal_id)
    lifecycle = tmp_path / "proposal_lifecycle.jsonl"
    assert lifecycle.exists()
    before_lines = lifecycle.read_text(encoding="utf-8").strip().splitlines()
    manager.approve(proposal_id, reviewer="human:uid1")
    after_lines = lifecycle.read_text(encoding="utf-8").strip().splitlines()
    # 旧行原样保留（append-only）
    assert after_lines[: len(before_lines)] == before_lines
    assert len(after_lines) == len(before_lines) + 1
    # proposals.jsonl 不被改写
    proposals = (tmp_path / "proposals.jsonl").read_text(encoding="utf-8")
    assert '"decision": "NEED_REVIEW"' in proposals
    # 仓库 data/ 零污染
    repo = Path(__file__).resolve().parents[1]
    assert not (repo / "data" / "governance" / "proposal_lifecycle.jsonl").exists()
    assert not (repo / "data" / "governance" / "proposals.jsonl").exists()
