"""
Phase 3.5.31: Creative Engine
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from src.contracts.creative_schema import CreativeDirection, CreativitySnapshot


class CreativeEngine:
    """
    只生成 CreativeDirection，不直接修改人格、记忆或提案状态。
    """

    def __init__(self, history_limit: int = 200):
        self.history_limit = history_limit
        self._history: List[CreativeDirection] = []

    def generate_directions(
        self,
        *,
        memories: List[Dict[str, Any]],
        emotional_snapshot: Dict[str, Any],
        emotional_patterns: List[Dict[str, Any]],
        self_model: Dict[str, Any],
        learning_goals: List[Dict[str, Any]],
    ) -> List[CreativeDirection]:
        directions: List[CreativeDirection] = []
        directions.extend(self._from_memory_associations(memories))
        directions.extend(self._from_emotion_and_memory(memories, emotional_snapshot, emotional_patterns))
        directions.extend(self._from_identity_values(memories, self_model))
        directions.extend(self._from_learning_goals(learning_goals, memories))

        dedup: Dict[str, CreativeDirection] = {}
        for item in directions:
            key = item.theme or item.summary
            if key not in dedup or item.confidence > dedup[key].confidence:
                dedup[key] = item

        result = list(dedup.values())[:8]
        self._history.extend(result)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
        return result

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [item.to_dict() for item in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        themes: List[str] = []
        seen = set()
        for item in reversed(self._history):
            if item.theme and item.theme not in seen:
                seen.add(item.theme)
                themes.append(item.theme)
            if len(themes) >= 5:
                break
        return CreativitySnapshot(
            total_directions=len(self._history),
            active_themes=themes,
            last_direction_id=self._history[-1].direction_id if self._history else "",
        ).to_dict()

    def _from_memory_associations(self, memories: List[Dict[str, Any]]) -> List[CreativeDirection]:
        subjects = self._extract_subjects(memories)
        if len(subjects) < 2:
            return []
        left, right = subjects[0], subjects[1]
        evidence = []
        for mem in memories:
            text = str(mem.get("content", ""))
            if left in text or right in text:
                evidence.append({"memory_id": mem.get("id", ""), "content": text[:100]})
            if len(evidence) >= 6:
                break
        return [CreativeDirection(
            theme=f"{left} × {right}",
            source_type="association",
            summary=f"把“{left}”与“{right}”联结，探索跨主题表达方向。",
            stimulus="近期记忆中出现了可交叉组合的兴趣主题。",
            associations=[left, right],
            suggested_explorations=[
                f"尝试把“{left}”作为核心意象，把“{right}”作为表达媒介。",
                f"比较“{left}”与“{right}”在情绪表达上的共性与反差。",
            ],
            evidence=evidence,
            confidence=0.72,
        )]

    def _from_emotion_and_memory(
        self,
        memories: List[Dict[str, Any]],
        emotional_snapshot: Dict[str, Any],
        emotional_patterns: List[Dict[str, Any]],
    ) -> List[CreativeDirection]:
        subjects = self._extract_subjects(memories)
        if not subjects:
            return []
        mood = str(emotional_snapshot.get("persistent_mood", "") or "")
        dominant = ""
        if emotional_patterns:
            top = sorted(emotional_patterns, key=lambda x: int(x.get("count", 0) or 0), reverse=True)[0]
            dominant = str(top.get("emotion") or top.get("emotion_tag") or "")
        emotion_tag = dominant or mood
        if not emotion_tag:
            return []
        subject = subjects[0]
        return [CreativeDirection(
            theme=f"{emotion_tag}:{subject}",
            source_type="emotion",
            summary=f"把当前情绪线索“{emotion_tag}”转译成与“{subject}”相关的创作方向。",
            stimulus="情绪连续性可作为创作张力来源，但不直接等同于人格结论。",
            associations=[emotion_tag, subject],
            suggested_explorations=[
                f"如果把“{emotion_tag}”视作一种氛围，它会怎样改变“{subject}”的表达方式？",
                f"围绕“{subject}”尝试一条更克制、一条更外放的双轨创作方向。",
            ],
            evidence=[{"emotion": emotion_tag, "mood": mood, "subject": subject}],
            confidence=0.66,
        )]

    def _from_identity_values(self, memories: List[Dict[str, Any]], self_model: Dict[str, Any]) -> List[CreativeDirection]:
        values = list(self_model.get("core_values", []) or [])
        subjects = self._extract_subjects(memories)
        if not values or not subjects:
            return []
        strongest = sorted(values, key=lambda x: float(x.get("weight", 0.0) or 0.0), reverse=True)[0]
        subject = subjects[0]
        value_name = str(strongest.get("name", "") or strongest.get("value_id", "value"))
        return [CreativeDirection(
            theme=f"{value_name}:{subject}",
            source_type="identity",
            summary=f"围绕核心价值“{value_name}”展开与“{subject}”相关的创作方向。",
            stimulus="创作探索应与稳定价值保持连接，而不是脱离身份边界随机发散。",
            associations=[value_name, subject],
            suggested_explorations=[
                f"思考“{value_name}”如何改变“{subject}”的叙事角度。",
                f"尝试把“{subject}”写成能体现“{value_name}”的长期主题。",
            ],
            evidence=[{"value": value_name, "weight": strongest.get("weight", 0.0), "subject": subject}],
            confidence=0.69,
        )]

    def _from_learning_goals(self, learning_goals: List[Dict[str, Any]], memories: List[Dict[str, Any]]) -> List[CreativeDirection]:
        directions: List[CreativeDirection] = []
        subjects = self._extract_subjects(memories)
        fallback_subject = subjects[0] if subjects else "current_theme"
        for goal in learning_goals[:3]:
            topic = str(goal.get("topic", "") or fallback_subject)
            questions = list(goal.get("questions", []) or [])
            goal_type = str(goal.get("goal_type", "") or "curiosity")
            directions.append(CreativeDirection(
                theme=f"{topic}:creative_exploration",
                source_type="curiosity",
                summary=f"把学习目标“{topic}”转成可探索的创作方向。",
                stimulus="Curiosity 提供问题，Creativity 负责把问题转成表达尝试。",
                associations=[topic, goal_type],
                suggested_explorations=questions[:2] or [f"围绕“{topic}”提出两种以上不同表达路径。"],
                evidence=[{"goal_type": goal_type, "topic": topic, "questions": questions[:3]}],
                confidence=max(0.55, float(goal.get("confidence", 0.0) or 0.0)),
            ))
        return directions

    @staticmethod
    def _extract_subjects(memories: List[Dict[str, Any]]) -> List[str]:
        counter = Counter()
        for mem in memories:
            text = str(mem.get("content", ""))
            for pat in [
                r"喜欢(.+)",
                r"热爱(.+)",
                r"研究(.+)",
                r"创作(.+)",
                r"探索(.+)",
                r"画(.+)",
                r"写(.+)",
            ]:
                match = re.search(pat, text)
                if match:
                    subject = re.sub(r"[，。！!？?\s]", "", match.group(1))[:24]
                    if subject:
                        counter[subject] += 1
                        break
        return [subject for subject, _ in counter.most_common(5)]
