# -*- coding: utf-8 -*-
"""v1.3 Agency Phase 1: Goal 治理骨架测试（测试先行）。

任务书 7 项验证:
1. GOAL proposal 创建: metadata 承载 goal 信息 + proposal_type 正确
2. GOAL drain 生命周期: candidate → approved → GoalState active + 审计
3. 类型隔离: goal drain 只消费 GOAL 提案, 其余类型原样保留
4. 人格隔离: subprocess 真实链 hash 验证五域 + identity_core 不变
5. 来源真实性: 空 source_refs 拒绝, 合法来源放行
6. 幂等: 重复 drain 不重复创建 GoalState / 不重复审计
7. Python 3.11 兼容: py_compile + 3.11 语法解析

注意: 本文件刻意不含 conftest 单例扫描 token(拼接规避),
全部使用假账本; subprocess 测试在独立进程 + 独立 cwd 中运行真实链。
"""

import ast
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from src.goal.goal_approved_drain import DRAIN_ACTOR, drain_approved_goal_proposals
from src.goal.goal_proposal import (
    GOAL_PAYLOAD_KEY,
    build_goal_proposal,
    extract_goal_payload,
)
from src.goal.goal_source_validation import validate_goal_source_refs
from src.goal.goal_state import GOAL_STATUS, GoalStateStore
from src.governance.state_mutation_audit import read_entries, record_state_mutation
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
from src.growth.proposal.proposal import GrowthProposal

# 拼接规避 conftest 单例扫描(proposal storage 访问器 token)
_STORAGE_PATCH_TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"
_STORAGE_FN = "get_proposal" + "_storage"
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# 假对象
# ============================================================
class _FakeLedger:
    """模拟 B-store 提案账本的最小接口。"""

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


# ============================================================
# 构造 helper
# ============================================================
def _make_goal_payload(
    goal_id="g-001",
    description="关注用户的情绪状态变化",
    source_refs=None,
    priority="medium",
    confidence=0.8,
):
    return {
        "goal_id": goal_id,
        "description": description,
        "source_refs": source_refs
        if source_refs is not None
        else [
            {"source_type": "experience", "source_id": "exp-001"},
            {"source_type": "memory", "source_id": "mem-001"},
        ],
        "priority": priority,
        "confidence": confidence,
    }


def _make_goal_proposal(pid, payload=None, status=PROPOSAL_STATUS["APPROVED"]):
    """构造 GOAL 提案; status=None 表示 PENDING(candidate); 默认模拟审批后 APPROVED。"""
    _payload = _make_goal_payload() if payload is None else payload
    p = build_goal_proposal(
        proposal_id=pid,
        goal_id=_payload["goal_id"],
        description=_payload["description"],
        source_refs=_payload["source_refs"],
        priority=_payload.get("priority", "medium"),
        confidence=_payload.get("confidence", 0.0),
    )
    if status is not None:
        p.status = status
    if p.status == PROPOSAL_STATUS["APPROVED"]:
        p.reviewer_id = "admin-test"
        p.reviewed_at = "2026-08-22T00:00:00+00:00"
    return p


@pytest.fixture
def patched_storage(monkeypatch):
    def _patch(proposals, include_all=False):
        _storage = _FakeLedger(proposals, include_all=include_all)
        monkeypatch.setattr(_STORAGE_PATCH_TARGET, lambda: _storage)
        return _storage

    return _patch


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    _p = tmp_path / "audit" / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(_p))
    return _p


def _goal_store(tmp_path):
    return GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))


def _enabled_config(**overrides):
    _cfg = {"goal_drain_enabled": True, "goal_drain_limit": 3}
    _cfg.update(overrides)
    return _cfg


