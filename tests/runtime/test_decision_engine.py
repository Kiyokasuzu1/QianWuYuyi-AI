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
        engine = DecisionEngine()
        ws = WorldState()
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
        ws.self_state = SelfState(curiosity=0.8)
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


if __name__ == "__main__":
    unittest.main()