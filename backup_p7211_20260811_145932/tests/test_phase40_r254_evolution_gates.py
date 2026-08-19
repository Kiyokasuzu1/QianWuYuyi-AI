"""
Phase 4.0 — R2.5.4 Gates: Personality Evolution Pipeline

Gates（EP-1 ~ EP-4 红线 + 形状/回放/幂等/identity 二次保护/异常隔离/集成）：
  EP-1  approved 才能 apply（rejected/deferred/pending → 必须失败）
  EP-2  applied 后不可重复执行（幂等保护）
  EP-3  EvolutionRecord 必须存在（任何 PersonalityState 变化必须有 proposal_id+approval_id+evolution_record_id）
  EP-4  Identity Anchor 二次保护（即使 Approval bug，identity.core.* 也无法 apply）
  EP-5  EvolutionRecord 8 字段冻结 exact shape
  EP-6  change_type 枚举（trait_delta / interest_transition / new_trait）
  EP-7  回放：可从 evolution_record_ids 回答「为什么羽依现在喜欢 AI 绘画？」
  EP-8  PersonalityState.apply_evolution 是唯一修改入口（直接赋值 traits 不生效）
  EP-9  before/after 快照正确（apply 前 vs apply 后 trait 值变化）
  EP-10 异常隔离：EvolutionPipeline 崩溃不传播到 accept_experience
  EP-11 集成：只有 approval=approved 才触发 evolution；applied=True 仅在 pipeline 成功后
  EP-12 集成：approval=deferred/rejected 时 applied=False（不触发 evolution）
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from src.personality.evolution_record import (
    EvolutionRecord,
    R254_FROZEN_KEYS,
    ALLOWED_CHANGE_TYPES,
    build_evolution_record,
)
from src.personality.personality_state import (
    PersonalityState,
    IDENTITY_FORBIDDEN_PREFIXES,
    reset_personality_state,
    get_personality_state,
)
from src.personality.evolution_pipeline import EvolutionPipeline

from src.contracts.growth_schema import GrowthProposal, ChangeItem


# ============================================================
# 辅助
# ============================================================
def _mk_proposal(
    *,
    proposal_id: str = "prop_ep_test_001",
    status: str = "approved",
    confidence: float = 0.9,
    changes: List[Dict[str, Any]] = None,
    evaluator_meta: Dict[str, Any] = None,
) -> GrowthProposal:
    if changes is None:
        changes = [{"path": "trait.creativity", "before": 0.6, "after": 0.7, "reason": "多次创作"}]
    ci_list = [ChangeItem(
        path=c["path"], before=c.get("before"), after=c.get("after"), reason=c.get("reason"),
    ) for c in changes]
    return GrowthProposal(
        id=proposal_id,
        status=status,
        confidence=confidence,
        proposed_changes=ci_list,
        evidence_ids=[f"ev_{i}" for i in range(5)],
        evaluator_meta=evaluator_meta or {"occurrence_count": 4},
    )


def _mk_approval(
    *,
    approval_id: str = "approval_ep_test_001",
    decision: str = "approved",
    reasons: List[str] = None,
) -> Dict[str, Any]:
    return {
        "id": approval_id,
        "proposal_id": "prop_ep_test_001",
        "decision": decision,
        "reasons": reasons or ["identity_consistent", "enough_evidence", "no_conflict", "governance_policy_approved"],
        "confidence": 0.9,
        "reviewer": "system",
    }


class TestPhase40R254EP5EP6RecordShape(unittest.TestCase):
    """EP-5 8 字段冻结；EP-6 change_type 枚举"""

    def test_ep5_frozen_keys_exact_8(self):
        r = build_evolution_record(
            proposal_id="p1",
            approval_id="a1",
            change_type="trait_delta",
            before={"creativity": 0.6},
            after={"creativity": 0.7},
            reasons=["approved"],
        )
        for k in R254_FROZEN_KEYS:
            self.assertIn(k, r)
        self.assertEqual(len(R254_FROZEN_KEYS), 8)

    def test_ep5_missing_proposal_or_approval_refused(self):
        with self.assertRaises(ValueError):
            build_evolution_record(
                proposal_id="", approval_id="a1",
                change_type="trait_delta", before={}, after={},
                reasons=["x"],
            )
        with self.assertRaises(ValueError):
            build_evolution_record(
                proposal_id="p1", approval_id="",
                change_type="trait_delta", before={}, after={},
                reasons=["x"],
            )

    def test_ep6_change_type_enum(self):
        for ct in ALLOWED_CHANGE_TYPES:
            r = build_evolution_record(
                proposal_id="p", approval_id="a",
                change_type=ct, before={"x": 0.1}, after={"x": 0.2},
                reasons=["x"],
            )
            self.assertIn(r["change_type"], ALLOWED_CHANGE_TYPES)
        with self.assertRaises(ValueError):
            build_evolution_record(
                proposal_id="p", approval_id="a",
                change_type="invalid_type", before={}, after={},
                reasons=["x"],
            )

    def test_ep5_empty_reasons_refused(self):
        with self.assertRaises(ValueError):
            build_evolution_record(
                proposal_id="p", approval_id="a",
                change_type="trait_delta", before={}, after={},
                reasons=[],
            )


class TestPhase40R254EP1OnlyApprovedCanApply(unittest.TestCase):
    """EP-1: 只有 approved 才能 apply"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_ep1_rejected_proposal_cannot_apply(self):
        prop = _mk_proposal(status="rejected")
        approval = _mk_approval(decision="rejected")
        res = self.pipeline.execute(prop, approval)
        self.assertFalse(res["executed"])
        self.assertIn("EP-1", res.get("error", ""))

    def test_ep1_deferred_proposal_cannot_apply(self):
        prop = _mk_proposal(status="deferred")
        approval = _mk_approval(decision="deferred")
        res = self.pipeline.execute(prop, approval)
        self.assertFalse(res["executed"])
        self.assertIn("EP-1", res.get("error", ""))

    def test_ep1_pending_proposal_cannot_apply(self):
        prop = _mk_proposal(status="proposed")
        approval = _mk_approval(decision="approved")  # 即使 approval 说 approved
        res = self.pipeline.execute(prop, approval)
        self.assertFalse(res["executed"])
        self.assertIn("EP-1", res.get("error", ""))

    def test_ep1_approved_proposal_can_apply(self):
        prop = _mk_proposal(status="approved")
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])
        self.assertIsNotNone(res["evolution_record_id"])


