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


class TestPhase721p3SilentBranchesLog(unittest.TestCase):
    """Phase 7.2.1-p3：静默 return 必须打 ERROR/WARNING 日志，不再无声失败。"""

    def setUp(self):
        import tempfile
        from pathlib import Path
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "ib_silent_branch_state.json"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_no_target_user_logs_error(self):
        """target_user 为 None 时应打印 ERROR 级日志，并返回 no target_user reason。"""
        ib = InitiativeBridge(
            orchestrator=None,
            send_config={
                # 故意不给 target_user / api_type 等
                "api_type": "onebot",
                "initiative_cooldown_seconds": 0,
            },
        )
        action = Action(
            action_id="t_no_target",
            action_type="send_message",
            payload={"message": "你好"},
            reason="test",
        )
        with self.assertLogs("src.runtime.initiative_bridge", level="ERROR") as log_ctx:
            res = ib.handle_send_message(action)
        self.assertFalse(res.get("sent"))
        self.assertEqual(res.get("reason"), "no target_user configured")
        self.assertTrue(
            any("未解析到 target_user" in m for m in log_ctx.output),
            f"期望 ERROR 日志包含'未解析到 target_user'，实际：{log_ctx.output}",
        )

    def test_no_message_logs_warning(self):
        """payload 无 message + orchestrator 无 / 返回空字符串 → WARNING 日志。"""
        class DummyEmptyOrchestrator:
            def generate_initiative(self, _user):
                return ""

        ib = InitiativeBridge(
            orchestrator=DummyEmptyOrchestrator(),
            send_config={
                "target_user": "user_123",
                "initiative_cooldown_seconds": 0,
            },
        )
        action = Action(action_id="t_empty", action_type="send_message",
                        payload={}, reason="t")
        with self.assertLogs("src.runtime.initiative_bridge", level="WARNING") as log_ctx:
            res = ib.handle_send_message(action)
        self.assertFalse(res.get("sent"))
        self.assertEqual(res.get("reason"), "no message generated; skipped")
        self.assertTrue(
            any("无消息内容" in m for m in log_ctx.output),
            f"期望 WARNING 日志包含'无消息内容'，实际：{log_ctx.output}",
        )

    def test_send_false_logs_error_and_rolls_back_cooldown(self):
        """_send_message 返回 False → ERROR 日志 + cooldown 记录回滚（不会永久锁死）。"""
        ib = InitiativeBridge(
            orchestrator=None,
            send_config={
                "api_type": "onebot",
                "onebot_url": "http://127.0.0.1:1",  # 故意不可达
                "target_user": "user_999",
                "initiative_cooldown_seconds": 9999,  # 失败回滚才能不影响下一次
                "send_callback": lambda _m: False,  # 模拟发送失败
            },
        )
        action = Action(action_id="t_fail", action_type="send_message",
                        payload={"message": "hi"}, reason="t")
        # 第 1 次：发送失败，cooldown 记录回滚
        with self.assertLogs("src.runtime.initiative_bridge", level="ERROR") as log_ctx:
            r1 = ib.handle_send_message(action)
        self.assertFalse(r1.get("sent"))
        self.assertEqual(r1.get("reason"), "send_message returned False")
        self.assertTrue(
            any("发送失败" in m for m in log_ctx.output),
            f"期望 ERROR 日志包含'发送失败'，实际：{log_ctx.output}",
        )
        # 第 2 次：因为 cooldown 已回滚，应当不会命中 cooldown；
        # 如果回滚失败会看到 skipped_by_cooldown=True。
        action2 = Action(action_id="t_fail_2", action_type="send_message",
                         payload={"message": "hi again"}, reason="t")
        r2 = ib.handle_send_message(action2)
        # send_callback=False，会再次走"发送失败 return False"分支（不能是 cooldown）
        self.assertFalse(r2.get("sent"))
        self.assertIsNone(r2.get("skipped_by_cooldown"))
        self.assertEqual(r2.get("reason"), "send_message returned False")


if __name__ == "__main__":
    unittest.main()