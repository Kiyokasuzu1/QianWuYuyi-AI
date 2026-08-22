"""
模块心跳系统

每个模块定期上报心跳，管理中心收集状态，
供管理面板实时展示模块运行状况。

不仅知道"配置开启了"，更知道"真的在运行吗"。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModuleStatus(Enum):
    """模块运行状态"""
    RUNNING = "running"         # 正常运行
    IDLE = "idle"               # 已启用但空闲
    STOPPED = "stopped"         # 已停止/未启用
    ERROR = "error"             # 运行出错
    STARTING = "starting"       # 正在启动
    DEGRADED = "degraded"       # 降级运行（部分功能不可用）


@dataclass
class HeartbeatData:
    """单次心跳数据"""
    module_name: str
    status: ModuleStatus = ModuleStatus.STOPPED
    last_tick: float = 0.0          # 时间戳
    cpu_percent: float = 0.0        # CPU 占用（可选）
    memory_mb: float = 0.0          # 内存占用 MB（可选）
    error_count: int = 0            # 累计错误数
    message: str = ""               # 附加消息
    custom_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_name": self.module_name,
            "status": self.status.value,
            "last_tick": self.last_tick,
            "last_tick_ago": round(time.time() - self.last_tick, 1) if self.last_tick else -1,
            "cpu_percent": self.cpu_percent,
            "memory_mb": round(self.memory_mb, 1),
            "error_count": self.error_count,
            "message": self.message,
            "custom_metrics": self.custom_metrics,
        }


class HeartbeatCollector:
    """
    心跳收集器 —— 全局单例

    模块通过 beat() 上报心跳，管理面板通过 get_status() 查询。
    超时未上报的模块自动标记为 STOPPED。
    """

    _instance: Optional[HeartbeatCollector] = None
    _lock = threading.Lock()

    def __init__(self, timeout: float = 60.0):
        """
        Args:
            timeout: 心跳超时秒数，超过此时间未上报则标记为 STOPPED
        """
        self._timeout = timeout
        self._heartbeats: Dict[str, HeartbeatData] = {}
        self._callbacks: List[Callable[[str, HeartbeatData], None]] = []

    @classmethod
    def get_instance(cls, timeout: float = 60.0) -> HeartbeatCollector:
        """获取全局单例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(timeout=timeout)
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（仅测试用）"""
        with cls._lock:
            cls._instance = None

    def beat(self, module_name: str, status: ModuleStatus = ModuleStatus.RUNNING,
             cpu_percent: float = 0.0, memory_mb: float = 0.0,
             error_count: int = 0, message: str = "",
             custom_metrics: Optional[Dict[str, Any]] = None):
        """
        模块上报心跳

        Args:
            module_name: 模块名
            status: 当前状态
            cpu_percent: CPU 占用百分比
            memory_mb: 内存占用 MB
            error_count: 累计错误数
            message: 附加消息
            custom_metrics: 自定义指标
        """
        data = HeartbeatData(
            module_name=module_name,
            status=status,
            last_tick=time.time(),
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            error_count=error_count,
            message=message,
            custom_metrics=custom_metrics or {},
        )

        with self._lock:
            self._heartbeats[module_name] = data

        # 触发回调
        for cb in self._callbacks:
            try:
                cb(module_name, data)
            except Exception as e:
                logger.warning(f"心跳回调执行失败: {e}")

    def register_stopped(self, module_name: str, message: str = "未启用"):
        """注册一个已停止/未启用的模块"""
        data = HeartbeatData(
            module_name=module_name,
            status=ModuleStatus.STOPPED,
            last_tick=time.time(),
            message=message,
        )
        with self._lock:
            self._heartbeats[module_name] = data

    def get_status(self, module_name: str) -> Optional[Dict[str, Any]]:
        """获取指定模块状态"""
        with self._lock:
            data = self._heartbeats.get(module_name)
            if data is None:
                return None

            # 检查是否超时
            if (data.status in (ModuleStatus.RUNNING, ModuleStatus.IDLE, ModuleStatus.DEGRADED)
                    and time.time() - data.last_tick > self._timeout):
                data.status = ModuleStatus.STOPPED
                data.message = "心跳超时"

            return data.to_dict()

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模块状态"""
        result = {}
        with self._lock:
            for name in list(self._heartbeats.keys()):
                data = self._heartbeats[name]
                # 检查超时
                if (data.status in (ModuleStatus.RUNNING, ModuleStatus.IDLE, ModuleStatus.DEGRADED)
                        and time.time() - data.last_tick > self._timeout):
                    data.status = ModuleStatus.STOPPED
                    data.message = "心跳超时"
                result[name] = data.to_dict()
        return result

    def on_heartbeat(self, callback: Callable[[str, HeartbeatData], None]):
        """注册心跳回调"""
        self._callbacks.append(callback)

    def remove_module(self, module_name: str):
        """移除模块的心跳记录"""
        with self._lock:
            self._heartbeats.pop(module_name, None)


