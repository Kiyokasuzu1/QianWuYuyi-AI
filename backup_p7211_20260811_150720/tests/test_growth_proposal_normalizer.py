# -*- coding: utf-8 -*-
"""
tests/test_growth_proposal_normalizer.py

Phase 3.6.2: GrowthProposalNormalizer 测试

覆盖：
- T1: Schema A detect
- T2: Schema B detect
- T3: A → canonical
- T4: B → canonical
- T5: canonical → governance
- T6: round trip A → B → A 数据一致
- T7: 字段缺失安全处理
- T8: 未知 schema 安全失败
- T9: 无 runtime/growth/personality 依赖
- T10: 冲突字段（id + proposal_id 同存）优先级

约束：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/* / src/personality/*
- 不修改 selfmodel_consumer.py / selfmodel_consumer_audit.py
"""
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FORBIDDEN_MODULES = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_canonical_a() -> Dict[str, Any]:
    """完整 Schema A 样本。"""
    return {
        "id": "prop_a_001",
        "source_event_id": "evt_a_001",
        "proposed_changes": [
            {
                "path": "personality.traits.curiosity",
                "before": 0.5,
                "after": 0.55,
                "reason": "increased engagement",
            }
        ],
        "confidence": 0.85,
        "evidence_ids": ["e1", "e2"],
        "evaluator_meta": {
            "reason_summary": "high_signal",
            "evaluator_id": "ev_main",
        },
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "proposed",
    }


@pytest.fixture
def sample_governance_b() -> Dict[str, Any]:
    """完整 Schema B 样本。"""
    return {
        "proposal_id": "prop_b_001",
        "timestamp": "2026-01-01T00:00:00.000000",  # 无 Z
        "proposal_type": "personality",
        "status": "pending",
        "source": "growth_engine",
        "source_event_id": "evt_b_001",
        "user_id": "user_001",
        "affected_dimensions": {
            "warmth": 0.03,
            "curiosity": 0.05,
        },
        "before_state": {
            "warmth": 0.6,
            "curiosity": 0.5,
        },
        "after_state": {
            "warmth": 0.63,
            "curiosity": 0.55,
        },
        "confidence": 0.78,
        "reason": "increased warmth signals",
        "evidence": ["inter_x", "inter_y"],
        "priority": "medium",
        "reviewer_id": "",
        "review_comment": "",
        "reviewed_at": None,
        "applied_at": None,
        "applied_by": "",
        "expires_at": None,
        "metadata": {"source": "growth_engine_v2"},
    }


# ============================================================
# T1: Schema A detect
# ============================================================

