# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_c/test_runtime_snapshot.py

Phase 5.0-C · Step 5: RuntimeSnapshot 独立模块单元测试。

覆盖:
- RuntimeSnapshot         数据类(默认构造、to_dict、from_dict、缺字段、多余字段、
                            schema 检查、boot 计数、copy、repr、mark_* 方法)
- RuntimeSnapshotStore    IO 层(不存在、正常读取、正常保存、损坏 JSON、schema 错误、
                            原子写、删除、health_check、并发)
- RuntimeSnapshotBuilder  业务编排(initial、state 合并、PR 合并、audit 合并、
                            source 推断、commit、异常隔离、并发)
- E2E                     跨实例 fresh→restore / 1000 次 commit 稳定性
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock


# ============================================================
# Fake PR / Audit(供 Builder 用)
# ============================================================
class FakePR:
    """最小 PersistenceRuntime 替身。"""

    def __init__(self, *, version: Optional[int] = 3, identity: Optional[str] = "yuyi_default",
                 evolution_count: int = 7) -> None:
        self.last_persisted_version = version
        self.current_identity_id = identity
        self._evolution_count = evolution_count

    def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
        return self._evolution_count


class FakeBrokenPR:
    """异常 PR(用于测试隔离)。"""

    @property
    def last_persisted_version(self) -> int:
        raise RuntimeError("pr broken")

    @property
    def current_identity_id(self) -> str:
        raise RuntimeError("pr broken")

    def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
        raise RuntimeError("pr broken")


class FakeAudit:
    """最小 RuntimeAuditLogger 替身。"""

    def __init__(self, write_count: int = 0, log_path: str = "data/fake_audit.jsonl") -> None:
        self.write_count = write_count
        self.log_path = log_path
        self.checkpoint_calls: List[Dict[str, Any]] = []

    def log_checkpoint(self, reason: str = "", state: Optional[Dict[str, Any]] = None) -> bool:
        self.checkpoint_calls.append({"reason": reason, "state": state or {}})
        self.write_count += 1
        return True


class FakeBrokenAudit:
    """异常 Audit。"""

    @property
    def write_count(self) -> int:
        return 42

    @property
    def log_path(self) -> str:
        return "data/broken.jsonl"

    def log_checkpoint(self, reason: str = "", state: Optional[Dict[str, Any]] = None) -> bool:
        raise RuntimeError("audit broken")


