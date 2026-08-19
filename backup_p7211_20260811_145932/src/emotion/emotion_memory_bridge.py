"""
情绪-记忆桥接层 (EmotionMemoryBridge)
在情绪事件发生时，绑定记忆 ID，生成 EmotionalTrace。
Phase 9.4 更新：传递 event_type 到 EmotionalTrace。
"""
from src.emotion.emotional_trace import EmotionalTrace, EmotionCause
from src.emotion.emotion_event import EmotionEvent


class EmotionMemoryBridge:
    def bind(
        self,
        event: EmotionEvent,
        memory_id: str = None,
    ) -> EmotionalTrace:
        cause_map = {
            "user_praise": EmotionCause.USER_INTERACTION,
            "user_conflict": EmotionCause.USER_INTERACTION,
            "achievement": EmotionCause.ACHIEVEMENT,
            "disappointment": EmotionCause.SYSTEM,
            "new_topic": EmotionCause.USER_INTERACTION,
            "long_silence": EmotionCause.SYSTEM,
        }
        cause = cause_map.get(event.event_type, EmotionCause.SYSTEM)

        return EmotionalTrace(
            emotion=self._event_type_to_emotion(event.event_type),
            cause=cause,
            intensity=event.intensity,
            event_type=event.event_type,        # 传递原始事件类型
            memory_id=memory_id,
        )

    def _event_type_to_emotion(self, event_type: str) -> str:
        mapping = {
            "user_praise": "joy",
            "user_conflict": "anxiety",
            "achievement": "joy",
            "disappointment": "sadness",
            "new_topic": "curiosity",
            "long_silence": "calm",
        }
        return mapping.get(event_type, "neutral")