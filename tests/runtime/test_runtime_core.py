"""
RuntimeCore 单元测试
"""

import unittest
import tempfile
import shutil
import time
from pathlib import Path

from src.runtime.runtime_core import RuntimeCore
from src.runtime.self_state import SelfState
from src.runtime.world_state import WorldState


class TestRuntimeCoreLifecycle(unittest.TestCase):
    """测试生命周期"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_start_stop(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        result = core.start()
        self.assertTrue(result)
        self.assertTrue(core.is_running)

        result = core.stop()
        self.assertTrue(result)
        self.assertFalse(core.is_running)

    def test_start_creates_state_file_after_stop(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        core.stop()

        self.assertTrue(self.state_file.exists())

    def test_restart_loads_state(self):
        # 第一次启动并修改状态
        core1 = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core1.start()
        core1.self_state.energy = 0.3
        core1.stop()

        # 第二次启动应恢复状态
        core2 = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core2.start()
        self.assertAlmostEqual(core2.self_state.energy, 0.3, places=2)
        core2.stop()


class TestRuntimeCoreSnapshot(unittest.TestCase):
    """测试快照"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_snapshot(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        snapshot = core.get_snapshot()

        self.assertIn("world_state", snapshot)
        self.assertIn("self_state", snapshot)
        self.assertIn("last_tick", snapshot)
        self.assertIn("is_running", snapshot)
        self.assertTrue(snapshot["is_running"])

        core.stop()

    def test_get_states(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        ss = core.get_self_state()
        self.assertIsInstance(ss, SelfState)

        ws = core.get_world_state()
        self.assertIsInstance(ws, WorldState)

        core.stop()


class TestRuntimeCoreTick(unittest.TestCase):
    """测试 tick"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tick_updates_time(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        old_time = core.world_state.current_time
        time.sleep(0.01)
        core.tick()
        new_time = core.world_state.current_time

        self.assertNotEqual(old_time, new_time)
        core.stop()

    def test_tick_decay(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()
        core.self_state.energy = 1.0

        # 模拟1小时过去
        core._last_tick = time.time() - 3600
        core.tick()

        self.assertLess(core.self_state.energy, 1.0)
        core.stop()


class TestRuntimeCoreEventHandling(unittest.TestCase):
    """测试事件处理"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inject_event_updates_state(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        old_social = core.self_state.social_need
        core.inject_event("user.input", {"content": "hello"})

        self.assertLess(core.self_state.social_need, old_social)
        self.assertTrue(len(core.world_state.recent_events) > 0)

        core.stop()

    def test_event_records_in_world_state(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
        })
        core.start()

        core.inject_event("user.input", {"content": "test"})
        events = core.world_state.recent_events

        self.assertEqual(events[-1]["type"], "user.input")
        core.stop()


class TestRuntimeCoreDecisionDispatch(unittest.TestCase):
    """测试决策到行动的闭环"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "runtime_state.json"
        self.dispatched = []

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_decision_to_action(self):
        core = RuntimeCore(config={
            "state_file": str(self.state_file),
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 0.5,
        })
        core.start()

        # 注册处理器
        def handler(action):
            self.dispatched.append(action.action_type)
            return {"status": "sent"}

        core.action_dispatcher.register("send_message", handler)

        # 制造高 initiative + 高 social_need 状态
        core.self_state.initiative = 0.8
        core.self_state.social_need = 0.8
        core.tick()

        # 应该触发 send_message
        self.assertIn("send_message", self.dispatched)
        core.stop()


if __name__ == "__main__":
    unittest.main()