# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/provider_bridge.py

Phase C.10.1 —— Provider Bridge

统一管理 8 个已有 Dashboard Provider,作为 Desktop 端访问 Runtime 数据的唯一入口。

设计原则:
- 懒加载:首次访问时按需实例化,避免启动时初始化 Runtime
- 容错:任何 Provider 不可用时,get_* 返回空 dict / 空 list
- 只读:本类仅调用 Provider 的 get_*/list_*/read_* 方法
- 线程安全:使用 RLock 保护懒加载过程
- 可注入:测试时可传入 mock Provider

约束(强):
- 禁止 import 任何 src/runtime/**、src/memory/**、src/growth/** 等业务模块
- 禁止调用 apply / resolve / 任何写接口
- 禁止修改主项目 Provider 行为
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 白名单:允许注入的 Provider key
PROVIDER_KEYS = (
    "runtime",
    "selfmodel",
    "memory",
    "growth",
    "initiative",
    "life_state",
    "life_timeline",
    "life_graph",
)


class ProviderBridge:
    """
    统一管理 8 个 Dashboard Provider 的桥接器。

    Usage:
        bridge = ProviderBridge()
        runtime_status = bridge.runtime_get_status()
    """

    def __init__(
        self,
        runtime_provider: Optional[Any] = None,
        selfmodel_provider: Optional[Any] = None,
        memory_provider: Optional[Any] = None,
        growth_provider: Optional[Any] = None,
        initiative_provider: Optional[Any] = None,
        life_state_provider: Optional[Any] = None,
        life_timeline_provider: Optional[Any] = None,
        life_graph_provider: Optional[Any] = None,
    ) -> None:
        self._lock = threading.RLock()
        # 注入槽
        self._runtime = runtime_provider
        self._selfmodel = selfmodel_provider
        self._memory = memory_provider
        self._growth = growth_provider
        self._initiative = initiative_provider
        self._life_state = life_state_provider
        self._life_timeline = life_timeline_provider
        self._life_graph = life_graph_provider

    # --------------------------------------------------------
    # 内部:懒加载单例(同线程安全)
    # --------------------------------------------------------
    def _get_runtime(self) -> Optional[Any]:
        with self._lock:
            if self._runtime is not None:
                return self._runtime
            try:
                from src.admin.runtime_dashboard_provider import (
                    get_runtime_dashboard_provider,
                )
                self._runtime = get_runtime_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: RuntimeProvider 不可用: %s", exc)
                self._runtime = None
            return self._runtime

    def _get_selfmodel(self) -> Optional[Any]:
        with self._lock:
            if self._selfmodel is not None:
                return self._selfmodel
            try:
                from src.admin.selfmodel_dashboard_provider import (
                    get_selfmodel_dashboard_provider,
                )
                self._selfmodel = get_selfmodel_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: SelfModelProvider 不可用: %s", exc)
                self._selfmodel = None
            return self._selfmodel

    def _get_memory(self) -> Optional[Any]:
        with self._lock:
            if self._memory is not None:
                return self._memory
            try:
                from src.admin.memory_dashboard_provider import (
                    get_memory_dashboard_provider,
                )
                self._memory = get_memory_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: MemoryProvider 不可用: %s", exc)
                self._memory = None
            return self._memory

    def _get_growth(self) -> Optional[Any]:
        with self._lock:
            if self._growth is not None:
                return self._growth
            try:
                from src.admin.growth_dashboard_provider import (
                    get_growth_dashboard_provider,
                )
                self._growth = get_growth_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: GrowthProvider 不可用: %s", exc)
                self._growth = None
            return self._growth

    def _get_initiative(self) -> Optional[Any]:
        with self._lock:
            if self._initiative is not None:
                return self._initiative
            try:
                from src.admin.initiative_dashboard_provider import (
                    get_initiative_dashboard_provider,
                )
                self._initiative = get_initiative_dashboard_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: InitiativeProvider 不可用: %s", exc)
                self._initiative = None
            return self._initiative

    def _get_life_state(self) -> Optional[Any]:
        with self._lock:
            if self._life_state is not None:
                return self._life_state
            try:
                from src.admin.life_state_provider import get_life_state_provider
                self._life_state = get_life_state_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: LifeStateProvider 不可用: %s", exc)
                self._life_state = None
            return self._life_state

    def _get_life_timeline(self) -> Optional[Any]:
        with self._lock:
            if self._life_timeline is not None:
                return self._life_timeline
            try:
                from src.admin.life_timeline_provider import get_life_timeline_provider
                self._life_timeline = get_life_timeline_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: LifeTimelineProvider 不可用: %s", exc)
                self._life_timeline = None
            return self._life_timeline

    def _get_life_graph(self) -> Optional[Any]:
        with self._lock:
            if self._life_graph is not None:
                return self._life_graph
            try:
                from src.admin.life_graph_provider import get_life_graph_provider
                self._life_graph = get_life_graph_provider()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ProviderBridge: LifeGraphProvider 不可用: %s", exc)
                self._life_graph = None
            return self._life_graph

    # --------------------------------------------------------
    # 对外只读 API
    # --------------------------------------------------------
    def runtime_get_status(self) -> Dict[str, Any]:
        p = self._get_runtime()
        if p is None:
            return {"available": False, "fallback": True, "fallback_reason": "provider_unavailable"}
        try:
            return p.get_runtime_status() or {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.runtime_get_status 异常: %s", exc)
            return {"available": False, "fallback": True, "fallback_reason": str(exc)}

    def runtime_get_lifecycle_tasks(self) -> Dict[str, Any]:
        p = self._get_runtime()
        if p is None:
            return {"tasks": [], "available": False, "fallback": True}
        try:
            return p.get_lifecycle_tasks() or {"tasks": [], "available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.runtime_get_lifecycle_tasks 异常: %s", exc)
            return {"tasks": [], "available": False, "fallback": True}

    def runtime_get_tick_history(self, limit: int = 20) -> Dict[str, Any]:
        p = self._get_runtime()
        if p is None:
            return {"ticks": [], "available": False, "fallback": True}
        try:
            return p.get_tick_history(limit=limit) or {"ticks": [], "available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.runtime_get_tick_history 异常: %s", exc)
            return {"ticks": [], "available": False, "fallback": True}

    def selfmodel_get_identity(self) -> Dict[str, Any]:
        p = self._get_selfmodel()
        if p is None:
            return {"available": False, "fallback": True}
        try:
            return p.get_identity() or {"available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.selfmodel_get_identity 异常: %s", exc)
            return {"available": False, "fallback": True}

    def memory_get_summary(self) -> Dict[str, Any]:
        p = self._get_memory()
        if p is None:
            return {"available": False, "total_count": 0, "fallback": True}
        try:
            return p.get_summary() or {"available": False, "total_count": 0}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.memory_get_summary 异常: %s", exc)
            return {"available": False, "total_count": 0, "fallback": True}

    def memory_list_recent(self, limit: int = 20) -> Dict[str, Any]:
        p = self._get_memory()
        if p is None:
            return {"items": [], "total": 0, "available": False, "fallback": True}
        try:
            return p.list_recent(limit=limit) or {"items": [], "total": 0, "available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.memory_list_recent 异常: %s", exc)
            return {"items": [], "total": 0, "available": False, "fallback": True}

    def growth_get_summary(self) -> Dict[str, Any]:
        p = self._get_growth()
        if p is None:
            return {"available": False, "total": 0, "fallback": True}
        try:
            return p.get_summary() or {"available": False, "total": 0}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.growth_get_summary 异常: %s", exc)
            return {"available": False, "total": 0, "fallback": True}

    def growth_get_recent(self, limit: int = 20) -> Dict[str, Any]:
        p = self._get_growth()
        if p is None:
            return {"items": [], "total": 0, "available": False, "fallback": True}
        try:
            return p.get_recent(limit=limit) or {"items": [], "total": 0, "available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.growth_get_recent 异常: %s", exc)
            return {"items": [], "total": 0, "available": False, "fallback": True}

    def initiative_get_summary(self) -> Dict[str, Any]:
        p = self._get_initiative()
        if p is None:
            return {"available": False, "fallback": True}
        try:
            return p.get_summary() or {"available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.initiative_get_summary 异常: %s", exc)
            return {"available": False, "fallback": True}

    def life_state_get_summary(self) -> Dict[str, Any]:
        p = self._get_life_state()
        if p is None:
            return {"available": False, "fallback": True}
        try:
            return p.get_state_summary() or {"available": False}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.life_state_get_summary 异常: %s", exc)
            return {"available": False, "fallback": True}

    def life_timeline_get(self, limit: int = 50) -> Dict[str, Any]:
        p = self._get_life_timeline()
        if p is None:
            return {"available": False, "fallback": True, "items": []}
        try:
            return p.get_timeline(limit=limit) or {"available": False, "items": []}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.life_timeline_get 异常: %s", exc)
            return {"available": False, "fallback": True, "items": []}

    def life_graph_get_timeline(self, limit: int = 50) -> Dict[str, Any]:
        p = self._get_life_graph()
        if p is None:
            return {"available": False, "fallback": True, "items": []}
        try:
            return p.get_timeline(limit=limit) or {"available": False, "items": []}
        except Exception as exc:  # noqa: BLE001
            logger.debug("ProviderBridge.life_graph_get_timeline 异常: %s", exc)
            return {"available": False, "fallback": True, "items": []}

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        """返回 8 个 Provider 的可用性概览。"""
        return {
            "runtime": self._get_runtime() is not None,
            "selfmodel": self._get_selfmodel() is not None,
            "memory": self._get_memory() is not None,
            "growth": self._get_growth() is not None,
            "initiative": self._get_initiative() is not None,
            "life_state": self._get_life_state() is not None,
            "life_timeline": self._get_life_timeline() is not None,
            "life_graph": self._get_life_graph() is not None,
        }


# ============================================================
# 模块级单例
# ============================================================
_bridge_instance: Optional[ProviderBridge] = None
_bridge_lock = threading.Lock()


def get_provider_bridge() -> ProviderBridge:
    """获取 ProviderBridge 单例(懒加载)。"""
    global _bridge_instance
    if _bridge_instance is None:
        with _bridge_lock:
            if _bridge_instance is None:
                _bridge_instance = ProviderBridge()
    return _bridge_instance


def reset_provider_bridge_for_testing() -> None:
    """测试用:重置单例。"""
    global _bridge_instance
    with _bridge_lock:
        _bridge_instance = None


__all__ = [
    "PROVIDER_KEYS",
    "ProviderBridge",
    "get_provider_bridge",
    "reset_provider_bridge_for_testing",
]
