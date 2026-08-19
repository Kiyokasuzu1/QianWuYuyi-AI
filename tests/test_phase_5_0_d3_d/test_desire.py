# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_desire.py

Phase 5.0-D3-D: Desire 单元测试。

覆盖:
- 创建 (default, factory, from_dict)
- 趋势变化 (set_trend, boost, decay)
- 来源验证 (has_evidence, is_valid)
- 序列化 (to_dict, from_dict, summary)
- DesireRegistry (add, get, list, remove, find, clear, thread safety)
"""
import threading
import unittest
from src.runtime.goal.desire import (
    ALL_DESIRE_TRENDS,
    DEFAULT_DESIRE_TREND,
    DESIRE_SCHEMA_VERSION,
    DESIRE_TREND_FADING,
    DESIRE_TREND_NEW,
    DESIRE_TREND_RISING,
    DESIRE_TREND_STABLE,
    Desire,
    DesireRegistry,
    build_desire,
)


class TestDesireBasic(unittest.TestCase):
    def test_default_construction(self):
        d = Desire()
        self.assertTrue(d.desire_id.startswith("des_"))
        self.assertEqual(d.trend, DEFAULT_DESIRE_TREND)
        self.assertEqual(d.version, DESIRE_SCHEMA_VERSION)
        self.assertFalse(d.has_evidence())

    def test_factory(self):
        d = build_desire(
            topic="ai_art",
            strength=0.7,
            reason="测试",
            supporting_signal_ids=["s1"],
            supporting_reflection_ids=["r1"],
            confidence=0.6,
            trend=DESIRE_TREND_RISING,
            now=10.0,
        )
        self.assertEqual(d.topic, "ai_art")
        self.assertEqual(d.trend, DESIRE_TREND_RISING)
        self.assertTrue(d.has_evidence())
        self.assertTrue(d.is_valid())
        self.assertEqual(d.created_at, 10.0)

    def test_invalid_no_topic(self):
        d = build_desire(topic="", supporting_signal_ids=["s1"])
        self.assertFalse(d.is_valid())

    def test_invalid_no_evidence(self):
        d = Desire(topic="t")
        self.assertFalse(d.has_evidence())
        self.assertFalse(d.is_valid())

    def test_invalid_trend_fallback(self):
        d = Desire(topic="t", trend="bogus", supporting_signal_ids=["s1"])
        self.assertEqual(d.trend, DEFAULT_DESIRE_TREND)


class TestDesireTrend(unittest.TestCase):
    def _make(self, trend: str = DESIRE_TREND_NEW) -> Desire:
        return build_desire(
            topic="t",
            trend=trend,
            supporting_signal_ids=["s1"],
        )

    def test_set_trend(self):
        d = self._make()
        self.assertTrue(d.set_trend(DESIRE_TREND_RISING))
        self.assertEqual(d.trend, DESIRE_TREND_RISING)

    def test_set_trend_invalid(self):
        d = self._make()
        self.assertFalse(d.set_trend("bogus"))
        self.assertEqual(d.trend, DESIRE_TREND_NEW)

    def test_set_trend_same(self):
        d = self._make()
        self.assertTrue(d.set_trend(DESIRE_TREND_NEW))

    def test_boost_increase(self):
        d = self._make()
        d.boost(0.1, now=1.0)
        self.assertAlmostEqual(d.strength, 0.6)
        self.assertEqual(d.updated_at, 1.0)

    def test_boost_clip_high(self):
        d = self._make()
        d.boost(10.0)
        self.assertEqual(d.strength, 1.0)

    def test_boost_invalid_delta(self):
        d = self._make()
        d.boost("bogus")
        self.assertEqual(d.strength, 0.5)

    def test_decay_decrease(self):
        d = self._make()
        d.decay(0.5, now=1.0)
        self.assertAlmostEqual(d.strength, 0.25)
        self.assertEqual(d.updated_at, 1.0)

    def test_decay_clip_factor(self):
        d = self._make()
        d.decay(2.0)
        self.assertEqual(d.strength, 0.5)
        d.decay(-1.0)
        self.assertEqual(d.strength, 0.0)

    def test_decay_invalid_factor(self):
        d = self._make()
        d.decay("bogus")
        self.assertEqual(d.strength, 0.5)

    def test_touch(self):
        d = self._make()
        d.touch(now=1.0)
        self.assertEqual(d.updated_at, 1.0)

    def test_touch_no_time(self):
        d = self._make()
        d.touch()
        # 不变


class TestDesireSourceIds(unittest.TestCase):
    def test_add_signal_ids(self):
        d = Desire(topic="t", supporting_signal_ids=["a"])
        d.add_supporting_signal_ids(["b", "a", "c"])
        self.assertEqual(d.supporting_signal_ids, ["a", "b", "c"])

    def test_add_reflection_ids(self):
        d = Desire(topic="t", supporting_signal_ids=["a"], supporting_reflection_ids=["x"])
        d.add_supporting_reflection_ids(["y", "x", "z"])
        self.assertEqual(d.supporting_reflection_ids, ["x", "y", "z"])

    def test_add_empty(self):
        d = Desire(topic="t", supporting_signal_ids=["a"])
        d.add_supporting_signal_ids([])
        self.assertEqual(d.supporting_signal_ids, ["a"])

    def test_add_limit(self):
        d = Desire(topic="t", supporting_signal_ids=["a"])
        d.add_supporting_signal_ids([f"id_{i}" for i in range(200)])
        self.assertLessEqual(len(d.supporting_signal_ids), 64)


class TestDesireSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        d = build_desire(
            topic="t",
            strength=0.6,
            trend=DESIRE_TREND_RISING,
            reason="r",
            supporting_signal_ids=["s1"],
            supporting_reflection_ids=["r1"],
            confidence=0.7,
            now=10.0,
        )
        d2 = Desire.from_dict(d.to_dict())
        self.assertEqual(d2.desire_id, d.desire_id)
        self.assertEqual(d2.topic, d.topic)
        self.assertEqual(d2.trend, d.trend)
        self.assertEqual(d2.supporting_signal_ids, d.supporting_signal_ids)
        self.assertEqual(d2.supporting_reflection_ids, d.supporting_reflection_ids)
        self.assertAlmostEqual(d2.confidence, d.confidence)
        self.assertEqual(d2.created_at, d.created_at)

    def test_from_dict_invalid(self):
        d = Desire.from_dict("not a dict")
        self.assertEqual(d.topic, "invalid")

    def test_summary(self):
        d = build_desire(
            topic="t",
            supporting_signal_ids=["s1", "s2"],
            supporting_reflection_ids=["r1"],
            trend=DESIRE_TREND_STABLE,
        )
        s = d.summary()
        self.assertEqual(s["topic"], "t")
        self.assertEqual(s["supporting_signal_count"], 2)
        self.assertEqual(s["supporting_reflection_count"], 1)
        self.assertTrue(s["has_evidence"])

    def test_repr(self):
        d = build_desire(topic="t", supporting_signal_ids=["a"])
        r = repr(d)
        self.assertIn("Desire", r)
        self.assertIn("t", r)


class TestDesireRegistry(unittest.TestCase):
    def test_add_get(self):
        r = DesireRegistry()
        d = build_desire(topic="t", supporting_signal_ids=["a"])
        self.assertTrue(r.add(d))
        self.assertEqual(r.get(d.desire_id), d)

    def test_add_duplicate_rejected(self):
        r = DesireRegistry()
        d = build_desire(topic="t", supporting_signal_ids=["a"])
        self.assertTrue(r.add(d))
        self.assertFalse(r.add(d))

    def test_add_non_desire(self):
        r = DesireRegistry()
        self.assertFalse(r.add("not desire"))
        self.assertFalse(r.add(None))

    def test_get_missing(self):
        r = DesireRegistry()
        self.assertIsNone(r.get("missing"))
        self.assertIsNone(r.get(""))

    def test_remove(self):
        r = DesireRegistry()
        d = build_desire(topic="t", supporting_signal_ids=["a"])
        r.add(d)
        self.assertTrue(r.remove(d.desire_id))
        self.assertIsNone(r.get(d.desire_id))
        self.assertFalse(r.remove(d.desire_id))
        self.assertFalse(r.remove(""))

    def test_list(self):
        r = DesireRegistry()
        for i in range(3):
            d = build_desire(topic=f"t{i}", supporting_signal_ids=[f"s{i}"])
            r.add(d)
        self.assertEqual(len(r.list()), 3)

    def test_get_by_topic(self):
        r = DesireRegistry()
        d1 = build_desire(topic="t1", supporting_signal_ids=["a"])
        d2 = build_desire(topic="t1", supporting_signal_ids=["b"])
        d3 = build_desire(topic="t2", supporting_signal_ids=["c"])
        r.add(d1)
        r.add(d2)
        r.add(d3)
        latest = r.get_by_topic("t1")
        # 最近加入的
        self.assertIsNotNone(latest)
        self.assertIn(latest, (d1, d2))

    def test_find_by_topic(self):
        r = DesireRegistry()
        d1 = build_desire(topic="t1", supporting_signal_ids=["a"])
        d2 = build_desire(topic="t1", supporting_signal_ids=["b"])
        d3 = build_desire(topic="t2", supporting_signal_ids=["c"])
        r.add(d1)
        r.add(d2)
        r.add(d3)
        self.assertEqual(len(r.find_by_topic("t1")), 2)
        self.assertEqual(len(r.find_by_topic("t2")), 1)
        self.assertEqual(len(r.find_by_topic("missing")), 0)
        self.assertEqual(len(r.find_by_topic("")), 0)

    def test_clear(self):
        r = DesireRegistry()
        for i in range(3):
            d = build_desire(topic=f"t{i}", supporting_signal_ids=[f"s{i}"])
            r.add(d)
        n = r.clear()
        self.assertEqual(n, 3)
        self.assertEqual(r.size, 0)

    def test_max_size_eviction(self):
        r = DesireRegistry(max_size=2)
        d1 = build_desire(topic="t1", supporting_signal_ids=["a"])
        d2 = build_desire(topic="t2", supporting_signal_ids=["b"])
        d3 = build_desire(topic="t3", supporting_signal_ids=["c"])
        r.add(d1)
        r.add(d2)
        r.add(d3)
        self.assertEqual(r.size, 2)
        # 最早 d1 应被淘汰
        self.assertIsNone(r.get(d1.desire_id))
        self.assertIsNotNone(r.get(d2.desire_id))
        self.assertIsNotNone(r.get(d3.desire_id))

    def test_describe(self):
        r = DesireRegistry()
        d = build_desire(topic="t", supporting_signal_ids=["a"])
        r.add(d)
        desc = r.describe()
        self.assertEqual(desc["size"], 1)
        self.assertEqual(desc["added"], 1)
        self.assertEqual(desc["max_size"], 1024)

    def test_concurrent_add(self):
        r = DesireRegistry(max_size=1000)
        results = []
        def worker(i: int):
            d = build_desire(topic=f"t{i}", supporting_signal_ids=[f"s{i}"])
            results.append(r.add(d))
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(sum(1 for x in results if x), 50)
        self.assertEqual(r.size, 50)

    def test_remove_clears_topic_index(self):
        r = DesireRegistry()
        d = build_desire(topic="t1", supporting_signal_ids=["a"])
        r.add(d)
        r.remove(d.desire_id)
        self.assertIsNone(r.get_by_topic("t1"))


if __name__ == "__main__":
    unittest.main()
