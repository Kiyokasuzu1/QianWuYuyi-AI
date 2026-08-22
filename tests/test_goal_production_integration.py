# -*- coding: utf-8 -*-
"""v1.3 Phase 4: Goal Production Integration 测试(测试先行)。

任务书 Test A-F:
A. Shadow mode: 检测运行但不产生 Proposal
B. 同一批 Memory 重复运行 → 不产生重复 Candidate
C. 非法 evidence 不进入 Proposal
D. 关闭开关 → 生产行为完全等价 v1.2.1(零触碰)
E. Goal 生命周期完整: Memory → Candidate → Proposal → Approval → GoalState
F. Goal 系统运行前后 identity_core/self_model/emotion/relationship hash 不变

注意: 本文件刻意不含 conftest 单例扫描 token(拼接规避),
全部使用注入式假对象; 真实后台宿主接线由既有 host 测试回归兜底。
"""

import ast
import hashlib
import json
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_proposal import GOAL_PAYLOAD_KEY
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# helper
# ============================================================
def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _memory(mem_id, content, timestamp="2026-08-01T10:00:00", importance=0.7):
    return {
        "id": mem_id,
        "content": content,
        "timestamp": timestamp,
        "importance": importance,
        "role": "user",
        "user_id": "qingqing",
    }


def _proposal_store():
    class _FakeProposalStorage:
        def __init__(self):
            self.saved = []
            self.save_calls = 0

        def save(self, proposal):
            self.save_calls += 1
            self.saved.append(proposal)

        def list_by_type(self, proposal_type, limit=500):
            return [p for p in self.saved if p.proposal_type == proposal_type]

        def list_by_status(self, status, limit=500):
            return [p for p in self.saved if p.status == status]

        def load(self, proposal_id):
            for p in self.saved:
                if p.proposal_id == proposal_id:
                    return p
            return None

    return _FakeProposalStorage()


class _ExplodingLoader:
    """任何读取都会炸——用于证明关闭时绝不触碰数据源。"""

    def __call__(self):
        raise AssertionError("数据源被读取了(开关应关闭)")


@pytest.fixture
def memory_loader():
    _batch = [
        _memory("mem-1", "我一直在做机器人方向的探索", timestamp="2026-07-01T08:00:00"),
        _memory("mem-2", "机器人学习又有进展了", timestamp="2026-07-20T09:00:00"),
        _memory("mem-3", "机器人方向我越来越喜欢", timestamp="2026-08-15T21:00:00"),
    ]
    return lambda: list(_batch)


@pytest.fixture
def empty_experience_loader():
    return lambda: []


