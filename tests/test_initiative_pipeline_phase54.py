# -*- coding: utf-8 -*-
"""v1.3 Phase 5.4: Initiative Controlled Pipeline 测试(测试先行)。

任务书 Test A-F:
A. off 模式: GoalState 不读取, 无 Candidate/Proposal/Action
B. shadow 模式: active Goal → Candidate → PENDING Proposal; 无 Action; 不调 sender
C. active 模式: APPROVED Proposal → Drain 生成 Action → SafetyFilter → Dispatcher 收到
D. 安全失败: permission deny / confidence / duplicate / cooldown 均阻断执行
E. 全链路追溯: goal_id → candidate_id → proposal_id → action_id
F. 人格隔离: identity_core/personality/self_model/emotion/relationship hash 一致

红线: 不接 InitiativeEngine / 不修改人格域 / 不默认开启主动行为。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_state import GOAL_STATUS, GoalStateStore
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


class _SpyDispatcher:
    def __init__(self):
        self.dispatched = []

    def dispatch(self, action):
        self.dispatched.append(action)
        return {"dispatched": True}


class _ExplodingGoalStore:
    def list_by_status(self, *args, **kwargs):
        raise AssertionError("GoalState 被读取了(off 模式应零触碰)")

    def list_all(self, *args, **kwargs):
        raise AssertionError("GoalState 被读取了(off 模式应零触碰)")


def _goal_store(tmp_path, records=()):
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    for _rec in records:
        _store.append_state(**_rec)
    return _store


def _goal_record(goal_id, confidence=0.7):
    return dict(
        goal_id=goal_id,
        status=GOAL_STATUS["ACTIVE"],
        description="关注用户的情绪状态变化",
        reason="goal: 关注用户的情绪状态变化",
        source_refs=[{"source_type": "experience", "source_id": "exp-1"}],
        priority="medium",
        confidence=confidence,
        proposal_id=f"gp-{goal_id}",
    )


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


def _make_pipeline(tmp_path, mode, *, pstore=None, gstore=None, dispatcher=None,
                   safety_filter=None, dispatch_enabled=False, target_user="366648462"):
    from src.initiative.initiative_pipeline import InitiativePipeline

    return InitiativePipeline(
        mode=mode,
        goal_store=gstore if gstore is not None else _goal_store(tmp_path),
        proposal_storage=pstore if pstore is not None else _FakeProposalStorage(),
        dispatcher=dispatcher,
        safety_filter=safety_filter,
        dispatch_enabled=dispatch_enabled,
        target_user=target_user,
    )


# ============================================================
# Test A: off 模式零触碰
# ============================================================
def test_off_mode_zero_touch(tmp_path):
    _pipeline = _make_pipeline(
        tmp_path, "off", gstore=_ExplodingGoalStore(),
    )
    _metrics = _pipeline.run_once()

    assert _metrics["mode"] == "off"
    assert _metrics["ran"] is False
    assert _metrics["candidates"] == []
    assert _metrics["proposals_created"] == []
    assert _metrics["actions"] == []
    assert _metrics["blocked"] == []


# ============================================================
# Test B: shadow 模式 — Candidate + PENDING Proposal, 无 Action
# ============================================================
def test_shadow_mode_produces_candidates_and_pending_proposals(
    tmp_path, patched_storage, audit_path,
):
    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _spy = _SpyDispatcher()
    _pipeline = _make_pipeline(
        tmp_path, "shadow", pstore=_pstore,
        gstore=_goal_store(tmp_path, records=[_goal_record("g-sh1")]),
        dispatcher=_spy, dispatch_enabled=True,  # 即使 dispatch 开关开, shadow 也不 drain
    )

    _metrics = _pipeline.run_once()

    assert _metrics["ran"] is True
    assert len(_metrics["candidates"]) == 1
    assert len(_metrics["proposals_created"]) == 1
    _pid = _metrics["proposals_created"][0]["proposal_id"]
    _proposal = _pstore.get(_pid)
    assert _proposal.status == PROPOSAL_STATUS["PENDING"]
    assert _proposal.proposal_type == PROPOSAL_TYPE["INITIATIVE"]
    # shadow: 不执行 Drain → 无 Action → dispatcher 零调用
    assert _metrics["actions"] == []
    assert _spy.dispatched == []

    # 重复运行: 同 goal 不重复提案(goal_reference 去重)
    _m2 = _pipeline.run_once()
    assert _m2["proposals_created"] == []
    assert _m2["candidates"]  # 候选仍产生(观察数据), 但不再桥接


# ============================================================
# Test C: active 模式 — Drain → Action → SafetyFilter → Dispatcher
# ============================================================
def test_active_mode_full_chain(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_drain import build_initiative_proposal

    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _spy = _SpyDispatcher()
    _pipeline = _make_pipeline(
        tmp_path, "active", pstore=_pstore,
        gstore=_goal_store(tmp_path, records=[_goal_record("g-ac1")]),
        dispatcher=_spy,
        safety_filter=ActionSafetyFilter(enabled=True, cooldown_seconds=0.0),
        dispatch_enabled=True,
    )

    # 第一轮: Candidate → PENDING 提案(drain 无可消费项)
    _m1 = _pipeline.run_once()
    assert len(_m1["proposals_created"]) == 1
    _pid = _m1["proposals_created"][0]["proposal_id"]
    assert _spy.dispatched == []

    # Admin Review(外部审批, 不自动批准)
    _proposal = _pstore.get(_pid)
    _proposal.status = PROPOSAL_STATUS["APPROVED"]
    _proposal.reviewer_id = "admin-test"
    _proposal.reviewed_at = "2026-08-23T00:00:00+00:00"

    # 第二轮: Drain 生成 Action → SafetyFilter 通过 → Dispatcher 收到
    _m2 = _pipeline.run_once()
    assert _m2["proposals_created"] == []  # 去重
    assert len(_m2["actions"]) == 1
    _action_entry = _m2["actions"][0]
    assert _action_entry["dispatched"] is True
    assert len(_spy.dispatched) == 1
    _action = _spy.dispatched[0]
    assert _action.payload["proposal_id"] == _pid
    assert _action.payload["goal_reference"] == "g-ac1"


# ============================================================
# Test D: 安全失败均阻断执行
# ============================================================
def test_safety_failures_block_execution(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_drain import build_initiative_proposal
    from src.runtime.action_dispatcher import Action

    _target = "366648462"

    def _approved(pid, meta_conf=0.8, spec=None):
        _p = build_initiative_proposal(
            initiative_id=f"ini-{pid}",
            goal_reference=f"g-{pid}",
            source_refs=[{"source_type": "memory", "source_id": f"mem-{pid}"}],
            action_spec=spec
            or {
                "action_type": "send_message",
                "message": f"msg-{pid}",
                "target_user": _target,
                "user_preference": "allow",
            },
            confidence=meta_conf,
            proposal_id=pid,
        )
        _p.status = PROPOSAL_STATUS["APPROVED"]
        _p.reviewer_id = "admin-test"
        _p.reviewed_at = "2026-08-23T00:00:00+00:00"
        return _p

    # p-deny: 用户拒绝; p-conf: 低置信; p-cool: 冷却(先放行一个); p-dup: 重复
    _pstore = _FakeProposalStorage([
        _approved("p-deny", spec={
            "action_type": "send_message", "message": "d", "target_user": _target,
            "user_preference": "deny",
        }),
        _approved("p-conf", meta_conf=0.3),
        _approved("p-cool1"),
        _approved("p-cool2"),
        _approved("p-dup"),
    ])
    patched_storage(_pstore)
    _spy = _SpyDispatcher()
    _safety = ActionSafetyFilter(enabled=True, cooldown_seconds=60.0)

    # 预置 duplicate 指纹: 同 proposal_id 已通过一次(模拟前序执行)
    # 注意 target_user 用他人, 避免污染本测试目标的冷却表
    _pre = Action(
        action_id="act-pre",
        action_type="send_message",
        payload={
            "proposal_id": "p-dup", "goal_reference": "g-p-dup",
            "initiative_id": "ini-p-dup", "message": "msg-p-dup",
            "confidence": 0.8, "user_preference": "allow", "target_user": "other-user",
        },
    )
    _safety.check(_pre)

    _pipeline = _make_pipeline(
        tmp_path, "active", pstore=_pstore,
        gstore=_goal_store(tmp_path),  # 无 active goal → 无新提案
        dispatcher=_spy, safety_filter=_safety, dispatch_enabled=True,
        target_user=_target,
    )
    _metrics = _pipeline.run_once()

    # 仅 p-cool1 放行并 dispatch; 其余 4 个阻断
    assert len(_metrics["actions"]) == 1
    assert _metrics["actions"][0]["dispatched"] is True
    assert len(_metrics["blocked"]) == 4
    _blocked_reasons = "|".join(b["reasons"][0] for b in _metrics["blocked"])
    assert "permission" in _blocked_reasons  # deny
    assert "confidence" in _blocked_reasons  # 低置信
    assert "cooldown" in _blocked_reasons  # 冷却
    assert "duplicate" in _blocked_reasons  # 重复
    assert len(_spy.dispatched) == 1


# ============================================================
# Test E: 全链路追溯
# ============================================================
def test_full_chain_traceability(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter

    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _pipeline = _make_pipeline(
        tmp_path, "active", pstore=_pstore,
        gstore=_goal_store(tmp_path, records=[_goal_record("g-trace")]),
        dispatcher=_SpyDispatcher(),
        safety_filter=ActionSafetyFilter(enabled=True, cooldown_seconds=0.0),
        dispatch_enabled=True,
    )

    _m1 = _pipeline.run_once()
    _candidate = _m1["candidates"][0]
    _proposal_entry = _m1["proposals_created"][0]
    _pid = _proposal_entry["proposal_id"]
    _proposal = _pstore.get(_pid)

    # goal_id → candidate.goal_reference → proposal metadata
    assert _candidate["goal_reference"] == "g-trace"
    assert _proposal.metadata["goal_reference"] == "g-trace"
    assert _proposal.metadata["candidate_id"] == _candidate["id"]

    # 审批后第二轮: proposal_id → action_id
    _proposal.status = PROPOSAL_STATUS["APPROVED"]
    _proposal.reviewer_id = "admin-test"
    _proposal.reviewed_at = "2026-08-23T00:00:00+00:00"
    _m2 = _pipeline.run_once()
    _action_entry = _m2["actions"][0]
    assert _action_entry["proposal_id"] == _pid
    assert _action_entry["action_id"]
    # 全链路可查询: goal 记录 ← candidate ← proposal ← action
    assert _proposal.metadata["initiative_id"] == _candidate["id"]


# ============================================================
# Test F: 人格隔离
# ============================================================
def test_personality_isolation(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter

    _sentinels = {
        "personality_state.json": {"version": "p54-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p54-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p54-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p54-sentinel", "trust": 0.5},
    }
    _data_dir = tmp_path / "data"
    _data_dir.mkdir()
    for _name, _payload in _sentinels.items():
        (_data_dir / _name).write_text(
            json.dumps(_payload, ensure_ascii=False), encoding="utf-8"
        )
    _identity_file = Path(_REPO_ROOT) / "src" / "personality" / "identity_core.py"
    _snap = {_k: _hash_bytes((_data_dir / _k).read_bytes()) for _k in _sentinels}
    _snap["identity_core"] = _hash_bytes(_identity_file.read_bytes())

    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _pipeline = _make_pipeline(
        tmp_path, "active", pstore=_pstore,
        gstore=_goal_store(tmp_path, records=[_goal_record("g-iso")]),
        dispatcher=_SpyDispatcher(),
        safety_filter=ActionSafetyFilter(enabled=True, cooldown_seconds=0.0),
        dispatch_enabled=True,
    )
    _pipeline.run_once()

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# 静态红线 + Python 3.11
# ============================================================
def test_pipeline_module_static_red_lines():
    _text = Path(
        _REPO_ROOT, "src/initiative/initiative_pipeline.py"
    ).read_text(encoding="utf-8")
    for _tok in ("initiative_sender", "initiative_bridge", "send_private_msg",
                 "initiative_engine", "InitiativeEngine"):
        assert _tok not in _text, f"pipeline 禁止引用: {_tok}"
    _tree = ast.parse(_text)
    for _node in ast.walk(_tree):
        _module = ""
        if isinstance(_node, ast.ImportFrom):
            _module = _node.module or ""
        elif isinstance(_node, ast.Import):
            _module = " ".join(a.name or "" for a in _node.names)
        assert "llm" not in _module.lower(), f"pipeline 禁止 LLM: {_module}"


_P54_MODULE_FILES = (
    "src/initiative/initiative_pipeline.py",
    "src/initiative/initiative_candidate.py",
    "src/initiative/initiative_drain.py",
    "src/initiative/action_safety.py",
    "src/initiative/__init__.py",
    "src/runtime/integration/runtime_integration_host.py",
    "src/runtime/runtime_core.py",
)


def test_py_compile_pipeline_modules():
    for _rel in _P54_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_pipeline_modules_python311_grammar():
    for _rel in _P54_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
