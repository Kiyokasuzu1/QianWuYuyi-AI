# -*- coding: utf-8 -*-
"""
src/runtime/supervisor.py

Phase 3.5 Runtime Startup —— RuntimeSupervisor

职责:
- 提供一个常驻主循环 driver,周期性调用 RuntimeCore.tick()
- 发布 system.tick 事件,触发 CognitiveCore 各 layer(Proactive / Memory / Reflection 等)
- 上报 HeartbeatCollector,供 Dashboard 监控
- 异常隔离:任何子任务失败不影响下一次 tick
- 优雅关闭:SIGINT / SIGTERM / atexit 触发 stop()

设计原则:
- 不修改 RuntimeCore / ProactiveEngine / AutonomousScheduler 任何内部实现
- 全部通过现有公开 API 集成(tick() / on_tick() / scan_all_users() / beat() / emit_event())
- 默认禁用(enabled=False),由 config / 启动脚本显式开启
- 单例模式避免重复启动

约束:
- 不修改任何业务模块
- 不修改 Personality / Memory / Emotion / Growth 内部实现
- 不自动接受 GrowthProposal
- 不启动 LLM / 业务调用
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 默认配置
# ============================================================

DEFAULT_TICK_INTERVAL_SECONDS = 60
DEFAULT_STARTUP_GRACE_SECONDS = 5  # 启动后等待 N 秒再开始第一次 tick,避免冷启动
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 120


# ============================================================
# RuntimeSupervisor
# ============================================================

class RuntimeSupervisor:
    """
    Runtime 常驻监督器(轻量包装层,只做驱动,不重写业务逻辑)

    典型用法:
        sup = RuntimeSupervisor(
            runtime_core=rc,
            cognitive_core=cc,
            heartbeat_collector=hc,
            tick_interval_seconds=60,
        )
        sup.start()
        ...
        sup.stop()
    """

    _instance: Optional["RuntimeSupervisor"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        runtime_core: Any = None,
        cognitive_core: Any = None,
        heartbeat_collector: Any = None,
        proactive_engine: Any = None,
        action_dispatcher: Any = None,
        tick_interval_seconds: int = DEFAULT_TICK_INTERVAL_SECONDS,
        startup_grace_seconds: float = DEFAULT_STARTUP_GRACE_SECONDS,
        enabled: bool = True,
        name: str = "runtime_supervisor",
    ) -> None:
        """
        Args:
            runtime_core: RuntimeCore 实例(可选,None 时 tick 步骤跳过)
            cognitive_core: YuyiCognitiveCore 实例(可选,None 时 system.tick 事件跳过)
            heartbeat_collector: HeartbeatCollector 实例(可选,None 时不上报心跳)
            proactive_engine: ProactiveEngine 实例(可选,Phase B 接入)
            action_dispatcher: ActionDispatcher 实例(可选,Phase B 接入)
            tick_interval_seconds: tick 间隔(秒),默认 60
            startup_grace_seconds: 启动后等待多少秒再开始第一次 tick
            enabled: 是否启用(默认 True)
            name: 模块名(用于心跳与日志)
        """
        self.runtime_core = runtime_core
        self.cognitive_core = cognitive_core
        self.heartbeat_collector = heartbeat_collector
        self.proactive_engine = proactive_engine
        self.action_dispatcher = action_dispatcher
        self.tick_interval_seconds = int(tick_interval_seconds)
        self.startup_grace_seconds = float(startup_grace_seconds)
        self.enabled = bool(enabled)
        self.name = name

        # 运行状态
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

        # 统计
        self._tick_count = 0
        self._error_count = 0
        self._last_tick_time: Optional[float] = None
        self._last_error: Optional[str] = None
        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None

        # 信号 handler 注册标记(避免重复注册)
        self._signal_handlers_installed = False
        self._original_sigint_handler = None
        self._original_sigterm_handler = None
        self._atexit_registered = False

        logger.info(
            f"RuntimeSupervisor 初始化: enabled={self.enabled}, "
            f"tick_interval={self.tick_interval_seconds}s, name={self.name}"
        )

    # ============================================================
    # 单例(可选使用)
    # ============================================================

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "RuntimeSupervisor":
        """获取或创建单例(同参数会返回同一实例)"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例(仅测试用)"""
        with cls._instance_lock:
            cls._instance = None

    # ============================================================
    # 公开接口
    # ============================================================

    def start(self) -> bool:
        """
        启动后台 Runtime Loop

        Returns:
            True 表示启动成功(或已在运行)
            False 表示启动失败(如 disabled / 线程已存在)
        """
        if not self.enabled:
            logger.info(f"[{self.name}] disabled,跳过 start()")
            return False

        with self._lock:
            if self._running:
                logger.info(f"[{self.name}] 已在运行,跳过 start()")
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name=f"{self.name}-loop",
                daemon=True,
            )
            self._thread.start()
            self._running = True
            self._start_time = time.time()

        # 注册信号 + atexit(只注册一次)
        self._install_signal_handlers()
        self._register_atexit()

        logger.info(f"[{self.name}] supervisor started, tick_interval={self.tick_interval_seconds}s")
        return True

    def stop(self, timeout: float = 10.0) -> bool:
        """
        停止 Runtime Loop,保存状态

        Args:
            timeout: 等待线程退出的最长时间(秒)

        Returns:
            True 表示成功停止
        """
        with self._lock:
            if not self._running:
                return True

            logger.info(f"[{self.name}] 正在停止 supervisor ...")
            self._stop_event.set()
            self._running = False

        # 等待线程退出
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=timeout)
            except Exception as e:
                logger.warning(f"[{self.name}] 等待线程退出异常: {e}")

        self._stop_time = time.time()

        # 保存 Runtime 状态(如有)
        try:
            if self.runtime_core is not None and hasattr(self.runtime_core, "save_lifecycle"):
                self.runtime_core.save_lifecycle()
                logger.info(f"[{self.name}] runtime_core.save_lifecycle() 已调用")
        except Exception as e:
            logger.warning(f"[{self.name}] save_lifecycle 失败(已隔离): {e}")

        # 最后一次心跳标记为 STOPPED(保留 custom_metrics,便于仪表盘读取 tick_count)
        try:
            if self.heartbeat_collector is not None:
                from src.core.heartbeat import ModuleStatus
                self.heartbeat_collector.beat(
                    module_name=self.name,
                    status=ModuleStatus.STOPPED,
                    error_count=self._error_count,
                    message="supervisor stopped",
                    custom_metrics={
                        "tick_count": self._tick_count,
                        "tick_interval_seconds": self.tick_interval_seconds,
                        "last_error": self._last_error,
                    },
                )
        except Exception as e:
            logger.debug(f"[{self.name}] 上报 stop 心跳失败(已隔离): {e}")

        logger.info(
            f"[{self.name}] supervisor stopped, "
            f"total_ticks={self._tick_count}, errors={self._error_count}"
        )
        return True

    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running and self._thread is not None and self._thread.is_alive()

    def get_status(self) -> Dict[str, Any]:
        """
        返回运行状态(供 Dashboard / 健康检查使用)

        Returns:
            {
                "running": bool,
                "tick_count": int,
                "last_tick_time": float|None,
                "error_count": int,
                "last_error": str|None,
                "uptime": float|None,        # 秒
                "tick_interval_seconds": int,
                "enabled": bool,
                "name": str,
            }
        """
        now = time.time()
        uptime = None
        if self._start_time is not None:
            end = self._stop_time if self._stop_time is not None else now
            uptime = round(end - self._start_time, 2)

        return {
            "running": self.is_running(),
            "tick_count": int(self._tick_count),
            "last_tick_time": self._last_tick_time,
            "error_count": int(self._error_count),
            "last_error": self._last_error,
            "uptime": uptime,
            "tick_interval_seconds": self.tick_interval_seconds,
            "enabled": self.enabled,
            "name": self.name,
        }

    # ============================================================
    # 主循环(线程入口)
    # ============================================================

    def _loop(self) -> None:
        """
        主循环(由后台线程调用)

        while running:
            1. RuntimeCore.tick()
            2. system.tick 事件
            3. ProactiveEngine.scan_all_users()(Phase B 接入)
            4. HeartbeatCollector.beat()
            5. sleep(interval)
        """
        logger.info(f"[{self.name}] supervisor loop 启动")
        logger.info(
            f"[{self.name}] 启动 grace {self.startup_grace_seconds}s, "
            f"interval {self.tick_interval_seconds}s"
        )

        # 启动 grace
        if self.startup_grace_seconds > 0:
            if self._stop_event.wait(timeout=self.startup_grace_seconds):
                logger.info(f"[{self.name}] grace 期间收到 stop 信号,退出")
                return

        while not self._stop_event.is_set():
            try:
                self._tick_iteration()
            except Exception as e:
                # _tick_iteration 内部已 try/except 包裹,此处为兜底
                self._error_count += 1
                self._last_error = repr(e)
                logger.exception(f"[{self.name}] tick 兜底异常(已隔离): {e}")

            # sleep(可被 stop_event 打断)
            if self._stop_event.wait(timeout=self.tick_interval_seconds):
                logger.info(f"[{self.name}] sleep 期间收到 stop 信号,退出")
                break

        logger.info(f"[{self.name}] supervisor loop 退出")

    def _tick_iteration(self) -> None:
        """
        单次 tick 迭代(子任务全部 try/except 隔离)

        步骤:
        1. RuntimeCore.tick()(如有)
        2. system.tick 事件(如有 cognitive_core)
        3. ProactiveEngine.scan_all_users()(Phase B 接入,如有)
        4. ActionDispatcher.flush_pending()(Phase B 接入,如有)
        5. HeartbeatCollector.beat()(如有)
        """
        self._tick_count += 1
        self._last_tick_time = time.time()
        tick_id = self._tick_count

        # ---------- 1. RuntimeCore.tick() ----------
        try:
            if self.runtime_core is not None and hasattr(self.runtime_core, "tick"):
                self.runtime_core.tick()
                logger.debug(f"[{self.name}] tick #{tick_id} RuntimeCore.tick() ok")
        except Exception as e:
            self._error_count += 1
            self._last_error = repr(e)
            logger.exception(f"[{self.name}] tick #{tick_id} RuntimeCore.tick() 失败(已隔离): {e}")

        # ---------- 2. system.tick 事件 ----------
        try:
            if self.cognitive_core is not None and hasattr(self.cognitive_core, "emit_event"):
                self.cognitive_core.emit_event(
                    event_type="system.tick",
                    data={
                        "tick_id": tick_id,
                        "tick_type": "minute",  # 兼容 ProactiveLayer.on_system_tick
                        "source": self.name,
                    },
                    user_id=None,
                    source=self.name,
                )
                logger.debug(f"[{self.name}] tick #{tick_id} system.tick event emitted")
        except Exception as e:
            self._error_count += 1
            self._last_error = repr(e)
            logger.exception(f"[{self.name}] tick #{tick_id} system.tick 事件失败(已隔离): {e}")

        # ---------- 3. ProactiveEngine.scan_all_users()(Phase B 接入) ----------
        try:
            if self.proactive_engine is not None and hasattr(self.proactive_engine, "scan_all_users"):
                self.proactive_engine.scan_all_users()
                logger.debug(f"[{self.name}] tick #{tick_id} proactive scan ok")
        except Exception as e:
            self._error_count += 1
            self._last_error = repr(e)
            logger.exception(
                f"[{self.name}] tick #{tick_id} proactive scan 失败(已隔离): {e}"
            )

        # ---------- 4. ActionDispatcher flush(Phase B 接入,可选) ----------
        try:
            if self.action_dispatcher is not None:
                # 兼容不同 dispatcher 接口
                flush = getattr(self.action_dispatcher, "flush_pending", None)
                if callable(flush):
                    flush()
                    logger.debug(f"[{self.name}] tick #{tick_id} action flush ok")
        except Exception as e:
            self._error_count += 1
            self._last_error = repr(e)
            logger.exception(
                f"[{self.name}] tick #{tick_id} action flush 失败(已隔离): {e}"
            )

        # ---------- 5. HeartbeatCollector.beat() ----------
        try:
            if self.heartbeat_collector is not None:
                # 延迟导入避免循环依赖
                from src.core.heartbeat import ModuleStatus
                self.heartbeat_collector.beat(
                    module_name=self.name,
                    status=ModuleStatus.RUNNING,
                    error_count=self._error_count,
                    message=f"tick #{tick_id} ok",
                    custom_metrics={
                        "tick_count": self._tick_count,
                        "tick_interval_seconds": self.tick_interval_seconds,
                        "last_error": self._last_error,
                    },
                )
        except Exception as e:
            # 心跳失败不计入 error_count(避免噪声)
            logger.debug(f"[{self.name}] tick #{tick_id} heartbeat 失败(已隔离): {e}")

        # 控制台 INFO 输出(模拟 [Yuyi Runtime] 风格)
        logger.info(
            f"[Yuyi Runtime] tick #{tick_id} completed, heartbeat ok, "
            f"errors={self._error_count}"
        )

    # ============================================================
    # 信号 + atexit
    # ============================================================

    def _install_signal_handlers(self) -> None:
        """
        注册 SIGINT / SIGTERM handler(只注册一次,启动时调用)
        """
        if self._signal_handlers_installed:
            return

        try:
            self._original_sigint_handler = signal.getsignal(signal.SIGINT)
            self._original_sigterm_handler = signal.getsignal(signal.SIGTERM)

            def _handler(signum: int, frame: Any) -> None:
                logger.info(f"[{self.name}] 收到信号 {signum}, 触发 stop()")
                try:
                    self.stop(timeout=5.0)
                except Exception as e:
                    logger.exception(f"[{self.name}] 信号处理 stop 失败: {e}")
                # 恢复原 handler 后重新抛信号(允许 KeyboardInterrupt 正常传播)
                try:
                    if signum == signal.SIGINT and callable(self._original_sigint_handler):
                        signal.signal(signal.SIGINT, self._original_sigint_handler)
                        os.kill(os.getpid(), signal.SIGINT)
                    elif signum == signal.SIGTERM and callable(self._original_sigterm_handler):
                        signal.signal(signal.SIGTERM, self._original_sigterm_handler)
                        os.kill(os.getpid(), signal.SIGTERM)
                except Exception:
                    # 兜底:如果原 handler 不可用,直接退出
                    os._exit(0)

            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
            self._signal_handlers_installed = True
            logger.debug(f"[{self.name}] SIGINT/SIGTERM handlers 已注册")
        except Exception as e:
            # 非主线程 / Windows 下可能失败,仅记录
            logger.warning(f"[{self.name}] 信号 handler 注册失败(非阻塞): {e}")

    def _register_atexit(self) -> None:
        """注册 atexit(进程退出兜底)"""
        if self._atexit_registered:
            return
        try:
            import atexit

            def _atexit_handler() -> None:
                try:
                    if self.is_running():
                        logger.info(f"[{self.name}] atexit 触发 stop()")
                        self.stop(timeout=5.0)
                except Exception as e:
                    logger.debug(f"[{self.name}] atexit handler 异常: {e}")

            atexit.register(_atexit_handler)
            self._atexit_registered = True
            logger.debug(f"[{self.name}] atexit handler 已注册")
        except Exception as e:
            logger.warning(f"[{self.name}] atexit 注册失败(非阻塞): {e}")


# ============================================================
# 便捷函数
# ============================================================

def get_supervisor(**kwargs: Any) -> RuntimeSupervisor:
    """获取全局 RuntimeSupervisor 单例"""
    return RuntimeSupervisor.get_instance(**kwargs)


__all__ = [
    "RuntimeSupervisor",
    "DEFAULT_TICK_INTERVAL_SECONDS",
    "DEFAULT_STARTUP_GRACE_SECONDS",
    "DEFAULT_HEARTBEAT_TIMEOUT_SECONDS",
    "get_supervisor",
]
