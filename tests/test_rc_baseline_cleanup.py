# -*- coding: utf-8 -*-
"""v1.3 RC Phase 1.1: Release Baseline Cleanup 验证测试。

覆盖:
- K1 修复契约: build_self_model_governance_proposal(PENDING + 类型 + metadata 形状)
- K1 静态: selfmodel_consumer.py 无 legacy GrowthProposal import
- VERSION: v1.3.0-rc1
- 8 开关冻结复核(类型正确)
- py_compile + Python 3.11 语法
"""

import ast
import os
import py_compile
from pathlib import Path

import yaml

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# K1 修复契约
# ============================================================
def test_build_self_model_governance_proposal_contract():
    from src.growth.proposal.storage import build_self_model_governance_proposal

    _p = build_self_model_governance_proposal(
        source_event_id="ev-k1",
        confidence=0.7,
        reason="consumer_governance",
        self_model_payload={"change_type": "narrative_append", "target": "x"},
        decision_meta={"action": "approve", "growth_level": "context"},
    )

    assert _p is not None
    assert _p.proposal_type == "self_model"
    assert _p.status == "pending"  # 恒 PENDING(不自动批准)
    assert _p.source == "selfmodel_consumer"
    assert _p.source_event_id == "ev-k1"
    assert abs(_p.confidence - 0.7) < 1e-6
    assert _p.reason == "consumer_governance"
    assert _p.metadata["self_model_proposal"]["target"] == "x"
    assert _p.metadata["governance_decision"]["action"] == "approve"
    assert _p.metadata["source"] == "selfmodel_consumer"


def test_selfmodel_consumer_has_no_legacy_import():
    """K1 静态验证: consumer 文件不再直接 import legacy GrowthProposal。"""
    _text = Path(_REPO_ROOT, "src/admin/selfmodel_consumer.py").read_text(
        encoding="utf-8"
    )
    assert "from src.growth.proposal.proposal import GrowthProposal" not in _text
    assert "from src.growth.proposal import GrowthProposal" not in _text
    # 兼容层 producer 已被使用
    assert "build_self_model_governance_proposal" in _text


# ============================================================
# VERSION
# ============================================================
def test_version_file_rc1():
    _text = Path(_REPO_ROOT, "VERSION.txt").read_text(encoding="utf-8")
    assert "Version: v1.3.0-rc1" in _text
    assert "RC FREEZE" in _text


# ============================================================
# 8 开关冻结复核
# ============================================================
def test_8_switches_frozen_off():
    cfg = yaml.safe_load(Path(_REPO_ROOT, "config.yaml").read_text(encoding="utf-8"))
    r = cfg.get("runtime", {})
    expected = {
        "initiative.enabled": (cfg.get("initiative", {}).get("enabled"), False),
        "legacy_decision_dispatch_enabled": (r.get("legacy_decision_dispatch_enabled"), False),
        "initiative_pipeline_mode": (r.get("initiative_pipeline_mode"), "off"),
        "initiative_dispatch_enabled": (r.get("initiative_dispatch_enabled"), False),
        "initiative_observability_enabled": (r.get("initiative_observability_enabled"), False),
        "goal_detection_mode": (r.get("goal_detection_mode"), "off"),
        "goal_context_enabled": (r.get("goal_context_enabled"), False),
        "goal_drain_enabled": (r.get("goal_drain_enabled"), False),
    }
    for _k, (_v, _e) in expected.items():
        assert _v == _e, f"{_k} = {_v!r} (期望 {_e!r})"


# ============================================================
# Python 3.11 兼容(本阶段改动文件)
# ============================================================
_CLEANUP_MODULE_FILES = (
    "src/growth/proposal/storage.py",
    "src/admin/selfmodel_consumer.py",
)


def test_py_compile_cleanup_modules():
    for _rel in _CLEANUP_MODULE_FILES:
        py_compile.compile(os.path.join(_REPO_ROOT, _rel), doraise=True)


def test_cleanup_modules_python311_grammar():
    for _rel in _CLEANUP_MODULE_FILES:
        with open(os.path.join(_REPO_ROOT, _rel), "r", encoding="utf-8") as f:
            _src = f.read()
        ast.parse(_src, filename=_rel, feature_version=(3, 11))
