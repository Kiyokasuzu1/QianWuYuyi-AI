"""
Phase 3.5.16: Memory Relevance Evaluator 测试

覆盖：
- 初始化
- 时间衰减
- 关系 / 身份 / 情绪相关性
- 排序与审计历史
- MemoryService 接线
- MemorySystem 主检索路径接线
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator
from src.memory.memory_service import MemoryService
from src.memory.memory_store import MemoryStore


def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class _StubVectorMemory:
    def __init__(self, results):
        self._results = results

    def search(self, query: str, top_k: int = 5):
        return self._results[:top_k]


class _StubEventMemory:
    def __init__(self, results):
        self._results = results

    def search(self, query: str, limit: int = 5):
        return self._results[:limit]


class _StubIdentityMemory:
    def get_identity_prompt(self):
        return "我是浅雾羽依，我重视真诚、成长与关系。"


class TestMemoryRelevanceEvaluator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history_path = os.path.join(self.tmp.name, "memory_relevance_history.json")
        self.evaluator = MemoryRelevanceEvaluator(history_path=self.history_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_01_initialization(self):
        snap = self.evaluator.get_snapshot()
        self.assertEqual(snap.total_records, 0)
        self.assertEqual(self.evaluator.get_history(), [])

    def test_02_time_decay_affects_retrieval_priority(self):
        recent = {
            "id": "mem_recent",
            "content": "我们最近讨论了长期成长路线",
            "memory_class": "event",
            "importance": 0.7,
            "timestamp": iso_days_ago(2),
        }
        old = {
            "id": "mem_old",
            "content": "我们很久以前讨论了长期成长路线",
            "memory_class": "event",
            "importance": 0.7,
            "timestamp": iso_days_ago(240),
        }
        r1 = self.evaluator.evaluate(recent, "成长路线")
        r2 = self.evaluator.evaluate(old, "成长路线")
        self.assertGreater(r1.breakdown.time_decay_score, r2.breakdown.time_decay_score)
        self.assertGreater(r1.breakdown.final_score, r2.breakdown.final_score)

    def test_03_relationship_identity_emotional_factors(self):
        relationship_mem = {
            "id": "mem_rel",
            "content": "这是关于我们之间约定与信任边界的记忆",
            "memory_class": "relationship",
            "importance": 0.6,
            "timestamp": iso_days_ago(30),
        }
        identity_mem = {
            "id": "mem_idn",
            "content": "我是浅雾羽依，我重视真诚与成长",
            "memory_class": "identity",
            "importance": 0.5,
            "timestamp": iso_days_ago(200),
        }
        emotion_mem = {
            "id": "mem_emo",
            "content": "那次交流让我很难过，但后来重新安心下来",
            "memory_class": "event",
            "importance": 0.6,
            "emotion_tag": "sad",
            "timestamp": iso_days_ago(5),
        }

        rr = self.evaluator.evaluate(relationship_mem, "我们的关系和约定", context={"relationship_focus": True})
        ir = self.evaluator.evaluate(identity_mem, "你是谁，你重视什么", context={"identity_focus": True})
        er = self.evaluator.evaluate(emotion_mem, "你当时的情绪怎么样", context={"current_emotion": "sad"})

        self.assertGreaterEqual(rr.breakdown.relationship_score, 0.7)
        self.assertGreaterEqual(ir.breakdown.identity_score, 0.7)
        self.assertGreaterEqual(er.breakdown.emotional_score, 0.7)

    def test_04_rank_memories_and_audit_history(self):
        memories = [
            {
                "id": "m1",
                "content": "我是浅雾羽依",
                "memory_class": "identity",
                "importance": 0.4,
                "timestamp": iso_days_ago(100),
            },
            {
                "id": "m2",
                "content": "今天普通聊天记录",
                "memory_class": "event",
                "importance": 0.2,
                "timestamp": iso_days_ago(1),
            },
        ]
        ranked = self.evaluator.rank_memories(memories, query="你是谁", context={"identity_focus": True}, top_k=2)
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["id"], "m1")
        self.assertIn("relevance_audit_id", ranked[0])

        history = self.evaluator.get_history(limit=10)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].memory_id, "m2")
        snap = self.evaluator.get_snapshot()
        self.assertEqual(snap.total_records, 2)


class TestMemoryServiceIntegration(unittest.TestCase):
    def test_01_semantic_search_uses_relevance_evaluator(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(os.path.join(tmp, "memory.json"))
            m1 = store.add({
                "id": "mem_rel_1",
                "user_id": "u1",
                "role": "user",
                "content": "我们的约定和信任让我一直记得",
                "memory_class": "relationship",
                "importance": 0.8,
                "timestamp": iso_days_ago(10),
            })
            m2 = store.add({
                "id": "mem_evt_1",
                "user_id": "u1",
                "role": "user",
                "content": "今天吃了晚饭",
                "memory_class": "event",
                "importance": 0.8,
                "timestamp": iso_days_ago(1),
            })
            service = MemoryService(
                store,
                relevance_evaluator=MemoryRelevanceEvaluator(),
                vector_memory=_StubVectorMemory([
                    {"mem_id": "mem_evt_1", "relevance": 0.92},
                    {"mem_id": "mem_rel_1", "relevance": 0.80},
                ]),
            )

            results = service.semantic_search(
                "我们的关系和约定",
                top_k=2,
                relationship_focus=True,
            )

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0]["id"], "mem_rel_1")
            self.assertIn("retrieval_priority", results[0])
            self.assertIn("relevance_breakdown", results[0])
            self.assertGreaterEqual(len(service.get_relevance_history(limit=10)), 2)


class TestMemorySystemIntegration(unittest.TestCase):
    def test_01_memory_system_search_updates_relevance_audit(self):
        from src.memory.memory_system import MemorySystem

        with tempfile.TemporaryDirectory() as tmp:
            system = MemorySystem.__new__(MemorySystem)
            system.store = MemoryStore(os.path.join(tmp, "memory.json"))
            system.vector = _StubVectorMemory([])
            system.event = _StubEventMemory([
                {
                    "event": "我们曾经做出一个重要约定",
                    "memory_summary": "与用户建立长期陪伴关系",
                    "importance": 0.9,
                    "timestamp": iso_days_ago(15),
                    "category": "关系建立",
                }
            ])
            system.identity = _StubIdentityMemory()
            system.relevance_evaluator = MemoryRelevanceEvaluator()

            system.store.add({
                "id": "mem_chat_1",
                "user_id": "terminal_user",
                "role": "user",
                "content": "普通聊天内容",
                "timestamp": iso_days_ago(1),
                "importance": 0.2,
            })

            results = system.search("terminal_user", "我们的约定", top_k=3)
            self.assertGreaterEqual(len(results), 1)
            history = system.get_relevance_history(limit=10)
            self.assertGreaterEqual(len(history), 1)
            snapshot = system.get_relevance_snapshot()
            self.assertGreaterEqual(snapshot["total_records"], 1)


if __name__ == "__main__":
    unittest.main()
