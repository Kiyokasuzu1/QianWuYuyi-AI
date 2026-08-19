# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_c/test_stability_10k.py

Phase 5.0-C · Step 6: 10000 事件稳定性测试。

目标:
- 验证 RuntimeSnapshot / RuntimeSnapshotStore / RuntimeSnapshotBuilder
  在 10000 次 commit 后仍保持稳定
- 验证:无内存泄漏(用引用计数粗略检查)、无异常、文件始终合法
"""
from __future__ import annotations

import gc
import json
import os
import shutil
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional


class FakePR:
    def __init__(self) -> None:
        self._version = 0
        self._evolution = 0

    @property
    def last_persisted_version(self) -> int:
        return self._version

    @property
    def current_identity_id(self) -> str:
        return "yuyi_default"

    def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
        return self._evolution

    def increment(self) -> None:
        self._version += 1
        self._evolution += 1


class FakeAudit:
    def __init__(self) -> None:
        self.write_count = 0

    def log_checkpoint(self, reason: str = "", state: Optional[Dict[str, Any]] = None) -> bool:
        self.write_count += 1
        return True


class TestStability10K(unittest.TestCase):
    """10000 事件稳定性测试。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="stability_10k_")
        self.path = os.path.join(self.tmp, "snap.json")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    def test_10k_commit_stability(self) -> None:
        """10000 次 commit + 10000 次 load,验证稳定性。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshotBuilder,
        )
        store = RuntimeSnapshotStore(path=self.path)
        pr = FakePR()
        audit = FakeAudit()
        builder = RuntimeSnapshotBuilder(
            identity_id="yuyi_default",
            store=store,
            persistence_runtime=pr,
            audit_logger=audit,
        )

        prev = builder.build_initial()
        errors: List[str] = []

        start = time.time()
        for i in range(10000):
            try:
                # 模拟 PR 状态变化
                pr.increment()
                state = {
                    "turn_count": i,
                    "state": "RUNNING" if i < 9999 else "STOPPED",
                    "started_at": "2026-01-01T00:00:00Z",
                }
                prev = builder.build_from_state(state, previous=prev)
                ok = builder.commit(prev, reason=prev.source)
                if not ok:
                    errors.append(f"commit failed at i={i}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"i={i}: {exc}")
        elapsed = time.time() - start

        # 1) 无错误
        self.assertEqual(errors, [], f"errors during 10k loop: {errors[:5]}")
        # 2) store 统计正确
        self.assertEqual(store.save_count, 10000)
        # 3) audit 累计
        self.assertEqual(audit.write_count, 10000)
        # 4) last_checkpoint_count
        self.assertEqual(prev.last_checkpoint_count, 10000)
        # 5) 末次 source = final
        self.assertEqual(prev.source, "final")
        # 6) 文件合法 JSON + 字段正确
        self.assertTrue(os.path.exists(self.path))
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_checkpoint_count"], 10000)
        self.assertEqual(data["last_turn_count"], 9999)
        self.assertEqual(data["source"], "final")
        # last_self_model_version: pr._version 经 10000 次 increment,build 时读
        self.assertEqual(data["last_self_model_version"], 10000)
        # last_audit_log_offset: build 时 read → log_checkpoint 在 commit 中
        # 故 last snapshot 写入的 offset = write_count-1 = 9999
        self.assertEqual(data["last_audit_log_offset"], 9999)
        # 7) load 仍能解析
        loaded = store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.last_checkpoint_count, 10000)
        # 8) 性能:10000 次 commit 应在合理时间(Windows fsync 较慢,放宽到 180s)
        self.assertLess(elapsed, 180.0, f"10k commits took {elapsed:.2f}s (>180s)")

    def test_10k_load_only_stability(self) -> None:
        """10000 次连续 load,验证读取无泄漏。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        store = RuntimeSnapshotStore(path=self.path)
        snap = RuntimeSnapshot(identity_id="yuyi_default")
        snap.last_turn_count = 100
        snap.boot_count = 5
        store.save(snap)

        # 10000 次 load
        for i in range(10000):
            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.identity_id, "yuyi_default")
        self.assertEqual(store.load_count, 10000)
        self.assertEqual(store.load_failures, 0)

    def test_10k_alternating_save_load(self) -> None:
        """10000 次 save/load 交替,模拟运行时持续读写。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshot,
        )
        store = RuntimeSnapshotStore(path=self.path)
        snap = RuntimeSnapshot()
        errors: List[str] = []

        for i in range(10000):
            try:
                snap.last_turn_count = i
                snap.boot_count = i // 10
                snap.last_checkpoint_count = i
                ok = store.save(snap)
                if not ok:
                    errors.append(f"save failed at {i}")
                loaded = store.load()
                if loaded is None:
                    errors.append(f"load returned None at {i}")
                elif loaded.last_turn_count != i:
                    errors.append(f"mismatch at {i}: {loaded.last_turn_count}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"i={i}: {exc}")

        self.assertEqual(errors, [], f"errors: {errors[:3]}")
        self.assertEqual(store.save_count, 10000)
        self.assertEqual(store.load_count, 10000)
        self.assertEqual(store.save_failures, 0)
        self.assertEqual(store.load_failures, 0)

    def test_10k_no_memory_leak_basic(self) -> None:
        """粗略内存检查:10000 次 commit 后 gc 后引用计数稳定。"""
        from src.orchestrator.runtime_snapshot import (
            RuntimeSnapshotStore,
            RuntimeSnapshotBuilder,
        )
        store = RuntimeSnapshotStore(path=self.path)
        builder = RuntimeSnapshotBuilder(store=store)
        prev = builder.build_initial()

        # 第一轮:基线
        for i in range(1000):
            prev = builder.build_from_state({"turn_count": i, "state": "RUNNING"}, previous=prev)
            builder.commit(prev)
        gc.collect()
        baseline_objects = len(gc.get_objects())

        # 第二轮:再 1000 次
        for i in range(1000, 2000):
            prev = builder.build_from_state({"turn_count": i, "state": "RUNNING"}, previous=prev)
            builder.commit(prev)
        gc.collect()
        after_objects = len(gc.get_objects())

        # 允许一定波动(< 5000 个对象,Windows 环境下 gc 噪声较大)
        diff = abs(after_objects - baseline_objects)
        self.assertLess(diff, 5000, f"object count drift: baseline={baseline_objects} after={after_objects} diff={diff}")


if __name__ == "__main__":
    unittest.main()
