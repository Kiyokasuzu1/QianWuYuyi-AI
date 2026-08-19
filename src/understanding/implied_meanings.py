"""
言外之意推断引擎。

负责：
- 识别表面表达背后的真实意图
- 基于相处模式进行推断
- 输出隐含情感和需求
"""

from typing import Dict, Optional

from src.understanding.pattern_learner import PatternLearner
from src.understanding.intent_inferrer import IntentInferrer


class ImpliedMeaningsEngine:
    """
    言外之意推断引擎。

    整合相处模式学习器和意图推断器，提供综合推断。
    """

    def __init__(self):
        self._pattern_learner = PatternLearner()
        self._intent_inferrer = IntentInferrer()

    def infer(self, text: str, context: Dict = None) -> Dict:
        """
        推断言外之意。

        Args:
            text: 用户输入文本
            context: 上下文信息（来自 ContextManager）

        Returns:
            {
                "surface": str,           # 表面表达
                "implied": str,           # 言外之意
                "emotion": str,           # 隐含情感
                "need": str,              # 潜在需求
                "confidence": float,      # 置信度
                "pattern": Optional[Dict], # 匹配的相处模式
                "context_modifier": str,  # 语境修饰
                "modifier_effect": str,   # 修饰效果
            }
        """
        # 1. 意图推断
        intent_result = self._intent_inferrer.infer_intent(text, context)

        # 2. 模式匹配
        user_id = ""
        if context and isinstance(context.get("user_id"), str):
            user_id = context["user_id"]

        matched_pattern = self._pattern_learner.match(text, user_id)

        # 3. 综合结果
        # 如果有匹配的模式，优先使用模式的言外之意
        implied = intent_result["implied_meaning"]
        emotion = intent_result["primary_emotion"]
        need = intent_result["inferred_need"]
        confidence = intent_result["confidence"]

        if matched_pattern is not None:
            # 模式匹配的置信度加权
            if matched_pattern.implied_meaning:
                implied = matched_pattern.implied_meaning
            if matched_pattern.emotional_tone:
                emotion = matched_pattern.emotional_tone
            if matched_pattern.underlying_need:
                need = matched_pattern.underlying_need

            # 匹配到已知模式，置信度提升
            confidence = min(1.0, confidence + 0.2)

        return {
            "surface": text,
            "implied": implied,
            "emotion": emotion,
            "need": need,
            "confidence": confidence,
            "pattern": matched_pattern.to_dict() if matched_pattern else None,
            "context_modifier": intent_result.get("context_modifier", ""),
            "modifier_effect": intent_result.get("modifier_effect", ""),
        }

    def learn_pattern(
        self,
        surface: str,
        implied: str,
        emotion: str = "",
        need: str = "",
        user_id: str = "",
        keywords: list = None,
        confidence: float = 0.5,
    ):
        """
        学习新的相处模式。

        交给内部的 PatternLearner 处理。
        """
        return self._pattern_learner.learn(
            surface=surface,
            implied=implied,
            emotion=emotion,
            need=need,
            user_id=user_id,
            keywords=keywords,
            confidence=confidence,
        )

    def get_patterns(self, user_id: str = "") -> list:
        """获取已学习的模式列表"""
        return [p.to_dict() for p in self._pattern_learner.get_patterns(user_id)]

    def get_pattern_count(self) -> int:
        """获取模式数量"""
        return self._pattern_learner.get_pattern_count()

    def get_stats(self) -> Dict:
        """获取引擎统计信息"""
        return {
            "pattern_count": self._pattern_learner.get_pattern_count(),
            "emotion_rules": self._intent_inferrer.get_emotion_rules_count(),
        }


# 全局实例
_global_engine: Optional[ImpliedMeaningsEngine] = None


def get_implied_meanings_engine() -> ImpliedMeaningsEngine:
    """获取全局言外之意推断引擎实例"""
    global _global_engine
    if _global_engine is None:
        _global_engine = ImpliedMeaningsEngine()
    return _global_engine