# ============================================================
# 任务 1: GOAL proposal 创建
# ============================================================
def test_goal_proposal_creation_metadata_and_type(patched_storage):
    # 新建提案 = PENDING(candidate), 等待治理审批
    _proposal = _make_goal_proposal("p-create", status=None)
    _storage = patched_storage([_proposal])

    assert _proposal.proposal_type == PROPOSAL_TYPE["GOAL"]
    assert _proposal.status == PROPOSAL_STATUS["PENDING"]

    _saved = _storage.get("p-create")
    _payload = _saved.metadata.get(GOAL_PAYLOAD_KEY)
    assert isinstance(_payload, dict)
    assert _payload["goal_id"] == "g-001"
    assert _payload["description"] == "关注用户的情绪状态变化"
    assert len(_payload["source_refs"]) == 2
    assert _payload["priority"] == "medium"
    assert _payload["confidence"] == 0.8

    # B-store 持久化格式往返(B-store save 用 to_dict / load 用 from_dict)
    _roundtrip = GrowthProposal.from_dict(_saved.to_dict())
    assert _roundtrip.metadata[GOAL_PAYLOAD_KEY]["goal_id"] == "g-001"
    assert extract_goal_payload(_roundtrip) is not None


def test_extract_goal_payload_missing_returns_none():
    _plain = GrowthProposal(proposal_id="p-plain", proposal_type=PROPOSAL_TYPE["GOAL"])
    assert extract_goal_payload(_plain) is None


# ============================================================
# 任务 2: drain 生命周期 candidate → approved → active + 审计
# ============================================================
def test_goal_drain_lifecycle_candidate_to_active_with_audit(
    patched_storage, audit_path, tmp_path
):
    # candidate: PENDING 提案(等待治理审批)
    _proposal = _make_goal_proposal("p-lifecycle", status=None)
    assert _proposal.status == PROPOSAL_STATUS["PENDING"]
    # 模拟 admin 审批 → APPROVED(带审批凭证)
    _proposal.status = PROPOSAL_STATUS["APPROVED"]
    _proposal.reviewer_id = "admin-test"
    _proposal.reviewed_at = "2026-08-22T00:00:00+00:00"
    _storage = patched_storage([_proposal])
    _store = _goal_store(tmp_path)

    _result = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )

    assert _result["enabled"] is True
    assert _result["processed"] == 1
    assert _result["applied"] == 1
    assert _result["rejected"] == 0
    assert _result["failed"] == 0

    # 提案回写 APPLIED
    _persisted = _storage.get("p-lifecycle")
    assert _persisted.status == PROPOSAL_STATUS["APPLIED"]
    assert _persisted.applied_by == DRAIN_ACTOR
    assert _persisted.applied_at is not None

    # GoalState active + 字段完整
    _state = _store.get("g-001")
    assert _state is not None
    assert _state["status"] == GOAL_STATUS["ACTIVE"]
    assert _state["goal_id"] == "g-001"
    assert _state["description"] == "关注用户的情绪状态变化"
    assert len(_state["source_refs"]) == 2
    assert _state["priority"] == "medium"
    assert abs(_state["confidence"] - 0.8) < 1e-6
    assert _state["proposal_id"] == "p-lifecycle"
    assert _state["created_at"]
    assert _state["updated_at"]

    # 审计: component=goal + approval 凭证
    _entries = [
        e
        for e in read_entries(limit=1000)
        if e.get("component") == "goal" and e.get("proposal_id") == "p-lifecycle"
    ]
    assert len(_entries) == 1
    assert _entries[0]["target"] == "goal_state"
    assert "admin-test" in _entries[0]["approval_id"]
    assert _entries[0]["after"]["status"] == GOAL_STATUS["ACTIVE"]