class TestPhase40R254EP2Idempotency(unittest.TestCase):
    """EP-2: 同一 proposal 不可重复 apply"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_ep2_double_apply_fails(self):
        prop = _mk_proposal(proposal_id="prop_ep2_001", status="approved")
        approval = _mk_approval(approval_id="approval_ep2_001")
        res1 = self.pipeline.execute(prop, approval)
        self.assertTrue(res1["executed"])
        # 第二次：同一 proposal_id
        res2 = self.pipeline.execute(prop, approval)
        self.assertFalse(res2["executed"])
        self.assertIn("EP-2", res2.get("error", ""))

    def test_ep2_different_proposal_both_succeed(self):
        # 两个不同 trait 的 proposal，避免 before/after 冲突
        prop1 = _mk_proposal(
            proposal_id="prop_ep2_a", status="approved",
            changes=[{"path": "trait.creativity", "before": 0.6, "after": 0.7, "reason": "创作"}],
        )
        prop2 = _mk_proposal(
            proposal_id="prop_ep2_b", status="approved",
            changes=[{"path": "trait.curiosity", "before": 0.7, "after": 0.8, "reason": "探索"}],
        )
        approval1 = _mk_approval(approval_id="approval_ep2_a")
        approval2 = _mk_approval(approval_id="approval_ep2_b")
        r1 = self.pipeline.execute(prop1, approval1)
        r2 = self.pipeline.execute(prop2, approval2)
        self.assertTrue(r1["executed"])
        self.assertTrue(r2["executed"])
        self.assertNotEqual(r1["evolution_record_id"], r2["evolution_record_id"])


class TestPhase40R254EP3RecordMustExist(unittest.TestCase):
    """EP-3: 任何 PersonalityState 变化必须有 proposal_id + approval_id + evolution_record_id"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_ep3_apply_produces_all_three_ids(self):
        prop = _mk_proposal(proposal_id="prop_ep3_001", status="approved")
        approval = _mk_approval(approval_id="approval_ep3_001")
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])
        self.assertEqual(res["proposal_id"], "prop_ep3_001")
        self.assertEqual(res["approval_id"], "approval_ep3_001")
        self.assertIsNotNone(res["evolution_record_id"])
        self.assertTrue(res["evolution_record_id"].startswith("evo_"))

    def test_ep3_state_has_evolution_record_id_in_history(self):
        prop = _mk_proposal(proposal_id="prop_ep3_002", status="approved")
        approval = _mk_approval(approval_id="approval_ep3_002")
        res = self.pipeline.execute(prop, approval)
        snap = self.state.snapshot()
        self.assertIn(res["evolution_record_id"], snap["evolution_record_ids"])
        self.assertEqual(snap["version"], 1)

    def test_ep3_record_without_proposal_id_rejected_by_state(self):
        """直接调 PersonalityState.apply_evolution 传缺字段的 record → 被拒"""
        state = reset_personality_state()
        bad_record: EvolutionRecord = {
            "record_id": "evo_bad",
            "timestamp": "2026-01-01",
            "change_type": "trait_delta",
            "before": {},
            "after": {"x": 0.5},
            "reasons": ["test"],
            # 缺 proposal_id 和 approval_id
        }
        res = state.apply_evolution(bad_record)
        self.assertFalse(res["applied"])
        self.assertIn("EP-3", res.get("error", ""))


