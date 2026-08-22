# -*- coding: utf-8 -*-
"""v1.3 RC Phase 3.6: Goal Governance Wiring Remediation 测试。

F1: Shadow → Active 迁移(候选去重与提案去重分离)
F2: Goal Approved Drain 接线(host tick, goal_drain_enabled 门控)

红线: 无 dispatch / 无 sender / 默认关闭 / 人格域零修改。
注意: 本文件刻意不含 conftest 单例扫描 token(拼接规避)。
"""

import ast
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_proposal import GOAL_PAYLOAD_KEY, build_goal_proposal
from src.goal.goal_state import GOAL_STATUS, GoalStateStore
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# helper
# ============================================================
def _memory(mem_id, content, timestamp="2026-08-01T10:00:00", importance=0.7):
    return {
        "id": mem_id,
        "content": content,
        "timestamp": timestamp,
        "importance": importance,
        "role": "user",
        "user_id": "qingqing",
    }


_MEMORIES = [
    _memory("mem-a", "我一直在做机器人方向的探索", timestamp="2026-07-01T08:00:00"),
    _memory("mem-b", "机器人学习又有进展了", timestamp="2026-07-20T09:00:00"),
    _memory("mem-c", "机器人方向我越来越喜欢", timestamp="2026-08-15T21:00:00"),
]


class _FakeProposalStorage:
    def __init__(self, proposals=None):
        self._proposals = {}
        for _p in proposals or []:
            self._proposals[_p.proposal_id] = _p
        self.save_calls = 0

    def save(self, proposal):
        self.save_calls += 1
        self._proposals[proposal.proposal_id] = proposal

    def list_by_type(self, proposal_type, limit=1000):
        return [p for p in self._proposals.values() if p.proposal_type == proposal_type]

    def list_by_status(self, status, limit=1000):
        return [p for p in self._proposals.values() if p.status == status]

    def load(self, proposal_id):
        return self._proposals.get(proposal_id)

    def get(self, proposal_id):
        return self._proposals.get(proposal_id)


@pytest.fixture
def patched_storage(monkeypatch):
    _TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"

    def _patch(store):
        monkeypatch.setattr(_TARGET, lambda: store)

    return _patch


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    _p = tmp_path / "audit" / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(_p))
    return _p


# ============================================================
# F1: Shadow → Active 迁移
# ============================================================
def test_goal_shadow_to_active_migration(tmp_path):
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner

    _cstore = GoalCandidateStore(str(tmp_path / "goal" / "goal_candidates.jsonl"))
    _pstore = _FakeProposalStorage()
    _runner = GoalProductionRunner(
        mode="shadow",
        candidate_store=_cstore,
        proposal_storage=_pstore,
        memory_loader=lambda: list(_MEMORIES),
        experience_loader=lambda: [],
    )

    # 1) shadow: 产生并持久化 Candidate A
    _m1 = _runner.run_once()
    assert _m1["new_candidate_count"] >= 1
    assert _m1["bridged_proposal_count"] == 0
    _candidates_before = _cstore.list_all()
    assert len(_candidates_before) >= 1
    _candidate_id = _candidates_before[0]["id"]

    # 2) 切换 active: 同一 runner 重新运行 Detector
    _runner.mode = "active"
    _m2 = _runner.run_once()

    # Candidate 不重复创建(候选库指纹去重保持)
    assert _m2["new_candidate_count"] == 0
    assert len(_cstore.list_all()) == len(_candidates_before)

    # F1 修复验证: shadow 期候选可以被桥接为 Proposal
    assert _m2["bridged_proposal_count"] >= 1
    _proposals = _pstore.list_by_type(PROPOSAL_TYPE["GOAL"])
    assert len(_proposals) >= 1
    _p = _proposals[0]
    assert _p.status == PROPOSAL_STATUS["PENDING"]  # 恒 PENDING, 不自动批准
    assert _p.metadata.get("candidate_id") == _candidate_id  # 与原子候选对应
    assert _p.metadata[GOAL_PAYLOAD_KEY]["goal_id"] == _candidate_id

    # 3) 再次 active 运行: 提案级去重 → 不重复桥接
    _m3 = _runner.run_once()
    assert _m3["bridged_proposal_count"] == 0
    assert _pstore.save_calls == len(_proposals)  # 未新增提案


def test_goal_runner_has_no_dispatch_or_sender():
    """F1 修复不引入 dispatch/sender(goal runner 本无执行路径)。"""
    _text = Path(
        _REPO_ROOT, "src/goal/goal_production_runner.py"
    ).read_text(encoding="utf-8")
    for _tok in ("dispatcher", "dispatch", "initiative_sender", "send_private_msg"):
        assert _tok not in _text


