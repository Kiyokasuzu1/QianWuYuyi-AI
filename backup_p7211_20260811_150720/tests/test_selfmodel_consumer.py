# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_consumer.py

Phase 3.5.3 Step 1: SelfModel Consumer 旁路测试

目标：
- 验证 GrowthProposal → PCR → apply_pcr → save_state 闭环
- 兼容 Schema A（growth_schema）和 Schema B（proposal）
- 不依赖 RuntimeCore / RuntimeBridge / Orchestrator
- 重复 proposal_id 不重复写入
- 默认 data_dir 指向临时目录，不污染正式 data
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ============================================================
# 路径
# ============================================================

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖基线
# ============================================================

FORBIDDEN_IMPORTS_AT_TEST_RUNTIME = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_data_dir():
    """独立临时目录作为 SelfModel 持久化目录。"""
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_5_3_consumer_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_proposal_a():
    """Schema A: growth_schema.GrowthProposal 形态（含 id / proposed_changes）。"""
    return {
        "id": "prop_a_001",
        "source_event_id": "evt_a_001",
        "proposed_changes": [
            {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.55, "reason": "user showed curiosity"},
            {"path": "personality.traits.warmth", "before": 0.6, "after": 0.62, "reason": "user thanked"},
        ],
        "confidence": 0.85,
        "evidence_ids": ["e1", "e2", "e3"],
        "evaluator_meta": {
            "pattern_detected": "high_user_activity",
            "reason_summary": "user highly active",
        },
        "timestamp": "2026-07-30T10:00:00Z",
        "status": "proposed",
    }


@pytest.fixture
def sample_proposal_b():
    """Schema B: proposal.GrowthProposal 形态（含 proposal_id / affected_dimensions）。"""
    return {
        "proposal_id": "prop_b_001",
        "source_event_id": "evt_b_001",
        "affected_dimensions": {
            "curiosity": 0.04,
            "warmth": 0.02,
        },
        "before_state": {"curiosity": 0.5, "warmth": 0.6},
        "after_state": {"curiosity": 0.54, "warmth": 0.62},
        "confidence": 0.78,
        "evidence": ["interaction_xyz"],
        "reason": "user engagement rising",
        "metadata": {
            "pattern_detected": "high_user_activity",
        },
        "timestamp": "2026-07-30T10:01:00Z",
        "status": "pending",
    }


# ============================================================
# T1: 空 proposal
# ============================================================

