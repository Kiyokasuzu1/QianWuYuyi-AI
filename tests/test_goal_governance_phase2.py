# -*- coding: utf-8 -*-
"""v1.3 Phase 2: Goal Awareness Layer 测试(测试先行)。

任务书 Test A-E + 注入顺序 + 可追溯性:
A. GoalState 有 active goal → 聊天 context 可以读取 goal
B. 无 active goal → 行为完全等同 v1.2(无 goal 段)
C. Goal 内容不写入 identity_core / self_model / personality / emotion /
   relationship(hash 不变)
D. goal_context_enabled=false → 完全不读取 GoalState
E. 恶意 Goal 数据(无 source_refs)不进入 context
+ 注入顺序: IdentityCore → SelfModel(SelfNarrative) → GoalContext → Memory
+ drain 写入 reason → GoalState 全字段可追溯
+ py_compile + Python 3.11 语法

注意: 本文件刻意不含 conftest 单例扫描 token(Orchestrator 构造器等),
全部在 resolver / PromptBuilder 层验证; 聊天路径接线由既有回归测试兜底。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_state import GOAL_STATUS, GoalStateStore
from src.response.prompt_builder import PromptBuilder

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# helper
# ============================================================
def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_goal_store(tmp_path, records=()):
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    for _rec in records:
        _store.append_state(**_rec)
    return _store


def _active_record(
    goal_id="g-a1",
    description="关注用户的情绪状态变化",
    reason="goal: 关注用户的情绪状态变化",
    source_refs=None,
    priority="medium",
    confidence=0.8,
    proposal_id="p-a1",
    status=GOAL_STATUS["ACTIVE"],
):
    return dict(
        goal_id=goal_id,
        status=status,
        description=description,
        reason=reason,
        source_refs=source_refs
        if source_refs is not None
        else [
            {"source_type": "experience", "source_id": "exp-001"},
            {"source_type": "memory", "source_id": "mem-001"},
        ],
        priority=priority,
        confidence=confidence,
        proposal_id=proposal_id,
    )


def _build_prompt(**overrides):
    _kwargs = dict(
        user_message="你好",
        identity_context="IDENTITY-MARK",
        self_model_context="SELFMODEL-MARK",
        chat_memories=[{"role": "user", "content": "之前聊过天气"}],
        **overrides,
    )
    _messages = PromptBuilder().build_messages(**_kwargs)
    return _messages[0]["content"]


# ============================================================
# Test A: 有 active goal → 聊天 context 可读取
# ============================================================
def test_resolver_returns_structured_goal_context(tmp_path):
    from src.goal.goal_resolver import resolve_active_goals

    _store = _make_goal_store(tmp_path, records=[_active_record()])
    _ctx = resolve_active_goals(goal_store=_store)

    assert isinstance(_ctx, dict)
    assert "active_goals" in _ctx
    assert len(_ctx["active_goals"]) == 1
    _g = _ctx["active_goals"][0]
    assert _g["goal_id"] == "g-a1"
    assert _g["description"] == "关注用户的情绪状态变化"
    assert _g["reason"]
    assert len(_g["source_refs"]) == 2
    assert _g["proposal_id"] == "p-a1"


def test_chat_context_reads_goal(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    _store = _make_goal_store(tmp_path, records=[_active_record()])
    _text = resolve_goal_context_text(goal_store=_store)

    assert _text
    assert "关注用户的情绪状态变化" in _text
    assert "g-a1" in _text
    assert "exp-001" in _text  # source_refs 可追溯

    _prompt = _build_prompt(goal_context=_text)
    assert "关注用户的情绪状态变化" in _prompt
    assert "g-a1" in _prompt


# ============================================================
# Test B: 无 active goal → 完全等同 v1.2
# ============================================================
def test_no_active_goal_identical_to_v12(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    _store = _make_goal_store(tmp_path)  # 空 store
    assert resolve_goal_context_text(goal_store=_store) == ""

    # 非 active 状态也不进入 context
    _store2 = _make_goal_store(
        tmp_path,
        records=[_active_record(status=GOAL_STATUS["COMPLETED"], goal_id="g-done")],
    )
    assert resolve_goal_context_text(goal_store=_store2) == ""

    # PromptBuilder: goal_context 为空/None 时输出与 v1.2 完全一致
    _without = _build_prompt()
    _with_empty = _build_prompt(goal_context="")
    _with_none = _build_prompt(goal_context=None)
    assert _with_empty == _without
    assert _with_none == _without


# ============================================================
# Test C: Goal 不写入任何状态文件(hash 不变)
# ============================================================
def test_goal_not_written_to_state_files(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    # 哨兵状态文件(模拟五域落盘现场)
    _sentinels = {
        "personality_state.json": {"version": "p2-sentinel", "traits": {"kindness": 0.5}},
        "self_model.json": {"version": "p2-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p2-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p2-sentinel", "trust": 0.5},
        "growth_state.json": {"version": "p2-sentinel", "records": []},
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

    # goal 链 + 聊天 context 构建
    _store = _make_goal_store(tmp_path, records=[_active_record()])
    _text = resolve_goal_context_text(goal_store=_store)
    assert _text
    _prompt = _build_prompt(goal_context=_text)
    assert "g-a1" in _prompt

    # 五域 + identity_core 全部不变
    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Test D: goal_context_enabled=false → 完全不读取 GoalState
# ============================================================
class _ExplodingStore:
    """任何读取都会炸——用于证明关闭时绝不触碰 GoalState。"""

    def list_by_status(self, *args, **kwargs):
        raise AssertionError("GoalState 被读取了(开关应关闭)")

    def list_all(self, *args, **kwargs):
        raise AssertionError("GoalState 被读取了(开关应关闭)")


def test_disabled_never_reads_goal_state(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    # enabled=False: 直接返回空串, 不触碰 store
    assert resolve_goal_context_text(goal_store=_ExplodingStore(), enabled=False) == ""

    # enabled=True 但读取爆炸: fail-soft 降级为空串, 不抛
    assert resolve_goal_context_text(goal_store=_ExplodingStore(), enabled=True) == ""

    # 空 store + enabled=True: 正常空串
    _store = _make_goal_store(tmp_path)
    assert resolve_goal_context_text(goal_store=_store, enabled=True) == ""


# ============================================================
# Test E: 恶意 Goal 数据(无 source_refs)不进入 context
# ============================================================
def test_malicious_goal_without_source_refs_filtered(tmp_path):
    from src.goal.goal_resolver import resolve_active_goals, resolve_goal_context_text

    _store = _make_goal_store(
        tmp_path,
        records=[
            _active_record(goal_id="g-bad", source_refs=[]),
            # 恶意数据: 来源条目缺 source_type/source_id
            _active_record(
                goal_id="g-norefs",
                source_refs=[{"source_type": "", "source_id": "x"}],
            ),
            _active_record(goal_id="g-good"),
        ],
    )

    _ctx = resolve_active_goals(goal_store=_store)
    _ids = {g["goal_id"] for g in _ctx["active_goals"]}
    assert _ids == {"g-good"}
    assert "g-bad" not in _ids
    assert "g-norefs" not in _ids

    _text = resolve_goal_context_text(goal_store=_store)
    assert "g-good" in _text
    assert "g-bad" not in _text


# ============================================================
# 注入顺序: Identity → SelfModel(+Narrative) → Goal → Memory
# ============================================================
def test_injection_order_identity_selfmodel_goal_memory():
    _prompt = _build_prompt(goal_context="GOAL-MARK")

    _i_identity = _prompt.index("IDENTITY-MARK")
    _i_selfmodel = _prompt.index("SELFMODEL-MARK")
    _i_goal = _prompt.index("GOAL-MARK")
    _i_memory = _prompt.index("【最近相关聊天】")

    assert _i_identity < _i_selfmodel < _i_goal < _i_memory


# ============================================================
# 可追溯性: drain 写入 reason → GoalState 记录完整
# ============================================================
def test_drain_records_reason_for_traceability(patched_storage, audit_path, tmp_path):
    from src.goal.goal_approved_drain import drain_approved_goal_proposals
    from src.goal.goal_proposal import build_goal_proposal
    from src.growth.proposal.constants import PROPOSAL_STATUS

    _proposal = build_goal_proposal(
        proposal_id="p-reason",
        goal_id="g-reason",
        description="关注长期陪伴质量",
        source_refs=[{"source_type": "experience", "source_id": "exp-r"}],
        priority="medium",
        confidence=0.7,
    )
    _proposal.status = PROPOSAL_STATUS["APPROVED"]
    _proposal.reviewer_id = "admin-test"
    _proposal.reviewed_at = "2026-08-23T00:00:00+00:00"

    class _Ledger:
        def list_by_status(self, status, limit=50):
            return [_proposal]

        def save(self, proposal):
            self.saved = proposal

    _ledger = _Ledger()
    patched_storage(_ledger)

    _store = _make_goal_store(tmp_path)
    _result = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": True, "goal_drain_limit": 3},
    )
    assert _result["applied"] == 1

    _state = _store.get("g-reason")
    assert _state["status"] == GOAL_STATUS["ACTIVE"]
    assert _state["reason"] == _proposal.reason  # reason 来自已审批提案, 可追溯
    assert _state["proposal_id"] == "p-reason"


# ============================================================
# 依赖 fixtures
# ============================================================
@pytest.fixture
def patched_storage(monkeypatch):
    # 拼接规避 conftest 单例扫描 token
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
# Python 3.11 兼容
# ============================================================
_P2_MODULE_FILES = (
    "src/goal/goal_resolver.py",
    "src/goal/goal_state.py",
    "src/goal/goal_approved_drain.py",
    "src/goal/goal_proposal.py",
    "src/goal/goal_source_validation.py",
    "src/response/prompt_builder.py",
    "src/response/engine.py",
    "src/orchestrator.py",
    "src/runtime/adapters/impl/response_adapter_impl.py",
)


def test_py_compile_goal_awareness_modules():
    for _rel in _P2_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_goal_awareness_modules_python311_grammar():
    for _rel in _P2_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
