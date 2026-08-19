# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_state.py

Phase 5.0-D3-A: Self Model System —— 核心状态数据结构

职责:
- 定义 SelfState 容器,保存身份视图、特质状态、能力状态、兴趣状态
- 定义 IdentityView / TraitState / CapabilityState / InterestState / ChangeRecord
- 所有数据不可变快照(使用 asdict / copy 进行序列化)
- 所有时间字段通过 Clock 注入生成(由调用方传入,非模块内自取)
- 所有状态结构线程安全(自身 immutable,共享由外部锁保护)
- 任何字段异常不抛错(返回降级副本)

约束:
- 仅依赖 Python 标准库
- 不 import 任何业务模块(memory/growth/personality/emotion/relationship)
- 不调用 LLM / DB / Network
- 不连接业务事件源
- 不读取配置文件
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


# ============================================================
# 常量
# ============================================================
SELF_MODEL_STATE_SCHEMA_VERSION = "1.0"

# 默认容量限制(防止膨胀)
MAX_TRAITS = 64
MAX_CAPABILITIES = 64
MAX_INTERESTS = 64
MAX_IDENTITY_FIELDS = 32

# 合法 direction 取值
VALID_TRAIT_DIRECTIONS = frozenset({"rising", "falling", "stable", "unknown"})

# 合法 change_kind 取值
VALID_CHANGE_KINDS = frozenset({
    "trait_update",
    "capability_update",
    "interest_update",
    "identity_update",
    "snapshot_refresh",
    "rollback",
    "bulk_load",
    "reset",
})

# 合法 change_source 取值
VALID_CHANGE_SOURCES = frozenset({
    "internal_tick",
    "external_event",
    "user_input",
    "operator",
    "lifecycle_task",
    "migration",
    "unknown",
})


