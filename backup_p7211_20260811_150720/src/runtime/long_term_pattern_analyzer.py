"""
Phase 3.5.29: Long Term Pattern Analyzer
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from src.contracts.self_reflection_schema import LongTermPatternReport, TrendItem


class LongTermPatternAnalyzer:
    def analyze(
        self,
        *,
        memories: List[Dict[str, Any]],
        experiences: List[Any],
        emotional_patterns: List[Dict[str, Any]],
        relationship_changes: List[Dict[str, Any]],
        personality_changes: List[Dict[str, Any]],
        self_model: Dict[str, Any],
    ) -> LongTermPatternReport:
        interests = self._analyze_interests(memories)
        values = self._analyze_values(self_model)
        relationship = self._analyze_relationship(relationship_changes)
        emotion = self._analyze_emotions(emotional_patterns)
        personality = self._analyze_personality(personality_changes, experiences)

        report = LongTermPatternReport(
            total_trends=len(interests) + len(values) + len(relationship) + len(emotion) + len(personality),
            interest_changes=[x.to_dict() for x in interests],
            value_changes=[x.to_dict() for x in values],
            relationship_changes=[x.to_dict() for x in relationship],
            emotion_changes=[x.to_dict() for x in emotion],
            personality_changes=[x.to_dict() for x in personality],
        )
        return report

    def _analyze_interests(self, memories: List[Dict[str, Any]]) -> List[TrendItem]:
        counter = Counter()
        evidence_map: Dict[str, List[Dict[str, Any]]] = {}
        for mem in memories:
            text = str(mem.get("content", ""))
            subject = self._extract_interest_subject(text)
            if not subject:
                continue
            counter[subject] += 1
            evidence_map.setdefault(subject, []).append({"memory_id": mem.get("id", ""), "content": text[:80]})
        items = []
        for subject, count in counter.most_common(4):
            direction = "emerging" if count <= 2 else "increase"
            items.append(TrendItem(
                trend_type="interest",
                subject=subject,
                direction=direction,
                strength=min(1.0, 0.25 * count),
                evidence=evidence_map.get(subject, [])[:8],
            ))
        return items

    def _analyze_values(self, self_model: Dict[str, Any]) -> List[TrendItem]:
        items = []
        for value in (self_model.get("core_values", []) or [])[:6]:
            weight = float(value.get("weight", 0.0) or 0.0)
            direction = "stable" if weight >= 0.6 else "decrease"
            items.append(TrendItem(
                trend_type="value",
                subject=str(value.get("name", "") or value.get("value_id", "")),
                direction=direction,
                strength=weight,
                evidence=[{"weight": weight, "confidence": value.get("confidence", 0.0)}],
            ))
        return items

    def _analyze_relationship(self, relationship_changes: List[Dict[str, Any]]) -> List[TrendItem]:
        if not relationship_changes:
            return []
        counter = Counter(str(c.get("dimension", "")) for c in relationship_changes if c.get("dimension"))
        items = []
        for dim, count in counter.most_common(3):
            total_delta = sum(float(c.get("delta", 0.0) or 0.0) for c in relationship_changes if c.get("dimension") == dim)
            direction = "increase" if total_delta > 0 else "decrease" if total_delta < 0 else "stable"
            items.append(TrendItem(
                trend_type="relationship",
                subject=dim,
                direction=direction,
                strength=min(1.0, abs(total_delta)),
                evidence=[c for c in relationship_changes if c.get("dimension") == dim][:8],
            ))
        return items

    def _analyze_emotions(self, emotional_patterns: List[Dict[str, Any]]) -> List[TrendItem]:
        items = []
        for pattern in emotional_patterns[:5]:
            subject = str(pattern.get("emotion") or pattern.get("emotion_tag") or "")
            if not subject:
                continue
            count = int(pattern.get("count", 0) or 0)
            items.append(TrendItem(
                trend_type="emotion",
                subject=subject,
                direction="increase" if count >= 2 else "emerging",
                strength=min(1.0, 0.2 * count),
                evidence=[pattern],
            ))
        return items

    def _analyze_personality(self, personality_changes: List[Dict[str, Any]], experiences: List[Any]) -> List[TrendItem]:
        items = []
        trend_map: Dict[str, float] = {}
        evidence_map: Dict[str, List[Dict[str, Any]]] = {}
        for rec in personality_changes[-20:]:
            before = rec.get("trait_states_before", {}) or {}
            after = rec.get("trait_states_after", {}) or {}
            for key, bv in before.items():
                av = after.get(key)
                try:
                    b = float(bv.get("current_value", bv) if isinstance(bv, dict) else bv)
                    a = float(av.get("current_value", av) if isinstance(av, dict) else av)
                    trend_map[key] = trend_map.get(key, 0.0) + (a - b)
                    evidence_map.setdefault(key, []).append({"record_id": rec.get("record_id", ""), "delta": round(a - b, 4)})
                except Exception:
                    pass
        if not trend_map:
            # 回退：从 experiences 的 self_state 变化中推导
            for exp in experiences[-20:]:
                before = getattr(exp, "self_state_before", {}) or {}
                after = getattr(exp, "self_state_after", {}) or {}
                for key, bv in before.items():
                    if key not in after:
                        continue
                    try:
                        delta = float(after[key]) - float(bv)
                        trend_map[key] = trend_map.get(key, 0.0) + delta
                        evidence_map.setdefault(key, []).append({"experience_id": getattr(exp, "experience_id", ""), "delta": round(delta, 4)})
                    except Exception:
                        pass
        for key, total in sorted(trend_map.items(), key=lambda x: abs(x[1]), reverse=True)[:5]:
            items.append(TrendItem(
                trend_type="personality",
                subject=key,
                direction="increase" if total > 0 else "decrease" if total < 0 else "stable",
                strength=min(1.0, abs(total)),
                evidence=evidence_map.get(key, [])[:8],
            ))
        return items

    @staticmethod
    def _extract_interest_subject(text: str) -> str:
        for pat in [r"喜欢(.+)", r"热爱(.+)", r"想继续研究(.+)", r"对(.+)很感兴趣", r"持续创作(.+)"]:
            m = re.search(pat, text)
            if m:
                return re.sub(r"[，。！!？?\s]", "", m.group(1))[:32]
        return ""