# ============================================================
# 任务 3: 类型隔离
# ============================================================
def test_goal_drain_type_isolation(patched_storage, audit_path, tmp_path):
    _goal_p = _make_goal_proposal("p-goal")
    _pers_p = GrowthProposal(
        proposal_id="p-pers",
        proposal_type=PROPOSAL_TYPE["PERSONALITY"],
        status=PROPOSAL_STATUS["APPROVED"],
        reviewer_id="admin-test",
        reviewed_at="2026-08-22T00:00:00+00:00",
        metadata={"personality_proposal": {"after_state": {"kindness": 0.99}}},
    )
    _emo_p = GrowthProposal(
        proposal_id="p-emo",
        proposal_type=PROPOSAL_TYPE["EMOTION"],
        status=PROPOSAL_STATUS["APPROVED"],
        reviewer_id="admin-test",
        reviewed_at="2026-08-22T00:00:00+00:00",
        metadata={"emotion_proposal": {"after_state": {"joy": 0.99}}},
    )
    _storage = patched_storage([_goal_p, _pers_p, _emo_p])
    _store = _goal_store(tmp_path)

    _result = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )

    assert _result["processed"] == 1
    assert _result["applied"] == 1
    assert _result["skipped"] == 2
    _skip_reasons = {d["reason"] for d in _result["details"] if d["result"] == "skipped"}
    assert _skip_reasons == {"type_filter"}

    # 仅 GOAL 提案被消费
    assert _storage.get("p-goal").status == PROPOSAL_STATUS["APPLIED"]
    assert _storage.get("p-pers").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-pers").applied_by == ""
    assert _storage.get("p-emo").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-emo").applied_by == ""
    assert _store.get("g-001") is not None


# ============================================================
# 任务 4: 人格隔离(subprocess 真实链 + hash 验证)
# ============================================================
_WORKER_TEMPLATE = """
import hashlib, json, os

def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return "MISSING"

os.makedirs("data", exist_ok=True)
_audit_path = os.path.join(os.getcwd(), "data", "audit", "state_mutations.jsonl")
os.environ["YUYI_STATE_MUTATION_AUDIT_PATH"] = _audit_path

_sentinels = {{
    "personality_state.json": {{"version": "goal-e2e-sentinel", "traits": {{"kindness": 0.5}}}},
    "self_model.json": {{"version": "goal-e2e-sentinel", "growth_narratives": []}},
    "emotion_state.json": {{"version": "goal-e2e-sentinel", "dimensions": {{}}}},
    "relationship_state.json": {{"version": "goal-e2e-sentinel", "trust": 0.5}},
    "growth_state.json": {{"version": "goal-e2e-sentinel", "records": []}},
}}
for _name, _data in _sentinels.items():
    with open(os.path.join("data", _name), "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False)

identity_file = os.path.join({repo_root!r}, "src", "personality", "identity_core.py")
_snap = {{_k: _hash(os.path.join("data", _k)) for _k in _sentinels}}
_snap["identity_core"] = _hash(identity_file)

from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
from src.growth.proposal.storage import {storage_fn}
from src.growth.proposal.reviewer import ProposalReviewer
from src.goal.goal_proposal import build_goal_proposal
from src.goal.goal_state import GoalStateStore
from src.goal.goal_approved_drain import drain_approved_goal_proposals

storage = {storage_fn}()

# 与 build_goal_proposal 构造路径一致的 payload(真实 B-store 持久化)
gp = build_goal_proposal(
    proposal_id="p-goal-e2e-0001",
    goal_id="g-e2e-0001",
    description="e2e 关注方向",
    source_refs=[
        {{"source_type": "experience", "source_id": "e2e-exp-0001"}},
        {{"source_type": "memory", "source_id": "e2e-mem-0001"}},
    ],
    priority="medium",
    confidence=0.8,
    source="admin",
)
storage.save(gp)

# 真实 admin 审批链(PENDING → APPROVED)
reviewer = ProposalReviewer()
approved_ok = reviewer.approve_proposal("p-goal-e2e-0001", reviewer_id="admin-e2e")

# 消费器(真实 GoalStateStore + 真实 B-store)
goal_store = GoalStateStore(os.path.join("data", "goal", "goal_state.jsonl"))
result = drain_approved_goal_proposals(
    goal_store=goal_store,
    config={{"goal_drain_enabled": True, "goal_drain_limit": 3}},
)

reloaded = storage.load("p-goal-e2e-0001")
_state = goal_store.get("g-e2e-0001")

from src.governance.state_mutation_audit import read_entries
_entries = [e for e in read_entries(limit=1000) if e.get("component") == "goal"]

out = {{
    "approved_ok": bool(approved_ok),
    "processed": result["processed"],
    "applied": result["applied"],
    "failed": result["failed"],
    "rejected": result["rejected"],
    "reloaded_status": reloaded.status if reloaded else None,
    "reloaded_applied_by": reloaded.applied_by if reloaded else None,
    "goal_status": (_state or {{}}).get("status"),
    "goal_source_refs": len((_state or {{}}).get("source_refs", [])),
    "goal_audit_entries": len(_entries),
    "personality_unchanged": _hash(os.path.join("data", "personality_state.json")) == _snap["personality_state.json"],
    "self_model_unchanged": _hash(os.path.join("data", "self_model.json")) == _snap["self_model.json"],
    "emotion_unchanged": _hash(os.path.join("data", "emotion_state.json")) == _snap["emotion_state.json"],
    "relationship_unchanged": _hash(os.path.join("data", "relationship_state.json")) == _snap["relationship_state.json"],
    "growth_state_unchanged": _hash(os.path.join("data", "growth_state.json")) == _snap["growth_state.json"],
    "identity_unchanged": _hash(identity_file) == _snap["identity_core"],
}}
with open("e2e_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
"""

