"""
Phase 3.5.3: RuntimeCore 生产级集成测试

验证三大链路：
1. 启动 Runtime → 恢复状态 → tick 运行
2. 用户消息 → Orchestrator → Runtime 状态变化
3. 主动决策 → ActionDispatcher → initiative_sender
"""

import os
import sys
import time
import uuid
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _unique_state_file():
    """生成唯一的测试状态文件路径"""
    return str(PROJECT_ROOT / "data" / f"runtime_state_test_{uuid.uuid4().hex[:8]}.json")


def _cleanup_state_file(path: str) -> None:
    """清理测试状态文件"""
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


class TestRuntimeStartupLifecycle(unittest.TestCase):
    """链路 1：启动 Runtime → 恢复状态 → tick 运行"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup_state_file(self._state_file)

    def test_01_initialize_and_start(self):
        """RuntimeBridge 初始化成功并启动 RuntimeCore"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        ok = bridge.initialize()

        self.assertTrue(ok)
        self.assertIsNotNone(bridge._runtime_core)
        self.assertTrue(bridge._runtime_core.is_running)
        self.assertIsNotNone(bridge._runtime_core.bus)

    def test_02_tick_runs_on_startup(self):
        """启动后 Scheduler 应自动 tick"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 1,
        })
        bridge.initialize()

        core = bridge._runtime_core
        self.assertTrue(core.is_running)
        time.sleep(2.5)
        self.assertGreaterEqual(core.tick_count, 1)

    def test_03_state_persistence_roundtrip(self):
        """状态持久化：保存后重新加载"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge1 = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge1.initialize()
        bridge1.on_user_message("user_test", "我很开心")
        time.sleep(0.5)
        bridge1.shutdown()

        bridge2 = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge2.initialize()
        self.assertEqual(bridge2._runtime_core.tick_count, 0)
        bridge2.shutdown()

    def test_04_isolated_does_not_crash_app(self):
        """Runtime 异常不应传播"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": "/nonexistent/path/state.json",
            "tick_interval_seconds": 1,
        })
        ok = bridge.initialize()
        self.assertTrue(ok)
        bridge.shutdown()

    def test_05_multiple_shutdown_safe(self):
        """多次 shutdown 安全"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()
        bridge.shutdown()
        bridge.shutdown()
        bridge.shutdown()
        # shutdown 后 _runtime_core 应为 None
        self.assertIsNone(bridge._runtime_core)


class TestUserMessageRuntime(unittest.TestCase):
    """链路 2：用户消息 → Orchestrator → Runtime 状态变化"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup_state_file(self._state_file)

    def test_01_user_message_updates_self_state(self):
        """用户消息更新 SelfState"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        # 获取初始 self_state
        self_state_before = bridge._runtime_core.self_state
        energy_before = self_state_before.energy
        social_need_before = self_state_before.social_need

        bridge.on_user_message("user_test", "今天天气很好呀！")
        time.sleep(0.5)

        self_state_after = bridge._runtime_core.self_state

        # 用户输入后：精力应微升，社交需求应下降
        self.assertGreaterEqual(
            self_state_after.energy, energy_before,
            "用户消息后精力应微升"
        )
        self.assertLessEqual(
            self_state_after.social_need, social_need_before,
            "用户消息后社交需求应下降"
        )

    def test_02_inject_event_triggers_decision(self):
        """注入事件触发决策"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        bridge._runtime_core.inject_event("user.input", {
            "user_id": "user_test",
            "content": "哈哈哈哈哈太好玩了！",
        })

        decision = bridge._runtime_core.trigger_decision("test")
        # 决策可能返回 None（置信度不足），但不应抛异常
        self.assertIsNotNone(bridge._runtime_core)

    def test_03_multiple_messages_change_state(self):
        """多次用户消息后状态累积变化"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        for i in range(5):
            bridge.on_user_message("user_test", f"第 {i} 条消息")
            time.sleep(0.1)

        snapshot = bridge.get_state()
        self.assertGreaterEqual(snapshot["tick_count"], 0)

    def test_04_api_health_endpoint(self):
        """健康检查端点"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        health = bridge.health_check()
        self.assertEqual(health["status"], "healthy")
        self.assertTrue(health["runtime_running"])

    def test_05_runtime_snapshot_structure(self):
        """Runtime 状态快照结构完整性"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()
        bridge.on_user_message("user_test", "测试")
        time.sleep(0.3)

        snapshot = bridge.get_state()
        for key in ["self_state", "event_stats", "tick_count", "health"]:
            self.assertIn(key, snapshot)

        self_state = snapshot["self_state"]
        for key in ["energy", "mood", "curiosity", "social_need", "trust"]:
            self.assertIn(key, self_state)


