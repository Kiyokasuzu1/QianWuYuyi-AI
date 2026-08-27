# -*- coding: utf-8 -*-
"""T1-C Growth Audit 测试。

覆盖（主架构师审核要求）：
- 生命周期完整轨迹（generated→approved→execution_started→applied）
- applied 必须经 execution_started（fail-closed：无执行轨迹不得 applied）
- audit 旁路：审计写失败不影响真相源（fail-soft）
- append-only：不修改历史
- 双写一致（events + audit_log）
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.growth.growth_audit import make_event, record  # noqa: E402
from src.growth.proposal_ledger import reduce_events  # noqa: E402


@pytest.fixture()
def paths(tmp_path):
    ev = str(tmp_path / "proposal_events.jsonl")
    au = str(tmp_path / "audit_log.jsonl")
    return ev, au


def _chain(pid="prop_x"):
    return [make_event("proposal_generated", pid),
            make_event("proposal_approved", pid),
            make_event("growth_execution_started", pid),
            make_event("proposal_applied", pid)]


# ---------- 完整生命周期轨迹 ----------
def test_full_lifecycle_with_execution(paths):
    ev_path, au_path = paths
    for e in _chain():
        assert record(e, ev_path, au_path) is True
    events = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    assert reduce_events(events) == {"prop_x": "applied"}
    # audit 双写存在且按序
    lines = [l for l in open(au_path, encoding="utf-8") if l.strip()]
    actions = [json.loads(l)["action"] for l in lines]
    assert actions == ["growth_proposal:proposal_generated",
                       "growth_proposal:proposal_approved",
                       "growth_proposal:growth_execution_started",
                       "growth_proposal:proposal_applied"]


# ---------- applied 必须经 execution_started（fail-closed） ----------
def test_applied_requires_execution_trace(paths):
    ev_path, au_path = paths
    for e in _chain()[0:2]:  # generated + approved，无 execution_started
        record(e, ev_path, au_path)
    events = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    events.append(make_event("proposal_applied", "prop_x"))
    state = reduce_events(events)
    assert state.get("prop_x") == "approved", "无 execution_started 不得 applied（保持 approved）"


# ---------- audit 旁路：写失败不影响真相源 ----------
def test_audit_is_bypass_fail_soft(paths, monkeypatch):
    ev_path, au_path = paths
    from src.growth import growth_audit

    def fake_append(path, ev):
        if path == ev_path:  # 真相源真实写入
            with open(ev_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            return True
        return False  # 审计路径模拟失败（旁路）

    monkeypatch.setattr(growth_audit, "_append", fake_append)
    assert record(make_event("proposal_approved", "p1"), ev_path, au_path) is True
    events = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    assert len(events) == 1, "审计失败不影响真相源写入"


# ---------- 真相源写失败 → record 返回 False（调用方可阻断） ----------
def test_source_write_failure_signals(paths, monkeypatch):
    ev_path, au_path = paths
    from src.growth import growth_audit
    monkeypatch.setattr(growth_audit, "_append", lambda path, ev: False)
    assert record(make_event("proposal_rejected", "p1"), ev_path, au_path) is False


# ---------- append-only：不修改历史 ----------
def test_append_only_never_rewrites(paths):
    ev_path, au_path = paths
    e1 = make_event("proposal_approved", "p1", reason="r1")
    record(e1, ev_path, au_path)
    e2 = make_event("proposal_rejected", "p1", reason="r2")  # 终态后事件
    record(e2, ev_path, au_path)
    lines = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    assert lines[0]["reason"] == "r1", "历史事件不可变"
    assert len(lines) == 2


# ---------- 状态源唯一性：audit 不拥有状态 ----------
def test_audit_does_not_own_state(paths):
    ev_path, au_path = paths
    for e in _chain():
        record(e, ev_path, au_path)
    # 即使 audit 文件被删除，真相源仍可推导状态
    os.remove(au_path)
    events = [json.loads(l) for l in open(ev_path, encoding="utf-8") if l.strip()]
    assert reduce_events(events) == {"prop_x": "applied"}
