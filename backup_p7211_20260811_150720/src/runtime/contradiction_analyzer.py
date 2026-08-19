"""
Phase 3.5.29: Contradiction Analyzer
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from src.contracts.self_reflection_schema import ContradictionItem, ContradictionReport


class ContradictionAnalyzer:
    def analyze(
        self,
        *,
        memories: List[Dict[str, Any]],
        experiences: List[Any],
        self_model: Dict[str, Any],
        emotional_patterns: List[Dict[str, Any]],
        personality_changes: List[Dict[str, Any]],
    ) -> ContradictionReport:
        items: List[ContradictionItem] = []
        items.extend(self._analyze_memory_conflicts(memories))
        items.extend(self._analyze_behavior_value_conflicts(experiences, self_model))
        items.extend(self._analyze_personality_change_anomalies(personality_changes))
        items.extend(self._analyze_emotional_pattern_anomalies(emotional_patterns))

        severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
        highest = "none"
        for item in items:
            if severity_rank.get(item.severity, 0) > severity_rank.get(highest, 0):
                highest = item.severity

        report = ContradictionReport(
            total_items=len(items),
            highest_severity=highest,
            items=[i.to_dict() for i in items],
        )
        return report

    def _analyze_memory_conflicts(self, memories: List[Dict[str, Any]]) -> List[ContradictionItem]:
        preference_map: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        for mem in memories:
            text = str(mem.get("content", ""))
            subject, polarity = self._extract_preference(text)
            if subject and polarity:
                preference_map.setdefault(subject, []).append((polarity, mem))

        items: List[ContradictionItem] = []
        for subject, entries in preference_map.items():
            polarities = {p for p, _ in entries}
            if len(polarities) >= 2:
                items.append(ContradictionItem(
                    contradiction_type="memory_conflict",
                    severity="medium",
                    description=f"关于“{subject}”的记忆出现正负冲突",
                    evidence=[
                        {"memory_id": e.get("id", ""), "content": e.get("content", ""), "polarity": p}
                        for p, e in entries[:8]
                    ],
                ))
        return items

    def _analyze_behavior_value_conflicts(self, experiences: List[Any], self_model: Dict[str, Any]) -> List[ContradictionItem]:
        items: List[ContradictionItem] = []
        core_values = self_model.get("core_values", []) or []
        value_names = [str(v.get("name", "") or v.get("value_id", "")).lower() for v in core_values]
        has_restraint = any(k in name for name in value_names for k in ("克制", "稳定", "边界", "尊重"))

        if not has_restraint:
            return items

        proactive_failures = []
        for exp in experiences:
            action_type = getattr(exp, "action_type", "")
            result = getattr(exp, "result", None)
            if action_type == "send_message" and result is not None and not getattr(result, "response_received", False):
                proactive_failures.append(exp)

        if len(proactive_failures) >= 3:
            items.append(ContradictionItem(
                contradiction_type="behavior_value",
                severity="medium",
                description="主动行为频繁但缺少回应，可能与克制/边界类价值存在张力",
                evidence=[
                    {
                        "experience_id": getattr(e, "experience_id", ""),
                        "action_type": getattr(e, "action_type", ""),
                        "response_received": getattr(getattr(e, "result", None), "response_received", False),
                    }
                    for e in proactive_failures[:8]
                ],
            ))
        return items

    def _analyze_personality_change_anomalies(self, personality_changes: List[Dict[str, Any]]) -> List[ContradictionItem]:
        items: List[ContradictionItem] = []
        abnormal = []
        for rec in personality_changes[-10:]:
            before = rec.get("trait_states_before", {}) or {}
            after = rec.get("trait_states_after", {}) or {}
            for key, bv in before.items():
                av = after.get(key)
                try:
                    b = float(bv.get("current_value", bv) if isinstance(bv, dict) else bv)
                    a = float(av.get("current_value", av) if isinstance(av, dict) else av)
                    delta = abs(a - b)
                    if delta > 0.2:
                        abnormal.append({"trait": key, "delta": round(delta, 4), "record_id": rec.get("record_id", "")})
                except Exception:
                    pass
        if abnormal:
            items.append(ContradictionItem(
                contradiction_type="personality_change",
                severity="high",
                description="近期人格变化幅度偏大，存在异常漂移风险",
                evidence=abnormal[:12],
            ))
        return items

    def _analyze_emotional_pattern_anomalies(self, emotional_patterns: List[Dict[str, Any]]) -> List[ContradictionItem]:
        items: List[ContradictionItem] = []
        by_emotion = {str(p.get("emotion") or p.get("emotion_tag") or ""): int(p.get("count", 0) or 0) for p in emotional_patterns}
        positive = by_emotion.get("joy", 0) + by_emotion.get("trust", 0)
        negative = by_emotion.get("sadness", 0) + by_emotion.get("fear", 0) + by_emotion.get("anger", 0)
        if positive >= 3 and negative >= 3:
            items.append(ContradictionItem(
                contradiction_type="emotional_pattern",
                severity="low",
                description="近期同时出现较高正向与负向情绪模式，可能存在复杂未解读状态",
                evidence=[{"emotion": k, "count": v} for k, v in by_emotion.items() if v > 0],
            ))
        return items

    @staticmethod
    def _extract_preference(text: str) -> Tuple[str, str]:
        patterns = [
            (r"不喜欢(.+)", "negative"),
            (r"讨厌(.+)", "negative"),
            (r"喜欢(.+)", "positive"),
            (r"热爱(.+)", "positive"),
            (r"偏爱(.+)", "positive"),
        ]
        for pat, polarity in patterns:
            m = re.search(pat, text)
            if m:
                subject = re.sub(r"[，。！!？?\s]", "", m.group(1))[:32]
                return subject, polarity
        return "", ""
