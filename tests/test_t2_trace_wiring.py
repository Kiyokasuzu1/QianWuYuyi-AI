# -*- coding: utf-8 -*-
"""T2-1-P0 ExperienceTrace Production Wiring 验收测试（Test 1-5）。

1. 真实 memory → 自动 Trace（pipeline 接线）
2. pipeline 生成 Proposal 必须含 evidence_trace_ids
3. 删除 trace → Validator 拒绝
4. 新 proposal → ReplayEngine PASS
5. 正式数据保护（memory/proposal/ledger hash 前后一致）
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from src.growth.pipeline import GrowthPipeline  # noqa: E402
from src.growth.proposal_validator import validate_proposal  # noqa: E402
from src.growth.experience_trace import resolve_trace_ids, load_traces  # noqa: E402
from src.growth.replay_engine import ReplayEngine  # noqa: E402
from src.growth.snapshot_hash import calculate_snapshot_hash  # noqa: E402


def _real_memory():
    recs = json.load(open(os.path.join(_REPO, "data", "memory.json"), encoding="utf-8"))
    rec = recs[0] if isinstance(recs, list) else next(iter(recs.values()))
    return str(rec.get("id") or "mem_real")  # 真实 memory id


def _pipeline_proposal(event):
    gp = GrowthPipeline.__new__(GrowthPipeline)
    gp.growth_state = None
    gp._snapshot_state_reader = lambda path: {"value": 0.5, "source": "growth_state.json"}
    evaluated = {"target_candidates": ["self_state.initiative"], "applied_delta": 0.1,
                 "confidence": 0.6, "growth_signal": "s"}
    return gp._build_proposal_from_evaluated(evaluated, event)


@pytest.fixture(autouse=True)
def _clean_trace_file():
    """teardown：清理 pipeline 写入正式路径的测试 trace。"""
    yield
    tf = os.path.join("data", "growth", "experience_trace.jsonl")
    if os.path.exists(tf):
        lines = open(tf, encoding="utf-8").read().strip()
        if "mem_real" in lines or "test_" in lines:  # 仅清理测试产物
            os.remove(tf)


# ---------- Test 1: 真实 memory → 自动 Trace ----------
def test_1_real_memory_auto_trace():
    mid = _real_memory()
    prop = _pipeline_proposal({"event_id": "ev_w1",
                               "evidence": [{"id": "exp_x", "source_id": mid}]})
    assert prop is not None
    traces = load_traces()
    assert any(t["source_memory_ids"][0] == mid for t in traces), \
        "真实 memory 必须自动固化 Trace"


# ---------- Test 2: Proposal 含 evidence_trace_ids ----------
def test_2_proposal_binds_trace():
    mid = _real_memory()
    prop = _pipeline_proposal({"event_id": "ev_w2",
                               "evidence": [{"id": "exp_y", "source_id": mid}]})
    assert prop is not None and prop.evidence_trace_ids, "提案必须绑定 trace"
    # trace 可解析
    resolved = resolve_trace_ids(prop.evidence_trace_ids)
    assert resolved.get(prop.evidence_trace_ids[0]) is not None


# ---------- Test 3: 删除 trace → Validator 拒绝 ----------
def test_3_missing_trace_rejected():
    prop = {
        "schema_version": 1, "provenance": "system_rule", "status": "pending",
        "proposal_id": "t2w_miss", "evidence_trace_ids": ["exp_ghost_missing"],
        "evaluator_meta": {"pattern_detected": "growth_pipeline",
                           "pattern_frequency": 3, "used_llm": False},
        "before_snapshot": [{"schema_version": 1, "path": "self_state.initiative",
                             "old_value": 0.5, "captured_at": "t",
                             "source": "growth_state.json",
                             "hash": calculate_snapshot_hash("self_state.initiative", 0.5, "growth_state.json"),
                             "provenance": "system_state_read"}],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4}],
    }
    errs = validate_proposal(prop, lambda ids: resolve_trace_ids(ids))
    assert any("未固化" in e for e in errs), "删除 trace 必须被 Validator 拒绝"


# ---------- Test 4: 新 proposal → Replay PASS ----------
def test_4_replay_pass_on_wired_proposal(tmp_path):
    mid = _real_memory()
    prop = _pipeline_proposal({"event_id": "ev_w4",
                               "evidence": [{"id": "exp_z", "source_id": mid}]})
    assert prop is not None
    events_path = str(tmp_path / "events.jsonl")
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": 1, "event": "proposal_generated",
                            "proposal_id": prop.id, "timestamp": "t",
                            "reviewer": "system"}) + "\n")
    eng = ReplayEngine(events_path=events_path,
                       proposal_loader=lambda pid: prop.to_dict())
    r = eng.replay(prop.id)
    assert r.result == "pass", [(c.check, c.ok, c.detail) for c in r.checks]


# ---------- Test 5: 正式数据保护 ----------
def test_5_official_data_untouched():
    targets = ["data/memory.json", "data/growth_state.json",
               "data/proposals/growth_proposals.json",
               "data/proposals/proposal_events.jsonl"]
    before = {}
    for t in targets:
        p = os.path.join(_REPO, t)
        before[t] = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "NONE"
    # 触发 pipeline 生成（写入 trace 正式路径——链目标行为）
    mid = _real_memory()
    prop = _pipeline_proposal({"event_id": "ev_w5",
                               "evidence": [{"id": "exp_q", "source_id": mid}]})
    assert prop is not None
    for t in targets:
        p = os.path.join(_REPO, t)
        after = hashlib.sha256(open(p, "rb").read()).hexdigest() if os.path.exists(p) else "NONE"
        assert before[t] == after, f"正式数据被修改: {t}"
