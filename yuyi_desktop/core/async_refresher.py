# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/async_refresher.py

Yuyi Desktop 异步刷新框架 (Phase D.2.3 P0 修复)。

目的:
- 解决 Qt 主线程同步执行 HTTP 请求导致的"未响应"问题
- 把耗时请求丢到 QThreadPool,完成后通过 Signal 回到主线程更新 UI
- 不修改任何 Service / ApiClient / 后端逻辑
- 业务调用方式与原 Widget.refresh() 完全兼容 (返回 dict,失败 envelope)

使用方式:
    from yuyi_desktop.core.async_refresher import AsyncRefresher

    class MyWidget(QWidget):
        def __init__(self):
            super().__init__()
            self._refresher = AsyncRefresher(self, fetch_fn=self._fetch_in_worker)
            self._refresher.finished.connect(self._on_data_ready)
            self._refresher.failed.connect(self._on_data_failed)
            # 替换 QTimer.timeout -> self.refresh
            self._timer.timeout.connect(self._refresher.submit)

        def _fetch_in_worker(self) -> Dict[str, Any]:
            # 此函数在线程池中执行,严禁操作 QWidget
            return {
                "overview": self._service.get_overview(),
                "details": self._api.get_path(...)
            }

        def _on_data_ready(self, data: Dict[str, Any]):
            # 此函数在主线程,可安全操作 QWidget
            self._render(data)

