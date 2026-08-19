# -*- coding: utf-8 -*-
"""
tests/runtime/test_phase381_meaning.py

Phase 3.8.1：经历意义理解层测试

验证：
  1. 同 event_type 不同内容 → 不同 ExperienceMeaning
  2. LLM 失败时 fallback 到 resolve_meaning()
  3. IDENTITY_CORE 约束检查（不可变原则）
  4. 向后兼容：resolve_meaning() 仍正常工作
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 测试 1：同 event_type 不同内容 → 不同意义
# ============================================================

class TestSameTypeDifferentMeaning(unittest.TestCase):
    """验证 deep_resolve_meaning 能够区分同类事件的不同含义。"""

    def setUp(self):
        # 模拟 LLM 返回不同的 JSON
        self.ai_companion_response = json.dumps({
            "surface_meaning": "用户表达了对开发AI伴侣机器人的兴趣",
            "deeper_significance": "用户重视长期关系、人机理解和创造有意义的AI存在",
            "suggested_growth_directions": [
                {
                    "dimension": "relationship_orientation",
                    "direction": "increase",
                    "magnitude": 0.08,
                    "reason": "用户关注AI与人的关系设计",
                    "evidence": ["用户想开发AI伴侣机器人"]
                },
                {
                    "dimension": "long_term_focus",
                    "direction": "increase",
                    "magnitude": 0.06,
                    "reason": "用户关注长期发展和成长",
                    "evidence": ["用户希望AI拥有长期记忆和人格"]
                }
            ],
            "value_alignment": [
                {
                    "value": "理解比回应更重要",
                    "alignment_score": 0.9,
                    "relevance": "用户追求理解型AI而非工具型AI"
                }
            ],
            "confidence": 0.85,
            "risk_flags": []
        }, ensure_ascii=False)

        self.painting_response = json.dumps({
            "surface_meaning": "用户完成了一幅画作",
            "deeper_significance": "用户通过艺术表达自我，探索创造力",
            "suggested_growth_directions": [
                {
                    "dimension": "self_expression",
                    "direction": "increase",
                    "magnitude": 0.06,
                    "reason": "用户通过艺术创作表达自我",
                    "evidence": ["用户画了一幅画"]
                },
                {
                    "dimension": "creativity",
                    "direction": "increase",
                    "magnitude": 0.05,
                    "reason": "用户展示创造行为",
                    "evidence": ["用户画了一幅画"]
                }
            ],
            "value_alignment": [
                {
                    "value": "创造比消费更有意义",
                    "alignment_score": 0.85,
                    "relevance": "用户主动创造而非被动消费"
                }
            ],
            "confidence": 0.8,
            "risk_flags": []
        }, ensure_ascii=False)

    def test_different_creation_events(self):
        """同是 creation 类型，不同内容应产生不同的 ExperienceMeaning。"""
        from src.growth.meaning_resolver import deep_resolve_meaning

        # 事件1：开发AI伴侣
        ai_event = {
            "event_id": "evt_001",
            "event_type": "creation",
            "event_identity": "ai_character_creation",
            "canonical_topic": "用户想开发AI伴侣机器人",
            "summary": "用户表达了对开发一个真正理解人的AI伴侣机器人的兴趣",
            "importance": 0.8,
        }

        # 事件2：画画
        paint_event = {
            "event_id": "evt_002",
            "event_type": "creation",
            "event_identity": "ai_image_creation",
            "canonical_topic": "用户画了一幅画",
            "summary": "用户完成了一幅画作",
            "importance": 0.5,
        }

        # Mock LLMClient 返回不同响应（LLMClient 在 deep_resolve_meaning 内部
        # 通过 from src.response.llm import LLMClient 动态导入，所以 patch 原始模块）
        with patch("src.response.llm.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.generate.side_effect = [
                self.ai_companion_response,
                self.painting_response,
            ]
            MockLLM.return_value = mock_llm

            meaning_ai = deep_resolve_meaning(ai_event)
            meaning_paint = deep_resolve_meaning(paint_event)

        # 验证：两个 meaning 不同
        self.assertNotEqual(
            meaning_ai.surface_meaning,
            meaning_paint.surface_meaning,
            "同类型不同内容的事件应产生不同的 surface_meaning"
        )
        self.assertNotEqual(
            meaning_ai.deeper_significance,
            meaning_paint.deeper_significance,
            "同类型不同内容的事件应产生不同的 deeper_significance"
        )

        # 验证：AI伴侣事件应关注关系导向
        ai_dims = [d.dimension for d in meaning_ai.suggested_growth_directions]
        self.assertIn("relationship_orientation", ai_dims,
                      "AI伴侣事件应包含 relationship_orientation 成长方向")

        # 验证：画画事件应关注自我表达
        paint_dims = [d.dimension for d in meaning_paint.suggested_growth_directions]
        self.assertIn("self_expression", paint_dims,
                      "画画事件应包含 self_expression 成长方向")

        # 验证：两者的成长方向不同
        self.assertNotEqual(
            set(ai_dims), set(paint_dims),
            "同类型不同内容的事件应产生不同的成长方向"
        )

        # 验证：两者都不是 fallback
        self.assertFalse(meaning_ai.fallback_used)
        self.assertFalse(meaning_paint.fallback_used)

        # 验证：置信度合理
        self.assertGreater(meaning_ai.confidence, 0.5)
        self.assertGreater(meaning_paint.confidence, 0.5)

    def test_meaning_has_required_fields(self):
        """ExperienceMeaning 应包含所有必要字段。"""
        from src.growth.meaning_resolver import deep_resolve_meaning

        event = {
            "event_id": "evt_003",
            "event_type": "creation",
            "summary": "测试事件",
            "importance": 0.5,
        }

        with patch("src.response.llm.LLMClient") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.generate.return_value = json.dumps({
                "surface_meaning": "测试含义",
                "deeper_significance": "测试深层意义",
                "suggested_growth_directions": [
                    {
                        "dimension": "curiosity",
                        "direction": "increase",
                        "magnitude": 0.05,
                        "reason": "测试原因",
                        "evidence": ["测试证据"]
                    }
                ],
                "value_alignment": [
                    {
                        "value": "理解比回应更重要",
                        "alignment_score": 0.7,
                        "relevance": "测试相关性"
                    }
                ],
                "confidence": 0.7,
                "risk_flags": []
            }, ensure_ascii=False)
            MockLLM.return_value = mock_llm

            meaning = deep_resolve_meaning(event)

        # 必要字段
        self.assertIsNotNone(meaning.surface_meaning)
        self.assertIsNotNone(meaning.deeper_significance)
        self.assertGreater(len(meaning.suggested_growth_directions), 0)
        self.assertGreater(meaning.confidence, 0.0)
        self.assertIsNotNone(meaning.source_event_id)
        self.assertIsNotNone(meaning.created_at)

        # to_dict 可用
        d = meaning.to_dict()
        self.assertIn("surface_meaning", d)
        self.assertIn("deeper_significance", d)
        self.assertIn("suggested_growth_directions", d)
        self.assertIn("value_alignment", d)
        self.assertIn("confidence", d)

        # get_growth_metrics 可用
        metrics = meaning.get_growth_metrics()
        self.assertIsInstance(metrics, dict)
        self.assertGreater(len(metrics), 0)


# ============================================================
# 测试 2：Fallback 测试
# ============================================================

class TestFallbackToRule(unittest.TestCase):
    """验证 LLM 失败时自动回退到 resolve_meaning()。"""

    def test_llm_failure_falls_back(self):
        """LLM 抛出异常时，应回退到规则映射。"""
        from src.growth.meaning_resolver import deep_resolve_meaning

        event = {
            "event_id": "evt_fallback",
            "event_type": "creation",
            "event_identity": "ai_character_creation",
            "summary": "任何内容",
            "importance": 0.5,
        }

        # Mock LLMClient 抛出异常
        with patch("src.response.llm.LLMClient") as MockLLM:
            MockLLM.side_effect = ValueError("模拟 LLM 不可用")

            meaning = deep_resolve_meaning(event)

        # 验证：使用了 fallback
        self.assertTrue(meaning.fallback_used, "LLM 失败时应使用 fallback")

        # 验证：规则映射的 meaning 是 "creation"
        self.assertIn("creation", meaning.surface_meaning,
                      "规则 fallback 应返回 'creation'")

        # 验证：置信度较低（规则 fallback 低于 LLM 理解）
        self.assertLess(meaning.confidence, 0.5,
                        "规则 fallback 的置信度应低于 LLM 理解")

    def test_unknown_event_falls_back_empty(self):
        """未知事件类型应回退到空 ExperienceMeaning。"""
        from src.growth.meaning_resolver import deep_resolve_meaning

        event = {
            "event_id": "evt_unknown",
            "event_type": "completely_unknown_type",
            "summary": "未知事件",
            "importance": 0.5,
        }

        with patch("src.response.llm.LLMClient") as MockLLM:
            MockLLM.side_effect = ValueError("模拟 LLM 不可用")

            meaning = deep_resolve_meaning(event)

        self.assertTrue(meaning.fallback_used)
        # 规则映射也找不到 → 空
        self.assertIn("无法解析", meaning.surface_meaning)


# ============================================================
# 测试 3：IDENTITY_CORE 约束检查
# ============================================================

class TestIdentityConstraint(unittest.TestCase):
    """验证 _validate_meaning_against_identity 正确约束成长方向。"""

    def test_attachment_increase_flagged(self):
        """增加 attachment 应被标记为风险。"""
        from src.growth.meaning_resolver import _validate_meaning_against_identity
        from src.growth.experience_meaning import ExperienceMeaning, GrowthDirection

        meaning = ExperienceMeaning(
            surface_meaning="测试",
            confidence=0.8,
            suggested_growth_directions=[
                GrowthDirection(
                    dimension="attachment",
                    direction="increase",
                    magnitude=0.1,
                    reason="测试",
                    evidence=["test"],
                )
            ],
        )

        validated = _validate_meaning_against_identity(meaning)

        # 应有 critical 风险
        self.assertTrue(validated.has_critical_risks(),
                        "增加 attachment 应触发 critical 风险")
        self.assertLessEqual(validated.confidence, 0.3,
                             "有 critical 风险时置信度应 ≤ 0.3")

    def test_identity_strength_large_magnitude_warned(self):
        """identity_strength 大幅变化应被警告。"""
        from src.growth.meaning_resolver import _validate_meaning_against_identity
        from src.growth.experience_meaning import ExperienceMeaning, GrowthDirection

        meaning = ExperienceMeaning(
            surface_meaning="测试",
            confidence=0.8,
            suggested_growth_directions=[
                GrowthDirection(
                    dimension="identity_strength",
                    direction="increase",
                    magnitude=0.2,  # 超过 0.15 阈值
                    reason="测试",
                    evidence=["test"],
                )
            ],
        )

        validated = _validate_meaning_against_identity(meaning)

        # 应有 warning 风险
        self.assertTrue(validated.has_risks())
        self.assertFalse(validated.has_critical_risks(),
                         "identity_strength 警告不应是 critical")

    def test_safe_dimensions_not_flagged(self):
        """安全维度（curiosity, creativity）不应被标记。"""
        from src.growth.meaning_resolver import _validate_meaning_against_identity
        from src.growth.experience_meaning import ExperienceMeaning, GrowthDirection

        meaning = ExperienceMeaning(
            surface_meaning="测试",
            confidence=0.8,
            suggested_growth_directions=[
                GrowthDirection(
                    dimension="curiosity",
                    direction="increase",
                    magnitude=0.08,
                    reason="测试",
                    evidence=["test"],
                ),
                GrowthDirection(
                    dimension="creativity",
                    direction="increase",
                    magnitude=0.05,
                    reason="测试",
                    evidence=["test"],
                ),
            ],
        )

        validated = _validate_meaning_against_identity(meaning)

        # 不应有风险
        self.assertFalse(validated.has_risks(),
                         "curiosity 和 creativity 不应被标记为风险")
        self.assertEqual(validated.confidence, 0.8,
                         "安全维度的置信度不应被降低")


# ============================================================
# 测试 4：向后兼容性
# ============================================================

class TestBackwardCompatibility(unittest.TestCase):
    """验证 resolve_meaning() 仍正常工作。"""

    def test_resolve_meaning_creation(self):
        """creation 事件类型应返回 'creation'。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {"event_type": "creation"}
        self.assertEqual(resolve_meaning(event), "creation")

    def test_resolve_meaning_relationship(self):
        """relationship 事件类型应返回 'relationship_start'。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {"event_type": "relationship"}
        self.assertEqual(resolve_meaning(event), "relationship_start")

    def test_resolve_meaning_emotional(self):
        """emotional_expression 事件类型应返回 'emotional_expression'。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {"event_type": "emotional_expression"}
        self.assertEqual(resolve_meaning(event), "emotional_expression")

    def test_resolve_meaning_identity_priority(self):
        """event_identity 应优先于 event_type。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {
            "event_type": "unknown",
            "event_identity": "ai_character_creation",
        }
        self.assertEqual(resolve_meaning(event), "creation")

    def test_resolve_meaning_existing_meaning(self):
        """已有 meaning 字段应直接使用。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {
            "event_type": "unknown",
            "meaning": "milestone",
        }
        self.assertEqual(resolve_meaning(event), "milestone")

    def test_resolve_meaning_unknown(self):
        """未知类型应返回空字符串。"""
        from src.growth.meaning_resolver import resolve_meaning

        event = {"event_type": "completely_unknown"}
        self.assertEqual(resolve_meaning(event), "")


