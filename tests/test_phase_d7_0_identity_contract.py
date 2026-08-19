# -*- coding: utf-8 -*-
"""Phase D.7.0 Identity Contract 单元测试。

重点:
======
1. 默认值全空 / 0 / 0.0 / UNKNOWN (不伪造默认身份)
2. 序列化/反序列化 to_dict → from_dict → 等价 (可写入存档)
3. 证据链合规性: IdentityEvidence / FormationSource 缺失则组件 q 自动 unknown
4. IdentityQuality.is_reliable 阈值: ok/locked + evidence_coverage_pct >= 0.30
5. version (identity_canonical_version): 默认 1,不能在 __post_init__ 里自动 bump
6. identity_name 默认空字符串 (不是"无名"或"浅雾羽依")
7. _clamp_unit 防御: NaN / 负数 / 超 1 必须夹到 [0,1]
8. is_built 判定: profile=None / built_at_iso="" → False
9. FormationSource.well_formed 要求 milestone_id AND formation_reason 非空
10. VALID_IDENTITY_STATUSES / VALID_SOURCE_TYPES 常量合法性检查
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import math

import pytest

from src.contracts.identity import (
    BeliefAnchorEntry,
    CoreValueEntry,
    FormationSource,
    IDENTITY_SCHEMA_VERSION,
    IDENTITY_STATUS_DEGRADED,
    IDENTITY_STATUS_LOCKED,
    IDENTITY_STATUS_OK,
    IDENTITY_STATUS_UNKNOWN,
    ID_SOURCE_EXISTENCE_MILESTONE,
    ID_SOURCE_STABLE_BELIEF,
    ID_SOURCE_STABLE_TRAIT,
    IdentityEvidence,
    IdentityProfile,
    IdentityQuality,
    IdentitySnapshot,
    ORIGIN_MANUAL,
    ORIGIN_STABLE_BELIEF,
    ORIGIN_STABLE_TRAIT,
    PersonalityAnchorEntry,
    VALID_IDENTITY_SOURCE_TYPES,
    VALID_IDENTITY_STATUSES,
    VALID_ORIGIN_TYPES,
    _clamp_unit,
)


# ============================================================
# Fixture helpers (只生成 最小可用 / 完全无数据 两种,不伪造)
# ============================================================
def _iso_z(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_min_evidence() -> IdentityEvidence:
    return IdentityEvidence(
        evidence_id="ide_test_001",
        source_type=ID_SOURCE_STABLE_BELIEF,
        source_id="b_test_core_001",
        source_field="content",
        weight=0.8,
        note="稳定信念原文支撑",
        confidence=0.9,
        timestamp_iso=_iso_z(days_ago=7),
    )


def _make_min_formation_source() -> FormationSource:
    return FormationSource(
        milestone_id="em_test_identity_anchor_01",
        formation_reason="belief milestone: 证据 7 → 形成核心价值",
        contribution_weight=0.6,
        milestone_timestamp_iso=_iso_z(days_ago=10),
    )


class TestPhaseD70ClampAndConstants:
    """_clamp_unit 夹值 + 常量合法性。"""

    def test_clamp_normal_values(self) -> None:
        assert _clamp_unit(0.0) == pytest.approx(0.0)
        assert _clamp_unit(0.5) == pytest.approx(0.5)
        assert _clamp_unit(1.0) == pytest.approx(1.0)

    def test_clamp_extremes_nan_none(self) -> None:
        assert _clamp_unit(-0.2) == pytest.approx(0.0)
        assert _clamp_unit(1.5) == pytest.approx(1.0)
        assert _clamp_unit(None) == pytest.approx(0.0)
        assert _clamp_unit(math.nan) == pytest.approx(0.0)
        assert _clamp_unit("not a number") == pytest.approx(0.0)

    def test_valid_statuses_contains_unknown_ok_locked(self) -> None:
        assert IDENTITY_STATUS_UNKNOWN in VALID_IDENTITY_STATUSES
        assert IDENTITY_STATUS_OK in VALID_IDENTITY_STATUSES
        assert IDENTITY_STATUS_LOCKED in VALID_IDENTITY_STATUSES
        assert IDENTITY_STATUS_DEGRADED in VALID_IDENTITY_STATUSES
        # 至少 5 种状态 (unknown / aggregating / ok / degraded / offline / locked)
        assert len(VALID_IDENTITY_STATUSES) >= 5

    def test_valid_source_types_contains_three_core(self) -> None:
        assert ID_SOURCE_STABLE_TRAIT in VALID_IDENTITY_SOURCE_TYPES
        assert ID_SOURCE_STABLE_BELIEF in VALID_IDENTITY_SOURCE_TYPES
        assert ID_SOURCE_EXISTENCE_MILESTONE in VALID_IDENTITY_SOURCE_TYPES
        assert len(VALID_IDENTITY_SOURCE_TYPES) >= 3

    def test_valid_origin_types_includes_manual(self) -> None:
        assert ORIGIN_STABLE_TRAIT in VALID_ORIGIN_TYPES
        assert ORIGIN_STABLE_BELIEF in VALID_ORIGIN_TYPES
        assert ORIGIN_MANUAL in VALID_ORIGIN_TYPES

    def test_schema_version_is_string_1_0(self) -> None:
        assert isinstance(IDENTITY_SCHEMA_VERSION, str)
        # 1.x 系列
        assert IDENTITY_SCHEMA_VERSION.startswith("1.")


class TestPhaseD70IdentityQuality:
    """默认值 + is_reliable + 夹值 + 序列化。"""

    def test_default_values(self) -> None:
        q = IdentityQuality()
        assert q.status == IDENTITY_STATUS_UNKNOWN
        assert q.source == ""
        assert q.error == ""
        assert q.evidence_coverage_pct == pytest.approx(0.0)
        assert q.stability_index == pytest.approx(0.0)
        assert q.last_audited_at == ""
        assert q.meta == {}

    def test_invalid_status_defaults_to_unknown(self) -> None:
        q = IdentityQuality(status="NOT_EXIST")
        assert q.status == IDENTITY_STATUS_UNKNOWN

    def test_invalid_meta_defaults_to_empty_dict(self) -> None:
        q = IdentityQuality(meta=["oops", "list"])  # type: ignore[arg-type]
        assert q.meta == {}

    def test_coverage_stability_clamped(self) -> None:
        q = IdentityQuality(evidence_coverage_pct=2.0, stability_index=-0.5)
        assert q.evidence_coverage_pct == pytest.approx(1.0)
        assert q.stability_index == pytest.approx(0.0)

    def test_is_reliable_requirements(self) -> None:
        # ① status unknown → False
        q1 = IdentityQuality(
            status=IDENTITY_STATUS_UNKNOWN, evidence_coverage_pct=0.80
        )
        assert q1.is_reliable is False

        # ② status ok 但 coverage < 0.30 → False
        q2 = IdentityQuality(status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.20)
        assert q2.is_reliable is False

        # ③ status ok + coverage >= 0.30 → True
        q3 = IdentityQuality(status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.30)
        assert q3.is_reliable is True
        q4 = IdentityQuality(status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.55)
        assert q4.is_reliable is True

        # ④ locked + coverage >= 0.30 → True (即使来源失效,锁定仍然可信)
        q5 = IdentityQuality(
            status=IDENTITY_STATUS_LOCKED, evidence_coverage_pct=0.30
        )
        assert q5.is_reliable is True

    def test_is_editable_locked_not_editable(self) -> None:
        q_locked = IdentityQuality(status=IDENTITY_STATUS_LOCKED)
        assert q_locked.is_editable is False
        for st in (IDENTITY_STATUS_UNKNOWN, IDENTITY_STATUS_OK, IDENTITY_STATUS_DEGRADED):
            q = IdentityQuality(status=st)
            assert q.is_editable is True, f"status {st} 应该可编辑"

    def test_to_dict_from_dict_roundtrip(self) -> None:
        q1 = IdentityQuality(
            status=IDENTITY_STATUS_OK,
            source="identity_aggregator_v1",
            error="",
            evidence_coverage_pct=0.72,
            stability_index=0.66,
            last_audited_at=_iso_z(days_ago=3),
            meta={"manual_reviewed_by": "creator"},
        )
        d = q1.to_dict()
        assert isinstance(d, dict)
        assert "is_reliable" in d
        assert "is_editable" in d
        q2 = IdentityQuality.from_dict(d)
        assert q2.status == q1.status
        assert q2.source == q1.source
        assert q2.evidence_coverage_pct == pytest.approx(0.72)
        assert q2.stability_index == pytest.approx(0.66)
        assert q2.last_audited_at == q1.last_audited_at
        assert q2.meta == {"manual_reviewed_by": "creator"}

    def test_from_dict_bad_input_returns_default(self) -> None:
        q = IdentityQuality.from_dict(None)
        assert q.status == IDENTITY_STATUS_UNKNOWN
        assert q.evidence_coverage_pct == pytest.approx(0.0)
        q2 = IdentityQuality.from_dict("not a dict")  # type: ignore[arg-type]
        assert q2.status == IDENTITY_STATUS_UNKNOWN


class TestPhaseD70FormationSource:
    """FormationSource 默认值 + well_formed + 序列化。"""

    def test_defaults_unknown(self) -> None:
        s = FormationSource()
        assert s.milestone_id == ""
        assert s.formation_reason == ""
        assert s.contribution_weight == pytest.approx(0.0)
        assert s.milestone_timestamp_iso == ""

    def test_well_formed_requires_both_milestone_id_and_reason(self) -> None:
        # 只有 milestone_id → False
        s1 = FormationSource(milestone_id="em_x", formation_reason="")
        assert s1.well_formed is False
        # 只有 reason → False
        s2 = FormationSource(milestone_id="", formation_reason="X change")
        assert s2.well_formed is False
        # 二者都有 → True
        s3 = FormationSource(milestone_id="em_x", formation_reason="X change")
        assert s3.well_formed is True

    def test_reason_clamped_160_chars(self) -> None:
        s = FormationSource(formation_reason="a" * 500, contribution_weight=9.0)
        assert len(s.formation_reason) == 160
        assert s.contribution_weight == pytest.approx(1.0)

    def test_roundtrip_dict(self) -> None:
        s1 = _make_min_formation_source()
        s2 = FormationSource.from_dict(s1.to_dict())
        assert s2.milestone_id == s1.milestone_id
        assert s2.formation_reason == s1.formation_reason
        assert s2.contribution_weight == pytest.approx(s1.contribution_weight)
        assert s2.well_formed is True


class TestPhaseD70IdentityEvidence:
    """IdentityEvidence 默认值 + valid_source + 夹值 + 序列化。"""

    def test_defaults_unknown(self) -> None:
        e = IdentityEvidence()
        assert e.evidence_id.startswith("ide_")
        assert e.source_type == ""
        assert e.source_id == ""
        assert e.source_field == ""
        assert e.weight == pytest.approx(0.0)
        assert e.note == ""
        assert e.confidence == pytest.approx(0.0)
        assert e.timestamp_iso == ""

    def test_valid_source_requires_type_and_id(self) -> None:
        # 未识别类型
        e1 = IdentityEvidence(source_type="xxx", source_id="b_1")
        assert e1.valid_source is False
        # 正确类型 但无 id
        e2 = IdentityEvidence(source_type=ID_SOURCE_STABLE_BELIEF, source_id="")
        assert e2.valid_source is False
        # 二者都合法 → True
        e3 = IdentityEvidence(source_type=ID_SOURCE_STABLE_TRAIT, source_id="t_warmth")
        assert e3.valid_source is True

    def test_weight_confidence_clamped_note_trimmed(self) -> None:
        e = IdentityEvidence(
            weight=-0.1, confidence=100.0, note="x" * 500, source_field="y" * 500
        )
        assert e.weight == pytest.approx(0.0)
        assert e.confidence == pytest.approx(1.0)
        assert len(e.note) == 80
        assert len(e.source_field) == 80

    def test_roundtrip_dict_preserves_ids(self) -> None:
        e1 = _make_min_evidence()
        e2 = IdentityEvidence.from_dict(e1.to_dict())
        assert e2.evidence_id == e1.evidence_id
        assert e2.source_type == e1.source_type
        assert e2.source_id == e1.source_id
        assert e2.weight == pytest.approx(0.8)
        assert e2.confidence == pytest.approx(0.9)
        assert e2.valid_source is True


class TestPhaseD70CoreValueEntry:
    """CoreValueEntry 默认值 + 合规性 (无证据自动 unknown) + 序列化。"""

    def test_default_values_no_fabrication(self) -> None:
        c = CoreValueEntry()
        assert c.value_id == ""
        assert c.content == ""
        assert c.origin_type == ORIGIN_STABLE_BELIEF
        assert c.origin_id == ""
        assert c.confidence == pytest.approx(0.0)
        assert c.evidence_count == 0
        assert c.first_formed_at == ""
        assert c.last_confirmed_at == ""
        assert c.formation_sources == []
        assert c.evidence == []
        # 没有证据 → q.status 必须 unknown, error 带标识
        assert c.q.status == IDENTITY_STATUS_UNKNOWN
        assert "no_evidence_or_formation_sources" in c.q.error

    def test_with_evidence_does_not_auto_set_unknown(self) -> None:
        c = CoreValueEntry(
            value_id="v_test_understand",
            content="稳定信念:理解比快速回答重要 (证据 7 条)",
            origin_type=ORIGIN_STABLE_BELIEF,
            origin_id="b_test_core_001",
            confidence=0.9,
            evidence_count=7,
            first_formed_at=_iso_z(days_ago=30),
            last_confirmed_at=_iso_z(days_ago=2),
            formation_sources=[_make_min_formation_source()],
            evidence=[_make_min_evidence()],
            q=IdentityQuality(status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.8),
        )
        assert c.q.status == IDENTITY_STATUS_OK
        assert c.evidence_count == 7
        assert c.confidence == pytest.approx(0.9)
        # content 最长 120
        assert len(c.content) <= 120

    def test_locked_prevents_unknown_downgrade(self) -> None:
        """status=locked 的组件即使现在数据没了,也不被降级 unknown (人工锁定过的身份)。"""
        c = CoreValueEntry(
            q=IdentityQuality(
                status=IDENTITY_STATUS_LOCKED,
                source="manual_lock",
                evidence_coverage_pct=0.40,
            )
        )
        # 即便 evidence_count=0, status 仍应保持 locked
        assert c.q.status == IDENTITY_STATUS_LOCKED

    def test_to_dict_roundtrip(self) -> None:
        c1 = CoreValueEntry(
            value_id="v_test_r1",
            content="优先理解他人,再给出回答",
            origin_type=ORIGIN_STABLE_BELIEF,
            origin_id="b_x",
            confidence=0.85,
            evidence_count=7,
            first_formed_at=_iso_z(days_ago=20),
            formation_sources=[_make_min_formation_source()],
            evidence=[_make_min_evidence()],
            q=IdentityQuality(
                status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.70
            ),
        )
        d = c1.to_dict()
        c2 = CoreValueEntry.from_dict(d)
        assert c2.value_id == "v_test_r1"
        assert c2.origin_id == "b_x"
        assert c2.evidence_count == 7
        assert len(c2.formation_sources) == 1
        assert len(c2.evidence) == 1
        assert c2.q.status == IDENTITY_STATUS_OK
        assert c2.q.evidence_coverage_pct == pytest.approx(0.70)


class TestPhaseD70PersonalityAnchorEntry:
    """PersonalityAnchorEntry 默认值 + 夹值 + 序列化 + stability=0 时自动 unknown。"""

    def test_default_values_no_fabrication(self) -> None:
        p = PersonalityAnchorEntry()
        assert p.trait_id == ""
        assert p.name == ""
        assert p.stability == pytest.approx(0.0)
        assert p.current_value_pct == 0
        assert p.evidence_count == 0
        assert p.trend_30d is None
        assert p.q.status == IDENTITY_STATUS_UNKNOWN

    def test_stability_value_pct_clamped(self) -> None:
        p = PersonalityAnchorEntry(
            stability=1.8, current_value_pct=150, evidence_count=-5,
            trend_30d="not a float",
        )
        assert p.stability == pytest.approx(1.0)
        assert p.current_value_pct == 100
        assert p.evidence_count == 0
        assert p.trend_30d is None  # 解析失败保持 None

    def test_with_high_stability_anchor_ok(self) -> None:
        s = FormationSource(
            milestone_id="em_trait_anchor_01",
            formation_reason="trait_change milestone: warmth 0.60 → 0.72 跨过 0.70",
            contribution_weight=0.5,
        )
        p = PersonalityAnchorEntry(
            trait_id="trait_warmth",
            name="温柔",
            stability=0.82,
            current_value_pct=72,
            evidence_count=17,
            first_observed_at=_iso_z(days_ago=40),
            last_observed_at=_iso_z(days_ago=1),
            trend_30d=+0.04,
            formation_sources=[s],
            q=IdentityQuality(status=IDENTITY_STATUS_OK, stability_index=0.82),
        )
        assert p.stability == pytest.approx(0.82)
        assert p.trend_30d == pytest.approx(0.04)
        assert p.q.status == IDENTITY_STATUS_OK
        # 夹到 0..100% 的强度值
        assert p.current_value_pct == 72

    def test_roundtrip_dict_preserves_trend_none_and_positive(self) -> None:
        # None trend
        p1 = PersonalityAnchorEntry(trait_id="t1", name="t1", trend_30d=None)
        d1 = p1.to_dict()
        p1r = PersonalityAnchorEntry.from_dict(d1)
        assert p1r.trend_30d is None
        # 0.0 trend (保持 0.0,不被转 None)
        p2 = PersonalityAnchorEntry(trait_id="t2", name="t2", trend_30d=0.0)
        d2 = p2.to_dict()
        p2r = PersonalityAnchorEntry.from_dict(d2)
        assert p2r.trend_30d == pytest.approx(0.0)


class TestPhaseD70BeliefAnchorEntry:
    """BeliefAnchorEntry 默认值 + version 夹值 >= 1 + active + 合规。"""

    def test_default_version_1_not_auto_bump(self) -> None:
        b = BeliefAnchorEntry()
        assert b.version == 1
        assert b.confidence == pytest.approx(0.0)
        assert b.evidence_count == 0
        assert b.active is True
        assert b.q.status == IDENTITY_STATUS_UNKNOWN
        assert "no_confidence_or_evidence" in b.q.error

    def test_version_cannot_be_below_1_via_invalid(self) -> None:
        b = BeliefAnchorEntry(version=0)
        assert b.version == 1
        b2 = BeliefAnchorEntry(version="not int")  # type: ignore[arg-type]
        assert b2.version == 1

    def test_locked_status_not_unknown(self) -> None:
        b = BeliefAnchorEntry(
            belief_id="b_locked_manual",
            q=IdentityQuality(status=IDENTITY_STATUS_LOCKED, evidence_coverage_pct=0.5),
        )
        assert b.q.status == IDENTITY_STATUS_LOCKED

    def test_to_dict_roundtrip_active_flag(self) -> None:
        b1 = BeliefAnchorEntry(
            belief_id="b_r1",
            domain="interaction",
            content="优先理解再回答",
            confidence=0.86,
            version=3,
            evidence_count=7,
            active=True,
            first_seen_at=_iso_z(days_ago=18),
            last_confirmed_at=_iso_z(days_ago=1),
            q=IdentityQuality(status=IDENTITY_STATUS_OK),
        )
        b2 = BeliefAnchorEntry.from_dict(b1.to_dict())
        assert b2.belief_id == "b_r1"
        assert b2.domain == "interaction"
        assert b2.version == 3
        assert b2.active is True
        assert b2.q.status == IDENTITY_STATUS_OK


class TestPhaseD70IdentityProfile:
    """IdentityProfile 默认值 + component_counts + reliable_ratio + 版本不自增。"""

    def test_defaults_no_fabrication(self) -> None:
        p = IdentityProfile()
        assert p.profile_id.startswith("ip_")
        assert p.built_at_iso == ""  # 不自动 now
        assert p.identity_name == ""  # 不自动填名字
        assert p.identity_canonical_version == 1
        assert p.core_values == []
        assert p.personality_anchors == []
        assert p.belief_anchors == []
        assert p.formation_sources_total == 0
        assert p.evidence_total == 0
        assert p.schema_version == IDENTITY_SCHEMA_VERSION
        assert p.identity_signature == ""

    def test_component_counts_and_reliable_ratio(self) -> None:
        p = IdentityProfile(
            core_values=[
                CoreValueEntry(
                    value_id="v1",
                    evidence_count=5,
                    q=IdentityQuality(
                        status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.8
                    ),
                )
            ],
            personality_anchors=[PersonalityAnchorEntry()],  # unknown
            belief_anchors=[
                BeliefAnchorEntry(
                    evidence_count=3,
                    q=IdentityQuality(
                        status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.8
                    ),
                ),
                BeliefAnchorEntry(),  # unknown
            ],
        )
        counts = p.component_counts
        assert counts == {"core_values": 1, "personality_anchors": 1, "belief_anchors": 2}
        # reliable_ratio = 2 / 4 = 0.50
        assert p.reliable_component_ratio == pytest.approx(0.50)

    def test_reliable_ratio_zero_when_empty(self) -> None:
        assert IdentityProfile().reliable_component_ratio == pytest.approx(0.0)

    def test_to_dict_includes_counts_and_ratio(self) -> None:
        p = IdentityProfile()
        d = p.to_dict()
        assert "component_counts" in d
        assert "reliable_component_ratio" in d
        assert "q" in d

    def test_from_dict_rebuilds_nested_lists(self) -> None:
        p1 = IdentityProfile(
            built_at_iso=_iso_z(),
            identity_name="",  # 保持未设置状态
            identity_canonical_version=1,
            core_values=[CoreValueEntry(value_id="v_x", evidence_count=3)],
            personality_anchors=[PersonalityAnchorEntry(trait_id="t_x", stability=0.78)],
            belief_anchors=[BeliefAnchorEntry(belief_id="b_x", confidence=0.88)],
            formation_sources_total=5,
            evidence_total=11,
        )
        p2 = IdentityProfile.from_dict(p1.to_dict())
        assert p2.built_at_iso == p1.built_at_iso
        assert p2.identity_name == ""  # 未设置
        assert p2.identity_canonical_version == 1
        assert len(p2.core_values) == 1
        assert len(p2.personality_anchors) == 1
        assert len(p2.belief_anchors) == 1
        assert p2.formation_sources_total == 5
        assert p2.evidence_total == 11


class TestPhaseD70IdentitySnapshot:
    """IdentitySnapshot 默认值 + is_built + profile=None 情况 + 序列化。"""

    def test_default_values(self) -> None:
        s = IdentitySnapshot()
        assert s.snapshot_id.startswith("ids_")
        assert s.created_at_iso == ""
        assert s.profile is None
        assert s.warnings == []
        assert s.generation_context == {}
        assert s.q.status == IDENTITY_STATUS_UNKNOWN
        assert s.schema_version == IDENTITY_SCHEMA_VERSION

    def test_is_built_requires_profile_and_built_at(self) -> None:
        s = IdentitySnapshot()
        # ① profile=None → False
        assert s.is_built is False
        # ② profile 存在但 built_at_iso 空 → False
        s.profile = IdentityProfile()  # built_at 默认空
        assert s.is_built is False
        # ③ profile.built_at 非空 → True
        s.profile.built_at_iso = _iso_z()
        assert s.is_built is True

    def test_component_counts_without_profile(self) -> None:
        s = IdentitySnapshot()
        assert s.component_counts == {
            "core_values": 0,
            "personality_anchors": 0,
            "belief_anchors": 0,
        }

    def test_to_dict_roundtrip_preserves_profile_none(self) -> None:
        s1 = IdentitySnapshot()
        d = s1.to_dict()
        assert d["profile"] is None
        assert d["is_built"] is False
        s2 = IdentitySnapshot.from_dict(d)
        assert s2.profile is None
        assert s2.is_built is False

    def test_to_dict_roundtrip_with_full_profile(self) -> None:
        s1 = IdentitySnapshot(
            snapshot_id="ids_test_known_001",
            created_at_iso=_iso_z(),
            profile=IdentityProfile(
                built_at_iso=_iso_z(),
                identity_name="浅雾羽依",  # 只有被 Aggregator 明确设置时才填,默认不填
                identity_canonical_version=1,
                core_values=[
                    CoreValueEntry(
                        value_id="v_1",
                        content="稳定信念:理解优先(证据 7)",
                        confidence=0.88,
                        evidence_count=7,
                        formation_sources=[_make_min_formation_source()],
                        evidence=[_make_min_evidence()],
                        q=IdentityQuality(status=IDENTITY_STATUS_OK),
                    )
                ],
                personality_anchors=[
                    PersonalityAnchorEntry(
                        trait_id="trait_warmth",
                        name="温柔",
                        stability=0.82,
                        current_value_pct=72,
                        evidence_count=17,
                        trend_30d=0.04,
                        formation_sources=[_make_min_formation_source()],
                        q=IdentityQuality(
                            status=IDENTITY_STATUS_OK, stability_index=0.82
                        ),
                    )
                ],
                belief_anchors=[
                    BeliefAnchorEntry(
                        belief_id="b_core_001",
                        domain="value",
                        content="帮助他人理解世界很重要",
                        confidence=0.86,
                        evidence_count=7,
                        active=True,
                        evidence=[_make_min_evidence()],
                        q=IdentityQuality(status=IDENTITY_STATUS_OK),
                    )
                ],
                formation_sources_total=2,
                evidence_total=2,
                identity_signature="sig_placeholder_hash_not_real_yet",
            ),
            warnings=[
                "identity_canonical_version still v1, only 2 formation sources",
            ],
            generation_context={"aggregator_version": "d7.1-impl-todo", "run": 1},
            q=IdentityQuality(status=IDENTITY_STATUS_OK, evidence_coverage_pct=0.75),
        )
        s2 = IdentitySnapshot.from_dict(s1.to_dict())
        assert s2.snapshot_id == "ids_test_known_001"
        assert s2.is_built is True
        assert s2.profile is not None
        assert s2.profile.identity_name == "浅雾羽依"
        assert len(s2.warnings) == 1
        assert s2.generation_context == {
            "aggregator_version": "d7.1-impl-todo", "run": 1
        }
        assert s2.q.status == IDENTITY_STATUS_OK
        counts = s2.component_counts
        assert counts == {"core_values": 1, "personality_anchors": 1, "belief_anchors": 1}

    def test_default_identity_name_never_defaults_to_string(self) -> None:
        """「不伪造」的核心测试: identity_name 必须是 空字符串,而不是任何占位词。"""
        assert IdentityProfile().identity_name == ""
        assert IdentitySnapshot().to_dict()["profile"] is None
        s = IdentitySnapshot(profile=IdentityProfile())
        assert s.profile is not None
        assert s.profile.identity_name == ""


class TestPhaseD70ConstantsValidation:
    """常量本身的合规性测试 (防拼写错误 / 重复)。"""

    def test_no_duplicate_statuses(self) -> None:
        assert len(set(VALID_IDENTITY_STATUSES)) == len(VALID_IDENTITY_STATUSES)

    def test_no_duplicate_source_types(self) -> None:
        assert len(set(VALID_IDENTITY_SOURCE_TYPES)) == len(VALID_IDENTITY_SOURCE_TYPES)

    def test_no_duplicate_origin_types(self) -> None:
        assert len(set(VALID_ORIGIN_TYPES)) == len(VALID_ORIGIN_TYPES)
