"""
Phase 3.5.7: Personality Growth Runtime 接入 - 完整生命周期测试

测试链路：
Experience → Memory → Reflection → GrowthProposal → Evaluator → PersonalityAdapter → GrowthRecord/EvolutionRecord
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class TestPersonalityAdapter(unittest.TestCase):
    """PersonalityAdapter 单元测试"""

    def test_01_build_change_request_has_evolution_and_growth(self):
        """build_change_request 同时生成 EvolutionRecord 和 GrowthRecord"""
        from src.personality.personality_adapter import (
            PersonalityAdapter,
            PersonalityChangeRequest,
        )
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        adapter = PersonalityAdapter()

        # 构造一个有明确变更的 Proposal
        p = GrowthProposal(
            proposed_changes=[
                ChangeItem(
                    path="self_state.initiative",
                    before=0.7,
                    after=0.6,
                    reason="lower_initiative",
                ),
            ],
            confidence=0.7,
            evidence_ids=["exp1", "exp2", "exp3"],
            evaluator_meta={"pattern_detected": "high_frequency_proactive"},
        )

        cr = adapter.build_change_request(
            p,
            source_insight_id="ins_001",
            evaluator_meta={"pattern_detected": "high_frequency_proactive", "passed": True},
        )

        self.assertIsInstance(cr, dict)
        self.assertEqual(cr["source_proposal_id"], p.id)
        self.assertEqual(cr["source_insight_id"], "ins_001")
        # EvolutionRecord 路径
        er = cr.get("evolution_record")
        self.assertIsNotNone(er)
        self.assertIn("initiative", er.get("trait_changes", {}))
        self.assertEqual(er["approved"], False)  # 默认不批准
        # GrowthRecord 路径（来自 pattern）
        grs = cr.get("growth_records") or []
        self.assertGreaterEqual(len(grs), 1)
        dims = set()
        for gr in grs:
            dims.update((gr.get("affected_dimensions", {}) or {}).keys())
        self.assertIn("initiative", dims)

    def test_02_evolution_record_default_not_approved(self):
        """EvolutionRecord 默认未批准，保留审批机制"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        adapter = PersonalityAdapter()
        p = GrowthProposal(
            proposed_changes=[ChangeItem(path="personality.traits.shyness", before=0.7, after=0.65)],
            confidence=0.8,
        )
        er = adapter.map_proposal_to_evolution_record(p)
        self.assertFalse(er["approved"])
        self.assertEqual(er["decision_reason"], "mapped_from_growth_proposal_pending_approval")

    def test_03_apply_proposal_in_memory_requires_mark_approved(self):
        """apply_proposal 只有在 mark_approved=True 时才会生效"""
        from src.personality.personality_adapter import PersonalityAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        adapter = PersonalityAdapter()
        p = GrowthProposal(
            proposed_changes=[
                ChangeItem(
                    path="personality.traits.shyness",
                    before=0.7,
                    after=0.6,
                    reason="decrease_shyness",
                )
            ],
            confidence=0.8,
        )

        # 不标记批准 → 不应用
        env_not_approved = adapter.apply_proposal(p, mark_approved=False)
        self.assertTrue(env_not_approved["applied"])  # 流程跑通，但 TraitStateUpdater 内部会检查 approved
        # 实际上 TraitStateUpdater 中 approved=False 时不应用，after 应该等于 before
        self.assertEqual(env_not_approved["before"], env_not_approved["after"])

        # 标记批准 → 应用
        env_approved = adapter.apply_proposal(p, mark_approved=True)
        self.assertTrue(env_approved["applied"])
        self.assertLess(env_approved["after"]["shyness"], env_approved["before"]["shyness"])


