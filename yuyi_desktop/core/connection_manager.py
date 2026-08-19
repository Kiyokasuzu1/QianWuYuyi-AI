# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/connection_manager.py

Phase C.10.4.2 —— Yuyi Desktop Connection Health Manager

负责:
- server online 检测
- latency
- reconnect
- degraded 状态
- 输出 ConnectionState {
    online, latency_ms, last_success, retry_count, degraded
  }

约束:
- 不直接 import src.*
- 仅使用 ApiClient
- 任何异常返回 offline 状态
- 线程安全
- 提供同步与异步(预留)心跳入口
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from yuyi_desktop.core.api_client import (
    ApiClient,
    get_api_client,
)

logger = logging.getLogger(__name__)


# ============================================================
# ConnectionState(对外契约)
# ============================================================
@dataclass
class ConnectionState:
    """
    标准化连接状态(C.10.4.2 契约)。

    字段:
        online:         bool,Server 是否可达
        latency_ms:     float,最近一次 ping 延迟(秒);-1 表示未知
        last_success:   str,最近一次成功时间(ISO8601)
        retry_count:    int,自上次成功以来的失败次数
        degraded:       bool,降级(online=True 但 latency 高 / 偶发失败)
    """

    online: bool = False
    latency_ms: float = -1.0
    last_success: str = ""
    retry_count: int = 0
    degraded: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "online": bool(self.online),
            "latency_ms": float(self.latency_ms) if self.latency_ms >= 0 else -1.0,
            "last_success": str(self.last_success or ""),
            "retry_count": int(self.retry_count),
            "degraded": bool(self.degraded),
        }

    @classmethod
    def from_status(cls, status: Dict[str, Any]) -> "ConnectionState":
        """从 ConnectionStatus.snapshot() 构造。"""
        return cls(
            online=bool(status.get("connected", False)),
            latency_ms=float(status.get("latency_ms", -1.0) or -1.0),
            last_success=str(status.get("last_success", "") or ""),
            retry_count=int(status.get("consecutive_failures", status.get("retry_count", 0)) or 0),
            degraded=bool(status.get("degraded", False)),
        )


# ============================================================
# 内部状态(向后兼容 + 扩展)
# ============================================================
@dataclass
class ConnectionStatus:
    """连接状态快照(可变,内部使用)。"""

    connected: bool = False
    latency_ms: float = -1.0
    server_version: str = ""
    last_check: str = ""
    last_success: str = ""  # C.10.4.2 新增
    last_error: str = ""
    consecutive_failures: int = 0  # alias of retry_count
    total_checks: int = 0
    total_successes: int = 0
    degraded: bool = False  # C.10.4.2 新增

    def snapshot(self) -> Dict[str, Any]:
        return {
            "connected": bool(self.connected),
            "latency_ms": float(self.latency_ms) if self.latency_ms >= 0 else -1.0,
            "server_version": str(self.server_version or ""),
            "last_check": str(self.last_check or ""),
            "last_success": str(self.last_success or ""),
            "last_error": str(self.last_error or ""),
            "consecutive_failures": int(self.consecutive_failures),
            "retry_count": int(self.consecutive_failures),
            "total_checks": int(self.total_checks),
            "total_successes": int(self.total_successes),
            "degraded": bool(self.degraded),
        }


