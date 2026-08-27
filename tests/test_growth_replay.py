# -*- coding: utf-8 -*-
"""T1-E Replay Engine 测试（六项验收）。

1. 完整 replay 通过（Trace→Snapshot→Proposal→Ledger 全链）
2. 篡改 snapshot old_value → hash 变化 → FAIL
3. 删除 trace → FAIL
4. validator 规则漂移（构造非法提案）→ FAIL
5. ledger 事件缺失 → FAIL
6. Replay 不修改状态（运行前后事件流/personality_state 不变）
"""
import hashlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.experience_trace import append_trace, make_trace, new_experience_id  # noqa: E402
from src.growth.growth_audit import DEFAULT_EVENTS_PATH  # noqa: E402
from src.growth.replay_engine import ReplayEngine  # noqa: E402
from src.growth.snapshot_hash import calculate_snapshot_hash  # noqa: E402


def _snap(path="self_state.initiative", value=0.5, source="growth_state.json"):
    return {"schema_version": 1, "path": path, "old_value": value,
            "captured_at": "t", "source": source,
            "hash": calculate_snapshot_hash(path, value, source),
            "provenance": "system_state_read"}


def _valid_proposal(pid="prop_r1"):
    return {
        "schema_version": 1, "provenance": "system_rule", "status": "pending",
        "proposal_id": pid,
        "evidence_trace_ids": ["exp_replay01"],
        "evaluator_meta": {"pattern_detected": "high_frequency_proactive",
                           "pattern_frequency": 5, "used_llm": False},
        "before_snapshot": [_snap()],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4}],
    }


@pytest.fixture()
def env(tmp_path):
    """完整环境：trace + ledger 事件 + proposal loader。"""
    trace_path = str(tmp_path / "experience_trace.jsonl")
    events_path = str(tmp_path / "proposal_events.jsonl")
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_001"], "runtime_pipeline", "高频主动消息 5 次",
                    "high_frequency_proactive", "system_rule")
    append_trace(tr, trace_path)
    # ledger 事件：generated + approved（pending 状态）
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"schema_version": 1, "event": "proposal_generated",
                            "proposal_id": "prop_r1", "timestamp": "t",
                            "reviewer": "system"}) + "\n")

    proposals = {"prop_r1": _valid_proposal()}

    def loader(pid):
        p = proposals.get(pid)
        return json.loads(json.dumps(p)) if p else None

    return {"trace_path": trace_path, "events_path": events_path,
            "loader": loader, "proposals": proposals, "tid": tid}


def _engine(env):
    return ReplayEngine(events_path=env["events_path"], trace_path=env["trace_path"],
                        proposal_loader=env["loader"])


# ---------- 1. 完整 replay 通过 ----------
def test_full_replay_pass(env):
    env["proposals"]["prop_r1"]["evidence_trace_ids"] = [env["tid"]]
    r = _engine(env).replay("prop_r1")
    assert r.result == "pass", [(c.check, c.ok, c.detail) for c in r.checks]
    assert all(c.ok for c in r.checks)
    assert r.replayed_state == "pending" == r.original_state


# ---------- 2. 篡改 snapshot old_value → hash 失配 → FAIL ----------
def test_snapshot_tamper_fail(env):
    prop = env["proposals"]["prop_r1"]
    prop["evidence_trace_ids"] = [env["tid"]]
    tampered = _snap(value=0.5)   # 合法快照（hash 基于 0.5）
    tampered["old_value"] = 0.9   # 篡改值但 hash 未更新 → 重算失配
    prop["before_snapshot"] = [tampered]
    r = _engine(env).replay("prop_r1")
    assert r.result == "fail"
    c = next(c for c in r.checks if c.check == "snapshot_hash")
    assert c.ok is False and "mismatch" in c.detail


# ---------- 3. 删除 trace → FAIL ----------
def test_trace_missing_fail(env):
    # 引用不存在的 trace id
    env["proposals"]["prop_r1"]["evidence_trace_ids"] = ["exp_ghost999"]
    r = _engine(env).replay("prop_r1")
    assert r.result == "fail"
    c = next(c for c in r.checks if c.check == "trace_exists")
    assert c.ok is False and "exp_ghost999" in c.detail


# ---------- 4. validator 漂移（非法提案）→ FAIL ----------
def test_validator_drift_fail(env):
    prop = env["proposals"]["prop_r1"]
    prop["evidence_trace_ids"] = [env["tid"]]
    prop["evaluator_meta"]["pattern_detected"] = "test_pattern"  # 规则 3 拒绝
    r = _engine(env).replay("prop_r1")
    assert r.result == "fail"
    c = next(c for c in r.checks if c.check == "validator_replay")
    assert c.ok is False


# ---------- 5. ledger 事件缺失 → FAIL ----------
def test_ledger_missing_fail(env):
    prop = env["proposals"]["prop_r1"]
    prop["evidence_trace_ids"] = [env["tid"]]
    # 事件流中无该提案事件（删除 events 文件）
    os.remove(env["events_path"])
    r = _engine(env).replay("prop_r1")
    assert r.result == "fail"
    c = next(c for c in r.checks if c.check == "ledger_state")
    assert c.ok is False


# ---------- 6. Replay 不修改状态 ----------
def test_replay_read_only(env):
    prop = env["proposals"]["prop_r1"]
    prop["evidence_trace_ids"] = [env["tid"]]
    before_events = open(env["events_path"], "rb").read()
    before_trace = open(env["trace_path"], "rb").read()
    before_prop = json.dumps(prop, sort_keys=True)
    r = _engine(env).replay("prop_r1")
    assert r.result == "pass"
    after_events = open(env["events_path"], "rb").read()
    after_trace = open(env["trace_path"], "rb").read()
    after_prop = json.dumps(env["proposals"]["prop_r1"], sort_keys=True)
    assert hashlib.sha256(before_events).hexdigest() == hashlib.sha256(after_events).hexdigest()
    assert hashlib.sha256(before_trace).hexdigest() == hashlib.sha256(after_trace).hexdigest()
    assert before_prop == after_prop, "Replay 不得修改提案"


# ---------- 附加：proposal 不存在 ----------
def test_proposal_not_found(env):
    r = _engine(env).replay("prop_nonexistent")
    assert r.result == "fail" and not r.checks[0].ok
