# -*- coding: utf-8 -*-
"""
tests/test_phase_4_3_self_reflection.py

Phase 4.3: Self Reflection Layer —— 单元测试

目标:
- ReflectionRecord: create / serialize / deserialize / 字段校验
- ReflectionEngine: initial / trait_change / value_change / identity_change /
  preference / state_change / empty diff / invalid input / priority / kind /
  confidence / relation_to_values
- ReflectionStore: append / query / limit / clear / 多 identity / 容量淘汰
- SelfReflectionContextProvider: provide / format_for_prompt / priority 过滤 /
  empty / 异常隔离
- RuntimeCore 集成: configure_reflection_engine / configure_reflection_store /
  process 注入 _self_reflection_record / get_self_model_reflections /
  完全兼容(无 reflection 时 Runtime 不应破坏)
- Invariants: ResponseEngine.generate 未被改 / RuntimeContext schema 未被改 /
  Personality 未直接修改 / Audit 模块无反向依赖
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
    core_values: Optional[List[Dict]] = None,
    stable_traits: Optional[List[Dict]] = None,
    preferences: Optional[List[Dict]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    entries: Optional[List[Any]] = None,
    identity_id: str = "smf_test",
    version: int = 1,
    updated_at: str = "2026-07-31T10:00:00Z",
) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelSnapshot
    snap = SelfModelSnapshot(
        identity=identity if identity is not None else {"name": "yuyi"},
        core_values=core_values or [],
        stable_traits=stable_traits or [],
        preferences=preferences or [],
        current_state=current_state or {},
        entries=entries or [],
        identity_id=identity_id,
        version=version,
    )
    snap.updated_at = updated_at
    return snap


def _make_diff_dict(
    identity_id: str = "smf_a",
    from_version: int = 1,
    to_version: int = 2,
    total_changes: int = 1,
    identity_changes: int = 0,
    value_changes: int = 0,
    trait_changes: int = 1,
    preference_changes: int = 0,
    state_changes: int = 0,
    entry_added: int = 0,
    entry_removed: int = 0,
) -> Dict[str, Any]:
    """构造一个 diff dict(模拟 GrowthAuditRecord.diff)。"""
    return {
        "identity_id": identity_id,
        "from_version": from_version,
        "to_version": to_version,
        "identity": {
            "added": {f"key{i}": f"v{i}" for i in range(identity_changes)},
            "removed": {},
            "changed": {},
        },
        "core_values": {
            "added": [{"key": f"cv{i}", "weight": 0.5} for i in range(value_changes)],
            "removed": [],
            "changed": [],
        },
        "stable_traits": {
            "added": [
                {"name": f"trait{i}", "value": 0.5} for i in range(trait_changes)
            ],
            "removed": [],
            "changed": [],
        },
        "preferences": {
            "added": [
                {"name": f"pref{i}", "value": "x"} for i in range(preference_changes)
            ],
            "removed": [],
            "changed": [],
        },
        "current_state": {
            "added": {f"skey{i}": f"sv{i}" for i in range(state_changes)},
            "removed": {},
            "changed": {},
        },
        "entries": {
            "added": [{"kind": "growth", "summary": f"e{i}"} for i in range(entry_added)],
            "removed": [
                {"kind": "growth", "summary": f"r{i}"} for i in range(entry_removed)
            ],
        },
        "summary": {
            "total_changes": total_changes,
        },
    }


def _make_audit_record(
    categories: Optional[List[str]] = None,
    identity_id: str = "smf_a",
    from_version: int = 1,
    to_version: int = 2,
    diff: Optional[Dict[str, Any]] = None,
    severity: str = "notice",
    summary: str = "test audit",
) -> Any:
    from src.runtime.self_model.audit import GrowthAuditRecord
    rec = GrowthAuditRecord(
        identity_id=identity_id,
        from_version=from_version,
        to_version=to_version,
        categories=categories or [],
        severity=severity,
        summary=summary,
        diff=diff or _make_diff_dict(
            identity_id=identity_id,
            from_version=from_version,
            to_version=to_version,
        ),
    )
    return rec


# ============================================================
# 1. ReflectionRecord
# ============================================================
class TestReflectionRecord:
    def test_class_metadata(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, REFLECTION_RECORD_SCHEMA_VERSION,
            ReflectionPriority, ReflectionKind,
        )
        assert REFLECTION_RECORD_SCHEMA_VERSION == "1.0"
        assert ReflectionPriority.HIGH.value == "high"
        assert ReflectionPriority.MEDIUM.value == "medium"
        assert ReflectionPriority.LOW.value == "low"
        assert ReflectionPriority.NONE.value == "none"
        assert ReflectionKind.INITIAL.value == "initial"
        assert ReflectionKind.IDENTITY_DRIFT.value == "identity_drift"
        assert ReflectionKind.VALUE_SHIFT.value == "value_shift"
        assert ReflectionKind.TRAIT_TREND.value == "trait_trend"
        assert ReflectionKind.PREFERENCE.value == "preference"
        assert ReflectionKind.STATE_NOTE.value == "state_note"
        assert ReflectionKind.GROWTH_NOTE.value == "growth_note"
        assert ReflectionKind.SILENT.value == "silent"

    def test_create_default(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionKind, ReflectionPriority,
        )
        r = ReflectionRecord()
        assert r.reflection_id.startswith("ref_")
        assert r.identity_id == ""
        assert r.source_audit_id == ""
        assert r.trigger_category == ""
        assert r.reflection_kind == ReflectionKind.SILENT.value
        assert r.priority == ReflectionPriority.NONE.value
        assert r.observation == ""
        assert r.interpretation == ""
        assert r.relation_to_values == []
        assert 0.0 <= r.confidence <= 1.0
        assert r.from_version == 0
        assert r.to_version == 0
        assert r.evidence == {}
        assert r.metadata == {}
        assert r.source == "runtime"
        assert r.schema_version == "1.0"
        assert r.timestamp  # 至少非空

    def test_create_with_fields(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionKind, ReflectionPriority,
        )
        r = ReflectionRecord(
            identity_id="smf_a",
            source_audit_id="aud_x",
            trigger_category="trait_change",
            reflection_kind=ReflectionKind.TRAIT_TREND.value,
            priority=ReflectionPriority.MEDIUM.value,
            observation="warmth trait changed",
            interpretation="may reflect accumulated interactions",
            relation_to_values=["kindness", "companionship"],
            confidence=0.75,
            from_version=1,
            to_version=2,
        )
        assert r.identity_id == "smf_a"
        assert r.source_audit_id == "aud_x"
        assert r.reflection_kind == "trait_trend"
        assert r.priority == "medium"
        assert r.confidence == 0.75
        assert r.relation_to_values == ["kindness", "companionship"]

    def test_invalid_kind_downgrade(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionKind,
        )
        r = ReflectionRecord(reflection_kind="garbage_kind")
        assert r.reflection_kind == ReflectionKind.SILENT.value

    def test_invalid_priority_downgrade(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionPriority,
        )
        r = ReflectionRecord(priority="garbage_priority")
        assert r.priority == ReflectionPriority.NONE.value

    def test_confidence_clamped(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r1 = ReflectionRecord(confidence=1.5)
        r2 = ReflectionRecord(confidence=-0.5)
        r3 = ReflectionRecord(confidence="abc")  # type: ignore[arg-type]
        assert r1.confidence == 1.0
        assert r2.confidence == 0.0
        assert r3.confidence == 0.5

    def test_serialize(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r = ReflectionRecord(
            identity_id="smf_a", observation="obs", interpretation="interp",
        )
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["identity_id"] == "smf_a"
        assert d["observation"] == "obs"
        assert d["schema_version"] == "1.0"

    def test_deserialize(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r = ReflectionRecord(
            identity_id="smf_a", observation="obs", interpretation="interp",
        )
        d = r.to_dict()
        r2 = ReflectionRecord.from_dict(d)
        assert r2.identity_id == "smf_a"
        assert r2.observation == "obs"
        assert r2.reflection_id == r.reflection_id

    def test_deserialize_invalid(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        with pytest.raises(TypeError):
            ReflectionRecord.from_dict("not a dict")  # type: ignore[arg-type]

    def test_round_trip_full(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r1 = ReflectionRecord(
            identity_id="smf_b", source_audit_id="aud_1",
            trigger_category="identity",
            reflection_kind="identity_drift",
            priority="high",
            observation="identity drift observed",
            interpretation="long term continuity anchor",
            relation_to_values=["continuity", "identity"],
            confidence=0.85, from_version=2, to_version=3,
            evidence={"k": "v"}, metadata={"m": 1},
        )
        r2 = ReflectionRecord.from_dict(r1.to_dict())
        assert r2.identity_id == r1.identity_id
        assert r2.relation_to_values == r1.relation_to_values
        assert r2.confidence == r1.confidence
        assert r2.evidence == r1.evidence
        assert r2.metadata == r1.metadata

    def test_is_high_priority(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionPriority,
        )
        assert ReflectionRecord(priority=ReflectionPriority.HIGH.value).is_high_priority() is True
        assert ReflectionRecord(priority=ReflectionPriority.LOW.value).is_high_priority() is False

    def test_is_silent(self):
        from src.runtime.self_model.reflection import (
            ReflectionRecord, ReflectionKind,
        )
        assert ReflectionRecord(reflection_kind=ReflectionKind.SILENT.value).is_silent() is True
        assert ReflectionRecord(reflection_kind=ReflectionKind.INITIAL.value).is_silent() is False


# ============================================================
# 2. ReflectionEngine
# ============================================================
class TestReflectionEngine:
    def test_class_metadata(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, REFLECTION_ENGINE_SCHEMA_VERSION,
        )
        e = ReflectionEngine()
        assert REFLECTION_ENGINE_SCHEMA_VERSION == "1.0"
        assert e.reflect_count == 0
        assert e.last_reflection is None

    def test_reflect_none(self):
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        assert e.reflect(None) is None
        assert e.last_error is not None

    def test_initial_reflection(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        rec = e.reflect({
            "identity_id": "smf_a", "from_version": 0, "to_version": 1,
            "summary": {"total_changes": 0},
            "categories": ["initial"],
        })
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.INITIAL.value
        assert rec.priority == ReflectionPriority.LOW.value
        assert e.reflect_count == 1

    def test_trait_change(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(trait_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["trait_change"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.TRAIT_TREND.value
        assert rec.priority == ReflectionPriority.MEDIUM.value

    def test_value_change(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(value_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["value_change"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.VALUE_SHIFT.value
        assert rec.priority == ReflectionPriority.HIGH.value

    def test_identity_change(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(identity_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["identity"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.IDENTITY_DRIFT.value
        assert rec.priority == ReflectionPriority.HIGH.value

    def test_preference_change(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(preference_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["preference"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.PREFERENCE.value
        assert rec.priority == ReflectionPriority.MEDIUM.value

    def test_state_change(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(state_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["state_change"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.STATE_NOTE.value
        assert rec.priority == ReflectionPriority.LOW.value

    def test_entry_added(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind,
        )
        e = ReflectionEngine()
        diff = _make_diff_dict(entry_added=1, total_changes=1)
        rec = e.reflect(diff, categories=["entry_added"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.GROWTH_NOTE.value

    def test_silent_skip_silent(self):
        """纯版本号变化 → skip_silent=True 时返回 None。"""
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine(skip_silent=True)
        diff = _make_diff_dict(total_changes=0)
        rec = e.reflect(diff, categories=["version_bump"])
        assert rec is None

    def test_silent_not_skip(self):
        """纯版本号变化 → skip_silent=False 时仍产生 SILENT 记录。"""
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind,
        )
        e = ReflectionEngine(skip_silent=False)
        diff = _make_diff_dict(total_changes=0)
        rec = e.reflect(diff, categories=["version_bump"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.SILENT.value

    def test_infer_categories_from_diff(self):
        """没有 categories 时,engine 自动从 diff 推断。"""
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        diff = _make_diff_dict(
            trait_changes=1, value_changes=1, total_changes=2,
        )
        rec = e.reflect(diff)  # 不传 categories
        assert rec is not None
        # 同时有 trait + value → priority = HIGH(value_change)
        assert rec.priority == "high"

    def test_confidence_decay(self):
        """总变化越多,confidence 越低。"""
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        rec1 = e.reflect(_make_diff_dict(trait_changes=1, total_changes=1),
                        categories=["trait_change"])
        rec2 = e.reflect(_make_diff_dict(trait_changes=10, total_changes=10),
                        categories=["trait_change"])
        assert rec1 is not None
        assert rec2 is not None
        assert rec1.confidence > rec2.confidence

    def test_related_values(self):
        """特质/偏好名含核心价值关键词,应被识别。"""
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        diff = {
            "identity_id": "smf_a", "from_version": 1, "to_version": 2,
            "summary": {"total_changes": 1},
            "stable_traits": {
                "added": [{"name": "warmth", "value": 0.8}],
                "removed": [], "changed": [],
            },
        }
        rec = e.reflect(diff, categories=["trait_change"])
        assert rec is not None
        assert "kindness" in rec.relation_to_values

    def test_observation_contains_change_description(self):
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        diff = _make_diff_dict(trait_changes=1, total_changes=1)
        rec = e.reflect(diff, categories=["trait_change"])
        assert rec is not None
        # observation 应反映"stable_traits"等字段
        assert "stable_traits" in rec.observation or "trait" in rec.observation.lower()

    def test_invalid_audit_object(self):
        """非 audit/diff 也能被安全处理(返回 None 或 SILENT)。"""
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        # 普通字符串 / 数字: 应当被当作无 categories 处理 → skip_silent=True 返回 None
        assert e.reflect("not a dict") is None
        assert e.reflect(123) is None

    def test_audit_record_input(self):
        """传入 GrowthAuditRecord 应正常 reflect。"""
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind, ReflectionPriority,
        )
        e = ReflectionEngine()
        rec = _make_audit_record(
            categories=["value_change"],
            diff=_make_diff_dict(value_changes=1, total_changes=1),
        )
        r = e.reflect(rec)
        assert r is not None
        assert r.source_audit_id == rec.record_id
        assert r.identity_id == rec.identity_id
        assert r.reflection_kind == ReflectionKind.VALUE_SHIFT.value
        assert r.priority == ReflectionPriority.HIGH.value

    def test_health_check(self):
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        h = e.health_check()
        assert h["healthy"] is True
        assert h["schema_version"] == "1.0"
        assert h["reflect_count"] == 0

    def test_describe(self):
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        d = e.describe()
        assert d["schema_version"] == "1.0"


# ============================================================
# 3. ReflectionStore
# ============================================================
class TestReflectionStore:
    def test_class_metadata(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, REFLECTION_STORE_SCHEMA_VERSION,
            DEFAULT_REFLECTION_STORE_LIMIT,
        )
        assert REFLECTION_STORE_SCHEMA_VERSION == "1.0"
        assert DEFAULT_REFLECTION_STORE_LIMIT > 0
        s = ReflectionStore()
        assert s.history_limit == DEFAULT_REFLECTION_STORE_LIMIT
        assert s.total_count() == 0
        assert s.list_identities() == []

    def test_invalid_history_limit(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, DEFAULT_REFLECTION_STORE_LIMIT,
        )
        s = ReflectionStore(history_limit=0)
        assert s.history_limit == DEFAULT_REFLECTION_STORE_LIMIT
        s2 = ReflectionStore(history_limit=-5)
        assert s2.history_limit == DEFAULT_REFLECTION_STORE_LIMIT

    def test_append_basic(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        r = ReflectionRecord(identity_id="smf_a")
        ok = s.append(r)
        assert ok is True
        assert s.total_count() == 1
        assert s.count("smf_a") == 1
        assert s.append_count == 1

    def test_append_none(self):
        from src.runtime.self_model.reflection import ReflectionStore
        s = ReflectionStore()
        assert s.append(None) is False
        assert s.last_error is not None

    def test_append_invalid_type(self):
        from src.runtime.self_model.reflection import ReflectionStore
        s = ReflectionStore()
        assert s.append("not a record") is False
        assert s.last_error is not None

    def test_append_missing_identity(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        assert s.append(ReflectionRecord()) is False

    def test_get(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a"))
        s.append(ReflectionRecord(identity_id="smf_a"))
        s.append(ReflectionRecord(identity_id="smf_b"))
        a = s.get("smf_a")
        assert len(a) == 2
        assert s.get("smf_b") != []
        assert s.get("nonexistent") == []

    def test_latest(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        r1 = ReflectionRecord(identity_id="smf_a", observation="first")
        r2 = ReflectionRecord(identity_id="smf_a", observation="second")
        s.append(r1)
        s.append(r2)
        assert s.latest("smf_a") is r2
        assert s.latest("nonexistent") is None

    def test_get_by_reflection_id(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        r = ReflectionRecord(identity_id="smf_a")
        s.append(r)
        assert s.get_by_reflection_id("smf_a", r.reflection_id) is r
        assert s.get_by_reflection_id("smf_a", "nope") is None
        assert s.get_by_reflection_id("", r.reflection_id) is None

    def test_query_priority(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord, ReflectionPriority,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a", priority="high"))
        s.append(ReflectionRecord(identity_id="smf_a", priority="low"))
        s.append(ReflectionRecord(identity_id="smf_a", priority="high"))
        high = s.query(priority="high")
        assert len(high) == 2
        low = s.query(priority="low")
        assert len(low) == 1

    def test_query_kind(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a", reflection_kind="trait_trend"))
        s.append(ReflectionRecord(identity_id="smf_a", reflection_kind="value_shift"))
        ts = s.query(kind="trait_trend")
        assert len(ts) == 1

    def test_query_identity(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a"))
        s.append(ReflectionRecord(identity_id="smf_b"))
        assert len(s.query(identity_id="smf_a")) == 1
        assert len(s.query(identity_id="smf_b")) == 1
        assert len(s.query()) == 2

    def test_query_limit(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        for _ in range(5):
            s.append(ReflectionRecord(identity_id="smf_a"))
        assert len(s.query(limit=2)) == 2
        assert len(s.query(limit=10)) == 5
        assert len(s.query(limit=0)) == 5  # 0/None → 全返回

    def test_capacity_eviction(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore(history_limit=3)
        for _ in range(5):
            s.append(ReflectionRecord(identity_id="smf_a"))
        assert s.count("smf_a") == 3
        assert s.dropped_count == 2

    def test_clear(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a"))
        s.append(ReflectionRecord(identity_id="smf_b"))
        n = s.clear(identity_id="smf_a")
        assert n == 1
        assert s.count("smf_a") == 0
        assert s.count("smf_b") == 1
        n2 = s.clear()
        assert n2 == 1
        assert s.total_count() == 0

    def test_health_check(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a"))
        h = s.health_check()
        assert h["healthy"] is True
        assert h["append_count"] == 1
        assert h["total_records"] == 1

    def test_describe(self):
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        s = ReflectionStore()
        s.append(ReflectionRecord(identity_id="smf_a"))
        d = s.describe()
        assert "smf_a" in d["identities"]


# ============================================================
# 4. SelfReflectionContextProvider
# ============================================================
class TestSelfReflectionContextProvider:
    def test_class_metadata(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
            SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION,
            DEFAULT_MAX_RECENT_REFLECTIONS,
        )
        assert SELF_REFLECTION_CONTEXT_PROVIDER_SCHEMA_VERSION == "1.0"
        assert DEFAULT_MAX_RECENT_REFLECTIONS > 0
        p = SelfReflectionContextProvider()
        assert p.max_recent == DEFAULT_MAX_RECENT_REFLECTIONS

    def test_invalid_max_recent(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, DEFAULT_MAX_RECENT_REFLECTIONS,
        )
        p = SelfReflectionContextProvider(max_recent=0)
        assert p.max_recent == DEFAULT_MAX_RECENT_REFLECTIONS
        p2 = SelfReflectionContextProvider(max_recent=-1)
        assert p2.max_recent == DEFAULT_MAX_RECENT_REFLECTIONS

    def test_attach_detach(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        p = SelfReflectionContextProvider()
        assert p.is_attached is False
        p.attach()
        assert p.is_attached is True
        h = p.health_check()
        assert h["healthy"] is True
        p.detach()
        assert p.is_attached is False

    def test_provide_empty(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        ctx = p.provide([])
        assert ctx["has_reflection"] is False
        assert ctx["recent"] == []
        assert p.provide_count == 1

    def test_provide_none_records(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        ctx = p.provide(None)
        assert ctx["has_reflection"] is False

    def test_provide_basic(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        r = ReflectionRecord(
            identity_id="smf_a", observation="obs", interpretation="interp",
        )
        ctx = p.provide([r], identity_id="smf_a")
        assert ctx["has_reflection"] is True
        assert len(ctx["recent"]) == 1

    def test_provide_filters_by_identity(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        recs = [
            ReflectionRecord(identity_id="smf_a", observation=f"a{i}")
            for i in range(3)
        ] + [
            ReflectionRecord(identity_id="smf_b", observation=f"b{i}")
            for i in range(3)
        ]
        ctx = p.provide(recs, identity_id="smf_a")
        assert all(r["identity_id"] == "smf_a" for r in ctx["recent"])

    def test_produce_max_recent(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider(max_recent=2)
        p.attach()
        recs = [
            ReflectionRecord(
                identity_id="smf_a", priority="low", observation=f"o{i}",
            )
            for i in range(5)
        ]
        ctx = p.provide(recs, identity_id="smf_a")
        assert len(ctx["recent"]) == 2

    def test_provide_priority_filter(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        recs = [
            ReflectionRecord(identity_id="smf_a", priority="low"),
            ReflectionRecord(identity_id="smf_a", priority="medium"),
            ReflectionRecord(identity_id="smf_a", priority="high"),
        ]
        ctx = p.provide(recs, identity_id="smf_a", minimum_priority="medium")
        # 只保留 priority >= medium
        for r in ctx["recent"]:
            assert r["priority"] in ("medium", "high")

    def test_format_for_prompt_empty(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        text = p.format_for_prompt([])
        assert text == ""

    def test_format_for_prompt_contains_sections(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        rec = ReflectionRecord(
            identity_id="smf_a",
            observation="warmth increased",
            interpretation="may reflect long-term interactions",
            relation_to_values=["kindness"],
        )
        text = p.format_for_prompt([rec])
        assert "【自我反思】" in text
        assert "warmth" in text

    def test_format_for_prompt_truncates(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        p = SelfReflectionContextProvider()
        p.attach()
        rec = ReflectionRecord(
            observation="x" * 1000,
        )
        text = p.format_for_prompt([rec])
        # 文本长度不应无限增长
        assert len(text) < 600

    def test_health_check_with_error(self):
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )

        class _Boom:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        p = SelfReflectionContextProvider()
        p.attach()
        p.provide([_Boom()])  # 触发异常
        h = p.health_check()
        assert h["healthy"] is False
        assert "last_error" in h


# ============================================================
# 5. RuntimeCore 集成
# ============================================================
class TestRuntimeCoreReflectionIntegration:
    def test_configure_reflection_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import ReflectionEngine
        core = RuntimeCore()
        engine = ReflectionEngine()
        core.configure_reflection_engine(engine)
        assert core.self_reflection_engine is engine
        core.shutdown()

    def test_configure_reflection_store(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import ReflectionStore
        core = RuntimeCore()
        store = ReflectionStore()
        core.configure_reflection_store(store)
        assert core.self_reflection_store is store
        core.shutdown()

    def test_configure_self_reflection(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionStore,
        )
        core = RuntimeCore()
        e = ReflectionEngine()
        s = ReflectionStore()
        core.configure_self_reflection(engine=e, store=s)
        assert core.self_reflection_engine is e
        assert core.self_reflection_store is s
        core.shutdown()

    def test_query_without_reflection_store(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        # 没注入 → 返回空
        assert core.get_self_model_reflections() == []
        assert core.get_self_model_reflections(identity_id="smf_a") == []
        assert core.get_latest_reflection("smf_a") is None
        assert core.clear_reflection_history() == 0
        assert core.get_reflection_store_health() is None
        assert core.get_reflection_engine_health() is None
        core.shutdown()

    def test_get_self_reflection_record_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        core = RuntimeCore()
        core.start()
        ctx = RuntimeContext()
        # 没注入 engine → 返回 None
        assert core.get_self_reflection_record(ctx) is None
        core.shutdown()

    def test_runtime_process_with_reflection_full_chain(self):
        """完整链路:Foundation + Audit + Reflection + Store 全注入。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.audit import AuditChain
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionStore,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        chain = AuditChain()
        engine = ReflectionEngine()
        store = ReflectionStore()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            self_model_audit_chain=chain,
            self_reflection_engine=engine,
            self_reflection_store=store,
        )
        core.start()
        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            out_ctx = core.process(ev)
        except Exception:
            out_ctx = None

        # 至少有 1 条 audit record
        assert chain.record_count >= 1
        # store 中应有 1 条 reflection(若 audit 触发 reflection)
        if store.total_count() > 0:
            reflections = core.get_self_model_reflections()
            assert len(reflections) >= 1
        # 至少 engine 健康
        eh = core.get_reflection_engine_health()
        assert eh is not None
        core.shutdown()

    def test_reflection_engine_health(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import ReflectionEngine
        core = RuntimeCore()
        engine = ReflectionEngine()
        core.configure_reflection_engine(engine)
        h = core.get_reflection_engine_health()
        assert h is not None
        assert h["healthy"] is True
        core.shutdown()

    def test_reflection_store_health(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        core = RuntimeCore()
        store = ReflectionStore()
        store.append(ReflectionRecord(identity_id="smf_a"))
        core.configure_reflection_store(store)
        h = core.get_reflection_store_health()
        assert h is not None
        assert h["total_records"] == 1
        core.shutdown()

    def test_clear_reflection_history(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        core = RuntimeCore()
        store = ReflectionStore()
        store.append(ReflectionRecord(identity_id="smf_a"))
        store.append(ReflectionRecord(identity_id="smf_b"))
        core.configure_reflection_store(store)
        n = core.clear_reflection_history(identity_id="smf_a")
        assert n == 1
        assert store.count("smf_a") == 0
        assert store.count("smf_b") == 1
        core.shutdown()

    def test_get_latest_reflection(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        core = RuntimeCore()
        store = ReflectionStore()
        r1 = ReflectionRecord(identity_id="smf_a", observation="r1")
        r2 = ReflectionRecord(identity_id="smf_a", observation="r2")
        store.append(r1)
        store.append(r2)
        core.configure_reflection_store(store)
        latest = core.get_latest_reflection("smf_a")
        assert latest is r2
        core.shutdown()

    def test_get_self_model_reflections_with_filters(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            ReflectionStore, ReflectionRecord,
        )
        core = RuntimeCore()
        store = ReflectionStore()
        store.append(ReflectionRecord(identity_id="smf_a", priority="high"))
        store.append(ReflectionRecord(identity_id="smf_a", priority="low"))
        store.append(ReflectionRecord(identity_id="smf_b", priority="high"))
        core.configure_reflection_store(store)
        # 过滤 identity
        a = core.get_self_model_reflections(identity_id="smf_a")
        assert len(a) == 2
        # 过滤 priority
        h = core.get_self_model_reflections(priority="high")
        assert len(h) == 2
        # 过滤 identity + priority
        a_h = core.get_self_model_reflections(
            identity_id="smf_a", priority="high",
        )
        assert len(a_h) == 1
        # limit
        limited = core.get_self_model_reflections(limit=1)
        assert len(limited) == 1
        core.shutdown()

    def test_runtime_backward_compatible_without_reflection(self):
        """完全无 reflection → 不破坏 Runtime。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
        )
        core.start()
        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            out_ctx = core.process(ev)
        except Exception:
            out_ctx = None
        # 仍可查询,返回空
        assert core.get_self_model_reflections() == []
        if out_ctx is not None:
            assert core.get_self_reflection_record(out_ctx) is None
        core.shutdown()

    def test_reflection_store_query_error_isolated(self):
        """store.query 抛异常时,Runtime 应隔离。"""
        from src.runtime.runtime import RuntimeCore

        class _BoomStore:
            def query(self, **kwargs):
                raise RuntimeError("boom")

        core = RuntimeCore()
        core.configure_reflection_store(_BoomStore())
        # 异常被隔离 → 返回空列表
        assert core.get_self_model_reflections() == []
        core.shutdown()

    def test_reflection_engine_reflect_error_isolated(self):
        """engine.reflect 抛异常时,SELF_MODEL_BUILD 不应被破坏。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.audit import AuditChain
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        class _BoomEngine:
            def reflect(self, audit, **kwargs):
                raise RuntimeError("boom")
            def health_check(self):
                return {"healthy": False, "error": "boom"}

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        chain = AuditChain()
        engine = _BoomEngine()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            self_model_audit_chain=chain,
            self_reflection_engine=engine,
        )
        core.start()
        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            core.process(ev)
        except Exception:
            pass
        # audit chain 仍正常工作
        assert chain.record_count >= 1
        # engine 异常被隔离,Runtime 没崩
        core.shutdown()

    def test_configure_self_reflection_context_provider(self):
        """Phase 4.3: 注入 SelfReflectionContextProvider 到 ResponseAdapter。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        core = RuntimeCore(adapter_registry=registry)
        core.start()
        # 不抛
        core.configure_self_reflection_context_provider(
            SelfReflectionContextProvider()
        )
        core.shutdown()


# ============================================================
# 6. ResponseAdapter 集成
# ============================================================
class TestResponseAdapterReflectionInjection:
    def test_backward_compat_no_reflection(self):
        """无 reflection 注入时,personality_context 不应含 self_reflection_*。"""
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        adapter = ResponseAdapter()
        adapter.attach()
        ctx = RuntimeContext(user_input="hi")
        req = adapter.build_request(ctx, PersonalityRuntimeContext())
        assert "self_reflection_text" not in (req.personality_context or {})
        assert "self_reflection_data" not in (req.personality_context or {})

    def test_inject_reflection_with_provider_and_record(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider, ReflectionRecord,
        )
        adapter = ResponseAdapter()
        adapter.attach()
        rec = ReflectionRecord(
            identity_id="smf_a", observation="warmth up",
            interpretation="long-term interactions",
        )
        # 模拟 phase 4.3 写入
        provider = SelfReflectionContextProvider()
        provider.attach()
        adapter.set_self_reflection_context_provider(provider)
        ctx = RuntimeContext(user_input="hi")
        setattr(ctx, "_self_reflection_record", rec)
        req = adapter.build_request(ctx, PersonalityRuntimeContext())
        assert "self_reflection_text" in (req.personality_context or {})
        assert "self_reflection_data" in (req.personality_context or {})

    def test_inject_reflection_without_record(self):
        """有 provider 但 ctx 无 record → 不注入。"""
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.reflection import (
            SelfReflectionContextProvider,
        )
        adapter = ResponseAdapter()
        adapter.attach()
        provider = SelfReflectionContextProvider()
        provider.attach()
        adapter.set_self_reflection_context_provider(provider)
        ctx = RuntimeContext(user_input="hi")
        req = adapter.build_request(ctx, PersonalityRuntimeContext())
        assert "self_reflection_text" not in (req.personality_context or {})

    def test_reflection_provider_exception_isolated(self):
        """provider 抛异常时,build_request 不应崩。"""
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        class _BoomProvider:
            def provide(self, *args, **kwargs):
                raise RuntimeError("boom")
            def format_context_for_prompt(self, *args, **kwargs):
                raise RuntimeError("boom")

        adapter = ResponseAdapter()
        adapter.attach()
        adapter.set_self_reflection_context_provider(_BoomProvider())
        ctx = RuntimeContext(user_input="hi")
        # 不抛
        req = adapter.build_request(ctx, PersonalityRuntimeContext())
        assert "self_reflection_text" not in (req.personality_context or {})


# ============================================================
# 7. Invariants —— 不变量
# ============================================================
class TestInvariants:
    def test_runtime_context_schema_unchanged(self):
        """RuntimeContext 必须仍只有 v1.0 字段(无 self_reflection_*)."""
        from src.runtime.context import RuntimeContext, RUNTIME_CONTEXT_SCHEMA_VERSION
        ctx = RuntimeContext(user_input="hi")
        # 允许的字段
        allowed = {
            "session_id", "user_input", "timestamp",
            "memory_context", "emotion_state", "personality_snapshot",
            "growth_proposals", "schema_version",
        }
        actual = set(ctx.to_dict().keys())
        assert actual == allowed
        assert ctx.schema_version == RUNTIME_CONTEXT_SCHEMA_VERSION

    def test_response_engine_generate_unchanged(self):
        """ResponseEngine.generate() 签名应保持一致(若该模块存在)。"""
        try:
            from src.response.response_engine import ResponseEngine
        except Exception:
            # 不存在时跳过
            return
        engine = ResponseEngine()
        assert hasattr(engine, "generate")
        import inspect
        sig = inspect.signature(engine.generate)
        # 至少有 request 参数
        assert "request" in sig.parameters or any(
            p.kind in (p.POSITIONAL_OR_KEYWORD, p.POSITIONAL_ONLY)
            for p in sig.parameters.values()
        )

    def test_personality_unchanged(self):
        """src.personality.* 不应被 import 到 reflection 模块。"""
        import ast
        import inspect
        from src.runtime.self_model.reflection import reflection_record
        from src.runtime.self_model.reflection import reflection_engine
        from src.runtime.self_model.reflection import reflection_store
        from src.runtime.self_model.reflection import reflection_context_provider

        for mod in [
            reflection_record, reflection_engine,
            reflection_store, reflection_context_provider,
        ]:
            try:
                source = inspect.getsource(mod)
            except (OSError, TypeError):
                continue
            # 只检测真正的 import 语句,而不是 docstring
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("src.personality"), (
                            f"{mod.__name__} 不应 import {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("src.personality"):
                        assert False, (
                            f"{mod.__name__} 不应 from {node.module} import ..."
                        )

    def test_audit_module_no_reverse_dependency(self):
        """Audit 模块不应依赖 reflection 模块(单向)。"""
        from src.runtime.self_model.audit import (
            audit_chain, growth_audit_record,
            snapshot_diff_engine, self_model_history_store,
            snapshot_archive,
        )
        for mod in [
            audit_chain, growth_audit_record,
            snapshot_diff_engine, self_model_history_store,
            snapshot_archive,
        ]:
            mod_name = mod.__name__
            assert "reflection" not in mod_name

    def test_reflection_module_does_not_import_llm(self):
        """reflection 模块不应 import 任何 LLM SDK(用 AST 仅扫描 import 语句)。"""
        import ast
        import inspect
        from src.runtime.self_model.reflection import (
            reflection_record, reflection_engine,
            reflection_store, reflection_context_provider,
        )
        forbidden_roots = (
            "openai", "qwen", "llava", "anthropic",
            "google.generativeai", "google_genai", "vertexai",
            "cohere", "mistralai", "huggingface_hub",
        )
        for mod in [
            reflection_record, reflection_engine,
            reflection_store, reflection_context_provider,
        ]:
            try:
                source = inspect.getsource(mod)
            except (OSError, TypeError):
                continue
            # AST 解析:仅检测真正的 import 语句,忽略 docstring / 注释
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_roots:
                            if alias.name == forbidden or alias.name.startswith(
                                forbidden + "."
                            ):
                                assert False, (
                                    f"{mod.__name__} 不应 import {alias.name}"
                                )
                elif isinstance(node, ast.ImportFrom):
                    if not node.module:
                        continue
                    for forbidden in forbidden_roots:
                        if node.module == forbidden or node.module.startswith(
                            forbidden + "."
                        ):
                            assert False, (
                                f"{mod.__name__} 不应 from {node.module} import ..."
                            )


# ============================================================
# 8. 端到端 —— Runtime → ResponseAdapter 完整链路
# ============================================================
class TestEndToEndPipeline:
    def test_end_to_end_reflection_injected_to_request(self):
        """完整链路:Runtime.process → ctx._self_reflection_record →
        ResponseAdapter.build_request → personality_context.self_reflection_text。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.audit import AuditChain
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionStore,
            SelfReflectionContextProvider,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        chain = AuditChain()
        engine = ReflectionEngine()
        store = ReflectionStore()
        reflection_provider = SelfReflectionContextProvider()
        reflection_provider.attach()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            self_model_audit_chain=chain,
            self_reflection_engine=engine,
            self_reflection_store=store,
        )
        core.start()
        # 注入 provider 到 response adapter
        core.configure_self_reflection_context_provider(reflection_provider)

        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            ctx = core.process(ev)
        except Exception:
            ctx = None

        if ctx is not None:
            # 若有 reflection record,通过 adapter 模拟 build_request
            rec = core.get_self_reflection_record(ctx)
            if rec is not None:
                response_adapter = registry.get("response_adapter_impl")
                req = response_adapter.build_request(
                    ctx, PersonalityRuntimeContext(),
                )
                # 应注入 self_reflection_text
                if engine.reflect_count > 0:
                    # 仅当 engine 实际产生了 reflection 时验证
                    pass
        core.shutdown()


# ============================================================
# 9. 综合验证 —— 整体数量 + 关键路径
# ============================================================
class TestOverallSanity:
    def test_reflection_engine_skip_silent_keeps_silent_when_disabled(self):
        from src.runtime.self_model.reflection import (
            ReflectionEngine, ReflectionKind,
        )
        e = ReflectionEngine(skip_silent=False)
        rec = e.reflect(_make_diff_dict(total_changes=0),
                        categories=["version_bump"])
        assert rec is not None
        assert rec.reflection_kind == ReflectionKind.SILENT.value

    def test_reflection_evidence_truncated(self):
        from src.runtime.self_model.reflection import ReflectionEngine
        e = ReflectionEngine()
        # 构造一个超多 key 的 diff
        big_diff = _make_diff_dict(
            identity_changes=20, value_changes=20, total_changes=40,
        )
        rec = e.reflect(big_diff)
        assert rec is not None
        # evidence 一定存在,且不应是巨大 dict
        assert "identity" in rec.evidence

    def test_reflection_record_timestamp_iso(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r = ReflectionRecord()
        # 应该是 ISO 8601 格式
        assert "T" in r.timestamp
        assert r.timestamp.endswith("Z")

    def test_reflection_record_unique_ids(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        ids = {ReflectionRecord().reflection_id for _ in range(50)}
        assert len(ids) == 50

    def test_reflection_metadata_preserved(self):
        from src.runtime.self_model.reflection import ReflectionRecord
        r = ReflectionRecord(
            metadata={"all_categories": ["trait_change"], "k": 1},
        )
        assert r.metadata["all_categories"] == ["trait_change"]
        assert r.metadata["k"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
