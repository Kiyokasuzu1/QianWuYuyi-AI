# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_runtime.py

P2.7 Phase D-1: LifecycleRuntime 薄层。

职责(任务书 Step 3 边界):
- 注册周期任务(委托 LifecycleManager → LifecycleRegistry)
- 判断是否到期(委托 BaseLifecycleTask.should_run + AfterInterval 条件)
- 调用任务(委托 LifecycleManager.tick 调度闭环)
- 写审计(每次执行结果转 LifecycleEvent → LifecycleAuditWriter JSONL)
- 异常隔离(Manager 已保证单任务失败不影响其他任务;本层再兜一层)

不负责:
- 不实现 Memory/Growth/Emotion/SelfModel 任何认知逻辑
- 不写任何业务状态文件(只写审计 JSONL)
- 不进入 apply 流程

架构(与任务书同构):
    LifecycleRuntime
        ↓
    LifecycleManager(调度闭环) → LifecycleRegistry
        ↓
    LifecycleTask(BaseLifecycleTask 子类)

驱动:
- 复用 src/runtime/scheduler.py 的 Scheduler(threading.Timer 链)周期触发 tick()
- 默认不接线任何生产入口(与 Phase C drain 同构:能力存在,config 未开启即不运行)
- 测试用 FrozenClock + 手动 tick() 驱动,无需真实线程
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from src.contracts.lifecycle_event_schema import (
    LifecycleEvent,
    SOURCE_LIFECYCLE_RUNTIME,
    TRIGGER_TYPE_TIMER,
    now_iso,
)
from src.runtime.lifecycle.audit_writer import LifecycleAuditWriter
from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
from src.runtime.lifecycle.lifecycle_result import LifecycleResult
from src.runtime.lifecycle.lifecycle_task import LifecycleTask
from src.runtime.scheduler import Scheduler


DEFAULT_TICK_INTERVAL_SECONDS = 60.0
DEFAULT_AUDIT_PATH = ".cache/audit/lifecycle_events.jsonl"
DEFAULT_IDEMPOTENCY_BUCKET_SECONDS = 3600.0
DRIVE_TASK_NAME = "lifecycle_runtime_tick"


