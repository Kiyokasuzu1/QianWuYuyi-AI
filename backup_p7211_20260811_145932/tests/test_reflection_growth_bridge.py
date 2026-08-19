"""
Phase 3.5.13: Reflection → Growth Integration Layer 测试

覆盖：
- 初始化
- evaluation → proposal flow
- rejected proposal flow
- 重复桥接复用
- 审计历史 / 快照
- RuntimeCore 集成
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.contracts.experience_schema import ActionResult, ReflectionInsight, RuntimeExperience
from src.runtime.adapters.growth_adapter import GrowthAdapter
from src.runtime.reflection_evaluator import ReflectionEvaluator
from src.runtime.reflection_growth_bridge import (
    ReflectionGrowthBridge,
    ReflectionGrowthBridgeConfig,
)


class TestReflectionGrowthBridge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proposals_path = os.path.join(self.tmp.name, "bridge_proposals.json")
        self.history_path = os.path.join(self.tmp.name, "bridge_history.json")
        self.adapter = GrowthAdapter(proposals_path=self.proposals_path)
        self.evaluator = ReflectionEvaluator()
        self.bridge = ReflectionGrowthBridge(
            self.adapter,
            config=ReflectionGrowthBridgeConfig(),
            history_path=self.history_path,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _make_insight(self, **kwargs) -> ReflectionInsight:
        defaults = {
            "insight_id": "ins_bridge_001",
            "insight_type": "improvement",
            "summary": "高频主动行为需要调节",
            "details": "多次高频主动联系后响应率下降",
            "pattern_detected": "high_frequency_proactive",
            "pattern_frequency": 4,
            "confidence": 0.88,
            "experience_ids": ["e1", "e2", "e3", "e4"],
            "suggested_adjustments": ["降低主动频率", "观察响应后再继续"],
        }
        defaults.update(kwargs)
        return ReflectionInsight(**defaults)

    def test_01_initialization(self):
        self.assertIsNotNone(self.bridge)
        self.assertEqual(self.bridge.get_history(), [])
        snap = self.bridge.get_snapshot()
        self.assertTrue(snap.enabled)
        self.assertEqual(snap.total_records, 0)

    def test_02_evaluation_to_proposal_flow(self):
        insight = self._make_insight()
        evaluation = self.evaluator.evaluate_insight(insight)

        record = self.bridge.process(
            insight,
            evaluation,
            identity_status={"identity_ok": True, "reason": "identity checks passed"},
        )

        self.assertEqual(record.decision, "proposal_created")
        self.assertEqual(len(record.proposal_ids), 1)

        proposals = self.adapter.list_proposals(limit=10)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].status, "proposed")
        self.assertEqual(
            proposals[0].evaluator_meta.get("reflection_evaluation_id"),
            evaluation.evaluation_id,
        )
        self.assertEqual(
            proposals[0].evaluator_meta.get("bridge_source"),
            "reflection_growth_bridge",
        )

    def test_03_rejected_proposal_flow_by_evaluation(self):
        insight = self._make_insight(
            insight_id="ins_bridge_reject",
            insight_type="pattern",
            confidence=0.05,
            pattern_frequency=0,
            experience_ids=[],
            suggested_adjustments=[],
        )
        evaluation = self.evaluator.evaluate_insight(insight)

        record = self.bridge.process(
            insight,
            evaluation,
            identity_status={"identity_ok": True, "reason": "identity checks passed"},
        )

        self.assertEqual(record.decision, "rejected")
        self.assertEqual(record.proposal_ids, [])
        self.assertEqual(self.adapter.list_proposals(limit=10), [])

    def test_04_rejected_proposal_flow_by_identity(self):
        insight = self._make_insight(insight_id="ins_bridge_identity")
        evaluation = self.evaluator.evaluate_insight(insight)

        record = self.bridge.process(
            insight,
            evaluation,
            identity_status={
                "continuity_report": {"is_continuous": False, "continuity_score": 0.4},
                "identity_ok": False,
                "reason": "continuity check blocked proposal generation",
            },
        )

        self.assertEqual(record.decision, "rejected")
        self.assertIn("continuity", record.decision_reason)
        self.assertEqual(self.adapter.list_proposals(limit=10), [])

    def test_05_duplicate_bridge_reuses_existing_proposal(self):
        insight = self._make_insight(insight_id="ins_bridge_reuse")
        evaluation = self.evaluator.evaluate_insight(insight)

        first = self.bridge.process(insight, evaluation, identity_status={"identity_ok": True})
        second = self.bridge.process(insight, evaluation, identity_status={"identity_ok": True})

        self.assertEqual(first.decision, "proposal_created")
        self.assertEqual(second.decision, "reused")
        self.assertEqual(len(self.adapter.list_proposals(limit=10)), 1)

    def test_06_audit_history_and_snapshot(self):
        accept_insight = self._make_insight(insight_id="ins_audit_accept")
        reject_insight = self._make_insight(
            insight_id="ins_audit_reject",
            insight_type="pattern",
            confidence=0.1,
            pattern_frequency=0,
            experience_ids=[],
            suggested_adjustments=[],
        )

        self.bridge.process(
            accept_insight,
            self.evaluator.evaluate_insight(accept_insight),
            identity_status={"identity_ok": True},
        )
        self.bridge.process(
            reject_insight,
            self.evaluator.evaluate_insight(reject_insight),
            identity_status={"identity_ok": True},
        )

        history = self.bridge.get_history(limit=10)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].insight_id, "ins_audit_reject")
        self.assertEqual(history[1].insight_id, "ins_audit_accept")

        snap = self.bridge.get_snapshot()
        self.assertEqual(snap.total_records, 2)
        self.assertEqual(snap.total_created, 1)
        self.assertEqual(snap.total_rejected, 1)

    def test_07_invalid_input_boundary(self):
        insight = self._make_insight(insight_id="ins_invalid_boundary")
        record = self.bridge.process(insight, None, identity_status={"identity_ok": True})
        self.assertEqual(record.decision, "rejected")
        self.assertIn("missing reflection evaluation", record.decision_reason)


class TestRuntimeCoreReflectionGrowthBridge(unittest.TestCase):
    def test_01_runtime_reflection_to_growth_integration(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore

            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
            }
            rc = RuntimeCore(config=config)

            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"exp_rgb_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hello_{i}"}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(
                        action_id=f"act_rgb_{i}",
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

            bridge_history = rc.get_reflection_growth_history(limit=10)
            self.assertEqual(len(bridge_history), 1)
            self.assertEqual(bridge_history[0]["decision"], "proposal_created")

            latest_eval = rc.get_latest_evaluation()
            self.assertIsNotNone(latest_eval)
            self.assertEqual(latest_eval["source_type"], "reflection_insight")

            snapshot = rc.get_reflection_growth_snapshot()
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot["total_created"], 1)


if __name__ == "__main__":
    unittest.main()
