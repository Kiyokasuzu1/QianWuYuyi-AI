"""
Phase 3.5.26: Personality Stability Runtime 集成测试
"""

from __future__ import annotations

import os
import tempfile
import unittest


class TestPersonalityStabilityRuntime(unittest.TestCase):
    def test_runtime_generates_personality_stability_report(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "identity_stability_enabled": True,
                "personality_stability_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            full = rc.get_self_model_full() or {}
            full["core_values"] = [
                {"value_id": "honesty", "name": "真诚", "weight": 0.3, "confidence": 0.9, "sources": ["test"]},
                {"value_id": "growth", "name": "成长", "weight": 0.8, "confidence": 0.9, "sources": ["test"]},
            ]
            full["contradictions"] = [{} for _ in range(10)]
            rc.self_model_manager.load_full(full)

            report = rc.refresh_personality_stability(force=True)
            self.assertIsNotNone(report)
            self.assertFalse(report["is_stable"])
            issue_types = [i["issue_type"] for i in report["issues"]]
            self.assertIn("core_value_drift", issue_types)
            self.assertIn("contradiction_risk", issue_types)

            snap = rc.get_snapshot()
            self.assertIn("personality_stability", snap)

    def test_change_request_generation_blocked_when_personality_unstable(self):
        from src.contracts.growth_schema import GrowthProposal, ChangeItem
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "personality_stability_enabled": True,
                "identity_stability_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            full = rc.get_self_model_full() or {}
            full["core_values"] = [
                {"value_id": "honesty", "name": "真诚", "weight": 0.2, "confidence": 0.9, "sources": ["test"]},
            ]
            full["contradictions"] = [{} for _ in range(12)]
            rc.self_model_manager.load_full(full)

            proposal = GrowthProposal(
                id="prop_ps_block",
                proposed_changes=[
                    ChangeItem(path="personality.traits.initiative", before=0.5, after=0.8, reason="test")
                ],
                confidence=0.9,
                status="accepted",
            )
            cr = rc.process_proposal_to_change_request(proposal)
            self.assertIsNone(cr)


if __name__ == "__main__":
    unittest.main()
