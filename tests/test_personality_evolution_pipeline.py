"""
Phase 3.5.18: Personality Evolution Pipeline 测试
"""

from __future__ import annotations

import unittest

from src.contracts.growth_schema import GrowthProposal, ChangeItem
from src.personality.trait_state import create_trait_state
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline


class TestPersonalityEvolutionPipeline(unittest.TestCase):
    def test_01_block_when_proposal_not_accepted(self):
        pipeline = PersonalityEvolutionPipeline()
        proposal = GrowthProposal(
            id="prop_x",
            status="proposed",
            proposed_changes=[ChangeItem(path="self_state.initiative", before=0.5, after=0.6)],
            confidence=0.8,
        )
        trait_states = {"initiative": create_trait_state("initiative", 0.5)}
        rec = pipeline.apply_approved_proposal(proposal=proposal, trait_states=trait_states, actor="test")
        self.assertEqual(rec.status, "blocked")

    def test_02_block_when_identity_unstable(self):
        pipeline = PersonalityEvolutionPipeline(block_when_identity_unstable=True)
        proposal = GrowthProposal(
            id="prop_ok",
            status="accepted",
            proposed_changes=[ChangeItem(path="self_state.initiative", before=0.5, after=0.62)],
            confidence=0.8,
            evaluator_meta={"reason_summary": "test"},
        )
        trait_states = {"initiative": create_trait_state("initiative", 0.5)}
        rec = pipeline.apply_approved_proposal(
            proposal=proposal,
            trait_states=trait_states,
            actor="test",
            identity_stability_report={"report_id": "isr_x", "is_stable": False},
        )
        self.assertEqual(rec.status, "blocked")
        self.assertIn("identity stability", rec.reason)

    def test_03_apply_updates_trait_states(self):
        pipeline = PersonalityEvolutionPipeline(block_when_identity_unstable=False)
        proposal = GrowthProposal(
            id="prop_apply",
            status="accepted",
            proposed_changes=[ChangeItem(path="self_state.initiative", before=0.5, after=0.58)],
            confidence=0.9,
        )
        trait_states = {"initiative": create_trait_state("initiative", 0.5)}
        rec = pipeline.apply_approved_proposal(
            proposal=proposal,
            trait_states=trait_states,
            actor="test",
        )
        self.assertEqual(rec.status, "applied")
        self.assertIn("initiative", rec.trait_states_after)

        # TraitStateUpdater 会把 current_value 更新（存在 dict 兼容）
        updated = trait_states["initiative"]["current_value"]
        self.assertNotEqual(updated, 0.5)

        snap = pipeline.get_snapshot()
        self.assertGreaterEqual(snap["applied"], 1)


if __name__ == "__main__":
    unittest.main()

