# -*- coding: utf-8 -*-
"""
tests/test_phase_3_6_5_schema_freeze.py

Phase 3.6.5: Schema Governance Final Audit 测试

目标：
- canonical schema version 默认存在（schema_version = "1.0"）
- legacy schema 仍可 normalize（向后兼容）
- round-trip 不丢字段
- consumer 不 import legacy schema
- runtime/personality 不依赖 legacy schema
- migration policy 文档存在
- 字段契约冻结（字段集合 / 顺序 / 默认值）

覆盖：
- T1: canonical schema 默认 schema_version
- T2: legacy schema 仍可 normalize（不破坏）
- T3: round-trip 不丢字段
- T4: consumer 不 import legacy schema
- T5: runtime/personality 不依赖 legacy schema
- T6: migration policy 文档存在
- T7: 字段契约冻结（11 核心字段）
- T8: 反依赖基线

约束：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/*（核心算法）/
  src/personality/*（核心算法）/ src/memory/* / src/emotion/*
- 不修改 proposal_normalizer.py 核心转换规则
- 不删除 legacy schema / 兼容入口
- 保持 backward compatibility
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# 关键文件路径
CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "src" / "contracts" / "growth_schema.py"
LEGACY_SCHEMA_PATH = PROJECT_ROOT / "src" / "growth" / "proposal" / "proposal.py"
NORMALIZER_PATH = PROJECT_ROOT / "src" / "contracts" / "proposal_normalizer.py"
LIFECYCLE_DOC_PATH = PROJECT_ROOT / "docs" / "growth_proposal_lifecycle.md"

# 反依赖基线（不允许 import 这些模块）
FORBIDDEN_MODULES = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}

# 业务模块（consumer / adapter / 主链路）— 不应直接 import legacy schema
# 注意：governance_provider 是 producer / 输入兼容层，允许 import legacy schema
CONSUMER_MODULES = [
    PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer.py",
    PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer_audit.py",
    PROJECT_ROOT / "src" / "admin" / "normalized_proposal_translator.py",
    PROJECT_ROOT / "src" / "admin" / "selfmodel_diagnostic.py",
]

# canonical 11 个核心字段（v1.0）
CANONICAL_V1_FIELDS = [
    "id",
    "source_event_id",
    "proposed_changes",
    "confidence",
    "evidence_ids",
    "evaluator_meta",
    "timestamp",
    "status",
    "accepted_at",
    "rejected_at",
    "schema_version",
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_canonical() -> Dict[str, Any]:
    """完整 canonical schema 样本（v1.0）。"""
    return {
        "id": "prop_freeze_001",
        "source_event_id": "evt_freeze_001",
        "proposed_changes": [
            {
                "path": "personality.traits.curiosity",
                "before": 0.5,
                "after": 0.55,
                "reason": "increased engagement",
            }
        ],
        "confidence": 0.88,
        "evidence_ids": ["e1", "e2"],
        "evaluator_meta": {
            "pattern_detected": "high_user_activity",
            "reason_summary": "high engagement signal",
        },
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "proposed",
        "accepted_at": None,
        "rejected_at": None,
        "schema_version": "1.0",
    }


@pytest.fixture
def sample_canonical_v0() -> Dict[str, Any]:
    """旧版本 canonical（无 schema_version，Phase 3.6.5 之前的数据）。"""
    return {
        "id": "prop_v0_001",
        "source_event_id": "evt_v0_001",
        "proposed_changes": [
            {
                "path": "personality.traits.warmth",
                "before": 0.6,
                "after": 0.62,
                "reason": "user thanks",
            }
        ],
        "confidence": 0.75,
        "evidence_ids": ["e1"],
        "evaluator_meta": {
            "reason_summary": "low signal",
        },
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "proposed",
        # 无 schema_version 字段（v0 数据）
    }


@pytest.fixture
def sample_legacy_governance() -> Dict[str, Any]:
    """完整 legacy schema 样本。"""
    return {
        "proposal_id": "prop_legacy_freeze_001",
        "timestamp": "2026-01-01T00:00:00.000000",
        "proposal_type": "personality",
        "status": "pending",
        "source": "admin_governance",
        "source_event_id": "evt_legacy_freeze_001",
        "user_id": "user_freeze_001",
        "affected_dimensions": {
            "curiosity": 0.05,
            "warmth": 0.03,
        },
        "before_state": {
            "curiosity": 0.5,
            "warmth": 0.6,
        },
        "after_state": {
            "curiosity": 0.55,
            "warmth": 0.63,
        },
        "confidence": 0.82,
        "reason": "user engagement rising",
        "evidence": ["inter_x", "inter_y"],
        "priority": "medium",
        "metadata": {
            "source": "admin_governance_v3",
        },
    }


# ============================================================
# T1: canonical schema 默认 schema_version
# ============================================================

class TestCanonicalSchemaVersion:
    """canonical schema 默认 schema_version = '1.0'（Phase 3.6.5 冻结）。"""

    def test_canonical_version_constant_exists(self):
        """T1.1: CANONICAL_SCHEMA_VERSION 常量存在且为 '1.0'"""
        from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
        assert CANONICAL_SCHEMA_VERSION == "1.0"

    def test_canonical_default_has_schema_version(self):
        """T1.2: 默认实例化时 schema_version = '1.0'"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal()
        assert hasattr(p, "schema_version")
        assert p.schema_version == "1.0"

    def test_canonical_explicit_construction(self):
        """T1.3: 显式构造时 schema_version 正确赋值"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal(
            id="prop_test_001",
            schema_version="1.0",
        )
        assert p.schema_version == "1.0"

    def test_canonical_to_dict_contains_schema_version(self):
        """T1.4: to_dict() 输出包含 schema_version 字段"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal(id="prop_test_002")
        d = p.to_dict()
        assert "schema_version" in d
        assert d["schema_version"] == "1.0"

    def test_canonical_from_dict_with_version(self):
        """T1.5: from_dict() 支持显式 schema_version"""
        from src.contracts.growth_schema import GrowthProposal
        d = {
            "id": "prop_test_003",
            "proposed_changes": [],
            "confidence": 0.5,
            "evidence_ids": [],
            "evaluator_meta": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "proposed",
            "schema_version": "1.0",
        }
        p = GrowthProposal.from_dict(d)
        assert p.schema_version == "1.0"

    def test_canonical_from_dict_backward_compat_no_version(self, sample_canonical_v0):
        """T1.6: from_dict() 在缺省 schema_version 时回退到 '1.0'（向后兼容）"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal.from_dict(sample_canonical_v0)
        assert p.schema_version == "1.0", (
            "旧数据（无 schema_version）反序列化时必须回退到 '1.0'（backward compatibility）"
        )

    def test_canonical_from_dict_explicit_none_falls_back(self):
        """T1.7: from_dict() 在 schema_version=None 时回退到 '1.0'"""
        from src.contracts.growth_schema import GrowthProposal
        d = {
            "id": "prop_test_004",
            "proposed_changes": [],
            "confidence": 0.5,
            "evidence_ids": [],
            "evaluator_meta": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "proposed",
            "schema_version": None,
        }
        p = GrowthProposal.from_dict(d)
        assert p.schema_version == "1.0"

    def test_canonical_from_dict_empty_string_falls_back(self):
        """T1.8: from_dict() 在 schema_version='' 时回退到 '1.0'"""
        from src.contracts.growth_schema import GrowthProposal
        d = {
            "id": "prop_test_005",
            "proposed_changes": [],
            "confidence": 0.5,
            "evidence_ids": [],
            "evaluator_meta": {},
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "proposed",
            "schema_version": "",
        }
        p = GrowthProposal.from_dict(d)
        assert p.schema_version == "1.0"


# ============================================================
# T2: legacy schema 仍可 normalize
# ============================================================

class TestLegacySchemaStillNormalizable:
    """legacy schema 仍可被 Normalizer 归一化（不破坏 backward compatibility）。"""

    def test_legacy_input_normalized_to_canonical(self, sample_legacy_governance):
        """T2.1: legacy dict → normalize_to_canonical → canonical dict 成功"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert isinstance(canonical, dict)
        # canonical 7 核心字段 + schema_version
        for k in (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        ):
            assert k in canonical, f"canonical 缺少 {k}"

    def test_legacy_normalized_preserves_all_fields(self, sample_legacy_governance):
        """T2.2: legacy → canonical 所有治理字段可追溯"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})
        # proposal_id 保留
        assert canonical["id"] == sample_legacy_governance["proposal_id"]
        # affected_dimensions 派生
        assert len(canonical["proposed_changes"]) > 0
        # before_state / after_state 保留
        assert origin.get("before_state", {}).get("curiosity") == 0.5
        assert origin.get("after_state", {}).get("curiosity") == 0.55
        # evidence 保留
        assert canonical["evidence_ids"] == sample_legacy_governance["evidence"]
        # confidence 保留
        assert abs(canonical["confidence"] - 0.82) < 1e-5
        # reason 保留
        assert em.get("reason") == sample_legacy_governance["reason"]
        # priority 保留
        assert origin.get("priority") == sample_legacy_governance["priority"]

    def test_legacy_to_pcr_via_translator(self, sample_legacy_governance):
        """T2.3: legacy → NormalizedProposalTranslator → PCR 成功"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_legacy_governance)
        assert pcr["source_proposal_id"] == sample_legacy_governance["proposal_id"]
        assert pcr["evaluator_meta"].get("_source_schema") == "proposal"

    def test_legacy_growthproposal_instantiable(self):
        """T2.4: legacy GrowthProposal 仍可实例化（向后兼容）"""
        from src.growth.proposal.proposal import GrowthProposal as LegacyGP
        from src.growth.proposal.constants import PROPOSAL_STATUS
        p = LegacyGP(
            proposal_id="prop_legacy_compat_001",
            status=PROPOSAL_STATUS["PENDING"],
        )
        assert p.proposal_id == "prop_legacy_compat_001"

    def test_legacy_to_dict_still_works(self):
        """T2.5: legacy.to_dict 仍能工作（不破坏既有调用方）"""
        from src.growth.proposal.proposal import GrowthProposal as LegacyGP
        p = LegacyGP(proposal_id="prop_legacy_002")
        d = p.to_dict()
        assert d["proposal_id"] == "prop_legacy_002"


