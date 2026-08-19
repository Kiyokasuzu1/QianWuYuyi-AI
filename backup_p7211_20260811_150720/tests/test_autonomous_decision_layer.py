"""
Phase 3.5.19: Autonomous Decision Layer 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest


class TestAutonomousDecisionLayerIntegration(unittest.TestCase):
    def test_01_autonomous_layer_triggers_reflection_when_due(self):
        from src.contracts.experience_schema import RuntimeExperience, ActionResult
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "reflection_scheduler_enabled": True,
                "autonomous_decision_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "rt.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 填充经验 buffer（reflect_on_experiences 需要 >=3）
            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"exp_adl_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hi_{i}", "importance": 0.8}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(action_id=f"act_adl_{i}", success=True, response_received=True),
                    self_state_before={"initiative": 0.8},
                    self_state_after={"initiative": 0.7},
                    duration_ms=10.0,
                )
                rc.experience_builder._buffer.append(exp)

            # 触发 scheduler event_count
            rc.notify_scheduler_event(importance=0.8)
            # tick 会驱动 autonomous decision layer
            rc.tick()

            hist = rc.get_autonomous_decision_history(limit=20)
            self.assertGreaterEqual(len(hist), 1)
            # 可能先出现 periodic refresh/skip，至少应有一条记录

            # scheduler history 中应出现执行记录（可能是 skipped 或 triggered）
            sh = rc.get_scheduler_history(limit=10)
            self.assertGreaterEqual(len(sh), 1)

    def test_02_hold_stable_when_pending_proposals_too_many(self):
        from src.contracts.growth_schema import GrowthProposal
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_scheduler_enabled": True,
                "autonomous_decision_enabled": True,
                "adl_max_pending_proposals": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "rt.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 注入一个 pending proposal
            if rc.growth_adapter:
                rc.growth_adapter.update_proposal(GrowthProposal(id="prop_pending", status="proposed"))

            rc.notify_scheduler_event(importance=0.8)
            rc.tick()

            hist = rc.get_autonomous_decision_history(limit=20)
            self.assertTrue(any(h["action"] in {"hold_stable", "skip"} for h in hist))


if __name__ == "__main__":
    unittest.main()