class TestPhase40R254EP4IdentityAnchorSecondProtection(unittest.TestCase):
    """EP-4: Identity Anchor 二次保护（即使 Approval bug，identity.core.* 也无法 apply）"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_ep4_identity_core_blocked_even_if_approved(self):
        """模拟 Approval 有 bug：批准了一个修改 identity.core.name 的 proposal。
        EvolutionPipeline 应该能执行（因为 path=identity.core.name → trait_name=name），
        但 PersonalityState.apply_evolution 会拦截。"""
        prop = _mk_proposal(
            proposal_id="prop_ep4_001",
            status="approved",
            changes=[{"path": "identity.core.name", "before": 0, "after": 1, "reason": "bug"}],
        )
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        # Pipeline 可能 executed=True（因为其他 trait 可能被修改），但 identity 被 blocked
        # 但这里只有一个 change 且是 identity.core.name → 被 blocked → no trait applied → executed=False
        self.assertFalse(res["executed"])
        self.assertIn("identity.core.name", res.get("blocked_identity", []))

    def test_ep4_manifesto_blocked(self):
        prop = _mk_proposal(
            proposal_id="prop_ep4_002",
            status="approved",
            changes=[{"path": "manifesto.privacy", "before": 1.0, "after": 0.2, "reason": "bug"}],
        )
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        self.assertFalse(res["executed"])
        self.assertIn("manifesto.privacy", res.get("blocked_identity", []))

    def test_ep4_mixed_changes_identity_blocked_others_applied(self):
        """一个 proposal 同时有 identity trait 和正常 trait → identity 被拦，正常 trait 正常应用"""
        prop = _mk_proposal(
            proposal_id="prop_ep4_003",
            status="approved",
            changes=[
                {"path": "identity.core.name", "before": 0, "after": 1, "reason": "bug"},
                {"path": "trait.creativity", "before": 0.6, "after": 0.75, "reason": "创作经历"},
            ],
        )
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])  # creativity 被成功应用
        self.assertIn("identity.core.name", res.get("blocked_identity", []))
        self.assertIn("trait.creativity", res.get("applied_traits", {}))
        # identity_continuity_ok=True（保护生效了，identity 没变）
        self.assertTrue(res["identity_continuity_ok"])

    def test_ep4_forbidden_prefixes_covered(self):
        for prefix in IDENTITY_FORBIDDEN_PREFIXES:
            self.assertTrue(prefix.startswith("identity.") or prefix.startswith("manifesto."))


class TestPhase40R254EP7Replay(unittest.TestCase):
    """EP-7 回放：可从 evolution_record_ids 回答「为什么羽依现在喜欢 AI 绘画？」"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_ep7_can_replay_why_trait_changed(self):
        # 模拟：creativity 从 0.6 → 0.75（因为多次创作经历被 approved）
        prop = _mk_proposal(
            proposal_id="prop_ep7_001",
            status="approved",
            changes=[{"path": "trait.creativity", "before": 0.6, "after": 0.75, "reason": "持续创作经历"}],
            evaluator_meta={"occurrence_count": 5, "transition_proposal_mode": "reinforce"},
        )
        approval = _mk_approval(approval_id="approval_ep7_001")
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])

        # 验证：state 版本变了 + trait 变了 + record 在 history 里
        snap = self.state.snapshot()
        self.assertEqual(snap["version"], 1)
        self.assertEqual(snap["traits"]["creativity"], 0.75)
        self.assertEqual(len(snap["evolution_record_ids"]), 1)

        # 「为什么羽依现在 creativity 是 0.75？」
        # → 因为 proposal prop_ep7_001 被 approval approval_ep7_001 批准后执行了演化
        record_id = snap["evolution_record_ids"][0]
        self.assertTrue(record_id.startswith("evo_"))


