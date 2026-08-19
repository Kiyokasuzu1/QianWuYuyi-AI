"""
Phase 3.5.22: PersistenceManager 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.storage.persistence_manager import PersistenceManager


class TestPersistenceManager(unittest.TestCase):
    def test_append_and_snapshot_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistenceManager(base_dir=os.path.join(tmp, "persistence"))
            pm.append("memory", {"id": "m1", "content": "hello"})
            pm.append("memory", {"id": "m2", "content": "world"})

            snap = pm.ensure_entity_ready("memory")
            self.assertEqual(snap["entity"], "memory")
            self.assertGreaterEqual(len(snap["items"]), 2)

            rebuilt, corruptions = pm.rebuild_snapshot("memory")
            self.assertEqual(corruptions, [])
            self.assertEqual(len(rebuilt["items"]), 2)

    def test_corruption_detection_hash_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistenceManager(base_dir=os.path.join(tmp, "persistence"))
            pm.append("audit", {"op": "a"})
            pm.append("audit", {"op": "b"})

            log_path = pm._log_path("audit")
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            # 篡改第二行
            lines[1] = lines[1].replace('"op":"b"', '"op":"tampered"')
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(lines)

            rebuilt, corruptions = pm.rebuild_snapshot("audit")
            self.assertTrue(corruptions)
            # 应至少保留第一条
            self.assertGreaterEqual(len(rebuilt["items"]), 1)

    def test_migration_applied_on_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistenceManager(base_dir=os.path.join(tmp, "persistence"))

            # 模拟旧 schema_version=0 的数据
            pm.register_migration("self_model", 0, lambda d: {"migrated": True, **d})
            pm.append("self_model", {"name": "yuyi"}, schema_version=0)

            rebuilt, _ = pm.rebuild_snapshot("self_model")
            self.assertTrue(rebuilt["items"][0].get("migrated"))

    def test_backup_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            pm = PersistenceManager(base_dir=os.path.join(tmp, "persistence"))
            pm.append("runtime_snapshot", {"tick": 1})
            pm.ensure_entity_ready("runtime_snapshot")

            backup_dir = pm.backup_entity("runtime_snapshot")
            self.assertTrue(os.path.isdir(backup_dir))

            ok = pm.restore_entity_from_backup("runtime_snapshot", backup_dir)
            self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

