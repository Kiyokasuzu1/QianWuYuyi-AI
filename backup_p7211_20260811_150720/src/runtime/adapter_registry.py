# -*- coding: utf-8 -*-
"""
src/runtime/adapter_registry.py

Phase 3.7.2: AdapterRegistry —— Adapter 实例的轻量注册表

职责：
- 保存 Adapter 实例（key: name）
- 提供 register / get / health_check_all
- 不实现 Runtime 生命周期
- 不感知业务实现

依赖：
- 仅 stdlib + src.runtime.adapters.base.AdapterBase
- 禁止 import src.memory / src.emotion / src.personality / src.growth
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Adapter 注册表（Phase 3.7.2 / v1.0）

    使用方式：
        registry = AdapterRegistry()
        registry.register(MemoryAdapterImpl())
        registry.register(EmotionAdapterImpl())
        registry.register(GrowthAdapterImpl())
        registry.register(PersonalityAdapterImpl())

        memory = registry.get("memory_adapter_impl")
        healths = registry.health_check_all()
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, AdapterBase] = {}

    # --------------------------------------------------------
    # 注册
    # --------------------------------------------------------
    def register(self, name: str, adapter: Optional[AdapterBase] = None) -> None:
        """注册一个 Adapter。

        Args:
            name:    Adapter 名（与 adapter.name 保持一致）
            adapter: AdapterBase 实例
        """
        if adapter is None:
            raise ValueError("AdapterRegistry.register() 拒绝 None Adapter")
        if not isinstance(adapter, AdapterBase):
            raise TypeError(
                f"Adapter 必须继承 AdapterBase,当前类型: {type(adapter)!r}"
            )
        if name in self._adapters:
            logger.warning("AdapterRegistry 已存在同名 Adapter: %s,覆盖", name)
        self._adapters[name] = adapter

    def unregister(self, name: str) -> Optional[AdapterBase]:
        """注销一个 Adapter。"""
        return self._adapters.pop(name, None)

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get(self, name: str) -> Optional[AdapterBase]:
        """按 name 获取 Adapter。"""
        return self._adapters.get(name)

    def has(self, name: str) -> bool:
        return name in self._adapters

    def names(self) -> List[str]:
        return list(self._adapters.keys())

    def all(self) -> List[AdapterBase]:
        return list(self._adapters.values())

    def __len__(self) -> int:
        return len(self._adapters)

    def __contains__(self, name: object) -> bool:
        return name in self._adapters

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check_all(self) -> Dict[str, Any]:
        """对所有 Adapter 执行 health_check。

        Returns:
            {
                "healthy": bool,        # 所有 Adapter 都健康
                "count": int,           # Adapter 总数
                "adapters": {           # 每个 Adapter 的 health_check 结果
                    name: {...}
                }
            }
        """
        results: Dict[str, Any] = {}
        all_healthy = True
        for name, adapter in self._adapters.items():
            try:
                r = adapter.health_check()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Adapter %s health_check 失败: %s", name, exc)
                r = {
                    "healthy": False,
                    "name": getattr(adapter, "name", name),
                    "schema_version": getattr(adapter, "schema_version", "1.0"),
                    "error": repr(exc),
                }
            results[name] = r
            if not r.get("healthy", False):
                all_healthy = False
        return {
            "healthy": all_healthy,
            "count": len(self._adapters),
            "adapters": results,
        }

    # --------------------------------------------------------
    # 生命周期辅助
    # --------------------------------------------------------
    def attach_all(self) -> Dict[str, bool]:
        """对所有 Adapter 调用 attach()。

        Returns:
            {name: success_bool}
        """
        results: Dict[str, bool] = {}
        for name, adapter in self._adapters.items():
            try:
                adapter.attach()
                results[name] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Adapter %s attach 失败: %s", name, exc)
                results[name] = False
        return results

    def detach_all(self) -> Dict[str, bool]:
        """对所有 Adapter 调用 detach()。"""
        results: Dict[str, bool] = {}
        for name, adapter in self._adapters.items():
            try:
                adapter.detach()
                results[name] = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Adapter %s detach 失败: %s", name, exc)
                results[name] = False
        return results
