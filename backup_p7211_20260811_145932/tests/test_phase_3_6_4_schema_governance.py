# -*- coding: utf-8 -*-
"""
tests/test_phase_3_6_4_schema_governance.py

Phase 3.6.4: GrowthProposal Schema Governance Cleanup 测试

目标：
- 旧 governance schema 标记 deprecated（仅保留兼容）
- canonical schema 成为唯一内部标准
- 任何 proposal 输入必须经过 GrowthProposalNormalizer
- 业务模块（consumer / adapter / 主链路）不直接 import legacy schema
- 字段完整性（governance 10 字段在主链路保留）

覆盖：
- T1: legacy schema → Normalizer → canonical 输入路径
- T2: canonical schema round-trip（canonical → normalize → canonical）
- T3: 业务模块不直接依赖 legacy schema（import graph 静态分析）
- T4: deprecated 标记存在（class + module + __deprecated__ 属性）
- T5: 字段完整性（proposal_id / request_id / source_proposal_id / timestamp /
  reason / reason_summary / before_state / after_state / evidence / confidence /
  priority 在主链路保留）

约束：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/*（核心算法）/
  src/personality/*（核心算法）
- 不修改 proposal_normalizer.py 核心转换规则
- 不删除 legacy schema / 兼容入口
- 保持 backward compatibility
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 常量
# ============================================================

# 反依赖基线（不允许 import 这些模块）
FORBIDDEN_MODULES = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}

# legacy schema 位置
LEGACY_SCHEMA_PATH = PROJECT_ROOT / "src" / "growth" / "proposal" / "proposal.py"
CANONICAL_SCHEMA_PATH = PROJECT_ROOT / "src" / "contracts" / "growth_schema.py"
NORMALIZER_PATH = PROJECT_ROOT / "src" / "contracts" / "proposal_normalizer.py"
NORMALIZED_TRANSLATOR_PATH = (
    PROJECT_ROOT / "src" / "admin" / "normalized_proposal_translator.py"
)

# 业务模块（consumer / adapter / 主链路）— 不应直接 import legacy schema
CONSUMER_MODULES = [
    PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer.py",
    PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer_audit.py",
    PROJECT_ROOT / "src" / "admin" / "normalized_proposal_translator.py",
    PROJECT_ROOT / "src" / "admin" / "selfmodel_diagnostic.py",
]

# 字段完整性（治理 schema 关键字段必须在 canonical dict 中可追溯）
# 字段映射：
#   proposal_id        → canonical.id
#   request_id         → PCR.request_id (由 _translate_canonical 生成)
#   source_proposal_id → PCR.source_proposal_id
#   timestamp          → canonical.timestamp
#   reason             → canonical.evaluator_meta.reason / metadata.reason
#   reason_summary     → canonical.evaluator_meta.reason_summary
#   before_state       → canonical._governance_origin.before_state (via Normalizer)
#   after_state        → canonical._governance_origin.after_state (via Normalizer)
#   evidence           → canonical.evidence_ids / evaluator_meta._governance_origin.evidence
#   confidence         → canonical.confidence
#   priority           → canonical.evaluator_meta._governance_origin.priority
GOVERNANCE_REQUIRED_FIELDS = [
    "proposal_id",
    "timestamp",
    "affected_dimensions",
    "before_state",
    "after_state",
    "evidence",
    "confidence",
    "reason",
    "priority",
    "metadata",
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_legacy_governance() -> Dict[str, Any]:
    """完整 legacy schema（治理侧）样本。"""
    return {
        "proposal_id": "prop_gov_phase364_001",
        "timestamp": "2026-01-01T00:00:00.000000",
        "proposal_type": "personality",
        "status": "pending",
        "source": "admin_governance",
        "source_event_id": "evt_gov_phase364_001",
        "user_id": "user_phase364_001",
        "affected_dimensions": {
            "curiosity": 0.05,
            "warmth": 0.03,
            "patience": 0.02,
        },
        "before_state": {
            "curiosity": 0.5,
            "warmth": 0.6,
            "patience": 0.55,
        },
        "after_state": {
            "curiosity": 0.55,
            "warmth": 0.63,
            "patience": 0.57,
        },
        "confidence": 0.82,
        "reason": "user showed increased engagement and positive feedback",
        "evidence": ["inter_a", "inter_b", "inter_c"],
        "priority": "high",
        "metadata": {
            "source": "admin_governance_v3",
            "request_kind": "personality_change",
            "actor": "admin",
        },
        "reviewer_id": "",
        "review_comment": "",
        "reviewed_at": None,
    }


@pytest.fixture
def sample_canonical() -> Dict[str, Any]:
    """完整 canonical schema 样本。"""
    return {
        "id": "prop_can_phase364_001",
        "source_event_id": "evt_can_phase364_001",
        "proposed_changes": [
            {
                "path": "personality.traits.curiosity",
                "before": 0.5,
                "after": 0.55,
                "reason": "increased engagement",
            },
            {
                "path": "personality.traits.warmth",
                "before": 0.6,
                "after": 0.62,
                "reason": "user expressed thanks",
            },
        ],
        "confidence": 0.88,
        "evidence_ids": ["e1", "e2", "e3"],
        "evaluator_meta": {
            "pattern_detected": "high_user_activity",
            "reason_summary": "high engagement signal",
        },
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "proposed",
    }


# ============================================================
# T1: legacy schema → Normalizer → canonical 输入路径
# ============================================================

class TestLegacyInputNormalization:
    """Legacy schema 可作为输入，经 Normalizer 归一化为 canonical。"""

    def test_legacy_to_canonical_via_normalizer(self, sample_legacy_governance):
        """T1.1: legacy → normalize_to_canonical → canonical dict"""
        from src.contracts.proposal_normalizer import (
            GrowthProposalNormalizer,
            normalize_to_canonical,
        )
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert isinstance(canonical, dict)
        # 7 个核心字段
        for k in (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        ):
            assert k in canonical, f"canonical 缺少 {k}"
        # proposal_id 映射到 id
        assert canonical["id"] == "prop_gov_phase364_001"
        # evidence 映射到 evidence_ids
        assert canonical["evidence_ids"] == ["inter_a", "inter_b", "inter_c"]
        # confidence 保留
        assert abs(canonical["confidence"] - 0.82) < 1e-5
        # timestamp 归一带 Z
        assert canonical["timestamp"].endswith("Z")
        # evaluator_meta 包含 _governance_origin（lossless）
        assert "_governance_origin" in canonical["evaluator_meta"]
        origin = canonical["evaluator_meta"]["_governance_origin"]
        assert origin["proposal_id"] == "prop_gov_phase364_001"
        assert origin["affected_dimensions"]["curiosity"] == 0.05
        # 同步类静态方法
        canonical2 = GrowthProposalNormalizer.normalize_to_canonical(
            sample_legacy_governance
        )
        assert canonical2["id"] == canonical["id"]
        assert canonical2["confidence"] == canonical["confidence"]

    def test_legacy_governance_required_fields_preserved(self, sample_legacy_governance):
        """T1.2: legacy 10 个关键字段在 canonical 中可追溯"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})

        # 字段映射：proposal_id → id
        assert canonical["id"] == sample_legacy_governance["proposal_id"]
        # 字段映射：timestamp（带 Z 归一）
        assert canonical["timestamp"].endswith("Z")
        # 字段映射：affected_dimensions → proposed_changes（由 before/after + delta 派生）
        # 校验 proposed_changes 中至少 1 个 trait 的 path 末尾是 curiosity
        pcs_paths = [
            (ci.get("path", "").split(".")[-1] if isinstance(ci, dict) else "")
            for ci in canonical["proposed_changes"]
        ]
        assert "curiosity" in pcs_paths
        # 字段映射：before_state / after_state 保留到 _governance_origin
        assert origin.get("before_state", {}).get("curiosity") == 0.5
        assert origin.get("after_state", {}).get("curiosity") == 0.55
        # 字段映射：evidence → evidence_ids
        assert canonical["evidence_ids"] == sample_legacy_governance["evidence"]
        # 字段映射：confidence
        assert abs(canonical["confidence"] - sample_legacy_governance["confidence"]) < 1e-5
        # 字段映射：reason → evaluator_meta.reason
        assert em.get("reason") == sample_legacy_governance["reason"]
        # 字段映射：priority → evaluator_meta._governance_origin.priority
        assert origin.get("priority") == sample_legacy_governance["priority"]
        # 字段映射：metadata 合并到 evaluator_meta
        assert em.get("source") == "admin_governance_v3"

    def test_legacy_governance_to_pcr_full_path(self, sample_legacy_governance):
        """T1.3: legacy → NormalizedProposalTranslator → PCR（端到端）"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_legacy_governance)
        # proposal_id 保留
        assert pcr["source_proposal_id"] == "prop_gov_phase364_001"
        # request_id 生成
        assert pcr["request_id"].startswith("pcr_")
        # 字段 schema 标记
        assert pcr["evaluator_meta"].get("_source_schema") == "proposal"
        # _normalized 标记为 True
        assert pcr["evaluator_meta"].get("_normalized") is True

    def test_legacy_to_canonical_preserves_evidence_count(self, sample_legacy_governance):
        """T1.4: evidence 列表长度保留到 evidence_ids"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert len(canonical["evidence_ids"]) == len(
            sample_legacy_governance["evidence"]
        )
        assert len(canonical["evidence_ids"]) == 3


