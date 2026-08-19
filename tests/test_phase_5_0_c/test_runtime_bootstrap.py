# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_c/test_runtime_bootstrap.py

Phase 5.0-C · Step 3: RuntimeBootstrap 单元测试。

覆盖范围:
1. 构造与默认值
2. configure() 一次性与幂等性
3. configure() 子组件构造(PR / SMO / Audit)
4. configure() 注入到 Orchestrator(configure_self_model)
5. configure() 失败隔离
6. start() 成功路径(fresh + restore 两种模式)
7. start() 幂等性
8. start() 失败隔离
9. checkpoint_provider 落盘与 source 推断
10. checkpoint_provider 异常隔离
11. snapshot() / health() / stats() 观测接口
12. 线程安全(基础)
13. 注入自定义组件
14. 向后兼容(Orchestrator 无 configure_self_model 时不崩)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from typing import Any, Dict, List, Optional
from unittest import mock


# ============================================================
# 测试用 fakes
# ============================================================
class FakeOrchestrator:
    """最小 Orchestrator 替身(只暴露 configure_self_model + process)。

    process() 返回固定字符串,用于让 LongLoop 跑通。
    """

    def __init__(self, with_configure_self_model: bool = True) -> None:
        self._smo: Optional[Any] = None
        self.process_calls: List[str] = []
        self._with_configure = with_configure_self_model
        if not with_configure_self_model:
            # 删除方法,模拟旧版 Orchestrator
            try:
                del self.configure_self_model
            except AttributeError:
                pass

    def configure_self_model(self, smo: Any) -> None:
        if not self._with_configure:
            raise RuntimeError("configure_self_model not supported")
        self._smo = smo

    def is_self_model_configured(self) -> bool:
        return self._smo is not None

    def process(self, user_message: str) -> str:
        self.process_calls.append(user_message)
        return f"echo: {user_message}"

    def clear_history(self) -> None:
        pass


