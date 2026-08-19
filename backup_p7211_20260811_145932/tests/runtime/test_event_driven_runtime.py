"""
Phase 3.5.23: Event Driven Architecture 测试
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.core.event_bus import EventBus


class TestEventDrivenRuntime(unittest.TestCase):
    def setUp(self):
        EventBus.reset_instance()

    def test_standard_and_legacy_runtime_events_emitted(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "approval_manager_enabled": True,
                "identity_continuity_enabled": True,
                "identity_anchor_enabled": True,
                "identity_stability_enabled": True,
                "event_driven_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            # 预填充历史经验以确保 reflection 能产生 proposal
            for idx, text in enumerate([
                "最近我开始喜欢创作图像",
                "持续创作让我觉得自己更有表达欲",
            ]):
                rc.experience_builder._buffer.append(
                    RuntimeExperience(
                        experience_id=f"exp_edr_{idx}",
                        trigger_event={"event_type": "user.input", "data": {"content": text}},
                        trigger_type="user_message",
                        decision_source="history",
                        action_type="internal_reflection_candidate",
                        result=ActionResult(action_id=f"act_edr_{idx}", success=True),
                        self_state_before={"initiative": 0.6},
                        self_state_after={"initiative": 0.7},
                        duration_ms=20.0,
                    )
                )

            rc.inject_event("user.input", {"content": "我越来越喜欢创造东西", "user_id": "tester", "importance": 0.9})
            exp_id = rc._building_experience_id
            self.assertTrue(exp_id)
            rc.experience_builder.record_decision(
                exp_id,
                decision_source="test",
                intention_id="edr_reflect",
                action_type="internal_reflection_candidate",
            )
            time.sleep(0.02)
            exp = rc.experience_builder.finish_building(
                exp_id,
                result=ActionResult(action_id="act_edr_current", success=True),
                self_state_after=rc.self_state.to_dict(),
            )
            self.assertIsNotNone(exp)
            rc.handle_completed_experience(exp, store_to_memory=True)
            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)
            stability = rc.refresh_identity_stability(force=True)
            self.assertIsNotNone(stability)

            proposed = rc.get_pending_growth_proposals(limit=10)
            self.assertGreaterEqual(len(proposed), 1)
            rc.accept_growth_proposal(proposed[0]["id"])

            history = rc.get_domain_event_history(limit=100)
            event_types = [h["event_type"] for h in history]
            self.assertIn("experience_created", event_types)
            self.assertIn("memory_created", event_types)
            self.assertIn("reflection_started", event_types)
            self.assertIn("reflection_completed", event_types)
            self.assertIn("evaluation_completed", event_types)
            self.assertIn("proposal_created", event_types)
            self.assertIn("identity_changed", event_types)
            self.assertIn("proposal_applied", event_types)

            bus = EventBus.get_instance()
            legacy = [e.event_type for e in bus.get_history(limit=100)]
            self.assertIn("growth.proposal_created", legacy)
            self.assertIn("identity.changed", legacy)
            self.assertIn("memory.created", legacy)

    def test_emotion_and_relationship_change_notifications(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "event_driven_enabled": True,
                "relationship_enabled": True,
                "emotion_enabled": True,
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            rc.notify_emotion_changed({"reason": "test"})
            rc.notify_relationship_changed({"reason": "test"})

            history = rc.get_domain_event_history(limit=20)
            event_types = [h["event_type"] for h in history]
            self.assertIn("emotion_changed", event_types)
            self.assertIn("relationship_changed", event_types)


if __name__ == "__main__":
    unittest.main()
