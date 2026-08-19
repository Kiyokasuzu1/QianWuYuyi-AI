# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段五契约测试:关系核心提案治理端点(admin API)。

覆盖(治理安全约束):
- 候选创建后停留 candidate,不自动推进;
- submit_for_review 只推进到 pending_review,绝不 accepted/activated;
- 无人工审核不能 accepted:approve 要求 pending_review + 非空 reviewer;
- approve → accepted,且绝不自动激活(activated 是独立治理步骤);
- reject → rejected 终态,之后 approve 必失败;
- list 支持 status 过滤。
"""
import os
import tempfile

from flask import Flask

from src.admin.api import routes as routes_mod
from src.admin.api.routes import admin_bp
from src.relationship.relationship_proposal import PROPOSAL_STATUS
from src.relationship.relationship_proposal_store import RelationshipProposalStore

REVIEWER = "366648462"
BASE = "/admin/api/admin/governance/relationship-proposals"


class _FakeAudit:
    def record(self, **kwargs):
        pass


def _make_client(tmp_path_store, monkeypatch):
    monkeypatch.setattr(
        routes_mod, "_get_relationship_proposal_store", lambda: tmp_path_store,
    )
    monkeypatch.setattr(
        "src.admin.core.audit.AuditLogger.get_instance",
        staticmethod(lambda: _FakeAudit()),
    )
    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    return app.test_client()


def _create(client, submit_for_review=False):
    return client.post(BASE, json={
        "source_memory_ids": ["mem_agreement"],
        "source_user_id": REVIEWER,
        "category": "relationship_core_candidate",
        "score": 0.82,
        "reason": "稳定约定,影响未来互动",
        "submit_for_review": submit_for_review,
    })


def _approve(client, proposal_id, reviewer=REVIEWER):
    return client.post(f"{BASE}/{proposal_id}/approve", json={
        "reviewer": reviewer, "reason": "证据充分,同意进入关系核心",
    })


def _reject(client, proposal_id, reviewer=REVIEWER):
    return client.post(f"{BASE}/{proposal_id}/reject", json={
        "reviewer": reviewer, "reason": "证据不足",
    })


# ---------------- 创建 ----------------

def test_create_candidate_stays_candidate(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        resp = _create(client)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["status"] == PROPOSAL_STATUS["CANDIDATE"]


def test_create_submit_reaches_pending_review_only(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        resp = _create(client, submit_for_review=True)
        data = resp.get_json()
        assert data["status"] == PROPOSAL_STATUS["PENDING_REVIEW"]
        assert data["status"] != PROPOSAL_STATUS["ACCEPTED"]
        assert data["status"] != PROPOSAL_STATUS["ACTIVATED"]


def test_create_submit_rejects_non_candidate_category(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        resp = client.post(BASE, json={
            "source_memory_ids": ["mem_x"],
            "source_user_id": REVIEWER,
            "category": "not_candidate",
            "submit_for_review": True,
        })
        assert resp.status_code == 400


# ---------------- approve / reject ----------------

def test_approve_without_pending_review_fails(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        pid = _create(client).get_json()["proposal_id"]
        resp = _approve(client, pid)
        assert resp.status_code == 400
        assert store.get(pid)["status"] == PROPOSAL_STATUS["CANDIDATE"]


def test_approve_requires_reviewer(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        pid = _create(client, submit_for_review=True).get_json()["proposal_id"]
        resp = _approve(client, pid, reviewer="")
        assert resp.status_code == 400
        assert store.get(pid)["status"] == PROPOSAL_STATUS["PENDING_REVIEW"]


def test_approve_accepted_but_never_auto_activated(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        pid = _create(client, submit_for_review=True).get_json()["proposal_id"]
        resp = _approve(client, pid)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == PROPOSAL_STATUS["ACCEPTED"]
        assert data["status"] != PROPOSAL_STATUS["ACTIVATED"]
        record = store.get(pid)
        assert record["status"] == PROPOSAL_STATUS["ACCEPTED"]
        assert record["audit"][-1]["action"] == "approve"
        assert record["audit"][-1]["actor"] == REVIEWER


def test_reject_is_terminal(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        pid = _create(client, submit_for_review=True).get_json()["proposal_id"]
        resp = _reject(client, pid)
        assert resp.status_code == 200
        assert resp.get_json()["status"] == PROPOSAL_STATUS["REJECTED"]
        # 终态:再 approve 必失败
        assert _approve(client, pid).status_code == 400
        assert store.get(pid)["status"] == PROPOSAL_STATUS["REJECTED"]


def test_approve_unknown_proposal_404(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        assert _approve(client, "relp_nonexistent").status_code == 404


# ---------------- 列表 ----------------

def test_list_filters_by_status(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipProposalStore(os.path.join(tmp, "props.jsonl"))
        client = _make_client(store, monkeypatch)
        _create(client)
        pid = _create(client, submit_for_review=True).get_json()["proposal_id"]
        _approve(client, pid)

        all_resp = client.get(BASE)
        assert all_resp.get_json()["count"] == 2

        accepted = client.get(f"{BASE}?status=accepted").get_json()
        assert accepted["count"] == 1
        assert accepted["proposals"][0]["proposal_id"] == pid
