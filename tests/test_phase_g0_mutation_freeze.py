# -*- coding: utf-8 -*-
"""
Phase G-0 Governance Freeze 验收测试。

验证:
1. 所有已知 mutation 写入口: 位于 LEGAL_WRITE_PATHS 或位于 deprecated registry。
2. identity_core: 无任何写入口。
3. 每个合法写入口: 有 proposal 来源声明 / approval 声明 / audit 声明。
4. 不修改任何已有测试(本文件为纯新增, 不触碰既有模块行为)。

注意(conftest 单例陷阱): 本文件只通过字符串路径/符号引用写入口,
不 import 域模块、不调用域单例。
"""

from __future__ import annotations

import json
import re
import warnings
from pathlib import Path

import pytest

from src.governance.write_path_registry import (
    DEPRECATED_WRITE_PATHS,
    LEGAL_WRITE_PATHS,
    is_deprecated_write,
    is_legal_write,
    warn_deprecated_once,
)
from src.governance.state_mutation_audit import (
    DEFAULT_AUDIT_PATH,
    SCHEMA_VERSION,
    append_entry,
    read_entries,
    record_state_mutation,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# ------------------------------------------------------------
# Phase G 前置架构审查取证的全部 mutation 写入口清单
# (文件相对路径, writer 符号)
# ------------------------------------------------------------
KNOWN_MUTATION_SITES = [
    # --- 合法写入口 ---
    ("src/personality/personality_state.py", "PersonalityState.apply_evolution"),
    ("src/personality/personality_evolution_pipeline.py", "PersonalityEvolutionPipeline.apply_approved_to_state"),
    ("src/personality/trait_state_updater.py", "TraitStateUpdater.apply"),
    ("src/runtime/trait_rebuilder.py", "TraitRebuilder.inject_trait_states"),
    ("src/emotion/emotion_updater.py", "EmotionUpdater.apply"),
    ("src/emotion/emotion_manager.py", "EmotionManager._apply_accepted_emotion"),
    ("src/personality/self_model_store.py", "SelfModelStore.apply_change_proposal"),
    ("src/growth/self_model_approved_drain.py", "SelfModelApprovedDrain 应用"),
    ("src/personality/self_model_updater.py", "SelfModelUpdater.apply_proposal"),
    ("src/emotion/emotion_memory_weight.py", "EmotionMemoryWeightBridge.apply_to_memory"),
    # --- 已标记 deprecated 的写路径 ---
    ("src/personality/personality_resolver.py", "PersonalityResolver.resolve 直写分支"),
    ("src/emotion/emotion_manager.py", "EmotionManager.process_event legacy 分支"),
    ("src/emotion/emotion_manager.py", "EmotionManager.update decay 直写"),
    ("src/growth/growth_integration.py", "GrowthIntegrationService._refresh_self_model"),
    ("src/orchestrator.py", "Orchestrator 治理 auto_apply 分支"),
    ("src/personality/personality_adapter.py", "PersonalityAdapter.apply_proposal mark_approved 自证"),
    ("src/admin/selfmodel_consumer.py", "SelfModelConsumer.process"),
    ("src/orchestrator.py", "新记忆 importance=0.5 硬编码 (Orchestrator)"),
    ("src/runtime/runtime_core.py", "经历投影 importance=0.5 硬编码 (RuntimeCore)"),
    ("src/orchestrator.py", "Orchestrator per-user 二次持久化"),
]


# ------------------------------------------------------------
# 1. 所有已知 mutation 写入口必须已注册
# ------------------------------------------------------------
def test_all_known_mutation_sites_registered():
    missing = []
    for file_path, writer in KNOWN_MUTATION_SITES:
        ok = is_legal_write(file_path, writer) or is_deprecated_write(file_path, writer)
        if not ok:
            missing.append(f"{file_path} :: {writer}")
    assert not missing, "未注册的 mutation 写入口: " + "; ".join(missing)


# ------------------------------------------------------------
# 2. identity_core 无任何写入口
# ------------------------------------------------------------
_SUBSCRIPT_ASSIGN = re.compile(r"IDENTITY_CORE\s*\[[^\]]*\]\s*=")
_METHOD_MUTATIONS = (
    "IDENTITY_CORE.update(",
    "IDENTITY_CORE.append(",
    "IDENTITY_CORE.setdefault(",
    "IDENTITY_CORE.pop(",
    "del IDENTITY_CORE",
)


def test_identity_core_has_no_write_entry():
    offenders = []
    for py_file in sorted((REPO_ROOT / "src").rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), 1):
            if "IDENTITY_CORE" not in line:
                continue
            if "IDENTITY_CORE:" in line or "IDENTITY_CORE =" in line or "IDENTITY_CORE=" in line:
                continue  # 定义行
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            is_mutation = bool(_SUBSCRIPT_ASSIGN.search(line)) or any(
                p in line for p in _METHOD_MUTATIONS
            )
            if is_mutation:
                offenders.append(f"{py_file.relative_to(REPO_ROOT)}:{idx}: {stripped}")
    assert not offenders, "identity_core 疑似写入口: " + "; ".join(offenders)


