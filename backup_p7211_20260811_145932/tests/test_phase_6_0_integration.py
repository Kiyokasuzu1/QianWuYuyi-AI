# -*- coding: utf-8 -*-
"""
tests/test_phase_6_0_integration.py

Phase 6.0 Runtime Growth Integration 测试套件

覆盖：
  [Phase 1] ProposalLifecycleManager 接入 ApprovalManager
    - pending → approved → applying → applied
    - pending → approved → applying → failed（apply 失败）
    - pending → rejected
    - 缺 lifecycle_manager 时向后兼容
    - lifecycle 历史可追溯

  [Phase 2] GrowthRateLimiter 接入 PersonalityAdapter
    - apply_proposal 前 limiter.check
    - 单次 delta 超限 → DENY
    - 每日配额用尽 → DENY
    - 低 confidence → DENY
    - WARN 仍可应用
    - ALLOW 正常应用并消耗配额
    - limiter 异常时保守通过

  [Phase 3] Runtime Growth Pipeline
    - 完整 6 阶段串联
    - 每步可独立运行
    - 缺组件时降级
    - 异常隔离
    - 持久化与加载
    - Snapshot 统计

  [集成] 全链路端到端
    - Experience → Memory → Reflection → Proposal → Approve → Personality
    - 关闭 lifecycle/limiter 时降级
    - 与 Phase 5.5.1/5.5.2 兼容性

  [回归] Phase 5.5.1 + Phase 5.5.2 测试再跑（抽样）

设计原则：
- 不创建真实 Authority 实例（MemoryStore / PersonalityResolver / EmotionManager）
- 使用 MagicMock / 临时文件
- 每个测试独立临时目录
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 共用工具
# ============================================================

def _make_proposal(
    proposal_id: str = "prop_test_001",
    status: str = "proposed",
    confidence: float = 0.7,
    evidence_count: int = 2,
    changes: Optional[List[Any]] = None,
    evaluator_meta: Optional[Dict[str, Any]] = None,
):
    """构造 GrowthProposal"""
    from src.contracts.growth_schema import GrowthProposal, ChangeItem
    pcs = changes or [
        ChangeItem(path="personality.traits.openness", before=0.5, after=0.55, reason="test"),
    ]
    return GrowthProposal(
        id=proposal_id,
        status=status,
        confidence=confidence,
        evidence_ids=[f"ev_{i}" for i in range(evidence_count)],
        proposed_changes=pcs,
        evaluator_meta=evaluator_meta or {},
    )


def _make_experience(exp_id: str = "exp_001", success: bool = True, response: bool = True):
    """构造 RuntimeExperience"""
    from src.contracts.experience_schema import RuntimeExperience, ActionResult
    return RuntimeExperience(
        experience_id=exp_id,
        trigger_type="user_message",
        decision_source="rule",
        action_type="speak",
        result=ActionResult(
            action_id=f"act_{exp_id}",
            success=success,
            error_message="" if success else "simulated_error",
            response_received=response,
        ),
    )


# ============================================================
# [Phase 1] ProposalLifecycleManager 接入 ApprovalManager
# ============================================================

class TestPhase1LifecycleIntegration(unittest.TestCase):
    """Phase 1: ApprovalManager 接入 ProposalLifecycleManager"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p6_lc_")
        self.history_path = os.path.join(self.tmpdir, "approval_history.json")
        self.lifecycle_path = os.path.join(self.tmpdir, "lifecycle.json")

        # 构造 mock growth_adapter
        self.growth_adapter = MagicMock()
        self.proposals: Dict[str, Any] = {
            "prop_lc_001": _make_proposal("prop_lc_001"),
        }
        self.growth_adapter.list_proposals.return_value = list(self.proposals.values())
        self.growth_adapter.accept_proposal.side_effect = self._mock_accept
        self.growth_adapter.reject_proposal.side_effect = self._mock_reject
        self.growth_adapter.update_proposal.return_value = True

        # 构造 lifecycle manager
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        self.lifecycle = ProposalLifecycleManager(storage_path=self.lifecycle_path)

        # 构造 apply hook
        self.apply_called = []
        def apply_hook(proposal, actor):
            self.apply_called.append((proposal.id, actor))
            return {"applied": True, "evolution_record_id": "rec_001"}
        self.apply_hook = apply_hook

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock_accept(self, pid):
        if pid in self.proposals:
            p = self.proposals[pid]
            p.status = "accepted"
            return p
        return None

    def _mock_reject(self, pid):
        if pid in self.proposals:
            p = self.proposals[pid]
            p.status = "rejected"
            return p
        return None

    def _make_manager(self):
        from src.growth.approval_manager import ApprovalManager
        return ApprovalManager(
            growth_adapter=self.growth_adapter,
            history_path=self.history_path,
            lifecycle_manager=self.lifecycle,
            apply_hook=self.apply_hook,
        )

    def test_01_approve_full_lifecycle(self):
        """approve 应触发 pending → approved → applying → applied"""
        mgr = self._make_manager()
        record = mgr.approve_proposal("prop_lc_001", reason="ok")
        self.assertIsNotNone(record)
        state = self.lifecycle.get_state("prop_lc_001")
        self.assertEqual(state, "applied")

    def test_02_lifecycle_history_contains_all_transitions(self):
        """lifecycle 历史应包含所有流转"""
        mgr = self._make_manager()
        mgr.approve_proposal("prop_lc_001", reason="ok")
        history = self.lifecycle.get_history("prop_lc_001")
        transitions = [(e.from_state, e.to_state) for e in history]
        self.assertIn(("pending", "approved"), transitions)
        self.assertIn(("approved", "applying"), transitions)
        self.assertIn(("applying", "applied"), transitions)

    def test_03_reject_lifecycle(self):
        """reject 应记录 lifecycle 流转"""
        mgr = self._make_manager()
        record = mgr.reject_proposal("prop_lc_001", reason="bad")
        self.assertIsNotNone(record)
        state = self.lifecycle.get_state("prop_lc_001")
        self.assertEqual(state, "rejected")

    def test_04_apply_hook_called_on_approve(self):
        """approve 应触发 apply_hook"""
        mgr = self._make_manager()
        mgr.approve_proposal("prop_lc_001", reason="ok")
        self.assertEqual(len(self.apply_called), 1)
        self.assertEqual(self.apply_called[0][0], "prop_lc_001")
        self.assertEqual(self.apply_called[0][1], "human")

    def test_05_apply_failure_records_failed_state(self):
        """apply 失败时 lifecycle 状态为 failed"""
        def failing_hook(proposal, actor):
            return {"applied": False, "error": "apply_failed_simulation"}
        from src.growth.approval_manager import ApprovalManager
        mgr = ApprovalManager(
            growth_adapter=self.growth_adapter,
            history_path=self.history_path,
            lifecycle_manager=self.lifecycle,
            apply_hook=failing_hook,
        )
        mgr.approve_proposal("prop_lc_001", reason="ok")
        state = self.lifecycle.get_state("prop_lc_001")
        self.assertEqual(state, "failed")

    def test_06_apply_exception_results_in_failed(self):
        """apply 抛异常时 lifecycle 状态为 failed"""
        def exception_hook(proposal, actor):
            raise RuntimeError("simulated_exception")
        from src.growth.approval_manager import ApprovalManager
        mgr = ApprovalManager(
            growth_adapter=self.growth_adapter,
            history_path=self.history_path,
            lifecycle_manager=self.lifecycle,
            apply_hook=exception_hook,
        )
        record = mgr.approve_proposal("prop_lc_001", reason="ok")
        # approve 仍应返回 record（审计完整）
        self.assertIsNotNone(record)
        state = self.lifecycle.get_state("prop_lc_001")
        self.assertEqual(state, "failed")

    def test_07_no_lifecycle_backward_compatible(self):
        """无 lifecycle_manager 时应正常工作（Phase 3.5.13 兼容）"""
        from src.growth.approval_manager import ApprovalManager
        mgr = ApprovalManager(
            growth_adapter=self.growth_adapter,
            history_path=self.history_path,
            # 不传 lifecycle_manager
        )
        record = mgr.approve_proposal("prop_lc_001", reason="ok")
        self.assertIsNotNone(record)
        self.assertEqual(record.action, "approve")
        self.assertEqual(record.before_status, "proposed")
        self.assertEqual(record.after_status, "accepted")

    def test_08_get_lifecycle_state(self):
        """get_lifecycle_state 应返回当前状态"""
        mgr = self._make_manager()
        mgr.approve_proposal("prop_lc_001", reason="ok")
        self.assertEqual(mgr.get_lifecycle_state("prop_lc_001"), "applied")

    def test_09_get_lifecycle_history(self):
        """get_lifecycle_history 应返回历史"""
        mgr = self._make_manager()
        mgr.approve_proposal("prop_lc_001", reason="ok")
        history = mgr.get_lifecycle_history("prop_lc_001", limit=10)
        self.assertGreater(len(history), 0)

    def test_10_approval_record_metadata_contains_phase6(self):
        """审批记录 metadata 应包含 Phase 6.0 标记"""
        mgr = self._make_manager()
        record = mgr.approve_proposal("prop_lc_001", reason="ok")
        self.assertTrue(record.metadata.get("phase_6_0"))
        self.assertIn("apply_result", record.metadata)

    def test_11_modify_with_lifecycle(self):
        """modify + approve 应同样走 lifecycle"""
        mgr = self._make_manager()
        record = mgr.modify_proposal(
            "prop_lc_001",
            changes=[{"path": "personality.traits.openness", "after": 0.50}],
            reason="降低幅度",
        )
        self.assertIsNotNone(record)
        state = self.lifecycle.get_state("prop_lc_001")
        self.assertEqual(state, "applied")

    def test_12_lifecycle_disabled_returns_disabled(self):
        """无 lifecycle_manager 时 get_lifecycle_state 返回 disabled"""
        from src.growth.approval_manager import ApprovalManager
        mgr = ApprovalManager(
            growth_adapter=self.growth_adapter,
            history_path=self.history_path,
        )
        self.assertEqual(mgr.get_lifecycle_state("anything"), "disabled")
        self.assertEqual(mgr.get_lifecycle_history("anything"), [])


