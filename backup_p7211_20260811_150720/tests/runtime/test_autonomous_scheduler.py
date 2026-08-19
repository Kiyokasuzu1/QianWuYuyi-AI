"""
Phase 3.5.24: Autonomous Scheduler 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.experience_schema import RuntimeExperience, ActionResult


class TestAutonomousScheduler(unittest.TestCase):
    def test_scheduler_runs_identity_and_growth_eval_without_accepting(self):
        from src.contracts.growth_schema import GrowthProposal, ChangeItem
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "identity_stability_enabled": True,
                "autonomous_scheduler_enabled": True,
                "as_identity_interval_ticks": 1,
                "as_growth_eval_interval_ticks": 1,
                "as_reflection_interval_ticks": 99,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "rt.json"),
            })

            rc.growth_adapter.update_proposal(GrowthProposal(
                id="prop_sched_1",
                status="proposed",
                proposed_changes=[ChangeItem(path="self_state.initiative", before=0.5, after=0.55)],
                confidence=0.8,
            ))

            rc.tick()
            hist = rc.get_autonomous_scheduler_history(limit=20)
            task_types = [h["task_type"] for h in hist]
            self.assertIn("identity_check", task_types)
            self.assertIn("growth_evaluation", task_types)

            pending = rc.get_growth_proposals(status="proposed", limit=10)
            self.assertGreaterEqual(len(pending), 1)
            self.assertEqual(pending[0].status, "proposed")

    def test_scheduler_runs_memory_maintenance_and_reflection(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_scheduler_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "autonomous_scheduler_enabled": True,
                "reflection_min_experiences": 1,
                "as_memory_interval_ticks": 1,
                "as_identity_interval_ticks": 99,
                "as_growth_eval_interval_ticks": 99,
                "as_reflection_interval_ticks": 1,
                "as_min_memories_for_maintenance": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "rt.json"),
            })

            exp = RuntimeExperience(
                experience_id="exp_sched_1",
                trigger_event={"event_type": "user.input", "data": {"content": "我越来越喜欢创造东西", "importance": 0.9}},
                trigger_type="user_message",
                decision_source="test",
                action_type="internal_reflection_candidate",
                result=ActionResult(action_id="act_sched_1", success=True),
                self_state_before={"initiative": 0.7},
                self_state_after={"initiative": 0.72},
                duration_ms=20.0,
            )
            rc.experience_builder._buffer.append(exp)
            rc.handle_completed_experience(exp, store_to_memory=True)
            rc.notify_scheduler_event(importance=0.9)

            rc.tick()
            hist = rc.get_autonomous_scheduler_history(limit=20)
            task_types = [h["task_type"] for h in hist]
            self.assertIn("memory_maintenance", task_types)
            self.assertIn("reflection", task_types)


if __name__ == "__main__":
    unittest.main()
