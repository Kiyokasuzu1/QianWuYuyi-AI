# -*- coding: utf-8 -*-
"""
tests/test_phase_4_7_self_model_reflection.py

Phase 4.7: Self Model Reflection & Consistency Validation Layer —— 单元测试

目标覆盖:
- ReflectionRecord 扩展字段(clamp / serialize / round-trip / Phase 4.7 helpers)
- ReflectionType / ConflictType 枚举
- ContradictionRecord: create / serialize / clamp
- ContradictionDetector: identity / value / trait / preference / behavior 冲突检测
- ConsistencyReport: score / is_consistent / issues / warnings
- ConsistencyChecker: identity / schema / drift / history
- SelfModelReflectionEngine: 协调两个 detector / 异常隔离 / 失败回退
- RuntimeCore 集成: 阶段调用 / no-op / 异常隔离
- Architecture invariants: 不 import personality / 不 import LLM SDK / 不 import 下层持久化模块 /
  不 import identity_binding / ResponseEngine 签名未变 / RuntimeContext schema 未变

总计 80+ 测试。
"""
import re
import sys
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures / 工具
# ============================================================
@pytest.fixture
def tmp_dir(tmp_path):
    """临时目录 fixture。"""
    return str(tmp_path)


def _make_snapshot(
    identity: Optional[Dict[str, Any]] = None,
    core_values: Optional[List[Dict]] = None,
    stable_traits: Optional[List[Dict]] = None,
    preferences: Optional[List[Dict]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    identity_id: str = "smf_test_4_7",
    version: int = 1,
    schema_version: str = "1.0",
) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelSnapshot
    snap = SelfModelSnapshot(
        identity=identity if identity is not None else {"name": "yuyi", "core_identity": "gentle_companion"},
        core_values=core_values if core_values is not None else [
            {"name": "kindness", "weight": 0.9},
            {"name": "honesty", "weight": 0.85},
        ],
        stable_traits=stable_traits if stable_traits is not None else [
            {"name": "warmth", "value": 0.8},
            {"name": "patience", "value": 0.75},
        ],
        preferences=preferences if preferences is not None else [
            {"name": "communication_style", "value": "gentle"},
        ],
        current_state=current_state or {},
        identity_id=identity_id,
        version=version,
    )
    snap.schema_version = schema_version
    return snap


# ============================================================
# TestReflectionRecordPhase47
# ============================================================
class TestReflectionRecordPhase47:
    """ReflectionRecord Phase 4.7 扩展字段测试。"""

    def test_default_creation(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord()
        assert rec.reflection_type == "unknown"
        assert rec.affected_fields == []
        assert rec.conflicts == []
        assert rec.severity == 0.0
        assert rec.evidence_ids == []
        assert rec.confidence == 0.5  # Phase 4.3 default
        assert rec.schema_version == "1.0"  # Phase 4.7 保持 v1.0 向后兼容

    def test_clamp_severity_high(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(severity=5.0)
        assert rec.severity == 1.0

    def test_clamp_severity_low(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(severity=-1.0)
        assert rec.severity == 0.0

    def test_clamp_confidence_high(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(confidence=2.0)
        assert rec.confidence == 1.0

    def test_clamp_confidence_low(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(confidence=-0.5)
        assert rec.confidence == 0.0

    def test_clamp_severity_invalid(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(severity="not-a-number")
        assert rec.severity == 0.0

    def test_serialize_contains_phase_4_7_fields(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(
            reflection_type="contradiction",
            affected_fields=["preferences.communication_style"],
            conflicts=[{"field_name": "x", "severity": 0.5}],
            severity=0.7,
            evidence_ids=["ev1", "ev2"],
        )
        d = rec.to_dict()
        assert d["reflection_type"] == "contradiction"
        assert d["affected_fields"] == ["preferences.communication_style"]
        assert d["conflicts"] == [{"field_name": "x", "severity": 0.5}]
        assert d["severity"] == 0.7
        assert d["evidence_ids"] == ["ev1", "ev2"]

    def test_round_trip(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(
            identity_id="smf_x",
            reflection_type="drift",
            affected_fields=["stable_traits.warmth"],
            conflicts=[{"field": "warmth", "delta": 0.3}],
            severity=0.6,
            evidence_ids=["audit:123", "snapshot:abc"],
            confidence=0.85,
        )
        d = rec.to_dict()
        rec2 = ReflectionRecord.from_dict(d)
        assert rec2.reflection_type == rec.reflection_type
        assert rec2.affected_fields == rec.affected_fields
        assert rec2.conflicts == rec.conflicts
        assert rec2.severity == rec.severity
        assert rec2.evidence_ids == rec.evidence_ids
        assert rec2.confidence == rec.confidence
        assert rec2.identity_id == rec.identity_id

    def test_invalid_reflection_type_falls_back(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(reflection_type="invalid_type")
        assert rec.reflection_type == "unknown"

    def test_phase_4_7_helpers(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(
            reflection_type="contradiction",
            conflicts=[{"x": 1}],
            severity=0.8,
            evidence_ids=["e1"],
        )
        assert rec.is_contradiction() is True
        assert rec.has_conflicts() is True
        assert rec.is_severe(0.7) is True
        assert rec.has_evidence() is True
        assert rec.conflict_count() == 1

    def test_is_consistent_helper(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(reflection_type="consistent")
        assert rec.is_consistent() is True
        rec2 = ReflectionRecord(reflection_type="contradiction")
        assert rec2.is_consistent() is False

    def test_is_validation_helper(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(reflection_type="validation")
        assert rec.is_validation() is True
        rec2 = ReflectionRecord(reflection_type="drift")
        assert rec2.is_drift() is True

    def test_from_dict_invalid_type(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        with pytest.raises(TypeError):
            ReflectionRecord.from_dict("not-a-dict")  # type: ignore

    def test_affected_fields_normalization(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        rec = ReflectionRecord(affected_fields=["a", 1, None, "b"])
        assert rec.affected_fields == ["a", "1", "b"]


# ============================================================
# TestReflectionEnums
# ============================================================
class TestReflectionEnums:
    def test_reflection_type_values(self):
        from src.runtime.self_model.reflection import ReflectionType
        assert ReflectionType.VALIDATION.value == "validation"
        assert ReflectionType.CONTRADICTION.value == "contradiction"
        assert ReflectionType.DRIFT.value == "drift"
        assert ReflectionType.CONSISTENT.value == "consistent"
        assert ReflectionType.UNKNOWN.value == "unknown"

    def test_conflict_type_values(self):
        from src.runtime.self_model.reflection import ConflictType
        assert ConflictType.IDENTITY_CONFLICT.value == "identity_conflict"
        assert ConflictType.VALUE_CONFLICT.value == "value_conflict"
        assert ConflictType.TRAIT_CONFLICT.value == "trait_conflict"
        assert ConflictType.PREFERENCE_CONFLICT.value == "preference_conflict"
        assert ConflictType.BEHAVIOR_CONFLICT.value == "behavior_conflict"
        assert ConflictType.NONE.value == "none"


# ============================================================
# TestContradictionRecord
# ============================================================
class TestContradictionRecord:
    def test_default_creation(self):
        from src.runtime.self_model.reflection import ContradictionRecord
        rec = ContradictionRecord()
        assert rec.field_name == ""
        assert rec.old_value is None
        assert rec.new_value is None
        assert rec.conflict_type == "none"
        assert rec.severity == 0.0
        assert rec.confidence == 0.5

    def test_clamp_severity(self):
        from src.runtime.self_model.reflection import ContradictionRecord
        rec = ContradictionRecord(severity=10.0)
        assert rec.severity == 1.0
        rec2 = ContradictionRecord(severity=-2.0)
        assert rec2.severity == 0.0

    def test_invalid_conflict_type(self):
        from src.runtime.self_model.reflection import ContradictionRecord
        rec = ContradictionRecord(conflict_type="invalid_type")
        assert rec.conflict_type == "none"

    def test_serialize_round_trip(self):
        from src.runtime.self_model.reflection import ContradictionRecord
        rec = ContradictionRecord(
            field_name="core_values.kindness",
            old_value={"name": "kindness", "weight": 0.9},
            new_value=None,
            conflict_type="value_conflict",
            severity=0.7,
            description="Core value removed",
            confidence=0.8,
        )
        d = rec.to_dict()
        rec2 = ContradictionRecord.from_dict(d)
        assert rec2.field_name == rec.field_name
        assert rec2.old_value == rec.old_value
        assert rec2.conflict_type == rec.conflict_type
        assert rec2.severity == rec.severity
        assert rec2.description == rec.description

    def test_is_real_conflict(self):
        from src.runtime.self_model.reflection import ContradictionRecord
        rec_no = ContradictionRecord(conflict_type="none", severity=0.5)
        assert rec_no.is_real_conflict() is False
        rec_yes = ContradictionRecord(conflict_type="trait_conflict", severity=0.5)
        assert rec_yes.is_real_conflict() is True
        rec_zero = ContradictionRecord(conflict_type="trait_conflict", severity=0.0)
        assert rec_zero.is_real_conflict() is False


# ============================================================
# TestContradictionDetector
# ============================================================
class TestContradictionDetector:
    def test_detect_no_snapshots(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        result = d.detect(None, None)
        assert result == []
        assert d.last_error == "missing_snapshot"

    def test_detect_one_snapshot_none(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        snap = _make_snapshot()
        result = d.detect(snap, None)
        assert result == []

    def test_detect_same_snapshot(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        snap = _make_snapshot()
        result = d.detect(snap, snap)
        assert result == []

    def test_detect_identity_id_change(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot(identity_id="smf_old")
        new = _make_snapshot(identity_id="smf_new")
        result = d.detect(old, new)
        ids = [c.field_name for c in result if c.conflict_type == "identity_conflict"]
        assert "identity.identity_id" in ids

    def test_detect_value_removal(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot(core_values=[{"name": "kindness", "weight": 0.9}])
        new = _make_snapshot(core_values=[])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "value_conflict" in types

    def test_detect_trait_value_change(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector(trait_threshold=0.5)
        old = _make_snapshot(stable_traits=[{"name": "warmth", "value": 0.8}])
        new = _make_snapshot(stable_traits=[{"name": "warmth", "value": 0.2}])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "trait_conflict" in types

    def test_detect_trait_opposite(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot(stable_traits=[{"name": "style", "value": "gentle"}])
        new = _make_snapshot(stable_traits=[{"name": "style", "value": "aggressive"}])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "trait_conflict" in types

    def test_detect_preference_opposite(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot(preferences=[{"name": "mood", "value": "calm"}])
        new = _make_snapshot(preferences=[{"name": "mood", "value": "agitated"}])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "preference_conflict" in types

    def test_detect_preference_removal(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot(preferences=[{"name": "mood", "value": "calm"}])
        new = _make_snapshot(preferences=[])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "preference_conflict" in types

    def test_detect_behavior_conflict(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector(behavior_threshold=0.5)
        old = _make_snapshot(current_state={"behavior_tendencies": {"risk_taking": 0.2}})
        new = _make_snapshot(current_state={"behavior_tendencies": {"risk_taking": 0.9}})
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "behavior_conflict" in types

    def test_detect_value_weight_change(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector(value_threshold=0.5)
        old = _make_snapshot(core_values=[{"name": "kindness", "weight": 0.9}])
        new = _make_snapshot(core_values=[{"name": "kindness", "weight": 0.1}])
        result = d.detect(old, new)
        types = [c.conflict_type for c in result]
        assert "value_conflict" in types

    def test_detect_increments_count(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = _make_snapshot()
        new = _make_snapshot()
        d.detect(old, new)
        d.detect(old, new)
        assert d.detect_count == 2

    def test_health_check(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        h = d.health_check()
        assert h["healthy"] is True
        assert "schema_version" in h

    def test_describe(self):
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        desc = d.describe()
        assert "schema_version" in desc
        assert "detect_count" in desc

    def test_detect_handles_dict_snapshots(self):
        """检测器应该能处理 dict 类型的 snapshot。"""
        from src.runtime.self_model.reflection import ContradictionDetector
        d = ContradictionDetector()
        old = {"identity_id": "x", "identity": {}, "core_values": [], "stable_traits": [], "preferences": [], "current_state": {}}
        new = {"identity_id": "x", "identity": {}, "core_values": [], "stable_traits": [], "preferences": [], "current_state": {}}
        result = d.detect(old, new)
        assert result == []


# ============================================================
# TestConsistencyReport
# ============================================================
class TestConsistencyReport:
    def test_default_creation(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport()
        assert r.score == 1.0
        assert r.is_consistent is True
        assert r.threshold == 0.7
        assert r.issues == []
        assert r.warnings == []
        assert r.identity_ok is True
        assert r.schema_ok is True
        assert r.drift_score == 0.0
        assert r.history_ok is True

    def test_clamp_score(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(score=2.0)
        assert r.score == 1.0
        r2 = ConsistencyReport(score=-0.5)
        assert r2.score == 0.0

    def test_is_consistent_recompute(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(score=0.5, threshold=0.7)
        assert r.is_consistent is False
        r2 = ConsistencyReport(score=0.8, threshold=0.7)
        assert r2.is_consistent is True

    def test_serialize_round_trip(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(
            score=0.6,
            threshold=0.7,
            issues=[{"code": "x", "severity": 0.3}],
            warnings=["w1"],
            identity_ok=False,
            drift_score=0.2,
        )
        d = r.to_dict()
        r2 = ConsistencyReport.from_dict(d)
        assert r2.score == r.score
        assert r2.issues == r.issues
        assert r2.warnings == r.warnings
        assert r2.identity_ok == r.identity_ok

    def test_issue_count(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(issues=[{"a": 1}, {"b": 2}])
        assert r.issue_count() == 2
        assert r.has_issues() is True

    def test_warning_count(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(warnings=["w1", "w2", "w3"])
        assert r.warning_count() == 3

    def test_invalid_issues_filtered(self):
        from src.runtime.self_model.reflection import ConsistencyReport
        r = ConsistencyReport(issues=[{"a": 1}, "not-a-dict", None, {"b": 2}])
        assert r.issue_count() == 2


# ============================================================
# TestConsistencyChecker
# ============================================================
class TestConsistencyChecker:
    def test_check_none_snapshot(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        r = c.check(None)
        assert r.score == 0.0
        assert r.is_consistent is False
        assert r.identity_ok is False

    def test_check_valid_snapshot(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot()
        r = c.check(snap)
        assert r.is_consistent is True
        assert r.identity_ok is True
        assert r.schema_ok is True
        assert r.issue_count() == 0

    def test_check_missing_identity_id(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot(identity={"name": "x"}, identity_id="")
        r = c.check(snap)
        assert r.identity_ok is False
        codes = [i.get("code") for i in r.issues]
        assert "missing_identity_id" in codes

    def test_check_invalid_schema_version(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot(schema_version="999.0")
        r = c.check(snap)
        assert r.schema_ok is False
        codes = [i.get("code") for i in r.issues]
        assert "invalid_schema_version" in codes

    def test_check_invalid_version(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot(version=0)
        r = c.check(snap)
        assert r.schema_ok is False

    def test_check_entries_not_list(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot()
        snap.entries = "not-a-list"  # type: ignore
        r = c.check(snap)
        assert r.schema_ok is False
        codes = [i.get("code") for i in r.issues]
        assert "entries_not_list" in codes

    def test_check_with_drift(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker(drift_count_threshold=3, trait_drift_threshold=0.3)
        snap = _make_snapshot()
        # 构造 5 个 change 记录,触发 drift
        history = []
        for i in range(5):
            history.append({
                "changes": [{
                    "field_name": "stable_traits.warmth",
                    "old_value": 0.5,
                    "new_value": 0.5 + 0.1 * (i + 1),
                }]
            })
        r = c.check(snap, history)
        assert r.drift_score > 0

    def test_check_with_empty_history(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot()
        r = c.check(snap, [])
        assert r.drift_score == 0.0

    def test_check_with_history_drift(self):
        """测试 value 字段的 drift 检测。"""
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker(value_drift_threshold=0.3)
        snap = _make_snapshot()
        history = [
            {
                "changes": [{
                    "field_name": "core_values.kindness",
                    "old_value": 0.5,
                    "new_value": 0.9,
                }]
            }
        ]
        r = c.check(snap, history)
        assert r.drift_score > 0

    def test_check_increments_count(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        snap = _make_snapshot()
        c.check(snap)
        c.check(snap)
        assert c.check_count == 2

    def test_health_check(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        h = c.health_check()
        assert h["healthy"] is True
        assert "schema_version" in h

    def test_describe(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        d = c.describe()
        assert "schema_version" in d
        assert "threshold" in d

    def test_threshold_default(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker()
        assert c._threshold == 0.7

    def test_threshold_custom(self):
        from src.runtime.self_model.reflection import ConsistencyChecker
        c = ConsistencyChecker(threshold=0.5)
        assert c._threshold == 0.5


# ============================================================
# TestSelfModelReflectionEngine
# ============================================================
class TestSelfModelReflectionEngine:
    def test_basic_reflect(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot()
        new = _make_snapshot()
        rec = engine.reflect(old, new)
        assert rec is not None
        assert rec.reflection_type in ("consistent", "validation", "drift", "contradiction")
        assert rec.confidence >= 0.0
        assert rec.severity >= 0.0

    def test_reflect_with_contradiction(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.9}],
            preferences=[{"name": "style", "value": "gentle"}],
        )
        new = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.1}],
            preferences=[{"name": "style", "value": "aggressive"}],
        )
        rec = engine.reflect(old, new)
        assert rec.is_contradiction() is True
        assert rec.has_conflicts() is True
        assert len(rec.affected_fields) > 0

    def test_reflect_with_no_contradiction(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot()
        new = _make_snapshot()
        rec = engine.reflect(old, new)
        # 没有任何变化时,应该是 consistent
        assert rec.is_consistent() is True
        assert rec.has_conflicts() is False

    def test_reflect_both_none(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        rec = engine.reflect(None, None)
        # 不会抛异常
        assert rec is not None

    def test_reflect_with_history(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot()
        new = _make_snapshot()
        history = [{"changes": []} for _ in range(5)]
        rec = engine.reflect(old, new, evolution_history=history)
        assert rec is not None

    def test_engine_count(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot()
        new = _make_snapshot()
        engine.reflect(old, new)
        engine.reflect(old, new)
        assert engine.reflect_count == 2

    def test_health_check(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        h = engine.health_check()
        assert h["healthy"] is True
        assert "detector_health" in h
        assert "checker_health" in h

    def test_describe(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        d = engine.describe()
        assert "schema_version" in d

    def test_detector_property(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine, ContradictionDetector
        engine = SelfModelReflectionEngine()
        assert isinstance(engine.detector, ContradictionDetector)

    def test_checker_property(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine, ConsistencyChecker
        engine = SelfModelReflectionEngine()
        assert isinstance(engine.checker, ConsistencyChecker)

    def test_inject_custom_detector(self):
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine, ContradictionDetector,
        )
        custom_detector = ContradictionDetector(trait_threshold=0.9)
        engine = SelfModelReflectionEngine(detector=custom_detector)
        assert engine.detector._trait_threshold == 0.9

    def test_inject_custom_checker(self):
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine, ConsistencyChecker,
        )
        custom_checker = ConsistencyChecker(threshold=0.5)
        engine = SelfModelReflectionEngine(checker=custom_checker)
        assert engine.checker._threshold == 0.5

    def test_observation_contains_drift_info(self):
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine, ConsistencyChecker,
        )
        # 强制让 drift_score > 0
        checker = ConsistencyChecker(
            drift_count_threshold=2,
            trait_drift_threshold=0.1,
        )
        engine = SelfModelReflectionEngine(checker=checker)
        snap = _make_snapshot()
        history = []
        for i in range(5):
            history.append({
                "changes": [{
                    "field_name": "stable_traits.warmth",
                    "old_value": 0.5,
                    "new_value": 0.5 + 0.1 * (i + 1),
                }]
            })
        rec = engine.reflect(snap, snap, evolution_history=history)
        assert rec.metadata.get("drift_score", 0) > 0

    def test_evidence_ids_populated(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot(identity_id="smf_a")
        new = _make_snapshot(identity_id="smf_b", version=2)
        rec = engine.reflect(old, new)
        assert any("snapshot:" in e for e in rec.evidence_ids)


# ============================================================
# TestRuntimeIntegration
# ============================================================
class TestRuntimeIntegration:
    def test_runtime_version_is_4_7(self):
        from src.runtime.runtime import RuntimeCore
        assert RuntimeCore.RUNTIME_VERSION == "4.7"

    def test_new_stages_in_lifecycle(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER, RuntimeStage
        assert RuntimeStage.SELF_MODEL_REFLECTION in RUNTIME_LIFECYCLE_ORDER
        assert RuntimeStage.SELF_MODEL_VALIDATION in RUNTIME_LIFECYCLE_ORDER

    def test_stage_ordering(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER, RuntimeStage
        idx_evo = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_EVOLUTION)
        idx_refl = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_REFLECTION)
        idx_val = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_VALIDATION)
        idx_pers = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_PERSISTENCE)
        # REFLECTION 和 VALIDATION 在 EVOLUTION 之后
        assert idx_refl > idx_evo
        assert idx_val > idx_evo
        # PERSISTENCE 在 VALIDATION 之后
        assert idx_pers > idx_val

    def test_configure_self_model_reflection(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)
        assert core.self_model_reflection_engine is engine

    def test_reflection_engine_property(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 默认 None
        assert core.self_model_reflection_engine is None

    def test_get_reflection_health_no_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.get_reflection_health() is None

    def test_get_reflection_health_with_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)
        h = core.get_reflection_health()
        assert h is not None
        assert h["healthy"] is True

    def test_no_op_when_no_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.reflect_self_model(None) is None
        assert core.validate_self_model(None) is None

    def test_reflect_self_model_no_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)
        # 没有 snapshot 时返回 None
        class FakeCtx:
            pass
        ctx = FakeCtx()
        assert core.reflect_self_model(ctx) is None

    def test_validate_self_model_no_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)

        class FakeCtx:
            pass
        ctx = FakeCtx()
        assert core.validate_self_model(ctx) is None

    def test_reflect_self_model_with_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)
        snap = _make_snapshot()

        class FakeCtx:
            pass
        ctx = FakeCtx()
        ctx._self_model_snapshot = snap
        rec = core.reflect_self_model(ctx)
        assert rec is not None

    def test_validate_self_model_with_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        core = RuntimeCore()
        core.configure_self_model_reflection(engine)
        snap = _make_snapshot()

        class FakeCtx:
            pass
        ctx = FakeCtx()
        ctx._self_model_snapshot = snap
        report = core.validate_self_model(ctx)
        assert report is not None

    def test_get_self_reflection_record_v2_empty(self):
        from src.runtime.runtime import RuntimeCore

        class FakeCtx:
            pass
        ctx = FakeCtx()
        core = RuntimeCore()
        assert core.get_self_reflection_record_v2(ctx) is None

    def test_get_self_validation_report_empty(self):
        from src.runtime.runtime import RuntimeCore

        class FakeCtx:
            pass
        ctx = FakeCtx()
        core = RuntimeCore()
        assert core.get_self_validation_report(ctx) is None

    def test_phase_4_3_engine_still_works(self):
        """Phase 4.3 的 configure_reflection_engine 仍然有效(不冲突)。"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 不传入 engine
        assert core._self_reflection_engine is None
        # 注入 audit-driven engine
        class FakePhase43Engine:
            pass
        engine = FakePhase43Engine()
        core.configure_reflection_engine(engine)
        assert core._self_reflection_engine is engine

    def test_stage_methods_exist(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert hasattr(core, "_invoke_self_model_reflection_stage")
        assert hasattr(core, "_invoke_self_model_validation_stage")
        assert callable(core._invoke_self_model_reflection_stage)
        assert callable(core._invoke_self_model_validation_stage)


# ============================================================
# TestInvariants —— 架构不变量
# ============================================================
class TestInvariants:
    def test_reflection_does_not_import_personality(self):
        """reflection 不 import src.personality.*"""
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(^|\n)\s*(import\s+src\.personality|from\s+src\.personality\b)"
            assert not re.search(pattern, content), (
                f"{f.name} imports src.personality.*"
            )

    def test_reflection_does_not_import_llm_sdk(self):
        """reflection 不 import LLM SDK。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        forbidden = ["openai", "qwen", "llava", "anthropic", "google.generativeai"]
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            for sdk in forbidden:
                # 检查实际的 import 语句
                pattern = r"(^|\n)\s*(import\s+" + re.escape(sdk) + r"|from\s+" + re.escape(sdk) + r"\b)"
                assert not re.search(pattern, content), (
                    f"{f.name} imports {sdk}"
                )

    def test_reflection_does_not_import_persistence(self):
        """reflection 不 import persistence。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            # 检查 "from src.runtime.self_model.persistence" 或 "import ... persistence"
            pattern = r"(from\s+src\.runtime\.self_model\.persistence|import\s+src\.runtime\.self_model\.persistence)"
            assert not re.search(pattern, content), (
                f"{f.name} imports persistence"
            )

    def test_reflection_does_not_import_identity_binding(self):
        """reflection 不 import identity_binding。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(from\s+src\.runtime\.self_model\.identity_binding|import\s+src\.runtime\.self_model\.identity_binding)"
            assert not re.search(pattern, content), (
                f"{f.name} imports identity_binding"
            )

    def test_reflection_does_not_import_evolution(self):
        """reflection 不 import evolution (单向: evolution 不应被 reflection 反向依赖)。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(from\s+src\.runtime\.self_model\.evolution|import\s+src\.runtime\.self_model\.evolution)"
            assert not re.search(pattern, content), (
                f"{f.name} imports evolution"
            )

    def test_response_engine_signature_unchanged(self):
        """ResponseEngine.generate() 签名未变。"""
        # ResponseEngine 位于 src.response.engine
        try:
            from src.response.engine import ResponseEngine
        except ImportError:
            # 兼容路径:src.engine
            try:
                from src.engine import ResponseEngine
            except ImportError:
                pytest.skip("ResponseEngine module not importable")
                return
        import inspect
        sig = inspect.signature(ResponseEngine.generate)
        # 保留原签名
        assert "self" in sig.parameters

    def test_runtime_context_schema_unchanged(self):
        """RuntimeContext.schema_version 未变。"""
        from src.runtime.context import RuntimeContext
        # 验证 schema_version 仍然存在
        assert hasattr(RuntimeContext, "schema_version")
        ctx = RuntimeContext()
        # schema_version 是类属性
        assert RuntimeContext.schema_version == "1.0"

    def test_evolution_does_not_import_reflection(self):
        """evolution 不 import reflection (Phase 4.5 不依赖 Phase 4.7)。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/evolution")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(from\s+src\.runtime\.self_model\.reflection|import\s+src\.runtime\.self_model\.reflection)"
            assert not re.search(pattern, content), (
                f"{f.name} imports reflection"
            )

    def test_persistence_does_not_import_reflection(self):
        """persistence 不 import reflection (Phase 4.6 不依赖 Phase 4.7)。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/persistence")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(from\s+src\.runtime\.self_model\.reflection|import\s+src\.runtime\.self_model\.reflection)"
            assert not re.search(pattern, content), (
                f"{f.name} imports reflection"
            )

    def test_identity_binding_does_not_import_reflection(self):
        """identity_binding 不 import reflection。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/identity_binding")
        if not base.exists():
            return
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            pattern = r"(from\s+src\.runtime\.self_model\.reflection|import\s+src\.runtime\.self_model\.reflection)"
            assert not re.search(pattern, content), (
                f"{f.name} imports reflection"
            )

    def test_backward_compatible_construction(self):
        """老 RuntimeCore 无新参数时仍可工作。"""
        from src.runtime.runtime import RuntimeCore
        # 只用最基础的参数
        core = RuntimeCore()
        # 应该有新的字段
        assert hasattr(core, "_reflection_engine")
        assert core._reflection_engine is None
        # 阶段应在 lifecycle 中
        from src.runtime.runtime import RuntimeStage
        assert hasattr(RuntimeStage, "SELF_MODEL_REFLECTION")
        assert hasattr(RuntimeStage, "SELF_MODEL_VALIDATION")


# ============================================================
# TestEndToEnd —— 端到端流程
# ============================================================
class TestEndToEnd:
    def test_evolution_to_reflection_to_validation_pipeline(self):
        """完整的演化→反思→验证→持久化流程。"""
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine,
            ContradictionRecord,
        )
        engine = SelfModelReflectionEngine()
        # 1. 初始 snapshot
        old = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.9}],
            preferences=[{"name": "style", "value": "gentle"}],
            core_values=[{"name": "kindness", "weight": 0.9}],
            identity_id="smf_e2e",
        )
        # 2. 演化后的 snapshot(模拟 Phase 4.5 evolution)
        new = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.1}],  # 大幅变化
            preferences=[{"name": "style", "value": "aggressive"}],  # 相反
            core_values=[{"name": "kindness", "weight": 0.1}],  # 大幅变化
            identity_id="smf_e2e",
            version=2,
        )
        # 3. history
        history = [
            {"changes": [
                {"field_name": "stable_traits.warmth", "old_value": 0.9, "new_value": 0.1},
                {"field_name": "preferences.style", "old_value": "gentle", "new_value": "aggressive"},
            ]}
        ]
        # 4. reflect
        rec = engine.reflect(old, new, evolution_history=history)
        assert rec.is_contradiction() is True
        assert rec.has_conflicts() is True
        assert rec.severity > 0
        # 5. 验证
        from src.runtime.self_model.reflection import ConsistencyChecker
        checker = ConsistencyChecker()
        report = checker.check(new, history)
        # 应该有 issue 或低分
        assert report.score <= 1.0

    def test_reflection_recognizes_identity_drift(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot(identity_id="smf_drift_old", identity={"name": "yuyi"})
        new = _make_snapshot(identity_id="smf_drift_new", identity={"name": "yuyi"})
        rec = engine.reflect(old, new)
        # identity_id 变化属于 identity conflict
        assert rec.has_conflicts() is True

    def test_no_reflection_with_identical_snapshot(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        snap = _make_snapshot()
        rec = engine.reflect(snap, snap)
        # 应该 consistent
        assert rec.is_consistent() is True
        assert rec.has_conflicts() is False

    def test_exception_isolation_in_engine(self):
        """engine 的 detector 抛异常时,engine 仍能返回记录。"""
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine, ContradictionDetector,
        )

        class BrokenDetector:
            def detect(self, old, new):
                raise RuntimeError("intentional broken")

        engine = SelfModelReflectionEngine(detector=BrokenDetector())  # type: ignore
        # 即便 detector 抛异常,engine 也不应该抛
        rec = engine.reflect(_make_snapshot(), _make_snapshot())
        assert rec is not None

    def test_exception_isolation_in_checker(self):
        """engine 的 checker 抛异常时,engine 仍能返回记录。"""
        from src.runtime.self_model.reflection import (
            SelfModelReflectionEngine, ConsistencyChecker,
        )

        class BrokenChecker:
            def check(self, snap, hist):
                raise RuntimeError("intentional broken")

        engine = SelfModelReflectionEngine(checker=BrokenChecker())  # type: ignore
        rec = engine.reflect(_make_snapshot(), _make_snapshot())
        assert rec is not None

    def test_serialize_full_reflection_record(self):
        from src.runtime.self_model.reflection import SelfModelReflectionEngine
        engine = SelfModelReflectionEngine()
        old = _make_snapshot(identity_id="smf_serial", version=1)
        new = _make_snapshot(identity_id="smf_serial", version=2)
        rec = engine.reflect(old, new)
        d = rec.to_dict()
        # 应该可以 json 序列化
        s = json.dumps(d, default=str, ensure_ascii=False)
        assert "smf_serial" in s