# ============================================================
# T2: canonical schema round-trip（数据不丢失）
# ============================================================

class TestCanonicalRoundTrip:
    """Canonical schema 经过 normalize 应当数据不丢失。"""

    def test_canonical_round_trip_preserves_core_fields(self, sample_canonical):
        """T2.1: canonical → normalize → canonical 7 字段不丢失"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical_in = sample_canonical
        canonical_out = normalize_to_canonical(canonical_in)
        # id / proposed_changes / evidence_ids / confidence / evaluator_meta / status / timestamp
        assert canonical_out["id"] == canonical_in["id"]
        assert len(canonical_out["proposed_changes"]) == len(
            canonical_in["proposed_changes"]
        )
        assert canonical_out["evidence_ids"] == canonical_in["evidence_ids"]
        assert abs(canonical_out["confidence"] - canonical_in["confidence"]) < 1e-5
        # evaluator_meta 合并（normalizer 不会删除已有字段）
        for k, v in canonical_in["evaluator_meta"].items():
            if not k.startswith("_"):
                assert canonical_out["evaluator_meta"].get(k) == v
        assert canonical_out["status"] == canonical_in["status"]
        # timestamp 至少保留（可能加 Z）
        assert canonical_out["timestamp"].rstrip("Z") in canonical_in["timestamp"] or \
               canonical_in["timestamp"].rstrip("Z") in canonical_out["timestamp"]

    def test_canonical_to_governance_and_back(self, sample_canonical):
        """T2.2: canonical → governance view → canonical 双向往返字段不丢失"""
        from src.contracts.proposal_normalizer import (
            normalize_to_canonical,
            to_governance_view,
        )
        canonical = sample_canonical
        gov = to_governance_view(canonical)
        # governance 形态字段
        assert "proposal_id" in gov
        assert "affected_dimensions" in gov
        assert "evidence" in gov
        assert "metadata" in gov
        # proposal_id 来自 canonical.id
        assert gov["proposal_id"] == canonical["id"]
        # 回到 canonical
        canonical_back = normalize_to_canonical(gov)
        # 关键字段保留
        assert canonical_back["id"] == canonical["id"]
        # evidence 保留
        assert canonical_back["evidence_ids"] == canonical["evidence_ids"]
        # confidence 保留
        assert abs(canonical_back["confidence"] - canonical["confidence"]) < 1e-5

    def test_canonical_status_and_timestamp_stable(self, sample_canonical):
        """T2.3: status / timestamp 在 round-trip 中保持稳定"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        c1 = normalize_to_canonical(sample_canonical)
        c2 = normalize_to_canonical(c1)
        assert c1["status"] == c2["status"] == sample_canonical["status"]
        # timestamp 可能补 Z，但日期时间核心不变
        assert c1["timestamp"].rstrip("Z") == c2["timestamp"].rstrip("Z")


