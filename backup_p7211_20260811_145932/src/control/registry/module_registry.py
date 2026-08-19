# -*- coding: utf-8 -*-
"""
src/control/registry/module_registry.py

Phase C.10.5.2 — Yuyi Control Plane: Module Registry

职责:
- 注册羽依所有可被控制平面管理的模块
- 提供模块元信息(name / display / version / description / readonly / controllable)
- 提供模块状态查询(enabled / disabled)
- 状态来源:ControlState(只读视图,Registry 自身不持有状态)

模块清单(默认 7 个):
- Runtime
- Memory
- Emotion
- Growth
- Initiative
- Dream
- Live2D

约束:
- Registry 不直接修改任何业务对象
- Registry 只与 ControlState 交互获取状态
- Registry 自身是只读结构(初始化后,模块清单通常不变)
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# ModuleInfo
# ============================================================
@dataclass
class ModuleInfo:
    """
    模块元信息。

    字段:
        name:           模块名(英文,唯一)
        display:        显示名
        version:        版本
        description:    描述
        state_field:    对应 ControlState 中的字段名(如 'growth_enabled')
        readonly:       是否只读(不可被关闭)
        controllable:   是否可通过 Control Plane 控制
        category:       分类(可选)
    """

    name: str
    display: str
    version: str
    description: str
    state_field: str
    readonly: bool = False
    controllable: bool = True
    category: str = "core"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModuleInfo":
        if not isinstance(data, dict):
            raise ValueError("data must be dict")
        return cls(
            name=str(data.get("name", "")),
            display=str(data.get("display", data.get("name", ""))),
            version=str(data.get("version", "1.0")),
            description=str(data.get("description", "")),
            state_field=str(data.get("state_field", "")),
            readonly=bool(data.get("readonly", False)),
            controllable=bool(data.get("controllable", True)),
            category=str(data.get("category", "core")),
        )


# ============================================================
# 内置模块清单(默认 7 个)
# ============================================================
def _build_builtin_modules() -> List[ModuleInfo]:
    return [
        ModuleInfo(
            name="runtime",
            display="Runtime",
            version="1.0",
            description="羽依运行时核心(Runtime Core + Adapter)",
            state_field="runtime_enabled",
            readonly=True,           # Runtime 是核心,不可关闭
            controllable=False,
            category="core",
        ),
        ModuleInfo(
            name="memory",
            display="Memory",
            version="1.0",
            description="羽依记忆系统(长期/事件/身份记忆)",
            state_field="memory_enabled",
            readonly=False,
            controllable=True,
            category="core",
        ),
        ModuleInfo(
            name="emotion",
            display="Emotion",
            version="1.0",
            description="羽依情绪系统(情绪状态、衰减、信念)",
            state_field="emotion_enabled",
            readonly=False,
            controllable=True,
            category="core",
        ),
        ModuleInfo(
            name="growth",
            display="Growth",
            version="1.0",
            description="羽依成长引擎(proposal/review/approval)",
            state_field="growth_enabled",
            readonly=False,
            controllable=True,
            category="evolution",
        ),
        ModuleInfo(
            name="initiative",
            display="Initiative",
            version="1.0",
            description="羽依主动行为引擎(兴趣、可能动作、过滤)",
            state_field="initiative_enabled",
            readonly=False,
            controllable=True,
            category="evolution",
        ),
        ModuleInfo(
            name="dream",
            display="Dream",
            version="1.0",
            description="羽依梦境层(dream_layer)",
            state_field="dream_enabled",
            readonly=False,
            controllable=True,
            category="optional",
        ),
        ModuleInfo(
            name="live2d",
            display="Live2D",
            version="1.0",
            description="羽依 Live2D 表现层(模型、动作、表情)",
            state_field="live2d_enabled",
            readonly=False,
            controllable=True,
            category="presentation",
        ),
    ]


BUILTIN_MODULES: List[ModuleInfo] = _build_builtin_modules()


# ============================================================
# ModuleRegistry
# ============================================================
class ModuleRegistry:
    """
    羽依模块注册中心。

    存储模块元信息(只读)。
    提供模块状态查询接口(状态来源由调用方注入,通常为 ControlState)。
    """

    def __init__(
        self,
        modules: Optional[List[ModuleInfo]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._modules: Dict[str, ModuleInfo] = {}
        for m in (modules if modules is not None else list(BUILTIN_MODULES)):
            if m and m.name:
                self._modules[m.name] = m

    # --------------------------------------------------------
    # 注册
    # --------------------------------------------------------
    def register(self, module: ModuleInfo, replace: bool = False) -> bool:
        """
        注册一个模块。

        Args:
            module: ModuleInfo
            replace: 若已存在是否覆盖

        Returns:
            是否成功注册。
        """
        if not isinstance(module, ModuleInfo) or not module.name:
            return False
        with self._lock:
            if module.name in self._modules and not replace:
                return False
            self._modules[module.name] = module
            return True

    def unregister(self, name: str) -> bool:
        """注销模块(测试用,默认 7 个模块不应被注销)。"""
        with self._lock:
            return self._modules.pop(name, None) is not None

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get_module(self, name: str) -> Optional[ModuleInfo]:
        with self._lock:
            return self._modules.get(name)

    def get_modules(self) -> List[ModuleInfo]:
        """获取所有模块(浅拷贝)。"""
        with self._lock:
            return list(self._modules.values())

    def get_module_dicts(self) -> List[Dict[str, Any]]:
        """获取所有模块的 dict 形式(便于序列化)。"""
        with self._lock:
            return [m.to_dict() for m in self._modules.values()]

    def list_names(self) -> List[str]:
        with self._lock:
            return list(self._modules.keys())

    def has_module(self, name: str) -> bool:
        with self._lock:
            return name in self._modules

    def is_controllable(self, name: str) -> bool:
        """模块是否可被 Control Plane 控制。"""
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return False
            return bool(m.controllable)

    def is_readonly(self, name: str) -> bool:
        """模块是否只读(不可关闭)。"""
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return False
            return bool(m.readonly)

    # --------------------------------------------------------
    # 状态查询(需要 state_provider 注入)
    # --------------------------------------------------------
    def is_enabled(
        self,
        name: str,
        state_provider: Optional[Any] = None,
    ) -> bool:
        """
        判断模块是否启用。

        Args:
            name: 模块名
            state_provider: 提供 get_field(name) 接口的对象(如 ControlState)。
                            若为 None,返回 False。
        """
        with self._lock:
            m = self._modules.get(name)
            if m is None:
                return False
        if state_provider is None:
            return False
        try:
            getter = getattr(state_provider, "get_field", None)
            if getter is None:
                return False
            v = getter(m.state_field)
            return bool(v) if v is not None else False
        except Exception:
            return False

    def get_status(
        self,
        state_provider: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取所有模块的状态。

        Returns:
            list of {
                "name", "display", "version", "description",
                "state_field", "enabled", "readonly", "controllable", "category"
            }
        """
        with self._lock:
            modules = list(self._modules.values())
        out: List[Dict[str, Any]] = []
        for m in modules:
            enabled = self.is_enabled(m.name, state_provider=state_provider)
            d = m.to_dict()
            d["enabled"] = bool(enabled)
            out.append(d)
        return out


# ============================================================
# 模块级单例
# ============================================================
_registry_instance: Optional[ModuleRegistry] = None
_registry_lock = threading.Lock()


def get_module_registry() -> ModuleRegistry:
    """获取 ModuleRegistry 单例。"""
    global _registry_instance
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = ModuleRegistry()
    return _registry_instance


def reset_module_registry_for_testing(
    modules: Optional[List[ModuleInfo]] = None,
) -> ModuleRegistry:
    """测试用:重置并返回新实例。"""
    global _registry_instance
    with _registry_lock:
        if modules is None:
            _registry_instance = ModuleRegistry()
        else:
            _registry_instance = ModuleRegistry(modules=modules)
    return _registry_instance


__all__ = [
    "ModuleInfo",
    "ModuleRegistry",
    "BUILTIN_MODULES",
    "get_module_registry",
    "reset_module_registry_for_testing",
]
