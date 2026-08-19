# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_action_filter.py

Phase 5.0-D3-C: ActionFilter 单元测试。
"""
import unittest
from src.runtime.initiative.action_filter import (
    ACTION_FILTER_SCHEMA_VERSION,
    DEFAULT_ENERGY_THRESHOLD,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_EXPECTED_VALUE,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_REPEAT_WINDOW_SECONDS,
    ActionFilter,
    FilterDecision,
    build_default_action_filter,
)
from src.runtime.initiative.internal_state import (
    INTERNAL_MOOD_CURIOUS,
    INTERNAL_MOOD_TIRED,
    InternalState,
)
from src.runtime.initiative.possible_action import (
    ACTION_EFFORT_HIGH,
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_OBSERVE,
    PossibleAction,
)


def _act(
    topic: str = "AI绘画",
    *,
    confidence: float = 0.6,
    expected_value: float = 0.6,
    effort_estimate: str = ACTION_EFFORT_MEDIUM,
    priority: float = 0.5,
    supporting_signal_ids=None,
    status: str = ACTION_STATUS_PENDING,
) -> PossibleAction:
    if supporting_signal_ids is None:
        sigs = ["sig_1"]
    else:
        sigs = list(supporting_signal_ids)
    return PossibleAction(
        action_type=ACTION_TYPE_LEARN,
        topic=topic,
        rationale="r",
        supporting_signal_ids=sigs,
        urgency="normal",
        effort_estimate=effort_estimate,
        expected_value=expected_value,
        confidence=confidence,
        status=status,
        priority=priority,
    )


class TestFilterDecision(unittest.TestCase):
    def test_default(self):
        d = FilterDecision()
        self.assertEqual(d.action_id, "")
        self.assertTrue(d.passed)
        self.assertEqual(d.new_status, ACTION_STATUS_PENDING)
        self.assertEqual(d.reason, "")
        self.assertEqual(d.priority, 0.5)
        self.assertEqual(d.rule_applied, "none")


class TestActionFilterDefaults(unittest.TestCase):
    def test_default_creation(self):
        f = ActionFilter()
        self.assertEqual(f.name, "action_filter")
        self.assertEqual(f.min_confidence, DEFAULT_MIN_CONFIDENCE)
        self.assertEqual(f.min_expected_value, DEFAULT_MIN_EXPECTED_VALUE)
        self.assertEqual(f.repeat_window_seconds, DEFAULT_REPEAT_WINDOW_SECONDS)
        self.assertEqual(f.energy_threshold, DEFAULT_ENERGY_THRESHOLD)
        self.assertEqual(f.repeat_penalty, DEFAULT_REPEAT_PENALTY)
        self.assertEqual(f.total_evaluated, 0)

    def test_custom_creation(self):
        f = ActionFilter(
            min_confidence=0.5,
            min_expected_value=0.4,
            repeat_window_seconds=10.0,
            energy_threshold=0.3,
            repeat_penalty=0.6,
            name="custom",
        )
        self.assertEqual(f.name, "custom")
        self.assertEqual(f.min_confidence, 0.5)
        self.assertEqual(f.min_expected_value, 0.4)
        self.assertEqual(f.repeat_window_seconds, 10.0)
        self.assertEqual(f.energy_threshold, 0.3)
        self.assertEqual(f.repeat_penalty, 0.6)

    def test_invalid_thresholds_clipped(self):
        f = ActionFilter(min_confidence=2.0, min_expected_value=-1.0, energy_threshold=5.0)
        self.assertEqual(f.min_confidence, 1.0)
        self.assertEqual(f.min_expected_value, 0.0)
        self.assertEqual(f.energy_threshold, 1.0)

    def test_repeat_window_clamped(self):
        f = ActionFilter(repeat_window_seconds=-5.0)
        self.assertEqual(f.repeat_window_seconds, 0.0)

    def test_repeat_penalty_clamped(self):
        f = ActionFilter(repeat_penalty=2.0)
        self.assertEqual(f.repeat_penalty, 1.0)
        f2 = ActionFilter(repeat_penalty=-0.5)
        self.assertEqual(f2.repeat_penalty, 0.0)


class TestActionFilterRules(unittest.TestCase):
    def test_low_confidence_discarded(self):
        f = ActionFilter()
        a = _act(confidence=0.1)
        d = f.evaluate(a)
        self.assertEqual(d.new_status, ACTION_STATUS_DISCARDED)
        self.assertEqual(d.rule_applied, "low_confidence")
        self.assertFalse(d.passed)
        self.assertEqual(f.total_discarded, 1)

    def test_low_value_deferred(self):
        f = ActionFilter()
        a = _act(expected_value=0.1)
        d = f.evaluate(a)
        self.assertEqual(d.new_status, ACTION_STATUS_DEFERRED)
        self.assertEqual(d.rule_applied, "low_value")
        self.assertFalse(d.passed)
        self.assertEqual(f.total_deferred, 1)

    def test_repeat_penalty(self):
        f = ActionFilter(repeat_window_seconds=100.0, repeat_penalty=0.5)
        a = _act(topic="AI绘画", priority=0.8)
        # 第一次:passed
        d1 = f.evaluate(a, now=10.0)
        self.assertTrue(d1.passed)
        # 第二次(同 topic):repeat_penalty
        d2 = f.evaluate(a, now=20.0)
        self.assertEqual(d2.rule_applied, "repeat")
        self.assertEqual(d2.priority, 0.4)  # 0.8 * (1 - 0.5)
        self.assertEqual(f.rule_hits["repeat"], 1)

    def test_no_repeat_outside_window(self):
        f = ActionFilter(repeat_window_seconds=10.0, repeat_penalty=0.5)
        a = _act(topic="A", priority=0.8)
        f.evaluate(a, now=10.0)
        # 100s 后不在窗口
        d2 = f.evaluate(a, now=100.0)
        self.assertEqual(d2.rule_applied, "none")
        self.assertEqual(d2.priority, 0.8)

    def test_repeat_no_time(self):
        f = ActionFilter(repeat_window_seconds=10.0)
        a = _act(topic="A")
        d = f.evaluate(a, now=0.0)
        self.assertEqual(d.rule_applied, "none")

    def test_energy_too_low_high_effort(self):
        f = ActionFilter(energy_threshold=0.3)
        state = InternalState(energy_level=0.1)
        a = _act(effort_estimate=ACTION_EFFORT_HIGH)
        d = f.evaluate(a, state=state, now=10.0)
        self.assertEqual(d.new_status, ACTION_STATUS_FILTERED)
        self.assertEqual(d.rule_applied, "energy")
        self.assertEqual(f.total_filtered, 1)

    def test_energy_low_low_effort_kept(self):
        f = ActionFilter(energy_threshold=0.3)
        state = InternalState(energy_level=0.1)
        a = _act(effort_estimate=ACTION_EFFORT_LOW, confidence=0.5, expected_value=0.5)
        d = f.evaluate(a, state=state, now=10.0)
        self.assertEqual(d.new_status, ACTION_STATUS_PENDING)
        self.assertEqual(f.total_passed, 1)

    def test_no_state_no_energy_rule(self):
        f = ActionFilter(energy_threshold=0.3)
        a = _act(effort_estimate=ACTION_EFFORT_HIGH, confidence=0.5, expected_value=0.5)
        d = f.evaluate(a, state=None, now=10.0)
        self.assertEqual(d.new_status, ACTION_STATUS_PENDING)

    def test_invalid_action(self):
        f = ActionFilter()
        d = f.evaluate("not an action")  # type: ignore[arg-type]
        self.assertEqual(d.new_status, ACTION_STATUS_DISCARDED)
        self.assertEqual(d.action_id, "")

    def test_all_passed(self):
        f = ActionFilter()
        a = _act(confidence=0.6, expected_value=0.6)
        d = f.evaluate(a, now=10.0)
        self.assertEqual(d.new_status, ACTION_STATUS_PENDING)
        self.assertTrue(d.passed)
        self.assertEqual(f.total_passed, 1)


class TestActionFilterApply(unittest.TestCase):
    def test_apply_changes_status(self):
        f = ActionFilter()
        a = _act(confidence=0.1)
        d = f.evaluate(a, now=10.0)
        f.apply(a, d, now=10.0)
        self.assertEqual(a.status, ACTION_STATUS_DISCARDED)

    def test_apply_invalid_action(self):
        f = ActionFilter()
        a = "bad"  # type: ignore[arg-type]
        d = FilterDecision()
        f.apply(a, d)  # type: ignore[arg-type]
        # 不抛错

    def test_apply_invalid_decision(self):
        f = ActionFilter()
        a = _act()
        f.apply(a, "bad")  # type: ignore[arg-type]
        self.assertEqual(a.status, ACTION_STATUS_PENDING)

    def test_apply_no_time(self):
        f = ActionFilter()
        a = _act(confidence=0.1)
        d = f.evaluate(a)
        f.apply(a, d, now=0.0)
        self.assertEqual(a.status, ACTION_STATUS_DISCARDED)

    def test_history_snapshot(self):
        f = ActionFilter()
        a = _act(topic="A")
        f.evaluate(a, now=10.0)
        h = f.history_snapshot()
        self.assertEqual(h.get("A"), 10.0)


class TestActionFilterBatch(unittest.TestCase):
    def test_filter_batch(self):
        f = ActionFilter()
        actions = [_act(topic=t, confidence=c) for t, c in [("A", 0.5), ("B", 0.1), ("C", 0.7)]]
        decisions = f.filter_batch(actions, now=10.0)
        self.assertEqual(len(decisions), 3)
        self.assertEqual(decisions[0].new_status, ACTION_STATUS_PENDING)
        self.assertEqual(decisions[1].new_status, ACTION_STATUS_DISCARDED)
        self.assertEqual(decisions[2].new_status, ACTION_STATUS_PENDING)

    def test_filter_batch_none(self):
        f = ActionFilter()
        d = f.filter_batch(None)  # type: ignore[arg-type]
        self.assertEqual(d, [])


class TestActionFilterMaintenance(unittest.TestCase):
    def test_clear_history(self):
        f = ActionFilter()
        a = _act(topic="A")
        f.evaluate(a, now=10.0)
        n = f.clear_history()
        self.assertEqual(n, 1)
        self.assertEqual(f.history_snapshot(), {})

    def test_reset_stats(self):
        f = ActionFilter()
        a = _act()
        f.evaluate(a, now=10.0)
        f.reset_stats()
        self.assertEqual(f.total_evaluated, 0)
        self.assertEqual(f.total_passed, 0)

    def test_describe(self):
        f = ActionFilter()
        a = _act()
        f.evaluate(a, now=10.0)
        d = f.describe()
        self.assertIn("name", d)
        self.assertEqual(d["total_evaluated"], 1)


class TestActionFilterThreadSafety(unittest.TestCase):
    def test_concurrent_evaluate(self):
        import threading
        f = ActionFilter()
        def worker():
            for _ in range(50):
                f.evaluate(_act(), now=10.0)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(f.total_evaluated, 200)


class TestFactory(unittest.TestCase):
    def test_build_default(self):
        f = build_default_action_filter()
        self.assertIsInstance(f, ActionFilter)


if __name__ == "__main__":
    unittest.main()
