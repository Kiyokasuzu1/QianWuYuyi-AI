"""
情绪模式分析器 (EmotionPatternAnalyzer) — Phase 9.4 v2.1 最终版
修正 stability 计算：仅使用当前 event_type 的近期 trace。
confidence 引入样本量因子，使低样本模式置信度降低。
"""
from typing import List, Dict
from collections import defaultdict
from src.emotion.emotional_trace import EmotionalTrace
from src.emotion.emotion_pattern import EmotionPattern


class EmotionPatternAnalyzer:
    def __init__(self, min_occurrences: int = 3, confidence_threshold: float = 0.4,
                 sample_weight_factor: float = 10.0):
        self.min_occurrences = min_occurrences
        self.confidence_threshold = confidence_threshold
        self.sample_weight_factor = sample_weight_factor

    def analyze(self, traces: List[EmotionalTrace]) -> List[EmotionPattern]:
        if not traces:
            return []

        patterns = []

        # 事件类型 → 情绪分布
        event_emotion_map: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: defaultdict(list))
        for t in traces:
            event_emotion_map[t.event_type][t.emotion].append(t.trace_id)

        for event_type, emotion_map in event_emotion_map.items():
            total = sum(len(ids) for ids in emotion_map.values())
            if total < self.min_occurrences:
                continue

            distribution = {emotion: len(ids) / total for emotion, ids in emotion_map.items()}
            main_emotion = max(distribution, key=distribution.get)

            # 置信度 = 主要情绪占比 × 样本量因子
            raw_proportion = distribution[main_emotion]
            sample_weight = min(1.0, total / self.sample_weight_factor)
            confidence = raw_proportion * sample_weight

            # 稳定性：仅使用当前 event_type 的 trace，按时间取最近
            event_traces = [t for t in traces if t.event_type == event_type]
            event_traces_sorted = sorted(event_traces, key=lambda t: t.created_at, reverse=True)
            recent_event_traces = event_traces_sorted[:min(10, len(event_traces))]
            recent_main = sum(1 for t in recent_event_traces if t.emotion == main_emotion)
            stability = recent_main / len(recent_event_traces) if recent_event_traces else 0.0

            if confidence >= self.confidence_threshold:
                patterns.append(EmotionPattern(
                    pattern_type="trigger_event",
                    event_type=event_type,
                    emotion=main_emotion,
                    description=f"当经历 '{event_type}' 事件时，主要产生 {self._emotion_label(main_emotion)} 情绪",
                    confidence=round(confidence, 2),
                    stability=round(stability, 2),
                    emotion_distribution=distribution,
                    evidence_trace_ids=[tid for ids in emotion_map.values() for tid in ids],
                    occurrence_count=total,
                    last_seen_at=max(t.created_at for t in event_traces),
                ))

        return patterns

    def _emotion_label(self, emotion: str) -> str:
        labels = {
            "joy": "积极",
            "anxiety": "不安",
            "curiosity": "好奇",
            "sadness": "低落",
            "calm": "平静",
            "neutral": "中性",
        }
        return labels.get(emotion, emotion)