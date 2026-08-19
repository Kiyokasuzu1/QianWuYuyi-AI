# -*- coding: utf-8 -*-
"""
src/runtime/policy/policy_context.py

Phase C.10.7 — PolicyContext

PolicyContext 是 PolicyEngine 评估时使用的统一上下文。

职责:
- 承载 ControlState(只读快照)
- 承载 Runtime 状态(当前 cycle_id / RuntimeContext 引用)
- 承载模块/系统元信息(模块名 / Runtime 版本)
- 不持有可写状态(只读)
- 不依赖任何业务模块 / 不依赖 Desktop

设计原则:
- 使用 dataclass 便于快速构造
- 全部字段可选,缺失时回退到安全默认值
- to_dict() 异常隔离
- PolicyEngine 接收 PolicyContext 而不是分散的多个参数,便于扩展

注意:
- 不在 PolicyContext 中 import src.runtime.runtime 任何具体类
- Runtime 传入的 ctx 引用只是为了读取 cycle_id / 调试信息
- 任何字段读取都应被 try/except 保护
"""

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


POLICY_CONTEXT_SCHEMA_VERSION = "1.0"


# ============================================================
# 工具函数
# ============================================================
def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if value is None:
            return default
        return bool(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    try:
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        return {}
    except Exception:  # noqa: BLE001
        return {}


# ============================================================
# PolicyContext
# ============================================================
@dataclass
class PolicyContext:
    """PolicyEngine 评估时使用的统一上下文(只读)。

    字段:
    - module:         待评估的模块名(memory/emotion/growth/...)
    - cycle_id:       当前 Runtime cycle ID(用于审计关联)
    - runtime_mode:   当前运行模式(normal/safe/maintenance/unknown)
    - is_safe_mode:   是否处于安全模式
    - is_maintenance_mode: 是否处于维护模式
    - control_state:  ControlState 快照(dict,可能为空)
    - module_enabled: ControlState 中该模块的 enabled 标志
    - runtime_context:可选 RuntimeContext 引用,仅用于读取调试信息
    - system_info:    系统元信息(主机名/PID/启动时间等)
    - request:        调用方附加的请求信息(action / cycle_kind / 其它)
    - extra:          其它任意附加字段(规则可自定义读取)

    所有字段都通过 _safe_* 工具函数读取,异常不会抛出。
    """

    module: str = ""
    cycle_id: str = ""
    runtime_mode: str = "unknown"
    is_safe_mode: bool = False
    is_maintenance_mode: bool = False
    control_state: Dict[str, Any] = field(default_factory=dict)
    module_enabled: Optional[bool] = None
    runtime_context: Optional[Any] = None
    system_info: Dict[str, Any] = field(default_factory=dict)
    request: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)
    schema_version: str = POLICY_CONTEXT_SCHEMA_VERSION

    # --------------------------------------------------------
    # 工厂方法
    # --------------------------------------------------------
    @classmethod
    def from_runtime(
        cls,
        module: str = "",
        runtime_context: Optional[Any] = None,
        control_state: Optional[Dict[str, Any]] = None,
        control_context: Optional[Any] = None,
        cycle_id: str = "",
        request: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> "PolicyContext":
        """从 Runtime 侧快速构造一个 PolicyContext。

        参数:
        - module:         待评估的模块名
        - runtime_context:RuntimeContext 引用(可选,仅用于读取 cycle_id 等)
        - control_state:  ControlState 快照(可选,通常从 Runtime 读)
        - control_context:RuntimeControlContext 引用(可选,从中读取 mode/enabled)
        - cycle_id:       显式传入的 cycle_id,缺省时尝试从 runtime_context 读取
        - request:        附加请求信息
        - extra:          其它扩展字段
        """
        cs = _safe_dict(control_state)
        mode = _safe_str(_read_attr(control_context, "is_safe_mode") or None, "unknown")

        # mode 判定优先级:Safe > Maintenance > Normal > Unknown
        is_safe = _safe_bool(_read_attr(control_context, "is_safe_mode")(), False) if _has_method(control_context, "is_safe_mode") else False
        is_maint = _safe_bool(_read_attr(control_context, "is_maintenance_mode")(), False) if _has_method(control_context, "is_maintenance_mode") else False

        if is_safe:
            runtime_mode = "safe"
        elif is_maint:
            runtime_mode = "maintenance"
        else:
            # 检查 control_state 字段
            try:
                if isinstance(cs, dict) and cs.get("safe_mode") is True:
                    runtime_mode = "safe"
                    is_safe = True
                elif isinstance(cs, dict) and cs.get("maintenance_mode") is True:
                    runtime_mode = "maintenance"
                    is_maint = True
                else:
                    runtime_mode = "normal"
            except Exception:  # noqa: BLE001
                runtime_mode = "normal"

        # module_enabled
        mod_enabled: Optional[bool] = None
        try:
            if _has_method(control_context, "is_enabled"):
                mod_enabled = _safe_bool(control_context.is_enabled(module), True)
            elif _has_method(control_context, "allow"):
                mod_enabled = _safe_bool(control_context.allow(module), True)
            elif isinstance(cs, dict) and module:
                key = f"{module}_enabled"
                if key in cs:
                    mod_enabled = _safe_bool(cs.get(key), True)
        except Exception:  # noqa: BLE001
            mod_enabled = None

        # cycle_id 解析
        if not cycle_id and runtime_context is not None:
            try:
                cycle_id = _safe_str(
                    getattr(runtime_context, "_cycle_id", "")
                    or getattr(runtime_context, "cycle_id", "")
                )
            except Exception:  # noqa: BLE001
                cycle_id = ""
        if not cycle_id:
            cycle_id = f"pc_{uuid.uuid4().hex[:8]}"

        return cls(
            module=_safe_str(module),
            cycle_id=_safe_str(cycle_id),
            runtime_mode=_safe_str(runtime_mode, "normal"),
            is_safe_mode=is_safe,
            is_maintenance_mode=is_maint,
            control_state=cs,
            module_enabled=mod_enabled,
            runtime_context=runtime_context,
            system_info=cls._collect_system_info(),
            request=_safe_dict(request),
            extra=_safe_dict(extra),
        )

    @staticmethod
    def _collect_system_info() -> Dict[str, Any]:
        """收集系统信息(主机名 / PID / 启动时间),只读一次,失败时返回空 dict。"""
        try:
            return {
                "hostname": socket.gethostname() if hasattr(socket, "gethostname") else "",
                "pid": os.getpid() if hasattr(os, "getpid") else 0,
                "captured_at": int(time.time()),
            }
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # 便捷方法
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict(写 audit / event 用)。"""
        try:
            return {
                "schema_version": str(self.schema_version or POLICY_CONTEXT_SCHEMA_VERSION),
                "module": str(self.module or ""),
                "cycle_id": str(self.cycle_id or ""),
                "runtime_mode": str(self.runtime_mode or "unknown"),
                "is_safe_mode": bool(self.is_safe_mode),
                "is_maintenance_mode": bool(self.is_maintenance_mode),
                "control_state": _safe_dict(self.control_state),
                "module_enabled": (
                    bool(self.module_enabled)
                    if self.module_enabled is not None
                    else None
                ),
                "system_info": _safe_dict(self.system_info),
                "request": _safe_dict(self.request),
                "extra": _safe_dict(self.extra),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyContext.to_dict 异常(已隔离): %s", exc)
            return {
                "schema_version": POLICY_CONTEXT_SCHEMA_VERSION,
                "module": "",
                "cycle_id": "",
                "runtime_mode": "unknown",
                "is_safe_mode": False,
                "is_maintenance_mode": False,
                "control_state": {},
                "module_enabled": None,
                "system_info": {},
                "request": {},
                "extra": {},
            }

    def is_module_allowed(self) -> bool:
        """根据 module_enabled 判定模块是否启用。

        - module_enabled=None → True(未知即允许,避免阻塞 Runtime)
        - module_enabled=True/False → 对应布尔
        """
        if self.module_enabled is None:
            return True
        return bool(self.module_enabled)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"PolicyContext(module={self.module!r}, mode={self.runtime_mode!r}, "
            f"cycle_id={self.cycle_id!r})"
        )


# ============================================================
# 内部工具
# ============================================================
def _read_attr(obj: Any, name: str) -> Any:
    """安全读取属性,失败返回 None。"""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:  # noqa: BLE001
        return None


def _has_method(obj: Any, name: str) -> bool:
    """安全判断对象是否有某方法。"""
    if obj is None:
        return False
    try:
        m = getattr(obj, name, None)
        return callable(m)
    except Exception:  # noqa: BLE001
        return False
