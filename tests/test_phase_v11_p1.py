# -*- coding: utf-8 -*-
"""
v1.1 Phase 1 验收测试: Runtime 收尾（治理闭环）。

覆盖:
1. relationship drain: approved 提案 → 运行时关系状态 apply + state_mutation 审计;
   幂等（重复 drain 只补回写, 不重复 apply）; 维度白名单; 默认关闭。
2. 内容哈希跨链去重: 同一内容经 evt_mem_* 与 evt_exp_* 两个 source_event_id
   只产一个提案（内容指纹次级键）。
3. B-store 镜像: canonical 提案 → B-store pending（治理事实来源, 字段完整）。

隔离: 假 repo / 假 A-store / 假 B-store; 审计 env 重定向; 遵守 conftest 陷阱规则。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.governance.state_mutation_audit import read_entries


class _FakeGovernanceStorage:
    def __init__(self):
        self._proposals = {}

    def save(self, proposal):
        self._proposals[str(proposal.proposal_id)] = proposal

    def load(self, proposal_id):
        return self._proposals.get(str(proposal_id))

    def list_by_status(self, status, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "status", "") == status][:limit]

    def list_by_type(self, proposal_type, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "proposal_type", "") == proposal_type][:limit]

    def list_all(self, limit=50):
        return list(self._proposals.values())[:limit]


def _patch_storage(monkeypatch, fake_storage):
    import importlib

    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


class _FakeRelState:
    def __init__(self):
        self.trust = 0.2
        self.familiarity = 0.3
        self.collaboration = 0.1
        self.interaction_frequency = 0.1
        self.updated_at = ""


class _FakeRelRepo:
    def __init__(self):
        self.state = _FakeRelState()
        self.saved = []

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.saved.append(state)


# ------------------------------------------------------------
# 1. relationship drain
# ------------------------------------------------------------
def test_relationship_drain_applies_and_audits(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    from src.growth.proposal.proposal import GrowthProposal as BProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS

    gp = BProposal(
        proposal_type=PROPOSAL_TYPE["RELATIONSHIP"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_rel_1",
        before_state={"trust": 0.2},
        after_state={"trust": 0.5},
        confidence=0.7,
        reason="v1.1 p1 验收",
    )
    gp.reviewer_id = "admin"
    gp.reviewed_at = "2026-08-21T00:00:00+00:00"
    fake_b.save(gp)

    repo = _FakeRelRepo()
    from src.relationship.relationship_approved_drain import drain_approved_relationship_proposals

    first = drain_approved_relationship_proposals(
        repository=repo,
        config={"relationship_drain_enabled": True},
        limit=5,
    )
    assert first["applied"] == 1, first
    assert abs(repo.state.trust - 0.5) < 1e-9, "批准后 trust 应更新到 after 值"
    assert fake_b.load(gp.proposal_id).status == "applied"

    entries = read_entries(limit=100, path=audit_path)
    rel_entries = [e for e in entries if e.get("component") == "relationship"]
    assert rel_entries, "缺少 relationship state_mutation 审计"
    assert rel_entries[0]["proposal_id"] == gp.proposal_id
    assert rel_entries[0]["approval_id"].startswith("admin:")
    assert rel_entries[0]["reviewer_id"] == "admin"

    # 幂等: 复位 approved → 二次 drain 只补回写
    gp.status = PROPOSAL_STATUS["APPROVED"]
    saved_before = len(repo.saved)
    second = drain_approved_relationship_proposals(
        repository=repo,
        config={"relationship_drain_enabled": True},
        limit=5,
    )
    assert second["applied"] == 1
    assert any(d.get("result") == "status_backfill" for d in second["details"])
    assert len(repo.saved) == saved_before, "不得重复 apply"
    assert abs(repo.state.trust - 0.5) < 1e-9


def test_relationship_drain_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    from src.relationship.relationship_approved_drain import drain_approved_relationship_proposals

    repo = _FakeRelRepo()
    result = drain_approved_relationship_proposals(repository=repo, config={})
    assert result["enabled"] is False
    assert result["reason"] == "disabled_by_config"


def test_relationship_drain_rejects_invalid_dimensions(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    from src.growth.proposal.proposal import GrowthProposal as BProposal
    from src.growth.proposal.constants import PROPOSAL_TYPE, PROPOSAL_STATUS

    gp = BProposal(
        proposal_type=PROPOSAL_TYPE["RELATIONSHIP"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        after_state={"personality_temperature": 0.9},
        confidence=0.7,
    )
    fake_b.save(gp)
    repo = _FakeRelRepo()
    from src.relationship.relationship_approved_drain import drain_approved_relationship_proposals

    result = drain_approved_relationship_proposals(
        repository=repo,
        config={"relationship_drain_enabled": True},
    )
    assert result["rejected"] == 1, result
    assert abs(repo.state.trust - 0.2) < 1e-9, "非法维度不得修改状态"


# ------------------------------------------------------------
# 2. 内容哈希跨链去重
# ------------------------------------------------------------
class _FakeAStore:
    def __init__(self):
        self.items = []
        self.path = Path("fake_a_store.jsonl")

    def save(self, proposal):
        self.items.append(proposal)

    def update(self, proposal):
        pass

    def load(self, pid):
        return None

    def exists_similar(self, source_event_id, fingerprint):
        return None

    def list(self, status=None, limit=50, offset=0):
        return [
            p for p in self.items
            if status is None or getattr(p, "status", "") == status
        ][offset:offset + limit]


def test_content_hash_cross_chain_dedup():
    from src.growth.proposal_manager import ProposalManager

    store = _FakeAStore()
    pm = ProposalManager(store=store, personality_adapter=None, config={})

    base = dict(
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
        evidence_ids=["e1"],
    )
    first = pm.create_proposal(
        source_event={"id": "evt_mem_1", "user_id": "u1", "raw_content": "同一个经历内容"},
        **base,
    )
    assert first["status"] == "created", first
    assert first["proposal"].evaluator_meta.get("content_fingerprint")

    second = pm.create_proposal(
        source_event={"id": "evt_exp_1", "user_id": "u1", "raw_content": "同一个经历内容"},
        **base,
    )
    assert second["status"] == "deduped", f"跨链同内容应去重: {second}"
    assert second["existing_id"] == first["proposal"].id
    assert len(store.items) == 1


# ------------------------------------------------------------
# 3. B-store 镜像
# ------------------------------------------------------------
def test_mirror_proposal_to_governance_store(monkeypatch):
    from src.growth.growth_integration import GrowthIntegrationService

    fake_b = _FakeGovernanceStorage()
    svc = GrowthIntegrationService(
        proposal_manager=object(),
        growth_history=object(),
        growth_history_bridge=object(),
        self_model_store=object(),
        config={
            "growth_governance_enabled": True,
            "governance_storage": fake_b,
        },
    )
    canonical = GrowthProposal(
        id="prop_a1",
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
        evaluator_meta={"content_fingerprint": "abc123"},
    )
    result = svc._mirror_proposal_to_governance_store(
        proposal=canonical,
        source_event={"user_id": "u1"},
    )
    assert result["ok"] is True, result
    pending = fake_b.list_by_status("pending")
    assert len(pending) == 1
    gp = pending[0]
    assert gp.proposal_type == "personality"
    assert gp.before_state.get("warmth") == 0.5
    assert gp.after_state.get("warmth") == 0.55
    assert (gp.metadata or {}).get("canonical_proposal_id") == "prop_a1"
    assert (gp.metadata or {}).get("content_fingerprint") == "abc123"
    assert gp.status == "pending", "镜像提案必须 pending（不自动 apply）"
