# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_c/test_possible_action.py

Phase 5.0-D3-C: PossibleAction 单元测试。
"""
import unittest
from src.runtime.initiative.possible_action import (
    ACTION_EFFORT_HIGH,
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_TYPE_ASK,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_OBSERVE,
    ACTION_TYPE_RECOMMEND,
    ACTION_TYPE_REMIND,
    ACTION_URGENCY_HIGH,
    ACTION_URGENCY_LOW,
    ACTION_URGENCY_NORMAL,
    ALL_ACTION_EFFORTS,
    ALL_ACTION_STATUSES,
    ALL_ACTION_TYPES,
    ALL_ACTION_URGENCIES,
    POSSIBLE_ACTION_SCHEMA_VERSION,
    VALID_TRANSITIONS,
    PossibleAction,
    build_pending_observation,
    is_valid_transition,
)


def _act(
    topic: str = "AI绘画",
    *,
    action_type: str = ACTION_TYPE_LEARN,
    urgency: str = ACTION_URGENCY_NORMAL,
    effort_estimate: str = ACTION_EFFORT_MEDIUM,
    expected_value: float = 0.6,
    confidence: float = 0.6,
    priority: float = 0.6,
    supporting_signal_ids=None,
    status: str = ACTION_STATUS_PENDING,
    rationale: str = "rationale",
    now: float = 0.0,
) -> PossibleAction:
    if supporting_signal_ids is None:
        sigs = ["sig_1"]
    else:
        sigs = list(supporting_signal_ids)
    return PossibleAction(
        action_type=action_type,
        topic=topic,
        rationale=rationale,
        supporting_signal_ids=sigs,
        urgency=urgency,
        effort_estimate=effort_estimate,
        expected_value=expected_value,
        confidence=confidence,
        status=status,
        priority=priority,
        created_at=now,
        updated_at=now,
    )


class TestPossibleActionCreation(unittest.TestCase):
    def test_default_creation(self):
        a = PossibleAction(topic="AI绘画", supporting_signal_ids=["sig_1"])
        self.assertTrue(a.action_id.startswith("act_"))
        self.assertEqual(a.action_type, ACTION_TYPE_OBSERVE)
        self.assertEqual(a.topic, "AI绘画")
        self.assertEqual(a.status, ACTION_STATUS_PENDING)
        self.assertEqual(a.urgency, ACTION_URGENCY_NORMAL)
        self.assertEqual(a.effort_estimate, ACTION_EFFORT_LOW)
        self.assertEqual(a.expected_value, 0.5)
        self.assertEqual(a.confidence, 0.5)
        self.assertEqual(a.priority, 0.5)
        self.assertEqual(a.version, POSSIBLE_ACTION_SCHEMA_VERSION)

    def test_invalid_action_type_fallback(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], action_type="bogus")
        self.assertEqual(a.action_type, ACTION_TYPE_OBSERVE)

    def test_invalid_status_fallback(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], status="bogus")
        self.assertEqual(a.status, ACTION_STATUS_PENDING)

    def test_invalid_urgency_fallback(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], urgency="bogus")
        self.assertEqual(a.urgency, ACTION_URGENCY_NORMAL)

    def test_invalid_effort_fallback(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], effort_estimate="bogus")
        self.assertEqual(a.effort_estimate, ACTION_EFFORT_LOW)

    def test_topic_clipping(self):
        a = PossibleAction(topic="x" * 500, supporting_signal_ids=["s"])
        self.assertEqual(len(a.topic), 128)

    def test_rationale_clipping(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], rationale="r" * 1000)
        self.assertEqual(len(a.rationale), 512)

    def test_expected_value_clipping(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], expected_value=2.0)
        self.assertEqual(a.expected_value, 1.0)
        a2 = PossibleAction(topic="x", supporting_signal_ids=["s"], expected_value=-1.0)
        self.assertEqual(a2.expected_value, 0.0)
        a3 = PossibleAction(topic="x", supporting_signal_ids=["s"], expected_value=float("nan"))
        self.assertEqual(a3.expected_value, 0.5)

    def test_confidence_clipping(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], confidence=5.0)
        self.assertEqual(a.confidence, 1.0)

    def test_priority_clipping(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], priority=5.0)
        self.assertEqual(a.priority, 1.0)

    def test_supporting_signal_ids_dedup(self):
        a = PossibleAction(topic="x", supporting_signal_ids=["s1", "s1", "s2"])
        self.assertEqual(a.supporting_signal_ids, ["s1", "s2"])

    def test_supporting_signal_ids_truncation(self):
        sigs = [f"s{i}" for i in range(100)]
        a = PossibleAction(topic="x", supporting_signal_ids=sigs)
        self.assertLessEqual(len(a.supporting_signal_ids), 64)

    def test_metadata_truncation(self):
        meta = {f"k{i}": i for i in range(30)}
        a = PossibleAction(topic="x", supporting_signal_ids=["s"], metadata=meta)
        self.assertEqual(len(a.metadata), 16)

    def test_invalid_supporting_signal_type(self):
        a = PossibleAction(topic="x", supporting_signal_ids="not a list")  # type: ignore[arg-type]
        self.assertEqual(a.supporting_signal_ids, [])


class TestPossibleActionValidation(unittest.TestCase):
    def test_is_valid_default(self):
        a = _act()
        self.assertTrue(a.is_valid())

    def test_is_valid_no_topic(self):
        a = _act(topic="")
        self.assertFalse(a.is_valid())

    def test_is_valid_no_evidence(self):
        a = _act(supporting_signal_ids=[])
        self.assertFalse(a.is_valid())

    def test_is_valid_invalid_expected_value(self):
        a = _act(expected_value=2.0)
        # post_init 已 clip,所以这里 2.0 变成 1.0,is_valid 仍为 True
        # 改用 is_valid() 的逻辑:直接篡改内部值测试
        a.expected_value = 1.5
        self.assertFalse(a.is_valid())

    def test_is_valid_invalid_confidence(self):
        a = _act(confidence=-1.0)
        a.confidence = -0.5
        self.assertFalse(a.is_valid())


class TestPossibleActionStateTransitions(unittest.TestCase):
    def test_mark_filtered(self):
        a = _act()
        a.mark_filtered(now=10.0)
        self.assertEqual(a.status, ACTION_STATUS_FILTERED)
        self.assertEqual(a.updated_at, 10.0)

    def test_mark_deferred(self):
        a = _act()
        a.mark_deferred(now=20.0)
        self.assertEqual(a.status, ACTION_STATUS_DEFERRED)
        self.assertEqual(a.updated_at, 20.0)

    def test_mark_discarded(self):
        a = _act()
        a.mark_discarded(now=30.0)
        self.assertEqual(a.status, ACTION_STATUS_DISCARDED)
        self.assertEqual(a.updated_at, 30.0)

    def test_mark_pending_back(self):
        a = _act()
        a.mark_filtered()
        a.mark_pending(now=40.0)
        self.assertEqual(a.status, ACTION_STATUS_PENDING)
        self.assertEqual(a.updated_at, 40.0)

    def test_state_helpers(self):
        a = _act()
        self.assertTrue(a.is_pending())
        self.assertFalse(a.is_filtered())
        self.assertFalse(a.is_deferred())
        self.assertFalse(a.is_discarded())
        a.mark_filtered()
        self.assertTrue(a.is_filtered())
        a.mark_deferred()
        self.assertTrue(a.is_deferred())
        a.mark_discarded()
        self.assertTrue(a.is_discarded())

    def test_is_actionable(self):
        a = _act()
        self.assertTrue(a.is_actionable())
        a.mark_filtered()
        self.assertFalse(a.is_actionable())

    def test_update_priority(self):
        a = _act()
        a.update_priority(0.9, now=50.0)
        self.assertEqual(a.priority, 0.9)
        self.assertEqual(a.updated_at, 50.0)

    def test_update_priority_invalid(self):
        a = _act(priority=0.3)
        a.update_priority("bad")  # type: ignore[arg-type]
        self.assertEqual(a.priority, 0.3)

    def test_update_priority_clipping(self):
        a = _act()
        a.update_priority(2.0)
        self.assertEqual(a.priority, 1.0)

    def test_mark_no_time(self):
        a = _act()
        a.mark_filtered()
        self.assertEqual(a.status, ACTION_STATUS_FILTERED)
        # updated_at 是构造时的 0.0
        self.assertEqual(a.updated_at, 0.0)


class TestPossibleActionSerialization(unittest.TestCase):
    def test_to_dict_roundtrip(self):
        a = _act(
            topic="music",
            action_type=ACTION_TYPE_LEARN,
            urgency=ACTION_URGENCY_HIGH,
            effort_estimate=ACTION_EFFORT_HIGH,
            expected_value=0.8,
            confidence=0.7,
            priority=0.85,
            rationale="r",
            now=100.0,
        )
        d = a.to_dict()
        a2 = PossibleAction.from_dict(d)
        self.assertEqual(a2.action_type, a.action_type)
        self.assertEqual(a2.topic, a.topic)
        self.assertEqual(a2.urgency, a.urgency)
        self.assertEqual(a2.effort_estimate, a.effort_estimate)
        self.assertEqual(a2.expected_value, a.expected_value)
        self.assertEqual(a2.confidence, a.confidence)
        self.assertEqual(a2.priority, a.priority)
        self.assertEqual(a2.supporting_signal_ids, a.supporting_signal_ids)
        self.assertEqual(a2.rationale, a.rationale)

    def test_from_dict_invalid(self):
        a = PossibleAction.from_dict("not a dict")  # type: ignore[arg-type]
        self.assertEqual(a.topic, "invalid")

    def test_from_dict_garbage(self):
        a = PossibleAction.from_dict({
            "expected_value": "bad",
            "confidence": "bad",
            "priority": "bad",
            "supporting_signal_ids": "bad",
            "urgency": "bad",
            "action_type": "bad",
            "status": "bad",
        })
        self.assertEqual(a.expected_value, 0.5)
        self.assertEqual(a.confidence, 0.5)
        self.assertEqual(a.priority, 0.5)
        self.assertEqual(a.supporting_signal_ids, [])
        self.assertEqual(a.urgency, ACTION_URGENCY_NORMAL)
        self.assertEqual(a.action_type, ACTION_TYPE_OBSERVE)
        self.assertEqual(a.status, ACTION_STATUS_PENDING)

    def test_summary(self):
        a = _act()
        sm = a.summary()
        self.assertEqual(sm["action_type"], ACTION_TYPE_LEARN)
        self.assertEqual(sm["topic"], "AI绘画")
        self.assertEqual(sm["status"], ACTION_STATUS_PENDING)
        self.assertEqual(sm["supporting_signal_count"], 1)


class TestValidTransitions(unittest.TestCase):
    def test_pending_to_filtered(self):
        self.assertTrue(is_valid_transition(ACTION_STATUS_PENDING, ACTION_STATUS_FILTERED))

    def test_pending_to_discarded(self):
        self.assertTrue(is_valid_transition(ACTION_STATUS_PENDING, ACTION_STATUS_DISCARDED))

    def test_pending_to_pending(self):
        self.assertTrue(is_valid_transition(ACTION_STATUS_PENDING, ACTION_STATUS_PENDING))

    def test_filtered_to_pending(self):
        self.assertTrue(is_valid_transition(ACTION_STATUS_FILTERED, ACTION_STATUS_PENDING))

    def test_discarded_to_pending_invalid(self):
        self.assertFalse(is_valid_transition(ACTION_STATUS_DISCARDED, ACTION_STATUS_PENDING))

    def test_invalid_from_status(self):
        self.assertFalse(is_valid_transition("bogus", ACTION_STATUS_PENDING))

    def test_all_transitions(self):
        # 验证 VALID_TRANSITIONS 中的所有目标都在 ALL_ACTION_STATUSES
        for from_st, to_set in VALID_TRANSITIONS.items():
            self.assertIn(from_st, ALL_ACTION_STATUSES)
            for to_st in to_set:
                self.assertIn(to_st, ALL_ACTION_STATUSES)


class TestFactory(unittest.TestCase):
    def test_build_pending_observation(self):
        a = build_pending_observation(
            topic="AI绘画",
            supporting_signal_ids=["sig_1"],
            rationale="r",
            confidence=0.7,
            expected_value=0.8,
            now=100.0,
        )
        self.assertEqual(a.action_type, ACTION_TYPE_OBSERVE)
        self.assertEqual(a.topic, "AI绘画")
        self.assertEqual(a.status, ACTION_STATUS_PENDING)
        self.assertEqual(a.created_at, 100.0)
        self.assertEqual(a.updated_at, 100.0)
        self.assertEqual(a.effort_estimate, ACTION_EFFORT_LOW)

    def test_build_pending_observation_no_signals(self):
        a = build_pending_observation(
            topic="x",
            supporting_signal_ids=[],
        )
        self.assertEqual(a.supporting_signal_ids, [])


class TestConstants(unittest.TestCase):
    def test_action_types_unique(self):
        self.assertEqual(len(ALL_ACTION_TYPES), len(set(ALL_ACTION_TYPES)))

    def test_statuses_unique(self):
        self.assertEqual(len(ALL_ACTION_STATUSES), len(set(ALL_ACTION_STATUSES)))

    def test_urgencies_unique(self):
        self.assertEqual(len(ALL_ACTION_URGENCIES), len(set(ALL_ACTION_URGENCIES)))

    def test_efforts_unique(self):
        self.assertEqual(len(ALL_ACTION_EFFORTS), len(set(ALL_ACTION_EFFORTS)))


if __name__ == "__main__":
    unittest.main()
