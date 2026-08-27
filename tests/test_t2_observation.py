# -*- coding: utf-8 -*-
"""T2-0 真实数据观察链测试（Test 1-5，隔离环境）。

输入：data/memory.json 真实记录（只读，不修改）
验证：真实格式 memory → Trace → Proposal → Validator → Ledger(pending) → Replay
约束：不修改任何正式数据（全部临时目录）；proposal 保持 pending（禁止 approved/applied）。
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from src.growth.experience_trace import (  # noqa: E402
    append_trace, load_traces, make_trace, new_experience_id, resolve_trace_ids,
)
from src.growth.proposal_ledger import reduce_events  # noqa: E402
from src.growth.proposal_validator import validate_proposal  # noqa: E402
from src.growth.replay_engine import ReplayEngine  # noqa: E402
from src.growth.snapshot_hash import calculate_snapshot_hash  # noqa: E402


@pytest.fixture()
def real_memory_record():
    """从 data/memory.json 取第一条真实记录（只读）。"""
    path = os.path.join(_REPO, "data", "memory.json")
    assert os.path.exists(path), "memory.json 不存在"
    recs = json.load(open(path, encoding="utf-8"))
    assert recs, "memory.json 为空"
    rec = recs[0] if isinstance(recs, list) else next(iter(recs.values()))
    assert isinstance(rec, dict) and rec.get("content"), "记录格式异常"
    return rec


@pytest.fixture()
def obs_env(tmp_path):
    return {"trace_path": str(tmp_path / "trace.jsonl"),
            "events_path": str(tmp_path / "events.jsonl"),
            "proposals_path": str(tmp_path / "proposals.json")}


def _snap(path="self_state.initiative", value=0.5):
    return {"schema_version": 1, "path": path, "old_value": value,
            "captured_at": "t", "source": "growth_state.json",
            "hash": calculate_snapshot_hash(path, value, "growth_state.json"),
            "provenance": "system_state_read"}


def _valid_proposal(tid, pid="t2_prop_001"):
    return {"schema_version": 1, "provenance": "system_rule", "status": "pending",
            "proposal_id": pid, "evidence_trace_ids": [tid],
            "evaluator_meta": {"pattern_detected": "recurring_topic",
                               "pattern_frequency": 3, "used_llm": False},
            "before_snapshot": [_snap()],
            "proposed_changes": [{"path": "self_state.initiative", "after": 0.45}]}


# ---------- Test 1: 真实 memory 输入 → Trace ----------
def test_1_real_memory_to_trace(real_memory_record, obs_env):
    rec = real_memory_record
    tid = new_experience_id()
    tr = make_trace(tid, [str(rec.get("id") or "mem_obs")], "runtime_pipeline",
                    str(rec.get("content"))[:150], "recurring_topic", "system_rule")
    assert tr is not None
    assert append_trace(tr, obs_env["trace_path"]) is True
    loaded = load_traces(obs_env["trace_path"])
    assert len(loaded) == 1
    assert loaded[0]["source_memory_ids"][0] == str(rec.get("id") or "mem_obs")
    assert loaded[0]["provenance"] == "system_rule"
    assert "meaning" not in loaded[0], "Trace 无人格解释"


# ---------- Test 2: Trace → Proposal（validator PASS） ----------
def test_2_trace_to_proposal(real_memory_record, obs_env):
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_obs"], "runtime_pipeline",
                    str(real_memory_record.get("content"))[:150], "recurring_topic")
    append_trace(tr, obs_env["trace_path"])
    prop = _valid_proposal(tid)
    errs = validate_proposal(prop, lambda ids: resolve_trace_ids(ids, obs_env["trace_path"]))
    assert errs == [], f"真实格式 trace → proposal 应通过 validator: {errs}"


# ---------- Test 3: 非法 Proposal（缺 trace/snapshot/provenance）FAIL ----------
def test_3_invalid_proposal_fails(real_memory_record, obs_env):
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_obs"], "runtime_pipeline", "摘要")
    append_trace(tr, obs_env["trace_path"])
    resolver = lambda ids: resolve_trace_ids(ids, obs_env["trace_path"])  # noqa: E731
    # 缺 trace_id
    p1 = _valid_proposal(tid)
    p1["evidence_trace_ids"] = []
    assert validate_proposal(p1, resolver), "缺 trace_id 必须 FAIL"
    # 缺 snapshot
    p2 = _valid_proposal(tid)
    p2["before_snapshot"] = []
    assert validate_proposal(p2, resolver), "缺 snapshot 必须 FAIL"
    # 缺 provenance
    p3 = _valid_proposal(tid)
    p3["provenance"] = "unknown_source"
    assert validate_proposal(p3, resolver), "非法 provenance 必须 FAIL"


# ---------- Test 4: Proposal → Ledger pending（禁止 approved/applied） ----------
def test_4_ledger_pending_only(obs_env):
    ev = {"schema_version": 1, "event": "proposal_generated",
          "proposal_id": "t2_prop_001", "timestamp": "t", "reviewer": "system"}
    with open(obs_env["events_path"], "a", encoding="utf-8") as f:
        f.write(json.dumps(ev) + "\n")
    state = reduce_events([json.loads(l) for l in open(obs_env["events_path"]) if l.strip()])
    assert state == {"t2_prop_001": "pending"}, "生成后必须 pending（禁止 approved/applied）"
    # 无 approve 事件 → 状态不变
    state2 = reduce_events([json.loads(l) for l in open(obs_env["events_path"]) if l.strip()])
    assert state2["t2_prop_001"] == "pending"


# ---------- Test 5: Replay PASS ----------
def test_5_replay_pass(real_memory_record, obs_env):
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_obs"], "runtime_pipeline",
                    str(real_memory_record.get("content"))[:150], "recurring_topic")
    append_trace(tr, obs_env["trace_path"])
    prop = _valid_proposal(tid)
    with open(obs_env["events_path"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": 1, "event": "proposal_generated",
                            "proposal_id": "t2_prop_001", "timestamp": "t",
                            "reviewer": "system"}) + "\n")
    eng = ReplayEngine(events_path=obs_env["events_path"], trace_path=obs_env["trace_path"],
                       proposal_loader=lambda pid: json.loads(json.dumps(prop)))
    r = eng.replay("t2_prop_001")
    assert r.result == "pass", [(c.check, c.ok, c.detail) for c in r.checks]
