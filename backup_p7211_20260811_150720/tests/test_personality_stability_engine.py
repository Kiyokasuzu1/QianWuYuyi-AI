"""
Phase 3.5.26: Personality Stability Engine 测试
"""

from __future__ import annotations

import unittest

from src.personality.personality_stability_engine import PersonalityStabilityEngine


class TestPersonalityStabilityEngine(unittest.TestCase):
    def test_detects_core_value_trait_and_identity_instability(self):
        engine = PersonalityStabilityEngine()
        report = engine.generate_report(
            identity_id="si_test",
            self_model_full={
                "core_values": [
                    {"value_id": "honesty", "weight": 0.3},
                    {"value_id": "growth", "weight": 0.8},
                ],
                "contradictions": [{} for _ in range(10)],
            },
            self_model_snapshot={"contradictions_count": 10},
            trait_states={"initiative": {"current_value": 0.7}},
            evolution_history=[
                {
                    "trait_states_before": {"initiative": {"current_value": 0.4}},
                    "trait_states_after": {"initiative": {"current_value": 0.7}},
                }
            ],
            identity_stability_report={"report_id": "isr_x", "is_stable": False, "stability_score": 0.5},
        )
        self.assertFalse(report.is_stable)
        issue_types = [i["issue_type"] for i in report.issues]
        self.assertIn("core_value_drift", issue_types)
        self.assertIn("trait_drift", issue_types)
        self.assertIn("contradiction_risk", issue_types)
        self.assertIn("identity_instability", issue_types)


if __name__ == "__main__":
    unittest.main()
