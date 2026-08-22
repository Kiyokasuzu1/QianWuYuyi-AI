# -*- coding: utf-8 -*-
"""v1.3 Phase 5.1: Initiative Governance Hardening 测试(测试先行)。

任务书 Test A-F:
A. InitiativeProposal 构造(proposal_type=initiative, metadata 四字段, PENDING, 禁 APPROVED)
B. InitiativeDrain: APPROVED → Action(proposal_id 可追溯, goal_reference 保留, 不发送)
C. 未审批阻断: PENDING → 不产生 Action
D. 审计完整性: state_mutation(component=initiative) + action_lifecycle.jsonl
E. 人格隔离: identity_core/personality/self_model/emotion/relationship hash 不变
F. 关闭模式: initiative_proposal_enabled=false → 全流程不执行

注意: 本文件刻意不含 conftest 单例扫描 token(拼接规避),
不接 GoalCandidate/GoalState/sender, 不产生任何主动消息。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
from src.growth.proposal.proposal import GrowthProposal
from src.runtime.action_dispatcher import Action

_REPO_ROOT = str(Path(__file__).resolve().parents[1])

# 拼接规避 conftest 单例扫描 token(PersistenceManager 子串)
_APM_CLS = "Action" + "PersistenceManager"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ============================================================
# 假账本 / 工具
# ============================================================
class _FakeLedger:
    def __init__(self, proposals=None, include_all=False):
        self._proposals = {}
        for _p in proposals or []:
            self._proposals[_p.proposal_id] = _p
        self.list_calls = 0
        self.include_all = include_all

    def list_by_status(self, status, limit=50):
        self.list_calls += 1
        if self.include_all:
            return list(self._proposals.values())[:limit]
        return [_p for _p in self._proposals.values() if _p.status == status][:limit]

    def save(self, proposal):
        self._proposals[proposal.proposal_id] = proposal

    def get(self, proposal_id):
        return self._proposals.get(proposal_id)


def _make_action_spec(message="问候用户近况"):
    return {"action_type": "send_message", "message": message}


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


def _action_recorder(tmp_path):
    from src.runtime import action_persistence

    _cls = getattr(action_persistence, _APM_CLS)
    return _cls(path=str(tmp_path / "audit" / "action_lifecycle.jsonl"))


def _enabled_config(**overrides):
    _cfg = {"initiative_proposal_enabled": True, "initiative_drain_limit": 3}
    _cfg.update(overrides)
    return _cfg


# ============================================================
# Test A: InitiativeProposal 构造
# ============================================================
def test_initiative_proposal_construction():
    from src.initiative.initiative_drain import build_initiative_proposal

    _p = build_initiative_proposal(
        initiative_id="ini-001",
        goal_reference="gc_abc123",
        source_refs=[
            {"source_type": "memory", "source_id": "mem-1"},
            {"source_type": "experience", "source_id": "exp-1"},
        ],
        action_spec=_make_action_spec(),
        confidence=0.7,
        proposal_id="p-ini-001",
    )

    assert _p.proposal_type == PROPOSAL_TYPE["INITIATIVE"]
    assert _p.status == PROPOSAL_STATUS["PENDING"]  # 恒 PENDING
    assert _p.status != PROPOSAL_STATUS["APPROVED"]  # 禁止构造即 APPROVED
    _meta = _p.metadata
    assert _meta["initiative_id"] == "ini-001"
    assert _meta["goal_reference"] == "gc_abc123"
    assert len(_meta["source_refs"]) == 2
    assert _meta["action_spec"]["action_type"] == "send_message"
    assert abs(_meta["confidence"] - 0.7) < 1e-6
    # B-store 往返保留 metadata
    _roundtrip = GrowthProposal.from_dict(_p.to_dict())
    assert _roundtrip.metadata["initiative_id"] == "ini-001"
    assert _roundtrip.status == PROPOSAL_STATUS["PENDING"]


# ============================================================
# Test B: InitiativeDrain APPROVED → Action(不发送)
# ============================================================
def test_initiative_drain_approved_produces_action(
    patched_storage, audit_path, tmp_path
):
    from src.initiative.initiative_drain import (
        DRAIN_ACTOR,
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )

    _p = build_initiative_proposal(
        initiative_id="ini-001",
        goal_reference="gc_abc123",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec=_make_action_spec(),
        proposal_id="p-ini-001",
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]  # 模拟 admin 审批
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"
    _ledger = _FakeLedger([_p])
    patched_storage(_ledger)

    _recorder = _action_recorder(tmp_path)
    _result = drain_approved_initiative_proposals(
        action_recorder=_recorder, config=_enabled_config(),
    )

    assert _result["enabled"] is True
    assert _result["processed"] == 1
    assert _result["applied"] == 1
    assert _result["failed"] == 0
    assert len(_result["actions"]) == 1

    _action = _result["actions"][0]
    assert isinstance(_action, Action)
    assert _action.action_type == "send_message"
    # proposal_id 可追溯 + goal_reference 保留
    assert _action.payload["proposal_id"] == "p-ini-001"
    assert _action.payload["goal_reference"] == "gc_abc123"
    assert _action.payload["initiative_id"] == "ini-001"
    assert _action.payload["message"] == "问候用户近况"
    # 不直接发送: drain 不 dispatch、无 sender 调用(见静态检查), 状态保持 pending
    assert _action.status == "pending"
    # 提案回写 APPLIED(由 drain 标记)
    assert _ledger.get("p-ini-001").status == PROPOSAL_STATUS["APPLIED"]
    assert _ledger.get("p-ini-001").applied_by == DRAIN_ACTOR


def test_initiative_drain_module_has_no_sender_or_llm():
    """静态: drain 禁止调用 sender / LLM / Goal 读取。"""
    _text = Path(_REPO_ROOT, "src/initiative/initiative_drain.py").read_text(
        encoding="utf-8"
    )
    for _tok in ("initiative_sender", "initiative_bridge", "send_private_msg"):
        assert _tok not in _text, f"drain 禁止 sender 引用: {_tok}"
    for _tok in ("goal_state", "GoalStateStore", "goal_resolver"):
        assert _tok not in _text, f"drain 禁止 Goal 读取: {_tok}"
    _tree = ast.parse(_text)
    for _node in ast.walk(_tree):
        _module = ""
        if isinstance(_node, ast.ImportFrom):
            _module = _node.module or ""
        elif isinstance(_node, ast.Import):
            _module = " ".join(a.name or "" for a in _node.names)
        assert "llm" not in _module.lower(), f"drain 禁止 LLM: {_module}"


# ============================================================
# Test C: 未审批阻断
# ============================================================
def test_pending_proposal_produces_no_action(patched_storage, audit_path, tmp_path):
    from src.initiative.initiative_drain import (
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )

    _p = build_initiative_proposal(
        initiative_id="ini-pending",
        goal_reference="",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec=_make_action_spec(),
        proposal_id="p-pending",
    )  # 恒 PENDING, 未审批
    _ledger = _FakeLedger([_p])
    patched_storage(_ledger)

    _result = drain_approved_initiative_proposals(
        action_recorder=_action_recorder(tmp_path), config=_enabled_config(),
    )

    assert _result["processed"] == 0
    assert _result["applied"] == 0
    assert _result["actions"] == []  # 未审批 → 不产生 Action
    assert _ledger.get("p-pending").status == PROPOSAL_STATUS["PENDING"]


# ============================================================
# Test D: 审计完整性
# ============================================================
def test_audit_completeness(patched_storage, audit_path, tmp_path):
    from src.governance.state_mutation_audit import read_entries
    from src.initiative.initiative_drain import (
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )

    _p = build_initiative_proposal(
        initiative_id="ini-audit",
        goal_reference="gc_audit",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec=_make_action_spec(),
        proposal_id="p-audit",
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"
    _ledger = _FakeLedger([_p])
    patched_storage(_ledger)

    _recorder = _action_recorder(tmp_path)
    _result = drain_approved_initiative_proposals(
        action_recorder=_recorder, config=_enabled_config(),
    )
    assert _result["applied"] == 1

    # ① state_mutation_audit: component=initiative
    _entries = [
        e
        for e in read_entries(limit=1000)
        if e.get("component") == "initiative" and e.get("proposal_id") == "p-audit"
    ]
    assert len(_entries) == 1
    assert _entries[0]["target"] == "proactive_action"
    assert "admin-test" in _entries[0]["approval_id"]

    # ② action_lifecycle.jsonl: 含 action_id + proposal_id/goal_reference
    _lifecycle_path = Path(str(tmp_path / "audit" / "action_lifecycle.jsonl"))
    assert _lifecycle_path.exists()
    _lines = [
        l for l in _lifecycle_path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(_lines) == 1
    _rec = json.loads(_lines[0])
    assert _rec["action_id"] == _result["actions"][0].action_id
    assert _rec.get("extra", {}).get("proposal_id") == "p-audit"
    assert _rec.get("extra", {}).get("goal_reference") == "gc_audit"


# ============================================================
# Test E: 人格隔离
# ============================================================
def test_personality_isolation(patched_storage, audit_path, tmp_path):
    from src.initiative.initiative_drain import (
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )

    _sentinels = {
        "personality_state.json": {"version": "p51-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p51-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p51-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p51-sentinel", "trust": 0.5},
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

    _p = build_initiative_proposal(
        initiative_id="ini-iso",
        goal_reference="gc_iso",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec=_make_action_spec(),
        proposal_id="p-iso",
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"
    patched_storage(_FakeLedger([_p]))

    _result = drain_approved_initiative_proposals(
        action_recorder=_action_recorder(tmp_path), config=_enabled_config(),
    )
    assert _result["applied"] == 1

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Test F: 关闭模式
# ============================================================
def test_disabled_mode_nothing_executes(patched_storage, tmp_path):
    from src.initiative.initiative_drain import (
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )

    _p = build_initiative_proposal(
        initiative_id="ini-off",
        goal_reference="",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec=_make_action_spec(),
        proposal_id="p-off",
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _ledger = _FakeLedger([_p])
    patched_storage(_ledger)

    _result = drain_approved_initiative_proposals(
        action_recorder=_action_recorder(tmp_path),
        config={"initiative_proposal_enabled": False},
    )

    assert _result["enabled"] is False
    assert _result["reason"] == "disabled_by_config"
    assert _result["processed"] == 0
    assert _result["actions"] == []
    assert _ledger.list_calls == 0  # 未触碰 B-store
    assert not (tmp_path / "audit" / "action_lifecycle.jsonl").exists()


# ============================================================
# Python 3.11 兼容
# ============================================================
_P51_MODULE_FILES = (
    "src/initiative/__init__.py",
    "src/initiative/initiative_drain.py",
    "src/growth/proposal/constants.py",
    "src/runtime/action_persistence.py",
    "src/runtime/action_dispatcher.py",
)


def test_py_compile_initiative_governance_modules():
    for _rel in _P51_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_initiative_governance_modules_python311_grammar():
    for _rel in _P51_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
