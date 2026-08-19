# -*- coding: utf-8 -*-
"""
src/runtime/goal/desire.py

Phase 5.0-D3-D: Desire 内在愿望倾向。

职责:
- 表达"产生目标之前的内在倾向"
- Desire 不执行行为,只是目标产生的来源
- 必须有来源(支撑信号/反思)

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 常量
# ============================================================
DESIRE_SCHEMA_VERSION = "1.0"

# trend
DESIRE_TREND_NEW = "new"
DESIRE_TREND_RISING = "rising"
DESIRE_TREND_STABLE = "stable"
DESIRE_TREND_FADING = "fading"
ALL_DESIRE_TRENDS = (
    DESIRE_TREND_NEW,
    DESIRE_TREND_RISING,
    DESIRE_TREND_STABLE,
    DESIRE_TREND_FADING,
)
DEFAULT_DESIRE_TREND = DESIRE_TREND_NEW

# 长度限制
MAX_TOPIC_LEN = 128
MAX_REASON_LEN = 512
MAX_SIGNAL_IDS = 64
MAX_REFLECTION_IDS = 64
MAX_METADATA_KEYS = 16


def _new_desire_id() -> str:
    return f"des_{uuid.uuid4().hex[:12]}"


# ============================================================
# 数据类
# ============================================================
@dataclass
class Desire:
    """内在愿望倾向。

    字段:
    - desire_id:                str
    - topic:                    主题
    - strength:                 强度 [0, 1]
    - trend:                    new/rising/stable/fading
    - reason:                   产生原因
    - supporting_signal_ids:    支撑信号 ID(evidence)
    - supporting_reflection_ids:支撑反思 ID(evidence)
    - confidence:               置信度 [0, 1]
    - created_at:               创建时间
    - updated_at:               更新时间
    - version:                  schema 版本
    - metadata:                 扩展元数据
    """

    desire_id: str = field(default_factory=_new_desire_id)
    topic: str = ""
    strength: float = 0.5
    trend: str = DEFAULT_DESIRE_TREND
    reason: str = ""
    supporting_signal_ids: List[str] = field(default_factory=list)
    supporting_reflection_ids: List[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: float = 0.0
    updated_at: float = 0.0
    version: str = DESIRE_SCHEMA_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # trend
        if self.trend not in ALL_DESIRE_TRENDS:
            self.trend = DEFAULT_DESIRE_TREND
        # 字符串裁剪
        if not isinstance(self.topic, str):
            self.topic = str(self.topic or "")
        if len(self.topic) > MAX_TOPIC_LEN:
            self.topic = self.topic[:MAX_TOPIC_LEN]
        if not isinstance(self.reason, str):
            self.reason = str(self.reason or "")
        if len(self.reason) > MAX_REASON_LEN:
            self.reason = self.reason[:MAX_REASON_LEN]
        # 数值
        self.strength = _clip01(self.strength, default=0.5)
        self.confidence = _clip01(self.confidence, default=0.5)
        # supporting ids
        self.supporting_signal_ids = _clean_id_list(self.supporting_signal_ids, limit=MAX_SIGNAL_IDS)
        self.supporting_reflection_ids = _clean_id_list(self.supporting_reflection_ids, limit=MAX_REFLECTION_IDS)
        # 时间戳
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.updated_at = float(self.updated_at)
        except Exception:
            self.updated_at = 0.0
        # metadata
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        if len(self.metadata) > MAX_METADATA_KEYS:
            keys = list(self.metadata.keys())[:MAX_METADATA_KEYS]
            self.metadata = {k: self.metadata[k] for k in keys}

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def has_evidence(self) -> bool:
        return len(self.supporting_signal_ids) > 0 or len(self.supporting_reflection_ids) > 0

    def is_valid(self) -> bool:
        if not self.topic:
            return False
        if not self.has_evidence():
            return False
        if self.trend not in ALL_DESIRE_TRENDS:
            return False
        if not (0.0 <= self.strength <= 1.0):
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        return True

    # --------------------------------------------------------
    # 趋势/强度管理
    # --------------------------------------------------------
    def set_trend(self, new_trend: str) -> bool:
        if new_trend not in ALL_DESIRE_TRENDS:
            return False
        if new_trend == self.trend:
            return True
        self.trend = new_trend
        return True

    def boost(self, delta: float = 0.05, now: Optional[float] = None) -> None:
        try:
            d = float(delta)
        except Exception:
            return
        new_v = self.strength + d
        self.strength = _clip01(new_v, default=self.strength)
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0

    def decay(self, factor: float = 0.95, now: Optional[float] = None) -> None:
        try:
            f = float(factor)
        except Exception:
            return
        if f < 0.0:
            f = 0.0
        if f > 1.0:
            f = 1.0
        self.strength = self.strength * f
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0

    def touch(self, now: Optional[float] = None) -> None:
        if now is not None:
            try:
                self.updated_at = float(now)
            except Exception:
                self.updated_at = 0.0

    def add_supporting_signal_ids(self, ids: List[str]) -> None:
        if not ids:
            return
        existing = set(self.supporting_signal_ids)
        for i in ids:
            s = str(i) if i is not None else ""
            if s and s not in existing:
                self.supporting_signal_ids.append(s)
                existing.add(s)
                if len(self.supporting_signal_ids) >= MAX_SIGNAL_IDS:
                    break

    def add_supporting_reflection_ids(self, ids: List[str]) -> None:
        if not ids:
            return
        existing = set(self.supporting_reflection_ids)
        for i in ids:
            s = str(i) if i is not None else ""
            if s and s not in existing:
                self.supporting_reflection_ids.append(s)
                existing.add(s)
                if len(self.supporting_reflection_ids) >= MAX_REFLECTION_IDS:
                    break

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Desire":
        if not isinstance(data, dict):
            return cls(topic="invalid")
        try:
            strength = float(data.get("strength", 0.5))
        except Exception:
            strength = 0.5
        try:
            confidence = float(data.get("confidence", 0.5))
        except Exception:
            confidence = 0.5
        try:
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            updated_at = float(data.get("updated_at", 0.0))
        except Exception:
            updated_at = 0.0
        return cls(
            desire_id=str(data.get("desire_id") or _new_desire_id()),
            topic=str(data.get("topic", "") or ""),
            strength=strength,
            trend=str(data.get("trend", DEFAULT_DESIRE_TREND) or DEFAULT_DESIRE_TREND),
            reason=str(data.get("reason", "") or ""),
            supporting_signal_ids=_safe_str_list(data.get("supporting_signal_ids"), limit=MAX_SIGNAL_IDS),
            supporting_reflection_ids=_safe_str_list(data.get("supporting_reflection_ids"), limit=MAX_REFLECTION_IDS),
            confidence=confidence,
            created_at=created_at,
            updated_at=updated_at,
            version=str(data.get("version", DESIRE_SCHEMA_VERSION) or DESIRE_SCHEMA_VERSION),
            metadata=_safe_dict(data.get("metadata")),
        )

    def summary(self) -> Dict[str, Any]:
        return {
            "desire_id": self.desire_id,
            "topic": self.topic,
            "strength": self.strength,
            "trend": self.trend,
            "confidence": self.confidence,
            "supporting_signal_count": len(self.supporting_signal_ids),
            "supporting_reflection_count": len(self.supporting_reflection_ids),
            "has_evidence": self.has_evidence(),
        }

    def __repr__(self) -> str:
        return (
            f"Desire(id={self.desire_id!r}, topic={self.topic!r}, "
            f"trend={self.trend!r}, strength={self.strength:.2f})"
        )


# ============================================================
# 容器
# ============================================================
class DesireRegistry:
    """Desire 注册表(线程安全)。"""

    def __init__(self, *, max_size: int = 1024) -> None:
        self._max_size = max(1, int(max_size))
        self._lock = threading.RLock()
        self._items: Dict[str, Desire] = {}
        self._topic_index: Dict[str, str] = {}  # topic -> desire_id
        self._added = 0
        self._removed = 0
        self._updated = 0

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)

    @property
    def added_count(self) -> int:
        with self._lock:
            return self._added

    @property
    def removed_count(self) -> int:
        with self._lock:
            return self._removed

    @property
    def updated_count(self) -> int:
        with self._lock:
            return self._updated

    def add(self, desire: Desire) -> bool:
        if not isinstance(desire, Desire):
            return False
        with self._lock:
            if desire.desire_id in self._items:
                return False
            if len(self._items) >= self._max_size:
                # 简单淘汰最早
                try:
                    oldest_key = next(iter(self._items))
                    self._items.pop(oldest_key, None)
                except Exception:
                    pass
            self._items[desire.desire_id] = desire
            # topic index: 同 topic 用最新 desire
            try:
                if desire.topic:
                    self._topic_index[desire.topic] = desire.desire_id
            except Exception:
                pass
            self._added += 1
        return True

    def get(self, desire_id: str) -> Optional[Desire]:
        if not desire_id:
            return None
        with self._lock:
            return self._items.get(desire_id)

    def get_by_topic(self, topic: str) -> Optional[Desire]:
        if not topic:
            return None
        with self._lock:
            did = self._topic_index.get(topic)
            if did is None:
                return None
            return self._items.get(did)

    def remove(self, desire_id: str) -> bool:
        if not desire_id:
            return False
        with self._lock:
            d = self._items.pop(desire_id, None)
            if d is None:
                return False
            # 清理 topic index
            try:
                if d.topic and self._topic_index.get(d.topic) == desire_id:
                    self._topic_index.pop(d.topic, None)
            except Exception:
                pass
            self._removed += 1
        return True

    def list(self) -> List[Desire]:
        with self._lock:
            return list(self._items.values())

    def find_by_topic(self, topic: str) -> List[Desire]:
        if not topic:
            return []
        with self._lock:
            return [d for d in self._items.values() if d.topic == topic]

    def clear(self) -> int:
        with self._lock:
            n = len(self._items)
            self._items.clear()
            self._topic_index.clear()
            return n

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._items),
                "max_size": self._max_size,
                "added": self._added,
                "removed": self._removed,
                "updated": self._updated,
            }

    def __repr__(self) -> str:
        with self._lock:
            return f"DesireRegistry(size={len(self._items)}, added={self._added})"


# ============================================================
# 工厂
# ============================================================
def build_desire(
    *,
    topic: str,
    strength: float = 0.5,
    reason: str = "",
    supporting_signal_ids: List[str] = None,
    supporting_reflection_ids: List[str] = None,
    confidence: float = 0.5,
    trend: str = DEFAULT_DESIRE_TREND,
    now: Optional[float] = None,
) -> Desire:
    """构造一个新 Desire。"""
    d = Desire(
        topic=topic,
        strength=strength,
        reason=reason,
        supporting_signal_ids=list(supporting_signal_ids or []),
        supporting_reflection_ids=list(supporting_reflection_ids or []),
        confidence=confidence,
        trend=trend,
    )
    try:
        if now is not None:
            d.created_at = float(now)
            d.updated_at = float(now)
    except Exception:
        pass
    return d


# ============================================================
# 工具
# ============================================================
def _clip01(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _clean_id_list(value: Any, *, limit: int) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for v in value:
        if v is None:
            continue
        s = str(v)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _safe_str_list(value: Any, *, limit: int) -> List[str]:
    if isinstance(value, list):
        return _clean_id_list(value, limit=limit)
    return []


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): value[k] for k in list(value.keys())[:MAX_METADATA_KEYS]}
    return {}


__all__ = [
    # 常量
    "DESIRE_SCHEMA_VERSION",
    "ALL_DESIRE_TRENDS",
    # trend
    "DESIRE_TREND_NEW",
    "DESIRE_TREND_RISING",
    "DESIRE_TREND_STABLE",
    "DESIRE_TREND_FADING",
    # 数据类
    "Desire",
    "DesireRegistry",
    # 工厂
    "build_desire",
]
