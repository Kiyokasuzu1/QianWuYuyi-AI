# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_data.py

Phase 4.2.1: Self Model Foundation —— 数据结构层

职责:
- 定义 SelfModel 基础数据契约
- 提供 SelfModelEntry / SelfModelSnapshot 数据类
- 不依赖任何具体 Personality 现有实现
- 不调用 LLM,纯结构化骨架

约束:
- 不 import openai / qwen / llava / yolo / clip
- 不修改 RuntimeContext / Fact / Observation schema
- 不引用 src.personality.self_model_manager / self_model_v3 等现有复杂模块
- 所有字段为结构化数据 + 来源追溯

数据层级:
  SelfModelSnapshot
    ├── identity_id: str              # 唯一身份 id
    ├── created_at: str               # 创建时间(ISO)
    ├── updated_at: str               # 最近更新时间(ISO)
    ├── schema_version: str           # "1.0"
    ├── version: int                  # 单调递增版本号
    │
    ├── identity: Dict[str, Any]      # 身份基础信息(name, archetype, ...)
    ├── core_values: List[Dict]       # 核心价值观(短列表, 来自 Identity Core)
    ├── stable_traits: List[Dict]     # 稳定特质(短列表, 来自 TraitState 长期聚合)
    ├── preferences: List[Dict]       # 偏好(短列表)
    ├── current_state: Dict[str, Any] # 当前运行时人格快照(只读)
    │
    ├── entries: List[SelfModelEntry] # 基础条目(成长/反思/事件)
    ├── counters: Dict[str, int]      # 各类条目计数
    │
    ├── meta: Dict[str, Any]          # 元信息
    └── health: Dict[str, Any]        # 健康度自检
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


SELF_MODEL_FOUNDATION_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ============================================================
# SelfModelEntry —— 基础条目(成长/反思/事件)
# ============================================================


VALID_ENTRY_KINDS = frozenset({
    "growth",        # 成长累积
    "reflection",    # 反思结论
    "trait_change",  # 特质变化
    "event",         # 事件标注
    "preference",    # 偏好固化
    "narrative",     # 第一人称叙事
})


