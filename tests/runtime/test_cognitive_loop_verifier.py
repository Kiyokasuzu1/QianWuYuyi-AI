"""
Phase 3.5.21: Cognitive Loop Verification 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.cognitive_loop_schema import DecisionBudget


class TestCognitiveLoopVerifier(unittest.TestCase):
    def test_three_stage_cognitive_loop_verification(self):
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.cognitive_loop_verifier import CognitiveLoopVerifier

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
                "runtime_integration_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            verifier = CognitiveLoopVerifier(
                decision_budget=DecisionBudget(
                    max_reflections=2,
                    max_evaluations=1,
                    max_proposals=1,
                )
            )

            r1 = verifier.verify_input(runtime=rc, user_text="我喜欢画画")
            self.assertTrue(r1.experience_created)
            self.assertTrue(r1.memory_written)
            self.assertFalse(r1.reflection_generated)
            self.assertFalse(r1.proposal_created)

            r2 = verifier.verify_input(runtime=rc, user_text="我最近一直持续创作")
            self.assertTrue(r2.experience_created)
            self.assertTrue(r2.memory_written)
            self.assertTrue(r2.reflection_generated)
            self.assertFalse(r2.evaluation_completed)
            self.assertFalse(r2.proposal_created)

            r3 = verifier.verify_input(runtime=rc, user_text="我发现自己已经形成了稳定的创造兴趣")
            self.assertTrue(r3.experience_created)
            self.assertTrue(r3.memory_written)
            self.assertTrue(r3.reflection_generated)
            self.assertTrue(r3.evaluation_completed)
            self.assertTrue(r3.proposal_created)
            self.assertTrue(r3.identity_checked)
            self.assertFalse(r3.evolution_applied)

            pending = rc.get_pending_growth_proposals(limit=10)
            self.assertGreaterEqual(len(pending), 1)
            self.assertEqual(pending[0]["status"], "proposed")

            history = verifier.get_history(limit=10)
            self.assertEqual(len(history), 3)

    def test_decision_budget_prevents_extra_reflection(self):
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.cognitive_loop_verifier import CognitiveLoopVerifier

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "approval_manager_enabled": True,
                "identity_stability_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            verifier = CognitiveLoopVerifier(
                decision_budget=DecisionBudget(
                    max_reflections=1,
                    max_evaluations=1,
                    max_proposals=1,
                )
            )

            verifier.verify_input(runtime=rc, user_text="我喜欢画画")
            verifier.verify_input(runtime=rc, user_text="我最近一直持续创作")
            r3 = verifier.verify_input(runtime=rc, user_text="我发现自己已经形成了稳定的创造兴趣")

            self.assertIn("reflection_budget_exhausted", r3.warnings)
            self.assertFalse(r3.proposal_created)


if __name__ == "__main__":
    unittest.main()
