# -*- coding: utf-8 -*-
"""v1.3 Phase 5.2: Initiative Execution Safety 测试(测试先行)。

任务书 Test A-G:
A. 低 confidence Action → reject
B. 用户 deny → reject
C. 重复 action → reject
D. 通过全部检查 → allow
E. 未知 action_type → reject
F. 人格隔离: identity_core/personality/self_model/emotion/relationship hash 不变
G. 关闭模式 initiative_safety_enabled=false → 不执行检查

红线: 不修改 Action / 不发送消息 / 不调用 LLM / 不接 InitiativeEngine。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.runtime.action_dispatcher import Action

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_action(
    action_type="send_message",
    payload=None,
    action_id="act-001",
    reason="",
):
    _payload = {
        "proposal_id": "p-safe-001",
        "goal_reference": "gc_safe",
        "initiative_id": "ini-safe",
        "message": "问候用户近况",
        "confidence": 0.8,
        "user_preference": "allow",
        "target_user": "366648462",
    }
    if payload is not None:
        _payload = dict(payload)
    return Action(
        action_id=action_id,
        action_type=action_type,
        payload=_payload,
        reason=reason,
    )


# ============================================================
# Test A: 低 confidence → reject
# ============================================================
def test_low_confidence_rejected():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True, min_confidence=0.5)
    _action = _make_action(payload={
        "proposal_id": "p-a",
        "goal_reference": "gc",
        "initiative_id": "ini",
        "message": "hi",
        "confidence": 0.3,
        "user_preference": "allow",
        "target_user": "366648462",
    })

    _decision = _filter.check(_action)

    assert _decision.allowed is False
    assert any("confidence" in r for r in _decision.reasons)
    assert _decision.checks["confidence"] == "fail"
    assert _action.status == "pending"  # Action 未被修改


# ============================================================
# Test B: 用户 deny → reject
# ============================================================
def test_user_deny_rejected():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True)
    _action = _make_action(payload={
        "proposal_id": "p-b",
        "goal_reference": "gc",
        "initiative_id": "ini",
        "message": "hi",
        "confidence": 0.9,
        "user_preference": "deny",
        "target_user": "366648462",
    })

    _decision = _filter.check(_action)

    assert _decision.allowed is False
    assert any("permission" in r for r in _decision.reasons)
    assert _decision.checks["permission"] == "fail"


# ============================================================
# Test C: 重复 action → reject
# ============================================================
def test_duplicate_action_rejected():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True, cooldown_seconds=0.0)
    _action = _make_action()

    _first = _filter.check(_action)
    assert _first.allowed is True  # 首次通过

    _second = _filter.check(_action)  # 同一 proposal_id 再次检查
    assert _second.allowed is False
    assert any("duplicate" in r for r in _second.reasons)
    assert _second.checks["duplicate"] == "fail"


# ============================================================
# Test D: 通过全部检查 → allow
# ============================================================
def test_all_checks_pass_allows():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True)
    _action = _make_action()

    _decision = _filter.check(_action)

    assert _decision.allowed is True
    assert _decision.reasons == []
    for _check in ("confidence", "permission", "duplicate", "cooldown", "schema"):
        assert _decision.checks[_check] == "pass", _check


# ============================================================
# Test E: 未知 action_type / 未知字段 → reject
# ============================================================
def test_unknown_action_type_rejected():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True)
    _action = _make_action(action_type="send_files_and_run_scripts")

    _decision = _filter.check(_action)

    assert _decision.allowed is False
    assert any("action_type" in r for r in _decision.reasons)
    assert _decision.checks["schema"] == "fail"


def test_unknown_payload_field_rejected():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=True)
    _payload = {
        "proposal_id": "p-e",
        "goal_reference": "gc",
        "initiative_id": "ini",
        "message": "hi",
        "confidence": 0.9,
        "user_preference": "allow",
        "target_user": "366648462",
        "execute_now": True,  # 未知字段
    }
    _decision = _filter.check(_make_action(payload=_payload))

    assert _decision.allowed is False
    assert any("unknown_field" in r for r in _decision.reasons)


# ============================================================
# Test F: 人格隔离
# ============================================================
def test_personality_isolation(tmp_path):
    from src.initiative.action_safety import ActionSafetyFilter

    _sentinels = {
        "personality_state.json": {"version": "p52-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p52-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p52-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p52-sentinel", "trust": 0.5},
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

    _filter = ActionSafetyFilter(enabled=True)
    for _i in range(3):
        _filter.check(_make_action(action_id=f"act-f{_i}"))
    _filter.check(_make_action(action_type="unknown_type", action_id="act-fx"))

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Test G: 关闭模式 → 不执行检查
# ============================================================
def test_disabled_mode_skips_checks():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(enabled=False)
    _decision = _filter.check(_make_action(action_type="unknown_type"))

    assert _decision.evaluated is False
    assert _decision.allowed is True  # 关闭 = 不拦截(等价于无安全层)
    assert _decision.checks == {}
    assert _decision.reasons == []


# ============================================================
# 集成: Phase 5.1 drain 产出 Action 携带 confidence(供安全层检查)
# ============================================================
def test_drain_action_carries_confidence(patched_storage, audit_path, tmp_path):
    from src.initiative.initiative_drain import (
        build_initiative_proposal,
        drain_approved_initiative_proposals,
    )
    from src.growth.proposal.constants import PROPOSAL_STATUS

    _p = build_initiative_proposal(
        initiative_id="ini-conf",
        goal_reference="gc_conf",
        source_refs=[{"source_type": "memory", "source_id": "mem-1"}],
        action_spec={"action_type": "send_message", "message": "hi"},
        confidence=0.75,
        proposal_id="p-conf",
    )
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"

    class _Ledger:
        def list_by_status(self, status, limit=50):
            return [_p]

        def save(self, proposal):
            pass

    patched_storage(_Ledger())
    _result = drain_approved_initiative_proposals(
        config={"initiative_proposal_enabled": True, "initiative_drain_limit": 3},
        action_recorder=None,
    )
    assert _result["applied"] == 1
    _action = _result["actions"][0]
    assert abs(_action.payload["confidence"] - 0.75) < 1e-6


# ============================================================
# fixtures / 3.11
# ============================================================
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


_P52_MODULE_FILES = (
    "src/initiative/action_safety.py",
    "src/initiative/initiative_drain.py",
    "src/initiative/__init__.py",
    "src/runtime/action_dispatcher.py",
)


def test_py_compile_safety_modules():
    for _rel in _P52_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_safety_modules_python311_grammar():
    for _rel in _P52_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))


def test_safety_module_has_no_sender_or_llm():
    _text = Path(_REPO_ROOT, "src/initiative/action_safety.py").read_text(
        encoding="utf-8"
    )
    for _tok in ("initiative_sender", "initiative_bridge", "send_private_msg"):
        assert _tok not in _text, f"safety 禁止 sender 引用: {_tok}"
    _tree = ast.parse(_text)
    for _node in ast.walk(_tree):
        _module = ""
        if isinstance(_node, ast.ImportFrom):
            _module = _node.module or ""
        elif isinstance(_node, ast.Import):
            _module = " ".join(a.name or "" for a in _node.names)
        assert "llm" not in _module.lower(), f"safety 禁止 LLM: {_module}"
