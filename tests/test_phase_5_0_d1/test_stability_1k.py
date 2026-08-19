# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_stability_1k.py

Phase 5.0-D1 Step 5: 稳定性测试。

按 D1 设计要求:
- 1000 次 tick 无明显内存增长
- 长周期运行 Manager 状态保持稳定
- 大量任务注册 / 注销无泄漏
- 高频决策 + 错误隔离
"""
from __future__ import annotations

import gc
import os
import sys
import time
import unittest
from typing import List

from src.runtime.lifecycle.internal.clock import FrozenClock, SystemClock
from src.runtime.lifecycle.internal.event_emitter import EventEmitter
from src.runtime.lifecycle.lifecycle_decision import (
    AfterInterval,
    Always,
    Decision,
    Never,
    Predicate,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle.lifecycle_registry import LifecycleRegistry
from src.runtime.lifecycle.lifecycle_result import LifecycleResult, LifecycleStatus
from src.runtime.lifecycle.lifecycle_state import LifecycleState
from src.runtime.lifecycle.lifecycle_task import SimpleTask


def _make_task(
    task_id: str,
    owner: str = "test",
    action=None,
    *,
    condition=None,
) -> SimpleTask:
    if action is None:
        action = lambda ctx: None
    return SimpleTask(
        task_id=task_id,
        owner=owner,
        action=action,
        condition=condition,
    )


def _rss_mb() -> float:
    """获取当前进程 RSS(MB)。Windows / Linux 通用。"""
    try:
        # Windows
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        psapi = ctypes.WinDLL("psapi.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")
        handle = kernel32.GetCurrentProcess()
        psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize / (1024 * 1024)
    except Exception:
        try:
            # Linux / macOS
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return 0.0


# ============================================================
# 1000 tick 稳定性
# ============================================================
class Test1KTickStability(unittest.TestCase):
    def test_1000_tick_simple(self):
        """1000 次 tick, 5 个任务,验证无错误 / 计数正确。"""
        m = LifecycleManager()
        m.start()
        try:
            for i in range(5):
                m.register(_make_task(f"t.{i}", owner=f"o{i}", action=lambda c, i=i: None))
            for _ in range(1000):
                results = m.tick()
                self.assertEqual(len(results), 5)
            # 计数
            for i in range(5):
                ts = m.get_task_state(f"t.{i}")
                self.assertEqual(ts.run_count, 1000)
            # Manager 仍 RUNNING
            self.assertEqual(m.state, LifecycleState.RUNNING)
            self.assertEqual(m.tick_count, 1000)
        finally:
            m.stop()

    def test_1000_tick_with_failures(self):
        """1000 次 tick, 半数任务失败,验证隔离。"""
        m = LifecycleManager()
        m.start()
        try:
            for i in range(10):
                if i % 2 == 0:
                    m.register(_make_task(
                        f"t.{i}",
                        action=lambda c: (_ for _ in ()).throw(RuntimeError("fail")),
                    ))
                else:
                    m.register(_make_task(f"t.{i}", action=lambda c: None))
            for _ in range(1000):
                m.tick()
            # 偶数失败计数
            for i in range(0, 10, 2):
                self.assertEqual(m.get_task_state(f"t.{i}").failure_count, 1000)
            # 奇数成功
            for i in range(1, 10, 2):
                self.assertEqual(m.get_task_state(f"t.{i}").run_count, 1000)
            # Manager 仍 RUNNING
            self.assertEqual(m.state, LifecycleState.RUNNING)
        finally:
            m.stop()

    def test_1000_tick_decision_variations(self):
        """1000 次 tick, 多种决策类型。"""
        m = LifecycleManager()
        m.start()
        try:
            m.register(_make_task("t.run", action=lambda c: None))
            m.register(_make_task("t.skip", condition=Never()))
            m.register(_make_task("t.iv", condition=AfterInterval(10.0)))
            m.register(_make_task(
                "t.defer",
                condition=Predicate(lambda c, s: Decision.defer(until=0.0, reason="d")),
            ))
            for _ in range(1000):
                results = m.tick()
                self.assertEqual(len(results), 4)
        finally:
            m.stop()


# ============================================================
# 1000 task 注册/注销 稳定性
# ============================================================
class Test1KTaskRegisterStability(unittest.TestCase):
    def test_1000_register_unregister(self):
        """注册 1000 个任务,注销 1000 个任务,验证无泄漏。"""
        m = LifecycleManager()
        m.start()
        try:
            # 注册 1000 个
            for i in range(1000):
                m.register(_make_task(f"t.{i}", owner=f"o{i % 10}"))
            self.assertEqual(m.task_count(), 1000)
            # 注销 1000 个
            for i in range(1000):
                m.unregister(f"t.{i}")
            self.assertEqual(m.task_count(), 0)
            # owner 索引应清空
            self.assertEqual(m.registry.owner_count(), 0)
        finally:
            m.stop()

    def test_register_unregister_loop(self):
        """注册-注销循环 1000 次,验证每次后状态干净。"""
        m = LifecycleManager()
        m.start()
        try:
            for i in range(1000):
                tid = f"t.{i}"
                m.register(_make_task(tid))
                self.assertIsNotNone(m.get_task(tid))
                m.unregister(tid)
                self.assertIsNone(m.get_task(tid))
            self.assertEqual(m.task_count(), 0)
        finally:
            m.stop()


# ============================================================
# 1000 决策评估
# ============================================================
class Test1KDecisionEval(unittest.TestCase):
    def test_1000_decisions(self):
        """1000 次决策评估,验证无性能 / 一致性问题。"""
        from src.runtime.lifecycle.lifecycle_context import LifecycleContext
        from src.runtime.lifecycle.lifecycle_decision import (
            And,
            AfterInterval,
            Always,
        )
        from src.runtime.lifecycle.lifecycle_decision import TaskState
        from src.runtime.lifecycle.internal.clock import FrozenClock

        cond = And(Always(), AfterInterval(30.0))
        clock = FrozenClock(initial=100.0)
        ctx = LifecycleContext(clock=clock, task_id="t")
        verdicts = []
        for i in range(1000):
            state = TaskState(task_id="t", last_run_at=float(i))
            d = cond.should_run(ctx, state)
            verdicts.append(d.verdict)
        # 验证至少包含 RUN 和 SKIP
        self.assertIn(Verdict.RUN, verdicts)
        self.assertIn(Verdict.SKIP, verdicts)


# ============================================================
# 1000 发射事件
# ============================================================
class Test1KEmitterStability(unittest.TestCase):
    def test_1000_emit(self):
        """1000 次事件发射,验证无历史丢失 + 计数正确。"""
        e = EventEmitter(history_capacity=2000)
        counter = [0]

        def h(ev):
            counter[0] += 1

        e.on("test.event", h)
        for i in range(1000):
            e.emit("test.event", {"i": i})
        self.assertEqual(counter[0], 1000)
        self.assertEqual(e.history_size(), 1000)
        self.assertEqual(e.total_emitted, 1000)

    def test_1000_emit_history_overflow(self):
        """1000 次发射, history_capacity=100,仅保留最近 100。"""
        e = EventEmitter(history_capacity=100)
        for i in range(1000):
            e.emit("e", {"i": i})
        self.assertEqual(e.history_size(), 100)
        # 最近 100 条
        hist = e.history()
        self.assertEqual(hist[0].payload["i"], 900)
        self.assertEqual(hist[-1].payload["i"], 999)


# ============================================================
# 1000 tick 内存泄漏检查
# ============================================================
class TestMemoryStability(unittest.TestCase):
    def test_1000_tick_no_obvious_memory_growth(self):
        """1000 次 tick, 检查内存增长不超过 50MB(允许 GC 误差)。"""
        gc.collect()
        m0 = _rss_mb()
        m = LifecycleManager()
        m.start()
        try:
            for i in range(10):
                m.register(_make_task(f"t.{i}", action=lambda c, i=i: None))
            for _ in range(1000):
                m.tick()
            gc.collect()
            m1 = _rss_mb()
            growth = m1 - m0
            # 阈值: 50MB 容忍(包含 Python 自身运行时波动)
            self.assertLess(
                growth, 50.0,
                f"1000 tick 内存增长 {growth:.2f}MB 超过 50MB 阈值",
            )
        finally:
            m.stop()

    def test_1000_register_unregister_no_leak(self):
        """1000 次注册-注销循环,检查内存增长。"""
        gc.collect()
        m0 = _rss_mb()
        m = LifecycleManager()
        m.start()
        try:
            for cycle in range(1000):
                tid = f"t.cycle_{cycle}"
                m.register(_make_task(tid))
                m.unregister(tid)
            gc.collect()
            m1 = _rss_mb()
            growth = m1 - m0
            self.assertLess(
                growth, 30.0,
                f"1000 register/unregister 内存增长 {growth:.2f}MB 超过 30MB 阈值",
            )
        finally:
            m.stop()

    def test_history_overflow_no_leak(self):
        """10000 次 tick, history_capacity=100,验证历史不会无限增长。"""
        m = LifecycleManager(history_capacity=100)
        m.start()
        try:
            m.register(_make_task("t", action=lambda c: None))
            for _ in range(10000):
                m.tick()
            self.assertLessEqual(m.history_capacity, 100)
            self.assertEqual(len(m.history()), 100)
        finally:
            m.stop()


# ============================================================
# 1000 状态机转移
# ============================================================
class Test1KStateMachineStability(unittest.TestCase):
    def test_1000_legal_transitions(self):
        """合法状态机转移 1000 次,验证无内部状态破坏。"""
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        sm = LifecycleStateMachine()
        for i in range(1000):
            sm.transition(LifecycleState.STARTING)
            sm.transition(LifecycleState.RUNNING)
            sm.transition(LifecycleState.PAUSED)
            sm.transition(LifecycleState.RUNNING)
            sm.transition(LifecycleState.DRAINING)
            sm.transition(LifecycleState.STOPPED)
            sm.reset()
        # 最后 reset 应回到 CREATED
        self.assertEqual(sm.state, LifecycleState.CREATED)
        # history 应有上限
        self.assertLessEqual(len(sm.transition_history()), 100)

    def test_1000_illegal_transitions(self):
        """1000 次非法转移,验证状态机不被破坏。"""
        from src.runtime.lifecycle.lifecycle_state import (
            LifecycleState,
            LifecycleStateMachine,
        )
        for _ in range(1000):
            sm = LifecycleStateMachine()
            # CREATED 状态下尝试非法转移
            self.assertFalse(sm.transition(LifecycleState.FAILED))
            self.assertFalse(sm.transition(LifecycleState.PAUSED))
            self.assertFalse(sm.transition(LifecycleState.RUNNING))
            self.assertFalse(sm.transition(LifecycleState.DRAINING))
            self.assertEqual(sm.state, LifecycleState.CREATED)


# ============================================================
# 性能烟雾测试
# ============================================================
class TestPerformanceSmoke(unittest.TestCase):
    def test_1000_tick_under_30s(self):
        """1000 次 tick, 5 个简单任务, 总耗时 < 30s。"""
        m = LifecycleManager()
        m.start()
        try:
            for i in range(5):
                m.register(_make_task(f"t.{i}", action=lambda c: None))
            t0 = time.time()
            for _ in range(1000):
                m.tick()
            elapsed = time.time() - t0
            self.assertLess(elapsed, 30.0, f"1000 tick 耗时 {elapsed:.2f}s 超过 30s")
        finally:
            m.stop()


if __name__ == "__main__":
    unittest.main()
