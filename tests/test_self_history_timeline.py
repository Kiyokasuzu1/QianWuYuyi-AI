# -*- coding: utf-8 -*-
"""Self History Minimal Layer 专项测试（T1-T8 + 真实链模拟）。

覆盖：schema / 首次演化 v0→v1 / 二次演化 v1→v2 / 幂等 / before 不可变 /
evidence / 持久化 / 失败不记录 / apply_evolution hook 真实链。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _mk_record(proposal_id="prop_001", approval_id="appr_001",
               before=None, after=None, reasons=None, evidence=None):
    from src.personality.evolution_record import build_evolution_record
    rec = build_evolution_record(
        proposal_id=proposal_id,
        approval_id=approval_id,
        change_type="trait_delta",
        before=before or {"playfulness": 0.55},
        after=after or {"playfulness": 0.60},
        reasons=reasons or ["approved_by:admin"],
        confidence=0.9,
    )
    if evidence:
        rec["evidence_ids"] = evidence
    return rec


def _fresh_state():
    from src.personality.personality_state import PersonalityState
    return PersonalityState()


# ---------- T1 schema ----------
def test_t1_schema(tmp_path):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    rid = store.record_evolution(
        version=1, before={"warmth": 0.5}, after={"playfulness": 0.60},
        proposal_id="p1", approval_id="a1", evidence_ids=["m1"], reason="r")
    assert rid and rid.startswith("sh_")
    rec = store.load()[0]
    for k in ("record_id", "version", "applied_at", "before", "after",
              "proposal_id", "approval_id", "evidence_ids", "reason"):
        assert k in rec, f"缺少字段 {k}"


# ---------- T2 first evolution (v0 → v1) ----------
def test_t2_first_evolution(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    assert ps.version == 0
    rec = _mk_record("prop_001", "appr_001",
                     before={"warmth": 0.5}, after={"playfulness": 0.60},
                     evidence=["memory:mem_x"])
    result = ps.apply_evolution(rec)
    assert result["applied"] is True
    assert ps.version == 1
    hist = store.load()
    assert len(hist) == 1
    h = hist[0]
    assert h["version"] == 1
    assert h["before"]["playfulness"] == 0.55
    assert h["after"]["playfulness"] == 0.60
    assert h["proposal_id"] == "prop_001"
    assert h["approval_id"] == "appr_001"
    assert h["evidence_ids"] == ["memory:mem_x"]


# ---------- T3 second evolution (v1 → v2) ----------
def test_t3_second_evolution(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    ps.apply_evolution(_mk_record("prop_001", "a1",
                                  before={"warmth": 0.5}, after={"playfulness": 0.60}))
    ps.apply_evolution(_mk_record("prop_002", "a2",
                                  before={"warmth": 0.55}, after={"playfulness": 0.65}))
    hist = store.timeline()
    assert [h["version"] for h in hist] == [1, 2]
    assert hist[1]["before"]["playfulness"] == 0.60
    assert hist[1]["after"]["playfulness"] == 0.65


# ---------- T4 no duplicate ----------
def test_t4_no_duplicate(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    rec = _mk_record("prop_dup", "a1", after={"playfulness": 0.60})
    ps.apply_evolution(rec)
    # 重复 apply（EP-2 拦截）
    result2 = ps.apply_evolution(rec)
    assert result2["applied"] is False
    assert len(store.load()) == 1
    # store 层幂等兜底
    rid = store.record_evolution(version=1, before={"warmth": 0.5},
                                 after={"playfulness": 0.60}, proposal_id="prop_dup")
    assert rid is None
    assert len(store.load()) == 1


# ---------- T5 before immutable ----------
def test_t5_before_immutable(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    ps.apply_evolution(_mk_record("p1", "a1", after={"playfulness": 0.60}))
    # 后续再演化，历史 before 不变
    ps.apply_evolution(_mk_record("p2", "a2", after={"playfulness": 0.65}))
    hist = store.timeline()
    assert hist[0]["before"]["playfulness"] == 0.55  # v0 基线
    assert hist[1]["before"]["playfulness"] == 0.60  # v1 快照


# ---------- T6 evidence ----------
def test_t6_evidence_saved(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    ps.apply_evolution(_mk_record("p_ev", "a_ev", after={"playfulness": 0.60},
                                  evidence=["memory:mem_1", "memory:mem_2"]))
    h = store.load()[0]
    assert h["evidence_ids"] == ["memory:mem_1", "memory:mem_2"]


# ---------- T7 persistence ----------
def test_t7_persistence(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    ps.apply_evolution(_mk_record("p_persist", "a_p", after={"playfulness": 0.60}))
    # 新实例（模拟进程重启）读回
    store2 = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    hist = store2.timeline()
    assert len(hist) == 1
    assert hist[0]["version"] == 1
    assert hist[0]["proposal_id"] == "p_persist"


# ---------- T8 invalid apply not recorded ----------
def test_t8_invalid_apply_not_recorded(tmp_path, monkeypatch):
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    ps = _fresh_state()
    # after 为空 → apply 失败
    rec = _mk_record("p_bad", "a_bad")
    rec["after"] = {}
    result = ps.apply_evolution(rec)
    assert result["applied"] is False
    assert len(store.load()) == 0
    # identity 拦截（EP-4）→ 不记录
    rec2 = _mk_record("p_id", "a_id", after={"identity.core.self": 0.9})
    result2 = ps.apply_evolution(rec2)
    assert result2["applied"] is False
    assert len(store.load()) == 0


# ---------- 真实链模拟（测试环境：proposal → apply_approved_to_state → self_history） ----------
def test_real_chain_pipeline_to_self_history(tmp_path, monkeypatch):
    from src.contracts.growth_schema import ChangeItem, GrowthProposal
    from src.personality.self_history_timeline import SelfHistoryTimelineStore
    store = SelfHistoryTimelineStore(str(tmp_path / "sh_chain.jsonl"))
    monkeypatch.setattr(
        "src.personality.self_history_timeline.get_self_history_timeline_store",
        lambda: store)
    # 隔离全局 PersonalityState（避免污染其他测试）
    import src.personality.personality_state as ps_mod
    fresh = ps_mod.PersonalityState()
    monkeypatch.setattr(ps_mod, "get_personality_state", lambda: fresh)
    monkeypatch.setattr(ps_mod, "save_personality_state", lambda ps, **k: True)

    from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
    pipe = PersonalityEvolutionPipeline()

    proposal = GrowthProposal(
        id="prop_real_001",
        status="accepted",
        confidence=0.9,
        evidence_ids=["memory:mem_real_1", "memory:mem_real_2"],
        proposed_changes=[ChangeItem(path="personality.traits.playfulness",
                                     before=0.55, after=0.65)],
    )
    result = pipe.apply_approved_to_state(proposal=proposal, actor="test_drain")
    assert result.get("applied") is True, result
    hist = store.timeline()
    assert len(hist) == 1
    h = hist[0]
    assert h["version"] == 1
    assert h["proposal_id"] == "prop_real_001"
    assert set(h["evidence_ids"]) == {"memory:mem_real_1", "memory:mem_real_2"}
    assert h["after"]["playfulness"] == pytest.approx(0.65)
    # before 是完整快照（含默认 traits 全集）
    assert "creativity" in h["before"] and "empathy" in h["before"]