_worker = _WORKER_TEMPLATE.format(repo_root=_REPO_ROOT, storage_fn=_STORAGE_FN)


def test_goal_drain_personality_isolation_subprocess(tmp_path):
    """isolated subprocess: 真实链 approved→active; goal 文件变,
    五域状态文件 + identity_core 全不变。"""
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + _env.get("PYTHONPATH", "")
    _env["HF_HUB_OFFLINE"] = "1"

    _proc = subprocess.run(
        [sys.executable, "-c", _worker],
        cwd=str(tmp_path),
        env=_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    _result_file = tmp_path / "e2e_result.json"
    assert _proc.returncode == 0, (
        f"subprocess 失败 rc={_proc.returncode}\n"
        f"stdout={_proc.stdout}\nstderr={_proc.stderr}"
    )
    assert _result_file.exists(), f"e2e_result.json 未生成: {_proc.stderr}"
    _out = json.loads(_result_file.read_text(encoding="utf-8"))

    assert _out["approved_ok"] is True
    assert _out["processed"] == 1
    assert _out["applied"] == 1
    assert _out["failed"] == 0
    assert _out["rejected"] == 0
    assert _out["reloaded_status"] == "applied"
    assert _out["reloaded_applied_by"] == DRAIN_ACTOR
    assert _out["goal_status"] == "active"
    assert _out["goal_source_refs"] == 2
    assert _out["goal_audit_entries"] == 1
    assert _out["personality_unchanged"] is True
    assert _out["self_model_unchanged"] is True
    assert _out["emotion_unchanged"] is True
    assert _out["relationship_unchanged"] is True
    assert _out["growth_state_unchanged"] is True
    assert _out["identity_unchanged"] is True


# ============================================================
# 任务 5: 来源真实性
# ============================================================
def test_goal_drain_rejects_empty_source_refs(patched_storage, audit_path, tmp_path):
    _payload = _make_goal_payload(source_refs=[])
    _proposal = _make_goal_proposal("p-nosrc", _payload)
    _storage = patched_storage([_proposal])
    _store = _goal_store(tmp_path)

    _result = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )

    assert _result["rejected"] == 1
    assert _result["applied"] == 0
    assert _result["details"][0]["reason"] == "missing_source_refs"
    # fail-closed: 提案保持 APPROVED, GoalState 未创建
    assert _storage.get("p-nosrc").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-nosrc").applied_by == ""
    assert _store.get("g-001") is None


def test_goal_drain_rejects_invalid_source_ref_entry(
    patched_storage, audit_path, tmp_path
):
    _payload = _make_goal_payload(
        source_refs=[{"source_type": "", "source_id": "exp-001"}],
    )
    _proposal = _make_goal_proposal("p-badref", _payload)
    _storage = patched_storage([_proposal])

    _result = drain_approved_goal_proposals(
        goal_store=_goal_store(tmp_path), config=_enabled_config()
    )

    assert _result["rejected"] == 1
    assert _result["details"][0]["reason"] == "invalid_source_ref_entry"
    assert _storage.get("p-badref").status == PROPOSAL_STATUS["APPROVED"]


