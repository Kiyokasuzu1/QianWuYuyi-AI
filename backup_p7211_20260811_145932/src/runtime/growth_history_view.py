# -*- coding: utf-8 -*-
"""
Phase 3.5.2 P2.1: GrowthHistoryView。

把 SelfModel history（SelfHistoryEvent 审计日志）投影成 PersonalityGrowthHistory 兼容接口。

设计原则：
- 单一事实源：SelfHistory 是唯一持久化数据，GrowthHistoryView 不复制、不缓存
- 只读视图：不提供 add()（写入仍走 SelfModelAdapter.apply_pcr）
- 有损投影：SelfHistoryEvent 只有 delta + summary，缺少 before/after/momentum；
  View 在转换时补充合理的默认值
- 接口兼容：实现 PersonalityGrowthHistory 的 all/latest/count/get_by_dimension 等方法，
  让 PersonalityResolver 和 SelfModelStore 无差别使用

数据流：
    SelfHistory (持久化审计层)
         ↓ read-only projection
    GrowthHistoryView (本模块)
         ↓ duck-typed interface
    PersonalityResolver.growth_history / SelfModelStore.should_update()
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _event_to_growth_record(event: Any) -> Dict[str, Any]:
    """
    把一条 SelfHistoryEvent 转换成 PersonalityGrowthRecord 格式。

    字段映射：
    - record_id     ← event_id
    - timestamp     ← timestamp
    - trigger_events← [source_id] （如果有）
    - changes       ← {trait: {delta: d, reason: summary}} for each affected_trait
    - affected_dimensions ← list(affected_traits.keys())
    - meaning       ← summary
    - narrative     ← summary
    - confidence    ← metadata.get("confidence", 0.7)
    - validation_count ← 1
    - growth_level  ← "trait"（因为 affected_traits 存在说明是人格变化）
    """
    # ---- 提取 affected_traits ----
    try:
        affected = event.affected_traits if hasattr(event, "affected_traits") else None
    except Exception:
        affected = None
    if affected is None and isinstance(event, dict):
        affected = event.get("affected_traits")
    if not isinstance(affected, dict):
        affected = {}

    # ---- 提取基础字段 ----
    def _get(key, default=""):
        if hasattr(event, key):
            return getattr(event, key, default)
        if isinstance(event, dict):
            return event.get(key, default)
        return default

    event_id = _get("event_id", "")
    timestamp = _get("timestamp", "")
    source_id = _get("source_id", "")
    source_type = _get("source_type", "")
    summary = _get("summary", "")
    event_type = _get("event_type", "")

    # ---- metadata ----
    metadata = _get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    # ---- 构建 changes ----
    changes: Dict[str, Dict[str, Any]] = {}
    for trait, delta in affected.items():
        try:
            delta_f = float(delta)
        except (TypeError, ValueError):
            continue
        changes[trait] = {
            "delta": delta_f,
            "reason": summary or f"{source_type} applied",
        }

    # ---- 构建 record ----
    return {
        "record_id": event_id or f"view_{id(event)}",
        "timestamp": timestamp,
        "trigger_events": [source_id] if source_id else [],
        "changes": changes,
        "affected_dimensions": list(affected.keys()),
        "meaning": summary or f"{event_type} from {source_type}",
        "narrative": summary,
        "confidence": float(metadata.get("confidence", 0.7)),
        "validation_count": 1,
        "growth_level": "trait" if affected else "context",
    }


class GrowthHistoryView:
    """
    SelfHistory 的只读投影，兼容 PersonalityGrowthHistory 接口。

    用法：
        view = GrowthHistoryView(adapter)
        view.all()       # List[PersonalityGrowthRecord]
        view.count()     # int
        view.latest()    # Optional[PersonalityGrowthRecord]
    """

    def __init__(self, adapter: Any = None) -> None:
        """
        Args:
            adapter: SelfModelAdapter 实例（已调用 load_state）。
                     传入 None 时所有方法返回空结果。
        """
        self._adapter = adapter

    # ============================================================
    # 内部：从 adapter 读取 events
    # ============================================================
    def _events(self) -> List[Any]:
        """从 adapter 获取 SelfHistoryEvent 列表。"""
        if self._adapter is None:
            return []
        try:
            history = (
                self._adapter.get_history()
                if hasattr(self._adapter, "get_history")
                and callable(getattr(self._adapter, "get_history"))
                else None
            )
        except Exception:
            return []
        if history is None:
            return []
        try:
            events = (
                history.all()
                if hasattr(history, "all") and callable(getattr(history, "all"))
                else []
            )
        except Exception:
            return []
        return list(events) if events else []

    # ============================================================
    # PersonalityGrowthHistory 兼容接口（只读）
    # ============================================================
    def all(self) -> List[Dict[str, Any]]:
        """返回所有成长记录（从 SelfHistory 投影）。"""
        return [_event_to_growth_record(e) for e in self._events()]

    def latest(self) -> Optional[Dict[str, Any]]:
        """获取最近一条成长记录。"""
        events = self._events()
        if not events:
            return None
        return _event_to_growth_record(events[-1])

    def count(self) -> int:
        """记录总数。"""
        return len(self._events())

    def get_by_dimension(self, trait: str) -> List[Dict[str, Any]]:
        """获取影响某个维度的所有成长记录。"""
        return [
            r for r in self.all()
            if trait in r.get("affected_dimensions", [])
        ]

    def get_by_level(self, level: str) -> List[Dict[str, Any]]:
        """获取指定成长层级的所有记录。"""
        return [
            r for r in self.all()
            if r.get("growth_level") == level
        ]

    def get_high_confidence(self, threshold: float = 0.7) -> List[Dict[str, Any]]:
        """获取高置信度的成长记录。"""
        return [
            r for r in self.all()
            if r.get("confidence", 0) >= threshold
        ]

    # ============================================================
    # 不支持的方法（写入仍走 SelfModelAdapter.apply_pcr）
    # ============================================================
    def add(self, record: Any) -> bool:
        """不支持：GrowthHistoryView 是只读视图。写入请通过 SelfModelAdapter.apply_pcr。"""
        return False


def inject_growth_history_view(resolver: Any, adapter: Any) -> bool:
    """
    把 GrowthHistoryView 注入 PersonalityResolver.growth_history。

    兼容策略（按优先级）：
    1) 如果 resolver 有 set_growth_history(view)，优先调用
    2) 否则直接 setattr(resolver, "growth_history", view)

    Args:
        resolver: PersonalityResolver 实例
        adapter: SelfModelAdapter 实例（已 load_state）

    Returns:
        bool: 注入是否成功
    """
    if resolver is None or adapter is None:
        return False
    try:
        view = GrowthHistoryView(adapter)
        # Strategy 1: public setter
        setter = getattr(resolver, "set_growth_history", None)
        if callable(setter):
            try:
                setter(view)
                return True
            except Exception:
                pass
        # Strategy 2: direct attribute
        setattr(resolver, "growth_history", view)
        return True
    except Exception:
        return False
