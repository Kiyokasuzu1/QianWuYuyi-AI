# -*- coding: utf-8 -*-
"""
src/runtime/status_tracker.py

Phase 7.0 Dashboard Runtime Center —— RuntimeStatusTracker。

职责:
    追踪 api_server 进程内 Runtime 的生命周期状态,供 Dashboard 实时展示。

追踪指标:
    - 启动时间 + uptime
    - 当前任务(current_task): idle / processing / memory_reflection / ...
    - 最近一次事件(last_event): 用户消息 / 主动消息 / 系统事件
    - 最近一次回复时间(last_response_at)
    - 请求计数(总数 / 成功 / 失败)

设计原则:
    1. 仅在 api_server 进程内使用,不跨进程(Dashboard API 直接读内存)。
    2. 异常隔离: 任何更新失败都吞掉,绝不影响主链路。
    3. 线程安全: 使用 RLock 保护内部状态。
    4. 无依赖: 仅使用 Python 标准库。
    5. 向后兼容: 所有方法都是新增,不修改现有 Runtime 行为。

跨进程说明:
    - api_server 状态: 本 tracker 直接读内存(同进程)。
    - Agent Server 状态: 读 data/agent_server_status.json(由 api_server 进程内 AgentStatusWriter 写)。
    - initiative_bridge 状态: 通过 agent_server_status.json 的新鲜度推导
      (api_server 每 5 秒写一次该文件,文件新鲜即代表进程存活,InitiativeBridge 在同进程内)。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

DEFAULT_AGENT_SERVER_STATUS_FILE = Path("data/agent_server_status.json")
DEFAULT_STALENESS_THRESHOLD_SECONDS = 30  # 超过 30 秒未更新视为过期


# ============================================================
# RuntimeStatusTracker
# ============================================================
class RuntimeStatusTracker:
    """追踪 api_server 进程内 Runtime 的生命周期状态。

    线程安全: 使用 RLock 保护内部状态。
    异常隔离: 所有公开方法均吞掉异常,只记录日志,绝不抛出。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at: float = time.time()
        self._current_task: str = "idle"
        self._current_task_since: float = self._started_at
        self._last_event: Optional[str] = None
        self._last_event_at: Optional[float] = None
        self._last_response_at: Optional[float] = None
        self._last_response_preview: str = ""
        self._request_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._last_error: Optional[str] = None
        self._last_trace_id: Optional[str] = None

    # --------------------------------------------------------
    # 公开 API: 状态更新(由 RuntimePipeline / api_server 调用)
    # --------------------------------------------------------
    def mark_started(self) -> None:
        """标记 api_server 启动(通常在 __init__ 后调用一次)。"""
        try:
            with self._lock:
                self._started_at = time.time()
                self._current_task = "idle"
                self._current_task_since = self._started_at
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] mark_started 失败(已吞掉): %s", exc)

    def update_task(self, task_name: str) -> None:
        """更新当前任务(如 "processing", "memory_reflection", "idle")。"""
        try:
            with self._lock:
                self._current_task = str(task_name or "idle")
                self._current_task_since = time.time()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] update_task 失败(已吞掉): %s", exc)

    def record_event(
        self,
        event_name: str,
        *,
        preview: str = "",
    ) -> None:
        """记录最近一次事件(如 "user_message", "initiative", "system")。"""
        try:
            with self._lock:
                self._last_event = str(event_name or "")
                self._last_event_at = time.time()
                if preview:
                    # 截断预览,防止巨型消息污染状态
                    self._last_event_preview = str(preview)[:200]
                else:
                    self._last_event_preview = ""
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] record_event 失败(已吞掉): %s", exc)

    def record_request_start(
        self,
        *,
        trace_id: Optional[str] = None,
        user_message: str = "",
    ) -> None:
        """记录一次请求开始(由 Pipeline 在 run() 入口调用)。"""
        try:
            with self._lock:
                self._request_count += 1
                self._current_task = "processing"
                self._current_task_since = time.time()
                self._last_event = "user_message"
                self._last_event_at = self._current_task_since
                self._last_event_preview = str(user_message)[:200]
                if trace_id:
                    self._last_trace_id = str(trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] record_request_start 失败(已吞掉): %s", exc)

    def record_response(
        self,
        *,
        reply: str = "",
        success: bool = True,
        error: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        """记录一次回复完成(由 Pipeline 在 run() 结束调用)。"""
        try:
            with self._lock:
                self._last_response_at = time.time()
                self._last_response_preview = str(reply)[:200]
                self._current_task = "idle"
                self._current_task_since = self._last_response_at
                if success:
                    self._success_count += 1
                    self._last_error = None
                else:
                    self._failure_count += 1
                    self._last_error = error
                if trace_id:
                    self._last_trace_id = str(trace_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] record_response 失败(已吞掉): %s", exc)

    # --------------------------------------------------------
    # 公开 API: 状态读取(由 Dashboard API 调用)
    # --------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """返回当前完整状态快照。"""
        try:
            with self._lock:
                now = time.time()
                uptime = max(0.0, float(now - self._started_at))
                return {
                    "status": "running",
                    "started_at": self._iso(self._started_at),
                    "uptime_seconds": round(uptime, 2),
                    "uptime_human": self._format_uptime(uptime),
                    "current_task": self._current_task,
                    "current_task_since": self._iso(self._current_task_since),
                    "current_task_duration_seconds": round(
                        max(0.0, now - self._current_task_since), 2,
                    ),
                    "last_event": self._last_event,
                    "last_event_at": self._iso(self._last_event_at) if self._last_event_at else None,
                    "last_event_preview": getattr(self, "_last_event_preview", ""),
                    "last_response_at": self._iso(self._last_response_at) if self._last_response_at else None,
                    "last_response_age_seconds": (
                        round(max(0.0, now - self._last_response_at), 2)
                        if self._last_response_at else None
                    ),
                    "last_response_preview": self._last_response_preview,
                    "last_trace_id": self._last_trace_id,
                    "last_error": self._last_error,
                    "request_count": self._request_count,
                    "success_count": self._success_count,
                    "failure_count": self._failure_count,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("[RuntimeStatusTracker] get_status 失败: %s", exc)
            return {
                "status": "unknown",
                "error": str(exc),
                "uptime_seconds": 0,
                "current_task": "unknown",
            }

    def get_uptime_seconds(self) -> float:
        with self._lock:
            return max(0.0, float(time.time() - self._started_at))

    def get_current_task(self) -> str:
        with self._lock:
            return self._current_task

    # --------------------------------------------------------
    # 内部: 工具
    # --------------------------------------------------------
    @staticmethod
    def _iso(ts: Optional[float]) -> str:
        if ts is None:
            return ""
        try:
            return datetime.fromtimestamp(float(ts)).isoformat(timespec="milliseconds")
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """格式化 uptime 为人类可读字符串(如 "12h 32m 5s")。"""
        try:
            s = int(max(0, seconds))
            days, s = divmod(s, 86400)
            hours, s = divmod(s, 3600)
            minutes, s = divmod(s, 60)
            parts = []
            if days > 0:
                parts.append(f"{days}d")
            if hours > 0 or days > 0:
                parts.append(f"{hours}h")
            if minutes > 0 or hours > 0 or days > 0:
                parts.append(f"{minutes}m")
            parts.append(f"{s}s")
            return " ".join(parts)
        except Exception:  # noqa: BLE001
            return f"{seconds:.1f}s"


# ============================================================
# Agent Server 状态读取(跨进程,文件共享)
# ============================================================
class AgentServerStatusReader:
    """读取 api_server 进程写入的 Agent Server 状态文件。

    设计要点:
    - 只读,绝不修改文件。
    - 30 秒过期检查:文件超过 30 秒未更新视为 stale(进程可能卡住/崩溃)。
    - 任何异常返回 fallback,绝不抛出。
    """

    def __init__(
        self,
        status_file: Path = DEFAULT_AGENT_SERVER_STATUS_FILE,
        *,
        staleness_threshold: int = DEFAULT_STALENESS_THRESHOLD_SECONDS,
    ) -> None:
        self._status_file = Path(status_file)
        self._staleness = max(5, int(staleness_threshold))

    def read(self) -> Dict[str, Any]:
        """读取 Agent Server 状态文件,返回结构化 dict。

        Returns:
            {
                "available": bool,        # 文件是否存在且可读
                "running": bool,          # Agent Server 是否在运行
                "host": str,
                "port": int,
                "pid": int|None,
                "total_agents": int,
                "authenticated_agents": int,
                "agents": list,
                "updated_at": str,        # ISO 时间戳
                "age_seconds": float|None,# 文件年龄
                "stale": bool,            # 是否过期
                # Phase 7.2.1-p1 起 initiative_sender 独立进程已删除，
                # 该状态文件由 api_server 直接写盘，代表 Runtime InitiativeBridge 单一路径
                # 存活；保留 initiative_sender_alive 旧字段名用于向后兼容。
                "initiative_sender_alive": bool,  # [DEPRECATED] 等价于 initiative_bridge_alive
                "initiative_bridge_alive": bool,  # InitiativeBridge（单一路径）是否存活
                "fallback": bool,
                "fallback_reason": str|None,
            }
        """
        try:
            if not self._status_file.exists():
                return self._fallback("file_not_found")
            size = self._status_file.stat().st_size
            if size <= 0 or size > 1024 * 1024:  # 1MB 保护
                return self._fallback("file_size_invalid")
            with open(self._status_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return self._fallback("file_format_invalid")

            updated_at_str = raw.get("updated_at", "")
            age_seconds = self._compute_age_seconds(updated_at_str)
            stale = age_seconds is not None and age_seconds > self._staleness
            running = bool(raw.get("running", False))
            # Phase 7.2.1-p1：文件由 api_server 进程内的 AgentStatusWriter 写，
            # 文件新鲜 + enabled=True 说明 InitiativeBridge 所在进程在存活。
            bridge_alive = (not stale) and bool(raw.get("enabled", False))
            # 保留旧字段 initiative_sender_alive，值与 initiative_bridge_alive 保持一致
            # （用于向后兼容旧版 Dashboard / Admin 聚合逻辑 / 既有测试）
            initiative_alive = bridge_alive

            return {
                "available": True,
                "running": running and not stale,
                "host": str(raw.get("host", "")),
                "port": int(raw.get("port", 0) or 0),
                "pid": raw.get("pid"),
                "total_agents": int(raw.get("total_agents", 0) or 0),
                "authenticated_agents": int(raw.get("authenticated_agents", 0) or 0),
                "agents": raw.get("agents", []) if isinstance(raw.get("agents"), list) else [],
                "updated_at": str(updated_at_str),
                "age_seconds": age_seconds,
                "stale": stale,
                "initiative_sender_alive": initiative_alive,
                "initiative_bridge_alive": bridge_alive,
                "fallback": False,
                "fallback_reason": None,
            }
        except json.JSONDecodeError as exc:
            return self._fallback(f"json_decode_error:{type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[AgentServerStatusReader] read 失败: %s", exc)
            return self._fallback(f"read_error:{type(exc).__name__}")

    def _fallback(self, reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "running": False,
            "host": "",
            "port": 0,
            "pid": None,
            "total_agents": 0,
            "authenticated_agents": 0,
            "agents": [],
            "updated_at": "",
            "age_seconds": None,
            "stale": True,
            "initiative_sender_alive": False,  # [DEPRECATED] 保留字段，值同上
            "initiative_bridge_alive": False,
            "fallback": True,
            "fallback_reason": reason,
        }

    @staticmethod
    def _compute_age_seconds(updated_at_str: str) -> Optional[float]:
        """计算文件 updated_at 距现在多少秒。"""
        if not updated_at_str:
            return None
        try:
            # 兼容 ISO 格式 with/without timezone
            ts = updated_at_str.strip()
            # 尝试 fromisoformat(Python 3.11+ 支持 'Z' 后缀)
            if ts.endswith("Z"):
                ts = ts[:-1] + "+00:00"
            dt = datetime.fromisoformat(ts)
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            return max(0.0, (now - dt).total_seconds())
        except Exception:  # noqa: BLE001
            return None


# ============================================================
# Services 状态聚合(组合 api_server + runtime + initiative + agent)
# ============================================================
def get_services_status(
    status_tracker: Optional[RuntimeStatusTracker] = None,
    runtime_provider: Optional[Any] = None,
    agent_status_reader: Optional[AgentServerStatusReader] = None,
) -> Dict[str, Any]:
    """聚合 4 个服务的状态,供 /runtime/services 端点返回。

    Args:
        status_tracker: api_server 进程内状态(同进程内存)
        runtime_provider: RuntimeProvider(可选,通过 get_status() 读 runtime 状态)
        agent_status_reader: Agent Server 状态文件读取器

    Returns:
        {
            "api_server": {...},
            "runtime": {...},
            "initiative_sender": {...},  # [DEPRECATED key 名保留] 内容语义=initiative_bridge 状态
            "agent_server": {...},
            "available": bool,
            "fallback": bool,
            "fallback_reason": str|None,
        }
    """
    # 1) api_server(本进程,直接读 tracker)
    api_server_status: Dict[str, Any]
    if status_tracker is not None:
        try:
            api_server_status = {
                "status": "running",
                "uptime_seconds": status_tracker.get_uptime_seconds(),
                "current_task": status_tracker.get_current_task(),
                "available": True,
                "fallback": False,
            }
        except Exception:  # noqa: BLE001
            api_server_status = {
                "status": "unknown", "available": False, "fallback": True,
                "fallback_reason": "tracker_error",
            }
    else:
        api_server_status = {
            "status": "running",  # 能响应说明 api_server 在跑
            "uptime_seconds": None,
            "current_task": "unknown",
            "available": True,
            "fallback": True,
            "fallback_reason": "tracker_not_injected",
        }

    # 2) runtime(同进程,通过 RuntimeProvider)
    runtime_status: Dict[str, Any]
    if runtime_provider is not None:
        try:
            raw = runtime_provider.get_status() or {}
            runtime_obj = raw.get("runtime", {}) if isinstance(raw, dict) else {}
            runtime_status = {
                "status": "running" if runtime_obj.get("is_running") else "stopped",
                "initialized": bool(runtime_obj.get("initialized", False)),
                "online": bool(raw.get("online", False)) if isinstance(raw, dict) else False,
                "bridge_error": raw.get("bridge_error") if isinstance(raw, dict) else None,
                "available": True,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001
            runtime_status = {
                "status": "unknown", "available": False, "fallback": True,
                "fallback_reason": f"runtime_error:{type(exc).__name__}",
            }
    else:
        runtime_status = {
            "status": "unknown", "available": False, "fallback": True,
            "fallback_reason": "runtime_provider_not_injected",
        }

    # 3) initiative_sender / initiative_bridge + agent_server(跨进程,读文件)
    # Phase 7.2.1-p1 起 initiative_sender 独立进程已移除，这里用 initiative_bridge_alive
    # 作为新语义，但保留旧字段 initiative_sender_alive 的派生逻辑用于兼容。
    if agent_status_reader is None:
        agent_status_reader = AgentServerStatusReader()
    try:
        agent_raw = agent_status_reader.read()
    except Exception as exc:  # noqa: BLE001
        agent_raw = {
            "available": False, "running": False, "stale": True,
            "initiative_sender_alive": False,
            "initiative_bridge_alive": False,
            "fallback": True, "fallback_reason": f"reader_error:{type(exc).__name__}",
        }

    # 新字段优先，否则回退旧字段兼容
    bridge_alive = bool(agent_raw.get("initiative_bridge_alive")) or bool(
        agent_raw.get("initiative_sender_alive"),
    )

    initiative_status = {
        # status 字段语义也更新为 InitiativeBridge 是否存活
        # （保留对旧字段的兼容查询，因此 Dashboard 端无需改动）
        "status": "running" if bridge_alive else "stopped",
        "available": agent_raw.get("available", False),
        "last_heartbeat_age_seconds": agent_raw.get("age_seconds"),
        "stale": agent_raw.get("stale", True),
        "fallback": agent_raw.get("fallback", False),
        "fallback_reason": agent_raw.get("fallback_reason"),
        # Phase 7.2.1-p1：区分新旧
        "mode": "bridge",  # 新：Runtime InitiativeBridge 单一路径；旧版 sender 模式下为 "sender"
        "initiative_bridge_alive": bridge_alive,
    }

    agent_server_status = {
        "status": "running" if agent_raw.get("running") else "stopped",
        "host": agent_raw.get("host", ""),
        "port": agent_raw.get("port", 0),
        "pid": agent_raw.get("pid"),
        "total_agents": agent_raw.get("total_agents", 0),
        "authenticated_agents": agent_raw.get("authenticated_agents", 0),
        "agents": agent_raw.get("agents", []),
        "updated_at": agent_raw.get("updated_at", ""),
        "age_seconds": agent_raw.get("age_seconds"),
        "stale": agent_raw.get("stale", True),
        "available": agent_raw.get("available", False),
        "fallback": agent_raw.get("fallback", False),
        "fallback_reason": agent_raw.get("fallback_reason"),
    }

    return {
        "api_server": api_server_status,
        "runtime": runtime_status,
        "initiative_sender": initiative_status,
        "agent_server": agent_server_status,
        "available": True,
        "fallback": False,
        "fallback_reason": None,
    }


# ============================================================
# 模块级单例
# ============================================================
_tracker_singleton: Optional[RuntimeStatusTracker] = None
_tracker_lock = threading.Lock()


def get_runtime_status_tracker() -> RuntimeStatusTracker:
    """获取全局 RuntimeStatusTracker 单例(懒加载)。"""
    global _tracker_singleton
    if _tracker_singleton is None:
        with _tracker_lock:
            if _tracker_singleton is None:
                _tracker_singleton = RuntimeStatusTracker()
    return _tracker_singleton


def set_runtime_status_tracker(tracker: Optional[RuntimeStatusTracker]) -> None:
    """注入全局 RuntimeStatusTracker(供 api_server.py 启动时使用)。"""
    global _tracker_singleton
    with _tracker_lock:
        _tracker_singleton = tracker


def reset_runtime_status_tracker_for_testing() -> None:
    """测试用: 重置单例。"""
    global _tracker_singleton
    with _tracker_lock:
        _tracker_singleton = None


__all__ = [
    "RuntimeStatusTracker",
    "AgentServerStatusReader",
    "get_services_status",
    "get_runtime_status_tracker",
    "set_runtime_status_tracker",
    "reset_runtime_status_tracker_for_testing",
    "DEFAULT_AGENT_SERVER_STATUS_FILE",
    "DEFAULT_STALENESS_THRESHOLD_SECONDS",
]
