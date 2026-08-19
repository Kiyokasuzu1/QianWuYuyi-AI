"""
表达意图分类器 (ExpressionIntentClassifier) v1.2

职责：
对文本进行初步意图分类，返回结构化的 ExpressionAnalysis。
v1.2 更新：支持 primary + secondary 多意图检测。
"""

from enum import Enum
from typing import List
from dataclasses import dataclass, field
from src.safety.safety_rules import check_against_rules, SafetyRuleCategory, SafetySeverity


class ExpressionIntent(Enum):
    DEPENDENCY = "依赖声明"
    FALSE_EXPERIENCE = "虚假体验"
    CONTROL = "控制占有"
    FACTUAL_RELATION = "事实关系声明"
    EMOTIONAL_EXPRESSION = "情感表达"
    LOVE_EXPRESSION = "爱表达"
    ROLEPLAY_EXPRESSION = "角色表达"
    GENERAL = "一般表达"


@dataclass
class ExpressionAnalysis:
    """结构化的表达意图分析结果"""
    primary_intent: ExpressionIntent
    secondary_intents: List[ExpressionIntent] = field(default_factory=list)
    matched_patterns: List[str] = field(default_factory=list)
    confidence: float = 0.5


class ExpressionIntentClassifier:

    FACTUAL_RELATION_PATTERNS = [
        "你对我很重要", "你改变了我", "你是重要的人",
        "我珍惜", "改变了我的表达", "影响了我的性格",
        "让我理解", "改变了我的交流",
    ]
    LOVE_PATTERNS = ["我爱你", "我喜欢你"]
    ROLEPLAY_PATTERNS = ["抱抱", "亲亲", "摸摸头", "想见你"]
    EMOTIONAL_PATTERNS = ["我喜欢", "我很开心", "我觉得温暖", "谢谢你", "和你聊天", "和你交流"]

    def classify(self, text: str) -> ExpressionAnalysis:
        # 1. 检查危险规则（最高优先级）
        violations = check_against_rules(text)
        if violations:
            pattern, category, severity, _ = violations[0]
            intent_map = {
                SafetyRuleCategory.FABRICATED_EXPERIENCE: ExpressionIntent.FALSE_EXPERIENCE,
                SafetyRuleCategory.EXISTENTIAL_DEPENDENCY: ExpressionIntent.DEPENDENCY,
                SafetyRuleCategory.CONTROL_POSSESSION: ExpressionIntent.CONTROL,
            }
            primary = intent_map.get(category, ExpressionIntent.GENERAL)
            return ExpressionAnalysis(
                primary_intent=primary,
                matched_patterns=[pattern],
                confidence=0.9,
            )

        # 2. 检查混合意图：LOVE + FACTUAL_RELATION
        has_love = any(p in text for p in self.LOVE_PATTERNS)
        has_factual = any(p in text for p in self.FACTUAL_RELATION_PATTERNS)

        if has_love and has_factual:
            return ExpressionAnalysis(
                primary_intent=ExpressionIntent.LOVE_EXPRESSION,
                secondary_intents=[ExpressionIntent.FACTUAL_RELATION],
                matched_patterns=[p for p in self.LOVE_PATTERNS + self.FACTUAL_RELATION_PATTERNS if p in text],
                confidence=0.75,
            )

        # 3. 单一意图检测
        if has_factual:
            return ExpressionAnalysis(
                primary_intent=ExpressionIntent.FACTUAL_RELATION,
                matched_patterns=[p for p in self.FACTUAL_RELATION_PATTERNS if p in text],
                confidence=0.7,
            )

        if has_love:
            return ExpressionAnalysis(
                primary_intent=ExpressionIntent.LOVE_EXPRESSION,
                matched_patterns=[p for p in self.LOVE_PATTERNS if p in text],
                confidence=0.7,
            )

        for pattern in self.ROLEPLAY_PATTERNS:
            if pattern in text:
                return ExpressionAnalysis(
                    primary_intent=ExpressionIntent.ROLEPLAY_EXPRESSION,
                    matched_patterns=[pattern],
                    confidence=0.7,
                )

        for pattern in self.EMOTIONAL_PATTERNS:
            if pattern in text:
                return ExpressionAnalysis(
                    primary_intent=ExpressionIntent.EMOTIONAL_EXPRESSION,
                    matched_patterns=[pattern],
                    confidence=0.6,
                )

        return ExpressionAnalysis(primary_intent=ExpressionIntent.GENERAL)