class HeartbeatReporter:
    """
    心跳上报器 —— 供模块使用

    模块创建一个 HeartbeatReporter 实例，
    在自己的工作循环中定期调用 tick() 即可。
    """

    def __init__(self, module_name: str,
                 interval: float = 30.0,
                 collector: Optional[HeartbeatCollector] = None):
        """
        Args:
            module_name: 模块名
            interval: 上报间隔（秒）
            collector: 心跳收集器，默认使用全局单例
        """
        self.module_name = module_name
        self.interval = interval
        self._collector = collector or HeartbeatCollector.get_instance()
        self._status = ModuleStatus.STOPPED
        self._error_count = 0
        self._message = ""
        self._custom_metrics: Dict[str, Any] = {}
        self._timer: Optional[threading.Timer] = None
        # 部署加固: 定时器链运行标记——防止重复 start 产生第二条链，
        # 以及 stop 与已触发的 tick 竞争导致链复活。
        self._timer_chain_running = False

    def start(self, status: ModuleStatus = ModuleStatus.RUNNING, message: str = ""):
        """启动定时心跳上报"""
        self._status = status
        self._message = message
        self._do_beat()
        if not self._timer_chain_running:
            self._timer_chain_running = True
            self._schedule_next()

    def stop(self):
        """停止心跳上报"""
        self._timer_chain_running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        self._collector.register_stopped(self.module_name, "已停止")

    def update(self, status: Optional[ModuleStatus] = None,
               message: Optional[str] = None,
               error_increment: int = 0,
               custom_metrics: Optional[Dict[str, Any]] = None):
        """更新状态（下次 tick 时自动上报）"""
        if status is not None:
            self._status = status
        if message is not None:
            self._message = message
        self._error_count += error_increment
        if custom_metrics:
            self._custom_metrics.update(custom_metrics)

    def tick(self):
        """手动触发一次心跳上报"""
        self._do_beat()

    def _do_beat(self):
        """执行一次心跳上报"""
        import os

        # 尝试获取进程级内存信息
        memory_mb = 0.0
        try:
            import psutil
            process = psutil.Process(os.getpid())
            memory_mb = process.memory_info().rss / (1024 * 1024)
        except ImportError:
            pass

        self._collector.beat(
            module_name=self.module_name,
            status=self._status,
            memory_mb=memory_mb,
            error_count=self._error_count,
            message=self._message,
            custom_metrics=self._custom_metrics,
        )

    def _schedule_next(self):
        """调度下一次心跳"""
        self._timer = threading.Timer(self.interval, self._tick_and_reschedule)
        self._timer.daemon = True
        self._timer.start()

    def _tick_and_reschedule(self):
        """tick 并重新调度"""
        self._do_beat()
        if self._timer_chain_running:
            self._schedule_next()
