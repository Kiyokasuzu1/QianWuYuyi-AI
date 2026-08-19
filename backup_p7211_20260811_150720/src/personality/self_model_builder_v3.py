"""
SelfModelBuilderV3 — 从 ReflectionRecord 构建 SelfModelV3
"""
from typing import List, Optional
from src.reflection.reflection_record import ReflectionRecord
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem


class SelfModelBuilderV3:
    def __init__(self, max_beliefs: int = 5, max_narratives: int = 3, min_confidence: float = 0.5):
        self.max_beliefs = max_beliefs
        self.max_narratives = max_narratives
        self.min_confidence = min_confidence

    def build(
        self,
        identity: str,
        traits: dict,
        values: dict,
        reflections: List[ReflectionRecord],
        previous_model: Optional[SelfModelV3] = None
    ) -> SelfModelV3:
        safe_reflections = [
            r for r in reflections
            if r.is_safe and r.confidence >= self.min_confidence
        ]

        new_beliefs = []
        if previous_model:
            new_beliefs.extend(previous_model.beliefs)
        for r in safe_reflections:
            new_beliefs.extend(r.new_beliefs)

        seen = set()
        unique_beliefs = []
        for b in new_beliefs:
            if b not in seen:
                seen.add(b)
                unique_beliefs.append(b)
        unique_beliefs = unique_beliefs[:self.max_beliefs]

        narrative_items = []
        if previous_model:
            narrative_items.extend(previous_model.narrative_items)

        for r in safe_reflections:
            if r.causal_chain and r.reflection_level in ("insight", "belief_change", "identity_change"):
                text = self._chain_to_narrative(r)
                if text:
                    narrative_items.append(NarrativeItem(text=text, source_ids=[r.reflection_id]))

        narrative_items = narrative_items[-self.max_narratives:]

        return SelfModelV3(
            identity=identity,
            traits=traits,
            values=values,
            beliefs=unique_beliefs,
            narrative_items=narrative_items
        )

    def _chain_to_narrative(self, record: ReflectionRecord) -> str:
        if not record.causal_chain:
            return ""
        chain_desc = "。".join(record.causal_chain)
        if record.current_understanding:
            return f"{chain_desc}，所以我理解到：{record.current_understanding}"
        return chain_desc