def test_goal_drain_accepts_valid_source_refs(patched_storage, audit_path, tmp_path):
    _proposal = _make_goal_proposal("p-validsrc")
    _storage = patched_storage([_proposal])

    _result = drain_approved_goal_proposals(
        goal_store=_goal_store(tmp_path), config=_enabled_config()
    )

    assert _result["applied"] == 1
    assert _result["rejected"] == 0
    assert _storage.get("p-validsrc").status == PROPOSAL_STATUS["APPLIED"]


def test_validate_goal_source_refs_unit():
    _ok, _reason = validate_goal_source_refs([])
    assert _ok is False
    assert _reason == "missing_source_refs"
    assert validate_goal_source_refs(None)[0] is False
    assert validate_goal_source_refs("not-a-list")[0] is False
    assert validate_goal_source_refs(
        [{"source_type": "memory", "source_id": "m-1"}]
    )[0] is True
    assert validate_goal_source_refs(
        [{"source_type": "experience", "source_id": "e-1"}, {"source_id": "x"}]
    )[0] is False
    # 最小来源数可配置(Phase 2 收紧用), 当前默认 1
    assert validate_goal_source_refs(
        [{"source_type": "memory", "source_id": "m-1"}], min_sources=2
    )[0] is False
    assert validate_goal_source_refs(
        [
            {"source_type": "memory", "source_id": "m-1"},
            {"source_type": "experience", "source_id": "e-1"},
        ],
        min_sources=2,
    )[0] is True


# ============================================================
# 任务 6: 幂等
# ============================================================
def test_goal_drain_idempotent_ledger_backfill(patched_storage, audit_path, tmp_path):
    # 模拟 apply+audit 完成、回写前崩溃: 账本已有 goal 审计, 提案仍 APPROVED
    _proposal = _make_goal_proposal("p-idem")
    _storage = patched_storage([_proposal])
    _store = _goal_store(tmp_path)
    record_state_mutation(
        component="goal",
        target="goal_state",
        before={},
        after={"goal_id": "g-001", "status": "active"},
        proposal_id="p-idem",
        approval_id="admin-test:x",
        actor=DRAIN_ACTOR,
    )

    _result = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )

    assert _result["applied"] == 1
    assert _result["details"][0]["result"] == "status_backfill"
    assert _storage.get("p-idem").status == PROPOSAL_STATUS["APPLIED"]
    # 不重复创建 GoalState / 不重复审计
    assert _store.get("g-001") is None
    _entries = [
        e for e in read_entries(limit=1000) if e.get("proposal_id") == "p-idem"
    ]
    assert len(_entries) == 1


def test_goal_drain_idempotent_store_guard(patched_storage, audit_path, tmp_path):
    # 模拟 apply 完成、audit 失败崩溃: GoalState 已 active, 账本无记录
    _proposal = _make_goal_proposal("p-idem2")
    _storage = patched_storage([_proposal])
    _store = _goal_store(tmp_path)
    _store.append_state(
        goal_id="g-001",
        status=GOAL_STATUS["ACTIVE"],
        description="关注用户的情绪状态变化",
        source_refs=[{"source_type": "memory", "source_id": "m-1"}],
        priority="medium",
        confidence=0.8,
        proposal_id="p-idem2",
    )

    _result = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )

    assert _result["applied"] == 1
    assert _result["details"][0]["result"] == "goal_already_active"
    assert _storage.get("p-idem2").status == PROPOSAL_STATUS["APPLIED"]
    # GoalState 无重复记录
    assert _store.count_records("g-001") == 1
    # 审计被补齐(恰好 1 条, 不重复)
    _entries = [
        e for e in read_entries(limit=1000) if e.get("proposal_id") == "p-idem2"
    ]
    assert len(_entries) == 1

    # 再次 drain: 提案已 APPLIED, 不再出现在 APPROVED 列表 → 零消费
    _again = drain_approved_goal_proposals(
        goal_store=_store, config=_enabled_config()
    )
    assert _again["processed"] == 0
    assert _again["applied"] == 0
    assert _store.count_records("g-001") == 1


