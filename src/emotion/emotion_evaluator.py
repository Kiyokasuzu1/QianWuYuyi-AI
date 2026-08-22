"""
情绪评估器 (EmotionEvaluator) — 规则版
根据事件类型查找预设的情绪变化规则，并按强度缩放。

Emotion System 2.0（E-Emotion-2 升级）：
- 保留 evaluate() → EmotionDelta（旧契约，兼容既有调用方）
- 新增 evaluate_proposals() → List[EmotionChangeProposal]
  只输出提案、不修改任何状态；confidence 按规则置信度表给出
"""
from typing import List, Optional

from src.emotion.emotion_change_proposal import EmotionChangeProposal
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_delta import EmotionDelta


# 规则表：每种事件对应的基础 Delta（强度为 1.0 时的变化量）
EVENT_RULES = {
    "user_praise": EmotionDelta(
        valence=0.2, arousal=0.1, confidence=0.15, energy=0.1,
        happiness=0.25, trust=0.05, attachment=0.03,
    ),
    "user_conflict": EmotionDelta(
        valence=-0.3, arousal=0.2, confidence=-0.1, anxiety=0.2, energy=-0.1,
        happiness=-0.2, sadness=0.15, trust=-0.05,
    ),
    "achievement": EmotionDelta(
        valence=0.3, arousal=0.2, confidence=0.2, anxiety=-0.1, curiosity=0.1, energy=0.2,
        happiness=0.3,
    ),
    "disappointment": EmotionDelta(
        valence=-0.2, arousal=-0.1, confidence=-0.1, anxiety=0.1, energy=-0.15,
        happiness=-0.2, sadness=0.2,
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
        valence=0.25, arousal=-0.15, anxiety=-0.35, confidence=0.1, energy=0.1,
        happiness=0.25, trust=0.05,
    ),
}

# 规则置信度表（E-Emotion-2）：已知事件类型 → 高置信度；未知 → 低置信度
DEFAULT_RULE_CONFIDENCE = 0.75
UNKNOWN_EVENT_CONFIDENCE = 0.2


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
            stability=self._scale(base.stability, event.intensity),
            happiness=self._scale(base.happiness, event.intensity),
            sadness=self._scale(base.sadness, event.intensity),
            trust=self._scale(base.trust, event.intensity),
            attachment=self._scale(base.attachment, event.intensity),
        )
        return scaled

    def _scale(self, value: float, intensity: float) -> float:
        return round(value * intensity, 4)

    # --------------------------------------------------------
    # Emotion System 2.0（E-Emotion-2）：提案输出
    # --------------------------------------------------------
    def evaluate_proposals(
        self,
        event: EmotionEvent,
        evidence_ids: Optional[List[str]] = None,
    ) -> List[EmotionChangeProposal]:
        """评估事件 → 每个非零维度一条 EmotionChangeProposal。

        纯评估：不读取/修改 EmotionState，不持久化任何数据。
        提案应用由 EmotionUpdater 负责（E-Emotion-3）。
        """
        delta = self.evaluate(event)
        confidence = (
            DEFAULT_RULE_CONFIDENCE
            if event.event_type in EVENT_RULES
            else UNKNOWN_EVENT_CONFIDENCE
        )
        reason = (
            f"事件类型 {event.event_type}（强度 {event.intensity:.2f}）"
            f"触发情绪评估规则"
        )
        evidence = list(evidence_ids or [])

        proposals: List[EmotionChangeProposal] = []
        for dim in (
            "valence", "arousal", "curiosity", "anxiety", "confidence", "energy",
            "stability", "happiness", "sadness", "trust", "attachment",
        ):
            value = getattr(delta, dim, 0.0)
            if abs(value) < 1e-9:
                continue
            proposals.append(
                EmotionChangeProposal(
                    emotion_dimension=dim,
                    delta=round(value, 4),
                    confidence=confidence,
                    reason=reason,
                    evidence_ids=evidence,
                )
            )
        return proposals