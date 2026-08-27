# -*- coding: utf-8 -*-
"""Governance 生命周期不变量 & 幂等测试矩阵（GOV 修复验收）。

覆盖：
- 不变量 1-10（family 唯一 confirmed / 多 fact 共存 / confirmed 不入队列 /
  reject/hold 不入 confirmed / modify 不覆盖 / superseded 不 active /
  重复 confirm 幂等 / 刷新一致性 / audit 解释状态）
- 幂等：confirm×2 / reject×2 / hold×2 / modify 同内容×2
- 投影：family 状态推导确认正确
"""
import json
import os

import pytest

from src.governance.fact_candidate import (
    FactCandidateStore, project_candidate, resolve_confirmation_target,
    STATUS_CONFIRMED, STATUS_REJECTED, STATUS_HELD, SOURCE_YUI_STATED,
)
from src.governance.self_model_statements import (
    SelfModelStatementsStore, STATUS_CONFIRMED as SM_CONFIRMED,
)
from src.relationship.relationship_core_store import RelationshipCoreStore


@pytest.fixture()
def fc(tmp_path):
    return FactCandidateStore(str(tmp_path / "fc.jsonl"))


@pytest.fixture()
def sm(tmp_path):
    return SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))


def _submit(fc, cid, layer="relationship_core"):
    cid = fc.submit(fact=f"事实{cid}", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.9,
                    recommended_layer=layer, candidate_id=cid)
    return cid


# ============ 幂等 ============
def test_idempotent_confirm_twice(fc):
    _submit(fc, "RC-001")
    r1 = fc.review("RC-001", "confirm", reviewer="admin")
    assert r1["status"] == STATUS_CONFIRMED
    r2 = fc.review("RC-001", "confirm", reviewer="admin")
    assert r2.get("already") is True  # 第二次 → already，不追加
    assert r2["status"] == STATUS_CONFIRMED
    # 文件里原始 + 1 条 confirmed（无第二条 confirmed）
    confs = [r for r in fc.load() if r.get("status") == STATUS_CONFIRMED]
    assert len(confs) == 1


def test_idempotent_reject_twice(fc):
    _submit(fc, "RC-002")
    r1 = fc.review("RC-002", "reject", reviewer="admin")
    assert r1["status"] == STATUS_REJECTED
    r2 = fc.review("RC-002", "reject", reviewer="admin")
    assert r2.get("already") is True
    r3 = fc.review("RC-002", "confirm", reviewer="admin")  # 终态后 confirm → already
    assert r3.get("already") is True
    assert r3["status"] == STATUS_REJECTED


def test_idempotent_hold_twice(fc):
    _submit(fc, "RC-003")
    fc.review("RC-003", "hold", reviewer="admin")
    r2 = fc.review("RC-003", "hold", reviewer="admin")
    assert r2.get("already") is True
    assert len([r for r in fc.load() if r.get("status") == STATUS_HELD]) == 1


def test_idempotent_modify_same(fc):
    _submit(fc, "RC-004")
    r1 = fc.review("RC-004", "modify", reviewer="admin", modified_fact="修改后")
    assert r1["fact"] == "修改后"
    r2 = fc.review("RC-004", "modify", reviewer="admin", modified_fact="修改后")
    assert r2.get("already") is True  # 同内容不再追加 revision
    # 不同修改允许
    r3 = fc.review("RC-004", "modify", reviewer="admin", modified_fact="修改后2")
    assert r3["fact"] == "修改后2"
    mods = [r for r in fc.load() if r.get("modified_from")]
    assert len(mods) == 2


# ============ 投影 ============
def test_projection_confirmed(fc):
    _submit(fc, "RC-005")
    fc.review("RC-005", "confirm", reviewer="admin")
    p = project_candidate("RC-005", fc.load())
    assert p["current_status"] == STATUS_CONFIRMED
    assert p["reviewed"] is True
    assert p["confirmed"] is True
    assert p["family_id"] == "RC-005"
    assert p["target"] == "relationship_core"


def test_projection_rejected(fc):
    _submit(fc, "RC-006")
    fc.review("RC-006", "reject", reviewer="admin")
    p = project_candidate("RC-006", fc.load())
    assert p["current_status"] == STATUS_REJECTED
    assert p["confirmed"] is False


def test_projection_after_modify(fc):
    _submit(fc, "RC-007")
    fc.review("RC-007", "modify", reviewer="admin", modified_fact="改后")
    fc.review("RC-007", "confirm", reviewer="admin")
    p = project_candidate("RC-007", fc.load())
    assert p["current_status"] == STATUS_CONFIRMED
    assert len(p["revisions"]) == 1


def test_confirm_with_modified_fact_is_new_text(fc):
    """confirm+modified_fact 组合：改后确认，确认记录必须是修改后的文本（防旧文本进入目标层）。"""
    _submit(fc, "RC-008")
    rec = fc.review("RC-008", "confirm", reviewer="admin", note="改后确认",
                    modified_fact="修改后的正式文本")
    assert rec["status"] == STATUS_CONFIRMED
    assert rec["fact"] == "修改后的正式文本"
    assert rec.get("modified_from") == "RC-008"
    p = project_candidate("RC-008", fc.load())
    assert p["current_status"] == STATUS_CONFIRMED
    assert len(p["revisions"]) == 1
    # 幂等：已 confirmed 后再 confirm → already，不追加
    again = fc.review("RC-008", "confirm", reviewer="admin")
    assert again.get("already") is True
    assert len([r for r in fc.load() if r.get("status") == STATUS_CONFIRMED]) == 1