class TestDetectSchemaA:
    """Schema A 形态应被 detect 为 'canonical'。"""

    def test_a_with_full_signature(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import detect
        assert detect(sample_canonical_a) == "canonical"

    def test_a_with_id_and_proposed_changes_only(self):
        from src.contracts.proposal_normalizer import detect
        p = {
            "id": "x",
            "proposed_changes": [{"path": "p.t.c", "before": 0.5, "after": 0.55}],
        }
        assert detect(p) == "canonical"

    def test_a_with_id_and_evidence_ids_only(self):
        from src.contracts.proposal_normalizer import detect
        p = {
            "id": "x",
            "evidence_ids": ["e1"],
        }
        assert detect(p) == "canonical"

    def test_a_with_only_id(self):
        """仅有 id 时也应被识别为 canonical（id 是 canonical 主键）。"""
        from src.contracts.proposal_normalizer import detect
        assert detect({"id": "x"}) == "canonical"


# ============================================================
# T2: Schema B detect
# ============================================================

class TestDetectSchemaB:
    """Schema B 形态应被 detect 为 'governance'。"""

    def test_b_with_full_signature(self, sample_governance_b):
        from src.contracts.proposal_normalizer import detect
        assert detect(sample_governance_b) == "governance"

    def test_b_with_proposal_id_and_affected_dimensions(self):
        from src.contracts.proposal_normalizer import detect
        p = {
            "proposal_id": "p1",
            "affected_dimensions": {"warmth": 0.05},
        }
        assert detect(p) == "governance"

    def test_b_with_proposal_id_and_evidence(self):
        from src.contracts.proposal_normalizer import detect
        p = {
            "proposal_id": "p1",
            "evidence": ["e1"],
        }
        assert detect(p) == "governance"

    def test_b_with_only_proposal_id(self):
        from src.contracts.proposal_normalizer import detect
        assert detect({"proposal_id": "x"}) == "governance"


# ============================================================
# T3: A → canonical
# ============================================================

class TestNormalizeAToCanonical:
    """A 形态 → canonical：直通 + 7 个核心字段齐全。"""

    def test_a_to_canonical_preserves_id(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import (
            GrowthProposalNormalizer, CANONICAL,
        )
        n = GrowthProposalNormalizer()
        assert n.detect(sample_canonical_a) == CANONICAL
        c = n.normalize_to_canonical(sample_canonical_a)
        assert c["id"] == "prop_a_001"

    def test_a_to_canonical_preserves_proposed_changes(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_canonical_a)
        assert isinstance(c["proposed_changes"], list)
        assert len(c["proposed_changes"]) == 1
        assert c["proposed_changes"][0]["path"] == "personality.traits.curiosity"
        assert c["proposed_changes"][0]["after"] == 0.55

    def test_a_to_canonical_has_all_seven_fields(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_canonical_a)
        required = (
            "id", "proposed_changes", "evidence_ids", "confidence",
            "evaluator_meta", "status", "timestamp",
        )
        for k in required:
            assert k in c, f"missing canonical field: {k}"

    def test_a_to_canonical_preserves_extra_fields(self, sample_canonical_a):
        """source_event_id 等额外字段不应被丢弃。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_canonical_a)
        assert c.get("source_event_id") == "evt_a_001"

    def test_a_to_canonical_preserves_evaluator_meta(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_canonical_a)
        assert c["evaluator_meta"]["evaluator_id"] == "ev_main"


# ============================================================
# T4: B → canonical
# ============================================================

class TestNormalizeBToCanonical:
    """B 形态 → canonical：字段映射 + 7 个核心字段齐全 + 保留 _governance_origin。"""

    def test_b_to_canonical_uses_proposal_id_as_id(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        assert c["id"] == "prop_b_001"

    def test_b_to_canonical_derives_proposed_changes(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        # 应该有 2 个 proposed_changes（warmth + curiosity）
        assert len(c["proposed_changes"]) == 2
        traits = {_extract_trait(pc.get("path", "")) for pc in c["proposed_changes"]}
        assert "warmth" in traits
        assert "curiosity" in traits

    def test_b_to_canonical_maps_evidence_to_evidence_ids(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        assert "inter_x" in c["evidence_ids"]
        assert "inter_y" in c["evidence_ids"]

    def test_b_to_canonical_maps_metadata_to_evaluator_meta(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        assert c["evaluator_meta"]["source"] == "growth_engine_v2"

    def test_b_to_canonical_preserves_governance_origin(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        # 应该有 _governance_origin 包含 B 原数据
        assert "_governance_origin" in c["evaluator_meta"]
        origin = c["evaluator_meta"]["_governance_origin"]
        assert origin["proposal_id"] == "prop_b_001"
        assert origin["affected_dimensions"]["warmth"] == 0.03

    def test_b_to_canonical_preserves_b_extra_fields(self, sample_governance_b):
        """B 的 priority / user_id 等扩展字段应保留到 evaluator_meta；top-level source 仅通过 _governance_origin 保留。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        assert c["evaluator_meta"]["priority"] == "medium"
        assert c["evaluator_meta"]["user_id"] == "user_001"
        # B 的 metadata.source 进入 evaluator_meta
        assert c["evaluator_meta"]["source"] == "growth_engine_v2"
        # B 的 top-level source 仅通过 _governance_origin 保留
        assert c["evaluator_meta"]["_governance_origin"]["source"] == "growth_engine"

    def test_b_to_canonical_normalizes_timestamp(self):
        """B 的 timestamp 无 Z 时应补 Z。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        p = {"proposal_id": "p1", "timestamp": "2026-01-01T00:00:00"}
        c = GrowthProposalNormalizer.normalize_to_canonical(p)
        assert c["timestamp"].endswith("Z")

    def test_b_to_canonical_maps_status(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        # B "pending" → A "proposed"
        c1 = GrowthProposalNormalizer.normalize_to_canonical({"proposal_id": "p", "status": "pending"})
        assert c1["status"] == "proposed"
        # B "approved" → A "accepted"
        c2 = GrowthProposalNormalizer.normalize_to_canonical({"proposal_id": "p", "status": "approved"})
        assert c2["status"] == "accepted"
        # B "rejected" → A "rejected"
        c3 = GrowthProposalNormalizer.normalize_to_canonical({"proposal_id": "p", "status": "rejected"})
        assert c3["status"] == "rejected"


# ============================================================
# T5: canonical → governance
# ============================================================

class TestNormalizeCanonicalToGovernance:
    """canonical → governance view。"""

    def test_canonical_to_governance_uses_id_as_proposal_id(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view(sample_canonical_a)
        assert g["proposal_id"] == "prop_a_001"

    def test_canonical_to_governance_derives_affected_dimensions(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view(sample_canonical_a)
        # 0.55 - 0.5 = 0.05
        assert abs(g["affected_dimensions"]["curiosity"] - 0.05) < 1e-6

    def test_canonical_to_governance_maps_evidence_ids(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view(sample_canonical_a)
        assert "e1" in g["evidence"]
        assert "e2" in g["evidence"]

    def test_canonical_to_governance_uses_metadata(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view(sample_canonical_a)
        assert "evaluator_id" in g["metadata"]
        assert g["metadata"]["evaluator_id"] == "ev_main"

    def test_canonical_to_governance_maps_status(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        # A "proposed" → B "pending"
        g1 = GrowthProposalNormalizer.to_governance_view({
            "id": "p", "status": "proposed",
            "proposed_changes": [{"path": "p.t.c", "before": 0.5, "after": 0.55}],
        })
        assert g1["status"] == "pending"
        # A "accepted" → B "approved"
        g2 = GrowthProposalNormalizer.to_governance_view({
            "id": "p", "status": "accepted",
            "proposed_changes": [{"path": "p.t.c", "before": 0.5, "after": 0.55}],
        })
        assert g2["status"] == "approved"

    def test_canonical_to_governance_preserves_governance_origin_id(self, sample_governance_b):
        """当 canonical 来自 B（带 _governance_origin），governance 输出应还原原 proposal_id。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(sample_governance_b)
        g = GrowthProposalNormalizer.to_governance_view(c)
        # proposal_id 跟原 B 一致
        assert g["proposal_id"] == "prop_b_001"
        # 扩展字段应回填
        assert g.get("priority") == "medium"
        assert g.get("source") == "growth_engine"


# ============================================================
# T6: round trip A → B → A
# ============================================================

class TestRoundTrip:
    """A → governance → A 应数据一致。"""

    def test_round_trip_a_to_b_to_a(self, sample_canonical_a):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        n = GrowthProposalNormalizer()

        # A → governance
        g = n.to_governance_view(sample_canonical_a)

        # governance → A
        a2 = n.normalize_to_canonical(g)

        # 数据一致性
        assert a2["id"] == sample_canonical_a["id"]
        # proposed_changes 数量与原始一致
        assert len(a2["proposed_changes"]) == len(sample_canonical_a["proposed_changes"])
        # confidence 一致
        assert a2["confidence"] == sample_canonical_a["confidence"]
        # evidence 一致
        assert sorted(a2["evidence_ids"]) == sorted(sample_canonical_a["evidence_ids"])
        # timestamp 归一带 Z
        assert a2["timestamp"].endswith("Z")

    def test_round_trip_b_to_a_to_b(self, sample_governance_b):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        n = GrowthProposalNormalizer()

        # B → A
        c = n.normalize_to_canonical(sample_governance_b)

        # A → governance
        g = n.to_governance_view(c)

        # governance 应保留原 B 的 proposal_id（通过 _governance_origin）
        assert g["proposal_id"] == sample_governance_b["proposal_id"]
        # affected_dimensions 数值一致
        assert abs(g["affected_dimensions"]["warmth"] - 0.03) < 1e-6
        assert abs(g["affected_dimensions"]["curiosity"] - 0.05) < 1e-6
        # evidence 一致
        assert sorted(g["evidence"]) == sorted(sample_governance_b["evidence"])
        # confidence 一致
        assert g.get("confidence") == sample_governance_b["confidence"]

    def test_round_trip_preserves_delta(self, sample_canonical_a):
        """delta 值在 round trip 后应保持。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        n = GrowthProposalNormalizer()
        g = n.to_governance_view(sample_canonical_a)
        c = n.normalize_to_canonical(g)
        # 找到 curiosity
        pc = next(p for p in c["proposed_changes"] if "curiosity" in p.get("path", ""))
        assert abs(pc["after"] - pc["before"] - 0.05) < 1e-4


# ============================================================
# T7: 字段缺失安全处理
# ============================================================

class TestFieldMissingSafe:
    """字段缺失 / 类型错误 / 空列表 / None 全部安全处理。"""

    def test_none_proposal_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(None)
        assert isinstance(c, dict)
        # 7 个核心字段都应存在
        for k in ("id", "proposed_changes", "evidence_ids", "confidence", "evaluator_meta", "status", "timestamp"):
            assert k in c

    def test_empty_dict_proposal_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({})
        assert c["id"] == ""
        assert c["proposed_changes"] == []
        assert c["evidence_ids"] == []
        assert c["confidence"] == 0.0
        assert c["evaluator_meta"] == {}
        assert c["status"] == "proposed"
        assert c["timestamp"].endswith("Z")

    def test_missing_confidence_defaults_to_zero(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p"})
        assert c["confidence"] == 0.0

    def test_invalid_confidence_defaults_to_zero(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p", "confidence": "not_a_number"})
        assert c["confidence"] == 0.0

    def test_proposed_changes_wrong_type_defaults_to_empty(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p", "proposed_changes": "not a list"})
        assert c["proposed_changes"] == []

    def test_evidence_ids_wrong_type_defaults_to_empty(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p", "evidence_ids": 42})
        assert c["evidence_ids"] == []

    def test_status_wrong_type_defaults_to_proposed(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p", "status": 123})
        assert c["status"] == "proposed"

    def test_proposed_changes_with_non_dict_items_filtered(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({
            "id": "p",
            "proposed_changes": [
                None,
                "string",
                {"path": "p.t.c", "before": 0.5, "after": 0.55},
            ],
        })
        # 只保留 dict 项
        assert len(c["proposed_changes"]) == 1
        assert c["proposed_changes"][0]["path"] == "p.t.c"

    def test_empty_list_proposed_changes(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({"id": "p", "proposed_changes": []})
        assert c["proposed_changes"] == []

    def test_governance_with_no_affected_dimensions(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical({
            "proposal_id": "p",
            "evidence": ["e1"],
        })
        assert c["proposed_changes"] == []
        assert c["evidence_ids"] == ["e1"]

    def test_to_governance_view_none_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view(None)
        assert isinstance(g, dict)
        assert g["proposal_id"] == ""
        assert g["affected_dimensions"] == {}
        assert g["evidence"] == []

    def test_to_governance_view_empty_dict_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view({})
        assert g["proposal_id"] == ""
        assert g["status"] == "pending"


# ============================================================
# T8: 未知 schema 安全失败
# ============================================================

class TestUnknownSchemaSafe:
    """未知 schema / 异常 dict → 安全默认输出。"""

    def test_unknown_schema_returns_safe_canonical(self):
        from src.contracts.proposal_normalizer import (
            GrowthProposalNormalizer, UNKNOWN, detect,
        )
        p = {"some_random_field": "x", "another": 42}
        assert detect(p) == UNKNOWN
        c = GrowthProposalNormalizer.normalize_to_canonical(p)
        assert isinstance(c, dict)
        # 7 个核心字段都在
        for k in ("id", "proposed_changes", "evidence_ids", "confidence", "evaluator_meta", "status", "timestamp"):
            assert k in c

    def test_string_input_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical("not a dict")
        assert isinstance(c, dict)
        assert c["id"] == ""

    def test_int_input_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical(42)
        assert isinstance(c, dict)
        assert c["id"] == ""

    def test_list_input_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        c = GrowthProposalNormalizer.normalize_to_canonical([{"id": "p"}])
        # list 不是 dict，走 unknown 路径
        assert isinstance(c, dict)

    def test_unknown_to_governance_safe(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        g = GrowthProposalNormalizer.to_governance_view({"foo": "bar"})
        assert isinstance(g, dict)
        assert "proposal_id" in g
        assert "affected_dimensions" in g

    def test_no_exception_raised_for_garbage(self):
        """任何异常输入都不应抛异常。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        for inp in [None, {}, [], 0, "", b"bytes", {"a": 1}, "string", float("nan")]:
            try:
                c = GrowthProposalNormalizer.normalize_to_canonical(inp)
                assert isinstance(c, dict)
                g = GrowthProposalNormalizer.to_governance_view(inp)
                assert isinstance(g, dict)
            except Exception as e:
                pytest.fail(f"unexpected exception for {inp!r}: {e}")


# ============================================================
# T9: 无 runtime/growth/personality 依赖
# ============================================================

class TestNoForbiddenDependency:
    """normalizer 不应 import 受保护模块。"""

    def test_module_source_no_runtime_imports(self):
        from src.contracts import proposal_normalizer as mod
        text = Path(mod.__file__).read_text(encoding="utf-8")
        imports: List[str] = []
        for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", text, re.MULTILINE):
            imports.append(m.group(1))
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.MULTILINE):
            imports.append(m.group(1))
        for mod_name in imports:
            for forbidden in (
                "src.runtime",
                "src.orchestrator",
                "src.growth",
                "src.personality",
                "src.admin.selfmodel_consumer",
                "src.admin.selfmodel_consumer_audit",
            ):
                assert not mod_name.startswith(forbidden), (
                    f"proposal_normalizer.py 不应 import {forbidden}（发现 {mod_name!r}）"
                )

    def test_normalize_does_not_load_runtime(self):
        """运行 normalizer 不应触发 Runtime/Orchestrator/Growth/Personality 加载。"""
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_MODULES or m.startswith("src.growth") or m.startswith("src.personality") or m.startswith("src.runtime"):
                del sys.modules[m]

        from src.contracts.proposal_normalizer import GrowthProposalNormalizer
        # 跑 1 次
        c = GrowthProposalNormalizer.normalize_to_canonical({
            "id": "p",
            "proposed_changes": [{"path": "p.t.c", "before": 0.5, "after": 0.55}],
        })
        assert c["id"] == "p"
        g = GrowthProposalNormalizer.to_governance_view(c)
        assert g["proposal_id"] == "p"

        loaded = set(sys.modules.keys())
        for forbidden in FORBIDDEN_MODULES:
            assert forbidden not in loaded
        # growth/personality 也不应被加载
        for prefix in ("src.growth", "src.personality", "src.runtime"):
            for mod_name in loaded:
                assert not mod_name.startswith(prefix), (
                    f"运行 normalizer 不应触发 {prefix}.* 加载（发现 {mod_name}）"
                )


# ============================================================
# T10: 冲突字段优先级
# ============================================================

class TestConflictResolution:
    """id 与 proposal_id 同时存在时，detect 优先级。"""

    def test_both_ids_with_canonical_signals_prefers_canonical(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer, CANONICAL
        p = {
            "id": "id_a",
            "proposal_id": "id_b",
            "proposed_changes": [{"path": "p.t.c", "before": 0.5, "after": 0.55}],
            "evidence_ids": ["e1"],
        }
        assert GrowthProposalNormalizer.detect(p) == CANONICAL

    def test_both_ids_with_governance_signals_prefers_governance(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer, GOVERNANCE
        p = {
            "id": "id_a",
            "proposal_id": "id_b",
            "affected_dimensions": {"warmth": 0.05},
            "evidence": ["e1"],
        }
        assert GrowthProposalNormalizer.detect(p) == GOVERNANCE

    def test_both_ids_only_meta_evaluator_prefers_canonical(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer, CANONICAL
        p = {
            "id": "id_a",
            "proposal_id": "id_b",
            "evaluator_meta": {"k": "v"},
        }
        assert GrowthProposalNormalizer.detect(p) == CANONICAL

    def test_both_ids_only_meta_metadata_prefers_governance(self):
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer, GOVERNANCE
        p = {
            "id": "id_a",
            "proposal_id": "id_b",
            "metadata": {"k": "v"},
        }
        assert GrowthProposalNormalizer.detect(p) == GOVERNANCE

    def test_both_ids_no_signals_defaults_canonical(self):
        """完全对等时默认 canonical（因为 Runtime 主链路用 canonical）。"""
        from src.contracts.proposal_normalizer import GrowthProposalNormalizer, CANONICAL
        p = {"id": "id_a", "proposal_id": "id_b"}
        assert GrowthProposalNormalizer.detect(p) == CANONICAL


# ============================================================
# 工具
# ============================================================

def _extract_trait(path: str) -> str:
    parts = [p for p in path.split(".") if p]
    return parts[-1] if parts else "unknown"