class FakeSMO:
    """最小 SelfModelOrchestrator 替身。"""

    def __init__(self, identity_id: str = "yuyi_default", with_pr: bool = True) -> None:
        self.identity_id = identity_id
        self._persistence = object() if with_pr else None
        self.run_count = 0
        self.last_context: Optional[Dict[str, Any]] = None

    def run_after_event(self, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.run_count += 1
        self.last_context = context
        return {
            "build": None,
            "evolution": None,
            "reflection": None,
            "validation": None,
            "persistence": None,
            "errors": [],
        }

    def health_check(self) -> Dict[str, Any]:
        return {
            "name": "fake_smo",
            "schema_version": "1.0",
            "identity_id": self.identity_id,
            "run_count": self.run_count,
            "errors_total": 0,
        }


class FakePersistenceRuntime:
    """最小 PersistenceRuntime 替身。"""

    def __init__(self) -> None:
        self.initialize_calls: List[str] = []
        self.restore_calls: List[Dict[str, Any]] = []
        self.persist_calls: List[Any] = []
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
        self.restore_calls.append({
            "identity_id": identity_id,
            "create_checkpoint": create_checkpoint,
        })
        return None  # 没有旧 snapshot

    def persist_evolution(self, evolution_result: Any, identity_id: Optional[str] = None) -> bool:
        self.persist_calls.append(evolution_result)
        return True

    def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
        return self._evolution_count

    def health_check(self) -> Dict[str, Any]:
        return {
            "name": "fake_pr",
            "schema_version": "1.0",
            "current_identity_id": self.current_identity_id,
            "persist_count": len(self.persist_calls),
            "restore_count": len(self.restore_calls),
        }


class FakeAuditLogger:
    """最小 RuntimeAuditLogger 替身。"""

    def __init__(self) -> None:
        self.write_count = 0
        self.events: List[Dict[str, Any]] = []
        self.log_path = "data/fake_audit.jsonl"

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
        return {
            "name": "fake_audit",
            "schema_version": "1.0",
            "write_count": self.write_count,
        }


# ============================================================
# TestRuntimeBootstrap
# ============================================================
class TestRuntimeBootstrap(unittest.TestCase):
    """RuntimeBootstrap 全功能测试。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="rtb_test_")
        self.snapshot_path = os.path.join(self.tmp, "snapshot.json")
        self.audit_path = os.path.join(self.tmp, "audit.jsonl")
        self.orch = FakeOrchestrator()
        self.pr = FakePersistenceRuntime()
        self.smo = FakeSMO()
        self.audit = FakeAuditLogger()

    def tearDown(self) -> None:
        try:
            import shutil
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 1. 构造
    # --------------------------------------------------------
    def test_construct_default(self) -> None:
        from src.orchestrator.runtime_bootstrap import (
            RuntimeBootstrap,
            RUNTIME_BOOTSTRAP_SCHEMA_VERSION,
            DEFAULT_IDENTITY_ID,
        )
        b = RuntimeBootstrap(orchestrator=self.orch)
        self.assertEqual(b.identity_id, DEFAULT_IDENTITY_ID)
        self.assertFalse(b.is_configured)
        self.assertFalse(b.is_started)
        self.assertEqual(b.boot_mode, "fresh")
        self.assertIsNone(b.long_loop)
        self.assertIsNone(b.audit_logger)
        self.assertIsNone(b.persistence_runtime)
        self.assertIsNone(b.self_model_orchestrator)
        self.assertEqual(b.schema_version, RUNTIME_BOOTSTRAP_SCHEMA_VERSION)

    def test_construct_custom_params(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            identity_id="test_id",
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            checkpoint_interval=5,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        self.assertEqual(b.identity_id, "test_id")
        self.assertEqual(b.snapshot_path, self.snapshot_path)
        self.assertIs(b.persistence_runtime, self.pr)
        self.assertIs(b.self_model_orchestrator, self.smo)
        self.assertIs(b.audit_logger, self.audit)

    def test_construct_requires_orchestrator(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        with self.assertRaises(ValueError):
            RuntimeBootstrap(orchestrator=None)  # type: ignore[arg-type]

    # --------------------------------------------------------
    # 2. configure() 一次性 + 幂等
    # --------------------------------------------------------
    def test_configure_idempotent(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        ok1 = b.configure()
        ok2 = b.configure()
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertTrue(b.is_configured)
        self.assertIsNone(b.configure_error)

    def test_configure_injects_smo_into_orchestrator(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        self.assertTrue(self.orch.is_self_model_configured())
        self.assertIs(self.orch._smo, self.smo)

    def test_configure_failure_isolated(self) -> None:
        """注入坏 SMO(无 run_after_event),configure 仍应成功(注入异常被隔离)。

        注意:configure 主要做"绑定"动作,运行期异常不在 configure 范围内。
        """
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap

        class BadSMO:
            """没有任何方法的 SMO,用于检测异常隔离。"""
            pass

        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=BadSMO(),  # type: ignore[arg-type]
            audit_logger=self.audit,
        )
        ok = b.configure()
        # configure_self_model 接受任何对象 → configure 不会失败
        self.assertTrue(ok)
        self.assertTrue(b.is_configured)

    def test_configure_orchestrator_without_configure_method(self) -> None:
        """Orchestrator 不含 configure_self_model 时,configure 不崩。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        legacy_orch = FakeOrchestrator(with_configure_self_model=False)
        b = RuntimeBootstrap(
            orchestrator=legacy_orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        ok = b.configure()
        self.assertTrue(ok)
        self.assertTrue(b.is_configured)

    # --------------------------------------------------------
    # 3. 自建子组件(无注入场景)
    # --------------------------------------------------------
    def test_configure_builds_components_when_not_injected(self) -> None:
        """所有子组件默认 None → Bootstrap 应自建。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
        )
        b.configure()
        # PR / Audit 应被自建;SMO 应被自建
        self.assertIsNotNone(b.persistence_runtime)
        self.assertIsNotNone(b.audit_logger)
        self.assertIsNotNone(b.self_model_orchestrator)
        # SMO 应该把 PR 注入进去
        self.assertIsNotNone(b.self_model_orchestrator.persistence_runtime)
        # 注入到 Orchestrator
        self.assertTrue(self.orch.is_self_model_configured())

    # --------------------------------------------------------
    # 4. start() 成功路径 — fresh
    # --------------------------------------------------------
    def test_start_fresh_mode(self) -> None:
        """首次启动 → boot_mode=fresh,snapshot 落盘。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        loop = b.start()
        self.assertIsNotNone(loop)
        self.assertTrue(b.is_started)
        self.assertEqual(b.boot_mode, "fresh")
        # PR.initialize 应被调用
        self.assertEqual(self.pr.initialize_calls, ["yuyi_default"])
        # PR.restore_on_startup 应被调用
        self.assertEqual(len(self.pr.restore_calls), 1)
        # audit.log_loop_start 应被调用
        loop_start_events = [e for e in self.audit.events if e["event"] == "loop_start"]
        self.assertEqual(len(loop_start_events), 1)
        self.assertEqual(loop_start_events[0]["boot_mode"], "fresh")
        self.assertEqual(loop_start_events[0]["identity_id"], "yuyi_default")
        # snapshot 应落盘
        self.assertTrue(os.path.exists(self.snapshot_path))
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_boot_mode"], "fresh")
        self.assertEqual(data["boot_count"], 1)

    # --------------------------------------------------------
    # 5. start() 成功路径 — restore
    # --------------------------------------------------------
    def test_start_restore_mode_when_snapshot_exists(self) -> None:
        """第二次启动:有旧 snapshot → boot_mode=restore。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        # 1) 第一次启动并落盘 snapshot
        b1 = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b1.start()
        self.assertEqual(b1.boot_mode, "fresh")
        # 2) 第二次启动(用新的 orch,模拟新进程)
        orch2 = FakeOrchestrator()
        pr2 = FakePersistenceRuntime()
        smo2 = FakeSMO()
        audit2 = FakeAuditLogger()
        b2 = RuntimeBootstrap(
            orchestrator=orch2,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=pr2,
            self_model_orchestrator=smo2,
            audit_logger=audit2,
        )
        loop2 = b2.start()
        self.assertIsNotNone(loop2)
        self.assertEqual(b2.boot_mode, "restore")
        # boot_count 应递增到 2
        snap_dict = b2.snapshot()
        self.assertEqual(snap_dict["boot_count"], 2)
        self.assertEqual(snap_dict["last_boot_mode"], "restore")
        # audit2.log_loop_start 应记录 boot_mode=restore
        loop_start = [e for e in audit2.events if e["event"] == "loop_start"]
        self.assertEqual(loop_start[0]["boot_mode"], "restore")

    # --------------------------------------------------------
    # 6. start() 幂等
    # --------------------------------------------------------
    def test_start_idempotent(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        loop1 = b.start()
        loop2 = b.start()
        # 同一实例
        self.assertIs(loop1, loop2)
        # PR.initialize 不应被重复调用
        self.assertEqual(len(self.pr.initialize_calls), 1)

    # --------------------------------------------------------
    # 7. start() 失败隔离
    # --------------------------------------------------------
    def test_start_returns_none_when_configure_fails(self) -> None:
        """configure 整体失败 → start 返回 None。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
        )
        # 强制 configure 返回 False(模拟整体失败)
        with mock.patch.object(RuntimeBootstrap, "configure", return_value=False):
            loop = b.start()
            self.assertIsNone(loop)
            self.assertIsNotNone(b.start_error)

    # --------------------------------------------------------
    # 8. checkpoint_provider
    # --------------------------------------------------------
    def test_checkpoint_provider_writes_snapshot(self) -> None:
        """checkpoint_provider 调用 → snapshot 落盘 + audit.log_checkpoint。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        self.assertIsNotNone(provider)
        # 模拟 LongLoop 调用
        state = {
            "schema_version": "1.0",
            "turn_count": 7,
            "error_count": 1,
            "started_at": "2026-01-01T00:00:00Z",
            "state": "RUNNING",
        }
        provider(state)
        # snapshot 应落盘
        self.assertTrue(os.path.exists(self.snapshot_path))
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_turn_count"], 7)
        self.assertEqual(data["source"], "periodic")
        # audit.log_checkpoint 应被调用
        ckpt_events = [e for e in self.audit.events if e["event"] == "checkpoint"]
        self.assertGreater(len(ckpt_events), 0)
        self.assertEqual(ckpt_events[-1]["reason"], "periodic")

    def test_checkpoint_provider_source_inference(self) -> None:
        """source 推断:STOPPED → final,RUNNING → periodic。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        # RUNNING → periodic
        provider({"turn_count": 1, "state": "RUNNING"})
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["source"], "periodic")
        # STOPPED → final
        provider({"turn_count": 2, "state": "STOPPED"})
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["source"], "final")

    def test_checkpoint_provider_increments_counter(self) -> None:
        """每次 checkpoint_provider 调用 → last_checkpoint_count +1。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        for i in range(5):
            provider({"turn_count": i, "state": "RUNNING"})
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_checkpoint_count"], 5)

    def test_checkpoint_provider_exception_isolated(self) -> None:
        """Provider 内部异常不应抛出(由 LongLoop 兜底,但这里再包一层防御)。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        # 给一个非 dict state → provider 内部用 isinstance 判断,不应崩
        try:
            provider("not a dict")  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            self.fail(f"checkpoint_provider raised: {exc}")
        # 再来一个 None
        try:
            provider(None)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            self.fail(f"checkpoint_provider raised: {exc}")

    def test_checkpoint_provider_merges_pr_state(self) -> None:
        """Provider 应合并 PR 的 last_persisted_version / evolution_count。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        provider({"turn_count": 1, "state": "RUNNING"})
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_self_model_version"], 3)
        self.assertEqual(data["evolution_record_count"], 7)
        self.assertEqual(data["last_self_model_identity"], "yuyi_default")

    def test_checkpoint_provider_merges_audit_state(self) -> None:
        """Provider 应合并 audit.write_count。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        self.audit.write_count = 42
        provider = b._checkpoint_provider
        provider({"turn_count": 1, "state": "RUNNING"})
        with open(self.snapshot_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["last_audit_log_offset"], 42)

    # --------------------------------------------------------
    # 9. 观测接口
    # --------------------------------------------------------
    def test_snapshot_returns_dict(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
        )
        snap = b.snapshot()
        self.assertIsInstance(snap, dict)
        self.assertIn("schema_version", snap)
        self.assertIn("identity_id", snap)
        self.assertIn("boot_count", snap)

    def test_health_returns_comprehensive(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        h = b.health()
        self.assertIn("name", h)
        self.assertIn("schema_version", h)
        self.assertIn("is_configured", h)
        self.assertIn("components", h)
        self.assertIn("snapshot", h)
        self.assertIn("persistence_health", h)
        self.assertIn("self_model_orchestrator_health", h)
        self.assertIn("audit_health", h)
        self.assertTrue(h["components"]["persistence_runtime"])
        self.assertTrue(h["components"]["self_model_orchestrator"])
        self.assertTrue(h["components"]["audit_logger"])

    def test_stats_returns_basic(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
        )
        s = b.stats()
        self.assertIn("identity_id", s)
        self.assertIn("is_configured", s)
        self.assertIn("boot_mode", s)
        self.assertEqual(s["identity_id"], "yuyi_default")

    def test_repr(self) -> None:
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(orchestrator=self.orch)
        s = repr(b)
        self.assertIn("RuntimeBootstrap", s)
        self.assertIn("yuyi_default", s)

    # --------------------------------------------------------
    # 10. 线程安全
    # --------------------------------------------------------
    def test_thread_safety_basic(self) -> None:
        """多线程并发调用 snapshot/health/stats 不崩。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
        )
        b.configure()

        errors: List[str] = []

        def worker() -> None:
            try:
                for _ in range(50):
                    b.snapshot()
                    b.health()
                    b.stats()
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    # --------------------------------------------------------
    # 11. End-to-end smoke: configure → start → checkpoint → re-start
    # --------------------------------------------------------
    def test_e2e_configure_start_checkpoint_restart(self) -> None:
        """完整链路:configure → start(fresh) → checkpoint → re-start(restore) → snapshot."""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        # 进程 1
        b1 = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        loop1 = b1.start()
        self.assertIsNotNone(loop1)
        self.assertEqual(b1.boot_mode, "fresh")
        # 模拟 LongLoop 退出前的 final checkpoint
        b1._checkpoint_provider({
            "turn_count": 100,
            "error_count": 0,
            "started_at": "2026-01-01T00:00:00Z",
            "state": "STOPPED",
        })
        # 进程 2(模拟新进程启动,所有引用都是新对象)
        orch2 = FakeOrchestrator()
        pr2 = FakePersistenceRuntime()
        smo2 = FakeSMO()
        audit2 = FakeAuditLogger()
        b2 = RuntimeBootstrap(
            orchestrator=orch2,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=pr2,
            self_model_orchestrator=smo2,
            audit_logger=audit2,
        )
        loop2 = b2.start()
        self.assertIsNotNone(loop2)
        self.assertEqual(b2.boot_mode, "restore")
        snap = b2.snapshot()
        self.assertEqual(snap["last_turn_count"], 100)
        self.assertEqual(snap["source"], "final")
        self.assertEqual(snap["boot_count"], 2)

    # --------------------------------------------------------
    # 12. 异常 PR 配置
    # --------------------------------------------------------
    def test_checkpoint_provider_handles_broken_pr(self) -> None:
        """PR 的 get_evolution_count 抛异常 → provider 不崩。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap

        class BrokenPR(FakePersistenceRuntime):
            def get_evolution_count(self, identity_id: Optional[str] = None) -> int:
                raise RuntimeError("pr broken")

        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=BrokenPR(),
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        b.configure()
        provider = b._checkpoint_provider
        try:
            provider({"turn_count": 1, "state": "RUNNING"})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"provider raised: {exc}")
        # snapshot 应仍落盘(只是 evolution_record_count 字段缺失或 None)
        self.assertTrue(os.path.exists(self.snapshot_path))

    def test_checkpoint_provider_handles_broken_audit(self) -> None:
        """Audit 抛异常 → provider 不崩。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap

        class BrokenAudit(FakeAuditLogger):
            def log_checkpoint(self, reason: str = "", state: Optional[Dict[str, Any]] = None) -> bool:
                raise RuntimeError("audit broken")

        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=BrokenAudit(),
        )
        b.configure()
        provider = b._checkpoint_provider
        try:
            provider({"turn_count": 1, "state": "RUNNING"})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"provider raised: {exc}")
        # snapshot 应落盘
        self.assertTrue(os.path.exists(self.snapshot_path))

    # --------------------------------------------------------
    # 13. schema 兼容性
    # --------------------------------------------------------
    def test_corrupted_snapshot_treated_as_fresh(self) -> None:
        """损坏的 snapshot 文件 → 视为 fresh,生成新 snapshot。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        # 写入损坏内容
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            f.write("not a json {{{")
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        loop = b.start()
        self.assertIsNotNone(loop)
        # 损坏时 load 返回 None → fresh 模式
        self.assertEqual(b.boot_mode, "fresh")

    def test_wrong_schema_snapshot_treated_as_fresh(self) -> None:
        """schema_version 不匹配 → 视为 fresh(向后兼容:老版本 snapshot 不被使用)。"""
        from src.orchestrator.runtime_bootstrap import RuntimeBootstrap
        # 写入旧 schema 的 snapshot
        old_data = {
            "schema_version": "0.9",  # 不匹配
            "identity_id": "old",
            "boot_count": 99,
        }
        with open(self.snapshot_path, "w", encoding="utf-8") as f:
            json.dump(old_data, f)
        b = RuntimeBootstrap(
            orchestrator=self.orch,
            audit_log_path=self.audit_path,
            runtime_snapshot_path=self.snapshot_path,
            persistence_runtime=self.pr,
            self_model_orchestrator=self.smo,
            audit_logger=self.audit,
        )
        loop = b.start()
        self.assertIsNotNone(loop)
        # schema 不兼容 → 走 fresh
        self.assertEqual(b.boot_mode, "fresh")


if __name__ == "__main__":
    unittest.main()
