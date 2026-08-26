# -*- coding: utf-8 -*-
"""Relationship P0 Freeze 专项测试（A-E）。

覆盖：
A. System C 冻结 —— MEMORY_CREATED 不再产生 relationship proposals（候选桥不再注册）
B. 历史 proposals 保留可读
C. Activation 端点 410，RelationshipCore 不变
D. RELATIONSHIP_CHANGED → create_proposal_from_event 返回 None
E. PERSONALITY_CHANGED 生成链不受影响（不 apply）
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------- Test A: System C frozen（候选桥不再注册） ----------
def test_a_system_c_writer_frozen():
    from src.events import handlers as h
    from src.events.bus import get_event_bus
    from src.events.events import EventType

    bus = get_event_bus()
    # 清除 MEMORY_CREATED 现有订阅（幂等重建现场）
    for handler in list(bus._handlers.get(EventType.MEMORY_CREATED, [])):
        bus.unsubscribe(EventType.MEMORY_CREATED, handler)
    h.register_builtin_handlers()
    subs = bus._handlers.get(EventType.MEMORY_CREATED, [])
    names = [getattr(f, "__name__", str(f)) for f in subs]
    assert "relationship_candidate_handler" not in names, (
        f"候选桥不应再订阅 MEMORY_CREATED: {names}")
    # 仍有 ExperienceBridge 等正常消费者
    assert any("experience" in n or "memory" in n for n in names), f"正常消费者丢失: {names}"
    # 清理现场，避免污染其他测试
    for handler in list(bus._handlers.get(EventType.MEMORY_CREATED, [])):
        bus.unsubscribe(EventType.MEMORY_CREATED, handler)


# ---------- Test B: 历史 proposals 保留可读 ----------
def test_b_historical_proposals_readable(tmp_path):
    from src.relationship.relationship_proposal_store import RelationshipProposalStore
    p = tmp_path / "rel_proposals.jsonl"
    lines = [
        '{"proposal_id": "relp_old_1", "status": "pending_review", "source_memory_ids": ["m1"], "score": 0.7}\n',
        '{"proposal_id": "relp_old_2", "status": "pending_review", "source_memory_ids": ["m2"], "score": 0.6}\n',
    ]
    p.write_text("".join(lines), encoding="utf-8")
    store = RelationshipProposalStore(str(p))
    recs = store.list()
    assert len(recs) == 2
    assert recs[0]["proposal_id"] == "relp_old_1"
    # 冻结不改 schema：字段原样可读


# ---------- Test C: Activation 端点 410，RelationshipCore 不变 ----------
def test_c_activation_disabled():
    os.environ.pop("YUYI_ADMIN_TOKEN", None)
    from flask import Flask
    from src.admin.api.routes import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.testing = True
    c = app.test_client()
    env = {"REMOTE_ADDR": "127.0.0.1", "SERVER_NAME": "test"}
    r = c.post("/admin/api/admin/governance/relationship-proposals/relp_x/activate",
               json={"reviewer": "366648462", "activation_reason": "x"}, environ_base=env)
    assert r.status_code == 410, f"activate 应 410: {r.status_code} {r.data[:120]}"
    # 存储侧不变：本地无写（激活端点不再触碰任何 store）


# ---------- Test D: RELATIONSHIP_CHANGED → None ----------
def test_d_relationship_mirror_disabled():
    from src.growth.proposal.reviewer import create_proposal_from_event
    from src.events.events import YuyiEvent, EventType

    ev = YuyiEvent(
        event_type=EventType.RELATIONSHIP_CHANGED,
        source="test",
        event_id="evt_rel_test",
        data={"dimension": "closeness", "old_value": 0.3, "new_value": 0.35, "user_id": "u1"},
    )
    result = create_proposal_from_event(ev)
    assert result is None, f"RELATIONSHIP_CHANGED 应返回 None: {result}"


# ---------- Test E: PERSONALITY_CHANGED 链不受影响 ----------
def test_e_personality_growth_untouched(monkeypatch):
    from src.growth.proposal.reviewer import create_proposal_from_event, get_proposal_reviewer
    from src.events.events import YuyiEvent, EventType

    calls = {}

    class FakeReviewer:
        def create_proposal(self, **kw):
            calls["kwargs"] = kw
            return {"proposal_type": kw.get("proposal_type")}

    monkeypatch.setattr("src.growth.proposal.reviewer.get_proposal_reviewer",
                        lambda: FakeReviewer())
    ev = YuyiEvent(
        event_type=EventType.PERSONALITY_CHANGED,
        source="test",
        event_id="evt_pers_test",
        data={"before_state": {"warmth": 0.5}, "after_state": {"warmth": 0.55}, "user_id": "u1"},
    )
    result = create_proposal_from_event(ev)
    assert result is not None
    assert calls["kwargs"]["proposal_type"] == "personality"
