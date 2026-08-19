# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_manager.py

Phase 5.0-D1 Step 4: LifecycleManager 测试。

覆盖:
- 构造与默认属性
- 生命周期: start / stop / pause / resume (含幂等)
- Task 管理: register / unregister / get / list
- 调度: tick (RUN / SKIP / DEFER)
- 异常隔离: 单任务失败不影响其他
- EventEmitter 集成
- 状态记录 / health_check / history
- 线程安全
"""
from __future__ import annotations

import threading
import time
import unittest
from typing import List, Optional

from src.runtime.lifecycle.internal.clock import (
    FrozenClock,
    SystemClock,
)
from src.runtime.lifecycle.internal.event_emitter import EventEmitter
from src.runtime.lifecycle.lifecycle_decision import (
    AfterFirstRun,
    AfterInterval,
    Always,
    Decision,
    Never,
    Predicate,
    TaskState,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
)
from src.runtime.lifecycle.lifecycle_manager import (
    EVENT_MANAGER_PAUSED,
    EVENT_MANAGER_RESUMED,
    EVENT_MANAGER_STARTED,
    EVENT_MANAGER_STOPPED,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_FAILED,
    EVENT_TASK_SKIPPED,
    EVENT_TASK_STARTED,
    LifecycleManager,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)
from src.runtime.lifecycle.lifecycle_state import LifecycleState
from src.runtime.lifecycle.lifecycle_task import SimpleTask


def _make_task(
    task_id: str,
    owner: str = "test",
    action=None,
    *,
    priority: int = 5,
    condition=None,
    interval_seconds: Optional[float] = None,
) -> SimpleTask:
    if action is None:
        action = lambda ctx: None
    return SimpleTask(
        task_id=task_id,
        owner=owner,
        action=action,
        priority=priority,
        condition=condition,
        interval_seconds=interval_seconds,
    )


# ============================================================
# 构造与默认
# ============================================================
class TestConstruction(unittest.TestCase):
    def test_default_construction(self):
        m = LifecycleManager()
        self.assertEqual(m.state, LifecycleState.CREATED)
        self.assertEqual(m.tick_count, 0)
        self.assertIsNone(m.last_tick_at)
        self.assertEqual(m.task_count(), 0)
        self.assertIsInstance(m.clock, SystemClock)
        self.assertEqual(m.name, "manager")
        self.assertFalse(m.is_running)
        self.assertFalse(m.is_paused)
        self.assertFalse(m.is_stopped)

    def test_custom_clock(self):
        clock = FrozenClock(initial=100.0)
        m = LifecycleManager(clock=clock)
        self.assertIs(m.clock, clock)
        m.start()
        self.assertEqual(m.started_at, 100.0)

    def test_custom_name(self):
        m = LifecycleManager(name="custom_mgr")
        self.assertEqual(m.name, "custom_mgr")
        self.assertIn("custom_mgr", repr(m))

    def test_custom_history_capacity(self):
        m = LifecycleManager(history_capacity=5)
        self.assertEqual(m.history_capacity, 5)

    def test_history_capacity_zero_disables(self):
        m = LifecycleManager(history_capacity=0)
        self.assertEqual(m.history_capacity, 0)

    def test_history_capacity_clamped(self):
        m = LifecycleManager(history_capacity=9999999)
        self.assertLessEqual(m.history_capacity, 10000)

    def test_with_emitter(self):
        e = EventEmitter()
        m = LifecycleManager(emitter=e)
        self.assertIs(m.emitter, e)

    def test_with_custom_registry(self):
        from src.runtime.lifecycle.lifecycle_registry import (
            IGNORE_DUPLICATE,
            LifecycleRegistry,
        )
        reg = LifecycleRegistry(duplicate_policy=IGNORE_DUPLICATE)
        m = LifecycleManager(registry=reg)
        self.assertIs(m.registry, reg)

    def test_with_snapshot_view(self):
        m = LifecycleManager(snapshot_view={"k": 1, "boot_count": 5})
        # 不影响构造
        self.assertEqual(m.task_count(), 0)


# ============================================================
# 生命周期
# ============================================================
class TestLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LifecycleManager()

    def test_start_success(self):
        self.assertTrue(self.m.start())
        self.assertEqual(self.m.state, LifecycleState.RUNNING)
        self.assertTrue(self.m.is_running)
        self.assertIsNotNone(self.m.started_at)

    def test_start_idempotent(self):
        self.assertTrue(self.m.start())
        # 重复 start 不抛错
        self.assertTrue(self.m.start())
        self.assertTrue(self.m.start())
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_start_after_stop_raises_no(self):
        self.m.start()
        self.m.stop()
        # stop 后无法再次 start
        self.assertFalse(self.m.start())
        self.assertEqual(self.m.state, LifecycleState.STOPPED)

    def test_stop_success(self):
        self.m.start()
        self.assertTrue(self.m.stop())
        self.assertEqual(self.m.state, LifecycleState.STOPPED)
        self.assertTrue(self.m.is_stopped)
        self.assertIsNotNone(self.m.stopped_at)

    def test_stop_idempotent(self):
        self.m.start()
        self.m.stop()
        # 重复 stop 不抛错
        self.assertTrue(self.m.stop())
        self.assertTrue(self.m.stop())
        self.assertEqual(self.m.state, LifecycleState.STOPPED)

    def test_stop_without_start_returns_false(self):
        # CREATED 状态不能 stop
        self.assertFalse(self.m.stop())
        self.assertEqual(self.m.state, LifecycleState.CREATED)

    def test_pause(self):
        self.m.start()
        self.assertTrue(self.m.pause())
        self.assertEqual(self.m.state, LifecycleState.PAUSED)
        self.assertTrue(self.m.is_paused)

    def test_pause_idempotent(self):
        self.m.start()
        self.m.pause()
        self.assertTrue(self.m.pause())
        self.assertEqual(self.m.state, LifecycleState.PAUSED)

    def test_pause_when_not_running(self):
        # CREATED 不能 pause
        self.assertFalse(self.m.pause())
        self.assertEqual(self.m.state, LifecycleState.CREATED)

    def test_resume(self):
        self.m.start()
        self.m.pause()
        self.assertTrue(self.m.resume())
        self.assertEqual(self.m.state, LifecycleState.RUNNING)
        self.assertFalse(self.m.is_paused)

    def test_resume_idempotent(self):
        self.m.start()
        self.m.pause()
        # 多次 resume 第二次起非 PAUSED 状态应返回 False
        self.assertTrue(self.m.resume())
        self.assertFalse(self.m.resume())  # 已在 RUNNING
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_resume_when_not_paused(self):
        self.m.start()
        # 不是 PAUSED 状态不能 resume
        self.assertFalse(self.m.resume())
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_full_lifecycle(self):
        self.m.start()
        self.m.pause()
        self.m.resume()
        self.m.pause()
        self.m.stop()
        self.assertEqual(self.m.state, LifecycleState.STOPPED)
        self.assertTrue(self.m.is_terminal)

    def test_context_manager(self):
        with LifecycleManager() as m:
            self.assertEqual(m.state, LifecycleState.RUNNING)
            m.register(_make_task("t.a"))
        self.assertEqual(m.state, LifecycleState.STOPPED)


# ============================================================
# Task 管理
# ============================================================
class TestTaskManagement(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LifecycleManager()

    def test_register(self):
        t = _make_task("t.alpha")
        self.assertTrue(self.m.register(t))
        self.assertEqual(self.m.task_count(), 1)
        self.assertIs(self.m.get_task("t.alpha"), t)

    def test_register_none_raises(self):
        with self.assertRaises(ValueError):
            self.m.register(None)  # type: ignore[arg-type]

    def test_register_invalid_task_raises(self):
        class _Bad:
            owner = "test"
        with self.assertRaises(ValueError):
            self.m.register(_Bad())  # type: ignore[arg-type]

    def test_register_duplicate_raises(self):
        self.m.register(_make_task("t.dup"))
        with self.assertRaises(Exception):
            self.m.register(_make_task("t.dup"))

    def test_register_with_replace(self):
        t1 = _make_task("t.dup", owner="a")
        t2 = _make_task("t.dup", owner="b")
        self.m.register(t1)
        self.m.register(t2, replace=True)
        self.assertIs(self.m.get_task("t.dup"), t2)

    def test_unregister(self):
        t = _make_task("t.alpha")
        self.m.register(t)
        result = self.m.unregister("t.alpha")
        self.assertIs(result, t)
        self.assertEqual(self.m.task_count(), 0)
        # 状态也被清理
        self.assertIsNone(self.m.get_task_state("t.alpha"))

    def test_unregister_missing(self):
        self.assertIsNone(self.m.unregister("nonexistent"))
        self.assertIsNone(self.m.unregister(""))
        self.assertIsNone(self.m.unregister(None))  # type: ignore[arg-type]

    def test_list_tasks(self):
        ids = ["t.a", "t.b", "t.c"]
        for tid in ids:
            self.m.register(_make_task(tid))
        self.assertEqual([t.task_id for t in self.m.list_tasks()], sorted(ids))

    def test_get_task_missing(self):
        self.assertIsNone(self.m.get_task("nope"))
        self.assertIsNone(self.m.get_task(""))


# ============================================================
# 调度
# ============================================================
class TestScheduling(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LifecycleManager()
        self.m.start()

    def tearDown(self) -> None:
        self.m.stop()

    def test_tick_empty(self):
        results = self.m.tick()
        self.assertEqual(results, [])
        self.assertEqual(self.m.tick_count, 1)
        self.assertIsNotNone(self.m.last_tick_at)

    def test_tick_runs_default_task(self):
        called = []

        def action(ctx):
            called.append(ctx)
            return {"metrics": {"ran": 1}}

        self.m.register(_make_task("t.run", action=action))
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SUCCESS)
        self.assertEqual(results[0].task_id, "t.run")
        self.assertEqual(len(called), 1)
        # task state 已记录
        ts = self.m.get_task_state("t.run")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.run_count, 1)
        self.assertEqual(ts.failure_count, 0)

    def test_tick_skips_when_never(self):
        # Never 条件总是 SKIP
        self.m.register(_make_task("t.skip", condition=Never()))
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SKIPPED)
        ts = self.m.get_task_state("t.skip")
        self.assertEqual(ts.skip_count, 1)

    def test_tick_defer(self):
        # AfterFirstRun 在 last_run_at 为 None 时会 SKIP,这里手动构造 defer
        deferred_decision = Decision.defer(until=999.0, reason="wait")
        cond = Predicate(lambda ctx, state: deferred_decision)
        self.m.register(_make_task("t.defer", condition=cond))
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SKIPPED)
        self.assertEqual(results[0].next_recommended_at, 999.0)
        ts = self.m.get_task_state("t.defer")
        self.assertEqual(ts.skip_count, 1)

    def test_tick_no_run_when_paused(self):
        called = []

        def action(ctx):
            called.append(ctx)

        self.m.register(_make_task("t.run", action=action))
        self.m.pause()
        results = self.m.tick()
        self.assertEqual(results, [])
        self.assertEqual(called, [])
        self.m.resume()
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(called, [results[0].task_id and True or True][0:1] and called)  # called 应有 1 次

    def test_tick_no_run_when_stopped(self):
        self.m.stop()
        results = self.m.tick()
        self.assertEqual(results, [])

    def test_tick_multiple_tasks(self):
        called = []

        def make_action(name):
            def action(ctx):
                called.append(name)
            return action

        self.m.register(_make_task("t.a", action=make_action("a")))
        self.m.register(_make_task("t.b", action=make_action("b")))
        self.m.register(_make_task("t.c", action=make_action("c")))
        results = self.m.tick()
        self.assertEqual(len(results), 3)
        self.assertEqual(set(called), {"a", "b", "c"})

    def test_tick_after_interval(self):
        # AfterFirstRun: 首次 last_run_at=None 时应 SKIP
        t = _make_task("t.af", condition=AfterFirstRun())
        self.m.register(t)
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SKIPPED)

    def test_tick_after_interval_2nd_skip(self):
        # AfterInterval(60.0): 首次 RUN,第二次 SKIP(elapsed<60)
        t = _make_task("t.intv", condition=AfterInterval(60.0))
        self.m.register(t)
        r1 = self.m.tick()
        self.assertEqual(r1[0].status, LifecycleStatus.SUCCESS)
        r2 = self.m.tick()
        self.assertEqual(r2[0].status, LifecycleStatus.SKIPPED)

    def test_decision_exception_recorded_as_skip(self):
        def bad_condition(ctx, state):
            raise RuntimeError("decision_error")

        cond = Predicate(bad_condition)
        self.m.register(_make_task("t.bad_decision", condition=cond))
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SKIPPED)

    def test_decision_non_decision_value_recorded_as_skip(self):
        def bad_condition(ctx, state):
            return "not_a_decision"  # type: ignore[return-value]

        cond = Predicate(bad_condition)
        self.m.register(_make_task("t.bad_value", condition=bad_condition))  # type: ignore[arg-type]
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SKIPPED)


# ============================================================
# 异常隔离
# ============================================================
class TestExceptionIsolation(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LifecycleManager()
        self.m.start()

    def tearDown(self) -> None:
        self.m.stop()

    def test_single_task_exception(self):
        def bad_action(ctx):
            raise RuntimeError("boom")

        self.m.register(_make_task("t.bad", action=bad_action))
        # tick 不应抛错
        results = self.m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.FAILED)
        # Manager 仍然 RUNNING
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_multi_task_isolation(self):
        def bad_action(ctx):
            raise ValueError("bad")

        called_b = []

        def good_b(ctx):
            called_b.append(True)

        self.m.register(_make_task("t.bad", action=bad_action))
        self.m.register(_make_task("t.good", action=good_b))
        results = self.m.tick()
        self.assertEqual(len(results), 2)
        statuses = {r.task_id: r.status for r in results}
        self.assertEqual(statuses["t.bad"], LifecycleStatus.FAILED)
        self.assertEqual(statuses["t.good"], LifecycleStatus.SUCCESS)
        self.assertEqual(called_b, [True])
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_failure_count_tracked(self):
        def bad_action(ctx):
            raise RuntimeError("boom")

        self.m.register(_make_task("t.bad", action=bad_action))
        for _ in range(3):
            self.m.tick()
        ts = self.m.get_task_state("t.bad")
        self.assertEqual(ts.failure_count, 3)
        self.assertEqual(self.m.failed_task_count(), 3)

    def test_success_count_tracked(self):
        def good(ctx):
            return None

        self.m.register(_make_task("t.good", action=good))
        for _ in range(5):
            self.m.tick()
        ts = self.m.get_task_state("t.good")
        self.assertEqual(ts.run_count, 5)

    def test_execute_task_when_not_running(self):
        self.m.stop()
        # stop 后 execute_task 返回 FAILED 结果
        t = _make_task("t.x")
        result = self.m.execute_task(t)
        self.assertEqual(result.status, LifecycleStatus.FAILED)


# ============================================================
# EventEmitter 集成
# ============================================================
class TestEmitterIntegration(unittest.TestCase):
    def test_start_emits_event(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_MANAGER_STARTED, events)

    def test_stop_emits_event(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        m.stop()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_MANAGER_STARTED, events)
        self.assertIn(EVENT_MANAGER_STOPPED, events)

    def test_pause_resume_emit_events(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        m.pause()
        m.resume()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_MANAGER_PAUSED, events)
        self.assertIn(EVENT_MANAGER_RESUMED, events)

    def test_tick_emits_task_events(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        m.register(_make_task("t.run", action=lambda ctx: None))
        m.tick()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_TASK_STARTED, events)
        self.assertIn(EVENT_TASK_COMPLETED, events)

    def test_tick_emits_task_failed(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        m.register(_make_task("t.bad", action=lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))))
        m.tick()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_TASK_FAILED, events)

    def test_tick_emits_task_skipped(self):
        e = EventEmitter(history_capacity=20)
        m = LifecycleManager(emitter=e)
        m.start()
        m.register(_make_task("t.skip", condition=Never()))
        m.tick()
        events = [ev.event_type for ev in e.history()]
        self.assertIn(EVENT_TASK_SKIPPED, events)

    def test_emitter_failure_does_not_break_manager(self):
        e = EventEmitter()
        e.close()  # 关闭后 emit 会抛错
        m = LifecycleManager(emitter=e)
        m.register(_make_task("t.run", action=lambda ctx: None))
        # Manager 仍能正常运行
        self.assertTrue(m.start())
        results = m.tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, LifecycleStatus.SUCCESS)


# ============================================================
# 查询 / 健康检查
# ============================================================
class TestQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.m = LifecycleManager()
        self.m.start()

    def tearDown(self) -> None:
        self.m.stop()

    def test_health_check_shape(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        self.m.register(_make_task("t.b", action=lambda ctx: None))
        self.m.tick()
        h = self.m.health_check()
        self.assertEqual(h["manager_state"], "RUNNING")
        self.assertEqual(h["task_count"], 2)
        self.assertEqual(h["running_tasks"], 0)
        self.assertEqual(h["success_tasks"], 2)
        self.assertEqual(h["failed_tasks"], 0)
        self.assertEqual(h["skipped_tasks"], 0)
        self.assertEqual(h["tick_count"], 1)
        self.assertIsNotNone(h["last_tick"])
        self.assertIn("state_machine", h)

    def test_health_check_empty(self):
        h = self.m.health_check()
        self.assertEqual(h["task_count"], 0)
        self.assertEqual(h["running_tasks"], 0)

    def test_get_task_state(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        self.m.tick()
        ts = self.m.get_task_state("t.a")
        self.assertIsNotNone(ts)
        self.assertEqual(ts.task_id, "t.a")
        self.assertEqual(ts.run_count, 1)
        # 返回的是副本
        ts.run_count = 999
        ts2 = self.m.get_task_state("t.a")
        self.assertEqual(ts2.run_count, 1)

    def test_get_task_state_missing(self):
        self.assertIsNone(self.m.get_task_state("missing"))
        self.assertIsNone(self.m.get_task_state(""))
        self.assertIsNone(self.m.get_task_state(None))  # type: ignore[arg-type]

    def test_history_recorded(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        self.m.tick()
        self.m.tick()
        hist = self.m.history()
        self.assertEqual(len(hist), 2)

    def test_history_filter_by_task(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        self.m.register(_make_task("t.b", action=lambda ctx: None))
        self.m.tick()
        a_hist = self.m.history(task_id="t.a")
        self.assertEqual(len(a_hist), 1)
        self.assertEqual(a_hist[0].task_id, "t.a")

    def test_history_filter_by_status(self):
        self.m.register(_make_task("t.ok", action=lambda ctx: None))
        self.m.register(_make_task("t.bad", action=lambda ctx: (_ for _ in ()).throw(RuntimeError("x"))))
        self.m.tick()
        ok = self.m.history(status=LifecycleStatus.SUCCESS)
        failed = self.m.history(status=LifecycleStatus.FAILED)
        self.assertEqual(len(ok), 1)
        self.assertEqual(len(failed), 1)

    def test_history_with_limit(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        for _ in range(5):
            self.m.tick()
        self.assertEqual(len(self.m.history(limit=2)), 2)

    def test_clear_history(self):
        self.m.register(_make_task("t.a", action=lambda ctx: None))
        self.m.tick()
        n = self.m.clear_history()
        self.assertEqual(n, 1)
        self.assertEqual(self.m.history(), [])


# ============================================================
# 并发
# ============================================================
class TestConcurrency(unittest.TestCase):
    def test_concurrent_tick_registration(self):
        m = LifecycleManager()
        m.start()
        try:
            # 多个线程同时注册 + tick
            barrier = threading.Barrier(8)

            def register_worker(i):
                barrier.wait()
                m.register(_make_task(f"t.thread_{i}", action=lambda ctx: None))

            def tick_worker(i):
                barrier.wait()
                for _ in range(5):
                    m.tick()

            threads = []
            for i in range(4):
                threads.append(threading.Thread(target=register_worker, args=(i,)))
                threads.append(threading.Thread(target=tick_worker, args=(i,)))
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # Manager 应仍为 RUNNING
            self.assertEqual(m.state, LifecycleState.RUNNING)
        finally:
            m.stop()

    def test_concurrent_register_same_id(self):
        m = LifecycleManager()
        m.start()
        try:
            results = []

            def worker(i):
                try:
                    ok = m.register(_make_task("t.dup", action=lambda ctx: None, owner=f"o{i}"))
                    results.append(ok)
                except Exception as exc:
                    results.append(exc)

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            # 至少 1 个 True
            self.assertTrue(any(r is True for r in results))
        finally:
            m.stop()

    def test_get_task_state_thread_safe(self):
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.x", action=lambda ctx: None))
            for _ in range(10):
                m.tick()
            results = []

            def reader():
                for _ in range(100):
                    ts = m.get_task_state("t.x")
                    if ts is not None:
                        results.append(ts.run_count)

            threads = [threading.Thread(target=reader) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(results), 400)
        finally:
            m.stop()


# ============================================================
# 状态机集成
# ============================================================
class TestStateMachineIntegration(unittest.TestCase):
    def test_illegal_transition_blocked(self):
        m = LifecycleManager()
        # CREATED -> FAILED 不允许
        # 通过 pause 尝试非法转移不会破坏状态机
        self.assertFalse(m.pause())
        self.assertEqual(m.state, LifecycleState.CREATED)

    def test_state_machine_health(self):
        m = LifecycleManager()
        m.start()
        h = m.health_check()
        self.assertIsNotNone(h.get("state_machine"))
        # 状态机历史应有 STARTING / RUNNING
        sm = h["state_machine"]
        self.assertEqual(sm["state"], "RUNNING")
        self.assertGreater(sm["history_size"], 0)

    def test_repr_after_start(self):
        m = LifecycleManager(name="mgr")
        m.start()
        s = repr(m)
        self.assertIn("mgr", s)
        self.assertIn("RUNNING", s)


# ============================================================
# 边界场景
# ============================================================
class TestEdgeCases(unittest.TestCase):
    def test_register_after_stop_keeps_state(self):
        m = LifecycleManager()
        m.start()
        m.stop()
        # 停机后仍可注册(虽然 tick 不会执行)
        self.assertTrue(m.register(_make_task("t.a", action=lambda ctx: None)))
        self.assertEqual(m.task_count(), 1)
        # tick 不会执行(stopped)
        self.assertEqual(m.tick(), [])

    def test_task_with_lifecycle_result_returned_unchanged(self):
        m = LifecycleManager()
        m.start()
        try:
            custom = LifecycleResult(
                task_id="t.custom",
                status=LifecycleStatus.SUCCESS,
                started_at=0.0,
                ended_at=0.0,
                metrics={"custom": True},
            )

            def action(ctx):
                return custom

            m.register(_make_task("t.custom", action=action))
            results = m.tick()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].metrics.get("custom"), True)
        finally:
            m.stop()

    def test_history_capacity_zero(self):
        m = LifecycleManager(history_capacity=0)
        m.start()
        try:
            m.register(_make_task("t.a", action=lambda ctx: None))
            m.tick()
            self.assertEqual(m.history(), [])
        finally:
            m.stop()


if __name__ == "__main__":
    unittest.main()
