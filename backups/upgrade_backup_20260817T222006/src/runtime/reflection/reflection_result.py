# -*- coding: utf-8 -*-
"""
src/runtime/reflection/reflection_result.py

Phase 5.0-D3-B: Reflection Result 数据结构。

职责:
- 定义 ReflectionResult / Insight / StateChangeSuggestion
- 提供序列化 / 反序列化 / roundtrip 能力
- 所有结构体不可变(immutable-style 字段),更新通过 with_* 返回新对象
- 所有字段类型安全,异常不抛错(降级)
- 所有 Suggestion 必含 evidence_event_ids

约束:
- 不依赖任何业务模块
- 仅依赖 Python 标准库
- 不调用 LLM / DB / Network
- 时间字段由 Clock 注入(本模块不主动获取)
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# 常量
# ============================================================
REFLECTION_RESULT_SCHEMA_VERSION = "1.0"

# Reflection 类型
REFLECTION_TYPE_DAILY = "daily"
REFLECTION_TYPE_EVENT = "event"
REFLECTION_TYPE_GROWTH = "growth"

ALL_REFLECTION_TYPES = (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
)

# Insight category
INSIGHT_CATEGORY_PATTERN = "pattern"
INSIGHT_CATEGORY_PREFERENCE = "preference"
INSIGHT_CATEGORY_GROWTH = "growth"
INSIGHT_CATEGORY_CONCERN = "concern"

ALL_INSIGHT_CATEGORIES = frozenset({
    INSIGHT_CATEGORY_PATTERN,
    INSIGHT_CATEGORY_PREFERENCE,
    INSIGHT_CATEGORY_GROWTH,
    INSIGHT_CATEGORY_CONCERN,
})

# 容量限制
MAX_INSIGHTS = 64
MAX_SUGGESTIONS = 64
MAX_EVIDENCE_PER_SUGGESTION = 64
MAX_DESCRIPTION_LEN = 512
MAX_REASON_LEN = 512
MAX_TARGET_FIELD_LEN = 128
MAX_METADATA_KEYS = 16


# ============================================================
# 工具
# ============================================================
def _new_id(prefix: str) -> str:
    """生成新 ID。"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clip_text(value: Any, max_len: int = MAX_DESCRIPTION_LEN) -> str:
    """裁剪字符串。"""
    if value is None:
        return ""
    try:
        s = str(value)
    except Exception:
        return ""
    if len(s) > max_len:
        return s[:max_len]
    return s


def _clip_float(value: Any, lo: float, hi: float, default: float) -> float:
    """裁剪浮点到 [lo, hi]。"""
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:  # NaN
        return default
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


