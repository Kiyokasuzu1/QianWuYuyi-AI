"""
完整生命闭环集成测试

验证完整链路：
    UserMessageEvent
    ↓
    RuntimeCore._on_event
    ↓
    SelfState 变化（social_need 下降）
    ↓
    + 模拟时间（衰减 social_need 上升）
    ↓
    DecisionEngine 产生 send_message 决策
    ↓
    ActionDispatcher 分发
    ↓
    InitiativeBridge handle_send_message 回调被调用
    ↓
    action.proactive_executed 事件
    ↓
    SelfState initiative 下降

这个测试是 Phase 3.5.2 的核心验收用例。
"""

import unittest
import tempfile
import shutil
from pathlib import Path
import time

from src.runtime.runtime_bridge import RuntimeBridge
from src.runtime.initiative_bridge import InitiativeBridge
from src.runtime.action_dispatcher import Action, ActionDispatcher


class TestLifeCycleClosedLoop(unittest.TestCase):
    """生命闭环测试"""

    def setUp(self):
        RuntimeBridge.reset_for_testing()
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = str(Path(self.temp_dir) / "state.json")
        self.sent_messages = []

    def tearDown(self):
        RuntimeBridge.reset_for_testing()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_high_initiative_high_social_need(self, bridge):
        """将 SelfState 调整到"应该主动"的状态"""
        core = bridge.get_runtime_core()
        core.self_state.initiative = 0.8      # 超过 initiative_min=0.5
        core.self_state.social_need = 0.8     # 超过 social_need_min=0.6
        core.self_state.energy = 0.8
        core.self_state.curiosity = 0.3
        core.world_state.self_state = core.self_state

    def test_full_closed_loop(self):
        """
        用户消息 → 状态变化 → 模拟时间 → 决策 → 行动 → 状态反馈

        说明：DecisionEngine v1 只产出 payload 不含 message 文本（消息生成是 LLM/Orchestrator 的职责）。
        此处用自定义 handler 在收到 payload 后补 message，再走 InitiativeBridge 反馈路径，
        以验证完整的状态反馈闭环（sent → proactive_executed → initiative 下降）。
        """
        # 1. 初始化
        bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 0.5,  # 开启决策
        })
        self.assertTrue(bridge.initialize())
        core = bridge.get_runtime_core()

        # 2. 注册自定义 send_message 处理器：补 message，再走 InitiativeBridge 的回调
        # Phase 7.2 语义更新：InitiativeBridge 要求明确的 target_user
        # （payload > send_config > orchestrator），无目标时直接跳过发送。
        # 本测试通过 send_config 提供目标用户（与下方 user001 一致）。
        ib = InitiativeBridge(send_config={
            "send_callback": lambda m: self.sent_messages.append(m) or True,
            "target_user": "user001",
        })

        def _send_handler(action):
            # 接收到 DecisionEngine 产出的 payload（如 {"context": "主动关心"}）
            # 实际生产中应由 LLM/Orchestrator 生成；此处补一个模拟消息
            if not action.payload.get("message"):
                action.payload["message"] = "模拟生成：我想你啦"
            return ib.handle_send_message(action)

        bridge.register_action_handler("send_message", _send_handler)

        # 3. 用户消息到达
        bridge.on_user_message("user001", "羽依，你好呀", "sess_001")

        # 验证：social_need 应下降
        self.assertLess(core.self_state.social_need, 0.4 + 0.01)  # 默认0.4 - 0.1 ≈ 0.3

        # 4. 模拟长时间没说话：设置 high initiative + high social_need
        self._setup_high_initiative_high_social_need(bridge)

        # 5. 触发一次 tick，应产生决策
        core.tick()

        # 6. 验证 send_message 行动被派发
        actions = core.action_dispatcher.get_history("send_message")
        self.assertGreaterEqual(len(actions), 1)
        act = actions[-1]
        self.assertEqual(act.action_type, "send_message")
        self.assertEqual(act.status, "completed")

        # 验证：通过 callback 确实"发送"了
        self.assertGreaterEqual(len(self.sent_messages), 1)

        # 7. 验证 action.proactive_executed 事件
        events = core.world_state.recent_events
        proactive_events = [e for e in events if e["type"] == "action.proactive_executed"]
        self.assertGreaterEqual(len(proactive_events), 1)

        # 8. 验证：主动性 initiative 应下降（释放压力）
        # 因为在 handle_send_message 内部会 inject proactive_executed
        self.assertLess(core.self_state.initiative, 0.8)

    def test_closed_loop_decision_triggers_action_without_tick(self):
        """
        通过 inject_event 直接触发决策流程（不走 tick）
        """
        bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 0.5,
        })
        self.assertTrue(bridge.initialize())
        core = bridge.get_runtime_core()

        # 注册 handler
        messages = []
        def handler(action):
            messages.append(action.payload)
            return {"ok": True, "action_id": action.action_id}
        bridge.register_action_handler("send_message", handler)

        # 设置应该主动的状态
        core.self_state.initiative = 0.9
        core.self_state.social_need = 0.9
        core.world_state.self_state = core.self_state

        # 直接 inject 一个 system.tick (hour) 触发 social_need 上升 + 决策
        bridge.inject_event = None  # 不走 bridge，直接 inject
        core.inject_event("system.tick", {"tick_type": "hour"})

        # 验证有 send_message 行动
        history = core.action_dispatcher.get_history("send_message")
        self.assertGreaterEqual(len(history), 1)
        self.assertTrue(any(m.get("context") == "主动关心" for m in messages))

    def test_rest_action_when_energy_low(self):
        """energy 低时应产生 rest 决策"""
        bridge = RuntimeBridge.get_instance({
            "state_file": self.state_file,
            "tick_interval_seconds": 10.0,
            "decision_confidence_threshold": 0.5,
        })
        self.assertTrue(bridge.initialize())
        core = bridge.get_runtime_core()

        rest_calls = []
        def rest_handler(action):
            rest_calls.append(action.payload)
            return {"ok": True}

        bridge.register_action_handler("rest", rest_handler)

        # 设置 energy 极低
        core.self_state.energy = 0.1
        core.world_state.self_state = core.self_state

        core.tick()

        # 验证 rest 行动被派发
        self.assertGreaterEqual(len(rest_calls), 1)
        self.assertIn("duration_minutes", rest_calls[0])
        self.assertEqual(rest_calls[0]["reason"], "energy_low")


if __name__ == "__main__":
    unittest.main()