# ============ 目标层 ============
def test_target_resolution():
    assert resolve_confirmation_target({"candidate_id": "RC-001"}) == "relationship_core"
    assert resolve_confirmation_target({"candidate_id": "SM-001"}) == "self_model"
    assert resolve_confirmation_target({"candidate_id": "X-001"}) is None


# ============ 不变量（SelfModel 幂等） ============
def test_sm_idempotent_confirm(sm):
    sid = sm.submit(fact="羽依认为爱是持续行动", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.9)
    sm.review(sid, "confirm", reviewer="admin")
    r2 = sm.review(sid, "confirm", reviewer="admin")
    assert r2.get("already") is True
    assert len(sm.confirmed_statements()) == 1


def test_sm_multi_coexist(sm):
    for i in ("1", "2", "3"):
        sid = sm.submit(fact=f"陈述{i}", category="x", source_type=SOURCE_YUI_STATED,
                        source_memory_ids=[f"m{i}"], evidence_summary="e", confidence=0.9)
        sm.review(sid, "confirm", reviewer="admin")
    assert len(sm.confirmed_statements()) == 3


def test_sm_reject_not_confirmed(sm):
    sid = sm.submit(fact="被拒", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    sm.review(sid, "reject", reviewer="admin")
    assert len(sm.confirmed_statements()) == 0


# ============ API 集成（投影 + 刷新一致性） ============
def _isolate_api_stores(monkeypatch, tmp_path):
    """API 测试隔离：candidate/RC/SM 三个 store 全部指向 tmp，不污染真实 data/。"""
    store = FactCandidateStore(str(tmp_path / "fc.jsonl"))
    rc_store = RelationshipCoreStore(str(tmp_path / "rc.jsonl"))
    sm_store = SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))
    monkeypatch.setattr("src.admin.api.governance_routes._store_fact_candidates",
                        lambda: store)
    monkeypatch.setattr("src.admin.api.governance_routes._store_relationship_core",
                        lambda: rc_store)
    monkeypatch.setattr("src.admin.api.governance_routes._store_self_model_statements",
                        lambda: sm_store)
    return store, rc_store, sm_store


def test_api_projection_and_refresh(monkeypatch, tmp_path):
    """API 返回投影；confirm 后刷新（重新 GET）状态一致（GOV-009/014）。"""
    import api_server as mod
    client = mod.app.test_client()
    store, _, _ = _isolate_api_stores(monkeypatch, tmp_path)
    # submit + confirm 经 API
    sid = store.submit(fact="API 投影测试", category="x", source_type=SOURCE_YUI_STATED,
                       source_memory_ids=["m1"], evidence_summary="e", confidence=0.9,
                       candidate_id="RC-API-1")
    r = client.get("/admin/api/governance/candidates")
    assert r.get_json()["count"] == 1
    proj = r.get_json()["candidates"][0]
    assert proj["current_status"] == "candidate"
    assert proj["reviewed"] is False
    # confirm
    client.post(f"/admin/api/governance/candidates/{sid}/review",
                json={"decision": "confirm", "reviewer": "admin"})
    # 刷新（重新 GET）→ 投影 confirmed，不再待审核
    r2 = client.get("/admin/api/governance/candidates")
    assert r2.get_json()["candidates"][0]["current_status"] == "confirmed"
    assert r2.get_json()["candidates"][0]["reviewed"] is True


def test_api_confirm_with_modified_fact_writes_target_layer(monkeypatch, tmp_path):
    """API 组合操作：confirm+modified_fact → 目标层（关系核心）写入修改后文本。"""
    import api_server as mod
    client = mod.app.test_client()
    store, rc_store, _ = _isolate_api_stores(monkeypatch, tmp_path)
    sid = store.submit(fact="原始：具体行为过细", category="x", source_type=SOURCE_YUI_STATED,
                       source_memory_ids=["m1"], evidence_summary="e", confidence=0.9,
                       candidate_id="RC-API-2")
    r = client.post(f"/admin/api/governance/candidates/{sid}/review",
                    json={"decision": "confirm", "reviewer": "admin",
                          "note": "改后确认", "modified_fact": "抽象化后的正式文本"})
    body = r.get_json()
    assert body["ok"] is True
    assert body["record"]["fact"] == "抽象化后的正式文本"
    assert body["record"]["status"] == "confirmed"
    # 投影 confirmed；关系核心落库为修改后文本，且只有一条
    cands = client.get("/admin/api/governance/candidates").get_json()["candidates"]
    assert cands[0]["current_status"] == "confirmed"
    rc_all = rc_store.list_all()
    assert len(rc_all) == 1
    assert rc_all[0]["agreements"] == ["抽象化后的正式文本"]
    assert rc_all[0]["status"] == "confirmed"
    assert rc_all[0]["fact_id"].startswith("RC-API-2#")