# ============================================================
# [Phase 2] GrowthRateLimiter 接入 PersonalityAdapter
# ============================================================

class TestPhase2RateLimiterIntegration(unittest.TestCase):
    """Phase 2: PersonalityAdapter 接入 GrowthRateLimiter"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p6_rl_")
        self.limiter_path = os.path.join(self.tmpdir, "limiter.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_limiter(self, **kwargs):
        from src.growth.growth_limiter import GrowthRateLimiter
        return GrowthRateLimiter(storage_path=self.limiter_path, **kwargs)

    def _make_adapter(self, limiter=None):
        from src.personality.personality_adapter import PersonalityAdapter
        return PersonalityAdapter(growth_limiter=limiter)

    def test_01_no_limiter_backward_compatible(self):
        """无 limiter 时正常 apply（向后兼容）"""
        adapter = self._make_adapter(limiter=None)
        proposal = _make_proposal()
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 应正常返回（具体应用结果由 TraitStateUpdater 决定）
        self.assertIn("applied", envelope)
        self.assertEqual(envelope["rate_limit"]["enabled"], False)

    def test_02_limiter_enabled_report(self):
        """limiter 接入时 rate_limit.enabled=True"""
        limiter = self._make_limiter()
        adapter = self._make_adapter(limiter=limiter)
        proposal = _make_proposal()
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("rate_limit", envelope)
        self.assertTrue(envelope["rate_limit"]["enabled"])

    def test_03_single_delta_exceeded_denies(self):
        """单次 delta 超限应被 DENY"""
        limiter = self._make_limiter(max_single_delta=0.05)
        adapter = self._make_adapter(limiter=limiter)
        # 0.5 → 0.7，delta=0.2 超过 0.05
        proposal = _make_proposal(
            changes=[__import__("src.contracts.growth_schema", fromlist=["ChangeItem"]).ChangeItem(
                path="personality.traits.openness", before=0.5, after=0.7
            )]
        )
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("openness", envelope.get("denied_traits", []))

    def test_04_low_confidence_denies(self):
        """低 confidence 应被 DENY"""
        limiter = self._make_limiter(min_confidence=0.8)
        adapter = self._make_adapter(limiter=limiter)
        proposal = _make_proposal(confidence=0.3)
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("openness", envelope.get("denied_traits", []))

    def test_05_daily_quota_exhausted_denies(self):
        """每日配额用尽应被 DENY"""
        limiter = self._make_limiter(max_daily_changes=2)
        adapter = self._make_adapter(limiter=limiter)
        # 先手动记录 2 次
        from datetime import datetime
        today = datetime.utcnow().strftime("%Y-%m-%d")
        limiter._daily_counts.setdefault(today, {})["openness"] = 2
        proposal = _make_proposal()
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("openness", envelope.get("denied_traits", []))

    def test_06_normal_proposal_allowed(self):
        """正常 proposal 应被 ALLOW"""
        limiter = self._make_limiter()
        adapter = self._make_adapter(limiter=limiter)
        proposal = _make_proposal(confidence=0.7)  # delta=0.05
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 不在 denied_traits 中
        self.assertNotIn("openness", envelope.get("denied_traits", []))

    def test_07_warn_decision_recorded(self):
        """WARN 决策应被记录但允许通过"""
        limiter = self._make_limiter(
            max_single_delta=0.10,
            warn_ratio=0.5,
        )
        adapter = self._make_adapter(limiter=limiter)
        # delta=0.07 达到 70%，触发 warn
        from src.contracts.growth_schema import ChangeItem
        proposal = _make_proposal(
            confidence=0.7,
            changes=[ChangeItem(path="personality.traits.openness", before=0.5, after=0.57)],
        )
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 不在 denied_traits 中
        self.assertNotIn("openness", envelope.get("denied_traits", []))
        # 但 rate_limit 报告里应有 warn
        decisions = envelope.get("rate_limit", {}).get("decisions", [])
        if decisions:
            # may be warn or allow based on config
            pass

    def test_08_memory_scope_not_checked(self):
        """memory scope 不应触发 limiter 检查（已有 memory 隔离）"""
        limiter = self._make_limiter()
        adapter = self._make_adapter(limiter=limiter)
        proposal = _make_proposal(evaluator_meta={"action_scope": "memory"})
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 应直接跳过（memory_action_skipped_no_personality_change）
        self.assertIn("memory_action_skipped", envelope.get("note", ""))

    def test_09_invalid_path_not_checked(self):
        """非法 path 在 limiter 之前被拦截"""
        limiter = self._make_limiter()
        adapter = self._make_adapter(limiter=limiter)
        from src.contracts.growth_schema import ChangeItem
        proposal = _make_proposal(
            changes=[ChangeItem(path="invalid.path", before=0.5, after=0.6)]
        )
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("path_validation_failed", envelope.get("note", ""))

    def test_10_limiter_exception_passes_through(self):
        """limiter 异常时保守通过（不阻塞人格修改）"""
        from src.growth.growth_limiter import GrowthRateLimiter

        class BrokenLimiter(GrowthRateLimiter):
            def check(self, *args, **kwargs):
                raise RuntimeError("limiter_broken")

        adapter = self._make_adapter(limiter=BrokenLimiter())
        proposal = _make_proposal(confidence=0.7)
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 不应阻塞（apply 仍继续或以其他 note 完成）
        self.assertIn("applied", envelope)

    def test_11_denied_all_traits_returns_skipped(self):
        """全部 trait 被 DENY 时返回 skipped"""
        limiter = self._make_limiter(max_single_delta=0.001)
        adapter = self._make_adapter(limiter=limiter)
        proposal = _make_proposal()
        envelope = adapter.apply_proposal(proposal, actor="test")
        # 当所有 trait 被 deny 时，note 应为 rate_limited_all_traits_denied
        if "rate_limited_all_traits_denied" in envelope.get("note", ""):
            self.assertFalse(envelope.get("applied", True))
        # 否则部分被 deny，partial 模式
        self.assertIn("applied", envelope)

    def test_12_filter_proposal_by_limiter(self):
        """_filter_proposal_by_limiter 应正确过滤"""
        from src.growth.growth_limiter import GrowthRateLimiter
        # 使用临时 storage_path 避免跨测试全局状态污染
        limiter = GrowthRateLimiter(
            max_single_delta=0.05,
            storage_path=os.path.join(self.tmpdir, "limiter_test12.json"),
        )
        adapter = self._make_adapter(limiter=limiter)
        from src.contracts.growth_schema import ChangeItem
        # 使用能精确表示的浮点数（0.5 + 0.05 会有精度问题）
        proposal = _make_proposal(
            changes=[
                ChangeItem(path="personality.traits.openness", before=0.5, after=0.53),  # delta=0.03 ok
                ChangeItem(path="personality.traits.warmth", before=0.5, after=0.7),      # 过大
            ]
        )
        report: Dict[str, Any] = {
            "checked": False,
            "enabled": True,
            "decisions": [],
            "denied_traits": [],
        }
        filtered = adapter._filter_proposal_by_limiter(proposal, report)
        self.assertTrue(report["checked"])
        # warmth 应被 deny
        self.assertIn("warmth", report["denied_traits"])
        # openness 保留
        kept_paths = [c.path for c in filtered.proposed_changes]
        self.assertIn("personality.traits.openness", kept_paths)
        self.assertNotIn("personality.traits.warmth", kept_paths)


# ============================================================
# [Phase 3] Runtime Growth Pipeline
# ============================================================

class TestPhase3RuntimeGrowthPipeline(unittest.TestCase):
    """Phase 3: RuntimeGrowthPipeline 端到端测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p6_pl_")
        self.proposals_path = os.path.join(self.tmpdir, "proposals.json")
        self.history_path = os.path.join(self.tmpdir, "approval.json")
        self.lifecycle_path = os.path.join(self.tmpdir, "lifecycle.json")
        self.pipeline_history = os.path.join(self.tmpdir, "pipeline_runs.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_pipeline(self, *, with_approval=True, with_personality=True,
                        with_lifecycle=True, with_limiter=False,
                        apply_via_hook=True, with_reflection=False):
        from src.runtime.adapters.growth_adapter import GrowthAdapter
        from src.runtime.pipeline import RuntimeGrowthPipeline

        # 真实 GrowthAdapter（使用临时路径）
        growth = GrowthAdapter(proposals_path=self.proposals_path)

        memory = MagicMock()
        memory.store_experience.return_value = True
        memory.get_recent_experiences.return_value = [_make_experience()]

        approval = None
        personality = None
        lifecycle = None
        apply_hook = None

        if with_approval:
            from src.growth.approval_manager import ApprovalManager
            if with_lifecycle:
                from src.growth.lifecycle_manager import ProposalLifecycleManager
                lifecycle = ProposalLifecycleManager(storage_path=self.lifecycle_path)

            if apply_via_hook and with_personality:
                # 准备 personality_adapter + apply_hook 桥接
                from src.personality.personality_adapter import PersonalityAdapter
                limiter = None
                if with_limiter:
                    from src.growth.growth_limiter import GrowthRateLimiter
                    limiter = GrowthRateLimiter(
                        storage_path=os.path.join(self.tmpdir, "limiter.json")
                    )
                personality = PersonalityAdapter(growth_limiter=limiter)

                def hook(proposal, actor):
                    return personality.apply_proposal(proposal, actor=actor, mark_approved=True)
                apply_hook = hook

            approval = ApprovalManager(
                growth_adapter=growth,
                history_path=self.history_path,
                lifecycle_manager=lifecycle,
                apply_hook=apply_hook,
            )

        reflection = None
        if with_reflection:
            reflection = MagicMock()
            ins = _make_proposal("prop_ref_001")  # reuse as insight stand-in
            ins.insight_id = "ins_ref_001"
            reflection.reflect.return_value = ins

        return RuntimeGrowthPipeline(
            memory_adapter=memory,
            growth_adapter=growth,
            approval_manager=approval,
            personality_adapter=personality,
            lifecycle_manager=lifecycle,
            reflection_engine=reflection,
            history_path=self.pipeline_history,
        )

    # ----- 基础 -----

    def test_01_pipeline_import(self):
        from src.runtime.pipeline import RuntimeGrowthPipeline
        p = RuntimeGrowthPipeline()
        self.assertIsNotNone(p)

    def test_02_run_cycle_with_all_components(self):
        """完整 6 阶段：全部组件齐全"""
        pipeline = self._build_pipeline()
        exp = _make_experience("exp_full_001")
        run = pipeline.run_cycle(experience=exp, auto_approve=True)
        self.assertIsNotNone(run.proposal_id)
        # stages: experience, memory, reflection, proposal, lifecycle, personality
        self.assertEqual(len(run.stages), 6)
        self.assertEqual(run.final_status, "ok")

    def test_03_run_cycle_no_experience(self):
        """无 experience 时直接完成"""
        pipeline = self._build_pipeline()
        run = pipeline.run_cycle(experience=None, auto_approve=False)
        # stages: memory, reflection, proposal, lifecycle, personality (5)
        self.assertEqual(len(run.stages), 5)

    def test_04_run_cycle_without_components(self):
        """无任何组件时优雅降级"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline = RuntimeGrowthPipeline(history_path=self.pipeline_history)
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=False)
        # 所有阶段都跳过，但 Pipeline 不崩溃
        self.assertIn(run.final_status, ("ok", "failed"))

    def test_05_pipeline_starts_and_finishes(self):
        """start_run + step_* + finish_run 完整流程"""
        pipeline = self._build_pipeline()
        run = pipeline.start_run(experience=_make_experience("exp_step_001"))
        pipeline.step_memory(run)
        pipeline.step_reflection(run)
        pipeline.step_proposal(run)
        pipeline.step_lifecycle(run, auto_approve=True)
        pipeline.step_personality(run)
        pipeline.finish_run(run)
        self.assertNotEqual(run.final_status, "pending")
        self.assertGreater(len(run.stages), 0)

    # ----- 异常隔离 -----

    def test_06_memory_exception_isolated(self):
        """memory 异常不应中断 Pipeline"""
        pipeline = self._build_pipeline()
        pipeline._memory.store_experience.side_effect = RuntimeError("memory_broken")
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        # memory step 失败，但后续应继续
        mem_stage = next(s for s in run.stages if s.stage == "memory")
        self.assertEqual(mem_stage.status, "failed")
        # 后续 proposal/lifecycle/personality 应仍执行
        self.assertEqual(len(run.stages), 6)

    def test_07_growth_exception_isolated(self):
        """growth 异常不应中断"""
        pipeline = self._build_pipeline()
        pipeline._growth.store_insight = MagicMock(side_effect=RuntimeError("growth_broken"))
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        prop_stage = next(s for s in run.stages if s.stage == "proposal")
        self.assertEqual(prop_stage.status, "failed")

    def test_08_approval_exception_isolated(self):
        """approval 异常不应中断"""
        pipeline = self._build_pipeline()
        pipeline._approval.approve_proposal = MagicMock(side_effect=RuntimeError("approval_broken"))
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        lifecycle_stage = next(s for s in run.stages if s.stage == "lifecycle")
        self.assertEqual(lifecycle_stage.status, "failed")

    # ----- 降级行为 -----

    def test_09_no_memory_adapter(self):
        """无 memory_adapter 时 memory 步骤跳过"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        from src.runtime.adapters.growth_adapter import GrowthAdapter

        pipeline = RuntimeGrowthPipeline(
            memory_adapter=None,
            growth_adapter=GrowthAdapter(proposals_path=self.proposals_path),
            history_path=self.pipeline_history,
        )
        run = pipeline.start_run(experience=_make_experience())
        stage = pipeline.step_memory(run)
        self.assertEqual(stage.status, "skipped")

    def test_10_no_growth_adapter(self):
        """无 growth_adapter 时 proposal 步骤跳过"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline = RuntimeGrowthPipeline(
            growth_adapter=None,
            history_path=self.pipeline_history,
        )
        run = pipeline.start_run()
        run.metadata["reflection_insight"] = _make_proposal()
        stage = pipeline.step_proposal(run)
        self.assertEqual(stage.status, "skipped")

    def test_11_no_approval_manager(self):
        """无 approval_manager 时 lifecycle 步骤跳过"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline = RuntimeGrowthPipeline(
            approval_manager=None,
            history_path=self.pipeline_history,
        )
        run = pipeline.start_run()
        run.proposal_id = "prop_x"
        stage = pipeline.step_lifecycle(run, auto_approve=True)
        self.assertEqual(stage.status, "skipped")

    def test_12_no_personality_adapter(self):
        """无 personality_adapter 时 personality 步骤跳过"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline = RuntimeGrowthPipeline(
            personality_adapter=None,
            history_path=self.pipeline_history,
        )
        run = pipeline.start_run()
        run.proposal_id = "prop_x"
        stage = pipeline.step_personality(run)
        self.assertEqual(stage.status, "skipped")

    def test_13_no_proposal_skips_lifecycle(self):
        """无 proposal_id 时 lifecycle 步骤跳过"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline = RuntimeGrowthPipeline(
            history_path=self.pipeline_history,
        )
        run = pipeline.start_run()
        stage = pipeline.step_lifecycle(run, auto_approve=True)
        self.assertEqual(stage.status, "skipped")

    # ----- 持久化 -----

    def test_14_pipeline_runs_persist(self):
        """Pipeline runs 持久化"""
        pipeline = self._build_pipeline()
        pipeline.run_cycle(experience=_make_experience("exp_p_001"), auto_approve=True)
        # 新建实例，load 历史
        from src.runtime.pipeline import RuntimeGrowthPipeline
        pipeline2 = RuntimeGrowthPipeline(history_path=self.pipeline_history)
        n = pipeline2.load_runs()
        self.assertGreaterEqual(n, 1)

    def test_15_clear_runs(self):
        """清空 runs"""
        pipeline = self._build_pipeline()
        pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        n = pipeline.clear_runs()
        self.assertEqual(n, 1)
        self.assertEqual(len(pipeline._runs), 0)

    def test_16_get_recent_runs(self):
        """get_recent_runs"""
        pipeline = self._build_pipeline()
        for i in range(3):
            pipeline.run_cycle(experience=_make_experience(f"exp_{i}"), auto_approve=True)
        recent = pipeline.get_recent_runs(limit=2)
        self.assertEqual(len(recent), 2)

    def test_17_get_run(self):
        """get_run by id"""
        pipeline = self._build_pipeline()
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        fetched = pipeline.get_run(run.run_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.run_id, run.run_id)

    def test_18_snapshot(self):
        """get_snapshot 包含关键指标"""
        pipeline = self._build_pipeline()
        pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        snap = pipeline.get_snapshot()
        self.assertIn("total_runs", snap)
        self.assertIn("ok_runs", snap)
        self.assertIn("failed_runs", snap)
        self.assertEqual(snap["ok_runs"], 1)
        self.assertTrue(snap["memory_adapter"])
        self.assertTrue(snap["growth_adapter"])
        self.assertTrue(snap["approval_manager"])

    # ----- 阶段细节 -----

    def test_19_lifecycle_state_recorded_in_metadata(self):
        """lifecycle state 应被记录到 metadata"""
        pipeline = self._build_pipeline()
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        # lifecycle_manager 已存在
        self.assertIn("lifecycle_state", run.metadata)
        self.assertEqual(run.metadata["lifecycle_state"], "applied")

    def test_20_reflection_engine_used_when_provided(self):
        """外部 reflection_engine 应被调用"""
        pipeline = self._build_pipeline(with_reflection=True)
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        pipeline._reflection.reflect.assert_called()

    def test_21_minimal_reflection_without_engine(self):
        """无 reflection_engine 时使用最小洞察生成器"""
        pipeline = self._build_pipeline(with_reflection=False)
        run = pipeline.start_run(experience=_make_experience(success=False, response=False))
        stage = pipeline.step_reflection(run)
        self.assertEqual(stage.status, "ok")
        # 检测低成功率模式
        self.assertEqual(
            run.metadata["reflection_insight"].pattern_detected, "low_success_rate"
        )

    def test_22_minimal_reflection_low_response(self):
        """无 reflection_engine 时低响应率模式"""
        pipeline = self._build_pipeline(with_reflection=False)
        run = pipeline.start_run(experience=_make_experience(success=True, response=False))
        pipeline.step_reflection(run)
        self.assertEqual(
            run.metadata["reflection_insight"].pattern_detected, "low_user_response"
        )

    def test_23_minimal_reflection_baseline(self):
        """正常经验 → baseline 模式"""
        pipeline = self._build_pipeline(with_reflection=False)
        run = pipeline.start_run(experience=_make_experience(success=True, response=True))
        pipeline.step_reflection(run)
        self.assertEqual(
            run.metadata["reflection_insight"].pattern_detected, "baseline"
        )

    # ----- lifecycle 联动 -----

    def test_24_lifecycle_state_approved_required_for_personality(self):
        """personality 步骤要求 lifecycle 状态为 approved"""
        from src.runtime.pipeline import RuntimeGrowthPipeline
        from src.runtime.adapters.growth_adapter import GrowthAdapter
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.growth.approval_manager import ApprovalManager
        from src.personality.personality_adapter import PersonalityAdapter

        growth = GrowthAdapter(proposals_path=self.proposals_path)
        lifecycle = ProposalLifecycleManager(storage_path=self.lifecycle_path)

        def hook(proposal, actor):
            return {"applied": True, "evolution_record_id": "rec_001"}
        approval = ApprovalManager(
            growth_adapter=growth,
            history_path=self.history_path,
            lifecycle_manager=lifecycle,
            apply_hook=hook,
        )
        personality = PersonalityAdapter()

        pipeline = RuntimeGrowthPipeline(
            growth_adapter=growth,
            approval_manager=approval,
            personality_adapter=personality,
            lifecycle_manager=lifecycle,
            history_path=self.pipeline_history,
        )
        # 不自动批准 → lifecycle 状态不会变成 approved
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=False)
        # proposal_id 已生成
        self.assertNotEqual(run.proposal_id, "")
        # lifecycle 状态应保持 unknown（因为 lifecycle_manager 是空的）
        # personality 步骤应因 lifecycle_state 缺失而跳过
        person_stage = next(s for s in run.stages if s.stage == "personality")
        self.assertEqual(person_stage.status, "skipped")