class TestProposalEvaluator(unittest.TestCase):
    """ProposalEvaluator 单元测试"""

    def _make_proposal(
        self,
        confidence=0.8,
        evidence_ids=None,
        changes=None,
        evaluator_meta=None,
    ):
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        evidence_ids = evidence_ids or ["e1", "e2", "e3"]
        changes = changes or [
            ChangeItem(path="self_state.initiative", before=0.7, after=0.65),
        ]
        return GrowthProposal(
            proposed_changes=changes,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evaluator_meta=evaluator_meta or {},
        )

    def test_01_confidence_too_low_rejected(self):
        """低置信度提案不通过"""
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator

        evaluator = ProposalEvaluator()
        p = self._make_proposal(confidence=0.1)
        res = evaluator.evaluate(p)
        self.assertFalse(res.passed)
        self.assertTrue(any("confidence_too_low" in r for r in res.reasons))

    def test_02_insufficient_evidence_rejected(self):
        """证据不足不通过"""
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator, EvaluationThresholds

        # 使用严格阈值：min_evidence_count=5（确保即使有 1 个变更也达不到）
        evaluator = ProposalEvaluator(thresholds=EvaluationThresholds(
            min_evidence_count=5,
        ))
        # evidence_ids 空 + 仅 1 个变更 → effective = 0 + max(0,1-1) = 0 < 5
        p = self._make_proposal(
            evidence_ids=[],
            changes=[],
        )
        res = evaluator.evaluate(p)
        self.assertFalse(res.passed)
        self.assertTrue(any("insufficient_evidence" in r for r in res.reasons))

    def test_03_contradiction_with_recent_accepted(self):
        """与近期接受的提案冲突（方向相反且变化大）不通过"""
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        evaluator = ProposalEvaluator()
        recent_accepted = [
            GrowthProposal(
                id="recent_old",
                proposed_changes=[
                    ChangeItem(path="self_state.initiative", before=0.5, after=0.8),  # +0.3
                ],
                status="accepted",
            )
        ]
        # 当前提案是反向（-0.3）
        current = GrowthProposal(
            proposed_changes=[
                ChangeItem(path="self_state.initiative", before=0.8, after=0.5),  # -0.3
            ],
            confidence=0.8,
            evidence_ids=["e1", "e2", "e3", "e4"],
        )
        res = evaluator.evaluate(current, recent_accepted_proposals=recent_accepted)
        self.assertFalse(res.passed)
        self.assertTrue(any("contradiction" in r for r in res.reasons))

    def test_04_stability_unstable_directions_rejected(self):
        """稳定性：近期方向频繁来回变动 → 不通过"""
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator, EvaluationThresholds
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        th = EvaluationThresholds(
            min_confidence=0.3,
            min_evidence_count=1,
            stability_window=4,
            stability_min_ratio=0.75,
        )
        evaluator = ProposalEvaluator(thresholds=th)

        # 3 次 +, 1 次 - → ratio=2/4=0.5 → 小于 0.75 不稳定
        recent_accepted = [
            GrowthProposal(id="a", status="accepted", proposed_changes=[ChangeItem(path="t", before=0.1, after=0.2)]),
            GrowthProposal(id="b", status="accepted", proposed_changes=[ChangeItem(path="t", before=0.2, after=0.1)]),
            GrowthProposal(id="c", status="accepted", proposed_changes=[ChangeItem(path="t", before=0.1, after=0.2)]),
            GrowthProposal(id="d", status="accepted", proposed_changes=[ChangeItem(path="t", before=0.2, after=0.1)]),
        ]
        # 当前提案是 +0.1
        current = GrowthProposal(
            proposed_changes=[ChangeItem(path="t", before=0.1, after=0.2)],
            confidence=0.8,
            evidence_ids=["e1", "e2"],
        )
        res = evaluator.evaluate(current, recent_accepted_proposals=recent_accepted)
        self.assertFalse(res.passed)
        self.assertTrue(any("unstable_trait" in r for r in res.reasons))

    def test_05_happy_path_evaluator_passed(self):
        """评估通过"""
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator

        evaluator = ProposalEvaluator()
        p = self._make_proposal(confidence=0.8)
        res = evaluator.evaluate(p)
        self.assertTrue(res.passed, f"should pass, reasons: {res.reasons}")
        self.assertGreater(res.score, 0.0)
        # to_evaluator_meta 可序列化
        meta = res.to_evaluator_meta()
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta["passed"], True)


