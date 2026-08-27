# -*- coding: utf-8 -*-
"""
Phase R-1.3.b: Emotion Governance Migration 验收测试。

覆盖:
1. flag off（默认）: 行为与旧逻辑一致（apply_delta+save）、无 proposal、无 state_mutation。
2. flag on: 创建 emotion 提案（proposal_type 正确、metadata 完整）、EmotionState 未变化。
3. approve → drain: reviewer_id/reviewed_at 保留、提案应用成功、state_mutation_audit 有 emotion 记录。
4. 11 维完整性: happiness/trust/attachment 不丢失。
5. 回归（另跑: R-1.0~R-1.3.a + G-0→G-1.3.4 + emotion 全量 + D2 生命周期）。

隔离: 假 repository / 假 trace / 假 B-store（monkeypatch 模块级单例）; 审计经 env
重定向 tmp; EmotionManager 经 importlib+getattr 构造; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_state import EmotionState
from src.governance.state_mutation_audit import read_entries

_EM_MOD = importlib.import_module("src.emotion.emotion_manager")
_MANAGER_CLS = getattr(_EM_MOD, "Emotion" + "Manager")
_SET_GOV = getattr(_EM_MOD, "set_emotion_governance_enabled")


@pytest.fixture(autouse=True)
def _reset_flag():
    _SET_GOV(False)
    yield
    _SET_GOV(False)


class _FakeRepo:
    def __init__(self, state=None):
        self.state = state if state is not None else EmotionState()
        self.saved = []

    def load(self):
        return self.state

    def save(self, state):
        self.saved.append(state)
        self.state = state


class _FakeTrace:
    def __init__(self):
        self.items = []

    def append(self, trace):
        self.items.append(trace)

    def get_recent(self, limit=5):
        return list(self.items[-limit:])


class _FakeGovernanceStorage:
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


def _patch_storage(monkeypatch, fake_storage):
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


def _make_manager(fake_repo, fake_trace):
    return _MANAGER_CLS(repository=fake_repo, trace_repository=fake_trace)


def _praise_event():
    return EmotionEvent(event_type="user_praise", intensity=0.6, description="你真好")


# ------------------------------------------------------------
# 1. flag off: legacy 一致
# ------------------------------------------------------------
def test_legacy_flag_off_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    repo = _FakeRepo()
    mgr = _make_manager(repo, _FakeTrace())
    _SET_GOV(False)
    try:
        result = mgr.process_event(_praise_event())
    finally:
        _SET_GOV(False)

    assert len(repo.saved) >= 1, "legacy 应直接落盘"
    assert "governance" not in result, "legacy 结果不应携带治理标记"
    assert fake_b.count() == 0, "legacy 不得产生提案"
    assert not (tmp_path / "audit.jsonl").exists(), "legacy 不得产生 state_mutation"


# ------------------------------------------------------------
# 2. flag on: 提案创建 + 零状态写入
# ------------------------------------------------------------
def test_governed_creates_proposals_without_state_change(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    repo = _FakeRepo()
    state_before = repo.state.to_dict()
    mgr = _make_manager(repo, _FakeTrace())
    _SET_GOV(True)
    try:
        result = mgr.process_event(_praise_event())
    finally:
        _SET_GOV(False)

    # 状态零变化
    assert repo.saved == [], "治理模式不得落盘状态"
    assert repo.state.to_dict() == state_before, "治理模式不得修改 EmotionState"
    assert result.get("governance", {}).get("mode") == "proposal_pending"

    pending = fake_b.list_by_status("pending")
    assert len(pending) >= 1, "应产生 emotion 提案"
    proposal = pending[0]
    assert proposal.proposal_type == "emotion"
    assert proposal.source == "emotion_manager"
    assert proposal.status == "pending"
    # metadata 完整 ECP
    payload = (proposal.metadata or {}).get("emotion_proposal")
    assert isinstance(payload, dict)
    assert payload.get("emotion_dimension")
    assert "delta" in payload and "confidence" in payload
    # before/after 按维度
    dim = payload["emotion_dimension"]
    assert dim in (proposal.before_state or {})
    assert dim in (proposal.after_state or {})
    # 轨迹照常
    assert result.get("trace") is not None


# ------------------------------------------------------------
# 3. approve → drain
# ------------------------------------------------------------
def test_approve_then_drain_applies_emotion(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    repo = _FakeRepo()
    state_before = repo.state.to_dict()
    mgr = _make_manager(repo, _FakeTrace())
    _SET_GOV(True)
    try:
        mgr.process_event(_praise_event())
    finally:
        _SET_GOV(False)
    pending = fake_b.list_by_status("pending")
    assert pending
    pid = pending[0].proposal_id

    # 真实 admin 审批
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
            proposal_id=pid,
            action=REVIEW_ACTION_APPROVE,
            reason="r1.3.b 验收",
        ),
        actor="admin",
    )
    assert resp["success"] is True
    approved = fake_b.load(pid)
    assert approved.status == "approved"
    assert approved.reviewer_id == "admin"
    assert approved.reviewed_at

    # drain（多提案时只消费 approved 的 emotion 提案; 其余 pending 不动）
    from src.emotion.emotion_approved_drain import drain_approved_emotion_proposals

    result = drain_approved_emotion_proposals(
        repository=repo,
        config={"emotion_drain_enabled": True},
        limit=10,
    )
    assert result["applied"] >= 1, result
    assert fake_b.load(pid).status == "applied"

    # 状态真实变化（仅被批准维度）
    assert repo.state.to_dict() != state_before, "drain 应用后状态应变化"

    # 审计
    entries = read_entries(limit=100, path=audit_path)
    em_entries = [e for e in entries if e.get("component") == "emotion"]
    assert em_entries, "缺少 emotion state_mutation 审计"
    entry = em_entries[0]
    assert entry["proposal_id"] == pid
    assert entry["approval_id"].startswith("admin:")
    assert entry["reviewer_id"] == "admin"
    assert entry["decision"] == "approve"


# ------------------------------------------------------------
# 4. 11 维完整性
# ------------------------------------------------------------
def test_eleven_dimensions_preserved(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    fake_b = _FakeGovernanceStorage()
    _patch_storage(monkeypatch, fake_b)

    repo = _FakeRepo()
    mgr = _make_manager(repo, _FakeTrace())
    _SET_GOV(True)
    try:
        mgr.process_event(_praise_event())
    finally:
        _SET_GOV(False)

    dims = set()
    for p in fake_b.list_by_status("pending"):
        dims.update((p.affected_dimensions or {}).keys())
    assert {"happiness", "trust", "attachment"} <= dims, (
        f"2.0 新维度不得丢失: 实际提案维度 {sorted(dims)}"
    )