# ============================================================
# 工具
# ============================================================
def _new_id(prefix: str) -> str:
    """生成新 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clip_text(value: Any, max_len: int = 256) -> str:
    """裁剪字符串到最大长度,非字符串转为字符串。"""
    if value is None:
        return ""
    try:
        s = str(value)
    except Exception:
        return ""
    if len(s) > max_len:
        return s[:max_len]
    return s


def _clip_value(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    """将任意值裁剪到 [lo, hi]。失败返回 lo。"""
    try:
        v = float(value)
    except Exception:
        return lo
    if v != v:  # NaN
        return lo
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _safe_evidence_list(value: Any) -> List[str]:
    """规范化 evidence_event_ids。"""
    if value is None:
        return []
    if isinstance(value, str):
        if value:
            return [value]
        return []
    if isinstance(value, (list, tuple, set, frozenset)):
        out: List[str] = []
        seen: set = set()
        for item in value:
            if item is None:
                continue
            s = str(item)
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out
    return [str(value)]


# ============================================================
# IdentityView —— 身份视图(只读快照)
# ============================================================
@dataclass
class IdentityView:
    """身份视图。

    表示"我是谁"的稳定事实集合。所有字段都是稳定/不可变的。
    字段:
    - identity_id:        str                # 唯一身份 ID
    - name:               str                # 名称
    - archetype:          str                # 原型
    - display_name:       str                # 显示名
    - language:           str                # 主语言
    - version:            str                # 身份 schema 版本
    - core_values:        Tuple[str, ...]    # 核心价值观标签
    - attributes:         Dict[str, str]     # 任意附加属性
    - created_at:         float              # 构造时间(epoch seconds)
    - updated_at:         float              # 最近更新时间(epoch seconds)
    """

    name: str = "yuyi"
    archetype: str = "companion_ai"
    display_name: str = ""
    language: str = "zh-CN"
    version: str = SELF_MODEL_STATE_SCHEMA_VERSION
    identity_id: str = field(default_factory=lambda: _new_id("idt"))
    core_values: Tuple[str, ...] = field(default_factory=tuple)
    attributes: Dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        # 类型与裁剪
        self.name = _clip_text(self.name, 64)
        self.archetype = _clip_text(self.archetype, 64)
        self.display_name = _clip_text(self.display_name, 64)
        self.language = _clip_text(self.language, 16)
        self.version = _clip_text(self.version, 32)
        # core_values: 规范化
        if not isinstance(self.core_values, (list, tuple)):
            try:
                cv = list(self.core_values) if self.core_values else []
            except Exception:
                cv = []
        else:
            cv = list(self.core_values)
        clean: List[str] = []
        seen: set = set()
        for item in cv:
            if item is None:
                continue
            s = str(item)
            if not s or s in seen:
                continue
            seen.add(s)
            clean.append(s)
            if len(clean) >= 16:
                break
        self.core_values = tuple(clean)
        # attributes: dict[str, str]
        if not isinstance(self.attributes, dict):
            try:
                self.attributes = dict(self.attributes) if self.attributes else {}
            except Exception:
                self.attributes = {}
        else:
            self.attributes = dict(self.attributes)
        # 截断 attributes
        if len(self.attributes) > MAX_IDENTITY_FIELDS:
            keys = list(self.attributes.keys())[:MAX_IDENTITY_FIELDS]
            self.attributes = {k: str(self.attributes[k])[:256] for k in keys}
        else:
            self.attributes = {str(k): str(v)[:256] for k, v in self.attributes.items()}
        # created_at / updated_at
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.updated_at = float(self.updated_at)
        except Exception:
            self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["core_values"] = list(self.core_values)
        d["attributes"] = dict(self.attributes)
        d["schema_version"] = SELF_MODEL_STATE_SCHEMA_VERSION
        return d

    @classmethod
    def default_view(cls, *, now: float = 0.0) -> "IdentityView":
        """构造默认身份视图。"""
        return cls(
            name="yuyi",
            archetype="companion_ai",
            display_name="浅雾羽依",
            language="zh-CN",
            version=SELF_MODEL_STATE_SCHEMA_VERSION,
            core_values=("honesty", "empathy", "growth", "kindness", "autonomy"),
            attributes={},
            created_at=now,
            updated_at=now,
        )

    def is_valid(self) -> bool:
        return bool(self.name) and bool(self.identity_id)

    def with_updated(self, *, now: float, **fields: Any) -> "IdentityView":
        """返回带字段更新的新 IdentityView(原对象不变)。

        支持更新的字段:name / archetype / display_name / language /
        core_values / attributes。
        """
        new_attr: Dict[str, str] = dict(self.attributes)
        new_cv: List[str] = list(self.core_values)
        kwargs: Dict[str, Any] = {
            "name": self.name,
            "archetype": self.archetype,
            "display_name": self.display_name,
            "language": self.language,
            "version": self.version,
            "identity_id": self.identity_id,
            "core_values": tuple(new_cv),
            "attributes": new_attr,
            "created_at": self.created_at,
            "updated_at": float(now),
        }
        for k, v in fields.items():
            if k in ("name", "archetype", "display_name", "language", "version"):
                kwargs[k] = v
            elif k == "core_values":
                if isinstance(v, (list, tuple)):
                    kwargs["core_values"] = tuple(str(x) for x in v if x)
                elif isinstance(v, str):
                    kwargs["core_values"] = (v,)
            elif k == "attributes" and isinstance(v, dict):
                kwargs["attributes"] = {str(kk): str(vv) for kk, vv in v.items()}
        return IdentityView(**kwargs)

    def __repr__(self) -> str:
        return (
            f"IdentityView(identity_id={self.identity_id!r}, "
            f"name={self.name!r}, archetype={self.archetype!r})"
        )


# ============================================================
# TraitState —— 特质状态
# ============================================================
@dataclass
class TraitState:
    """特质状态。

    表示某个稳定特质的当前值、方向、稳定性、置信度、证据来源。
    字段:
    - trait_id:        str                # 唯一 ID
    - name:            str                # 特质名
    - current_value:   float              # 当前值 [0, 1]
    - direction:       str                # rising/falling/stable/unknown
    - stability:       float              # 稳定性 [0, 1]
    - confidence:      float              # 置信度 [0, 1]
    - evidence_event_ids: List[str]       # 证据事件 ID 列表(可追溯)
    - last_updated:    float              # 最近更新时间
    - description:     str                # 简短描述
    """

    name: str = ""
    current_value: float = 0.5
    direction: str = "stable"
    stability: float = 0.5
    confidence: float = 0.5
    evidence_event_ids: List[str] = field(default_factory=list)
    description: str = ""
    trait_id: str = field(default_factory=lambda: _new_id("trt"))
    last_updated: float = 0.0

    def __post_init__(self) -> None:
        self.name = _clip_text(self.name, 64)
        if self.direction not in VALID_TRAIT_DIRECTIONS:
            self.direction = "stable"
        self.current_value = _clip_value(self.current_value, 0.0, 1.0)
        self.stability = _clip_value(self.stability, 0.0, 1.0)
        self.confidence = _clip_value(self.confidence, 0.0, 1.0)
        self.evidence_event_ids = _safe_evidence_list(self.evidence_event_ids)
        self.description = _clip_text(self.description, 256)
        try:
            self.last_updated = float(self.last_updated)
        except Exception:
            self.last_updated = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = SELF_MODEL_STATE_SCHEMA_VERSION
        return d

    def with_value(
        self,
        *,
        now: float,
        new_value: float,
        new_direction: Optional[str] = None,
        new_stability: Optional[float] = None,
        new_confidence: Optional[float] = None,
        new_evidence: Optional[List[str]] = None,
        new_description: Optional[str] = None,
    ) -> "TraitState":
        """返回带新值的不可变副本(原对象不变)。

        自动从旧 direction 推断 new_direction(若未显式指定)。
        evidence_event_ids 自动合并(去重)。
        """
        old_value = self.current_value
        direction = new_direction
        if direction is None:
            try:
                if new_value > old_value + 1e-6:
                    direction = "rising"
                elif new_value < old_value - 1e-6:
                    direction = "falling"
                else:
                    direction = "stable"
            except Exception:
                direction = self.direction
        if direction not in VALID_TRAIT_DIRECTIONS:
            direction = "stable"
        merged_evidence = list(self.evidence_event_ids)
        if new_evidence:
            for eid in _safe_evidence_list(new_evidence):
                if eid not in merged_evidence:
                    merged_evidence.append(eid)
            # 截断到 64
            if len(merged_evidence) > 64:
                merged_evidence = merged_evidence[-64:]
        return TraitState(
            name=self.name,
            current_value=_clip_value(new_value, 0.0, 1.0),
            direction=direction,
            stability=_clip_value(new_stability, 0.0, 1.0) if new_stability is not None else self.stability,
            confidence=_clip_value(new_confidence, 0.0, 1.0) if new_confidence is not None else self.confidence,
            evidence_event_ids=merged_evidence,
            description=new_description if new_description is not None else self.description,
            trait_id=self.trait_id,
            last_updated=float(now),
        )

    def is_valid(self) -> bool:
        return bool(self.name) and bool(self.trait_id)

    def __repr__(self) -> str:
        return (
            f"TraitState(name={self.name!r}, value={self.current_value:.3f}, "
            f"dir={self.direction!r}, conf={self.confidence:.3f})"
        )


# ============================================================
# CapabilityState —— 能力状态
# ============================================================
@dataclass
class CapabilityState:
    """能力状态。

    表示羽依能够做某事的能力(可启用/未启用)。
    字段:
    - capability_id:   str                # 唯一 ID
    - name:            str                # 能力名
    - enabled:         bool               # 是否启用
    - confidence:      float              # 置信度 [0, 1]
    - proficiency:     float              # 熟练度 [0, 1]
    - evidence_event_ids: List[str]       # 证据事件 ID 列表
    - last_used:       float              # 最近使用时间
    - last_updated:    float              # 最近更新时间
    - description:     str                # 简短描述
    """

    name: str = ""
    enabled: bool = True
    confidence: float = 0.5
    proficiency: float = 0.5
    evidence_event_ids: List[str] = field(default_factory=list)
    description: str = ""
    capability_id: str = field(default_factory=lambda: _new_id("cap"))
    last_used: float = 0.0
    last_updated: float = 0.0

    def __post_init__(self) -> None:
        self.name = _clip_text(self.name, 64)
        self.confidence = _clip_value(self.confidence, 0.0, 1.0)
        self.proficiency = _clip_value(self.proficiency, 0.0, 1.0)
        self.evidence_event_ids = _safe_evidence_list(self.evidence_event_ids)
        self.description = _clip_text(self.description, 256)
        try:
            self.last_used = float(self.last_used)
        except Exception:
            self.last_used = 0.0
        try:
            self.last_updated = float(self.last_updated)
        except Exception:
            self.last_updated = 0.0
        self.enabled = bool(self.enabled)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = SELF_MODEL_STATE_SCHEMA_VERSION
        return d

    def with_update(
        self,
        *,
        now: float,
        enabled: Optional[bool] = None,
        new_confidence: Optional[float] = None,
        new_proficiency: Optional[float] = None,
        new_evidence: Optional[List[str]] = None,
        mark_used: bool = False,
        new_description: Optional[str] = None,
    ) -> "CapabilityState":
        """返回带新值的不可变副本(原对象不变)。"""
        merged_evidence = list(self.evidence_event_ids)
        if new_evidence:
            for eid in _safe_evidence_list(new_evidence):
                if eid not in merged_evidence:
                    merged_evidence.append(eid)
            if len(merged_evidence) > 64:
                merged_evidence = merged_evidence[-64:]
        return CapabilityState(
            name=self.name,
            enabled=bool(enabled) if enabled is not None else self.enabled,
            confidence=_clip_value(new_confidence, 0.0, 1.0) if new_confidence is not None else self.confidence,
            proficiency=_clip_value(new_proficiency, 0.0, 1.0) if new_proficiency is not None else self.proficiency,
            evidence_event_ids=merged_evidence,
            description=new_description if new_description is not None else self.description,
            capability_id=self.capability_id,
            last_used=float(now) if mark_used else self.last_used,
            last_updated=float(now),
        )

    def is_valid(self) -> bool:
        return bool(self.name) and bool(self.capability_id)

    def __repr__(self) -> str:
        return (
            f"CapabilityState(name={self.name!r}, enabled={self.enabled!r}, "
            f"prof={self.proficiency:.3f}, conf={self.confidence:.3f})"
        )


# ============================================================
# InterestState —— 兴趣状态
# ============================================================
@dataclass
class InterestState:
    """兴趣状态。

    表示羽依对某话题/领域的兴趣强度。
    字段:
    - interest_id:     str                # 唯一 ID
    - name:            str                # 兴趣名(话题)
    - level:           float              # 兴趣强度 [0, 1]
    - decay_rate:      float              # 自然衰减率(/秒)
    - evidence_event_ids: List[str]       # 证据事件 ID 列表
    - last_reinforced: float              # 最近强化时间
    - last_updated:    float              # 最近更新时间
    - tags:            Tuple[str, ...]    # 标签
    - description:     str                # 简短描述
    """

    name: str = ""
    level: float = 0.5
    decay_rate: float = 0.0
    evidence_event_ids: List[str] = field(default_factory=list)
    description: str = ""
    interest_id: str = field(default_factory=lambda: _new_id("int"))
    last_reinforced: float = 0.0
    last_updated: float = 0.0
    tags: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        self.name = _clip_text(self.name, 64)
        self.level = _clip_value(self.level, 0.0, 1.0)
        # decay_rate 范围 [-1, 1] (1 = 极快衰减, 0 = 不衰减, 负 = 增长)
        try:
            dr = float(self.decay_rate)
        except Exception:
            dr = 0.0
        if dr != dr:
            dr = 0.0
        if dr < -1.0:
            dr = -1.0
        if dr > 1.0:
            dr = 1.0
        self.decay_rate = dr
        self.evidence_event_ids = _safe_evidence_list(self.evidence_event_ids)
        self.description = _clip_text(self.description, 256)
        try:
            self.last_reinforced = float(self.last_reinforced)
        except Exception:
            self.last_reinforced = 0.0
        try:
            self.last_updated = float(self.last_updated)
        except Exception:
            self.last_updated = 0.0
        if not isinstance(self.tags, (list, tuple)):
            try:
                self.tags = tuple(self.tags) if self.tags else ()
            except Exception:
                self.tags = ()
        else:
            clean_tags: List[str] = []
            seen: set = set()
            for t in self.tags:
                if t is None:
                    continue
                s = str(t)
                if not s or s in seen:
                    continue
                seen.add(s)
                clean_tags.append(s)
                if len(clean_tags) >= 16:
                    break
            self.tags = tuple(clean_tags)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["tags"] = list(self.tags)
        d["schema_version"] = SELF_MODEL_STATE_SCHEMA_VERSION
        return d

    def with_level(
        self,
        *,
        now: float,
        new_level: Optional[float] = None,
        boost: Optional[float] = None,
        new_decay_rate: Optional[float] = None,
        new_evidence: Optional[List[str]] = None,
        mark_reinforced: bool = True,
        new_tags: Optional[Tuple[str, ...]] = None,
        new_description: Optional[str] = None,
    ) -> "InterestState":
        """返回带新 level 的不可变副本。

        - new_level: 显式设置 level
        - boost: 增量(叠加在当前 level 上,自动 clip)
        - 其余字段可选
        """
        if new_level is not None:
            final_level = _clip_value(new_level, 0.0, 1.0)
        elif boost is not None:
            try:
                b = float(boost)
            except Exception:
                b = 0.0
            final_level = _clip_value(self.level + b, 0.0, 1.0)
        else:
            final_level = self.level

        merged_evidence = list(self.evidence_event_ids)
        if new_evidence:
            for eid in _safe_evidence_list(new_evidence):
                if eid not in merged_evidence:
                    merged_evidence.append(eid)
            if len(merged_evidence) > 64:
                merged_evidence = merged_evidence[-64:]

        if new_tags is not None:
            tags_clean: List[str] = []
            seen: set = set()
            for t in new_tags:
                if t is None:
                    continue
                s = str(t)
                if not s or s in seen:
                    continue
                seen.add(s)
                tags_clean.append(s)
                if len(tags_clean) >= 16:
                    break
            final_tags = tuple(tags_clean)
        else:
            final_tags = self.tags

        return InterestState(
            name=self.name,
            level=final_level,
            decay_rate=float(new_decay_rate) if new_decay_rate is not None else self.decay_rate,
            evidence_event_ids=merged_evidence,
            description=new_description if new_description is not None else self.description,
            interest_id=self.interest_id,
            last_reinforced=float(now) if mark_reinforced else self.last_reinforced,
            last_updated=float(now),
            tags=final_tags,
        )

    def decay(self, *, now: float, delta_seconds: float) -> "InterestState":
        """按 decay_rate 自然衰减,返回新副本(原对象不变)。"""
        if not isinstance(delta_seconds, (int, float)):
            delta_seconds = 0.0
        if delta_seconds <= 0:
            return self
        new_level = self.level - float(self.decay_rate) * float(delta_seconds)
        new_level = _clip_value(new_level, 0.0, 1.0)
        return InterestState(
            name=self.name,
            level=new_level,
            decay_rate=self.decay_rate,
            evidence_event_ids=list(self.evidence_event_ids),
            description=self.description,
            interest_id=self.interest_id,
            last_reinforced=self.last_reinforced,
            last_updated=float(now),
            tags=self.tags,
        )

    def is_valid(self) -> bool:
        return bool(self.name) and bool(self.interest_id)

    def __repr__(self) -> str:
        return (
            f"InterestState(name={self.name!r}, level={self.level:.3f}, "
            f"decay={self.decay_rate:.4f})"
        )


# ============================================================
# ChangeRecord —— 变化记录
# ============================================================
@dataclass
class ChangeRecord:
    """状态变化记录(不可变)。

    每一次 SelfState 内部状态变化都必须生成 ChangeRecord,
    且必须包含至少一个 evidence_event_id 用于追溯。
    字段:
    - change_id:          str              # 唯一 ID
    - change_kind:        str              # 变化类型
    - change_source:      str              # 变化来源
    - target:             str              # 影响对象(trait/capability/interest/identity)
    - target_id:          str              # 对象 ID
    - target_name:        str              # 对象名
    - before:             Dict[str, Any]   # 变化前快照(子集)
    - after:              Dict[str, Any]   # 变化后快照(子集)
    - evidence_event_ids: List[str]       # 证据事件 ID
    - timestamp:          float            # 时间戳(Clock.now())
    - reason:             str              # 原因
    - metadata:           Dict[str, Any]   # 附加元信息
    """

    change_kind: str = "trait_update"
    change_source: str = "unknown"
    target: str = "trait"
    target_id: str = ""
    target_name: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    evidence_event_ids: List[str] = field(default_factory=list)
    timestamp: float = 0.0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    change_id: str = field(default_factory=lambda: _new_id("chg"))

    def __post_init__(self) -> None:
        if self.change_kind not in VALID_CHANGE_KINDS:
            self.change_kind = "trait_update"
        if self.change_source not in VALID_CHANGE_SOURCES:
            self.change_source = "unknown"
        self.target = _clip_text(self.target, 32)
        self.target_id = _clip_text(self.target_id, 64)
        self.target_name = _clip_text(self.target_name, 64)
        if not isinstance(self.before, dict):
            try:
                self.before = dict(self.before) if self.before else {}
            except Exception:
                self.before = {}
        else:
            self.before = dict(self.before)
        if not isinstance(self.after, dict):
            try:
                self.after = dict(self.after) if self.after else {}
            except Exception:
                self.after = {}
        else:
            self.after = dict(self.after)
        self.evidence_event_ids = _safe_evidence_list(self.evidence_event_ids)
        try:
            self.timestamp = float(self.timestamp)
        except Exception:
            self.timestamp = 0.0
        self.reason = _clip_text(self.reason, 256)
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        else:
            self.metadata = dict(self.metadata)
        # 截断 metadata 字段
        if len(self.metadata) > 16:
            keys = list(self.metadata.keys())[:16]
            self.metadata = {k: self.metadata[k] for k in keys}

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = SELF_MODEL_STATE_SCHEMA_VERSION
        return d

    def has_evidence(self) -> bool:
        return len(self.evidence_event_ids) > 0

    def is_valid(self) -> bool:
        return bool(self.change_id) and bool(self.target) and bool(self.target_id)

    def __repr__(self) -> str:
        return (
            f"ChangeRecord(change_id={self.change_id!r}, "
            f"kind={self.change_kind!r}, target={self.target!r}, "
            f"target_name={self.target_name!r}, evidence={len(self.evidence_event_ids)})"
        )


# ============================================================
# SelfState —— SelfModel 状态容器
# ============================================================
@dataclass
class SelfState:
    """SelfModel 运行时状态容器(只读快照,内部不可变)。

    字段:
    - identity:         IdentityView              # 身份视图
    - traits:           Dict[str, TraitState]     # 特质(name -> state)
    - capabilities:     Dict[str, CapabilityState]# 能力(name -> state)
    - interests:        Dict[str, InterestState]  # 兴趣(name -> state)
    - schema_version:   str                       # schema 版本
    - created_at:       float                     # 容器创建时间
    - last_refreshed:   float                     # 最近刷新时间
    - lock:             threading.RLock           # 容器内锁(用于 with_update 等)
    """

    identity: IdentityView = field(default_factory=IdentityView.default_view)
    traits: Dict[str, TraitState] = field(default_factory=dict)
    capabilities: Dict[str, CapabilityState] = field(default_factory=dict)
    interests: Dict[str, InterestState] = field(default_factory=dict)
    schema_version: str = SELF_MODEL_STATE_SCHEMA_VERSION
    created_at: float = 0.0
    last_refreshed: float = 0.0
    # lock 字段不参与 __repr__ / __eq__,但提供线程安全
    _lock: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 类型与截断
        if not isinstance(self.identity, IdentityView):
            self.identity = IdentityView.default_view()
        if not isinstance(self.traits, dict):
            try:
                self.traits = dict(self.traits) if self.traits else {}
            except Exception:
                self.traits = {}
        # 只保留合法 TraitState
        clean_traits: Dict[str, TraitState] = {}
        for k, v in list(self.traits.items()):
            if isinstance(v, TraitState) and v.is_valid():
                clean_traits[str(k)] = v
            if len(clean_traits) >= MAX_TRAITS:
                break
        self.traits = clean_traits

        if not isinstance(self.capabilities, dict):
            try:
                self.capabilities = dict(self.capabilities) if self.capabilities else {}
            except Exception:
                self.capabilities = {}
        clean_caps: Dict[str, CapabilityState] = {}
        for k, v in list(self.capabilities.items()):
            if isinstance(v, CapabilityState) and v.is_valid():
                clean_caps[str(k)] = v
            if len(clean_caps) >= MAX_CAPABILITIES:
                break
        self.capabilities = clean_caps

        if not isinstance(self.interests, dict):
            try:
                self.interests = dict(self.interests) if self.interests else {}
            except Exception:
                self.interests = {}
        clean_interests: Dict[str, InterestState] = {}
        for k, v in list(self.interests.items()):
            if isinstance(v, InterestState) and v.is_valid():
                clean_interests[str(k)] = v
            if len(clean_interests) >= MAX_INTERESTS:
                break
        self.interests = clean_interests

        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.last_refreshed = float(self.last_refreshed)
        except Exception:
            self.last_refreshed = self.created_at

        if self._lock is None:
            self._lock = threading.RLock()

    # --------------------------------------------------------
    # 派生属性
    # --------------------------------------------------------
    @property
    def trait_count(self) -> int:
        return len(self.traits)

    @property
    def capability_count(self) -> int:
        return len(self.capabilities)

    @property
    def interest_count(self) -> int:
        return len(self.interests)

    @property
    def enabled_capability_count(self) -> int:
        return sum(1 for c in self.capabilities.values() if c.enabled)

    @property
    def total_evidence_count(self) -> int:
        n = 0
        for t in self.traits.values():
            n += len(t.evidence_event_ids)
        for c in self.capabilities.values():
            n += len(c.evidence_event_ids)
        for i in self.interests.values():
            n += len(i.evidence_event_ids)
        return n

    # --------------------------------------------------------
    # 不可变更新(返回新 SelfState)
    # --------------------------------------------------------
    def with_identity(self, *, now: float, new_view: IdentityView) -> "SelfState":
        """返回带新 identity 的副本。"""
        new_state = self._copy_shallow()
        new_state.identity = new_view
        new_state.last_refreshed = float(now)
        return new_state

    def with_trait(
        self,
        *,
        now: float,
        trait: TraitState,
    ) -> "SelfState":
        """返回带新/更新 trait 的副本(超出容量时 FIFO 淘汰最早 last_updated)。"""
        new_state = self._copy_shallow()
        new_traits: Dict[str, TraitState] = dict(new_state.traits)
        if trait.is_valid():
            new_traits[trait.name] = trait
            # 容量保护
            if len(new_traits) > MAX_TRAITS:
                # 淘汰 last_updated 最小的
                items = sorted(
                    new_traits.items(),
                    key=lambda kv: (kv[1].last_updated, kv[0]),
                )
                keep = items[-MAX_TRAITS:]
                new_traits = {k: v for k, v in keep}
        new_state.traits = new_traits
        new_state.last_refreshed = float(now)
        return new_state

    def with_capability(
        self,
        *,
        now: float,
        capability: CapabilityState,
    ) -> "SelfState":
        """返回带新/更新 capability 的副本(超出容量时 FIFO 淘汰最早 last_updated)。"""
        new_state = self._copy_shallow()
        new_caps: Dict[str, CapabilityState] = dict(new_state.capabilities)
        if capability.is_valid():
            new_caps[capability.name] = capability
            if len(new_caps) > MAX_CAPABILITIES:
                items = sorted(
                    new_caps.items(),
                    key=lambda kv: (kv[1].last_updated, kv[0]),
                )
                keep = items[-MAX_CAPABILITIES:]
                new_caps = {k: v for k, v in keep}
        new_state.capabilities = new_caps
        new_state.last_refreshed = float(now)
        return new_state

    def with_interest(
        self,
        *,
        now: float,
        interest: InterestState,
    ) -> "SelfState":
        """返回带新/更新 interest 的副本。"""
        new_state = self._copy_shallow()
        new_interests: Dict[str, InterestState] = dict(new_state.interests)
        if interest.is_valid():
            new_interests[interest.name] = interest
            if len(new_interests) > MAX_INTERESTS:
                items = sorted(
                    new_interests.items(),
                    key=lambda kv: (kv[1].last_updated, kv[0]),
                )
                keep = items[-MAX_INTERESTS:]
                new_interests = {k: v for k, v in keep}
        new_state.interests = new_interests
        new_state.last_refreshed = float(now)
        return new_state

    def with_removed(
        self,
        *,
        now: float,
        trait: Optional[str] = None,
        capability: Optional[str] = None,
        interest: Optional[str] = None,
    ) -> "SelfState":
        """返回移除指定 name 后副本(原对象不变)。"""
        new_state = self._copy_shallow()
        if trait is not None and trait in new_state.traits:
            new_state.traits = {k: v for k, v in new_state.traits.items() if k != trait}
        if capability is not None and capability in new_state.capabilities:
            new_state.capabilities = {k: v for k, v in new_state.capabilities.items() if k != capability}
        if interest is not None and interest in new_state.interests:
            new_state.interests = {k: v for k, v in new_state.interests.items() if k != interest}
        new_state.last_refreshed = float(now)
        return new_state

    def with_interest_decay(self, *, now: float, delta_seconds: float) -> "SelfState":
        """返回所有 interest 应用自然衰减的副本。"""
        new_state = self._copy_shallow()
        new_interests: Dict[str, InterestState] = {}
        for name, interest in new_state.interests.items():
            new_interests[name] = interest.decay(now=now, delta_seconds=delta_seconds)
        new_state.interests = new_interests
        new_state.last_refreshed = float(now)
        return new_state

    def with_refresh(self, *, now: float) -> "SelfState":
        """返回仅刷新 last_refreshed 的副本。"""
        new_state = self._copy_shallow()
        new_state.last_refreshed = float(now)
        return new_state

    # --------------------------------------------------------
    # 复制
    # --------------------------------------------------------
    def _copy_shallow(self) -> "SelfState":
        """浅拷贝(共享内部 dict 内容,不共享引用)。

        使用深拷贝以彻底隔离。
        """
        new_identity = IdentityView(
            name=self.identity.name,
            archetype=self.identity.archetype,
            display_name=self.identity.display_name,
            language=self.identity.language,
            version=self.identity.version,
            identity_id=self.identity.identity_id,
            core_values=tuple(self.identity.core_values),
            attributes=dict(self.identity.attributes),
            created_at=self.identity.created_at,
            updated_at=self.identity.updated_at,
        )
        new_traits: Dict[str, TraitState] = dict(self.traits)
        new_caps: Dict[str, CapabilityState] = dict(self.capabilities)
        new_interests: Dict[str, InterestState] = dict(self.interests)
        return SelfState(
            identity=new_identity,
            traits=new_traits,
            capabilities=new_caps,
            interests=new_interests,
            schema_version=self.schema_version,
            created_at=self.created_at,
            last_refreshed=self.last_refreshed,
        )

    def snapshot_dict(self) -> Dict[str, Any]:
        """返回完整快照 dict(用于序列化 / 审计)。"""
        with self._lock:
            return {
                "schema_version": self.schema_version,
                "created_at": self.created_at,
                "last_refreshed": self.last_refreshed,
                "identity": self.identity.to_dict(),
                "traits": {k: v.to_dict() for k, v in self.traits.items()},
                "capabilities": {k: v.to_dict() for k, v in self.capabilities.items()},
                "interests": {k: v.to_dict() for k, v in self.interests.items()},
                "counters": {
                    "traits": self.trait_count,
                    "capabilities": self.capability_count,
                    "interests": self.interest_count,
                    "enabled_capabilities": self.enabled_capability_count,
                    "total_evidence": self.total_evidence_count,
                },
            }

    def health_check(self) -> Dict[str, Any]:
        """健康度自检。"""
        with self._lock:
            healthy = (
                self.identity.is_valid()
                and self.trait_count >= 0
                and self.capability_count >= 0
                and self.interest_count >= 0
            )
            return {
                "healthy": bool(healthy),
                "schema_version": self.schema_version,
                "has_identity": self.identity.is_valid(),
                "trait_count": self.trait_count,
                "capability_count": self.capability_count,
                "interest_count": self.interest_count,
                "enabled_capability_count": self.enabled_capability_count,
                "total_evidence_count": self.total_evidence_count,
                "created_at": self.created_at,
                "last_refreshed": self.last_refreshed,
            }

    @classmethod
    def empty(cls, *, now: float = 0.0) -> "SelfState":
        """构造一个空的 SelfState。"""
        return cls(
            identity=IdentityView.default_view(now=now),
            traits={},
            capabilities={},
            interests={},
            created_at=now,
            last_refreshed=now,
        )

    def __repr__(self) -> str:
        return (
            f"SelfState(traits={self.trait_count}, "
            f"capabilities={self.capability_count}, "
            f"interests={self.interest_count}, "
            f"identity={self.identity.name!r})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_self_state(*, now: float = 0.0) -> SelfState:
    """构造带默认 identity 的空 SelfState。"""
    return SelfState.empty(now=now)


__all__ = [
    # 常量
    "SELF_MODEL_STATE_SCHEMA_VERSION",
    "MAX_TRAITS",
    "MAX_CAPABILITIES",
    "MAX_INTERESTS",
    "MAX_IDENTITY_FIELDS",
    "VALID_TRAIT_DIRECTIONS",
    "VALID_CHANGE_KINDS",
    "VALID_CHANGE_SOURCES",
    # 数据类
    "IdentityView",
    "TraitState",
    "CapabilityState",
    "InterestState",
    "ChangeRecord",
    "SelfState",
    # 工厂
    "build_default_self_state",
]