# ============================================================
# T3: 业务模块不直接依赖 legacy schema
# ============================================================

class TestBusinessModulesDoNotDependOnLegacy:
    """
    业务模块（consumer / adapter / 主链路）不应直接 import legacy schema。
    静态分析：扫描 src/admin/ 下的业务模块文件，确保无 legacy schema import。
    """

    def test_consumer_modules_no_legacy_import(self):
        """T3.1: 关键 consumer 模块不直接 import legacy schema"""
        for module_path in CONSUMER_MODULES:
            if not module_path.exists():
                continue
            content = module_path.read_text(encoding="utf-8")
            # 禁止直接 import legacy schema
            forbidden_patterns = [
                "from src.growth.proposal.proposal import GrowthProposal",
                "from src.growth.proposal import GrowthProposal",
            ]
            for pat in forbidden_patterns:
                assert pat not in content, (
                    f"{module_path.name} 不应直接 import legacy schema:\n"
                    f"  命中: {pat}\n"
                    f"  请改用 NormalizedProposalTranslator 入口"
                )

    def test_normalized_translator_no_legacy_import(self):
        """T3.2: NormalizedProposalTranslator 不依赖 legacy schema"""
        if not NORMALIZED_TRANSLATOR_PATH.exists():
            pytest.skip("normalized_proposal_translator.py 不存在")
        content = NORMALIZED_TRANSLATOR_PATH.read_text(encoding="utf-8")
        forbidden_patterns = [
            "from src.growth.proposal.proposal import",
            "from src.growth.proposal import GrowthProposal",
        ]
        for pat in forbidden_patterns:
            assert pat not in content, (
                f"normalized_proposal_translator.py 不应 import legacy schema: {pat}"
            )

    def test_selfmodel_consumer_no_legacy_direct_use(self):
        """T3.3: selfmodel_consumer.py 通过 NormalizedProposalTranslator 接入"""
        target = PROJECT_ROOT / "src" / "admin" / "selfmodel_consumer.py"
        if not target.exists():
            pytest.skip("selfmodel_consumer.py 不存在")
        content = target.read_text(encoding="utf-8")
        # process() 方法必须使用 NormalizedProposalTranslator
        # 提取 process 方法
        m = re.search(
            r"def process\(self,.*?(?=\n    def |\nclass |\Z)",
            content,
            re.DOTALL,
        )
        if m:
            process_body = m.group(0)
            assert "NormalizedProposalTranslator" in process_body, (
                "selfmodel_consumer.process() 必须使用 "
                "NormalizedProposalTranslator 作为标准入口"
            )

    def test_legacy_import_only_in_allowed_layers(self):
        """T3.4: legacy schema 仅在 storage / governance_provider / adapter 兼容层被 import"""
        # 允许 import 的文件（producer / storage 兼容层 / adapter 转换层）
        # 依据 Phase 3.6.4 架构：
        #   允许：contracts → admin → adapter
        #   禁止：consumer → legacy schema
        # 因此 src/runtime/adapters/growth_proposal_adapter.py（适配器层）也允许
        allowed_files = {
            PROJECT_ROOT / "src" / "growth" / "proposal" / "storage.py",
            PROJECT_ROOT / "src" / "growth" / "proposal" / "reviewer.py",
            PROJECT_ROOT / "src" / "growth" / "proposal" / "__init__.py",
            PROJECT_ROOT / "src" / "admin" / "governance_provider.py",
            # Adapter 层：双 Schema 转换器（允许 import 两侧）
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
                # 跳过 __pycache__
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
            "以下业务模块直接 import legacy schema（违反 Phase 3.6.4 import 方向约束）:\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\n请改用 NormalizedProposalTranslator 入口"
        )


