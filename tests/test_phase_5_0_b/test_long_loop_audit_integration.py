# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_b/test_long_loop_audit_integration.py

Phase 5.0-B: LongLoop 接入 RuntimeAuditLogger 后的集成测试。

验证:
- 注入 audit_logger 后,生命周期事件被记录
- 注入 crashy audit_logger 不影响主流程
- 默认 None 行为不变
- event_start / event_end / exception / checkpoint / loop_start / loop_stop 均被记录
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 src 可导入
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.orchestrator.long_loop import LongLoop  # noqa: E402
from src.runtime.audit_log.runtime_audit_logger import (  # noqa: E402
    RuntimeAuditLogger,
    AuditEvent,
)


# ============================================================
# FakeOrchestrator: 模拟 + 可注入崩溃轮
# ============================================================
class FakeOrchestrator:
    def __init__(self, replies: Optional[List[str]] = None, fail_on: Optional[int] = None) -> None:
        self._replies = list(replies or ["reply"])
        self._idx = 0
        self._turn = 0
        self._fail_on = fail_on
        self.history: List[str] = []

    def process(self, user_input: str) -> str:
        self._turn += 1
        if self._fail_on is not None and self._turn == self._fail_on:
            raise RuntimeError(f"fake failure at turn {self._turn}")
        self.history.append(user_input)
        if self._idx < len(self._replies):
            r = self._replies[self._idx]
            self._idx += 1
        else:
            r = "reply"
        return r

    def clear_history(self) -> None:
        self.history = []


# ============================================================
# TestBackwardCompat: 默认 audit_logger=None 行为不变
# ============================================================
class TestBackwardCompat(unittest.TestCase):
    def test_default_construction_no_audit(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        self.assertIsNone(loop._audit_logger)
        h = loop.health_check()
        self.assertFalse(h["components"]["audit_logger"])

    def test_run_without_audit_still_works(self):
        loop = LongLoop(
            input_provider=lambda: "exit",
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state.value, "STOPPED")


# ============================================================
# TestAuditHook: 注入 audit_logger 后被调用
# ============================================================
class TestAuditHook(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="ll_audit_")
        self.log_path = os.path.join(self._tmpdir, "audit.jsonl")
        self.audit = RuntimeAuditLogger(log_path=self.log_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_loop_start_and_stop_recorded(self):
        loop = LongLoop(
            input_provider=lambda: "exit",
            orchestrator=FakeOrchestrator(),
            audit_logger=self.audit,
        )
        loop.run()
        records = self.audit.read_all()
        events = [r.get("event") for r in records]
        self.assertIn("loop_start", events)
        self.assertIn("loop_stop", events)

    def test_each_turn_records_event_start_and_end(self):
        inputs = iter(["hi", "how are you", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(replies=["a", "b"]),
            audit_logger=self.audit,
            on_reply=lambda u, r: None,
        )
        loop.run()
        records = self.audit.read_all()
        starts = [r for r in records if r.get("event") == "event_start"]
        ends = [r for r in records if r.get("event") == "event_end"]
        # 2 轮对话 + exit 是 turn_count 之外的循环退出
        # exit 关键字不调用 _handle_turn,所以只 2 轮
        self.assertEqual(len(starts), 2)
        self.assertEqual(len(ends), 2)
        for s in starts:
            self.assertIn("event_id", s)
        for e in ends:
            self.assertTrue(e.get("success"))

    def test_exception_recorded(self):
        inputs = iter(["hi", "boom", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(fail_on=1),
            audit_logger=self.audit,
            on_reply=lambda u, r: None,
        )
        loop.run()
        records = self.audit.read_all()
        exceptions = [r for r in records if r.get("event") == "exception"]
        self.assertGreaterEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["phase"], "long_loop.orchestrator.process")
        # 对应 event_end 记录为 success=False
        ends = [r for r in records if r.get("event") == "event_end" and r.get("success") is False]
        self.assertEqual(len(ends), 1)

    def test_checkpoint_recorded(self):
        states: List[Dict[str, Any]] = []

        def cp(state: Dict[str, Any]) -> None:
            states.append(dict(state))

        inputs = iter(["hi", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            audit_logger=self.audit,
            on_reply=lambda u, r: None,
            checkpoint_provider=cp,
        )
        loop.run()
        records = self.audit.read_all()
        ckpts = [r for r in records if r.get("event") == "checkpoint"]
        # 1 final
        self.assertGreaterEqual(len(ckpts), 1)
        for c in ckpts:
            self.assertEqual(c["reason"], "final")

    def test_periodic_checkpoint_recorded(self):
        inputs = iter(["a", "b", "c", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(replies=["1", "2", "3"]),
            audit_logger=self.audit,
            on_reply=lambda u, r: None,
            checkpoint_interval=2,
            checkpoint_provider=lambda s: None,
        )
        loop.run()
        records = self.audit.read_all()
        ckpts = [r for r in records if r.get("event") == "checkpoint"]
        # 周期 2:turn 2 触发;final
        # turn 1 后:1%2!=0 不触发;turn 2:2%2==0 触发;turn 3:3%2!=0 不触发
        periodic = [c for c in ckpts if c["reason"] == "periodic"]
        self.assertGreaterEqual(len(periodic), 1)

    def test_crashy_audit_does_not_break_loop(self):
        """audit_logger 抛错不影响主流程。"""

        class CrashyAudit:
            def log_event_start(self, *a, **k): raise RuntimeError("audit boom")
            def log_event_end(self, *a, **k): raise RuntimeError("audit boom")
            def log_exception(self, *a, **k): raise RuntimeError("audit boom")
            def log_checkpoint(self, *a, **k): raise RuntimeError("audit boom")
            def log_loop_start(self, *a, **k): raise RuntimeError("audit boom")
            def log_loop_stop(self, *a, **k): raise RuntimeError("audit boom")

        crashy = CrashyAudit()
        inputs = iter(["hi", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            audit_logger=crashy,
            on_reply=lambda u, r: None,
        )
        # 不应抛
        loop.run()
        self.assertEqual(loop.state.value, "STOPPED")
        self.assertEqual(loop.turn_count, 1)


# ============================================================
# TestAuditDisabled: audit_logger 关闭时 no-op
# ============================================================
class TestAuditDisabled(unittest.TestCase):
    def test_disabled_audit_does_not_write(self):
        tmpdir = tempfile.mkdtemp()
        try:
            log_path = os.path.join(tmpdir, "audit.jsonl")
            audit = RuntimeAuditLogger(log_path=log_path, enabled=False)
            loop = LongLoop(
                input_provider=lambda: "exit",
                orchestrator=FakeOrchestrator(),
                audit_logger=audit,
            )
            loop.run()
            self.assertEqual(audit.write_count, 0)
            self.assertFalse(os.path.exists(log_path))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
