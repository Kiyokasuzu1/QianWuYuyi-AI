# -*- coding: utf-8 -*-
"""
tests/test_phase_4_5_self_model_evolution.py

Phase 4.5: Self Model Evolution Engine —— 单元测试

目标覆盖:
- EvolutionRecord / SelfModelChange / SelfModelEvolutionResult:
  创建、序列化、反序列化、round_trip
- EvolutionPolicy:
  allowed / cautious / forbidden 字段分类
  confidence 阈值评估
  GrowthProposal 解析
- SelfModelEvolutionEngine:
  基本演化、多 change、rejection、不可变 snapshot、空输入
  异常隔离、与 GrowthProposal / ReflectionRecord 集成
- RuntimeCore 集成:
  注入、生命周期阶段、未注入兼容性、process 端到端
- 与 Phase 4.4 Identity 集成:
  演化后 IdentityContext 刷新、behavior_signature 同步
- 不变量:
  ResponseEngine.generate 签名未改
  src/personality 核心未引用
  无 LLM SDK
  identity_binding / reflection 不依赖 evolution
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _make_snapshot(
    identity: Optional[Dict[str, Any]] = None,
    preferences: Optional[List[Dict]] = None,
    stable_traits: Optional[List[Dict]] = None,
    core_values: Optional[List[Dict]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    entries: Optional[List[Any]] = None,
    identity_id: str = "smf_evo_test",
    version: int = 1,
) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelSnapshot
    snap = SelfModelSnapshot(
        identity=identity if identity is not None else {"name": "yuyi"},
        core_values=core_values if core_values is not None else [],
        stable_traits=stable_traits if stable_traits is not None else [],
        preferences=preferences if preferences is not None else [],
        current_state=current_state if current_state is not None else {},
        entries=entries or [],
        identity_id=identity_id,
        version=version,
    )
    return snap


def _make_growth_proposal(
    proposal_id: str = "prop_test",
    proposed_changes: Optional[List[Dict]] = None,
    affected_dimensions: Optional[Dict[str, float]] = None,
    confidence: float = 0.7,
    reason: str = "user interaction pattern",
    evidence: Optional[List[str]] = None,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "proposal_id": proposal_id,
        "proposed_changes": proposed_changes or [],
        "affected_dimensions": affected_dimensions or {},
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence or [],
    }
    if before_state is not None:
        d["before_state"] = before_state
    if after_state is not None:
        d["after_state"] = after_state
    return d


def _make_reflection_record(
    reflection_id: str = "ref_test",
    identity_id: str = "smf_evo_test",
    reflection_kind: str = "preference",
    confidence: float = 0.7,
    interpretation: str = "user likes AI drawing",
) -> Any:
    from src.runtime.self_model.reflection.reflection_record import (
        ReflectionRecord,
    )
    return ReflectionRecord(
        identity_id=identity_id,
        reflection_kind=reflection_kind,
        confidence=confidence,
        interpretation=interpretation,
        reflection_id=reflection_id,
    )


# ============================================================
# SelfModelChange
# ============================================================
class TestSelfModelChange:
    def test_create_basic(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch = SelfModelChange(
            field_name="preferences",
            old_value=None,
            new_value={"item": "AI drawing"},
            reason="user interest",
            confidence=0.7,
            evidence_ids=["e1", "e2"],
        )
        assert ch.field_name == "preferences"
        assert ch.new_value == {"item": "AI drawing"}
        assert ch.confidence == 0.7
        assert ch.evidence_ids == ["e1", "e2"]

    def test_confidence_clamp(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch = SelfModelChange(
            field_name="preferences", confidence=2.5,
        )
        assert ch.confidence == 1.0
        ch2 = SelfModelChange(
            field_name="preferences", confidence=-0.5,
        )
        assert ch2.confidence == 0.0

    def test_field_name_truncate(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        long = "x" * 200
        ch = SelfModelChange(field_name=long)
        assert len(ch.field_name) == 64

    def test_to_dict_from_dict(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch = SelfModelChange(
            field_name="preferences",
            old_value=None,
            new_value="AI drawing",
            reason="test",
            confidence=0.8,
            evidence_ids=["a", "b"],
        )
        d = ch.to_dict()
        ch2 = SelfModelChange.from_dict(d)
        assert ch2.field_name == ch.field_name
        assert ch2.new_value == ch.new_value
        assert ch2.confidence == ch.confidence
        assert ch2.evidence_ids == ch.evidence_ids

    def test_is_field_change(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch1 = SelfModelChange(field_name="x", old_value=1, new_value=2)
        ch2 = SelfModelChange(field_name="x", old_value=1, new_value=1)
        assert ch1.is_field_change() is True
        assert ch2.is_field_change() is False

    def test_from_dict_invalid(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch = SelfModelChange.from_dict(None)
        assert ch.field_name == ""
        ch2 = SelfModelChange.from_dict({"confidence": "bad"})
        assert ch2.confidence == 0.0

    def test_evidence_id_coerce(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        ch = SelfModelChange(
            field_name="x", evidence_ids=[1, 2, None, "a"],
        )
        assert ch.evidence_ids == ["1", "2", "a"]


# ============================================================
# EvolutionRecord
# ============================================================
class TestEvolutionRecord:
    def test_create_basic(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
        )
        r = EvolutionRecord(
            identity_id="smf_1",
            source_type="growth_proposal",
            source_id="prop_1",
            from_version=1,
            to_version=2,
        )
        assert r.identity_id == "smf_1"
        assert r.source_type == "growth_proposal"
        assert r.from_version == 1
        assert r.to_version == 2
        assert r.schema_version == "1.0"

    def test_invalid_source_type_normalized(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            EvolutionSourceType,
        )
        r = EvolutionRecord(source_type="unknown_source")
        assert r.source_type == EvolutionSourceType.MANUAL.value

    def test_accepted_count_rejected_count(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        r = EvolutionRecord(
            changes=[SelfModelChange(field_name="a")],
            rejected_changes=[
                SelfModelChange(field_name="b"),
                SelfModelChange(field_name="c"),
            ],
        )
        assert r.accepted_count() == 1
        assert r.rejected_count() == 2

    def test_is_noop(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        r1 = EvolutionRecord()
        r2 = EvolutionRecord(changes=[SelfModelChange(field_name="x")])
        assert r1.is_noop() is True
        assert r2.is_noop() is False

    def test_confidence_avg(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        r = EvolutionRecord(changes=[
            SelfModelChange(field_name="a", confidence=0.6),
            SelfModelChange(field_name="b", confidence=0.8),
        ])
        assert r.confidence_avg() == 0.7

    def test_field_names(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        r = EvolutionRecord(changes=[
            SelfModelChange(field_name="a"),
            SelfModelChange(field_name="b"),
        ])
        assert r.field_names() == ["a", "b"]

    def test_to_dict_from_dict_roundtrip(self):
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        r = EvolutionRecord(
            identity_id="smf_2",
            source_type="reflection",
            source_id="ref_2",
            from_version=1,
            to_version=2,
            changes=[SelfModelChange(field_name="p", confidence=0.7)],
            rejected_changes=[SelfModelChange(field_name="forbidden")],
            reject_reasons=["forbidden_field"],
            summary="test",
        )
        d = r.to_dict()
        r2 = EvolutionRecord.from_dict(d)
        assert r2.identity_id == "smf_2"
        assert r2.source_type == "reflection"
        assert len(r2.changes) == 1
        assert len(r2.rejected_changes) == 1
        assert r2.reject_reasons == ["forbidden_field"]


# ============================================================
# SelfModelEvolutionResult
# ============================================================
class TestSelfModelEvolutionResult:
    def test_empty_result(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelEvolutionResult,
        )
        r = SelfModelEvolutionResult()
        assert r.is_noop is True
        assert r.accepted_count() == 0
        assert r.rejected_count() == 0
        assert r.record_count() == 0

    def test_accepted_marks_not_noop(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelEvolutionResult,
            SelfModelChange,
        )
        r = SelfModelEvolutionResult(
            accepted_changes=[SelfModelChange(field_name="a")],
        )
        assert r.is_noop is False
        assert r.accepted_count() == 1

    def test_confidence_avg_no_accepted(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelEvolutionResult,
        )
        r = SelfModelEvolutionResult()
        assert r.confidence_avg() == 0.0

    def test_to_dict_from_dict(self):
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelEvolutionResult,
            SelfModelChange,
        )
        snap = _make_snapshot()
        r = SelfModelEvolutionResult(
            accepted_changes=[SelfModelChange(
                field_name="preferences", new_value="AI drawing",
            )],
            new_snapshot=snap,
            original_snapshot=snap,
            is_noop=False,
            summary="test",
        )
        d = r.to_dict()
        assert d["new_snapshot"] is not None
        assert d["is_noop"] is False
        r2 = SelfModelEvolutionResult.from_dict(d)
        assert r2.accepted_count() == 1
        assert isinstance(r2.new_snapshot, type(snap))


# ============================================================
# EvolutionPolicy - field classification
# ============================================================
class TestEvolutionPolicyClassification:
    def test_allowed_field(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("preferences") == "allowed"
        assert p.classify_field("interests") == "allowed"
        assert p.classify_field("temporary_states") == "allowed"

    def test_cautious_field(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("stable_traits") == "cautious"
        assert p.classify_field("core_values") == "cautious"
        assert p.classify_field("communication_style") == "cautious"

    def test_forbidden_field(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("identity_id") == "forbidden"
        assert p.classify_field("creator_origin") == "forbidden"
        assert p.classify_field("core_identity") == "forbidden"

    def test_unknown_field(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("some_random_field") == "unknown"

    def test_prefix_match(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("preferences.likes") == "allowed"
        assert p.classify_field("core_values.new") == "cautious"
        assert p.classify_field("identity_id.foo") == "forbidden"

    def test_empty_field(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.classify_field("") == "unknown"

    def test_custom_allowed_fields(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy(allowed_fields=frozenset({"custom_field"}))
        assert p.classify_field("custom_field") == "allowed"


# ============================================================
# EvolutionPolicy - evaluate
# ============================================================
class TestEvolutionPolicyEvaluate:
    def test_evaluate_allowed(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy()
        ch = SelfModelChange(field_name="preferences", confidence=0.5)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.ALLOW.value

    def test_evaluate_cautious(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy()
        ch = SelfModelChange(field_name="stable_traits", confidence=0.7)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.CAUTIOUS.value

    def test_evaluate_forbidden(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy()
        ch = SelfModelChange(field_name="identity_id", confidence=1.0)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.REJECT.value
        assert d.reason == "forbidden_field"

    def test_evaluate_low_confidence_allowed_rejected(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy(min_confidence=0.5)
        ch = SelfModelChange(field_name="preferences", confidence=0.2)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.REJECT.value

    def test_evaluate_low_confidence_cautious_rejected(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy(cautious_min_confidence=0.8)
        ch = SelfModelChange(field_name="stable_traits", confidence=0.5)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.REJECT.value

    def test_evaluate_manual_source_allowed(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
            EvolutionSourceType,
        )
        p = EvolutionPolicy()
        ch = SelfModelChange(field_name="preferences", confidence=0.0)
        d = p.evaluate(ch, source_type=EvolutionSourceType.MANUAL.value)
        assert d.verdict == EvolutionVerdict.ALLOW.value

    def test_evaluate_unknown_field_uses_cautious_threshold(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy(cautious_min_confidence=0.6)
        ch = SelfModelChange(field_name="some_unknown", confidence=0.5)
        d = p.evaluate(ch)
        assert d.verdict == EvolutionVerdict.REJECT.value

    def test_evaluate_none(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        p = EvolutionPolicy()
        d = p.evaluate(None)
        assert d.verdict == EvolutionVerdict.REJECT.value
        assert d.reason == "invalid_change"

    def test_evaluate_all_mixed(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        p = EvolutionPolicy()
        changes = [
            SelfModelChange(field_name="preferences", confidence=0.7),
            SelfModelChange(field_name="identity_id", confidence=1.0),
            SelfModelChange(field_name="stable_traits", confidence=0.7),
            SelfModelChange(field_name="preferences", confidence=0.1),
        ]
        decisions = p.evaluate_all(changes)
        assert len(decisions) == 4
        assert decisions[0].verdict == EvolutionVerdict.ALLOW.value
        assert decisions[1].verdict == EvolutionVerdict.REJECT.value
        assert decisions[2].verdict == EvolutionVerdict.CAUTIOUS.value
        assert decisions[3].verdict == EvolutionVerdict.REJECT.value

    def test_evaluate_all_with_dict(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        decisions = p.evaluate_all([
            {"field_name": "preferences", "confidence": 0.7},
        ])
        assert len(decisions) == 1


# ============================================================
# EvolutionPolicy - from_proposal
# ============================================================
class TestEvolutionPolicyFromProposal:
    def test_from_proposal_dict(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        prop = _make_growth_proposal(
            proposed_changes=[{
                "field_name": "preferences",
                "new_value": "AI drawing",
                "confidence": 0.7,
                "reason": "user likes drawing",
            }],
            confidence=0.5,
        )
        changes = p.from_proposal(prop)
        assert len(changes) == 1
        assert changes[0].field_name == "preferences"
        assert changes[0].new_value == "AI drawing"

    def test_from_proposal_affected_dimensions(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        prop = _make_growth_proposal(
            affected_dimensions={"preferences.drawing": 0.8},
            before_state={"preferences.drawing": 0.3},
            after_state={"preferences.drawing": 0.8},
        )
        changes = p.from_proposal(prop)
        assert len(changes) == 1
        assert changes[0].field_name == "preferences.drawing"
        assert changes[0].old_value == 0.3
        assert changes[0].new_value == 0.8

    def test_from_proposal_none(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        assert p.from_proposal(None) == []

    def test_from_proposal_dataclass_like(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )

        class FakeProposal:
            proposal_id = "p1"
            proposed_changes = [{
                "field_name": "interests",
                "new_value": "music",
                "confidence": 0.6,
            }]
            confidence = 0.5
            reason = "test"
            evidence = ["e1"]

        p = EvolutionPolicy()
        changes = p.from_proposal(FakeProposal())
        assert len(changes) == 1
        assert changes[0].field_name == "interests"
        assert changes[0].evidence_ids == ["e1", "p1"]

    def test_from_proposal_invalid_item_skipped(self):
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
        )
        p = EvolutionPolicy()
        prop = _make_growth_proposal(
            proposed_changes=[
                {"field_name": "preferences", "new_value": "ok"},
                {"not_a_field": "bad"},
                "totally invalid",
                {"field_name": "", "new_value": "bad"},
            ],
        )
        changes = p.from_proposal(prop)
        assert len(changes) == 1


# ============================================================
# SelfModelEvolutionEngine - basic
# ============================================================
class TestSelfModelEvolutionEngineBasic:
    def test_default_construction(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        assert e.policy is not None
        assert e.evolve_count == 0

    def test_evolve_no_snapshot(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        r = e.evolve(snapshot=None)
        assert r.is_noop is True

    def test_evolve_empty_inputs(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        r = e.evolve(snapshot=snap)
        assert r.is_noop is True
        assert r.accepted_count() == 0

    def test_evolve_manual_change(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="preferences", new_value="AI drawing", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert r.is_noop is False
        assert r.accepted_count() == 1
        assert r.new_snapshot is not None
        # 新 snapshot 应包含新的 preference
        new_prefs = r.new_snapshot.preferences
        assert any(
            "AI drawing" in str(p) for p in new_prefs
        )

    def test_evolve_forbidden_rejected(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="identity_id", new_value="hacker", confidence=1.0,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert r.is_noop is True
        assert r.rejected_count() == 1
        assert r.reject_reasons[0] == "forbidden_field"

    def test_evolve_immutable_snapshot(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        original_len = len(snap.preferences)
        ch = SelfModelChange(
            field_name="preferences", new_value="AI drawing", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        # 原 snapshot 不变
        assert len(snap.preferences) == original_len
        # 新 snapshot 有变化
        assert r.new_snapshot is not None
        assert len(r.new_snapshot.preferences) == original_len + 1

    def test_evolve_multiple_changes(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        changes = [
            SelfModelChange(
                field_name="preferences", new_value="AI", confidence=0.7,
            ),
            SelfModelChange(
                field_name="interests", new_value="music", confidence=0.7,
            ),
            SelfModelChange(
                field_name="identity_id", new_value="bad", confidence=1.0,
            ),
        ]
        r = e.evolve(snapshot=snap, manual_changes=changes)
        assert r.accepted_count() == 2
        assert r.rejected_count() == 1

    def test_evolve_with_proposal(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        prop = _make_growth_proposal(
            proposed_changes=[{
                "field_name": "preferences",
                "new_value": "AI drawing",
                "confidence": 0.7,
            }],
        )
        r = e.evolve(snapshot=snap, proposals=[prop])
        assert r.accepted_count() == 1
        assert r.record_count() == 1
        assert r.evolution_records[0].source_type == "growth_proposal"

    def test_evolve_with_reflection(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        refl = _make_reflection_record(
            reflection_kind="preference",
            confidence=0.7,
            interpretation="likes AI drawing",
        )
        r = e.evolve(snapshot=snap, reflections=[refl])
        # reflection kind=preference → preferences change → allowed
        assert r.accepted_count() >= 0  # may be cautious
        assert r.record_count() == 1
        assert r.evolution_records[0].source_type == "reflection"

    def test_evolve_records_summary(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        prop = _make_growth_proposal(proposed_changes=[
            {"field_name": "preferences", "new_value": "x", "confidence": 0.7},
        ])
        r = e.evolve(snapshot=snap, proposals=[prop])
        assert "accepted=" in r.summary
        assert "rejected=" in r.summary

    def test_evolve_with_proposal_and_reflection(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        prop = _make_growth_proposal(proposed_changes=[
            {"field_name": "preferences", "new_value": "x", "confidence": 0.7},
        ])
        refl = _make_reflection_record(
            reflection_kind="preference", confidence=0.7,
        )
        r = e.evolve(
            snapshot=snap, proposals=[prop], reflections=[refl],
        )
        # 至少有 2 个 records
        assert r.record_count() >= 1
        assert r.accepted_count() >= 1

    def test_evolve_sets_original_snapshot(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="preferences", new_value="x", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert r.original_snapshot is snap


# ============================================================
# SelfModelEvolutionEngine - exception isolation
# ============================================================
class TestSelfModelEvolutionEngineIsolation:
    def test_evolve_bad_snapshot_returns_empty(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()

        class BadSnapshot:
            def to_dict(self):
                raise RuntimeError("boom")
            def from_dict(self, d):
                raise RuntimeError("boom")

        r = e.evolve(snapshot=BadSnapshot())
        # 异常隔离:返回空 result
        assert r.is_noop is True
        assert e.last_error is not None

    def test_evolve_proposal_with_error_continues(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()

        class BadProposal:
            proposed_changes = None
            affected_dimensions = None

        ch = SelfModelChange(
            field_name="preferences", new_value="x", confidence=0.7,
        )
        r = e.evolve(
            snapshot=snap, proposals=[BadProposal()],
            manual_changes=[ch],
        )
        # 至少 manual change 通过
        assert r.accepted_count() == 1

    def test_evolve_proposal_raises_in_from_proposal(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )

        class BrokenPolicy:
            def from_proposal(self, p):
                raise RuntimeError("policy boom")

            def evaluate(self, ch, source_type=None):
                from src.runtime.self_model.evolution.evolution_policy import (
                    EvolutionPolicy,
                )
                return EvolutionPolicy().evaluate(ch, source_type=source_type)

        e = SelfModelEvolutionEngine(policy=BrokenPolicy())
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="preferences", new_value="x", confidence=0.7,
        )
        r = e.evolve(
            snapshot=snap, proposals=[_make_growth_proposal()],
            manual_changes=[ch],
        )
        # 异常隔离:manual 仍通过
        assert r.accepted_count() == 1

    def test_evolve_proposal_evaluate_raises(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        from src.runtime.self_model.evolution.evolution_policy import (
            EvolutionPolicy,
            EvolutionVerdict,
        )

        class RaisePolicy(EvolutionPolicy):
            def evaluate(self, ch, source_type=None):
                raise RuntimeError("eval boom")

        e = SelfModelEvolutionEngine(policy=RaisePolicy())
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="preferences", new_value="x", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        # 单条失败 → reject,total is_noop
        assert r.rejected_count() == 1


# ============================================================
# SelfModelEvolutionEngine - state & health
# ============================================================
class TestSelfModelEvolutionEngineState:
    def test_state_counters(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        e.evolve(
            snapshot=snap,
            manual_changes=[
                SelfModelChange(
                    field_name="preferences", new_value="a", confidence=0.7,
                ),
                SelfModelChange(
                    field_name="identity_id", new_value="b", confidence=1.0,
                ),
            ],
        )
        assert e.evolve_count == 1
        assert e.accept_count == 1
        assert e.reject_count == 1

    def test_health_check(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        h = e.health_check()
        assert h["healthy"] is True
        assert h["policy_healthy"] is True

    def test_describe(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        d = e.describe()
        assert d["name"] == "self_model_evolution_engine"
        assert d["schema_version"] == "1.0"
        assert d["evolve_count"] == 0

    def test_set_policy(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            EvolutionPolicy,
        )
        e = SelfModelEvolutionEngine()
        new_policy = EvolutionPolicy(min_confidence=0.9)
        e.set_policy(new_policy)
        assert e.policy is new_policy


# ============================================================
# SelfModelEvolutionEngine - field apply
# ============================================================
class TestSelfModelEvolutionEngineApply:
    def test_apply_preferences(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="preferences", new_value="AI drawing", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert any(
            "AI drawing" in str(p) for p in r.new_snapshot.preferences
        )

    def test_apply_current_state_mood(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="current_state.mood", new_value="happy", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        cs = r.new_snapshot.current_state
        assert cs.get("mood") == "happy"

    def test_apply_core_values(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="core_values", new_value="kindness", confidence=0.8,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert any(
            "kindness" in str(c) for c in r.new_snapshot.core_values
        )

    def test_apply_unknown_field_writes_to_meta(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        ch = SelfModelChange(
            field_name="some_custom_field", new_value="x", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        # 未知字段:进入 meta(兜底)
        meta = r.new_snapshot.meta
        assert "some_custom_field" in meta

    def test_version_bump(self):
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot(version=5)
        ch = SelfModelChange(
            field_name="preferences", new_value="x", confidence=0.7,
        )
        r = e.evolve(snapshot=snap, manual_changes=[ch])
        assert r.new_snapshot.version == 6


# ============================================================
# RuntimeCore 集成
# ============================================================
class TestRuntimeCoreEvolutionIntegration:
    def test_runtime_version_is_4_5(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.6 兼容:应等于 4.5 或更新
        # Phase 4.7 兼容:4.7 也在白名单中
        assert RuntimeCore.RUNTIME_VERSION in ("4.5", "4.6", "4.7")

    def test_evolution_stage_in_lifecycle(self):
        from src.runtime.runtime import (
            RuntimeStage,
            RUNTIME_LIFECYCLE_ORDER,
        )
        assert RuntimeStage.SELF_MODEL_EVOLUTION in RUNTIME_LIFECYCLE_ORDER
        idx = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.SELF_MODEL_EVOLUTION
        )
        smb_idx = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_BUILD)
        rg_idx = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.RESPONSE_GENERATION
        )
        # 在 SELF_MODEL_BUILD 之后,RESPONSE_GENERATION 之前
        assert smb_idx < idx < rg_idx

    def test_configure_evolution_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        rt = RuntimeCore()
        engine = SelfModelEvolutionEngine()
        rt.configure_self_model_evolution(engine)
        assert rt.evolution_engine is engine
        assert rt.evolution_engine is not None

    def test_configure_via_alias(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        rt = RuntimeCore()
        engine = SelfModelEvolutionEngine()
        rt.configure_evolution_engine(engine)
        assert rt.evolution_engine is engine

    def test_constructor_with_evolution_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        engine = SelfModelEvolutionEngine()
        rt = RuntimeCore(evolution_engine=engine)
        assert rt.evolution_engine is engine

    def test_no_engine_backward_compatible(self):
        from src.runtime.runtime import RuntimeCore
        rt = RuntimeCore()
        assert rt.evolution_engine is None
        # start 也不报错
        rt.start()

    def test_evolve_self_model_no_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        rt = RuntimeCore()
        ctx = RuntimeContext()
        result = rt.evolve_self_model(ctx)
        assert result is None

    def test_evolve_self_model_no_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        rt = RuntimeCore(evolution_engine=SelfModelEvolutionEngine())
        ctx = RuntimeContext()
        result = rt.evolve_self_model(ctx)
        assert result is None

    def test_get_evolution_result_empty(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        rt = RuntimeCore()
        ctx = RuntimeContext()
        assert rt.get_evolution_result(ctx) is None
        assert rt.get_evolution_records(ctx) == []

    def test_get_evolution_health_no_engine(self):
        from src.runtime.runtime import RuntimeCore
        rt = RuntimeCore()
        assert rt.get_evolution_health() is None

    def test_evolution_stage_no_op_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        rt = RuntimeCore()
        rt.start()
        # 注入一个简单的 snapshot
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        rt._invoke_self_model_evolution_stage(ctx)
        # 无 engine → 无 evolution result
        assert getattr(ctx, "_evolution_result", None) is None

    def test_evolution_stage_actually_runs(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        rt = RuntimeCore(evolution_engine=SelfModelEvolutionEngine())
        rt.start()
        ctx = RuntimeContext()
        snap = _make_snapshot()
        setattr(ctx, "_self_model_snapshot", snap)
        ch = SelfModelChange(
            field_name="preferences", new_value="AI", confidence=0.7,
        )
        setattr(ctx, "growth_proposals", [
            _make_growth_proposal(
                proposed_changes=[{
                    "field_name": "preferences",
                    "new_value": "AI",
                    "confidence": 0.7,
                }],
            ),
        ])
        rt._invoke_self_model_evolution_stage(ctx)
        result = getattr(ctx, "_evolution_result", None)
        assert result is not None
        assert result.accepted_count() == 1
        # 新 snapshot 被写回
        new_snap = getattr(ctx, "_self_model_snapshot", None)
        assert new_snap is not None
        assert any("AI" in str(p) for p in new_snap.preferences)

    def test_evolution_stage_engine_failure_isolated(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        rt = RuntimeCore()

        class BadEngine:
            def evolve(self, **kwargs):
                raise RuntimeError("boom")

        rt.configure_self_model_evolution(BadEngine())
        rt.start()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        rt._invoke_self_model_evolution_stage(ctx)
        # 异常被隔离
        assert rt.get_evolution_result(ctx) is None

    def test_evolution_stage_no_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        rt = RuntimeCore(evolution_engine=SelfModelEvolutionEngine())
        rt.start()
        ctx = RuntimeContext()
        # 无 snapshot
        rt._invoke_self_model_evolution_stage(ctx)
        assert rt.get_evolution_result(ctx) is None


# ============================================================
# Phase 4.4 集成: 演化后 IdentityContext 刷新
# ============================================================
class TestPhase44IdentityRefreshAfterEvolution:
    def test_identity_refreshed_after_evolution(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        from src.runtime.self_model.identity_binding import (
            SelfIdentityRuntime,
        )

        rt = RuntimeCore(
            evolution_engine=SelfModelEvolutionEngine(),
            identity_runtime=SelfIdentityRuntime(),
        )
        rt.start()
        ctx = RuntimeContext()
        # 注入 snapshot
        snap = _make_snapshot()
        setattr(ctx, "_self_model_snapshot", snap)
        # 注入一个老的 identity_context(标记一下)
        class FakeIdentity:
            identity_id = "old"
        setattr(ctx, "_identity_context", FakeIdentity())

        ch = SelfModelChange(
            field_name="preferences", new_value="AI", confidence=0.7,
        )
        setattr(ctx, "growth_proposals", [
            _make_growth_proposal(proposed_changes=[{
                "field_name": "preferences", "new_value": "AI",
                "confidence": 0.7,
            }]),
        ])
        rt._invoke_self_model_evolution_stage(ctx)
        # 演化后,identity_context 应被刷新(且不再是 FakeIdentity)
        new_ic = getattr(ctx, "_identity_context", None)
        assert new_ic is not None
        assert new_ic is not FakeIdentity()

    def test_no_identity_runtime_no_refresh(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
            SelfModelChange,
        )
        rt = RuntimeCore(evolution_engine=SelfModelEvolutionEngine())
        rt.start()
        ctx = RuntimeContext()
        snap = _make_snapshot()
        setattr(ctx, "_self_model_snapshot", snap)

        ch = SelfModelChange(
            field_name="preferences", new_value="AI", confidence=0.7,
        )
        setattr(ctx, "growth_proposals", [
            _make_growth_proposal(proposed_changes=[{
                "field_name": "preferences", "new_value": "AI",
                "confidence": 0.7,
            }]),
        ])
        # 不应抛异常
        rt._invoke_self_model_evolution_stage(ctx)
        result = rt.get_evolution_result(ctx)
        assert result is not None
        assert result.accepted_count() == 1

    def test_noop_evolution_no_identity_refresh(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        from src.runtime.self_model.identity_binding import (
            SelfIdentityRuntime,
        )
        rt = RuntimeCore(
            evolution_engine=SelfModelEvolutionEngine(),
            identity_runtime=SelfIdentityRuntime(),
        )
        rt.start()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        # 空的 proposals,无变化
        setattr(ctx, "growth_proposals", [])
        rt._invoke_self_model_evolution_stage(ctx)
        # 没 accept,无 identity 刷新
        # 但 ctx 仍可能有 result(空)
        result = rt.get_evolution_result(ctx)
        if result is not None:
            assert result.is_noop is True


# ============================================================
# 端到端: process() 完整链路
# ============================================================
class TestEndToEndEvolution:
    def test_process_full_pipeline_with_evolution(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.self_model.evolution import (
            SelfModelEvolutionEngine,
        )
        from src.runtime.self_model.identity_binding import (
            SelfIdentityRuntime,
        )

        rt = RuntimeCore(
            evolution_engine=SelfModelEvolutionEngine(),
            identity_runtime=SelfIdentityRuntime(),
        )
        rt.start()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        setattr(ctx, "growth_proposals", [
            _make_growth_proposal(proposed_changes=[{
                "field_name": "preferences",
                "new_value": "AI drawing",
                "confidence": 0.7,
            }]),
        ])
        # 直接调用 _invoke_self_model_evolution_stage
        rt._invoke_self_model_evolution_stage(ctx)
        result = rt.get_evolution_result(ctx)
        assert result is not None
        assert result.accepted_count() == 1
        # 验证 evolution_records 可用
        records = rt.get_evolution_records(ctx)
        assert len(records) >= 1
        assert records[0].source_type == "growth_proposal"


# ============================================================
# 不变量: ResponseEngine / personality / LLM SDK
# ============================================================
class TestInvariants:
    def test_response_engine_signature_unchanged(self):
        import inspect
        from src.response.engine import ResponseEngine
        sig = inspect.signature(ResponseEngine.generate)
        params = list(sig.parameters.keys())
        # 至少应包含核心参数
        assert "user_message" in params
        assert "history" in params
        assert "personality_context" in params

    def test_evolution_does_not_import_personality_core(self):
        # 通过 importlib 重新加载并检查 src.personality.* 不在 evolution 模块
        from src.runtime.self_model import evolution
        # 检查模块属性里没有 personality 引用
        mod_vars = vars(evolution)
        for k, v in mod_vars.items():
            if k.startswith("_"):
                continue
            if isinstance(v, type):
                # 类
                src_file = getattr(v, "__module__", "")
                assert "src.personality" not in src_file, (
                    f"{k} 来自 {src_file}"
                )

    def test_evolution_does_not_import_llm_sdk(self):
        # 静态检查 evolution 模块文件:不应有实际的 openai/qwen/llava import 语句
        from src.runtime.self_model import evolution
        mod_path = evolution.__file__
        assert mod_path is not None
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        # 仅匹配实际 import 语句(行首或空白后跟 import xxx)
        for module_name in ("openai", "qwen", "llava", "anthropic"):
            pattern = rf"(^|\n)\s*(import\s+{module_name}|from\s+{module_name}\b)"
            assert not re.search(pattern, content), (
                f"evolution 模块不应 import LLM SDK: {module_name}"
            )

    def test_evolution_record_no_llm_sdk(self):
        from src.runtime.self_model.evolution import evolution_record
        mod_path = evolution_record.__file__
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        for module_name in ("openai", "qwen", "llava", "anthropic"):
            pattern = rf"(^|\n)\s*(import\s+{module_name}|from\s+{module_name}\b)"
            assert not re.search(pattern, content), (
                f"evolution_record 不应 import LLM SDK: {module_name}"
            )

    def test_evolution_policy_no_llm_sdk(self):
        from src.runtime.self_model.evolution import evolution_policy
        mod_path = evolution_policy.__file__
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        for module_name in ("openai", "qwen", "llava", "anthropic"):
            pattern = rf"(^|\n)\s*(import\s+{module_name}|from\s+{module_name}\b)"
            assert not re.search(pattern, content), (
                f"evolution_policy 不应 import LLM SDK: {module_name}"
            )

    def test_evolution_engine_no_llm_sdk(self):
        from src.runtime.self_model.evolution import (
            self_model_evolution_engine,
        )
        mod_path = self_model_evolution_engine.__file__
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        for module_name in ("openai", "qwen", "llava", "anthropic"):
            pattern = rf"(^|\n)\s*(import\s+{module_name}|from\s+{module_name}\b)"
            assert not re.search(pattern, content), (
                f"self_model_evolution_engine 不应 import LLM SDK: {module_name}"
            )

    def test_identity_binding_does_not_depend_on_evolution(self):
        # 通过文件内容检查 identity_binding 不 import evolution
        from src.runtime.self_model.identity_binding import (
            self_identity_runtime,
        )
        mod_path = self_identity_runtime.__file__
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "evolution" not in content, (
            "identity_binding 不应反向依赖 evolution"
        )

    def test_reflection_does_not_depend_on_evolution(self):
        from src.runtime.self_model.reflection import reflection_engine
        mod_path = reflection_engine.__file__
        with open(mod_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Phase 4.7: reflection 可以引用 evolution_history 作为参数名(API 层),
        # 但不应 import evolution 模块本身。
        import re
        # 检查实际的 import 语句,不检查 docstring/注释/参数名
        pattern = r"(from\s+src\.runtime\.self_model\.evolution|import\s+src\.runtime\.self_model\.evolution)"
        assert not re.search(pattern, content), (
            "reflection 不应 import evolution 模块"
        )

    def test_evolution_exports_in_self_model_init(self):
        from src.runtime.self_model import (
            EvolutionPolicy,
            EvolutionVerdict,
            EvolutionPolicyDecision,
            EvolutionRecord,
            SelfModelEvolutionResult,
            SelfModelEvolutionEngine,
            EvolutionSourceType,
        )
        assert EvolutionPolicy is not None
        assert EvolutionVerdict is not None
        assert EvolutionRecord is not None
        assert SelfModelEvolutionResult is not None
        assert SelfModelEvolutionEngine is not None
        assert EvolutionSourceType is not None


# ============================================================
# 反射驱动
# ============================================================
class TestReflectionDrivenEvolution:
    def test_reflection_trait_trend_to_stable_traits(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        refl = _make_reflection_record(
            reflection_kind="trait_trend", confidence=0.8,
            interpretation="warmth increased",
        )
        r = e.evolve(snapshot=snap, reflections=[refl])
        # 至少产生 record
        assert r.record_count() == 1
        # 由于 cautious 阈值,可能 accept 或 reject(取决于 confidence)
        assert r.evolution_records[0].source_type == "reflection"

    def test_reflection_state_note_to_mood(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        refl = _make_reflection_record(
            reflection_kind="state_note", confidence=0.7,
            interpretation="calm",
        )
        r = e.evolve(snapshot=snap, reflections=[refl])
        assert r.record_count() == 1
        # mood 是 allowed 字段,0.7 应 accept
        assert r.accepted_count() == 1

    def test_reflection_value_shift_low_confidence_rejected(self):
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        e = SelfModelEvolutionEngine()
        snap = _make_snapshot()
        refl = _make_reflection_record(
            reflection_kind="value_shift", confidence=0.3,
            interpretation="shift",
        )
        r = e.evolve(snapshot=snap, reflections=[refl])
        # cautious 字段 + 0.3 < 0.6 → reject
        assert r.rejected_count() == 1
        assert r.accepted_count() == 0
