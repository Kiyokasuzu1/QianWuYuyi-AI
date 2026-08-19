# -*- coding: utf-8 -*-
# src/runtime/self_model/personality_self_model_adapter.py
"""
Phase 4.2.2: PersonalitySelfModelAdapter —— Personality 数据源 Adapter

职责:
- 桥接 Runtime → PersonalitySelfModelAdapter → PersonalityAdapter
- 从 PersonalityAdapter 提取 personality_snapshot,生成 SelfModel 输入
- 不修改 Personality 核心模块(PersonalityResolver)
- 不直接 import PersonalityResolver

数据流:
    Runtime → PersonalitySelfModelAdapter.extract_inputs(ctx)
        → PersonalityAdapter.snapshot() → PersonalityVector
        → SelfModelFoundation.build({
              "trait_states": {...},
              "current_state": {...},
              "identity_overrides": {...},
          })

约束:
- 不修改 Personality 核心模块
- 不直接 import src.personality.*
- 仅使用 PersonalityAdapter 暴露的接口(snapshot)
- 任何异常被静默吞掉,返回 {}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_source_adapter import (
    SelfModelSourceAdapter,
    SOURCE_TYPE_PERSONALITY,
)


logger = logging.getLogger(__name__)


PERSONALITY_SELF_MODEL_ADAPTER_NAME = "personality_self_model_adapter"


class PersonalitySelfModelAdapter(SelfModelSourceAdapter):
    """Personality 数据源 Adapter(Phase 4.2.2 / v1.0)。

    从 PersonalityAdapter 提取 SelfModel 输入:
    - trait_states:      Dict[str, Dict]  # 来自 personality_vector.traits
    - current_state:     Dict[str, Any]   # 完整 personality snapshot
    - identity_overrides:Dict[str, Any]   # 来自 identity / core_value
    - core_values:       List[Dict]       # 来自 core_value

    构造参数:
    - personality_adapter: 任意满足 PersonalityAdapter 接口的对象
                          (如 PersonalityAdapterImpl 或外部测试桩)
                          若为 None,extract_inputs 返回 {}
    """

    name: str = PERSONALITY_SELF_MODEL_ADAPTER_NAME
    schema_version: str = "1.0"

    def __init__(self, personality_adapter: Optional[Any] = None) -> None:
        super().__init__()
        self._personality_adapter: Optional[Any] = personality_adapter
        self._last_snapshot: Optional[Any] = None

    @property
    def source_type(self) -> str:
        return SOURCE_TYPE_PERSONALITY

    @property
    def personality_adapter(self) -> Optional[Any]:
        return self._personality_adapter

    def set_personality_adapter(
        self, personality_adapter: Optional[Any]
    ) -> None:
        """运行时注入 PersonalityAdapter。"""
        self._personality_adapter = personality_adapter

    @property
    def last_snapshot(self) -> Optional[Any]:
        return self._last_snapshot

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
            "has_personality_adapter": self._personality_adapter is not None,
        })

    # --------------------------------------------------------
    # 核心:extract_inputs
    # --------------------------------------------------------
    def extract_inputs(self, ctx: Optional[Any] = None) -> Dict[str, Any]:
        """从 PersonalityAdapter 提取 SelfModel 结构化输入。

        行为:
        1) 调 personality_adapter.snapshot() 获取当前 personality vector
        2) 解析 traits → trait_states
        3) 解析 core_value → core_values
        4) 解析 identity / name → identity_overrides
        5) 整体作为 current_state

        Args:
            ctx: Runtime 共享上下文(暂未直接使用,保留扩展)

        Returns:
            {
                "trait_states":       Dict[str, Dict],
                "current_state":      Dict[str, Any],
                "identity_overrides": Dict[str, Any],
                "core_values":        List[Dict],
            }
        """
        if not self.is_attached:
            return {}

        snapshot: Any = None
        # 注:不再内部 try/except —— 让 personality_adapter 抛出的异常向上传播,
        # 由基类 safe_extract 统一捕获并返回 {}。这样 Registry 才能正确追踪错误。
        if self._personality_adapter is not None:
            snapshot_method = getattr(
                self._personality_adapter, "snapshot", None
            )
            if snapshot_method is not None and callable(snapshot_method):
                snapshot = snapshot_method()

        self._last_snapshot = snapshot
        return self._parse_snapshot(snapshot)

    # --------------------------------------------------------
    # 内部:snapshot 解析
    # --------------------------------------------------------
    def _parse_snapshot(self, snapshot: Any) -> Dict[str, Any]:
        """解析 personality snapshot 为 SelfModel 输入 Dict。"""
        if snapshot is None:
            return {}

        # 1) 标准化为 Dict
        norm = self._normalize_snapshot(snapshot)
        if not isinstance(norm, dict):
            return {}

        trait_states: Dict[str, Dict[str, Any]] = {}
        core_values: List[Dict[str, Any]] = []
        identity_overrides: Dict[str, Any] = {}

        # 2) 解析 traits
        traits = norm.get("traits") or norm.get("trait_states") or {}
        if isinstance(traits, dict):
            for name, t in traits.items():
                if not isinstance(t, (dict, int, float)):
                    continue
                if isinstance(t, dict):
                    trait_states[str(name)] = {
                        "current_value": float(t.get("current_value", t.get("value", 0.5)) or 0.5),
                        "direction": str(t.get("direction", "stable")),
                        "stability": float(t.get("stability", 0.5) or 0.5),
                        "confidence": float(t.get("confidence", 0.5) or 0.5),
                        "sources": ["personality"],
                        "last_updated": str(t.get("last_updated", "") or ""),
                    }
                else:
                    trait_states[str(name)] = {
                        "current_value": float(t),
                        "direction": "stable",
                        "stability": 0.5,
                        "confidence": 0.5,
                        "sources": ["personality"],
                        "last_updated": "",
                    }
        elif isinstance(traits, list):
            for t in traits:
                if not isinstance(t, dict):
                    continue
                name = t.get("trait") or t.get("name")
                if not name:
                    continue
                trait_states[str(name)] = {
                    "current_value": float(t.get("current_value", t.get("value", 0.5)) or 0.5),
                    "direction": str(t.get("direction", "stable")),
                    "stability": float(t.get("stability", 0.5) or 0.5),
                    "confidence": float(t.get("confidence", 0.5) or 0.5),
                    "sources": ["personality"],
                    "last_updated": "",
                }

        # 3) 解析 core_values / core_value
        cv_raw = (
            norm.get("core_values")
            or norm.get("core_value")
            or norm.get("values")
            or []
        )
        if isinstance(cv_raw, list):
            for v in cv_raw:
                if not isinstance(v, dict):
                    continue
                key = v.get("key") or v.get("name") or v.get("value_id")
                if not key:
                    continue
                core_values.append({
                    "key": str(key),
                    "label": str(v.get("label") or v.get("name") or key),
                    "weight": float(v.get("weight", 0.7) or 0.7),
                    "confidence": float(v.get("confidence", 0.7) or 0.7),
                    "source": "personality",
                })
        elif isinstance(cv_raw, dict):
            for key, v in cv_raw.items():
                if isinstance(v, (int, float)):
                    core_values.append({
                        "key": str(key),
                        "label": str(key),
                        "weight": float(v),
                        "confidence": 0.7,
                        "source": "personality",
                    })

        # 4) 解析 identity overrides
        ident = norm.get("identity") or norm.get("meta") or {}
        if isinstance(ident, dict):
            name = ident.get("name")
            if name:
                identity_overrides["name"] = str(name)
            archetype = ident.get("archetype")
            if archetype:
                identity_overrides["archetype"] = str(archetype)
            display_name = ident.get("display_name")
            if display_name:
                identity_overrides["display_name"] = str(display_name)

        return {
            "trait_states": trait_states,
            "current_state": dict(norm),
            "identity_overrides": identity_overrides,
            "core_values": core_values,
        }

    def _normalize_snapshot(self, snapshot: Any) -> Optional[Dict[str, Any]]:
        """把 snapshot 各种形态标准化为 Dict。"""
        if isinstance(snapshot, dict):
            return snapshot
        if hasattr(snapshot, "to_dict") and callable(snapshot.to_dict):
            try:
                d = snapshot.to_dict()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        if hasattr(snapshot, "get_all") and callable(snapshot.get_all):
            try:
                d = snapshot.get_all()
                if isinstance(d, dict):
                    return d
            except Exception:  # noqa: BLE001
                pass
        if hasattr(snapshot, "__dict__"):
            try:
                return dict(snapshot.__dict__)
            except Exception:  # noqa: BLE001
                return None
        return None


__all__ = [
    "PersonalitySelfModelAdapter",
    "PERSONALITY_SELF_MODEL_ADAPTER_NAME",
]
