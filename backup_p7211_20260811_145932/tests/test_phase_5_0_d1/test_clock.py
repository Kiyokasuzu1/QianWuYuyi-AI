# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_clock.py

Phase 5.0-D1 Step 1: Clock 抽象测试。

覆盖:
- SystemClock: 默认实现
- FrozenClock: 冻结/推进/设置/线程安全
- MockClock: 注入式
- Clock protocol: isinstance 检查
"""
import threading
import time
import unittest

from src.runtime.lifecycle.internal.clock import (
    Clock,
    ClockError,
    FrozenClock,
    FrozenClockError,
    MockClock,
    SystemClock,
)


class TestSystemClock(unittest.TestCase):
    """SystemClock 基础测试。"""

    def test_default_returns_wall_time(self) -> None:
        """默认 Clock 返回 wall time,接近真实时间。"""
        c = SystemClock()
        t0 = time.time()
        t1 = c.now()
        self.assertAlmostEqual(t1, t0, delta=0.1)

    def test_monotonic_close_to_perf_counter(self) -> None:
        c = SystemClock()
        t0 = time.monotonic()
        t1 = c.monotonic()
        self.assertAlmostEqual(t1, t0, delta=0.1)

    def test_name_attribute(self) -> None:
        c = SystemClock(name="custom")
        self.assertEqual(c.name, "custom")

    def test_repr(self) -> None:
        c = SystemClock()
        s = repr(c)
        self.assertIn("SystemClock", s)


class TestFrozenClock(unittest.TestCase):
    """FrozenClock 核心测试。"""

    def test_initial_value(self) -> None:
        """初始化时间正确。"""
        c = FrozenClock(initial=100.0)
        self.assertEqual(c.now(), 100.0)
        self.assertEqual(c.initial, 100.0)

    def test_advance(self) -> None:
        """advance(delta) 推进时间并返回新值。"""
        c = FrozenClock(initial=0.0)
        v = c.advance(5)
        self.assertEqual(v, 5.0)
        self.assertEqual(c.now(), 5.0)
        c.advance(2.5)
        self.assertEqual(c.now(), 7.5)

    def test_set(self) -> None:
        """set(value) 跳变。"""
        c = FrozenClock(initial=100.0)
        c.set(0.0)
        self.assertEqual(c.now(), 0.0)
        c.set(42)
        self.assertEqual(c.now(), 42.0)

    def test_is_frozen_flag(self) -> None:
        """is_frozen 默认 False,freeze() 后 True。"""
        c = FrozenClock()
        self.assertFalse(c.is_frozen)
        c.freeze()
        self.assertTrue(c.is_frozen)

    def test_frozen_advance_raises(self) -> None:
        """冻结后 advance 抛 FrozenClockError。"""
        c = FrozenClock(initial=0.0, frozen=True)
        with self.assertRaises(FrozenClockError):
            c.advance(1.0)

    def test_frozen_set_raises(self) -> None:
        """冻结后 set 抛 FrozenClockError。"""
        c = FrozenClock(initial=0.0, frozen=True)
        with self.assertRaises(FrozenClockError):
            c.set(1.0)

    def test_unfreeze(self) -> None:
        """unfreeze 后可再次修改。"""
        c = FrozenClock(initial=0.0, frozen=True)
        c.unfreeze()
        c.advance(10)
        self.assertEqual(c.now(), 10.0)

    def test_thread_safe(self) -> None:
        """多线程并发 advance 不破坏状态。"""
        c = FrozenClock(initial=0.0)
        errors: list = []

        def worker() -> None:
            try:
                for _ in range(100):
                    c.advance(1)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(c.now(), 500.0)

    def test_repr(self) -> None:
        c = FrozenClock(initial=1.0, frozen=True)
        s = repr(c)
        self.assertIn("FrozenClock", s)
        self.assertIn("frozen=True", s)


class TestMockClock(unittest.TestCase):
    """MockClock 注入式测试。"""

    def test_default_returns_zero(self) -> None:
        c = MockClock()
        self.assertEqual(c.now(), 0.0)
        self.assertEqual(c.monotonic(), 0.0)

    def test_inject_callable(self) -> None:
        counter = {"t": 100.0}

        def get_t() -> float:
            counter["t"] += 1.0
            return counter["t"]

        c = MockClock(now_fn=get_t)
        self.assertEqual(c.now(), 101.0)
        self.assertEqual(c.now(), 102.0)

    def test_custom_monotonic(self) -> None:
        c = MockClock(now_fn=lambda: 1.0, monotonic_fn=lambda: 2.0)
        self.assertEqual(c.now(), 1.0)
        self.assertEqual(c.monotonic(), 2.0)

    def test_callable_exception_safely_returns_zero(self) -> None:
        def bad() -> float:
            raise RuntimeError("boom")

        c = MockClock(now_fn=bad)
        # 不抛,安全降级到 0.0
        self.assertEqual(c.now(), 0.0)

    def test_name(self) -> None:
        c = MockClock(name="test")
        self.assertEqual(c.name, "test")


class TestClockProtocol(unittest.TestCase):
    """Clock Protocol isinstance 检查。"""

    def test_system_clock_isinstance(self) -> None:
        c = SystemClock()
        self.assertIsInstance(c, Clock)

    def test_frozen_clock_isinstance(self) -> None:
        c = FrozenClock()
        self.assertIsInstance(c, Clock)

    def test_mock_clock_isinstance(self) -> None:
        c = MockClock()
        self.assertIsInstance(c, Clock)

    def test_custom_clock_subclass_recognized(self) -> None:
        class MyClock:
            def now(self) -> float:
                return 1.0

            def monotonic(self) -> float:
                return 1.0

        self.assertIsInstance(MyClock(), Clock)


if __name__ == "__main__":
    unittest.main()
