"""
InitiativeBridge 集成测试

验证：
- 注册 send_message handler
- handle_send_message 流程
- callback 模式发送
- 触发 action.proactive_executed 事件
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from src.runtime.initiative_bridge import InitiativeBridge
from src.runtime.runtime_bridge import RuntimeBridge
from src.runtime.action_dispatcher import Action


class TestInitiativeBridgeRegister(unittest.TestCase):
    """注册测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 1.0,
        })
        self.bridge.initialize()

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_register_send_message_handler(self):
        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "send_callback": lambda m: True,
        })
        self.assertTrue(ib.register())
        self.assertTrue(ib.is_registered)
        self.assertTrue(self.bridge.is_action_handler_registered("send_message"))

    def test_double_register_idempotent(self):
        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "send_callback": lambda m: True,
        })
        self.assertTrue(ib.register())
        self.assertTrue(ib.register())  # 第二次
        self.assertTrue(ib.is_registered)


class TestInitiativeBridgeHandleSendMessage(unittest.TestCase):
    """handle_send_message 处理流程测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 1.0,
        })
        self.bridge.initialize()

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_send_via_callback_with_prebuilt_message(self):
        sent_messages = []

        def cb(msg):
            sent_messages.append(msg)
            return True

        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "initiative_cooldown_seconds": 0,  # 关 cooldown 避免误跳
            "send_callback": cb,
        })
        ib.register()

        action = Action(
            action_id="test_act_001",
            action_type="send_message",
            payload={"message": "这是预生成的消息", "context": "主动关心"},
            reason="social_need high",
        )
        result = ib.handle_send_message(action)

        self.assertTrue(result["sent"])
        self.assertEqual(result["message"], "这是预生成的消息")
        self.assertEqual(sent_messages, ["这是预生成的消息"])
        self.assertEqual(result["action_id"], "test_act_001")

    def test_send_no_message_generated(self):
        sent_messages = []

        def cb(msg):
            sent_messages.append(msg)
            return True

        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "initiative_cooldown_seconds": 0,
            "send_callback": cb,
        })

        action = Action(
            action_id="test_act_002",
            action_type="send_message",
            payload={},  # 没有 message，也没有 orchestrator
            reason="test",
        )
        result = ib.handle_send_message(action)

        self.assertFalse(result["sent"])
        self.assertIsNone(result["message"])
        self.assertEqual(len(sent_messages), 0)

    def test_send_via_orchestrator_generate(self):
        sent_messages = []

        def cb(msg):
            sent_messages.append(msg)
            return True

        class MockOrch:
            target_user_id = "user123"

            def generate_initiative(self, target_user):
                self._last_target = target_user
                return "由Orchestrator生成的主动消息"

        orch = MockOrch()
        ib = InitiativeBridge(
            orchestrator=orch,
            send_config={
                "initiative_cooldown_seconds": 0,
                "send_callback": cb,
            },
        )

        action = Action(
            action_id="test_act_003",
            action_type="send_message",
            payload={"context": "greeting"},
            reason="initiative high",
        )
        result = ib.handle_send_message(action)

        self.assertTrue(result["sent"])
        self.assertEqual(result["message"], "由Orchestrator生成的主动消息")
        self.assertEqual(getattr(orch, "_last_target", None), "user123")
        self.assertEqual(sent_messages, ["由Orchestrator生成的主动消息"])

    def test_callback_exception_returns_false(self):
        def bad_cb(msg):
            raise ValueError("模拟网络错误")

        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "initiative_cooldown_seconds": 0,
            "send_callback": bad_cb,
        })

        action = Action(
            action_id="test_act_004",
            action_type="send_message",
            payload={"message": "会失败的消息"},
            reason="test",
        )
        result = ib.handle_send_message(action)

        self.assertFalse(result["sent"])
        self.assertEqual(result["message"], "会失败的消息")


class TestInitiativeBridgeProactiveEventFeedback(unittest.TestCase):
    """发送后触发 action.proactive_executed 事件 -> SelfState 变化"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 1.0,
        })
        self.bridge.initialize()

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_send_decreases_initiative(self):
        core = self.bridge.get_runtime_core()
        core.self_state.initiative = 0.9
        old = core.self_state.initiative

        ib = InitiativeBridge(send_config={
            "target_user": "user_test_001",
            "initiative_cooldown_seconds": 0,
            "send_callback": lambda m: True,
        })
        ib.register()

        action = Action(
            action_id="test_act_005",
            action_type="send_message",
            payload={"message": "测试反馈消息"},
            reason="test",
        )
        ib.handle_send_message(action)

        self.assertLess(core.self_state.initiative, old)
        self.assertEqual(core.world_state.recent_events[-1]["type"], "action.proactive_executed")


if __name__ == "__main__":
    unittest.main()