# -*- coding: utf-8 -*-
"""Phase 2.5-D Commit 1 契约测试:关系核心激活(事务日志 + ActivationDraft)。

治理约束(全部必须成立,与 Phase 2.5-C 一致):
- 激活是独立的人工步骤,只能从 accepted 提案出发,绝不自动激活;
- 事务日志式写入:PREPARED 记录 → 写核心 → COMPLETED → proposal.activate()
  → 锚点刷新;崩溃后扫描 activation_records.jsonl 即可区分未完成/已完成;
- 回滚保证:重复 relationship_id 激活被拒,旧核心永不被覆盖;
- ActivationDraft 由人工定义(可只认可提案中的部分记忆),
  不允许 proposal.source_memory_ids 原样直落 RelationshipCore;
- 只允许人工落库:LLM / 系统侧永远无法让提案到达 accepted / activated。
"""
import json
import os
import tempfile

import pytest
from flask import Flask

from src.admin.api import routes as routes_mod
from src.admin.api.routes import admin_bp
from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS
from src.relationship.relationship_activation import (
    ACTIVATION_STATUS_COMPLETED,
    ACTIVATION_STATUS_PREPARED,
    ActivationDraft,
    RelationshipActivationRecord,
    RelationshipActivationRecordStore,
    RelationshipActivationService,
)
from src.relationship.relationship_core import DEFAULT_VISIBILITY, VISIBILITY_GLOBAL
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.relationship.relationship_proposal import PROPOSAL_STATUS, RelationshipProposal
from src.relationship.relationship_proposal_store import RelationshipProposalStore

UID = "366648462"
BASE = "/admin/api/admin/governance/relationship-proposals"
ACT_BASE = "/admin/api/admin/governance/relationship-activations"


@pytest.fixture(autouse=True)
def _isolate_core_relationship_memory_ids():
    """activate() 会经 _refresh_anchors 把锚点写进模块级全局白名单
    (src.identity.yui_core_profile.CORE_RELATIONSHIP_MEMORY_IDS),
    同进程内会污染后续测试(如 test_relationship_prompt_integration
    的 seed 计数断言);每个测试结束后还原快照。
    """
    snapshot = set(CORE_RELATIONSHIP_MEMORY_IDS)
    yield
    CORE_RELATIONSHIP_MEMORY_IDS.clear()
    CORE_RELATIONSHIP_MEMORY_IDS.update(snapshot)


# ---------------- helpers ----------------

def _make_proposal(tmp_path, status="pending_review", memory_ids=None):
    store = RelationshipProposalStore(str(tmp_path / "proposals.jsonl"))
    proposal = RelationshipProposal(
        source_memory_ids=memory_ids or ["mem_100", "mem_200"],
        source_user_id=UID,
        category="relationship_core_candidate",
        score=0.82,
        reason="长期约定,有证据链",
    )
    if status != PROPOSAL_STATUS["CANDIDATE"]:
        assert proposal.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator", reason="评估")
        assert proposal.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator", reason="提交审核")
    if status == PROPOSAL_STATUS["ACCEPTED"]:
        assert proposal.approve("admin", "证据充分,同意")
    assert store.save(proposal)
    return store, proposal


def _make_service(tmp_path, proposal_store=None):
    core_store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    record_store = RelationshipActivationRecordStore(str(tmp_path / "records.jsonl"))
    service = RelationshipActivationService(
        proposal_store=proposal_store,
        core_store=core_store,
        record_store=record_store,
    )
    return service, core_store, record_store


def _make_draft(proposal_id, **kwargs):
    defaults = {
        "approved_memory_ids": ["mem_100"],
        "relationship_type": "creator",
        "agreements": ["只对清清保持特定称呼"],
        "boundaries": ["不向其他人复述清清的私事"],
        "visibility": VISIBILITY_GLOBAL,
    }
    defaults.update(kwargs)
    return ActivationDraft(proposal_id=proposal_id, **defaults)


# ---------------- ActivationDraft 模型 ----------------

