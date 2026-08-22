# -*- coding: utf-8 -*-
"""v1.3 Phase 5.3: InitiativeCandidate 生成层测试(测试先行)。

任务书 Test A-H:
A. active goal → candidate
B. 无 active goal → zero candidate
C. archived/completed goal → ignored
D. candidate 不包含非法 action
E. 不调用 LLM
F. 人格隔离 hash
G. candidate 不产生 Proposal
H. py_compile Python 3.11

红线: 不发送消息 / 不接 ActionDispatcher/InitiativeBridge/InitiativeEngine /
不自动 APPROVED / 不绕过 Admin Review。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_state import GOAL_STATUS, GoalStateStore

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _goal_store(tmp_path, records=()):
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    for _rec in records:
        _store.append_state(**_rec)
    return _store


def _goal_record(goal_id, status=GOAL_STATUS["ACTIVE"], confidence=0.7):
    return dict(
        goal_id=goal_id,
        status=status,
        description="关注用户的情绪状态变化",
        reason="goal: 关注用户的情绪状态变化",
        source_refs=[
            {"source_type": "experience", "source_id": "exp-001"},
            {"source_type": "memory", "source_id": "mem-001"},
        ],
        priority="medium",
        confidence=confidence,
        proposal_id=f"p-{goal_id}",
    )


# ============================================================
# Test A: active goal → candidate
# ============================================================
def test_active_goal_produces_candidate(tmp_path):
    from src.initiative.initiative_candidate import generate_candidates

    _store = _goal_store(tmp_path, records=[_goal_record("g-a1")])
    _candidates = generate_candidates(
        goal_store=_store, target_user="366648462",
    )

    assert len(_candidates) == 1
    _c = _candidates[0]
    assert _c["id"]
    assert _c["goal_reference"] == "g-a1"
    assert _c["action_type"] == "send_message"
    assert _c["payload"]["target_user"] == "366648462"
    assert "message" in _c["payload"] and _c["payload"]["message"]
    assert _c["reason"]
    assert abs(_c["confidence"] - 0.7) < 1e-6
    assert len(_c["evidence_refs"]) == 2  # 来源随 candidate 传递
    assert _c["created_at"]
    assert _c["generator_version"]


# ============================================================
# Test B: 无 active goal → zero candidate
# ============================================================
def test_no_active_goal_zero_candidates(tmp_path):
    from src.initiative.initiative_candidate import generate_candidates

    _store = _goal_store(tmp_path)  # 空 store
    assert generate_candidates(goal_store=_store, target_user="366648462") == []


# ============================================================
# Test C: archived/completed goal → ignored
# ============================================================
def test_terminal_goals_ignored(tmp_path):
    from src.initiative.initiative_candidate import generate_candidates

    _store = _goal_store(
        tmp_path,
        records=[
            _goal_record("g-done", status=GOAL_STATUS["COMPLETED"]),
            _goal_record("g-arch", status=GOAL_STATUS["ARCHIVED"]),
            _goal_record("g-alive", status=GOAL_STATUS["ACTIVE"]),
        ],
    )
    _candidates = generate_candidates(
        goal_store=_store, target_user="366648462",
    )

    _refs = {c["goal_reference"] for c in _candidates}
    assert _refs == {"g-alive"}  # 终态 goal 被忽略


# ============================================================
# Test D: candidate 不包含非法 action
# ============================================================
def test_candidate_action_types_are_legal(tmp_path):
    from src.initiative.initiative_candidate import (
        SUPPORTED_ACTION_TYPES,
        generate_candidates,
    )

    _store = _goal_store(
        tmp_path,
        records=[
            _goal_record(f"g-{i}") for i in range(1, 4)
        ],
    )
    _candidates = generate_candidates(
        goal_store=_store, target_user="366648462", max_candidates=5,
    )

    assert _candidates
    for _c in _candidates:
        assert _c["action_type"] in SUPPORTED_ACTION_TYPES
        assert SUPPORTED_ACTION_TYPES == frozenset({"send_message"})
        # payload 字段白名单(不允许未知执行字段)
        _allowed = {"message", "target_user"}
        assert set(_c["payload"].keys()) <= _allowed


# ============================================================
# Test E: 不调用 LLM
# ============================================================
def test_generator_has_no_llm():
    _text = Path(
        _REPO_ROOT, "src/initiative/initiative_candidate.py"
    ).read_text(encoding="utf-8")
    _tree = ast.parse(_text)
    for _node in ast.walk(_tree):
        _module = ""
        if isinstance(_node, ast.ImportFrom):
            _module = _node.module or ""
        elif isinstance(_node, ast.Import):
            _module = " ".join(a.name or "" for a in _node.names)
        assert "llm" not in _module.lower(), f"generator 禁止 LLM: {_module}"
        assert "openai" not in _module.lower()


def test_generator_runs_without_llm_env(tmp_path, monkeypatch):
    from src.initiative.initiative_candidate import generate_candidates

    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    _store = _goal_store(tmp_path, records=[_goal_record("g-e")])
    _candidates = generate_candidates(goal_store=_store, target_user="366648462")
    assert len(_candidates) == 1  # 无 LLM 环境照常产出


# ============================================================
# Test F: 人格隔离
# ============================================================
def test_personality_isolation(tmp_path):
    from src.initiative.initiative_candidate import generate_candidates

    _sentinels = {
        "personality_state.json": {"version": "p53-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p53-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p53-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p53-sentinel", "trust": 0.5},
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

    _store = _goal_store(tmp_path, records=[_goal_record("g-f")])
    _candidates = generate_candidates(goal_store=_store, target_user="366648462")
    assert len(_candidates) == 1

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Test G: candidate 不产生 Proposal
# ============================================================
def test_candidate_produces_no_proposal(tmp_path):
    from src.initiative.initiative_candidate import generate_candidates

    # 生成器只读 GoalState: 不得 import 提案构造/drain/store 写入口
    _text = Path(
        _REPO_ROOT, "src/initiative/initiative_candidate.py"
    ).read_text(encoding="utf-8")
    for _tok in (
        "build_initiative_proposal",
        "drain_approved_initiative_proposals",
        "proposal.storage",
        "get_proposal",
        "initiative_sender",
        "initiative_bridge",
        "action_dispatcher",
        "ActionSafetyFilter",
    ):
        assert _tok not in _text, f"generator 禁止 Proposal/发送依赖: {_tok}"

    _store = _goal_store(tmp_path, records=[_goal_record("g-g")])
    _candidates = generate_candidates(goal_store=_store, target_user="366648462")
    assert len(_candidates) == 1
    # 未产生任何提案文件/状态文件(仅读取)
    assert not (tmp_path / "data").exists()


# ============================================================
# Test H: Python 3.11
# ============================================================
_P53_MODULE_FILES = (
    "src/initiative/initiative_candidate.py",
    "src/initiative/__init__.py",
    "src/goal/goal_state.py",
    "src/goal/goal_resolver.py",
)


def test_py_compile_candidate_modules():
    for _rel in _P53_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_candidate_modules_python311_grammar():
    for _rel in _P53_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
