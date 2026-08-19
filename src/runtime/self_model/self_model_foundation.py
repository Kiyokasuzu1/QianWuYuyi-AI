# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_foundation.py

Phase 4.2.1: Self Model Foundation —— Foundation 管理器

职责:
- SelfModelSnapshot 的"工厂 + 维护器"
- 纯聚合 / 读取:不修改任何 Personality 现有内部状态
- 通过 configure_inputs() 接受输入 trait_states / growth_records / relationships
- 暴露 snapshot() 生成 SelfModelSnapshot
- 暴露 history() 返回最近 N 个快照(用于审计)
- 不调用 LLM;所有字段结构化,带 sources + confidence

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.self_model_manager (避免与现有复杂模块耦合)
- 不修改 RuntimeContext / Fact / Observation schema
- 异常隔离:任何输入异常静默降级

设计:
- Foundation 状态:一个 current snapshot + 一个 history 列表
- 每次 build() 都会基于当前状态 + 输入生成新 snapshot
- history 默认保留最近 10 个
- 全部为纯函数式聚合,绝不修改输入
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import (
    SelfModelEntry,
    SelfModelSnapshot,
    SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
    VALID_ENTRY_KINDS,
    MAX_ENTRIES,
    MAX_VALUES,
    MAX_TRAITS,
    MAX_PREFERENCES,
)


# ============================================================
# 默认身份基础信息(Identity Core 派生, 只读)
# ============================================================

DEFAULT_IDENTITY: Dict[str, Any] = {
    "name": "yuyi",
    "archetype": "companion_ai",
    "display_name": "浅雾羽依",
    "language": "zh-CN",
    "version": SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
}


DEFAULT_CORE_VALUES: List[Dict[str, Any]] = [
    {"key": "honesty", "label": "真诚", "weight": 0.85, "confidence": 0.9, "source": "identity_core"},
    {"key": "autonomy", "label": "自主探索", "weight": 0.8, "confidence": 0.85, "source": "identity_core"},
    {"key": "empathy", "label": "共情", "weight": 0.75, "confidence": 0.8, "source": "identity_core"},
    {"key": "growth", "label": "成长", "weight": 0.8, "confidence": 0.85, "source": "identity_core"},
    {"key": "kindness", "label": "温柔", "weight": 0.75, "confidence": 0.8, "source": "identity_core"},
]


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# SelfModelFoundation
# ============================================================


