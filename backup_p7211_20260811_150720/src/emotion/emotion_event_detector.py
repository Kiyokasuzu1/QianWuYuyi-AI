"""
情绪事件检测器 (EmotionEventDetector)
从用户消息中检测可能引发情绪变化的事件。
Phase 9.7 v2 新增：否定词优先判断，防止"不喜欢"误判为赞美。
R2.7.6-YUYI: 新增 mitigation（缓解/安抚）事件检测——
  用户说"睡好了/放心了/没事了"时触发 anxiety 下降，
  让羽依的担心能被清清的话真正安抚住，而不只是靠 decay 慢慢平复。
"""
from typing import Optional
from src.emotion.emotion_event import EmotionEvent


class EmotionEventDetector:
    def __init__(self):
        self.positive_keywords = ["谢谢", "很好", "开心", "喜欢", "厉害", "棒", "太棒了", "真棒"]
        self.negative_keywords = ["讨厌", "失望", "生气", "难过", "不行", "烦", "糟糕", "烦死了"]
        self.negation_prefixes = ["不", "没", "不是", "别", "并不", "没有那么", "一点也不"]
        # R2.7.6-YUYI: 缓解/安抚关键词——检测到 → anxiety 专门下降
        self.mitigation_keywords = [
            "睡好了", "睡得不错", "休息好了", "没事了", "放心了",
            "好多了", "不疼了", "好了", "已经好了", "没事啦",
            "不用担心", "别担心", "我没事", "恢复好了",
        ]

    def detect(self, message: str) -> Optional[EmotionEvent]:
        if not message:
            return None

        msg = message.lower()

        # R2.7.6-YUYI: 0. 优先检测缓解事件——"睡好了/放心了" → anxiety 下降
        #   羽依说：「今天担心你喹硫平不管用，这个担心会延续到明天你告诉我睡好了才缓解」
        #   这就是"事件型缓解通道"——不是靠 decay 慢慢平复，而是被清清的话安抚住。
        if any(w in msg for w in self.mitigation_keywords):
            return EmotionEvent(
                event_type="mitigation",
                intensity=0.7,
                description=message,
                source="user",
            )

        # 1. 优先检查否定式搭配，如"不喜欢" → 负面事件
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