# ============================================================
# T3: round-trip 不丢字段
# ============================================================

class TestRoundTripPreservesFields:
    """canonical schema 经过 normalize → to_dict → from_dict 字段不丢。"""

    def test_canonical_round_trip_preserves_all_v1_fields(self, sample_canonical):
        """T3.1: canonical → to_dict → from_dict 11 字段全部保留"""
        from src.contracts.growth_schema import GrowthProposal
        p1 = GrowthProposal.from_dict(sample_canonical)
        d1 = p1.to_dict()
        p2 = GrowthProposal.from_dict(d1)
        d2 = p2.to_dict()
        # 11 个核心字段全部保留
        for k in CANONICAL_V1_FIELDS:
            assert k in d1, f"to_dict 缺 {k}"
            assert k in d2, f"from_dict 缺 {k}"
        # 关键字段值一致
        assert d1["id"] == d2["id"] == sample_canonical["id"]
        assert d1["schema_version"] == d2["schema_version"] == "1.0"
        assert d1["confidence"] == d2["confidence"]

    def test_canonical_governance_round_trip(self, sample_legacy_governance):
        """T3.2: legacy → canonical → governance view 字段保留"""
        from src.contracts.proposal_normalizer import (
            normalize_to_canonical,
            to_governance_view,
        )
        canonical = normalize_to_canonical(sample_legacy_governance)
        # 核心字段
        assert canonical["id"] == sample_legacy_governance["proposal_id"]
        assert canonical["evidence_ids"] == sample_legacy_governance["evidence"]
        # 再回到 governance view
        gov = to_governance_view(canonical)
        assert gov["proposal_id"] == sample_legacy_governance["proposal_id"]
        assert gov["evidence"] == sample_legacy_governance["evidence"]

    def test_v0_canonical_upgrades_to_v1(self, sample_canonical_v0):
        """T3.3: v0 canonical（无 schema_version）反序列化为 v1.0"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal.from_dict(sample_canonical_v0)
        assert p.schema_version == "1.0"
        # 其他字段也保留
        assert p.id == sample_canonical_v0["id"]
        assert p.confidence == sample_canonical_v0["confidence"]

    def test_pcr_field_round_trip_preserves_schema_marker(self, sample_canonical):
        """T3.4: canonical → PCR → 关键 schema 标记保留"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_canonical)
        # 关键 schema 标记
        assert pcr["source_proposal_id"] == sample_canonical["id"]
        assert pcr["evaluator_meta"].get("_source_schema") == "growth_schema"
        # evaluator_meta.reason_summary 保留
        assert (
            pcr["evaluator_meta"].get("reason_summary")
            == sample_canonical["evaluator_meta"]["reason_summary"]
        )