def test_draft_roundtrip_preserves_human_defined_fields():
    draft = _make_draft("relp_x")
    again = ActivationDraft.from_dict(draft.to_dict())
    assert again.proposal_id == "relp_x"
    assert again.approved_memory_ids == ["mem_100"]
    assert again.relationship_type == "creator"
    assert again.agreements == ["只对清清保持特定称呼"]
    assert again.boundaries == ["不向其他人复述清清的私事"]
    assert again.visibility == VISIBILITY_GLOBAL


def test_draft_from_dict_coerces_invalid_values():
    draft = ActivationDraft.from_dict({
        "proposal_id": "relp_x",
        "approved_memory_ids": "mem_1",
        "relationship_type": "boss",
        "visibility": "not_a_visibility",
    })
    assert draft.approved_memory_ids == []
    assert draft.relationship_type == "other"
    assert draft.visibility == DEFAULT_VISIBILITY


# ---------------- RelationshipActivationRecord 模型 ----------------

def test_record_model_has_all_required_audit_fields():
    record = RelationshipActivationRecord(
        proposal_id="relp_x",
        relationship_id="yuyi:366648462",
        activated_by="admin",
        activation_reason="审核通过,进入关系核心",
        source_memories=["mem_1"],
    )
    data = record.to_dict()
    for key in (
        "record_id", "proposal_id", "relationship_id", "activated_by",
        "activation_reason", "source_memories", "timestamp",
        "previous_state", "new_state", "status",
    ):
        assert key in data, f"缺少审计字段: {key}"
    assert data["record_id"].startswith("rela_")
    assert data["previous_state"] == "no_core"
    assert data["new_state"] == "activated"
    assert data["status"] == ACTIVATION_STATUS_PREPARED


# ---------------- RelationshipActivationRecordStore ----------------

def test_record_store_latest_wins_by_record_id(tmp_path):
    store = RelationshipActivationRecordStore(str(tmp_path / "records.jsonl"))
    record = RelationshipActivationRecord(
        proposal_id="relp_x", relationship_id="yuyi:1", activated_by="admin",
        activation_reason="激活", source_memories=["mem_1"],
    )
    assert store.save(record)
    record.status = ACTIVATION_STATUS_COMPLETED
    assert store.save(record)
    assert store.get(record.record_id)["status"] == ACTIVATION_STATUS_COMPLETED
    assert len(store.list()) == 1


def test_record_store_skips_corrupt_lines(tmp_path):
    path = tmp_path / "records.jsonl"
    good = {
        "record_id": "rela_1", "proposal_id": "relp_x",
        "relationship_id": "yuyi:1", "status": ACTIVATION_STATUS_PREPARED,
    }
    path.write_text(
        "{broken json\n" + json.dumps(good) + "\n", encoding="utf-8",
    )
    store = RelationshipActivationRecordStore(str(path))
    assert len(store.list()) == 1
    assert store.list()[0]["record_id"] == "rela_1"


def test_record_store_fully_corrupt_backs_up_and_degrades(tmp_path):
    path = tmp_path / "records.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    store = RelationshipActivationRecordStore(str(path))
    assert store.list() == []
    # 损坏文件被备份(不删除原文件)
    backups = list(tmp_path.glob("records.jsonl.corrupt.*"))
    assert len(backups) == 1


# ---------------- RelationshipActivationService: 完整激活 ----------------

def test_activation_full_flow_writes_core_and_marks_proposal_activated(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)

    result = service.activate(
        _make_draft(proposal.proposal_id), reviewer="admin", reason="审核通过,激活",
    )
    assert result["ok"] is True
    assert result["relationship_id"] == "yuyi:366648462"
    assert result["status"] == "activated"

    cores = core_store.load()
    assert len(cores) == 1
    core = cores[0]
    assert core["relationship_id"] == "yuyi:366648462"
    assert core["source_user_id"] == UID
    assert core["agreements"] == ["只对清清保持特定称呼"]
    assert core["boundaries"] == ["不向其他人复述清清的私事"]
    assert core["anchor_memory_ids"] == ["mem_100"]
    assert core["visibility"] == VISIBILITY_GLOBAL

    prop_record = prop_store.get(proposal.proposal_id)
    assert prop_record["status"] == PROPOSAL_STATUS["ACTIVATED"]
    assert prop_record["audit"][-1]["action"] == "activate"
    assert prop_record["audit"][-1]["actor"] == "admin"

    rec = record_store.get(result["record_id"])
    assert rec["status"] == ACTIVATION_STATUS_COMPLETED
    assert rec["activated_by"] == "admin"
    assert rec["activation_reason"] == "审核通过,激活"
    assert rec["source_memories"] == ["mem_100"]
    assert rec["previous_state"] == "no_core"
    assert rec["new_state"] == "activated"
    assert rec["timestamp"]


