"""
SelfState 单元测试
"""

import unittest
import time

from src.runtime.self_state import SelfState


class TestSelfStateDefault(unittest.TestCase):
    """测试默认状态"""

    def test_default_values(self):
        # Phase 7.2.1-p3: 默认 social_need / initiative 调高到 0.65，
        # 保证 RuntimeCore.tick 启动后第一个周期内即可触发主动消息。
        s = SelfState.default()
        self.assertEqual(s.mood, "平静")
        self.assertAlmostEqual(s.energy, 0.7, places=2)
        self.assertAlmostEqual(s.curiosity, 0.5, places=2)
        self.assertAlmostEqual(s.social_need, 0.65, places=2)
        self.assertAlmostEqual(s.focus, 0.6, places=2)
        self.assertAlmostEqual(s.trust, 0.5, places=2)
        self.assertAlmostEqual(s.initiative, 0.65, places=2)
        self.assertGreater(s.last_updated, 0)


class TestSelfStateDecay(unittest.TestCase):
    """测试状态衰减"""

    def test_decay_over_one_hour(self):
        s = SelfState(energy=1.0, curiosity=1.0, social_need=1.0)
        changes = s.decay(3600)  # 1小时

        # 衰减后应该小于1.0
        self.assertLess(s.energy, 1.0)
        self.assertLess(s.curiosity, 1.0)
        self.assertLess(s.social_need, 1.0)

        # changes 应该包含负值
        self.assertIn("energy", changes)
        self.assertLess(changes["energy"], 0)

    def test_decay_bounds(self):
        s = SelfState(energy=0.0)
        s.decay(3600)
        # 不会低于0
        self.assertGreaterEqual(s.energy, 0.0)

        s.energy = 1.0
        s.decay(-1)  # 负时间不应改变值
        self.assertAlmostEqual(s.energy, 1.0, places=3)


class TestSelfStateEventUpdate(unittest.TestCase):
    """测试事件驱动的状态更新"""

    def test_user_input_event(self):
        s = SelfState.default()
        old_social = s.social_need
        changes = s.update_from_event("user.input", {"content": "hello"})

        # 社交需求应下降
        self.assertLess(s.social_need, old_social)
        self.assertIn("social_need", changes)
        self.assertIn("energy", changes)

    def test_emotion_changed_event(self):
        s = SelfState.default()
        changes = s.update_from_event(
            "emotion.state_changed",
            {"current_emotion": "开心", "previous_emotion": "平静"}
        )
        self.assertEqual(s.mood, "开心")
        self.assertIn("mood", changes)

    def test_proactive_executed_event(self):
        s = SelfState(initiative=0.8)
        old_initiative = s.initiative
        s.update_from_event("action.proactive_executed", {})
        self.assertLess(s.initiative, old_initiative)

    def test_system_tick_hour(self):
        s = SelfState(social_need=0.3)
        old = s.social_need
        s.update_from_event("system.tick", {"tick_type": "hour"})
        self.assertGreater(s.social_need, old)


class TestSelfStateSerialization(unittest.TestCase):
    """测试序列化/反序列化"""

    def test_to_dict(self):
        s = SelfState(energy=0.8, mood="开心")
        d = s.to_dict()
        self.assertEqual(d["mood"], "开心")
        self.assertAlmostEqual(d["energy"], 0.8, places=2)

    def test_from_dict(self):
        d = {
            "energy": 0.9,
            "mood": "兴奋",
            "curiosity": 0.8,
            "social_need": 0.3,
            "focus": 0.7,
            "trust": 0.6,
            "initiative": 0.4,
            "last_updated": time.time(),
        }
        s = SelfState.from_dict(d)
        self.assertEqual(s.mood, "兴奋")
        self.assertAlmostEqual(s.energy, 0.9, places=2)

    def test_round_trip(self):
        s1 = SelfState(energy=0.85, mood="平静", curiosity=0.6)
        d = s1.to_dict()
        s2 = SelfState.from_dict(d)
        self.assertEqual(s1.mood, s2.mood)
        self.assertAlmostEqual(s1.energy, s2.energy, places=3)


if __name__ == "__main__":
    unittest.main()