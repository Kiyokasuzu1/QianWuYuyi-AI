"""
Phase 3.5.31: Creative Engine 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.runtime.creative_engine import CreativeEngine


class TestCreativeEngine(unittest.TestCase):
    def test_generate_creative_directions_from_memory_emotion_self_model_and_goals(self):
        engine = CreativeEngine()
        memories = [
            {"id": "m1", "content": "我喜欢画画，也想继续研究色彩与光影"},
            {"id": "m2", "content": "我热爱音乐，想把旋律写成关系变化"},
            {"id": "m3", "content": "我创作故事时，想探索情绪如何塑造画面"},
        ]
        emotional_snapshot = {"persistent_mood": "warm"}
        emotional_patterns = [
            {"emotion_tag": "joy", "count": 4},
            {"emotion_tag": "trust", "count": 2},
        ]
        self_model = {
            "core_values": [
                {"value_id": "growth", "name": "成长", "weight": 0.82, "confidence": 0.9},
                {"value_id": "empathy", "name": "共情", "weight": 0.76, "confidence": 0.88},
            ]
        }
        learning_goals = [
            {
                "goal_type": "interest_discovery",
                "topic": "画画",
                "confidence": 0.72,
                "questions": ["如何把关系变化画出来？", "如何让情绪进入画面节奏？"],
            }
        ]

        directions = engine.generate_directions(
            memories=memories,
            emotional_snapshot=emotional_snapshot,
            emotional_patterns=emotional_patterns,
            self_model=self_model,
            learning_goals=learning_goals,
        )

        self.assertGreaterEqual(len(directions), 3)
        source_types = {item.source_type for item in directions}
        self.assertIn("association", source_types)
        self.assertIn("emotion", source_types)
        self.assertIn("identity", source_types)
        self.assertIn("curiosity", source_types)
        self.assertTrue(all(item.status == "proposed" for item in directions))

        snapshot = engine.get_snapshot()
        self.assertGreaterEqual(snapshot["total_directions"], len(directions))
        self.assertTrue(snapshot["active_themes"])
        self.assertTrue(snapshot["last_direction_id"])
        self.assertEqual(self_model["core_values"][0]["name"], "成长")


class TestRuntimeCoreCreativity(unittest.TestCase):
    @staticmethod
    def _build_experience(exp_id: str, content: str) -> RuntimeExperience:
        return RuntimeExperience(
            experience_id=exp_id,
            trigger_event={"event_type": "user_message", "data": {"content": content}},
            trigger_type="user_message",
            decision_source="test",
            action_type="send_message",
            result=ActionResult(
                action_id=f"act_{exp_id}",
                success=True,
                response_received=True,
            ),
            self_state_before={"initiative": 0.2},
            self_state_after={"initiative": 0.3},
            duration_ms=20.0,
        )

    def test_generate_creative_directions_runtime_boundary(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "self_reflection_enabled": True,
                "curiosity_enabled": True,
                "creativity_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            contents = [
                "我喜欢画画，也想继续研究光影表达",
                "我热爱音乐，想知道如何把旋律写成关系变化",
                "我创作故事时，想探索情绪如何塑造画面",
            ]
            for idx, content in enumerate(contents, start=1):
                exp = self._build_experience(f"cre_{idx}", content)
                rc.experience_builder._buffer.append(exp)
                rc.handle_completed_experience(exp, store_to_memory=True)

            goals = rc.generate_learning_goals(limit=8)
            self.assertTrue(goals)

            proposals_before = rc.get_growth_proposals(status="proposed", limit=20)
            change_requests_before = rc.list_personality_change_requests(limit=20)

            directions = rc.generate_creative_directions(limit=8)

            self.assertGreaterEqual(len(directions), 1)
            self.assertTrue(all(direction["status"] == "proposed" for direction in directions))
            self.assertIsNotNone(rc.get_creativity_snapshot())
            self.assertGreaterEqual(len(rc.get_creative_direction_history(limit=20)), len(directions))

            proposals_after = rc.get_growth_proposals(status="proposed", limit=20)
            change_requests_after = rc.list_personality_change_requests(limit=20)
            self.assertEqual(len(proposals_after), len(proposals_before))
            self.assertEqual(change_requests_after, change_requests_before)


if __name__ == "__main__":
    unittest.main()