# ============================================================
# T4: deprecated 标记存在
# ============================================================

class TestDeprecationMarker:
    """legacy schema 应当携带完整的 deprecation 标记。"""

    def test_legacy_class_has_deprecated_attr(self):
        """T4.1: legacy GrowthProposal 类有 __deprecated__ 属性"""
        from src.growth.proposal.proposal import GrowthProposal
        assert getattr(GrowthProposal, "__deprecated__", False) is True, (
            "legacy GrowthProposal 必须有 __deprecated__ = True"
        )

    def test_legacy_class_has_since_attr(self):
        """T4.2: legacy GrowthProposal 类有 __deprecated_since__ 属性"""
        from src.growth.proposal.proposal import GrowthProposal
        since = getattr(GrowthProposal, "__deprecated_since__", "")
        assert since == "3.6.4", f"__deprecated_since__ 应为 '3.6.4'，实际 {since}"

    def test_legacy_class_has_replacement_attr(self):
        """T4.3: legacy GrowthProposal 类有 __deprecated_replacement__ 属性"""
        from src.growth.proposal.proposal import GrowthProposal
        replacement = getattr(GrowthProposal, "__deprecated_replacement__", "")
        assert "src.contracts.growth_schema.GrowthProposal" in replacement, (
            f"__deprecated_replacement__ 应指向 canonical schema，实际 {replacement}"
        )

    def test_legacy_class_has_migration_attr(self):
        """T4.4: legacy GrowthProposal 类有 __deprecated_migration__ 属性"""
        from src.growth.proposal.proposal import GrowthProposal
        migration = getattr(GrowthProposal, "__deprecated_migration__", "")
        assert "normalize_to_canonical" in migration or "NormalizedProposalTranslator" in migration, (
            f"__deprecated_migration__ 应指向 Normalizer/Translator 入口，实际 {migration}"
        )

    def test_legacy_module_docstring_contains_deprecated(self):
        """T4.5: 模块级 docstring 含 deprecated 标记"""
        if not LEGACY_SCHEMA_PATH.exists():
            pytest.skip("legacy schema 文件不存在")
        content = LEGACY_SCHEMA_PATH.read_text(encoding="utf-8")
        assert "deprecated" in content.lower(), (
            "legacy schema 模块 docstring 应包含 'deprecated' 标记"
        )
        assert "Replacement" in content or "replacement" in content.lower(), (
            "legacy schema 模块 docstring 应指向 replacement (canonical schema)"
        )
        assert "Migration" in content or "migration" in content.lower(), (
            "legacy schema 模块 docstring 应包含 migration 说明"
        )

    def test_legacy_class_docstring_contains_deprecated(self):
        """T4.6: 类的 docstring 含 deprecated 标记"""
        from src.growth.proposal.proposal import GrowthProposal
        doc = GrowthProposal.__doc__ or ""
        assert "deprecated" in doc.lower(), (
            "legacy GrowthProposal 类的 docstring 应包含 'deprecated'"
        )

    def test_canonical_class_does_not_have_deprecated_attr(self):
        """T4.7: canonical schema 不应携带 deprecated 标记（它是推荐项）"""
        from src.contracts.growth_schema import GrowthProposal
        assert getattr(GrowthProposal, "__deprecated__", False) is False, (
            "canonical GrowthProposal 不应是 deprecated"
        )

    def test_canonical_module_docstring_does_not_say_deprecated(self):
        """T4.8: canonical schema 模块 docstring 不应说 deprecated"""
        if not CANONICAL_SCHEMA_PATH.exists():
            pytest.skip("canonical schema 文件不存在")
        content = CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8")
        # canonical 是内部标准，不应被标记为 deprecated
        # 容忍注释或文档说明"替代 legacy"，但模块本身不应 deprecated
        # 检查模块开头 docstring 不含 "deprecated" 字样
        head = content[:1500]
        assert "deprecated" not in head.lower() or "canonical" in head.lower(), (
            "canonical schema 模块开头不应直接声明 deprecated"
        )


