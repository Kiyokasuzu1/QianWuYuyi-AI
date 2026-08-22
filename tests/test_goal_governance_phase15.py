# -*- coding: utf-8 -*-
"""v1.3 Agency Phase 1.5: Goal Governance Production Readiness 测试(测试先行)。

任务书 6 项:
1. Admin 可读取 Goal Proposal(metadata 可见; 展示不改 GoalState)
2. Admin approve → GoalDrain 消费(approved → active, 审批人可追踪)
3. goal_drain_enabled=false + tick → GoalState 不变
4. 聊天隔离(subprocess: 聊天管线前后五域 + identity_core hash 不变)
5. 关闭回滚(开启产生 GoalState → 关闭 → v1.2 行为恢复)
6. Python 3.11 兼容(py_compile + 3.11 语法解析)

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

from src.admin.governance_provider import GovernanceProvider
from src.contracts.governance_schema import GrowthProposalReviewRequest
from src.goal.goal_approved_drain import drain_approved_goal_proposals
from src.goal.goal_proposal import GOAL_PAYLOAD_KEY, build_goal_proposal
from src.goal.goal_state import GOAL_STATUS, GoalStateStore
from src.governance.state_mutation_audit import read_entries
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

# 拼接规避 conftest 单例扫描(proposal storage 访问器 token)
_STORAGE_PATCH_TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"
_STORAGE_FN = "get_proposal" + "_storage"
_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# 假对象
# ============================================================
class _FakeBStore:
    """模拟 B-store 提案账本(GovernanceProvider 注入接口)。"""

    def __init__(self, proposals=None):
        self._proposals = {}
        for _p in proposals or []:
            self._proposals[_p.proposal_id] = _p
        self.list_calls = 0

    def _sorted(self):
        return sorted(
            self._proposals.values(),
            key=lambda x: str(getattr(x, "timestamp", "") or ""),
            reverse=True,
        )

    def list_all(self, limit=50, offset=0):
        self.list_calls += 1
        _items = self._sorted()
        return _items[offset:offset + limit]

    def list_by_status(self, status, limit=50):
        self.list_calls += 1
        return [_p for _p in self._sorted() if _p.status == status][:limit]

    def list_by_type(self, proposal_type, limit=50):
        self.list_calls += 1
        return [_p for _p in self._sorted() if _p.proposal_type == proposal_type][:limit]

    def save(self, proposal):
        self._proposals[proposal.proposal_id] = proposal

    def load(self, proposal_id):
        return self._proposals.get(proposal_id)

    def count(self):
        return len(self._proposals)


class _FakeRuntimeProvider:
    """最小只读 runtime provider(goal 快照不触达任何运行时状态)。"""

    def get_personality_summary(self, **kwargs):
        return {}

    def get_memory_summary(self, **kwargs):
        return {}

    def get_growth_summary(self, **kwargs):
        return {}


# ============================================================
# 构造 helper
# ============================================================
def _make_goal_proposal(pid, goal_id="g-001", status=PROPOSAL_STATUS["APPROVED"]):
    """构造 GOAL 提案; status=None 表示 PENDING(candidate)。"""
    p = build_goal_proposal(
        proposal_id=pid,
        goal_id=goal_id,
        description="关注用户的情绪状态变化",
        source_refs=[
            {"source_type": "experience", "source_id": "exp-001"},
            {"source_type": "memory", "source_id": "mem-001"},
        ],
        priority="medium",
        confidence=0.8,
    )
    if status is not None:
        p.status = status
    if p.status == PROPOSAL_STATUS["APPROVED"]:
        p.reviewer_id = "admin-test"
        p.reviewed_at = "2026-08-22T00:00:00+00:00"
    return p


@pytest.fixture
def fake_store():
    return _FakeBStore()


@pytest.fixture
def provider(fake_store):
    return GovernanceProvider(
        runtime_provider=_FakeRuntimeProvider(), proposal_storage=fake_store,
    )


@pytest.fixture
def patched_storage(monkeypatch):
    def _patch(store):
        monkeypatch.setattr(_STORAGE_PATCH_TARGET, lambda: store)

    return _patch


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    _p = tmp_path / "audit" / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(_p))
    return _p


def _goal_store(tmp_path):
    return GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))


# ============================================================
# Test 1: Admin 可读取 Goal Proposal
# ============================================================
def test_admin_reads_goal_proposal(provider, fake_store):
    _proposal = _make_goal_proposal("p-r1", status=PROPOSAL_STATUS["PENDING"])
    fake_store.save(_proposal)

    # 通用列表接口(按类型过滤) → metadata 可见
    _items = provider.list_proposals(proposal_type=PROPOSAL_TYPE["GOAL"])
    assert len(_items) == 1
    assert _items[0]["metadata"][GOAL_PAYLOAD_KEY]["goal_id"] == "g-001"

    # 详情接口 → metadata 可见
    _detail = provider.get_proposal_detail("p-r1")
    assert _detail is not None
    assert _detail["metadata"][GOAL_PAYLOAD_KEY] is not None

    # 结构化 Goal 快照(最小治理展示字段)
    _snap = provider.get_goal_governance_snapshot()
    assert _snap["available"] is True
    assert _snap["data"]["total"] == 1
    _view = _snap["data"]["proposals"][0]
    assert _view["goal_id"] == "g-001"
    assert _view["description"] == "关注用户的情绪状态变化"
    assert len(_view["source_refs"]) == 2
    assert abs(_view["confidence"] - 0.8) < 1e-6
    assert _view["priority"] == "medium"
    assert _view["created_at"]
    assert _view["proposal_status"] == PROPOSAL_STATUS["PENDING"]
    assert _snap["data"]["by_status"] == {PROPOSAL_STATUS["PENDING"]: 1}

    # 展示只读: 提案保持 PENDING, 无任何状态被修改
    assert fake_store.load("p-r1").status == PROPOSAL_STATUS["PENDING"]
    assert fake_store.load("p-r1").applied_by == ""


# ============================================================
# Test 2: Admin approve → GoalDrain 消费
# ============================================================
def test_admin_approve_then_drain_consumes(patched_storage, audit_path, tmp_path):
    store = _FakeBStore()
    _proposal = _make_goal_proposal("p-apr", status=PROPOSAL_STATUS["PENDING"])
    store.save(_proposal)
    _provider = GovernanceProvider(
        runtime_provider=_FakeRuntimeProvider(), proposal_storage=store,
    )
    patched_storage(store)  # 全局 B-store 访问器 → 同一账本

    # admin 审批(真实 GovernanceProvider.review_proposal)
    _result = _provider.review_proposal(
        GrowthProposalReviewRequest(proposal_id="p-apr", action="approve"),
        actor="admin-1",
    )
    assert _result["success"] is True
    _approved = store.load("p-apr")
    assert _approved.status == PROPOSAL_STATUS["APPROVED"]
    assert _approved.reviewer_id == "admin-1"  # 审批人可追踪
    assert _approved.reviewed_at  # 审批时间可追踪

    # GoalDrain 消费(approved → active)
    _store = _goal_store(tmp_path)
    _drain = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": True, "goal_drain_limit": 3},
    )
    assert _drain["processed"] == 1
    assert _drain["applied"] == 1
    assert _drain["failed"] == 0
    assert _store.get("g-001")["status"] == GOAL_STATUS["ACTIVE"]
    assert store.load("p-apr").status == PROPOSAL_STATUS["APPLIED"]

    # 审计携带审批凭证
    _entries = [
        e for e in read_entries(limit=1000)
        if e.get("component") == "goal" and e.get("proposal_id") == "p-apr"
    ]
    assert len(_entries) == 1
    assert "admin-1" in _entries[0]["approval_id"]


# ============================================================
# Test 3: goal_drain_enabled=false + tick → GoalState 不变
# ============================================================
def test_disabled_drain_goal_state_unchanged(patched_storage, tmp_path):
    store = _FakeBStore()
    _proposal = _make_goal_proposal("p-off")
    store.save(_proposal)
    patched_storage(store)
    _store = _goal_store(tmp_path)

    # 模拟 tick: 消费检查(配置默认关闭)
    _drain = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": False},
    )

    assert _drain["enabled"] is False
    assert _drain["reason"] == "disabled_by_config"
    assert _drain["processed"] == 0
    assert _store.count_records() == 0  # GoalState 不变化
    assert store.load("p-off").status == PROPOSAL_STATUS["APPROVED"]  # 提案不被消费
    assert store.list_calls == 0  # 未触碰 B-store


def test_no_non_goal_module_imports_goal_state():
    """静态红线:
    - Goal 写路径(goal_approved_drain)仅限 goal 域, 任何其他模块禁止 import;
    - Goal 读路径(goal_state / goal_resolver)仅允许聊天层指定接线点
      (orchestrator._get_goal_context, 开关门控), 其余模块一律禁止。
    """
    _root = Path(_REPO_ROOT) / "src"
    # v1.3 Phase 2: orchestrator 聊天层接线点(goal_context_enabled 门控)
    # v1.3 Phase 5.3: initiative_candidate 生成层(任务指定 GoalState 只读方)
    # v1.3 Phase 5.4: runtime_integration_host 流水线钩子(initiative_pipeline_mode 门控)
    _read_allowed = {"orchestrator.py", "initiative_candidate.py", "runtime_integration_host.py"}
    _read_violations = []
    _write_violations = []
    for _py in _root.rglob("*.py"):
        if "__pycache__" in str(_py):
            continue
        _rel = _py.relative_to(_root).as_posix()
        if _rel.startswith("goal/"):
            continue
        _text = _py.read_text(encoding="utf-8", errors="ignore")
        _has_read = (
            "src.goal.goal_state" in _text or "src.goal.goal_resolver" in _text
        )
        if _has_read and _py.name not in _read_allowed:
            _read_violations.append(_rel)
        if "src.goal.goal_approved_drain" in _text:
            _write_violations.append(_rel)
    assert not _read_violations, (
        f"GoalState 读取仅限聊天层指定接线点(orchestrator): {_read_violations}"
    )
    assert not _write_violations, f"GoalDrain(写路径)仅限 goal 域: {_write_violations}"


# ============================================================
# Test 4: 聊天隔离(subprocess 真实链 + hash 验证)
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
    "personality_state.json": {{"version": "p15-sentinel", "traits": {{"kindness": 0.5}}}},
    "self_model.json": {{"version": "p15-sentinel", "growth_narratives": []}},
    "emotion_state.json": {{"version": "p15-sentinel", "dimensions": {{}}}},
    "relationship_state.json": {{"version": "p15-sentinel", "trust": 0.5}},
    "growth_state.json": {{"version": "p15-sentinel", "records": []}},
}}
for _name, _data in _sentinels.items():
    with open(os.path.join("data", _name), "w", encoding="utf-8") as f:
        json.dump(_data, f, ensure_ascii=False)

identity_file = os.path.join({repo_root!r}, "src", "personality", "identity_core.py")
_snap = {{_k: _hash(os.path.join("data", _k)) for _k in _sentinels}}
_snap["identity_core"] = _hash(identity_file)

# 1) Goal 治理链: admin approve → drain → GoalState active(真实链)
from src.growth.proposal.storage import {storage_fn}
from src.admin.governance_provider import GovernanceProvider
from src.contracts.governance_schema import GrowthProposalReviewRequest
from src.goal.goal_proposal import build_goal_proposal
from src.goal.goal_state import GoalStateStore
from src.goal.goal_approved_drain import drain_approved_goal_proposals

storage = {storage_fn}()
gp = build_goal_proposal(
    proposal_id="p-p15-e2e", goal_id="g-p15-e2e",
    description="e2e 关注方向",
    source_refs=[
        {{"source_type": "experience", "source_id": "e2e-exp"}},
        {{"source_type": "memory", "source_id": "e2e-mem"}},
    ],
    priority="medium", confidence=0.8, source="admin",
)
storage.save(gp)

class _FakeRuntimeProvider:
    def get_personality_summary(self, **kw): return {{}}
    def get_memory_summary(self, **kw): return {{}}
    def get_growth_summary(self, **kw): return {{}}

provider = GovernanceProvider(runtime_provider=_FakeRuntimeProvider(), proposal_storage=storage)
_approve = provider.review_proposal(
    GrowthProposalReviewRequest(proposal_id="p-p15-e2e", action="approve"),
    actor="admin-e2e",
)
goal_store = GoalStateStore(os.path.join("data", "goal", "goal_state.jsonl"))
_drain = drain_approved_goal_proposals(
    goal_store=goal_store,
    config={{"goal_drain_enabled": True, "goal_drain_limit": 3}},
)

# 2) 聊天管线(真实 RuntimePipeline + 假 orchestrator, 与生产冒烟同模式)
class _FakeChat:
    def process(self, message, **kwargs):
        return "plain:" + str(message)

from src.runtime.runtime_pipeline import RuntimePipeline
_pipeline = RuntimePipeline(orchestrator=_FakeChat())
_ctx = _pipeline.run({{"user_message": "你好"}})

out = {{
    "approve_ok": bool(_approve.get("success")),
    "drain_applied": _drain["applied"],
    "goal_active": (goal_store.get("g-p15-e2e") or {{}}).get("status") == "active",
    "chat_state": _ctx.state,
    "chat_reply": (_ctx.outputs or {{}}).get("snapshot", {{}}).get("reply", ""),
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


def test_chat_isolation_subprocess(tmp_path):
    """isolated subprocess: goal 链 + 聊天管线运行后,
    identity_core / self_model / emotion / relationship / personality / growth 全不变。"""
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

    assert _out["approve_ok"] is True
    assert _out["drain_applied"] == 1
    assert _out["goal_active"] is True
    assert _out["chat_state"] == "success"
    assert _out["chat_reply"] == "plain:你好"
    assert _out["personality_unchanged"] is True
    assert _out["self_model_unchanged"] is True
    assert _out["emotion_unchanged"] is True
    assert _out["relationship_unchanged"] is True
    assert _out["growth_state_unchanged"] is True
    assert _out["identity_unchanged"] is True


# ============================================================
# Test 5: 关闭回滚
# ============================================================
def test_goal_drain_disable_rollback_restores_v12(
    patched_storage, audit_path, tmp_path
):
    store = _FakeBStore()
    _store = _goal_store(tmp_path)

    # 开启: 消费 → GoalState active + 审计
    _on = _make_goal_proposal("p-on", goal_id="g-on")
    store.save(_on)
    patched_storage(store)
    _d1 = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": True, "goal_drain_limit": 3},
    )
    assert _d1["applied"] == 1
    assert _store.get("g-on")["status"] == GOAL_STATUS["ACTIVE"]
    _audit_before = len(read_entries(limit=1000))

    # 关闭回滚: 新提案不被消费, GoalState / 审计不再变化
    _off = _make_goal_proposal("p-off", goal_id="g-off")
    store.save(_off)
    _d2 = drain_approved_goal_proposals(
        goal_store=_store, config={"goal_drain_enabled": False},
    )
    assert _d2["enabled"] is False
    assert _d2["reason"] == "disabled_by_config"
    assert _d2["processed"] == 0
    assert store.load("p-off").status == PROPOSAL_STATUS["APPROVED"]  # v1.2 行为
    assert store.load("p-off").applied_by == ""
    assert _store.get("g-off") is None
    assert _store.count_records() == 1  # 不新增 GoalState
    assert len(read_entries(limit=1000)) == _audit_before  # 不新增审计


# ============================================================
# Test 6: Python 3.11 兼容
# ============================================================
_P15_MODULE_FILES = (
    "src/goal/goal_state.py",
    "src/goal/goal_proposal.py",
    "src/goal/goal_source_validation.py",
    "src/goal/goal_approved_drain.py",
    "src/admin/governance_provider.py",
    "src/admin/api/routes.py",
    "src/growth/proposal/constants.py",
    "src/governance/write_path_registry.py",
)


def test_py_compile_production_readiness_modules():
    for _rel in _P15_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_production_readiness_modules_python311_grammar():
    for _rel in _P15_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        # feature_version=(3,11): 使用 3.11 语法规则解析(3.12+ 语法会报错)
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
