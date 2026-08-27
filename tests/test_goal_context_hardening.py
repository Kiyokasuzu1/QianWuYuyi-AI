# -*- coding: utf-8 -*-
"""v1.3 RC Phase 7.1: Goal Context Governance Hardening 测试。

覆盖生产缺陷场景:
1. source_refs=565 开启 goal_context → prompt 正常生成、context 截断、无异常
2. source_refs=1000 → 不崩溃、warning、正常回复路径
3. 正常小型 Goal → 原行为保持
4. goal_context=false → 零行为变化(不读 GoalState)
5. 桥接层/检测器 source_refs 防膨胀
"""

import ast
import os
import py_compile
from pathlib import Path

import pytest

from src.goal.goal_state import GOAL_STATUS, GoalStateStore

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _goal_store(tmp_path, ref_count):
    _store = GoalStateStore(str(tmp_path / "goal" / "goal_state.jsonl"))
    _refs = [
        {"source_type": "memory", "source_id": f"rec_{i:04d}"}
        for i in range(ref_count)
    ]
    _store.append_state(
        goal_id="g-huge",
        status=GOAL_STATUS["ACTIVE"],
        description="关注方向的描述",
        reason="goal: 关注方向的描述",
        source_refs=_refs,
        priority="medium",
        confidence=0.8,
        proposal_id="p-huge",
    )
    return _store


# ============================================================
# 1/2: 巨型 source_refs 截断 + fail-safe
# ============================================================
def test_565_refs_truncated_context(tmp_path):
    from src.goal.goal_resolver import (
        DEFAULT_MAX_GOAL_CONTEXT_CHARS,
        resolve_goal_context_text,
    )

    _store = _goal_store(tmp_path, 565)
    _text = resolve_goal_context_text(goal_store=_store, enabled=True)

    assert _text  # 非空(不触发空回复/fallback)
    assert len(_text) <= DEFAULT_MAX_GOAL_CONTEXT_CHARS + 60  # 预算 + 截断标记
    assert "等 555 条来源" in _text  # 截断后保留总数提示
    assert _text.count("rec_") <= 12  # 只渲染 ≤10 条引用(含 goal_id 中无 rec_)

    # 注入 PromptBuilder 不异常
    from src.response.prompt_builder import PromptBuilder

    _messages = PromptBuilder().build_messages(
        user_message="你好", goal_context=_text,
    )
    assert _messages[0]["role"] == "system"
    assert "关注方向的描述" in _messages[0]["content"]


def test_1000_refs_no_crash(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    _store = _goal_store(tmp_path, 1000)
    _text = resolve_goal_context_text(goal_store=_store, enabled=True)

    assert _text  # 不崩溃、非空
    assert len(_text) < 7000  # 硬预算生效
    assert "等 990 条来源" in _text


def test_resolve_active_goals_carries_total_count(tmp_path):
    from src.goal.goal_resolver import resolve_active_goals

    _store = _goal_store(tmp_path, 565)
    _ctx = resolve_active_goals(goal_store=_store)

    _g = _ctx["active_goals"][0]
    assert _g["evidence_total_count"] == 565  # 总数保留(可追溯)
    assert len(_g["source_refs"]) == 10  # 读取层截断


# ============================================================
# 3: 小型 Goal 原行为保持
# ============================================================
def test_small_goal_unchanged(tmp_path):
    from src.goal.goal_resolver import resolve_goal_context_text

    _store = _goal_store(tmp_path, 2)
    _text = resolve_goal_context_text(goal_store=_store, enabled=True)

    assert "rec_0000" in _text
    assert "rec_0001" in _text  # 全部渲染
    assert "等" not in _text.split("来源:")[1]  # 无截断汇总标记
    assert "已截断" not in _text


# ============================================================
# 4: goal_context=false 零变化
# ============================================================
def test_goal_context_off_zero_change():
    from src.goal.goal_resolver import resolve_goal_context_text

    class _ExplodingStore:
        def list_by_status(self, *a, **k):
            raise AssertionError("GoalState 被读取了(关闭模式)")

    assert resolve_goal_context_text(goal_store=_ExplodingStore(), enabled=False) == ""


# ============================================================
# 5: 桥接层 / 检测器防膨胀
# ============================================================
def test_bridge_caps_source_refs():
    from src.goal.goal_candidate_bridge import (
        BRIDGE_MAX_SOURCE_REFS,
        bridge_candidate_to_proposal,
    )

    _candidate = {
        "id": "gc-cap",
        "description": "关注机器人方向",
        "evidence_refs": [
            {"source_type": "memory", "source_id": f"m-{i}"} for i in range(100)
        ],
        "confidence": 0.7,
        "created_at": "2026-08-23T00:00:00+00:00",
        "detector_version": "goal_pattern_detector.1.0",
        "fingerprint": "fp-cap",
    }
    _proposal = bridge_candidate_to_proposal(_candidate)

    assert _proposal is not None
    from src.goal.goal_proposal import GOAL_PAYLOAD_KEY

    _refs = _proposal.metadata[GOAL_PAYLOAD_KEY]["source_refs"]
    assert len(_refs) == BRIDGE_MAX_SOURCE_REFS  # 100 -> 32 截断
    assert _proposal.metadata[GOAL_PAYLOAD_KEY]["goal_id"] == "gc-cap"


def _same_theme_memories(count):
    return [
        {
            "id": f"mem-{i:03d}",
            "content": f"我持续关注机器人方向探索第{i}次",
            "timestamp": f"2026-08-{1 + i % 28:02d}T10:00:00",
            "importance": 0.7,
        }
        for i in range(count)
    ]


def test_detector_caps_candidate_evidence_refs(tmp_path):
    from src.goal.goal_pattern_detector import (
        MAX_EVIDENCE_REFS_PER_CANDIDATE,
        GoalCandidateStore,
        detect_candidates,
    )

    _cstore = GoalCandidateStore(str(tmp_path / "goal" / "goal_candidates.jsonl"))
    _candidates = detect_candidates(
        memories=_same_theme_memories(40),
        candidate_store=_cstore,
    )

    assert len(_candidates) == 1
    _c = _candidates[0]
    assert len(_c["evidence_refs"]) <= MAX_EVIDENCE_REFS_PER_CANDIDATE
    assert _c["evidence_total_count"] == 40  # 总数保留
    assert _c["fingerprint"]  # 幂等键不变(基于全集)


# ============================================================
# Python 3.11
# ============================================================
_HARDENING_MODULE_FILES = (
    "src/goal/goal_resolver.py",
    "src/goal/goal_candidate_bridge.py",
    "src/goal/goal_pattern_detector.py",
)


def test_py_compile_hardening_modules():
    for _rel in _HARDENING_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_hardening_modules_python311_grammar():
    for _rel in _HARDENING_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
