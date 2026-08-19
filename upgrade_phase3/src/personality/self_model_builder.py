"""
自我模型构建器 (SelfModelBuilder) v2.1.3

兼容：
- SelfModel v2 架构
- 旧测试接口
- SelfModelStore 调用方式

职责：
将 Identity Core、成长历史、当前特质状态和人格矛盾
整合为羽依的动态自我认知模型。
"""

from typing import Dict, List
from datetime import datetime

from src.personality.identity_core import IDENTITY_CORE
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.trait_state import TraitState
from src.personality.personality_tension import detect_tensions


class SelfModelBuilder:

    def build(
        self,
        history: PersonalityGrowthHistory = None,
        trait_states: Dict[str, TraitState] = None,
        base_identity: str = None,
        capability_limitations: List[str] = None,
        growth_history: PersonalityGrowthHistory = None,
    ):
        if history is None:
            history = growth_history

        history = history or PersonalityGrowthHistory()
        trait_states = trait_states or {}

        identity_name = base_identity or IDENTITY_CORE.get("name", "")
        capability_limitations = capability_limitations or []

        stable_traits = []
        developing_traits = []

        for r in history.get_high_confidence(0.7):
            level = r.get("growth_level", "")
            meaning = r.get("meaning", "")
            validation = r.get("validation_count", 0)
            momentum = self._get_record_momentum(r, trait_states)

            if level == "trait" and validation >= 3 and meaning:
                stable_traits.append(meaning)
            elif level == "preference" and momentum >= 0.5 and meaning:
                developing_traits.append(meaning)

        return {
            "identity_id": IDENTITY_CORE.get("identity_id", ""),
            "identity_name": identity_name,
            "self_description": self._generate_self_description(identity_name, history),
            "growth_narratives": self._extract_growth_narratives(history),
            "current_traits": self._collect_current_traits(trait_states),
            "personality_tensions": self._detect_tensions(trait_states),
            "self_understanding": self._calc_self_understanding(history),
            "last_updated": datetime.now().isoformat(),
            "stable_traits": stable_traits,
            "developing_traits": developing_traits,
            "known_limitations": capability_limitations,
            "identity_summary": identity_name + "。" + "".join(stable_traits),
        }

    def _get_record_momentum(self, record, trait_states):
        momentum = record.get("momentum", 0)
        if momentum:
            return momentum
        for dim in record.get("affected_dimensions", []):
            state = trait_states.get(dim)
            if isinstance(state, dict):
                momentum = state.get("momentum", 0)
                if momentum:
                    return momentum
            else:
                value = getattr(state, "momentum", 0)
                if value:
                    return value
        return 0

    def _generate_self_description(self, name, history):
        text = f"我是{name}。"
        for r in history.get_high_confidence(0.7):
            narrative = r.get("narrative", "")
            if narrative:
                text += narrative
                break
        return {"text": text, "sources": ["growth_history"], "confidence": 0.8}

    def _extract_growth_narratives(self, history):
        result = []
        for r in history.get_high_confidence(0.7):
            dims = r.get("affected_dimensions", [])
            if not dims:
                continue
            event = r.get("event", "")
            if not event:
                events = r.get("trigger_events", [])
                if events:
                    event = events[0]
            result.append({
                "record_id": r.get("record_id", ""),
                "dimension": dims[0],
                "event": event,
                "narrative": r.get("narrative", ""),
                "meaning": r.get("meaning", ""),
                "timestamp": r.get("timestamp", ""),
            })
        return result

    def _collect_current_traits(self, states):
        return {k: self._get_value(v) for k, v in states.items()}

    def _detect_tensions(self, states):
        traits = self._collect_current_traits(states)
        result = []
        for t in detect_tensions(traits):
            dims = t.get("dimensions", [])
            if len(dims) < 2:
                continue
            result.append({
                "trait_a": dims[0],
                "trait_b": dims[1],
                "trait_values": {dims[0]: traits.get(dims[0], 0), dims[1]: traits.get(dims[1], 0)},
                "description": t.get("description", ""),
                "intensity": t.get("intensity", 0),
            })
        return result

    def _calc_self_understanding(self, history):
        count = history.count()
        experience = min(0.2 + count * 0.01, 1)
        awareness = min(0.1 + len(history.get_high_confidence(0.7)) * 0.04, 1)
        overall = (experience + awareness + 0.4) / 3
        return {
            "experience_awareness": round(experience, 3),
            "trait_awareness": round(awareness, 3),
            "identity_continuity": 0.4,
            "overall": round(overall, 3),
        }

    def _get_value(self, state):
        if isinstance(state, dict):
            return state.get("current_value", 0.5)
        return getattr(state, "current_value", 0.5)