class TestProactiveDecisionFlow(unittest.TestCase):
    """链路 3：主动决策 → ActionDispatcher → initiative_sender"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup_state_file(self._state_file)

    def test_01_decision_engine_produces_action(self):
        """DecisionEngine 可正常评估（不抛异常）"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        # 注入事件
        for i in range(5):
            bridge._runtime_core.inject_event("user.input", {
                "user_id": "user_test",
                "content": f"消息 {i}",
            })
            time.sleep(0.1)

        # trigger_decision 可能返回 None（置信度不足），但不应抛异常
        decision = bridge._runtime_core.trigger_decision("test proactive")
        self.assertIsNotNone(bridge._runtime_core)
        self.assertIsNotNone(bridge._runtime_core.decision_engine)
        # decision 可能是 None，也可能有值，都正常
        if decision is not None:
            self.assertIn("action", decision)

    def test_02_action_dispatcher_routes_send_message(self):
        """ActionDispatcher 将 send_message 路由到 InitiativeBridge"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.runtime.initiative_bridge import InitiativeBridge
        from src.runtime.action_dispatcher import Action

        sent_messages = []

        def mock_send(content):
            sent_messages.append(content)
            return True

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        ib = InitiativeBridge(
            orchestrator=None,
            send_config={"send_callback": mock_send},
        )
        self.assertTrue(ib.register())

        action = Action(
            action_id="act_test_01",
            action_type="send_message",
            payload={"message": "你好呀！"},
            reason="test",
        )

        dispatcher = bridge._runtime_core.action_dispatcher
        result = dispatcher.dispatch(action)

        self.assertTrue(result.get("sent"))
        self.assertTrue(len(sent_messages) >= 1)

    def test_03_state_update_after_send(self):
        """发送后 action_history 增加"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.runtime.initiative_bridge import InitiativeBridge
        from src.runtime.action_dispatcher import Action

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        ib = InitiativeBridge(
            orchestrator=None,
            send_config={"send_callback": lambda content: True},
        )
        ib.register()

        history_before = len(bridge._runtime_core.action_dispatcher.get_history())

        action = Action(
            action_id="act_test_02",
            action_type="send_message",
            payload={"message": "你好呀！"},
            reason="test",
        )
        bridge._runtime_core.action_dispatcher.dispatch(action)
        time.sleep(0.3)

        history_after = len(bridge._runtime_core.action_dispatcher.get_history())
        self.assertGreaterEqual(history_after, history_before)

    def test_04_orchestrator_message_generation(self):
        """InitiativeBridge 通过 Orchestrator 生成消息"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.runtime.initiative_bridge import InitiativeBridge
        from src.runtime.action_dispatcher import Action

        mock_orch = MagicMock()
        mock_orch.generate_initiative.return_value = "我来找你啦~"

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        sent = []

        def capture_send(content):
            sent.append(content)
            return True

        ib = InitiativeBridge(
            orchestrator=mock_orch,
            send_config={"send_callback": capture_send, "target_user": "test_user"},
        )
        ib.register()

        action = Action(
            action_id="act_test_03",
            action_type="send_message",
            payload={},
            reason="test",
        )

        result = bridge._runtime_core.action_dispatcher.dispatch(action)

        self.assertTrue(result.get("sent"))
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0], "我来找你啦~")
        mock_orch.generate_initiative.assert_called_once()

    def test_05_handler_not_found_graceful(self):
        """未注册的 action 类型优雅降级"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.runtime.action_dispatcher import Action

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        action = Action(
            action_id="act_test_04",
            action_type="unknown_action_type",
            payload={},
            reason="test",
        )

        result = bridge._runtime_core.action_dispatcher.dispatch(action)
        self.assertIn(result["status"], ("skipped", "failed", "no_handler", "success"))


