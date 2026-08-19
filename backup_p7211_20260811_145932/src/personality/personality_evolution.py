"""
人格演化引擎 (PersonalityEvolutionEngine) v1.1

职责：
接收 GrowthAccumulator 输出的成长偏移量，结合 TraitState 和 PersonalityHistory，
计算人格维度的动态演化（值、动量、稳定性、置信度）。
支持 TRAIT_RELATIONS 联动（只影响动量，不改变数值）。

v1.1 更新：
- 增加 apply_relations 方法，实现人格维度联动
- 联动效果受目标稳定性影响（高稳定性抗联动）
- 明确设置反向联动的 direction
"""

from typing import Optional, Dict
from src.personality.trait_state import TraitState
from src.personality.personality_history import PersonalityHistory


class PersonalityEvolutionEngine:
    """
    人格演化引擎

    输入：当前 TraitState、成长偏移量、人格历史
    输出：更新后的 TraitState
    """

    def update_trait(
        self,
        trait_state: TraitState,
        growth_delta: float,
        history: Optional[PersonalityHistory] = None,
    ) -> TraitState:
        """
        综合更新单一人格维度。
        """
        if history is None:
            history = PersonalityHistory()

        direction = self._determine_direction(growth_delta)
        new_value = self._apply_growth(trait_state, growth_delta)
        new_momentum = self._update_momentum(trait_state, direction)
        new_stability = self._update_stability(trait_state, history)
        new_confidence = self._update_confidence(trait_state, direction, history)

        updated: TraitState = {
            "trait": trait_state["trait"],
            "current_value": round(new_value, 4),
            "momentum": round(new_momentum, 4),
            "direction": direction,
            "stability": round(new_stability, 4),
            "confidence": round(new_confidence, 4),
            "last_growth_direction": direction,
            "last_updated": trait_state.get("last_updated", ""),
            "consecutive_same_direction": self._update_consecutive(trait_state, direction),
        }
        return updated

    def apply_relations(
        self,
        trait_name: str,
        growth_delta: float,
        trait_states: Dict[str, TraitState],
    ) -> Dict[str, TraitState]:
        """
        当一个维度发生变化时，联动更新相关维度的动量。
        只修改 momentum 和 direction，不修改 current_value。
        联动效果受目标维度 stability 影响：越稳定越抗联动。
        """
        from src.personality.trait_relations import get_relations_for

        relations = get_relations_for(trait_name)
        for target, relation in relations.items():
            if target not in trait_states:
                continue

            target_state = trait_states[target]
            strength = relation["strength"]
            rel_type = relation["type"]
            target_stability = target_state.get("stability", 0.3)

            # 联动效果受目标稳定性抑制
            effect = strength * 0.5 * (1 - target_stability)

            if rel_type == "positive":
                if growth_delta > 0:
                    target_state["momentum"] = min(1.0, target_state.get("momentum", 0.1) + effect)
                    target_state["direction"] = "increase"
                elif growth_delta < 0:
                    target_state["momentum"] = min(1.0, target_state.get("momentum", 0.1) + effect)
                    target_state["direction"] = "decrease"
            else:
                # 反向联动
                if growth_delta > 0:
                    target_state["momentum"] = max(0.0, target_state.get("momentum", 0.1) - effect)
                    target_state["direction"] = "decrease"
                elif growth_delta < 0:
                    target_state["momentum"] = min(1.0, target_state.get("momentum", 0.1) + effect)
                    target_state["direction"] = "increase"

        return trait_states

    def _apply_growth(self, trait_state: TraitState, growth_delta: float) -> float:
        stability = trait_state.get("stability", 0.3)
        momentum = trait_state.get("momentum", 0.1)
        effective = growth_delta * momentum * (1 - stability * 0.5)
        new_value = trait_state.get("current_value", 0.5) + effective
        return self._clamp(new_value)

    def _update_momentum(self, trait_state: TraitState, direction: str) -> float:
        last_direction = trait_state.get("last_growth_direction", "stable")
        current_momentum = trait_state.get("momentum", 0.1)

        if direction == "stable":
            return max(0.0, current_momentum - 0.05)
        elif direction == last_direction:
            return min(1.0, current_momentum + 0.1)
        else:
            return 0.1

    def _update_stability(self, trait_state: TraitState, history: PersonalityHistory) -> float:
        trait_name = trait_state.get("trait", "")
        changes = history.get_changes_for_dimension(trait_name)
        verified_count = len(changes)
        return min(0.95, 0.3 + verified_count * 0.05)

    def _update_confidence(self, trait_state: TraitState, direction: str, history: PersonalityHistory) -> float:
        last_direction = trait_state.get("last_growth_direction", "stable")
        current_confidence = trait_state.get("confidence", 0.1)

        if direction == "stable":
            return current_confidence
        elif direction == last_direction:
            return min(1.0, current_confidence + 0.05)
        else:
            return max(0.1, current_confidence - 0.1)

    def _determine_direction(self, growth_delta: float) -> str:
        if growth_delta > 0.001:
            return "increase"
        elif growth_delta < -0.001:
            return "decrease"
        else:
            return "stable"

    def _update_consecutive(self, trait_state: TraitState, direction: str) -> int:
        last_direction = trait_state.get("last_growth_direction", "stable")
        if direction == last_direction and direction != "stable":
            return trait_state.get("consecutive_same_direction", 0) + 1
        else:
            return 1 if direction != "stable" else 0

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
        return max(low, min(high, value))