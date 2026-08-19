"""
Phase 3.5.28: Emotion Dynamics Engine 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.emotion import EmotionEvent, EmotionManager, EmotionRepository, EmotionTraceRepository
from src.emotion.emotion_dynamics_engine import EmotionDynamicsEngine


class TestEmotionDynamicsEngine(unittest.TestCase):
    def test_process_event_creates_transition_and_memory_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EmotionManager(
                repository=EmotionRepository(os.path.join(tmp, "emotion_state.json")),
                trace_repository=EmotionTraceRepository(os.path.join(tmp, "emotion_traces.json")),
                counter_file=os.path.join(tmp, "emotion_counter.json"),
            )
            engine = EmotionDynamicsEngine(manager)

            result = engine.process_event(
                EmotionEvent("user_praise", intensity=0.9, source="user"),
                memory_id="mem_em_1",
            )

            self.assertIsNotNone(result["transition"])
            self.assertEqual(result["transition"]["event_type"], "user_praise")
            self.assertGreaterEqual(result["snapshot"]["total_transitions"], 1)

            summary = engine.get_emotional_memory_summary(limit=50)
            self.assertGreaterEqual(summary["total_traces"], 1)
            self.assertGreaterEqual(len(summary["dominant_emotions"]), 1)
            self.assertIn("mem_em_1", summary["recent_memory_ids"])

    def test_persistent_mood_and_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = EmotionManager(
                repository=EmotionRepository(os.path.join(tmp, "emotion_state.json")),
                trace_repository=EmotionTraceRepository(os.path.join(tmp, "emotion_traces.json")),
                counter_file=os.path.join(tmp, "emotion_counter.json"),
            )
            engine = EmotionDynamicsEngine(manager)

            engine.process_event(EmotionEvent("user_praise", intensity=0.8))
            engine.process_event(EmotionEvent("achievement", intensity=0.7))

            snap = engine.get_snapshot()
            history = engine.get_transition_history(limit=10)
            self.assertGreaterEqual(snap["mood_streak"], 1)
            self.assertGreaterEqual(len(history), 2)


if __name__ == "__main__":
    unittest.main()