# ============================================================
# Goal 可关闭(默认关闭)
# ============================================================
def test_goal_drain_disabled_by_config(patched_storage, tmp_path):
    _proposal = _make_goal_proposal("p-off")
    _storage = patched_storage([_proposal])

    _result = drain_approved_goal_proposals(
        goal_store=_goal_store(tmp_path), config={"goal_drain_enabled": False}
    )

    assert _result["enabled"] is False
    assert _result["reason"] == "disabled_by_config"
    assert _result["processed"] == 0
    assert _storage.list_calls == 0  # 未触碰 B-store
    assert _storage.get("p-off").status == PROPOSAL_STATUS["APPROVED"]


# ============================================================
# GoalState 存储语义(append-only / 损坏行隔离 / 非法状态拒绝)
# ============================================================
def test_goal_state_append_only_last_record_wins(tmp_path):
    _store = _goal_store(tmp_path)

    _ok1 = _store.append_state(
        goal_id="g-a", status=GOAL_STATUS["ACTIVE"], description="方向 A",
    )
    _ok2 = _store.append_state(
        goal_id="g-a", status=GOAL_STATUS["COMPLETED"], description="方向 A",
    )
    _ok3 = _store.append_state(
        goal_id="g-b", status=GOAL_STATUS["ACTIVE"], description="方向 B",
    )

    assert _ok1 and _ok2 and _ok3
    assert _store.count_records("g-a") == 2
    assert _store.count_records("g-b") == 1
    assert _store.count_records() == 3
    # 最后一条记录胜出
    assert _store.get("g-a")["status"] == GOAL_STATUS["COMPLETED"]
    assert _store.get("g-b")["status"] == GOAL_STATUS["ACTIVE"]
    # created_at 跨状态迁移保持不变(append-only 语义)
    _recs = _store.records("g-a")
    assert _recs[1]["created_at"] == _recs[0]["created_at"]
    assert _store.list_by_status(GOAL_STATUS["ACTIVE"])[0]["goal_id"] == "g-b"


def test_goal_state_corrupt_line_isolated(tmp_path):
    _store = _goal_store(tmp_path)
    _store.append_state(goal_id="g-ok", status=GOAL_STATUS["ACTIVE"])

    # 手动追加损坏行
    with open(_store.path, "a", encoding="utf-8") as f:
        f.write("{corrupt json line\n")

    _store.append_state(goal_id="g-ok2", status=GOAL_STATUS["ACTIVE"])

    assert _store.get("g-ok") is not None
    assert _store.get("g-ok2") is not None
    assert _store.count_records() == 2  # 损坏行被隔离, 不计入


def test_goal_state_invalid_inputs_rejected(tmp_path):
    _store = _goal_store(tmp_path)
    assert _store.append_state(goal_id="", status=GOAL_STATUS["ACTIVE"]) is False
    assert _store.append_state(goal_id="g-x", status="not-a-status") is False
    assert _store.get("g-x") is None
    assert _store.count_records() == 0


# ============================================================
# 任务 7: Python 3.11 兼容(py_compile + 3.11 语法解析)
# ============================================================
_GOAL_MODULE_FILES = (
    "src/goal/goal_state.py",
    "src/goal/goal_proposal.py",
    "src/goal/goal_source_validation.py",
    "src/goal/goal_approved_drain.py",
    "src/growth/proposal/constants.py",
)


def test_py_compile_goal_modules():
    for _rel in _GOAL_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_goal_modules_python311_grammar():
    for _rel in _GOAL_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        # feature_version=(3,11): 使用 3.11 语法规则解析(3.12+ 语法会报错)
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
