# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_c/test_crash_recovery.py

Phase 5.0-C · Step 6: Crash Recovery 测试。

覆盖 5 个场景:
S1  正常启动 → final checkpoint → 重启恢复
S2  启动前不存在 snapshot → 视为 fresh
S3  snapshot 文件被截断为损坏 JSON → 视为 fresh
S4  snapshot schema 不匹配(v0.9) → 视为 fresh
S5  连续多次 crash → boot_count 单调递增
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional


# ============================================================
# 复用 Step 3 的 fakes
# ============================================================
class FakeOrchestrator:
    def __init__(self) -> None:
        self._smo: Optional[Any] = None
        self.process_calls: List[str] = []

    def configure_self_model(self, smo: Any) -> None:
        self._smo = smo

    def is_self_model_configured(self) -> bool:
        return self._smo is not None

    def process(self, user_message: str) -> str:
        self.process_calls.append(user_message)
        return f"echo: {user_message}"

    def clear_history(self) -> None:
        pass


class FakeSMO:
    def __init__(self, identity_id: str = "yuyi_default") -> None:
        self.identity_id = identity_id
        self._persistence = object()

    def run_after_event(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {}

    def health_check(self) -> Dict[str, Any]:
        return {"name": "fake_smo", "schema_version": "1.0"}


class FakePR:
    def __init__(self) -> None:
        self.initialize_calls: List[str] = []
        self.restore_calls: List[Dict[str, Any]] = []
        self.last_persisted_version: Optional[int] = 3
        self.current_identity_id: Optional[str] = None
        self._evolution_count = 7

    def initialize(self, identity_id: str) -> bool:
        self.initialize_calls.append(identity_id)
        self.current_identity_id = identity_id
        return True

    def restore_on_startup(
        self,
        identity_id: Optional[str] = None,
        create_checkpoint: Optional[bool] = None,
    ) -> Any:
        self.restore_calls.append({"identity_id": identity_id, "create_checkpoint": create_checkpoint})
        return None

    def persist_evolution(self, evolution_result: Any, identity_id: Optional[str] = None) -> bool:
        return True

    def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
        return self._evolution_count

    def health_check(self) -> Dict[str, Any]:
        return {"name": "fake_pr"}


class FakeAudit:
    def __init__(self) -> None:
        self.write_count = 0
        self.events: List[Dict[str, Any]] = []

    def log_event_start(self, **kwargs: Any) -> bool:
        self.write_count += 1
        self.events.append({"event": "event_start", **kwargs})
        return True

    def log_event_end(self, **kwargs: Any) -> bool:
        self.write_count += 1
        self.events.append({"event": "event_end", **kwargs})
        return True

    def log_checkpoint(self, reason: str = "", state: Optional[Dict[str, Any]] = None) -> bool:
        self.write_count += 1
        self.events.append({"event": "checkpoint", "reason": reason, "state": state or {}})
        return True

    def log_loop_start(self, **kwargs: Any) -> bool:
        self.write_count += 1
        self.events.append({"event": "loop_start", **kwargs})
        return True

    def log_loop_stop(self, **kwargs: Any) -> bool:
        self.write_count += 1
        self.events.append({"event": "loop_stop", **kwargs})
        return True

    def log_exception(self, **kwargs: Any) -> bool:
        self.write_count += 1
        self.events.append({"event": "exception", **kwargs})
        return True

    def health_check(self) -> Dict[str, Any]:
        return {"name": "fake_audit"}


def _make_bootstrap(tmp: str):
    """构造一个完整的 RuntimeBootstrap(全 Fake 组件)。"""
    from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
    snap_path = os.path.join(tmp, "snap.json")
    audit_path = os.path.join(tmp, "audit.jsonl")
    orch = FakeOrchestrator()
    pr = FakePR()
    smo = FakeSMO()
    audit = FakeAudit()
    b = RuntimeBootstrap(
        orchestrator=orch,
        identity_id="yuyi_default",
        audit_log_path=audit_path,
        runtime_snapshot_path=snap_path,
        persistence_runtime=pr,
        self_model_orchestrator=smo,
        audit_logger=audit,
    )
    return b, snap_path, audit_path, orch, pr, smo, audit


# ============================================================
# 5 个 Crash Recovery 场景
# ============================================================
class TestCrashRecovery(unittest.TestCase):
    """Crash Recovery 5 场景测试。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="crash_recovery_")

    def tearDown(self) -> None:
        try:
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # S1: 正常启动 → final checkpoint → 重启恢复
    # --------------------------------------------------------
    def test_s1_normal_lifecycle_with_final_checkpoint(self) -> None:
        """S1: 完整生命周期,重启后 boot_count=2 + last_turn_count 完整。"""
        b1, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)

        # 进程 1:启动
        loop1 = b1.start()
        self.assertIsNotNone(loop1)
        self.assertEqual(b1.boot_mode, "fresh")
        self.assertEqual(b1.snapshot()["boot_count"], 1)

        # 模拟 final checkpoint(LongLoop 退出前)
        b1._checkpoint_provider({
            "turn_count": 100,
            "error_count": 2,
            "started_at": "2026-01-01T00:00:00Z",
            "state": "STOPPED",
        })

        # 进程 2:重启(模拟崩溃后启动)
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b2, _, _, orch2, pr2, smo2, audit2 = _make_bootstrap(self.tmp)
        # 注:_make_bootstrap 会生成新的 fake,但 path 一样
        loop2 = b2.start()
        self.assertIsNotNone(loop2)
        self.assertEqual(b2.boot_mode, "restore")
        self.assertEqual(b2.snapshot()["boot_count"], 2)
        self.assertEqual(b2.snapshot()["last_turn_count"], 100)
        self.assertEqual(b2.snapshot()["source"], "final")

    # --------------------------------------------------------
    # S2: 启动前不存在 snapshot → 视为 fresh
    # --------------------------------------------------------
    def test_s2_no_snapshot_fresh_start(self) -> None:
        """S2: 无 snapshot → fresh 模式,boot_count=1。"""
        b, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)
        # 确认文件不存在
        self.assertFalse(os.path.exists(snap_path))

        loop = b.start()
        self.assertIsNotNone(loop)
        self.assertEqual(b.boot_mode, "fresh")
        self.assertEqual(b.snapshot()["boot_count"], 1)
        self.assertEqual(b.snapshot()["last_turn_count"], 0)
        # 启动后应已落盘
        self.assertTrue(os.path.exists(snap_path))

    # --------------------------------------------------------
    # S3: snapshot 文件被截断为损坏 JSON → 视为 fresh
    # --------------------------------------------------------
    def test_s3_corrupted_snapshot_fallback_to_fresh(self) -> None:
        """S3: 损坏 JSON → fresh 模式,自动重建。"""
        b, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)
        # 写入损坏内容
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write('{"schema_version":"1.0","identity_id":"old","boot_coun')  # 截断

        loop = b.start()
        self.assertIsNotNone(loop)
        self.assertEqual(b.boot_mode, "fresh")
        self.assertEqual(b.snapshot()["boot_count"], 1)
        # 损坏文件已被覆盖为新的 fresh snapshot
        with open(snap_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["boot_count"], 1)
        self.assertEqual(data["last_boot_mode"], "fresh")

    # --------------------------------------------------------
    # S4: snapshot schema 不匹配(v0.9) → 视为 fresh
    # --------------------------------------------------------
    def test_s4_schema_mismatch_fallback_to_fresh(self) -> None:
        """S4: schema_version 不匹配 → fresh 模式,boot_count 不继承。"""
        b, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)
        # 写入旧 schema 的 snapshot
        old = {
            "schema_version": "0.9",
            "identity_id": "old",
            "boot_count": 99,
            "last_turn_count": 500,
        }
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(old, f)

        loop = b.start()
        self.assertIsNotNone(loop)
        self.assertEqual(b.boot_mode, "fresh")
        # 不应继承旧的 boot_count
        self.assertEqual(b.snapshot()["boot_count"], 1)
        self.assertEqual(b.snapshot()["last_turn_count"], 0)

    # --------------------------------------------------------
    # S5: 连续多次 crash → boot_count 单调递增
    # --------------------------------------------------------
    def test_s5_consecutive_crashes_boot_count_increments(self) -> None:
        """S5: 5 次连续 crash → boot_count 5,last_turn_count 取最后一次的 final。"""
        b, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)
        # 第一次启动
        b.start()
        self.assertEqual(b.snapshot()["boot_count"], 1)

        # 后续 4 次 crash + 重启
        last_turn = 0
        for i in range(2, 6):
            # 模拟崩溃前最后一次 checkpoint
            last_turn = i * 10
            b._checkpoint_provider({
                "turn_count": last_turn,
                "error_count": 0,
                "started_at": "2026-01-01T00:00:00Z",
                "state": "STOPPED",
            })
            # 模拟崩溃:直接销毁,下次重新 start
            del b
            from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
            b2, _, _, _, _, _, _ = _make_bootstrap(self.tmp)
            b2.start()
            self.assertEqual(b2.boot_mode, "restore")
            self.assertEqual(b2.snapshot()["boot_count"], i)
            self.assertEqual(b2.snapshot()["last_turn_count"], last_turn)
            b = b2

        # 最终 boot_count=5
        self.assertEqual(b.snapshot()["boot_count"], 5)

    # --------------------------------------------------------
    # Bonus: 混合 S3 + S5(中间损坏一次,但 boot_count 仍递增)
    # --------------------------------------------------------
    def test_s5b_corrupt_in_middle_recovers(self) -> None:
        """S5b: 第 3 次启动时 snapshot 损坏,后续启动应能继续递增。"""
        b, snap_path, _, _, _, _, _ = _make_bootstrap(self.tmp)
        b.start()
        b._checkpoint_provider({"turn_count": 10, "state": "STOPPED"})
        # 第二次启动
        del b
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b2, _, _, _, _, _, _ = _make_bootstrap(self.tmp)
        b2.start()
        self.assertEqual(b2.snapshot()["boot_count"], 2)
        b2._checkpoint_provider({"turn_count": 20, "state": "STOPPED"})

        # 模拟磁盘损坏
        with open(snap_path, "w", encoding="utf-8") as f:
            f.write("garbage")
        del b2
        # 第三次启动:应为 fresh
        b3, _, _, _, _, _, _ = _make_bootstrap(self.tmp)
        b3.start()
        self.assertEqual(b3.boot_mode, "fresh")
        # boot_count 从 1 重新开始(因为损坏),但系统稳定
        self.assertEqual(b3.snapshot()["boot_count"], 1)

        # 第四次启动:正常递增
        b3._checkpoint_provider({"turn_count": 30, "state": "STOPPED"})
        del b3
        b4, _, _, _, _, _, _ = _make_bootstrap(self.tmp)
        b4.start()
        self.assertEqual(b4.boot_mode, "restore")
        self.assertEqual(b4.snapshot()["boot_count"], 2)
        self.assertEqual(b4.snapshot()["last_turn_count"], 30)


if __name__ == "__main__":
    unittest.main()
