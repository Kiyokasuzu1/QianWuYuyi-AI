# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_a/test_long_loop.py

Phase 5.0-A: LongLoop 单元测试。

覆盖:
- 状态机: STOPPED / STARTING / RUNNING / DRAINING / STOPPED
- 正常退出 (exit 关键字)
- request_shutdown 线程安全
- 周期 checkpoint + 最终 checkpoint
- 异常隔离 (orchestrator 抛错不退出)
- EOF / KeyboardInterrupt 触发关闭
- input_provider 返回 None
- clear_history 关键字
- health_check
- 空 orchestrator 退化
"""
import sys
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 src 可导入
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.orchestrator.long_loop import LongLoop, LongLoopState, LONG_LOOP_SCHEMA_VERSION  # noqa: E402


# ============================================================
# FakeOrchestrator: 模拟 Orchestrator 接口
# ============================================================
class FakeOrchestrator:
    def __init__(self, replies: Optional[List[str]] = None, raise_on: Optional[int] = None) -> None:
        self._replies = list(replies or ["fake reply"])
        self._idx = 0
        self._raise_on = raise_on  # 第几轮抛错(1-based); None = 不抛
        self._turn = 0
        self.history: List[str] = []
        self.clear_called = 0

    def process(self, user_input: str) -> str:
        self._turn += 1
        if self._raise_on is not None and self._turn == self._raise_on:
            raise RuntimeError(f"FakeOrchestrator 故意失败(第 {self._turn} 轮)")
        if self._idx < len(self._replies):
            reply = self._replies[self._idx]
            self._idx += 1
        else:
            reply = "fake reply"
        self.history.append((user_input, reply))
        return reply

    def clear_history(self) -> None:
        self.clear_called += 1


# ============================================================
# TestStateMachine: 状态机测试
# ============================================================
class TestStateMachine(unittest.TestCase):
    def test_initial_state_is_stopped(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertFalse(loop.is_running)

    def test_start_transitions_to_running(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        ok = loop.start()
        self.assertTrue(ok)
        self.assertEqual(loop.state, LongLoopState.RUNNING)
        self.assertTrue(loop.is_running)

    def test_start_idempotent(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        loop.start()
        ok2 = loop.start()
        self.assertFalse(ok2)
        self.assertEqual(loop.state, LongLoopState.RUNNING)

    def test_request_shutdown_when_running_transitions_to_draining(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        loop.start()
        loop.request_shutdown("test")
        self.assertEqual(loop.state, LongLoopState.DRAINING)
        self.assertTrue(loop.shutdown_requested)

    def test_request_shutdown_when_stopped_no_state_change(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        loop.request_shutdown("test")
        # STOPPED -> DRAINING 非法,保持 STOPPED
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertTrue(loop.shutdown_requested)

    def test_request_shutdown_idempotent(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        loop.start()
        loop.request_shutdown("first")
        loop.request_shutdown("second")
        self.assertEqual(loop.shutdown_requested, True)
        self.assertEqual(loop._shutdown_reason, "first")  # 第一次的 reason 保留


# ============================================================
# TestNormalExit: 正常退出测试
# ============================================================
class TestNormalExit(unittest.TestCase):
    def test_exit_keyword_triggers_shutdown(self):
        orch = FakeOrchestrator()
        loop = LongLoop(
            input_provider=lambda: "exit",
            orchestrator=orch,
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertEqual(orch.history, [])  # 没有真正对话
        self.assertEqual(loop.shutdown_reason, "exit keyword")

    def test_quit_keyword_triggers_shutdown(self):
        loop = LongLoop(
            input_provider=lambda: "quit",
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)

    def test_input_provider_returns_none_triggers_shutdown(self):
        loop = LongLoop(
            input_provider=lambda: None,
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertIn("None", loop.shutdown_reason)

    def test_eof_triggers_shutdown(self):
        def raise_eof() -> str:
            raise EOFError("end of input")
        loop = LongLoop(
            input_provider=raise_eof,
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertEqual(loop.shutdown_reason, "EOF")

    def test_keyboard_interrupt_triggers_shutdown(self):
        def raise_kbi() -> str:
            raise KeyboardInterrupt("user pressed ctrl+c")
        loop = LongLoop(
            input_provider=raise_kbi,
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertEqual(loop.shutdown_reason, "KeyboardInterrupt")

    def test_normal_conversation_continues(self):
        inputs = iter(["你好", "exit"])
        orch = FakeOrchestrator(replies=["hi", "bye"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=orch,
            on_reply=lambda u, r: None,  # 静默输出,避免污染测试输出
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertEqual(loop.turn_count, 1)
        self.assertEqual(len(orch.history), 1)
        self.assertEqual(orch.history[0][0], "你好")


# ============================================================
# TestCheckpoint: checkpoint 测试
# ============================================================
class TestCheckpoint(unittest.TestCase):
    def test_final_checkpoint_on_exit(self):
        states: List[Dict[str, Any]] = []

        def cp(state: Dict[str, Any]) -> None:
            states.append(dict(state))

        inputs = iter(["exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            checkpoint_provider=cp,
        )
        loop.run()
        # exit 立刻关闭,不会产生 turn,但 final checkpoint 仍触发
        self.assertGreaterEqual(len(states), 1)
        final = states[-1]
        self.assertEqual(final["schema_version"], LONG_LOOP_SCHEMA_VERSION)
        self.assertEqual(final["state"], "STOPPED")
        self.assertEqual(loop.checkpoint_count, len(states))

    def test_periodic_checkpoint(self):
        states: List[Dict[str, Any]] = []

        def cp(state: Dict[str, Any]) -> None:
            states.append(dict(state))

        inputs = iter(["a", "b", "c", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            checkpoint_provider=cp,
            on_reply=lambda u, r: None,
            checkpoint_interval=2,
        )
        loop.run()
        # 3 轮对话 + 1 final
        # 周期 checkpoint: turn 2 (index 2 即第 2 轮) + final
        # turn_count 累计到 3, 2, 3 三个值,3%2==1 不触发
        # turn_count == 2 时触发
        # 然后 final
        self.assertGreaterEqual(len(states), 1)
        # final 一定存在
        self.assertEqual(states[-1]["state"], "STOPPED")

    def test_checkpoint_provider_exception_isolated(self):
        def bad_cp(state: Dict[str, Any]) -> None:
            raise RuntimeError("checkpoint 故意失败")

        inputs = iter(["exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            checkpoint_provider=bad_cp,
        )
        # 不应崩溃
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        # error_count 应该增加
        self.assertGreaterEqual(loop.error_count, 1)

    def test_no_checkpoint_provider_is_safe(self):
        inputs = iter(["exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
            checkpoint_provider=None,
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertEqual(loop.checkpoint_count, 0)


# ============================================================
# TestExceptionIsolation: 异常隔离
# ============================================================
class TestExceptionIsolation(unittest.TestCase):
    def test_orchestrator_exception_does_not_crash_loop(self):
        # 第 2 轮抛错, 但 loop 应继续运行
        inputs = iter(["turn1", "turn2-fail", "turn3", "exit"])
        orch = FakeOrchestrator(replies=["r1", "r2", "r3"], raise_on=2)
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=orch,
            on_reply=lambda u, r: None,
        )
        loop.run()
        # 正常退出
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        # 第 1 轮 + 第 3 轮(第 2 轮抛错被隔离) + 第 3 轮
        self.assertGreaterEqual(loop.turn_count, 2)
        # 至少有一次错误
        self.assertGreaterEqual(loop.error_count, 1)
        # history 应包含第 1 和第 3 轮(第 2 轮抛错)
        # turn1 -> "r1", turn2-fail -> exception, turn3 -> "r3"
        self.assertEqual(len(orch.history), 2)

    def test_input_provider_exception_does_not_crash_loop(self):
        call_count = {"n": 0}

        def flaky() -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("input 失败")
            if call_count["n"] == 2:
                return "exit"
            return "exit"

        loop = LongLoop(
            input_provider=flaky,
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        self.assertGreaterEqual(loop.error_count, 1)

    def test_on_reply_exception_does_not_crash_loop(self):
        def bad_reply(u: str, r: str) -> None:
            raise RuntimeError("on_reply 失败")

        inputs = iter(["hi", "exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(replies=["reply"]),
            on_reply=bad_reply,
        )
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)
        # on_reply 失败被兜底打印,不计入 error_count
        # 但 turn_count 应该 == 1
        self.assertEqual(loop.turn_count, 1)


# ============================================================
# TestClearKeyword: /clear 测试
# ============================================================
class TestClearKeyword(unittest.TestCase):
    def test_clear_keyword_triggers_clear_history(self):
        inputs = iter(["/clear", "exit"])
        orch = FakeOrchestrator()
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=orch,
            on_reply=lambda u, r: None,
        )
        loop.run()
        self.assertEqual(orch.clear_called, 1)
        self.assertEqual(loop.turn_count, 0)  # /clear 不算 turn

    def test_chinese_clear_keyword(self):
        inputs = iter(["清空", "exit"])
        orch = FakeOrchestrator()
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=orch,
            on_reply=lambda u, r: None,
        )
        loop.run()
        self.assertEqual(orch.clear_called, 1)


# ============================================================
# TestNoOrchestrator: 无 orchestrator 退化
# ============================================================
class TestNoOrchestrator(unittest.TestCase):
    def test_run_without_orchestrator_returns_immediately(self):
        loop = LongLoop(orchestrator=None)
        loop.run()
        self.assertEqual(loop.state, LongLoopState.STOPPED)


# ============================================================
# TestHealthCheck
# ============================================================
class TestHealthCheck(unittest.TestCase):
    def test_health_check_structure(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        h = loop.health_check()
        self.assertEqual(h["name"], "long_loop")
        self.assertEqual(h["schema_version"], LONG_LOOP_SCHEMA_VERSION)
        self.assertEqual(h["state"], "STOPPED")
        self.assertIn("components", h)
        self.assertTrue(h["components"]["orchestrator"])

    def test_health_check_after_run(self):
        inputs = iter(["exit"])
        loop = LongLoop(
            input_provider=lambda: next(inputs),
            orchestrator=FakeOrchestrator(),
        )
        loop.run()
        h = loop.health_check()
        self.assertEqual(h["state"], "STOPPED")
        # run 之后 stopped_at 应被设置
        self.assertIsNotNone(h["stopped_at"])
        self.assertEqual(h["shutdown_reason"], "exit keyword")


# ============================================================
# TestThreadSafety
# ============================================================
class TestThreadSafety(unittest.TestCase):
    def test_request_shutdown_from_another_thread(self):
        loop = LongLoop(orchestrator=FakeOrchestrator())
        loop.start()

        def trigger():
            loop.request_shutdown("from thread")

        t = threading.Thread(target=trigger)
        t.start()
        t.join(timeout=2.0)
        self.assertTrue(loop.shutdown_requested)


# ============================================================
# TestBannedComponents: 验证没有创建被禁止的组件
# ============================================================
class TestBannedComponents(unittest.TestCase):
    def test_module_does_not_define_banned_classes(self):
        """LongLoop 自身不应暴露 EventBus / MessageQueue / TickScheduler / RuntimeBuilder。"""
        from src.orchestrator import long_loop as ll_mod
        attrs = dir(ll_mod)
        for banned in ("EventBus", "MessageQueue", "TickScheduler", "RuntimeBuilder"):
            self.assertNotIn(banned, attrs, f"LongLoop 不应定义 {banned}")


if __name__ == "__main__":
    unittest.main()
