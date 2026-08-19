"""
RuntimeBridge 集成测试

验证：
- 单例机制
- 初始化/关闭
- on_user_message -> SelfState变化
- get_snapshot 返回结构
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from src.runtime.runtime_bridge import RuntimeBridge, get_runtime_bridge


class TestRuntimeBridgeSingleton(unittest.TestCase):
    """单例测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_singleton_returns_same_instance(self):
        a = RuntimeBridge.get_instance({"state_file": self.state_file})
        b = RuntimeBridge.get_instance()
        self.assertIs(a, b)

    def test_get_runtime_bridge_helper(self):
        a = get_runtime_bridge({"state_file": self.state_file})
        b = RuntimeBridge.get_instance()
        self.assertIs(a, b)

    def test_reset_for_testing(self):
        a = RuntimeBridge.get_instance({"state_file": self.state_file})
        RuntimeBridge.reset_for_testing()
        b = RuntimeBridge.get_instance({"state_file": self.state_file})
        self.assertIsNot(a, b)


class TestRuntimeBridgeInitialize(unittest.TestCase):
    """初始化/关闭测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialize_and_shutdown(self):
        bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
        })
        self.assertTrue(bridge.initialize())
        self.assertIsNotNone(bridge.get_runtime_core())
        self.assertTrue(bridge.get_runtime_core().is_running)

        bridge.shutdown()
        self.assertIsNone(bridge.get_runtime_core())

    def test_double_initialize_idempotent(self):
        bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
        })
        self.assertTrue(bridge.initialize())
        core1 = bridge.get_runtime_core()

        self.assertTrue(bridge.initialize())  # 第二次
        core2 = bridge.get_runtime_core()

        self.assertIs(core1, core2)


class TestRuntimeBridgeOnUserMessage(unittest.TestCase):
    """用户消息 -> SelfState 变化"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 1.0,  # 关闭实际决策
        })
        self.bridge.initialize()

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_on_user_message_decreases_social_need(self):
        core = self.bridge.get_runtime_core()
        core.self_state.social_need = 0.9
        old_social = core.self_state.social_need

        self.bridge.on_user_message("user123", "你好，羽依", "sess001")

        self.assertLess(core.self_state.social_need, old_social)

    def test_on_user_message_records_in_world_state(self):
        self.bridge.on_user_message("user123", "测试消息", "sess001")

        core = self.bridge.get_runtime_core()
        events = core.world_state.recent_events
        self.assertGreater(len(events), 0)
        last_event = events[-1]
        self.assertEqual(last_event["type"], "user.input")
        self.assertEqual(last_event["data"]["content"], "测试消息")


class TestRuntimeBridgeSnapshot(unittest.TestCase):
    """快照观测接口测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 1.0,
        })

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_snapshot_not_initialized(self):
        snap = self.bridge.get_snapshot()
        self.assertFalse(snap.get("is_running"))
        self.assertEqual(snap.get("status"), "not_initialized")

    def test_snapshot_running(self):
        self.bridge.initialize()
        snap = self.bridge.get_snapshot()
        self.assertTrue(snap.get("is_running"))
        self.assertIn("self_state", snap)
        self.assertIn("world_state", snap)
        self.assertIn("recent_actions", snap)
        self.assertIn("mood", snap["self_state"])

    def test_snapshot_recent_actions(self):
        self.bridge.initialize()

        # 注册一个测试 handler
        calls = []
        def test_handler(action):
            calls.append(action.action_id)
            return {"ok": True}

        self.bridge.register_action_handler("test", test_handler)

        # 手动 dispatch
        core = self.bridge.get_runtime_core()
        from src.runtime.action_dispatcher import Action
        act = Action(action_id="t1", action_type="test", payload={"key": "v"})
        core.action_dispatcher.dispatch(act)

        snap = self.bridge.get_snapshot()
        actions = snap["recent_actions"]
        self.assertGreaterEqual(len(actions), 1)
        self.assertEqual(actions[-1]["action_id"], "t1")
        self.assertEqual(actions[-1]["status"], "completed")


class TestRuntimeBridgeActionHandler(unittest.TestCase):
    """注册行动处理器测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
        })
        self.bridge.initialize()

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_and_check_handler(self):
        def handler(action):
            return {"ok": True}

        self.bridge.register_action_handler("custom", handler)
        self.assertTrue(self.bridge.is_action_handler_registered("custom"))
        self.assertFalse(self.bridge.is_action_handler_registered("unknown"))


if __name__ == "__main__":
    unittest.main()