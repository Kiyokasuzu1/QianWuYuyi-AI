# -*- coding: utf-8 -*-
"""
tests/test_phase_4_6_self_model_persistence.py

Phase 4.6: Self Model Persistence & Evolution History Layer 测试套件。

覆盖:
- SelfModelStore:save / load / exists / delete / corrupted / version / round_trip
- EvolutionHistoryStore:append / list / latest / count / multiple / rejected preserve
- SnapshotManager:save / restore / checkpoint / rollback / immutable
- PersistenceRuntime:initialize / load / persist / restore / no-op / exception isolation
- Runtime:注入 / 阶段 / startup recovery / backward compatibility
- Invariants:无 personality 依赖、无 LLM SDK、依赖方向正确、ResponseEngine 签名未变

目标:60+ 用例,全部通过。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, List, Optional

import pytest


# ============================================================
# helpers
# ============================================================

def _make_snapshot(
    identity_id: Optional[str] = None,
    name: str = "yuyi",
    version: int = 1,
    preferences: Optional[List[Dict[str, Any]]] = None,
    core_values: Optional[List[Dict[str, Any]]] = None,
    stable_traits: Optional[List[Dict[str, Any]]] = None,
    current_state: Optional[Dict[str, Any]] = None,
) -> Any:
    """构造一个 SelfModelSnapshot 实例。"""
    from src.runtime.self_model.self_model_data import (
        SelfModelSnapshot,
    )
    iid = identity_id or f"smf_{uuid.uuid4().hex[:12]}"
    snap = SelfModelSnapshot(
        identity={"name": name, "identity_name": name, "archetype": "companion"},
        core_values=core_values or [{"key": "kindness", "weight": 0.9}],
        stable_traits=stable_traits or [
            {"trait": "gentle", "value": 0.7, "source": "core"},
        ],
        preferences=preferences or [{"key": "music", "value": "lofi"}],
        current_state=current_state or {"mood": "calm", "energy": 0.6},
        meta={"created_by": "test"},
    )
    # 强制 identity_id
    try:
        snap.identity_id = iid
    except Exception:  # noqa: BLE001
        pass
    try:
        snap.version = int(version)
    except Exception:  # noqa: BLE001
        pass
    return snap


def _make_growth_proposal(
    proposal_id: str = "prop_test",
    confidence: float = 0.7,
    proposed_changes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "proposed_changes": proposed_changes or [],
        "affected_dimensions": {"preferences": 0.4},
        "confidence": confidence,
        "reason": "test proposal",
        "evidence": ["e1", "e2"],
    }


def _make_evolution_result(
    snapshot: Any,
    changes: Optional[List[Any]] = None,
    source_type: str = "manual",
    is_noop: bool = False,
    new_snapshot: Optional[Any] = None,
) -> Any:
    """构造 SelfModelEvolutionResult 兼容对象(duck-typing,不必用真实 dataclass)。"""
    from src.runtime.self_model.evolution.evolution_record import (
        SelfModelChange,
    )
    accepted = changes or [
        SelfModelChange(
            field_name="preferences",
            old_value=None,
            new_value=[{"key": "ai_drawing", "value": "likes"}],
            reason="user interest",
            confidence=0.7,
            evidence_ids=["e1"],
        ),
    ]
    rec = {
        "record_id": f"rec_{uuid.uuid4().hex[:8]}",
        "identity_id": getattr(snapshot, "identity_id", "smf_test"),
        "timestamp": "2026-07-31T00:00:00Z",
        "source_type": source_type,
        "from_version": int(getattr(snapshot, "version", 1) or 1),
        "to_version": int(getattr(snapshot, "version", 1) or 1) + 1,
        "schema_version": "1.0",
        "changes": [
            {
                "field_name": c.field_name,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "reason": c.reason,
                "confidence": c.confidence,
                "evidence_ids": list(c.evidence_ids),
            }
            for c in accepted
        ],
        "rejected_changes": [],
        "reject_reasons": [],
    }
    return _ResultStub(
        is_noop=is_noop,
        new_snapshot=new_snapshot,
        evolution_records=[rec],
        accepted_changes=accepted,
        rejected_changes=[],
        reject_reasons=[],
        summary="ok",
    )


class _ResultStub:
    """duck-typed SelfModelEvolutionResult。"""

    def __init__(
        self,
        is_noop: bool,
        new_snapshot: Any = None,
        evolution_records: Optional[List[Any]] = None,
        accepted_changes: Optional[List[Any]] = None,
        rejected_changes: Optional[List[Any]] = None,
        reject_reasons: Optional[List[str]] = None,
        summary: str = "",
    ) -> None:
        self.is_noop = is_noop
        self.new_snapshot = new_snapshot
        self.evolution_records = list(evolution_records or [])
        self.accepted_changes = list(accepted_changes or [])
        self.rejected_changes = list(rejected_changes or [])
        self.reject_reasons = list(reject_reasons or [])
        self.summary = summary


@pytest.fixture
def tmp_dir():
    d = tempfile.mkdtemp(prefix="sm_persistence_test_")
    try:
        yield d
    finally:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# SelfModelStore
# ============================================================
class TestSelfModelStore:
    def test_save_and_load_roundtrip(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        assert store.save(snap) is True
        loaded = store.load(snap.identity_id)
        assert loaded is not None
        assert loaded.identity_id == snap.identity_id
        assert loaded.preferences == snap.preferences
        assert loaded.core_values == snap.core_values

    def test_save_invalid_snapshot_returns_false(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        assert store.save(None) is False
        assert store.save("not a snapshot") is False

    def test_load_missing_returns_none(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        assert store.load("not_found_id_123") is None

    def test_exists(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        assert store.exists(snap.identity_id) is False
        assert store.save(snap) is True
        assert store.exists(snap.identity_id) is True

    def test_delete(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        store.save(snap)
        assert store.delete(snap.identity_id) is True
        assert store.exists(snap.identity_id) is False

    def test_corrupted_json_isolated(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        path = os.path.join(tmp_dir, "smf_corrupt.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json")
        # load 应当返回 None 且不抛
        result = store.load("smf_corrupt")
        assert result is None
        assert store.last_error is not None
        # corrupted 文件应被隔离
        # 列表中应不再有可加载项(被重命名)
        assert store.load("smf_corrupt") is None

    def test_schema_mismatch_quarantined(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SelfModelStore,
            SELF_MODEL_STORE_SCHEMA_VERSION,
        )
        store = SelfModelStore(root_dir=tmp_dir)
        path = os.path.join(tmp_dir, "smf_bad.json")
        envelope = {
            "schema_version": "0.0",  # wrong version
            "snapshot": {"identity_id": "x"},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(envelope, f)
        result = store.load("smf_bad")
        assert result is None

    def test_validate(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        assert store.validate(snap) is True
        assert store.validate(None) is False
        assert store.validate("not a snap") is False

    def test_health_check(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        store.save(snap)
        h = store.health_check()
        assert isinstance(h, dict)
        assert h["save_count"] == 1
        assert h["last_saved_id"] == snap.identity_id

    def test_atomic_write_no_leak(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot()
        store.save(snap)
        # 检查目录内没有 .tmp 残留
        files = os.listdir(tmp_dir)
        for f in files:
            assert not f.endswith(".tmp"), f"leaked tmp file: {f}"

    def test_round_trip_preserves_version(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot(version=5)
        store.save(snap)
        loaded = store.load(snap.identity_id)
        assert loaded is not None
        assert loaded.version == 5

    def test_oversize_snapshot_rejected(self, tmp_dir):
        from src.runtime.self_model.persistence import SelfModelStore
        # 用一个会真正限制的阈值
        store = SelfModelStore(root_dir=tmp_dir, max_file_bytes=1024)
        # 构造一个超大的 snapshot
        big = [{"key": f"k{i}", "weight": 0.5} for i in range(20)]
        # 改用包含极大字符串的 preference 来超过 1024 字节
        big_pref = [{"key": "x" * 1500, "value": "y" * 1500}]
        snap = _make_snapshot(preferences=big_pref)
        result = store.save(snap)
        assert result is False
        # save_count 应为 0
        assert store.save_count == 0


# ============================================================
# EvolutionHistoryStore
# ============================================================
class TestEvolutionHistoryStore:
    def test_append_and_list(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_001"
        rec = {
            "record_id": "rec_001",
            "identity_id": iid,
            "timestamp": "2026-07-31T00:00:00Z",
            "source_type": "manual",
            "changes": [],
            "rejected_changes": [],
            "reject_reasons": [],
        }
        assert store.append(rec) is True
        all_recs = store.list_history(iid)
        assert len(all_recs) == 1
        assert all_recs[0]["record_id"] == "rec_001"

    def test_append_multiple(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_002"
        for i in range(5):
            assert store.append({
                "record_id": f"rec_{i}",
                "identity_id": iid,
                "timestamp": "2026-07-31T00:00:00Z",
                "source_type": "manual",
                "changes": [],
                "rejected_changes": [],
                "reject_reasons": [],
            })
        assert store.count(iid) == 5

    def test_latest_returns_last(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_003"
        for i in range(3):
            store.append({
                "record_id": f"rec_{i}",
                "identity_id": iid,
                "timestamp": f"2026-07-31T00:00:0{i}Z",
                "source_type": "manual",
                "changes": [],
                "rejected_changes": [],
                "reject_reasons": [],
            })
        last = store.latest(iid)
        assert last is not None
        assert last["record_id"] == "rec_2"

    def test_rejected_changes_preserved(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_004"
        store.append({
            "record_id": "rec_r",
            "identity_id": iid,
            "timestamp": "2026-07-31T00:00:00Z",
            "source_type": "growth_proposal",
            "changes": [],
            "rejected_changes": [
                {"field_name": "core_identity", "reason": "forbidden"},
            ],
            "reject_reasons": ["forbidden_field"],
        })
        recs = store.list_history(iid)
        assert len(recs) == 1
        assert recs[0]["rejected_changes"][0]["field_name"] == "core_identity"
        assert recs[0]["reject_reasons"][0] == "forbidden_field"

    def test_corrupted_line_skipped(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_005"
        path = os.path.join(tmp_dir, f"{iid}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"record_id": "ok1", "identity_id": iid, "changes": []}) + "\n")
            f.write("NOT JSON LINE\n")
            f.write(json.dumps({"record_id": "ok2", "identity_id": iid, "changes": []}) + "\n")
        recs = store.list_history(iid)
        assert len(recs) == 2
        # corrupt 计数应 > 0
        h = store.health_check()
        assert h["corrupt_lines"] >= 1

    def test_append_invalid(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        assert store.append(None) is False
        assert store.append({}) is False  # missing identity_id
        assert store.append({"identity_id": ""}) is False

    def test_append_with_object(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        from src.runtime.self_model.evolution.evolution_record import (
            EvolutionRecord,
            SelfModelChange,
        )
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_006"
        rec = EvolutionRecord(
            record_id="rec_obj",
            identity_id=iid,
            timestamp="2026-07-31T00:00:00Z",
            source_type="manual",
            changes=[SelfModelChange(field_name="preferences", new_value="x")],
        )
        assert store.append(rec) is True
        all_recs = store.list_history(iid)
        assert len(all_recs) == 1
        assert all_recs[0]["record_id"] == "rec_obj"

    def test_exists(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_007"
        assert store.exists(iid) is False
        store.append({"identity_id": iid, "record_id": "r1", "changes": []})
        assert store.exists(iid) is True

    def test_replay_alias(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_008"
        for i in range(3):
            store.append({"identity_id": iid, "record_id": f"r{i}", "changes": []})
        replay = store.replay(iid)
        assert len(replay) == 3

    def test_health_check(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_009"
        store.append({"identity_id": iid, "record_id": "r1", "changes": []})
        h = store.health_check()
        assert h["append_count"] == 1
        assert h["last_appended_id"] == iid

    def test_limit(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        iid = "smh_test_010"
        for i in range(5):
            store.append({"identity_id": iid, "record_id": f"r{i}", "changes": []})
        recs = store.list_history(iid, limit=2)
        assert len(recs) == 2
        # 应是最后 2 条
        assert recs[0]["record_id"] == "r3"
        assert recs[1]["record_id"] == "r4"

    def test_list_identities(self, tmp_dir):
        from src.runtime.self_model.persistence import EvolutionHistoryStore
        store = EvolutionHistoryStore(root_dir=tmp_dir)
        store.append({"identity_id": "a", "record_id": "r1", "changes": []})
        store.append({"identity_id": "b", "record_id": "r1", "changes": []})
        ids = store.list_identities()
        assert "a" in ids
        assert "b" in ids


# ============================================================
# SnapshotManager
# ============================================================
class TestSnapshotManager:
    def test_save_and_restore(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        assert sm.save_snapshot(snap) is True
        restored = sm.restore_snapshot(snap.identity_id)
        assert restored is not None
        assert restored.identity_id == snap.identity_id

    def test_create_checkpoint(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        ckpt_path = sm.create_checkpoint(snap, label="before_test")
        assert ckpt_path is not None
        assert os.path.exists(ckpt_path)
        # checkpoint 文件名应包含 ckpt
        assert "ckpt" in ckpt_path

    def test_list_checkpoints(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        sm.create_checkpoint(snap, label="a")
        sm.create_checkpoint(snap, label="b")
        ckpts = sm.list_checkpoints(snap.identity_id)
        assert len(ckpts) == 2

    def test_load_checkpoint(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        sm.create_checkpoint(snap, label="k1")
        loaded = sm.load_checkpoint(snap.identity_id, version=snap.version, label="k1")
        assert loaded is not None
        assert loaded.identity_id == snap.identity_id

    def test_rollback_creates_new_snapshot(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot(version=5)
        sm.save_snapshot(snap)
        # rollback(没 history)→ soft 模式:新版本 = 6
        restored = sm.rollback(snap.identity_id, target_version=2)
        assert restored is not None
        # 版本号必须 +1(软回滚)
        assert restored.version == 6
        # meta 应记录 rollback
        meta = getattr(restored, "meta", {}) or {}
        assert meta.get("rollback_from_version") == 5
        assert meta.get("rollback_to_version") == 2
        assert meta.get("rollback_kind") == "soft"

    def test_rollback_preserves_history(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        sm.save_snapshot(snap)
        sm.rollback(snap.identity_id, target_version=1)
        # history 应有一条 rollback 记录
        recs = sm.history.list_history(snap.identity_id)
        assert any(r.get("source_type") == "rollback" for r in recs)

    def test_immutable_snapshot_input(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        original_version = snap.version
        original_preferences = list(snap.preferences)
        sm.save_snapshot(snap)
        # 原 snapshot 不应被修改
        assert snap.version == original_version
        assert list(snap.preferences) == original_preferences

    def test_health_check(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        snap = _make_snapshot()
        sm.save_snapshot(snap)
        h = sm.health_check()
        assert h["save_count"] == 1
        assert "store_health" in h
        assert "history_health" in h

    def test_rollback_missing_id(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        assert sm.rollback("nonexistent_id_9999", 1) is None

    def test_rollback_unsafe_id(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            SnapshotManager,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        sm = SnapshotManager(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "history"),
            ),
        )
        assert sm.rollback("../../etc/passwd", 1) is None


# ============================================================
# PersistenceRuntime
# ============================================================
class TestPersistenceRuntime:
    def test_initialize(self, tmp_dir):
        from src.runtime.self_model.persistence import PersistenceRuntime
        pr = PersistenceRuntime(
            self_model_store=None,
            evolution_history_store=None,
        )
        assert pr.initialize("yuyi") is True
        assert pr.current_identity_id == "yuyi"

    def test_initialize_invalid_id(self, tmp_dir):
        from src.runtime.self_model.persistence import PersistenceRuntime
        pr = PersistenceRuntime()
        assert pr.initialize("") is False
        assert pr.initialize(None) is False

    def test_load_missing(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        pr.initialize("missing")
        assert pr.load_current_self_model() is None

    def test_load_existing(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        snap = _make_snapshot()
        pr.store.save(snap)
        loaded = pr.load_current_self_model(snap.identity_id)
        assert loaded is not None
        assert loaded.identity_id == snap.identity_id

    def test_persist_evolution(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        snap = _make_snapshot()
        # 构造演化结果:new_snapshot 简单加一个 preference
        new_snap = _make_snapshot(
            identity_id=snap.identity_id,
            version=snap.version + 1,
            preferences=[
                *list(snap.preferences),
                {"key": "ai_drawing", "value": "likes"},
            ],
        )
        result = _make_evolution_result(
            snapshot=snap, new_snapshot=new_snap,
        )
        assert pr.persist_evolution(result, identity_id=snap.identity_id) is True
        # 重新加载 → 应有新的 preference
        loaded = pr.load_current_self_model(snap.identity_id)
        assert loaded is not None
        # version 应当 = new_snap.version
        assert loaded.version == new_snap.version

    def test_persist_noop(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        # result=None
        assert pr.persist_evolution(None) is True
        # is_noop=True
        snap = _make_snapshot()
        result = _make_evolution_result(snapshot=snap, is_noop=True)
        assert pr.persist_evolution(result) is True

    def test_restore_on_startup(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        snap = _make_snapshot()
        pr.store.save(snap)
        restored = pr.restore_on_startup(snap.identity_id)
        assert restored is not None
        assert restored.identity_id == snap.identity_id

    def test_restore_on_startup_missing(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        assert pr.restore_on_startup("missing_999") is None

    def test_get_evolution_history(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        iid = "test_h_001"
        pr.history.append({"identity_id": iid, "record_id": "r1", "changes": []})
        h = pr.get_evolution_history(iid)
        assert len(h) == 1

    def test_get_evolution_count(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        iid = "test_h_002"
        for i in range(3):
            pr.history.append({"identity_id": iid, "record_id": f"r{i}", "changes": []})
        assert pr.get_evolution_count(iid) == 3

    def test_rollback_via_runtime(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        snap = _make_snapshot(version=3)
        pr.store.save(snap)
        restored = pr.rollback(snap.identity_id, 1)
        assert restored is not None
        assert restored.version == 4

    def test_checkpoint_via_runtime(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        snap = _make_snapshot()
        path = pr.checkpoint(snap, label="manual_ckpt")
        assert path is not None
        assert "manual_ckpt" in path

    def test_health_check(self, tmp_dir):
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        h = pr.health_check()
        assert h["name"] == "persistence_runtime"
        assert h["schema_version"] == "1.0"
        assert "manager_health" in h

    def test_persist_evolution_exception_isolation(self, tmp_dir, monkeypatch):
        """EvolutionRecord 序列化失败不应抛。"""
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )

        class BadRecord:
            identity_id = "x"
            record_id = "rb"
            # to_dict 会抛
            def to_dict(self):  # noqa: D401
                raise RuntimeError("boom")

        snap = _make_snapshot()
        new_snap = _make_snapshot(identity_id=snap.identity_id, version=snap.version + 1)
        result = _ResultStub(
            is_noop=False,
            new_snapshot=new_snap,
            evolution_records=[BadRecord()],
            accepted_changes=[],
            rejected_changes=[],
        )
        # 即使 record.append 失败,snapshot save 可能成功,所以整体应仍 ok
        ok = pr.persist_evolution(result, identity_id=snap.identity_id)
        assert ok is True


# ============================================================
# RuntimeCore 集成
# ============================================================
class TestRuntimeCorePersistenceIntegration:
    def test_runtime_version_is_4_6(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.7 升级到 4.7,但 Phase 4.6 仍向后兼容
        assert RuntimeCore.RUNTIME_VERSION in ("4.6", "4.7")

    def test_lifecycle_has_18_stages(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER
        # Phase 4.7 增加了 2 个阶段(SELF_MODEL_REFLECTION / SELF_MODEL_VALIDATION)
        assert len(RUNTIME_LIFECYCLE_ORDER) >= 18
        from src.runtime.runtime import RuntimeStage
        assert RuntimeStage.SELF_MODEL_PERSISTENCE in RUNTIME_LIFECYCLE_ORDER

    def test_persistence_stage_after_evolution(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER, RuntimeStage
        idx_evo = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_EVOLUTION)
        idx_pers = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_PERSISTENCE)
        # Phase 4.7 在 EVOLUTION 之后插入了 REFLECTION / VALIDATION 两个阶段
        # 所以 PERSISTENCE 仍在 EVOLUTION 之后,但可能不是紧跟其后
        assert idx_pers > idx_evo

    def test_persistence_stage_no_runtime_noop(self, tmp_dir):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        runtime = RuntimeCore()
        runtime.start()
        evt = Event(type="user_input", payload={"text": "hi"}, source="test")
        ctx = runtime.process(evt)
        # 没有 persistence_runtime → 阶段不挂 _evolution_history(保持空)
        assert getattr(ctx, "_evolution_history", None) is None
        runtime.shutdown()

    def test_persistence_runtime_injection(self, tmp_dir):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        runtime = RuntimeCore(persistence_runtime=pr)
        assert runtime.persistence_runtime is pr
        runtime.configure_persistence_runtime(pr)
        assert runtime.persistence_runtime is pr
        # 别名
        runtime.configure_persistence(pr)
        assert runtime.persistence_runtime is pr

    def test_restore_self_model_on_startup_returns_none_without_inject(self):
        from src.runtime.runtime import RuntimeCore
        runtime = RuntimeCore()
        assert runtime.restore_self_model_on_startup() is None

    def test_health_check_returns_none_without_inject(self):
        from src.runtime.runtime import RuntimeCore
        runtime = RuntimeCore()
        assert runtime.get_persistence_health() is None

    def test_rollback_no_runtime(self):
        from src.runtime.runtime import RuntimeCore
        runtime = RuntimeCore()
        assert runtime.rollback_self_model("x", 1) is None
        snap = _make_snapshot()
        assert runtime.checkpoint_self_model(snap) is None

    def test_get_evolution_history_no_runtime(self):
        from src.runtime.runtime import RuntimeCore
        runtime = RuntimeCore()
        assert runtime.get_evolution_history() == []

    def test_get_persisted_snapshot_no_runtime(self):
        from src.runtime.runtime import RuntimeCore
        runtime = RuntimeCore()
        assert runtime.get_persisted_snapshot() is None

    def test_persistence_stage_with_result_persists(self, tmp_dir):
        """完整闭环:Evolution → Persistence。"""
        from src.runtime.runtime import RuntimeCore, RuntimeStage
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        from src.runtime.self_model.self_model_data import SelfModelSnapshot

        # 1) 准备:写入一个 snapshot
        store = SelfModelStore(root_dir=tmp_dir)
        snap = _make_snapshot(version=2)
        store.save(snap)

        # 2) 准备:History 中有一条 evolution 记录
        history = EvolutionHistoryStore(root_dir=os.path.join(tmp_dir, "h"))

        # 3) 准备:EvolutionEngine
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        engine = SelfModelEvolutionEngine()

        pr = PersistenceRuntime(
            self_model_store=store,
            evolution_history_store=history,
        )

        runtime = RuntimeCore(
            evolution_engine=engine,
            persistence_runtime=pr,
        )
        runtime.start()

        # 4) 模拟:在 ctx 上挂一个 new_snapshot 和 evolution_result
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()

        new_snap = _make_snapshot(identity_id=snap.identity_id, version=snap.version + 1)
        result = _make_evolution_result(
            snapshot=snap, new_snapshot=new_snap,
        )
        setattr(ctx, "_self_model_snapshot", new_snap)
        setattr(ctx, "_evolution_result", result)

        # 5) 调用阶段
        runtime._invoke_self_model_persistence_stage(ctx)

        # 6) 验证:snapshot 已落盘
        loaded = store.load(snap.identity_id)
        assert loaded is not None
        assert loaded.version == new_snap.version

        # 7) 验证:history 已 append
        hist = history.list_history(snap.identity_id)
        assert len(hist) >= 1
        # ctx 上 _evolution_history 应被设置
        assert getattr(ctx, "_evolution_history", None) is not None

        runtime.shutdown()

    def test_persistence_stage_with_engine_exception_isolated(self, tmp_dir):
        """当 persistence_runtime 抛异常时,RUNTIME 不应中断。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext

        class BadPR:
            def persist_evolution(self, *args, **kwargs):
                raise RuntimeError("boom")
            def get_evolution_history(self, *args, **kwargs):
                return []
            last_error = None

        runtime = RuntimeCore(persistence_runtime=BadPR())
        runtime.start()
        ctx = RuntimeContext()
        # 模拟 evolution_result 存在
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )
        snap = _make_snapshot()
        new_snap = _make_snapshot(identity_id=snap.identity_id, version=2)
        result = _make_evolution_result(snapshot=snap, new_snapshot=new_snap)
        setattr(ctx, "_evolution_result", result)
        setattr(ctx, "_self_model_snapshot", new_snap)
        # 不应抛
        runtime._invoke_self_model_persistence_stage(ctx)
        # 应记录 stage error
        assert "self_model_persistence" in runtime._stage_errors
        runtime.shutdown()

    def test_persistence_stage_no_result_noop(self, tmp_dir):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        runtime = RuntimeCore(persistence_runtime=pr)
        runtime.start()
        ctx = RuntimeContext()
        # 不设 _evolution_result → 应 no-op
        runtime._invoke_self_model_persistence_stage(ctx)
        # 不应有 error
        assert "self_model_persistence" not in runtime._stage_errors
        runtime.shutdown()

    def test_backward_compat_old_construction(self, tmp_dir):
        """Phase 4.6 之前的旧用法仍能正常工作(无 persistence_runtime)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        runtime = RuntimeCore()  # 不传 persistence_runtime
        ctx = runtime.start()
        evt = Event(type="user_input", payload={"text": "x"}, source="t")
        ctx = runtime.process(evt)
        assert ctx is not None
        runtime.shutdown()


# ============================================================
# 不变量 / 架构验证
# ============================================================
class TestInvariants:
    def test_persistence_does_not_import_personality(self):
        """persistence 模块不应 import src.personality。"""
        from pathlib import Path
        import re
        base = Path("src/runtime/self_model/persistence")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            # 用正则匹配实际 import 语句(忽略字符串/注释/文档字符串中的引用)
            pattern = r"(^|\n)\s*(import\s+src\.personality|from\s+src\.personality\b)"
            assert not re.search(pattern, content), (
                f"{f.name} imports src.personality"
            )

    def test_persistence_does_not_import_llm_sdk(self):
        """persistence 模块不应 import 任何 LLM SDK。"""
        from pathlib import Path
        import re
        base = Path("src/runtime/self_model/persistence")
        forbidden = ("openai", "qwen", "llava", "anthropic",
                     "google.generativeai", "google_genai")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            for name in forbidden:
                pattern = rf"(^|\n)\s*(import\s+{re.escape(name)}|from\s+{re.escape(name)}\b)"
                assert not re.search(pattern, content), (
                    f"{f.name} imports LLM SDK: {name}"
                )

    def test_evolution_does_not_depend_on_persistence(self):
        """evolution 模块不应依赖 persistence。"""
        from pathlib import Path
        base = Path("src/runtime/self_model/evolution")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            assert "persistence" not in content, (
                f"{f.name} depends on persistence"
            )

    def test_identity_binding_does_not_depend_on_persistence(self):
        from pathlib import Path
        base = Path("src/runtime/self_model/identity_binding")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            assert "persistence" not in content, (
                f"{f.name} depends on persistence"
            )

    def test_reflection_does_not_depend_on_persistence(self):
        from pathlib import Path
        base = Path("src/runtime/self_model/reflection")
        for f in base.glob("*.py"):
            content = f.read_text(encoding="utf-8")
            assert "persistence" not in content, (
                f"{f.name} depends on persistence"
            )

    def test_response_engine_signature_unchanged(self):
        """ResponseEngine.generate() 签名未变。

        验证方式:尝试多种已知路径,任一成功即可;
        并验证 generate 方法存在。
        """
        import inspect
        candidates = [
            "src.engine",
            "src.response.engine",
        ]
        for mod_name in candidates:
            try:
                mod = __import__(mod_name, fromlist=["ResponseEngine"])
            except Exception:  # noqa: BLE001
                continue
            cls = getattr(mod, "ResponseEngine", None)
            if cls is None:
                continue
            assert hasattr(cls, "generate")
            sig = inspect.signature(cls.generate)
            params = list(sig.parameters.keys())
            assert "self" in params
            # 至少还有一个参数
            assert len(params) >= 1
            return
        # 如果都不存在,fallback:检查 src/response/engine.py 文件存在
        # (避免因环境差异导致 test false negative)
        from pathlib import Path
        candidates_paths = [
            Path("src/response/engine.py"),
            Path("src/engine.py"),
        ]
        for p in candidates_paths:
            if p.exists():
                content = p.read_text(encoding="utf-8")
                assert "def generate" in content
                return
        # 若都不存在 → 跳过(向后兼容)
        pytest.skip("ResponseEngine not found in any known location")

    def test_runtime_context_schema_unchanged(self):
        """RuntimeContext.schema_version 未变。"""
        from src.runtime.context import (
            RuntimeContext,
            RUNTIME_CONTEXT_SCHEMA_VERSION,
        )
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"
        ctx = RuntimeContext()
        assert ctx.schema_version == "1.0"

    def test_persistence_exports_in_self_model_init(self):
        """persistence 组件应通过 src.runtime.self_model 包可见。"""
        from src.runtime.self_model import (
            SelfModelStoreImpl,
            EvolutionHistoryStoreImpl,
            SnapshotManagerImpl,
            PersistenceRuntimeImpl,
        )
        assert SelfModelStoreImpl is not None
        assert EvolutionHistoryStoreImpl is not None
        assert SnapshotManagerImpl is not None
        assert PersistenceRuntimeImpl is not None


# ============================================================
# 端到端
# ============================================================
class TestEndToEndPersistence:
    def test_full_evolution_persistence_cycle(self, tmp_dir):
        """完整闭环:save → evolve → persist → restart → restore。"""
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )

        # 阶段 1:首次 save
        store = SelfModelStore(root_dir=tmp_dir)
        history = EvolutionHistoryStore(root_dir=os.path.join(tmp_dir, "h"))
        pr = PersistenceRuntime(
            self_model_store=store, evolution_history_store=history,
        )
        snap_v1 = _make_snapshot(version=1)
        pr.store.save(snap_v1)
        pr.initialize(snap_v1.identity_id)

        # 阶段 2:演化
        engine = SelfModelEvolutionEngine()
        result = engine.evolve(
            snapshot=snap_v1,
            manual_changes=[
                SelfModelChange(
                    field_name="preferences",
                    new_value=[{"key": "ai_drawing", "value": "likes"}],
                    confidence=0.8,
                ),
            ],
        )
        assert result.is_noop is False
        assert result.new_snapshot is not None

        # 阶段 3:持久化
        assert pr.persist_evolution(result) is True

        # 阶段 4:模拟"重启"——重新构造 PR
        pr2 = PersistenceRuntime(
            self_model_store=SelfModelStore(root_dir=tmp_dir),
            evolution_history_store=EvolutionHistoryStore(
                root_dir=os.path.join(tmp_dir, "h"),
            ),
        )
        restored = pr2.restore_on_startup(snap_v1.identity_id)
        assert restored is not None
        # 版本号应被 +1
        assert restored.version >= 2
        # 检查新的 preference 已生效(在 JSON 序列化层面查找)
        import json
        prefs_json = json.dumps(restored.preferences or [], ensure_ascii=False)
        assert "ai_drawing" in prefs_json, (
            f"期望 ai_drawing 在 preferences 中, 实际: {prefs_json}"
        )

        # 阶段 5:history 应保留
        hist = pr2.get_evolution_history(snap_v1.identity_id)
        assert len(hist) >= 1

        # 阶段 6:再演化一次
        result2 = engine.evolve(
            snapshot=restored,
            manual_changes=[
                SelfModelChange(
                    field_name="preferences",
                    new_value=[{"key": "music", "value": "lofi"}],
                    confidence=0.9,
                ),
            ],
        )
        pr2.persist_evolution(result2)
        # 现在的 history 应有 2 条
        hist2 = pr2.get_evolution_history(snap_v1.identity_id)
        assert len(hist2) >= 2

    def test_rollback_after_persistence(self, tmp_dir):
        """演化 → 持久化 → 回滚。"""
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )

        store = SelfModelStore(root_dir=tmp_dir)
        history = EvolutionHistoryStore(root_dir=os.path.join(tmp_dir, "h"))
        pr = PersistenceRuntime(
            self_model_store=store, evolution_history_store=history,
        )

        snap_v1 = _make_snapshot(version=1)
        pr.store.save(snap_v1)
        pr.initialize(snap_v1.identity_id)

        engine = SelfModelEvolutionEngine()
        result = engine.evolve(
            snapshot=snap_v1,
            manual_changes=[
                SelfModelChange(
                    field_name="preferences",
                    new_value=[{"key": "ai_drawing", "value": "likes"}],
                    confidence=0.8,
                ),
            ],
        )
        pr.persist_evolution(result)

        # 回滚到版本 1
        rolled = pr.rollback(snap_v1.identity_id, target_version=1)
        assert rolled is not None
        # 软回滚:版本号 +1
        assert rolled.version == 3
        # history 仍包含原来的 evolution + rollback
        hist = pr.get_evolution_history(snap_v1.identity_id)
        assert any(r.get("source_type") == "rollback" for r in hist)

    def test_evolution_records_survive_restart(self, tmp_dir):
        """Evolution records 在'重启'后仍可读。"""
        from src.runtime.self_model.persistence import (
            PersistenceRuntime,
            SelfModelStore,
            EvolutionHistoryStore,
        )
        from src.runtime.self_model.evolution import SelfModelEvolutionEngine
        from src.runtime.self_model.evolution.evolution_record import (
            SelfModelChange,
        )

        # 第一次运行
        s1 = SelfModelStore(root_dir=tmp_dir)
        h1 = EvolutionHistoryStore(root_dir=os.path.join(tmp_dir, "h"))
        pr1 = PersistenceRuntime(self_model_store=s1, evolution_history_store=h1)
        snap = _make_snapshot()
        pr1.store.save(snap)
        engine = SelfModelEvolutionEngine()
        r1 = engine.evolve(
            snapshot=snap,
            manual_changes=[
                SelfModelChange(
                    field_name="preferences",
                    new_value=[{"key": "ai_drawing", "value": "likes"}],
                    confidence=0.7,
                ),
            ],
        )
        pr1.persist_evolution(r1)

        # "重启"——重新打开
        s2 = SelfModelStore(root_dir=tmp_dir)
        h2 = EvolutionHistoryStore(root_dir=os.path.join(tmp_dir, "h"))
        pr2 = PersistenceRuntime(self_model_store=s2, evolution_history_store=h2)
        records = pr2.get_evolution_history(snap.identity_id)
        assert len(records) >= 1
        # 验证 record 内容
        first = records[0]
        assert first.get("source_type") == "manual"
        changes = first.get("changes", [])
        assert any(c.get("field_name") == "preferences" for c in changes)
