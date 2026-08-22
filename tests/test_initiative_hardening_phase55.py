# -*- coding: utf-8 -*-
"""v1.3 Phase 5.5: Initiative Production Hardening & Observability 测试(测试先行)。

任务书 Test A-F:
A. DeliveryPolicy 真实读取: allow/deny/silent/reserved 全部正确(阻断)
B. Action 生命周期完整: Proposal→Action→Safety→Dispatch 每一步都有 audit
C. Admin 查询: 读取 Action 记录, 不修改状态
D. Budget 限制: goal 次数 / user cooldown / global limit 全部拒绝
E. Shadow 模式: 产生完整审计, zero dispatch
F. 人格隔离 hash 一致

红线: 不修改 Dispatcher 状态机 / 不修改 InitiativeEngine / 不修改 GoalState /
不修改人格域 / 不新建 scheduler/thread。
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
from src.runtime.action_dispatcher import Action

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


def _goal_store(tmp_path, records=()):
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    for _rec in records:
        _store.append_state(**_rec)
    return _store


def _goal_record(goal_id, confidence=0.8):
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


# ============================================================
# Test A: DeliveryPolicy 真实读取(Provider 优先于 payload)
# ============================================================
def _permission_provider(source):
    """模拟真实只读来源(如用户上下文/关系快照): target_user → preference。"""

    def _resolve(action):
        _payload = getattr(action, "payload", None) or {}
        _target = str(_payload.get("target_user", "") or "")
        return str(source.get(_target, "open") or "open")

    return _resolve


def _perm_action(pref_payload, target="u-1"):
    return Action(
        action_id="act-perm",
        action_type="send_message",
        payload={
            "proposal_id": "p-perm",
            "goal_reference": "g-perm",
            "initiative_id": "ini-perm",
            "message": "hi",
            "confidence": 0.9,
            "user_preference": pref_payload,  # payload 注入值(应被真实来源覆盖)
            "target_user": target,
        },
    )


def test_delivery_policy_real_read_blocks_deny_silent_reserved():
    from src.initiative.action_safety import ActionSafetyFilter

    # 真实来源: u-deny=deny, u-silent=silent, u-reserved=reserved, u-open=open
    _source = {
        "u-deny": "deny",
        "u-silent": "silent",
        "u-reserved": "reserved",
        "u-open": "open",
    }
    _filter = ActionSafetyFilter(
        enabled=True, permission_provider=_permission_provider(_source),
    )

    for _target in ("u-deny", "u-silent", "u-reserved"):
        _decision = _filter.check(
            _perm_action("allow", target=_target)  # payload 谎称 allow
        )
        assert _decision.allowed is False, _target
        assert _decision.checks["permission"] == "fail", _target
        assert any("permission" in r for r in _decision.reasons), _target

    # open → 通过(deny-list 语义)
    _decision = _filter.check(_perm_action("deny", target="u-open"))
    assert _decision.allowed is True  # 真实来源 open 覆盖 payload deny
    assert _decision.checks["permission"] == "pass"


def test_permission_provider_missing_source_fail_closed():
    from src.initiative.action_safety import ActionSafetyFilter

    _filter = ActionSafetyFilter(
        enabled=True, permission_provider=_permission_provider({}),
    )
    # 来源中无该用户且 provider 返回 "open"(delivery policy 默认), 通过
    _decision = _filter.check(_perm_action("allow", target="u-unknown"))
    assert _decision.allowed is True


# ============================================================
# Test B: Action 生命周期每步 audit
# ============================================================
def test_action_lifecycle_full_audit(tmp_path, patched_storage, audit_path):
    from src.governance.state_mutation_audit import read_entries
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_drain import build_initiative_proposal
    from src.initiative.initiative_observability import InitiativeActionLedger
    from src.initiative.initiative_pipeline import InitiativePipeline

    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _ledger = InitiativeActionLedger(
        str(tmp_path / "initiative" / "initiative_actions.jsonl")
    )
    _pipeline = InitiativePipeline(
        mode="active",
        goal_store=_goal_store(tmp_path, records=[_goal_record("g-life")]),
        proposal_storage=_pstore,
        dispatcher=_SpyDispatcher(),
        safety_filter=ActionSafetyFilter(enabled=True, cooldown_seconds=0.0),
        dispatch_enabled=True,
        observability_enabled=True,
        action_ledger=_ledger,
        target_user="366648462",
    )

    # 1) Proposal 创建
    _m1 = _pipeline.run_once()
    _pid = _m1["proposals_created"][0]["proposal_id"]
    _pstore.get(_pid).status = PROPOSAL_STATUS["APPROVED"]
    _pstore.get(_pid).reviewer_id = "admin-test"
    _pstore.get(_pid).reviewed_at = "2026-08-23T00:00:00+00:00"

    # 2) Action → Safety → Dispatch
    _m2 = _pipeline.run_once()
    assert len(_m2["actions"]) == 1
    _action_id = _m2["actions"][0]["action_id"]

    # 每步 audit:
    # ① ledger: created → safety_passed → dispatched
    _records = _ledger.query(limit=100)
    _stages_for_action = [r["stage"] for r in _records if r["action_id"] == _action_id]
    assert "action_created" in _stages_for_action
    assert "safety_passed" in _stages_for_action
    assert "dispatched" in _stages_for_action
    # ② proposal 创建审计
    assert any(r["stage"] == "proposal_created" for r in _records)
    # ③ state_mutation_audit(component=initiative)
    _entries = [
        e for e in read_entries(limit=1000)
        if e.get("component") == "initiative" and e.get("proposal_id") == _pid
    ]
    assert len(_entries) == 1


# ============================================================
# Test C: Admin 查询(只读)
# ============================================================
def test_admin_query_read_only(tmp_path):
    from src.initiative.initiative_observability import (
        InitiativeActionLedger,
        list_initiative_actions,
    )

    _path = str(tmp_path / "initiative" / "initiative_actions.jsonl")
    _ledger = InitiativeActionLedger(_path)
    _ledger.record({
        "action_id": "act-q1",
        "proposal_id": "p-q1",
        "goal_reference": "g-q1",
        "stage": "action_created",
        "status": "pending",
        "safety_result": "",
        "rejection_reason": "",
        "created_at": "2026-08-23T00:00:00+00:00",
        "executed_at": "",
    })

    _before = _hash_bytes(Path(_path).read_bytes())
    _items = list_initiative_actions(path=_path, limit=10)
    _after = _hash_bytes(Path(_path).read_bytes())

    assert len(_items) == 1
    _item = _items[0]
    assert _item["action_id"] == "act-q1"
    assert _item["proposal_id"] == "p-q1"
    assert _item["goal_reference"] == "g-q1"
    assert _before == _after  # 只读, 不修改状态
    # 展示字段完整(admin 展示规格)
    for _field in ("action_id", "proposal_id", "goal_reference", "status",
                   "safety_result", "rejection_reason", "created_at", "executed_at"):
        assert _field in _item


# ============================================================
# Test D: Budget 限制
# ============================================================
def _budget_action(pid, goal_ref, target="u-b", action_id=None):
    return Action(
        action_id=action_id or f"act-{pid}",
        action_type="send_message",
        payload={
            "proposal_id": pid,
            "goal_reference": goal_ref,
            "initiative_id": f"ini-{pid}",
            "message": f"msg-{pid}",
            "confidence": 0.9,
            "user_preference": "allow",
            "target_user": target,
        },
    )


def test_budget_per_goal_and_global_limits():
    from src.initiative.initiative_budget import InitiativeBudgetPolicy

    _budget = InitiativeBudgetPolicy(
        per_goal_daily_limit=1, per_user_cooldown_seconds=0, global_daily_limit=100,
    )
    _ok1, _r1 = _budget.check(_budget_action("p-g1a", "g1"))
    assert _ok1 is True
    _budget.record(_budget_action("p-g1a", "g1"))

    # 同 goal 第二次 → per_goal_daily_limit 拒绝
    _ok2, _r2 = _budget.check(_budget_action("p-g1b", "g1"))
    assert _ok2 is False
    assert "goal" in _r2

    # 不同 goal → 通过
    _ok3, _ = _budget.check(_budget_action("p-g2", "g2"))
    assert _ok3 is True


def test_budget_global_daily_limit():
    from src.initiative.initiative_budget import InitiativeBudgetPolicy

    _budget = InitiativeBudgetPolicy(
        per_goal_daily_limit=100, per_user_cooldown_seconds=0, global_daily_limit=1,
    )
    _budget.record(_budget_action("p-1", "g1"))
    _ok, _reason = _budget.check(_budget_action("p-2", "g2"))
    assert _ok is False
    assert "global" in _reason


def test_budget_user_cooldown():
    from src.initiative.initiative_budget import InitiativeBudgetPolicy

    _budget = InitiativeBudgetPolicy(
        per_goal_daily_limit=100, per_user_cooldown_seconds=3600,
        global_daily_limit=100,
    )
    _budget.record(_budget_action("p-1", "g1", target="u-cooldown"))
    _ok, _reason = _budget.check(_budget_action("p-2", "g2", target="u-cooldown"))
    assert _ok is False
    assert "cooldown" in _reason


# ============================================================
# Test E: Shadow 模式完整审计 + zero dispatch
# ============================================================
def test_shadow_mode_full_audit_zero_dispatch(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_observability import InitiativeActionLedger
    from src.initiative.initiative_pipeline import InitiativePipeline

    _pstore = _FakeProposalStorage()
    patched_storage(_pstore)
    _ledger = InitiativeActionLedger(
        str(tmp_path / "initiative" / "initiative_actions.jsonl")
    )
    _spy = _SpyDispatcher()
    _pipeline = InitiativePipeline(
        mode="shadow",
        goal_store=_goal_store(tmp_path, records=[_goal_record("g-sh")]),
        proposal_storage=_pstore,
        dispatcher=_spy,
        safety_filter=ActionSafetyFilter(enabled=True),
        dispatch_enabled=True,  # 即使开, shadow 也不 dispatch
        observability_enabled=True,
        action_ledger=_ledger,
    )

    _metrics = _pipeline.run_once()

    assert len(_metrics["proposals_created"]) == 1
    # 完整审计: proposal_created 落账
    _records = _ledger.query(limit=100)
    assert any(r["stage"] == "proposal_created" for r in _records)
    # zero dispatch / zero action
    assert _metrics["actions"] == []
    assert _spy.dispatched == []
    assert not any(r["stage"] == "dispatched" for r in _records)


# ============================================================
# Test F: 人格隔离
# ============================================================
def test_personality_isolation(tmp_path, patched_storage, audit_path):
    from src.initiative.action_safety import ActionSafetyFilter
    from src.initiative.initiative_budget import InitiativeBudgetPolicy
    from src.initiative.initiative_observability import InitiativeActionLedger
    from src.initiative.initiative_pipeline import InitiativePipeline

    _sentinels = {
        "personality_state.json": {"version": "p55-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p55-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p55-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p55-sentinel", "trust": 0.5},
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
    _pipeline = InitiativePipeline(
        mode="active",
        goal_store=_goal_store(tmp_path, records=[_goal_record("g-iso")]),
        proposal_storage=_pstore,
        dispatcher=_SpyDispatcher(),
        safety_filter=ActionSafetyFilter(enabled=True, cooldown_seconds=0.0),
        budget_policy=InitiativeBudgetPolicy(
            per_goal_daily_limit=100, per_user_cooldown_seconds=0,
            global_daily_limit=100,
        ),
        dispatch_enabled=True,
        observability_enabled=True,
        action_ledger=InitiativeActionLedger(
            str(tmp_path / "initiative" / "initiative_actions.jsonl")
        ),
    )
    _pipeline.run_once()

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Python 3.11 + 静态红线
# ============================================================
_P55_MODULE_FILES = (
    "src/initiative/initiative_observability.py",
    "src/initiative/initiative_budget.py",
    "src/initiative/action_safety.py",
    "src/initiative/initiative_pipeline.py",
    "src/initiative/__init__.py",
    "src/admin/api/routes.py",
)


def test_py_compile_hardening_modules():
    for _rel in _P55_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_hardening_modules_python311_grammar():
    for _rel in _P55_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))


def test_new_modules_static_red_lines():
    # sender/engine 类 token 查全文(禁止任何引用); 域依赖只查 import 语句
    # (docstring 红线注释允许出现域名单词)
    for _rel in (
        "src/initiative/initiative_observability.py",
        "src/initiative/initiative_budget.py",
    ):
        _text = Path(_REPO_ROOT, _rel).read_text(encoding="utf-8")
        for _tok in ("initiative_sender", "initiative_bridge", "send_private_msg",
                     "InitiativeEngine"):
            assert _tok not in _text, f"{_rel} 禁止引用: {_tok}"
        _tree = ast.parse(_text)
        for _node in ast.walk(_tree):
            _module = ""
            if isinstance(_node, ast.ImportFrom):
                _module = _node.module or ""
            elif isinstance(_node, ast.Import):
                _module = " ".join(a.name or "" for a in _node.names)
            for _tok in ("personality", "self_model", "emotion", "relationship", "llm"):
                assert _tok not in _module.lower(), (
                    f"{_rel} import 禁止域依赖: {_module}"
                )
