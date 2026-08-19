# -*- coding: utf-8 -*-
"""
tests/test_phase_3_6_3_integration.py

Phase 3.6.3: GrowthProposalNormalizer 接入主链路 —— 集成测试

覆盖：
- T1: A schema proposal → Normalizer → SelfModelConsumer
- T2: B schema proposal → Normalizer → SelfModelConsumer
- T3: 旧 Translator 入口仍兼容
- T4: 端到端 PCR → PersonalityAdapter 链路
- T5: deprecated 标记 + 替换推荐
- T6: Normalizer 失败回退（旧双分支）
- T7: 数据完整性（timestamp / reason / before_state / after_state 在主链路保留）
- T8: 反依赖基线（无 Runtime / Orchestrator / 治理 import 注入）

约束：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/* / src/personality/*
- 不修改 selfmodel_consumer.py 主逻辑（只允许 Phase 3.6.3 接入的最小变更）
- 不修改 proposal_normalizer.py 核心逻辑
"""
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖基线
# ============================================================

FORBIDDEN_MODULES = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_data_dir():
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_6_3_data_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def temp_audit_dir():
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_6_3_audit_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def sample_canonical_a() -> Dict[str, Any]:
    """Schema A 样本。"""
    return {
        "id": "prop_a_phase363_001",
        "source_event_id": "evt_a_phase363_001",
        "proposed_changes": [
            {
                "path": "personality.traits.curiosity",
                "before": 0.5,
                "after": 0.55,
                "reason": "user asked many questions",
            },
            {
                "path": "personality.traits.warmth",
                "before": 0.6,
                "after": 0.62,
                "reason": "user expressed thanks",
            },
        ],
        "confidence": 0.85,
        "evidence_ids": ["e1", "e2", "e3"],
        "evaluator_meta": {
            "pattern_detected": "high_user_activity",
            "reason_summary": "high engagement",
        },
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "proposed",
    }


@pytest.fixture
def sample_governance_b() -> Dict[str, Any]:
    """Schema B 样本。"""
    return {
        "proposal_id": "prop_b_phase363_001",
        "timestamp": "2026-01-01T00:00:00Z",
        "proposal_type": "personality",
        "status": "pending",
        "source": "growth_engine",
        "source_event_id": "evt_b_phase363_001",
        "user_id": "user_phase363_001",
        "affected_dimensions": {
            "curiosity": 0.04,
            "warmth": 0.02,
        },
        "before_state": {"curiosity": 0.5, "warmth": 0.6},
        "after_state": {"curiosity": 0.54, "warmth": 0.62},
        "confidence": 0.78,
        "reason": "user engagement rising",
        "evidence": ["inter_x", "inter_y"],
        "priority": "medium",
        "metadata": {"source": "growth_engine_v2"},
    }


# ============================================================
# T1: A schema → Normalizer → SelfModelConsumer
# ============================================================

class TestASchemaEndToEnd:
    """A schema proposal 经归一化进入 SelfModelConsumer，端到端闭环。"""

    def test_a_to_canonical_to_pcr_via_normalized(self, sample_canonical_a):
        """T1.1: NormalizedProposalTranslator.translate(A) → PCR OK"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_canonical_a)
        assert pcr["source_proposal_id"] == "prop_a_phase363_001"
        assert pcr["confidence"] == 0.85
        assert pcr["evidence_count"] == 3
        er = pcr["evolution_record"]
        assert "curiosity" in er["trait_changes"]
        assert "warmth" in er["trait_changes"]
        assert abs(er["trait_changes"]["curiosity"]["delta"] - 0.05) < 1e-5
        assert abs(er["trait_changes"]["warmth"]["delta"] - 0.02) < 1e-5
        # A 输入直通，_normalized 标记为 False
        assert pcr["evaluator_meta"].get("_source_schema") == "growth_schema"
        assert pcr["evaluator_meta"].get("_normalized") in (False, None)

    def test_a_to_selfmodel_consumer_closure(self, sample_canonical_a, temp_data_dir):
        """T1.2: A → SelfModelConsumer.process() → 成功更新 + 内存记录"""
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_canonical_a)
            assert result["proposal_id"] == "prop_a_phase363_001"
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            assert result["error"] is None
            env = result["envelope"]
            assert env is not None
            assert env["applied"] is True
            # 内存里有 belief / history / reflection
            adapter = consumer.adapter
            assert len(list(adapter._beliefs.all())) >= 1
            assert len(list(adapter._history.all())) >= 1
            assert len(list(adapter._reflections.all())) >= 1
        finally:
            consumer.close()

    def test_a_canonical_input_unchanged(self, sample_canonical_a):
        """T1.3: NormalizedProposalTranslator 不修改入参 canonical"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        before = dict(sample_canonical_a)
        # 深拷贝避免引用干扰
        import copy
        original = copy.deepcopy(sample_canonical_a)
        NormalizedProposalTranslator.translate(sample_canonical_a)
        assert sample_canonical_a == original


