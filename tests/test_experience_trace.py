# -*- coding: utf-8 -*-
"""T1-A Experience Trace 测试（三架构约束 + 基础行为）。

约束 1：Trace 早于 Proposal（Memory→Experience→Trace 固化→Proposal 引用；未固化证据被拒）
约束 2：Trace 只保存事实，禁止人格解释字段
约束 3：MC 作为第一类验证对象（MC Event → MemoryStore → Trace → Proposal 引用全链）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.audit.mc_event_normalizer import normalize_mc_event  # noqa: E402
from src.growth.experience_trace import (  # noqa: E402
    FORBIDDEN_FIELDS, append_trace, exists, load_traces, make_trace,
    new_experience_id, resolve_trace_ids,
)
from src.growth.proposal_validator import validate_proposal  # noqa: E402


@pytest.fixture()
def trace_path(tmp_path):
    return str(tmp_path / "experience_trace.jsonl")


# ---------- 基础：构造/固化/解析 ----------
def test_make_and_append(trace_path):
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_001", "mc_002"], "mc_events", "玩家上线 3 次，均在同一时段", "join_pattern")
    assert tr and tr["schema_version"] == 1
    assert append_trace(tr, trace_path) is True
    assert exists(tid, trace_path) is True
    loaded = load_traces(trace_path)
    assert len(loaded) == 1 and loaded[0]["experience_id"] == tid
    assert resolve_trace_ids([tid], trace_path)[tid] is not None


def test_append_idempotent(trace_path):
    tid = new_experience_id()
    tr = make_trace(tid, ["mem_1"], "runtime_pipeline", "事实摘要")
    assert append_trace(tr, trace_path) is True
    assert append_trace(tr, trace_path) is False, "同 experience_id 幂等拒绝（不重复固化）"
    assert len(load_traces(trace_path)) == 1


def test_invalid_inputs(trace_path):
    assert make_trace("", ["mem_1"], "x", "s") is None
    assert make_trace("bad_id", ["mem_1"], "x", "s") is None
    assert make_trace("exp_abc", [], "x", "s") is None, "空证据不得固化"
    assert make_trace("exp_abc", ["mem_1"], "x", "s" * 201) is None, "summary 超长拒绝"


# ---------- 约束 1：Trace 早于 Proposal ----------
def test_constraint1_trace_before_proposal(trace_path):
    """未固化的 evidence 引用 → Validator 拒绝（trace_resolver 实装）。"""
    from src.growth.experience_trace import resolve_trace_ids
    tid = new_experience_id()
    proposal = {
        "schema_version": 1, "proposal_id": "prop_t1a",
        "evidence_trace_ids": [tid],
        "evaluator_meta": {"pattern_detected": "high_frequency_proactive",
                           "pattern_frequency": 5, "used_llm": False},
        "before_snapshot": [{"path": "self_state.initiative", "old_value": 0.5,
                             "captured_at": "t", "source": "growth_state.json", "hash": "h"}],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4}],
    }
    # 先固化（Trace 先于 Proposal）
    tr = make_trace(tid, ["mem_001"], "runtime_pipeline", "高频主动消息 5 次", "high_frequency_proactive")
    append_trace(tr, trace_path)
    assert validate_proposal(proposal, lambda ids: resolve_trace_ids(ids, trace_path)) == []
    # 未固化（Proposal 引用不存在的 trace）→ 拒绝
    tid2 = new_experience_id()
    proposal["evidence_trace_ids"] = [tid2]
    errs = validate_proposal(proposal, lambda ids: resolve_trace_ids(ids, trace_path))
    assert any("未固化" in e for e in errs), "Trace 必须早于 Proposal"


# ---------- 约束 2：Trace 无人格解释 ----------
def test_constraint2_no_meaning_in_trace(trace_path):
    tr = make_trace(new_experience_id(), ["mem_1"], "runtime_pipeline", "用户连续多次讨论建造")
    assert tr is not None
    for field in FORBIDDEN_FIELDS:
        assert field not in tr, f"Trace 事实层禁止: {field}"
    # 源码扫描（剔除 FORBIDDEN_FIELDS 常量定义行——定义本身含词，属自指）
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "src", "growth", "experience_trace.py"), encoding="utf-8").read()
    src = "\n".join(l for l in src.splitlines() if "FORBIDDEN_FIELDS =" not in l)
    for field in FORBIDDEN_FIELDS:
        assert field not in src, f"experience_trace.py 禁止字段名: {field}"


# ---------- 约束 3：MC 第一类验证对象 ----------
def test_constraint3_mc_full_chain(trace_path):
    """MC Event → normalizer → MemoryStore 记录 → Trace → Proposal 引用（同池同权）。"""
    # MC 事件 → 归一化记录（统一 MemoryStore 契约）
    rec, err = normalize_mc_event("join", {"player": "Kiyoka_suzu"}, "2026-08-27T12:00:00")
    assert err is None and rec["metadata"]["source"] == "mc_events"
    mem_id = f"mc_20260827_{rec['source_event_id'][-4:]}"
    # MC 经历固化（source_type=mc_events，memory 前缀 mc_）
    tid = new_experience_id()
    tr = make_trace(tid, [mem_id], "mc_events", "Kiyoka_suzu 上线（MC 载体）", "join_pattern")
    assert tr is not None
    append_trace(tr, trace_path)
    # Proposal 引用 MC trace → 校验通过（MC 与 QQ 同池同权）
    proposal = {
        "schema_version": 1, "proposal_id": "prop_mc_chain",
        "evidence_trace_ids": [tid],
        "evaluator_meta": {"pattern_detected": "join_pattern", "pattern_frequency": 3,
                           "used_llm": False},
        "before_snapshot": [{"path": "self_state.initiative", "old_value": 0.5,
                             "captured_at": "t", "source": "growth_state.json", "hash": "h"}],
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.45}],
    }
    from src.growth.experience_trace import resolve_trace_ids
    assert validate_proposal(proposal, lambda ids: resolve_trace_ids(ids, trace_path)) == []
    # trace 的 source_memory_ids 指向 MC 记忆（可追溯）
    loaded = load_traces(trace_path)[0]
    assert loaded["source_type"] == "mc_events"
    assert loaded["source_memory_ids"][0].startswith("mc_"), "MC 证据可回溯到 mc_ 记忆"
