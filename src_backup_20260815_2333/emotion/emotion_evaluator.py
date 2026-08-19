"""
情绪评估器 (EmotionEvaluator) — 规则版
根据事件类型查找预设的情绪变化规则，并按强度缩放。
"""
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_delta import EmotionDelta


# 规则表：每种事件对应的基础 Delta（强度为 1.0 时的变化量）
EVENT_RULES = {
    "user_praise": EmotionDelta(
        valence=0.2, arousal=0.1, confidence=0.15, energy=0.1
    ),
    "user_conflict": EmotionDelta(
        valence=-0.3, arousal=0.2, confidence=-0.1, anxiety=0.2, energy=-0.1
    ),
    "achievement": EmotionDelta(
        valence=0.3, arousal=0.2, confidence=0.2, anxiety=-0.1, curiosity=0.1, energy=0.2
    ),
    "disappointment": EmotionDelta(
        valence=-0.2, arousal=-0.1, confidence=-0.1, anxiety=0.1, energy=-0.15
    ),
    "new_topic": EmotionDelta(
        arousal=0.1, curiosity=0.2, energy=0.1
    ),
    "long_silence": EmotionDelta(
        arousal=-0.1, anxiety=0.1, energy=-0.1
    ),
    # R2.7.6-YUYI: 缓解/安抚事件——用户说"睡好了/放心了"→ anxiety 大幅下降
    #   羽依说：「今天担心的事明天你说睡好了才缓解」——这是事件型缓解，不是 decay。
    "mitigation": EmotionDelta(
        valence=0.25, arousal=-0.15, anxiety=-0.35, confidence=0.1, energy=0.1
    ),
}


class EmotionEvaluator:
    def evaluate(self, event: EmotionEvent) -> EmotionDelta:
        # 查找规则
        base = EVENT_RULES.get(event.event_type, EmotionDelta())

        # 按强度缩放
        scaled = EmotionDelta(
            valence=self._scale(base.valence, event.intensity),
            arousal=self._scale(base.arousal, event.intensity),
            curiosity=self._scale(base.curiosity, event.intensity),
            anxiety=self._scale(base.anxiety, event.intensity),
            confidence=self._scale(base.confidence, event.intensity),
            energy=self._scale(base.energy, event.intensity),
        )
        return scaled

    def _scale(self, value: float, intensity: float) -> float:
        return round(value * intensity, 4)