def test_draft_approved_subset_only_never_full_proposal_list(tmp_path):
    prop_store, proposal = _make_proposal(
        tmp_path, status="accepted", memory_ids=["mem_100", "mem_200", "mem_300"],
    )
    service, core_store, _ = _make_service(tmp_path, proposal_store=prop_store)

    result = service.activate(
        _make_draft(proposal.proposal_id, approved_memory_ids=["mem_100", "mem_300"]),
        reviewer="admin", reason="只认可其中两条",
    )
    assert result["ok"] is True
    core = core_store.load()[0]
    assert core["anchor_memory_ids"] == ["mem_100", "mem_300"]
    assert core["anchor_memory_ids"] != proposal.source_memory_ids


# ---------------- RelationshipActivationService: 校验拒绝 ----------------

def test_activate_non_accepted_proposal_rejected(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="pending_review")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)

    result = service.activate(_make_draft(proposal.proposal_id), reviewer="admin", reason="激活")
    assert result["ok"] is False
    assert result["stage"] == "validation"
    assert core_store.load() == []
    assert record_store.list() == []
    assert prop_store.get(proposal.proposal_id)["status"] == PROPOSAL_STATUS["PENDING_REVIEW"]


def test_activate_requires_reviewer_and_reason(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)
    draft = _make_draft(proposal.proposal_id)

    assert service.activate(draft, reviewer="", reason="激活")["ok"] is False
    assert service.activate(draft, reviewer="admin", reason="")["ok"] is False
    assert core_store.load() == []
    assert record_store.list() == []
    assert prop_store.get(proposal.proposal_id)["status"] == PROPOSAL_STATUS["ACCEPTED"]


def test_activate_rejects_empty_approved_memory_ids(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)

    result = service.activate(
        _make_draft(proposal.proposal_id, approved_memory_ids=[]),
        reviewer="admin", reason="激活",
    )
    assert result["ok"] is False
    assert core_store.load() == []
    assert record_store.list() == []


# ---------------- RelationshipActivationService: 回滚保证 ----------------

def test_duplicate_relationship_id_rejected_old_core_untouched(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)
    old_core = {
        "relationship_id": "yuyi:366648462",
        "source_user_id": UID,
        "relationship_type": "other",
        "agreements": ["旧约定(不可被覆盖)"],
        "anchor_memory_ids": ["mem_old"],
        "visibility": DEFAULT_VISIBILITY,
    }
    assert core_store.save(old_core) is True

    result = service.activate(_make_draft(proposal.proposal_id), reviewer="admin", reason="激活")
    assert result["ok"] is False
    assert result["stage"] == "validation"
    # 旧核心原样保留,无任何新记录
    assert core_store.load() == [old_core]
    assert record_store.list() == []
    assert prop_store.get(proposal.proposal_id)["status"] == PROPOSAL_STATUS["ACCEPTED"]


# ---------------- RelationshipActivationService: 事务日志与崩溃恢复 ----------------

def test_core_write_failure_leaves_prepared_record_scannable(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)
    original_save = core_store.save
    core_store.save = lambda core: False  # 模拟第 4 步写核心失败(事务中断)

    result = service.activate(_make_draft(proposal.proposal_id), reviewer="admin", reason="激活")
    core_store.save = original_save

    assert result["ok"] is False
    assert result["stage"] == "core_write_failed"
    assert result["record_id"]
    rec = record_store.get(result["record_id"])
    assert rec["status"] == ACTIVATION_STATUS_PREPARED
    assert core_store.load() == []

    # 崩溃后扫描:PREPARED 记录清晰标识「未完成激活」
    prepared = record_store.list(status=ACTIVATION_STATUS_PREPARED)
    assert [r["record_id"] for r in prepared] == [result["record_id"]]

    # 核心未落库时 finalize 不能凭空完成事务
    assert service.finalize(result["record_id"])["ok"] is False
    assert record_store.get(result["record_id"])["status"] == ACTIVATION_STATUS_PREPARED

    # 人工重新发起激活:新事务成功,旧 PREPARED 记录保留作为失败审计
    result2 = service.activate(_make_draft(proposal.proposal_id), reviewer="admin", reason="重新激活")
    assert result2["ok"] is True
    assert record_store.get(result["record_id"])["status"] == ACTIVATION_STATUS_PREPARED
    assert record_store.get(result2["record_id"])["status"] == ACTIVATION_STATUS_COMPLETED
    assert len(core_store.load()) == 1


