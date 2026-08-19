"""
Phase 3.5.27: Relationship Model + Intelligence Engine
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.contracts.relationship_intelligence_schema import (
    RelationshipInteractionRecord,
    RelationshipEmotionalPattern,
    SharedExperienceRecord,
    RelationshipMilestoneRecord,
    RelationshipModelSnapshot,
)
from src.relationship.relationship_event_extractor import RelationshipEventExtractor
from src.relationship.relationship_evaluator import RelationshipEvaluator
from src.relationship.relationship_change import RelationshipChange
from src.relationship.relationship_state import RelationshipState


@dataclass
class RelationshipModel:
    interaction_history: List[Dict[str, Any]] = field(default_factory=list)
    trust_changes: List[Dict[str, Any]] = field(default_factory=list)
    emotional_patterns: List[Dict[str, Any]] = field(default_factory=list)
    shared_experiences: List[Dict[str, Any]] = field(default_factory=list)
    relationship_milestones: List[Dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interaction_history": list(self.interaction_history),
            "trust_changes": list(self.trust_changes),
            "emotional_patterns": list(self.emotional_patterns),
            "shared_experiences": list(self.shared_experiences),
            "relationship_milestones": list(self.relationship_milestones),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipModel":
        return cls(
            interaction_history=list(data.get("interaction_history", []) or []),
            trust_changes=list(data.get("trust_changes", []) or []),
            emotional_patterns=list(data.get("emotional_patterns", []) or []),
            shared_experiences=list(data.get("shared_experiences", []) or []),
            relationship_milestones=list(data.get("relationship_milestones", []) or []),
            updated_at=str(data.get("updated_at", "") or ""),
        )

    def get_snapshot(self) -> Dict[str, Any]:
        top_emotions = sorted(
            self.emotional_patterns,
            key=lambda x: x.get("count", 0),
            reverse=True,
        )[:5]
        return RelationshipModelSnapshot(
            total_interactions=len(self.interaction_history),
            total_shared_experiences=len(self.shared_experiences),
            total_milestones=len(self.relationship_milestones),
            total_trust_changes=len(self.trust_changes),
            top_emotions=top_emotions,
        ).to_dict()


class RelationshipIntelligenceEngine:
    def __init__(self):
        self.extractor = RelationshipEventExtractor()
        self.evaluator = RelationshipEvaluator()

    def process_interaction(
        self,
        *,
        state: RelationshipState,
        model: RelationshipModel,
        user_message: str,
        evidence_id: str = "",
        emotion_tag: str = "",
    ) -> Dict[str, Any]:
        before_stage = state.relationship_stage
        event = self.extractor.extract(user_message, evidence_id=evidence_id)
        evaluation = self.evaluator.evaluate(event) if event is not None else None

        # Phase 4.2-A：event 为统一 TypedDict（dict），键访问提取字段
        if event is not None:
            ev_type = str(event.get("type", "") or "")
            ev_ts = str(event.get("created_at", "") or "")
            ev_conf = float(event.get("confidence", 0.0) or 0.0)
            ev_evidence = list(event.get("evidence_ids") or [])
            ev_content = str(event.get("content") or "")
        else:
            ev_type, ev_ts, ev_conf, ev_evidence, ev_content = "", "", 0.0, [], ""

        trust_delta = 0.0
        familiarity_delta = 0.0
        collaboration_delta = 0.0
        trust_change: Optional[RelationshipChange] = None
        shared_experience: Optional[SharedExperienceRecord] = None
        milestones: List[RelationshipMilestoneRecord] = []

        if evaluation and evaluation.passed and event is not None:
            trust_delta, familiarity_delta, collaboration_delta = self._plan_deltas(ev_type)
            prev_trust = state.trust
            prev_familiarity = state.familiarity
            prev_collab = state.collaboration

            if familiarity_delta > 0:
                state.familiarity = min(1.0, state.familiarity + familiarity_delta)
            if trust_delta > 0:
                state.trust = min(1.0, state.trust + trust_delta)
            if collaboration_delta > 0:
                state.collaboration = min(1.0, state.collaboration + collaboration_delta)

            state.interaction_frequency = min(1.0, max(state.interaction_frequency, 0.1) + 0.03)
            state.last_interaction_at = ev_ts
            state.relationship_stage = self._infer_stage(state)

            if state.trust != prev_trust:
                trust_change = RelationshipChange(
                    dimension="trust",
                    previous_value=prev_trust,
                    new_value=state.trust,
                    delta=round(state.trust - prev_trust, 4),
                    reason=ev_type,
                    confidence=ev_conf,
                    evidence_ids=list(ev_evidence),
                )

            if ev_type == "collaboration":
                shared_experience = SharedExperienceRecord(
                    topic="collaboration",
                    description=ev_content[:120],
                    evidence_ids=list(ev_evidence),
                    confidence=ev_conf,
                    timestamp=ev_ts,
                )

            if before_stage != state.relationship_stage:
                milestones.append(RelationshipMilestoneRecord(
                    milestone_type="relationship_stage_changed",
                    title=f"关系阶段进入 {state.relationship_stage}",
                    details={
                        "before_stage": before_stage,
                        "after_stage": state.relationship_stage,
                        "trust": state.trust,
                        "familiarity": state.familiarity,
                        "collaboration": state.collaboration,
                    },
                    evidence_ids=list(ev_evidence),
                    timestamp=ev_ts,
                ))

            if ev_type == "trust_building" and state.trust >= 0.6:
                milestones.append(RelationshipMilestoneRecord(
                    milestone_type="trust_threshold",
                    title="信任进入稳定区间",
                    details={"trust": state.trust},
                    evidence_ids=list(ev_evidence),
                    timestamp=ev_ts,
                ))

            if ev_type == "collaboration" and state.collaboration >= 0.5:
                milestones.append(RelationshipMilestoneRecord(
                    milestone_type="shared_workflow",
                    title="形成稳定协作模式",
                    details={"collaboration": state.collaboration},
                    evidence_ids=list(ev_evidence),
                    timestamp=ev_ts,
                ))

            state.updated_at = ev_ts

        record = RelationshipInteractionRecord(
            message_summary=user_message[:120],
            event_type=(ev_type if event is not None else ""),
            evidence_ids=list(ev_evidence) if event is not None else ([evidence_id] if evidence_id else []),
            emotion_tag=emotion_tag,
            trust_delta=round(trust_delta, 4),
            familiarity_delta=round(familiarity_delta, 4),
            collaboration_delta=round(collaboration_delta, 4),
            stage_after=state.relationship_stage,
        )
        model.interaction_history.append(record.to_dict())
        model.interaction_history = model.interaction_history[-300:]

        if trust_change is not None:
            model.trust_changes.append(trust_change.to_dict())
            model.trust_changes = model.trust_changes[-200:]

        if emotion_tag:
            self._update_emotional_pattern(model, emotion_tag, record.timestamp)

        if shared_experience is not None:
            model.shared_experiences.append(shared_experience.to_dict())
            model.shared_experiences = model.shared_experiences[-200:]

        if milestones:
            for ms in milestones:
                if not self._milestone_exists(model, ms):
                    model.relationship_milestones.append(ms.to_dict())
            model.relationship_milestones = model.relationship_milestones[-100:]

        model.updated_at = record.timestamp
        return {
            "event": dict(event) if event is not None else None,
            "evaluation": evaluation.__dict__ if evaluation is not None else None,
            "interaction": record.to_dict(),
            "trust_change": trust_change.to_dict() if trust_change is not None else None,
            "shared_experience": shared_experience.to_dict() if shared_experience is not None else None,
            "milestones": [m.to_dict() for m in milestones],
            "state": state.to_dict(),
            "model_snapshot": model.get_snapshot(),
        }

    def _update_emotional_pattern(self, model: RelationshipModel, emotion_tag: str, ts: str) -> None:
        for pattern in model.emotional_patterns:
            if pattern.get("emotion_tag") == emotion_tag:
                pattern["count"] = int(pattern.get("count", 0)) + 1
                pattern["last_seen"] = ts
                return
        model.emotional_patterns.append(RelationshipEmotionalPattern(
            emotion_tag=emotion_tag,
            count=1,
            last_seen=ts,
        ).to_dict())
        model.emotional_patterns = model.emotional_patterns[-100:]

    @staticmethod
    def _plan_deltas(event_type: str) -> tuple[float, float, float]:
        mapping = {
            "collaboration": (0.04, 0.05, 0.08),
            "trust_building": (0.06, 0.02, 0.0),
            "boundary_respect": (0.05, 0.01, 0.0),
            "preference_learning": (0.01, 0.06, 0.0),
        }
        return mapping.get(event_type, (0.0, 0.0, 0.0))

    @staticmethod
    def _infer_stage(state: RelationshipState) -> str:
        if state.trust >= 0.72 and state.collaboration >= 0.6 and state.familiarity >= 0.7:
            return "deep_collaboration"
        if state.trust >= 0.55 and state.familiarity >= 0.5:
            return "stable"
        if state.trust >= 0.25 or state.familiarity >= 0.25:
            return "developing"
        return "initial"

    @staticmethod
    def _milestone_exists(model: RelationshipModel, milestone: RelationshipMilestoneRecord) -> bool:
        for item in model.relationship_milestones:
            if (
                item.get("milestone_type") == milestone.milestone_type
                and item.get("title") == milestone.title
                and item.get("details", {}) == milestone.details
            ):
                return True
        return False
