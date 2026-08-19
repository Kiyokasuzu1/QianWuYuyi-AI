"""
情绪事件检测器 (EmotionEventDetector)
从用户消息中检测可能引发情绪变化的事件。
Phase 9.7 v2 新增：否定词优先判断，防止"不喜欢"误判为赞美。
"""
from typing import Optional
from src.emotion.emotion_event import EmotionEvent


class EmotionEventDetector:
    def __init__(self):
        self.positive_keywords = ["谢谢", "很好", "开心", "喜欢", "厉害", "棒", "太棒了", "真棒"]
        self.negative_keywords = ["讨厌", "失望", "生气", "难过", "不行", "烦", "糟糕", "烦死了"]
        self.negation_prefixes = ["不", "没", "不是", "别", "并不", "没有那么", "一点也不"]

    def detect(self, message: str) -> Optional[EmotionEvent]:
        if not message:
            return None

        msg = message.lower()

        # 1. 优先检查否定式搭配，如“不喜欢” → 负面事件
        for prefix in self.negation_prefixes:
            if prefix in msg:
                for word in self.positive_keywords:
                    if word in msg and (prefix + word) in msg:
                        return EmotionEvent(
                            event_type="user_conflict",
                            intensity=0.5,
                            description=message,
                            source="user",
                        )

        # 2. 再检查负面关键词
        if any(w in msg for w in self.negative_keywords):
            return EmotionEvent(
                event_type="user_conflict",
                intensity=0.5,
                description=message,
                source="user",
            )

        # 3. 最后检查正向关键词
        if any(w in msg for w in self.positive_keywords):
            return EmotionEvent(
                event_type="user_praise",
                intensity=0.6,
                description=message,
                source="user",
            )

        return None