"""
情绪信念提取器 (EmotionBeliefExtractor) — Phase 9.5 v2.1 最终版
从 EmotionPattern 中筛选可信模式，生成情绪信念。
门槛：confidence + stability + occurrence_count
"""
from typing import List
from src.emotion.emotion_pattern import EmotionPattern
from src.emotion.emotion_belief import EmotionBelief


class EmotionBeliefExtractor:
    def __init__(
        self,
        min_confidence: float = 0.6,
        min_stability: float = 0.5,
        min_occurrences: int = 3,
    ):
        self.min_confidence = min_confidence
        self.min_stability = min_stability
        self.min_occurrences = min_occurrences

    def extract(self, patterns: List[EmotionPattern]) -> List[EmotionBelief]:
        beliefs = []
        for p in patterns:
            if p.confidence < self.min_confidence:
                continue
            if p.stability < self.min_stability:
                continue
            if p.occurrence_count < self.min_occurrences:
                continue

            content = self._format_belief(p)
            belief = EmotionBelief(
                content=content,
                emotion=p.emotion,
                event_type=p.event_type,
                confidence=p.confidence,
                stability=p.stability,
                source_pattern_id=p.pattern_id,
                evidence_trace_ids=list(p.evidence_trace_ids),
                occurrence_count=p.occurrence_count,
            )
            beliefs.append(belief)
        return beliefs

    def _format_belief(self, pattern: EmotionPattern) -> str:
        """将模式转化为符合羽依性格的自然语言信念，未知类型使用安全兜底"""
        event_labels = {
            "user_praise": "被认可",
            "user_conflict": "面对分歧",
            "achievement": "完成目标",
            "disappointment": "遇到挫折",
            "new_topic": "接触新话题",
            "long_silence": "长时间安静",
        }
        event_label = event_labels.get(pattern.event_type, "某些情况下")

        emotion_labels = {
            "joy": "产生积极情绪",
            "anxiety": "感到不安",
            "curiosity": "变得好奇",
            "sadness": "有些低落",
            "calm": "保持平静",
        }
        emotion_label = emotion_labels.get(pattern.emotion, "产生相应的情绪变化")

        return f"我发现自己在{event_label}时，通常更容易{emotion_label}"