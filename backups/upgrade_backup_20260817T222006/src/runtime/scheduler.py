"""
Scheduler —— 周期任务调度器

管理定时/周期任务，
供 RuntimeCore 驱动生命周期。

设计原则：
- 轻量，基于 threading.Timer
- 可启动/停止
- 任务异常不影响其他任务
"""

import threading
import time
from typing import Callable, Dict, Optional


class ScheduledTask:
    """单个定时任务"""

    def __init__(
        self,
        name: str,
        interval_seconds: float,
        callback: Callable,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ):
        self.name = name
        self.interval_seconds = interval_seconds
        self.callback = callback
        self.args = args or ()
        self.kwargs = kwargs or {}
        self._timer: Optional[threading.Timer] = None
        self._running = False
        self._last_run = 0.0
        self._error_count = 0

    def start(self) -> None:
        """启动任务"""
        if self._running:
            return
        self._running = True
        self._schedule_next()

    def stop(self) -> None:
        """停止任务"""
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self) -> None:
        """调度下一次执行"""
        if not self._running:
            return
        self._timer = threading.Timer(self.interval_seconds, self._run_wrapper)
        self._timer.daemon = True
        self._timer.start()

    def _run_wrapper(self) -> None:
        """执行包装器（捕获异常）"""
        if not self._running:
            return
        self._last_run = time.time()
        try:
            self.callback(*self.args, **self.kwargs)
        except Exception as e:
            self._error_count += 1
            print(f"[Scheduler] Task '{self.name}' error: {e}")
        self._schedule_next()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_run(self) -> float:
        return self._last_run

    @property
    def error_count(self) -> int:
        return self._error_count


class Scheduler:
    """
    周期任务调度器

    管理多个定时任务。
    """

    def __init__(self):
        self._tasks: Dict[str, ScheduledTask] = {}

    def add_interval_task(
        self,
        name: str,
        interval_seconds: float,
        callback: Callable,
        args: Optional[tuple] = None,
        kwargs: Optional[dict] = None,
    ) -> ScheduledTask:
        """
        添加周期任务

        Args:
            name: 任务名称
            interval_seconds: 执行间隔（秒）
            callback: 回调函数
            args: 位置参数
            kwargs: 关键字参数

        Returns:
            任务对象
        """
        task = ScheduledTask(name, interval_seconds, callback, args, kwargs)
        self._tasks[name] = task
        return task

    def remove_task(self, name: str) -> None:
        """移除任务"""
        task = self._tasks.pop(name, None)
        if task:
            task.stop()

    def start(self) -> None:
        """启动所有任务"""
        for task in self._tasks.values():
            task.start()

    def start_task(self, name: str) -> None:
        """启动单个任务"""
        task = self._tasks.get(name)
        if task:
            task.start()

    def stop(self) -> None:
        """停止所有任务"""
        for task in self._tasks.values():
            task.stop()

    def stop_task(self, name: str) -> None:
        """停止单个任务"""
        task = self._tasks.get(name)
        if task:
            task.stop()

    def get_task(self, name: str) -> Optional[ScheduledTask]:
        """获取任务"""
        return self._tasks.get(name)

    def list_tasks(self) -> Dict[str, dict]:
        """列出所有任务状态"""
        return {
            name: {
                "running": task.is_running,
                "last_run": task.last_run,
                "error_count": task.error_count,
                "interval": task.interval_seconds,
            }
            for name, task in self._tasks.items()
        }