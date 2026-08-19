"""
Phase 3.5.30: Curiosity Engine
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from src.contracts.curiosity_schema import LearningGoal, CuriositySnapshot


class CuriosityEngine:
    """
    只生成 LearningGoal，不直接修改知识与人格。
    """

    def __init__(self, history_limit: int = 200):
        self.history_limit = history_limit
        self._history: List[LearningGoal] = []

    def generate_goals(
        self,
        *,
        memories: List[Dict[str, Any]],
        reflection_history: List[Dict[str, Any]],
        contradiction_report: Dict[str, Any],
        self_model: Dict[str, Any],
    ) -> List[LearningGoal]:
        goals: List[LearningGoal] = []

        goals.extend(self._goals_from_interests(memories))
        goals.extend(self._goals_from_unknowns(memories, reflection_history))
        goals.extend(self._goals_from_contradictions(contradiction_report))
        goals.extend(self._goals_from_identity(self_model))

        dedup: Dict[str, LearningGoal] = {}
        for goal in goals:
            key = f"{goal.goal_type}:{goal.topic}"
            if key not in dedup or goal.confidence > dedup[key].confidence:
                dedup[key] = goal
        result = list(dedup.values())[:8]
        self._history.extend(result)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
        return result

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [g.to_dict() for g in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        topics = []
        seen = set()
        for item in reversed(self._history):
            if item.topic and item.topic not in seen:
                seen.add(item.topic)
                topics.append(item.topic)
            if len(topics) >= 5:
                break
        return CuriositySnapshot(
            total_goals=len(self._history),
            active_topics=topics,
            last_goal_id=self._history[-1].goal_id if self._history else "",
        ).to_dict()

    def _goals_from_interests(self, memories: List[Dict[str, Any]]) -> List[LearningGoal]:
        counter = Counter()
        evidence_map: Dict[str, List[Dict[str, Any]]] = {}
        for mem in memories:
            subject = self._extract_subject(str(mem.get("content", "")))
            if not subject:
                continue
            counter[subject] += 1
            evidence_map.setdefault(subject, []).append({"memory_id": mem.get("id", ""), "content": mem.get("content", "")[:80]})
        goals = []
        for subject, count in counter.most_common(3):
            goals.append(LearningGoal(
                goal_type="interest_discovery",
                topic=subject,
                reason=f"该主题在近期记忆中重复出现 {count} 次，值得继续探索。",
                questions=[
                    f"为什么我会持续被“{subject}”吸引？",
                    f"“{subject}”和我的长期价值有什么关系？",
                ],
                evidence=evidence_map.get(subject, [])[:6],
                confidence=min(0.9, 0.25 * count + 0.2),
            ))
        return goals

    def _goals_from_unknowns(self, memories: List[Dict[str, Any]], reflection_history: List[Dict[str, Any]]) -> List[LearningGoal]:
        candidates = []
        for mem in memories[-12:]:
            text = str(mem.get("content", ""))
            if any(k in text for k in ("为什么", "怎么", "如何", "未知", "还不理解", "想知道")):
                candidates.append({"memory_id": mem.get("id", ""), "content": text[:100]})
        if not candidates:
            for item in reflection_history[:6]:
                text = str(item.get("uncertainty", "") or "")
                if text:
                    candidates.append({"reflection_id": item.get("insight_id", ""), "content": text[:100]})
        if not candidates:
            return []
        return [LearningGoal(
            goal_type="unknown_exploration",
            topic="unknown_area",
            reason="近期记忆或反思中反复出现未解释的空白区域。",
            questions=[
                "哪些问题我其实还没有真正理解？",
                "这些未知点是否值得形成长期学习目标？",
            ],
            evidence=candidates[:8],
            confidence=0.62,
        )]

    def _goals_from_contradictions(self, contradiction_report: Dict[str, Any]) -> List[LearningGoal]:
        items = contradiction_report.get("items", []) or []
        if not items:
            return []
        top = items[0]
        return [LearningGoal(
            goal_type="contradiction_resolution",
            topic=top.get("contradiction_type", "contradiction"),
            reason="近期出现潜在矛盾，值得进入主动澄清与学习。",
            questions=[
                "这个矛盾是阶段性变化，还是长期身份冲突？",
                "需要继续收集什么证据，才能理解这个矛盾？",
            ],
            evidence=items[:6],
            confidence=0.7 if contradiction_report.get("highest_severity") in {"medium", "high"} else 0.55,
        )]

    def _goals_from_identity(self, self_model: Dict[str, Any]) -> List[LearningGoal]:
        values = self_model.get("core_values", []) or []
        if not values:
            return []
        weakest = sorted(values, key=lambda x: float(x.get("weight", 0.0) or 0.0))[0]
        topic = str(weakest.get("name", "") or weakest.get("value_id", "identity_value"))
        return [LearningGoal(
            goal_type="question_generation",
            topic=topic,
            reason="较弱或尚未稳定的价值维度值得继续理解。",
            questions=[
                f"为什么“{topic}”在当前自我模型里还不够稳定？",
                f"未来哪些经历可能帮助我理解“{topic}”？",
            ],
            evidence=[{"value": topic, "weight": weakest.get("weight", 0.0), "confidence": weakest.get("confidence", 0.0)}],
            confidence=0.58,
        )]

    @staticmethod
    def _extract_subject(text: str) -> str:
        for pat in [r"喜欢(.+)", r"热爱(.+)", r"研究(.+)", r"创作(.+)", r"探索(.+)"]:
            m = re.search(pat, text)
            if m:
                return re.sub(r"[，。！!？?\s]", "", m.group(1))[:32]
        return ""
