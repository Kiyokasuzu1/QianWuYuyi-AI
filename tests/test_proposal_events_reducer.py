# -*- coding: utf-8 -*-
"""T0-A Proposal Events Reducer 测试。

覆盖：事件解析/状态机转移/投影/非法事件拒绝/只追加语义。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.proposal_ledger import (  # noqa: E402
    SCHEMA_VERSION, apply_event, parse_event, project, reduce_events,
)


def _ev(etype, pid, reason=""):
    e = {"schema_version": SCHEMA_VERSION, "event": etype, "proposal_id": pid,
         "timestamp": "2026-08-27T12:00:00", "reviewer": "admin"}
    if reason:
        e["reason"] = reason
    return e


# ---------- 解析 ----------
def test_parse_valid_and_invalid():
    assert parse_event(json.dumps(_ev("proposal_rejected", "p1")))["proposal_id"] == "p1"
    assert parse_event("not json") is None
    assert parse_event(json.dumps({"event": "unknown", "proposal_id": "x"})) is None
    assert parse_event(json.dumps({"event": "proposal_rejected"})) is None, "缺 proposal_id"
    bad = _ev("proposal_rejected", "p1")
    bad["schema_version"] = 99
    assert parse_event(json.dumps(bad)) is None, "不支持的 schema_version 拒绝"


# ---------- 状态机 ----------
def test_state_machine_transitions():
    assert apply_event("", _ev("proposal_generated", "p")) == "pending"
    assert apply_event("pending", _ev("proposal_generated", "p")) == "pending", "生成不覆盖既有状态"
    assert apply_event("pending", _ev("proposal_approved", "p")) == "approved"
    # 执行轨迹预留：approved → executing → applied（无 execution_started 不得 applied）
    assert apply_event("approved", _ev("growth_execution_started", "p")) == "executing"
    assert apply_event("executing", _ev("proposal_applied", "p")) == "applied"
    assert apply_event("approved", _ev("proposal_applied", "p")) == "approved", \
        "approved 不可直接 applied（须经 execution_started）"
    assert apply_event("pending", _ev("proposal_rejected", "p")) == "rejected"
    assert apply_event("approved", _ev("proposal_rejected", "p")) == "approved", "已批准不可被拒绝覆盖"
    assert apply_event("rejected", _ev("proposal_approved", "p")) == "rejected", "终态不可逆转"


# ---------- 投影 ----------
def test_project_with_initial_baseline():
    initial = {"p1": "pending", "p2": "pending"}
    events = [_ev("proposal_rejected", "p1", "test 残留"),
              _ev("proposal_rejected", "p2", "证据断裂")]
    out = project(initial, events)
    assert out == {"p1": "rejected", "p2": "rejected"}, "基线 + 事件 → 投影"
    # 其余提案不受影响；无基线提案收到 reject 保持无状态（fail-closed：状态机要求先 pending）
    out2 = project({"p3": "pending"}, [_ev("proposal_rejected", "p1")])
    assert out2 == {"p3": "pending"} and "p1" not in out2


def test_proposed_baseline_alias():
    """生产历史基线 status="proposed" 与 pending 同义（rejected/approved 均接受）。"""
    initial = {"p1": "proposed", "p2": "proposed"}
    events = [_ev("proposal_rejected", "p1"), _ev("proposal_approved", "p2")]
    out = project(initial, events)
    assert out == {"p1": "rejected", "p2": "approved"}, "proposed 基线可被 reject/approve 转移"
    # 但 applied 仍需先 approved（proposed 不能直接 applied）
    out2 = project({"p3": "proposed"}, [_ev("proposal_applied", "p3")])
    assert out2 == {"p3": "proposed"}


def test_reduce_events_pure():
    events = [_ev("proposal_generated", "a"), _ev("proposal_approved", "a")]
    assert reduce_events(events) == {"a": "approved"}
    assert reduce_events([]) == {}


# ---------- 只追加语义 ----------
def test_append_only_contract():
    """同一提案两条 reject 事件：第二条不改变终态（幂等），不覆盖。"""
    events = [_ev("proposal_rejected", "p1"), _ev("proposal_rejected", "p1")]
    assert project({"p1": "pending"}, events) == {"p1": "rejected"}


# ---------- 全生命周期 ----------
def test_lifecycle_generated_to_applied():
    events = [_ev("proposal_generated", "x"), _ev("proposal_approved", "x"),
              _ev("growth_execution_started", "x"), _ev("proposal_applied", "x")]
    assert reduce_events(events) == {"x": "applied"}