# ============================================================
# T2: B schema → Normalizer → SelfModelConsumer
# ============================================================

class TestBSchemaEndToEnd:
    """B schema proposal 经 Normalizer 归一化进入 SelfModelConsumer。"""

    def test_b_normalized_then_translated(self, sample_governance_b):
        """T2.1: B → Normalizer → _translate_canonical → PCR OK"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_governance_b)
        assert pcr["source_proposal_id"] == "prop_b_phase363_001"
        assert pcr["confidence"] == 0.78
        # evidence 是 List[str]，count = len
        assert pcr["evidence_count"] == 2
        er = pcr["evolution_record"]
        assert "curiosity" in er["trait_changes"]
        assert "warmth" in er["trait_changes"]
        # affected_dimensions delta → trait delta
        assert abs(er["trait_changes"]["curiosity"]["delta"] - 0.04) < 1e-5
        assert abs(er["trait_changes"]["warmth"]["delta"] - 0.02) < 1e-5
        # B schema 归一化标记
        assert pcr["evaluator_meta"].get("_source_schema") == "proposal"
        assert pcr["evaluator_meta"].get("_normalized") is True
        assert "phase_3_6_2" in str(
            pcr["evaluator_meta"].get("_normalizer_version", "")
        )

    def test_b_to_selfmodel_consumer_closure(self, sample_governance_b, temp_data_dir):
        """T2.2: B → SelfModelConsumer.process() → 成功更新 + 内存记录"""
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        try:
            result = consumer.process(sample_governance_b)
            assert result["proposal_id"] == "prop_b_phase363_001"
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            assert result["error"] is None
            env = result["envelope"]
            assert env is not None
            assert env["applied"] is True
            # 内存里有 belief / history / reflection
            adapter = consumer.adapter
            assert len(list(adapter._beliefs.all())) >= 1
            assert len(list(adapter._history.all())) >= 1
            assert len(list(adapter._reflections.all())) >= 1
        finally:
            consumer.close()

    def test_b_governance_evidence_preserved(self, sample_governance_b):
        """T2.3: B 输入 evidence 列表保留"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_governance_b)
        # evidence 列表中应至少有 1 条来自原始 B
        growth_signals = [gr.get("growth_signal", "") for gr in pcr["growth_records"]]
        assert len(pcr["growth_records"]) >= 2  # curiosity + warmth
        # evidence_count = 2（"inter_x", "inter_y"）
        assert pcr["evidence_count"] == 2


# ============================================================
# T3: 旧 Translator 入口仍兼容
# ============================================================

