# -*- coding: utf-8 -*-
"""
Phase D.6.1.0 tests: ExistenceMilestone Contract
==================================================

闸口测试 (Gate Tests):
- 默认值绝对不能伪造 (空 / 0.0 / False / UNKNOWN)
- Milestone 类型 / 来源类型 枚举严格
- 没有证据的 Milestone = 不进入 Timeline (is_well_formed=False)
- to_dict / from_dict 往返一致且对脏数据容错
"""

from __future__ import annotations

import pytest

from src.contracts.existence import (
    ExistenceMilestone,
    EvidenceReference,
    ImpactMap,
    ExplanationMetadata,
    VALID_MILESTONE_TYPES,
    VALID_SOURCE_TYPES,
    MILESTONE_GROWTH,
    MILESTONE_BELIEF,
    MILESTONE_TRAIT_CHANGE,
    SOURCE_GROWTH_PROPOSAL,
    SOURCE_SELF_MODEL_TRAIT,
    now_iso,
)


# ============================================================
# T0: 模块级导入
# ============================================================

class TestExistenceContractImports:
    """确保模块导出完整。"""

    def test_all_6_milestone_types_declared(self):
        assert isinstance(VALID_MILESTONE_TYPES, tuple)
        assert len(VALID_MILESTONE_TYPES) == 6
        for t in ("growth", "belief", "reflection",
                  "trait_change", "relationship", "identity"):
            assert t in VALID_MILESTONE_TYPES

    def test_source_types_cover_3_sources_plus_extensions(self):
        # 三源:Proposal / SelfModel(belief+trait+history) / Evolution
        for required in (
            SOURCE_GROWTH_PROPOSAL,
            "selfmodel_belief",
            "selfmodel_trait",
            "selfmodel_history",
            SOURCE_PERSONALITY_EVOLUTION := "personality_evolution",
        ):
            assert required in VALID_SOURCE_TYPES


# ============================================================
# T1: EvidenceReference 默认值 + 防伪造
# ============================================================

class TestEvidenceReferenceDefaults:
    """证据引用默认值 = 完全不可用,需要 Adapter 显式填。"""

    def test_source_type_default_empty_not_valid(self):
        er = EvidenceReference()
        assert er.source_type == ""
        assert er.is_source_valid() is False

    def test_source_id_default_empty_has_no_real_source(self):
        er = EvidenceReference()
        assert er.source_id == ""
        assert er.has_real_source() is False

    def test_confidence_default_0_NOT_0_72(self):
        """核心闸口:绝不能默认给一个中等置信度。"""
        er = EvidenceReference()
        assert er.confidence == 0.0

    def test_note_and_metadata_empty_by_default(self):
        er = EvidenceReference()
        assert er.note == ""
        assert er.metadata == {}

    def test_has_real_source_requires_both_valid_type_and_id(self):
        # 只有 valid type,没有 id → 不算可追溯
        er = EvidenceReference(source_type=SOURCE_GROWTH_PROPOSAL)
        assert er.is_source_valid() is True
        assert er.has_real_source() is False

        # 有 id 但 type 不合法 → 也不算
        er2 = EvidenceReference(source_type="unknown_hack", source_id="abc123")
        assert er2.is_source_valid() is False
        assert er2.has_real_source() is False

        # 两个都齐 → OK
        er3 = EvidenceReference(
            source_type=SOURCE_GROWTH_PROPOSAL,
            source_id="pcr_ecf5397dc4",
        )
        assert er3.has_real_source() is True


# ============================================================
# T2: ImpactMap + ExplanationMetadata 默认值
# ============================================================

class TestImpactAndExplanationDefaults:
    """
    影响范围 + 可解释性元数据。
    关键闸口:默认值 = 全 False + 全 0。
    """

    def test_impact_map_all_false_by_default(self):
        imp = ImpactMap()
        assert imp.personality is False
        assert imp.belief is False
        assert imp.relationship is False
        assert imp.identity is False
        assert imp.memory is False
        assert imp.any_impact() is False

    def test_explanation_why_created_empty(self):
        exp = ExplanationMetadata()
        # 绝不能默认填 "长期互动后形成的倾向" 这类文字
        assert exp.why_created == ""
        assert exp.has_explanation() is False

    def test_explanation_evidence_count_0_NOT_5(self):
        exp = ExplanationMetadata()
        assert exp.evidence_count == 0

    def test_explanation_confidence_0_NOT_0_72(self):
        exp = ExplanationMetadata()
        assert exp.confidence == 0.0

    def test_explanation_stability_score_0(self):
        exp = ExplanationMetadata()
        assert exp.stability_score == 0.0


# ============================================================
# T3: ExistenceMilestone 防伪造闸口
# ============================================================

