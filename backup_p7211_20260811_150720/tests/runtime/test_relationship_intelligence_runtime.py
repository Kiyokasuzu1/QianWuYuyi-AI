"""
Phase 3.5.27: Relationship Intelligence Runtime 集成测试
"""

from __future__ import annotations

import os
import tempfile
import unittest


class TestRelationshipIntelligenceRuntime(unittest.TestCase):
    def test_runtime_records_relationship_interaction(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "relationship_enabled": True,
                "event_driven_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            result = rc.record_relationship_interaction(
                user_message="我们一起长期合作这个项目，我很相信你",
                evidence_id="mem_rt_rel_1",
                emotion_tag="joy",
            )
            self.assertIsNotNone(result)
            state = rc.get_relationship_state_snapshot()
            model = rc.get_relationship_model_snapshot()
            self.assertIsNotNone(state)
            self.assertIsNotNone(model)
            self.assertGreaterEqual(model["total_interactions"], 1)
            self.assertGreaterEqual(model["total_shared_experiences"], 1)

            event_history = rc.get_domain_event_history(event_type="relationship_changed", limit=10)
            self.assertGreaterEqual(len(event_history), 1)


if __name__ == "__main__":
    unittest.main()
