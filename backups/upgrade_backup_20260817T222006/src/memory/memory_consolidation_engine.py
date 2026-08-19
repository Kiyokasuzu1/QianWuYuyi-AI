"""
Phase 3.5.25: Memory Consolidation Engine

目标：
- 在不删除原始记忆的前提下，生成长期记忆视图
- 支持：
  episodic / semantic / identity / relationship / emotional memory
- 增加：
  memory decay / reinforcement / conflict resolution
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.long_term_memory_schema import (
    ConsolidatedMemory,
    MemoryConflictRecord,
    MemoryConsolidationReport,
    LongTermMemorySnapshot,
)
from src.memory.memory_verifier import MemoryVerifier


class MemoryConsolidationEngine:
    def __init__(self, half_life_days: float = 60.0, semantic_repeat_threshold: int = 2):
        self.half_life_days = half_life_days
        self.semantic_repeat_threshold = semantic_repeat_threshold
        self.verifier = MemoryVerifier()
        self._history: List[MemoryConsolidationReport] = []

    def consolidate(self, memories: List[Dict[str, Any]], limit: Optional[int] = None) -> MemoryConsolidationReport:
        if limit is not None:
            memories = memories[-limit:]

        verified = [self._verify_if_needed(m) for m in memories]
        grouped = self._group_by_canonical_key(verified)

        episodic: List[ConsolidatedMemory] = []
        semantic: List[ConsolidatedMemory] = []
        identity: List[ConsolidatedMemory] = []
        relationship: List[ConsolidatedMemory] = []
        emotional: List[ConsolidatedMemory] = []
        conflicts = self._detect_conflicts(verified)

        for key, group in grouped.items():
            leader = self._select_leader(group)
            mc = leader.get("memory_class", "unknown")
            reinforcement = len(group)
            truth = max(float(leader.get("truth", 0.0) or 0.0), 0.0)
            decay = self._decay_score(leader)

            consolidated = ConsolidatedMemory(
                memory_type=self._memory_type_for(leader, reinforcement),
                canonical_key=key,
                content=str(leader.get("content", "")),
                source_ids=[str(x.get("id", "")) for x in group if x.get("id")],
                truth=round(min(1.0, truth + min(0.2, 0.03 * (reinforcement - 1))), 4),
                reinforcement_count=reinforcement,
                decay_score=round(decay, 4),
                metadata={
                    "memory_class": mc,
                    "emotion_tag": leader.get("emotion_tag", "") or leader.get("metadata", {}).get("emotion_tag", ""),
                },
            )

            mtype = consolidated.memory_type
            if mtype == "identity":
                identity.append(consolidated)
            elif mtype == "relationship":
                relationship.append(consolidated)
            elif mtype == "emotional":
                emotional.append(consolidated)
            elif mtype == "semantic":
                semantic.append(consolidated)
            else:
                episodic.append(consolidated)

        report = MemoryConsolidationReport(
            episodic_memories=[m.to_dict() for m in episodic],
            semantic_memories=[m.to_dict() for m in semantic],
            identity_memories=[m.to_dict() for m in identity],
            relationship_memories=[m.to_dict() for m in relationship],
            emotional_memories=[m.to_dict() for m in emotional],
            conflicts=[c.to_dict() for c in conflicts],
            stats={
                "input_count": len(memories),
                "verified_count": len(verified),
                "episodic_count": len(episodic),
                "semantic_count": len(semantic),
                "identity_count": len(identity),
                "relationship_count": len(relationship),
                "emotional_count": len(emotional),
                "conflict_count": len(conflicts),
            },
        )
        self._history.append(report)
        self._history = self._history[-200:]
        return report

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        last = self._history[-1] if self._history else None
        snap = LongTermMemorySnapshot(
            total_reports=len(self._history),
            total_conflicts=sum(len(r.conflicts) for r in self._history),
            last_report_id=last.report_id if last else "",
        )
        return snap.to_dict()

    # ================= internal =================

    def _verify_if_needed(self, memory: Dict[str, Any]) -> Dict[str, Any]:
        if memory.get("memory_class"):
            return dict(memory)
        return self.verifier.verify(memory)

    def _group_by_canonical_key(self, memories: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for m in memories:
            key = self._canonical_key(m)
            grouped.setdefault(key, []).append(m)
        return grouped

    def _canonical_key(self, memory: Dict[str, Any]) -> str:
        mc = str(memory.get("memory_class", "unknown"))
        content = str(memory.get("content", "")).strip().lower()
        content = re.sub(r"\s+", "", content)
        content = content[:64]
        return f"{mc}:{content}"

    def _memory_type_for(self, memory: Dict[str, Any], reinforcement: int) -> str:
        mc = str(memory.get("memory_class", "unknown"))
        if mc == "identity":
            return "identity"
        if mc == "relationship":
            return "relationship"
        if mc == "emotion_candidate" or memory.get("emotion_tag") or memory.get("metadata", {}).get("emotion_tag"):
            return "emotional"
        if reinforcement >= self.semantic_repeat_threshold and mc in {"event", "preference", "growth_memory"}:
            return "semantic"
        return "episodic"

    def _select_leader(self, group: List[Dict[str, Any]]) -> Dict[str, Any]:
        def score(m: Dict[str, Any]) -> float:
            truth = float(m.get("truth", m.get("metadata", {}).get("truth", 0.0)) or 0.0)
            age_bonus = self._decay_score(m)
            return truth * 0.7 + age_bonus * 0.3
        return sorted(group, key=score, reverse=True)[0]

    def _decay_score(self, memory: Dict[str, Any]) -> float:
        ts = str(memory.get("timestamp", "") or memory.get("updated_at", "") or "")
        if not ts:
            return 0.5
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
            mc = str(memory.get("memory_class", "unknown"))
            base = math.exp(-age_days / max(1.0, self.half_life_days))
            if mc in {"identity", "relationship"}:
                return max(base, 0.72)
            return base
        except Exception:
            return 0.5

    def _detect_conflicts(self, memories: List[Dict[str, Any]]) -> List[MemoryConflictRecord]:
        conflicts: List[MemoryConflictRecord] = []
        preference_map: Dict[str, List[Tuple[str, str, Dict[str, Any]]]] = {}

        for m in memories:
            subject, polarity = self._extract_preference_subject(m)
            if not subject or not polarity:
                continue
            preference_map.setdefault(subject, []).append((polarity, str(m.get("id", "")), m))

        for subject, items in preference_map.items():
            polarities = {p for p, _, _ in items}
            if len(polarities) < 2:
                continue
            chosen = self._select_leader([m for _, _, m in items])
            conflicts.append(MemoryConflictRecord(
                conflict_type="preference_conflict",
                subject=subject,
                candidate_ids=[mid for _, mid, _ in items if mid],
                chosen_id=str(chosen.get("id", "") or ""),
                resolution_reason="preferred higher truth/recent memory while preserving all originals",
            ))
        return conflicts

    def _extract_preference_subject(self, memory: Dict[str, Any]) -> Tuple[str, str]:
        if str(memory.get("memory_class", "")) != "preference":
            return "", ""
        text = str(memory.get("content", ""))
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
