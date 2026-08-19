# -*- coding: utf-8 -*-
"""
src/runtime/context/control_context.py

Phase C.10.6.3 — RuntimeControlContext

职责:
- 包装 ControlStateProvider,给 Runtime cycle 提供统一的控制状态查询接口
- 不直接 import 任何业务模块
- 不持有状态(每次实时从 provider 读取)
- 默认 None 时,所有 allow() 返回 True(向后兼容)

设计目标:
- Runtime 依赖注入:RuntimeContext.control_context 字段
- 旧 Runtime 代码 0 改动:未注入时所有 allow() 返回 True
- 测试友好:可通过构造 ControlStateProvider mock 注入
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)


# ============================================================
# 协议:ControlStateProvider
# ============================================================
class ControlStateProvider(Protocol):
    """
    ControlState 提供者协议(鸭子类型)。

    任何实现了 is_enabled(module) -> bool 接口的对象都可以作为 Provider。
    例如 RuntimeControlProvider / RuntimeControlAdapter / mock。
    """

    def is_enabled(self, module: str) -> bool: ...


# ============================================================
# NullControlStateProvider
# ============================================================
class NullControlStateProvider:
    """
    空 Provider,所有模块视为 enabled。

    - 当 RuntimeContext 未注入 control_context 时,使用此默认实现
    - 完全向后兼容,所有 allow() 返回 True
    """

    def is_enabled(self, module: str) -> bool:
        return True

    def is_safe_mode(self) -> bool:
        return False

    def is_maintenance_mode(self) -> bool:
        return False

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {}

    def __repr__(self) -> str:
        return "<NullControlStateProvider enabled=True>"


# ============================================================
# RuntimeControlContext
# ============================================================
class RuntimeControlContext:
    """
    Runtime 周期内的控制上下文。

    - 包装一个 ControlStateProvider
    - 提供简单的 allow(module) 接口,Runtime 调用
    - 不修改任何状态(只读)
    - 兼容无 provider 场景(NullProvider)

    示例:
        # 旧代码(未注入 control_context)
        if control_context.allow("memory"):    # 永远 True
            memory.retrieve()

        # 新代码(已注入 control_context)
        if control_context.allow("growth"):    # 读 ControlState
            growth.evaluate()
    """

    def __init__(self, provider: Optional[Any] = None) -> None:
        self._lock = threading.RLock()
        self._provider: Any = provider or NullControlStateProvider()

    @property
    def provider(self) -> Any:
        """当前注入的 provider(可能为 NullProvider)。"""
        return self._provider

    def set_provider(self, provider: Any) -> None:
        """
        注入/替换 provider。

        - provider=None 时,回退到 NullProvider
        - 用于运行时切换(测试 / 动态重配置)
        """
        with self._lock:
            self._provider = provider or NullControlStateProvider()

    # --------------------------------------------------------
    # 核心 API:模块放行
    # --------------------------------------------------------
    def allow(self, module: str) -> bool:
        """
        判断一个模块是否应该执行。

        - provider 为 NullProvider → 永远 True
        - provider.is_enabled() 抛错 → True(fail-soft)
        - module 为空字符串 → True
        """
        if not module:
            return True
        try:
            is_enabled = getattr(self._provider, "is_enabled", None)
            if is_enabled is None or not callable(is_enabled):
                return True
            v = is_enabled(module)
            return bool(v) if v is not None else True
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "RuntimeControlContext.allow(%s) 异常(已隔离,默认 enabled): %s",
                module, exc,
            )
            return True

    def disallow(self, module: str) -> bool:
        """allow() 的反向表达,语义更清晰。"""
        return not self.allow(module)

    # --------------------------------------------------------
    # 便捷查询
    # --------------------------------------------------------
    def is_safe_mode(self) -> bool:
        try:
            fn = getattr(self._provider, "is_safe_mode", None)
            if fn is None or not callable(fn):
                return False
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def is_maintenance_mode(self) -> bool:
        try:
            fn = getattr(self._provider, "is_maintenance_mode", None)
            if fn is None or not callable(fn):
                return False
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def is_normal_mode(self) -> bool:
        return not self.is_safe_mode() and not self.is_maintenance_mode()

    def get_state_snapshot(self) -> Dict[str, Any]:
        try:
            fn = getattr(self._provider, "get_state_snapshot", None)
            if fn is None or not callable(fn):
                return {}
            v = fn()
            return v if isinstance(v, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    # --------------------------------------------------------
    # 多模块批量放行
    # --------------------------------------------------------
    def allow_all(self, modules: List[str]) -> Dict[str, bool]:
        """
        批量检查多个模块。

        返回 {module: bool} 的字典。
        """
        return {m: self.allow(m) for m in (modules or [])}

    def which_allowed(self, modules: List[str]) -> List[str]:
        """返回所有 allow() 为 True 的模块列表。"""
        return [m for m in (modules or []) if self.allow(m)]

    def which_blocked(self, modules: List[str]) -> List[str]:
        """返回所有 allow() 为 False 的模块列表。"""
        return [m for m in (modules or []) if not self.allow(m)]

    # --------------------------------------------------------
    # 调试
    # --------------------------------------------------------
    def __repr__(self) -> str:
        try:
            mode = "normal"
            if self.is_safe_mode():
                mode = "safe"
            elif self.is_maintenance_mode():
                mode = "maintenance"
            return f"<RuntimeControlContext mode={mode} provider={self._provider!r}>"
        except Exception:  # noqa: BLE001
            return "<RuntimeControlContext mode=unknown>"

    def to_dict(self) -> Dict[str, Any]:
        """序列化(给 audit / debug 用)。"""
        return {
            "provider_type": type(self._provider).__name__,
            "is_safe_mode": self.is_safe_mode(),
            "is_maintenance_mode": self.is_maintenance_mode(),
            "is_normal_mode": self.is_normal_mode(),
        }


# ============================================================
# 工厂
# ============================================================
def create_control_context(
    provider: Optional[Any] = None,
) -> RuntimeControlContext:
    """
    工厂函数:创建 RuntimeControlContext。

    - provider=None → NullControlContext(全 allow)
    - provider=Mock → 包装 Mock(用于测试)
    """
    return RuntimeControlContext(provider=provider)


# ============================================================
# 模块级单例(默认 context)
# ============================================================
_default_context: Optional[RuntimeControlContext] = None
_default_lock = threading.Lock()


def get_default_control_context() -> RuntimeControlContext:
    """
    获取默认 RuntimeControlContext 单例。

    - 第一次调用时,使用 NullProvider(全 enabled)
    - 后续可通过 RuntimeCore.configure_control_context() 替换 provider
    - 旧代码(不调用 configure)仍按 NullProvider 行为运行
    """
    global _default_context
    if _default_context is None:
        with _default_lock:
            if _default_context is None:
                _default_context = RuntimeControlContext(provider=NullControlStateProvider())
    return _default_context


def reset_default_control_context_for_testing(
    provider: Optional[Any] = None,
) -> RuntimeControlContext:
    """测试用:重置默认 context。"""
    global _default_context
    with _default_lock:
        _default_context = RuntimeControlContext(provider=provider)
    return _default_context
