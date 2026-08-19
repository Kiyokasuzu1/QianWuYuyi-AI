# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_invariants.py

Phase 5.0-D1 Step 5: 不变量专项测试。

按 D1 设计要求验证以下不变量:
1. Task A 崩溃不影响 Task B
2. 重复 start 不会创建多个后台循环
3. stop 不会丢失 task state
4. FrozenClock 同样输入得到同样 Decision
5. 非法状态迁移不会破坏状态机
6. Lifecycle Core 不能 import 任何业务模块
7. Context 同一次执行: Task A Context != Task B Context
8. Task 异常被包装为 LifecycleError,不逃逸到 Manager
9. Emitter 不影响 Manager 主流程
10. Decision 引擎异常隔离
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys
import unittest
from typing import List

# 业务模块黑名单(若 Lifecycle Core import 这些则视为违规)
BUSINESS_MODULES = [
    "src.memory",
    "src.growth",
    "src.personality",
    "src.emotion",
    "src.relationship",
    "src.goal",
    "src.proactive",
    "src.dream",
    "src.orchestrator",
    "src.runtime.self_model",
    "src.runtime.audit_log",
]


def _make_simple_task(
    task_id: str,
    owner: str = "test",
    action=None,
    *,
    condition=None,
) -> "SimpleTask":
    from src.runtime.lifecycle.lifecycle_task import SimpleTask
    if action is None:
        action = lambda ctx: None
    return SimpleTask(
        task_id=task_id,
        owner=owner,
        action=action,
        condition=condition,
    )


# ============================================================
# 不变量 1: 任务隔离
# ============================================================
class TestTaskIsolationInvariant(unittest.TestCase):
    def test_task_a_crash_does_not_affect_task_b(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        def bad_action(ctx):
            raise RuntimeError("task A always fails")

        b_count = [0]

        def good_action(ctx):
            b_count[0] += 1

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_simple_task("t.A", action=bad_action))
            m.register(_make_simple_task("t.B", action=good_action))
            # 执行 10 次,A 始终失败,B 始终成功
            for _ in range(10):
                results = m.tick()
                statuses = {r.task_id: r.status for r in results}
                self.assertEqual(statuses["t.A"], LifecycleStatus.FAILED)
                self.assertEqual(statuses["t.B"], LifecycleStatus.SUCCESS)
            self.assertEqual(b_count[0], 10)
            # Manager 仍 RUNNING
            self.assertEqual(m.state, "RUNNING")
        finally:
            m.stop()

    def test_decision_exception_in_a_does_not_affect_b(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_decision import Predicate
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        def bad_decision(ctx, state):
            raise ValueError("decision error in A")

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_simple_task(
                "t.A",
                condition=Predicate(bad_decision),
            ))
            m.register(_make_simple_task("t.B", action=lambda c: None))
            results = m.tick()
            statuses = {r.task_id: r.status for r in results}
            # A 决策异常 -> SKIP; B 正常 -> SUCCESS
            self.assertEqual(statuses["t.A"], LifecycleStatus.SKIPPED)
            self.assertEqual(statuses["t.B"], LifecycleStatus.SUCCESS)
        finally:
            m.stop()