约束:
- fetch_fn 必须是纯函数(无 GUI 副作用),在子线程执行
- 渲染方法通过 Signal 槽自动回到主线程
- 不会因为 refresh() 阻塞 UI
- 任何异常不会抛到主线程 (failed signal 统一处理)
"""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

logger = logging.getLogger(__name__)


# ============================================================
# Worker
# ============================================================
class _RefreshWorker(QRunnable):
    """单次刷新任务。

    - 在 QThreadPool 线程中执行 fetch_fn
    - 通过 signals 信号把结果或异常回传主线程
    - 自身不持有 QWidget 引用,完全不操作 GUI
    """

    class _Signals(QObject):
        """嵌套 QObject 用于在线程间发送信号。"""
        finished = Signal(dict)   # fetch_fn 成功返回的 dict
        failed = Signal(str)      # 异常 message (str)

    def __init__(self, fetch_fn: Callable[[], Dict[str, Any]], tag: str = "") -> None:
        super().__init__()
        self._fetch_fn = fetch_fn
        self._tag = tag or "worker"
        self.signals = self._Signals()
        # 防止 QRunnable 被立即 GC
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            logger.debug("[AsyncRefresher] worker start tag=%s", self._tag)
            data = self._fetch_fn()
            if not isinstance(data, dict):
                # 业务调用方应返回 dict; 兼容返回 envelope
                data = {"_raw": data}
            self.signals.finished.emit(data)
            logger.debug("[AsyncRefresher] worker ok tag=%s keys=%s", self._tag, list(data.keys()))
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"
            logger.warning("[AsyncRefresher] worker failed tag=%s err=%s", self._tag, err)
            logger.debug("[AsyncRefresher] traceback:\n%s", traceback.format_exc())
            self.signals.failed.emit(err)


# ============================================================
# AsyncRefresher
# ============================================================
class AsyncRefresher(QObject):
    """异步刷新调度器。

    关键设计:
    - 提交任务到全局 QThreadPool,fetch_fn 在子线程执行
    - finished / failed 信号自动通过 Qt 事件队列回到创建者所在线程
    - 内置 inflight 标记:任务未完成时新提交会丢弃,避免堆积
    - 失败不会抛到 GUI,仅通过 failed 信号

    Attributes:
        finished(dict): fetch_fn 返回 dict 时触发 (主线程)
        failed(str):    fetch_fn 抛异常时触发 (主线程)
    """

    finished = Signal(dict)
    failed = Signal(str)

    # 默认最大线程数: 保守值,避免与 requests 池抢占
    _DEFAULT_MAX_THREADS = 4

    def __init__(
        self,
        parent: Optional[QObject] = None,
        fetch_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        tag: str = "refresher",
    ) -> None:
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._tag = tag
        self._inflight = False
        self._cancelled = False  # Phase D.2.4: 取消后丢弃所有信号
        self._inflight_lock = threading.Lock()
        self._total = 0
        self._ok = 0
        self._failed = 0
        self._skipped = 0  # Phase D.2.4: 因 inflight 跳过的次数
        self._last_error = ""
        self._last_submit_ts = 0.0
        # 全局池(懒加载)
        self._pool = QThreadPool.globalInstance()
        try:
            # 设置线程上限,避免 3 个 widget 同时触发 16+ 线程
            self._pool.setMaxThreadCount(self._DEFAULT_MAX_THREADS)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def set_fetch_fn(self, fetch_fn: Callable[[], Dict[str, Any]]) -> None:
        """注入/替换 fetch 函数。"""
        self._fetch_fn = fetch_fn

    def set_tag(self, tag: str) -> None:
        self._tag = tag

    # --------------------------------------------------------
    # 提交
    # --------------------------------------------------------
    def submit(self) -> bool:
        """提交一次刷新任务。

        严格 inflight 策略:
        - 若已有任务在跑,本次直接跳过 (避免堆积)
        - 若 refresher 已 cancel,所有 submit 都被丢弃
        - timer 5s 一次,即使跳过 5 次,下一次 5s 仍能拿到最新数据

        Returns:
            True  - 任务已提交
            False - 已有任务在运行 / 已取消 / 未设 fetch_fn
        """
        with self._inflight_lock:
            if self._cancelled:
                logger.debug("[AsyncRefresher] submit ignored (cancelled) tag=%s", self._tag)
                return False
            if self._inflight:
                self._skipped += 1
                logger.debug(
                    "[AsyncRefresher] submit skipped (inflight) tag=%s skipped=%d",
                    self._tag, self._skipped,
                )
                return False
            if self._fetch_fn is None:
                logger.warning("[AsyncRefresher] submit ignored (no fetch_fn) tag=%s", self._tag)
                return False
            self._inflight = True
            self._total += 1
            import time as _t
            self._last_submit_ts = _t.monotonic()

        worker = _RefreshWorker(self._fetch_fn, tag=self._tag)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)
        # Phase D.2.4: 改为 debug 级别,避免每 5s 一行噪音
        logger.debug("[AsyncRefresher] submit tag=%s total=%d", self._tag, self._total)
        return True

    def force_submit(self) -> bool:
        """强制提交(忽略 inflight 标记,用于手动刷新按钮)。"""
        with self._inflight_lock:
            # 强制重置 inflight
            self._inflight = False
        return self.submit()

    def cancel(self) -> None:
        """取消 refresher。Phase D.2.4: 防止 widget 销毁后 worker 信号回主线程抛异常。

        行为:
        - 设置 _cancelled = True
        - 断开信号连接(避免已入队的信号回主线程)
        - 不尝试 kill 已在跑的 worker (QRunnable 无法中断)
        """
        with self._inflight_lock:
            self._cancelled = True
        try:
            self.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            self.failed.disconnect()
        except (TypeError, RuntimeError):
            pass
        logger.info("[AsyncRefresher] cancelled tag=%s", self._tag)

    def is_cancelled(self) -> bool:
        with self._inflight_lock:
            return self._cancelled

    # --------------------------------------------------------
    # 信号处理 (主线程)
    # --------------------------------------------------------
    @Slot(dict)
    def _on_finished(self, data: Dict[str, Any]) -> None:
        with self._inflight_lock:
            if self._cancelled:
                # Phase D.2.4: 已 cancel, 丢弃信号
                return
            self._inflight = False
            self._ok += 1
            self._last_error = ""
        # 转发到订阅者
        try:
            self.finished.emit(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AsyncRefresher] finished emit error tag=%s: %s", self._tag, exc)

    @Slot(str)
    def _on_failed(self, err: str) -> None:
        with self._inflight_lock:
            if self._cancelled:
                return
            self._inflight = False
            self._failed += 1
            self._last_error = err
        try:
            self.failed.emit(err)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AsyncRefresher] failed emit error tag=%s: %s", self._tag, exc)

    # --------------------------------------------------------
    # 状态查询 (供 UI 展示)
    # --------------------------------------------------------
    def is_running(self) -> bool:
        with self._inflight_lock:
            return self._inflight

    def stats(self) -> Dict[str, Any]:
        with self._inflight_lock:
            return {
                "running": self._inflight,
                "cancelled": self._cancelled,
                "total": self._total,
                "ok": self._ok,
                "failed": self._failed,
                "skipped": self._skipped,
                "last_error": self._last_error,
                "tag": self._tag,
            }


# ============================================================
# 模块导出
# ============================================================
__all__ = [
    "AsyncRefresher",
]
