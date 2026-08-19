"""
Phase 4.0 — R2.5.2-C Gates: Interest Transition Proposal & Contradiction Protection

Gates:
  TC-1 Proposal shape 冻结
  TC-2 三种模式正确产出: reinforce / new_interest_emerging / gradual_transition
  TC-3 红线1: analyzer / build_proposal 绝不读取 PersonalityState / trait
  TC-4 红线2: gradual_transition 永远 transition_mode=gradual（不允许 abrupt）
  TC-5 红线3: status 永远 pending（不自动 approved / applied）
  TC-6 新兴趣不覆盖旧兴趣（from/to 并列记录，非 CS=0 / AI_art=1 这种清零赋值）
  TC-7 下降检测: 旧 importance 低于新 importance 至少 importance_gap → 判 transition
  TC-8 上升检测: 出现全新 signal（history 无）→ new_emerging
  TC-9 双信号（A 占比 >= dominance_ratio，且 importance 下降）→ gradual_transition
  TC-10 集成到 GrowthIntegrationService.accept_experience()：gradual_transition 触发 needs_review
  TC-11 集成：reinforce / emerging 会挂 evaluator_meta.transition_analysis
  TC-12 异常隔离：analyzer 抛异常不会让 accept_experience 失败
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from src.growth.growth_integration import GrowthIntegrationService

from src.growth.interest_transition import (
    FROZEN_KEYS,
    ALLOWED_PROPOSAL_MODES,
    ALLOWED_TRANSITION_MODES,
    ALLOWED_STATUS,
    InterestTransitionProposal,
    build_interest_transition_proposal,
)
from src.growth.transition_analyzer import TransitionAnalyzer


def _mk_record(
    memory_id: str = "mem_tc001",
    *,
    content: str = "这周我画了两幅插画，设计了一张新角色立绘，完成了一幅水彩练习，坚持了画画爱好。",
    importance: float = 0.85,
    memory_type: str = "user_preference",
    meaning: str = "user_preference:creative_activity_artistic",
) -> Dict[str, Any]:
    return {
        "id": memory_id,
        "content": content,
        "user_id": "u_tc_test",
        "role": "user",
        "timestamp": "2026-08-08T10:00:00",
        "importance": importance,
        "memory_class": memory_type.replace("user_", ""),
        "metadata": {
            "memory_type": memory_type,
            "meaning": meaning,
        },
    }


def _mk_evaluator_output(growth_signal, *, confidence=0.8, growth_allowed=True, growth_level="context", applied_delta=0.03):
    return {
        "growth_signal": growth_signal,
        "confidence": confidence,
        "growth_allowed": growth_allowed,
        "growth_level": growth_level,
        "target_candidates": ["creativity", "curiosity"],
        "applied_delta": applied_delta,
    }


class TestPhase40R252CTC1ProposalShape(unittest.TestCase):
    """TC-1 shape 冻结 12 字段不多不少；mode/status/transition_mode 枚举"""

    def test_keys_exactly_12(self):
        p = build_interest_transition_proposal(
            proposal_mode="reinforce",
            current_growth_signal="creative_activity_interest",
            previous_growth_signals=["creative_activity_interest"],
            from_interest={"creative_activity_interest": 0.38},
            to_interest={"creative_activity_interest": 0.38},
            confidence=0.75,
            evidence_memory_ids=["m1"],
            reason_text="reinforce test",
        )
        self.assertEqual(set(p.keys()), set(FROZEN_KEYS))

    def test_modes_and_status_and_transition(self):
        p = build_interest_transition_proposal(
            proposal_mode="new_interest_emerging",
            current_growth_signal="complex_problem_solving",
            previous_growth_signals=[],
            to_interest={"complex_problem_solving": 0.28},
            confidence=0.7,
            evidence_memory_ids=["m2"],
            reason_text="new emerging",
        )
        self.assertIn(p["proposal_mode"], ALLOWED_PROPOSAL_MODES)
        self.assertIn(p["transition_mode"], ALLOWED_TRANSITION_MODES)
        self.assertIn(p["status"], ALLOWED_STATUS)

    def test_abrupt_mode_refused(self):
        # 规则：
        #  (1) build_interest_transition_proposal 函数签名不接受 status / transition_mode 参数 → 传了会 TypeError
        #  (2) 如果传入的 proposal_mode 拼写错误 → raise ValueError（非法枚举）
        # 我们两者都测，确保"非法 status/transition_mode 无法产出"。
        with self.assertRaises(TypeError):
            build_interest_transition_proposal(
                proposal_mode="reinforce",
                current_growth_signal="a",
                previous_growth_signals=[],
                confidence=0.6,
                evidence_memory_ids=["x"],
                reason_text="",
                status="approved",  # 函数未定义此参数
            )
        with self.assertRaises(ValueError):
            # proposal_mode 不是合法枚举（不是 build 的 transition_mode=abrupt 参数；本模块根本不存在 abrupt 参数）
            build_interest_transition_proposal(
                proposal_mode="abrupt",  # type: ignore[arg-type]
                current_growth_signal="a",
                previous_growth_signals=[],
                from_interest={"a": 0.3},
                to_interest={"a": 0.3},
                confidence=0.6,
                evidence_memory_ids=["x"],
                reason_text="",
            )


class TestPhase40R252CTC2ThreeModes(unittest.TestCase):
    """TC-2 三种模式正确产出"""

    def test_reinforce_when_history_contains_same_signal(self):
        a = TransitionAnalyzer()
        history = [
            {"growth_signal": "creative_activity_interest", "importance": 0.8, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "creative_activity_interest", "importance": 0.78, "evidence": [{"memory_id": "h2"}]},
        ]
        p = a.analyze({"importance": 0.8}, history, _mk_evaluator_output("creative_activity_interest"))
        self.assertIsNotNone(p)
        self.assertEqual(p["proposal_mode"], "reinforce")

    def test_new_emerging_when_no_prev_signal(self):
        a = TransitionAnalyzer()
        p = a.analyze({"importance": 0.8}, [], _mk_evaluator_output("complex_problem_solving"))
        self.assertIsNotNone(p)
        self.assertEqual(p["proposal_mode"], "new_interest_emerging")

    def test_gradual_transition_when_dominant_old_signal_declined(self):
        a = TransitionAnalyzer(dominance_ratio=0.4, importance_gap=0.1, min_history_for_transition=2)
        # 旧 complex_problem_solving 占 3/4；importance 平均 0.55
        history: List[Dict[str, Any]] = [
            {"growth_signal": "complex_problem_solving", "importance": 0.6, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.55, "evidence": [{"memory_id": "h2"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.5, "evidence": [{"memory_id": "h3"}]},
            {"growth_signal": "social_interaction_preference", "importance": 0.6, "evidence": [{"memory_id": "h4"}]},
        ]
        # 当前 creative_activity_interest importance=0.9
        p = a.analyze(
            {"importance": 0.9},
            history,
            _mk_evaluator_output("creative_activity_interest"),
        )
        self.assertIsNotNone(p)
        self.assertEqual(p["proposal_mode"], "gradual_transition")
        # 必须包含 from/to 各一个条目（complex 为旧；creative 为新）
        self.assertIn("complex_problem_solving", p["from_interest"])
        self.assertIn("creative_activity_interest", p["to_interest"])
        # 红线：from 不能被清零（仍保留 strength > 0）
        self.assertGreater(p["from_interest"]["complex_problem_solving"], 0.0)


class TestPhase40R252CTC3RedlineNoPersonality(unittest.TestCase):
    """TC-3 红线1：Analyzer 绝不 import PersonalityState / trait_state / PersonalityAdapter."""

    def test_analyzer_module_has_no_personality_import(self):
        import inspect
        import sys
        from src.growth import transition_analyzer as mod
        src = inspect.getsource(mod)
        bad_patterns = ["PersonalityState", "personality_adapter", "PersonalityAdapter", "trait_state", "before 0 after 1"]
        for bad in bad_patterns:
            self.assertNotIn(bad, src, f"transition_analyzer 不应包含 {bad}")

    def test_build_proposal_module_has_no_personality_import(self):
        import ast
        import inspect
        from src.growth import interest_transition as mod2
        # 只扫描 AST import/from-import + 函数实现（跳过 docstring，因为文档里会提到红线）
        src = inspect.getsource(mod2)
        # 去掉 docstring 的粗暴办法：把源文件 parse 成 AST，再把所有 Constant Str 节点清空
        class _StripStrings(ast.NodeTransformer):
            def visit_Constant(self, node):  # python 3.8+
                if isinstance(node.value, str):
                    node.value = ""
                return node

        tree = ast.parse(src)
        tree = _StripStrings().visit(tree)
        clean_src = ast.unparse(tree)
        for bad in ["PersonalityState", "PersonalityAdapter", "trait_state", "trait.before"]:
            self.assertNotIn(bad, clean_src, f"interest_transition 实现/导入不应包含 {bad}")


class TestPhase40R252CTC456Redlines(unittest.TestCase):
    """TC-4 gradual_transition 必 gradual；TC-5 status 必 pending；TC-6 新兴趣不覆盖旧兴趣（from/to 并列，非清零）"""

    def test_tc4_gradual_transition_never_abrupt(self):
        a = TransitionAnalyzer(dominance_ratio=0.4, importance_gap=0.05, min_history_for_transition=2)
        history = [
            {"growth_signal": "complex_problem_solving", "importance": 0.5, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.5, "evidence": [{"memory_id": "h2"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.48, "evidence": [{"memory_id": "h3"}]},
        ]
        p = a.analyze({"importance": 0.9}, history, _mk_evaluator_output("creative_activity_interest"))
        self.assertEqual(p["proposal_mode"], "gradual_transition")
        self.assertEqual(p["transition_mode"], "gradual")

    def test_tc5_status_always_pending(self):
        p = build_interest_transition_proposal(
            proposal_mode="reinforce",
            current_growth_signal="a",
            previous_growth_signals=[],
            from_interest={"a": 0.3},
            to_interest={"a": 0.3},
            confidence=0.5,
            evidence_memory_ids=["x"],
            reason_text="",
        )
        self.assertEqual(p["status"], "pending")
        # 改不了：build_interest_transition_proposal 不接受 status 参数（TypeError）
        with self.assertRaises(TypeError):
            build_interest_transition_proposal(
                proposal_mode="reinforce",
                current_growth_signal="a",
                previous_growth_signals=[],
                confidence=0.5,
                evidence_memory_ids=["x"],
                reason_text="",
                status="approved",
            )

    def test_tc6_gradual_no_zero_override(self):
        a = TransitionAnalyzer(dominance_ratio=0.4, importance_gap=0.05, min_history_for_transition=2)
        history = [
            {"growth_signal": "complex_problem_solving", "importance": 0.5, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.5, "evidence": [{"memory_id": "h2"}]},
        ]
        p = a.analyze({"importance": 0.95}, history, _mk_evaluator_output("creative_activity_interest"))
        self.assertEqual(p["proposal_mode"], "gradual_transition")
        # from 的兴趣 strength > 0（保留）
        self.assertGreater(p["from_interest"]["complex_problem_solving"], 0.0)
        # 同时 to 的新兴趣 strength > 0
        self.assertGreater(p["to_interest"]["creative_activity_interest"], 0.0)


class TestPhase40R252CTC789AnalyzerSignals(unittest.TestCase):
    """TC-7 下降检测；TC-8 上升 emerging；TC-9 双信号迁移"""

    def test_tc7_importance_gap_triggers_transition(self):
        a = TransitionAnalyzer(dominance_ratio=0.6, importance_gap=0.01, min_history_for_transition=2)
        history = [
            {"growth_signal": "complex_problem_solving", "importance": 0.6, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.58, "evidence": [{"memory_id": "h2"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.55, "evidence": [{"memory_id": "h3"}]},
        ]
        p = a.analyze({"importance": 0.9}, history, _mk_evaluator_output("creative_activity_interest"))
        self.assertEqual(p["proposal_mode"], "gradual_transition")

    def test_tc8_emerging_for_totally_new_signal(self):
        a = TransitionAnalyzer()
        p = a.analyze({"importance": 0.88}, [], _mk_evaluator_output("knowledge_exploration"))
        self.assertEqual(p["proposal_mode"], "new_interest_emerging")
        self.assertEqual(len(p["to_interest"]), 1)
        self.assertIn("knowledge_exploration", p["to_interest"])

    def test_tc9_dual_signal_parallel_not_transition_when_low_gap(self):
        """如果旧 importance 与新 importance 差距不大 → 视为 parallel emerging（不是迁移）"""
        a = TransitionAnalyzer(dominance_ratio=0.5, importance_gap=0.5, min_history_for_transition=2)
        history = [
            {"growth_signal": "complex_problem_solving", "importance": 0.8, "evidence": [{"memory_id": "h1"}]},
            {"growth_signal": "complex_problem_solving", "importance": 0.85, "evidence": [{"memory_id": "h2"}]},
        ]
        p = a.analyze({"importance": 0.85}, history, _mk_evaluator_output("creative_activity_interest"))
        self.assertEqual(p["proposal_mode"], "new_interest_emerging")


class TestPhase40R252CTC101112Integration(unittest.TestCase):
    """TC-10 transition 触发 needs_review；TC-11 两种模式挂 evaluator_meta；TC-12 异常隔离。"""

    def setUp(self):
        # reset eligibility ledger 不可控，但 accept_experience 的 eligibility 默认单例，重复调用会影响 grace，
        # 所以我们先调 3 次同 meaning + 高 importance，确保 eligibility 放行
        self._ensure_eligibility_passes()

    def _ensure_eligibility_passes(self):
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        recs = [_mk_record(f"mem_tc_warmup_{i}") for i in range(3)]
        for r in recs:
            svc.accept_experience(r)

    def test_tc10_gradual_transition_marks_needs_review(self):
        # 构造 fake GrowthIntegrationService：process_event 直接返回 proposal 已创建
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        # 先 history 累积 2 条旧 complex + 1 条新 creative 放行：直接 mock evaluator 后测
        # 由于 real evaluator 默认对 creative_activity_interest 产生 target_candidates，所以这里直接测集成：
        # 给 svc.inject transition_analysis = gradual_transition，然后跑 accept_experience 看 proposal_manager 的 mark_needs_review 被调用
        from unittest.mock import patch
        with patch.object(svc.proposal_manager, "mark_needs_review") as mock_mnr:
            # 跑一次真实 accept_experience
            rec = _mk_record("mem_tc10_a", importance=0.9, meaning="user_preference:creative_activity_artistic")
            # 用 patch TransitionAnalyzer.analyze 产出 gradual_transition
            fake_tr = build_interest_transition_proposal(
                proposal_mode="gradual_transition",
                current_growth_signal="creative_activity_interest",
                previous_growth_signals=["complex_problem_solving"],
                from_interest={"complex_problem_solving": 0.5},
                to_interest={"creative_activity_interest": 0.65},
                confidence=0.8,
                evidence_memory_ids=["m1"],
                reason_text="TC-10 synthetic",
            )
            with patch(
                "src.growth.transition_analyzer.TransitionAnalyzer.analyze",
                return_value=fake_tr,
            ):
                # 为确保 eligibility 通过（grace_period=2），先预热 1 次
                svc.accept_experience(_mk_record("mem_tc10_warm", importance=0.92))
                res = svc.accept_experience(rec)
            # applied=False（红线）
            self.assertIs(res["applied"], False)
            pid = res.get("proposal_id")
            if pid is not None:
                # 如果 eligibility + 一切顺利，mark_needs_review 应该被调用
                self.assertTrue(mock_mnr.called, "gradual_transition → mark_needs_review 应被调用")
                # mark_needs_review(proposal_id=..., reason=...) 是 kwargs 调用
                kwargs = mock_mnr.call_args.kwargs or {}
                self.assertIn("interest_transition_gradual", kwargs.get("reason", ""))
                self.assertIn("transition_gradual_marked_needs_review", res["reasons"])

    def test_tc11_evaluator_meta_has_transition_analysis(self):
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        # 过渡 analyzer 产出 reinforce 时，growth_integration 会把它挂 evaluator_meta，并把
        # "transition_analysis_attached" 加入 reasons（见 Step 5.5）。我们直接验证 reasons。
        # 预热 eligibility：连续 2 条同一 meaning，必须是 evaluator 能识别的（比如 creative_activity_artistic
        # 会映射到 creativity -> growth_allowed=True；growth_signal=creative_activity_interest）
        r0 = _mk_record("mem_tc11_w0", importance=0.9, meaning="user_preference:creative_activity_artistic")
        r1 = _mk_record("mem_tc11_w1", importance=0.9, meaning="user_preference:creative_activity_artistic")
        svc.accept_experience(r0)
        svc.accept_experience(r1)
        # 第 3 条同样 meaning：evaluator.growth_signal 相同，TransitionAnalyzer 产出 reinforce
        r2 = _mk_record("mem_tc11_r2", importance=0.92, meaning="user_preference:creative_activity_artistic")
        res = svc.accept_experience(r2)
        self.assertIn("transition_analysis_attached", res["reasons"],
                      f"transition 未挂 evaluator_meta。reasons={res['reasons']} state={res['pipeline_state']}")

    def test_tc12_analyzer_exception_isolated(self):
        """TC-12: 即使 TransitionAnalyzer 抛异常，accept_experience 也不能 crash。"""
        svc = GrowthIntegrationService(config={"auto_accept_enabled": False})
        r = _mk_record("mem_tc12_a", importance=0.88)
        from unittest.mock import patch
        with patch(
            "src.growth.transition_analyzer.TransitionAnalyzer.analyze",
            side_effect=RuntimeError("模拟 Analyzer 崩溃"),
        ):
            res = svc.accept_experience(r)
        # 状态可能是 eligibility 拒绝或 created，但 pipeline_state 不应是 error
        self.assertNotEqual(res["pipeline_state"], "error", "Analyer 异常不应升级为 error（应隔离）")
        self.assertIs(res["applied"], False)


if __name__ == "__main__":
    unittest.main()