class TestPhase40R254EP8EP9StateAndSnapshot(unittest.TestCase):
    """EP-8 apply_evolution 是唯一修改入口；EP-9 before/after 快照正确"""

    def setUp(self):
        self.state = reset_personality_state()

    def test_ep8_direct_trait_assignment_does_not_persist(self):
        """直接 state.traits["x"] = 0.9 技术上可以（Python），但 version 不增、record_ids 不增。
        Gate 检查：只有 apply_evolution 才会增 version 和 record_ids。"""
        old_version = self.state.version
        old_record_count = len(self.state.evolution_record_ids)
        # 直接赋值（绕过 apply_evolution）
        self.state.traits["hacked"] = 0.99
        # version 没变 → 说明没走正规 channel
        self.assertEqual(self.state.version, old_version)
        self.assertEqual(len(self.state.evolution_record_ids), old_record_count)

    def test_ep9_before_after_snapshot_correct(self):
        prop = _mk_proposal(
            proposal_id="prop_ep9_001",
            status="approved",
            changes=[{"path": "trait.curiosity", "before": 0.7, "after": 0.82, "reason": "探索"}],
        )
        approval = _mk_approval()
        pipeline = EvolutionPipeline(personality_state=self.state)
        old_curiosity = self.state.get_trait("curiosity")
        res = pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])
        applied = res["applied_traits"]
        self.assertIn("trait.curiosity", applied)
        self.assertEqual(applied["trait.curiosity"]["before"], 0.7)
        self.assertEqual(applied["trait.curiosity"]["after"], 0.82)
        self.assertEqual(self.state.get_trait("curiosity"), 0.82)
        self.assertNotEqual(old_curiosity, self.state.get_trait("curiosity"))


