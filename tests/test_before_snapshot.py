# -*- coding: utf-8 -*-
"""T1-B Before Snapshot v2 验收测试（审核要求六项 + pipeline 接入）。

1. 正常生成 snapshot（六字段+hash）
2. 状态读取失败拒绝生成（fail-closed）
3. hash 篡改检测 + 同状态 hash 稳定
4. path 不匹配拒绝（Validator 规则 8）
5. MC 事件成长链不受影响
6. 旧数据兼容（SelfHistory 旧记录读取 + 旧提案无 snapshot 可读）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.before_snapshot import capture_snapshots  # noqa: E402
from src.growth.snapshot_hash import (  # noqa: E402
    calculate_snapshot_hash, verify_snapshot_hash,
)
from src.growth.proposal_validator import validate_proposal  # noqa: E402
from src.personality.self_history_timeline import SelfHistoryTimelineStore  # noqa: E402


def _reader(**values):
    def reader(path):
        if path in values:
            return {"value": values[path], "source": "growth_state.json"}
        return None
    return reader


# ---------- 1. 正常生成 snapshot ----------
def test_normal_capture():
    snaps = capture_snapshots(["self_state.initiative", "self_state.curiosity"],
                              _reader(**{"self_state.initiative": 0.5,
                                         "self_state.curiosity": 0.7}))
    assert snaps is not None and len(snaps) == 2
    for s in snaps:
        assert set(s.keys()) == {"schema_version", "path", "old_value", "captured_at",
                                 "source", "hash", "provenance"}
        assert s["schema_version"] == 1
        assert s["provenance"] == "system_state_read"
        assert s["hash"].startswith("sha256:")
    assert snaps[0]["old_value"] == 0.5


# ---------- 2. 状态读取失败拒绝生成（fail-closed） ----------
def test_capture_failure_fail_closed():
    # 路径缺失 → None
    assert capture_snapshots(["self_state.unknown"], _reader()) is None
    # reader 抛异常 → None
    def boom(path):
        raise RuntimeError("state unavailable")
    assert capture_snapshots(["self_state.x"], boom) is None
    # old_value=None → 拒绝
    assert capture_snapshots(["self_state.x"], _reader(**{"self_state.x": None})) is None
    # 空 paths → None
    assert capture_snapshots([], _reader()) is None


# ---------- 3. hash 稳定 + 篡改检测 ----------
def test_hash_stability_and_tamper():
    h1 = calculate_snapshot_hash("self_state.initiative", 0.5, "growth_state.json")
    h2 = calculate_snapshot_hash("self_state.initiative", 0.5, "growth_state.json")
    assert h1 == h2, "同输入必须同 hash"
    assert len(h1) == len("sha256:") + 64
    # 篡改检测：改值 → hash 失配
    assert not verify_snapshot_hash("self_state.initiative", 0.6, "growth_state.json", h1)
    assert not verify_snapshot_hash("self_state.initiative", 0.5, "other.json", h1)
    # captured_at 不参与 hash（同状态重冻结 hash 稳定）
    s1 = capture_snapshots(["p"], _reader(**{"p": 0.3}))[0]
    s2 = capture_snapshots(["p"], _reader(**{"p": 0.3}))[0]
    assert s1["hash"] == s2["hash"], "时间不同但状态相同 → hash 相同"


# ---------- 4. path 不匹配拒绝（Validator 规则 8 逐条断言） ----------
def test_path_mismatch_rejected():
    snap = capture_snapshots(["self_state.initiative"], _reader(**{"self_state.initiative": 0.5}))[0]
    proposal = {
        "schema_version": 1, "proposal_id": "prop_x",
        "evidence_trace_ids": ["exp_aaa111"],
        "evaluator_meta": {"pattern_detected": "p1", "pattern_frequency": 2, "used_llm": False},
        "before_snapshot": [snap],
        "proposed_changes": [{"path": "self_state.OTHER", "after": 0.4}],  # 错配
    }
    assert any("不在 before_snapshot" in e for e in validate_proposal(proposal))


# ---------- 5. MC 事件成长链不受影响 ----------
def test_mc_chain_compatible():
    """MC 事件 → 记忆 → snapshot 冻结（source 标注 growth_state 或 personality_state）不受影响。"""
    from src.audit.mc_event_normalizer import normalize_mc_event
    rec, err = normalize_mc_event("join", {"player": "K"}, "2026-08-27T12:00:00")
    assert err is None
    # MC 经历触发的提案同样必须带快照（同池同权）
    snaps = capture_snapshots(["self_state.initiative"], _reader(**{"self_state.initiative": 0.5}))
    assert snaps is not None
    proposal = {
        "schema_version": 1, "proposal_id": "prop_mc",
        "evidence_trace_ids": ["exp_mc001"],
        "evaluator_meta": {"pattern_detected": "join_pattern", "pattern_frequency": 3,
                           "used_llm": False},
        "before_snapshot": snaps,
        "proposed_changes": [{"path": "self_state.initiative", "after": 0.4}],
    }
    assert validate_proposal(proposal) == [], "MC 链提案带快照即通过"


# ---------- 6. 旧数据兼容 ----------
def test_legacy_compatibility(tmp_path):
    # SelfHistory 旧记录（无新字段）可读
    store = SelfHistoryTimelineStore(path=str(tmp_path / "sh.jsonl"))
    # 写入一条"旧格式"记录（模拟历史数据）
    import json as _json
    with open(str(tmp_path / "sh.jsonl"), "a", encoding="utf-8") as f:
        f.write(_json.dumps({"record_id": "sh_old", "version": 1, "applied_at": "t",
                             "before": {"x": 0.1}, "after": {"x": 0.2},
                             "proposal_id": "prop_old", "approval_id": "",
                             "evidence_ids": [], "reason": ""}) + "\n")
    rec = store.get_by_proposal("prop_old")
    assert rec is not None
    assert rec.get("before_snapshot", []) == [], "旧记录无新字段 → 默认空（兼容）"
    assert rec.get("schema_version", None) is None or rec.get("schema_version") == 1
    # 新记录追加新字段
    rid = store.record_evolution(version=1, before={"x": 0.2}, after={"x": 0.3},
                                 proposal_id="prop_new",
                                 before_snapshot=[{"path": "x", "old_value": 0.2}],
                                 experience_trace_ids=["exp_001"])
    assert rid is not None
    rec2 = store.get_by_proposal("prop_new")
    assert rec2["before_snapshot"] and rec2["experience_trace_ids"] == ["exp_001"]


# ---------- pipeline 接入：生成失败走 fail-closed ----------
def test_pipeline_capture_integration(monkeypatch):
    """_build_proposal_from_evaluated 在状态不可证明时返回 None（生成失败）。"""
    from src.growth.pipeline import GrowthPipeline
    gp = GrowthPipeline.__new__(GrowthPipeline)
    gp.growth_state = None  # 无状态源 → reader 全 None → capture 失败
    evaluated = {"target_candidates": ["self_state.initiative"], "applied_delta": 0.1,
                 "confidence": 0.6, "growth_signal": "s"}
    event = {"event_id": "ev_1", "evidence": [{"id": "exp_1"}]}
    assert gp._build_proposal_from_evaluated(evaluated, event) is None, \
        "状态不可证明 → 提案生成失败（fail-closed）"


def test_pipeline_capture_success(monkeypatch):
    """状态可读时生成提案并携带 before_snapshot。"""
    from src.growth.pipeline import GrowthPipeline
    gp = GrowthPipeline.__new__(GrowthPipeline)
    gp.growth_state = None  # 用 monkeypatch 覆盖 reader 提供状态
    monkeypatch.setattr(gp, "_snapshot_state_reader",
                        lambda path: {"value": 0.5, "source": "growth_state.json"})
    evaluated = {"target_candidates": ["self_state.initiative"], "applied_delta": 0.1,
                 "confidence": 0.6, "growth_signal": "s"}
    event = {"event_id": "ev_1", "evidence": [{"id": "exp_1"}]}
    prop = gp._build_proposal_from_evaluated(evaluated, event)
    assert prop is not None
    assert prop.before_snapshot and prop.before_snapshot[0]["old_value"] == 0.5
    assert prop.proposed_changes[0].before == 0.5, "change.before 由快照冻结注入"
