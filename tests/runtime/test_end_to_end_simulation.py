"""
Phase 3.5.20: EndToEndSimulationTest

模拟：
用户输入："最近我发现自己越来越喜欢创造东西"

验证：
1. Experience 创建
2. Memory 写入
3. Memory Relevance Evaluation
4. Reflection 分析
5. Reflection Evaluation
6. GrowthProposal 生成
7. Identity Stability 检查
8. Proposal 保持 pending
9. Audit 完整记录
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience


class EndToEndSimulationTest(unittest.TestCase):
    def test_full_runtime_lifecycle_simulation(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "approval_manager_enabled": True,
                "identity_continuity_enabled": True,
                "identity_anchor_enabled": True,
                "identity_stability_enabled": True,
                "runtime_integration_enabled": True,
                "reflection_min_experiences": 1,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "reflection_growth_history_path": os.path.join(tmp, "rgb.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })

            user_text = "最近我发现自己越来越喜欢创造东西"

            # 历史经验：为 Reflection 提供模式背景
            for idx, text in enumerate([
                "这段时间我越来越愿意自己做点新的东西",
                "我发现创造和整理想法会让我很有满足感",
            ]):
                rc.experience_builder._buffer.append(
                    RuntimeExperience(
                        experience_id=f"exp_hist_{idx}",
                        trigger_event={"event_type": "user.input", "data": {"content": text}},
                        trigger_type="user_message",
                        decision_source="history",
                        action_type="internal_reflection_candidate",
                        result=ActionResult(action_id=f"act_hist_{idx}", success=True),
                        self_state_before={"initiative": 0.7},
                        self_state_after={"initiative": 0.72},
                        duration_ms=20.0,
                    )
                )

            # 1) User Input -> RuntimeCore
            rc.inject_event("user.input", {"content": user_text, "user_id": "tester", "importance": 0.9})

            # 2) 明确完成一次 experience（当前 RuntimeCore 默认在无 action 时不会自动 finish）
            exp_id = rc._building_experience_id
            self.assertTrue(exp_id)
            rc.experience_builder.record_decision(
                exp_id,
                decision_source="simulation",
                intention_id="sim_reflect",
                action_type="internal_reflection_candidate",
            )
            time.sleep(0.02)
            exp = rc.experience_builder.finish_building(
                exp_id,
                result=ActionResult(
                    action_id="act_sim_001",
                    success=True,
                    response_received=False,
                ),
                self_state_after=rc.self_state.to_dict(),
            )
            self.assertIsNotNone(exp)

            # 3) Experience 持久化（Phase 4.1D：写入 Experience Journal，不进 MemoryStore）
            ok = rc.memory_adapter.store_experience(exp)
            self.assertTrue(ok)
            experiences = rc.memory_adapter.get_recent_experiences(limit=10)
            self.assertGreaterEqual(len(experiences), 1)
            # MemoryStore 保持纯净（runtime_experience 不得进入，C.2.3 设计意图）
            self.assertEqual(rc.memory_adapter.get_memory_store().load() or [], [])

            # 4) Memory Relevance Evaluation（MemoryStore 现只承载用户记忆）
            rc.memory_adapter.get_memory_store().add({
                "user_id": "tester",
                "content": "用户最近持续创作绘画作品",
                "role": "user",
                "metadata": {"memory_type": "user_event"},
            })
            mem_list = rc.memory_adapter.get_memory_store().load() or []
            ranked = rc.memory_relevance_evaluator.rank_memories(mem_list, query="我最近喜欢创造什么")
            self.assertGreaterEqual(len(ranked), 1)
            self.assertIn("relevance_audit_id", ranked[0])

            # 5) Reflection -> Evaluation -> GrowthProposal
            insight = rc.reflect_on_experiences()
            self.assertIsNotNone(insight)
            latest_eval = rc.get_latest_evaluation()
            self.assertIsNotNone(latest_eval)

            proposals = rc.get_growth_proposals(status="proposed", limit=10)
            self.assertGreaterEqual(len(proposals), 1)

            # 6) Identity Stability Check
            stability = rc.refresh_identity_stability(force=True)
            self.assertIsNotNone(stability)

            # 7) Proposal Approval Boundary: 仍保持 pending/proposed
            pending = rc.get_pending_growth_proposals(limit=10)
            self.assertGreaterEqual(len(pending), 1)
            self.assertEqual(proposals[0].status, "proposed")

            # 8) Audit 完整记录
            bridge_history = rc.get_reflection_growth_history(limit=10)
            self.assertGreaterEqual(len(bridge_history), 1)
            self.assertIsNotNone(rc.get_identity_stability_report())

            health = rc.get_runtime_health_report()
            self.assertIsNotNone(health)
            self.assertIn("overall_status", health)