class TestExistenceMilestoneGates:
    """
    核心闸口:保证 Timeline 不会出现 "编出来" 的人生事件。
    """

    def _make_good_evidence(self):
        return EvidenceReference(
            source_type=SOURCE_GROWTH_PROPOSAL,
            source_id="pcr_ecf5397dc4",
            confidence=0.85,
        )

    # --------------------------------------------------------
    # 默认值 = 完全不可用 (UNKNOWN)
    # --------------------------------------------------------
    def test_defaults_all_unknown(self):
        m = ExistenceMilestone()
        assert m.milestone_type == ""
        assert m.timestamp == ""
        assert m.title == ""
        assert m.summary == ""
        assert m.sources == []
        assert m.affected_keys == []
        assert m.explanation.confidence == 0.0
        assert m.explanation.evidence_count == 0

    def test_default_well_formed_false(self):
        m = ExistenceMilestone()
        assert m.is_well_formed() is False

    # --------------------------------------------------------
    # 4 条 well_formed 规则独立检查
    # --------------------------------------------------------
    def test_invalid_type_rejected(self):
        # type = "llm_generated_story" 不在合法列表里
        m = ExistenceMilestone(
            timestamp=now_iso(),
            milestone_type="llm_generated_story",
            title="第一章 觉醒",
            sources=[self._make_good_evidence()],
        )
        assert m.has_valid_type() is False
        assert m.is_well_formed() is False

    def test_missing_timestamp_rejected(self):
        m = ExistenceMilestone(
            milestone_type=MILESTONE_GROWTH,
            title="温柔倾向增强",
            sources=[self._make_good_evidence()],
        )
        assert m.has_valid_timestamp() is False
        assert m.is_well_formed() is False

    def test_timestamp_not_utc_z_rejected(self):
        # 必须以 Z 结尾(UTC)
        m = ExistenceMilestone(
            timestamp="2026-08-02T10:00:00",
            milestone_type=MILESTONE_GROWTH,
            title="温柔倾向增强",
            sources=[self._make_good_evidence()],
        )
        assert m.has_valid_timestamp() is False

    def test_no_sources_rejected(self):
        """即使一切都填好了,没有证据 = 不算 Milestone。"""
        m = ExistenceMilestone(
            timestamp=now_iso(),
            milestone_type=MILESTONE_GROWTH,
            title="温柔倾向增强",
            summary="来自 14 次互动",
        )
        assert m.is_evidence_backed() is False
        assert m.is_well_formed() is False

    def test_empty_fact_content_rejected(self):
        """即使有证据,标题摘要都空 = 没什么可展示的。"""
        m = ExistenceMilestone(
            timestamp=now_iso(),
            milestone_type=MILESTONE_GROWTH,
            sources=[self._make_good_evidence()],
        )
        assert m.has_fact_content() is False
        assert m.is_well_formed() is False

    # --------------------------------------------------------
    # 幸福路径:一个合格的 Milestone
    # --------------------------------------------------------
    def test_happy_path_growth_milestone_is_well_formed(self):
        m = ExistenceMilestone(
            timestamp=now_iso(),
            milestone_type=MILESTONE_GROWTH,
            title="温柔倾向增强",
            summary="经过多次互动后检测到对理解型回应的稳定偏好",
            sources=[self._make_good_evidence()],
            affected_keys=["warmth"],
            impact=ImpactMap(personality=True),
            explanation=ExplanationMetadata(
                evidence_count=1,
                confidence=0.85,
            ),
        )
        assert m.is_well_formed() is True
        assert m.is_evidence_backed() is True
        assert m.confidence() == pytest.approx(0.85)
        assert m.impact.personality is True
        assert m.impact.any_impact() is True

    # --------------------------------------------------------
    # 证据有效性独立闸口
    # --------------------------------------------------------
    def test_sources_without_real_source_ids_not_backed(self):
        """证据存在,但 source_id 是空的 → 证据是假的。"""
        fake_evidence = EvidenceReference(
            source_type=SOURCE_GROWTH_PROPOSAL,
            source_id="",   # 没有真实 ID
            confidence=0.9,
        )
        m = ExistenceMilestone(
            timestamp=now_iso(),
            milestone_type=MILESTONE_BELIEF,
            title="形成核心信念",
            sources=[fake_evidence],
        )
        # is_evidence_backed 要求至少 1 条 has_real_source()
        assert m.is_evidence_backed() is False
        assert m.is_well_formed() is False


# ============================================================
# T4: Confidence 夹取 [0, 1]
# ============================================================

class TestConfidenceClamping:
    def test_confidence_above_1_clipped(self):
        exp = ExplanationMetadata(confidence=1.72)
        m = ExistenceMilestone(explanation=exp)
        assert m.confidence() == pytest.approx(1.0)

    def test_confidence_below_0_clipped(self):
        exp = ExplanationMetadata(confidence=-0.5)
        m = ExistenceMilestone(explanation=exp)
        assert m.confidence() == pytest.approx(0.0)

    def test_evidence_reference_clamp_Not_yet_applied_via_method(self):
        # EvidenceReference 目前不 clamp,但我们要求取值 ∈ [0,1]
        # 这里只做结构验证
        er = EvidenceReference(confidence=0.8)
        assert 0.0 <= er.confidence <= 1.0


# ============================================================
# T5: 序列化往返 (to_dict / from_dict) + 容错
# ============================================================