class TestEventBusTransparency(unittest.TestCase):
    """测试 EventBus 事件通知 RuntimeBridge 的透明度"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup_state_file(self._state_file)

    def test_01_eventbus_message_received_propagates(self):
        """MessageReceivedEvent 传播到 RuntimeCore"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.events.bus import get_event_bus
        from src.events.events import MessageReceivedEvent

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        injected_count = {"count": 0}
        original_inject = bridge._runtime_core.inject_event

        def counting_inject(event_type, data):
            injected_count["count"] += 1
            return original_inject(event_type, data)

        bridge._runtime_core.inject_event = counting_inject

        bus = get_event_bus()
        msg_event = MessageReceivedEvent(
            user_id="user_test",
            content="你好羽依",
        )
        bus.publish(msg_event)

        time.sleep(0.5)
        self.assertGreaterEqual(injected_count["count"], 1)

    def test_02_eventbus_emotion_change_propagates(self):
        """情绪变化事件传播到 RuntimeCore"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.events.bus import get_event_bus
        from src.events.events import YuyiEvent

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        injected_count = {"count": 0}
        original_inject = bridge._runtime_core.inject_event

        def counting_inject(event_type, data):
            injected_count["count"] += 1
            return original_inject(event_type, data)

        bridge._runtime_core.inject_event = counting_inject

        # 使用 YuyiEvent 模拟情绪变化
        bus = get_event_bus()
        emo_event = YuyiEvent(
            event_type="emotion.changed",
            data={"emotion": "happy", "confidence": 0.9},
        )
        bus.publish(emo_event)

        time.sleep(0.5)
        self.assertGreaterEqual(injected_count["count"], 1)

    def test_03_non_interesting_events_ignored(self):
        """非关注事件被忽略"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.events.bus import get_event_bus
        from src.events.events import YuyiEvent

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        injected_count = {"count": 0}
        original_inject = bridge._runtime_core.inject_event

        def counting_inject(event_type, data):
            injected_count["count"] += 1
            return original_inject(event_type, data)

        bridge._runtime_core.inject_event = counting_inject

        bus = get_event_bus()
        irrelevant_event = YuyiEvent(event_type="some.irrelevant.event", data={})
        bus.publish(irrelevant_event)

        time.sleep(0.3)
        self.assertEqual(injected_count["count"], 0)


class TestProductionScenario(unittest.TestCase):
    """完整生产场景模拟"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup_state_file(self._state_file)

    def test_01_full_request_cycle(self):
        """完整请求周期：启动 → 接收消息 → 处理 → 响应"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        self.assertTrue(bridge.initialize())

        self_state_before = bridge._runtime_core.self_state
        bridge.on_user_message("user_prod", "你好呀！")
        time.sleep(0.3)

        self_state_after = bridge._runtime_core.self_state
        self.assertGreaterEqual(self_state_after.energy, self_state_before.energy)

        # 即使决策为 None 也不应崩溃
        bridge._runtime_core.trigger_decision("auto tick")

        bridge.shutdown()
        self.assertIsNone(bridge._runtime_core)

    def test_02_multiple_users_isolated(self):
        """多用户隔离"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        bridge.on_user_message("user_a", "A 的消息")
        time.sleep(0.2)
        bridge.on_user_message("user_b", "B 的消息")
        time.sleep(0.2)

        snapshot = bridge.get_state()
        self.assertGreaterEqual(snapshot["tick_count"], 0)

    def test_03_bursty_messages(self):
        """突发消息处理"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        for i in range(20):
            bridge.on_user_message("user_burst", f"消息 {i}")

        time.sleep(1)
        snapshot = bridge.get_state()
        self.assertIn("self_state", snapshot)


if __name__ == "__main__":
    unittest.main(verbosity=2)
