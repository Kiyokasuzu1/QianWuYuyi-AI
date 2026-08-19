# -*- coding: utf-8 -*-
# src/runtime/self_model/memory_self_model_adapter.py
"""
Phase 4.2.2: MemorySelfModelAdapter —— Memory 数据源 Adapter

职责:
- 桥接 Runtime → MemorySelfModelAdapter → MemoryAdapter
- 从 MemoryAdapter 检索长期记忆,提取结构化 SelfModel 输入
- 不直接调用 MemoryStore / MemoryService 内部逻辑

数据流:
    Runtime → MemorySelfModelAdapter.extract_inputs(ctx)
        → MemoryAdapter.retrieve(ctx) → MemoryContext
        → SelfModelFoundation.build({"preferences": [...], "extra_entries": [...]})

约束:
- 不修改 Memory 核心模块
- 不直接 import MemoryStore / MemoryService
- 仅使用 MemoryAdapterSpec 暴露的接口(retrieve)
- 任何异常被静默吞掉,返回 {}

输入 → SelfModel 字段映射:
- MemoryContext.items (list[dict]) → preferences (key/value 模式)
- MemoryContext.items 包含 "preference" type → 合并为 preferences
- 高频记忆条目 → extra_entries(kind="narrative" 或 "event")
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import SelfModelEntry
from src.runtime.self_model.self_model_source_adapter import (
    SelfModelSourceAdapter,
    SOURCE_TYPE_MEMORY,
)


logger = logging.getLogger(__name__)


MEMORY_SELF_MODEL_ADAPTER_NAME = "memory_self_model_adapter"


class MemorySelfModelAdapter(SelfModelSourceAdapter):
    """Memory 数据源 Adapter(Phase 4.2.2 / v1.0)。

    从 MemoryAdapter 检索长期记忆,提取:
    - preferences:        List[Dict]  # 偏好条目
    - extra_entries:      List[SelfModelEntry]  # 记忆事件条目
    - trait_states:       Dict[str, Dict]  # 行为模式派生的稳定特质

    构造参数:
    - memory_adapter: 任意满足 MemoryAdapterSpec 接口的对象
                      若为 None,extract_inputs 返回 {}
    - preference_max:  最多返回的偏好数(默认 20,防止爆炸)
    - entry_max:       最多返回的额外 entry 数(默认 50)
    """

    name: str = MEMORY_SELF_MODEL_ADAPTER_NAME
    schema_version: str = "1.0"

    def __init__(
        self,
        memory_adapter: Optional[Any] = None,
        preference_max: int = 20,
        entry_max: int = 50,
    ) -> None:
        super().__init__()
        self._memory_adapter: Optional[Any] = memory_adapter
        self._preference_max: int = max(1, int(preference_max))
        self._entry_max: int = max(1, int(entry_max))

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_MEMORY

    @property
    def memory_adapter(self) -> Optional[Any]:
        return self._memory_adapter

    def set_memory_adapter(self, memory_adapter: Optional[Any]) -> None:
        """运行时注入 MemoryAdapter。"""
        self._memory_adapter = memory_adapter

    # --------------------------------------------------------
    # AdapterBase 生命周期
    # --------------------------------------------------------
    def attach(self) -> None:
        self._mark_attached()
        self._cache_health({
            "healthy": True,
            "name": self.name,
            "schema_version": self.schema_version,
            "source_type": self.source_type,
            "has_memory_adapter": self._memory_adapter is not None,
        })

    # --------------------------------------------------------
    # 核心:extract_inputs
    # --------------------------------------------------------
    def extract_inputs(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """从 MemoryAdapter 提取 SelfModel 结构化输入。

        行为:
        1) 尝试调 memory_adapter.retrieve(ctx) 获取 MemoryContext
           (若 memory_adapter 抛异常,将向上传播,由 safe_extract 捕获)
        2) 解析 MemoryContext.items:
           - type=preference → preferences
           - type=event / experience → extra_entries
           - type=pattern / trait → trait_states
        3) 限流:preferences / entries 不超过 max

        Args:
            ctx: Runtime 共享上下文(传递给 memory_adapter.retrieve)

        Returns:
            {
                "preferences":   List[Dict],
                "extra_entries": List[SelfModelEntry],
                "trait_states":  Dict[str, Dict],
            }
        """
        if not self.is_attached:
            return {}

        memory_context: Any = None
        # 注:不再内部 try/except —— 让 memory_adapter 抛出的异常向上传播,
        # 由基类 safe_extract 统一捕获并返回 {}。这样 safe_extract 的 last_error
        # 才能被 Registry 正确追踪。
        if self._memory_adapter is not None and ctx is not None:
            retrieve_method = getattr(
                self._memory_adapter, "retrieve", None
            )
            if retrieve_method is not None and callable(retrieve_method):
                memory_context = retrieve_method(ctx)

        preferences: List[Dict[str, Any]] = []
        extra_entries: List[SelfModelEntry] = []
        trait_states: Dict[str, Dict[str, Any]] = {}

        items = self._extract_items(memory_context)
        for item in items:
            try:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type", "")).lower()
                # preferences (限流)
                if (
                    "preference" in item_type or item.get("key") is not None
                ) and len(preferences) < self._preference_max:
                    pref = self._build_preference(item)
                    if pref is not None:
                        preferences.append(pref)
                # trait_states
                if "trait" in item_type or "pattern" in item_type:
                    trait = self._build_trait_state(item)
                    if trait is not None:
                        trait_states[trait["trait"]] = trait
                # 任何 item 都作为一个 event entry(限流)
                if len(extra_entries) < self._entry_max:
                    extra_entries.append(SelfModelEntry(
                        kind="event" if "event" in item_type else "narrative",
                        summary=str(item.get("summary") or item.get("content", ""))[:200],
                        sources=[str(item.get("id", ""))] if item.get("id") else [],
                        confidence=float(item.get("confidence", 0.4) or 0.4),
                        meta={
                            "source": "memory",
                            "type": item_type or "unknown",
                            "memory_id": item.get("id"),
                        },
                    ))
            except Exception:  # noqa: BLE001
                continue

        return {
            "preferences": preferences,
            "extra_entries": extra_entries,
            "trait_states": trait_states,
        }

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _extract_items(self, memory_context: Any) -> List[Dict[str, Any]]:
        """从 MemoryContext 提取 items 列表。"""
        if memory_context is None:
            return []
        # dict 形态
        if isinstance(memory_context, dict):
            items = memory_context.get("items") or memory_context.get("memories") or []
            if isinstance(items, list):
                return [i for i in items if isinstance(i, dict)]
            return []
        # object 形态
        items_attr = getattr(memory_context, "items", None)
        if isinstance(items_attr, list):
            return [i for i in items_attr if isinstance(i, dict)]
        # 兜底:整个对象当做一个 item
        if hasattr(memory_context, "__dict__"):
            return [dict(memory_context.__dict__)]
        return []

    def _build_preference(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 item 构造 preference Dict。"""
        key = item.get("key") or item.get("preference_key")
        if not key:
            return None
        return {
            "domain": str(item.get("domain") or item.get("category") or "memory"),
            "key": str(key),
            "value": item.get("value") or item.get("content"),
            "evidence_count": int(item.get("evidence_count", 1) or 1),
            "confidence": float(item.get("confidence", 0.4) or 0.4),
            "sources": [str(item.get("id", ""))] if item.get("id") else ["memory"],
        }

    def _build_trait_state(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从 item 构造 trait_state Dict。"""
        name = item.get("trait") or item.get("name") or item.get("key")
        if not name:
            return None
        try:
            return {
                "trait": str(name),
                "current_value": float(item.get("current_value", 0.5) or 0.5),
                "direction": str(item.get("direction", "stable")),
                "stability": float(item.get("stability", 0.3) or 0.3),
                "confidence": float(item.get("confidence", 0.3) or 0.3),
                "sources": ["memory"],
                "last_updated": str(item.get("timestamp", "") or ""),
            }
        except Exception:  # noqa: BLE001
            return None

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def preference_max(self) -> int:
        return self._preference_max

    @property
    def entry_max(self) -> int:
        return self._entry_max


__all__ = [
    "MemorySelfModelAdapter",
    "MEMORY_SELF_MODEL_ADAPTER_NAME",
]