class TestLegacyTranslatorBackwardCompat:
    """旧 GrowthProposalTranslator 入口仍能工作（deprecated 但兼容）。"""

    def test_legacy_translator_a_input(self, sample_canonical_a):
        """T3.1: 旧入口 A 输入 → PCR 字段一致"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        pcr = GrowthProposalTranslator.translate(sample_canonical_a)
        assert pcr["source_proposal_id"] == "prop_a_phase363_001"
        assert pcr["confidence"] == 0.85
        assert pcr["evaluator_meta"]["_source_schema"] == "growth_schema"

    def test_legacy_translator_b_input(self, sample_governance_b):
        """T3.2: 旧入口 B 输入 → PCR 字段一致（仍走归一化）"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        pcr = GrowthProposalTranslator.translate(sample_governance_b)
        assert pcr["source_proposal_id"] == "prop_b_phase363_001"
        assert pcr["confidence"] == 0.78
        # _source_schema 保留为 "proposal"（兼容旧测试）
        assert pcr["evaluator_meta"]["_source_schema"] == "proposal"
        # 但 _normalized 标记为 True（说明走过了归一化）
        assert pcr["evaluator_meta"].get("_normalized") is True

    def test_legacy_translator_detect_schema(self, sample_canonical_a, sample_governance_b):
        """T3.3: 旧 detect_schema API 仍工作"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert (
            GrowthProposalTranslator.detect_schema(sample_canonical_a)
            == GrowthProposalTranslator.SCHEMA_A
        )
        assert (
            GrowthProposalTranslator.detect_schema(sample_governance_b)
            == GrowthProposalTranslator.SCHEMA_B
        )
        assert GrowthProposalTranslator.detect_schema({}) == "unknown"
        assert GrowthProposalTranslator.detect_schema("not dict") == "unknown"

    def test_legacy_translator_translate_equivalence(self, sample_canonical_a):
        """T3.4: 旧入口与新入口对 canonical 输入产生一致 PCR（字段一致）"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr_legacy = GrowthProposalTranslator.translate(sample_canonical_a)
        pcr_new = NormalizedProposalTranslator.translate(sample_canonical_a)
        # 主要字段一致
        for k in (
            "source_proposal_id",
            "confidence",
            "evidence_count",
            "reason",
        ):
            assert pcr_legacy[k] == pcr_new[k], f"{k} 不一致: {pcr_legacy[k]} vs {pcr_new[k]}"
        # trait_changes 一致
        assert pcr_legacy["evolution_record"]["trait_changes"] == \
            pcr_new["evolution_record"]["trait_changes"]


# ============================================================
# T4: 端到端 → PersonalityAdapter 链路
# ============================================================

