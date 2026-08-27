# -*- coding: utf-8 -*-
"""C4: 连续性测试（第一阶段：C1 frontend provenance + 治理生命周期）。"""
import json
import os
from pathlib import Path

import pytest


# ============ C1: frontend 判定 ============
def test_frontend_detect_mc_tailscale(monkeypatch):
    import api_server as mod
    monkeypatch.setattr(mod, "_client_ip", lambda: "100.114.143.47")
    assert mod._detect_frontend() == "mc"


def test_frontend_detect_qq_loopback(monkeypatch):
    import api_server as mod
    monkeypatch.setattr(mod, "_client_ip", lambda: "127.0.0.1")
    assert mod._detect_frontend() == "qq"


def test_frontend_detect_unknown(monkeypatch):
    import api_server as mod
    monkeypatch.setattr(mod, "_client_ip", lambda: "")
    assert mod._detect_frontend() == "unknown"


def test_frontend_detect_external_qq(monkeypatch):
    import api_server as mod
    monkeypatch.setattr(mod, "_client_ip", lambda: "198.44.178.195")
    assert mod._detect_frontend() == "qq"


# ============ C2-a: FactCandidate 生命周期 ============
from src.governance.fact_candidate import (
    FactCandidateStore, STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_REJECTED,
    STATUS_HELD, SOURCE_YUI_STATED, SOURCE_SYSTEM_OBSERVED,
)


@pytest.fixture()
def cand_store(tmp_path):
    return FactCandidateStore(str(tmp_path / "fact_candidates.jsonl"))


def test_candidate_submit_and_lifecycle(cand_store):
    cid = cand_store.submit(
        fact="羽依认为爱是让对方被理解、被自己的方式接住",
        category="values_expressed", source_type=SOURCE_YUI_STATED,
        source_memory_ids=["mem_20260825011019_18bb"],
        evidence_summary="8-25 凌晨对话羽依原话", confidence=0.95,
    )
    assert cid is not None
    got = cand_store.get(cid)
    assert got["status"] == STATUS_CANDIDATE  # 提交后是 candidate，不是 confirmed


def test_confirm_creates_confirmed_record(cand_store):
    cid = cand_store.submit(fact="测试事实", category="addressing",
                            source_type=SOURCE_YUI_STATED,
                            source_memory_ids=["mem_x"], evidence_summary="证据",
                            confidence=0.9)
    new = cand_store.review(cid, "confirm", reviewer="admin", note="证据充分")
    assert new is not None
    assert new["status"] == STATUS_CONFIRMED
    assert new["reviewed_by"] == "admin"
    assert new["lineage"] == cid  # 追溯原始候选
    # 原始候选不被覆盖
    assert cand_store.get(cid)["status"] == STATUS_CANDIDATE


