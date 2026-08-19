# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_state.py

Phase 5.0-D1 Step 1: 状态机测试。

覆盖:
- 初始状态
- 合法转移 (8 种)
- 非法转移不破坏状态
- can_transition 预测
- 幂等转移
- force_set 兜底
- 历史记录
- 终态判定
- 线程安全
- value 序列化
- 工具函数
"""
import threading
import unittest

from src.runtime.lifecycle.lifecycle_state import (
    LifecycleState,
    LifecycleStateMachine,
    all_valid_targets,
    is_valid_transition,
)


class TestInitialState(unittest.TestCase):
    """初始状态。"""

    def test_default_initial(self) -> None:
        m = LifecycleStateMachine()
        self.assertEqual(m.state, LifecycleState.CREATED)

    def test_custom_initial(self) -> None:
        m = LifecycleStateMachine(initial=LifecycleState.RUNNING)
        self.assertEqual(m.state, LifecycleState.RUNNING)

    def test_value_serialization(self) -> None:
        m = LifecycleStateMachine()
        self.assertEqual(m.value, "CREATED")


class TestValidTransitions(unittest.TestCase):
    """合法状态转移。"""

    def setUp(self) -> None:
        self.m = LifecycleStateMachine()

    def test_created_to_starting(self) -> None:
        self.assertTrue(self.m.transition(LifecycleState.STARTING))
        self.assertEqual(self.m.state, LifecycleState.STARTING)

    def test_starting_to_running(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.assertTrue(self.m.transition(LifecycleState.RUNNING))
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_running_to_paused(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.RUNNING)
        self.assertTrue(self.m.transition(LifecycleState.PAUSED))
        self.assertEqual(self.m.state, LifecycleState.PAUSED)

    def test_paused_to_running(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.RUNNING)
        self.m.transition(LifecycleState.PAUSED)
        self.assertTrue(self.m.transition(LifecycleState.RUNNING))
        self.assertEqual(self.m.state, LifecycleState.RUNNING)

    def test_running_to_draining(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.RUNNING)
        self.assertTrue(self.m.transition(LifecycleState.DRAINING))
        self.assertEqual(self.m.state, LifecycleState.DRAINING)

    def test_draining_to_stopped(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.RUNNING)
        self.m.transition(LifecycleState.DRAINING)
        self.assertTrue(self.m.transition(LifecycleState.STOPPED))
        self.assertEqual(self.m.state, LifecycleState.STOPPED)

    def test_starting_to_failed(self) -> None:
        """启动失败: STARTING -> FAILED。"""
        self.m.transition(LifecycleState.STARTING)
        self.assertTrue(self.m.transition(LifecycleState.FAILED))
        self.assertEqual(self.m.state, LifecycleState.FAILED)

    def test_running_to_failed(self) -> None:
        """运行中失败: RUNNING -> FAILED。"""
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.RUNNING)
        self.assertTrue(self.m.transition(LifecycleState.FAILED))
        self.assertEqual(self.m.state, LifecycleState.FAILED)

    def test_failed_to_stopped(self) -> None:
        self.m.transition(LifecycleState.STARTING)
        self.m.transition(LifecycleState.FAILED)
        self.assertTrue(self.m.transition(LifecycleState.STOPPED))
        self.assertEqual(self.m.state, LifecycleState.STOPPED)

    def test_idempotent_same_state(self) -> None:
        """幂等:相同状态转移返回 True。"""
        self.m.transition(LifecycleState.STARTING)
        self.assertTrue(self.m.transition(LifecycleState.STARTING))
        self.assertEqual(self.m.state, LifecycleState.STARTING)


class TestInvalidTransitions(unittest.TestCase):
    """非法转移不破坏状态。"""

    def test_created_to_running_rejected(self) -> None:
        m = LifecycleStateMachine()
        self.assertFalse(m.transition(LifecycleState.RUNNING))
        self.assertEqual(m.state, LifecycleState.CREATED)

    def test_created_to_paused_rejected(self) -> None:
        m = LifecycleStateMachine()
        self.assertFalse(m.transition(LifecycleState.PAUSED))
        self.assertEqual(m.state, LifecycleState.CREATED)

    def test_stopped_is_terminal(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.STOPPED)
        self.assertFalse(m.transition(LifecycleState.RUNNING))
        self.assertEqual(m.state, LifecycleState.STOPPED)

    def test_repeated_invalid_keeps_state(self) -> None:
        """连续 100 次非法转移,状态保持稳定。"""
        m = LifecycleStateMachine()
        for _ in range(100):
            self.assertFalse(m.transition(LifecycleState.RUNNING))
        self.assertEqual(m.state, LifecycleState.CREATED)

    def test_can_transition_predicate(self) -> None:
        m = LifecycleStateMachine()
        self.assertTrue(m.can_transition(LifecycleState.STARTING))
        self.assertFalse(m.can_transition(LifecycleState.RUNNING))
        self.assertFalse(m.can_transition(LifecycleState.PAUSED))
        self.assertFalse(m.can_transition(LifecycleState.FAILED))


class TestTerminal(unittest.TestCase):
    """终态判定。"""

    def test_stopped_is_terminal(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.STOPPED)
        self.assertTrue(m.is_terminal)

    def test_created_not_terminal(self) -> None:
        m = LifecycleStateMachine()
        self.assertFalse(m.is_terminal)

    def test_running_not_terminal(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.RUNNING)
        self.assertFalse(m.is_terminal)


class TestForceSet(unittest.TestCase):
    """force_set 兜底。"""

    def test_force_set_bypasses_validation(self) -> None:
        m = LifecycleStateMachine()
        m.force_set(LifecycleState.RUNNING)
        self.assertEqual(m.state, LifecycleState.RUNNING)

    def test_force_set_records_in_history(self) -> None:
        m = LifecycleStateMachine()
        m.force_set(LifecycleState.RUNNING, reason="recovery")
        history = m.transition_history()
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["forced"])


class TestHistory(unittest.TestCase):
    """历史记录。"""

    def test_history_records_transitions(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING, reason="boot")
        m.transition(LifecycleState.RUNNING, reason="ready")
        history = m.transition_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["from"], "CREATED")
        self.assertEqual(history[0]["to"], "STARTING")
        self.assertEqual(history[0]["reason"], "boot")
        self.assertEqual(history[1]["from"], "STARTING")
        self.assertEqual(history[1]["to"], "RUNNING")

    def test_history_limit(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.STOPPED)
        m.transition(LifecycleState.STARTING)  # 非法的会失败
        m.transition(LifecycleState.RUNNING)
        history = m.transition_history(limit=2)
        self.assertEqual(len(history), 2)

    def test_history_max_size(self) -> None:
        m = LifecycleStateMachine(max_history=3)
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.RUNNING)
        m.transition(LifecycleState.PAUSED)
        m.transition(LifecycleState.RUNNING)
        m.transition(LifecycleState.PAUSED)  # 第 5 次转移
        history = m.transition_history()
        # max_history=3 时只保留最近 3 条
        self.assertLessEqual(len(history), 3)

    def test_history_zero_means_disabled(self) -> None:
        m = LifecycleStateMachine(max_history=0)
        m.transition(LifecycleState.STARTING)
        history = m.transition_history()
        self.assertEqual(history, [])


class TestHealthCheck(unittest.TestCase):
    """健康度。"""

    def test_health_check_shape(self) -> None:
        m = LifecycleStateMachine()
        h = m.health_check()
        self.assertIn("state", h)
        self.assertIn("is_terminal", h)
        self.assertIn("valid_targets", h)
        self.assertIn("history_size", h)
        self.assertEqual(h["state"], "CREATED")


class TestReset(unittest.TestCase):
    """reset(测试用)。"""

    def test_reset(self) -> None:
        m = LifecycleStateMachine()
        m.transition(LifecycleState.STARTING)
        m.transition(LifecycleState.RUNNING)
        m.reset()
        self.assertEqual(m.state, LifecycleState.CREATED)
        self.assertEqual(m.transition_history(), [])


class TestThreadSafety(unittest.TestCase):
    """线程安全。"""

    def test_concurrent_transitions(self) -> None:
        m = LifecycleStateMachine()
        errors: list = []

        def worker() -> None:
            try:
                m.transition(LifecycleState.STARTING)
                m.transition(LifecycleState.RUNNING)
                m.transition(LifecycleState.PAUSED)
                m.transition(LifecycleState.RUNNING)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        # 状态在合法集合内
        self.assertIn(
            m.state,
            {LifecycleState.STARTING, LifecycleState.RUNNING, LifecycleState.PAUSED},
        )


class TestUtilityFunctions(unittest.TestCase):
    """工具函数。"""

    def test_is_valid_transition_true(self) -> None:
        self.assertTrue(
            is_valid_transition(LifecycleState.STARTING, LifecycleState.RUNNING)
        )

    def test_is_valid_transition_false(self) -> None:
        self.assertFalse(
            is_valid_transition(LifecycleState.CREATED, LifecycleState.RUNNING)
        )

    def test_all_valid_targets(self) -> None:
        targets = all_valid_targets(LifecycleState.RUNNING)
        self.assertIn(LifecycleState.PAUSED, targets)
        self.assertIn(LifecycleState.DRAINING, targets)
        self.assertIn(LifecycleState.STOPPED, targets)
        self.assertIn(LifecycleState.FAILED, targets)

    def test_all_valid_targets_for_terminal(self) -> None:
        targets = all_valid_targets(LifecycleState.STOPPED)
        self.assertEqual(targets, [])


class TestRepr(unittest.TestCase):
    """repr。"""

    def test_repr_contains_state(self) -> None:
        m = LifecycleStateMachine()
        s = repr(m)
        self.assertIn("LifecycleStateMachine", s)
        self.assertIn("CREATED", s)


if __name__ == "__main__":
    unittest.main()
