# -*- coding: utf-8 -*-
"""
src/control/manager/control_manager.py

Phase C.10.5.3 — Yuyi Control Plane: Control Manager

职责:
- 控制平面统一入口
- 接收控制操作(enable/disable/toggle/safe_mode)
- 调用 ControlStatePersistence 修改状态
- 调用 ModuleRegistry 校验模块
- 不启动/停止任何业务对象(只改状态)

安全规则:
- runtime 模块不可关闭(readonly)
- safe_mode 开启后,所有 module 操作需带 reason
- 所有操作必须通过 ControlStatePersistence(已自带 audit)
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.control.state.control_state import (
    ControlStateChange,
    ControlStatePersistence,
    get_control_state_persistence,
    reset_control_state_persistence_for_testing,
)
from src.control.registry.module_registry import (
    ModuleInfo,
    ModuleRegistry,
    get_module_registry,
    reset_module_registry_for_testing,
)


# ============================================================
# 错误
# ============================================================
class ControlManagerError(Exception):
    """ControlManager 错误。"""


# ============================================================
# 结果
# ============================================================
@dataclass
class ControlResult:
    """
    控制操作结果。

    字段:
        success:        是否成功
        action:         执行的动作
        module:         涉及的模块名(若适用)
        old_value:      旧值(若适用)
        new_value:      新值(若适用)
        reason:         原因说明
        error:          错误信息(失败时)
        change:         ControlStateChange 详情(成功时)
        timestamp:      ISO8601 UTC
    """

    success: bool
    action: str
    module: str = ""
    old_value: Optional[bool] = None
    new_value: Optional[bool] = None
    reason: str = ""
    error: str = ""
    change: Optional[Dict[str, Any]] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# ControlManager
# ============================================================
class ControlManager:
    """
    Yuyi Control Plane 管理器。

    提供:
    - enable_module(name, operator, reason)
    - disable_module(name, operator, reason)
    - toggle_module(name, operator, reason)
    - enter_safe_mode(operator, reason)
    - exit_safe_mode(operator, reason)
    - get_overview(operator)  # 当前控制状态总览
    """

    SAFE_MODE_FIELDS = ("safe_mode", "maintenance_mode")

    def __init__(
        self,
        state_persistence: Optional[ControlStatePersistence] = None,
        registry: Optional[ModuleRegistry] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state = state_persistence or get_control_state_persistence()
        self._registry = registry or get_module_registry()

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _ensure_module(self, name: str) -> ModuleInfo:
        m = self._registry.get_module(name)
        if m is None:
            raise ControlManagerError(f"module_not_found: {name}")
        return m

    def _ensure_controllable(self, m: ModuleInfo, new_value: bool) -> None:
        """
        校验模块是否可被切换到 new_value。

        规则:
        - 只读模块不可被 disable
        - 不可控模块抛错
        """
        if not m.controllable:
            raise ControlManagerError(
                f"module_not_controllable: {m.name}"
            )
        if m.readonly and new_value is False:
            raise ControlManagerError(
                f"module_readonly_cannot_disable: {m.name}"
            )

    def _do_set(
        self,
        module_name: str,
        new_value: bool,
        operator: str,
        reason: str,
        action: str,
    ) -> ControlResult:
        try:
            m = self._ensure_module(module_name)
            self._ensure_controllable(m, new_value)
        except ControlManagerError as exc:
            return ControlResult(
                success=False,
                action=action,
                module=module_name,
                new_value=new_value,
                reason=reason,
                error=str(exc),
            )

        try:
            change = self._state.set_field(
                m.state_field,
                new_value,
                operator=operator,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            return ControlResult(
                success=False,
                action=action,
                module=module_name,
                new_value=new_value,
                reason=reason,
                error=f"state_set_failed: {exc}",
            )

        return ControlResult(
            success=True,
            action=action,
            module=module_name,
            old_value=change.old_value,
            new_value=change.new_value,
            reason=reason,
            change=change.to_dict(),
        )

    # --------------------------------------------------------
    # 公开 API:模块操作
    # --------------------------------------------------------
    def enable_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        """启用模块。"""
        return self._do_set(
            module_name=name,
            new_value=True,
            operator=operator,
            reason=reason,
            action="enable",
        )

    def disable_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        """禁用模块。"""
        return self._do_set(
            module_name=name,
            new_value=False,
            operator=operator,
            reason=reason,
            action="disable",
        )

    def toggle_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        """切换模块状态。"""
        try:
            m = self._ensure_module(name)
        except ControlManagerError as exc:
            return ControlResult(
                success=False,
                action="toggle",
                module=name,
                reason=reason,
                error=str(exc),
            )
        current = self._state.get_field(m.state_field)
        if current is None:
            return ControlResult(
                success=False,
                action="toggle",
                module=name,
                reason=reason,
                error="state_field_unavailable",
            )
        new_val = not bool(current)
        return self._do_set(
            module_name=name,
            new_value=new_val,
            operator=operator,
            reason=reason,
            action="toggle",
        )

    # --------------------------------------------------------
    # 公开 API:系统模式
    # --------------------------------------------------------
    def enter_safe_mode(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        """
        进入安全模式。

        安全模式下:
        - 所有模块 enable 操作被允许
        - 所有模块 disable 操作被允许
        - 但推荐先 disable 高风险模块(此方法只翻转 safe_mode)
        """
        return self._do_set_system(
            field="safe_mode",
            new_value=True,
            operator=operator,
            reason=reason or "enter_safe_mode",
            action="enter_safe_mode",
        )

    def exit_safe_mode(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        """退出安全模式。"""
        return self._do_set_system(
            field="safe_mode",
            new_value=False,
            operator=operator,
            reason=reason or "exit_safe_mode",
            action="exit_safe_mode",
        )

    def enter_maintenance(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        return self._do_set_system(
            field="maintenance_mode",
            new_value=True,
            operator=operator,
            reason=reason or "enter_maintenance",
            action="enter_maintenance",
        )

    def exit_maintenance(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> ControlResult:
        return self._do_set_system(
            field="maintenance_mode",
            new_value=False,
            operator=operator,
            reason=reason or "exit_maintenance",
            action="exit_maintenance",
        )

    def _do_set_system(
        self,
        field: str,
        new_value: bool,
        operator: str,
        reason: str,
        action: str,
    ) -> ControlResult:
        try:
            change = self._state.set_field(
                field,
                new_value,
                operator=operator,
                reason=reason,
            )
        except Exception as exc:  # noqa: BLE001
            return ControlResult(
                success=False,
                action=action,
                reason=reason,
                error=f"state_set_failed: {exc}",
            )
        return ControlResult(
            success=True,
            action=action,
            old_value=change.old_value,
            new_value=change.new_value,
            reason=reason,
            change=change.to_dict(),
        )

    # --------------------------------------------------------
    # 公开 API:总览
    # --------------------------------------------------------
    def get_overview(self) -> Dict[str, Any]:
        """
        获取控制平面总览(状态 + 模块 + 系统模式)。
        """
        with self._lock:
            state = self._state.get_state()
            modules = self._registry.get_status(state_provider=state)
            audit_count = self._state.audit_count()
            recent_audit = self._state.list_audit(limit=20)
            return {
                "state": state.to_dict(),
                "modules": modules,
                "audit_count": int(audit_count),
                "recent_audit": recent_audit,
            }

    def get_recent_audit(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            return self._state.list_audit(limit=limit)

    def is_module_enabled(self, name: str) -> bool:
        with self._lock:
            m = self._registry.get_module(name)
            if m is None:
                return False
            v = self._state.get_field(m.state_field)
            return bool(v) if v is not None else False


# ============================================================
# 模块级单例
# ============================================================
_manager_instance: Optional[ControlManager] = None
_manager_lock = threading.Lock()


def get_control_manager() -> ControlManager:
    """获取 ControlManager 单例。"""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = ControlManager()
    return _manager_instance


def reset_control_manager_for_testing(
    state_persistence: Optional[ControlStatePersistence] = None,
    registry: Optional[ModuleRegistry] = None,
) -> ControlManager:
    """测试用:重置并返回新实例。"""
    global _manager_instance
    with _manager_lock:
        if state_persistence is None:
            state_persistence = reset_control_state_persistence_for_testing()
        if registry is None:
            registry = reset_module_registry_for_testing()
        _manager_instance = ControlManager(
            state_persistence=state_persistence,
            registry=registry,
        )
    return _manager_instance


__all__ = [
    "ControlManager",
    "ControlManagerError",
    "ControlResult",
    "get_control_manager",
    "reset_control_manager_for_testing",
]