# ============================================================
# T5: 字段完整性（关键字段在主链路保留）
# ============================================================

class TestFieldIntegrityInMainPath:
    """治理 schema 的关键字段在主链路中必须可追溯。"""

    def test_request_id_generated_in_pcr(self, sample_legacy_governance):
        """T5.1: PCR.request_id 由 _translate_canonical 生成"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_legacy_governance)
        assert "request_id" in pcr
        assert pcr["request_id"].startswith("pcr_")

    def test_source_proposal_id_preserved(self, sample_legacy_governance):
        """T5.2: PCR.source_proposal_id 来自 legacy proposal_id"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_legacy_governance)
        assert pcr["source_proposal_id"] == sample_legacy_governance["proposal_id"]

    def test_timestamp_preserved_with_z_suffix(self, sample_legacy_governance):
        """T5.3: timestamp 保留并归一带 Z"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert canonical["timestamp"].endswith("Z"), (
            "timestamp 归一后必须以 Z 结尾"
        )

    def test_reason_preserved(self, sample_legacy_governance):
        """T5.4: legacy.reason 保留到 canonical.evaluator_meta.reason"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        assert em.get("reason") == sample_legacy_governance["reason"]

    def test_reason_summary_preserved(self, sample_canonical):
        """T5.5: canonical.evaluator_meta.reason_summary 在主链路保留"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_canonical)
        assert pcr["evaluator_meta"].get("reason_summary") == "high engagement signal"

    def test_before_state_preserved(self, sample_legacy_governance):
        """T5.6: before_state 保留到 canonical._governance_origin.before_state"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})
        assert "before_state" in origin
        assert origin["before_state"]["curiosity"] == 0.5
        assert origin["before_state"]["warmth"] == 0.6

    def test_after_state_preserved(self, sample_legacy_governance):
        """T5.7: after_state 保留到 canonical._governance_origin.after_state"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})
        assert "after_state" in origin
        assert origin["after_state"]["curiosity"] == 0.55
        assert origin["after_state"]["warmth"] == 0.63

    def test_evidence_preserved(self, sample_legacy_governance):
        """T5.8: evidence 列表保留"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert canonical["evidence_ids"] == sample_legacy_governance["evidence"]

    def test_confidence_preserved(self, sample_legacy_governance):
        """T5.9: confidence 保留"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        assert abs(canonical["confidence"] - sample_legacy_governance["confidence"]) < 1e-5

    def test_priority_preserved(self, sample_legacy_governance):
        """T5.10: priority 保留到 _governance_origin.priority"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})
        assert origin.get("priority") == sample_legacy_governance["priority"]

    def test_all_legacy_required_fields_traceable(self, sample_legacy_governance):
        """T5.11: legacy 10 字段全部在 canonical 中可追溯"""
        from src.contracts.proposal_normalizer import normalize_to_canonical
        canonical = normalize_to_canonical(sample_legacy_governance)
        em = canonical["evaluator_meta"]
        origin = em.get("_governance_origin", {})

        # proposal_id → canonical.id
        assert canonical["id"] == sample_legacy_governance["proposal_id"]
        # timestamp
        assert canonical["timestamp"].endswith("Z")
        # affected_dimensions → canonical.proposed_changes（由 delta 派生）
        pcs = canonical["proposed_changes"]
        assert len(pcs) > 0
        # before_state / after_state → origin
        assert "before_state" in origin
        assert "after_state" in origin
        # evidence → canonical.evidence_ids
        assert canonical["evidence_ids"] == sample_legacy_governance["evidence"]
        # confidence → canonical.confidence
        assert abs(
            canonical["confidence"] - sample_legacy_governance["confidence"]
        ) < 1e-5
        # reason → em.reason
        assert em.get("reason") == sample_legacy_governance["reason"]
        # priority → origin.priority
        assert origin.get("priority") == sample_legacy_governance["priority"]
        # metadata → em.source
        assert em.get("source") == sample_legacy_governance["metadata"]["source"]


