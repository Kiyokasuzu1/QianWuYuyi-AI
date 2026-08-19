"""
Phase 3.5.30: Curiosity Engine 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.runtime.curiosity_engine import CuriosityEngine


class TestCuriosityEngine(unittest.TestCase):
    def test_generate_learning_goals_from_memories_reflection_and_identity(self):
        engine = CuriosityEngine()
        memories = [
            {"id": "m1", "content": "我喜欢画画，也想继续研究色彩表达"},
            {"id": "m2", "content": "我热爱画画，想知道如何把情绪放进创作"},
            {"id": "m3", "content": "我还不理解为什么自己会反复回到画画"},
            {"id": "m4", "content": "我探索音乐时，想知道如何让旋律表达关系变化"},
        ]
        reflection_history = [
            {
                "insight_id": "ins_1",
                "summary": "近期兴趣持续聚焦创作",
                "uncertainty": "还不确定为什么画画会持续成为重要主题",
            }
        ]
        contradiction_report = {
            "highest_severity": "medium",
            "items": [
                {
                    "contradiction_id": "ctr_1",
                    "contradiction_type": "memory_conflict",
                    "description": "关于画画的偏好出现阶段性冲突",
                }
            ],
        }
        self_model = {
            "core_values": [
                {"value_id": "growth", "name": "成长", "weight": 0.82, "confidence": 0.9},
                {"value_id": "expression", "name": "表达", "weight": 0.31, "confidence": 0.72},
            ]
        }

        goals = engine.generate_goals(
            memories=memories,
            reflection_history=reflection_history,
            contradiction_report=contradiction_report,
            self_model=self_model,
        )

        self.assertGreaterEqual(len(goals), 3)
        goal_types = {goal.goal_type for goal in goals}
        self.assertIn("interest_discovery", goal_types)
        self.assertIn("unknown_exploration", goal_types)
        self.assertIn("contradiction_resolution", goal_types)
        self.assertIn("question_generation", goal_types)

        snapshot = engine.get_snapshot()
        self.assertGreaterEqual(snapshot["total_goals"], len(goals))
        self.assertTrue(snapshot["active_topics"])
        self.assertTrue(snapshot["last_goal_id"])

        history = engine.get_history(limit=10)
        self.assertGreaterEqual(len(history), len(goals))
        self.assertEqual(self_model["core_values"][1]["name"], "表达")


class TestRuntimeCoreCuriosity(unittest.TestCase):
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
            self_state_before={"initiative": 0.25},
            self_state_after={"initiative": 0.35},
            duration_ms=20.0,
        )

    def test_generate_learning_goals_runtime_boundary(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "self_reflection_enabled": True,
                "curiosity_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            contents = [
                "我喜欢画画，也想继续研究光影表达",
                "我想知道为什么自己总会回到创作主题",
                "我还不理解怎样把关系变化写进作品里",
            ]
            for idx, content in enumerate(contents, start=1):
                exp = self._build_experience(f"cur_{idx}", content)
                rc.experience_builder._buffer.append(exp)
                rc.handle_completed_experience(exp, store_to_memory=True)

            self_reflection = rc.run_self_reflection(limit=10)
            self.assertTrue(self_reflection)

            proposals_before = rc.get_growth_proposals(status="proposed", limit=20)
            change_requests_before = rc.list_personality_change_requests(limit=20)

            goals = rc.generate_learning_goals(limit=8)

            self.assertGreaterEqual(len(goals), 1)
            self.assertTrue(all(goal["status"] == "proposed" for goal in goals))
            self.assertIsNotNone(rc.get_curiosity_snapshot())
            self.assertGreaterEqual(len(rc.get_learning_goal_history(limit=20)), len(goals))

            proposals_after = rc.get_growth_proposals(status="proposed", limit=20)
            change_requests_after = rc.list_personality_change_requests(limit=20)
            self.assertEqual(len(proposals_after), len(proposals_before))
            self.assertEqual(change_requests_after, change_requests_before)


if __name__ == "__main__":
    unittest.main()
