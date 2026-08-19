"""
Phase 3.5.11: Autonomous Reflection Scheduler

职责：
- 管理反思触发器（event_count / time_interval / importance_score / manual）
- 检查触发条件（check_triggers）
- 执行调度反思（run_reflection）：调用 RuntimeCore 的 reflect_on_experiences + 后续链路
- 记录执行历史（可审计）

约束：
- 不自动接受 GrowthProposal（仅触发反思流程，Proposal 仍需人工审批）
- 不接入真实后台线程（由 RuntimeCore tick 或外部显式调用驱动）
- 不调用 LLM
- 所有调度行为必须可审计
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.contracts.reflection_scheduler_schema import (
    TriggerResult,
    ReflectionTaskRecord,
    SchedulerSnapshot,
)


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def now_ts() -> float:
    return datetime.utcnow().timestamp()


# ============================================================
# ReflectionTrigger: 触发器基类与实现
# ============================================================

@dataclass
class ReflectionTriggerConfig:
    """触发器配置"""
    trigger_id: str = ""
    trigger_type: str = ""  # event_count / time_interval / importance_score / manual
    enabled: bool = True

    # event_count
    min_event_count: int = 5

    # time_interval (seconds)
    interval_seconds: float = 300.0  # 默认 5 分钟

    # importance_score
    min_importance: float = 0.6


class ReflectionTrigger:
    """
    单个反思触发器。

    check() 方法返回 TriggerResult，表示是否满足触发条件。
    """

    def __init__(self, config: ReflectionTriggerConfig):
        self.config = config
        self._last_fired_ts: float = 0.0

    @property
    def trigger_id(self) -> str:
        return self.config.trigger_id

    @property
    def trigger_type(self) -> str:
        return self.config.trigger_type

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def check(
        self,
        event_count_since_last: int = 0,
        time_since_last_seconds: float = 0.0,
        current_importance: float = 0.0,
    ) -> TriggerResult:
        """
        检查是否满足触发条件。

        子类可覆盖此方法。
        """
        result = TriggerResult(
            trigger_id=self.config.trigger_id,
            trigger_type=self.config.trigger_type,
            triggered=False,
        )

        if not self.config.enabled:
            result.reason = "trigger disabled"
            return result

        if self.config.trigger_type == "event_count":
            result.current_value = float(event_count_since_last)
            result.threshold_value = float(self.config.min_event_count)
            if event_count_since_last >= self.config.min_event_count:
                result.triggered = True
                result.reason = f"event_count={event_count_since_last} >= {self.config.min_event_count}"
            else:
                result.reason = f"event_count={event_count_since_last} < {self.config.min_event_count}"

        elif self.config.trigger_type == "time_interval":
            result.current_value = time_since_last_seconds
            result.threshold_value = self.config.interval_seconds
            if time_since_last_seconds >= self.config.interval_seconds:
                result.triggered = True
                result.reason = f"time_elapsed={time_since_last_seconds:.1f}s >= {self.config.interval_seconds}s"
            else:
                result.reason = f"time_elapsed={time_since_last_seconds:.1f}s < {self.config.interval_seconds}s"

        elif self.config.trigger_type == "importance_score":
            result.current_value = current_importance
            result.threshold_value = self.config.min_importance
            if current_importance >= self.config.min_importance:
                result.triggered = True
                result.reason = f"importance={current_importance:.3f} >= {self.config.min_importance}"
            else:
                result.reason = f"importance={current_importance:.3f} < {self.config.min_importance}"

        elif self.config.trigger_type == "manual":
            # manual trigger is always checked externally
            result.reason = "manual trigger (use schedule_reflection directly)"

        else:
            result.reason = f"unknown trigger type: {self.config.trigger_type}"

        return result


# ============================================================
# ReflectionSchedule: 调度配置（多个触发器的集合）
# ============================================================

@dataclass
class ReflectionSchedule:
    """
    反思调度配置。

    包含多个触发器，任一触发即可执行反思。
    """
    schedule_id: str = "default"
    enabled: bool = True

    # 触发器列表
    triggers: List[ReflectionTrigger] = field(default_factory=list)

    # 触发策略：any（任一满足）或 all（全部满足）
    trigger_mode: str = "any"  # any / all

    # 冷却时间（秒）：上次执行后多少秒内不再触发
    cooldown_seconds: float = 60.0

    # 执行后是否自动刷新 SelfModel / IdentityContinuity
    auto_refresh_self_model: bool = False
    auto_refresh_continuity: bool = False

    # 最大历史记录数
    max_history: int = 200


# ============================================================
# ReflectionScheduler: 核心调度器
# ============================================================

class ReflectionScheduler:
    """
    自主反思调度器。

    纯同步：不启动后台线程，由外部调用 check_and_run / check_triggers / run_reflection。
    所有执行结果记录在 _history 中，可审计。
    """

    def __init__(self, schedule: Optional[ReflectionSchedule] = None):
        self.schedule = schedule or self._default_schedule()
        self._history: List[ReflectionTaskRecord] = []
        self._last_run_ts: float = 0.0
        self._event_count_since_last: int = 0
        self._current_importance: float = 0.0
        self._last_trigger_results: List[TriggerResult] = []

        # 统计
        self._total_executed: int = 0
        self._total_succeeded: int = 0
        self._total_failed: int = 0
        self._total_skipped: int = 0

    # ============================================================
    # 默认配置
    # ============================================================

    @staticmethod
    def _default_schedule() -> ReflectionSchedule:
        """默认调度配置"""
        return ReflectionSchedule(
            schedule_id="default",
            enabled=True,
            triggers=[
                ReflectionTrigger(ReflectionTriggerConfig(
                    trigger_id="event_trigger",
                    trigger_type="event_count",
                    min_event_count=5,
                )),
                ReflectionTrigger(ReflectionTriggerConfig(
                    trigger_id="time_trigger",
                    trigger_type="time_interval",
                    interval_seconds=300.0,
                )),
                ReflectionTrigger(ReflectionTriggerConfig(
                    trigger_id="importance_trigger",
                    trigger_type="importance_score",
                    min_importance=0.6,
                )),
            ],
            trigger_mode="any",
            cooldown_seconds=60.0,
        )

    # ============================================================
    # 状态更新（由 RuntimeCore 调用）
    # ============================================================

    def on_event(self, importance: float = 0.0) -> None:
        """事件发生时调用，更新计数和重要度"""
        self._event_count_since_last += 1
        if importance > self._current_importance:
            self._current_importance = importance

    def reset_event_count(self) -> None:
        """重置事件计数（通常在反思执行后调用）"""
        self._event_count_since_last = 0
        self._current_importance = 0.0

    # ============================================================
    # check_triggers: 检查所有触发器
    # ============================================================

    def check_triggers(self) -> List[TriggerResult]:
        """
        检查所有触发器，返回结果列表。

        不执行反思，只检查条件。
        """
        results: List[TriggerResult] = []
        # 从未执行时 time_since_last=0，避免首次检查即触发 time_interval
        time_since_last = now_ts() - self._last_run_ts if self._last_run_ts > 0 else 0.0

        for trigger in self.schedule.triggers:
            result = trigger.check(
                event_count_since_last=self._event_count_since_last,
                time_since_last_seconds=time_since_last,
                current_importance=self._current_importance,
            )
            results.append(result)

        self._last_trigger_results = results
        return results

    # ============================================================
    # should_trigger: 综合判断是否应该触发
    # ============================================================

    def should_trigger(self) -> Tuple[bool, str, Optional[TriggerResult]]:
        """
        综合判断是否应该触发反思。

        Returns:
            (should_trigger, reason, matched_trigger)
        """
        if not self.schedule.enabled:
            return False, "schedule disabled", None

        # 冷却检查
        time_since_last = now_ts() - self._last_run_ts if self._last_run_ts > 0 else 0.0
        if self._last_run_ts > 0 and time_since_last < self.schedule.cooldown_seconds:
            return False, f"cooldown ({time_since_last:.1f}s < {self.schedule.cooldown_seconds}s)", None

        results = self.check_triggers()

        if self.schedule.trigger_mode == "any":
            for r in results:
                if r.triggered:
                    return True, r.reason, r
            return False, "no trigger matched", None
        else:  # all
            all_triggered = all(r.triggered for r in results if r.trigger_type != "manual")
            if all_triggered:
                return True, "all triggers matched", results[0] if results else None
            return False, "not all triggers matched", None

    # ============================================================
    # run_reflection: 执行反思（回调式，不直接依赖 RuntimeCore）
    # ============================================================

    def run_reflection(
        self,
        reflection_callback: Callable[[], Dict[str, Any]],
        *,
        trigger_type: str = "manual",
        trigger_reason: str = "",
        force: bool = False,
    ) -> ReflectionTaskRecord:
        """
        执行一次反思。

        Args:
            reflection_callback: 由 RuntimeCore 提供的回调函数，执行实际反思逻辑。
                                 返回 dict，包含 insight_id / proposal_ids 等。
            trigger_type: 触发类型
            trigger_reason: 触发原因
            force: 是否强制执行（跳过冷却检查）

        Returns:
            ReflectionTaskRecord: 执行记录
        """
        task = ReflectionTaskRecord(
            trigger_type=trigger_type,
            trigger_reason=trigger_reason or f"{trigger_type} triggered",
            status="running",
            executed_at=now_iso(),
        )

        # 冷却检查：force=False 时所有触发类型（含 manual）均受冷却约束
        if not force:
            should, reason, _ = self.should_trigger()
            if not should:
                task.status = "skipped"
                task.error = reason
                task.completed_at = now_iso()
                self._total_skipped += 1
                self._history.append(task)
                if len(self._history) > self.schedule.max_history:
                    self._history = self._history[-self.schedule.max_history:]
                return task

        # 执行反思回调
        try:
            result = reflection_callback()
            task.insight_id = result.get("insight_id", "")
            task.proposal_ids = result.get("proposal_ids", [])
            task.experience_count = result.get("experience_count", 0)
            task.proposal_count = len(task.proposal_ids)
            task.self_model_updated = result.get("self_model_updated", False)
            task.continuity_checked = result.get("continuity_checked", False)
            task.status = "completed"
            task.completed_at = now_iso()
            self._total_succeeded += 1
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.completed_at = now_iso()
            self._total_failed += 1

        # 更新状态
        self._last_run_ts = now_ts()
        self._total_executed += 1
        self.reset_event_count()

        # 存入历史
        self._history.append(task)
        if len(self._history) > self.schedule.max_history:
            self._history = self._history[-self.schedule.max_history:]

        return task

    # ============================================================
    # 历史与快照
    # ============================================================

    def get_history(self, limit: int = 50) -> List[ReflectionTaskRecord]:
        """获取执行历史（最新在前）"""
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def get_latest_task(self) -> Optional[ReflectionTaskRecord]:
        if not self._history:
            return None
        return self._history[-1]

    def get_snapshot(self) -> SchedulerSnapshot:
        """生成调度器快照"""
        time_since_last = now_ts() - self._last_run_ts if self._last_run_ts > 0 else 0.0
        return SchedulerSnapshot(
            enabled=self.schedule.enabled,
            active_triggers=[t.trigger_id for t in self.schedule.triggers if t.enabled],
            total_tasks_executed=self._total_executed,
            total_tasks_succeeded=self._total_succeeded,
            total_tasks_failed=self._total_failed,
            total_tasks_skipped=self._total_skipped,
            last_trigger_results=[r.to_dict() for r in self._last_trigger_results],
            last_task_id=self._history[-1].task_id if self._history else "",
            event_count_since_last=self._event_count_since_last,
            time_since_last_seconds=round(time_since_last, 2),
            current_importance_score=round(self._current_importance, 4),
        )

    def clear_history(self) -> int:
        """清空历史，返回清理数"""
        n = len(self._history)
        self._history.clear()
        return n


# 类型导入
from typing import Tuple