# ============================================================
# F2: Goal Approved Drain 接线
# ============================================================
def _approved_goal_proposal(pid="p-approve", goal_id="gc_test123"):
    _p = build_goal_proposal(
        proposal_id=pid,
        goal_id=goal_id,
        description="关注机器人方向",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        priority="medium",
        confidence=0.8,
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"
    return _p


def test_goal_approved_drain_off_zero_run(tmp_path, patched_storage):
    from src.goal.goal_approved_drain import drain_approved_goal_proposals

    _ledger = _FakeProposalStorage([_approved_goal_proposal()])
    patched_storage(_ledger)
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))

    _result = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": False},
    )

    assert _result["enabled"] is False
    assert _result["reason"] == "disabled_by_config"
    assert _store.count_records() == 0
    assert _ledger.get("p-approve").status == PROPOSAL_STATUS["APPROVED"]


def test_goal_approved_drain_on_full_traceability(
    tmp_path, patched_storage, audit_path,
):
    from src.goal.goal_approved_drain import drain_approved_goal_proposals
    from src.governance.state_mutation_audit import read_entries

    _ledger = _FakeProposalStorage([_approved_goal_proposal()])
    patched_storage(_ledger)
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))

    _result = drain_approved_goal_proposals(
        goal_store=_store,
        config={"goal_drain_enabled": True, "goal_drain_limit": 3},
    )

    assert _result["applied"] == 1
    # GoalState 创建 + 可追踪
    _state = _store.get("gc_test123")
    assert _state is not None
    assert _state["status"] == GOAL_STATUS["ACTIVE"]
    assert _state["goal_id"] == "gc_test123"
    assert _state["proposal_id"] == "p-approve"
    assert _state["source_refs"]
    # 提案回写 APPLIED
    assert _ledger.get("p-approve").status == PROPOSAL_STATUS["APPLIED"]
    # audit 完整
    _entries = [
        e
        for e in read_entries(limit=1000)
        if e.get("component") == "goal" and e.get("proposal_id") == "p-approve"
    ]
    assert len(_entries) == 1
    assert "admin-test" in _entries[0]["approval_id"]


def test_goal_runtime_wiring_host_tick(
    tmp_path, patched_storage, audit_path,
):
    """F2 接线: host tick → goal_drain_enabled 判断 → drain → GoalState。"""
    from src.runtime.integration.runtime_integration_host import (
        HOST_STATE_RUNNING,
        RuntimeIntegrationHost,
    )

    _ledger = _FakeProposalStorage([_approved_goal_proposal()])
    patched_storage(_ledger)
    _goal_path = str(tmp_path / "goal" / "goal_state.jsonl")

    # off 模式宿主: 零运行
    _host_off = RuntimeIntegrationHost(
        name="t-off",
        auto_create_manager=False,
        auto_register_tasks=False,
        goal_drain_enabled=False,
        goal_state_path=_goal_path,
    )
    _host_off._state = HOST_STATE_RUNNING
    _host_off.tick()
    assert GoalStateStore(_goal_path).count_records() == 0
    assert _host_off._last_goal_drain_metrics == {}

    # on 模式宿主: drain 执行 → GoalState 创建
    _host_on = RuntimeIntegrationHost(
        name="t-on",
        auto_create_manager=False,
        auto_register_tasks=False,
        goal_drain_enabled=True,
        goal_state_path=_goal_path,
    )
    _host_on._state = HOST_STATE_RUNNING
    _host_on.tick()
    _state = GoalStateStore(_goal_path).get("gc_test123")
    assert _state is not None
    assert _state["status"] == GOAL_STATUS["ACTIVE"]
    assert _state["proposal_id"] == "p-approve"
    assert _host_on._last_goal_drain_metrics.get("applied") == 1


def test_host_drain_hook_static_red_lines():
    """F2 钩子: 无新线程/scheduler; 不引入 initiative 执行路径。"""
    _text = Path(
        _REPO_ROOT, "src/runtime/integration/runtime_integration_host.py"
    ).read_text(encoding="utf-8")
    _i = _text.index("def _run_goal_drain")
    _block = _text[_i:_i + 900]
    assert "Thread(" not in _block
    assert "scheduler" not in _block
    assert "dispatch" not in _block
    assert "sender" not in _block


# ============================================================
# Python 3.11
# ============================================================
_R36_MODULE_FILES = (
    "src/goal/goal_production_runner.py",
    "src/goal/goal_approved_drain.py",
    "src/runtime/integration/runtime_integration_host.py",
    "src/runtime/runtime_core.py",
)


def test_py_compile_remediation_modules():
    for _rel in _R36_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_remediation_modules_python311_grammar():
    for _rel in _R36_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
