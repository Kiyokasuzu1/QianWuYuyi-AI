# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_internal_state.py

Phase 5.0-D3-C: InternalState / InternalStateStore 单元测试。
"""
import unittest
from src.runtime.initiative.internal_state import (
    ALL_INTERNAL_MOODS,
    DEFAULT_ATTENTION,
    DEFAULT_CURIOSITY,
    DEFAULT_ENERGY,
    DEFAULT_MOOD,
    DEFAULT_SOCIAL,
    INTERNAL_MOOD_CURIOUS,
    INTERNAL_MOOD_NEUTRAL,
    INTERNAL_MOOD_TIRED,
    INTERNAL_STATE_SCHEMA_VERSION,
    InternalState,
    InternalStateStore,
    build_default_internal_state_store,
)
from src.runtime.lifecycle.internal.clock import FrozenClock


class TestInternalStateCreation(unittest.TestCase):
    def test_default_creation(self):
        s = InternalState()
        self.assertTrue(s.state_id.startswith("ist_"))
        self.assertEqual(s.mood, DEFAULT_MOOD)
        self.assertEqual(s.energy_level, DEFAULT_ENERGY)
        self.assertEqual(s.curiosity_drive, DEFAULT_CURIOSITY)
        self.assertEqual(s.attention_focus, "")
        self.assertEqual(s.social_disposition, DEFAULT_SOCIAL)
        self.assertEqual(s.last_stimulus_event_id, "")
        self.assertEqual(s.last_updated_at, 0.0)
        self.assertEqual(s.version, INTERNAL_STATE_SCHEMA_VERSION)
        self.assertEqual(s.created_at, 0.0)

    def test_custom_creation(self):
        s = InternalState(
            mood=INTERNAL_MOOD_CURIOUS,
            energy_level=0.8,
            curiosity_drive=0.9,
            attention_focus="AI绘画",
            social_disposition=0.7,
            last_stimulus_event_id="ev_1",
            last_updated_at=123.0,
            created_at=120.0,
        )
        self.assertEqual(s.mood, INTERNAL_MOOD_CURIOUS)
        self.assertEqual(s.energy_level, 0.8)
        self.assertEqual(s.curiosity_drive, 0.9)
        self.assertEqual(s.attention_focus, "AI绘画")
        self.assertEqual(s.social_disposition, 0.7)
        self.assertEqual(s.last_stimulus_event_id, "ev_1")
        self.assertEqual(s.last_updated_at, 123.0)
        self.assertEqual(s.created_at, 120.0)

    def test_invalid_mood_fallback(self):
        s = InternalState(mood="not_a_mood")
        self.assertEqual(s.mood, DEFAULT_MOOD)

    def test_energy_clipping(self):
        s = InternalState(energy_level=2.0)
        self.assertEqual(s.energy_level, 1.0)
        s2 = InternalState(energy_level=-1.0)
        self.assertEqual(s2.energy_level, 0.0)
        s3 = InternalState(energy_level=float("nan"))
        self.assertEqual(s3.energy_level, DEFAULT_ENERGY)

    def test_curiosity_clipping(self):
        s = InternalState(curiosity_drive=5.0)
        self.assertEqual(s.curiosity_drive, 1.0)

    def test_social_clipping(self):
        s = InternalState(social_disposition=-0.5)
        self.assertEqual(s.social_disposition, 0.0)

    def test_attention_focus_clipping(self):
        long = "x" * 500
        s = InternalState(attention_focus=long)
        self.assertEqual(len(s.attention_focus), 128)

    def test_attention_focus_none(self):
        s = InternalState(attention_focus=None)  # type: ignore[arg-type]
        self.assertEqual(s.attention_focus, "")

    def test_stimulus_event_id_clipping(self):
        s = InternalState(last_stimulus_event_id="x" * 500)
        self.assertEqual(len(s.last_stimulus_event_id), 128)

    def test_metadata_truncation(self):
        meta = {f"k{i}": i for i in range(50)}
        s = InternalState(metadata=meta)
        self.assertEqual(len(s.metadata), 16)

    def test_metadata_invalid_type(self):
        s = InternalState(metadata="not a dict")  # type: ignore[arg-type]
        self.assertEqual(s.metadata, {})


class TestInternalStateValidation(unittest.TestCase):
    def test_is_valid_default(self):
        s = InternalState()
        self.assertTrue(s.is_valid())

    def test_is_valid_bad_mood(self):
        s = InternalState()
        s.mood = "bogus"
        self.assertFalse(s.is_valid())

    def test_is_low_energy_default_threshold(self):
        s = InternalState(energy_level=0.1)
        self.assertTrue(s.is_low_energy())
        s2 = InternalState(energy_level=0.5)
        self.assertFalse(s2.is_low_energy())

    def test_is_low_energy_custom_threshold(self):
        s = InternalState(energy_level=0.5)
        self.assertTrue(s.is_low_energy(threshold=0.7))
        self.assertFalse(s.is_low_energy(threshold=0.2))

    def test_is_low_energy_invalid_threshold(self):
        s = InternalState(energy_level=0.5)
        self.assertFalse(s.is_low_energy(threshold="bad"))  # type: ignore[arg-type]
        self.assertTrue(s.is_low_energy(threshold=2.0))
        self.assertFalse(s.is_low_energy(threshold=-1.0))


class TestInternalStateSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        s = InternalState(
            mood=INTERNAL_MOOD_CURIOUS,
            energy_level=0.8,
            curiosity_drive=0.9,
            attention_focus="AI绘画",
            social_disposition=0.7,
            last_stimulus_event_id="ev_1",
            last_updated_at=123.0,
            created_at=120.0,
            metadata={"k": 1},
        )
        d = s.to_dict()
        s2 = InternalState.from_dict(d)
        self.assertEqual(s2.mood, s.mood)
        self.assertEqual(s2.energy_level, s.energy_level)
        self.assertEqual(s2.curiosity_drive, s.curiosity_drive)
        self.assertEqual(s2.attention_focus, s.attention_focus)
        self.assertEqual(s2.social_disposition, s.social_disposition)
        self.assertEqual(s2.last_stimulus_event_id, s.last_stimulus_event_id)
        self.assertEqual(s2.metadata, s.metadata)

    def test_from_dict_invalid(self):
        s = InternalState.from_dict("not a dict")  # type: ignore[arg-type]
        self.assertEqual(s.mood, DEFAULT_MOOD)

    def test_from_dict_garbage_values(self):
        s = InternalState.from_dict({
            "energy_level": "bad",
            "curiosity_drive": "bad",
            "mood": "bogus",
        })
        self.assertEqual(s.energy_level, DEFAULT_ENERGY)
        self.assertEqual(s.curiosity_drive, DEFAULT_CURIOSITY)
        self.assertEqual(s.mood, DEFAULT_MOOD)

    def test_summary(self):
        s = InternalState(mood=INTERNAL_MOOD_TIRED, energy_level=0.2)
        sm = s.summary()
        self.assertEqual(sm["mood"], INTERNAL_MOOD_TIRED)
        self.assertEqual(sm["energy_level"], 0.2)
        self.assertIn("state_id", sm)

    def test_repr(self):
        s = InternalState()
        r = repr(s)
        self.assertIn("InternalState", r)
        self.assertIn("ist_", r)


class TestInternalStateStore(unittest.TestCase):
    def test_default_creation(self):
        clk = FrozenClock(initial=100.0)
        store = InternalStateStore(clock=clk)
        s = store.get()
        self.assertIsInstance(s, InternalState)
        self.assertEqual(s.last_updated_at, 100.0)
        self.assertEqual(s.created_at, 100.0)

    def test_with_initial(self):
        init = InternalState(mood=INTERNAL_MOOD_CURIOUS)
        store = InternalStateStore(initial=init)
        s = store.get()
        self.assertEqual(s.mood, INTERNAL_MOOD_CURIOUS)

    def test_update_partial(self):
        clk = FrozenClock(initial=200.0)
        store = InternalStateStore(clock=clk)
        out = store.update(mood=INTERNAL_MOOD_CURIOUS, energy_level=0.9, attention_focus="AI绘画")
        self.assertEqual(out.mood, INTERNAL_MOOD_CURIOUS)
        self.assertEqual(out.energy_level, 0.9)
        self.assertEqual(out.attention_focus, "AI绘画")
        self.assertEqual(out.last_updated_at, 200.0)

    def test_update_keeps_unchanged(self):
        clk = FrozenClock(initial=200.0)
        store = InternalStateStore(clock=clk)
        store.update(mood=INTERNAL_MOOD_CURIOUS, energy_level=0.9)
        out = store.update(attention_focus="AI绘画")
        self.assertEqual(out.mood, INTERNAL_MOOD_CURIOUS)
        self.assertEqual(out.energy_level, 0.9)

    def test_replace(self):
        store = InternalStateStore()
        new = InternalState(mood=INTERNAL_MOOD_TIRED)
        ok = store.replace(new)
        self.assertTrue(ok)
        self.assertEqual(store.get().mood, INTERNAL_MOOD_TIRED)

    def test_replace_invalid(self):
        store = InternalStateStore()
        ok = store.replace("bad")  # type: ignore[arg-type]
        self.assertFalse(ok)

    def test_reset(self):
        clk = FrozenClock(initial=100.0)
        store = InternalStateStore(clock=clk)
        store.update(mood=INTERNAL_MOOD_CURIOUS)
        store.reset()
        s = store.get()
        self.assertEqual(s.mood, DEFAULT_MOOD)

    def test_history_capped(self):
        store = InternalStateStore()
        for i in range(50):
            store.update(energy_level=0.1 + (i % 9) * 0.1)
        h = store.history()
        self.assertLessEqual(len(h), 32)

    def test_history_with_limit(self):
        store = InternalStateStore()
        for i in range(10):
            store.update(energy_level=0.1 + (i % 9) * 0.1)
        h = store.history(limit=3)
        self.assertEqual(len(h), 3)
        h2 = store.history(limit=0)
        self.assertEqual(h2, [])

    def test_update_count(self):
        store = InternalStateStore()
        store.update(mood=INTERNAL_MOOD_CURIOUS)
        store.update(energy_level=0.5)
        self.assertEqual(store.update_count, 2)

    def test_describe(self):
        store = InternalStateStore(name="test_store")
        d = store.describe()
        self.assertEqual(d["name"], "test_store")
        self.assertIn("state", d)
        self.assertEqual(d["state"]["mood"], DEFAULT_MOOD)


class TestInternalStateStoreThreadSafety(unittest.TestCase):
    def test_concurrent_update(self):
        import threading
        store = InternalStateStore()

        def worker():
            for _ in range(50):
                store.update(energy_level=0.5)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(store.update_count, 200)


class TestFactory(unittest.TestCase):
    def test_build_default(self):
        store = build_default_internal_state_store(name="x")
        self.assertEqual(store.name, "x")


class TestConstants(unittest.TestCase):
    def test_all_moods_unique(self):
        self.assertEqual(len(ALL_INTERNAL_MOODS), len(set(ALL_INTERNAL_MOODS)))


if __name__ == "__main__":
    unittest.main()
