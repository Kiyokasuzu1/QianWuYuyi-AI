"""
DecisionEngine 单元测试
"""

import unittest

from src.runtime.decision_engine import DecisionEngine
from src.runtime.world_state import WorldState
from src.runtime.self_state import SelfState


class TestDecisionEngineNoDecision(unittest.TestCase):
    """测试不产生决策的场景"""

    def test_low_initiative_and_social_need(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.1, social_need=0.1, energy=0.5)
        decision = engine.evaluate(ws)
        self.assertIsNone(decision)

    def test_empty_world_state(self):
        # Phase 7.2.1-p3 后 SelfState 默认值 0.65/0.65 会命中 send_message，
        # 这里显式指定低状态值，保持原测试语义：低 initiative/social_need 下应无决策。
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.1, social_need=0.1, energy=0.5)
        decisions = engine.evaluate_all(ws)
        self.assertEqual(len(decisions), 0)


class TestDecisionEngineSendMessage(unittest.TestCase):
    """测试主动发消息决策"""

    def test_high_initiative_and_social_need(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.8, social_need=0.8)
        decision = engine.evaluate(ws)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action_type, "send_message")
        self.assertGreater(decision.priority, 0.5)
        self.assertIn("initiative", decision.reason)

    def test_evaluate_all_returns_sorted(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.8, social_need=0.8, energy=0.1)
        decisions = engine.evaluate_all(ws)

        # 应该有多个决策
        self.assertGreaterEqual(len(decisions), 1)
        # 按优先级排序
        for i in range(len(decisions) - 1):
            self.assertGreaterEqual(decisions[i].priority, decisions[i + 1].priority)

    def test_send_message_payload_contains_default_message(self):
        """Phase 7.2.1-p3-final：DecisionEngine 主动消息必须在 payload.message 中写默认文本，
        否则 InitiativeBridge（orchestrator=None）会报"无消息内容"WARNING。"""
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.7, social_need=0.7, mood="思念")
        decision = engine.evaluate(ws)
        self.assertIsNotNone(decision)
        payload = decision.payload or {}
        self.assertTrue(
            bool(payload.get("message")),
            f"主动消息 payload.message 不能为空，当前 payload.keys={list(payload.keys())}",
        )
        self.assertIsInstance(payload["message"], str)
        self.assertGreaterEqual(
            len(payload["message"].strip()), 4,
            "主动消息 payload.message 应该是一条完整话术，不能是空字符串或 1~2 字",
        )
        # 附带字段：mood_used / time_of_day / alternatives
        self.assertIn("mood_used", payload)
        self.assertIn("time_of_day", payload)
        self.assertIn("alternatives", payload)
        self.assertIsInstance(payload["alternatives"], list)
        self.assertGreaterEqual(len(payload["alternatives"]), 1)

    def test_send_message_payload_mood_is_respected(self):
        """mood=开心 时，message 内容应当落在"开心"对应的候选池里。"""
        from src.runtime.decision_engine import _PROACTIVE_TEMPLATES_BY_MOOD
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.7, social_need=0.7, mood="开心")
        # _generate_proactive_message 有基于时间的 seed，多跑几次至少有一次命中开心模板
        hits = 0
        for _ in range(8):
            d = engine.evaluate(ws)
            msg = (d.payload or {}).get("message") or ""
            if any(msg == cand for cand in _PROACTIVE_TEMPLATES_BY_MOOD["开心"]):
                hits += 1
        self.assertGreaterEqual(hits, 1, "多次采样中至少 1 次的 message 应与'开心'模板逐字匹配")


class TestDecisionEngineRest(unittest.TestCase):
    """测试休息决策"""

    def test_low_energy(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(energy=0.1)
        decision = engine.evaluate(ws)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action_type, "rest")
        self.assertIn("energy", decision.reason)

    def test_rest_priority_over_message(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(energy=0.1, initiative=0.8, social_need=0.8)
        decisions = engine.evaluate_all(ws)

        # 休息应该排在最前面（优先级0.9）
        self.assertEqual(decisions[0].action_type, "rest")


class TestDecisionEngineExplore(unittest.TestCase):
    """测试探索决策"""

    def test_high_curiosity_with_environment(self):
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState(curiosity=0.8)
        ws.environment = {"active_window": "Code"}
        decision = engine.evaluate(ws)

        self.assertIsNotNone(decision)
        self.assertEqual(decision.action_type, "explore")

    def test_high_curiosity_without_environment(self):
        engine = DecisionEngine()
        ws = WorldState()
        # 主动压低 social_need / initiative，避免 send_message 干扰；
        # 原测试语义："好奇高 + 无环境 → 不触发 explore 决策"。
        ws.self_state = SelfState(curiosity=0.8, initiative=0.0, social_need=0.0)
        ws.environment = {}
        decision = engine.evaluate(ws)

        # 没有环境信息，不应产生探索决策
        self.assertIsNone(decision)


class TestDecisionEngineConfig(unittest.TestCase):
    """测试配置阈值"""

    def test_custom_threshold(self):
        engine = DecisionEngine(config={"initiative_min": 0.9})
        ws = WorldState()
        ws.self_state = SelfState(initiative=0.8, social_need=0.8)
        decision = engine.evaluate(ws)

        # initiative 0.8 < 0.9，不应产生决策
        self.assertIsNone(decision)


class TestPhase721p3DefaultTrigger(unittest.TestCase):
    """Phase 7.2.1-p3 修复：默认 SelfState + 默认阈值 立即触发主动消息。

    修复前 Bug 复现：SelfState 默认 initiative=0.3 / social_need=0.4，
    DecisionEngine 默认阈值 initiative_min=0.5 / social_need_min=0.6，
    导致 RuntimeCore.tick 永远无法触发 send_message 决策，
    用户观察表现为：tick 跑但没有任何主动消息。
    """

    def test_default_selfstate_hits_default_thresholds(self):
        """默认 SelfState.default() 应直接命中默认阈值。"""
        engine = DecisionEngine()
        ws = WorldState()
        ws.self_state = SelfState.default()

        decisions = engine.evaluate_all(ws)
        send_decisions = [d for d in decisions if d.action_type == "send_message"]

        self.assertGreaterEqual(
            len(send_decisions), 1,
            f"SelfState 默认值 {ws.self_state.initiative}/{ws.self_state.social_need} 应命中默认阈值，"
            f"实际 decisions={decisions}"
        )
        d = send_decisions[0]
        self.assertEqual(d.action_type, "send_message")
        self.assertEqual(d.payload.get("type"), "proactive")

    def test_from_dict_empty_data_uses_new_defaults(self):
        """SelfState.from_dict({}) 应回退到新默认值，而不是旧版 0.3 / 0.4。"""
        ss = SelfState.from_dict({})
        self.assertGreaterEqual(ss.initiative, 0.6)
        self.assertGreaterEqual(ss.social_need, 0.6)

    def test_default_thresholds_are_lowered(self):
        """DecisionEngine 默认阈值应 <= 0.6，保证默认 SelfState(0.65, 0.65) 可达。"""
        engine = DecisionEngine()
        self.assertLessEqual(engine.thresholds["initiative_min"], 0.6)
        self.assertLessEqual(engine.thresholds["social_need_min"], 0.6)


if __name__ == "__main__":
    unittest.main()