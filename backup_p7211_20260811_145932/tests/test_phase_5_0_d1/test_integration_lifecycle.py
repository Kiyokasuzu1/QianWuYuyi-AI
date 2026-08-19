# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_integration_lifecycle.py

Phase 5.0-D1 Step 5: 跨模块集成测试。

覆盖:
- Clock + Context + Decision + Task + Registry + Emitter + Manager 协同
- FrozenClock 决定性: 同样输入得到同样结果
- Decision 引擎与 Manager 决策路径一致性
- Emitter 事件贯穿决策 -> 执行 -> 结果全流程
- TaskState / LifecycleResult / LifecycleError 跨模块流转
- Manager + Registry 事件联动
"""
from __future__ import annotations

import unittest
from typing import List, Optional

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.internal.event_emitter import (
    EVENT_TASK_REGISTERED,
    EVENT_TASK_UNREGISTERED,
    EventEmitter,
)
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import (
    AfterFirstRun,
    AfterInterval,
    Always,
    And,
    Decision,
    Never,
    Or,
    Predicate,
    TaskState,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_errors import LifecycleError
from src.runtime.lifecycle.lifecycle_manager import (
    EVENT_MANAGER_STARTED,
    EVENT_MANAGER_STOPPED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_TASK_SKIPPED,
    EVENT_TASK_STARTED,
    LifecycleManager,
)
from src.runtime.lifecycle.lifecycle_registry import LifecycleRegistry
from src.runtime.lifecycle.lifecycle_result import LifecycleResult, LifecycleStatus
from src.runtime.lifecycle.lifecycle_state import LifecycleState
from src.runtime.lifecycle.lifecycle_task import SimpleTask


def _make_simple_task(
    task_id: str,
    owner: str = "test",
    action=None,
    *,
    priority: int = 5,
    condition=None,
) -> SimpleTask:
    if action is None:
        action = lambda ctx: None
    return SimpleTask(
        task_id=task_id,
        owner=owner,
        action=action,
        priority=priority,
        condition=condition,
    )


# ============================================================
# Clock + Context + Decision 一致性
# ============================================================
class TestClockContextDecision(unittest.TestCase):
    def test_frozen_clock_decision_determinism(self):
        """同样输入 (clock + task_state) 同样 Decision。"""
        clock = FrozenClock(initial=100.0)
        cond = AfterInterval(60.0)
        results_a = []
        results_b = []
        for _ in range(10):
            ctx = LifecycleContext(clock=clock, task_id="t.x")
            state = TaskState(task_id="t.x", last_run_at=50.0)
            d = cond.should_run(ctx, state)
            results_a.append((d.verdict, d.reason))
        # 重置 clock 到相同时间,使用相同 state
        clock.set(100.0)
        for _ in range(10):
            ctx = LifecycleContext(clock=clock, task_id="t.x")
            state = TaskState(task_id="t.x", last_run_at=50.0)
            d = cond.should_run(ctx, state)
            results_b.append((d.verdict, d.reason))
        self.assertEqual(results_a, results_b)

    def test_frozen_clock_after_first_run(self):
        clock = FrozenClock(initial=0.0)
        cond = AfterFirstRun()
        ctx = LifecycleContext(clock=clock, task_id="t.x")
        # 首次应 SKIP
        d1 = cond.should_run(ctx, TaskState(task_id="t.x"))
        self.assertEqual(d1.verdict, Verdict.SKIP)
        # 模拟已运行 -> RUN
        d2 = cond.should_run(ctx, TaskState(task_id="t.x", last_run_at=0.0))
        self.assertEqual(d2.verdict, Verdict.RUN)

    def test_context_clock_propagation(self):
        clock = FrozenClock(initial=42.0)
        ctx = LifecycleContext(clock=clock, task_id="t")
        self.assertEqual(ctx.now(), 42.0)
        # 推进时间
        clock.advance(10.0)
        self.assertEqual(ctx.now(), 52.0)


# ============================================================
# Task + Context + Result 流转
# ============================================================
class TestTaskContextResult(unittest.TestCase):
    def test_task_action_receives_context(self):
        captured = []

        def action(ctx):
            captured.append(ctx.task_id)
            captured.append(ctx.now())
            return None

        clock = FrozenClock(initial=10.0)
        ctx = LifecycleContext(clock=clock, task_id="t.action")
        t = _make_simple_task("t.action", action=action)
        result = t.run_safely(ctx)
        self.assertEqual(result.status, LifecycleStatus.SUCCESS)
        self.assertEqual(captured, ["t.action", 10.0])
        # duration >= 0
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_task_return_dict_normalized(self):
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        t = _make_simple_task("t", action=lambda c: {"metrics": {"k": 1}})
        result = t.run_safely(ctx)
        self.assertEqual(result.status, LifecycleStatus.SUCCESS)
        self.assertEqual(result.metrics.get("k"), 1)

    def test_task_return_lifecycle_result_preserved(self):
        custom = LifecycleResult(
            task_id="t",
            status=LifecycleStatus.SUCCESS,
            started_at=1.0,
            ended_at=2.0,
            metrics={"custom": True},
        )
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        t = _make_simple_task("t", action=lambda c: custom)
        result = t.run_safely(ctx)
        self.assertEqual(result.metrics.get("custom"), True)
        self.assertEqual(result.duration_ms, 1000)

    def test_task_exception_wrapped(self):
        def bad(ctx):
            raise ValueError("bad")

        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        t = _make_simple_task("t", action=bad)
        result = t.run_safely(ctx)
        self.assertEqual(result.status, LifecycleStatus.FAILED)
        self.assertIsInstance(result.error, LifecycleError)
        self.assertIn("bad", result.error.message)

    def test_task_cancelled_context(self):
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        ctx.cancel()
        t = _make_simple_task("t", action=lambda c: None)
        result = t.run_safely(ctx)
        self.assertEqual(result.status, LifecycleStatus.CANCELLED)

    def test_task_on_success_called(self):
        called = []

        class T(SimpleTask):
            def on_success(self, result):
                called.append(result.status)

        t = T(task_id="t", owner="test", action=lambda c: None)
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        t.run_safely(ctx)
        self.assertEqual(called, [LifecycleStatus.SUCCESS])

    def test_task_on_failure_called(self):
        called = []

        class T(SimpleTask):
            def on_failure(self, error):
                called.append(error.category)

        def bad(ctx):
            raise ValueError("x")

        t = T(task_id="t", owner="test", action=bad)
        ctx = LifecycleContext(clock=FrozenClock(initial=0.0), task_id="t")
        t.run_safely(ctx)
        self.assertEqual(len(called), 1)


# ============================================================
# Manager + Registry + Emitter 协同
# ============================================================
class TestManagerRegistryEmitter(unittest.TestCase):
    def test_register_emits_via_registry(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.a"))
            events = [ev.event_type for ev in e.history()]
            self.assertIn(EVENT_TASK_REGISTERED, events)
        finally:
            m.stop()

    def test_unregister_emits_via_registry(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.a"))
            m.unregister("t.a")
            events = [ev.event_type for ev in e.history()]
            self.assertIn(EVENT_TASK_UNREGISTERED, events)
        finally:
            m.stop()

    def test_tick_emits_full_event_sequence(self):
        e = EventEmitter(history_capacity=50)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.run", action=lambda c: None))
            m.tick()
            events = [ev.event_type for ev in e.history()]
            # 必须有: manager.started, task.registered, manager.tick, task.started, task.completed
            self.assertIn(EVENT_MANAGER_STARTED, events)
            self.assertIn(EVENT_TASK_REGISTERED, events)
            self.assertIn(EVENT_TASK_STARTED, events)
            self.assertIn(EVENT_TASK_COMPLETED, events)
        finally:
            m.stop()

    def test_failure_emits_task_failed(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.bad", action=lambda c: (_ for _ in ()).throw(RuntimeError("x"))))
            m.tick()
            events = [ev.event_type for ev in e.history()]
            self.assertIn(EVENT_TASK_FAILED, events)
        finally:
            m.stop()

    def test_skip_emits_task_skipped(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.skip", condition=Never()))
            m.tick()
            events = [ev.event_type for ev in e.history()]
            self.assertIn(EVENT_TASK_SKIPPED, events)
        finally:
            m.stop()

    def test_history_contains_all_results(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.ok", action=lambda c: None))
            m.register(_make_simple_task("t.bad", action=lambda c: (_ for _ in ()).throw(RuntimeError("x"))))
            m.register(_make_simple_task("t.skip", condition=Never()))
            results = m.tick()
            self.assertEqual(len(results), 3)
            statuses = {r.task_id: r.status for r in results}
            self.assertEqual(statuses["t.ok"], LifecycleStatus.SUCCESS)
            self.assertEqual(statuses["t.bad"], LifecycleStatus.FAILED)
            self.assertEqual(statuses["t.skip"], LifecycleStatus.SKIPPED)
        finally:
            m.stop()


# ============================================================
# 复合决策条件集成
# ============================================================
class TestCompositeDecisionIntegration(unittest.TestCase):
    def test_and_or_not_with_manager(self):
        """复杂条件 (And/Or/Not) 在 Manager tick 中按预期工作。"""
        m = LifecycleManager()
        m.start()
        try:
            # And(Always, AfterFirstRun) -> 首次 SKIP(AfterFirstRun 主导)
            t1 = _make_simple_task(
                "t.and",
                condition=And(Always(), AfterFirstRun()),
            )
            # Or(Never, Always) -> Always 胜出 -> RUN
            t2 = _make_simple_task("t.or", condition=Or(Never(), Always()))
            # Not(Never) -> RUN
            t3 = _make_simple_task("t.not", condition=Predicate(lambda c, s: Decision.run("ok")))
            m.register(t1)
            m.register(t2)
            m.register(t3)
            results = {r.task_id: r.status for r in m.tick()}
            self.assertEqual(results["t.and"], LifecycleStatus.SKIPPED)
            self.assertEqual(results["t.or"], LifecycleStatus.SUCCESS)
            self.assertEqual(results["t.not"], LifecycleStatus.SUCCESS)
        finally:
            m.stop()

    def test_after_interval_integration(self):
        clock = FrozenClock(initial=100.0)
        m = LifecycleManager(clock=clock)
        m.start()
        try:
            t = _make_simple_task("t.iv", condition=AfterInterval(60.0))
            m.register(t)
            # 首次 RUN
            r1 = m.tick()
            self.assertEqual(r1[0].status, LifecycleStatus.SUCCESS)
            # 推进时间 < 60: SKIP
            clock.advance(30.0)
            r2 = m.tick()
            self.assertEqual(r2[0].status, LifecycleStatus.SKIPPED)
            # 推进时间 >= 60: RUN
            clock.advance(40.0)
            r3 = m.tick()
            self.assertEqual(r3[0].status, LifecycleStatus.SUCCESS)
        finally:
            m.stop()


# ============================================================
# 跨模块数据流验证
# ============================================================
class TestCrossModuleDataFlow(unittest.TestCase):
    def test_task_state_lifecycle(self):
        """完整生命周期: register -> tick -> state 记录 -> unregister -> state 清理。"""
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_simple_task("t.life", action=lambda c: None))
            # register 后 state 应已创建(run_count=0)
            ts = m.get_task_state("t.life")
            self.assertIsNotNone(ts)
            self.assertEqual(ts.run_count, 0)
            # 第一次 tick 后 run_count=1
            m.tick()
            ts = m.get_task_state("t.life")
            self.assertIsNotNone(ts)
            self.assertEqual(ts.run_count, 1)
            # 第二次 tick
            m.tick()
            ts = m.get_task_state("t.life")
            self.assertEqual(ts.run_count, 2)
            # 注销后 state 清理
            m.unregister("t.life")
            self.assertIsNone(m.get_task_state("t.life"))
        finally:
            m.stop()

    def test_emitter_payload_consistency(self):
        """事件 payload 包含 task_id / status / duration_ms 等关键字段。"""
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_simple_task("t.p", action=lambda c: None))
            m.tick()
            completed = [ev for ev in e.history() if ev.event_type == EVENT_TASK_COMPLETED]
            self.assertEqual(len(completed), 1)
            payload = completed[0].payload
            self.assertEqual(payload["task_id"], "t.p")
            self.assertEqual(payload["status"], "SUCCESS")
            self.assertIn("duration_ms", payload)
        finally:
            m.stop()

    def test_history_filter_with_decision(self):
        """历史可按 task_id 与 status 过滤。"""
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_simple_task("t.f1", action=lambda c: None))
            m.register(_make_simple_task("t.f2", action=lambda c: (_ for _ in ()).throw(RuntimeError("x"))))
            m.tick()
            ok = m.history(status=LifecycleStatus.SUCCESS)
            failed = m.history(status=LifecycleStatus.FAILED)
            self.assertEqual(len(ok), 1)
            self.assertEqual(len(failed), 1)
            self.assertEqual(ok[0].task_id, "t.f1")
            self.assertEqual(failed[0].task_id, "t.f2")
        finally:
            m.stop()


# ============================================================
# Snapshot view 集成
# ============================================================
class TestSnapshotViewIntegration(unittest.TestCase):
    def test_snapshot_view_in_context(self):
        snapshot = {"boot_count": 5, "last_state": "RUNNING"}
        m = LifecycleManager(snapshot_view=snapshot)
        m.start()
        try:
            captured = {}

            def action(ctx):
                captured["snapshot"] = dict(ctx.snapshot_view) if ctx.snapshot_view else {}

            m.register(_make_simple_task("t.snap", action=action))
            m.tick()
            self.assertEqual(captured.get("snapshot"), snapshot)
        finally:
            m.stop()

    def test_runtime_state_view_in_context(self):
        m = LifecycleManager()
        m.start()
        try:
            captured = {}

            def action(ctx):
                rs = ctx.runtime_state
                captured["state"] = rs.last_state if rs else None
                captured["boot"] = rs.last_boot_mode if rs else None

            m.register(_make_simple_task("t.rs", action=action))
            m.tick()
            # manager 自身状态是 RUNNING
            self.assertEqual(captured.get("state"), "RUNNING")
        finally:
            m.stop()


# ============================================================
# Custom Registry 集成
# ============================================================
class TestCustomRegistryIntegration(unittest.TestCase):
    def test_replace_policy_works_with_manager(self):
        from src.runtime.lifecycle.lifecycle_registry import (
            REPLACE_DUPLICATE,
            LifecycleRegistry,
        )
        reg = LifecycleRegistry(duplicate_policy=REPLACE_DUPLICATE)
        m = LifecycleManager(registry=reg)
        m.start()
        try:
            t1 = _make_simple_task("t.r", owner="a")
            t2 = _make_simple_task("t.r", owner="b")
            m.register(t1)
            m.register(t2)
            self.assertIs(m.get_task("t.r"), t2)
            # owner 索引应已更新
            self.assertFalse(reg.has_owner("a"))
            self.assertTrue(reg.has_owner("b"))
        finally:
            m.stop()


if __name__ == "__main__":
    unittest.main()
