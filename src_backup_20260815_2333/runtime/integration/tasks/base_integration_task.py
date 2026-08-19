# -*- coding: utf-8 -*-
"""
src/runtime/integration/tasks/base_integration_task.py

Phase 5.0-D2 Step 3: 业务 Task 公共基类。

职责：
- 提供 BaseIntegrationTask 抽象基类
- 持有 Adapter 引用(注入)
- 维护执行统计(execution_count / success_count / failure_count)
- 提供 on_success / on_failure 默认实现
- 不实现 execute()(子类必须实现)
- 不调用业务模块

约束：
- 仅依赖 src.runtime.integration.adapters.base
- 仅依赖 src.runtime.lifecycle(通过 BaseLifecycleTask)
- 不修改 Lifecycle Core
- 不修改 Adapter 实现
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from src.runtime.integration.adapters.base import BaseAdapter
from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.lifecycle.lifecycle_errors import LifecycleError
from src.runtime.lifecycle.lifecycle_result import LifecycleResult
from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask


logger = logging.getLogger(__name__)


# ============================================================
# 业务 Task 异常
# ============================================================
class IntegrationTaskError(Exception):
    """IntegrationTask 错误基类。"""


# ============================================================
# BaseIntegrationTask
# ============================================================
class BaseIntegrationTask(BaseLifecycleTask):
    """业务 Task 公共基类。

    设计：
    - 子类必须实现 _do_execute(ctx) -> IntegrationEvent
    - 父类负责统计、异常包装、生命周期回调
    - adapter 通过构造函数注入
    - 不调用 adapter 任何业务方法,只调用 emit_* 便捷方法
    """

    def __init__(
        self,
        task_id: str,
        owner: str,
        adapter: BaseAdapter,
        *,
        display_name: str = "",
        priority: int = 5,
        interval_seconds: float = 60.0,
        budget_ms: Optional[int] = None,
    ) -> None:
        if not isinstance(adapter, BaseAdapter):
            raise IntegrationTaskError(
                f"BaseIntegrationTask 需要 BaseAdapter 实例,实际: {type(adapter).__name__}"
            )

        # 引入条件原语
        from src.runtime.lifecycle.lifecycle_decision import AfterInterval

        super().__init__(
            task_id=task_id,
            owner=owner,
            display_name=display_name or task_id,
            priority=priority,
            condition=AfterInterval(interval=float(interval_seconds or 0.0)),
            budget_ms=budget_ms,
            interval_seconds=float(interval_seconds) if interval_seconds else None,
        )
        self._adapter = adapter
        self._lock = threading.RLock()

        # 统计
        self._execution_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._last_event: Optional[IntegrationEvent] = None
        self._last_event_type: str = ""
        self._last_run_at: Optional[float] = None
        self._last_error: str = ""

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def adapter(self) -> BaseAdapter:
        return self._adapter

    @property
    def execution_count(self) -> int:
        with self._lock:
            return self._execution_count

    @property
    def success_count(self) -> int:
        with self._lock:
            return self._success_count

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    @property
    def last_event(self) -> Optional[IntegrationEvent]:
        with self._lock:
            return self._last_event

    @property
    def last_event_type(self) -> str:
        with self._lock:
            return self._last_event_type

    @property
    def last_run_at(self) -> Optional[float]:
        with self._lock:
            return self._last_run_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    # --------------------------------------------------------
    # 执行入口(由 LifecycleManager 调用)
    # --------------------------------------------------------
    def execute(self, context: Any) -> Any:
        """执行入口。

        流程:
        1) 累加 execution_count
        2) 调用 _do_execute() 获取 IntegrationEvent
        3) 记录 last_event
        4) 返回 dict(LifecycleResult 友好格式)
        """
        with self._lock:
            self._execution_count += 1
            self._last_run_at = context.now() if hasattr(context, "now") else None

        try:
            event = self._do_execute(context)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._failure_count += 1
                self._last_error = str(exc)
            # 重新抛出让 BaseLifecycleTask.run_safely 包装为 LifecycleError
            raise

        if not isinstance(event, IntegrationEvent):
            with self._lock:
                self._failure_count += 1
                self._last_error = "_do_execute 返回非 IntegrationEvent"
            raise IntegrationTaskError(
                f"{self.task_id}._do_execute() 必须返回 IntegrationEvent,实际: {type(event).__name__}"
            )

        with self._lock:
            self._last_event = event
            self._last_event_type = event.event_type

        # 返回 dict 让 LifecycleResult 自动包装
        return {
            "events": [event.event_id],
            "metrics": {
                "adapter_name": self._adapter.name,
                "adapter_emitted_count": self._adapter.emitted_count,
                "event_type": event.event_type,
            },
        }

    # --------------------------------------------------------
    # 子类必须实现
    # --------------------------------------------------------
    def _do_execute(self, context: Any) -> IntegrationEvent:
        """子类实现:调用 adapter 触发对应 IntegrationEvent。

        返回 IntegrationEvent 实例。
        """
        raise NotImplementedError(
            f"{type(self).__name__}._do_execute() 未实现"
        )

    # --------------------------------------------------------
    # 钩子
    # --------------------------------------------------------
    def on_success(self, result: LifecycleResult) -> None:
        with self._lock:
            self._success_count += 1
        return None

    def on_failure(self, error: LifecycleError) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_error = str(error)
        return None

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "task_id": self.task_id,
                "owner": self.owner,
                "adapter_name": self._adapter.name,
                "execution_count": self._execution_count,
                "success_count": self._success_count,
                "failure_count": self._failure_count,
                "last_event_type": self._last_event_type,
                "last_run_at": self._last_run_at,
                "last_error": self._last_error,
                "interval_seconds": self.interval_seconds,
                "priority": self.priority,
            }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(task_id={self.task_id!r}, "
            f"adapter={self._adapter.name!r}, "
            f"exec={self.execution_count}, success={self.success_count}, "
            f"failure={self.failure_count})"
        )
