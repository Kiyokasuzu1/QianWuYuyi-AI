"""
Phase 3.5.28: Emotion Dynamics Runtime 集成测试
"""

from __future__ import annotations

import os
import tempfile
import unittest


class TestEmotionDynamicsRuntime(unittest.TestCase):
    def test_runtime_records_emotion_event_and_emits_event(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "emotion_enabled": True,
                "event_driven_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            result = rc.record_emotion_event(
                event_type="user_praise",
                intensity=0.9,
                description="用户表达赞赏",
                source="user",
                memory_id="mem_rt_em_1",
            )
            self.assertIsNotNone(result)
            self.assertGreaterEqual(result["snapshot"]["total_transitions"], 1)

            dynamics = rc.get_emotion_dynamics_snapshot()
            self.assertIsNotNone(dynamics)
            summary = rc.get_emotional_memory_summary(limit=50)
            self.assertIsNotNone(summary)
            self.assertGreaterEqual(summary["total_traces"], 1)

            history = rc.get_domain_event_history(event_type="emotion_changed", limit=10)
            self.assertGreaterEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
