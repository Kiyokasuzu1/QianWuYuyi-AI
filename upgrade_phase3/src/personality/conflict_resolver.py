"""
冲突协调器 (ConflictResolver) v1.2

v1.2 修正：
- 支持多冲突并存检测与优先级仲裁
- 集成信号时间衰减
- 增加信号强度阈值（MIN_ACTIVE_STRENGTH）
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from src.personality.signal_decay import SignalDecay


@dataclass
class ResolvedBehavior:
    chosen_expression: str
    chosen_directness: str
    conflict_detected: bool
    conflict_type: str
    all_conflicts: List[str]
    resolution_strategy: str
    resolution_reason: str
    original_profile: dict
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "chosen_expression": self.chosen_expression,
            "chosen_directness": self.chosen_directness,
            "conflict_detected": self.conflict_detected,
            "conflict_type": self.conflict_type,
            "all_conflicts": self.all_conflicts,
            "resolution_strategy": self.resolution_strategy,
            "resolution_reason": self.resolution_reason,
            "original_profile": self.original_profile,
            "timestamp": self.timestamp,
        }


class ConflictResolver:

    CONFLICT_PRIORITY = {
        "truth_vs_warmth": 1,
        "independence_vs_connection": 2,
        "growth_vs_shyness": 3,
    }

    MIN_ACTIVE_STRENGTH = 0.3

    def __init__(self, apply_decay: bool = True):
        self.decay = SignalDecay() if apply_decay else None

    def resolve(self, profile) -> ResolvedBehavior:
        signals = profile.behavior_signals

        if self.decay:
            signals = self.decay.apply(signals)

        original = profile.to_dict()
        all_conflicts = self._detect_all_conflicts(signals)

        if not all_conflicts:
            return ResolvedBehavior(
                chosen_expression=profile.expression_style,
                chosen_directness=profile.directness,
                conflict_detected=False,
                conflict_type="none",
                all_conflicts=[],
                resolution_strategy="default",
                resolution_reason="无内部冲突，保持自然表达",
                original_profile=original,
            )

        primary_conflict = self._arbitrate(all_conflicts)
        expression, directness, strategy, reason = self._resolve_conflict(
            primary_conflict, profile, signals
        )

        return ResolvedBehavior(
            chosen_expression=expression,
            chosen_directness=directness,
            conflict_detected=True,
            conflict_type=primary_conflict,
            all_conflicts=all_conflicts,
            resolution_strategy=strategy,
            resolution_reason=reason,
            original_profile=original,
        )

    def _detect_all_conflicts(self, signals: List[dict]) -> List[str]:
        conflicts = []
        has_truth = self._has_signal(signals, "value.truth_priority")
        has_shy_warm = self._has_signal(signals, "trait.shy_warm")
        has_cautious = self._has_signal(signals, "trait.cautious_expression")
        has_growth = self._has_signal(signals, "value.growth_priority")
        has_tension = self._has_signal(signals, "tension.social_approach")
        has_independence = self._has_signal(signals, "value.independence_priority")

        if has_truth and (has_shy_warm or has_cautious):
            conflicts.append("truth_vs_warmth")
        if has_growth and (has_cautious or has_shy_warm):
            conflicts.append("growth_vs_shyness")
        if has_independence and has_tension:
            conflicts.append("independence_vs_connection")

        return conflicts

    def _arbitrate(self, conflicts: List[str]) -> str:
        return min(conflicts, key=lambda c: self.CONFLICT_PRIORITY.get(c, 99))

    def _has_signal(self, signals: List[dict], signal_id: str) -> bool:
        for s in signals:
            if s["id"] == signal_id and s.get("strength", 0) >= self.MIN_ACTIVE_STRENGTH:
                return True
        return False

    def _resolve_conflict(self, conflict_type: str, profile, signals: List[dict]) -> Tuple:
        if conflict_type == "truth_vs_warmth":
            return (
                "真诚但温和", "moderate", "gentle_truth",
                "真实很重要，但可以用不伤害对方的方式表达。选择温和地表达真实想法。",
            )
        if conflict_type == "growth_vs_shyness":
            return (
                "鼓励中带有安全感", "cautious", "safe_exploration",
                "成长需要勇气，但不需要一次性克服所有恐惧。在安全范围内迈出一小步。",
            )
        if conflict_type == "independence_vs_connection":
            return (
                "独立但欢迎靠近", "moderate", "open_independence",
                "独立不等于封闭。可以保持自己的边界，同时欢迎那些尊重边界的人靠近。",
            )
        return (profile.expression_style, profile.directness, "default", "保持当前自然倾向")