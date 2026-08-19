# -*- coding: utf-8 -*-
"""
yuyi_desktop/services/control_service.py

Phase C.10.5.6 —— Yuyi Desktop Control Service

通过 ControlApiClient 与 Yuyi Server Control Plane 通信。
提供:
- 获取控制平面总览
- 获取模块状态列表
- 启用/禁用/切换模块
- 进入/退出安全模式 / 维护模式
- 查询 audit

约束(强):
- 不允许 import 任何 src.* 业务模块
- 失败返回 fallback dict(供 UI 安全消费)
- 不抛错
- 走 RemoteProviderBridge 同样的 envelope 模式
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from yuyi_desktop.core.control_api_client import (
    ControlApiClient,
    get_control_api_client,
    reset_control_api_client_for_testing,
)
from yuyi_desktop.core.events.event_bus import (
    EventTypes,
    get_event_bus,
)

logger = logging.getLogger(__name__)


# ============================================================
# 标准事件类型扩展
# ============================================================
# 注:复用 EventTypes 已有的连接/数据事件,Control 平面自身变更
# 使用 CONTROL_UPDATED;并把"控制可用性"接入 CONNECTION_CHANGED。
CONTROL_UPDATED = "ControlUpdated"


# ============================================================
# Service
# ============================================================
class ControlService:
    """
    桌面端 Control 平面服务。

    行为:
    - 调用 ControlApiClient(GET/POST)
    - 拉取/控制 后发出 ControlUpdated 事件
    - 失败返回 envelope(success=False, degraded=True)
    """

    def __init__(
        self,
        api_client: Optional[ControlApiClient] = None,
        event_bus: Optional[Any] = None,
        enable_events: bool = True,
        default_operator: str = "desktop",
    ) -> None:
        self._lock = threading.RLock()
        self._api = api_client if api_client is not None else get_control_api_client()
        self._event_bus = event_bus if event_bus is not None else get_event_bus()
        self._enable_events = bool(enable_events)
        self._default_operator = str(default_operator or "desktop")

    # --------------------------------------------------------
    # 状态总览
    # --------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        """
        获取 Control 平面总览(状态 + 模块 + 系统模式)。
        """
        with self._lock:
            envelope = self._api.get_status()
            if envelope.get("success", False):
                self._publish(CONTROL_UPDATED, {"domain": "status"})
            return envelope

    def get_overview(self) -> Dict[str, Any]:
        """
        拉取并解析 Control 平面总览,返回扁平 dict(便于 UI 展示)。
        """
        with self._lock:
            envelope = self.get_status()
        if not isinstance(envelope, dict):
            return {
                "available": False,
                "error": "envelope_not_dict",
                "modules": [],
                "state": {},
                "audit_count": 0,
                "recent_audit": [],
            }
        if not envelope.get("success", False):
            return {
                "available": False,
                "error": str(envelope.get("error", "unknown")),
                "modules": [],
                "state": {},
                "audit_count": 0,
                "recent_audit": [],
            }
        data = envelope.get("data", {}) or {}
        if not isinstance(data, dict):
            data = {}
        return {
            "available": True,
            "error": "",
            "modules": data.get("modules", []) or [],
            "state": data.get("state", {}) or {},
            "audit_count": int(data.get("audit_count", 0) or 0),
            "recent_audit": data.get("recent_audit", []) or [],
            "server_status": data.get("server_status", "unknown"),
            "version": data.get("version", ""),
            "uptime_seconds": float(data.get("uptime_seconds", 0.0) or 0.0),
        }

    # --------------------------------------------------------
    # 模块列表
    # --------------------------------------------------------
    def list_modules(self) -> List[Dict[str, Any]]:
        """获取所有模块的元信息 + 启用状态。"""
        with self._lock:
            envelope = self._api.get_modules()
        if not isinstance(envelope, dict) or not envelope.get("success", False):
            return []
        data = envelope.get("data", {}) or {}
        if not isinstance(data, dict):
            return []
        mods = data.get("modules", []) or []
        return [m for m in mods if isinstance(m, dict)]

    # --------------------------------------------------------
    # 模块操作
    # --------------------------------------------------------
    def enable_module(
        self,
        name: str,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_module_op(
            "enable",
            name,
            reason=reason,
            operator=operator,
        )

    def disable_module(
        self,
        name: str,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_module_op(
            "disable",
            name,
            reason=reason,
            operator=operator,
        )

    def toggle_module(
        self,
        name: str,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_module_op(
            "toggle",
            name,
            reason=reason,
            operator=operator,
        )

    def _do_module_op(
        self,
        action: str,
        name: str,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        op = operator if operator else self._default_operator
        with self._lock:
            if action == "enable":
                envelope = self._api.enable_module(name, operator=op, reason=reason)
            elif action == "disable":
                envelope = self._api.disable_module(name, operator=op, reason=reason)
            elif action == "toggle":
                envelope = self._api.toggle_module(name, operator=op, reason=reason)
            else:
                return self._fail(f"unsupported_action: {action}")
        if isinstance(envelope, dict) and envelope.get("success", False):
            self._publish(
                CONTROL_UPDATED,
                {
                    "domain": "module",
                    "module": name,
                    "action": action,
                    "operator": op,
                    "reason": reason,
                },
            )
        return envelope if isinstance(envelope, dict) else self._fail("envelope_not_dict")

    # --------------------------------------------------------
    # 系统模式
    # --------------------------------------------------------
    def enter_safe_mode(
        self,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_system_op("enter_safe_mode", reason=reason, operator=operator)

    def exit_safe_mode(
        self,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_system_op("exit_safe_mode", reason=reason, operator=operator)

    def enter_maintenance(
        self,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_system_op("enter_maintenance", reason=reason, operator=operator)

    def exit_maintenance(
        self,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._do_system_op("exit_maintenance", reason=reason, operator=operator)

    def _do_system_op(
        self,
        action: str,
        reason: str = "",
        operator: Optional[str] = None,
    ) -> Dict[str, Any]:
        op = operator if operator else self._default_operator
        with self._lock:
            if action == "enter_safe_mode":
                envelope = self._api.enter_safe_mode(operator=op, reason=reason)
            elif action == "exit_safe_mode":
                envelope = self._api.exit_safe_mode(operator=op, reason=reason)
            elif action == "enter_maintenance":
                envelope = self._api.enter_maintenance(operator=op, reason=reason)
            elif action == "exit_maintenance":
                envelope = self._api.exit_maintenance(operator=op, reason=reason)
            else:
                return self._fail(f"unsupported_action: {action}")
        if isinstance(envelope, dict) and envelope.get("success", False):
            self._publish(
                CONTROL_UPDATED,
                {
                    "domain": "system",
                    "action": action,
                    "operator": op,
                    "reason": reason,
                },
            )
        return envelope if isinstance(envelope, dict) else self._fail("envelope_not_dict")

    # --------------------------------------------------------
    # 审计
    # --------------------------------------------------------
    def get_audit(
        self,
        limit: int = 20,
        field: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            return self._api.get_audit(limit=limit, field=field)

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        if not self._enable_events:
            return
        try:
            self._event_bus.publish_typed(
                event_type=event_type,
                source="service.control",
                data=data if isinstance(data, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ControlService: publish 失败: %s", exc)

    @staticmethod
    def _fail(error: str) -> Dict[str, Any]:
        return {
            "success": False,
            "data": {},
            "error": str(error or "control_failed"),
            "timestamp": "",
            "schema_version": "1.0",
            "degraded": True,
            "latency_ms": 0.0,
        }


# ============================================================
# 模块级单例
# ============================================================
_svc_instance: Optional[ControlService] = None
_svc_lock = threading.Lock()


def get_control_service() -> ControlService:
    """获取 ControlService 单例(懒加载)。"""
    global _svc_instance
    if _svc_instance is None:
        with _svc_lock:
            if _svc_instance is None:
                _svc_instance = ControlService()
    return _svc_instance


def reset_control_service_for_testing(
    api_client: Optional[ControlApiClient] = None,
    event_bus: Optional[Any] = None,
) -> ControlService:
    """测试用:重置并返回新实例。"""
    global _svc_instance
    with _svc_lock:
        if api_client is None:
            api_client = reset_control_api_client_for_testing()
        _svc_instance = ControlService(
            api_client=api_client,
            event_bus=event_bus,
        )
    return _svc_instance


__all__ = [
    "ControlService",
    "CONTROL_UPDATED",
    "get_control_service",
    "reset_control_service_for_testing",
]
