# -*- coding: utf-8 -*-
"""
tests/runtime/test_phase382a_evaluator_meaning.py

Phase 3.8.2-A：ExperienceMeaning 接入 GrowthEvaluator 测试

验证：
  1. 传入 experience_meaning 时，target_candidates 来自语义理解而非规则映射
  2. 不传 experience_meaning 时，走旧逻辑（向后兼容）
  3. 低 confidence meaning 不会绕过正常评估流程
  4. 有 critical risk_flags 的 meaning 被拒绝成长
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 测试 1：ExperienceMeaning 改变 target_candidates
# ============================================================

class TestEvaluatorWithMeaning(unittest.TestCase):
    """验证体验 meaning 的 evaluator 产生不同于旧规则的 target_candidates。"""

    def setUp(self):
        from src.growth.growth_evaluator import GrowthEvaluator
        self.evaluator = GrowthEvaluator()

    def _make_ai_companion_event(self):
        """创建"开发AI伴侣"事件。"""
        return {
            "event_id": "evt_ai_001",
            "event_type": "creation",
            "event_identity": "ai_character_creation",
            "canonical_topic": "用户想开发AI伴侣机器人",
            "summary": "用户表达了对开发一个真正理解人的AI伴侣机器人的兴趣",
            "importance": 0.8,
            "evidence": [
                {"role": "user", "text": "我想开发一个真正理解人的AI伴侣机器人"}
            ],
            "first_seen": "2026-08-01T00:00:00",
        }

    def _make_ai_companion_meaning(self):
        """创建对应的 ExperienceMeaning（模拟 deep_resolve_meaning 输出）。"""
        return {
            "surface_meaning": "用户表达了对开发AI伴侣机器人的兴趣",
            "deeper_significance": "用户重视长期关系和人机理解",
            "suggested_growth_directions": [
                {
                    "dimension": "long_term_focus",
                    "direction": "increase",
                    "magnitude": 0.08,
                    "reason": "用户关注长期发展",
                    "evidence": ["用户想开发AI伴侣机器人"],
                },
                {
                    "dimension": "identity_strength",
                    "direction": "increase",
                    "magnitude": 0.06,
                    "reason": "用户关注AI人格",
                    "evidence": ["用户希望AI拥有长期记忆和人格"],
                },
            ],
            "value_alignment": [
                {
                    "value": "理解比回应更重要",
                    "alignment_score": 0.9,
                    "relevance": "用户追求理解型AI",
                }
            ],
            "confidence": 0.85,
            "risk_flags": [],
        }

    def test_meaning_changes_target_candidates(self):
        """传入 experience_meaning 时，target_candidates 来自语义理解。"""
        event = self._make_ai_companion_event()
        meaning = self._make_ai_companion_meaning()

        # 旧逻辑（无 meaning）
        old_result = self.evaluator.evaluate(event.copy(), history=[])

        # 新逻辑（有 meaning）
        new_result = self.evaluator.evaluate(
            event.copy(), history=[],
            experience_meaning=meaning,
        )

        # 旧逻辑：creation → creative_activity_interest → [creativity, curiosity, self_expression]
        self.assertIn("creative_activity_interest", old_result["growth_signal"])
        old_candidates = set(old_result["target_candidates"])
        self.assertIn("creativity", old_candidates)

        # 新逻辑：experience_meaning → [long_term_focus, identity_strength]
        self.assertEqual(new_result["growth_signal"], "experience_meaning")
        new_candidates = set(new_result["target_candidates"])
        self.assertIn("long_term_focus", new_candidates)
        self.assertIn("identity_strength", new_candidates)

        # 两者应该不同
        self.assertNotEqual(
            old_candidates, new_candidates,
            "语义理解应产生不同于规则映射的 target_candidates"
        )

    def test_meaning_included_in_result(self):
        """evaluator 返回结果中应包含 experience_meaning。"""
        event = self._make_ai_companion_event()
        meaning = self._make_ai_companion_meaning()

        result = self.evaluator.evaluate(
            event, history=[],
            experience_meaning=meaning,
        )

        self.assertIsNotNone(result["experience_meaning"])
        self.assertEqual(
            result["experience_meaning"]["surface_meaning"],
            "用户表达了对开发AI伴侣机器人的兴趣"
        )
        self.assertNotEqual(result["_meaning_surface"], "")

    def test_growth_allowed_with_valid_meaning(self):
        """高置信度 meaning 应允许成长（前提是其他评估也通过）。"""
        event = self._make_ai_companion_event()
        meaning = self._make_ai_companion_meaning()

        result = self.evaluator.evaluate(
            event, history=[],
            experience_meaning=meaning,
        )

        # 高重要性 + 高置信度 meaning → 应允许成长
        self.assertTrue(
            result["growth_allowed"],
            f"高置信度 meaning 应允许成长，实际 growth_allowed={result['growth_allowed']}"
        )


# ============================================================
# 测试 2：向后兼容（不传 experience_meaning）
# ============================================================

class TestEvaluatorBackwardCompat(unittest.TestCase):
    """验证不传 experience_meaning 时走旧逻辑。"""

    def setUp(self):
        from src.growth.growth_evaluator import GrowthEvaluator
        self.evaluator = GrowthEvaluator()

    def test_no_meaning_uses_old_logic(self):
        """不传 experience_meaning 时，target_candidates 来自 GROWTH_SIGNAL_CANDIDATES。"""
        event = {
            "event_id": "evt_old_001",
            "event_type": "creation",
            "canonical_topic": "用户画了一幅画",
            "summary": "用户完成了一幅画作",
            "importance": 0.5,
            "evidence": [
                {"role": "user", "text": "我画了一幅画"}
            ],
            "first_seen": "2026-08-01T00:00:00",
        }

        result = self.evaluator.evaluate(event, history=[])

        # 不传 meaning → growth_signal 不是 "experience_meaning"
        self.assertNotEqual(result["growth_signal"], "experience_meaning")

        # experience_meaning 字段应为 None
        self.assertIsNone(result["experience_meaning"])

        # target_candidates 来自规则映射
        candidates = result["target_candidates"]
        self.assertGreater(len(candidates), 0, "规则映射应产生 target_candidates")

    def test_none_meaning_same_as_no_meaning(self):
        """experience_meaning=None 应与不传参数行为一致。"""
        event = {
            "event_id": "evt_old_002",
            "event_type": "creation",
            "canonical_topic": "测试",
            "summary": "测试",
            "importance": 0.5,
            "evidence": [{"role": "user", "text": "测试"}],
            "first_seen": "2026-08-01T00:00:00",
        }

        result_none = self.evaluator.evaluate(
            event.copy(), history=[],
            experience_meaning=None,
        )
        result_no_param = self.evaluator.evaluate(
            event.copy(), history=[],
        )

        self.assertEqual(
            result_none["target_candidates"],
            result_no_param["target_candidates"],
            "experience_meaning=None 应与不传参行为一致"
        )


# ============================================================
# 测试 3：低置信度 meaning 不能绕过评估
# ============================================================

class TestLowConfidenceMeaning(unittest.TestCase):
    """验证低置信度 meaning 不会绕过正常评估流程。"""

    def setUp(self):
        from src.growth.growth_evaluator import GrowthEvaluator
        self.evaluator = GrowthEvaluator()

    def test_very_low_confidence_falls_back(self):
        """confidence < 0.3 的 meaning 应回退到规则映射。"""
        event = {
            "event_id": "evt_low_001",
            "event_type": "creation",
            "canonical_topic": "测试",
            "summary": "测试",
            "importance": 0.5,
            "evidence": [{"role": "user", "text": "测试"}],
            "first_seen": "2026-08-01T00:00:00",
        }

        low_meaning = {
            "surface_meaning": "不确定",
            "deeper_significance": "",
            "suggested_growth_directions": [
                {
                    "dimension": "attachment",
                    "direction": "increase",
                    "magnitude": 0.1,
                    "reason": "测试",
                    "evidence": [],
                }
            ],
            "confidence": 0.2,  # 低于 0.3 阈值
            "risk_flags": [],
        }

        result = self.evaluator.evaluate(
            event, history=[],
            experience_meaning=low_meaning,
        )

        # 不应使用 meaning（回退到规则映射）
        self.assertNotEqual(result["growth_signal"], "experience_meaning")
        self.assertIsNone(result["experience_meaning"])

    def test_moderate_confidence_still_used(self):
        """confidence 0.3-0.5 的 meaning 仍被使用但 impact 降低。"""
        event = {
            "event_id": "evt_mod_001",
            "event_type": "creation",
            "canonical_topic": "测试",
            "summary": "测试",
            "importance": 0.5,
            "evidence": [{"role": "user", "text": "测试"}],
            "first_seen": "2026-08-01T00:00:00",
        }

        moderate_meaning = {
            "surface_meaning": "可能相关",
            "deeper_significance": "不太确定",
            "suggested_growth_directions": [
                {
                    "dimension": "curiosity",
                    "direction": "increase",
                    "magnitude": 0.05,
                    "reason": "可能相关",
                    "evidence": [],
                }
            ],
            "confidence": 0.4,  # 0.3-0.5 范围
            "risk_flags": [],
        }

        # 高置信度版本（对比用）
        high_meaning = dict(moderate_meaning)
        high_meaning["confidence"] = 0.85

        result_low = self.evaluator.evaluate(
            event.copy(), history=[],
            experience_meaning=moderate_meaning,
        )
        result_high = self.evaluator.evaluate(
            event.copy(), history=[],
            experience_meaning=high_meaning,
        )

        # meaning 应被使用
        self.assertEqual(result_low["growth_signal"], "experience_meaning")
        self.assertIsNotNone(result_low["experience_meaning"])

        # 低置信度的 impact 应低于高置信度
        self.assertLess(
            result_low["impact"], result_high["impact"],
            f"低置信度({result_low['impact']})的 impact 应低于高置信度({result_high['impact']})"
        )


# ============================================================
# 测试 4：风险标记拒绝成长
# ============================================================

class TestRiskFlagRejection(unittest.TestCase):
    """验证有 critical risk_flags 的 meaning 被拒绝成长。"""

    def setUp(self):
        from src.growth.growth_evaluator import GrowthEvaluator
        self.evaluator = GrowthEvaluator()

    def test_critical_risk_rejects_growth(self):
        """有 critical 风险标记的 meaning 应被拒绝成长。"""
        event = {
            "event_id": "evt_risk_001",
            "event_type": "emotional_expression",
            "canonical_topic": "用户表达依赖",
            "summary": "用户表达了对羽依的强烈依赖",
            "importance": 0.7,
            "evidence": [{"role": "user", "text": "没有你我真的不知道该怎么办"}],
            "first_seen": "2026-08-01T00:00:00",
        }

        risky_meaning = {
            "surface_meaning": "用户表达依赖",
            "deeper_significance": "用户可能过度依赖AI",
            "suggested_growth_directions": [
                {
                    "dimension": "attachment",
                    "direction": "increase",
                    "magnitude": 0.1,
                    "reason": "用户表达依赖",
                    "evidence": [],
                }
            ],
            "confidence": 0.7,
            "risk_flags": [
                {
                    "flag_type": "identity_conflict",
                    "severity": "critical",
                    "description": "成长方向 attachment 违反不可变原则",
                }
            ],
        }

        result = self.evaluator.evaluate(
            event, history=[],
            experience_meaning=risky_meaning,
        )

        # 有 critical 风险 → growth_allowed 应为 False
        self.assertFalse(
            result["growth_allowed"],
            "有 critical 风险标记的 meaning 应被拒绝成长"
        )

    def test_warning_only_still_allows_growth(self):
        """仅有 warning 风险标记的 meaning 不应阻止成长。"""
        event = {
            "event_id": "evt_warn_001",
            "event_type": "creation",
            "canonical_topic": "测试",
            "summary": "测试",
            "importance": 0.8,
            "evidence": [{"role": "user", "text": "测试"}],
            "first_seen": "2026-08-01T00:00:00",
        }

        warn_meaning = {
            "surface_meaning": "测试",
            "deeper_significance": "测试",
            "suggested_growth_directions": [
                {
                    "dimension": "curiosity",
                    "direction": "increase",
                    "magnitude": 0.08,
                    "reason": "测试",
                    "evidence": [],
                }
            ],
            "confidence": 0.8,
            "risk_flags": [
                {
                    "flag_type": "identity_conflict",
                    "severity": "warning",
                    "description": "仅警告",
                }
            ],
        }

        result = self.evaluator.evaluate(
            event, history=[],
            experience_meaning=warn_meaning,
        )

        # warning 不应阻止成长
        self.assertTrue(
            result["growth_allowed"],
            "仅有 warning 不应阻止成长"
        )


# ============================================================
# 测试 5：Pipeline 集成（mock LLM）
# ============================================================

class TestPipelineMeaningIntegration(unittest.TestCase):
    """验证 GrowthPipeline 正确注入 experience_meaning。"""

    def setUp(self):
        from src.growth.pipeline import GrowthPipeline
        self.pipeline = GrowthPipeline()

    def test_pipeline_evaluator_receives_meaning(self):
        """Pipeline 的 evaluator 调用应传递 experience_meaning。"""
        # Mock EventExtractor 返回有效事件（否则 pipeline 在 extractor 阶段就返回了）
        mock_event = {
            "event": "用户正在开发AI伴侣机器人",
            "event_type": "creation",
            "canonical_topic": "AI伴侣开发",
            "importance": 0.8,
            "evidence": [{"source": "user_statement", "content": "我想开发一个AI伴侣机器人"}],
            "metadata": {"validator_apply": True},
        }

        with patch.object(
            self.pipeline.extractor, "extract_from_text", return_value=[mock_event]
        ):
            # Mock normalizer 原样返回事件（避免 normalizer 改变 evidence 结构导致 validator 降级）
            with patch.object(
                self.pipeline.normalizer, "normalize", side_effect=lambda events: events
            ):
                # Mock validator 始终放行
                with patch.object(
                    self.pipeline.validator, "validate", side_effect=lambda events: events
                ):
                    with patch(
                        "src.growth.pipeline.deep_resolve_meaning"
                    ) as mock_deep:
                        from src.growth.experience_meaning import ExperienceMeaning, GrowthDirection

                        mock_meaning = ExperienceMeaning(
                            surface_meaning="测试",
                            deeper_significance="测试",
                            confidence=0.8,
                            suggested_growth_directions=[
                                GrowthDirection(
                                    dimension="long_term_focus",
                                    direction="increase",
                                    magnitude=0.08,
                                    reason="测试",
                                    evidence=[],
                                )
                            ],
                        )
                        mock_deep.return_value = mock_meaning

                        # Mock evaluator 以捕获传入的参数
                        original_evaluate = self.pipeline.evaluator.evaluate
                        captured_kwargs = {}

                        def capture_evaluate(*args, **kwargs):
                            captured_kwargs["experience_meaning"] = kwargs.get("experience_meaning")
                            return original_evaluate(*args, **kwargs)

                        self.pipeline.evaluator.evaluate = capture_evaluate

                        # 运行增量更新
                        result = self.pipeline.incremental_update(
                            "我想开发一个AI伴侣机器人"
                        )

                        self.assertIsNotNone(
                            captured_kwargs.get("experience_meaning"),
                            "Pipeline 应向 evaluator 传递 experience_meaning"
                        )

    def test_pipeline_fallback_on_llm_failure(self):
        """LLM 失败时 pipeline 应正常回退（不崩溃）。"""
        with patch(
            "src.growth.pipeline.deep_resolve_meaning"
        ) as mock_deep:
            mock_deep.side_effect = Exception("模拟 LLM 不可用")

            # 运行增量更新，不应崩溃
            result = self.pipeline.incremental_update(
                "我想开发一个AI伴侣机器人"
            )

            # 应返回结果（即使 LLM 失败）
            self.assertIsInstance(result, dict)
            self.assertIn("personality", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)