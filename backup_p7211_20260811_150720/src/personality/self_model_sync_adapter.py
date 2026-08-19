"""
Phase 6.3: SelfModelSyncAdapter

职责：
- 桥接 legacy SelfModelStore（v1.2/v3 数据世界）与 runtime SelfModelAdapter（v6.1 数据世界）
- 仅提供统一读视图（combined view）
- 不写任何一方；不创建第二事实来源
- 不复制大量数据；不持有数据所有权

约束：
- legacy 数据所有权归 SelfModelStore
- runtime 数据所有权归 SelfModelAdapter
- 本 Adapter 仅做 view 聚合

数据流向：

    ┌────────────────────┐         ┌────────────────────┐
    │ SelfModelStore     │  read   │ SelfModelAdapter   │
    │ (legacy dict/v3)   │ ───────→│ (Phase 6.1)        │
    │                    │         │                    │
    │ - identity         │         │ - SelfBeliefStore  │
    │ - traits/values    │         │ - SelfHistory      │
    │ - narratives       │         │ - SelfReflection   │
    └────────────────────┘         └────────────────────┘
              ↑                                ↑
              └──────── SelfModelSyncAdapter ─┘
                       (combined view only)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约
# ============================================================

class SyncView:
    """
    SelfModelSyncAdapter 输出的统一视图（只读，不可写）。

    Fields:
        identity: legacy identity 文本
        traits: legacy traits dict
        values: legacy values dict
        beliefs: runtime SelfBelief dicts
        history: runtime SelfHistoryEvent dicts
        reflections: runtime SelfReflectionNote dicts
        narratives: legacy narrative texts (string)
        source: dict[str, str] 各字段来源标记
            - "legacy" | "runtime" | "both" | "none"
    """
    __slots__ = (
        "identity",
        "traits",
        "values",
        "beliefs",
        "history",
        "reflections",
        "narratives",
        "source",
    )

    def __init__(
        self,
        identity: str,
        traits: Dict[str, float],
        values: Dict[str, float],
        beliefs: List[Dict[str, Any]],
        history: List[Dict[str, Any]],
        reflections: List[Dict[str, Any]],
        narratives: List[str],
        source: Dict[str, str],
    ) -> None:
        self.identity = identity
        self.traits = traits
        self.values = values
        self.beliefs = beliefs
        self.history = history
        self.reflections = reflections
        self.narratives = narratives
        self.source = source

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity": self.identity,
            "traits": dict(self.traits or {}),
            "values": dict(self.values or {}),
            "beliefs": list(self.beliefs or []),
            "history": list(self.history or []),
            "reflections": list(self.reflections or []),
            "narratives": list(self.narratives or []),
            "source": dict(self.source or {}),
        }

    def is_empty(self) -> bool:
        """是否完全没有数据（用于 fallback 决策）"""
        return (
            not self.identity
            and not self.traits
            and not self.values
            and not self.beliefs
            and not self.history
            and not self.reflections
            and not self.narratives
        )


# ============================================================
# SelfModelSyncAdapter
# ============================================================

class SelfModelSyncAdapter:
    """
    SelfModel legacy ↔ runtime 同步桥接器。

    用法：
        sync = SelfModelSyncAdapter(legacy_store, runtime_adapter)
        view = sync.get_combined_view()
        # view.identity 来自 legacy
        # view.beliefs/history/reflections 来自 runtime

    设计原则：
    1. 不修改任何一方数据（no write-through）
    2. 不持有数据所有权（no caching beyond request scope）
    3. 不创建第二事实来源（每次调用从双方实时读取）
    4. 异常隔离：任何一方失败不影响另一方读取
    """

    def __init__(
        self,
        legacy_store: Optional[Any] = None,
        runtime_adapter: Optional[Any] = None,
    ) -> None:
        self._legacy_store = legacy_store
        self._runtime_adapter = runtime_adapter
        # Sync metadata
        self._last_view_count: int = 0
        self._last_error: Optional[str] = None

    # ============================================================
    # 公共 API: attachment
    # ============================================================

    def attach_legacy(self, legacy_store: Any) -> None:
        """绑定 legacy store（运行时注入；保持 backward compat）"""
        self._legacy_store = legacy_store

    def attach_runtime(self, runtime_adapter: Any) -> None:
        """绑定 runtime adapter（运行时注入）"""
        self._runtime_adapter = runtime_adapter

    def has_legacy(self) -> bool:
        return self._legacy_store is not None

    def has_runtime(self) -> bool:
        return self._runtime_adapter is not None

    # ============================================================
    # 公共 API: combined view
    # ============================================================

    def get_combined_view(
        self,
        max_beliefs: int = 5,
        max_history: int = 3,
        max_reflections: int = 3,
        max_narratives: int = 3,
    ) -> SyncView:
        """
        返回统一视图（combined view）。

        字段来源：
            - identity / traits / values / narratives: 来自 legacy
            - beliefs / history / reflections: 来自 runtime
        """
        identity = ""
        traits: Dict[str, float] = {}
        values: Dict[str, float] = {}
        narratives: List[str] = []
        beliefs: List[Dict[str, Any]] = []
        history: List[Dict[str, Any]] = []
        reflections: List[Dict[str, Any]] = []
        source: Dict[str, str] = {
            "identity": "none",
            "traits": "none",
            "values": "none",
            "narratives": "none",
            "beliefs": "none",
            "history": "none",
            "reflections": "none",
        }
        # 1) legacy 数据
        try:
            if self._legacy_store is not None:
                v3 = self._legacy_store.get_active_self_model() if hasattr(self._legacy_store, "get_active_self_model") else None
                if v3 is not None:
                    identity = str(getattr(v3, "identity", "") or "")
                    traits = dict(getattr(v3, "traits", {}) or {})
                    values = dict(getattr(v3, "values", {}) or {})
                    # narratives
                    raw_narratives = list(getattr(v3, "narrative_items", []) or [])
                    narratives = [
                        n.text if hasattr(n, "text") else str(n)
                        for n in raw_narratives[-max_narratives:]
                        if n
                    ]
                    source["identity"] = "legacy"
                    source["traits"] = "legacy" if traits else "none"
                    source["values"] = "legacy" if values else "none"
                    source["narratives"] = "legacy" if narratives else "none"
        except Exception as e:
            logger.warning(f"SelfModelSyncAdapter: legacy read failed: {e}")
            self._last_error = f"legacy_read_failed: {e}"
        # 2) runtime 数据
        runtime_ran = False
        try:
            if self._runtime_adapter is not None:
                runtime_ran = True
                # beliefs
                try:
                    beliefs_obj = self._runtime_adapter.get_beliefs() if hasattr(self._runtime_adapter, "get_beliefs") else None
                    if beliefs_obj is not None and hasattr(beliefs_obj, "all"):
                        all_beliefs = list(beliefs_obj.all() or [])
                        for b in all_beliefs[-max_beliefs:]:
                            try:
                                beliefs.append(b.to_dict() if hasattr(b, "to_dict") else dict(b))
                            except Exception:
                                continue
                    elif beliefs_obj is not None and hasattr(beliefs_obj, "__iter__"):
                        # backup: 直接迭代
                        for b in list(beliefs_obj)[-max_beliefs:]:
                            try:
                                beliefs.append(b.to_dict() if hasattr(b, "to_dict") else dict(b))
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"SyncAdapter: beliefs read failed: {e}")
                # history
                try:
                    history_obj = self._runtime_adapter.get_history() if hasattr(self._runtime_adapter, "get_history") else None
                    if history_obj is not None and hasattr(history_obj, "all"):
                        all_history = list(history_obj.all() or [])
                        for ev in all_history[-max_history:]:
                            try:
                                history.append(ev.to_dict() if hasattr(ev, "to_dict") else dict(ev))
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"SyncAdapter: history read failed: {e}")
                # reflections
                try:
                    ref_obj = self._runtime_adapter.get_reflections() if hasattr(self._runtime_adapter, "get_reflections") else None
                    if ref_obj is not None and hasattr(ref_obj, "all"):
                        all_refs = list(ref_obj.all() or [])
                        for n in all_refs[-max_reflections:]:
                            try:
                                reflections.append(n.to_dict() if hasattr(n, "to_dict") else dict(n))
                            except Exception:
                                continue
                except Exception as e:
                    logger.debug(f"SyncAdapter: reflections read failed: {e}")
        except Exception as e:
            logger.warning(f"SelfModelSyncAdapter: runtime read failed: {e}")
            self._last_error = f"runtime_read_failed: {e}"

        # 3) 设置 source（基于最终读取结果；放在 try 块外确保不被异常短路）
        if runtime_ran:
            source["beliefs"] = "runtime" if beliefs else "none"
            source["history"] = "runtime" if history else "none"
            source["reflections"] = "runtime" if reflections else "none"

        self._last_view_count += 1
        return SyncView(
            identity=identity,
            traits=traits,
            values=values,
            beliefs=beliefs,
            history=history,
            reflections=reflections,
            narratives=narratives,
            source=source,
        )

    def get_combined_summary(self) -> Dict[str, Any]:
        """
        返回轻量级摘要（用于 health check / 监控）。

        不返回实际数据内容，只返回计数。
        """
        try:
            view = self.get_combined_view(
                max_beliefs=10000, max_history=10000,
                max_reflections=10000, max_narratives=10000,
            )
            return {
                "has_legacy": self.has_legacy(),
                "has_runtime": self.has_runtime(),
                "identity_present": bool(view.identity),
                "traits_count": len(view.traits),
                "values_count": len(view.values),
                "beliefs_count": len(view.beliefs),
                "history_count": len(view.history),
                "reflections_count": len(view.reflections),
                "narratives_count": len(view.narratives),
                "source": view.source,
            }
        except Exception as e:
            return {
                "has_legacy": self.has_legacy(),
                "has_runtime": self.has_runtime(),
                "error": str(e),
            }

    # ============================================================
    # 状态查询
    # ============================================================

    def get_status(self) -> Dict[str, Any]:
        return {
            "has_legacy": self.has_legacy(),
            "has_runtime": self.has_runtime(),
            "view_call_count": self._last_view_count,
            "last_error": self._last_error,
        }


# ============================================================
# 便捷函数
# ============================================================

def create_sync_adapter(
    legacy_store: Optional[Any] = None,
    runtime_adapter: Optional[Any] = None,
) -> SelfModelSyncAdapter:
    """
    工厂函数：创建 SelfModelSyncAdapter 实例。
    允许 None（保持空 sync，仅作为占位）。
    """
    return SelfModelSyncAdapter(
        legacy_store=legacy_store,
        runtime_adapter=runtime_adapter,
    )