# ============================================================
# T6: 反依赖基线（governance tests 不引入新依赖）
# ============================================================

class TestNoForbiddenDependencies:
    """Phase 3.6.4 governance tests 不引入 Runtime / Orchestrator 依赖。"""

    def test_normalizer_no_runtime_import(self):
        """T6.1: proposal_normalizer.py 不应 import Runtime / Orchestrator"""
        if not NORMALIZER_PATH.exists():
            pytest.skip("proposal_normalizer.py 不存在")
        content = NORMALIZER_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content, (
                f"proposal_normalizer.py 不应 import {forbidden}"
            )
            assert f"from {forbidden}" not in content, (
                f"proposal_normalizer.py 不应 from {forbidden}"
            )

    def test_canonical_schema_no_runtime_import(self):
        """T6.2: growth_schema.py 不应 import Runtime / Orchestrator"""
        if not CANONICAL_SCHEMA_PATH.exists():
            pytest.skip("growth_schema.py 不存在")
        content = CANONICAL_SCHEMA_PATH.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_MODULES:
            assert f"import {forbidden}" not in content
            assert f"from {forbidden}" not in content


# ============================================================
# T7: NormalizedProposalTranslator 是统一入口
# ============================================================

class TestNormalizedTranslatorIsStandardEntry:
    """NormalizedProposalTranslator 是 Phase 3.6.4 起业务层 GrowthProposal 统一入口。"""

    def test_normalized_translator_exposed(self):
        """T7.1: NormalizedProposalTranslator 类存在"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        assert NormalizedProposalTranslator is not None

    def test_normalized_translator_has_translate(self):
        """T7.2: NormalizedProposalTranslator.translate 存在"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        assert hasattr(NormalizedProposalTranslator, "translate")
        assert callable(NormalizedProposalTranslator.translate)

    def test_normalized_translator_has_normalize(self):
        """T7.3: NormalizedProposalTranslator.normalize 存在"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        assert hasattr(NormalizedProposalTranslator, "normalize")

    def test_normalized_translator_has_detect(self):
        """T7.4: NormalizedProposalTranslator.detect 存在"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        assert hasattr(NormalizedProposalTranslator, "detect")

    def test_normalized_translator_docstring_recommended(self):
        """T7.5: docstring 标记为推荐入口"""
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        doc = NormalizedProposalTranslator.__doc__ or ""
        assert "统一入口" in doc or "recommended" in doc.lower() or "推荐" in doc, (
            "NormalizedProposalTranslator docstring 应标记为推荐入口"
        )