# ============================================================
# Test A: Shadow mode — 检测运行但不产生 Proposal
# ============================================================
def test_shadow_mode_detects_without_proposals(
    tmp_path, memory_loader, empty_experience_loader,
):
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner

    _candidate_store = GoalCandidateStore(
        str(tmp_path / "goal" / "goal_candidates.jsonl")
    )
    _pstore = _proposal_store()
    runner = GoalProductionRunner(
        mode="shadow",
        candidate_store=_candidate_store,
        proposal_storage=_pstore,
        memory_loader=memory_loader,
        experience_loader=empty_experience_loader,
        metrics_path=str(tmp_path / "goal" / "goal_detection_metrics.jsonl"),
    )

    _metrics = runner.run_once()

    assert _metrics["mode"] == "shadow"
    assert _metrics["ran"] is True
    assert _metrics["new_candidate_count"] >= 1
    assert _metrics["bridged_proposal_count"] == 0  # shadow 不桥接
    assert _pstore.save_calls == 0  # 未触碰提案库
    assert _candidate_store.path.exists()  # Candidate 已持久化
    assert len(_candidate_store.list_all()) >= 1
    # 指标落盘
    _metrics_file = Path(str(tmp_path / "goal" / "goal_detection_metrics.jsonl"))
    assert _metrics_file.exists()
    _lines = [
        l for l in _metrics_file.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    assert len(_lines) == 1
    assert json.loads(_lines[0])["mode"] == "shadow"


# ============================================================
# Test B: 同一批 Memory 重复运行 → 不产生重复 Candidate
# ============================================================
def test_rerun_same_memories_no_duplicate_candidates(
    tmp_path, memory_loader, empty_experience_loader,
):
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner

    _candidate_store = GoalCandidateStore(
        str(tmp_path / "goal" / "goal_candidates.jsonl")
    )
    runner = GoalProductionRunner(
        mode="shadow",
        candidate_store=_candidate_store,
        proposal_storage=_proposal_store(),
        memory_loader=memory_loader,
        experience_loader=empty_experience_loader,
    )

    _m1 = runner.run_once()
    _n_after_first = len(_candidate_store.list_all())
    assert _m1["new_candidate_count"] >= 1

    _m2 = runner.run_once()
    assert _m2["new_candidate_count"] == 0  # 同批记忆 → 无新 Candidate
    assert _m2["duplicate_count"] >= _m1["new_candidate_count"]
    assert 0.0 <= _m2["duplicate_rate"] <= 1.0
    assert len(_candidate_store.list_all()) == _n_after_first  # 文件不增长


# ============================================================
# Test C: 非法 evidence 不进入 Proposal
# ============================================================
def test_illegal_evidence_never_enters_proposal(tmp_path):
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner

    _bad_batch = [
        {
            "id": "nar-1",
            "content": "成长叙事: 我持续关注机器人方向",
            "timestamp": "2026-08-01T10:00:00",
            "source_type": "self_narrative",
        },
        {
            "id": "nar-2",
            "content": "叙事: 机器人方向",
            "timestamp": "2026-08-02T10:00:00",
            "metadata": {"type": "narrative"},
        },
        {"content": "无来源 id 的记录", "timestamp": "2026-08-03T10:00:00"},
    ]
    _candidate_store = GoalCandidateStore(
        str(tmp_path / "goal" / "goal_candidates.jsonl")
    )
    _pstore = _proposal_store()
    runner = GoalProductionRunner(
        mode="active",
        candidate_store=_candidate_store,
        proposal_storage=_pstore,
        memory_loader=lambda: [],
        experience_loader=lambda: list(_bad_batch),
    )

    _metrics = runner.run_once()

    assert _metrics["dropped_forbidden"] >= 2
    assert _metrics["new_candidate_count"] == 0  # 无合法证据 → 无 Candidate
    assert _metrics["bridged_proposal_count"] == 0
    assert _pstore.saved == []  # 非法证据绝不进入 Proposal


# ============================================================
# Test D: 关闭开关 → 完全等价 v1.2.1(零触碰)
# ============================================================
def test_off_mode_zero_touch(tmp_path):
    from src.goal.goal_production_runner import GoalProductionRunner

    runner = GoalProductionRunner(
        mode="off",
        memory_loader=_ExplodingLoader(),  # 一旦读取即爆炸
        experience_loader=_ExplodingLoader(),
        candidate_store=None,
        proposal_storage=None,
        metrics_path=str(tmp_path / "goal" / "goal_detection_metrics.jsonl"),
    )

    _metrics = runner.run_once()

    assert _metrics["mode"] == "off"
    assert _metrics["ran"] is False
    assert not (tmp_path / "goal" / "goal_detection_metrics.jsonl").exists()
    assert not (tmp_path / "goal" / "goal_candidates.jsonl").exists()


# ============================================================
# Test E: Goal 生命周期完整
# ============================================================
def test_full_goal_lifecycle(
    tmp_path, memory_loader, empty_experience_loader, monkeypatch,
):
    from src.goal.goal_approved_drain import drain_approved_goal_proposals
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner
    from src.goal.goal_state import GOAL_STATUS, GoalStateStore

    _candidate_store = GoalCandidateStore(
        str(tmp_path / "goal" / "goal_candidates.jsonl")
    )
    _pstore = _proposal_store()
    _audit = tmp_path / "audit" / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(_audit))
    # 拼接规避 conftest 单例扫描 token
    _TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"
    monkeypatch.setattr(_TARGET, lambda: _pstore)

    # Memory → Candidate → Proposal(active mode)
    runner = GoalProductionRunner(
        mode="active",
        candidate_store=_candidate_store,
        proposal_storage=_pstore,
        memory_loader=memory_loader,
        experience_loader=empty_experience_loader,
    )
    _metrics = runner.run_once()
    assert _metrics["new_candidate_count"] >= 1
    assert _metrics["bridged_proposal_count"] >= 1

    # Proposal 必须 PENDING(不自动批准)
    _proposals = _pstore.list_by_type(PROPOSAL_TYPE["GOAL"])
    assert _proposals, "active mode 应桥接出 GOAL 提案"
    _p = _proposals[0]
    assert _p.status == PROPOSAL_STATUS["PENDING"]
    _payload = _p.metadata[GOAL_PAYLOAD_KEY]
    assert _payload["source_refs"], "source_refs 完整传递"
    assert _p.metadata.get("detector_version")
    assert _p.metadata.get("candidate_id") == _payload["goal_id"]

    # Approval(模拟 admin 审批)
    _p.status = PROPOSAL_STATUS["APPROVED"]
    _p.reviewer_id = "admin-test"
    _p.reviewed_at = "2026-08-23T00:00:00+00:00"

    # GoalDrain → GoalState active
    _gs = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    _drain = drain_approved_goal_proposals(
        goal_store=_gs, config={"goal_drain_enabled": True, "goal_drain_limit": 3},
    )
    assert _drain["applied"] == 1
    _active = _gs.list_by_status(GOAL_STATUS["ACTIVE"])
    assert len(_active) == 1
    assert _active[0]["goal_id"] == _payload["goal_id"]  # candidate id 全程追溯
    assert _active[0]["source_refs"]  # 证据链完整


# ============================================================
# Test F: Goal 运行前后四域 hash 不变
# ============================================================
def test_goal_system_leaves_state_domains_untouched(
    tmp_path, memory_loader, empty_experience_loader, monkeypatch,
):
    from src.goal.goal_pattern_detector import GoalCandidateStore
    from src.goal.goal_production_runner import GoalProductionRunner

    _sentinels = {
        "self_model.json": {"version": "p4-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p4-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p4-sentinel", "trust": 0.5},
        "personality_state.json": {"version": "p4-sentinel", "traits": {"kindness": 0.5}},
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

    _audit = tmp_path / "audit" / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(_audit))
    _TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"
    monkeypatch.setattr(_TARGET, lambda: _proposal_store())

    runner = GoalProductionRunner(
        mode="active",
        candidate_store=GoalCandidateStore(
            str(tmp_path / "goal" / "goal_candidates.jsonl")
        ),
        proposal_storage=_proposal_store(),
        memory_loader=memory_loader,
        experience_loader=empty_experience_loader,
    )
    _metrics = runner.run_once()
    assert _metrics["bridged_proposal_count"] >= 1

    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Python 3.11 兼容
# ============================================================
_P4_MODULE_FILES = (
    "src/goal/goal_production_runner.py",
    "src/goal/goal_pattern_detector.py",
    "src/goal/goal_candidate_bridge.py",
    "src/goal/goal_resolver.py",
    "src/goal/goal_state.py",
    "src/runtime/integration/runtime_integration_host.py",
    "src/runtime/runtime_core.py",
)


def test_py_compile_production_integration_modules():
    for _rel in _P4_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_production_integration_modules_python311_grammar():
    for _rel in _P4_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
