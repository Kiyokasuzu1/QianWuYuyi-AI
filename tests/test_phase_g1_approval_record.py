# -*- coding: utf-8 -*-
"""
Phase G-1.3.1: PersonalityAdapter 审批凭证硬化验收测试。

覆盖:
1. 无 approval_record + enforcement=False → legacy 兼容（照旧 apply）。
2. 无 approval_record + enforcement=True → 拒绝（approval_record_required）。
3. 真实 approval_record → apply 成功。
4. proposal_id 不匹配 → 拒绝。
5. 伪造 record_id="approved_by:test" → 拒绝。
6. preview 凭证 → 允许（预览语义标记），不生成真实 mutation audit。

隔离: 不触碰 data/（apply_proposal 为 in-memory）；审计路径重定向 tmp（本测试
实际不产生审计, 断言文件不存在）；遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.personality.personality_adapter import (
    PersonalityAdapter,
    set_approval_record_enforced,
)


@pytest.fixture(autouse=True)
def _reset_enforced():
    """每测前后恢复默认（False = legacy）。"""
    set_approval_record_enforced(False)
    yield
    set_approval_record_enforced(False)


def _proposal(pid: str = "P001") -> GrowthProposal:
    return GrowthProposal(
        id=pid,
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
    )


def _real_record(proposal_id: str = "P001") -> dict:
    return {
        "record_id": "A001",
        "proposal_id": proposal_id,
        "reviewer_id": "admin",
        "reviewed_at": "2026-08-21T00:00:00Z",
        "decision": "approved",
    }


# ------------------------------------------------------------
# 1. legacy 兼容
# ------------------------------------------------------------
def test_mark_approved_without_record_legacy_compat():
    adapter = PersonalityAdapter()
    envelope = adapter.apply_proposal(_proposal(), actor="system", mark_approved=True)
    assert envelope.get("applied") is True, envelope
    assert envelope.get("note") == "applied_in_memory_no_persistence"


# ------------------------------------------------------------
# 2. enforcement 拒绝
# ------------------------------------------------------------
def test_mark_approved_without_record_enforced_rejects():
    set_approval_record_enforced(True)
    try:
        adapter = PersonalityAdapter()
        envelope = adapter.apply_proposal(
            _proposal(), actor="system", mark_approved=True,
        )
    finally:
        set_approval_record_enforced(False)
    assert envelope.get("applied") is False
    assert envelope.get("note") == "approval_record_required"


# ------------------------------------------------------------
# 3. 真实凭证 apply 成功
# ------------------------------------------------------------
def test_real_approval_record_applies():
    set_approval_record_enforced(True)
    try:
        adapter = PersonalityAdapter()
        envelope = adapter.apply_proposal(
            _proposal(),
            actor="runtime",
            mark_approved=True,
            approval_record=_real_record(),
        )
    finally:
        set_approval_record_enforced(False)
    assert envelope.get("applied") is True, envelope
    assert envelope.get("note") == "applied_in_memory_no_persistence"


# ------------------------------------------------------------
# 4. proposal_id 不匹配
# ------------------------------------------------------------
def test_proposal_id_mismatch_rejects():
    adapter = PersonalityAdapter()
    envelope = adapter.apply_proposal(
        _proposal(pid="P001"),
        actor="runtime",
        mark_approved=True,
        approval_record=_real_record(proposal_id="P999"),
    )
    assert envelope.get("applied") is False
    assert "proposal_id_mismatch" in envelope.get("note", "")


# ------------------------------------------------------------
# 5. 伪造 approval_id
# ------------------------------------------------------------
def test_forged_approval_id_rejects():
    adapter = PersonalityAdapter()
    forged_record_id = {
        "record_id": "approved_by:test",
        "proposal_id": "P001",
        "reviewer_id": "admin",
        "decision": "approved",
    }
    envelope = adapter.apply_proposal(
        _proposal(),
        actor="runtime",
        mark_approved=True,
        approval_record=forged_record_id,
    )
    assert envelope.get("applied") is False
    assert "forged_approval_id_pattern" in envelope.get("note", "")

    # 兼容字段名 approval_id 的伪造同样拒绝
    forged_approval_id = {
        "approval_id": "approved_by:test",
        "proposal_id": "P001",
        "reviewer_id": "admin",
        "decision": "approved",
    }
    envelope2 = adapter.apply_proposal(
        _proposal(),
        actor="runtime",
        mark_approved=True,
        approval_record=forged_approval_id,
    )
    assert envelope2.get("applied") is False
    assert "forged_approval_id_pattern" in envelope2.get("note", "")


def test_missing_decision_rejects():
    adapter = PersonalityAdapter()
    record = {
        "record_id": "A002",
        "proposal_id": "P001",
        "reviewer_id": "admin",
        # decision 缺失
    }
    envelope = adapter.apply_proposal(
        _proposal(),
        actor="runtime",
        mark_approved=True,
        approval_record=record,
    )
    assert envelope.get("applied") is False
    assert "decision_not_approved" in envelope.get("note", "")


# ------------------------------------------------------------
# 6. preview 凭证
# ------------------------------------------------------------
def test_preview_record_allowed_without_mutation_audit(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    adapter = PersonalityAdapter()
    envelope = adapter.apply_proposal(
        _proposal(),
        actor="runtime_core_preview",
        mark_approved=True,
        approval_record={"record_id": "preview", "proposal_id": "P001"},
    )
    assert envelope.get("applied") is True, envelope
    assert "_preview_credential" in envelope.get("note", "")
    # 预览凭证不生成真实 mutation audit
    assert not (tmp_path / "state_mutations.jsonl").exists()
