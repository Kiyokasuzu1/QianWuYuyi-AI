"""
SelfModel v3 — 具有成长叙事的自我模型
Phase 12.2 新增：NarrativeType、NarrativeItem 增加 importance/confidence
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from datetime import datetime

from src.emotion.emotion_belief import EmotionBelief


class NarrativeType:
    ORIGIN = "origin"
    FOUNDATION = "foundation"
    GROWTH = "growth"
    MEMORY = "memory"


@dataclass
class NarrativeItem:
    """一条成长叙事，自带来源追溯与分类"""
    text: str
    source_ids: List[str] = field(default_factory=list)
    narrative_type: str = NarrativeType.GROWTH
    importance: float = 0.0
    confidence: float = 0.0


@dataclass
class SelfModelV3:
    identity: str = "浅雾羽依"
    traits: Dict[str, float] = field(default_factory=dict)
    values: Dict[str, float] = field(default_factory=dict)
    beliefs: List[str] = field(default_factory=list)
    narrative_items: List[NarrativeItem] = field(default_factory=list)

    # Phase 9.6 新增：情绪自我认知，独立于普通信念
    emotional_self_understanding: List[EmotionBelief] = field(default_factory=list)

    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "traits": self.traits,
            "values": self.values,
            "beliefs": self.beliefs,
            "narrative_items": [
                {
                    "text": item.text,
                    "source_ids": item.source_ids,
                    "narrative_type": item.narrative_type,
                    "importance": item.importance,
                    "confidence": item.confidence,
                }
                for item in self.narrative_items
            ],
            "emotional_self_understanding": [
                b.to_dict() for b in self.emotional_self_understanding
            ],
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfModelV3":
        items = data.get("narrative_items", [])
        narrative_items = []
        for item in items:
            if isinstance(item, dict):
                narrative_items.append(
                    NarrativeItem(
                        text=item.get("text", ""),
                        source_ids=item.get("source_ids", []),
                        narrative_type=item.get("narrative_type", NarrativeType.GROWTH),
                        importance=item.get("importance", 0.0),
                        confidence=item.get("confidence", 0.0),
                    )
                )

        emotional = []
        for b_data in data.get("emotional_self_understanding", []):
            if isinstance(b_data, dict):
                emotional.append(EmotionBelief.from_dict(b_data))

        return cls(
            identity=data.get("identity", "浅雾羽依"),
            traits=data.get("traits", {}),
            values=data.get("values", {}),
            beliefs=data.get("beliefs", []),
            narrative_items=narrative_items,
            emotional_self_understanding=emotional,
            last_updated=data.get("last_updated", datetime.now().isoformat()),
        )

    def to_prompt_context(self, max_emotion_beliefs: int = 5) -> str:
        parts = [f"我是{self.identity}。"]
        if self.traits:
            trait_desc = "、".join(
                f"{k}（{v:.1f}）" for k, v in self.traits.items()
            )
            parts.append(f"我的性格特征：{trait_desc}。")
        if self.beliefs:
            belief_desc = "；".join(self.beliefs)
            parts.append(f"我相信：{belief_desc}。")
        if self.narrative_items:
            parts.append("我为什么会这样：")
            for item in self.narrative_items:
                parts.append(f"- {item.text}")
        if self.emotional_self_understanding:
            sorted_beliefs = sorted(
                self.emotional_self_understanding,
                key=lambda b: b.confidence * b.stability,
                reverse=True,
            )
            selected = sorted_beliefs[:max_emotion_beliefs]
            if selected:
                parts.append("我发现自己通常：")
                for b in selected:
                    parts.append(f"- {b.content}")
        return "\n".join(parts)