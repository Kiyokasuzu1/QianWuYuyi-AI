"""
Phase 3.5.27: Relationship Intelligence 测试
"""

from __future__ import annotations

import tempfile
import unittest

from src.relationship.relationship_model import RelationshipModel, RelationshipIntelligenceEngine
from src.relationship.relationship_repository import RelationshipRepository
from src.relationship.relationship_state import RelationshipState


class TestRelationshipIntelligenceEngine(unittest.TestCase):
    def test_relationship_intelligence_updates_state_and_model(self):
        engine = RelationshipIntelligenceEngine()
        state = RelationshipState()
        model = RelationshipModel()

        result = engine.process_interaction(
            state=state,
            model=model,
            user_message="我们一起合作开发这个项目，我很相信你",
            evidence_id="mem_rel_1",
            emotion_tag="joy",
        )

        self.assertIsNotNone(result["event"])
        self.assertTrue(result["evaluation"]["passed"])
        self.assertGreater(state.trust, 0.0)
        self.assertGreater(state.familiarity, 0.0)
        self.assertGreater(state.collaboration, 0.0)
        self.assertGreaterEqual(len(model.interaction_history), 1)
        self.assertGreaterEqual(len(model.shared_experiences), 1)
        self.assertGreaterEqual(len(model.emotional_patterns), 1)

    def test_relationship_repository_model_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = RelationshipRepository(tmp, user_id="user_rel")
            model = RelationshipModel(
                interaction_history=[{"interaction_id": "i1"}],
                trust_changes=[{"change_id": "c1"}],
                emotional_patterns=[{"emotion_tag": "joy", "count": 2}],
                shared_experiences=[{"experience_id": "e1"}],
                relationship_milestones=[{"milestone_id": "m1"}],
            )
            repo.save_relationship_model(model)
            loaded = repo.load_relationship_model()
            self.assertEqual(len(loaded.interaction_history), 1)
            self.assertEqual(len(loaded.relationship_milestones), 1)


if __name__ == "__main__":
    unittest.main()
