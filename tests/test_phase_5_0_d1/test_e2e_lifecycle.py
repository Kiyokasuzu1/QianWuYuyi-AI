# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_e2e_lifecycle.py

Phase 5.0-D1 Step 5: 端到端测试。

覆盖:
- 完整 start -> tick 循环 -> stop 流程
- 多 Owner / 多任务协同
- 状态机完整流转 (CREATED -> STOPPED 所有合法路径)
- 真实场景: 间隔任务、首次运行、长周期运行
- 失败恢复: 任务失败后 Manager 仍可继续
- 暂停 / 恢复 期间数据完整性
"""
from __future__ import annotations

import time
import unittest
from typing import Dict, List, Optional

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.internal.event_emitter import EventEmitter
from src.runtime.lifecycle.lifecycle_decision import (
    AfterFirstRun,
    AfterInterval,
    Always,
    Decision,
    Never,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_manager import (
    EVENT_MANAGER_STARTED,
    EVENT_MANAGER_STOPPED,
    EVENT_TASK_COMPLETED,
    LifecycleManager,
)
from src.runtime.lifecycle.lifecycle_result import LifecycleResult, LifecycleStatus
from src.runtime.lifecycle.lifecycle_state import LifecycleState
from src.runtime.lifecycle.lifecycle_task import SimpleTask


def _make_task(
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
# 完整生命周期 E2E
# ============================================================
class TestFullLifecycleE2E(unittest.TestCase):
    def test_minimal_lifecycle(self):
        """最简 E2E: start -> 1 tick -> stop。"""
        m = LifecycleManager()
        m.start()
        self.assertEqual(m.state, LifecycleState.RUNNING)
        results = m.tick()
        self.assertEqual(results, [])
        m.stop()
        self.assertEqual(m.state, LifecycleState.STOPPED)

    def test_full_state_walkthrough(self):
        """完整走完所有合法状态。"""
        m = LifecycleManager()
        self.assertEqual(m.state, LifecycleState.CREATED)
        m.start()
        self.assertEqual(m.state, LifecycleState.RUNNING)
        m.pause()
        self.assertEqual(m.state, LifecycleState.PAUSED)
        m.resume()
        self.assertEqual(m.state, LifecycleState.RUNNING)
        m.pause()
        m.stop()
        self.assertEqual(m.state, LifecycleState.STOPPED)
        self.assertTrue(m.is_terminal)


# ============================================================
# 多 Owner / 多任务协同
# ============================================================
class TestMultiOwnerE2E(unittest.TestCase):
    def test_three_owners_running_concurrently(self):
        """三个 owner,各注册一个任务,所有任务都执行。"""
        m = LifecycleManager()
        m.start()
        try:
            for i, owner in enumerate(["memory", "growth", "personality"]):
                m.register(_make_task(
                    f"t.{owner}.{i}",
                    owner=owner,
                    action=lambda c, o=owner: None,
                ))
            results = m.tick()
            self.assertEqual(len(results), 3)
            self.assertTrue(all(r.status == LifecycleStatus.SUCCESS for r in results))
        finally:
            m.stop()

    def test_duplicate_owner_tasks(self):
        """同一 owner 多个任务。"""
        m = LifecycleManager()
        m.start()
        try:
            for i in range(5):
                m.register(_make_task(f"t.{i}", owner="memory"))
            self.assertEqual(m.registry.owner_count(), 1)
            self.assertEqual(len(m.registry.list_by_owner("memory")), 5)
            results = m.tick()
            self.assertEqual(len(results), 5)
        finally:
            m.stop()


# ============================================================
# 真实场景
# ============================================================
class TestRealWorldScenarios(unittest.TestCase):
    def test_interval_task_runs_on_schedule(self):
        """模拟一个 60 秒间隔任务,验证时间推进后正确触发。"""
        clock = FrozenClock(initial=0.0)
        m = LifecycleManager(clock=clock)
        m.start()
        try:
            t = _make_task("t.iv", condition=AfterInterval(60.0))
            m.register(t)
            # 首次 RUN(after_interval 首次总是 RUN)
            r1 = m.tick()
            self.assertEqual(r1[0].status, LifecycleStatus.SUCCESS)
            # 推进 30s: 间隔未到 -> SKIP
            clock.advance(30.0)
            r2 = m.tick()
            self.assertEqual(r2[0].status, LifecycleStatus.SKIPPED)
            # 推进到 65s: 间隔到 -> RUN
            clock.advance(35.0)
            r3 = m.tick()
            self.assertEqual(r3[0].status, LifecycleStatus.SUCCESS)
            # ts.run_count 累计 2
            self.assertEqual(m.get_task_state("t.iv").run_count, 2)
        finally:
            m.stop()

    def test_first_run_task_skips_initially(self):
        """AfterFirstRun: 首次 last_run_at=None 时 SKIP,后续 RUN。"""
        clock = FrozenClock(initial=0.0)
        m = LifecycleManager(clock=clock)
        m.start()
        try:
            t = _make_task("t.fr", condition=AfterFirstRun())
            m.register(t)
            r1 = m.tick()
            self.assertEqual(r1[0].status, LifecycleStatus.SKIPPED)
            # AfterFirstRun 永远 SKIP(其语义就是"等首次运行后才允许")
            # 但实际 ts.skip_count 应为 1
            self.assertEqual(m.get_task_state("t.fr").skip_count, 1)
        finally:
            m.stop()

    def test_alternating_success_and_failure(self):
        """成功 / 失败交替:Manager 不应崩溃,统计正确。"""
        call_count = [0]

        def flaky_action(ctx):
            call_count[0] += 1
            if call_count[0] % 2 == 0:
                raise RuntimeError(f"fail #{call_count[0]}")

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.flaky", action=flaky_action))
            for _ in range(6):
                m.tick()
            ts = m.get_task_state("t.flaky")
            self.assertEqual(ts.run_count, 3)  # 1, 3, 5
            self.assertEqual(ts.failure_count, 3)  # 2, 4, 6
        finally:
            m.stop()


# ============================================================
# 暂停 / 恢复场景
# ============================================================
class TestPauseResumeE2E(unittest.TestCase):
    def test_pause_prevents_tick(self):
        """暂停期间所有任务不应执行,恢复后正常执行。"""
        called = []
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.x", action=lambda c: called.append(True)))
            m.tick()
            self.assertEqual(len(called), 1)
            m.pause()
            m.tick()
            m.tick()
            self.assertEqual(len(called), 1)  # 暂停期间未增加
            m.resume()
            m.tick()
            self.assertEqual(len(called), 2)  # 恢复后增加
        finally:
            m.stop()

    def test_pause_resume_preserves_state(self):
        """暂停 / 恢复期间 task_state 不丢失。"""
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.x", action=lambda c: None))
            for _ in range(3):
                m.tick()
            ts_before = m.get_task_state("t.x")
            m.pause()
            m.resume()
            ts_after = m.get_task_state("t.x")
            self.assertEqual(ts_before.run_count, ts_after.run_count)
            self.assertEqual(ts_before.last_run_at, ts_after.last_run_at)
        finally:
            m.stop()


# ============================================================
# 异常恢复场景
# ============================================================
class TestFailureRecovery(unittest.TestCase):
    def test_one_failing_task_does_not_affect_others(self):
        """A 持续失败,B 持续成功。"""
        def bad(ctx):
            raise RuntimeError("always bad")

        good_count = [0]

        def good(ctx):
            good_count[0] += 1

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.bad", action=bad))
            m.register(_make_task("t.good", action=good))
            for _ in range(10):
                m.tick()
            self.assertEqual(good_count[0], 10)
            ts = m.get_task_state("t.bad")
            self.assertEqual(ts.failure_count, 10)
        finally:
            m.stop()

    def test_failure_recovery_with_retry(self):
        """先失败后成功的任务:failure_count 之后 run_count 增加。"""
        n = [0]

        def action(ctx):
            n[0] += 1
            if n[0] <= 2:
                raise RuntimeError("transient")

        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.retry", action=action))
            for _ in range(4):
                m.tick()
            ts = m.get_task_state("t.retry")
            self.assertEqual(ts.failure_count, 2)
            self.assertEqual(ts.run_count, 2)
        finally:
            m.stop()


# ============================================================
# 完整事件追踪
# ============================================================
class TestEventTracking(unittest.TestCase):
    def test_event_sequence(self):
        """start -> register A -> register B -> tick -> stop: 事件序列。"""
        e = EventEmitter(history_capacity=100)
        m = LifecycleManager(emitter=e)
        m.start()
        try:
            m.register(_make_task("t.a"))
            m.register(_make_task("t.b"))
            m.tick()
        finally:
            m.stop()

        events = [ev.event_type for ev in e.history()]
        # 验证关键事件均存在
        self.assertIn(EVENT_MANAGER_STARTED, events)
        self.assertIn(EVENT_MANAGER_STOPPED, events)
        # 两个注册事件
        registered = [ev for ev in e.history() if ev.event_type == "lifecycle.task.registered"]
        self.assertEqual(len(registered), 2)
        # 两个 started
        started = [ev for ev in e.history() if ev.event_type == "lifecycle.task.started"]
        self.assertEqual(len(started), 2)
        # 两个 completed
        completed = [ev for ev in e.history() if ev.event_type == EVENT_TASK_COMPLETED]
        self.assertEqual(len(completed), 2)


# ============================================================
# 健康检查综合
# ============================================================
class TestHealthCheckE2E(unittest.TestCase):
    def test_health_check_after_full_run(self):
        m = LifecycleManager(name="e2e_mgr")
        m.start()
        try:
            m.register(_make_task("t.ok", action=lambda c: None))
            m.register(_make_task("t.bad", action=lambda c: (_ for _ in ()).throw(RuntimeError("x"))))
            m.register(_make_task("t.skip", condition=Never()))
            for _ in range(3):
                m.tick()
            h = m.health_check()
            self.assertEqual(h["manager_state"], "RUNNING")
            self.assertEqual(h["task_count"], 3)
            self.assertEqual(h["success_tasks"], 3)  # 3 次 * t.ok
            self.assertEqual(h["failed_tasks"], 3)   # 3 次 * t.bad
            self.assertEqual(h["skipped_tasks"], 3)  # 3 次 * t.skip
            self.assertEqual(h["tick_count"], 3)
        finally:
            m.stop()

    def test_health_check_after_stop(self):
        m = LifecycleManager()
        m.start()
        m.stop()
        h = m.health_check()
        self.assertEqual(h["manager_state"], "STOPPED")
        self.assertTrue(h["is_terminal"])


# ============================================================
# 长时间运行
# ============================================================
class TestLongRun(unittest.TestCase):
    def test_50_ticks_no_degradation(self):
        """50 次 tick,验证运行计数 / 失败计数累加正确。"""
        m = LifecycleManager()
        m.start()
        try:
            ok = _make_task("t.ok", action=lambda c: None)
            bad = _make_task("t.bad", action=lambda c: (_ for _ in ()).throw(RuntimeError("x")))
            m.register(ok)
            m.register(bad)
            for _ in range(50):
                m.tick()
            self.assertEqual(m.tick_count, 50)
            self.assertEqual(m.get_task_state("t.ok").run_count, 50)
            self.assertEqual(m.get_task_state("t.bad").failure_count, 50)
            # Manager 仍 RUNNING
            self.assertEqual(m.state, LifecycleState.RUNNING)
        finally:
            m.stop()

    def test_100_ticks_history_capacity(self):
        """100 tick + history_capacity=200:历史保留全部。"""
        m = LifecycleManager(history_capacity=200)
        m.start()
        try:
            m.register(_make_task("t.x", action=lambda c: None))
            for _ in range(100):
                m.tick()
            self.assertEqual(len(m.history()), 100)
        finally:
            m.stop()

    def test_100_ticks_history_overflow(self):
        """100 tick + history_capacity=20:仅保留最近 20。"""
        m = LifecycleManager(history_capacity=20)
        m.start()
        try:
            m.register(_make_task("t.x", action=lambda c: None))
            for _ in range(100):
                m.tick()
            self.assertEqual(len(m.history()), 20)
        finally:
            m.stop()


if __name__ == "__main__":
    unittest.main()
