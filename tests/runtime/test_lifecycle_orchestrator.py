"""
Phase 3.5.14: Runtime Lifecycle Orchestrator 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.lifecycle_schema import (
    STAGE_EXPERIENCE_RECEIVED,
    STAGE_MEMORY_CREATED,
    STAGE_REFLECTION_STARTED,
    STAGE_EVALUATION_COMPLETED,
    STAGE_PROPOSAL_CREATED,
    STAGE_PROPOSAL_APPLIED,
)
from src.runtime.lifecycle_orchestrator import RuntimeLifecycleOrchestrator


class TestRuntimeLifecycleOrchestrator(unittest.TestCase):
    def setUp(self):
        self.orchestrator = RuntimeLifecycleOrchestrator()

    def test_initialization(self):
        state = self.orchestrator.get_state()
        self.assertEqual(state.current_state, "idle")
        self.assertEqual(state.transition_count, 0)
        self.assertEqual(state.invalid_transition_count, 0)

    def test_normal_flow(self):
        self.orchestrator.record_event(STAGE_EXPERIENCE_RECEIVED, source_id="exp_1")
        self.orchestrator.record_event(STAGE_MEMORY_CREATED, source_id="exp_1")
        self.orchestrator.record_event(STAGE_REFLECTION_STARTED, details={"insight_id": "ins_1"})
        self.orchestrator.record_event(STAGE_EVALUATION_COMPLETED, source_id="rev_1")
        self.orchestrator.record_event(STAGE_PROPOSAL_CREATED, source_id="prop_1", details={"proposal_ids": ["prop_1"]})
        self.orchestrator.record_event(STAGE_PROPOSAL_APPLIED, source_id="prop_1")

        state = self.orchestrator.get_state()
        self.assertEqual(state.current_state, STAGE_PROPOSAL_APPLIED)
        self.assertEqual(state.transition_count, 6)
        self.assertEqual(state.last_experience_id, "exp_1")
        self.assertEqual(state.last_memory_experience_id, "exp_1")
        self.assertEqual(state.last_evaluation_id, "rev_1")
        self.assertEqual(state.last_proposal_id, "prop_1")
        self.assertEqual(state.last_applied_proposal_id, "prop_1")

    def test_invalid_transition(self):
        event = self.orchestrator.record_event(STAGE_PROPOSAL_CREATED, source_id="prop_bad")
        self.assertFalse(event.success)
        state = self.orchestrator.get_state()
        self.assertEqual(state.current_state, "idle")
        self.assertEqual(state.invalid_transition_count, 1)

    def test_history_order(self):
        self.orchestrator.record_event(STAGE_EXPERIENCE_RECEIVED, source_id="exp_1")
        self.orchestrator.record_event(STAGE_MEMORY_CREATED, source_id="exp_1")
        history = self.orchestrator.get_history(limit=10)
        self.assertEqual(history[0].event_name, STAGE_MEMORY_CREATED)
        self.assertEqual(history[1].event_name, STAGE_EXPERIENCE_RECEIVED)

    def test_clear_history(self):
        self.orchestrator.record_event(STAGE_EXPERIENCE_RECEIVED, source_id="exp_1")
        cleared = self.orchestrator.clear_history()
        self.assertEqual(cleared, 1)
        self.assertEqual(self.orchestrator.get_state().current_state, "idle")


class TestRuntimeLifecycleIntegration(unittest.TestCase):
    def test_runtime_core_emits_standard_lifecycle_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.contracts.experience_schema import RuntimeExperience, ActionResult
            from src.runtime.runtime_core import RuntimeCore

            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
                "tick_interval_seconds": 1,
            })

            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"exp_lc_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hello_{i}"}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(
                        action_id=f"act_lc_{i}",
                        success=True,
                        response_received=(i % 2 == 0),
                    ),
                    self_state_before={"initiative": 0.8},
                    self_state_after={"initiative": 0.7},
                    duration_ms=100.0,
                )
                rc.experience_builder._buffer.append(exp)

            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)

            proposals = rc.get_growth_proposals(status="proposed", limit=10)
            self.assertEqual(len(proposals), 1)

            rc.accept_growth_proposal(proposals[0].id)

            history = rc.get_runtime_lifecycle_history(limit=10)
            names = [item["event_name"] for item in reversed(history)]

            self.assertIn(STAGE_REFLECTION_STARTED, names)
            self.assertIn(STAGE_EVALUATION_COMPLETED, names)
            self.assertIn(STAGE_PROPOSAL_CREATED, names)
            self.assertIn(STAGE_PROPOSAL_APPLIED, names)

            state = rc.get_runtime_lifecycle_state()
            self.assertEqual(state["current_state"], STAGE_PROPOSAL_APPLIED)
            self.assertEqual(state["last_applied_proposal_id"], proposals[0].id)
