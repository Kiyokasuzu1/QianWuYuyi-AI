"""
Phase 6.2: SelfModel Persistence 测试

验证：
- save/load 三个 store
- restart cycle（save → 重新构造 → load → 数据恢复）
- backup / clear
- export snapshot
- SelfModelAdapter 集成 (attach_persistence / save_state / load_state)
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_belief import SelfBelief, SelfBeliefStore
from src.personality.self_history import SelfHistory, SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
from src.personality.self_model_persistence import (
    SelfModelPersistence,
    BELIEFS_FILENAME,
    HISTORY_FILENAME,
    REFLECTION_FILENAME,
    META_FILENAME,
)
from src.personality.self_model_adapter import SelfModelAdapter


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="self_model_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================
# 1. 基本 IO
# ============================================================

class TestPersistenceBasic:
    def test_01_init_creates_dir(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        assert os.path.isdir(tmp_data_dir)
        assert p.data_dir.name == os.path.basename(tmp_data_dir)

    def test_02_save_and_load_beliefs(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfBeliefStore()
        store.add(SelfBelief(domain="value", content="x1", confidence=0.5, sources=["t"]))
        store.add(SelfBelief(domain="value", content="x2", confidence=0.6, sources=["t"]))
        ok = p.save_beliefs(store)
        assert ok is True
        loaded = p.load_beliefs()
        assert len(loaded) == 2
        contents = {b["content"] for b in loaded}
        assert "x1" in contents
        assert "x2" in contents

    def test_03_save_and_load_history(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        h = SelfHistory()
        for i in range(5):
            h.append(SelfHistoryEvent(
                event_type=SelfHistoryEventType.PCR_APPLIED,
                source_type="test",
                source_id=str(i),
                summary=f"e_{i}",
            ))
        assert p.save_history(h) is True
        loaded = p.load_history()
        assert len(loaded) == 5

    def test_04_save_and_load_reflections(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfReflectionStore()
        for i in range(3):
            store.append(SelfReflectionNote(
                trigger_source="manual",
                reflection_type="identity",
                content=f"r_{i}",
                confidence=0.5,
            ))
        assert p.save_reflections(store) is True
        loaded = p.load_reflections()
        assert len(loaded) == 3

    def test_05_load_nonexistent_returns_empty(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        assert p.load_beliefs() == []
        assert p.load_history() == []
        assert p.load_reflections() == []


# ============================================================
# 2. 文件格式
# ============================================================

class TestFileFormat:
    def test_01_beliefs_file_is_jsonl(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfBeliefStore()
        store.add(SelfBelief(domain="value", content="abc", confidence=0.5))
        p.save_beliefs(store)
        path = os.path.join(tmp_data_dir, BELIEFS_FILENAME)
        assert os.path.exists(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["content"] == "abc"

    def test_02_meta_file_updated(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfBeliefStore()
        store.add(SelfBelief(domain="value", content="x", confidence=0.5))
        p.save_beliefs(store)
        meta = p.get_meta()
        assert "beliefs" in meta
        assert meta["beliefs"]["count"] == 1


# ============================================================
# 3. 备份
# ============================================================

class TestBackup:
    def test_01_backup_creates_copy(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfBeliefStore()
        store.add(SelfBelief(domain="value", content="bk", confidence=0.5))
        p.save_beliefs(store)
        backup_dir = p.backup(suffix="test")
        assert backup_dir is not None
        assert os.path.isdir(backup_dir)
        # 备份目录应包含 beliefs.jsonl
        assert os.path.exists(os.path.join(backup_dir, BELIEFS_FILENAME))

    def test_02_clear_removes_files(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        store = SelfBeliefStore()
        store.add(SelfBelief(domain="value", content="c", confidence=0.5))
        p.save_beliefs(store)
        assert p.clear() is True
        assert p.load_beliefs() == []


# ============================================================
# 4. SelfModelAdapter 集成
# ============================================================

class TestAdapterPersistence:
    def test_01_attach_persistence(self, tmp_data_dir):
        adapter = SelfModelAdapter()
        pers = SelfModelPersistence(tmp_data_dir)
        adapter.attach_persistence(pers)
        assert adapter.has_persistence() is True

    def test_02_save_state_persists(self, tmp_data_dir):
        adapter = SelfModelAdapter()
        adapter.attach_persistence(SelfModelPersistence(tmp_data_dir))
        adapter._beliefs.add(SelfBelief(domain="value", content="persist_test", confidence=0.6))
        adapter._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="test",
            source_id="x",
            summary="persist test",
        ))
        result = adapter.save_state()
        assert result["beliefs"] is True
        assert result["history"] is True
        # 文件存在
        assert os.path.exists(os.path.join(tmp_data_dir, BELIEFS_FILENAME))
        assert os.path.exists(os.path.join(tmp_data_dir, HISTORY_FILENAME))

    def test_03_restart_load_state(self, tmp_data_dir):
        # 第一次：保存
        adapter1 = SelfModelAdapter()
        adapter1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        adapter1._beliefs.add(SelfBelief(domain="value", content="restart_test", confidence=0.7))
        adapter1.save_state()
        # 第二次：构造新 adapter，加载
        adapter2 = SelfModelAdapter()
        adapter2.attach_persistence(SelfModelPersistence(tmp_data_dir))
        counts = adapter2.load_state()
        assert counts["beliefs"] == 1
        beliefs = adapter2.get_beliefs().all()
        contents = [b.content for b in beliefs]
        assert "restart_test" in contents

    def test_04_no_persistence_noop(self):
        adapter = SelfModelAdapter()
        assert adapter.has_persistence() is False
        result = adapter.save_state()
        assert result["beliefs"] is False
        counts = adapter.load_state()
        assert counts == {"beliefs": 0, "history": 0, "reflections": 0}

    def test_05_get_persistence_stats(self, tmp_data_dir):
        adapter = SelfModelAdapter()
        pers = SelfModelPersistence(tmp_data_dir)
        adapter.attach_persistence(pers)
        stats = adapter.get_persistence_stats()
        assert stats["attached"] is True
        assert "files" in stats


# ============================================================
# 5. export snapshot
# ============================================================

class TestSnapshotExport:
    def test_01_export_snapshot(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        b = SelfBeliefStore()
        b.add(SelfBelief(domain="value", content="snap1", confidence=0.6))
        h = SelfHistory()
        h.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="snap1",
        ))
        r = SelfReflectionStore()
        r.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="identity",
            content="r1", confidence=0.5,
        ))
        snap = p.export_snapshot(b, h, r, note="test")
        assert snap is not None
        assert len(snap["beliefs"]) == 1
        assert len(snap["history"]) == 1
        assert len(snap["reflections"]) == 1

    def test_02_write_snapshot_to_file(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        b = SelfBeliefStore()
        b.add(SelfBelief(domain="value", content="x", confidence=0.5))
        out_path = os.path.join(tmp_data_dir, "snap.json")
        ok = p.write_snapshot_to_file(b, SelfHistory(), SelfReflectionStore(), out_path, "t")
        assert ok is True
        assert os.path.exists(out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "beliefs" in data

    def test_03_restore_from_snapshot(self, tmp_data_dir):
        p = SelfModelPersistence(tmp_data_dir)
        b = SelfBeliefStore()
        b.add(SelfBelief(domain="value", content="restore1", confidence=0.6))
        h = SelfHistory()
        h.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="restore1",
        ))
        r = SelfReflectionStore()
        r.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="identity",
            content="r_restore", confidence=0.5,
        ))
        snap = p.export_snapshot(b, h, r)
        # 重新构造 adapter 并 restore
        adapter = SelfModelAdapter()
        adapter.attach_persistence(SelfModelPersistence(tmp_data_dir))
        counts = adapter.restore_from_snapshot(snap)
        assert counts["beliefs"] == 1
        assert counts["history"] == 1
        assert counts["reflections"] == 1