class TestSerializationRoundTrip:

    def _make_complete_milestone(self) -> ExistenceMilestone:
        return ExistenceMilestone(
            milestone_id="em_TESTCASE001",
            timestamp="2026-08-02T00:00:00Z",
            milestone_type=MILESTONE_TRAIT_CHANGE,
            title="温柔倾向开始稳定形成",
            summary="经过多次互动后检测到对理解型回应的稳定偏好",
            sources=[
                EvidenceReference(
                    evidence_id="er_E01",
                    source_type=SOURCE_SELF_MODEL_TRAIT,
                    source_id="trait_warmth_2026_08_02",
                    confidence=0.82,
                    note="self_model_v3 检测到 warmth 连续 5 次高于阈值",
                    metadata={"batch_id": "smv3_b8f2"},
                ),
                EvidenceReference(
                    evidence_id="er_E02",
                    source_type=SOURCE_GROWTH_PROPOSAL,
                    source_id="pcr_ecf5397dc4",
                    confidence=0.78,
                ),
            ],
            impact=ImpactMap(
                personality=True,
                belief=False,
                identity=False,
                relationship=False,
                memory=True,
            ),
            explanation=ExplanationMetadata(
                why_created="",  # D.8 才填,当前保持空
                evidence_count=2,
                confidence=0.80,
                stability_score=0.55,
            ),
            affected_keys=["warmth", "empathy"],
            adapter_name="trait_change_adapter_v1",
        )

    def test_to_dict_shape_contains_all_core_keys(self):
        m = self._make_complete_milestone()
        d = m.to_dict()
        for k in (
            "milestone_id", "timestamp", "milestone_type",
            "title", "summary", "sources", "impact",
            "explanation", "affected_keys", "adapter_name",
            "schema_version",
        ):
            assert k in d
        assert len(d["sources"]) == 2

    def test_from_dict_none_falls_back_defaults(self):
        """防 crash:from_dict(None) 返回默认值实例,不伪造。"""
        m = ExistenceMilestone.from_dict(None)
        assert isinstance(m, ExistenceMilestone)
        assert m.is_well_formed() is False
        assert m.explanation.evidence_count == 0
        assert m.impact.any_impact() is False

    def test_from_dict_empty_dict_falls_back_defaults(self):
        m = ExistenceMilestone.from_dict({})
        assert m.milestone_type == ""
        assert m.timestamp == ""
        assert m.sources == []

    def test_from_dict_partial_only_type_does_not_invent_other_fields(self):
        m = ExistenceMilestone.from_dict({"milestone_type": MILESTONE_BELIEF})
        assert m.milestone_type == MILESTONE_BELIEF
        # 其他字段必须仍为空
        assert m.timestamp == ""
        assert m.title == ""
        assert m.sources == []

    def test_round_trip_preserves_values(self):
        m1 = self._make_complete_milestone()
        d1 = m1.to_dict()
        m2 = ExistenceMilestone.from_dict(d1)
        d2 = m2.to_dict()
        # 核心字段完全一致
        for k in (
            "milestone_id", "timestamp", "milestone_type",
            "title", "summary", "affected_keys", "adapter_name",
            "schema_version",
        ):
            assert d1[k] == d2[k], f"round-trip key mismatch: {k}"
        assert len(d1["sources"]) == len(d2["sources"])
        assert d2["sources"][0]["source_id"] == "trait_warmth_2026_08_02"
        assert d2["impact"]["personality"] is True
        assert d2["impact"]["memory"] is True
        assert d2["explanation"]["evidence_count"] == 2
        assert d2["explanation"]["confidence"] == pytest.approx(0.80)

    def test_from_dict_dirty_data_does_not_crash(self):
        """防攻击:类型不匹配时回落默认,不抛异常。"""
        dirty = {
            "milestone_id": 123,          # 期望 str,给 int → 回落 uuid 默认
            "timestamp": None,            # 期望 str → ""
            "milestone_type": True,       # 期望 str → ""
            "sources": [None, "not_a_dict", 42],  # 全是垃圾 → 忽略
            "impact": ["not", "a", "dict"],       # 忽略
            "explanation": None,                   # 忽略
            "affected_keys": "not_a_list",  # 字符串 → 空列表
        }
        m = ExistenceMilestone.from_dict(dirty)
        assert isinstance(m, ExistenceMilestone)
        # milestone_id 回落为默认工厂 → 开头 em_
        assert m.milestone_id.startswith("em_")
        # sources 列表中没有一条合法证据
        assert m.sources == []
        assert m.affected_keys == []
        assert m.impact.any_impact() is False
        assert m.explanation.confidence == 0.0


# ============================================================
# T6: now_iso 辅助函数
# ============================================================

class TestNowIsoHelper:
    def test_now_iso_ends_with_z(self):
        ts = now_iso()
        assert ts.endswith("Z")
        assert "T" in ts

    def test_now_iso_can_be_parsed(self):
        from datetime import datetime
        ts = now_iso()
        # 去掉 Z 后能被 fromisoformat 解析
        dt = datetime.fromisoformat(ts[:-1])
        assert isinstance(dt, datetime)
