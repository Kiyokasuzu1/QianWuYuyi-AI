"""
Phase 3.5.19: Autonomous Decision Layer

目标：
- 在不使用 LLM 的前提下，基于当前运行时指标做可审计决策
- 决定何时触发反思 / 何时刷新身份稳定性 / 何时保持稳定

约束：
- 不自动接受 GrowthProposal
- 不启动后台线程（由 RuntimeCore.tick 驱动）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.contracts.autonomous_decision_schema import (
    AutonomousDecisionRecord,
    AutonomousDecisionSnapshot,
)


@dataclass
class AutonomousDecisionConfig:
    enabled: bool = False

    # reflection control
    max_pending_proposals: int = 5
    min_identity_stability: float = 0.75

    # stability refresh
    stability_refresh_interval_ticks: int = 30

    history_limit: int = 300


class AutonomousDecisionLayer:
    def __init__(self, config: Optional[AutonomousDecisionConfig] = None):
        self.cfg = config or AutonomousDecisionConfig()
        self._history: List[AutonomousDecisionRecord] = []
        self._tick_counter: int = 0
        self._last_stability_refresh_tick: int = 0

    def on_tick(self, runtime: Any) -> Optional[AutonomousDecisionRecord]:
        if not self.cfg.enabled:
            return None
        self._tick_counter += 1

        # 1) Periodic identity stability refresh
        if self._tick_counter - self._last_stability_refresh_tick >= self.cfg.stability_refresh_interval_ticks:
            rec = self._refresh_stability(runtime)
            self._last_stability_refresh_tick = self._tick_counter
            self._push(rec)
            return rec

        # 2) Reflection trigger decision
        rec = self._maybe_trigger_reflection(runtime)
        if rec:
            self._push(rec)
        return rec

    def on_event(self, runtime: Any, importance: float = 0.0) -> Optional[AutonomousDecisionRecord]:
        if not self.cfg.enabled:
            return None
        # currently no special event-level action; placeholder for future
        rec = AutonomousDecisionRecord(
            decision_type="event_observed",
            action="skip",
            reason="event observed",
            inputs={"importance": importance},
            executed=False,
        )
        self._push(rec)
        return rec

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        last = self._history[-1] if self._history else None
        snap = AutonomousDecisionSnapshot(
            total_records=len(self._history),
            total_executed=sum(1 for r in self._history if r.executed),
            total_failed=sum(1 for r in self._history if r.executed and not r.success),
            last_action=last.action if last else "",
            last_reason=last.reason if last else "",
        )
        return snap.to_dict()

    def clear_history(self) -> int:
        n = len(self._history)
        self._history.clear()
        return n

    # ================= internal =================

    def _maybe_trigger_reflection(self, runtime: Any) -> Optional[AutonomousDecisionRecord]:
        if not getattr(runtime, "reflection_scheduler", None):
            return None

        pending = 0
        try:
            pending = len(runtime.get_growth_proposals(status="proposed", limit=50) or [])
        except Exception:
            pending = 0

        # if too many pending proposals, hold stable
        if pending >= self.cfg.max_pending_proposals:
            return AutonomousDecisionRecord(
                decision_type="reflection",
                action="hold_stable",
                reason=f"pending_proposals={pending} >= {self.cfg.max_pending_proposals}",
                inputs={"pending_proposals": pending},
                executed=False,
            )

        # identity stability gate (if available)
        stability = None
        try:
            stability = runtime.get_identity_stability_report()
        except Exception:
            stability = None

        if stability and not bool(stability.get("is_stable", True)):
            return AutonomousDecisionRecord(
                decision_type="reflection",
                action="hold_stable",
                reason="identity_stability_failed",
                inputs={"identity_stability": stability},
                executed=False,
            )

        # should trigger?
        check = runtime.check_reflection_trigger()
        if not check:
            return None

        if not bool(check.get("should_trigger", False)):
            return AutonomousDecisionRecord(
                decision_type="reflection",
                action="skip",
                reason=str(check.get("reason", "")),
                inputs={"trigger_check": check},
                executed=False,
            )

        # execute reflection
        try:
            task = runtime.run_scheduled_reflection()
            return AutonomousDecisionRecord(
                decision_type="reflection",
                action="trigger_reflection",
                reason=str(check.get("reason", "")),
                inputs={"trigger_check": check},
                outputs={"task": task},
                executed=True,
                success=True,
            )
        except Exception as e:
            return AutonomousDecisionRecord(
                decision_type="reflection",
                action="trigger_reflection",
                reason="execution_exception",
                inputs={"trigger_check": check},
                executed=True,
                success=False,
                error=str(e),
            )

    def _refresh_stability(self, runtime: Any) -> AutonomousDecisionRecord:
        try:
            report = runtime.refresh_identity_stability(force=True)
            return AutonomousDecisionRecord(
                decision_type="stability",
                action="refresh_stability",
                reason="periodic_refresh",
                inputs={"tick": self._tick_counter},
                outputs={"report": report},
                executed=True,
                success=True,
            )
        except Exception as e:
            return AutonomousDecisionRecord(
                decision_type="stability",
                action="refresh_stability",
                reason="periodic_refresh_failed",
                inputs={"tick": self._tick_counter},
                executed=True,
                success=False,
                error=str(e),
            )

    def _push(self, rec: AutonomousDecisionRecord) -> None:
        self._history.append(rec)
        if len(self._history) > self.cfg.history_limit:
            self._history = self._history[-self.cfg.history_limit :]

