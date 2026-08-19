"""
Phase 3.5.14: Runtime Lifecycle Orchestrator

职责：
- 管理 Experience → Memory → Reflection → Evaluation → Growth → Identity 的闭环状态
- 防止明显无效的阶段跳转
- 输出统一的 lifecycle logs

说明：
- 这里管理的是“认知闭环生命周期”
- 不替代 `LifecycleManager` 的模块启停职责
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from src.contracts.lifecycle_schema import (
    RuntimeLifecycleEvent,
    RuntimeLifecycleState,
    STAGE_IDLE,
    STAGE_EXPERIENCE_RECEIVED,
    STAGE_MEMORY_CREATED,
    STAGE_REFLECTION_STARTED,
    STAGE_EVALUATION_COMPLETED,
    STAGE_PROPOSAL_CREATED,
    STAGE_PROPOSAL_APPLIED,
)


class RuntimeLifecycleOrchestrator:
    """运行时认知生命周期状态机。"""

    def __init__(self, history_limit: int = 500):
        self._state = RuntimeLifecycleState()
        self._history: List[RuntimeLifecycleEvent] = []
        self._history_limit = history_limit

        self._allowed_transitions: Dict[str, set[str]] = {
            STAGE_IDLE: {STAGE_EXPERIENCE_RECEIVED, STAGE_REFLECTION_STARTED},
            STAGE_EXPERIENCE_RECEIVED: {STAGE_MEMORY_CREATED, STAGE_REFLECTION_STARTED, STAGE_EXPERIENCE_RECEIVED},
            STAGE_MEMORY_CREATED: {STAGE_REFLECTION_STARTED, STAGE_EXPERIENCE_RECEIVED},
            STAGE_REFLECTION_STARTED: {STAGE_EVALUATION_COMPLETED, STAGE_PROPOSAL_CREATED, STAGE_EXPERIENCE_RECEIVED},
            STAGE_EVALUATION_COMPLETED: {STAGE_PROPOSAL_CREATED, STAGE_EXPERIENCE_RECEIVED, STAGE_REFLECTION_STARTED},
            STAGE_PROPOSAL_CREATED: {STAGE_PROPOSAL_APPLIED, STAGE_EXPERIENCE_RECEIVED, STAGE_REFLECTION_STARTED},
            STAGE_PROPOSAL_APPLIED: {STAGE_EXPERIENCE_RECEIVED, STAGE_REFLECTION_STARTED},
        }

    def record_event(
        self,
        event_name: str,
        *,
        source: str = "runtime",
        source_id: str = "",
        details: Optional[Dict] = None,
        force: bool = False,
    ) -> RuntimeLifecycleEvent:
        before = self._state.current_state
        allowed = event_name in self._allowed_transitions.get(before, set())
        success = force or allowed

        event = RuntimeLifecycleEvent(
            event_name=event_name,
            before_state=before,
            after_state=event_name if success else before,
            success=success,
            source=source,
            source_id=source_id,
            details=details or {},
            error="" if success else f"invalid transition: {before} -> {event_name}",
        )

        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        self._state.timestamp = event.timestamp
        self._state.last_event_name = event_name
        if success:
            self._state.current_state = event_name
            self._state.transition_count += 1
            self._apply_event_to_state(event_name, source_id, details or {})
        else:
            self._state.invalid_transition_count += 1

        return event

    def get_state(self) -> RuntimeLifecycleState:
        return self._state

    def get_history(self, limit: int = 50) -> List[RuntimeLifecycleEvent]:
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def clear_history(self) -> int:
        n = len(self._history)
        self._history.clear()
        self._state = RuntimeLifecycleState()
        return n

    def can_transition(self, event_name: str) -> bool:
        return event_name in self._allowed_transitions.get(self._state.current_state, set())

    def _apply_event_to_state(self, event_name: str, source_id: str, details: Dict) -> None:
        if event_name == STAGE_EXPERIENCE_RECEIVED:
            self._state.last_experience_id = source_id or details.get("experience_id", "")
        elif event_name == STAGE_MEMORY_CREATED:
            self._state.last_memory_experience_id = source_id or details.get("experience_id", "")
        elif event_name == STAGE_REFLECTION_STARTED:
            self._state.last_insight_id = source_id or details.get("insight_id", "")
        elif event_name == STAGE_EVALUATION_COMPLETED:
            self._state.last_evaluation_id = source_id or details.get("evaluation_id", "")
        elif event_name == STAGE_PROPOSAL_CREATED:
            proposal_ids = details.get("proposal_ids", []) or []
            self._state.last_proposal_id = source_id or (proposal_ids[0] if proposal_ids else "")
        elif event_name == STAGE_PROPOSAL_APPLIED:
            self._state.last_applied_proposal_id = source_id or details.get("proposal_id", "")