# ============================================================
# 测试 5：ExperienceMeaning 数据结构
# ============================================================

class TestExperienceMeaningDataClass(unittest.TestCase):
    """验证 ExperienceMeaning 数据结构的辅助方法。"""

    def test_empty_meaning(self):
        """empty() 应返回有效的空 ExperienceMeaning。"""
        from src.growth.experience_meaning import ExperienceMeaning

        meaning = ExperienceMeaning.empty("evt_test")
        self.assertEqual(meaning.source_event_id, "evt_test")
        self.assertTrue(meaning.fallback_used)
        self.assertEqual(meaning.confidence, 0.0)

    def test_from_rule_fallback(self):
        """from_rule_fallback() 应正确包装规则映射结果。"""
        from src.growth.experience_meaning import ExperienceMeaning

        meaning = ExperienceMeaning.from_rule_fallback("creation", "evt_001")
        self.assertTrue(meaning.fallback_used)
        self.assertIn("creation", meaning.surface_meaning)
        self.assertLess(meaning.confidence, 0.5)

    def test_has_critical_risks(self):
        """has_critical_risks() 应正确检测 critical 风险。"""
        from src.growth.experience_meaning import (
            ExperienceMeaning, RiskFlag
        )

        meaning = ExperienceMeaning()
        self.assertFalse(meaning.has_critical_risks())

        meaning.risk_flags.append(RiskFlag(
            flag_type="test",
            severity="warning",
            description="测试警告",
        ))
        self.assertFalse(meaning.has_critical_risks())

        meaning.risk_flags.append(RiskFlag(
            flag_type="test",
            severity="critical",
            description="测试严重风险",
        ))
        self.assertTrue(meaning.has_critical_risks())

    def test_get_growth_metrics(self):
        """get_growth_metrics() 应返回 {dimension: magnitude} 字典。"""
        from src.growth.experience_meaning import (
            ExperienceMeaning, GrowthDirection
        )

        meaning = ExperienceMeaning(
            suggested_growth_directions=[
                GrowthDirection(
                    dimension="curiosity", direction="increase",
                    magnitude=0.08, reason="测试", evidence=[]
                ),
                GrowthDirection(
                    dimension="creativity", direction="increase",
                    magnitude=0.05, reason="测试", evidence=[]
                ),
            ]
        )

        metrics = meaning.get_growth_metrics()
        self.assertEqual(metrics["curiosity"], 0.08)
        self.assertEqual(metrics["creativity"], 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)