def _safe_string_list(value: Any) -> List[str]:
    """规范化字符串列表(不强制,允许空)。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            s = str(item)
            if s:
                out.append(s)
        return out
    return [str(value)] if value else []


def _safe_metadata(value: Any) -> Dict[str, Any]:
    """规范化 metadata dict。"""
    if not isinstance(value, dict):
        try:
            return dict(value) if value else {}
        except Exception:
            return {}
    out: Dict[str, Any] = {}
    for k, v in list(value.items())[:MAX_METADATA_KEYS]:
        try:
            out[str(k)] = v
        except Exception:
            continue
    return out


# ============================================================
# Insight
# ============================================================
@dataclass
class Insight:
    """从事件中抽取的洞察。

    字段:
    - insight_id:            str                    # 唯一 ID
    - category:              str                    # pattern / preference / growth / concern
    - description:           str                    # 描述
    - supporting_event_ids:  List[str]              # 支撑事件 ID
    - confidence:            float                  # 置信度 [0, 1]
    - created_at:            float                  # 创建时间
    - metadata:              Dict[str, Any]         # 附加元信息
    """

    category: str = INSIGHT_CATEGORY_PATTERN
    description: str = ""
    supporting_event_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    insight_id: str = field(default_factory=lambda: _new_id("ins"))

    def __post_init__(self) -> None:
        if self.category not in ALL_INSIGHT_CATEGORIES:
            self.category = INSIGHT_CATEGORY_PATTERN
        self.description = _clip_text(self.description, MAX_DESCRIPTION_LEN)
        self.supporting_event_ids = _safe_evidence_list(self.supporting_event_ids)
        if len(self.supporting_event_ids) > MAX_EVIDENCE_PER_SUGGESTION:
            self.supporting_event_ids = self.supporting_event_ids[-MAX_EVIDENCE_PER_SUGGESTION:]
        self.confidence = _clip_float(self.confidence, 0.0, 1.0, 0.5)
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        self.metadata = _safe_metadata(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = REFLECTION_RESULT_SCHEMA_VERSION
        return d

    def has_evidence(self) -> bool:
        return len(self.supporting_event_ids) > 0

    def is_valid(self) -> bool:
        return bool(self.insight_id) and bool(self.category)

    def __repr__(self) -> str:
        return (
            f"Insight(category={self.category!r}, "
            f"confidence={self.confidence:.3f}, "
            f"evidence={len(self.supporting_event_ids)})"
        )


# ============================================================
# StateChangeSuggestion
# ============================================================
@dataclass
class StateChangeSuggestion:
    """建议的状态变更(不直接生效)。

    字段:
    - target_field:       str                      # 目标字段(例 "interest_state.ai_art")
    - old_value:          Optional[Any]            # 旧值(可空)
    - suggested_value:    Any                      # 建议值
    - delta:              float                    # 建议偏移
    - reason:             str                      # 原因
    - evidence_event_ids: List[str]                # 证据事件 ID(必填)
    - confidence:         float                    # 置信度 [0, 1]
    - created_at:         float                    # 创建时间
    - metadata:           Dict[str, Any]           # 附加元信息
    """

    target_field: str = ""
    old_value: Any = None
    suggested_value: Any = None
    delta: float = 0.0
    reason: str = ""
    evidence_event_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.target_field = _clip_text(self.target_field, MAX_TARGET_FIELD_LEN)
        self.reason = _clip_text(self.reason, MAX_REASON_LEN)
        self.evidence_event_ids = _safe_evidence_list(self.evidence_event_ids)
        if len(self.evidence_event_ids) > MAX_EVIDENCE_PER_SUGGESTION:
            self.evidence_event_ids = self.evidence_event_ids[-MAX_EVIDENCE_PER_SUGGESTION:]
        # delta 可以是任意浮点(不裁剪),但要求能转 float
        try:
            self.delta = float(self.delta)
        except Exception:
            self.delta = 0.0
        self.confidence = _clip_float(self.confidence, 0.0, 1.0, 0.5)
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        self.metadata = _safe_metadata(self.metadata)
        # suggested_value 保留原值(可能是 int/float/str/bool/None)
        # old_value 保留原值

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = REFLECTION_RESULT_SCHEMA_VERSION
        return d

    def has_evidence(self) -> bool:
        return len(self.evidence_event_ids) > 0

    def is_valid(self) -> bool:
        return (
            bool(self.target_field)
            and bool(self.evidence_event_ids)
        )

    def __repr__(self) -> str:
        return (
            f"StateChangeSuggestion(target={self.target_field!r}, "
            f"delta={self.delta:+.3f}, confidence={self.confidence:.3f}, "
            f"evidence={len(self.evidence_event_ids)})"
        )


# ============================================================
# ReflectionResult
# ============================================================
@dataclass
class ReflectionResult:
    """一次反思的结果。

    字段:
    - reflection_id:         str                        # 唯一 ID
    - reflection_type:       str                        # daily / event / growth
    - triggered_at:          float                      # 触发时间
    - window_start:          float                      # 窗口起点
    - window_end:            float                      # 窗口终点
    - source_event_ids:      List[str]                  # 涉及事件 ID
    - insights:              List[Insight]              # 洞察
    - suggested_changes:     List[StateChangeSuggestion]# 状态变更建议
    - confidence:            float                      # 整体置信度
    - evidence_strength:     float                      # 证据强度
    - schema_version:        str                        # schema 版本
    - created_at:            float                      # 创建时间
    - metadata:              Dict[str, Any]             # 元信息
    """

    reflection_type: str = REFLECTION_TYPE_DAILY
    triggered_at: float = 0.0
    window_start: float = 0.0
    window_end: float = 0.0
    source_event_ids: List[str] = field(default_factory=list)
    insights: List[Insight] = field(default_factory=list)
    suggested_changes: List[StateChangeSuggestion] = field(default_factory=list)
    confidence: float = 0.5
    evidence_strength: float = 0.5
    created_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    reflection_id: str = field(default_factory=lambda: _new_id("ref"))
    schema_version: str = REFLECTION_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.reflection_type not in ALL_REFLECTION_TYPES:
            self.reflection_type = REFLECTION_TYPE_DAILY
        try:
            self.triggered_at = float(self.triggered_at)
        except Exception:
            self.triggered_at = 0.0
        try:
            self.window_start = float(self.window_start)
        except Exception:
            self.window_start = self.triggered_at
        try:
            self.window_end = float(self.window_end)
        except Exception:
            self.window_end = self.triggered_at
        # 规范化 source_event_ids(去重)
        self.source_event_ids = _safe_string_list(self.source_event_ids)
        seen_ids: set = set()
        dedup_ids: List[str] = []
        for s in self.source_event_ids:
            if s in seen_ids:
                continue
            seen_ids.add(s)
            dedup_ids.append(s)
        self.source_event_ids = dedup_ids
        if len(self.source_event_ids) > 256:
            self.source_event_ids = self.source_event_ids[-256:]
        # 规范化 insights
        if not isinstance(self.insights, list):
            try:
                self.insights = list(self.insights) if self.insights else []
            except Exception:
                self.insights = []
        clean_insights: List[Insight] = []
        for it in self.insights:
            if isinstance(it, Insight):
                clean_insights.append(it)
            if len(clean_insights) >= MAX_INSIGHTS:
                break
        self.insights = clean_insights
        # 规范化 suggested_changes
        if not isinstance(self.suggested_changes, list):
            try:
                self.suggested_changes = list(self.suggested_changes) if self.suggested_changes else []
            except Exception:
                self.suggested_changes = []
        clean_changes: List[StateChangeSuggestion] = []
        for sc in self.suggested_changes:
            if isinstance(sc, StateChangeSuggestion):
                clean_changes.append(sc)
            if len(clean_changes) >= MAX_SUGGESTIONS:
                break
        self.suggested_changes = clean_changes
        # confidence / evidence_strength
        self.confidence = _clip_float(self.confidence, 0.0, 1.0, 0.5)
        self.evidence_strength = _clip_float(self.evidence_strength, 0.0, 1.0, 0.5)
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        self.metadata = _safe_metadata(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "reflection_type": self.reflection_type,
            "triggered_at": self.triggered_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "source_event_ids": list(self.source_event_ids),
            "insights": [i.to_dict() for i in self.insights],
            "suggested_changes": [s.to_dict() for s in self.suggested_changes],
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReflectionResult":
        """从 dict 反序列化。容错处理缺失字段。"""
        if not isinstance(data, dict):
            return cls()
        # insights
        raw_insights = data.get("insights") or []
        insights: List[Insight] = []
        if isinstance(raw_insights, list):
            for raw in raw_insights:
                if isinstance(raw, Insight):
                    insights.append(raw)
                elif isinstance(raw, dict):
                    try:
                        # 容错: confidence / created_at 可能不是合法数字
                        try:
                            conf = float(raw.get("confidence", 0.5))
                            if conf != conf:
                                conf = 0.5
                        except Exception:
                            conf = 0.5
                        try:
                            cat_ts = float(raw.get("created_at", 0.0))
                        except Exception:
                            cat_ts = 0.0
                        insights.append(Insight(
                            category=str(raw.get("category", INSIGHT_CATEGORY_PATTERN) or INSIGHT_CATEGORY_PATTERN),
                            description=str(raw.get("description", "") or ""),
                            supporting_event_ids=list(raw.get("supporting_event_ids", []) or []),
                            confidence=conf,
                            created_at=cat_ts,
                            metadata=dict(raw.get("metadata", {}) or {}),
                            insight_id=str(raw.get("insight_id", "") or _new_id("ins")),
                        ))
                    except Exception:
                        continue
        # suggested_changes
        raw_changes = data.get("suggested_changes") or []
        changes: List[StateChangeSuggestion] = []
        if isinstance(raw_changes, list):
            for raw in raw_changes:
                if isinstance(raw, StateChangeSuggestion):
                    changes.append(raw)
                elif isinstance(raw, dict):
                    try:
                        try:
                            d = float(raw.get("delta", 0.0))
                        except Exception:
                            d = 0.0
                        try:
                            conf = float(raw.get("confidence", 0.5))
                            if conf != conf:
                                conf = 0.5
                        except Exception:
                            conf = 0.5
                        try:
                            cat_ts = float(raw.get("created_at", 0.0))
                        except Exception:
                            cat_ts = 0.0
                        changes.append(StateChangeSuggestion(
                            target_field=str(raw.get("target_field", "") or ""),
                            old_value=raw.get("old_value"),
                            suggested_value=raw.get("suggested_value"),
                            delta=d,
                            reason=str(raw.get("reason", "") or ""),
                            evidence_event_ids=list(raw.get("evidence_event_ids", []) or []),
                            confidence=conf,
                            created_at=cat_ts,
                            metadata=dict(raw.get("metadata", {}) or {}),
                        ))
                    except Exception:
                        continue
        return cls(
            reflection_type=str(data.get("reflection_type", REFLECTION_TYPE_DAILY) or REFLECTION_TYPE_DAILY),
            triggered_at=_safe_float(data.get("triggered_at", 0.0)),
            window_start=_safe_float(data.get("window_start", 0.0)),
            window_end=_safe_float(data.get("window_end", 0.0)),
            source_event_ids=list(data.get("source_event_ids", []) or []),
            insights=insights,
            suggested_changes=changes,
            confidence=_safe_float(data.get("confidence", 0.5)),
            evidence_strength=_safe_float(data.get("evidence_strength", 0.5)),
            created_at=_safe_float(data.get("created_at", 0.0)),
            metadata=dict(data.get("metadata", {}) or {}),
            reflection_id=str(data.get("reflection_id", "") or _new_id("ref")),
            schema_version=str(data.get("schema_version", REFLECTION_RESULT_SCHEMA_VERSION) or REFLECTION_RESULT_SCHEMA_VERSION),
        )

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def has_evidence(self) -> bool:
        """所有 Suggestion 都有 evidence。"""
        if not self.suggested_changes:
            return True
        return all(s.has_evidence() for s in self.suggested_changes)

    def all_insights_have_evidence(self) -> bool:
        """所有 Insight 都有 evidence。"""
        if not self.insights:
            return True
        return all(i.has_evidence() for i in self.insights)

    def is_valid(self) -> bool:
        """是否合法: reflection_id 非空、类型合法、窗口合法。"""
        if not self.reflection_id:
            return False
        if self.reflection_type not in ALL_REFLECTION_TYPES:
            return False
        try:
            if self.window_end < self.window_start:
                return False
        except Exception:
            return False
        return True

    @property
    def insight_count(self) -> int:
        return len(self.insights)

    @property
    def suggestion_count(self) -> int:
        return len(self.suggested_changes)

    def summary(self) -> Dict[str, Any]:
        """返回用于历史记录存储的摘要。"""
        return {
            "reflection_id": self.reflection_id,
            "reflection_type": self.reflection_type,
            "triggered_at": self.triggered_at,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "insight_count": self.insight_count,
            "suggestion_count": self.suggestion_count,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "source_event_count": len(self.source_event_ids),
        }

    def __repr__(self) -> str:
        return (
            f"ReflectionResult(id={self.reflection_id!r}, "
            f"type={self.reflection_type!r}, "
            f"insights={self.insight_count}, "
            f"suggestions={self.suggestion_count}, "
            f"confidence={self.confidence:.3f})"
        )


def _safe_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except Exception:
        return 0.0


# ============================================================
# 工厂
# ============================================================
def build_empty_reflection_result(
    reflection_type: str = REFLECTION_TYPE_DAILY,
    *,
    now: float = 0.0,
) -> ReflectionResult:
    """构造一个空的 ReflectionResult。"""
    return ReflectionResult(
        reflection_type=str(reflection_type or REFLECTION_TYPE_DAILY),
        triggered_at=float(now),
        window_start=float(now),
        window_end=float(now),
        created_at=float(now),
    )


__all__ = [
    # 常量
    "REFLECTION_RESULT_SCHEMA_VERSION",
    "REFLECTION_TYPE_DAILY",
    "REFLECTION_TYPE_EVENT",
    "REFLECTION_TYPE_GROWTH",
    "ALL_REFLECTION_TYPES",
    "INSIGHT_CATEGORY_PATTERN",
    "INSIGHT_CATEGORY_PREFERENCE",
    "INSIGHT_CATEGORY_GROWTH",
    "INSIGHT_CATEGORY_CONCERN",
    "ALL_INSIGHT_CATEGORIES",
    "MAX_INSIGHTS",
    "MAX_SUGGESTIONS",
    # 数据类
    "Insight",
    "StateChangeSuggestion",
    "ReflectionResult",
    # 工厂
    "build_empty_reflection_result",
]
