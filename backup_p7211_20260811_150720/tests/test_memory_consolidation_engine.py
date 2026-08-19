"""
Phase 3.5.25: Memory Consolidation Engine 测试
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.memory.memory_consolidation_engine import MemoryConsolidationEngine


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestMemoryConsolidationEngine(unittest.TestCase):
    def test_consolidate_multi_memory_types_and_conflicts(self):
        engine = MemoryConsolidationEngine()
        memories = [
            {
                "id": "m1",
                "content": "我喜欢画画",
                "memory_class": "preference",
                "truth": 0.8,
                "timestamp": iso_days_ago(2),
            },
            {
                "id": "m2",
                "content": "我喜欢画画",
                "memory_class": "preference",
                "truth": 0.85,
                "timestamp": iso_days_ago(1),
            },
            {
                "id": "m3",
                "content": "我不喜欢画画",
                "memory_class": "preference",
                "truth": 0.7,
                "timestamp": iso_days_ago(3),
            },
            {
                "id": "m4",
                "content": "我是浅雾羽依",
                "memory_class": "identity",
                "truth": 1.0,
                "timestamp": iso_days_ago(100),
            },
            {
                "id": "m5",
                "content": "这是关于我们之间约定的记忆",
                "memory_class": "relationship",
                "truth": 0.9,
                "timestamp": iso_days_ago(20),
            },
            {
                "id": "m6",
                "content": "那次交流让我很开心",
                "memory_class": "event",
                "emotion_tag": "joy",
                "truth": 0.8,
                "timestamp": iso_days_ago(5),
            },
        ]

        report = engine.consolidate(memories)
        data = report.to_dict()

        self.assertGreaterEqual(data["stats"]["semantic_count"], 1)
        self.assertGreaterEqual(data["stats"]["identity_count"], 1)
        self.assertGreaterEqual(data["stats"]["relationship_count"], 1)
        self.assertGreaterEqual(data["stats"]["emotional_count"], 1)
        self.assertGreaterEqual(data["stats"]["conflict_count"], 1)

        semantic = data["semantic_memories"][0]
        self.assertGreaterEqual(semantic["reinforcement_count"], 2)

    def test_decay_and_snapshot(self):
        engine = MemoryConsolidationEngine()
        report = engine.consolidate([
            {
                "id": "old_id",
                "content": "我是浅雾羽依",
                "memory_class": "identity",
                "truth": 1.0,
                "timestamp": iso_days_ago(240),
            }
        ])
        identity = report.identity_memories[0]
        self.assertGreaterEqual(identity["decay_score"], 0.72)
        snap = engine.get_snapshot()
        self.assertGreaterEqual(snap["total_reports"], 1)


if __name__ == "__main__":
    unittest.main()

