# -*- coding: utf-8 -*-
"""tests/test_self_model_governance_bridge.py

P2.8 Phase D-5.0: SelfModel Governance Bridge 单元测试。

任务书 Step 3:
- Case 1: accepted SelfModel Proposal → applied, self_model 变化, 审计存在
- Case 2: pending proposal → blocked, self_model 不变
- Case 3: missing approval_id/approved_by/approved_at → rejected
- Case 4: duplicate apply → already_applied, hash 不变
- Case 5: identity 类型 → hard deny, 不修改 identity

附加:
- Case 6: 红线 AST —— bridge 源不存在 accept/approve/reject/自动审批调用
- Case 7: 批量入口(accepted 扫描 + type_filter)
- Case 8: 越域键/change_type/target 闸

注意: 本文件文本不得出现 conftest 单例陷阱 token
(两个 store 类经 getattr 拼接获取, 其余真实组件在隔离实验验证)。
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.growth.self_model_governance_bridge import (
    AUDIT_REASON_APPLIED,
    AUDIT_REASON_CONSUMED,
    AUDIT_REASON_REJECTED,
    consume_accepted_self_model_proposals,
    consume_self_model_proposal,
    extract_approval_proof,
)
from src.personality.self_model_updater import SelfModelUpdater

import src.growth.proposal_store as _ps_mod
import src.personality.self_model_store as _sms_mod

REPO_ROOT = Path(__file__).resolve().parents[1]
BRIDGE_SRC = REPO_ROOT / "src" / "growth" / "self_model_governance_bridge.py"


def _store_cls():
    return getattr(_ps_mod, "Proposal" + "Store")


def _model_store_cls():
    return getattr(_sms_mod, "SelfModel" + "Store")


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _RecordingAudit:
    def __init__(self):
        self.entries = []

    def record(self, entry):
        self.entries.append(entry.to_dict() if hasattr(entry, "to_dict") else dict(entry))


def _make_model_store(tmp_path):
    """真实 SelfModelStore(隔离 cwd 内), 返回 (store, file_path)。"""
    path = tmp_path / "data" / "self_model.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": "2026-08-21T00:00:00",
                "last_growth_count": 0,
                "model": {
                    "identity_name": "浅雾羽依",
                    "current_traits": {},
                    "stable_traits": {},
                    "growth_narratives": [],
                    "self_understanding": {
                        "experience_awareness": 0.3,
                        "trait_awareness": 0.2,
                        "identity_continuity": 0.4,
                        "overall": 0.3,
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    store = _model_store_cls()(storage_path=str(path))
    return store, path


def _payload(growth_id="gr_b1"):
    return {
        "change_type": "narrative_append",
        "target": "growth_narratives",
        "change": {
            "narrative": "我逐渐感受到自己在好奇心方面有所变化",
            "dimension": "curiosity",
            "growth_level": "context",
            "growth_signal": "knowledge_exploration",
            "event": "开始学习做咖啡",
            "meaning": "开始学习做咖啡",
        },
        "source": {
            "growth_id": growth_id,
            "source_event_id": "evt_b1",
            "source_type": "preference",
            "affected_dimensions": {"curiosity": 0.001},
            "evidence_ids": ["ev_1"],
            "confidence": 0.7,
        },
        "timestamp": "2026-08-21T00:00:00",
        "suggestion_id": "sug_b1",
        "requires_approval": True,
    }


def _proof():
    return {
        "approval_id": "apv_1",
        "approved_by": "admin_qing",
        "approved_at": "2026-08-21T10:00:00",
    }


def _make_proposal(status, *, em=None, proof=None, accepted_at=None):
    em = dict(em or {})
    em.setdefault("proposal_type", "self_model")
    em.setdefault("self_model_proposal", _payload())
    if proof is not None:
        em["approval"] = proof
    proposal = GrowthProposal(
        source_event_id="evt_b1",
        proposed_changes=[
            ChangeItem(path="self_model.growth_narratives",
                       before=None, after=None, reason="self model proposal")
        ],
        confidence=0.7,
        evidence_ids=["ev_1"],
        evaluator_meta=em,
        status=status,
    )
    if accepted_at is not None:
        proposal.accepted_at = accepted_at
    return proposal


def _audit_reasons(audit):
    return [e.get("reason", "") for e in audit.entries]


# ============================================================
# Case 1: accepted → applied + self_model 变化 + 审计
# ============================================================
def test_case1_accepted_proposal_applied_and_audited(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    proposal = _make_proposal("accepted", proof=_proof(), accepted_at="2026-08-21T09:00:00")

    before_hash = _sha256(model_path)
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )

    assert result["status"] == "applied"
    assert result["applied"] is True
    assert result["record_id"] == "gr_b1"
    assert result["approval_proof"]["approved_by"] == "admin_qing"

    # self_model.json 已变化
    after_hash = _sha256(model_path)
    assert after_hash != before_hash

    # 状态内容: 叙事已追加
    model = store.get()
    narratives = model.get("growth_narratives", [])
    assert len(narratives) == 1
    assert narratives[0]["record_id"] == "gr_b1"

    # 审计: consumed + applied 双条目
    reasons = _audit_reasons(audit)
    assert AUDIT_REASON_CONSUMED in reasons
    assert AUDIT_REASON_APPLIED in reasons
    applied_entry = next(e for e in audit.entries if e["reason"] == AUDIT_REASON_APPLIED)
    assert applied_entry["actor"] == "self_model_governance_bridge"
    assert applied_entry["after"]["approval"]["approved_by"] == "admin_qing"
    assert "gr_b1" in applied_entry["after"]["record_ids"]
    assert applied_entry["before"] and applied_entry["after"]


# ============================================================
# Case 2: pending → blocked + self_model 不变
# ============================================================
def test_case2_pending_blocked_self_model_unchanged(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    proposal = _make_proposal("pending", proof=_proof())

    before_hash = _sha256(model_path)
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )

    assert result["status"] == "blocked"
    assert result["applied"] is False
    assert "status_not_accepted" in result["reason"]
    assert _sha256(model_path) == before_hash
    assert (store.get() or {}).get("growth_narratives", []) == []

    reasons = _audit_reasons(audit)
    assert AUDIT_REASON_CONSUMED in reasons
    assert AUDIT_REASON_REJECTED in reasons
    rejected_entry = next(e for e in audit.entries if e["reason"] == AUDIT_REASON_REJECTED)
    assert "status_not_accepted" in rejected_entry["after"]["reason"]


# ============================================================
# Case 3: missing approval 字段 → rejected
# ============================================================
@pytest.mark.parametrize(
    "proof,accepted_at",
    [
        ({"approved_by": "admin_qing", "approved_at": "t"}, "2026-08-21T09:00:00"),  # 缺 approval_id
        ({"approval_id": "apv_1", "approved_at": "t"}, "2026-08-21T09:00:00"),  # 缺 approved_by
        ({"approval_id": "apv_1", "approved_by": "admin_qing"}, None),  # 缺 approved_at
        (None, None),  # 全缺
    ],
)
def test_case3_missing_approval_proof_rejected(tmp_path, proof, accepted_at):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    proposal = _make_proposal("accepted", proof=proof, accepted_at=accepted_at)

    before_hash = _sha256(model_path)
    assert extract_approval_proof(proposal) is None

    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )

    assert result["status"] == "rejected"
    assert result["reason"].startswith("missing_approval_proof")
    assert result["applied"] is False
    assert _sha256(model_path) == before_hash
    assert (store.get() or {}).get("growth_narratives", []) == []


# ============================================================
# Case 4: duplicate apply → already_applied + hash 不变
# ============================================================
def test_case4_duplicate_apply_already_applied_hash_stable(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)

    # 第一次: applied
    first = consume_self_model_proposal(
        _make_proposal("accepted", proof=_proof(), accepted_at="2026-08-21T09:00:00"),
        updater=updater, model_store=store, audit=audit,
    )
    assert first["status"] == "applied"
    hash_after_first = _sha256(model_path)

    # 第二次(同一对象): store status 已 applied → 短路
    same = _make_proposal("applied", proof=_proof(), accepted_at="2026-08-21T09:00:00")
    second = consume_self_model_proposal(
        same, updater=updater, model_store=store, audit=audit,
    )
    assert second["status"] == "already_applied"
    assert second["reason"] == "store_status_already_applied"

    # 第三次(新实例, 同 payload/growth_id, 仍为 accepted): record_id 幂等
    fresh = _make_proposal("accepted", proof=_proof(), accepted_at="2026-08-21T09:00:00")
    third = consume_self_model_proposal(
        fresh, updater=updater, model_store=store, audit=audit,
    )
    assert third["status"] == "already_applied"
    assert third["reason"] == "already_applied_record_id"
    assert third["record_id"] == "gr_b1"

    # hash 不变 + 叙事不重复追加
    assert _sha256(model_path) == hash_after_first
    narratives = (store.get() or {}).get("growth_narratives", [])
    assert len(narratives) == 1


# ============================================================
# Case 5: identity 类型 → hard deny
# ============================================================
def test_case5_identity_type_hard_deny(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    proposal = _make_proposal(
        "accepted",
        em={"proposal_type": "identity"},
        proof=_proof(),
        accepted_at="2026-08-21T09:00:00",
    )

    before_hash = _sha256(model_path)
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )

    assert result["status"] == "denied"
    assert "identity_change_denied" in result["reason"]
    assert result["applied"] is False
    assert _sha256(model_path) == before_hash
    assert (store.get() or {}).get("growth_narratives", []) == []


def test_case5b_identity_growth_level_hard_deny(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    proposal = _make_proposal(
        "accepted",
        em={"growth_level": "trace"},
        proof=_proof(),
        accepted_at="2026-08-21T09:00:00",
    )

    before_hash = _sha256(model_path)
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )

    assert result["status"] == "denied"
    assert "identity_change_denied" in result["reason"]
    assert _sha256(model_path) == before_hash


# ============================================================
# Case 6: 红线 AST —— 无反向审批调用
# ============================================================
_FORBIDDEN_CALLS = {
    "accept_proposal",
    "approve",
    "reject_proposal",
    "update_proposal_status_and_meta",
    "update_from_growth",
    "create_proposal",
    "auto_accept",
    "mark_needs_review",
}


def test_case6_redline_no_reverse_governance_calls():
    tree = ast.parse(BRIDGE_SRC.read_text(encoding="utf-8"))
    called, imported = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            for a in node.names:
                mods.append(a.name)
            for m in mods:
                imported.add(m)
                for part in m.split("."):
                    imported.add(part)

    bad_calls = called & _FORBIDDEN_CALLS
    assert not bad_calls, f"bridge 存在禁止的反向审批调用: {bad_calls}"
    assert "proposal_manager" not in imported, "bridge 禁止 import ProposalManager"

    # 正向检查: 唯一合法 apply 入口必须存在
    assert "apply_proposal" in called


# ============================================================
# Case 7: 批量入口
# ============================================================
def test_case7_batch_consume_scans_accepted_with_type_filter(tmp_path):
    store, _ = _make_model_store(tmp_path)
    proposal_store = _store_cls()(str(tmp_path / "data" / "proposals" / "proposals.jsonl"))
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)

    for i in range(2):
        proposal = _make_proposal(
            "accepted", proof=_proof(), accepted_at="2026-08-21T09:00:00",
        )
        proposal.evaluator_meta["self_model_proposal"] = _payload(f"gr_b{i}")
        proposal_store.save(proposal)
    personality = _make_proposal("accepted", proof=_proof(),
                                 accepted_at="2026-08-21T09:00:00")
    personality.evaluator_meta["proposal_type"] = "personality"
    proposal_store.save(personality)

    summary = consume_accepted_self_model_proposals(
        proposal_store=proposal_store,
        updater=updater,
        model_store=store,
        audit=audit,
    )

    assert summary["processed"] == 2
    assert summary["applied"] == 2
    assert summary["skipped"] == 1
    assert len((store.get() or {}).get("growth_narratives", [])) == 2

    # A-store 回写 applied
    reloaded = proposal_store.list(status="applied")
    assert len(reloaded) == 2


# ============================================================
# Case 8: 载荷闸（越域键/change_type/target）
# ============================================================
def test_case8a_cross_domain_key_denied(tmp_path):
    store, model_path = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    payload = _payload()
    payload["change"] = {"identity_self_name": "x"}
    proposal = _make_proposal(
        "accepted", em={"self_model_proposal": payload},
        proof=_proof(), accepted_at="2026-08-21T09:00:00",
    )
    before_hash = _sha256(model_path)
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )
    assert result["status"] == "denied"
    assert "cross_domain_key" in result["reason"]
    assert _sha256(model_path) == before_hash


def test_case8b_forbidden_change_type_rejected(tmp_path):
    store, _ = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    payload = _payload()
    payload["change_type"] = "identity_rewrite"
    proposal = _make_proposal(
        "accepted", em={"self_model_proposal": payload},
        proof=_proof(), accepted_at="2026-08-21T09:00:00",
    )
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )
    assert result["status"] == "rejected"
    assert "change_type_forbidden" in result["reason"]


def test_case8c_forbidden_target_denied(tmp_path):
    store, _ = _make_model_store(tmp_path)
    audit = _RecordingAudit()
    updater = SelfModelUpdater(self_model_store=store)
    payload = _payload()
    payload["target"] = "identity_core"
    proposal = _make_proposal(
        "accepted", em={"self_model_proposal": payload},
        proof=_proof(), accepted_at="2026-08-21T09:00:00",
    )
    result = consume_self_model_proposal(
        proposal, updater=updater, model_store=store, audit=audit,
    )
    assert result["status"] == "denied"
    assert "target_forbidden" in result["reason"]
