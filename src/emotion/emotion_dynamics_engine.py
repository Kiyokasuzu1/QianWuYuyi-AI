"""
Phase 3.5.28: Emotion Dynamics Engine
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from src.contracts.emotion_dynamics_schema import (
    EmotionTransitionRecord,
    EmotionalMemorySummary,
    EmotionDynamicsSnapshot,
)
from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_manager import EmotionManager
from src.emotion.emotion_pattern_analyzer import EmotionPatternAnalyzer


class EmotionDynamicsEngine:
    def __init__(self, manager: EmotionManager, history_limit: int = 300):
        self.manager = manager
        self.pattern_analyzer = EmotionPatternAnalyzer()
        self.history_limit = history_limit
        self._transition_history: List[EmotionTransitionRecord] = []
        self._persistent_mood: str = self.manager.get_context().mood if self.manager else "neutral"
        self._mood_streak: int = 0
        self._last_event_type: str = ""

    def process_event(self, event: EmotionEvent, memory_id: Optional[str] = None) -> Dict[str, Any]:
        before = self.manager.state.to_dict()
        payload = self.manager.process_event(event, memory_id=memory_id) or {}
        after = self.manager.state.to_dict()
        ctx = self.manager.get_context()
        trace = payload.get("trace")
        dominant_emotion = getattr(trace, "emotion", "") if trace is not None else ""

        transition = EmotionTransitionRecord(
            event_type=event.event_type,
            source=event.source,
            memory_id=memory_id or "",
            dominant_emotion=dominant_emotion,
            mood_after=ctx.mood,
            state_before=before,
            state_after=after,
        )
        self._push_transition(transition)
        self._last_event_type = event.event_type

        return {
            "transition": transition.to_dict(),
            "state": after,
            "context": ctx.to_dict(),
            "trace": trace.to_dict() if trace is not None else None,
            "snapshot": self.get_snapshot(),
            "emotional_memory": self.get_emotional_memory_summary(limit=100),
        }

    def get_transition_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._transition_history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_emotional_memory_summary(self, limit: int = 200) -> Dict[str, Any]:
        traces = self.manager.get_recent_traces(limit=limit)
        if not traces:
            return EmotionalMemorySummary().to_dict()

        emotion_counter = Counter(t.emotion for t in traces)
        dominant = [
            {"emotion": emotion, "count": count}
            for emotion, count in emotion_counter.most_common(5)
        ]
        memory_ids = []
        seen = set()
        for trace in reversed(traces):
            mid = getattr(trace, "memory_id", None)
            if mid and mid not in seen:
                seen.add(mid)
                memory_ids.append(mid)
            if len(memory_ids) >= 10:
                break
        patterns = [p.to_dict() for p in self.pattern_analyzer.analyze(traces)]

        summary = EmotionalMemorySummary(
            total_traces=len(traces),
            dominant_emotions=dominant,
            recent_memory_ids=memory_ids,
            patterns=patterns,
        )
        return summary.to_dict()

    def get_snapshot(self) -> Dict[str, Any]:
        memory_summary = self.get_emotional_memory_summary(limit=100)
        snap = EmotionDynamicsSnapshot(
            total_transitions=len(self._transition_history),
            persistent_mood=self._persistent_mood,
            mood_streak=self._mood_streak,
            last_event_type=self._last_event_type,
            emotional_memory_total=int(memory_summary.get("total_traces", 0)),
        )
        return snap.to_dict()

    def _push_transition(self, transition: EmotionTransitionRecord) -> None:
        self._transition_history.append(transition)
        if len(self._transition_history) > self.history_limit:
            self._transition_history = self._transition_history[-self.history_limit :]

        mood = transition.mood_after or "neutral"
        if mood == self._persistent_mood:
            self._mood_streak += 1
        else:
            self._persistent_mood = mood
            self._mood_streak = 1
