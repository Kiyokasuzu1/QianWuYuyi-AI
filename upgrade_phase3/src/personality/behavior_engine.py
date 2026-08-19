"""
行为引擎 (BehaviorEngine) v1.1

职责：
接收 CurrentIdentitySnapshot，生成当前的行为倾向。
这是人格系统与对话系统之间的桥梁。

v1.1 修正：
- 直接使用 IdentityResolver 输出的结构化信号，不再重新解析
- 统一信号 ID 命名规则（trait.* / tension.* / value.*）
- 增加 snapshot 版本检查
- sensitivity_notes 替代 avoid_guidelines
- confidence 计算保留基础版本
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime


SUPPORTED_SNAPSHOT_VERSION = "identity_snapshot_v1"


@dataclass
class BehaviorProfile:
    """当前行为倾向画像"""

    expression_style: str
    warmth_level: str
    directness: str
    openness: str
    decision_style: str
    conflict_style: str
    growth_orientation: str
    sensitivity_notes: List[str]
    behavior_signals: List[dict]
    confidence: float
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    schema_version: str = "behavior_profile_v1"

    def to_dict(self) -> Dict:
        return {
            "expression_style": self.expression_style,
            "warmth_level": self.warmth_level,
            "directness": self.directness,
            "openness": self.openness,
            "decision_style": self.decision_style,
            "conflict_style": self.conflict_style,
            "growth_orientation": self.growth_orientation,
            "sensitivity_notes": self.sensitivity_notes,
            "behavior_signals": self.behavior_signals,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }


class BehaviorEngine:

    def analyze(self, snapshot) -> BehaviorProfile:
        # 版本检查
        if getattr(snapshot, 'schema_version', None) != SUPPORTED_SNAPSHOT_VERSION:
            raise ValueError(
                f"Unsupported snapshot version: "
                f"{getattr(snapshot, 'schema_version', 'unknown')}"
            )

        signals = snapshot.personality_signals  # 直接使用结构化信号
        traits = snapshot.current_traits
        tensions = snapshot.active_tensions
        conflicts = snapshot.active_conflicts

        expression_style = self._infer_expression_style(signals, traits)
        warmth_level = self._infer_warmth_level(traits)
        directness = self._infer_directness(traits, tensions, signals)
        openness = self._infer_openness(traits, signals)
        decision_style = self._infer_decision_style(signals)
        conflict_style = self._infer_conflict_style(signals, conflicts, tensions)
        growth_orientation = self._infer_growth_orientation(signals, traits)
        sensitivity_notes = self._generate_sensitivity_notes(signals, tensions, conflicts)
        confidence = self._calc_confidence(signals, traits)

        return BehaviorProfile(
            expression_style=expression_style,
            warmth_level=warmth_level,
            directness=directness,
            openness=openness,
            decision_style=decision_style,
            conflict_style=conflict_style,
            growth_orientation=growth_orientation,
            sensitivity_notes=sensitivity_notes,
            behavior_signals=signals,
            confidence=round(confidence, 3),
        )

    def _find_signal(self, signals: List[dict], signal_id: str) -> Optional[dict]:
        for s in signals:
            if s["id"] == signal_id:
                return s
        return None

    def _has_signal(self, signals: List[dict], signal_id: str) -> bool:
        return self._find_signal(signals, signal_id) is not None

    def _infer_expression_style(self, signals: List[dict], traits: Dict[str, float]) -> str:
        if self._has_signal(signals, "trait.shy_warm"):
            return "谨慎但温暖"
        if self._has_signal(signals, "trait.cautious_expression"):
            return "含蓄内敛"
        if self._has_signal(signals, "trait.creativity.high") and traits.get("warmth", 0.5) >= 0.6:
            return "温和而富有创造力"
        return "自然平和"

    def _infer_warmth_level(self, traits: Dict[str, float]) -> str:
        w = traits.get("warmth", 0.5)
        if w >= 0.75: return "high"
        if w >= 0.5: return "medium"
        return "low"

    def _infer_directness(self, traits: Dict[str, float], tensions: List[dict], signals: List[dict]) -> str:
        if self._has_signal(signals, "tension.social_approach"):
            return "cautious"
        if traits.get("shyness", 0.5) >= 0.7:
            return "cautious"
        if traits.get("confidence", 0.5) >= 0.7:
            return "direct"
        return "moderate"

    def _infer_openness(self, traits: Dict[str, float], signals: List[dict]) -> str:
        if self._has_signal(signals, "trait.creativity.high") and traits.get("curiosity", 0.5) >= 0.6:
            return "open"
        if traits.get("curiosity", 0.5) >= 0.6:
            return "selective"
        return "reserved"

    def _infer_decision_style(self, signals: List[dict]) -> str:
        if self._has_signal(signals, "value.understanding_priority"):
            return "analytical"
        return "balanced"

    def _infer_conflict_style(self, signals: List[dict], conflicts: List[dict], tensions: List[dict]) -> str:
        has_truth = self._has_signal(signals, "value.truth_priority")
        has_tension = self._has_signal(signals, "tension.social_approach")
        has_independence = self._has_signal(signals, "value.independence_priority")

        if has_truth and has_tension:
            return "gentle_truth"
        if has_truth and has_independence:
            return "direct"
        if has_tension:
            return "diplomatic"
        return "diplomatic"

    def _infer_growth_orientation(self, signals: List[dict], traits: Dict[str, float]) -> str:
        if self._has_signal(signals, "value.growth_priority") and self._has_signal(signals, "value.independence_priority"):
            if traits.get("shyness", 0.5) >= 0.7:
                return "stable"
            return "exploratory"
        if self._has_signal(signals, "trait.cautious_expression"):
            return "protective"
        return "stable"

    def _generate_sensitivity_notes(self, signals: List[dict], tensions: List[dict], conflicts: List[dict]) -> List[str]:
        notes = []
        if self._has_signal(signals, "trait.cautious_expression") or self._has_signal(signals, "trait.shy_warm"):
            notes.append("表达时注意对方的接受度，但不必压抑真实感受")
        if self._has_signal(signals, "value.independence_priority"):
            notes.append("重视自己的判断，但不必疏远他人")
        if self._has_signal(signals, "value.truth_priority") and self._has_signal(signals, "tension.social_approach"):
            notes.append("诚实与温柔可以共存，寻找既真实又不伤人的表达方式")
        return notes

    def _calc_confidence(self, signals: List[dict], traits: Dict[str, float]) -> float:
        if not signals:
            return 0.5
        strengths = [s.get("strength", 0.5) for s in signals]
        avg_strength = sum(strengths) / len(strengths)
        return round(avg_strength * 0.7 + 0.5 * 0.3, 3)