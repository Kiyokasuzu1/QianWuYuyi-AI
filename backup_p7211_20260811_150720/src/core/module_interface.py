"""
模块生命周期接口

所有可被管理面板控制的模块都继承 ModuleBase，
提供统一的 start/stop/reload/health 接口。

管理面板通过这套接口真正控制模块，
而不是只改 config.yaml。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.heartbeat import HeartbeatReporter, ModuleStatus

logger = logging.getLogger(__name__)


class ModuleLifeCycleState(Enum):
    """模块生命周期状态"""
    UNINITIALIZED = "uninitialized"   # 未初始化
    INITIALIZING = "initializing"     # 初始化中
    RUNNING = "running"               # 正常运行
    STOPPING = "stopping"             # 停止中
    STOPPED = "stopped"               # 已停止
    ERROR = "error"                   # 出错
    DEGRADED = "degraded"             # 降级运行


@dataclass
class ModuleHealth:
    """模块健康状态"""
    status: ModuleLifeCycleState = ModuleLifeCycleState.STOPPED
    error_count: int = 0
    last_error: str = ""
    last_heartbeat: float = 0.0
    custom_metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_heartbeat": self.last_heartbeat,
            "custom_metrics": self.custom_metrics,
        }


class ModuleBase(ABC):
    """
    模块基类 — 所有可管理模块的统一接口

    子类实现：
        - _start(): 启动逻辑
        - _stop(): 停止逻辑
        - _reload(): 重载逻辑（可选）
        - _health_check(): 健康检查（可选）
    """

    def __init__(self, module_name: str, config: Optional[Dict] = None):
        """
        Args:
            module_name: 模块名（应与 MODULE_INFO.name 一致）
            config: 模块配置
        """
        self.module_name = module_name
        self.config = config or {}
        self._state = ModuleLifeCycleState.UNINITIALIZED
        self._health = ModuleHealth()
        self._dependencies: List[str] = []
        self._heartbeat_reporter = HeartbeatReporter(module_name)

    @property
    def state(self) -> ModuleLifeCycleState:
        """当前生命周期状态"""
        return self._state

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._state == ModuleLifeCycleState.RUNNING

    def start(self) -> bool:
        """
        启动模块

        Returns:
            True 表示启动成功
        """
        if self._state == ModuleLifeCycleState.RUNNING:
            logger.debug(f"模块 {self.module_name} 已在运行，跳过 start")
            return True

        logger.info(f"启动模块: {self.module_name}")
        self._state = ModuleLifeCycleState.INITIALIZING

        try:
            success = self._start()
            if success:
                self._state = ModuleLifeCycleState.RUNNING
                self._health.status = ModuleLifeCycleState.RUNNING
                self._heartbeat_reporter.start(ModuleStatus.RUNNING)
                logger.info(f"模块 {self.module_name} 启动成功")
                return True
            else:
                self._state = ModuleLifeCycleState.ERROR
                self._health.status = ModuleLifeCycleState.ERROR
                logger.error(f"模块 {self.module_name} 启动失败")
                return False
        except Exception as e:
            self._state = ModuleLifeCycleState.ERROR
            self._health.status = ModuleLifeCycleState.ERROR
            self._health.error_count += 1
            self._health.last_error = str(e)
            logger.error(f"模块 {self.module_name} 启动异常: {e}")
            return False

    def stop(self) -> bool:
        """
        停止模块

        Returns:
            True 表示停止成功
        """
        if self._state in (ModuleLifeCycleState.STOPPED, ModuleLifeCycleState.UNINITIALIZED):
            logger.debug(f"模块 {self.module_name} 已停止，跳过 stop")
            return True

        logger.info(f"停止模块: {self.module_name}")
        self._state = ModuleLifeCycleState.STOPPING

        try:
            success = self._stop()
            if success:
                self._state = ModuleLifeCycleState.STOPPED
                self._health.status = ModuleLifeCycleState.STOPPED
                self._heartbeat_reporter.stop()
                logger.info(f"模块 {self.module_name} 已停止")
                return True
            else:
                self._state = ModuleLifeCycleState.ERROR
                self._health.status = ModuleLifeCycleState.ERROR
                logger.error(f"模块 {self.module_name} 停止失败")
                return False
        except Exception as e:
            self._state = ModuleLifeCycleState.ERROR
            self._health.status = ModuleLifeCycleState.ERROR
            self._health.error_count += 1
            self._health.last_error = str(e)
            logger.error(f"模块 {self.module_name} 停止异常: {e}")
            return False

    def reload(self, new_config: Optional[Dict] = None) -> bool:
        """
        重载模块（热重载）

        Args:
            new_config: 新配置（可选，不传则使用当前配置）

        Returns:
            True 表示重载成功
        """
        if new_config is not None:
            self.config = new_config

        logger.info(f"重载模块: {self.module_name}")

        try:
            success = self._reload()
            if success:
                self._state = ModuleLifeCycleState.RUNNING
                self._health.status = ModuleLifeCycleState.RUNNING
                self._heartbeat_reporter.update(status=ModuleStatus.RUNNING)
                self._heartbeat_reporter.tick()
                logger.info(f"模块 {self.module_name} 重载成功")
                return True
            else:
                self._state = ModuleLifeCycleState.ERROR
                self._health.status = ModuleLifeCycleState.ERROR
                logger.error(f"模块 {self.module_name} 重载失败")
                return False
        except Exception as e:
            self._state = ModuleLifeCycleState.ERROR
            self._health.status = ModuleLifeCycleState.ERROR
            self._health.error_count += 1
            self._health.last_error = str(e)
            logger.error(f"模块 {self.module_name} 重载异常: {e}")
            return False

    def health_check(self) -> ModuleHealth:
        """
        健康检查

        Returns:
            ModuleHealth 对象
        """
        try:
            custom = self._health_check()
            if custom:
                self._health.custom_metrics = custom
        except Exception as e:
            self._health.error_count += 1
            self._health.last_error = str(e)

        import time
        self._health.last_heartbeat = time.time()
        return self._health

    def set_state(self, state: ModuleLifeCycleState):
        """手动设置状态（供子类内部使用）"""
        self._state = state
        self._health.status = state

    # ==================== 子类实现 ====================

    def _start(self) -> bool:
        """
        启动逻辑（子类重写）

        返回 True 表示成功
        """
        return True

    def _stop(self) -> bool:
        """
        停止逻辑（子类重写）

        返回 True 表示成功
        """
        return True

    def _reload(self) -> bool:
        """
        重载逻辑（子类重写，可选）

        默认实现：stop + start
        """
        if self._stop():
            return self._start()
        return False

    def _health_check(self) -> Optional[Dict[str, Any]]:
        """
        自定义健康检查（子类重写，可选）

        返回自定义指标字典，或 None
        """
        return None