# ------------------------------------------------------------
# 3. 每个合法写入口必须声明三要素
# ------------------------------------------------------------
def test_every_legal_entry_declares_governance_elements():
    bad = []
    for entry in LEGAL_WRITE_PATHS:
        for field_name in ("proposal_source", "approval_requirement", "audit_requirement"):
            value = str(getattr(entry, field_name, "") or "").strip()
            if not value:
                bad.append(f"{entry.file}::{entry.writer} 缺少 {field_name}")
    assert not bad, "; ".join(bad)


def test_deprecated_entries_have_reason_and_migration_phase():
    bad = []
    for entry in DEPRECATED_WRITE_PATHS:
        if not str(entry.reason or "").strip():
            bad.append(f"{entry.file}::{entry.writer} 缺少 reason")
        if not str(entry.migration_phase or "").strip():
            bad.append(f"{entry.file}::{entry.writer} 缺少 migration_phase")
    assert not bad, "; ".join(bad)


def test_registry_files_exist():
    missing = []
    seen = set()
    for entry in (*LEGAL_WRITE_PATHS, *DEPRECATED_WRITE_PATHS):
        key = entry.file
        if key in seen:
            continue
        seen.add(key)
        if not (REPO_ROOT / entry.file).exists():
            missing.append(entry.file)
    assert not missing, "注册表引用了不存在的文件: " + "; ".join(missing)


# ------------------------------------------------------------
# 4. warn_deprecated_once: 同一 key 只告警一次
# ------------------------------------------------------------
def test_warn_deprecated_once_emits_only_once():
    key = "g0_test_warn_once_key"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        warn_deprecated_once(key, "g0 test first")
        warn_deprecated_once(key, "g0 test second")
    messages = [str(w.message) for w in caught]
    assert len(messages) == 1
    assert "g0 test first" in messages[0]


# ------------------------------------------------------------
# 5. state_mutation_audit: 行为验证
# ------------------------------------------------------------
class TestStateMutationAudit:
    def test_record_writes_valid_jsonl(self, tmp_path):
        audit_path = tmp_path / "state_mutations.jsonl"
        ok = record_state_mutation(
            component="emotion",
            target="emotion_state.valence",
            before={"valence": 0.1},
            after={"valence": 0.2},
            proposal_id="ecp_test_1",
            approval_id="apr_test_1",
            actor="g0_test",
            path=audit_path,
        )
        assert ok is True
        entries = read_entries(limit=10, path=audit_path)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["component"] == "emotion"
        assert entry["target"] == "emotion_state.valence"
        assert entry["before"] == {"valence": 0.1}
        assert entry["after"] == {"valence": 0.2}
        assert entry["proposal_id"] == "ecp_test_1"
        assert entry["approval_id"] == "apr_test_1"
        assert entry["actor"] == "g0_test"
        assert entry["version"] == SCHEMA_VERSION
        assert entry["id"].startswith("sm_")
        assert entry["timestamp"].endswith("Z")

    def test_append_only_latest_first(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        record_state_mutation(
            "emotion", "emotion_state.valence", 0.0, 0.1,
            "p1", "a1", "g0_test", path=audit_path,
        )
        record_state_mutation(
            "personality", "personality_state.traits.warmth", 0.7, 0.75,
            "p2", "a2", "g0_test", path=audit_path,
        )
        entries = read_entries(limit=10, path=audit_path)
        assert len(entries) == 2
        assert entries[0]["proposal_id"] == "p2"  # 最新在前
        assert entries[1]["proposal_id"] == "p1"

    def test_failure_isolated_returns_false(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        bad_path = blocker / "nested" / "audit.jsonl"  # 父路径是文件 → 写入必失败
        ok = record_state_mutation(
            "emotion", "emotion_state.valence", 0.0, 0.1,
            "p1", "a1", "g0_test", path=bad_path,
        )
        assert ok is False  # 不抛异常, 不阻断业务

    def test_corrupt_line_isolated_on_read(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        record_state_mutation(
            "emotion", "emotion_state.valence", 0.0, 0.1,
            "p1", "a1", "g0_test", path=audit_path,
        )
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write("{corrupt json line}\n")
        record_state_mutation(
            "emotion", "emotion_state.valence", 0.1, 0.2,
            "p2", "a2", "g0_test", path=audit_path,
        )
        entries = read_entries(limit=10, path=audit_path)
        assert len(entries) == 2  # 损坏行被隔离
        assert {e["proposal_id"] for e in entries} == {"p1", "p2"}

    def test_append_entry_rejects_non_dict(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        ok = append_entry(["not", "a", "dict"], path=audit_path)
        assert ok is False

    def test_default_path_constant(self):
        assert DEFAULT_AUDIT_PATH.endswith("state_mutations.jsonl")

    def test_roundtrip_json_line_parity(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        record_state_mutation(
            "emotion", "emotion_state.valence", "中文值", {"x": 1},
            "p1", "a1", "g0_test", path=audit_path,
        )
        with open(audit_path, "r", encoding="utf-8") as f:
            raw_lines = [ln for ln in f.readlines() if ln.strip()]
        assert len(raw_lines) == 1
        parsed = json.loads(raw_lines[0])
        assert parsed["before"] == "中文值"
        assert parsed["after"] == {"x": 1}
