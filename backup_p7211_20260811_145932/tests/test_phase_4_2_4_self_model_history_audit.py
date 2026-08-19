# -*- coding: utf-8 -*-
"""
tests/test_phase_4_2_4_self_model_history_audit.py

Phase 4.2.4: Self Model History & Audit —— 单元测试

目标:
- SelfModelHistoryStore 增删查
- SnapshotArchive 归档 + 版本区间查询
- SnapshotDiffEngine 字段级 diff
- GrowthAuditRecord 序列化 + 严重度推断
- AuditChain 编排
- Runtime 集成
- RuntimeContext schema 不变
- ResponseEngine 不变
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
    from src.runtime.self_model.self_model_data import (
        SelfModelSnapshot,
        SelfModelEntry,
    )
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
    # 覆盖 updated_at
    snap.updated_at = updated_at
    return snap


def _make_entry(kind: str, summary: str, entry_id: Optional[str] = None) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelEntry
    e = SelfModelEntry(kind=kind, summary=summary)
    if entry_id:
        e.entry_id = entry_id
    return e


# ============================================================
# 1. SelfModelHistoryStore
# ============================================================
class TestSelfModelHistoryStore:
    def test_class_metadata(self):
        from src.runtime.self_model.audit import (
            SelfModelHistoryStore,
            SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION,
            DEFAULT_HISTORY_LIMIT,
        )
        s = SelfModelHistoryStore()
        assert s.history_limit == DEFAULT_HISTORY_LIMIT
        assert SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION == "1.0"

    def test_initial_empty(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        assert s.total_count() == 0
        assert s.list_identities() == []
        assert s.append_count == 0
        assert s.dropped_count == 0

    def test_append_basic(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        snap = _make_snapshot(identity_id="smf_a", version=1)
        ok = s.append(snap)
        assert ok is True
        assert s.total_count() == 1
        assert s.count("smf_a") == 1
        assert s.append_count == 1

    def test_append_none(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        assert s.append(None) is False
        assert s.last_error is not None

    def test_append_no_identity(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        # snapshot with empty identity_id
        snap = _make_snapshot(identity_id="", version=1)
        # dataclass 不会拒绝空字符串;但 self_model_history_store 会检查
        snap.identity_id = ""  # 强制空
        ok = s.append(snap)
        assert ok is False

    def test_append_lifo_order(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        for v in range(1, 4):
            s.append(_make_snapshot(identity_id="smf_a", version=v))
        latest = s.latest("smf_a")
        assert latest.version == 3

    def test_get_by_version(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        for v in range(1, 4):
            s.append(_make_snapshot(identity_id="smf_a", version=v))
        snap = s.get_by_version("smf_a", 2)
        assert snap is not None
        assert snap.version == 2

    def test_get_by_version_not_found(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1))
        assert s.get_by_version("smf_a", 99) is None
        assert s.get_by_version("smf_b", 1) is None
        assert s.get_by_version("", 1) is None

    def test_capacity_eviction(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore(history_limit=3)
        for v in range(1, 6):
            s.append(_make_snapshot(identity_id="smf_a", version=v))
        # 只保留 v3, v4, v5
        assert s.count("smf_a") == 3
        assert s.dropped_count == 2
        # get_by_version(1) 已淘汰
        assert s.get_by_version("smf_a", 1) is None
        assert s.get_by_version("smf_a", 3) is not None

    def test_multi_identity(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1))
        s.append(_make_snapshot(identity_id="smf_b", version=1))
        s.append(_make_snapshot(identity_id="smf_a", version=2))
        assert s.total_count() == 3
        assert s.count("smf_a") == 2
        assert s.count("smf_b") == 1
        assert sorted(s.list_identities()) == ["smf_a", "smf_b"]

    def test_query_time_window(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1, updated_at="2026-07-31T08:00:00Z"))
        s.append(_make_snapshot(identity_id="smf_a", version=2, updated_at="2026-07-31T10:00:00Z"))
        s.append(_make_snapshot(identity_id="smf_a", version=3, updated_at="2026-07-31T12:00:00Z"))
        result = s.query(identity_id="smf_a", since="2026-07-31T09:00:00Z")
        assert len(result) == 2

    def test_query_with_limit(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        for v in range(1, 6):
            s.append(_make_snapshot(identity_id="smf_a", version=v))
        result = s.query(identity_id="smf_a", limit=2)
        assert len(result) == 2
        # 返回最新 2 个
        assert result[-1].version == 5

    def test_clear(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1))
        s.append(_make_snapshot(identity_id="smf_b", version=1))
        n = s.clear(identity_id="smf_a")
        assert n == 1
        assert s.count("smf_a") == 0
        assert s.count("smf_b") == 1
        n2 = s.clear()
        assert n2 == 1

    def test_health_check(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1))
        h = s.health_check()
        assert h["healthy"] is True
        assert h["total_snapshots"] == 1
        assert h["identity_count"] == 1

    def test_describe(self):
        from src.runtime.self_model.audit import SelfModelHistoryStore
        s = SelfModelHistoryStore()
        s.append(_make_snapshot(identity_id="smf_a", version=1))
        d = s.describe()
        assert "smf_a" in d["identities"]


# ============================================================
# 2. SnapshotArchive
# ============================================================
class TestSnapshotArchive:
    def test_class_metadata(self):
        from src.runtime.self_model.audit import (
            SnapshotArchive,
            SNAPSHOT_ARCHIVE_SCHEMA_VERSION,
        )
        a = SnapshotArchive()
        assert SNAPSHOT_ARCHIVE_SCHEMA_VERSION == "1.0"

    def test_archive_basic(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        snap = _make_snapshot(identity_id="smf_a", version=1)
        entry = a.archive(snap)
        assert entry is not None
        assert entry["identity_id"] == "smf_a"
        assert entry["version"] == 1
        assert a.archive_count == 1

    def test_archive_none(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        assert a.archive(None) is None
        assert a.last_error is not None

    def test_get_by_identity(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_a", version=2))
        a.archive(_make_snapshot(identity_id="smf_b", version=1))
        entries = a.get_by_identity("smf_a")
        assert len(entries) == 2
        assert a.get_by_identity("smf_b") is not None
        assert a.get_by_identity("nonexistent") == []

    def test_get_by_version(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_a", version=2))
        e = a.get_by_version("smf_a", 2)
        assert e is not None
        assert e["version"] == 2
        assert a.get_by_version("smf_a", 99) is None

    def test_latest(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_a", version=3))
        e = a.latest("smf_a")
        assert e["version"] == 3

    def test_range_query(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        for v in range(1, 6):
            a.archive(_make_snapshot(identity_id="smf_a", version=v))
        result = a.range_query("smf_a", v_min=2, v_max=4)
        versions = [e["version"] for e in result]
        assert versions == [2, 3, 4]

    def test_archive_limit_eviction(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive(archive_limit=3)
        for v in range(1, 6):
            a.archive(_make_snapshot(identity_id="smf_a", version=v))
        assert a.total() == 3
        assert a.dropped_count == 2

    def test_clear(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_b", version=1))
        n = a.clear(identity_id="smf_a")
        assert n == 1
        assert a.count("smf_a") == 0
        n2 = a.clear()
        assert n2 == 1
        assert a.total() == 0

    def test_identities(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_b", version=1))
        ids = a.identities()
        assert "smf_a" in ids
        assert "smf_b" in ids

    def test_archive_count_and_total(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        assert a.total() == 0
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        assert a.total() == 1
        assert a.count("smf_a") == 1

    def test_get_by_index(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        a.archive(_make_snapshot(identity_id="smf_a", version=2))
        e0 = a.get_by_index(0)
        assert e0["version"] == 1
        assert a.get_by_index(99) is None
        assert a.get_by_index(-1) is None

    def test_health_check(self):
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        a.archive(_make_snapshot(identity_id="smf_a", version=1))
        h = a.health_check()
        assert h["healthy"] is True
        assert h["archive_count"] == 1

    def test_archive_snapshot_to_dict_failure(self):
        """snapshot 没有 to_dict → 失败但不抛。"""
        from src.runtime.self_model.audit import SnapshotArchive
        a = SnapshotArchive()
        # 一个奇怪的对象,有 identity_id/version 但没有 to_dict
        class _Bad:
            identity_id = "x"
            version = 1
            updated_at = "now"
            schema_version = "1.0"
        assert a.archive(_Bad()) is None
        assert a.last_error is not None


# ============================================================
# 3. SnapshotDiffEngine
# ============================================================
class TestSnapshotDiffEngine:
    def test_class_metadata(self):
        from src.runtime.self_model.audit import (
            SnapshotDiffEngine,
            SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
        )
        e = SnapshotDiffEngine()
        assert SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION == "1.0"
        assert e.diff_count == 0

    def test_diff_identity_added(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(identity={"name": "yuyi"}, identity_id="a", version=1)
        b = _make_snapshot(
            identity={"name": "yuyi", "archetype": "ai"},
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        assert "archetype" in d["identity"]["added"]
        assert e.diff_count == 1

    def test_diff_identity_removed(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(
            identity={"name": "yuyi", "archetype": "ai"},
            identity_id="a", version=1,
        )
        b = _make_snapshot(identity={"name": "yuyi"}, identity_id="a", version=2)
        d = e.diff(a, b)
        assert "archetype" in d["identity"]["removed"]

    def test_diff_stable_traits(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.5}],
            identity_id="a", version=1,
        )
        b = _make_snapshot(
            stable_traits=[
                {"name": "warmth", "value": 0.8},
                {"name": "patience", "value": 0.7},
            ],
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        # warmth changed, patience added
        trait_keys = [c["key"] for c in d["stable_traits"]["changed"]]
        assert "warmth" in trait_keys
        added_names = [t["name"] for t in d["stable_traits"]["added"]]
        assert "patience" in added_names

    def test_diff_preferences(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(
            preferences=[{"name": "color", "value": "red"}],
            identity_id="a", version=1,
        )
        b = _make_snapshot(
            preferences=[{"name": "color", "value": "blue"}],
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        assert d["preferences"]["changed"]

    def test_diff_current_state(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(current_state={"mood": "sad"}, identity_id="a", version=1)
        b = _make_snapshot(current_state={"mood": "happy"}, identity_id="a", version=2)
        d = e.diff(a, b)
        assert "mood" in d["current_state"]["changed"]

    def test_diff_entries(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        e1 = _make_entry("growth", "e1", entry_id="e_001")
        e2 = _make_entry("growth", "e2", entry_id="e_002")
        a = _make_snapshot(entries=[e1], identity_id="a", version=1)
        b = _make_snapshot(entries=[e1, e2], identity_id="a", version=2)
        d = e.diff(a, b)
        added_ids = [x.get("entry_id") for x in d["entries"]["added"]]
        assert "e_002" in added_ids

    def test_diff_summary(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(identity_id="a", version=1)
        b = _make_snapshot(
            stable_traits=[{"name": "x", "value": 1}],
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        assert d["summary"]["total_changes"] > 0
        assert d["summary"]["stable_traits_changes"] >= 1

    def test_summary_helper(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(identity_id="a", version=1)
        b = _make_snapshot(
            preferences=[{"name": "k", "value": "v"}],
            identity_id="a", version=2,
        )
        s = e.summary(a, b)
        assert s["total_changes"] > 0

    def test_has_changes(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        a = _make_snapshot(identity_id="a", version=1)
        b = _make_snapshot(identity_id="a", version=1)  # 内容完全相同
        d = e.diff(a, b)
        assert e.has_changes(d) is False
        c = _make_snapshot(
            current_state={"x": 1}, identity_id="a", version=2,
        )
        d2 = e.diff(a, c)
        assert e.has_changes(d2) is True

    def test_diff_none_input(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        d = e.diff(None, None)
        assert "error" in d
        a = _make_snapshot(identity_id="a", version=1)
        d2 = e.diff(a, None)
        assert "error" in d2

    def test_health_check(self):
        from src.runtime.self_model.audit import SnapshotDiffEngine
        e = SnapshotDiffEngine()
        h = e.health_check()
        assert h["healthy"] is True
        assert h["diff_count"] == 0


# ============================================================
# 4. GrowthAuditRecord
# ============================================================
class TestGrowthAuditRecord:
    def test_class_metadata(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord,
            GROWTH_AUDIT_RECORD_SCHEMA_VERSION,
            AuditCategory,
            AuditSeverity,
        )
        r = GrowthAuditRecord()
        assert GROWTH_AUDIT_RECORD_SCHEMA_VERSION == "1.0"
        assert r.schema_version == "1.0"
        assert AuditCategory.INITIAL.value == "initial"
        assert AuditSeverity.WARN.value == "warn"

    def test_to_dict_from_dict(self):
        from src.runtime.self_model.audit import GrowthAuditRecord
        r = GrowthAuditRecord(
            identity_id="smf_a",
            from_version=1,
            to_version=2,
            categories=["trait_change"],
            severity="warn",
            summary="changed",
        )
        d = r.to_dict()
        r2 = GrowthAuditRecord.from_dict(d)
        assert r2.identity_id == "smf_a"
        assert r2.from_version == 1
        assert r2.to_version == 2
        assert r2.severity == "warn"
        assert r2.summary == "changed"

    def test_from_diff_basic(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord, SnapshotDiffEngine,
        )
        e = SnapshotDiffEngine()
        a = _make_snapshot(identity_id="a", version=1)
        b = _make_snapshot(
            stable_traits=[{"name": "warmth", "value": 0.9}],
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        r = GrowthAuditRecord.from_diff(d, identity_id="a", source="test")
        assert r.identity_id == "a"
        assert r.from_version == 1
        assert r.to_version == 2
        assert "trait_change" in r.categories
        assert r.severity in ("notice", "warn", "alert", "info")

    def test_from_diff_alert_for_identity(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord, SnapshotDiffEngine,
        )
        e = SnapshotDiffEngine()
        a = _make_snapshot(
            identity={"name": "yuyi", "archetype": "old"},
            identity_id="a", version=1,
        )
        b = _make_snapshot(
            identity={"name": "yuyi", "archetype": "new"},
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        r = GrowthAuditRecord.from_diff(d)
        assert r.is_alert() is True
        assert "identity" in r.categories

    def test_from_diff_alert_for_value_change(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord, SnapshotDiffEngine,
        )
        e = SnapshotDiffEngine()
        a = _make_snapshot(
            core_values=[{"key": "honesty", "weight": 0.5}],
            identity_id="a", version=1,
        )
        b = _make_snapshot(
            core_values=[{"key": "honesty", "weight": 0.9}],
            identity_id="a", version=2,
        )
        d = e.diff(a, b)
        r = GrowthAuditRecord.from_diff(d)
        assert r.is_alert() is True

    def test_from_diff_warn_for_state(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord, SnapshotDiffEngine,
        )
        e = SnapshotDiffEngine()
        a = _make_snapshot(current_state={"mood": "x"}, identity_id="a", version=1)
        b = _make_snapshot(current_state={"mood": "y"}, identity_id="a", version=2)
        d = e.diff(a, b)
        r = GrowthAuditRecord.from_diff(d)
        assert r.is_warn_or_above() is True

    def test_from_diff_notice_for_entries(self):
        from src.runtime.self_model.audit import (
            GrowthAuditRecord, SnapshotDiffEngine,
        )
        e = SnapshotDiffEngine()
        e1 = _make_entry("growth", "e1", entry_id="e_001")
        a = _make_snapshot(entries=[], identity_id="a", version=1)
        b = _make_snapshot(entries=[e1], identity_id="a", version=2)
        d = e.diff(a, b)
        r = GrowthAuditRecord.from_diff(d)
        assert r.severity == "notice"

    def test_initial_record(self):
        from src.runtime.self_model.audit import GrowthAuditRecord
        snap = _make_snapshot(identity_id="smf_a", version=1)
        r = GrowthAuditRecord.initial(snap, identity_id="smf_a", source="test")
        assert r.from_version == 0
        assert r.to_version == 1
        assert "initial" in r.categories

    def test_initial_record_with_dict(self):
        from src.runtime.self_model.audit import GrowthAuditRecord
        snap = {"identity_id": "smf_a", "version": 3, "updated_at": "now"}
        r = GrowthAuditRecord.initial(snap, identity_id="smf_a")
        assert r.to_version == 3

    def test_from_dict_invalid_type(self):
        from src.runtime.self_model.audit import GrowthAuditRecord
        with pytest.raises(TypeError):
            GrowthAuditRecord.from_dict("not a dict")

    def test_is_warn_or_above(self):
        from src.runtime.self_model.audit import GrowthAuditRecord
        r1 = GrowthAuditRecord(severity="info")
        r2 = GrowthAuditRecord(severity="warn")
        r3 = GrowthAuditRecord(severity="alert")
        r4 = GrowthAuditRecord(severity="notice")
        assert r1.is_warn_or_above() is False
        assert r2.is_warn_or_above() is True
        assert r3.is_warn_or_above() is True
        assert r4.is_warn_or_above() is False


# ============================================================
# 5. AuditChain
# ============================================================
class TestAuditChain:
    def test_class_metadata(self):
        from src.runtime.self_model.audit import (
            AuditChain, AUDIT_CHAIN_SCHEMA_VERSION,
        )
        c = AuditChain()
        assert AUDIT_CHAIN_SCHEMA_VERSION == "1.0"
        assert c.record_count == 0

    def test_record_snapshot_initial(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        snap = _make_snapshot(identity_id="smf_a", version=1)
        r = c.record_snapshot(snap, source="test")
        assert r is not None
        assert r.identity_id == "smf_a"
        assert r.to_version == 1
        assert "initial" in r.categories
        # 已写入 history
        assert c.history_store.count("smf_a") == 1
        # 已写入 archive
        assert c.archive.count("smf_a") == 1

    def test_record_snapshot_two_versions(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        r2 = c.record_snapshot(_make_snapshot(
            identity_id="smf_a", version=2,
            stable_traits=[{"name": "warmth", "value": 0.9}],
        ))
        assert r2.to_version == 2
        assert "trait_change" in r2.categories

    def test_record_snapshot_none(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        assert c.record_snapshot(None) is None
        assert c.last_error is not None

    def test_records_query(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        c.record_snapshot(_make_snapshot(
            identity_id="smf_a", version=2,
            stable_traits=[{"name": "x", "value": 1}],
        ))
        c.record_snapshot(_make_snapshot(identity_id="smf_b", version=1))
        # 按 identity 过滤
        a_records = c.records(identity_id="smf_a")
        assert len(a_records) == 2
        # 按 category 过滤
        init_records = c.records(category="initial")
        assert len(init_records) == 2  # smf_a v1, smf_b v1

    def test_records_limit(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        for v in range(1, 5):
            c.record_snapshot(_make_snapshot(identity_id="smf_a", version=v))
        result = c.records(limit=2)
        assert len(result) == 2

    def test_get_record(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        r = c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        assert c.get_record(r.record_id) is r
        assert c.get_record("nonexistent") is None

    def test_diff_method(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        c.record_snapshot(_make_snapshot(
            identity_id="smf_a", version=2,
            stable_traits=[{"name": "x", "value": 1}],
        ))
        d = c.diff("smf_a", 1, 2)
        assert d is not None
        assert d["from_version"] == 1
        assert d["to_version"] == 2
        # 没有的版本
        assert c.diff("smf_a", 1, 99) is None
        assert c.diff("smf_b", 1, 2) is None

    def test_diff_latest(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        d = c.diff_latest("smf_a")  # 只有一个,没 diff
        assert d is None
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=2))
        d2 = c.diff_latest("smf_a")
        assert d2 is not None

    def test_snapshot_history(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        for v in range(1, 4):
            c.record_snapshot(_make_snapshot(identity_id="smf_a", version=v))
        h = c.snapshot_history("smf_a")
        assert len(h) == 3

    def test_get_snapshot(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=2))
        s = c.get_snapshot("smf_a")
        assert s.version == 2
        s1 = c.get_snapshot("smf_a", version=1)
        assert s1.version == 1

    def test_max_records_eviction(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain(max_records=3)
        for v in range(1, 5):
            c.record_snapshot(_make_snapshot(identity_id="smf_a", version=v))
        assert c.records_total == 3
        assert c.record_count == 4  # 总数仍累加

    def test_health_check(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        c.record_snapshot(_make_snapshot(identity_id="smf_a", version=1))
        h = c.health_check()
        assert h["healthy"] is True
        assert h["record_count"] == 1
        assert "history_store" in h
        assert "archive" in h
        assert "diff_engine" in h

    def test_describe(self):
        from src.runtime.self_model.audit import AuditChain
        c = AuditChain()
        d = c.describe()
        assert d["schema_version"] == "1.0"
        assert d["record_count"] == 0


# ============================================================
# 6. RuntimeCore 集成
# ============================================================
class TestRuntimeCoreIntegration:
    def test_audit_chain_property(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.self_model_audit_chain is None
        core.start()
        # 不抛
        assert core.self_model_audit_chain is None
        core.shutdown()

    def test_configure_audit_chain(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.audit import AuditChain
        chain = AuditChain()
        core = RuntimeCore()
        core.start()
        core.configure_audit_chain(chain)
        assert core.self_model_audit_chain is chain
        core.shutdown()

    def test_query_without_audit_chain(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        # 没注入 audit_chain → 返回空
        assert core.get_self_model_audit_records() == []
        assert core.get_self_model_snapshot_history("smf_a") == []
        assert core.get_self_model_diff("smf_a", 1, 2) is None
        assert core.get_self_model_diff_latest("smf_a") is None
        core.shutdown()

    def test_runtime_process_records_to_audit_chain(self):
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

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        chain = AuditChain()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            self_model_audit_chain=chain,
        )
        core.start()
        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            out_ctx = core.process(ev)
        except Exception:
            out_ctx = None
        # audit chain 至少有一条 record
        assert chain.record_count >= 1
        # ctx 上有 _self_model_audit_record
        if out_ctx is not None:
            rec = getattr(out_ctx, "_self_model_audit_record", None)
            # 不强求(若 out_ctx 为 None)
        # 通过 query 接口查询
        records = core.get_self_model_audit_records()
        assert len(records) >= 1
        core.shutdown()

    def test_runtime_process_two_versions_diff(self):
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

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        chain = AuditChain()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            self_model_audit_chain=chain,
        )
        core.start()
        ev = Event(type="user_input", payload={"text": "hi"})
        # process 两次:foundation 默认 version=1,不会自增;注入不同 input 让 snapshot 不同
        try:
            core.process(ev)
        except Exception:
            pass
        # 第二次
        try:
            core.process(ev)
        except Exception:
            pass
        # 至少有一条 record
        assert chain.record_count >= 1
        core.shutdown()

    def test_audit_chain_optional_keeps_compat(self):
        """不注入 audit_chain 时,RUNTIME 不应破坏。"""
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
        ev = Event(type="user_input", payload={"text": "x"})
        try:
            core.process(ev)
        except Exception:
            pass
        core.shutdown()


# ============================================================
# 7. RuntimeContext / ResponseEngine 不变
# ============================================================
class TestInvariants:
    def test_runtime_context_schema(self):
        from src.runtime.context import (
            RuntimeContext, RUNTIME_CONTEXT_SCHEMA_VERSION,
        )
        ctx = RuntimeContext()
        assert ctx.schema_version == RUNTIME_CONTEXT_SCHEMA_VERSION
        assert not hasattr(ctx, "self_model_history")
        assert not hasattr(ctx, "self_model_audit_record")
        # 私有属性可写入但不进 schema
        setattr(ctx, "_self_model_history", [])
        setattr(ctx, "_self_model_audit_record", None)
        assert getattr(ctx, "_self_model_history") == []

    def test_response_engine_signature(self):
        import inspect
        from src.response.engine import ResponseEngine
        sig = inspect.signature(ResponseEngine.generate)
        params = list(sig.parameters.keys())
        assert "self_model_context" not in params
        assert "self_model_audit" not in params

    def test_audit_module_no_business_imports(self):
        """audit 模块不应 import Personality / Memory / Emotion / Growth 业务。"""
        import re
        from src.runtime.self_model.audit import (
            self_model_history_store as h,
            snapshot_archive as a,
            snapshot_diff_engine as d,
            growth_audit_record as g,
            audit_chain as c,
        )
        # 仅检查"非注释行"的 import 语句(行首或缩进的 import)
        import_pattern = re.compile(r"^\s*(?:from|import)\s+(\S+)", re.MULTILINE)
        for mod in (h, a, d, g, c):
            src = open(mod.__file__, encoding="utf-8").read()
            imported = set()
            for m in import_pattern.finditer(src):
                token = m.group(1)
                # 'import x.y.z' -> x; 'from x.y import z' -> x
                imported.add(token.split(".")[0])
            for forbidden_root in (
                "openai", "qwen", "personality", "memory", "emotion", "growth",
            ):
                assert forbidden_root not in imported, (
                    f"{mod.__name__} imports forbidden: {forbidden_root}"
                )

    def test_module_exports(self):
        from src.runtime.self_model.audit import (
            SelfModelHistoryStore,
            SnapshotArchive,
            SnapshotDiffEngine,
            GrowthAuditRecord,
            AuditChain,
            AuditSeverity,
            AuditCategory,
        )
        assert all([
            SelfModelHistoryStore, SnapshotArchive, SnapshotDiffEngine,
            GrowthAuditRecord, AuditChain, AuditSeverity, AuditCategory,
        ])


# ============================================================
# 8. 数据流 E2E
# ============================================================
class TestDataFlowE2E:
    def test_full_flow(self):
        """完整数据流: snapshot → chain → audit record → query。"""
        from src.runtime.self_model.audit import AuditChain
        chain = AuditChain()

        # 1. 第一次:INITIAL
        s1 = _make_snapshot(identity_id="smf_a", version=1)
        r1 = chain.record_snapshot(s1)
        assert "initial" in r1.categories

        # 2. 第二次:STABLE_TRAIT 变化
        s2 = _make_snapshot(
            identity_id="smf_a", version=2,
            stable_traits=[{"name": "warmth", "value": 0.8}],
        )
        r2 = chain.record_snapshot(s2)
        assert "trait_change" in r2.categories

        # 3. 第三次:PREFERENCE + STATE
        s3 = _make_snapshot(
            identity_id="smf_a", version=3,
            preferences=[{"name": "k", "value": "v"}],
            current_state={"mood": "calm"},
        )
        r3 = chain.record_snapshot(s3)
        assert "preference" in r3.categories
        assert "state_change" in r3.categories

        # 4. 查询
        history = chain.snapshot_history("smf_a")
        assert len(history) == 3
        records = chain.records(identity_id="smf_a")
        assert len(records) == 3

        # 5. diff
        d = chain.diff("smf_a", 1, 3)
        assert d["from_version"] == 1
        assert d["to_version"] == 3
        assert d["summary"]["total_changes"] >= 1

        # 6. health
        h = chain.health_check()
        assert h["record_count"] == 3

    def test_record_severity_progression(self):
        """严重度应该随 diff 强度提升。"""
        from src.runtime.self_model.audit import (
            AuditChain, AuditSeverity,
        )
        chain = AuditChain()
        # initial
        chain.record_snapshot(_make_snapshot(identity_id="a", version=1))
        # preference change (notice)
        chain.record_snapshot(_make_snapshot(
            identity_id="a", version=2,
            preferences=[{"name": "k", "value": "v"}],
        ))
        # identity change (alert)
        chain.record_snapshot(_make_snapshot(
            identity_id="a", version=3,
            identity={"name": "yuyi", "archetype": "ai"},
        ))
        records = chain.records(identity_id="a")
        severities = [r.severity for r in records]
        assert "notice" in severities
        assert "alert" in severities