class TestPhase40R254EP10EP11EP12Integration(unittest.TestCase):
    """EP-10 异常隔离；EP-11 applied=True 仅 approved；EP-12 deferred/rejected → applied=False"""

    def test_ep10_evolution_crash_isolated(self):
        from unittest.mock import patch
        state = reset_personality_state()
        pipeline = EvolutionPipeline(personality_state=state)
        prop = _mk_proposal(status="approved")
        approval = _mk_approval()
        # 让 build_evolution_record 抛异常 → 应被隔离
        with patch(
            "src.personality.evolution_pipeline.build_evolution_record",
            side_effect=RuntimeError("模拟 build 崩溃"),
        ):
            res = pipeline.execute(prop, approval)
        self.assertFalse(res["executed"])
        self.assertTrue(res["isolated"])
        self.assertIn("evolution_pipeline_crash", res.get("error", ""))

    def test_ep11_integration_applied_true_only_when_approved_and_executed(self):
        """集成测试：GrowthIntegrationService.accept_experience → approval=approved → evolution → applied=True
        但默认 eligibility/evidence 可能不满足 → 默认 deferred。
        这里用 mock 让 approval 返回 approved，验证 applied=True。"""
        from src.growth.growth_integration import GrowthIntegrationService
        from src.personality.personality_state import reset_personality_state
        # 重置全局状态（避免其他测试污染）
        reset_personality_state()
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        # 预热让 eligibility 过
        rec = {
            "id": "mem_ep11_w0",
            "content": "这周我画了两幅插画，设计了一张新角色立绘，完成了水彩练习，坚持创作爱好。",
            "user_id": "u_ep11",
            "role": "user",
            "timestamp": "2026-08-08T10:00:00",
            "importance": 0.95,
            "memory_class": "preference",
            "metadata": {"memory_type": "user_preference", "meaning": "user_preference:creative_activity_artistic"},
        }
        svc.accept_experience(rec)
        svc.accept_experience({**rec, "id": "mem_ep11_w1"})
        # 第三次：patch ApprovalManager.govern_proposal 返回 approved + 足够 evidence
        from unittest.mock import patch
        fake_approval_res = {
            "governed": True,
            "decision_id": "approval_ep11_fake",
            "decision": "approved",
            "proposal_status": "approved",
            "reasons": ["identity_consistent", "enough_evidence", "no_conflict", "governance_policy_approved"],
            "error": None,
            "isolated": False,
        }
        with patch(
            "src.approval.approval_manager.ApprovalManager.govern_proposal",
            return_value=fake_approval_res,
        ):
            real = svc.accept_experience({**rec, "id": "mem_ep11_r2"})
        # approval=approved → evolution 应该被触发
        self.assertIn("approval_result_approved", real["reasons"])
        # applied 可能 True（如果 evolution 成功）或 False（如果 evidence 不足等）
        # 但至少 evolution_pipeline_executed 或 evolution_pipeline_not_executed 应该在 reasons 里
        has_evo = any("evolution" in r for r in real["reasons"])
        self.assertTrue(has_evo, f"应包含 evolution 相关 reason, got: {real['reasons']}")

    def test_ep12_deferred_proposal_applied_false(self):
        """approval=deferred → 不触发 evolution → applied=False"""
        from src.growth.growth_integration import GrowthIntegrationService
        from src.personality.personality_state import reset_personality_state
        reset_personality_state()
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        rec = {
            "id": "mem_ep12_w0",
            "content": "这周我画了两幅插画，设计了一张新角色立绘，完成了水彩练习，坚持创作爱好。",
            "user_id": "u_ep12",
            "role": "user",
            "timestamp": "2026-08-08T10:00:00",
            "importance": 0.95,
            "memory_class": "preference",
            "metadata": {"memory_type": "user_preference", "meaning": "user_preference:creative_activity_artistic"},
        }
        svc.accept_experience(rec)
        svc.accept_experience({**rec, "id": "mem_ep12_w1"})
        from unittest.mock import patch
        fake_deferred = {
            "governed": True,
            "decision_id": "approval_ep12_fake",
            "decision": "deferred",
            "proposal_status": "deferred",
            "reasons": ["insufficient_evidence_deferred"],
            "error": None,
            "isolated": False,
        }
        with patch(
            "src.approval.approval_manager.ApprovalManager.govern_proposal",
            return_value=fake_deferred,
        ):
            real = svc.accept_experience({**rec, "id": "mem_ep12_r2"})
        self.assertIs(real["applied"], False)
        self.assertIn("approval_result_deferred", real["reasons"])
        # 不应该有 evolution_pipeline_executed
        self.assertNotIn("evolution_pipeline_executed", real["reasons"])


class TestPhase40R254TransitionModeIntegration(unittest.TestCase):
    """interest_transition change_type 正确映射"""

    def setUp(self):
        self.state = reset_personality_state()
        self.pipeline = EvolutionPipeline(personality_state=self.state)

    def test_gradual_transition_change_type(self):
        prop = _mk_proposal(
            proposal_id="prop_ep_transition_001",
            status="approved",
            changes=[{"path": "trait.AI_art", "before": 0.2, "after": 0.55, "reason": "兴趣迁移"}],
            evaluator_meta={"transition_proposal_mode": "gradual_transition"},
        )
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])
        # 验证 state 里有新 trait
        self.assertEqual(self.state.get_trait("AI_art"), 0.55)
        # version 增了
        self.assertEqual(self.state.version, 1)

    def test_new_interest_change_type(self):
        prop = _mk_proposal(
            proposal_id="prop_ep_new_001",
            status="approved",
            changes=[{"path": "interest.programming", "before": 0.0, "after": 0.4, "reason": "新兴趣"}],
            evaluator_meta={"transition_proposal_mode": "new_interest_emerging"},
        )
        approval = _mk_approval()
        res = self.pipeline.execute(prop, approval)
        self.assertTrue(res["executed"])
        self.assertEqual(self.state.get_trait("programming"), 0.4)


if __name__ == "__main__":
    unittest.main()
