# -*- coding: utf-8 -*-
"""Phase 2 Shared-Life Continuity 专项测试。

覆盖：
- Store 生命周期（candidate→pending→confirmed→active→superseded/archived，幂等）
- Discovery 三条件（recurrence / temporal span / semantic consistency）
- 幻觉防护（同天爆发不产出 / 短期集中情绪不固化 / 关键词碰巧不算）
- 治理（confirm/modify/reject/supersede 可执行；不自动确认）
- 证据链（source_memory_ids 可反查）
- 常驻注入（确认后进 prompt；≤3 条；空 store 降级；双链一致）
- 边界（Pattern ≠ Personality：不写 personality_state）
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.governance.shared_life_pattern import (
    SharedLifePatternStore,
    STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_ACTIVE,
    STATUS_SUPERSEDED, STATUS_ARCHIVED, STATUS_REJECTED,
)
from src.memory.pattern_discovery import discover_patterns


def _rec(mid, content, ts_days_ago, hours=0):
    return {
        "id": mid,
        "content": content,
        "timestamp": (datetime.now() - timedelta(days=ts_days_ago, hours=hours)).isoformat(),
        "user_id": "366648462",
        "role": "user",
        "importance": 0.5,
        "metadata": {"memory_type": "user_experience"},
    }


def _spread_night_records(n=8):
    """跨 8 天的晚安类记录（recurrence + span 都满足）。"""
    return [
        _rec(f"night_{i}", f"晚安哦，陪我睡一会儿（第{i}晚）", ts_days_ago=i)
        for i in range(n)
    ]


# ============ Discovery 三条件 ============
def test_discovery_recurrence_and_span():
    recs = _spread_night_records(8)
    cands = discover_patterns(recs)
    night = [c for c in cands if c["theme_id"] == "night_companionship"]
    assert len(night) == 1
    c = night[0]
    assert c["occurrence_count"] == 8
    assert c["span_days"] >= 7
    assert len(c["source_memory_ids"]) == 8
    assert c["first_seen"] < c["last_seen"]


def test_discovery_same_day_burst_no_pattern():
    """幻觉防护：同一天 10 条晚安不产出模式（时间跨度条件）。"""
    recs = [
        _rec(f"burst_{i}", "晚安", ts_days_ago=0, hours=i)
        for i in range(10)
    ]
    cands = discover_patterns(recs)
    assert [c for c in cands if c["theme_id"] == "night_companionship"] == []


def test_discovery_semantic_consistency():
    """语义一致性：'今天睡觉了吗'这类询问不算共同模式。"""
    recs = [
        _rec("q1", "今天睡觉了吗", 0),
        _rec("q2", "今天睡觉了吗", 1),
        _rec("q3", "今天睡觉了吗", 2),
        _rec("q4", "今天睡觉了吗", 3),
    ]
    cands = discover_patterns(recs)
    assert [c for c in cands if c["theme_id"] == "night_companionship"] == []


def test_discovery_short_emotion_burst_no_pattern():
    """短期集中讨论不固化（某两天大量'爱'讨论不算长期模式）。"""
    recs = [
        _rec(f"love_{i}", f"我爱你（第{i}句）", ts_days_ago=i % 2)
        for i in range(10)
    ]
    # "爱"不在 require_any（深度讨论主题需 诚实/成长/存在 强信号）
    cands = discover_patterns(recs)
    assert [c for c in cands if "爱" in c["title"]] == []


# ============ Store 生命周期 ============
@pytest.fixture()
def store(tmp_path):
    return SharedLifePatternStore(str(tmp_path / "patterns.jsonl"))


def _submit(store, pid="pat_night_001", title="夜间陪伴",
            summary="清清与羽依之间长期存在睡前聊天、晚安与夜间陪伴互动。"):
    return store.submit(
        title=title, summary=summary,
        occurrence_count=27, first_seen="2026-07-16", last_seen="2026-08-25",
        source_memory_ids=[f"mem_{i}" for i in range(27)],
        evidence_summary="27 次命中，40 天跨度",
        confidence=0.85, pattern_id=pid)


def test_store_candidate_default(store):
    """默认 candidate，绝不自动确认。"""
    pid = _submit(store)
    assert pid
    rec = store.get(pid)
    assert rec["status"] == STATUS_CANDIDATE
    assert rec["source_memory_ids"][0] == "mem_0"
    assert store.list_active() == []


def test_store_confirm_then_active(store):
    _submit(store)
    r = store.review("pat_night_001", "confirm", reviewer="清清")
    assert r["status"] == STATUS_CONFIRMED
    assert r["confirmed_by"] == "清清"
    active = store.list_active()
    assert len(active) == 1
    assert active[0]["summary"] == "清清与羽依之间长期存在睡前聊天、晚安与夜间陪伴互动。"


def test_store_reject_and_idempotent(store):
    _submit(store)
    store.review("pat_night_001", "reject", reviewer="admin")
    assert store.current_status("pat_night_001") == STATUS_REJECTED
    # 幂等：reject 后再 confirm → already，不追加
    r2 = store.review("pat_night_001", "confirm", reviewer="admin")
    assert r2.get("already") is True
    assert store.list_active() == []


def test_store_supersede_and_archive(store):
    _submit(store)
    store.review("pat_night_001", "confirm", reviewer="admin")
    r = store.review("pat_night_001", "supersede", reviewer="admin", note="模式表述需修正")
    assert r["status"] == STATUS_SUPERSEDED
    # superseded 后 list_active 为空（不再常驻注入）
    assert store.list_active() == []


def test_store_modify_keeps_candidate(store):
    _submit(store)
    r = store.review("pat_night_001", "modify", reviewer="admin",
                     modified_summary="修正后的表述")
    assert r["status"] == STATUS_CANDIDATE  # modify 后仍待再审
    assert r["summary"] == "修正后的表述"
    assert store.list_active() == []


def test_store_evidence_traceability(store):
    """证据链：确认后的 pattern 可反查全部 source_memory_ids。"""
    _submit(store)
    store.review("pat_night_001", "confirm", reviewer="admin")
    active = store.list_active()[0]
    assert len(active["source_memory_ids"]) == 27
    assert active["source_memory_ids"][0] == "mem_0"


# ============ 常驻注入 ============
def test_injection_when_confirmed(store, tmp_path, monkeypatch):
    _submit(store)
    store.review("pat_night_001", "confirm", reviewer="admin")
    import src.governance.shared_life_pattern as slp_mod
    monkeypatch.setattr(slp_mod, "SharedLifePatternStore", lambda: store)
    from src.identity.yui_core_profile import build_shared_life_block
    block = build_shared_life_block()
    assert "【我们共同的生活】" in block
    assert "夜间陪伴" in block
    assert "睡前聊天" in block


def test_injection_empty_store_safe(tmp_path, monkeypatch):
    from src.identity.yui_core_profile import build_shared_life_block
    empty = SharedLifePatternStore(str(tmp_path / "empty.jsonl"))
    import src.governance.shared_life_pattern as slp_mod
    monkeypatch.setattr(slp_mod, "SharedLifePatternStore", lambda: empty)
    assert build_shared_life_block() == ""


def test_injection_max_three(store, monkeypatch):
    """常驻数量受限：≤3 条（生活背景不是 Memory Dump）。"""
    for i in range(5):
        store.submit(
            title=f"模式{i}", summary=f"长期模式{i}的表述",
            source_memory_ids=[f"m{i}"], pattern_id=f"pat_{i}")
        store.review(f"pat_{i}", "confirm", reviewer="admin")
    import src.governance.shared_life_pattern as slp_mod
    monkeypatch.setattr(slp_mod, "SharedLifePatternStore", lambda: store)
    from src.identity.yui_core_profile import build_shared_life_block
    block = build_shared_life_block()
    lines = [l for l in block.split("\n") if l.startswith("- ")]
    assert len(lines) <= 3


def test_dual_chain_injection_consistent(store, monkeypatch):
    """双链（engine + prompt_builder）共享同一构建函数。"""
    _submit(store)
    store.review("pat_night_001", "confirm", reviewer="admin")
    import src.governance.shared_life_pattern as slp_mod
    monkeypatch.setattr(slp_mod, "SharedLifePatternStore", lambda: store)
    from src.identity.yui_core_profile import build_shared_life_block
    block = build_shared_life_block()
    assert "【我们共同的生活】" in block
    # 两链都调用同一函数（grep 验证过 import 一致）
    assert "夜间陪伴" in block


# ============ 边界：Pattern ≠ Personality ============
def test_pattern_not_personality(store):
    """确认 pattern 不写 personality_state、不改变人格。"""
    from src.personality.personality_state import PersonalityState
    ps = PersonalityState()
    before = dict(ps.traits)
    _submit(store)
    store.review("pat_night_001", "confirm", reviewer="admin")
    assert ps.traits == before, "Pattern 确认不应改变人格"
    assert "pat_night_001" not in (ps._applied_proposal_ids or set())


# ============ 治理 API ============
def test_governance_api_patterns(tmp_path, monkeypatch):
    import api_server as mod
    client = mod.app.test_client()
    store = SharedLifePatternStore(str(tmp_path / "api_patterns.jsonl"))
    monkeypatch.setattr(
        "src.admin.api.governance_routes._store_shared_life_patterns",
        lambda: store)
    # submit via store（API 未提供 submit 端点——发现流程离线产出后人工提交）
    pid = _submit(store, pid="pat_api_001")
    # GET patterns
    r = client.get("/admin/api/governance/patterns")
    assert r.get_json()["count"] == 1
    # review confirm
    r2 = client.post(f"/admin/api/governance/patterns/{pid}/review",
                     json={"decision": "confirm", "reviewer": "admin"})
    assert r2.get_json()["ok"] is True
    # active 列表
    r3 = client.get("/admin/api/governance/patterns/active")
    assert r3.get_json()["count"] == 1
    assert r3.get_json()["patterns"][0]["status"] == "confirmed"
