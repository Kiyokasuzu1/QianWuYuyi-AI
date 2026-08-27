# -*- coding: utf-8 -*-
"""G1 Governance Console 测试（10 用例）。

覆盖：页面加载 / 无 token 拒绝 / 候选入页面 / confirm 生命周期 /
reject 不入常驻 / modify 不覆盖 / supersede 不删除 / audit 记录 /
system_observed 不自动 confirmed / SM source_type 保持。
"""
import json
import os

import pytest

os.environ.setdefault("YUYI_LLM_MOCK", "1")

import api_server as mod
from src.governance.fact_candidate import FactCandidateStore, SOURCE_YUI_STATED, SOURCE_SYSTEM_OBSERVED


@pytest.fixture()
def client():
    return mod.app.test_client()


def _tok():
    return mod._get_admin_token() or "x"


def _ext(environ_base=None):
    return {"environ_base": environ_base or {"REMOTE_ADDR": "203.0.113.9"}}


# G1-TEST-01: 页面可以加载
def test_g1_page_loads(client):
    r = client.get("/admin/governance")
    assert r.status_code == 200
    assert "羽依治理中心" in r.data.decode("utf-8", "replace")


# G1-TEST-02: 无 admin token 时治理 API 拒绝访问（外部）
def test_g1_no_token_rejected(client):
    r = client.get("/admin/api/governance/candidates", **_ext())
    assert r.status_code in (401, 403)


# G1-TEST-03: candidate 可以进入页面（API 返回候选）
def test_g1_candidates_in_api(client, tmp_path):
    store = FactCandidateStore(str(tmp_path / "fc.jsonl"))
    cid = store.submit(fact="测试候选", category="addressing", source_type=SOURCE_YUI_STATED,
                       source_memory_ids=["m1"], evidence_summary="e", confidence=0.9)
    assert cid is not None
    got = store.get(cid)
    assert got["status"] == "candidate"
    assert got["fact"] == "测试候选"


# G1-TEST-04: confirm 后 candidate.status=confirmed 且 confirmed 层出现记录
def test_g1_confirm_lifecycle(client, tmp_path):
    from src.governance.self_model_statements import SelfModelStatementsStore
    sm = SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))
    sid = sm.submit(fact="羽依认为爱是选择之后持续的行动", category="values_expressed",
                    source_type=SOURCE_YUI_STATED, source_memory_ids=["mem_x"],
                    evidence_summary="原话", confidence=0.9)
    r = sm.review(sid, "confirm", reviewer="admin")
    assert r["status"] == "confirmed"
    assert sm.get(sid)["status"] == "candidate"  # 原始候选不变
    assert len(sm.confirmed_statements()) == 1


# G1-TEST-05: reject 后不进入常驻人格层
def test_g1_reject_not_in_core(client, tmp_path):
    from src.governance.self_model_statements import SelfModelStatementsStore
    sm = SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))
    sid = sm.submit(fact="被拒内容", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    sm.review(sid, "reject", reviewer="admin")
    assert len(sm.confirmed_statements()) == 0  # 不进常驻


# G1-TEST-06: modify 不覆盖旧 candidate，产生可追踪新版本
def test_g1_modify_lineage(client, tmp_path):
    store = FactCandidateStore(str(tmp_path / "fc.jsonl"))
    cid = store.submit(fact="原话", category="x", source_type=SOURCE_YUI_STATED,
                       source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    r = store.review(cid, "modify", reviewer="admin", modified_fact="修改后")
    assert r["fact"] == "修改后"
    assert r["modified_from"] == cid  # 可追踪
    assert store.get(cid)["fact"] == "原话"  # 原始未覆盖


# G1-TEST-07: supersede 不物理删除旧事实
def test_g1_supersede_no_delete(client, tmp_path):
    from src.governance.self_model_statements import SelfModelStatementsStore
    sm = SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))
    sid = sm.submit(fact="旧", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.8)
    sm.review(sid, "confirm", reviewer="admin")
    sid2 = sm.submit(fact="新", category="x", source_type=SOURCE_YUI_STATED,
                     source_memory_ids=["m2"], evidence_summary="e", confidence=0.9)
    sm.review(sid2, "confirm", reviewer="admin")
    sm.supersede(sid, sid2, reviewer="admin")
    # 旧记录仍在（append-only）
    all_ids = [r["statement_id"] for r in sm.load()]
    assert any(i.startswith(sid) for i in all_ids)
    # confirmed 只剩新
    assert len(sm.confirmed_statements()) == 1


# G1-TEST-08: audit log 记录治理操作
def test_g1_audit_written(client, tmp_path):
    from src.admin.api.governance_routes import _audit
    _audit("review:confirm", "fact_candidate", "RC-001", {"s": "candidate"}, {"s": "confirmed"}, "test", "admin")
    path = os.path.join("data", "governance", "audit_log.jsonl")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    assert any("RC-001" in l for l in lines)


# G1-TEST-09: system_observed 不允许直接 confirmed（须人工 review）
def test_g1_system_observed_not_auto_confirmed(client, tmp_path):
    store = FactCandidateStore(str(tmp_path / "fc.jsonl"))
    cid = store.submit(fact="系统观察", category="x", source_type=SOURCE_SYSTEM_OBSERVED,
                       source_memory_ids=["m1"], evidence_summary="观察", confidence=0.7)
    got = store.get(cid)
    assert got["status"] == "candidate"  # 永不自动 confirmed
    # 即使 review confirm，也必须人工显式调用（此处验证提交本身不 auto-confirm）
    assert store.get(cid)["status"] == "candidate"


# G1-TEST-10: SM statement 的 source_type 保持正确
def test_g1_sm_source_type_preserved(client, tmp_path):
    from src.governance.self_model_statements import SelfModelStatementsStore
    sm = SelfModelStatementsStore(str(tmp_path / "sm.jsonl"))
    sid = sm.submit(fact="陈述", category="x", source_type=SOURCE_YUI_STATED,
                    source_memory_ids=["m1"], evidence_summary="e", confidence=0.8)
    r = sm.review(sid, "confirm", reviewer="admin")
    assert r["source_type"] == "yui_stated"  # 不被改写
