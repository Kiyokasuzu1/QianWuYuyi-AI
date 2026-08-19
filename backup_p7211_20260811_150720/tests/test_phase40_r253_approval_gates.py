"""
Phase 4.0 — R2.5.3 Gates: Evolution Governance Layer（Approval Framework）

Gates（目标 12 条 + 扩展共 ~17 用例）：
  AP-1  ApprovalDecision 7 字段冻结 exact
  AP-2  decision（3 枚举）+ reviewer（仅 system）红线校验
  AP-3  Identity Layer: identity.core.* 路径 → rejected + identity_violation_rejected
  AP-4  Evidence: evidence_ids < 3 → deferred + insufficient_evidence_deferred
  AP-5  Evidence: evaluator_confidence < 0.75 → deferred + confidence_below_threshold
  AP-6  Evidence: occurrence_count < 2 → deferred + time_span_too_short
  AP-7  Transition 保护: gradual_transition mode → deferred + interest_transition_gradual_requires_observation
  AP-8  Conflict: needs_review=True → under_review + conflict_detected_under_review
  AP-9  Conflict: before/after 跨越 0.5 + delta >= 0.15 → under_review + conflict_with_existing_trait_deferred
  AP-10 三层全通过 → approved + reasons 含 (identity_consistent, enough_evidence, no_conflict, governance_policy_approved)
  AP-11 红线: approval 三模块无 PersonalityState / PersonalityAdapter / accept_proposal / apply_proposal 调用（AST 扫描）
  AP-12 红线: approved 后 proposal.status=="approved"（不是 "accepted"/"applied"）；GrowthIntegration applied=False
  AP-13 集成: accept_experience reasons 含 approval_decision_applied + approval_result_$decision
  AP-14 审计: evaluator_meta.approval_decision_id 与外层返回 approval_decision_id 一致
  AP-15 异常隔离: ApprovalManager.govern_proposal 抛异常，外层 accept_experience pipeline_state 不为 error
  AP-16 状态映射: rejected → status=rejected；deferred → status=deferred；status_hint=under_review → 优先用 under_review
  AP-17 from_states 保护: 若 proposal.status 已是 accepted/applied，则治理不修改（不回滚历史已应用 proposal）
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional

from src.approval.approval_decision import (
    FROZEN_KEYS,
    ALLOWED_DECISIONS,
    ALLOWED_REVIEWERS,
    ApprovalDecision,
    build_approval_decision,
)
from src.approval.approval_policy import (
    ApprovalPolicy,
    ProposalSnapshot,
    DEFAULT_MIN_EVIDENCE_COUNT,
    DEFAULT_MIN_EVALUATOR_CONFIDENCE,
    DEFAULT_MIN_OCCURRENCE_COUNT,
)
from src.approval.approval_manager import ApprovalManager, ApprovalDecisionStore

from src.growth.growth_integration import GrowthIntegrationService


# ============================================================
# 辅助：构造一个"完全合规"的 proposal snapshot（三层都能过）
# ============================================================
def _mk_perfect_snapshot(
    *,
    proposal_id: str = "prop_test_perfect_001",
    evaluator_confidence: float = 0.9,
    evidence_count: int = 5,
    occurrence_count: int = 4,
    proposed_changes: Optional[List[Dict[str, Any]]] = None,
    evaluator_meta: Optional[Dict[str, Any]] = None,
    needs_review: bool = False,
) -> ProposalSnapshot:
    if proposed_changes is None:
        # 小幅度 creativity 提升（非 identity；不跨越 midpoint；delta < reversal 阈值）→ no_conflict
        proposed_changes = [
            {"path": "trait.creativity", "before": 0.6, "after": 0.65, "reason": "多次创作经历支持"},
        ]
    evidence_ids = [f"ev_{i}" for i in range(max(0, evidence_count))]
    meta = dict(evaluator_meta or {})
    meta.setdefault("occurrence_count", occurrence_count)
    return ProposalSnapshot(
        proposal_id=proposal_id,
        evaluator_confidence=evaluator_confidence,
        evidence_ids=evidence_ids,
        proposed_changes=proposed_changes,
        evaluator_meta=meta,
        needs_review=needs_review,
        occurrence_count=occurrence_count,
    )


def _mk_record(
    memory_id: str = "mem_ap001",
    *,
    content: str = "这周我画了两幅插画，设计了一张新角色立绘，完成了水彩练习，坚持创作爱好。",
    importance: float = 0.95,
) -> Dict[str, Any]:
    return {
        "id": memory_id,
        "content": content,
        "user_id": "u_ap_test",
        "role": "user",
        "timestamp": "2026-08-08T10:00:00",
        "importance": importance,
        "memory_class": "preference",
        "metadata": {
            "memory_type": "user_preference",
            "meaning": "user_preference:creative_activity_artistic",
        },
    }


class TestPhase40R253AP12DecisionShapeAndEnum(unittest.TestCase):
    """AP-1 shape 冻结 7 字段 exact；AP-2 decision + reviewer 枚举红线"""

    def test_ap1_keys_exactly_7(self):
        d = build_approval_decision(
            proposal_id="prop_test_a",
            decision="approved",
            reasons=["enough_evidence"],
            confidence=0.8,
        )
        self.assertEqual(set(d.keys()), set(FROZEN_KEYS))
        self.assertEqual(len(FROZEN_KEYS), 7)

    def test_ap1_missing_proposal_id_refused(self):
        with self.assertRaises(ValueError):
            build_approval_decision(
                proposal_id="",   # type: ignore[arg-type]
                decision="approved",
                reasons=["x"],
                confidence=0.1,
            )

    def test_ap1_extra_keyword_refused(self):
        # build_approval_decision 函数签名只接受 (proposal_id,decision,reasons,confidence,reviewer)。
        # 传入 extra 会 TypeError
        with self.assertRaises(TypeError):
            build_approval_decision(
                proposal_id="x",
                decision="approved",
                reasons=["x"],
                confidence=0.1,
                applied=True,  # 非法参数
            )

    def test_ap2_decision_only_3_choices(self):
        for good in ALLOWED_DECISIONS:
            d = build_approval_decision(
                proposal_id=f"prop_{good}",
                decision=good,
                reasons=["x"],
                confidence=0.6,
            )
            self.assertIn(d["decision"], ALLOWED_DECISIONS)
        with self.assertRaises(ValueError):
            build_approval_decision(
                proposal_id="p", decision="accepted",  # 红线：approved vs accepted 不能混（accepted 是 R2.5.4 apply pre-step）
                reasons=["x"], confidence=0.0,
            )
        with self.assertRaises(ValueError):
            build_approval_decision(
                proposal_id="p", decision="applied",
                reasons=["x"], confidence=0.0,
            )

    def test_ap2_reviewer_only_system(self):
        # R2.5.3 只允许 system；未来人类 reviewer 再扩字段
        with self.assertRaises(ValueError):
            build_approval_decision(
                proposal_id="p", decision="deferred",
                reasons=["x"], confidence=0.5,
                reviewer="human_admin",  # type: ignore[arg-type]
            )
        d = build_approval_decision(
            proposal_id="p_sys", decision="deferred",
            reasons=["x"], confidence=0.5,
        )
        self.assertIn(d["reviewer"], ALLOWED_REVIEWERS)

    def test_ap2_empty_reasons_refused(self):
        with self.assertRaises(ValueError):
            build_approval_decision(
                proposal_id="p", decision="approved",
                reasons=[], confidence=0.0,
            )


class TestPhase40R253AP3IdentityLayer(unittest.TestCase):
    """AP-3 Identity 层硬拒绝"""

    def test_ap3_identity_core_path_rejected(self):
        snap = _mk_perfect_snapshot(
            proposed_changes=[
                {"path": "identity.core.name", "before": "羽依", "after": "新名字", "reason": "用户提过"},
            ],
        )
        policy = ApprovalPolicy()
        rec = policy.evaluate(snap)
        self.assertEqual(rec.decision, "rejected")
        self.assertIn("identity_violation_rejected", rec.reasons)

    def test_ap3_identity_origin_path_rejected(self):
        snap = _mk_perfect_snapshot(
            proposed_changes=[
                {"path": "identity.origin.birth_story", "before": "...", "after": "重写出生"},
            ],
        )
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "rejected")


class TestPhase40R253AP456EvidenceLayer(unittest.TestCase):
    """AP-4/5/6 证据不足 → deferred"""

    def test_ap4_few_evidence_ids_deferred(self):
        snap = _mk_perfect_snapshot(evidence_count=1)
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertIn("insufficient_evidence_deferred", rec.reasons)

    def test_ap5_low_confidence_deferred(self):
        # evidence 够但 confidence 低于 DEFAULT 0.75
        snap = _mk_perfect_snapshot(evaluator_confidence=0.6)
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertIn("confidence_below_threshold_deferred", rec.reasons)

    def test_ap6_occurrence_count_1_deferred(self):
        snap = _mk_perfect_snapshot(occurrence_count=1)
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertIn("time_span_too_short_deferred", rec.reasons)


class TestPhase40R253AP789ConflictLayer(unittest.TestCase):
    """AP-7 Transition 保护；AP-8 needs_review；AP-9 跨越 midpoint + 大 delta"""

    def test_ap7_gradual_transition_observation_required(self):
        meta = {"transition_proposal_mode": "gradual_transition"}
        snap = _mk_perfect_snapshot(
            evidence_count=10,
            occurrence_count=6,
            evaluator_confidence=0.98,
            evaluator_meta=meta,
        )
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertIn("interest_transition_gradual_requires_observation", rec.reasons)

    def test_ap8_needs_review_under_review_status_hint(self):
        snap = _mk_perfect_snapshot(needs_review=True)
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertEqual(rec.status_hint, "under_review")
        self.assertIn("conflict_detected_under_review", rec.reasons)

    def test_ap9_reversal_conflict_under_review(self):
        # before=0.7, after=0.35 → 跨 midpoint 0.5，delta=-0.35 >= 0.15
        snap = _mk_perfect_snapshot(
            proposed_changes=[
                {"path": "trait.empathy", "before": 0.7, "after": 0.35, "reason": "似乎减少共情"},
            ],
        )
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "deferred")
        self.assertEqual(rec.status_hint, "under_review")
        self.assertIn("conflict_with_existing_trait_deferred", rec.reasons)


class TestPhase40R253AP10AllLayersApproved(unittest.TestCase):
    """AP-10 三层全过 → approved"""

    def test_ap10_perfect_snapshot_approved(self):
        snap = _mk_perfect_snapshot()
        rec = ApprovalPolicy().evaluate(snap)
        self.assertEqual(rec.decision, "approved")
        for must in (
            "identity_consistent",
            "enough_evidence",
            "no_conflict",
            "governance_policy_approved",
        ):
            self.assertIn(must, rec.reasons, f"approved 必须含 {must}，实际={rec.reasons}")


class TestPhase40R253AP11RedlineNoPersonalityAccess(unittest.TestCase):
    """AP-11 AST 扫描：approval 3 模块 + manager 不包含禁止调用"""

    BAD_TOKENS = [
        "PersonalityState",
        "PersonalityAdapter",
        "accept_proposal",   # proposal_manager 自带的 accept → 必须 R2.5.4 才能调用
        "apply_proposal",
        "personality_adapter",
        "trait_state",
        "GrowthRecord",       # 不直接写成长记录
        ".apply(",            # 任何 apply 调用
    ]

    @staticmethod
    def _clean_module_src(module_name: str) -> str:
        import ast
        import importlib
        import inspect
        mod = importlib.import_module(module_name)
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        # 清空所有 docstring / 字符串常量
        class _Strip(ast.NodeTransformer):
            def visit_Constant(self, n):
                if isinstance(n.value, str):
                    n.value = ""
                return n
        return ast.unparse(_Strip().visit(tree))

    def test_ap11_approval_decision_clean(self):
        s = self._clean_module_src("src.approval.approval_decision")
        for bad in self.BAD_TOKENS:
            self.assertNotIn(bad, s, f"approval_decision 不应包含 {bad}")

    def test_ap11_approval_policy_clean(self):
        s = self._clean_module_src("src.approval.approval_policy")
        for bad in self.BAD_TOKENS:
            self.assertNotIn(bad, s, f"approval_policy 不应包含 {bad}")

    def test_ap11_approval_manager_clean(self):
        s = self._clean_module_src("src.approval.approval_manager")
        # manager 允许 import proposal_manager，但不允许 accept_proposal / apply_proposal 调用
        forbidden = ["accept_proposal(", "apply_proposal(", "PersonalityState", "PersonalityAdapter", "GrowthRecord"]
        for bad in forbidden:
            self.assertNotIn(bad, s, f"approval_manager 不应包含 {bad}")


class TestPhase40R253AP1213141617Integration(unittest.TestCase):
    """
    AP-12/13/14/16/17：manager 更新 proposal.status / 集成到 GrowthIntegration / 审计 / from_states 保护
    """

    def _build_manager_with_stub_proposal(
        self,
        *,
        status: str = "proposed",
        evaluator_confidence: float = 0.95,
        evidence_count: int = 8,
        occurrence_count: int = 5,
        proposed_changes: Optional[List[Dict[str, Any]]] = None,
        evaluator_meta: Optional[Dict[str, Any]] = None,
        needs_review: bool = False,
    ) -> ApprovalManager:
        """构造一个 fake proposal_manager，里面挂一个 GrowthProposal-like 对象 + update_proposal_status_and_meta 方法。"""
        from src.contracts.growth_schema import GrowthProposal, ChangeItem
        changes = []
        for pc in proposed_changes or [
            {"path": "trait.curiosity", "before": 0.6, "after": 0.65, "reason": "多次学习"},
        ]:
            changes.append(ChangeItem(
                path=pc["path"], before=pc.get("before"), after=pc.get("after"),
                reason=pc.get("reason"),
            ))
        em = dict(evaluator_meta or {})
        if needs_review:
            em["needs_review"] = True
        em["occurrence_count"] = occurrence_count
        prop = GrowthProposal(
            id="prop_stub_ap12",
            confidence=evaluator_confidence,
            evidence_ids=[f"e{i}" for i in range(evidence_count)],
            proposed_changes=changes,
            status=status,
            evaluator_meta=em,
        )
        _store: Dict[str, GrowthProposal] = {prop.id: prop}

        class _StubProposalMgr:
            def __init__(self):
                self.store_like = _store

            def get_proposal(self, pid):
                return _store.get(pid)

            def update_proposal_status_and_meta(
                self,
                proposal_id,
                *,
                new_status=None,
                meta_updates=None,
                from_states=None,
            ):
                p = _store.get(proposal_id)
                if p is None:
                    return {"status": "not_found", "proposal": None}
                if from_states and p.status not in list(from_states):
                    return {"status": "skipped_transition_mismatch", "proposal": p}
                if new_status is not None:
                    p.status = new_status
                if meta_updates:
                    ne = dict(p.evaluator_meta or {})
                    ne.update({k: v for k, v in meta_updates.items() if v is not None})
                    p.evaluator_meta = ne
                return {"status": "updated", "proposal": p}

        return ApprovalManager(proposal_manager=_StubProposalMgr())

    def test_ap12_approved_status_not_accepted_nor_applied(self):
        mgr = self._build_manager_with_stub_proposal()
        res = mgr.govern_proposal("prop_stub_ap12")
        self.assertTrue(res.get("governed"), f"治理未应用: {res}")
        self.assertEqual(res.get("decision"), "approved")
        self.assertEqual(res.get("proposal_status"), "approved")  # 红线：必须 approved
        # 不允许变成 accepted 或 applied
        self.assertNotIn(res.get("proposal_status"), {"accepted", "applied"})
        # 再走 GrowthIntegrationService 验证 applied=False（红线）
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        r0 = _mk_record("mem_ap12_w0")
        r1 = _mk_record("mem_ap12_w1")
        r2 = _mk_record("mem_ap12_r2")
        svc.accept_experience(r0)
        svc.accept_experience(r1)
        res2 = svc.accept_experience(r2)
        self.assertIs(res2["applied"], False)

    def test_ap13_reasons_contains_approval_result_tags(self):
        # 降低 occurrence 到 1 → deferred
        mgr = self._build_manager_with_stub_proposal(occurrence_count=1)
        res = mgr.govern_proposal("prop_stub_ap12")
        self.assertEqual(res.get("decision"), "deferred")
        # 真实集成：预热 eligibility 然后调用 accept_experience；若三层没过 → deferred
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        svc.accept_experience(_mk_record("mem_ap13_w0"))
        svc.accept_experience(_mk_record("mem_ap13_w1"))
        real = svc.accept_experience(_mk_record("mem_ap13_r2"))
        self.assertIn("approval_decision_applied", real["reasons"])
        # 真实 run 默认 evidence_count 可能小于 3 → 应 deferred
        self.assertIn("approval_result_deferred", real["reasons"])
        self.assertIs(real["applied"], False)

    def test_ap14_audit_trail_decision_id(self):
        mgr = self._build_manager_with_stub_proposal()
        res = mgr.govern_proposal("prop_stub_ap12")
        self.assertIsNotNone(res.get("decision_id"))
        # proposal.evaluator_meta 里也有
        prop = mgr.proposal_manager.get_proposal("prop_stub_ap12")
        self.assertEqual(prop.evaluator_meta.get("approval_decision_id"), res["decision_id"])
        self.assertEqual(prop.evaluator_meta.get("approval_decision"), "approved")

    def test_ap16_status_mapping(self):
        # Identity 层 → rejected → proposal.status=rejected
        mgr1 = self._build_manager_with_stub_proposal(
            proposed_changes=[{"path": "manifesto.privacy", "before": 1.0, "after": 0.2}],
        )
        r1 = mgr1.govern_proposal("prop_stub_ap12")
        self.assertEqual(r1.get("proposal_status"), "rejected")
        # evidence 少 → deferred
        mgr2 = self._build_manager_with_stub_proposal(evidence_count=1)
        r2 = mgr2.govern_proposal("prop_stub_ap12")
        self.assertEqual(r2.get("proposal_status"), "deferred")
        # needs_review → under_review（status_hint 优先生效，虽然 decision 还是 deferred）
        mgr3 = self._build_manager_with_stub_proposal(needs_review=True)
        r3 = mgr3.govern_proposal("prop_stub_ap12")
        self.assertEqual(r3.get("proposal_status"), "under_review")
        self.assertEqual(r3.get("decision"), "deferred")

    def test_ap17_from_states_prevent_tampering_historical_applied(self):
        # 如果 proposal 已经 applied（历史遗留应用过），治理不应该回滚状态
        mgr = self._build_manager_with_stub_proposal(status="applied")
        res = mgr.govern_proposal("prop_stub_ap12")
        self.assertFalse(res.get("governed"))  # 没 governed
        self.assertEqual(res.get("proposal_status"), "applied")  # 状态不变
        # proposal.evaluator_meta 也不应被塞 approval_decision_id（因为 update 被 from_states 拦了）
        prop = mgr.proposal_manager.get_proposal("prop_stub_ap12")
        self.assertNotIn("approval_decision_id", prop.evaluator_meta)


class TestPhase40R253AP15ExceptionIsolation(unittest.TestCase):
    """AP-15 异常隔离"""

    def test_ap15_govern_crash_does_not_break_pipeline(self):
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        # 预热 2 次让 eligibility 过
        svc.accept_experience(_mk_record("mem_ap15_w0"))
        svc.accept_experience(_mk_record("mem_ap15_w1"))
        from unittest.mock import patch
        # 让 ApprovalPolicy.evaluate 抛出 RuntimeError
        with patch(
            "src.approval.approval_policy.ApprovalPolicy.evaluate",
            side_effect=RuntimeError("模拟治理层崩溃"),
        ):
            real = svc.accept_experience(_mk_record("mem_ap15_r2"))
        # pipeline_state 不应是 error
        self.assertNotEqual(real["pipeline_state"], "error")
        # 红线：applied=False（双重保险）
        self.assertIs(real["applied"], False)


if __name__ == "__main__":
    unittest.main()
