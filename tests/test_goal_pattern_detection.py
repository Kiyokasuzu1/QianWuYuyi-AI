# -*- coding: utf-8 -*-
"""v1.3 Phase 3: Evidence Pattern Detection 测试(测试先行)。

任务书 Test A-G:
A. 单条 Memory → 不产生 Candidate
B. 两条以上独立来源 Memory → 允许产生 Candidate
C. 相同主题但不同时间窗口 → 合并为一个 Pattern(一个 Candidate)
D. SelfNarrative 作为输入 → 拒绝
E. LLM 不参与 Candidate 判断(关闭 LLM 仍可运行 + 无 LLM import)
F. Candidate 无法直接进入 GoalState(必须经过 Proposal, PENDING)
G. Candidate 删除不影响 identity_core / self_model / emotion / relationship

注意: 本文件刻意不含 conftest 单例扫描 token(拼接规避),
全部使用普通 dict 假数据 + 注入式假账本。
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


def _experience(exp_id, content, timestamp="2026-08-02T10:00:00"):
    return {
        "id": exp_id,
        "content": content,
        "timestamp": timestamp,
        "metadata": {"type": "runtime_experience"},
    }


class _FakeCandidateStore:
    """注入式 candidate 账本(append-only 语义的最小接口)。"""

    def __init__(self):
        self.records = []
        self.append_calls = 0

    def append_candidate(self, candidate):
        self.append_calls += 1
        self.records.append(dict(candidate))
        return True

    def fingerprint_exists(self, fingerprint):
        return any(r.get("fingerprint") == fingerprint for r in self.records)

    def list_all(self):
        return [dict(r) for r in self.records]


class _FakeBStore:
    def __init__(self):
        self.saved = []

    def save(self, proposal):
        self.saved.append(proposal)
        return proposal


@pytest.fixture
def fake_candidate_store():
    return _FakeCandidateStore()


# ============================================================
# Test A: 单条 Memory → 不产生 Candidate
# ============================================================
def test_single_memory_no_candidate(tmp_path, fake_candidate_store):
    from src.goal.goal_pattern_detector import detect_candidates

    memories = [_memory("mem-1", "我一直在做机器人方向的探索")]
    result = detect_candidates(
        memories=memories,
        candidate_store=fake_candidate_store,
    )
    assert result == []
    assert fake_candidate_store.append_calls == 0


# ============================================================
# Test B: 两条以上独立来源 → 允许产生 Candidate
# ============================================================
def test_two_independent_memories_produce_candidate(tmp_path, fake_candidate_store):
    from src.goal.goal_pattern_detector import detect_candidates

    memories = [
        _memory("mem-1", "我一直在做机器人方向的探索"),
        _memory("mem-2", "我很想做一个机器人，一直在学机器人技术"),
    ]
    result = detect_candidates(
        memories=memories,
        candidate_store=fake_candidate_store,
    )
    assert len(result) == 1
    _c = result[0]
    assert _c["id"]
    assert _c["description"]
    assert _c["created_at"]
    assert _c["detector_version"]
    assert 0.0 <= _c["confidence"] <= 1.0
    # 独立来源都进入 evidence_refs
    _src_ids = {r["source_id"] for r in _c["evidence_refs"]}
    assert _src_ids == {"mem-1", "mem-2"}
    # 已 append 持久化(append-only)
    assert fake_candidate_store.append_calls == 1
    assert len(fake_candidate_store.list_all()) == 1


def test_experience_records_can_form_candidate(tmp_path, fake_candidate_store):
    from src.goal.goal_pattern_detector import detect_candidates

    experiences = [
        _experience("exp-1", "羽依发现自己持续关注用户情绪状态"),
        _experience("exp-2", "羽依又多次注意到用户情绪波动"),
    ]
    result = detect_candidates(
        experience_records=experiences,
        candidate_store=fake_candidate_store,
    )
    assert len(result) == 1
    _types = {r["source_type"] for r in result[0]["evidence_refs"]}
    assert _types == {"experience"}


# ============================================================
# Test C: 相同主题不同时间窗口 → 合并 Pattern
# ============================================================
def test_same_theme_across_time_windows_merges(tmp_path, fake_candidate_store):
    from src.goal.goal_pattern_detector import detect_candidates

    memories = [
        _memory("mem-a", "我在研究机器人", timestamp="2026-07-01T08:00:00"),
        _memory("mem-b", "机器人学习又有进展了", timestamp="2026-07-20T09:00:00"),
        _memory("mem-c", "机器人方向我越来越喜欢", timestamp="2026-08-15T21:00:00"),
    ]
    result = detect_candidates(
        memories=memories,
        candidate_store=fake_candidate_store,
    )
    # 三个时间窗口、同一主题 → 合并为一个 Candidate, 而非三个
    assert len(result) == 1
    _refs = result[0]["evidence_refs"]
    assert len(_refs) == 3
    _timestamps = sorted(r.get("timestamp", "") for r in _refs)
    assert _timestamps[0] < _timestamps[-1]


# ============================================================
# Test D: SelfNarrative 作为输入 → 拒绝
# ============================================================
def test_self_narrative_input_rejected(tmp_path, fake_candidate_store):
    from src.goal.goal_pattern_detector import detect_candidates

    narrative_records = [
        {
            "id": "nar-1",
            "content": "我的成长叙事表明我持续关注机器人方向",
            "timestamp": "2026-08-01T10:00:00",
            "source_type": "self_narrative",
        },
        {
            "id": "nar-2",
            "content": "叙事快照: 关注机器人方向",
            "timestamp": "2026-08-02T10:00:00",
            "source_type": "narrative",
        },
    ]
    result = detect_candidates(
        experience_records=narrative_records,
        candidate_store=fake_candidate_store,
    )
    assert result == []
    assert fake_candidate_store.append_calls == 0

    # 混合输入: narrative 被剔除, 仅合法证据计数(单条合法 → 仍无 candidate)
    mixed = [
        narrative_records[0],
        _memory("mem-1", "机器人方向探索"),
    ]
    result2 = detect_candidates(
        experience_records=mixed,
        candidate_store=fake_candidate_store,
    )
    assert result2 == []
    assert fake_candidate_store.append_calls == 0


# ============================================================
# Test E: LLM 不参与(无 LLM import + 关闭 LLM 仍可运行)
# ============================================================
def test_detector_has_no_llm_imports():
    """无 LLM 依赖: 只检查 import 语句(模块文本中的红线圈注说明除外)。"""
    _forbidden = ("llm", "openai", "deepseek")
    for _rel in (
        "src/goal/goal_pattern_detector.py",
        "src/goal/goal_candidate_bridge.py",
    ):
        _tree = ast.parse(
            Path(_REPO_ROOT, _rel).read_text(encoding="utf-8"), filename=_rel,
        )
        for _node in ast.walk(_tree):
            _module = ""
            if isinstance(_node, ast.ImportFrom):
                _module = _node.module or ""
            elif isinstance(_node, ast.Import):
                _module = " ".join(_a.name or "" for _a in _node.names)
            if not _module:
                continue
            for _tok in _forbidden:
                assert _tok not in _module.lower(), (
                    f"{_rel} import 含 LLM 依赖: {_module}"
                )


def test_detector_runs_with_llm_closed(tmp_path, fake_candidate_store, monkeypatch):
    from src.goal.goal_pattern_detector import detect_candidates

    # 模拟 LLM 关闭: 没有任何 LLM 客户端可用, detector 也必须正常运行
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    memories = [
        _memory("mem-1", "持续关注音乐创作"),
        _memory("mem-2", "音乐创作是我一直喜欢的方向"),
    ]
    result = detect_candidates(
        memories=memories,
        candidate_store=fake_candidate_store,
    )
    assert len(result) == 1  # LLM 关闭下照常产出


# ============================================================
# Test F: Candidate 无法直接进入 GoalState, 必须经 Proposal(PENDING)
# ============================================================
def test_candidate_cannot_enter_goal_state_directly(tmp_path):
    from src.goal.goal_candidate_bridge import bridge_candidate_to_proposal
    from src.goal.goal_state import GoalStateStore

    candidate = {
        "id": "gc-test-0001",
        "description": "关注机器人方向",
        "evidence_refs": [
            {"source_type": "memory", "source_id": "mem-1"},
            {"source_type": "memory", "source_id": "mem-2"},
        ],
        "confidence": 0.65,
        "created_at": "2026-08-23T00:00:00+00:00",
        "detector_version": "goal_pattern_detector.1.0",
        "fingerprint": "fp-test",
    }
    bstore = _FakeBStore()
    proposal = bridge_candidate_to_proposal(candidate, storage=bstore)

    assert proposal is not None
    # 桥接产物: PENDING 提案(不是 active, 不是 GoalState)
    assert proposal.status == PROPOSAL_STATUS["PENDING"]
    assert proposal.proposal_type == PROPOSAL_TYPE["GOAL"]
    _payload = proposal.metadata[GOAL_PAYLOAD_KEY]
    assert _payload["goal_id"] == "gc-test-0001"  # 追溯链: candidate id → goal id
    assert _payload["description"] == "关注机器人方向"
    assert len(_payload["source_refs"]) == 2
    assert "goal_pattern_detector" in str(proposal.metadata.get("detector_version", ""))
    assert abs(_payload["confidence"] - 0.65) < 1e-6

    # GoalState 未被触碰(桥接不创建任何 goal_state 文件)
    _gs = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    assert _gs.count_records() == 0
    assert not (tmp_path / "goal" / "goal_state.jsonl").exists()


def test_bridge_rejects_candidate_without_valid_sources(tmp_path):
    from src.goal.goal_candidate_bridge import bridge_candidate_to_proposal

    bad_candidate = {
        "id": "gc-bad",
        "description": "无来源 candidate",
        "evidence_refs": [],
        "confidence": 0.5,
        "created_at": "2026-08-23T00:00:00+00:00",
        "detector_version": "goal_pattern_detector.1.0",
        "fingerprint": "fp-bad",
    }
    bstore = _FakeBStore()
    assert bridge_candidate_to_proposal(bad_candidate, storage=bstore) is None
    assert bstore.saved == []


# ============================================================
# Test G: Candidate 删除不影响 identity_core/self_model/emotion/relationship
# ============================================================
def test_candidate_deletion_leaves_state_domains_untouched(tmp_path):
    from src.goal.goal_pattern_detector import (
        GoalCandidateStore,
        detect_candidates,
    )

    # 哨兵状态文件(模拟四域落盘现场)
    _sentinels = {
        "self_model.json": {"version": "p3-sentinel", "growth_narratives": []},
        "emotion_state.json": {"version": "p3-sentinel", "dimensions": {}},
        "relationship_state.json": {"version": "p3-sentinel", "trust": 0.5},
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

    # 检测 + 持久化 Candidate + 删除候选文件
    _store = GoalCandidateStore(str(tmp_path / "goal" / "goal_candidates.jsonl"))
    _result = detect_candidates(
        memories=[
            _memory("mem-1", "持续关注机器人方向"),
            _memory("mem-2", "机器人探索又进一步"),
        ],
        candidate_store=_store,
    )
    assert len(_result) == 1
    assert _store.path.exists()
    _store.path.unlink()  # 删除 Candidate 文件

    # 四域 + identity_core 完全不受影响
    for _k in _sentinels:
        assert _hash_bytes((_data_dir / _k).read_bytes()) == _snap[_k], _k
    assert _hash_bytes(_identity_file.read_bytes()) == _snap["identity_core"]


# ============================================================
# Python 3.11 兼容
# ============================================================
_P3_MODULE_FILES = (
    "src/goal/goal_pattern_detector.py",
    "src/goal/goal_candidate_bridge.py",
    "src/goal/goal_proposal.py",
    "src/goal/goal_resolver.py",
    "src/goal/goal_state.py",
    "src/goal/goal_approved_drain.py",
)


def test_py_compile_pattern_detection_modules():
    for _rel in _P3_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        py_compile.compile(_path, doraise=True)


def test_pattern_detection_modules_python311_grammar():
    for _rel in _P3_MODULE_FILES:
        _path = os.path.join(_REPO_ROOT, _rel)
        with open(_path, "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