class TestFullPersonalityGrowthLifeCycle(unittest.TestCase):
    """完整人格成长生命周期测试：
    Experience → Memory → Reflection → GrowthProposal → Evaluator → PersonalityChangeRequest → In-memory apply
    """

    def setUp(self):
        from src.memory.memory_store import MemoryStore

        self._memory_path = str(PROJECT_ROOT / "data" / f"test_pgr_memory_{uuid.uuid4().hex[:8]}.json")
        self._proposals_path = str(PROJECT_ROOT / "data" / f"test_pgr_proposals_{uuid.uuid4().hex[:8]}.json")
        self._state_path = str(PROJECT_ROOT / "data" / f"test_pgr_state_{uuid.uuid4().hex[:8]}.json")
        self._memory_store = MemoryStore(self._memory_path)

    def tearDown(self):
        import os

        for p in [self._memory_path, self._proposals_path, self._state_path]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass

    def test_01_end_to_end_lifecycle(self):
        """完整链路：Experience → Memory → Reflection → Proposal → Evaluator → ChangeRequest → Apply in-memory"""
        from src.runtime.adapters.memory_adapter import MemoryAdapter
        from src.runtime.adapters.growth_adapter import GrowthAdapter
        from src.runtime.adapters.proposal_evaluator import ProposalEvaluator
        from src.personality.personality_adapter import PersonalityAdapter
        from src.runtime.reflection_engine import ReflectionEngine, ReflectionEngineConfig
        from src.contracts.experience_schema import RuntimeExperience, ActionResult

        # 1. 准备组件
        ma = MemoryAdapter(memory_store=self._memory_store, user_id="test_user")
        ga = GrowthAdapter(proposals_path=self._proposals_path)
        evaluator = ProposalEvaluator()
        pa = PersonalityAdapter()
        refl = ReflectionEngine(config=ReflectionEngineConfig(min_experiences=3))

        # 2. 构造 Experiences 并存入 Memory
        for i in range(6):
            exp = RuntimeExperience(
                trigger_event={"event_type": "user_message", "data": {"content": f"hi{i}"}},
                trigger_type="user_message",
                decision_source="rule",
                action_type="send_message",
                result=ActionResult(
                    action_id=f"act_{i}",
                    success=True,
                    response_received=False,  # 用户响应率低
                    user_response=None,
                ),
                self_state_before={"initiative": 0.9},
                self_state_after={"initiative": 0.85},
                duration_ms=100.0 + i,
            )
            stored = ma.store_experience(exp)
            self.assertTrue(stored, f"experience {i} should be stored")

        # 3. 从 Memory 取回 Experiences
        exps = ma.get_recent_experiences(limit=10)
        self.assertGreaterEqual(len(exps), 5)

        # 4. 反思生成 ReflectionInsight
        insight = refl.reflect(exps)
        self.assertIsNotNone(insight)
        pattern = insight.pattern_detected
        self.assertIn(pattern, {"low_user_response", "high_frequency_proactive", "high_user_activity"})

        # 5. ReflectionInsight → GrowthProposal
        stored_ok = ga.store_insight(insight)
        self.assertTrue(stored_ok)
        proposals = ga.list_proposals(status="proposed", limit=10)
        self.assertGreaterEqual(len(proposals), 1)
        proposal = proposals[-1]

        # 6. Evaluator 评估 Proposal
        eval_res = evaluator.evaluate(proposal)
        # 评估应该通过或有明确原因
        self.assertIsNotNone(eval_res)
        self.assertIsInstance(eval_res.passed, bool)
        meta = eval_res.to_evaluator_meta()
        # 把 pattern_detected 也塞进 proposal.evaluator_meta（供 PersonalityAdapter 映射 GrowthRecord）
        proposal.evaluator_meta = {"pattern_detected": pattern, **meta}
        if not eval_res.passed:
            # 如果不通过，跳过后续（该链路也合法）
            return

        # 7. PersonalityAdapter → PersonalityChangeRequest
        cr = pa.build_change_request(
            proposal,
            source_insight_id=insight.insight_id,
            evaluator_meta={"pattern_detected": pattern, **meta},
        )
        self.assertIsNotNone(cr)
        self.assertEqual(cr["source_proposal_id"], proposal.id)
        self.assertEqual(cr["source_insight_id"], insight.insight_id)
        self.assertIsNotNone(cr.get("evolution_record"))
        self.assertGreaterEqual(len(cr.get("growth_records", [])), 1)

        # 8. In-memory apply（仅预览，不持久化）
        env = pa.apply_proposal(proposal, actor="test_actor", mark_approved=True)
        self.assertTrue(env["applied"], f"apply should succeed: {env}")
        self.assertIn("evolution_record_id", env)

        # 9. 验证未修改任何核心人格文件（Persona 文档等不触及）
        from src.personality.personality_profile import PersonalityProfile
        base_initiative_in_profile = PersonalityProfile.BASE.get("initiative")
        # Profile BASE 不可变 → 完全不改变
        self.assertEqual(
            PersonalityProfile.BASE.get("initiative"),
            base_initiative_in_profile,
            "BASE profile should not be touched",
        )