# ============================================================
# T4: consumer 不 import legacy schema
# ============================================================

class TestConsumerDoesNotImportLegacy:
    """业务模块（consumer / translator / diagnostic）不直接 import legacy schema。"""

    def test_consumer_modules_no_legacy_import(self):
        """T4.1: 关键 consumer 模块不直接 import legacy schema"""
        forbidden_patterns = [
            "from src.growth.proposal.proposal import GrowthProposal",
            "from src.growth.proposal import GrowthProposal",
        ]
        for module_path in CONSUMER_MODULES:
            if not module_path.exists():
                continue
            content = module_path.read_text(encoding="utf-8", errors="ignore")
            for pat in forbidden_patterns:
                assert pat not in content, (
                    f"{module_path.name} 不应直接 import legacy schema:\n"
                    f"  命中: {pat}\n"
                    f"  请改用 NormalizedProposalTranslator 入口"
                )

    def test_normalized_translator_no_legacy_import(self):
        """T4.2: NormalizedProposalTranslator 不依赖 legacy schema"""
        target = PROJECT_ROOT / "src" / "admin" / "normalized_proposal_translator.py"
        if not target.exists():
            pytest.skip("normalized_proposal_translator.py 不存在")
        content = target.read_text(encoding="utf-8")
        forbidden_patterns = [
            "from src.growth.proposal.proposal import",
            "from src.growth.proposal import GrowthProposal",
        ]
        for pat in forbidden_patterns:
            assert pat not in content

    def test_selfmodel_consumer_uses_normalized_translator(self):
        """T4.3: selfmodel_consumer.process() 使用 NormalizedProposalTranslator"""
        target = PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer.py"
        if not target.exists():
            pytest.skip("selfmodel_consumer.py 不存在")
        content = target.read_text(encoding="utf-8")
        assert "NormalizedProposalTranslator" in content, (
            "selfmodel_consumer.py 必须使用 NormalizedProposalTranslator"
        )

    def test_legacy_import_only_in_allowed_layers(self):
        """T4.4: legacy schema 仅在 storage / reviewer / governance_provider / adapter 兼容层被 import"""
        # 允许的 import 位置
        allowed_files = {
            PROJECT_ROOT / "src" / "growth" / "proposal" / "storage.py",
            PROJECT_ROOT / "src" / "growth" / "proposal" / "reviewer.py",
            PROJECT_ROOT / "src" / "growth" / "proposal" / "__init__.py",
            PROJECT_ROOT / "src" / "admin" / "governance_provider.py",
            PROJECT_ROOT / "src" / "runtime" / "adapters" / "growth_proposal_adapter.py",
        }
        # 扫描整个 src/admin 和 src/runtime
        scan_dirs = [
            PROJECT_ROOT / "src" / "admin",
            PROJECT_ROOT / "src" / "runtime",
        ]
        violations: List[str] = []
        for d in scan_dirs:
            if not d.exists():
                continue
            for py in d.rglob("*.py"):
                if "__pycache__" in str(py):
                    continue
                if py in allowed_files:
                    continue
                content = py.read_text(encoding="utf-8", errors="ignore")
                if (
                    "from src.growth.proposal.proposal import GrowthProposal" in content
                    or "from src.growth.proposal import GrowthProposal" in content
                ):
                    violations.append(str(py.relative_to(PROJECT_ROOT)))

        assert not violations, (
            "以下模块直接 import legacy schema（违反 Phase 3.6.5 import 方向约束）:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )


# ============================================================
# T5: runtime/personality 不依赖 legacy schema
# ============================================================

class TestRuntimeAndPersonalityDoNotDependOnLegacy:
    """Runtime / Personality 模块不直接依赖 legacy schema。"""

    def test_runtime_no_legacy_import(self):
        """T5.1: src/runtime/* 不应 import legacy schema（除允许位置）"""
        allowed = {
            PROJECT_ROOT / "src" / "runtime" / "adapters" / "growth_proposal_adapter.py",
        }
        runtime_dir = PROJECT_ROOT / "src" / "runtime"
        if not runtime_dir.exists():
            pytest.skip("src/runtime 不存在")
        violations: List[str] = []
        for py in runtime_dir.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            if py in allowed:
                continue
            content = py.read_text(encoding="utf-8", errors="ignore")
            if (
                "from src.growth.proposal.proposal import GrowthProposal" in content
                or "from src.growth.proposal import GrowthProposal" in content
            ):
                violations.append(str(py.relative_to(PROJECT_ROOT)))
        assert not violations, (
            "以下 runtime 模块直接 import legacy schema:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_personality_no_legacy_import(self):
        """T5.2: src/personality/* 不应 import legacy schema"""
        personality_dir = PROJECT_ROOT / "src" / "personality"
        if not personality_dir.exists():
            pytest.skip("src/personality 不存在")
        violations: List[str] = []
        for py in personality_dir.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            content = py.read_text(encoding="utf-8", errors="ignore")
            if (
                "from src.growth.proposal.proposal import GrowthProposal" in content
                or "from src.growth.proposal import GrowthProposal" in content
            ):
                violations.append(str(py.relative_to(PROJECT_ROOT)))
        assert not violations, (
            "以下 personality 模块直接 import legacy schema:\n"
            + "\n".join(f"  - {v}" for v in violations)
        )

    def test_runtime_no_forbidden_modules(self):
        """T5.3: contracts/proposal_normalizer.py 不 import Runtime"""
        if not NORMALIZER_PATH.exists():
            pytest.skip("proposal_normalizer.py 不存在")
        content = NORMALIZER_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content
            assert f"from {forbidden}" not in content


# ============================================================
# T6: migration policy 文档存在
# ============================================================

class TestMigrationPolicyDocumentExists:
    """migration policy / lifecycle 文档存在。"""

    def test_lifecycle_doc_exists(self):
        """T6.1: docs/growth_proposal_lifecycle.md 存在"""
        assert LIFECYCLE_DOC_PATH.exists(), (
            f"lifecycle 文档不存在: {LIFECYCLE_DOC_PATH}\n"
            f"Phase 3.6.5 要求提供完整生命周期文档"
        )

    def test_lifecycle_doc_contains_key_sections(self):
        """T6.2: lifecycle 文档包含关键章节"""
        assert LIFECYCLE_DOC_PATH.exists()
        content = LIFECYCLE_DOC_PATH.read_text(encoding="utf-8")
        required_sections = [
            "Legacy Governance Proposal",
            "GrowthProposalNormalizer",
            "Canonical GrowthProposal",
            "GrowthProposalTranslator",
            "Personality Change Request",
            "Personality Update",
            "Schema Migration Policy",
        ]
        for section in required_sections:
            assert section in content, (
                f"lifecycle 文档缺少章节: {section}"
            )

    def test_lifecycle_doc_has_migration_policy(self):
        """T6.3: lifecycle 文档含 migration policy"""
        assert LIFECYCLE_DOC_PATH.exists()
        content = LIFECYCLE_DOC_PATH.read_text(encoding="utf-8")
        # 关键策略点
        assert "Deprecated" in content or "deprecated" in content
        assert "Source of Truth" in content or "唯一内部标准" in content
        assert "Import Direction" in content or "import 方向" in content
        # legacy / canonical 角色
        assert "legacy" in content.lower()
        assert "canonical" in content.lower()

    def test_lifecycle_doc_has_schema_version_section(self):
        """T6.4: lifecycle 文档含 schema_version 章节"""
        assert LIFECYCLE_DOC_PATH.exists()
        content = LIFECYCLE_DOC_PATH.read_text(encoding="utf-8")
        assert "schema_version" in content
        assert "1.0" in content


# ============================================================
# T7: 字段契约冻结
# ============================================================

class TestFieldContractFrozen:
    """canonical schema 字段契约在 v1.0 冻结。"""

    def test_canonical_has_11_core_fields(self):
        """T7.1: canonical GrowthProposal 有 11 个核心字段"""
        from src.contracts.growth_schema import GrowthProposal
        import dataclasses
        fields = [f.name for f in dataclasses.fields(GrowthProposal)]
        for k in CANONICAL_V1_FIELDS:
            assert k in fields, f"canonical 缺少字段 {k}"
        assert len(fields) == len(CANONICAL_V1_FIELDS), (
            f"canonical 字段数应为 {len(CANONICAL_V1_FIELDS)}，实际 {len(fields)}"
        )

    def test_canonical_field_order_locked(self):
        """T7.2: canonical 字段顺序锁定（v1.0）"""
        from src.contracts.growth_schema import GrowthProposal
        import dataclasses
        fields = [f.name for f in dataclasses.fields(GrowthProposal)]
        assert fields == CANONICAL_V1_FIELDS, (
            f"字段顺序变化:\n"
            f"  期望: {CANONICAL_V1_FIELDS}\n"
            f"  实际: {fields}"
        )

    def test_canonical_field_types_locked(self):
        """T7.3: canonical 字段类型锁定"""
        from src.contracts.growth_schema import GrowthProposal
        import dataclasses
        for f in dataclasses.fields(GrowthProposal):
            assert f.type is not None, f"字段 {f.name} 缺类型注解"
        # 关键字段类型校验
        type_map = {f.name: f.type for f in dataclasses.fields(GrowthProposal)}
        assert type_map["id"] == "str"
        assert type_map["schema_version"] == "str"
        assert type_map["confidence"] == "float"

    def test_canonical_change_item_frozen(self):
        """T7.4: ChangeItem 字段冻结"""
        from src.contracts.growth_schema import ChangeItem
        import dataclasses
        fields = [f.name for f in dataclasses.fields(ChangeItem)]
        expected = ["path", "before", "after", "reason"]
        assert fields == expected, (
            f"ChangeItem 字段变化:\n  期望 {expected}\n  实际 {fields}"
        )

    def test_legacy_schema_did_not_add_schema_version(self):
        """T7.5: legacy schema **不**含 schema_version 字段（冻结）"""
        if not LEGACY_SCHEMA_PATH.exists():
            pytest.skip("legacy schema 文件不存在")
        content = LEGACY_SCHEMA_PATH.read_text(encoding="utf-8")
        # 字段定义中不应出现 schema_version
        # 简单检测：行内不应有 "schema_version:" 字段定义
        lines = content.split("\n")
        for line in lines:
            stripped = line.strip()
            # 跳过 docstring / 注释
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "schema_version" in stripped and ":" in stripped:
                # 字段定义形式（如 "schema_version: str = ..."）
                if "field(default" in stripped or "= \"" in stripped or "= \"" in stripped:
                    pytest.fail(
                        f"legacy schema 不应新增 schema_version 字段: {stripped}"
                    )

    def test_normalizer_preserves_schema_version(self, sample_canonical):
        """T7.6: Normalizer 透传 schema_version 字段"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_canonical)
        # Normalizer 不应该删除 schema_version
        assert "schema_version" in canonical
        assert canonical["schema_version"] == sample_canonical["schema_version"]


# ============================================================
# T8: 反依赖基线
# ============================================================

class TestNoForbiddenDependencies:
    """Phase 3.6.5 不引入新的反向依赖。"""

    def test_canonical_schema_no_runtime_import(self):
        """T8.1: canonical schema 不 import Runtime / Orchestrator"""
        if not CANONICAL_SCHEMA_PATH.exists():
            pytest.skip("canonical schema 文件不存在")
        content = CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content
            assert f"from {forbidden}" not in content

    def test_legacy_schema_no_runtime_import(self):
        """T8.2: legacy schema 不 import Runtime / Orchestrator"""
        if not LEGACY_SCHEMA_PATH.exists():
            pytest.skip("legacy schema 文件不存在")
        content = LEGACY_SCHEMA_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content
            assert f"from {forbidden}" not in content

    def test_normalizer_no_runtime_import(self):
        """T8.3: Normalizer 不 import Runtime / Orchestrator"""
        if not NORMALIZER_PATH.exists():
            pytest.skip("proposal_normalizer.py 不存在")
        content = NORMALIZER_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content
            assert f"from {forbidden}" not in content


# ============================================================
# T9: Schema 治理架构目标验证
# ============================================================

class TestSchemaGovernanceArchitectureFrozen:
    """Phase 3.6.5 schema 治理架构目标验证。"""

    def test_canonical_is_source_of_truth(self):
        """T9.1: canonical schema 是唯一 source of truth"""
        from src.contracts.growth_schema import GrowthProposal
        # 类存在
        assert GrowthProposal is not None
        # CANONICAL_SCHEMA_VERSION 常量存在
        from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
        assert CANONICAL_SCHEMA_VERSION == "1.0"

    def test_legacy_is_deprecated(self):
        """T9.2: legacy schema 已 deprecated"""
        from src.growth.proposal.proposal import GrowthProposal as LegacyGP
        assert getattr(LegacyGP, "__deprecated__", False) is True
        assert getattr(LegacyGP, "__deprecated_since__", "") == "3.6.4"

    def test_normalizer_is_single_translation_layer(self):
        """T9.3: Normalizer 是唯一归一化层"""
        from src.contracts.proposal_normalizer import (
            GrowthProposalNormalizer,
            normalize_to_canonical,
            detect,
            to_governance_view,
        )
        assert callable(GrowthProposalNormalizer.normalize_to_canonical)
        assert callable(GrowthProposalNormalizer.detect)
        assert callable(GrowthProposalNormalizer.to_governance_view)
        assert callable(normalize_to_canonical)
        assert callable(detect)
        assert callable(to_governance_view)

    def test_normalized_translator_is_standard_entry(self):
        """T9.4: NormalizedProposalTranslator 是业务层统一入口"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        assert callable(NormalizedProposalTranslator.translate)
        assert callable(NormalizedProposalTranslator.normalize)
        assert callable(NormalizedProposalTranslator.detect)

    def test_full_lifecycle_works(self, sample_legacy_governance):
        """T9.5: 完整 lifecycle 端到端跑通"""
        from src.contracts.proposal_normalizer import (
            normalize_to_canonical,
            detect,
        )
        from src.contracts.growth_schema import GrowthProposal as Canonical
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )

        # Step 1: detect (识别 legacy schema)
        kind = detect(sample_legacy_governance)
        assert kind == "governance"

        # Step 2: normalize (legacy → canonical dict)
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert canonical["id"] == sample_legacy_governance["proposal_id"]
        # 7 核心字段存在
        for k in (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        ):
            assert k in canonical, f"canonical 缺 {k}"

        # Step 3: canonical dict → GrowthProposal dataclass
        # 验证 schema_version 在 dataclass 层正确填充（默认 "1.0"）
        canonical_obj = Canonical.from_dict(canonical)
        assert canonical_obj.schema_version == "1.0", (
            "GrowthProposal.from_dict() 应回填 schema_version='1.0'"
        )

        # Step 4: translate (canonical → PCR)
        # canonical dict 直接喂入，_normalized 为 False（无需再归一化）
        pcr = NormalizedProposalTranslator.translate(canonical)
        assert pcr["source_proposal_id"] == sample_legacy_governance["proposal_id"]
        assert pcr["request_id"].startswith("pcr_")
        assert pcr["evaluator_meta"].get("_source_schema") == "growth_schema"
        # canonical 直通：_normalized 为 False（不走归一化）
        assert pcr["evaluator_meta"].get("_normalized") in (False, None)

        # Step 5: 完整链路（legacy → canonical → PCR，_normalized=True）
        # 模拟真实场景：直接传 legacy dict 给 translator（内含 normalize 步骤）
        pcr_full = NormalizedProposalTranslator.translate(sample_legacy_governance)
        assert pcr_full["source_proposal_id"] == sample_legacy_governance["proposal_id"]
        assert pcr_full["evaluator_meta"].get("_source_schema") == "proposal"
        # 走过了归一化：_normalized 为 True
        assert pcr_full["evaluator_meta"].get("_normalized") is True


# ============================================================
# 主入口（直接运行时打印治理状态）
# ============================================================

def test_phase_3_6_5_freeze_summary():
    """汇总 Phase 3.6.5 schema freeze 状态（用于完成报告参考）"""
    # 1) canonical schema version 存在
    from src.contracts.growth_schema import (
        GrowthProposal as Canonical,
        CANONICAL_SCHEMA_VERSION,
    )
    assert CANONICAL_SCHEMA_VERSION == "1.0"
    # 2) 默认实例化时 schema_version = "1.0"
    p = Canonical()
    assert p.schema_version == "1.0"
    # 3) legacy schema 仍可实例化
    from src.growth.proposal.proposal import GrowthProposal as Legacy
    legacy_p = Legacy(proposal_id="prop_summary_001")
    assert legacy_p.proposal_id == "prop_summary_001"
    # 4) Normalizer / Translator 存在
    from src.contracts.proposal_normalizer import GrowthProposalNormalizer
    from src.admin.normalized_proposal_translator import (
        NormalizedProposalTranslator,
    )
    assert GrowthProposalNormalizer is not None
    assert NormalizedProposalTranslator is not None
    # 5) lifecycle 文档存在
    assert LIFECYCLE_DOC_PATH.exists()
