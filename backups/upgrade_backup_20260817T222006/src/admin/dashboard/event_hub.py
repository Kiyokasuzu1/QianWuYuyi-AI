# -*- coding: utf-8 -*-
"""
src/admin/dashboard/event_hub.py

Phase 5.0 Dashboard Upgrade —— DashboardEventHub。

职责:
- 订阅 IntegrationEvent 流
- 维护 Dashboard 端事件缓存(最近 N 条)
- 为 WebSocket 推送提供事件源
- 严格只读,不修改任何 Authority 数据

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 仅通过 RuntimeIntegrationHost.event_log 访问事件
- 失败隔离:任何错误不得影响现有系统
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


# 默认事件缓存容量
DEFAULT_EVENT_CACHE_CAPACITY = 200
# 订阅者回调类型
SubscriberCallback = Callable[[Dict[str, Any]], None]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _summarize_event(event: Any) -> Dict[str, Any]:
    """
    将 IntegrationEvent 转换为 dict 摘要(供 WebSocket / API 使用)。

    严格只读,不修改原始 event。
    """
    return {
        "event_id": str(_safe_get(event, "event_id", "") or ""),
        "event_type": str(_safe_get(event, "event_type", "") or ""),
        "source": str(_safe_get(event, "source", "") or ""),
        "timestamp": _safe_get(event, "timestamp", None),
        "received_at": _now_iso(),
    }


class DashboardEventHub:
    """
    Dashboard 事件中枢。

    设计:
    - 内部维护固定容量的事件缓存(默认 200)
    - 提供 subscribe / unsubscribe 供 WebSocket 推送
    - poll_recent(limit) 供 HTTP API 查询
    - 容错:任何 RuntimeIntegrationHost 不可用时返回空,绝不抛错
    """

    def __init__(self, *, capacity: int = DEFAULT_EVENT_CACHE_CAPACITY) -> None:
        self._lock = threading.RLock()
        self._capacity = max(0, int(capacity))
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=self._capacity)
        self._subscribers: List[SubscriberCallback] = []
        self._last_poll_ts: float = 0.0
        self._host_unavailable_logged: bool = False

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def poll_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        拉取最近事件(供 HTTP API)。

        同时尝试从 RuntimeIntegrationHost 拉取新事件并写入缓存。
        """
        self._refresh_from_host()
        n = max(0, int(limit))
        if n == 0:
            return []
        with self._lock:
            data = list(self._buffer)
        return data[-n:]

    def subscribe(self, callback: SubscriberCallback) -> bool:
        """订阅事件回调(WebSocket 推送用)。返回是否成功。"""
        if not callable(callback):
            return False
        with self._lock:
            if callback in self._subscribers:
                return True
            self._subscribers.append(callback)
        return True

    def unsubscribe(self, callback: SubscriberCallback) -> bool:
        """取消订阅。"""
        if not callable(callback):
            return False
        with self._lock:
            try:
                self._subscribers.remove(callback)
                return True
            except ValueError:
                return False

    def clear_cache(self) -> int:
        """清空缓存(测试用)。"""
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
            return n

    def describe(self) -> Dict[str, Any]:
        """描述(调试/健康检查用)。"""
        with self._lock:
            return {
                "capacity": self._capacity,
                "cached": len(self._buffer),
                "subscribers": len(self._subscribers),
                "last_poll_ts": self._last_poll_ts,
            }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _refresh_from_host(self) -> None:
        """从 RuntimeIntegrationHost.event_log 拉取新事件,写入缓存并通知订阅者。"""
        host = self._get_host()
        if host is None:
            return
        try:
            event_log = _safe_get(host, "event_log")
            if event_log is None:
                return
            try:
                latest_n = max(0, self._capacity)
            except Exception:
                latest_n = DEFAULT_EVENT_CACHE_CAPACITY
            raw_events = event_log.latest(limit=latest_n) if latest_n > 0 else []
        except Exception as exc:  # noqa: BLE001
            logger.debug("DashboardEventHub: 拉取事件失败: %s", exc)
            return

        new_events: List[Dict[str, Any]] = []
        with self._lock:
            existing_ids = {
                str(item.get("event_id", "")) for item in self._buffer
            }
            for ev in raw_events:
                summary = _summarize_event(ev)
                eid = summary.get("event_id", "")
                if not eid or eid in existing_ids:
                    continue
                self._buffer.append(summary)
                existing_ids.add(eid)
                new_events.append(summary)
            self._last_poll_ts = time.time()

        # 通知订阅者(在锁外执行,避免回调阻塞)
        if new_events:
            self._notify_subscribers(new_events)

    def _notify_subscribers(self, events: List[Dict[str, Any]]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(events)
            except Exception as exc:  # noqa: BLE001
                logger.debug("DashboardEventHub 订阅者回调失败: %s", exc)

    def _get_host(self) -> Optional[Any]:
        """获取 RuntimeIntegrationHost(可注入,用于测试)。"""
        try:
            from src.runtime.integration.runtime_integration_host import (
                RuntimeIntegrationHost,
            )
        except Exception:
            return None
        try:
            # 先尝试 get_default
            if hasattr(RuntimeIntegrationHost, "get_default"):
                host = RuntimeIntegrationHost.get_default()
                if host is not None:
                    return host
            # fallback: 通过单例 helper
            try:
                from src.runtime.integration.runtime_integration_host import (
                    get_default_runtime_integration_host,
                )
                return get_default_runtime_integration_host()
            except Exception:
                pass
            return None
        except Exception as exc:  # noqa: BLE001
            if not self._host_unavailable_logged:
                logger.debug("DashboardEventHub: RuntimeIntegrationHost 不可用: %s", exc)
                self._host_unavailable_logged = True
            return None


# ============================================================
# 模块级单例
# ============================================================
_hub_instance: Optional[DashboardEventHub] = None
_hub_lock = threading.Lock()


def get_dashboard_event_hub() -> DashboardEventHub:
    """获取 DashboardEventHub 单例(懒加载)。"""
    global _hub_instance
    if _hub_instance is None:
        with _hub_lock:
            if _hub_instance is None:
                _hub_instance = DashboardEventHub()
    return _hub_instance


def reset_dashboard_event_hub_for_testing() -> None:
    """测试用:重置单例。"""
    global _hub_instance
    with _hub_lock:
        _hub_instance = None


__all__ = [
    "DashboardEventHub",
    "get_dashboard_event_hub",
    "reset_dashboard_event_hub_for_testing",
    "DEFAULT_EVENT_CACHE_CAPACITY",
]