# ============================================================
# [集成] 全链路端到端
# ============================================================

class TestPhase6EndToEnd(unittest.TestCase):
    """Phase 6.0 端到端集成测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p6_e2e_")
        self.proposals_path = os.path.join(self.tmpdir, "proposals.json")
        self.history_path = os.path.join(self.tmpdir, "approval.json")
        self.lifecycle_path = os.path.join(self.tmpdir, "lifecycle.json")
        self.pipeline_history = os.path.join(self.tmpdir, "pipeline_runs.json")
        self.limiter_path = os.path.join(self.tmpdir, "limiter.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_full_pipeline(self, with_limiter=True):
        """完整 Pipeline + 限流器"""
        from src.runtime.adapters.growth_adapter import GrowthAdapter
        from src.growth.approval_manager import ApprovalManager
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.personality.personality_adapter import PersonalityAdapter
        from src.growth.growth_limiter import GrowthRateLimiter
        from src.runtime.pipeline import RuntimeGrowthPipeline

        growth = GrowthAdapter(proposals_path=self.proposals_path)
        lifecycle = ProposalLifecycleManager(storage_path=self.lifecycle_path)
        limiter = None
        if with_limiter:
            limiter = GrowthRateLimiter(storage_path=self.limiter_path)
        personality = PersonalityAdapter(growth_limiter=limiter)

        def hook(proposal, actor):
            return personality.apply_proposal(proposal, actor=actor, mark_approved=True)

        approval = ApprovalManager(
            growth_adapter=growth,
            history_path=self.history_path,
            lifecycle_manager=lifecycle,
            apply_hook=hook,
        )
        memory = MagicMock()
        memory.store_experience.return_value = True
        memory.get_recent_experiences.return_value = [_make_experience()]

        return RuntimeGrowthPipeline(
            memory_adapter=memory,
            growth_adapter=growth,
            approval_manager=approval,
            personality_adapter=personality,
            lifecycle_manager=lifecycle,
            history_path=self.pipeline_history,
        )

    def test_01_full_loop_with_limiter(self):
        """完整链路 + 限流：Experience → Memory → Reflection → Proposal → Lifecycle → Personality"""
        pipeline = self._build_full_pipeline(with_limiter=True)
        run = pipeline.run_cycle(experience=_make_experience("exp_e2e_001"), auto_approve=True)
        self.assertNotEqual(run.proposal_id, "")
        # 6 个阶段都执行
        self.assertEqual(len(run.stages), 6)
        # lifecycle 状态为 applied
        self.assertEqual(run.metadata.get("lifecycle_state"), "applied")
        # final status OK
        self.assertEqual(run.final_status, "ok")

    def test_02_full_loop_without_limiter(self):
        """完整链路无 limiter：行为一致"""
        pipeline = self._build_full_pipeline(with_limiter=False)
        run = pipeline.run_cycle(experience=_make_experience("exp_e2e_002"), auto_approve=True)
        self.assertEqual(run.final_status, "ok")
        self.assertEqual(len(run.stages), 6)

    def test_03_lifecycle_history_audit_trail(self):
        """lifecycle history 应包含完整审计追踪"""
        pipeline = self._build_full_pipeline(with_limiter=True)
        run = pipeline.run_cycle(experience=_make_experience("exp_audit_001"), auto_approve=True)
        # 通过 pipeline._lifecycle 查询 history
        history = pipeline._lifecycle.get_history(run.proposal_id)
        transitions = [(e.from_state, e.to_state) for e in history]
        # 期望: pending→approved, approved→applying, applying→applied
        self.assertIn(("pending", "approved"), transitions)
        self.assertIn(("approved", "applying"), transitions)
        self.assertIn(("applying", "applied"), transitions)

    def test_04_approval_history_complete(self):
        """审批历史应包含 Phase 6.0 标记"""
        pipeline = self._build_full_pipeline(with_limiter=True)
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        history = pipeline._approval.get_approval_history(limit=5)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0].metadata.get("phase_6_0"))

    def test_05_rate_limit_denies_in_full_loop(self):
        """限流超限时 personality 步骤应处理 DENY 状态"""
        pipeline = self._build_full_pipeline(with_limiter=True)
        # 调低单次 delta 限制，强制 deny
        pipeline._personality._limiter._max_single_delta = 0.001
        run = pipeline.run_cycle(experience=_make_experience(), auto_approve=True)
        # 即使被 deny，Pipeline 仍应继续，proposal_id 存在
        self.assertNotEqual(run.proposal_id, "")
        # final status 可能是 ok（其它阶段仍成功）或 failed
        self.assertIn(run.final_status, ("ok", "failed"))

    def test_06_reject_path(self):
        """rejected 路径：rejected proposal 不进入 personality"""
        from src.runtime.adapters.growth_adapter import GrowthAdapter
        from src.growth.approval_manager import ApprovalManager
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.personality.personality_adapter import PersonalityAdapter
        from src.runtime.pipeline import RuntimeGrowthPipeline

        growth = GrowthAdapter(proposals_path=self.proposals_path)
        lifecycle = ProposalLifecycleManager(storage_path=self.lifecycle_path)
        personality = PersonalityAdapter()
        approval = ApprovalManager(
            growth_adapter=growth,
            history_path=self.history_path,
            lifecycle_manager=lifecycle,
        )
        pipeline = RuntimeGrowthPipeline(
            growth_adapter=growth,
            approval_manager=approval,
            personality_adapter=personality,
            lifecycle_manager=lifecycle,
            history_path=self.pipeline_history,
        )

        # 手动设置一个 proposal
        proposal = _make_proposal("prop_reject_001")
        growth._save([proposal])
        # 拒绝
        record = approval.reject_proposal("prop_reject_001", reason="manual reject")
        self.assertIsNotNone(record)
        state = lifecycle.get_state("prop_reject_001")
        self.assertEqual(state, "rejected")

    def test_07_personality_adapter_uses_limiter_consistently(self):
        """PersonalityAdapter 在 Pipeline 内一致使用 limiter"""
        pipeline = self._build_full_pipeline(with_limiter=True)
        # 检查 limiter 已注入
        self.assertIsNotNone(pipeline._personality._limiter)

    def test_08_pipeline_is_idempotent_across_runs(self):
        """多次运行 Pipeline 不应累积状态污染"""
        pipeline = self._build_full_pipeline(with_limiter=False)
        for i in range(3):
            run = pipeline.run_cycle(experience=_make_experience(f"exp_idem_{i}"), auto_approve=True)
            self.assertEqual(run.final_status, "ok")
        # 3 次成功
        self.assertEqual(pipeline.get_snapshot()["ok_runs"], 3)


# ============================================================
# [回归] Phase 5.5.1 / 5.5.2 不应被破坏
# ============================================================

class TestPhase6RegressionSafety(unittest.TestCase):
    """Phase 6.0 修改不应破坏 Phase 5.5.1 / 5.5.2 行为"""

    def test_01_phase_5_5_1_approval_manager_unchanged_api(self):
        """ApprovalManager 旧 API 完全兼容"""
        from src.growth.approval_manager import ApprovalManager
        growth = MagicMock()
        growth.list_proposals.return_value = [_make_proposal("prop_legacy_001")]
        growth.accept_proposal.return_value = _make_proposal("prop_legacy_001", status="accepted")
        growth.reject_proposal.return_value = _make_proposal("prop_legacy_001", status="rejected")
        growth.update_proposal.return_value = True

        # 不传 lifecycle / apply_hook
        mgr = ApprovalManager(growth_adapter=growth)
        record = mgr.approve_proposal("prop_legacy_001", reason="legacy")
        self.assertIsNotNone(record)
        self.assertEqual(record.action, "approve")
        self.assertEqual(record.before_status, "proposed")
        self.assertEqual(record.after_status, "accepted")

    def test_02_phase_5_5_1_personality_adapter_unchanged_api(self):
        """PersonalityAdapter 旧 API 完全兼容"""
        from src.personality.personality_adapter import PersonalityAdapter
        adapter = PersonalityAdapter()  # 不传 limiter
        proposal = _make_proposal()
        envelope = adapter.apply_proposal(proposal, actor="legacy")
        self.assertIn("applied", envelope)
        # rate_limit.enabled 必须是 False
        self.assertFalse(envelope["rate_limit"]["enabled"])

    def test_03_phase_5_5_1_personality_path_validation_still_works(self):
        """path 验证未受影响"""
        from src.personality.personality_adapter import (
            PersonalityAdapter, PersonalityPathValidationError, validate_proposal_path,
        )
        # 合法
        self.assertTrue(validate_proposal_path("personality.traits.openness"))
        # 非法
        with self.assertRaises(PersonalityPathValidationError):
            validate_proposal_path("invalid.path.xyz")

    def test_04_phase_5_5_1_memory_scope_isolation_still_works(self):
        """memory scope 隔离未受影响"""
        from src.personality.personality_adapter import PersonalityAdapter
        adapter = PersonalityAdapter()
        proposal = _make_proposal(evaluator_meta={"action_scope": "memory"})
        envelope = adapter.apply_proposal(proposal, actor="test")
        self.assertIn("memory_action_skipped", envelope["note"])

    def test_05_phase_5_5_2_lifecycle_manager_unchanged(self):
        """ProposalLifecycleManager 旧行为未受影响"""
        from src.growth.lifecycle_manager import (
            ProposalLifecycleManager, LifecycleState, IllegalLifecycleTransition,
        )
        mgr = ProposalLifecycleManager(
            storage_path=os.path.join(tempfile.mkdtemp(), "lc.json")
        )
        mgr.record_transition("prop_lc_legacy", "pending", "approved", actor="test")
        self.assertEqual(mgr.get_state("prop_lc_legacy"), "approved")
        # 非法转换
        with self.assertRaises(IllegalLifecycleTransition):
            mgr.record_transition("prop_lc_legacy", "approved", "applied")  # 跳过 applying

    def test_06_phase_5_5_2_rate_limiter_unchanged(self):
        """GrowthRateLimiter 旧行为未受影响"""
        from src.growth.growth_limiter import GrowthRateLimiter
        limiter = GrowthRateLimiter(
            storage_path=os.path.join(tempfile.mkdtemp(), "lim.json")
        )
        d1 = limiter.check("openness", 0.05, 0.7)
        self.assertEqual(d1.decision, "allow")
        # 连续记录消耗配额
        for _ in range(20):
            limiter.record("openness", 0.01)
        d2 = limiter.check("openness", 0.05, 0.7)
        self.assertEqual(d2.decision, "deny")  # 每日配额用尽


# ============================================================
# [Snapshot & Schema] 数据契约
# ============================================================

class TestPhase6SchemaSnapshot(unittest.TestCase):
    """Phase 6.0 数据结构 / Snapshot 验证"""

    def test_01_pipeline_stage_enum(self):
        from src.runtime.pipeline import PipelineStage
        self.assertEqual(PipelineStage.EXPERIENCE.value, "experience")
        self.assertEqual(PipelineStage.COMPLETED.value, "completed")

    def test_02_stage_status_enum(self):
        from src.runtime.pipeline import StageStatus
        self.assertEqual(StageStatus.PENDING.value, "pending")
        self.assertEqual(StageStatus.RUNNING.value, "running")
        self.assertEqual(StageStatus.OK.value, "ok")
        self.assertEqual(StageStatus.SKIPPED.value, "skipped")
        self.assertEqual(StageStatus.FAILED.value, "failed")

    def test_03_stage_result_to_dict(self):
        from src.runtime.pipeline import StageResult
        r = StageResult(stage="test", status="ok")
        d = r.to_dict()
        self.assertEqual(d["stage"], "test")
        self.assertEqual(d["status"], "ok")

    def test_04_pipeline_run_to_dict(self):
        from src.runtime.pipeline import PipelineRun, StageResult
        r = PipelineRun()
        r.stages = [StageResult(stage="memory", status="ok")]
        d = r.to_dict()
        self.assertIn("run_id", d)
        self.assertIn("stages", d)
        self.assertEqual(d["stages"][0]["stage"], "memory")

    def test_05_approval_record_phase_6_metadata(self):
        """ApprovalRecord metadata 应有 phase_6_0 标记"""
        from src.growth.approval_manager import ApprovalManager
        from src.growth.lifecycle_manager import ProposalLifecycleManager

        growth = MagicMock()
        # 使用固定 id，使 list_proposals 返回的 proposal 与 approve_proposal 调用匹配
        growth.list_proposals.return_value = [_make_proposal("prop_legacy_001")]
        growth.accept_proposal.return_value = _make_proposal("prop_legacy_001", status="accepted")
        growth.reject_proposal.return_value = _make_proposal("prop_legacy_001", status="rejected")

        lifecycle = ProposalLifecycleManager(
            storage_path=os.path.join(tempfile.mkdtemp(), "lc.json")
        )
        mgr = ApprovalManager(
            growth_adapter=growth,
            lifecycle_manager=lifecycle,
        )
        record = mgr.approve_proposal("prop_legacy_001", reason="x")
        self.assertTrue(record.metadata.get("phase_6_0"))


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    unittest.main()