def test_finalize_completes_tail_after_core_landed(tmp_path):
    """核心已落库但尾部(提案流转)失败 → finalize 补齐,绝不重复写核心。"""
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)
    original_save = prop_store.save
    prop_store.save = lambda p: False  # 模拟第 6 步提案保存失败

    result = service.activate(_make_draft(proposal.proposal_id), reviewer="admin", reason="激活")
    prop_store.save = original_save

    assert result["ok"] is False
    assert result["stage"] == "proposal_save_failed"
    # 核心与 COMPLETED 记录都已落库,只有提案还停留在 accepted
    assert len(core_store.load()) == 1
    assert record_store.get(result["record_id"])["status"] == ACTIVATION_STATUS_COMPLETED
    assert prop_store.get(proposal.proposal_id)["status"] == PROPOSAL_STATUS["ACCEPTED"]

    final = service.finalize(result["record_id"])
    assert final["ok"] is True
    assert prop_store.get(proposal.proposal_id)["status"] == PROPOSAL_STATUS["ACTIVATED"]
    # 核心只写了一次,没有被 finalize 重复写入
    assert len(core_store.load()) == 1


def test_finalize_prepared_without_core_fails(tmp_path):
    prop_store, proposal = _make_proposal(tmp_path, status="accepted")
    service, core_store, record_store = _make_service(tmp_path, proposal_store=prop_store)
    record = RelationshipActivationRecord(
        proposal_id=proposal.proposal_id,
        relationship_id="yuyi:366648462",
        activated_by="admin",
        activation_reason="激活",
        source_memories=["mem_100"],
    )
    assert record_store.save(record)

    result = service.finalize(record.record_id)
    assert result["ok"] is False
    assert record_store.get(record.record_id)["status"] == ACTIVATION_STATUS_PREPARED
    assert core_store.load() == []


# ---------------- 锚点刷新 ----------------

def test_activation_seeds_anchor_whitelist(tmp_path):
    prop_store, proposal = _make_proposal(
        tmp_path, status="accepted", memory_ids=["mem_seed_1"],
    )
    service, _, _ = _make_service(tmp_path, proposal_store=prop_store)
    before = set(CORE_RELATIONSHIP_MEMORY_IDS)
    try:
        CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_seed_1")
        result = service.activate(
            _make_draft(proposal.proposal_id, approved_memory_ids=["mem_seed_1"]),
            reviewer="admin", reason="激活",
        )
        assert result["ok"] is True
        assert "mem_seed_1" in CORE_RELATIONSHIP_MEMORY_IDS
    finally:
        CORE_RELATIONSHIP_MEMORY_IDS.clear()
        CORE_RELATIONSHIP_MEMORY_IDS.update(before)


# ---------------- admin 端点 ----------------

class _FakeAudit:
    def record(self, **kwargs):
        pass


def _make_client(tmp_path, monkeypatch):
    prop_store = RelationshipProposalStore(str(tmp_path / "props.jsonl"))
    core_store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    record_store = RelationshipActivationRecordStore(str(tmp_path / "recs.jsonl"))
    service = RelationshipActivationService(
        proposal_store=prop_store, core_store=core_store, record_store=record_store,
    )
    monkeypatch.setattr(routes_mod, "_get_relationship_proposal_store", lambda: prop_store)
    monkeypatch.setattr(routes_mod, "_get_activation_service", lambda: service)
    monkeypatch.setattr(routes_mod, "_get_activation_record_store", lambda: record_store)
    monkeypatch.setattr(
        "src.admin.core.audit.AuditLogger.get_instance",
        staticmethod(lambda: _FakeAudit()),
    )
    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app.test_client(), prop_store, core_store, record_store


