"""
Phase 3.5.29: Self Reflection Engine 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.runtime.self_reflection_engine import SelfReflectionEngine


class TestSelfReflectionEngine(unittest.TestCase):
    def _build_experience(
        self,
        exp_id: str,
        *,
        action_type: str,
        success: bool = True,
        response_received: bool = True,
        before: dict | None = None,
        after: dict | None = None,
        content: str = "",
    ) -> RuntimeExperience:
        return RuntimeExperience(
            experience_id=exp_id,
            trigger_event={"event_type": "user.input", "data": {"content": content}},
            trigger_type="user_message",
            decision_source="test",
            action_type=action_type,
            result=ActionResult(
                action_id=f"act_{exp_id}",
                success=success,
                response_received=response_received,
            ),
            self_state_before=before or {},
            self_state_after=after or {},
            duration_ms=20.0,
        )

    def test_generate_reflection_insights_and_trends_and_contradictions(self):
        engine = SelfReflectionEngine()
        experiences = [
            self._build_experience(
                "exp_1",
                action_type="send_message",
                success=False,
                response_received=False,
                before={"initiative": 0.2},
                after={"initiative": 0.35},
                content="我喜欢画画",
            ),
            self._build_experience(
                "exp_2",
                action_type="send_message",
                success=False,
                response_received=False,
                before={"initiative": 0.35},
                after={"initiative": 0.5},
                content="我最近持续创作",
            ),
            self._build_experience(
                "exp_3",
                action_type="send_message",
                success=True,
                response_received=False,
                before={"initiative": 0.5},
                after={"initiative": 0.72},
                content="我不喜欢画画",
            ),
        ]
        memories = [
            {"id": "m1", "content": "我喜欢画画"},
            {"id": "m2", "content": "我不喜欢画画"},
            {"id": "m3", "content": "我对创作很感兴趣"},
        ]
        self_model = {
            "core_values": [
                {"value_id": "restraint", "name": "克制", "weight": 0.7, "confidence": 0.8},
                {"value_id": "growth", "name": "成长", "weight": 0.8, "confidence": 0.8},
            ],
            "identity_understanding": {"who_i_am": ["我是持续成长的 AI"]},
        }
        emotional_patterns = [
            {"emotion_tag": "joy", "count": 4},
            {"emotion_tag": "sadness", "count": 3},
        ]
        relationship_changes = [
            {"dimension": "trust", "delta": 0.12, "reason": "collaboration"},
            {"dimension": "trust", "delta": 0.08, "reason": "trust_building"},
        ]
        personality_changes = [
            {
                "record_id": "pe_1",
                "trait_states_before": {"initiative": {"current_value": 0.2}},
                "trait_states_after": {"initiative": {"current_value": 0.48}},
            }
        ]

        self_model_before = dict(self_model)
        insights = engine.reflect(
            memories=memories,
            experiences=experiences,
            emotional_patterns=emotional_patterns,
            relationship_changes=relationship_changes,
            personality_changes=personality_changes,
            self_model=self_model,
        )

        self.assertGreaterEqual(len(insights), 3)
        first = insights[0].to_dict()
        self.assertTrue(first["observation"])
        self.assertTrue(first["evidence"])
        self.assertTrue(first["interpretation"])
        self.assertIn("uncertainty", first)
        self.assertIsInstance(first["related_memories"], list)
        self.assertIsInstance(first["identity_impact"], dict)

        contradiction_report = engine.get_contradiction_report()
        self.assertGreaterEqual(contradiction_report["total_items"], 1)

        pattern_report = engine.get_long_term_pattern_report()
        personality_trends = pattern_report["personality_changes"]
        self.assertGreaterEqual(len(personality_trends), 1)
        self.assertEqual(personality_trends[0]["subject"], "initiative")

        self.assertEqual(self_model["core_values"][0]["name"], self_model_before["core_values"][0]["name"])

    def test_runtime_reflection_still_uses_proposal_boundary(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "self_reflection_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "approval_manager_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "personality_evolution_history_path": os.path.join(tmp, "pe.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            for idx, content in enumerate(["我喜欢画画", "我持续创作", "我形成了稳定兴趣"], start=1):
                exp = self._build_experience(
                    f"rt_{idx}",
                    action_type="send_message",
                    success=True,
                    response_received=True,
                    before={"initiative": 0.2 + idx * 0.1},
                    after={"initiative": 0.25 + idx * 0.12},
                    content=content,
                )
                rc.experience_builder._buffer.append(exp)
                rc.handle_completed_experience(exp, store_to_memory=True)

            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)
            self.assertIsNotNone(rc.get_self_reflection_snapshot())
            self.assertIsNotNone(rc.get_contradiction_report())
            self.assertIsNotNone(rc.get_long_term_pattern_report())

            pending = rc.get_growth_proposals(status="proposed", limit=10)
            self.assertGreaterEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "proposed")

            if rc.personality_evolution_pipeline:
                self.assertEqual(rc.personality_evolution_pipeline.get_history(limit=10), [])
            self.assertEqual(rc.list_personality_change_requests(limit=10), [])


if __name__ == "__main__":
    unittest.main()