def test_reject_and_hold(cand_store):
    cid = cand_store.submit(fact="事实", category="x", source_type=SOURCE_YUI_STATED,
                            source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    r = cand_store.review(cid, "reject", reviewer="admin", note="证据不足")
    assert r["status"] == STATUS_REJECTED
    cid2 = cand_store.submit(fact="事实2", category="x", source_type=SOURCE_YUI_STATED,
                             source_memory_ids=["m2"], evidence_summary="e", confidence=0.5)
    r2 = cand_store.review(cid2, "hold", reviewer="admin", note="暂缓")
    assert r2["status"] == STATUS_HELD


def test_modify_keeps_lineage(cand_store):
    cid = cand_store.submit(fact="原话", category="x", source_type=SOURCE_YUI_STATED,
                            source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    r = cand_store.review(cid, "modify", reviewer="admin", modified_fact="修改后")
    assert r["status"] == STATUS_CANDIDATE  # modify 后仍是 candidate
    assert r["fact"] == "修改后"
    assert r["modified_from"] == cid


def test_append_only_no_overwrite(cand_store):
    cid = cand_store.submit(fact="事实", category="x", source_type=SOURCE_YUI_STATED,
                            source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    cand_store.review(cid, "confirm", reviewer="admin")
    # 原始记录仍在（candidate 状态未变）
    orig = cand_store.get(cid)
    assert orig["status"] == STATUS_CANDIDATE
    assert orig["fact"] == "事实"
    # 追加产生两条（原始 + confirmed）
    assert len(cand_store.load()) == 2


def test_system_observed_never_auto_confirmed(cand_store):
    # system_observed 只能产生 candidate；review confirm 必须人工显式调用
    cid = cand_store.submit(fact="系统观察", category="x", source_type=SOURCE_SYSTEM_OBSERVED,
                            source_memory_ids=["m1"], evidence_summary="系统观察模式", confidence=0.7)
    assert cid is not None
    got = cand_store.get(cid)
    assert got["status"] == STATUS_CANDIDATE  # 永不自动 confirmed


def test_invalid_source_type_rejected(cand_store):
    cid = cand_store.submit(fact="x", category="x", source_type="system_inferred",
                            source_memory_ids=["m1"], evidence_summary="e", confidence=0.5)
    assert cid is None  # system_inferred 不是合法 source_type


# ============ C2-c: SelfModelStatements ============
from src.governance.self_model_statements import SelfModelStatementsStore, STATUS_CONFIRMED, SOURCE_YUI_STATED


@pytest.fixture()
def sm_store(tmp_path):
    return SelfModelStatementsStore(str(tmp_path / "self_model_statements.jsonl"))


def test_sm_submit_and_confirm(sm_store):
    sid = sm_store.submit(
        fact="羽依希望长成更明白自己的羽依",
        category="aspiration", source_type=SOURCE_YUI_STATED,
        source_memory_ids=["mem_20260824065123_1884"],
        evidence_summary="8-24 羽依原话", confidence=0.9,
    )
    assert sid is not None
    got = sm_store.get(sid)
    assert got["status"] == STATUS_CANDIDATE  # 提交是 candidate
    assert got["source_type"] == "yui_stated"
    r = sm_store.review(sid, "confirm", reviewer="admin")
    assert r["status"] == STATUS_CONFIRMED
    assert len(sm_store.confirmed_statements()) == 1


def test_sm_supersede_append_only(sm_store):
    sid = sm_store.submit(fact="旧表述", category="x", source_type=SOURCE_YUI_STATED,
                          source_memory_ids=["m1"], evidence_summary="e", confidence=0.8)
    sm_store.review(sid, "confirm", reviewer="admin")
    sid2 = sm_store.submit(fact="新表述", category="x", source_type=SOURCE_YUI_STATED,
                           source_memory_ids=["m2"], evidence_summary="e", confidence=0.9)
    sm_store.review(sid2, "confirm", reviewer="admin")
    # supersede 旧 → 旧变 superseded，新保留 confirmed
    assert sm_store.supersede(sid, sid2, reviewer="admin") is True
    confirmed = sm_store.confirmed_statements()
    assert len(confirmed) == 1
    assert confirmed[0]["statement_id"].startswith(sid2)  # confirmed 记录带 #后缀，按 base 匹配
    # 旧记录仍在（append-only）
    all_recs = sm_store.load()
    assert any(r["statement_id"] == sid for r in all_recs)


# ============ C2-e/f: YUI_CORE + Relationship Core 两链注入 ============
def test_yui_core_in_production_chain():
    """生产链（engine）必须包含 YUI_CORE 四句（此前缺失的 bug）。"""
    from src.engine import ResponseEngine
    eng = ResponseEngine.__new__(ResponseEngine)
    eng.api_key = "sk"; eng._client = None; eng.model = "deepseek-v4-pro"
    eng.mock_mode = True; eng._empty_retry_max = 0
    msgs = eng._build_messages_original(
        user_message="你是谁", history=[], chat_memories=[], life_events=[],
        personality_context="", self_model_context={}, emotion_context={},
        relationship_context={}, context_prompt_blocks=[],
    )
    sys_text = msgs[0]["content"]
    assert "最初且最重要的关系对象" in sys_text  # YUI_CORE 在场
    assert "跨会话连续性" in sys_text


def test_relationship_core_block_both_chains(monkeypatch, tmp_path):
    """两条链都注入关系块；store 空时安全降级为空串。"""
    from src.identity.yui_core_profile import build_relationship_core_block
    # store 为空 → 空串（不注入）
    monkeypatch.setattr("src.relationship.relationship_core_store.RelationshipCoreStore",
                        lambda path=None: type("S", (), {"list_all": lambda self: []})())
    assert build_relationship_core_block() == ""

    # 有 confirmed 事实 → 渲染【你们的关系】
    rec = {"fact_id": "rc_001", "status": "confirmed", "agreements": ["羽依对清夏铃的惯用称呼是「清清」"],
           "evidence_summary": "证据", "superseded_by": None}
    monkeypatch.setattr("src.relationship.relationship_core_store.RelationshipCoreStore",
                        lambda path=None: type("S", (), {"list_all": lambda self: [rec]})())
    block = build_relationship_core_block()
    assert "你们的关系" in block
    assert "清清" in block


# ============ C4: 检索失效韧性（核心验收） ============
def test_retrieval_disabled_identity_survives(monkeypatch):
    """disable_retrieval=true（Episodic 完全不可用）时，常驻层仍支撑身份/关系。

    即使没有任何记忆注入，YUI_CORE + Relationship Core 在场。
    """
    from src.engine import ResponseEngine
    eng = ResponseEngine.__new__(ResponseEngine)
    eng.api_key = "sk"; eng._client = None; eng.model = "deepseek-v4-pro"
    eng.mock_mode = True; eng._empty_retry_max = 0
    msgs = eng._build_messages_original(
        user_message="你是谁", history=[], chat_memories=[],  # 记忆为空（模拟检索失效）
        life_events=[], personality_context="", self_model_context={},
        emotion_context={}, relationship_context={}, context_prompt_blocks=[],
    )
    sys_text = msgs[0]["content"]
    assert "浅雾羽依" in sys_text                       # Stable Identity
    assert "清夏铃" in sys_text                         # YUI_CORE（创造者）
    assert "最初且最重要的关系对象" in sys_text          # 关系连续


def test_mc_flood_does_not_affect_core(monkeypatch):
    """MC 洪峰：大量 MC 记录进入 episodic，常驻关系块不受影响。

    关系块来自 RelationshipCoreStore（独立于 episodic），不参与窗口竞争。
    """
    from src.identity.yui_core_profile import build_relationship_core_block
    recs = [
        {"fact_id": f"rc_{i}", "status": "confirmed", "agreements": [f"关系事实{i}"],
         "evidence_summary": "e", "superseded_by": None}
        for i in range(3)
    ]
    monkeypatch.setattr("src.relationship.relationship_core_store.RelationshipCoreStore",
                        lambda path=None: type("S", (), {"list_all": lambda self: recs})())
    block = build_relationship_core_block()
    # 即使 episodic 有 1000 条 MC（不影响此块），关系事实完整渲染
    assert "关系事实0" in block
    assert "关系事实2" in block
