# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_interest_signal.py

Phase 5.0-D3-C: InterestSignal / InterestSignalRegistry 单元测试。
"""
import unittest
from src.runtime.initiative.interest_signal import (
    ALL_INTEREST_TRENDS,
    DEFAULT_CONFIDENCE,
    DEFAULT_STRENGTH,
    DEFAULT_TREND,
    INTEREST_SIGNAL_SCHEMA_VERSION,
    INTEREST_TREND_FADING,
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    InterestSignal,
    InterestSignalRegistry,
    build_default_interest_signal_registry,
)


def _sig(
    topic: str = "AI绘画",
    *,
    trend: str = DEFAULT_TREND,
    strength: float = 0.6,
    confidence: float = 0.6,
    source_event_ids=None,
    source_reflection_ids=None,
    created_at: float = 0.0,
    last_seen_at: float = 0.0,
) -> InterestSignal:
    if source_event_ids is None:
        ev_ids = ["ev_1"]
    else:
        ev_ids = list(source_event_ids)
    if source_reflection_ids is None:
        ref_ids = []
    else:
        ref_ids = list(source_reflection_ids)
    return InterestSignal(
        topic=topic,
        trend=trend,
        strength=strength,
        confidence=confidence,
        source_event_ids=ev_ids,
        source_reflection_ids=ref_ids,
        created_at=created_at,
        last_seen_at=last_seen_at,
    )


class TestInterestSignalCreation(unittest.TestCase):
    def test_default_creation(self):
        s = InterestSignal(topic="AI绘画", source_event_ids=["ev_1"])
        self.assertTrue(s.signal_id.startswith("sig_"))
        self.assertEqual(s.topic, "AI绘画")
        self.assertEqual(s.trend, DEFAULT_TREND)
        self.assertEqual(s.strength, DEFAULT_STRENGTH)
        self.assertEqual(s.confidence, DEFAULT_CONFIDENCE)
        self.assertEqual(s.source_event_ids, ["ev_1"])
        self.assertEqual(s.source_reflection_ids, [])
        self.assertEqual(s.version, INTEREST_SIGNAL_SCHEMA_VERSION)

    def test_topic_clipping(self):
        s = InterestSignal(topic="x" * 500, source_event_ids=["ev_1"])
        self.assertEqual(len(s.topic), 128)

    def test_topic_invalid(self):
        s = InterestSignal(topic=None, source_event_ids=["ev_1"])  # type: ignore[arg-type]
        self.assertEqual(s.topic, "")

    def test_strength_clipping(self):
        s = InterestSignal(topic="x", source_event_ids=["ev_1"], strength=2.0)
        self.assertEqual(s.strength, 1.0)
        s2 = InterestSignal(topic="x", source_event_ids=["ev_1"], strength=-0.5)
        self.assertEqual(s2.strength, 0.0)
        s3 = InterestSignal(topic="x", source_event_ids=["ev_1"], strength=float("nan"))
        self.assertEqual(s3.strength, DEFAULT_STRENGTH)

    def test_confidence_clipping(self):
        s = InterestSignal(topic="x", source_event_ids=["ev_1"], confidence=10.0)
        self.assertEqual(s.confidence, 1.0)

    def test_trend_fallback(self):
        s = InterestSignal(topic="x", source_event_ids=["ev_1"], trend="bogus")
        self.assertEqual(s.trend, DEFAULT_TREND)

    def test_source_event_ids_dedup(self):
        s = InterestSignal(topic="x", source_event_ids=["ev_1", "ev_1", "ev_2"])
        self.assertEqual(s.source_event_ids, ["ev_1", "ev_2"])

    def test_source_reflection_ids_dedup(self):
        s = InterestSignal(
            topic="x",
            source_event_ids=["ev_1"],
            source_reflection_ids=["r_1", "r_1", "r_2"],
        )
        self.assertEqual(s.source_reflection_ids, ["r_1", "r_2"])

    def test_source_truncation(self):
        evs = [f"ev_{i}" for i in range(100)]
        s = InterestSignal(topic="x", source_event_ids=evs)
        self.assertLessEqual(len(s.source_event_ids), 64)

    def test_rationale_clipping(self):
        s = InterestSignal(topic="x", source_event_ids=["ev_1"], rationale="r" * 1000)
        self.assertEqual(len(s.rationale), 512)

    def test_metadata_truncation(self):
        meta = {f"k{i}": i for i in range(30)}
        s = InterestSignal(topic="x", source_event_ids=["ev_1"], metadata=meta)
        self.assertEqual(len(s.metadata), 16)

    def test_invalid_source_type(self):
        s = InterestSignal(topic="x", source_event_ids="not a list")  # type: ignore[arg-type]
        self.assertEqual(s.source_event_ids, [])


class TestInterestSignalSourceValidation(unittest.TestCase):
    def test_has_source_with_event(self):
        s = _sig(source_event_ids=["ev_1"])
        self.assertTrue(s.has_source())

    def test_has_source_with_reflection(self):
        s = _sig(source_event_ids=[], source_reflection_ids=["r_1"])
        self.assertTrue(s.has_source())

    def test_no_source(self):
        s = _sig(source_event_ids=[], source_reflection_ids=[])
        self.assertFalse(s.has_source())

    def test_is_valid_no_topic(self):
        s = _sig(topic="", source_event_ids=["ev_1"])
        self.assertFalse(s.is_valid())

    def test_is_valid_no_source(self):
        s = _sig(topic="AI", source_event_ids=[])
        self.assertFalse(s.is_valid())

    def test_is_valid_invalid_strength(self):
        s = _sig()
        s.strength = 2.0
        self.assertFalse(s.is_valid())

    def test_is_valid_invalid_trend(self):
        s = _sig()
        s.trend = "bogus"
        self.assertFalse(s.is_valid())


class TestInterestSignalTrendChange(unittest.TestCase):
    def test_decay(self):
        s = _sig(strength=1.0)
        s.decay(0.5)
        self.assertEqual(s.strength, 0.5)

    def test_decay_factor_clipping(self):
        s = _sig(strength=0.5)
        s.decay(2.0)
        self.assertEqual(s.strength, 0.5 * 1.0)
        s2 = _sig(strength=0.5)
        s2.decay(-1.0)
        self.assertEqual(s2.strength, 0.0)

    def test_decay_zero(self):
        s = _sig(strength=0.5)
        s.decay(0.0)
        self.assertEqual(s.strength, 0.0)

    def test_decay_invalid_factor(self):
        s = _sig(strength=0.5)
        s.decay("bad")  # type: ignore[arg-type]
        self.assertEqual(s.strength, 0.5 * 0.95)

    def test_set_trend_valid(self):
        s = _sig()
        s.set_trend(INTEREST_TREND_RISING)
        self.assertEqual(s.trend, INTEREST_TREND_RISING)

    def test_set_trend_invalid(self):
        s = _sig()
        s.set_trend("bogus")
        self.assertEqual(s.trend, DEFAULT_TREND)

    def test_touch(self):
        s = _sig()
        s.touch(123.0)
        self.assertEqual(s.last_seen_at, 123.0)

    def test_touch_invalid(self):
        s = _sig()
        s.touch("bad")  # type: ignore[arg-type]
        self.assertEqual(s.last_seen_at, 0.0)


class TestInterestSignalSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        s = _sig(
            topic="AI绘画",
            trend=INTEREST_TREND_RISING,
            strength=0.8,
            confidence=0.7,
            source_event_ids=["ev_1", "ev_2"],
            source_reflection_ids=["r_1"],
            created_at=100.0,
            last_seen_at=110.0,
        )
        d = s.to_dict()
        s2 = InterestSignal.from_dict(d)
        self.assertEqual(s2.topic, s.topic)
        self.assertEqual(s2.trend, s.trend)
        self.assertEqual(s2.strength, s.strength)
        self.assertEqual(s2.confidence, s.confidence)
        self.assertEqual(s2.source_event_ids, s.source_event_ids)
        self.assertEqual(s2.source_reflection_ids, s.source_reflection_ids)

    def test_from_dict_invalid(self):
        s = InterestSignal.from_dict("not a dict")  # type: ignore[arg-type]
        self.assertEqual(s.topic, "invalid")

    def test_from_dict_garbage(self):
        s = InterestSignal.from_dict({
            "strength": "bad",
            "confidence": "bad",
            "trend": "bogus",
            "source_event_ids": "bad",
        })
        self.assertEqual(s.strength, DEFAULT_STRENGTH)
        self.assertEqual(s.confidence, DEFAULT_CONFIDENCE)
        self.assertEqual(s.trend, DEFAULT_TREND)
        self.assertEqual(s.source_event_ids, [])

    def test_summary(self):
        s = _sig()
        sm = s.summary()
        self.assertIn("signal_id", sm)
        self.assertEqual(sm["topic"], "AI绘画")
        self.assertEqual(sm["source_count"], 1)


class TestInterestSignalRegistry(unittest.TestCase):
    def test_default_creation(self):
        r = InterestSignalRegistry()
        self.assertEqual(r.size, 0)
        self.assertEqual(r.capacity, 256)

    def test_add_valid(self):
        r = InterestSignalRegistry()
        ok = r.add(_sig())
        self.assertTrue(ok)
        self.assertEqual(r.size, 1)

    def test_add_invalid_no_source(self):
        r = InterestSignalRegistry()
        ok = r.add(_sig(source_event_ids=[]))
        self.assertFalse(ok)
        self.assertEqual(r.dropped_count, 1)

    def test_add_invalid_type(self):
        r = InterestSignalRegistry()
        ok = r.add("not a signal")  # type: ignore[arg-type]
        self.assertFalse(ok)

    def test_capacity_eviction(self):
        r = InterestSignalRegistry(capacity=2)
        r.add(_sig(topic="A"))
        r.add(_sig(topic="B"))
        r.add(_sig(topic="C"))
        self.assertEqual(r.size, 2)
        self.assertEqual(r.dropped_count, 1)
        # A should be evicted
        self.assertEqual(r.find_by_topic("A"), [])
        self.assertEqual(len(r.find_by_topic("B")), 1)
        self.assertEqual(len(r.find_by_topic("C")), 1)

    def test_get_by_id(self):
        r = InterestSignalRegistry()
        s = _sig()
        r.add(s)
        self.assertIs(r.get(s.signal_id), s)
        self.assertIsNone(r.get("not_exists"))

    def test_remove(self):
        r = InterestSignalRegistry()
        s = _sig()
        r.add(s)
        self.assertTrue(r.remove(s.signal_id))
        self.assertEqual(r.size, 0)
        self.assertIsNone(r.get(s.signal_id))

    def test_find_by_topic(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="A"))
        r.add(_sig(topic="A"))
        r.add(_sig(topic="B"))
        result = r.find_by_topic("A")
        self.assertEqual(len(result), 2)

    def test_find_by_trend(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="A", trend=INTEREST_TREND_RISING))
        r.add(_sig(topic="B", trend=INTEREST_TREND_FADING))
        result = r.find_by_trend(INTEREST_TREND_RISING)
        self.assertEqual(len(result), 1)

    def test_find_by_strength(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="A", strength=0.2))
        r.add(_sig(topic="B", strength=0.8))
        result = r.find_by_strength(min_strength=0.5)
        self.assertEqual(len(result), 1)

    def test_list_with_limit(self):
        r = InterestSignalRegistry()
        for i in range(5):
            r.add(_sig(topic=f"T{i}"))
        self.assertEqual(len(r.list(limit=2)), 2)
        self.assertEqual(len(r.list(limit=0)), 0)
        self.assertEqual(len(r.list(limit="bad")), 5)  # type: ignore[arg-type]

    def test_update_signal(self):
        r = InterestSignalRegistry()
        s = _sig()
        r.add(s)
        ok = r.update_signal(
            s.signal_id,
            trend=INTEREST_TREND_RISING,
            strength=0.9,
            confidence=0.95,
            append_event_id="ev_2",
        )
        self.assertTrue(ok)
        self.assertEqual(s.trend, INTEREST_TREND_RISING)
        self.assertEqual(s.strength, 0.9)
        self.assertEqual(s.source_event_ids, ["ev_1", "ev_2"])

    def test_update_signal_not_exists(self):
        r = InterestSignalRegistry()
        ok = r.update_signal("missing", trend=INTEREST_TREND_RISING)
        self.assertFalse(ok)

    def test_clear(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="A"))
        r.add(_sig(topic="B"))
        n = r.clear()
        self.assertEqual(n, 2)
        self.assertEqual(r.size, 0)

    def test_trend_counts(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="A", trend=INTEREST_TREND_RISING))
        r.add(_sig(topic="B", trend=INTEREST_TREND_RISING))
        r.add(_sig(topic="C", trend=INTEREST_TREND_FADING))
        counts = r.trend_counts()
        self.assertEqual(counts[INTEREST_TREND_RISING], 2)
        self.assertEqual(counts[INTEREST_TREND_FADING], 1)

    def test_has_topic(self):
        r = InterestSignalRegistry()
        r.add(_sig(topic="AI绘画"))
        self.assertTrue(r.has_topic("AI绘画"))
        self.assertFalse(r.has_topic("music"))

    def test_describe(self):
        r = InterestSignalRegistry()
        r.add(_sig())
        d = r.describe()
        self.assertEqual(d["size"], 1)
        self.assertEqual(d["added"], 1)


class TestRegistryThreadSafety(unittest.TestCase):
    def test_concurrent_add(self):
        import threading
        r = InterestSignalRegistry(capacity=1000)
        def worker():
            for i in range(20):
                r.add(_sig(topic=f"T{threading.get_ident()}_{i}"))
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 至少添加了一些
        self.assertGreater(r.size, 0)


class TestFactory(unittest.TestCase):
    def test_build_default(self):
        r = build_default_interest_signal_registry(capacity=10)
        self.assertEqual(r.capacity, 10)


if __name__ == "__main__":
    unittest.main()
