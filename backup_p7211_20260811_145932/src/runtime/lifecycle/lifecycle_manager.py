# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_manager.py

Phase 5.0-D1: 生命周期核心调度层。

职责:
- 持有 LifecycleRegistry / EventEmitter / Clock
- 管理自身状态机(LifecycleStateMachine)
- 调度: tick() 遍历任务 -> 决策 -> 执行 -> 收集结果
- 异常隔离: 单任务失败不影响其他任务 / Manager 自身
- 状态记录: 每 task 维护 TaskState
- 健康检查: health_check()

约束:
- 不依赖任何业务模块(Memory/Growth/Personality/...)
- 不调用 LLM / 不连接数据库 / 不引入第三方依赖
- 仅依赖 src.runtime.lifecycle.* 与 Python 标准库
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import (
    Any,
    Deque,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.lifecycle.internal.event_emitter import EventEmitter
from src.runtime.lifecycle.lifecycle_context import (
    LifecycleContext,
    RuntimeLifecycleStateView,
)
from src.runtime.lifecycle.lifecycle_decision import (
    Decision,
    TaskState,
    Verdict,
)
from src.runtime.lifecycle.lifecycle_errors import ErrorCategory, LifecycleError
from src.runtime.lifecycle.lifecycle_registry import (
    IGNORE_DUPLICATE,
    LifecycleRegistry,
    REJECT_DUPLICATE,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)
from src.runtime.lifecycle.lifecycle_state import (
    LifecycleState,
    LifecycleStateMachine,
)
from src.runtime.lifecycle.lifecycle_task import LifecycleTask

logger = logging.getLogger(__name__)


# ============================================================
# Manager 事件类型常量(独立定义,避免修改 event_emitter.py)
# ============================================================
EVENT_MANAGER_STARTED = "lifecycle.manager.started"
EVENT_MANAGER_PAUSED = "lifecycle.manager.paused"
EVENT_MANAGER_RESUMED = "lifecycle.manager.resumed"
EVENT_MANAGER_STOPPED = "lifecycle.manager.stopped"
EVENT_MANAGER_TICK = "lifecycle.manager.tick"

EVENT_TASK_STARTED = "lifecycle.task.started"
EVENT_TASK_COMPLETED = "lifecycle.task.completed"
EVENT_TASK_FAILED = "lifecycle.task.failed"
EVENT_TASK_SKIPPED = "lifecycle.task.skipped"
EVENT_TASK_DEFERRED = "lifecycle.task.deferred"


# ============================================================
# 常量
# ============================================================
DEFAULT_HISTORY_CAPACITY = 256
MAX_HISTORY_CAPACITY = 10000
DEFAULT_MAX_CONCURRENCY = 1


# ============================================================
# 异常
# ============================================================
class LifecycleManagerError(Exception):
    """LifecycleManager 错误基类。"""


# ============================================================
# 辅助:运行时状态视图
# ============================================================
def _build_runtime_state_view(sm: LifecycleStateMachine) -> RuntimeLifecycleStateView:
    """从状态机构建只读运行时视图。"""
    state = sm.state
    last_boot_mode = "fresh"
    # 简单从历史推断最近一次 boot_mode
    for h in reversed(sm.transition_history(limit=20)):
        reason = h.get("reason") or ""
        if "restore" in reason:
            last_boot_mode = "restore"
            break
        if "fresh" in reason:
            last_boot_mode = "fresh"
            break
    return RuntimeLifecycleStateView(
        source="lifecycle_manager",
        last_state=state.value,
        last_boot_mode=last_boot_mode,
    )


# ============================================================
# LifecycleManager
# ============================================================
class LifecycleManager:
    """生命周期核心调度器。

    状态机:
        CREATED -> STARTING -> RUNNING -> PAUSED -> RUNNING
                                       \\-> DRAINING -> STOPPED
                                       \\-> FAILED  -> STOPPED
                                       \\-> STOPPED

    使用方式:
        manager = LifecycleManager(clock=SystemClock())
        manager.register(task_a)
        manager.register(task_b)
        manager.start()
        results = manager.tick()
        manager.stop()
    """

    def __init__(
        self,
        *,
        clock: Optional[Clock] = None,
        emitter: Optional[EventEmitter] = None,
        registry: Optional[LifecycleRegistry] = None,
        snapshot_view: Optional[Mapping[str, Any]] = None,
        history_capacity: int = DEFAULT_HISTORY_CAPACITY,
        name: str = "manager",
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._emitter = emitter
        self._snapshot_view: Mapping[str, Any] = dict(snapshot_view) if snapshot_view else {}
        self._name = str(name or "manager")

        # 状态机
        self._state_machine = LifecycleStateMachine(initial=LifecycleState.CREATED)
        # 注册表(默认 REJECT_DUPLICATE 策略)
        self._registry = registry if registry is not None else LifecycleRegistry(
            emitter=emitter,
            duplicate_policy=REJECT_DUPLICATE,
            name=f"{self._name}.registry",
        )

        # 任务状态表: task_id -> TaskState
        self._task_states: Dict[str, TaskState] = {}
        # 任务结果历史(最近 N 条)
        cap = int(history_capacity or 0)
        if cap < 0:
            cap = 0
        if cap > MAX_HISTORY_CAPACITY:
            cap = MAX_HISTORY_CAPACITY
        self._history_capacity = cap
        self._history: Deque[LifecycleResult] = deque(maxlen=cap) if cap > 0 else deque()

        # 当前正在执行的任务快照(用于 stop 时等待)
        self._active_executions: Dict[str, threading.Thread] = {}
        self._execution_lock = threading.Lock()

        # 主锁(保护 _task_states / _history / _active_executions 之外的共享状态)
        self._lock = threading.RLock()

        # 统计
        self._tick_count = 0
        self._last_tick_at: Optional[float] = None
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        # 失败任务计数(在 result 中判断)
        self._failed_task_count = 0

    # ===========================================================
    # 属性
    # ===========================================================
    @property
    def name(self) -> str:
        return self._name

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def emitter(self) -> Optional[EventEmitter]:
        return self._emitter

    @property
    def state(self) -> LifecycleState:
        return self._state_machine.state

    @property
    def state_machine(self) -> LifecycleStateMachine:
        return self._state_machine

    @property
    def registry(self) -> LifecycleRegistry:
        return self._registry

    @property
    def tick_count(self) -> int:
        with self._lock:
            return self._tick_count

    @property
    def last_tick_at(self) -> Optional[float]:
        with self._lock:
            return self._last_tick_at

    @property
    def started_at(self) -> Optional[float]:
        return self._started_at

    @property
    def stopped_at(self) -> Optional[float]:
        return self._stopped_at

    @property
    def history_capacity(self) -> int:
        return self._history_capacity

    @property
    def is_running(self) -> bool:
        return self.state == LifecycleState.RUNNING

    @property
    def is_paused(self) -> bool:
        return self.state == LifecycleState.PAUSED

    @property
    def is_stopped(self) -> bool:
        return self.state == LifecycleState.STOPPED

    @property
    def is_terminal(self) -> bool:
        return self._state_machine.is_terminal

    def task_count(self) -> int:
        """返回已注册任务总数。"""
        return self._registry.size

    def running_task_count(self) -> int:
        """返回当前正在执行的任务数(粗略,基于 TaskState.last_result 状态)。"""
        with self._lock:
            return len(self._active_executions)

    def failed_task_count(self) -> int:
        """返回历史上失败的任务数(累积)。"""
        with self._lock:
            return self._failed_task_count

    # ===========================================================
    # 生命周期
    # ===========================================================
    def start(self, *, reason: str = "boot") -> bool:
        """启动 Manager。

        - CREATED -> STARTING -> RUNNING
        - 已 RUNNING 幂等
        - 已 STOPPED / FAILED / DRAINING 不能再次 start
        """
        current = self._state_machine.state
        if current == LifecycleState.RUNNING:
            return True
        if current in (LifecycleState.STOPPED, LifecycleState.FAILED, LifecycleState.DRAINING):
            logger.warning(
                "[LifecycleManager(%s)] 无法在 %s 状态启动",
                self._name,
                current.value,
            )
            return False
        if current == LifecycleState.PAUSED:
            # 暂停状态下 start 视为 resume
            return self.resume(reason=reason)

        ok = self._state_machine.transition(LifecycleState.STARTING, reason=reason)
        if not ok:
            return False
        ok2 = self._state_machine.transition(LifecycleState.RUNNING, reason="running")
        if not ok2:
            return False
        with self._lock:
            self._started_at = self._clock.now()
            self._stopped_at = None
        self._emit(EVENT_MANAGER_STARTED, {"reason": reason, "name": self._name})
        return True

    def stop(self, *, reason: str = "shutdown", timeout: Optional[float] = None) -> bool:
        """停止 Manager。

        - RUNNING / PAUSED -> DRAINING -> STOPPED
        - 已 STOPPED 幂等
        - DRAINING 等待超时或完成
        """
        current = self._state_machine.state
        if current == LifecycleState.STOPPED:
            return True
        if current not in (LifecycleState.RUNNING, LifecycleState.PAUSED, LifecycleState.FAILED, LifecycleState.DRAINING):
            logger.warning(
                "[LifecycleManager(%s)] 无法在 %s 状态停止",
                self._name,
                current.value,
            )
            return False
        # 进入 DRAINING
        if current != LifecycleState.DRAINING:
            self._state_machine.transition(LifecycleState.DRAINING, reason=reason)
        # 等待正在执行的任务(如有)
        self._wait_active_executions(timeout=timeout)
        # 进入 STOPPED
        self._state_machine.transition(LifecycleState.STOPPED, reason="stopped")
        with self._lock:
            self._stopped_at = self._clock.now()
        self._emit(EVENT_MANAGER_STOPPED, {"reason": reason, "name": self._name})
        return True

    def pause(self, *, reason: str = "pause") -> bool:
        """暂停 Manager。"""
        current = self._state_machine.state
        if current == LifecycleState.PAUSED:
            return True
        if current != LifecycleState.RUNNING:
            logger.warning(
                "[LifecycleManager(%s)] 无法在 %s 状态暂停",
                self._name,
                current.value,
            )
            return False
        ok = self._state_machine.transition(LifecycleState.PAUSED, reason=reason)
        if ok:
            self._emit(EVENT_MANAGER_PAUSED, {"reason": reason, "name": self._name})
        return ok

    def resume(self, *, reason: str = "resume") -> bool:
        """恢复 Manager。仅在 PAUSED 状态有效,其他状态返回 False。"""
        current = self._state_machine.state
        if current != LifecycleState.PAUSED:
            logger.warning(
                "[LifecycleManager(%s)] 无法在 %s 状态恢复",
                self._name,
                current.value,
            )
            return False
        ok = self._state_machine.transition(LifecycleState.RUNNING, reason=reason)
        if ok:
            self._emit(EVENT_MANAGER_RESUMED, {"reason": reason, "name": self._name})
        return ok

    # ===========================================================
    # Task 管理
    # ===========================================================
    def register(self, task: LifecycleTask, *, replace: bool = False) -> bool:
        """注册任务。replace=False 时重复 task_id 抛错(由 Registry 控制)。"""
        if task is None:
            raise ValueError("task 不能为 None")
        task_id = getattr(task, "task_id", None)
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task.task_id 必须为非空字符串")
        # 若 replace=True 但原策略是 REJECT,临时使用 IGNORE + 提前 unregister 实现覆盖
        if replace and self._registry.contains(task_id):
            self._registry.unregister(task_id)
            # 清理旧 task_state
            with self._lock:
                self._task_states.pop(task_id, None)
        registered = self._registry.register(task)
        if registered:
            with self._lock:
                if task_id not in self._task_states:
                    self._task_states[task_id] = TaskState(task_id=task_id)
        return bool(registered)

    def unregister(self, task_id: str) -> Optional[LifecycleTask]:
        """注销任务。"""
        if not isinstance(task_id, str) or not task_id:
            return None
        task = self._registry.unregister(task_id)
        with self._lock:
            self._task_states.pop(task_id, None)
        return task

    def get_task(self, task_id: str) -> Optional[LifecycleTask]:
        return self._registry.get(task_id)

    def list_tasks(self) -> List[LifecycleTask]:
        return self._registry.list_all()

    def get_task_state(self, task_id: str) -> Optional[TaskState]:
        if not isinstance(task_id, str) or not task_id:
            return None
        with self._lock:
            ts = self._task_states.get(task_id)
            # 返回浅拷贝避免外部修改污染
            if ts is None:
                return None
            return TaskState(
                task_id=ts.task_id,
                last_run_at=ts.last_run_at,
                last_ended_at=ts.last_ended_at,
                run_count=ts.run_count,
                failure_count=ts.failure_count,
                skip_count=ts.skip_count,
                last_result=ts.last_result,
                last_error=ts.last_error,
            )

    # ===========================================================
    # Tick 调度
    # ===========================================================
    def tick(self) -> List[LifecycleResult]:
        """执行一次调度。

        行为:
        - 状态非 RUNNING: 不调度任何任务,返回空列表
        - 遍历所有已注册任务
        - 对每个任务:
            1. 构造 LifecycleContext
            2. 调用 task.should_run(ctx, task_state) -> Decision
            3. RUN  -> execute_task(task)
            4. SKIP -> 记录 skip
            5. DEFER -> 记录 defer
        - 异常隔离: 单任务失败不影响其他任务

        返回本次 tick 产出的所有 LifecycleResult 列表(含 SKIPPED/FAILED/SUCCESS)。
        """
        results: List[LifecycleResult] = []
        with self._lock:
            self._tick_count += 1
            self._last_tick_at = self._clock.now()

        if self.state != LifecycleState.RUNNING:
            return results

        self._emit(EVENT_MANAGER_TICK, {"tick": self._tick_count})

        # 拷贝任务列表,避免迭代期间注册变化
        tasks = self._registry.list_all()
        for task in tasks:
            try:
                outcome = self._evaluate_and_execute(task)
                if outcome is not None:
                    results.append(outcome)
            except Exception as exc:  # noqa: BLE001 - 极端安全网
                logger.exception(
                    "[LifecycleManager(%s)] tick 中出现未捕获异常 (task=%s): %s",
                    self._name,
                    getattr(task, "task_id", "?"),
                    exc,
                )
                # 记录到 failed_task_count,但不让 Manager 整体崩溃
                with self._lock:
                    self._failed_task_count += 1
        return results

    def _evaluate_and_execute(self, task: LifecycleTask) -> Optional[LifecycleResult]:
        """对单个任务执行评估 + 执行,返回结果(若产生)。"""
        task_id = getattr(task, "task_id", None)
        if not isinstance(task_id, str) or not task_id:
            return None

        # 构造任务状态副本(用于决策与统计)
        with self._lock:
            ts = self._task_states.get(task_id)
            if ts is None:
                ts = TaskState(task_id=task_id)
                self._task_states[task_id] = ts
            ts_copy = TaskState(
                task_id=ts.task_id,
                last_run_at=ts.last_run_at,
                last_ended_at=ts.last_ended_at,
                run_count=ts.run_count,
                failure_count=ts.failure_count,
                skip_count=ts.skip_count,
                last_result=ts.last_result,
                last_error=ts.last_error,
            )

        # 构造 Context
        ctx = self._build_context(task_id)
        # 决策
        try:
            decision = task.should_run(ctx, ts_copy)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LifecycleManager(%s)] task.should_run 抛错 (task=%s): %s",
                self._name,
                task_id,
                exc,
            )
            self._record_skip(task_id, reason="decision_error", next_at=None)
            return self._make_result(
                task_id,
                LifecycleStatus.SKIPPED,
                started_at=ctx.now(),
                ended_at=ctx.now(),
                metrics={"reason": "decision_error"},
            )

        # 决策类型分支
        if not isinstance(decision, Decision):
            # 非法决策视为 SKIP
            self._record_skip(task_id, reason="invalid_decision", next_at=None)
            return self._make_result(
                task_id,
                LifecycleStatus.SKIPPED,
                started_at=ctx.now(),
                ended_at=ctx.now(),
                metrics={"reason": "invalid_decision"},
            )

        if decision.verdict == Verdict.SKIP:
            self._record_skip(task_id, reason=decision.reason, next_at=decision.defer_until)
            result = self._make_result(
                task_id,
                LifecycleStatus.SKIPPED,
                started_at=ctx.now(),
                ended_at=ctx.now(),
                metrics={"reason": decision.reason} if decision.reason else {},
            )
            return result

        if decision.verdict == Verdict.DEFER:
            self._record_defer(task_id, decision)
            return self._make_result(
                task_id,
                LifecycleStatus.SKIPPED,
                started_at=ctx.now(),
                ended_at=ctx.now(),
                metrics={"reason": "defer"},
                next_recommended_at=decision.defer_until,
            )

        # RUN
        return self.execute_task(task, ctx=ctx)

    def execute_task(
        self,
        task: LifecycleTask,
        *,
        ctx: Optional[LifecycleContext] = None,
    ) -> LifecycleResult:
        """执行单个任务(异常隔离)。

        - 状态非 RUNNING 返回 FAILED(no_running)
        - 自动包装异常为 LifecycleResult
        - 触发事件
        - 记录到 history
        """
        task_id = getattr(task, "task_id", None) or ""
        if ctx is None:
            ctx = self._build_context(task_id)
        started_at = ctx.now()

        if self.state != LifecycleState.RUNNING:
            return self._make_result(
                task_id,
                LifecycleStatus.FAILED,
                started_at=started_at,
                ended_at=ctx.now(),
                error=LifecycleError.internal("manager_not_running"),
            )

        self._emit(EVENT_TASK_STARTED, {"task_id": task_id, "timestamp": started_at})

        # 标记活跃执行(stop 时等待)
        with self._execution_lock:
            self._active_executions[task_id] = threading.current_thread()

        result: LifecycleResult
        try:
            result = task.run_safely(ctx)
        except Exception as exc:  # noqa: BLE001 - 兜底保护
            # 极端情况:run_safely 自身抛错(不应该发生)
            logger.exception(
                "[LifecycleManager(%s)] task.run_safely 抛错 (task=%s): %s",
                self._name,
                task_id,
                exc,
            )
            result = self._make_result(
                task_id,
                LifecycleStatus.FAILED,
                started_at=started_at,
                ended_at=ctx.now(),
                error=LifecycleError.internal(str(exc), cause=exc),
            )
        finally:
            with self._execution_lock:
                self._active_executions.pop(task_id, None)

        # 记录 + 发射事件
        self._record_result(task_id, result)
        if result.status in (LifecycleStatus.FAILED, LifecycleStatus.FATAL, LifecycleStatus.TIMEOUT):
            self._emit(
                EVENT_TASK_FAILED,
                {
                    "task_id": task_id,
                    "error": result.error.to_dict() if result.error else None,
                    "status": result.status.value,
                },
            )
        else:
            self._emit(
                EVENT_TASK_COMPLETED,
                {
                    "task_id": task_id,
                    "status": result.status.value,
                    "duration_ms": result.duration_ms,
                },
            )
        return result

    # ===========================================================
    # 单入口 run(Step 6.0.2 桥接)
    # ===========================================================
    def run(
        self,
        context: Optional[Any] = None,
        *,
        lifecycle_name: Optional[str] = None,
        snapshot: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        """执行一次完整的 Manager 生命周期(单入口)。

        行为:
            - context=None:
                旧 API 兼容。执行 start -> tick -> stop,返回 List[LifecycleResult]。
            - context=RuntimeContext:
                新 API 桥接。执行完整生命周期并把结果写入 context,
                返回更新后的 RuntimeContext。

        详见 ``src.runtime.lifecycle.lifecycle_bridge.run_with_context``。
        """
        if context is None:
            # 旧 API 兼容: 直接 start -> tick -> stop
            self.start()
            try:
                results = self.tick()
            finally:
                self.stop()
            return results

        # 新 API: 桥接到 RuntimeContext
        # 延迟 import,避免循环依赖;仅引用本地 lifecycle 包,绝不 import 业务 Authority
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        return run_with_context(
            self,
            context,
            lifecycle_name=lifecycle_name,
            snapshot=dict(snapshot) if snapshot else None,
        )

    # ===========================================================
    # 状态记录
    # ===========================================================
    def _record_result(self, task_id: str, result: LifecycleResult) -> None:
        """根据结果更新 task 状态。"""
        if not isinstance(task_id, str) or not task_id:
            return
        with self._lock:
            ts = self._task_states.get(task_id)
            if ts is None:
                ts = TaskState(task_id=task_id)
                self._task_states[task_id] = ts
            ts.last_run_at = result.started_at
            ts.last_ended_at = result.ended_at
            ts.last_result = result
            ts.last_error = result.error
            if result.status in (LifecycleStatus.FAILED, LifecycleStatus.FATAL, LifecycleStatus.TIMEOUT):
                ts.failure_count += 1
                self._failed_task_count += 1
            elif result.status == LifecycleStatus.SUCCESS:
                ts.run_count += 1
            elif result.status == LifecycleStatus.SKIPPED:
                ts.skip_count += 1
            # 加入历史
            if self._history_capacity > 0:
                self._history.append(result)

    def _record_skip(self, task_id: str, *, reason: str, next_at: Optional[float]) -> None:
        with self._lock:
            ts = self._task_states.get(task_id)
            if ts is None:
                ts = TaskState(task_id=task_id)
                self._task_states[task_id] = ts
            ts.skip_count += 1
            ts.last_ended_at = self._clock.now()
            ts.last_error = None
        self._emit(
            EVENT_TASK_SKIPPED,
            {"task_id": task_id, "reason": reason or "", "next_recommended_at": next_at},
        )

    def _record_defer(self, task_id: str, decision: Decision) -> None:
        with self._lock:
            ts = self._task_states.get(task_id)
            if ts is None:
                ts = TaskState(task_id=task_id)
                self._task_states[task_id] = ts
            ts.skip_count += 1
            ts.last_ended_at = self._clock.now()
        self._emit(
            EVENT_TASK_DEFERRED,
            {
                "task_id": task_id,
                "reason": decision.reason,
                "defer_until": decision.defer_until,
            },
        )

    # ===========================================================
    # 历史 / 调试
    # ===========================================================
    def history(
        self,
        task_id: Optional[str] = None,
        status: Optional[LifecycleStatus] = None,
        limit: Optional[int] = None,
    ) -> List[LifecycleResult]:
        """返回结果历史快照。"""
        with self._lock:
            data = list(self._history)
        if task_id is not None:
            data = [r for r in data if r.task_id == task_id]
        if status is not None:
            data = [r for r in data if r.status == status]
        if limit is not None:
            n = int(limit)
            if n < 0:
                n = 0
            if n == 0:
                return []
            data = data[-n:]
        return data

    def clear_history(self) -> int:
        with self._lock:
            n = len(self._history)
            self._history.clear()
            return n

    def health_check(self) -> Dict[str, Any]:
        """Manager 健康检查。"""
        with self._lock:
            running_tasks = len(self._active_executions)
            failed_tasks = 0
            success_tasks = 0
            skipped_tasks = 0
            task_summaries: List[Dict[str, Any]] = []
            for tid, ts in self._task_states.items():
                failed_tasks += ts.failure_count
                success_tasks += ts.run_count
                skipped_tasks += ts.skip_count
                task_summaries.append(
                    {
                        "task_id": tid,
                        "run_count": ts.run_count,
                        "failure_count": ts.failure_count,
                        "skip_count": ts.skip_count,
                        "last_run_at": ts.last_run_at,
                    }
                )
            snapshot = {
                "manager_state": self.state.value,
                "is_terminal": self.is_terminal,
                "task_count": self._registry.size,
                "running_tasks": running_tasks,
                "failed_tasks": failed_tasks,
                "success_tasks": success_tasks,
                "skipped_tasks": skipped_tasks,
                "last_tick": self._last_tick_at,
                "tick_count": self._tick_count,
                "started_at": self._started_at,
                "stopped_at": self._stopped_at,
                "history_size": len(self._history),
                "history_capacity": self._history_capacity,
                "tasks": task_summaries,
            }
        # 附加 state machine 健康信息
        try:
            sm_health = self._state_machine.health_check()
            snapshot["state_machine"] = sm_health
        except Exception:  # noqa: BLE001
            snapshot["state_machine"] = None
        return snapshot

    # ===========================================================
    # 内部工具
    # ===========================================================
    def _build_context(self, task_id: str) -> LifecycleContext:
        """为单次执行构造 LifecycleContext。"""
        runtime_state = _build_runtime_state_view(self._state_machine)
        ctx = LifecycleContext(
            clock=self._clock,
            runtime_state=runtime_state,
            snapshot_view=self._snapshot_view,
            emitter=self._emitter,
            task_id=str(task_id or ""),
        )
        return ctx

    def _make_result(
        self,
        task_id: str,
        status: LifecycleStatus,
        *,
        started_at: float,
        ended_at: float,
        error: Optional[LifecycleError] = None,
        metrics: Optional[Dict[str, Any]] = None,
        next_recommended_at: Optional[float] = None,
    ) -> LifecycleResult:
        """构造一个 LifecycleResult(并归一化字段)。"""
        try:
            duration = max(0, int((ended_at - started_at) * 1000))
        except Exception:
            duration = 0
        return LifecycleResult(
            task_id=str(task_id or ""),
            status=status,
            started_at=float(started_at),
            ended_at=float(ended_at),
            duration_ms=duration,
            metrics=dict(metrics) if metrics else {},
            error=error,
            next_recommended_at=next_recommended_at,
        )

    def _emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """安全发射事件(Emitter 不可用 / 抛错时不影响 Manager)。"""
        if self._emitter is None:
            return
        try:
            self._emitter.emit(event_type, payload, source=self._name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[LifecycleManager(%s)] emit %s 失败: %s",
                self._name,
                event_type,
                exc,
            )

    def _wait_active_executions(self, timeout: Optional[float]) -> None:
        """等待正在执行的任务线程结束。

        - timeout=None: 立即返回
        - timeout=0: 不等待
        - timeout>0: 等待至多 timeout 秒
        """
        if timeout is None or timeout <= 0:
            return
        deadline = self._clock.monotonic() + timeout
        while True:
            with self._execution_lock:
                if not self._active_executions:
                    return
                threads = list(self._active_executions.values())
            for t in threads:
                remaining = deadline - self._clock.monotonic()
                if remaining <= 0:
                    return
                t.join(timeout=remaining)
            with self._execution_lock:
                if not self._active_executions:
                    return
            if self._clock.monotonic() >= deadline:
                return

    # ===========================================================
    # 上下文管理
    # ===========================================================
    def __enter__(self) -> "LifecycleManager":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.stop()

    def __repr__(self) -> str:
        return (
            f"LifecycleManager(name={self._name!r}, "
            f"state={self.state.value}, "
            f"tasks={self._registry.size}, "
            f"tick={self._tick_count})"
        )


__all__ = [
    "LifecycleManager",
    "LifecycleManagerError",
    "EVENT_MANAGER_STARTED",
    "EVENT_MANAGER_PAUSED",
    "EVENT_MANAGER_RESUMED",
    "EVENT_MANAGER_STOPPED",
    "EVENT_MANAGER_TICK",
    "EVENT_TASK_STARTED",
    "EVENT_TASK_COMPLETED",
    "EVENT_TASK_FAILED",
    "EVENT_TASK_SKIPPED",
    "EVENT_TASK_DEFERRED",
]