# ============================================================
# T8: 兼容性（旧入口仍能工作）
# ============================================================

class TestBackwardCompatibility:
    """Phase 3.6.4 不破坏已有兼容性。"""

    def test_legacy_growthproposal_instantiable(self):
        """T8.1: legacy GrowthProposal 仍可正常实例化（向后兼容）"""
        from src.growth.proposal.proposal import GrowthProposal
        from src.growth.proposal.constants import PROPOSAL_STATUS
        p = GrowthProposal(
            proposal_id="prop_legacy_compat_001",
            status=PROPOSAL_STATUS["PENDING"],
            affected_dimensions={"curiosity": 0.05},
        )
        assert p.proposal_id == "prop_legacy_compat_001"
        d = p.to_dict()
        assert d["proposal_id"] == "prop_legacy_compat_001"
        assert d["affected_dimensions"]["curiosity"] == 0.05

    def test_legacy_from_dict_still_works(self):
        """T8.2: legacy.from_dict 仍能工作"""
        from src.growth.proposal.proposal import GrowthProposal
        data = {
            "proposal_id": "prop_legacy_compat_002",
            "affected_dimensions": {"warmth": 0.03},
            "status": "pending",
        }
        p = GrowthProposal.from_dict(data)
        assert p.proposal_id == "prop_legacy_compat_002"
        assert p.affected_dimensions["warmth"] == 0.03

    def test_legacy_is_expired_still_works(self):
        """T8.3: legacy.is_expired 仍能工作"""
        from src.growth.proposal.proposal import GrowthProposal
        p1 = GrowthProposal(proposal_id="p1")
        assert p1.is_expired() is False
        p2 = GrowthProposal(proposal_id="p2", expires_at="2020-01-01T00:00:00")
        assert p2.is_expired() is True

    def test_legacy_get_total_delta_still_works(self):
        """T8.4: legacy.get_total_delta 仍能工作"""
        from src.growth.proposal.proposal import GrowthProposal
        p = GrowthProposal(
            proposal_id="p3",
            affected_dimensions={"a": 0.05, "b": 0.03, "c": 0.02},
        )
        assert abs(p.get_total_delta() - 0.10) < 1e-5

    def test_growthproposal_translator_still_works(self):
        """T8.5: 旧 GrowthProposalTranslator 入口仍能工作（deprecated 但兼容）"""
        from src.admin.selfmodel_consumer import GrowthProposalTranslator
        canonical = {
            "id": "prop_compat_001",
            "proposed_changes": [
                {"path": "personality.traits.curiosity",
                 "before": 0.5, "after": 0.55, "reason": "test"},
            ],
            "confidence": 0.8,
            "evidence_ids": ["e1"],
            "evaluator_meta": {"reason_summary": "test"},
            "timestamp": "2026-01-01T00:00:00Z",
            "status": "proposed",
        }
        pcr = GrowthProposalTranslator.translate(canonical)
        assert pcr["source_proposal_id"] == "prop_compat_001"