def _create_accepted(client):
    resp = client.post(BASE, json={
        "source_memory_ids": ["mem_agreement"],
        "source_user_id": UID,
        "category": "relationship_core_candidate",
        "score": 0.82,
        "reason": "稳定约定,影响未来互动",
        "submit_for_review": True,
    })
    pid = resp.get_json()["proposal_id"]
    assert client.post(
        f"{BASE}/{pid}/approve", json={"reviewer": UID, "reason": "证据充分,同意"},
    ).status_code == 200
    return pid


def test_admin_activate_full_flow(tmp_path, monkeypatch):
    client, prop_store, core_store, record_store = _make_client(tmp_path, monkeypatch)
    pid = _create_accepted(client)

    resp = client.post(f"{BASE}/{pid}/activate", json={
        "reviewer": UID,
        "activation_reason": "审核通过,进入关系核心",
        "approved_memory_ids": ["mem_agreement"],
        "relationship_type": "creator",
        "agreements": ["只对清清用特定称呼"],
        "boundaries": ["不复述私事"],
        "visibility": "global",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["status"] == "activated"
    assert data["relationship_id"] == f"yuyi:{UID}"

    cores = core_store.load()
    assert len(cores) == 1
    assert cores[0]["agreements"] == ["只对清清用特定称呼"]
    assert prop_store.get(pid)["status"] == PROPOSAL_STATUS["ACTIVATED"]

    # 激活记录可通过扫描接口读取
    listed = client.get(ACT_BASE).get_json()
    assert listed["ok"] is True
    assert listed["count"] == 1
    assert listed["records"][0]["status"] == ACTIVATION_STATUS_COMPLETED


def test_admin_activate_requires_reason(tmp_path, monkeypatch):
    client, prop_store, core_store, record_store = _make_client(tmp_path, monkeypatch)
    pid = _create_accepted(client)

    resp = client.post(f"{BASE}/{pid}/activate", json={
        "reviewer": UID, "activation_reason": "",
    })
    assert resp.status_code == 400
    assert core_store.load() == []
    assert record_store.list() == []
    assert prop_store.get(pid)["status"] == PROPOSAL_STATUS["ACCEPTED"]


def test_admin_activate_non_accepted_proposal_fails(tmp_path, monkeypatch):
    client, prop_store, core_store, record_store = _make_client(tmp_path, monkeypatch)
    resp = client.post(BASE, json={
        "source_memory_ids": ["mem_x"], "source_user_id": UID,
        "category": "relationship_core_candidate", "score": 0.8,
        "reason": "r", "submit_for_review": True,
    })
    pid = resp.get_json()["proposal_id"]

    resp = client.post(f"{BASE}/{pid}/activate", json={
        "reviewer": UID, "activation_reason": "激活",
    })
    assert resp.status_code == 400
    assert core_store.load() == []
    assert record_store.list() == []


def test_admin_activate_unknown_proposal_404(tmp_path, monkeypatch):
    client, _, _, _ = _make_client(tmp_path, monkeypatch)
    resp = client.post(f"{BASE}/relp_nonexistent/activate", json={
        "reviewer": UID, "activation_reason": "激活",
    })
    assert resp.status_code == 404


def test_admin_finalize_recovers_incomplete_transaction(tmp_path, monkeypatch):
    client, prop_store, core_store, record_store = _make_client(tmp_path, monkeypatch)
    pid = _create_accepted(client)

    # 模拟尾部失败:先让核心与 COMPLETED 记录落库、提案保存失败
    original_save = prop_store.save
    prop_store.save = lambda p: False
    resp = client.post(f"{BASE}/{pid}/activate", json={
        "reviewer": UID, "activation_reason": "激活",
        "approved_memory_ids": ["mem_agreement"],
    })
    prop_store.save = original_save
    assert resp.status_code == 500
    record_id = resp.get_json()["record_id"]

    finalize_resp = client.post(f"{ACT_BASE}/{record_id}/finalize", json={})
    assert finalize_resp.status_code == 200
    assert prop_store.get(pid)["status"] == PROPOSAL_STATUS["ACTIVATED"]
    assert len(core_store.load()) == 1