# ============================================================
# Connection Manager
# ============================================================
class ConnectionManager:
    """
    Yuyi Desktop 连接管理器(Phase C.10.4.2)。

    线程安全,所有方法可并发调用。
    """

    DEFAULT_HEARTBEAT_INTERVAL_SEC = 15.0
    DEFAULT_LATENCY_THRESHOLD_MS = 1500.0
    DEGRADED_FAILURE_THRESHOLD = 2  # 连续失败达到此次数进入 degraded

    def __init__(
        self,
        api_client: Optional[ApiClient] = None,
        heartbeat_interval_sec: float = DEFAULT_HEARTBEAT_INTERVAL_SEC,
        latency_threshold_ms: float = DEFAULT_LATENCY_THRESHOLD_MS,
        degraded_failure_threshold: int = DEGRADED_FAILURE_THRESHOLD,
    ) -> None:
        self._api_client = api_client if api_client is not None else get_api_client()
        self._heartbeat_interval_sec = float(heartbeat_interval_sec)
        self._latency_threshold_ms = float(latency_threshold_ms)
        self._degraded_failure_threshold = int(degraded_failure_threshold)
        self._lock = threading.RLock()
        self._status = ConnectionStatus()
        self._last_heartbeat_ts: float = 0.0
        # 主动 stop 信号(留给未来异步心跳线程)
        self._stop_event = threading.Event()

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def check_once(self) -> Dict[str, Any]:
        """
        执行一次连接检查(ping + server_info)。

        返回 envelope(dict)同时更新内部状态。
        """
        with self._lock:
            self._status.total_checks += 1

        ping_start = time.monotonic()
        ping_resp = self._api_client.ping()
        ping_latency_ms = (time.monotonic() - ping_start) * 1000.0

        with self._lock:
            if not ping_resp.get("success", False):
                # 失败
                self._status.connected = False
                self._status.latency_ms = -1.0
                self._status.last_error = str(ping_resp.get("error", "ping_failed"))
                self._status.last_check = datetime.now(timezone.utc).isoformat()
                self._status.consecutive_failures += 1
                # 更新 degraded
                self._status.degraded = (
                    self._status.consecutive_failures >= self._degraded_failure_threshold
                )
                logger.debug(
                    "ConnectionManager: ping 失败: %s (failures=%d)",
                    self._status.last_error,
                    self._status.consecutive_failures,
                )
                return self._status.snapshot()

            # 成功
            now_iso = datetime.now(timezone.utc).isoformat()
            self._status.connected = True
            self._status.latency_ms = float(ping_latency_ms)
            self._status.last_check = now_iso
            self._status.last_success = now_iso
            self._status.consecutive_failures = 0
            self._status.total_successes += 1
            self._last_heartbeat_ts = time.monotonic()
            # 高延迟也算 degraded
            self._status.degraded = (
                self._status.latency_ms > self._latency_threshold_ms
            )

            # 尝试获取 server info(不阻塞)
            try:
                info_resp = self._api_client.server_info()
                if info_resp.get("success", False):
                    info_data = info_resp.get("data", {}) or {}
                    if isinstance(info_data, dict):
                        version = (
                            info_data.get("version")
                            or info_data.get("server_version")
                            or info_data.get("schema_version")
                        )
                        if version:
                            self._status.server_version = str(version)
            except Exception as exc:  # noqa: BLE001
                logger.debug("ConnectionManager: server_info 失败: %s", exc)

            return self._status.snapshot()

    def heartbeat_if_needed(self) -> Dict[str, Any]:
        """
        如果距离上次心跳超过 heartbeat_interval_sec,执行一次心跳。
        否则返回当前状态(不发起新请求)。
        """
        with self._lock:
            now = time.monotonic()
            if (now - self._last_heartbeat_ts) < self._heartbeat_interval_sec:
                return self._status.snapshot()
        return self.check_once()

    def get_status(self) -> Dict[str, Any]:
        """返回当前状态(不发起新请求)。"""
        with self._lock:
            return self._status.snapshot()

    def get_state(self) -> ConnectionState:
        """返回 C.10.4.2 标准的 ConnectionState。"""
        with self._lock:
            return ConnectionState.from_status(self._status.snapshot())

    def get_state_dict(self) -> Dict[str, Any]:
        """返回 C.10.4.2 ConnectionState 的 dict 形式。"""
        return self.get_state().to_dict()

    def is_connected(self) -> bool:
        with self._lock:
            return bool(self._status.connected)

    def is_degraded(self) -> bool:
        with self._lock:
            return bool(self._status.degraded)

    def trigger_reconnect(self) -> Dict[str, Any]:
        """
        主动触发一次 reconnect(立即发起一次 check)。
        """
        with self._lock:
            self._last_heartbeat_ts = 0.0
        return self.check_once()

    def reset_status(self) -> None:
        """重置状态(供测试/手动重连)。"""
        with self._lock:
            self._status = ConnectionStatus()
            self._last_heartbeat_ts = 0.0

    def stop(self) -> None:
        """停止后台活动(预留)。"""
        self._stop_event.set()


# ============================================================
# 模块级单例
# ============================================================
_manager_instance: Optional[ConnectionManager] = None
_manager_lock = threading.Lock()


def get_connection_manager() -> ConnectionManager:
    """获取 ConnectionManager 单例(懒加载)。"""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = ConnectionManager()
    return _manager_instance


def reset_connection_manager_for_testing() -> ConnectionManager:
    """测试用:重置单例。"""
    global _manager_instance
    with _manager_lock:
        _manager_instance = None
    return get_connection_manager()


__all__ = [
    "ConnectionState",
    "ConnectionStatus",
    "ConnectionManager",
    "get_connection_manager",
    "reset_connection_manager_for_testing",
]
