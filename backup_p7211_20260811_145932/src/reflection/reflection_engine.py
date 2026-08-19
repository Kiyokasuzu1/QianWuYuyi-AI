"""
反思引擎 v3.1
增加 trait_changes 兼容处理，防止旧数据崩溃
"""
import uuid
from datetime import datetime
from typing import List, Protocol

from src.reflection.reflection_candidate import ReflectionCandidate
from src.reflection.reflection_record import ReflectionRecord, ReflectionLevel
from src.reflection.reflection_evaluator import ReflectionEvaluator
from src.reflection.reflection_safety import ReflectionSafetyEvaluator


class CandidateGenerator(Protocol):
    def generate(self, events: List) -> List[ReflectionCandidate]:
        ...


class RuleBasedCandidateGenerator:
    def generate(self, events: List) -> List[ReflectionCandidate]:
        candidates = []
        for event in events:
            trait_changes = getattr(event, "trait_changes", [])
            changes = []
            for change in trait_changes:
                # 兼容对象和字典
                if isinstance(change, dict):
                    delta = change.get("delta", 0)
                    dimension = change.get("dimension", "unknown")
                else:
                    delta = getattr(change, "delta", 0)
                    dimension = getattr(change, "dimension", "unknown")
                if delta > 0:
                    changes.append(f"{dimension}提高")
            if not changes:
                continue

            causal_chain = [
                f"经历: {event.description}",
                *[f"维度变化: {c}" for c in changes]
            ]

            candidate = ReflectionCandidate(
                source_event_ids=[event.id],
                possible_changes=changes,
                possible_beliefs=[],
                event_summary=event.description,
                previous_self_view="",
                tentative_understanding=f"因为{event.description}，我的{', '.join(changes)}了",
                causal_chain=causal_chain,
                confidence=min(1.0, 0.5 + event.significance * 0.5)
            )
            candidates.append(candidate)
        return candidates


class ReflectionEngine:
    def __init__(self,
                 candidate_generator: CandidateGenerator,
                 evaluator: ReflectionEvaluator,
                 safety_evaluator: ReflectionSafetyEvaluator):
        self.generator = candidate_generator
        self.evaluator = evaluator
        self.safety_evaluator = safety_evaluator

    def process_events(self, recent_events: List) -> List[ReflectionRecord]:
        records = []
        candidates = self.generator.generate(recent_events)

        for cand in candidates:
            record = self._candidate_to_record(cand)
            depth = self.evaluator.evaluate(record)
            record.reflection_level = depth.value
            safety_result = self.safety_evaluator.evaluate(record)
            record.is_safe = safety_result.is_safe
            record.contains_dependency = safety_result.contains_dependency
            record.contains_exaggeration = safety_result.contains_exaggeration
            records.append(record)

        return records

    def _candidate_to_record(self, cand: ReflectionCandidate) -> ReflectionRecord:
        return ReflectionRecord(
            reflection_id=f"ref_{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now().isoformat(),
            source_event_ids=cand.source_event_ids,
            event_summary=cand.event_summary,
            previous_self_view=cand.previous_self_view,
            current_understanding=cand.tentative_understanding,
            self_change=cand.possible_changes,
            new_beliefs=cand.possible_beliefs,
            causal_chain=cand.causal_chain,
            confidence=cand.confidence,
            reflection_level=ReflectionLevel.OBSERVATION.value,
            is_safe=True,
            contains_dependency=False,
            contains_exaggeration=False
        )