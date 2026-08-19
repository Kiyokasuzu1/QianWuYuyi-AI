"""
情绪引擎 (EmotionEngine)
接收 EmotionEvent，委托 EmotionEvaluator 评估，输出 EmotionDelta。
"""
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_delta import EmotionDelta
from src.emotion.emotion_evaluator import EmotionEvaluator


class EmotionEngine:
    def __init__(self, evaluator: EmotionEvaluator = None):
        self.evaluator = evaluator or EmotionEvaluator()

    def process(self, event: EmotionEvent) -> EmotionDelta:
        return self.evaluator.evaluate(event)