# ============================================================
# 不变量 2: 幂等性 (重复 start 不创建后台循环)
# ============================================================
class TestIdempotencyInvariant(unittest.TestCase):
    def test_repeated_start_no_duplicate_loop(self):
        """重复 start 5 次,Manager 状态仍为 RUNNING,但不应有 5 个 worker。"""
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_state import LifecycleState

        m = LifecycleManager()
        # 连续 start 5 次
        for _ in range(5):
            m.start()
        self.assertEqual(m.state, LifecycleState.RUNNING)
        # 历史中 start 事件应仅 1 次
        sm_history = m.state_machine.transition_history()
        starts = [h for h in sm_history if h.get("to") == "STARTING"]
        self.assertEqual(len(starts), 1)
        m.stop()

    def test_repeated_stop_no_error(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        m = LifecycleManager()
        m.start()
        for _ in range(5):
            m.stop()
        # 仍 STOPPED
        from src.runtime.lifecycle.lifecycle_state import LifecycleState
        self.assertEqual(m.state, LifecycleState.STOPPED)


# ============================================================
# 不变量 3: stop 不丢失 task state
# ============================================================
class TestStatePreservationInvariant(unittest.TestCase):
    def test_state_preserved_after_stop(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        m = LifecycleManager()
        m.start()
        m.register(_make_simple_task("t.a", action=lambda c: None))
        for _ in range(5):
            m.tick()
        ts_before = m.get_task_state("t.a")
        m.stop()
        # stop 后 task state 仍可访问
        ts_after = m.get_task_state("t.a")
        self.assertEqual(ts_before.run_count, ts_after.run_count)
        self.assertEqual(ts_before.last_run_at, ts_after.last_run_at)
        self.assertEqual(ts_before.task_id, ts_after.task_id)

    def test_state_preserved_through_pause_resume(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        m = LifecycleManager()
        m.start()
        m.register(_make_simple_task("t.a", action=lambda c: None))
        for _ in range(3):
            m.tick()
        ts_before = m.get_task_state("t.a")
        m.pause()
        m.resume()
        ts_after = m.get_task_state("t.a")
        self.assertEqual(ts_before.run_count, ts_after.run_count)


# ============================================================
# 不变量 4: FrozenClock 决定性
# ============================================================
class TestDeterminismInvariant(unittest.TestCase):
    def test_same_clock_same_state_same_decision(self):
        """相同 clock + 相同 task_state -> 相同 Decision。"""
        from src.runtime.lifecycle.internal.clock import FrozenClock
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.lifecycle_decision import (
            AfterInterval,
            Verdict,
        )

        cond = AfterInterval(60.0)
        # 第一轮
        clock_a = FrozenClock(initial=100.0)
        ctx_a = LifecycleContext(clock=clock_a, task_id="t")
        state_a = type("S", (), {"last_run_at": 50.0, "last_ended_at": None,
                                  "run_count": 0, "failure_count": 0,
                                  "skip_count": 0, "last_result": None,
                                  "last_error": None, "task_id": "t"})()
        d_a = cond.should_run(ctx_a, state_a)
        # 第二轮:重置 clock 到相同时间
        clock_b = FrozenClock(initial=100.0)
        ctx_b = LifecycleContext(clock=clock_b, task_id="t")
        state_b = type("S", (), {"last_run_at": 50.0, "last_ended_at": None,
                                  "run_count": 0, "failure_count": 0,
                                  "skip_count": 0, "last_result": None,
                                  "last_error": None, "task_id": "t"})()
        d_b = cond.should_run(ctx_b, state_b)
        # 完全一致
        self.assertEqual(d_a.verdict, d_b.verdict)
        self.assertEqual(d_a.reason, d_b.reason)
        self.assertEqual(d_a.confidence, d_b.confidence)

    def test_deterministic_run_100_times(self):
        """100 次执行同样决策,每次结果一致。"""
        from src.runtime.lifecycle.internal.clock import FrozenClock
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.lifecycle_decision import (
            AfterInterval,
            Always,
            Never,
            And,
            Or,
            Predicate,
            Verdict,
        )

        clock = FrozenClock(initial=50.0)
        cond = And(
            Always(),
            AfterInterval(30.0),
            Or(Never(), Predicate(lambda c, s: __import__("src.runtime.lifecycle.lifecycle_decision", fromlist=["Decision"]).Decision.run())),
        )
        verdicts = set()
        for _ in range(100):
            ctx = LifecycleContext(clock=clock, task_id="t")
            state = type("S", (), {"last_run_at": 0.0, "last_ended_at": None,
                                      "run_count": 0, "failure_count": 0,
                                      "skip_count": 0, "last_result": None,
                                      "last_error": None, "task_id": "t"})()
            d = cond.should_run(ctx, state)
            verdicts.add(d.verdict)
        # 100 次全部一致
        self.assertEqual(len(verdicts), 1)


# ============================================================
# 不变量 5: 非法状态迁移不破坏状态机
# ============================================================
class TestStateMachineInvariant(unittest.TestCase):
    def test_illegal_transition_no_crash(self):
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        sm = LifecycleStateMachine()
        # CREATED -> FAILED 非法
        self.assertFalse(sm.transition(LifecycleState.FAILED))
        self.assertEqual(sm.state, LifecycleState.CREATED)
        # CREATED -> PAUSED 非法
        self.assertFalse(sm.transition(LifecycleState.PAUSED))
        self.assertEqual(sm.state, LifecycleState.CREATED)
        # 合法转移
        self.assertTrue(sm.transition(LifecycleState.STARTING))
        self.assertTrue(sm.transition(LifecycleState.RUNNING))
        # 再次非法
        self.assertFalse(sm.transition(LifecycleState.STARTING))
        self.assertEqual(sm.state, LifecycleState.RUNNING)

    def test_repeated_illegal_transitions_state_unchanged(self):
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        sm = LifecycleStateMachine()
        for _ in range(100):
            # 真正非法的转移(注意 CREATED -> STOPPED 是合法的)
            self.assertFalse(sm.transition(LifecycleState.FAILED))
            self.assertFalse(sm.transition(LifecycleState.PAUSED))
            self.assertFalse(sm.transition(LifecycleState.RUNNING))
            self.assertFalse(sm.transition(LifecycleState.DRAINING))
        self.assertEqual(sm.state, LifecycleState.CREATED)

    def test_force_set_bypasses_validation(self):
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        sm = LifecycleStateMachine()
        sm.force_set(LifecycleState.RUNNING, reason="test")
        self.assertEqual(sm.state, LifecycleState.RUNNING)

    def test_history_not_corrupted_by_illegal_transitions(self):
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        sm = LifecycleStateMachine()
        for _ in range(50):
            sm.transition(LifecycleState.FAILED)  # 非法
        # history 不记录非法转移
        hist = sm.transition_history()
        self.assertEqual(len(hist), 0)


# ============================================================
# 不变量 6: 业务模块隔离
# ============================================================
class TestBusinessModuleIsolationInvariant(unittest.TestCase):
    def test_no_business_module_import_in_lifecycle(self):
        """lifecycle 目录下的所有 .py 文件不应 import 业务模块。"""
        lifecycle_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "src", "runtime", "lifecycle",
        )
        violations = []
        for root, _, files in os.walk(lifecycle_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fp:
                    content = fp.read()
                for biz in BUSINESS_MODULES:
                    # 简单字符串扫描(支持 from X import Y / import X)
                    if f"from {biz}" in content or f"import {biz}" in content:
                        violations.append((path, biz))
        self.assertEqual(
            violations, [],
            f"Lifecycle 模块不应 import 业务模块: {violations}",
        )

    def test_no_business_module_dependency_via_modules(self):
        """通过 sys.modules 验证 import lifecycle 不会触发业务模块。"""
        # 清空业务模块缓存(测试独立验证)
        for biz in BUSINESS_MODULES:
            for mod_name in list(sys.modules.keys()):
                if mod_name == biz or mod_name.startswith(biz + "."):
                    del sys.modules[mod_name]
        # 导入 lifecycle
        import src.runtime.lifecycle  # noqa: F401
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager  # noqa: F401
        # 验证业务模块未被触发
        for biz in BUSINESS_MODULES:
            self.assertNotIn(
                biz, sys.modules,
                f"导入 Lifecycle 触发了业务模块: {biz}",
            )


# ============================================================
# 不变量 7: Context 隔离
# ============================================================
class TestContextIsolationInvariant(unittest.TestCase):
    def test_task_a_context_differs_from_task_b_context(self):
        """同一次 tick 中,Task A 与 Task B 获得的 Context 是不同实例。"""
        from src.runtime.lifecycle.internal.clock import SystemClock
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager

        contexts = {}

        def capture(ctx):
            contexts[ctx.task_id] = ctx

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_simple_task("t.A", action=capture))
            m.register(_make_simple_task("t.B", action=capture))
            m.tick()
            self.assertIn("t.A", contexts)
            self.assertIn("t.B", contexts)
            # 不同实例
            self.assertIsNot(contexts["t.A"], contexts["t.B"])
            # task_id 区分
            self.assertEqual(contexts["t.A"].task_id, "t.A")
            self.assertEqual(contexts["t.B"].task_id, "t.B")
        finally:
            m.stop()

    def test_context_destroy_isolates_state(self):
        """destroy 后 context 的 quota 等状态被隔离。"""
        from src.runtime.lifecycle.internal.clock import SystemClock
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext

        ctx = LifecycleContext(clock=SystemClock(), task_id="t1", quota_max=10)
        ctx.check_quota()  # 1
        ctx.destroy()
        self.assertTrue(ctx.destroyed)
        # 新 Context 不受影响
        ctx2 = LifecycleContext(clock=SystemClock(), task_id="t2", quota_max=10)
        # quota 独立
        self.assertTrue(ctx2.check_quota())


# ============================================================
# 不变量 8: 异常包装
# ============================================================
class TestExceptionWrappingInvariant(unittest.TestCase):
    def test_any_exception_becomes_lifecycle_error(self):
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.internal.clock import FrozenClock
        from src.runtime.lifecycle.lifecycle_errors import LifecycleError
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        exceptions = [
            ValueError("v"),
            KeyError("k"),
            TypeError("t"),
            RuntimeError("r"),
            IOError("i"),
            TimeoutError("to"),
        ]
        for exc in exceptions:
            def make_action(e):
                def action(ctx):
                    raise e
                return action

            t = SimpleTask(task_id=f"t.{type(exc).__name__}", owner="test", action=make_action(exc))
            ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
            result = t.run_safely(ctx)
            self.assertEqual(result.status, LifecycleStatus.FAILED)
            self.assertIsInstance(result.error, LifecycleError)
            # result.error.message 应包含原始异常消息
            self.assertTrue(len(result.error.message) > 0)

    def test_lifecycle_error_passes_through(self):
        """LifecycleError 直接透传,不重复包装。"""
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.internal.clock import FrozenClock
        from src.runtime.lifecycle.lifecycle_errors import (
            ErrorCategory,
            LifecycleError,
        )
        from src.runtime.lifecycle.lifecycle_result import LifecycleStatus

        def action(ctx):
            raise LifecycleError.transient("custom", retry_after=1.0)

        t = SimpleTask(task_id="t", owner="test", action=action)
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        result = t.run_safely(ctx)
        self.assertEqual(result.status, LifecycleStatus.FAILED)
        self.assertEqual(result.error.category, ErrorCategory.TRANSIENT)
        self.assertEqual(result.error.retry_after, 1.0)


# ============================================================
# 不变量 9: Emitter 不影响 Manager 主流程
# ============================================================
class TestEmitterIsolationInvariant(unittest.TestCase):
    def test_closed_emitter_does_not_break_tick(self):
        from src.runtime.lifecycle.internal.event_emitter import EventEmitter
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        e = EventEmitter()
        e.close()
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.x", action=lambda c: None))
            results = m.tick()
            # 即使 Emitter 关闭,tick 仍正常返回
            self.assertEqual(len(results), 1)
        finally:
            m.stop()

    def test_emitter_raises_does_not_break_tick(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        # 构造一个 emit 时抛错的 emitter

        class _BadEmitter:
            name = "bad"
            history_capacity = 0
            is_closed = False

            def emit(self, *args, **kwargs):
                raise RuntimeError("emit failed")

            def history(self, *args, **kwargs):
                return []

        m = LifecycleManager(emitter=_BadEmitter())
        m.start()
        try:
            m.register(_make_simple_task("t.x", action=lambda c: None))
            # tick 不应因 emit 抛错而崩溃
            results = m.tick()
            self.assertEqual(len(results), 1)
        finally:
            m.stop()


# ============================================================
# 不变量 10: Decision 引擎异常隔离
# ============================================================
class TestDecisionEngineInvariant(unittest.TestCase):
    def test_decision_engine_handles_invalid_input(self):
        from src.runtime.lifecycle.lifecycle_decision import DecisionEngine
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.internal.clock import FrozenClock
        from src.runtime.lifecycle.lifecycle_decision import TaskState

        # 各种非法输入都应得到 SKIP(不抛错)
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        state = TaskState(task_id="t")
        invalid_inputs = [
            None,
            "string",
            123,
            ["list"],
            {"dict": True},
        ]
        engine = DecisionEngine()
        for inp in invalid_inputs:
            d = engine.evaluate(inp, ctx, state)  # type: ignore[arg-type]
            from src.runtime.lifecycle.lifecycle_decision import Verdict
            self.assertEqual(d.verdict, Verdict.SKIP)


# ============================================================
# 不变量: 第三方依赖检查
# ============================================================
class TestNoThirdPartyDependency(unittest.TestCase):
    def test_lifecycle_uses_only_stdlib(self):
        """lifecycle 目录下不应 import 任何第三方包(仅 stdlib + src.runtime.lifecycle.*)。"""
        lifecycle_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "src", "runtime", "lifecycle",
        )
        # 已知 stdlib 模块名前缀
        stdlib_prefixes = {
            "__future__", "abc", "argparse", "ast", "asyncio", "base64", "collections", "contextlib",
            "copy", "dataclasses", "datetime", "enum", "functools", "hashlib", "io",
            "itertools", "json", "logging", "math", "os", "pathlib", "pickle", "queue",
            "random", "re", "shutil", "signal", "socket", "sqlite3", "string", "subprocess",
            "sys", "tempfile", "threading", "time", "traceback", "types", "typing",
            "unittest", "urllib", "uuid", "warnings", "weakref",
        }
        # 允许的内部模块前缀
        allowed_prefixes = {
            "src.runtime.lifecycle",
        }
        violations = []
        for root, _, files in os.walk(lifecycle_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                with open(path, "r", encoding="utf-8") as fp:
                    content = fp.read()
                # 解析 import 语句
                import ast as _ast
                try:
                    tree = _ast.parse(content)
                except SyntaxError:
                    continue
                for node in _ast.walk(tree):
                    if isinstance(node, _ast.Import):
                        for alias in node.names:
                            top = alias.name.split(".")[0]
                            if top not in stdlib_prefixes and not any(
                                alias.name.startswith(p) for p in allowed_prefixes
                            ):
                                violations.append((path, alias.name))
                    elif isinstance(node, _ast.ImportFrom):
                        if node.module is None:
                            continue
                        # 相对导入 (level > 0) 允许
                        if node.level and node.level > 0:
                            continue
                        top = node.module.split(".")[0]
                        if top not in stdlib_prefixes and not any(
                            node.module.startswith(p) for p in allowed_prefixes
                        ):
                            violations.append((path, node.module))
        self.assertEqual(
            violations, [],
            f"Lifecycle 引入非 stdlib / 非 lifecycle 依赖: {violations}",
        )


if __name__ == "__main__":
    unittest.main()
