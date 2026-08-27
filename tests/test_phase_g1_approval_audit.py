# -*- coding: utf-8 -*-
"""
Phase G-1.3.4: 审批审计增强验收测试。

覆盖:
1. record_state_mutation(extra=...) → 条目携带 reviewer_id / decision。
2. extra 非 dict → 忽略、不崩、返回 True。
3. extra 不得覆盖核心字段（proposal_id 等）。
4. pipeline apply（真实凭证）→ 审计含 proposal_id / approval_id==record_id /
   reviewer_id / decision。
5. pipeline apply（legacy 兜底无凭证）→ 审计仍写入, reviewer_id/decision 为空,
   不崩（旧兼容保留）。

隔离: 审计经 env 重定向 tmp; 人格状态单例 monkeypatch; 遵守 conftest 陷阱规则。
"""

from __future__ import annotations

import importlib

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.governance.state_mutation_audit import (
    read_entries,
    record_state_mutation,
)
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline


def _proposal(pid: str = "P001") -> GrowthProposal:
    return GrowthProposal(
        id=pid,
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
    )


def _patch_personality_singleton(monkeypatch, isolated):
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)


# ------------------------------------------------------------
# 1. extra 合并
# ------------------------------------------------------------
def test_extra_fields_merged(tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    ok = record_state_mutation(
        component="personality",
        target="personality_trait",
        before={"warmth": 0.5},
        after={"warmth": 0.55},
        proposal_id="P001",
        approval_id="apr_x1",
        actor="runtime",
        path=audit_path,
        extra={"reviewer_id": "admin", "decision": "approve"},
    )
    assert ok is True
    entries = read_entries(limit=10, path=audit_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["reviewer_id"] == "admin"
    assert entry["decision"] == "approve"
    assert entry["proposal_id"] == "P001"
    assert entry["approval_id"] == "apr_x1"


# ------------------------------------------------------------
# 2. extra 非 dict 忽略
# ------------------------------------------------------------
def test_extra_non_dict_ignored(tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    ok = record_state_mutation(
        component="personality",
        target="personality_trait",
        before=0.5,
        after=0.55,
        proposal_id="P002",
        approval_id="apr_x2",
        actor="runtime",
        path=audit_path,
        extra="not-a-dict",
    )
    assert ok is True
    entry = read_entries(limit=10, path=audit_path)[0]
    assert "reviewer_id" not in entry
    assert entry["proposal_id"] == "P002"


# ------------------------------------------------------------
# 3. extra 不得覆盖核心字段
# ------------------------------------------------------------
def test_extra_cannot_override_core(tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    record_state_mutation(
        component="personality",
        target="personality_trait",
        before=0.5,
        after=0.55,
        proposal_id="P003",
        approval_id="apr_x3",
        actor="runtime",
        path=audit_path,
        extra={"proposal_id": "EVIL", "approval_id": "EVIL", "decision": "approve"},
    )
    entry = read_entries(limit=10, path=audit_path)[0]
    assert entry["proposal_id"] == "P003"
    assert entry["approval_id"] == "apr_x3"
    assert entry["decision"] == "approve"


# ------------------------------------------------------------
# 4. pipeline apply（真实凭证）审计完整上下文
# ------------------------------------------------------------
def test_pipeline_audit_full_context(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)

    proposal = _proposal()
    proposal.status = "accepted"
    record = {
        "record_id": "apr_full_1",
        "proposal_id": "P001",
        "reviewer_id": "admin",
        "reviewed_at": "2026-08-21T00:00:00Z",
        "decision": "approved",
    }
    envelope = PersonalityEvolutionPipeline().apply_approved_to_state(
        proposal=proposal,
        actor="runtime",
        approval_record=record,
    )
    assert envelope.get("applied") is True, envelope

    entries = read_entries(limit=50, path=audit_path)
    applied = [e for e in entries if e.get("proposal_id") == "P001"]
    assert applied, "缺少 apply 审计"
    entry = applied[0]
    assert entry["approval_id"] == "apr_full_1"
    assert entry["reviewer_id"] == "admin"
    assert entry["decision"] == "approved"
    assert not str(entry["approval_id"]).startswith("approved_by:")


# ------------------------------------------------------------
# 5. legacy 兜底（无凭证）审计不崩、字段为空
# ------------------------------------------------------------
def test_pipeline_audit_legacy_fallback(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)

    proposal = _proposal(pid="P009")
    proposal.status = "accepted"
    envelope = PersonalityEvolutionPipeline().apply_approved_to_state(
        proposal=proposal,
        actor="legacy_caller",
    )
    assert envelope.get("applied") is True, envelope

    entries = read_entries(limit=50, path=audit_path)
    applied = [e for e in entries if e.get("proposal_id") == "P009"]
    assert applied
    entry = applied[0]
    # legacy 兜底：approval_id 为旧拼接格式，reviewer/decision 留空（可辨识非真实凭证）
    assert entry["reviewer_id"] == ""
    assert entry["decision"] == ""