@dataclass
class SelfModelEntry:
    """SelfModel 基础条目 —— Phase 4.2.1 v1.0。

    字段:
    - entry_id:       str                # 唯一 id
    - kind:           str                # 类别(见 VALID_ENTRY_KINDS)
    - summary:        str                # 简短描述
    - sources:        List[str]          # 来源追溯 ID 列表
    - confidence:     float (0~1)        # 置信度
    - created_at:     str                # ISO 时间
    - meta:           Dict[str, Any]     # 附加元信息
    """

    kind: str = "event"
    summary: str = ""
    sources: List[str] = field(default_factory=list)
    confidence: float = 0.5
    meta: Dict[str, Any] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: _new_id("sme"))
    created_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        if self.kind not in VALID_ENTRY_KINDS:
            raise ValueError(
                f"SelfModelEntry.kind must be one of {sorted(VALID_ENTRY_KINDS)}; "
                f"got {self.kind!r}."
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"SelfModelEntry.confidence must be in [0.0, 1.0]; got {self.confidence!r}."
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfModelEntry":
        return cls(**data)


# ============================================================
# SelfModelSnapshot —— 完整快照
# ============================================================


# 基础结构大小限制(防止外部污染)
MAX_ENTRIES = 200
MAX_VALUES = 20
MAX_TRAITS = 50
MAX_PREFERENCES = 50


@dataclass
class SelfModelSnapshot:
    """SelfModel 在某时间点的完整可序列化快照 —— Phase 4.2.1 v1.0。

    字段:
    - identity_id:     str
    - schema_version:  str = "1.0"
    - version:         int                # 单调递增
    - created_at:      str                # ISO
    - updated_at:      str                # ISO
    - identity:        Dict[str, Any]     # 身份基础信息
    - core_values:     List[Dict]         # 核心价值观
    - stable_traits:   List[Dict]         # 稳定特质
    - preferences:     List[Dict]         # 偏好
    - current_state:   Dict[str, Any]     # 当前运行时人格(只读快照)
    - entries:         List[SelfModelEntry]
    - counters:        Dict[str, int]
    - meta:            Dict[str, Any]
    - health:          Dict[str, Any]
    """

    identity: Dict[str, Any] = field(default_factory=dict)
    core_values: List[Dict[str, Any]] = field(default_factory=list)
    stable_traits: List[Dict[str, Any]] = field(default_factory=list)
    preferences: List[Dict[str, Any]] = field(default_factory=list)
    current_state: Dict[str, Any] = field(default_factory=dict)
    entries: List[SelfModelEntry] = field(default_factory=list)
    counters: Dict[str, int] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    health: Dict[str, Any] = field(default_factory=dict)
    identity_id: str = field(default_factory=lambda: _new_id("smf"))
    schema_version: str = SELF_MODEL_FOUNDATION_SCHEMA_VERSION
    version: int = 1
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def __post_init__(self) -> None:
        # 基础大小限制(不强制 truncate, 只校验, 由 foundation 控制)
        if len(self.core_values) > MAX_VALUES:
            raise ValueError(
                f"core_values count {len(self.core_values)} exceeds MAX_VALUES={MAX_VALUES}"
            )
        if len(self.stable_traits) > MAX_TRAITS:
            raise ValueError(
                f"stable_traits count {len(self.stable_traits)} exceeds MAX_TRAITS={MAX_TRAITS}"
            )
        if len(self.preferences) > MAX_PREFERENCES:
            raise ValueError(
                f"preferences count {len(self.preferences)} exceeds MAX_PREFERENCES={MAX_PREFERENCES}"
            )
        if len(self.entries) > MAX_ENTRIES:
            raise ValueError(
                f"entries count {len(self.entries)} exceeds MAX_ENTRIES={MAX_ENTRIES}"
            )

    # --------------------------------------------------------
    # 不可变 / 版本控制
    # --------------------------------------------------------
    def version_bump(self) -> None:
        """版本号 +1,更新时间。"""
        self.version += 1
        self.updated_at = _now_iso()

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_id": self.identity_id,
            "schema_version": self.schema_version,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "identity": dict(self.identity),
            "core_values": list(self.core_values),
            "stable_traits": list(self.stable_traits),
            "preferences": list(self.preferences),
            "current_state": dict(self.current_state),
            "entries": [e.to_dict() for e in self.entries],
            "counters": dict(self.counters),
            "meta": dict(self.meta),
            "health": dict(self.health),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfModelSnapshot":
        entries_data = data.get("entries", []) or []
        entries = [SelfModelEntry.from_dict(e) for e in entries_data]
        return cls(
            identity=dict(data.get("identity", {}) or {}),
            core_values=list(data.get("core_values", []) or []),
            stable_traits=list(data.get("stable_traits", []) or []),
            preferences=list(data.get("preferences", []) or []),
            current_state=dict(data.get("current_state", {}) or {}),
            entries=entries,
            counters=dict(data.get("counters", {}) or {}),
            meta=dict(data.get("meta", {}) or {}),
            health=dict(data.get("health", {}) or {}),
            identity_id=data.get("identity_id") or _new_id("smf"),
            schema_version=data.get("schema_version") or SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
            version=int(data.get("version", 1) or 1),
            created_at=data.get("created_at") or _now_iso(),
            updated_at=data.get("updated_at") or _now_iso(),
        )

    # --------------------------------------------------------
    # 健康度自检
    # --------------------------------------------------------
    def compute_health(self) -> Dict[str, Any]:
        """根据当前快照内容计算健康度指标。"""
        has_identity = bool(self.identity.get("name") or self.identity.get("identity_name"))
        value_count = len(self.core_values)
        trait_count = len(self.stable_traits)
        entry_count = len(self.entries)
        # 简单健康度:0~1 范围
        completeness = min(
            1.0,
            (0.25 if has_identity else 0.0)
            + (0.25 if value_count > 0 else 0.0)
            + (0.25 if trait_count > 0 else 0.0)
            + (0.25 if entry_count > 0 else 0.0),
        )
        return {
            "complete": completeness >= 0.5,
            "completeness": round(completeness, 4),
            "has_identity": has_identity,
            "core_values_count": value_count,
            "stable_traits_count": trait_count,
            "preferences_count": len(self.preferences),
            "entries_count": entry_count,
            "schema_version": self.schema_version,
            "version": self.version,
        }

    # --------------------------------------------------------
    # 比较 / 调试
    # --------------------------------------------------------
    def diff(self, other: "SelfModelSnapshot") -> Dict[str, Any]:
        """与另一个快照对比,返回差异摘要(用于审计 / 测试)。"""
        if not isinstance(other, SelfModelSnapshot):
            raise TypeError(
                f"other must be SelfModelSnapshot, got {type(other).__name__}"
            )
        return {
            "self_version": self.version,
            "other_version": other.version,
            "version_diff": self.version - other.version,
            "entries_diff": len(self.entries) - len(other.entries),
            "values_diff": len(self.core_values) - len(other.core_values),
            "traits_diff": len(self.stable_traits) - len(other.stable_traits),
            "preferences_diff": len(self.preferences) - len(other.preferences),
        }


__all__ = [
    "SELF_MODEL_FOUNDATION_SCHEMA_VERSION",
    "SelfModelEntry",
    "SelfModelSnapshot",
    "VALID_ENTRY_KINDS",
    "MAX_ENTRIES",
    "MAX_VALUES",
    "MAX_TRAITS",
    "MAX_PREFERENCES",
]
