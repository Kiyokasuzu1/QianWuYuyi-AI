# -*- coding: utf-8 -*-
"""T0-B Proposal Validator 测试（含第一条治理闭环：reject → 事件 → ledger 回放）。

覆盖：八项校验正反例 + 规则7去重 + 规则8逐条path断言 + 闭环可回放 + 只拒绝无批准能力。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.proposal_ledger import reduce_events  # noqa: E402
from src.growth.proposal_validator import (  # noqa: E402
    validate_proposal, validator_rejection_event,
)


def _valid_proposal():
    return {
        "schema_version": 1,
        "proposal_id": "prop_test_valid_001",
        "evidence_trace_ids": ["exp_aaa111", "exp_bbb222"],
        "evaluator_meta": {"insight_type": "pattern",
                           "pattern_detected": "high_frequency_proactive",
                           "pattern_frequency": 5, "used_llm": False},
        "before_snapshot": [
            {"path": "self_state.initiative", "old_value": 0.5,
             "captured_at": "2026-08-27T12:00:00", "source": "growth_state.json",
             "hash": "abc123"}],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4,
                              "reason": "高频主动行为"}],
    }


# ---------- 规则 1: schema_version ----------
def test_r1_schema_version():
    p = _valid_proposal()
    assert validate_proposal(p) == []
    bad = dict(p); bad.pop("schema_version")
    assert any("schema_version" in e for e in validate_proposal(bad))
    bad2 = dict(p); bad2["schema_version"] = 99
    assert validate_proposal(bad2)


# ---------- 规则 2: evidence 非空且可解析 ----------
def test_r2_evidence():
    p = _valid_proposal()
    bad = dict(p); bad["evidence_trace_ids"] = []
    assert any("为空" in e for e in validate_proposal(bad))
    bad2 = dict(p); bad2["evidence_trace_ids"] = ["not_an_id!"]
    assert any("不可解析" in e for e in validate_proposal(bad2))
    bad3 = dict(p); bad3["evidence_trace_ids"] = ["test_exp_001"]
    assert any("测试数据" in e for e in validate_proposal(bad3))


# ---------- 规则 3/4: pattern ----------
def test_r34_pattern():
    p = _valid_proposal()
    bad = dict(p)
    bad["evaluator_meta"] = dict(p["evaluator_meta"], pattern_detected="test_pattern")
    assert any("测试模式" in e for e in validate_proposal(bad))
    bad2 = dict(p)
    bad2["evaluator_meta"] = dict(p["evaluator_meta"], pattern_frequency=0)
    assert any("pattern_frequency" in e for e in validate_proposal(bad2))


# ---------- 规则 5: before_snapshot 完整 ----------
def test_r5_snapshot_complete():
    p = _valid_proposal()
    bad = dict(p); bad["before_snapshot"] = []
    assert any("缺失" in e for e in validate_proposal(bad))
    bad2 = dict(p)
    bad2["before_snapshot"] = [{"path": "x"}]  # 缺 4 字段
    errs = validate_proposal(bad2)
    assert any("缺字段" in e for e in errs)


# ---------- 规则 6: LLM 标注 ----------
def test_r6_llm_must_be_attributed():
    p = _valid_proposal()
    bad = dict(p)
    bad["evaluator_meta"] = dict(p["evaluator_meta"], used_llm=True)
    assert any("llm_source" in e for e in validate_proposal(bad))
    ok = dict(p)
    ok["evaluator_meta"] = dict(p["evaluator_meta"], used_llm=True, llm_source="llm_candidate")
    assert validate_proposal(ok) == []


# ---------- 规则 7: evidence 去重 ----------
def test_r7_evidence_dedup():
    p = _valid_proposal()
    bad = dict(p)
    bad["evidence_trace_ids"] = ["exp_aaa111", "exp_aaa111", "exp_aaa111"]
    assert any("重复" in e for e in validate_proposal(bad)), "重复证据不得增强强度"


# ---------- 规则 8: change.path 必须在 snapshot.paths（逐条断言） ----------
def test_r8_paths_match_per_change():
    p = _valid_proposal()
    bad = dict(p)
    bad["proposed_changes"] = [{"path": "self_state.curiosity", "after": 0.8}]
    errs = validate_proposal(bad)
    assert any("不在 before_snapshot" in e for e in errs), \
        "change.path 必须逐条存在于 snapshot.paths（防路径错配）"
    # 集合级一致但多一条 change 也应拒绝
    bad2 = dict(p)
    bad2["proposed_changes"] = [{"path": "self_state.initiative", "after": 0.4},
                                {"path": "personality.traits.empathy", "after": 0.7}]
    assert any("不在 before_snapshot" in e for e in validate_proposal(bad2))


# ---------- fail-closed ----------
def test_non_dict_rejected():
    assert validate_proposal(None)
    assert validate_proposal("x")


# ---------- 第一条治理闭环：reject → 事件 → ledger 回放 ----------
def test_governance_loop_reject_event_replay():
    """invalid proposal → Validator reject → validator_rejected 事件 → ledger 可回放。"""
    bad = dict(_valid_proposal())
    bad["proposal_id"] = "prop_invalid_001"
    bad["evidence_trace_ids"] = ["test_exp_x"]
    bad["evaluator_meta"] = dict(bad["evaluator_meta"], pattern_detected="test_pattern")
    errors = validate_proposal(bad)
    assert errors, "非法提案必须被拒绝"

    ev = validator_rejection_event(bad, errors)
    assert ev["event"] == "proposal_validator_rejected"
    assert ev["proposal_id"] == "prop_invalid_001"
    assert "test_pattern" in ev["reason"]

    # 事件入账本后 reducer 可回放（rejected 终态）
    events = [json.loads(json.dumps(ev))]
    state = reduce_events(events)
    assert state.get("prop_invalid_001") == "rejected", "回放：validator 拒绝 → rejected"
    # 与人工 reject 事件同终态（幂等/一致）
    events2 = [json.loads(json.dumps(ev)),
               {"schema_version": 1, "event": "proposal_rejected",
                "proposal_id": "prop_invalid_001", "timestamp": "t", "reviewer": "admin"}]
    assert reduce_events(events2).get("prop_invalid_001") == "rejected"


# ---------- 只拒绝，无批准能力（源码断言） ----------
def test_validator_only_rejects():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "growth", "proposal_validator.py"), encoding="utf-8").read()
    for banned in ("def approve", "def apply", "personality_state", "apply_evolution"):
        assert banned not in src, f"Validator 禁止: {banned}"
