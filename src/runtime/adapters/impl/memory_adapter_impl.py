# -*- coding: utf-8 -*-
"""
src/runtime/adapters/impl/memory_adapter_impl.py

Phase 3.7.2: MemoryAdapterImpl —— Memory 模块 Adapter 实现

职责：
- 桥接 Runtime Adapter 接口（MemoryAdapterSpec）到 MemoryService
- 内部依赖：src.memory.memory_service.MemoryService
- 不修改 MemoryService

Runtime → MemoryAdapterSpec → MemoryAdapterImpl → MemoryService

依赖：
- src.runtime.adapters.base.AdapterBase（继承）
- src.runtime.adapters.memory_adapter.MemoryAdapterSpec（继承，接口契约）
- src.memory.memory_service.MemoryService（业务实现,延迟 import）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.adapters.base import AdapterBase
from src.runtime.adapters.memory_adapter import MemoryAdapterSpec
from src.runtime.context import RuntimeContext
from src.runtime.events import Event

logger = logging.getLogger(__name__)


class MemoryAdapterImpl(MemoryAdapterSpec):
    """Memory 模块 Adapter 实现（Phase 3.7.2 / v1.0）

    桥接 Runtime 抽象接口到现有 MemoryService。

    生命周期：
    - attach()  注入 MemoryService 实例（默认自建）
    - detach()  清空引用
    - health_check()  返回基础健康信息

    业务接口：
    - retrieve(context)  调 MemoryService.semantic_search
    - store(event)       调 MemoryService 内部 store（轻量包装）
    """

    name: str = "memory_adapter_impl"
    schema_version: str = "1.0"

    def __init__(self, memory_service: Optional[Any] = None) -> None:
        super().__init__()
        self._service: Optional[Any] = memory_service
        self._last_retrieve_count: int = 0

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        """注入 MemoryService（默认自建）。"""
        if self._service is None:
            try:
                from src.memory.memory_service import MemoryService
                self._service = MemoryService()
            except Exception as exc:  # noqa: BLE001
                logger.warning("MemoryAdapterImpl.attach() 自建 MemoryService 失败: %s", exc)
                self._service = None
        self._mark_attached()
        self._cache_health({
            "healthy": self._service is not None,
            "name": self.name,
            "schema_version": self.schema_version,
        })

    def detach(self) -> None:
        """解除接入（不关闭业务实例,只清空引用）。"""
        self._service = None
        self._mark_detached()

    def health_check(self) -> Dict[str, Any]:
        healthy = self.is_attached and self._service is not None
        result = {
            "healthy": healthy,
            "name": self.name,
            "schema_version": self.schema_version,
        }
        self._cache_health(result)
        return result

    # --------------------------------------------------------
    # Memory 业务接口实现
    # --------------------------------------------------------
    def retrieve(self, context: RuntimeContext) -> Any:
        """检索相关记忆。

        Args:
            context: Runtime 共享上下文（含 user_input / session_id）

        Returns:
            List[Dict] 命中的记忆条目；异常时返回 []
        """
        if not self.is_attached or self._service is None:
            logger.debug("MemoryAdapterImpl.retrieve() 未 attach 或无 service,返回空列表")
            self._last_retrieve_count = 0
            return []

        query = getattr(context, "user_input", "") or ""
        if not query:
            self._last_retrieve_count = 0
            return []

        # Phase 2.5-B: 携带当前用户身份做 scope 授权检索。
        # 兼容两种 context 形态:属性 user_id 或 frozen ctx 的 inputs["user_id"]。
        # 取不到时传 None → 保持旧行为(不过滤),向后兼容。
        uid = getattr(context, "user_id", None)
        if not uid:
            inputs = getattr(context, "inputs", None) or {}
            if isinstance(inputs, dict):
                uid = inputs.get("user_id")

        try:
            results = self._service.semantic_search(query, top_k=5, user_id=uid)
            if results is None:
                results = []
            self._last_retrieve_count = len(results)
            return results
        except Exception as exc:  # noqa: BLE001
            logger.warning("MemoryAdapterImpl.retrieve() 调用 MemoryService 失败: %s", exc)
            self._last_retrieve_count = 0
            return []

    def store(self, event: Event) -> Any:
        """存储事件到长期 Memory（轻量包装）。

        本阶段不引入新的存储路径,只保证不破坏业务;
        返回 dict 以便调用方判断成功状态。
        """
        if not self.is_attached or self._service is None:
            return {"stored": False, "reason": "not_attached"}

        try:
            # Phase 3.7.2: 仅做引用保留,真正落盘由 MemoryService 持有 store
            # 留出扩展点,后续可以在这里把 Event 转换为 MemoryEntry 再调用 store.add
            return {
                "stored": True,
                "event_id": getattr(event, "id", None),
                "event_type": getattr(event, "type", None),
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("MemoryAdapterImpl.store() 失败: %s", exc)
            return {"stored": False, "reason": str(exc)}

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def last_retrieve_count(self) -> int:
        return self._last_retrieve_count