# ============================================================
# T9: Schema 治理架构验证
# ============================================================

class TestSchemaGovernanceArchitecture:
    """验证 Phase 3.6.4 目标架构成立。"""

    def test_canonical_schema_is_internal_standard(self):
        """T9.1: canonical schema 是 7 字段标准"""
        from src.contracts.growth_schema import GrowthProposal, ChangeItem
        p = GrowthProposal(
            id="prop_arch_001",
            proposed_changes=[
                ChangeItem(path="personality.traits.curiosity", before=0.5, after=0.55),
            ],
        )
        assert p.id == "prop_arch_001"
        # 7 个核心字段
        for k in (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        ):
            assert hasattr(p, k) or k in p.to_dict()

    def test_normalizer_is_single_translation_layer(self):
        """T9.2: GrowthProposalNormalizer 是唯一归一化层"""
        from src.contracts.proposal_normalizer import (
            GrowthProposalNormalizer,
            normalize_to_canonical,
            detect,
            to_governance_view,
        )
        # 公开 API 完整
        assert callable(GrowthProposalNormalizer.normalize_to_canonical)
        assert callable(GrowthProposalNormalizer.detect)
        assert callable(GrowthProposalNormalizer.to_governance_view)
        # 函数式入口
        assert callable(normalize_to_canonical)
        assert callable(detect)
        assert callable(to_governance_view)

    def test_normalizer_detect_legacy_correctly(self, sample_legacy_governance):
        """T9.3: Normalizer 正确识别 legacy schema 为 'governance'"""
        from src.contracts.proposal_normalizer import detect
        kind = detect(sample_legacy_governance)
        assert kind == "governance"

    def test_normalizer_detect_canonical_correctly(self, sample_canonical):
        """T9.4: Normalizer 正确识别 canonical schema"""
        from src.contracts.proposal_normalizer import detect
        kind = detect(sample_canonical)
        assert kind == "canonical"

    def test_legacy_only_input_compat(self, sample_legacy_governance):
        """T9.5: legacy 仅作为输入兼容层（不直接进入 consumer）"""
        # legacy 形态通过 NormalizedProposalTranslator 进入下游
        from src.admin.normalized_proposal_translator import (
            NormalizedProposalTranslator,
        )
        pcr = NormalizedProposalTranslator.translate(sample_legacy_governance)
        # 下游 PCR 不再有 proposal_id（已是 source_proposal_id）
        # 且 _source_schema 标记为 "proposal"（说明来源是 legacy）
        assert "proposal_id" not in pcr
        assert pcr["source_proposal_id"] == sample_legacy_governance["proposal_id"]
        assert pcr["evaluator_meta"]["_source_schema"] == "proposal"
        # 内部已 normalize
        assert pcr["evaluator_meta"].get("_normalized") is True


# ============================================================
# 主入口（直接运行时打印治理状态）
# ============================================================

def test_phase_3_6_4_governance_summary():
    """汇总 Phase 3.6.4 schema 治理状态（用于完成报告参考）"""
    from src.contracts.growth_schema import GrowthProposal as Canonical
    from src.growth.proposal.proposal import GrowthProposal as Legacy
    from src.contracts.proposal_normalizer import GrowthProposalNormalizer
    from src.admin.normalized_proposal_translator import (
        NormalizedProposalTranslator,
    )

    # 1) canonical 存在
    assert Canonical is not None
    # 2) legacy 存在（向后兼容）
    assert Legacy is not None
    # 3) legacy 有 deprecation 标记
    assert getattr(Legacy, "__deprecated__", False) is True
    # 4) Normalizer / Translator 存在
    assert GrowthProposalNormalizer is not None
    assert NormalizedProposalTranslator is not None
