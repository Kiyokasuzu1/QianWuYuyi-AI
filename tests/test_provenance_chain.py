# -*- coding: utf-8 -*-
"""T1-D Provenance 全链测试——四对象来源链统一。

覆盖：Trace/Snapshot/Proposal/SelfHistory 均带合法 provenance；
llm_candidate 必须带 source；test_contamination 生成侧拒绝；全链一致性。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.before_snapshot import capture_snapshots  # noqa: E402
from src.growth.experience_trace import make_trace  # noqa: E402
from src.growth.proposal_validator import validate_proposal  # noqa: E402
from src.growth.snapshot_hash import calculate_snapshot_hash  # noqa: E402
from src.personality.self_history_timeline import SelfHistoryTimelineStore  # noqa: E402

PROVENANCE_VALUES = ("system_rule", "llm_candidate", "system_state_read")


def _snap(path="self_state.initiative", value=0.5):
    return {"schema_version": 1, "path": path, "old_value": value,
            "captured_at": "t", "source": "growth_state.json",
            "hash": calculate_snapshot_hash(path, value, "growth_state.json"),
            "provenance": "system_state_read"}


def _proposal(**over):
    p = {
        "schema_version": 1, "proposal_id": "prop_p",
        "provenance": "system_rule",
        "evidence_trace_ids": ["exp_aaa111"],
        "evaluator_meta": {"pattern_detected": "p1", "pattern_frequency": 2,
                           "used_llm": False},
        "before_snapshot": [_snap()],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4}],
    }
    p.update(over)
    return p


# ---------- 1. 四对象 provenance 存在且合法 ----------
def test_all_objects_have_provenance():
    # Trace
    tr = make_trace("exp_aaa111", ["mem_1"], "runtime_pipeline", "摘要", "p1")
    assert tr["provenance"] in PROVENANCE_VALUES and tr["provenance"] == "system_rule"
    # Snapshot
    snap = capture_snapshots(["self_state.initiative"],
                             lambda p: {"value": 0.5, "source": "growth_state.json"})[0]
    assert snap["provenance"] == "system_state_read"
    # Proposal（schema 默认 + validator 接受）
    assert _proposal()["provenance"] == "system_rule"
    assert validate_proposal(_proposal()) == []
    # SelfHistory
    store = SelfHistoryTimelineStore(path=str(pytest.importorskip("tempfile").mkdtemp()) + "/sh.jsonl")
    store = SelfHistoryTimelineStore(path=os.path.join(
        pytest.importorskip("tempfile").mkdtemp(), "sh.jsonl"))
    rid = store.record_evolution(version=1, before={"x": 0.1}, after={"x": 0.2},
                                 proposal_id="prop_pv", provenance="system_rule")
    rec = store.get_by_proposal("prop_pv")
    assert rec["provenance"] == "system_rule"


# ---------- 2. llm_candidate 必须带 source ----------
def test_llm_candidate_requires_source():
    bad = _proposal(provenance="llm_candidate")
    assert any("llm_source" in e for e in validate_proposal(bad))
    ok = _proposal(provenance="llm_candidate",
                   evaluator_meta={"pattern_detected": "p1", "pattern_frequency": 2,
                                   "used_llm": True, "llm_source": "llm_candidate_v1"})
    assert validate_proposal(ok) == []


# ---------- 3. test_contamination 生成侧拒绝 ----------
def test_test_contamination_rejected_at_generation():
    bad = _proposal(provenance="test_contamination")
    assert any("provenance 非法" in e for e in validate_proposal(bad))


# ---------- 4. 全链一致性（提案 → SelfHistory 继承 provenance） ----------
def test_provenance_chain_inheritance():
    store = SelfHistoryTimelineStore(path=os.path.join(
        pytest.importorskip("tempfile").mkdtemp(), "sh.jsonl"))
    rid = store.record_evolution(version=1, before={"x": 0.1}, after={"x": 0.2},
                                 proposal_id="prop_chain", provenance="system_rule",
                                 before_snapshot=[_snap()],
                                 experience_trace_ids=["exp_aaa111"])
    rec = store.get_by_proposal("prop_chain")
    assert rec["provenance"] == "system_rule"
    assert rec["before_snapshot"][0]["provenance"] == "system_state_read"
    # trace 与 proposal 引用一致
    assert rec["experience_trace_ids"] == ["exp_aaa111"]


# ---------- 5. 旧数据兼容（无 provenance 字段可读） ----------
def test_legacy_without_provenance(tmp_path):
    store = SelfHistoryTimelineStore(path=str(tmp_path / "sh.jsonl"))
    with open(str(tmp_path / "sh.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"record_id": "sh_old", "version": 1, "applied_at": "t",
                            "before": {}, "after": {}, "proposal_id": "prop_old",
                            "approval_id": "", "evidence_ids": [],
                            "reason": ""}) + "\n")
    rec = store.get_by_proposal("prop_old")
    assert rec.get("provenance", "system_rule") == "system_rule", "旧记录默认值兼容"
