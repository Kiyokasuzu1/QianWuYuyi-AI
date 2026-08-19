"""
Phase 3.5.24: Autonomous Scheduler

职责：
- 调度何时进行记忆维护
- 调度何时进行反思
- 调度何时进行身份稳定性检查
- 调度何时进行成长评估（仅评估 pending proposal，不自动接受）

约束：
- 不改变人格
- 不自动接受 GrowthProposal
- 只负责调度与审计
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.contracts.autonomous_scheduler_schema import (
    SchedulerTaskAudit,
    AutonomousSchedulerSnapshot,
)


@dataclass
class AutonomousSchedulerConfig:
    enabled: bool = False
    history_limit: int = 400

    memory_maintenance_interval_ticks: int = 10
    identity_check_interval_ticks: int = 15
    growth_evaluation_interval_ticks: int = 8
    reflection_check_interval_ticks: int = 1

    min_memories_for_maintenance: int = 5
    proposal_evaluation_batch: int = 3


class AutonomousScheduler:
    def __init__(self, config: Optional[AutonomousSchedulerConfig] = None):
        self.cfg = config or AutonomousSchedulerConfig()
        self._history: List[SchedulerTaskAudit] = []
        self._tick_counter = 0
        self._last_memory_tick = 0
        self._last_identity_tick = 0
        self._last_growth_eval_tick = 0
        self._last_reflection_tick = 0

    def on_tick(self, runtime: Any) -> List[SchedulerTaskAudit]:
        if not self.cfg.enabled:
            return []
        self._tick_counter += 1
        audits: List[SchedulerTaskAudit] = []

        if self._should_run(self._last_memory_tick, self.cfg.memory_maintenance_interval_ticks):
            rec = self._run_memory_maintenance(runtime)
            audits.append(rec)
            self._last_memory_tick = self._tick_counter

        if self._should_run(self._last_identity_tick, self.cfg.identity_check_interval_ticks):
            rec = self._run_identity_check(runtime)
            audits.append(rec)
            self._last_identity_tick = self._tick_counter

        if self._should_run(self._last_growth_eval_tick, self.cfg.growth_evaluation_interval_ticks):
            rec = self._run_growth_evaluation(runtime)
            audits.append(rec)
            self._last_growth_eval_tick = self._tick_counter

        if self._should_run(self._last_reflection_tick, self.cfg.reflection_check_interval_ticks):
            rec = self._run_reflection(runtime)
            if rec:
                audits.append(rec)
            self._last_reflection_tick = self._tick_counter

        for rec in audits:
            self._push(rec)
        return audits

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        last = self._history[-1] if self._history else None
        snap = AutonomousSchedulerSnapshot(
            total_tasks=len(self._history),
            executed_tasks=sum(1 for r in self._history if r.executed),
            failed_tasks=sum(1 for r in self._history if r.executed and not r.success),
            last_task_type=last.task_type if last else "",
            last_action=last.action if last else "",
        )
        return snap.to_dict()

    # ================= internal =================

    def _run_memory_maintenance(self, runtime: Any) -> SchedulerTaskAudit:
        try:
            store = runtime.memory_adapter.get_memory_store() if getattr(runtime, "memory_adapter", None) else None
            memories = store.load() if store else []
            if len(memories) < self.cfg.min_memories_for_maintenance:
                return SchedulerTaskAudit(
                    task_type="memory_maintenance",
                    action="skip",
                    reason=f"memory_count={len(memories)} < {self.cfg.min_memories_for_maintenance}",
                    inputs={"memory_count": len(memories)},
                    executed=False,
                )
            if hasattr(runtime, "run_memory_consolidation"):
                report = runtime.run_memory_consolidation(limit=20)
                return SchedulerTaskAudit(
                    task_type="memory_maintenance",
                    action="run",
                    reason="memory consolidation executed",
                    inputs={"memory_count": len(memories)},
                    outputs={"report": report},
                    executed=True,
                    success=True,
                )
            ranked = []
            if getattr(runtime, "memory_relevance_evaluator", None):
                ranked = runtime.memory_relevance_evaluator.rank_memories(
                    memories[-20:],
                    query="长期记忆整理",
                    context={},
                    top_k=5,
                )
            return SchedulerTaskAudit(
                task_type="memory_maintenance",
                action="run",
                reason="memory maintenance executed",
                inputs={"memory_count": len(memories)},
                outputs={
                    "ranked_count": len(ranked),
                    "top_memory_ids": [r.get("id", "") for r in ranked[:5]],
                },
                executed=True,
                success=True,
            )
        except Exception as e:
            return SchedulerTaskAudit(
                task_type="memory_maintenance",
                action="run",
                reason="memory maintenance failed",
                executed=True,
                success=False,
                error=str(e),
            )

    def _run_identity_check(self, runtime: Any) -> SchedulerTaskAudit:
        try:
            report = runtime.refresh_identity_stability(force=True)
            return SchedulerTaskAudit(
                task_type="identity_check",
                action="run",
                reason="periodic identity check",
                outputs={"report": report},
                executed=True,
                success=True,
            )
        except Exception as e:
            return SchedulerTaskAudit(
                task_type="identity_check",
                action="run",
                reason="identity check failed",
                executed=True,
                success=False,
                error=str(e),
            )

    def _run_growth_evaluation(self, runtime: Any) -> SchedulerTaskAudit:
        try:
            proposals = runtime.get_growth_proposals(status="proposed", limit=self.cfg.proposal_evaluation_batch) or []
            if not proposals:
                return SchedulerTaskAudit(
                    task_type="growth_evaluation",
                    action="skip",
                    reason="no pending proposals",
                    executed=False,
                )
            results = []
            for proposal in proposals:
                ev = runtime.evaluate_growth_proposal(proposal)
                results.append(ev.to_dict() if ev else {"proposal_id": getattr(proposal, "id", ""), "evaluation": None})
            return SchedulerTaskAudit(
                task_type="growth_evaluation",
                action="run",
                reason="evaluated pending proposals",
                outputs={"evaluations": results},
                executed=True,
                success=True,
            )
        except Exception as e:
            return SchedulerTaskAudit(
                task_type="growth_evaluation",
                action="run",
                reason="growth evaluation failed",
                executed=True,
                success=False,
                error=str(e),
            )

    def _run_reflection(self, runtime: Any) -> Optional[SchedulerTaskAudit]:
        if not getattr(runtime, "reflection_scheduler", None):
            return None
        try:
            check = runtime.check_reflection_trigger()
            if not check:
                return SchedulerTaskAudit(
                    task_type="reflection",
                    action="skip",
                    reason="reflection scheduler unavailable",
                    executed=False,
                )
            if not bool(check.get("should_trigger", False)):
                return SchedulerTaskAudit(
                    task_type="reflection",
                    action="skip",
                    reason=str(check.get("reason", "")),
                    inputs={"trigger_check": check},
                    executed=False,
                )
            task = runtime.run_scheduled_reflection()
            return SchedulerTaskAudit(
                task_type="reflection",
                action="run",
                reason=str(check.get("reason", "")),
                inputs={"trigger_check": check},
                outputs={"task": task},
                executed=True,
                success=True,
            )
        except Exception as e:
            return SchedulerTaskAudit(
                task_type="reflection",
                action="run",
                reason="reflection execution failed",
                executed=True,
                success=False,
                error=str(e),
            )

    def _should_run(self, last_tick: int, interval: int) -> bool:
        return interval > 0 and (self._tick_counter - last_tick) >= interval

    def _push(self, rec: SchedulerTaskAudit) -> None:
        self._history.append(rec)
        if len(self._history) > self.cfg.history_limit:
            self._history = self._history[-self.cfg.history_limit :]