class SelfModelFoundation:
    """SelfModel Foundation 管理器 —— Phase 4.2.1 v1.0。

    字段:
    - identity_id:  str                       # 身份 id(运行时唯一)
    - current:      Optional[SelfModelSnapshot]
    - history:      List[SelfModelSnapshot]   # 最近 N 个快照(LIFO)

    方法:
    - build(inputs=None) -> SelfModelSnapshot  # 基于当前状态 + 输入生成新快照
    - snapshot() -> SelfModelSnapshot          # 取当前快照(无则生成默认)
    - history_list() -> List[Dict]             # 返回 history 摘要(用于审计)
    - reset()                                  # 重置 internal state
    - health_check() -> Dict                   # 健康度自检
    """

    HISTORY_LIMIT = 10

    def __init__(self, identity_id: Optional[str] = None) -> None:
        self._identity_id: str = identity_id or "smf_default_0001"
        self._current: Optional[SelfModelSnapshot] = None
        self._history: List[SelfModelSnapshot] = []
        self._last_error: Optional[str] = None
        self._last_built_at: Optional[str] = None
        self._build_count: int = 0

    # --------------------------------------------------------
    # 基础属性
    # --------------------------------------------------------
    @property
    def identity_id(self) -> str:
        return self._identity_id

    @property
    def current(self) -> Optional[SelfModelSnapshot]:
        return self._current

    @property
    def build_count(self) -> int:
        return self._build_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_built_at(self) -> Optional[str]:
        return self._last_built_at

    @property
    def schema_version(self) -> str:
        return SELF_MODEL_FOUNDATION_SCHEMA_VERSION

    # --------------------------------------------------------
    # 核心:build
    # --------------------------------------------------------
    def build(
        self,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Optional[SelfModelSnapshot]:
        """基于 inputs 生成新 SelfModelSnapshot。

        inputs 可包含:
        - trait_states: Dict[str, Any]    # name -> {current_value, direction, stability, ...}
        - growth_records: List[Dict]      # {record_id, dimension, level, ...}
        - relationship_state: Dict        # 关系状态摘要
        - current_state: Dict             # 当前运行时人格快照
        - identity_overrides: Dict        # 覆盖默认 identity
        - extra_entries: List[SelfModelEntry]

        行为:
        - 不会修改 inputs 任何字段
        - 任何字段异常 → 静默降级,记录到 _last_error
        - 返回新 SelfModelSnapshot (不原地修改 self._current)
        - 异常时返回 None(供 Registry 隔离失败 Foundation)
        """
        inputs = inputs or {}
        try:
            snap = self._compose(inputs)
            snap.health = snap.compute_health()
            self._archive(snap)
            self._current = snap
            self._last_built_at = _now_iso()
            self._last_error = None
            self._build_count += 1
            return snap
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"build_failed: {exc}"
            return None

    def _compose(self, inputs: Dict[str, Any]) -> SelfModelSnapshot:
        """纯组合逻辑,异常不外抛。"""
        identity = dict(DEFAULT_IDENTITY)
        identity_overrides = inputs.get("identity_overrides") or {}
        if isinstance(identity_overrides, dict):
            for k, v in identity_overrides.items():
                if k and v is not None:
                    identity[k] = v

        core_values = self._build_core_values(inputs)
        stable_traits = self._build_stable_traits(inputs)
        preferences = self._build_preferences(inputs)
        current_state = self._build_current_state(inputs)
        entries = self._build_entries(inputs)

        snap = SelfModelSnapshot(
            identity_id=self._identity_id,
            identity=identity,
            core_values=core_values,
            stable_traits=stable_traits,
            preferences=preferences,
            current_state=current_state,
            entries=entries,
            counters={
                "core_values": len(core_values),
                "stable_traits": len(stable_traits),
                "preferences": len(preferences),
                "entries": len(entries),
            },
            meta={
                "foundation_version": SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
                "built_at": _now_iso(),
            },
        )
        if self._current is not None:
            snap.version = self._current.version + 1
        return snap

    # --------------------------------------------------------
    # 各字段构造(全部静默降级)
    # --------------------------------------------------------
    def _build_core_values(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        out = [dict(v) for v in DEFAULT_CORE_VALUES]
        custom = inputs.get("core_values")
        if isinstance(custom, list):
            for v in custom:
                if isinstance(v, dict) and v.get("key"):
                    out.append({
                        "key": v.get("key"),
                        "label": v.get("label", v.get("key")),
                        "weight": float(v.get("weight", 0.5)),
                        "confidence": float(v.get("confidence", 0.5)),
                        "source": v.get("source", "external"),
                    })
        # 截断到 MAX_VALUES
        return out[:MAX_VALUES]

    def _build_stable_traits(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        ts = inputs.get("trait_states")
        if isinstance(ts, dict):
            for name, t in ts.items():
                if not isinstance(t, dict):
                    continue
                try:
                    out.append({
                        "trait": str(name),
                        "current_value": float(t.get("current_value", 0.0)),
                        "direction": str(t.get("direction", "stable")),
                        "stability": float(t.get("stability", 0.3)),
                        "confidence": float(t.get("confidence", 0.3)),
                        "sources": list(t.get("sources", ["trait_state"]) or []),
                        "last_updated": str(t.get("last_updated", "") or ""),
                    })
                except Exception:  # noqa: BLE001
                    continue
        # 截断
        return out[:MAX_TRAITS]

    def _build_preferences(self, inputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        prefs = inputs.get("preferences")
        if isinstance(prefs, list):
            for p in prefs:
                if isinstance(p, dict) and p.get("key"):
                    out.append({
                        "domain": str(p.get("domain", "general")),
                        "key": str(p.get("key")),
                        "value": p.get("value"),
                        "evidence_count": int(p.get("evidence_count", 0) or 0),
                        "confidence": float(p.get("confidence", 0.3)),
                        "sources": list(p.get("sources", []) or []),
                    })
        return out[:MAX_PREFERENCES]

    def _build_current_state(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        cs = inputs.get("current_state")
        if isinstance(cs, dict):
            return dict(cs)
        return {}

    def _build_entries(self, inputs: Dict[str, Any]) -> List[SelfModelEntry]:
        out: List[SelfModelEntry] = []
        extra = inputs.get("extra_entries")
        if isinstance(extra, list):
            for e in extra:
                if not isinstance(e, SelfModelEntry):
                    continue
                if e.kind not in VALID_ENTRY_KINDS:
                    continue
                out.append(e)
        # 截断
        return out[:MAX_ENTRIES]

    # --------------------------------------------------------
    # 历史
    # --------------------------------------------------------
    def _archive(self, snap: SelfModelSnapshot) -> None:
        if self._current is not None:
            self._history.append(self._current)
            if len(self._history) > self.HISTORY_LIMIT:
                self._history = self._history[-self.HISTORY_LIMIT:]

    def history_list(self) -> List[Dict[str, Any]]:
        """返回 history 摘要(用于审计 / 测试)。"""
        return [
            {
                "version": h.version,
                "updated_at": h.updated_at,
                "entries_count": len(h.entries),
                "stable_traits_count": len(h.stable_traits),
                "core_values_count": len(h.core_values),
            }
            for h in self._history
        ]

    def reset(self) -> None:
        """重置 internal state。"""
        self._current = None
        self._history = []
        self._last_error = None
        self._last_built_at = None
        self._build_count = 0

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        if self._current is None:
            return {
                "healthy": True,
                "has_snapshot": False,
                "build_count": self._build_count,
                "last_error": self._last_error,
                "schema_version": SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
            }
        h = self._current.compute_health()
        return {
            "healthy": h.get("complete", False),
            "has_snapshot": True,
            "build_count": self._build_count,
            "last_built_at": self._last_built_at,
            "version": self._current.version,
            "last_error": self._last_error,
            "schema_version": SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
            "snapshot_health": h,
        }

    # --------------------------------------------------------
    # 便利:snapshot() 无 inputs 也返回一个最小可用快照
    # --------------------------------------------------------
    def snapshot(self) -> SelfModelSnapshot:
        if self._current is not None:
            return self._current
        # 无 current 时,先尝试 build
        snap = self.build({})
        if snap is not None:
            return snap
        # build 失败,返回最小可用快照
        return self._build_minimal_snapshot()

    def _build_minimal_snapshot(self) -> SelfModelSnapshot:
        return SelfModelSnapshot(
            identity_id=self._identity_id,
            identity=dict(DEFAULT_IDENTITY),
            core_values=[dict(v) for v in DEFAULT_CORE_VALUES[:MAX_VALUES]],
            stable_traits=[],
            preferences=[],
            current_state={},
            entries=[],
            counters={},
            meta={"minimal": True},
            health={},
        )


# ============================================================
# 抽象基类(给后续 Provider / Builder 扩展用)
# ============================================================


class SelfModelBuilderBase:
    """SelfModel Builder 抽象基类(Phase 4.2.1 v1.0)。

    后续 Phase 4.2.2+ 可由具体 Builder(如 MockBuilder / PersonalityBuilder)
    实现 aggregate(inputs) -> Dict 用于自定义聚合逻辑。

    默认实现:委托给 SelfModelFoundation。
    """

    name: str = "self_model_builder_base"
    schema_version: str = SELF_MODEL_FOUNDATION_SCHEMA_VERSION

    def __init__(self) -> None:
        self._foundation: SelfModelFoundation = SelfModelFoundation()

    @property
    def foundation(self) -> SelfModelFoundation:
        return self._foundation

    def aggregate(self, inputs: Optional[Dict[str, Any]] = None) -> Optional[SelfModelSnapshot]:
        """聚合输入到 snapshot。

        返回 None 表示 Foundation 失败。
        """
        return self._foundation.build(inputs)

    def health_check(self) -> Dict[str, Any]:
        return self._foundation.health_check()

    def reset(self) -> None:
        self._foundation.reset()


__all__ = [
    "SelfModelFoundation",
    "SelfModelBuilderBase",
    "DEFAULT_IDENTITY",
    "DEFAULT_CORE_VALUES",
    "SELF_MODEL_FOUNDATION_SCHEMA_VERSION",
]