# ============================================================
# 1. RuntimeSnapshot 数据类
# ============================================================
class TestRuntimeSnapshot(unittest.TestCase):
    """RuntimeSnapshot 数据类测试。"""

    def test_construct_default(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshot,
            RUNTIME_SNAPSHOT_SCHEMA_VERSION,
            DEFAULT_IDENTITY_ID,
            SOURCE_INITIAL,
        )
        s = RuntimeSnapshot()
        self.assertEqual(s.schema_version, RUNTIME_SNAPSHOT_SCHEMA_VERSION)
        self.assertEqual(s.identity_id, DEFAULT_IDENTITY_ID)
        self.assertEqual(s.source, SOURCE_INITIAL)
        self.assertEqual(s.boot_count, 0)
        self.assertEqual(s.last_turn_count, 0)
        self.assertEqual(s.last_checkpoint_count, 0)
        self.assertEqual(s.last_boot_mode, "fresh")
        self.assertIsNone(s.last_started_at)
        self.assertIsNone(s.last_stopped_at)
        self.assertIsNone(s.last_self_model_version)
        self.assertIsNone(s.last_self_model_identity)
        self.assertIsNone(s.evolution_record_count)
        self.assertIsNone(s.last_audit_log_offset)
        self.assertIsNone(s.last_audit_log_path)
        self.assertIsNone(s.last_boot_at)
        self.assertNotEqual(s.created_at, "")
        self.assertNotEqual(s.updated_at, "")

    def test_construct_custom(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot, SOURCE_PERIODIC
        s = RuntimeSnapshot(identity_id="custom_id", source=SOURCE_PERIODIC, created_at="2026-01-01T00:00:00Z")
        self.assertEqual(s.identity_id, "custom_id")
        self.assertEqual(s.source, SOURCE_PERIODIC)
        self.assertEqual(s.created_at, "2026-01-01T00:00:00Z")
        self.assertEqual(s.updated_at, "2026-01-01T00:00:00Z")

    def test_to_dict_has_17_keys(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        d = s.to_dict()
        self.assertEqual(len(d), 17)
        # 字段集合严格匹配
        expected = {
            "schema_version", "identity_id", "created_at", "updated_at", "source",
            "last_turn_count", "last_checkpoint_count",
            "last_started_at", "last_stopped_at",
            "last_self_model_version", "last_self_model_identity",
            "evolution_record_count",
            "last_audit_log_offset", "last_audit_log_path",
            "boot_count", "last_boot_mode", "last_boot_at",
        }
        self.assertEqual(set(d.keys()), expected)

    def test_from_dict_roundtrip(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot(identity_id="abc")
        s.last_turn_count = 10
        s.last_self_model_version = 5
        s.last_audit_log_offset = 100
        s.boot_count = 3
        d = s.to_dict()
        s2 = RuntimeSnapshot.from_dict(d)
        self.assertEqual(s2.identity_id, "abc")
        self.assertEqual(s2.last_turn_count, 10)
        self.assertEqual(s2.last_self_model_version, 5)
        self.assertEqual(s2.last_audit_log_offset, 100)
        self.assertEqual(s2.boot_count, 3)

    def test_from_dict_missing_fields(self) -> None:
        """缺字段应使用默认值。"""
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot.from_dict({})
        self.assertIsNotNone(s.schema_version)
        self.assertEqual(s.boot_count, 0)
        self.assertEqual(s.last_turn_count, 0)
        self.assertIsNone(s.last_started_at)

    def test_from_dict_extra_fields_ignored(self) -> None:
        """多余字段应被静默忽略。"""
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        d = {
            "schema_version": "1.0",
            "identity_id": "x",
            "boot_count": 1,
            "unknown_field": "ignored",
            "another_unknown": {"nested": True},
        }
        s = RuntimeSnapshot.from_dict(d)
        self.assertEqual(s.identity_id, "x")
        self.assertEqual(s.boot_count, 1)

    def test_from_dict_invalid_types_safe(self) -> None:
        """类型错误应安全降级。"""
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        d = {
            "boot_count": "not a number",
            "last_turn_count": [1, 2, 3],
            "last_self_model_version": {"weird": "object"},
        }
        s = RuntimeSnapshot.from_dict(d)
        # 字符串数字会被 int() 转换
        self.assertEqual(s.boot_count, 0)  # "not a number" -> int 失败 -> default 0
        self.assertEqual(s.last_turn_count, 0)  # list -> int 失败 -> default 0
        # last_self_model_version: dict 非 None,被 _safe_int 转换
        # dict 不是数字,会失败,降为 None
        self.assertIsNone(s.last_self_model_version)

    def test_from_dict_non_dict_returns_default(self) -> None:
        """非 dict 输入应返回新实例。"""
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        for bad in [None, "string", 42, [1, 2], 3.14]:
            s = RuntimeSnapshot.from_dict(bad)
            self.assertIsInstance(s, RuntimeSnapshot)

    def test_is_schema_compatible_v1(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        self.assertTrue(s.is_schema_compatible())
        s.schema_version = "0.9"
        self.assertFalse(s.is_schema_compatible())

    def test_mark_boot_increments_count(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        self.assertEqual(s.boot_count, 0)
        n1 = s.mark_boot("fresh")
        self.assertEqual(n1, 1)
        self.assertEqual(s.last_boot_mode, "fresh")
        self.assertIsNotNone(s.last_boot_at)
        n2 = s.mark_boot("restore")
        self.assertEqual(n2, 2)
        self.assertEqual(s.last_boot_mode, "restore")

    def test_mark_boot_preserves_schema_and_created(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        original_schema = s.schema_version
        original_created = s.created_at
        s.mark_boot("fresh")
        self.assertEqual(s.schema_version, original_schema)
        self.assertEqual(s.created_at, original_created)

    def test_mark_stopped(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        s.mark_stopped("2026-01-01T00:00:00Z")
        self.assertEqual(s.last_stopped_at, "2026-01-01T00:00:00Z")
        self.assertNotEqual(s.updated_at, "")

    def test_touch(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot(created_at="2026-01-01T00:00:00Z")
        s.updated_at = "2026-01-01T00:00:00Z"
        time.sleep(0.01)
        s.touch()
        self.assertNotEqual(s.updated_at, "2026-01-01T00:00:00Z")

    def test_copy_independence(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        s.last_turn_count = 5
        s2 = s.copy()
        s2.last_turn_count = 99
        self.assertEqual(s.last_turn_count, 5)
        self.assertEqual(s2.last_turn_count, 99)

    def test_repr(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshot
        s = RuntimeSnapshot()
        r = repr(s)
        self.assertIn("RuntimeSnapshot", r)
        self.assertIn("yuyi_default", r)


# ============================================================
# 2. RuntimeSnapshotStore IO
# ============================================================
class TestRuntimeSnapshotStore(unittest.TestCase):
    """RuntimeSnapshotStore IO 层测试。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="snap_store_")
        self.path = os.path.join(self.tmp, "snap.json")

    def tearDown(self) -> None:
        try:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def test_load_not_exists(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        s = RuntimeSnapshotStore(path=self.path)
        result = s.load()
        self.assertIsNone(result)

    def test_save_and_load_roundtrip(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        snap = RuntimeSnapshot(identity_id="test_id")
        snap.last_turn_count = 42
        snap.boot_count = 7
        ok = s.save(snap)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.path))
        # load
        loaded = s.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.identity_id, "test_id")
        self.assertEqual(loaded.last_turn_count, 42)
        self.assertEqual(loaded.boot_count, 7)

    def test_load_corrupted_json(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("not a json {{{")
        s = RuntimeSnapshotStore(path=self.path)
        result = s.load()
        self.assertIsNone(result)
        self.assertIsNotNone(s.last_error)

    def test_load_schema_mismatch(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": "0.9", "identity_id": "old"}, f)
        s = RuntimeSnapshotStore(path=self.path)
        result = s.load()
        self.assertIsNone(result)
        self.assertIn("schema_mismatch", s.last_error or "")

    def test_load_envelope_not_dict(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("[1, 2, 3]")
        s = RuntimeSnapshotStore(path=self.path)
        result = s.load()
        self.assertIsNone(result)

    def test_save_invalid_type(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        s = RuntimeSnapshotStore(path=self.path)
        ok = s.save("not a snapshot")  # type: ignore[arg-type]
        self.assertFalse(ok)
        self.assertIn("invalid_snapshot_type", s.last_error or "")

    def test_atomic_write_via_tmp(self) -> None:
        """原子写:写完应留下 path,不应有 .tmp 残留。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        s.save(RuntimeSnapshot())
        self.assertTrue(os.path.exists(self.path))
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_atomic_write_overwrites_existing(self) -> None:
        """覆盖写:再次 save 应替换而非追加。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        snap1 = RuntimeSnapshot()
        snap1.identity_id = "first"
        s.save(snap1)
        snap2 = RuntimeSnapshot()
        snap2.identity_id = "second"
        s.save(snap2)
        loaded = s.load()
        self.assertEqual(loaded.identity_id, "second")

    def test_delete(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        s.save(RuntimeSnapshot())
        self.assertTrue(s.exists())
        ok = s.delete()
        self.assertTrue(ok)
        self.assertFalse(s.exists())

    def test_delete_not_exists(self) -> None:
        from src.orchestrator.runtime_snapshot import RuntimeSnapshotStore
        s = RuntimeSnapshotStore(path=self.path)
        ok = s.delete()
        self.assertTrue(ok)  # 不存在也算成功

    def test_auto_create_dir(self) -> None:
        """auto_create_dir=True 时应自动创建子目录。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        nested = os.path.join(self.tmp, "sub", "dir", "snap.json")
        s = RuntimeSnapshotStore(path=nested, auto_create_dir=True)
        ok = s.save(RuntimeSnapshot())
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(nested))

    def test_health_check(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        s.save(RuntimeSnapshot())
        s.load()
        h = s.health_check()
        self.assertIn("save_count", h)
        self.assertIn("load_count", h)
        self.assertEqual(h["save_count"], 1)
        self.assertEqual(h["load_count"], 1)
        self.assertTrue(h["file_exists"])

    def test_concurrent_save_load(self) -> None:
        """并发 save + load 不应崩。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        s = RuntimeSnapshotStore(path=self.path)
        errors: List[str] = []

        def save_worker(i: int) -> None:
            try:
                snap = RuntimeSnapshot()
                snap.identity_id = f"id_{i}"
                for _ in range(20):
                    s.save(snap)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        def load_worker() -> None:
            try:
                for _ in range(50):
                    s.load()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        threads = [threading.Thread(target=save_worker, args=(i,)) for i in range(3)]
        threads += [threading.Thread(target=load_worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ============================================================
# 3. RuntimeSnapshotBuilder
# ============================================================
class TestRuntimeSnapshotBuilder(unittest.TestCase):
    """RuntimeSnapshotBuilder 业务编排测试。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="snap_builder_")
        self.path = os.path.join(self.tmp, "snap.json")

    def tearDown(self) -> None:
        try:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def test_build_initial(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
            SOURCE_INITIAL,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(identity_id="test_id", store=store)
        snap = b.build_initial()
        self.assertEqual(snap.identity_id, "test_id")
        self.assertEqual(snap.source, SOURCE_INITIAL)
        self.assertEqual(snap.boot_count, 0)

    def test_build_from_state_running(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
            SOURCE_PERIODIC,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        snap = b.build_from_state({
            "turn_count": 5,
            "state": "RUNNING",
            "started_at": "2026-01-01T00:00:00Z",
        })
        self.assertEqual(snap.source, SOURCE_PERIODIC)
        self.assertEqual(snap.last_turn_count, 5)
        self.assertEqual(snap.last_started_at, "2026-01-01T00:00:00Z")
        self.assertEqual(snap.last_checkpoint_count, 1)

    def test_build_from_state_stopped(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
            SOURCE_FINAL,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        snap = b.build_from_state({"turn_count": 10, "state": "STOPPED"})
        self.assertEqual(snap.source, SOURCE_FINAL)
        self.assertEqual(snap.last_turn_count, 10)

    def test_build_from_state_other(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
            SOURCE_CHECKPOINT,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        snap = b.build_from_state({"turn_count": 1, "state": "WEIRD"})
        self.assertEqual(snap.source, SOURCE_CHECKPOINT)

    def test_build_from_state_none(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        snap = b.build_from_state(None)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.last_checkpoint_count, 1)

    def test_checkpoint_count_increments(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        prev = b.build_initial()
        for i in range(5):
            prev = b.build_from_state({"turn_count": i, "state": "RUNNING"}, previous=prev)
        self.assertEqual(prev.last_checkpoint_count, 5)

    def test_merge_pr_state(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        pr = FakePR(version=3, identity="yuyi_default", evolution_count=7)
        b = RuntimeSnapshotBuilder(
            identity_id="yuyi_default",
            store=store,
            persistence_runtime=pr,
        )
        snap = b.build_from_state({"turn_count": 1, "state": "RUNNING"})
        self.assertEqual(snap.last_self_model_version, 3)
        self.assertEqual(snap.last_self_model_identity, "yuyi_default")
        self.assertEqual(snap.evolution_record_count, 7)

    def test_merge_pr_state_broken_isolated(self) -> None:
        """坏 PR 不应导致 build_from_state 抛。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(
            store=store,
            persistence_runtime=FakeBrokenPR(),
        )
        try:
            snap = b.build_from_state({"turn_count": 1, "state": "RUNNING"})
            self.assertIsNotNone(snap)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"build_from_state raised: {exc}")

    def test_merge_audit_state(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        audit = FakeAudit(write_count=42, log_path="data/my.jsonl")
        b = RuntimeSnapshotBuilder(
            store=store,
            audit_logger=audit,
        )
        snap = b.build_from_state({"turn_count": 1, "state": "RUNNING"})
        self.assertEqual(snap.last_audit_log_offset, 42)
        self.assertEqual(snap.last_audit_log_path, "data/my.jsonl")

    def test_merge_audit_state_broken_isolated(self) -> None:
        """坏 Audit 不应导致 build_from_state 抛。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(
            store=store,
            audit_logger=FakeBrokenAudit(),
        )
        try:
            snap = b.build_from_state({"turn_count": 1, "state": "RUNNING"})
            self.assertIsNotNone(snap)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"build_from_state raised: {exc}")

    def test_commit_writes_to_disk_and_audit(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        audit = FakeAudit()
        b = RuntimeSnapshotBuilder(
            store=store,
            audit_logger=audit,
        )
        snap = b.build_from_state({"turn_count": 1, "state": "RUNNING"})
        ok = b.commit(snap, reason="periodic")
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(len(audit.checkpoint_calls), 1)
        self.assertEqual(audit.checkpoint_calls[0]["reason"], "periodic")
        # last_snapshot 应被更新
        self.assertIsNotNone(b.last_snapshot)

    def test_commit_invalid_snap(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        ok = b.commit("not a snap")  # type: ignore[arg-type]
        self.assertFalse(ok)

    def test_commit_no_audit(self) -> None:
        """无 audit 时 commit 也应成功。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        snap = b.build_initial()
        ok = b.commit(snap)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(self.path))

    def test_health_check(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotBuilder,
            RuntimeSnapshotStore,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(
            store=store,
            persistence_runtime=FakePR(),
            audit_logger=FakeAudit(),
        )
        h = b.health_check()
        self.assertIn("store", h)
        self.assertTrue(h["has_persistence_runtime"])
        self.assertTrue(h["has_audit_logger"])
        self.assertFalse(h["has_last_snapshot"])


# ============================================================
# 4. E2E
# ============================================================
class TestRuntimeSnapshotE2E(unittest.TestCase):
    """E2E / 稳定性。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="snap_e2e_")
        self.path = os.path.join(self.tmp, "snap.json")

    def tearDown(self) -> None:
        try:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def test_fresh_then_restore(self) -> None:
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshotBuilder,
        )
        # 第一次:Builder 1 落盘
        store1 = RuntimeSnapshotStore(path=self.path)
        b1 = RuntimeSnapshotBuilder(store=store1)
        snap1 = b1.build_from_state({"turn_count": 100, "state": "STOPPED"})
        snap1.mark_boot("fresh")
        self.assertTrue(b1.commit(snap1))
        # 第二次:Store 2 加载
        store2 = RuntimeSnapshotStore(path=self.path)
        loaded = store2.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.last_turn_count, 100)
        self.assertEqual(loaded.source, "final")
        self.assertEqual(loaded.boot_count, 1)

    def test_1000_commit_stability(self) -> None:
        """1000 次 commit 应稳定,文件应始终合法。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshotBuilder,
        )
        store = RuntimeSnapshotStore(path=self.path)
        b = RuntimeSnapshotBuilder(store=store)
        prev = b.build_initial()
        for i in range(1000):
            prev = b.build_from_state(
                {"turn_count": i, "state": "RUNNING"},
                previous=prev,
            )
            ok = b.commit(prev)
            self.assertTrue(ok)
        # 最后一次 load 应成功
        loaded = store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.last_checkpoint_count, 1000)
        self.assertEqual(loaded.last_turn_count, 999)


if __name__ == "__main__":
    unittest.main()