class TestEmptyProposal:
    """空 / 非法 proposal 应安全失败，不崩。"""

    def test_none_proposal_returns_safe_failure(self, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(None)
            assert result["pcr_generated"] is False
            assert result["selfmodel_updated"] is False
            assert result["error"] is not None
            assert "translate" in result["error"].lower()
        finally:
            consumer.close()

    def test_empty_dict_returns_safe_failure(self, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process({})
            assert result["pcr_generated"] is False
            assert result["error"] is not None
        finally:
            consumer.close()

    def test_unknown_schema_returns_safe_failure(self, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            # 既没有 id 也没有 proposal_id
            result = consumer.process({"foo": "bar"})
            assert result["pcr_generated"] is False
            assert "schema" in result["error"].lower() or "识别" in result["error"]
        finally:
            consumer.close()

    def test_non_dict_returns_safe_failure(self, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            for bad in ("just a string", 42, ["list", "of", "items"]):
                result = consumer.process(bad)
                assert result["pcr_generated"] is False
                assert result["error"] is not None
        finally:
            consumer.close()


# ============================================================
# T2: 合法 proposal → PCR
# ============================================================

class TestValidProposalTranslation:
    """合法 proposal 应被翻译为完整 PCR dict。"""

    def test_schema_a_translates_to_pcr(self, sample_proposal_a):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        pcr = GrowthProposalTranslator.translate(sample_proposal_a)
        # 必填字段
        assert "request_id" in pcr
        assert pcr["request_id"].startswith("pcr_")
        assert pcr["source_proposal_id"] == "prop_a_001"
        # evolution_record.trait_changes 至少 2 个 trait
        er = pcr["evolution_record"]
        assert "trait_changes" in er
        assert "curiosity" in er["trait_changes"]
        assert "warmth" in er["trait_changes"]
        # delta 计算（after - before）
        assert abs(er["trait_changes"]["curiosity"]["delta"] - 0.05) < 1e-5
        # confidence
        assert pcr["confidence"] == 0.85
        # evidence_count
        assert pcr["evidence_count"] == 3
        # growth_records 与 trait_changes 数量一致
        assert len(pcr["growth_records"]) == len(er["trait_changes"])

    def test_schema_b_translates_to_pcr(self, sample_proposal_b):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        pcr = GrowthProposalTranslator.translate(sample_proposal_b)
        assert pcr["source_proposal_id"] == "prop_b_001"
        er = pcr["evolution_record"]
        assert "curiosity" in er["trait_changes"]
        assert "warmth" in er["trait_changes"]
        # affected_dimensions 里的 delta 直接使用
        assert abs(er["trait_changes"]["curiosity"]["delta"] - 0.04) < 1e-5
        assert pcr["confidence"] == 0.78
        assert pcr["evidence_count"] == 1  # evidence 是 1 个字符串

    def test_schema_detection(self, sample_proposal_a, sample_proposal_b):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert GrowthProposalTranslator.detect_schema(sample_proposal_a) == \
            GrowthProposalTranslator.SCHEMA_A
        assert GrowthProposalTranslator.detect_schema(sample_proposal_b) == \
            GrowthProposalTranslator.SCHEMA_B
        assert GrowthProposalTranslator.detect_schema({}) == \
            GrowthProposalTranslator.SCHEMA_UNKNOWN
        assert GrowthProposalTranslator.detect_schema("not dict") == \
            GrowthProposalTranslator.SCHEMA_UNKNOWN

    def test_evaluator_meta_captures_schema_source(self, sample_proposal_a, sample_proposal_b):
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        pcr_a = GrowthProposalTranslator.translate(sample_proposal_a)
        pcr_b = GrowthProposalTranslator.translate(sample_proposal_b)
        assert pcr_a["evaluator_meta"]["_source_schema"] == "growth_schema"
        assert pcr_b["evaluator_meta"]["_source_schema"] == "proposal"


# ============================================================
# T3: apply_pcr 闭环
# ============================================================

class TestApplyPcrClosure:
    """apply_pcr 后内存中应有 belief / history / reflection 数据。"""

    def test_closure_creates_in_memory_records(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_proposal_a)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            # envelope
            env = result["envelope"]
            assert env is not None
            assert env["applied"] is True
            assert env["history_event_id"] is not None
            # 内存
            adapter = consumer.adapter
            assert len(list(adapter._beliefs.all())) >= 1
            assert len(list(adapter._history.all())) >= 1
            assert len(list(adapter._reflections.all())) >= 1
        finally:
            consumer.close()

    def test_envelope_reports_beliefs_added(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_proposal_a)
            env = result["envelope"]
            assert env["beliefs_added"] >= 1
        finally:
            consumer.close()


# ============================================================
# T4: JSONL 持久化
# ============================================================

class TestJsonlPersistence:
    """save_state 后 JSONL 文件应被生成。"""

    def test_save_state_writes_jsonl_files(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer, BELIEFS_FILENAME, HISTORY_FILENAME, REFLECTION_FILENAME
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_proposal_a)
            files = result["files_written"]
            # 至少 beliefs 和 history 应被写入
            assert files["beliefs"] is not None
            assert files["history"] is not None
            assert files["reflection"] is not None
            # 文件实际存在
            assert (temp_data_dir / BELIEFS_FILENAME).exists()
            assert (temp_data_dir / HISTORY_FILENAME).exists()
            assert (temp_data_dir / REFLECTION_FILENAME).exists()
            # 文件内容合法
            with open(temp_data_dir / BELIEFS_FILENAME, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            assert len(lines) >= 1
            for line in lines:
                obj = json.loads(line)
                assert "belief_id" in obj
        finally:
            consumer.close()

    def test_jsonl_content_has_required_fields(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            consumer.process(sample_proposal_a)
            history_path = temp_data_dir / "history.jsonl"
            with open(history_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            for line in lines:
                obj = json.loads(line)
                assert "event_id" in obj
                assert "affected_traits" in obj or "summary" in obj
        finally:
            consumer.close()


# ============================================================
# T5: 无 Runtime 依赖
# ============================================================

class TestNoRuntimeDependency:
    """consumer 模块不应 import runtime_core / runtime_bridge / orchestrator。"""

    def test_module_source_no_runtime_imports(self):
        from src.admin import selfmodel_consumer as mod
        import re
        text = Path(mod.__file__).read_text(encoding="utf-8")
        # 提取所有 import 语句
        imports: List[str] = []
        for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", text, re.MULTILINE):
            imports.append(m.group(1))
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.MULTILINE):
            imports.append(m.group(1))
        for mod_name in imports:
            for forbidden in FORBIDDEN_IMPORTS_AT_TEST_RUNTIME:
                assert not mod_name.startswith(forbidden), (
                    f"selfmodel_consumer.py 不应 import {forbidden}（发现 {mod_name!r}）"
                )

    def test_consumer_creation_does_not_load_runtime_modules(self):
        import sys
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_IMPORTS_AT_TEST_RUNTIME:
                del sys.modules[m]

        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=tempfile.mkdtemp(prefix="dep_test_"))
        try:
            loaded = set(sys.modules.keys())
            for forbidden in FORBIDDEN_IMPORTS_AT_TEST_RUNTIME:
                assert forbidden not in loaded, (
                    f"创建 SelfModelConsumer 不应加载 {forbidden}"
                )
        finally:
            consumer.close()

    def test_process_does_not_load_runtime_modules(self, sample_proposal_a):
        import sys
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_IMPORTS_AT_TEST_RUNTIME:
                del sys.modules[m]

        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=tempfile.mkdtemp(prefix="dep_test_"))
        try:
            consumer.process(sample_proposal_a)
            loaded = set(sys.modules.keys())
            for forbidden in FORBIDDEN_IMPORTS_AT_TEST_RUNTIME:
                assert forbidden not in loaded
        finally:
            consumer.close()


# ============================================================
# T6: 双 schema 兼容
# ============================================================

class TestDualSchemaCompatibility:
    """两种 GrowthProposal schema 都应能完整跑通 consumer。"""

    def test_schema_a_full_closure(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_proposal_a)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            assert result["files_written"]["beliefs"] is not None
        finally:
            consumer.close()

    def test_schema_b_full_closure(self, sample_proposal_b, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_proposal_b)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            assert result["files_written"]["beliefs"] is not None
        finally:
            consumer.close()

    def test_both_schemas_in_batch(self, sample_proposal_a, sample_proposal_b, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            results = consumer.process_batch([sample_proposal_a, sample_proposal_b])
            assert len(results) == 2
            for r in results:
                assert r["pcr_generated"] is True
                assert r["selfmodel_updated"] is True
            # proposal_id 不同
            assert results[0]["proposal_id"] == "prop_a_001"
            assert results[1]["proposal_id"] == "prop_b_001"
        finally:
            consumer.close()


# ============================================================
# T7: 重复 proposal_id 不重复写入
# ============================================================

class TestDedup:
    """同一 proposal_id 第二次处理应被跳过。"""

    def test_duplicate_proposal_id_skipped(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            # 第一次
            r1 = consumer.process(sample_proposal_a)
            assert r1["dedup_skipped"] is False
            assert r1["selfmodel_updated"] is True
            beliefs_after_first = len(list(consumer.adapter._beliefs.all()))

            # 第二次（相同 proposal_id）
            r2 = consumer.process(sample_proposal_a)
            assert r2["dedup_skipped"] is True
            assert "duplicate" in (r2["error"] or "").lower()
            # 内存数据未增加
            beliefs_after_second = len(list(consumer.adapter._beliefs.all()))
            assert beliefs_after_second == beliefs_after_first
        finally:
            consumer.close()

    def test_dedup_disabled_reprocesses(self, sample_proposal_a, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(
            data_dir=str(temp_data_dir),
            enable_dedup=False,
        )
        try:
            r1 = consumer.process(sample_proposal_a)
            r2 = consumer.process(sample_proposal_a)
            assert r1["dedup_skipped"] is False
            assert r2["dedup_skipped"] is False
        finally:
            consumer.close()

    def test_processed_ids_recorded(self, sample_proposal_a, sample_proposal_b, temp_data_dir):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            consumer.process(sample_proposal_a)
            consumer.process(sample_proposal_b)
            processed = consumer.processed_ids
            assert "prop_a_001" in processed
            assert "prop_b_001" in processed
            assert len(processed) == 2
        finally:
            consumer.close()


# ============================================================
# T8: 临时目录默认行为（不污染正式 data）
# ============================================================

class TestDefaultDataDirSafety:
    """不传 data_dir 时应使用临时目录，不影响正式 data/self_model/。"""

    def test_default_uses_temp_dir(self):
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer()
        try:
            # data_dir 应该是临时目录（不在项目根的 data/self_model/ 下）
            assert consumer.data_dir.exists()
            # 不应是项目正式目录
            project_formal = Path(PROJECT_ROOT) / "data" / "self_model"
            assert consumer.data_dir != project_formal
        finally:
            consumer.close()

    def test_consume_one_convenience_function(self, sample_proposal_a):
        from src.admin.selfmodel_consumer import consume_one
        result = consume_one(sample_proposal_a)
        # 临时目录被清理后 result 仍可读取
        assert result["pcr_generated"] is True
        assert result["selfmodel_updated"] is True