class LifecycleRuntime:
    """生命周期运行薄层:装配调度闭环 + 审计收口 + 周期驱动。"""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        *,
        clock: Optional[Clock] = None,
        manager: Optional[LifecycleManager] = None,
        audit_writer: Optional[LifecycleAuditWriter] = None,
        scheduler: Optional[Scheduler] = None,
    ) -> None:
        cfg = dict(config or {})
        try:
            tick_interval = float(cfg.get("tick_interval_seconds", DEFAULT_TICK_INTERVAL_SECONDS))
        except Exception:
            tick_interval = DEFAULT_TICK_INTERVAL_SECONDS
        if tick_interval <= 0:
            tick_interval = DEFAULT_TICK_INTERVAL_SECONDS
        self._tick_interval_seconds = tick_interval

        audit_path = str(cfg.get("audit_path", DEFAULT_AUDIT_PATH) or DEFAULT_AUDIT_PATH)

        self._clock: Clock = clock if clock is not None else SystemClock()
        self._manager = manager if manager is not None else LifecycleManager(
            clock=self._clock,
            name="p2.7.d1",
        )
        self._audit = audit_writer if audit_writer is not None else LifecycleAuditWriter(audit_path)
        self._scheduler = scheduler if scheduler is not None else Scheduler()

        self._started = False
        self._lock = threading.RLock()

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def manager(self) -> LifecycleManager:
        return self._manager

    @property
    def audit_writer(self) -> LifecycleAuditWriter:
        return self._audit

    @property
    def is_started(self) -> bool:
        with self._lock:
            return self._started

    # --------------------------------------------------------
    # 任务注册(委托 Manager → Registry)
    # --------------------------------------------------------
    def register_task(self, task: LifecycleTask, *, replace: bool = False) -> bool:
        return self._manager.register(task, replace=replace)

    def unregister_task(self, task_id: str):
        return self._manager.unregister(task_id)

    def get_task(self, task_id: str) -> Optional[LifecycleTask]:
        return self._manager.get_task(task_id)

    def task_ids(self) -> List[str]:
        return self._manager.registry.task_ids()

    # --------------------------------------------------------
    # 生命周期
    # --------------------------------------------------------
    def start(self) -> bool:
        """启动(幂等)。

        - 重复 start 不会重复注册驱动任务、不会重复执行任务
          (本层 _started 标志 + Manager.start 幂等 + TaskState.last_run_at 三重保护)
        """
        with self._lock:
            if self._started:
                return True
            if not self._manager.start(reason="lifecycle_runtime_boot"):
                return False
            self._scheduler.add_interval_task(
                name=DRIVE_TASK_NAME,
                interval_seconds=self._tick_interval_seconds,
                callback=self.tick,
            )
            self._scheduler.start()
            self._started = True
            return True

    def stop(self) -> bool:
        """停止(幂等)。停止后 tick() 不再调度任务(Manager 状态机保证)。"""
        with self._lock:
            if not self._started:
                return True
            self._scheduler.stop()
            ok = self._manager.stop(reason="lifecycle_runtime_shutdown")
            self._started = False
            return ok

    # --------------------------------------------------------
    # 调度与审计
    # --------------------------------------------------------
    def tick(self) -> List[LifecycleEvent]:
        """执行一次调度并落审计。

        - Manager 未 RUNNING 时 Manager.tick() 返回空列表(本层直接透传)
        - 每个 LifecycleResult 转一条 LifecycleEvent 写 JSONL
        - 审计写入失败不影响返回值(事件仍在内存返回,审计尽力而为)
        """
        results: List[LifecycleResult] = []
        try:
            results = self._manager.tick()
        except Exception:
            # 极端安全网:Manager 异常不向上传播(审计层自身也隔离)
            return []
        events: List[LifecycleEvent] = []
        for result in results:
            task = self._manager.get_task(result.task_id)
            event = self._to_event(task, result)
            if event is None:
                continue
            self._audit.append(event)
            events.append(event)
        return events

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """运行状态快照(供观测/健康检查;只读)。"""
        tasks_status: Dict[str, Any] = {}
        for task_id in self.task_ids():
            state = self._manager.get_task_state(task_id)
            tasks_status[task_id] = state.to_dict() if state is not None else {}
        return {
            "started": self.is_started,
            "manager_state": self._manager.state.value,
            "manager_tick_count": self._manager.tick_count,
            "manager_last_tick_at": self._manager.last_tick_at,
            "tick_interval_seconds": self._tick_interval_seconds,
            "audit_path": str(self._audit.path),
            "task_count": len(tasks_status),
            "tasks": tasks_status,
        }

    # --------------------------------------------------------
    # 内部:LifecycleResult → LifecycleEvent
    # --------------------------------------------------------
    def _to_event(
        self,
        task: Optional[LifecycleTask],
        result: LifecycleResult,
    ) -> Optional[LifecycleEvent]:
        if result is None:
            return None
        try:
            task_id = str(getattr(result, "task_id", "") or "")
            task_type = str(getattr(task, "TASK_TYPE", "") or task_id)
            bucket_seconds = float(
                getattr(task, "IDEMPOTENCY_BUCKET_SECONDS", DEFAULT_IDEMPOTENCY_BUCKET_SECONDS)
                or DEFAULT_IDEMPOTENCY_BUCKET_SECONDS
            )
            now = self._clock.now()
            bucket = int(now / bucket_seconds) if bucket_seconds > 0 else 0

            state = self._manager.get_task_state(task_id)
            run_count = int(getattr(state, "run_count", 0) or 0)
            failure_count = int(getattr(state, "failure_count", 0) or 0)

            # result 信封(只审计,不含任何状态变更语义)
            reason = ""
            error_category = ""
            error_message = ""
            if getattr(result, "error", None) is not None:
                error_category = str(getattr(result.error, "category", "") or "")
                error_message = str(getattr(result.error, "message", "") or "")
                reason = f"error:{error_category}:{error_message}" if error_category else f"error:{error_message}"
            elif result.status.value == "SKIPPED":
                _m = result.metrics or {}
                reason = str(_m.get("reason", "") or _m.get("skip_reason", "") or "")
            result_dict = {
                "status": result.status.value,
                "reason": reason,
                "duration_ms": int(getattr(result, "duration_ms", 0) or 0),
                "produced_events": list(getattr(result, "produced_events", []) or []),
                "produced_proposal_ids": [],
                "metrics": dict(getattr(result, "metrics", {}) or {}),
            }
            audit_dict = {
                "idempotency_key": f"{task_type}:{bucket}",
                "run_count": run_count,
                "failure_count": failure_count,
                "error": error_message,
                "error_category": error_category,
            }
            return LifecycleEvent(
                timestamp=now_iso(now),
                trigger_type=TRIGGER_TYPE_TIMER,
                task_type=task_type,
                source=SOURCE_LIFECYCLE_RUNTIME,
                result=result_dict,
                audit=audit_dict,
            )
        except Exception:
            return None

    def __repr__(self) -> str:
        return (
            f"LifecycleRuntime(started={self._started!r}, "
            f"manager_state={self._manager.state.value!r}, "
            f"tasks={len(self.task_ids())})"
        )


__all__ = [
    "LifecycleRuntime",
    "DEFAULT_TICK_INTERVAL_SECONDS",
    "DEFAULT_AUDIT_PATH",
    "DEFAULT_IDEMPOTENCY_BUCKET_SECONDS",
    "DRIVE_TASK_NAME",
]