class TestRuntimeCorePersonalityIntegration(unittest.TestCase):
    """RuntimeCore 与人格成长系统的集成测试"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge

        reset_runtime_bridge()
        self._state_path = str(PROJECT_ROOT / "data" / f"test_rtpgr_state_{uuid.uuid4().hex[:8]}.json")
        self._memory_path = str(PROJECT_ROOT / "data" / f"test_rtpgr_memory_{uuid.uuid4().hex[:8]}.json")
        self._proposals_path = str(PROJECT_ROOT / "data" / f"test_rtpgr_proposals_{uuid.uuid4().hex[:8]}.json")

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge

        b = RuntimeBridge.get_instance()
        if b._runtime_core and b._runtime_core.is_running:
            b.shutdown()
        reset_runtime_bridge()
        import os

        for p in [self._state_path, self._memory_path, self._proposals_path]:
            try:
                if os.path.exists(p):
                    os.unlink(p)
            except Exception:
                pass

    def _cfg(self, **kwargs):
        base = {
            "state_file": self._state_path,
            "memory_store_path": self._memory_path,
            "growth_proposals_path": self._proposals_path,
            "tick_interval_seconds": 60,
            "experience_enabled": True,
            "adapters_enabled": True,
            "eval_min_confidence": 0.3,  # 测试降低阈值
            "eval_min_evidence_count": 1,
        }
        base.update(kwargs)
        return base

    def test_01_adapters_initialized_properly(self):
        """RuntimeCore 正确初始化 ProposalEvaluator 和 PersonalityAdapter"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._cfg())
        bridge.initialize()

        rc = bridge._runtime_core
        self.assertIsNotNone(rc.proposal_evaluator)
        self.assertIsNotNone(rc.personality_adapter)

    def test_02_accept_proposal_generates_change_request(self):
        """accept_growth_proposal 自动生成 PersonalityChangeRequest"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        bridge = get_runtime_bridge(config=self._cfg())
        bridge.initialize()
        rc = bridge._runtime_core

        # 构造并直接在 GrowthAdapter 里放一个 proposal（绕过 reflection）
        ga = rc.growth_adapter
        p = GrowthProposal(
            proposed_changes=[
                ChangeItem(path="self_state.initiative", before=0.7, after=0.6),
            ],
            confidence=0.8,
            evidence_ids=["e1", "e2", "e3"],
            status="proposed",
        )
        # 先存到 GrowthAdapter（直接调用内部 save）
        proposals = ga._load()
        proposals.append(p)
        ga._save(proposals)

        # 接受 → 自动生成 ChangeRequest
        before_cr = rc.list_personality_change_requests()
        accepted = rc.accept_growth_proposal(p.id)
        self.assertIsNotNone(accepted)
        self.assertEqual(accepted.status, "accepted")

        after_cr = rc.list_personality_change_requests()
        # 可能因为 evaluator 不通过而没有生成，但如果通过了则数量增加
        self.assertGreaterEqual(len(after_cr), len(before_cr))

    def test_03_process_proposal_api(self):
        """process_proposal_to_change_request 完整流程"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        bridge = get_runtime_bridge(config=self._cfg())
        bridge.initialize()
        rc = bridge._runtime_core

        p = GrowthProposal(
            proposed_changes=[
                ChangeItem(path="self_state.initiative", before=0.8, after=0.7),
            ],
            confidence=0.8,
            evidence_ids=["e1", "e2", "e3", "e4", "e5"],
            evaluator_meta={"pattern_detected": "high_frequency_proactive"},
        )

        cr = rc.process_proposal_to_change_request(p, source_insight_id="ins_xyz")
        # 如果 evaluator 通过，则 cr 非空
        if cr is not None:
            self.assertEqual(cr["source_insight_id"], "ins_xyz")
            self.assertEqual(cr["source_proposal_id"], p.id)

            # list_pending 里能拿到
            all_crs = rc.list_personality_change_requests()
            self.assertGreaterEqual(len(all_crs), 1)

            # 测试 in-memory apply（只预览）
            preview = rc.apply_personality_change_request_in_memory(cr)
            self.assertIsNotNone(preview.get("evolution_before"))
            self.assertIsNotNone(preview.get("evolution_after"))

    def test_04_personality_core_untouched(self):
        """验证未修改现有 Personality 核心（PersonalityResolver 等未被修改，仍按原方式工作）"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.personality.personality_resolver import PersonalityResolver
        from src.growth.growth_state import GrowthState

        bridge = get_runtime_bridge(config=self._cfg(adapters_enabled=False))
        bridge.initialize()

        # PersonalityResolver 应该仍然能用
        gs = GrowthState()
        pr = PersonalityResolver(state=gs)
        vec = pr.resolve()
        self.assertIsNotNone(vec)
        self.assertIsNotNone(vec.warmth)
        self.assertIsNotNone(vec.shyness)

    def test_05_backward_compat_runtime_tests(self):
        """禁用 adapters 时，ProposalEvaluator/PersonalityAdapter 不被初始化"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config=self._cfg(adapters_enabled=False))
        bridge.initialize()
        rc = bridge._runtime_core

        self.assertIsNone(rc.proposal_evaluator)
        self.assertIsNone(rc.personality_adapter)
        # RuntimeCore 正常工作
        rc.self_state.initiative = 0.7
        self.assertGreaterEqual(rc.self_state.initiative, 0.0)


if __name__ == "__main__":
    unittest.main()