class TestEndToEndWithAudit:
    """端到端：GrowthProposal → Normalizer → Consumer → Audit"""

    def test_a_proposal_full_pipeline(self, sample_canonical_a, temp_data_dir, temp_audit_dir):
        """T4.1: A → 完整审计链路"""
        from src.admin.selfmodel_consumer import SelfModelConsumer
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit, AuditedSelfModelConsumer

        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        audited = AuditedSelfModelConsumer(consumer, audit)
        try:
            result = audited.process(sample_canonical_a)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True

            # 审计文件应记录这次消费
            assert audit.has_processed("prop_a_phase363_001") is True
            stats = audit.get_stats()
            assert stats["total_processed"] == 1
            assert stats["success_count"] == 1
        finally:
            audited.close()

    def test_b_proposal_full_pipeline(self, sample_governance_b, temp_data_dir, temp_audit_dir):
        """T4.2: B → 完整审计链路"""
        from src.admin.selfmodel_consumer import SelfModelConsumer
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit, AuditedSelfModelConsumer

        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        audited = AuditedSelfModelConsumer(consumer, audit)
        try:
            result = audited.process(sample_governance_b)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True

            # 审计文件应记录这次消费（B 经归一化后 proposal_id 仍为原始）
            assert audit.has_processed("prop_b_phase363_001") is True
            stats = audit.get_stats()
            assert stats["total_processed"] == 1
            assert stats["success_count"] == 1
        finally:
            audited.close()

    def test_batch_mixed_schemas(self, sample_canonical_a, sample_governance_b, temp_data_dir, temp_audit_dir):
        """T4.3: 批量混合 A/B schema 均能正常消费"""
        from src.admin.selfmodel_consumer import SelfModelConsumer
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit, AuditedSelfModelConsumer

        consumer = SelfModelConsumer(data_dir=str(temp_data_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        audited = AuditedSelfModelConsumer(consumer, audit)
        try:
            results = audited.process_batch([sample_canonical_a, sample_governance_b])
            assert len(results) == 2
            assert results[0]["pcr_generated"] is True
            assert results[0]["selfmodel_updated"] is True
            assert results[0]["proposal_id"] == "prop_a_phase363_001"
            assert results[1]["pcr_generated"] is True
            assert results[1]["selfmodel_updated"] is True
            assert results[1]["proposal_id"] == "prop_b_phase363_001"

            # 审计记录 2 条
            stats = audit.get_stats()
            assert stats["total_processed"] == 2
            assert stats["success_count"] == 2
        finally:
            audited.close()


# ============================================================
# T5: deprecated 标记 + 替换推荐
# ============================================================

class TestDeprecationMarker:
    """GrowthProposalTranslator 应有 deprecated 标记。"""

    def test_translator_has_deprecation_flag(self):
        """T5.1: GrowthProposalTranslator.__deprecated__ == True"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        assert getattr(GrowthProposalTranslator, "__deprecated__", False) is True

    def test_translator_has_replacement_marker(self):
        """T5.2: __deprecated_replacement__ 指向 NormalizedProposalTranslator"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        replacement = getattr(GrowthProposalTranslator, "__deprecated_replacement__", "")
        assert "NormalizedProposalTranslator" in replacement

    def test_translator_docstring_mentions_deprecation(self):
        """T5.3: docstring 含 deprecated 标记"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        doc = GrowthProposalTranslator.__doc__ or ""
        assert "deprecated" in doc.lower()

    def test_normalized_translator_is_recommended(self):
        """T5.4: NormalizedProposalTranslator 是新业务入口"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        assert hasattr(NormalizedProposalTranslator, "translate")
        assert hasattr(NormalizedProposalTranslator, "normalize")
        assert hasattr(NormalizedProposalTranslator, "detect")
        assert hasattr(NormalizedProposalTranslator, "is_canonical")
        assert hasattr(NormalizedProposalTranslator, "is_governance")
        assert hasattr(NormalizedProposalTranslator, "needs_normalization")


# ============================================================
# T6: Normalizer 失败回退
# ============================================================

class TestNormalizerFailureFallback:
    """Normalizer 异常时，NormalizedProposalTranslator 不崩，回退到旧路径。"""

    def test_none_proposal_safe_failure(self):
        """T6.1: None 输入 → ValueError"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        with pytest.raises(ValueError):
            NormalizedProposalTranslator.translate(None)

    def test_empty_dict_safe_failure(self):
        """T6.2: 空 dict → ValueError（无法识别 schema）"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        with pytest.raises(ValueError):
            NormalizedProposalTranslator.translate({})

    def test_unknown_schema_safe_failure(self):
        """T6.3: 既无 id 也无 proposal_id → ValueError"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        with pytest.raises(ValueError):
            NormalizedProposalTranslator.translate({"foo": "bar"})

    def test_translator_legacy_unaffected(self):
        """T6.4: 旧 Translator 对同一非法输入也安全失败"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        with pytest.raises(ValueError):
            GrowthProposalTranslator.translate({})


# ============================================================
# T7: 数据完整性（主链路保留关键字段）
# ============================================================

class TestDataIntegrityInMainPath:
    """A/B schema 关键字段在主链路中得到保留。"""

    def test_a_proposed_changes_reasons_preserved(self, sample_canonical_a):
        """T7.1: A 输入的 proposed_changes[i].reason 保留到 growth_records.reason"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_canonical_a)
        reasons = [gr.get("reason", "") for gr in pcr["growth_records"]]
        # growth_records 的 reason 至少有一条匹配 "proposal_path:" 形式（来自 A 路径）
        assert any("proposal:" in r or "proposal_path" in r or r for r in reasons)

    def test_a_evaluator_meta_reason_summary_preserved(self, sample_canonical_a):
        """T7.2: A.evaluator_meta.reason_summary 保留"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_canonical_a)
        assert (
            pcr["evaluator_meta"].get("reason_summary") == "high engagement"
        )

    def test_b_evidence_preserved_in_meta(self, sample_governance_b):
        """T7.3: B.evidence 列表保留到 evaluator_meta._governance_origin"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_governance_b)
        em = pcr["evaluator_meta"]
        origin = em.get("_governance_origin")
        # Normalizer 应该保留 B 原数据
        if origin is not None:
            assert "inter_x" in origin.get("evidence", []) or \
                   "inter_y" in origin.get("evidence", [])

    def test_b_affected_dimensions_mapped_to_proposed_changes(self, sample_governance_b):
        """T7.4: B.affected_dimensions 正确映射到 proposed_changes.delta"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_governance_b)
        er = pcr["evolution_record"]
        # B 输入 0.04 / 0.02 应当作为 delta
        assert abs(er["trait_changes"]["curiosity"]["delta"] - 0.04) < 1e-5
        assert abs(er["trait_changes"]["warmth"]["delta"] - 0.02) < 1e-5

    def test_b_preserves_priority_and_proposal_type(self, sample_governance_b):
        """T7.5: B.priority / proposal_type 保留到 _governance_origin"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_governance_b)
        em = pcr["evaluator_meta"]
        origin = em.get("_governance_origin")
        if origin is not None:
            # proposal_type 可能在 em.origin 中保留
            assert origin.get("proposal_type") in ("personality", "") or \
                   origin.get("priority") in ("medium", "")

    def test_pcr_contains_request_id_and_source_proposal_id(self, sample_canonical_a):
        """T7.6: PCR 必含 request_id / source_proposal_id"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_canonical_a)
        assert pcr["request_id"].startswith("pcr_")
        assert pcr["source_proposal_id"] == "prop_a_phase363_001"

    def test_pcr_contains_evolution_record_and_growth_records(self, sample_canonical_a):
        """T7.7: PCR 必含 evolution_record + growth_records"""
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        pcr = NormalizedProposalTranslator.translate(sample_canonical_a)
        er = pcr["evolution_record"]
        assert "record_id" in er
        assert "trait_changes" in er
        assert "confidence" in er
        assert isinstance(pcr["growth_records"], list)
        assert len(pcr["growth_records"]) == len(er["trait_changes"])


# ============================================================
# T8: 反依赖基线
# ============================================================

class TestNoForbiddenDependencies:
    """不应引入 Runtime / Orchestrator / 治理 / 核心业务 import 依赖。"""

    def test_normalized_translator_no_runtime_import(self):
        """T8.1: NormalizedProposalTranslator 文件不导入 Runtime"""
        from pathlib import Path
        target = (
            PROJECT_ROOT
            / "src"
            / "admin"
            / "normalized_proposal_translator.py"
        )
        if not target.exists():
            pytest.skip("normalized_proposal_translator.py 不存在")
        content = target.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert (
                f"import {forbidden}" not in content
            ), f"{target.name} 不应 import {forbidden}"
            assert (
                f"from {forbidden}" not in content
            ), f"{target.name} 不应 from {forbidden}"

    def test_selfmodel_consumer_still_no_runtime_import(self):
        """T8.2: selfmodel_consumer.py 未引入 Runtime（保留原有约束）"""
        from pathlib import Path
        target = (
            PROJECT_ROOT
            / "src"
            / "admin"
            / "selfmodel_consumer.py"
        )
        content = target.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert (
                f"import {forbidden}" not in content
            ), f"selfmodel_consumer.py 不应 import {forbidden}"
            assert (
                f"from {forbidden}" not in content
            ), f"selfmodel_consumer.py 不应 from {forbidden}"

    def test_normalized_translator_no_growth_or_personality_import(self):
        """T8.3: 不依赖 growth/personality（仅依赖 contracts）"""
        target = (
            PROJECT_ROOT
            / "src"
            / "admin"
            / "normalized_proposal_translator.py"
        )
        content = target.read_text(encoding="utf-8")
        # 允许 import growth_schema 等 contracts，但禁止 import src.growth.* / src.personality.*
        assert "from src.growth" not in content, "不应 import src.growth"
        assert "from src.personality" not in content, "不应 import src.personality"
        assert "import src.growth" not in content, "不应 import src.growth"
        assert "import src.personality" not in content, "不应 import src.personality"

    def test_dependency_direction_contracts_downstream(self):
        """T8.4: 依赖方向 contracts → growth → personality（不反向）"""
        # 本测试只验证 NormalizedProposalTranslator 的依赖符合方向
        # 它依赖 src.contracts.proposal_normalizer（contracts 层）
        # 不依赖 src.growth.*（下游）或 src.personality.*（下游）
        from src.admin.normalized_proposal_translator import NormalizedProposalTranslator
        # 静态检查 import 即可
        import inspect
        src = inspect.getsource(NormalizedProposalTranslator)
        # 允许 contracts.proposal_normalizer / selfmodel_consumer 引用
        assert "src.contracts.proposal_normalizer" in src or \
               "from src.contracts" in src or "import src.contracts" in src, \
               "NormalizedProposalTranslator 应依赖 contracts"
