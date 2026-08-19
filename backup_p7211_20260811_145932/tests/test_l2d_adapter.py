"""
Live2DAdapter 单元测试 — 情绪/事件 → 雪芽2.0 L2D 参数映射

覆盖：
- 场景驱动（achievement / user_praise / user_conflict / new_topic / disappointment）
- 事件有效期（超过 EVENT_FRESH_WINDOW 后回退到情绪维度）
- 情绪维度兜底（高愉悦高激活 → 爱心眼 等）
- 默认状态
"""
import unittest

from src.admin.core.l2d_adapter import (
    Live2DAdapter,
    SCENE_ACTION_MAP,
    L2DParam,
)


class TestLive2DAdapterScenes(unittest.TestCase):
    """场景驱动：event_type 直接映射到 L2D 动作。"""

    def setUp(self):
        self.adapter = Live2DAdapter()

    def test_achievement_event_returns_star_eyes(self):
        """用户成就（如打游戏完成）→ 星星眼 + 哇塞表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="achievement",
            event_age_seconds=2.0,
        )
        self.assertEqual(action["expression_name"], "哇塞")
        self.assertEqual(action["duration"], 8.0)
        self.assertIn({"id": L2DParam.STAR_EYE, "value": 1.0}, action["params"])
        self.assertEqual(action["reason"], "event:achievement")

    def test_user_praise_event_returns_shy(self):
        """用户夸赞羽依 → 脸红 + 害羞表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="user_praise",
            event_age_seconds=3.0,
        )
        self.assertEqual(action["expression_name"], "害羞")
        self.assertEqual(action["duration"], 6.0)
        # 包含脸红参数
        param_ids = [p["id"] for p in action["params"]]
        self.assertIn(L2DParam.CHEEK_SHY, param_ids)

    def test_user_conflict_event_returns_distress(self):
        """用户冲突 → 委屈表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="user_conflict",
            event_age_seconds=1.0,
        )
        self.assertEqual(action["expression_name"], "委屈")
        self.assertEqual(action["duration"], 5.0)

    def test_new_topic_event_returns_curious(self):
        """新话题 → 好奇表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="new_topic",
            event_age_seconds=5.0,
        )
        self.assertEqual(action["expression_name"], "好奇")

    def test_disappointment_event_returns_sad(self):
        """失望 → 难过表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="disappointment",
            event_age_seconds=10.0,
        )
        self.assertEqual(action["expression_name"], "难过")


class TestLive2DAdapterEventExpiry(unittest.TestCase):
    """事件有效期：超过窗口后回退到情绪维度。"""

    def setUp(self):
        self.adapter = Live2DAdapter()

    def test_expired_event_falls_back_to_emotion(self):
        """achievement 事件超过 30s 后不再触发星星眼"""
        # 默认情绪（valence=0, arousal=0.5）→ 应该走兜底分支
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": 0.0, "arousal": 0.5,
                            "anxiety": 0.0, "curiosity": 0.5},
            last_event_type="achievement",
            event_age_seconds=999.0,  # 远超 EVENT_FRESH_WINDOW
        )
        # 不应该是"哇塞"
        self.assertNotEqual(action["expression_name"], "哇塞")
        # 应该是兜底的"平静"
        self.assertEqual(action["expression_name"], "平静")
        self.assertEqual(action["reason"], "emotion:dimension")

    def test_just_within_window_still_triggers(self):
        """事件正好在窗口内仍然触发"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="user_praise",
            event_age_seconds=Live2DAdapter.EVENT_FRESH_WINDOW,
        )
        self.assertEqual(action["expression_name"], "害羞")


class TestLive2DAdapterEmotionFallback(unittest.TestCase):
    """情绪维度兜底：当没有事件时，根据 valence/arousal 推断。"""

    def setUp(self):
        self.adapter = Live2DAdapter()

    def test_high_valence_high_arousal_returns_happy(self):
        """高愉悦 + 高激活 → 爱心眼（开心）"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": 0.5, "arousal": 0.8,
                            "anxiety": 0.0, "curiosity": 0.5},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "开心")
        param_ids = [p["id"] for p in action["params"]]
        self.assertIn(L2DParam.HEART_EYE, param_ids)

    def test_low_valence_high_arousal_returns_angry(self):
        """低愉悦 + 高激活 → 生气"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": -0.5, "arousal": 0.8,
                            "anxiety": 0.0, "curiosity": 0.5},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "生气")
        param_ids = [p["id"] for p in action["params"]]
        self.assertIn(L2DParam.ANGRY, param_ids)

    def test_low_valence_low_arousal_returns_sad(self):
        """低愉悦 + 低激活 → 难过"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": -0.5, "arousal": 0.2,
                            "anxiety": 0.0, "curiosity": 0.5},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "难过")

    def test_high_anxiety_returns_uneasy(self):
        """高焦虑 → 不安"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": 0.0, "arousal": 0.5,
                            "anxiety": 0.8, "curiosity": 0.5},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "不安")

    def test_high_curiosity_returns_curious(self):
        """高好奇 → 好奇表情"""
        action = self.adapter.map_to_l2d_action(
            emotion_state={"valence": 0.0, "arousal": 0.5,
                            "anxiety": 0.0, "curiosity": 0.8},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "好奇")


class TestLive2DAdapterDefault(unittest.TestCase):
    """默认状态。"""

    def test_empty_state_returns_calm(self):
        """空状态 → 平静"""
        adapter = Live2DAdapter()
        action = adapter.map_to_l2d_action(
            emotion_state={},
            last_event_type="",
            event_age_seconds=999.0,
        )
        self.assertEqual(action["expression_name"], "平静")
        self.assertEqual(action["params"], [])
        self.assertEqual(action["duration"], 0.0)

    def test_get_default_action(self):
        """get_default_action 静态方法"""
        action = Live2DAdapter.get_default_action()
        self.assertEqual(action["expression_name"], "平静")
        self.assertEqual(action["reason"], "default")


class TestSceneActionMapIntegrity(unittest.TestCase):
    """映射表完整性检查。"""

    def test_all_scenes_have_required_fields(self):
        """每个场景必须包含 expression_name/params/duration/message"""
        required = {"expression_name", "params", "duration", "message"}
        for event_type, scene in SCENE_ACTION_MAP.items():
            with self.subTest(event_type=event_type):
                self.assertTrue(required.issubset(scene.keys()),
                                f"场景 {event_type} 缺少字段: {required - scene.keys()}")
                self.assertIsInstance(scene["params"], list)
                self.assertIsInstance(scene["duration"], float)
                self.assertGreater(scene["duration"], 0)

    def test_all_params_have_id_and_value(self):
        """每个参数项必须包含 id 和 value"""
        for event_type, scene in SCENE_ACTION_MAP.items():
            for p in scene["params"]:
                with self.subTest(event_type=event_type, param=p):
                    self.assertIn("id", p)
                    self.assertIn("value", p)
                    self.assertIsInstance(p["value"], float)


if __name__ == "__main__":
    unittest.main()
