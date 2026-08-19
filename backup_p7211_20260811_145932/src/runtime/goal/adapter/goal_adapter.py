# -*- coding: utf-8 -*-
"""
src/runtime/goal/adapter/goal_adapter.py

Phase 5.0-D3-D: GoalAdapter。

职责:
- 桥接 IntegrationEvent <-> GoalManager
- 接收 ReflectionCompleted / InitiativeCreated / ExperienceRecorded 事件
- 把输入推送给 GoalGenerator
- 通过 GoalEventEmitter 发射 IntegrationEvent
- 不直接读取业务模块

约束:
- 不依赖业务模块(memory/growth/personality/emotion/relationship)
- 仅依赖 Runtime 内部模块(integration / reflection / initiative / goal / lifecycle)
- 异常隔离
- enable_goal = False 短路
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.goal.adapter.goal_event_emitter import (
    DEFAULT_GOAL_EMITTER_OWNER,
    GoalEventEmitter,
)
from src.runtime.goal.desire import Desire
from src.runtime.goal.goal_generator import GoalGenerator
from src.runtime.goal.goal_manager import (
    DEFAULT_MAX_GOALS,
    DEFAULT_MAX_PLANS,
    GoalManager,
)
from src.runtime.goal.goal_planner import GoalPlanner
from src.runtime.goal.goal_state import (
    GOAL_STATUS_CANDIDATE,
    GoalState,
    build_candidate_goal,
)
from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import (
    INTEGRATION_INITIATIVE_CREATED,
    INTEGRATION_INTEREST_SIGNAL_CREATED,
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    INTEGRATION_REFLECTION_GROWTH_COMPLETED,
    IntegrationEvent,
)
from src.runtime.initiative.interest_signal import InterestSignal
from src.runtime.initiative.possible_action import PossibleAction
from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.reflection.reflection_result import ReflectionResult


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_ADAPTER_SCHEMA_VERSION = "1.0"

DEFAULT_GOAL_ADAPTER_OWNER = DEFAULT_GOAL_EMITTER_OWNER  # "goal"
DEFAULT_MAX_EVENTS = 256
DEFAULT_MAX_REFLECTIONS = 64
DEFAULT_MAX_SIGNALS = 256
DEFAULT_MAX_ACTIONS = 256
DEFAULT_MAX_DESIRES = 256


# ============================================================
# GoalAdapter
# ============================================================
class GoalAdapter(BaseAdapter):
    """GoalManager 与 IntegrationLayer 的桥接。"""

    def __init__(
        self,
        *,
        name: str = "goal_adapter",
        owner: str = DEFAULT_GOAL_ADAPTER_OWNER,
        manager: Optional[GoalManager] = None,
        generator: Optional[GoalGenerator] = None,
        planner: Optional[GoalPlanner] = None,
        emitter: Optional[GoalEventEmitter] = None,
        clock: Optional[Clock] = None,
        max_events: Optional[int] = None,
        max_reflections: Optional[int] = None,
        max_signals: Optional[int] = None,
        max_actions: Optional[int] = None,
        max_desires: Optional[int] = None,
        enabled: bool = True,
    ) -> None:
        self._generator = generator if isinstance(generator, GoalGenerator) else GoalGenerator()
        self._planner = planner if isinstance(planner, GoalPlanner) else GoalPlanner()
        self._emitter = emitter if isinstance(emitter, GoalEventEmitter) else GoalEventEmitter()
        self._clock: Clock = clock or SystemClock()
        # 限制
        try:
            self._max_events = int(max_events) if max_events is not None else DEFAULT_MAX_EVENTS
        except Exception:
            self._max_events = DEFAULT_MAX_EVENTS
        if self._max_events < 0:
            self._max_events = 0
        try:
            self._max_reflections = int(max_reflections) if max_reflections is not None else DEFAULT_MAX_REFLECTIONS
        except Exception:
            self._max_reflections = DEFAULT_MAX_REFLECTIONS
        if self._max_reflections < 0:
            self._max_reflections = 0
        try:
            self._max_signals = int(max_signals) if max_signals is not None else DEFAULT_MAX_SIGNALS
        except Exception:
            self._max_signals = DEFAULT_MAX_SIGNALS
        if self._max_signals < 0:
            self._max_signals = 0
        try:
            self._max_actions = int(max_actions) if max_actions is not None else DEFAULT_MAX_ACTIONS
        except Exception:
            self._max_actions = DEFAULT_MAX_ACTIONS
        if self._max_actions < 0:
            self._max_actions = 0
        try:
            self._max_desires = int(max_desires) if max_desires is not None else DEFAULT_MAX_DESIRES
        except Exception:
            self._max_desires = DEFAULT_MAX_DESIRES
        if self._max_desires < 0:
            self._max_desires = 0

        # 构造 manager(若未提供)
        if isinstance(manager, GoalManager):
            self._manager = manager
        else:
            self._manager = GoalManager(
                planner=self._planner,
                max_goals=DEFAULT_MAX_GOALS,
                max_plans=DEFAULT_MAX_PLANS,
                clock=self._clock,
            )
        self._enabled = bool(enabled)

        super().__init__(
            name=str(name or "goal_adapter"),
            owner=str(owner or DEFAULT_GOAL_ADAPTER_OWNER),
        )
        # pending
        self._pending_events: list = []
        self._pending_reflections: list = []
        self._pending_signals: list = []
        self._pending_actions: list = []
        self._pending_desires: list = []
        # 统计
        self._tick_count = 0
        self._desires_emitted = 0
        self._goals_emitted = 0
        self._plans_emitted = 0
        self._last_error = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def manager(self) -> GoalManager:
        return self._manager

    @property
    def generator(self) -> GoalGenerator:
        return self._generator

    @property
    def planner(self) -> GoalPlanner:
        return self._planner

    @property
    def emitter(self) -> GoalEventEmitter:
        return self._emitter

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tick_count(self) -> int:
        with self._lock:
            return self._tick_count

    @property
    def desires_emitted(self) -> int:
        with self._lock:
            return self._desires_emitted

    @property
    def goals_emitted(self) -> int:
        with self._lock:
            return self._goals_emitted

    @property
    def plans_emitted(self) -> int:
        with self._lock:
            return self._plans_emitted

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return GOAL_ADAPTER_SCHEMA_VERSION

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    # --------------------------------------------------------
    # 事件输入
    # --------------------------------------------------------
    def push_events(self, events: Sequence[IntegrationEvent]) -> int:
        if events is None:
            return 0
        n = 0
        for e in events:
            if isinstance(e, IntegrationEvent):
                if self._max_events > 0 and len(self._pending_events) >= self._max_events:
                    try:
                        self._pending_events.pop(0)
                    except Exception:
                        self._pending_events = self._pending_events[-self._max_events:]
                self._pending_events.append(e)
                n += 1
        return n

    def push_reflections(self, reflections: Sequence[Any]) -> int:
        if reflections is None:
            return 0
        from src.runtime.reflection.reflection_result import ReflectionResult
        n = 0
        for r in reflections:
            if isinstance(r, ReflectionResult):
                if self._max_reflections > 0 and len(self._pending_reflections) >= self._max_reflections:
                    try:
                        self._pending_reflections.pop(0)
                    except Exception:
                        self._pending_reflections = self._pending_reflections[-self._max_reflections:]
                self._pending_reflections.append(r)
                n += 1
        return n

    def push_signals(self, signals: Sequence[Any]) -> int:
        if signals is None:
            return 0
        n = 0
        for s in signals:
            if isinstance(s, InterestSignal):
                if self._max_signals > 0 and len(self._pending_signals) >= self._max_signals:
                    try:
                        self._pending_signals.pop(0)
                    except Exception:
                        self._pending_signals = self._pending_signals[-self._max_signals:]
                self._pending_signals.append(s)
                n += 1
        return n

    def push_actions(self, actions: Sequence[Any]) -> int:
        if actions is None:
            return 0
        n = 0
        for a in actions:
            if isinstance(a, PossibleAction):
                if self._max_actions > 0 and len(self._pending_actions) >= self._max_actions:
                    try:
                        self._pending_actions.pop(0)
                    except Exception:
                        self._pending_actions = self._pending_actions[-self._max_actions:]
                self._pending_actions.append(a)
                n += 1
        return n

    def push_desires(self, desires: Sequence[Any]) -> int:
        if desires is None:
            return 0
        n = 0
        for d in desires:
            if isinstance(d, Desire):
                if self._max_desires > 0 and len(self._pending_desires) >= self._max_desires:
                    try:
                        self._pending_desires.pop(0)
                    except Exception:
                        self._pending_desires = self._pending_desires[-self._max_desires:]
                self._pending_desires.append(d)
                n += 1
        return n

    def clear_pending(self) -> int:
        with self._lock:
            ne = len(self._pending_events)
            nr = len(self._pending_reflections)
            ns = len(self._pending_signals)
            na = len(self._pending_actions)
            nd = len(self._pending_desires)
            self._pending_events = []
            self._pending_reflections = []
            self._pending_signals = []
            self._pending_actions = []
            self._pending_desires = []
        return ne + nr + ns + na + nd

    @property
    def pending_event_count(self) -> int:
        with self._lock:
            return len(self._pending_events)

    @property
    def pending_reflection_count(self) -> int:
        with self._lock:
            return len(self._pending_reflections)

    @property
    def pending_signal_count(self) -> int:
        with self._lock:
            return len(self._pending_signals)

    @property
    def pending_action_count(self) -> int:
        with self._lock:
            return len(self._pending_actions)

    @property
    def pending_desire_count(self) -> int:
        with self._lock:
            return len(self._pending_desires)

    # --------------------------------------------------------
    # BaseAdapter 接口
    # --------------------------------------------------------
    def is_available(self) -> bool:
        return isinstance(self._manager, GoalManager)

    def get_target(self) -> Any:
        return self._manager

    # --------------------------------------------------------
    # 评估/执行
    # --------------------------------------------------------
    def evaluate(
        self,
        events: Optional[Sequence[IntegrationEvent]] = None,
        reflections: Optional[Sequence[Any]] = None,
        signals: Optional[Sequence[Any]] = None,
        actions: Optional[Sequence[Any]] = None,
        desires: Optional[Sequence[Any]] = None,
    ) -> bool:
        """评估是否值得执行 tick。"""
        if not self._enabled:
            return False
        with self._lock:
            has_pending = (
                bool(self._pending_events)
                or bool(self._pending_reflections)
                or bool(self._pending_signals)
                or bool(self._pending_actions)
                or bool(self._pending_desires)
            )
        has_input = (
            bool(events)
            or bool(reflections)
            or bool(signals)
            or bool(actions)
            or bool(desires)
        )
        if not has_pending and not has_input:
            return False
        return True

    def tick(
        self,
        events: Optional[Sequence[IntegrationEvent]] = None,
        reflections: Optional[Sequence[Any]] = None,
        signals: Optional[Sequence[Any]] = None,
        actions: Optional[Sequence[Any]] = None,
        desires: Optional[Sequence[Any]] = None,
        *,
        now: Optional[float] = None,
        plan: bool = True,
    ) -> Optional["GoalAdapterTickOutput"]:
        """执行一次 tick。

        流程:
        1) drain pending
        2) 合并传入输入
        3) 把 pending_desires 写进 manager(add_desire)
        4) generator.generate(...) -> 候选 goals
        5) manager.create_goal(...)
        6) emitter 发射 desire / goal / plan 事件
        """
        try:
            with self._lock:
                self._tick_count += 1
            # 1) drain pending
            pending_evs = list(self._pending_events)
            pending_refs = list(self._pending_reflections)
            pending_sigs = list(self._pending_signals)
            pending_acts = list(self._pending_actions)
            pending_des = list(self._pending_desires)
            with self._lock:
                self._pending_events = []
                self._pending_reflections = []
                self._pending_signals = []
                self._pending_actions = []
                self._pending_desires = []
            # 2) merge
            merged_events: List[IntegrationEvent] = list(pending_evs)
            if events:
                merged_events.extend(list(events))
            merged_refs = list(pending_refs)
            if reflections:
                merged_refs.extend(list(reflections))
            merged_sigs = list(pending_sigs)
            if signals:
                merged_sigs.extend(list(signals))
            merged_acts = list(pending_acts)
            if actions:
                merged_acts.extend(list(actions))
            merged_desires = list(pending_des)
            if desires:
                merged_desires.extend(list(desires))
            try:
                if now is not None:
                    now_f = float(now)
                else:
                    now_f = self._safe_now()
            except Exception:
                now_f = self._safe_now()
            output = GoalAdapterTickOutput()
            output.started_at = now_f
            # 3) 写 desires
            for d in merged_desires:
                if isinstance(d, Desire):
                    if self._manager.add_desire(d):
                        output.desires_added += 1
            # 4) generator
            gen_out = self._generator.generate(
                desires=merged_desires,
                reflections=merged_refs,
                signals=merged_sigs,
                actions=merged_acts,
                integration_events=merged_events,
                now=now_f,
            )
            output.candidates = list(gen_out.candidates)
            output.skipped = list(gen_out.skipped_desires)
            output.skipped_reason = dict(gen_out.skipped_reason)
            # 5) manager.create_goal
            for cand in gen_out.candidates:
                if not isinstance(cand, GoalState):
                    continue
                if cand.status != GOAL_STATUS_CANDIDATE:
                    # 强制为 candidate(防御)
                    try:
                        cand.status = GOAL_STATUS_CANDIDATE
                    except Exception:
                        pass
                # 关联 desires
                try:
                    for d in merged_desires:
                        if isinstance(d, Desire):
                            if d.topic == cand.title.split(":", 1)[-1] if ":" in cand.title else d.topic == cand.title:
                                cand.add_source_desire_ids([d.desire_id])
                except Exception:
                    pass
                if cand.is_valid():
                    self._manager.create_goal(cand, plan=plan, now=now_f)
                    output.goals_created += 1
            # 6) 发射事件
            desire_events = self._emitter.emit_desires(
                [d for d in merged_desires if isinstance(d, Desire)]
            )
            output.desire_events = desire_events
            goal_events = self._emitter.emit_goals(
                list(self._manager.list_goals(status=GOAL_STATUS_CANDIDATE)),
                lifecycle="created",
            )
            output.goal_events = goal_events
            # 7) plan
            if plan:
                plans = self._manager.list_plans()
                plan_events = self._emitter.emit_plans(plans)
                output.plan_events = plan_events
                output.plans_created = len(plans)
            with self._lock:
                self._desires_emitted += len(desire_events)
                self._goals_emitted += len(goal_events)
                self._plans_emitted += len(output.plan_events)
            # 8) emit
            for ev in desire_events + goal_events + output.plan_events:
                try:
                    self.emit(ev)
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        self._last_error = f"emit 失败: {exc}"
            output.finished_at = self._safe_now()
            return output
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"tick 异常: {exc}"
            logger.warning("GoalAdapter.tick 失败: %s", exc)
            return None

    def drain_and_tick(self) -> Optional["GoalAdapterTickOutput"]:
        return self.tick()

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "tick_count": self.tick_count,
            "desires_emitted": self.desires_emitted,
            "goals_emitted": self.goals_emitted,
            "plans_emitted": self.plans_emitted,
            "pending_event_count": self.pending_event_count,
            "pending_reflection_count": self.pending_reflection_count,
            "pending_signal_count": self.pending_signal_count,
            "pending_action_count": self.pending_action_count,
            "pending_desire_count": self.pending_desire_count,
            "manager": self._manager.describe(),
            "generator": self._generator.describe(),
            "emitter": self._emitter.describe(),
            "last_error": self.last_error,
        })
        return d

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalAdapter(name={self._name!r}, enabled={self.enabled}, "
                f"ticks={self._tick_count}, desires={self._desires_emitted}, "
                f"goals={self._goals_emitted}, plans={self._plans_emitted})"
            )


# ============================================================
# Tick Output
# ============================================================
class GoalAdapterTickOutput:
    """GoalAdapter tick 输出。"""

    def __init__(self) -> None:
        self.desires_added: int = 0
        self.goals_created: int = 0
        self.plans_created: int = 0
        self.candidates: List[GoalState] = []
        self.skipped: List[Desire] = []
        self.skipped_reason: Dict[str, str] = {}
        self.desire_events: List[IntegrationEvent] = []
        self.goal_events: List[IntegrationEvent] = []
        self.plan_events: List[IntegrationEvent] = []
        self.started_at: float = 0.0
        self.finished_at: float = 0.0
        self.error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "desires_added": self.desires_added,
            "goals_created": self.goals_created,
            "plans_created": self.plans_created,
            "candidates": [g.goal_id for g in self.candidates],
            "skipped_count": len(self.skipped),
            "skipped_reason": dict(self.skipped_reason),
            "desire_event_count": len(self.desire_events),
            "goal_event_count": len(self.goal_events),
            "plan_event_count": len(self.plan_events),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }

    def __repr__(self) -> str:
        return (
            f"GoalAdapterTickOutput(desires={self.desires_added}, "
            f"goals={self.goals_created}, plans={self.plans_created})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_goal_adapter() -> GoalAdapter:
    return GoalAdapter()


# 重新导出 constant
__all__ = [
    "GOAL_ADAPTER_SCHEMA_VERSION",
    "DEFAULT_GOAL_ADAPTER_OWNER",
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_REFLECTIONS",
    "DEFAULT_MAX_SIGNALS",
    "DEFAULT_MAX_ACTIONS",
    "DEFAULT_MAX_DESIRES",
    "GoalAdapter",
    "GoalAdapterTickOutput",
    "build_default_goal_adapter",
]
