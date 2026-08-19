# -*- coding: utf-8 -*-
"""
src/runtime/initiative/interest_signal.py

Phase 5.0-D3-C: InterestSignal 信号。

职责:
- 表达"羽依认为某个 topic 值得关注"
- 趋势:new/rising/stable/fading
- 必须有来源事件/来源反思
- 线程安全(由 InterestSignalRegistry 维护)

约束:
- 不依赖业务模块
- 仅 Python 标准库
- 不调用 LLM / DB / Network
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 常量
# ============================================================
INTEREST_SIGNAL_SCHEMA_VERSION = "1.0"

# 趋势取值
INTEREST_TREND_NEW = "new"
INTEREST_TREND_RISING = "rising"
INTEREST_TREND_STABLE = "stable"
INTEREST_TREND_FADING = "fading"
ALL_INTEREST_TRENDS = (
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    INTEREST_TREND_FADING,
)

DEFAULT_TREND = INTEREST_TREND_NEW
DEFAULT_STRENGTH = 0.5
DEFAULT_CONFIDENCE = 0.5

# 来源要求
MIN_SOURCES_REQUIRED = 1  # 至少 1 个 source
MAX_SOURCES = 64
MAX_TOPIC_LEN = 128
MAX_RATIONALE_LEN = 512


def _new_signal_id() -> str:
    import uuid
    return f"sig_{uuid.uuid4().hex[:12]}"


# ============================================================
# 数据类
# ============================================================
@dataclass
class InterestSignal:
    """兴趣信号。

    字段:
    - signal_id:               str
    - topic:                   兴趣主题
    - strength:                兴趣强度 [0, 1]
    - source_event_ids:        触发该信号的事件 ID 列表(必须非空)
    - source_reflection_ids:   触发该信号的反思 ID 列表(可空)
    - trend:                   趋势 new/rising/stable/fading
    - confidence:              信号置信度 [0, 1]
    - created_at:              创建时间(epoch seconds)
    - last_seen_at:            最近被识别时间
    - rationale:               说明(可选)
    - metadata:                扩展元数据
    - version:                 schema 版本
    """

    signal_id: str = field(default_factory=_new_signal_id)
    topic: str = ""
    strength: float = DEFAULT_STRENGTH
    source_event_ids: List[str] = field(default_factory=list)
    source_reflection_ids: List[str] = field(default_factory=list)
    trend: str = DEFAULT_TREND
    confidence: float = DEFAULT_CONFIDENCE
    created_at: float = 0.0
    last_seen_at: float = 0.0
    rationale: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    version: str = INTEREST_SIGNAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        # topic
        if not isinstance(self.topic, str):
            self.topic = str(self.topic or "")
        if len(self.topic) > MAX_TOPIC_LEN:
            self.topic = self.topic[:MAX_TOPIC_LEN]
        # strength
        self.strength = _clip01(self.strength, default=DEFAULT_STRENGTH)
        # trend
        if self.trend not in ALL_INTEREST_TRENDS:
            self.trend = DEFAULT_TREND
        # confidence
        self.confidence = _clip01(self.confidence, default=DEFAULT_CONFIDENCE)
        # source_event_ids
        self.source_event_ids = _clean_id_list(self.source_event_ids, limit=MAX_SOURCES)
        self.source_reflection_ids = _clean_id_list(self.source_reflection_ids, limit=MAX_SOURCES)
        # 时间戳
        try:
            self.created_at = float(self.created_at)
        except Exception:
            self.created_at = 0.0
        try:
            self.last_seen_at = float(self.last_seen_at)
        except Exception:
            self.last_seen_at = 0.0
        # rationale
        if not isinstance(self.rationale, str):
            self.rationale = str(self.rationale or "")
        if len(self.rationale) > MAX_RATIONALE_LEN:
            self.rationale = self.rationale[:MAX_RATIONALE_LEN]
        # metadata
        if not isinstance(self.metadata, dict):
            try:
                self.metadata = dict(self.metadata) if self.metadata else {}
            except Exception:
                self.metadata = {}
        if len(self.metadata) > 16:
            keys = list(self.metadata.keys())[:16]
            self.metadata = {k: self.metadata[k] for k in keys}

    # --------------------------------------------------------
    # 校验
    # --------------------------------------------------------
    def has_source(self) -> bool:
        """是否拥有至少 1 个事件来源(任何来源:event 或 reflection)。"""
        return len(self.source_event_ids) > 0 or len(self.source_reflection_ids) > 0

    def is_valid(self) -> bool:
        """合法:topic 非空,有来源,字段在范围内。"""
        if not self.topic:
            return False
        if not self.has_source():
            return False
        if not (0.0 <= self.strength <= 1.0):
            return False
        if not (0.0 <= self.confidence <= 1.0):
            return False
        if self.trend not in ALL_INTEREST_TRENDS:
            return False
        return True

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InterestSignal":
        if not isinstance(data, dict):
            return cls(topic="invalid")
        try:
            strength = float(data.get("strength", DEFAULT_STRENGTH))
        except Exception:
            strength = DEFAULT_STRENGTH
        try:
            confidence = float(data.get("confidence", DEFAULT_CONFIDENCE))
        except Exception:
            confidence = DEFAULT_CONFIDENCE
        try:
            created_at = float(data.get("created_at", 0.0))
        except Exception:
            created_at = 0.0
        try:
            last_seen_at = float(data.get("last_seen_at", 0.0))
        except Exception:
            last_seen_at = 0.0
        return cls(
            signal_id=str(data.get("signal_id") or _new_signal_id()),
            topic=str(data.get("topic", "") or ""),
            strength=strength,
            source_event_ids=_safe_str_list(data.get("source_event_ids")),
            source_reflection_ids=_safe_str_list(data.get("source_reflection_ids")),
            trend=str(data.get("trend", DEFAULT_TREND) or DEFAULT_TREND),
            confidence=confidence,
            created_at=created_at,
            last_seen_at=last_seen_at,
            rationale=str(data.get("rationale", "") or ""),
            metadata=_safe_dict(data.get("metadata")),
        )

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def decay(self, factor: float = 0.95) -> None:
        """降低 strength(被新的刺激重置时通常不应调用)。"""
        try:
            f = float(factor)
        except Exception:
            f = 0.95
        if f <= 0.0:
            f = 0.0
        if f > 1.0:
            f = 1.0
        self.strength = self.strength * f
        if self.strength < 0.0:
            self.strength = 0.0
        if self.strength > 1.0:
            self.strength = 1.0

    def set_trend(self, new_trend: str) -> None:
        if new_trend in ALL_INTEREST_TRENDS:
            self.trend = str(new_trend)

    def touch(self, now: float) -> None:
        try:
            self.last_seen_at = float(now)
        except Exception:
            self.last_seen_at = 0.0

    def summary(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "topic": self.topic,
            "trend": self.trend,
            "strength": self.strength,
            "confidence": self.confidence,
            "source_count": len(self.source_event_ids) + len(self.source_reflection_ids),
        }

    def __repr__(self) -> str:
        return (
            f"InterestSignal(id={self.signal_id!r}, topic={self.topic!r}, "
            f"trend={self.trend!r}, strength={self.strength:.2f})"
        )


# ============================================================
# 容器:InterestSignalRegistry
# ============================================================
class InterestSignalRegistry:
    """兴趣信号注册表(线程安全,FIFO 容量限制)。

    职责:
    - 注册/查询/删除 InterestSignal
    - 按 topic 索引
    - 按 trend 过滤
    - 容量控制
    """

    def __init__(
        self,
        *,
        name: str = "interest_signal_registry",
        capacity: int = 256,
    ) -> None:
        self._name = str(name or "interest_signal_registry")
        try:
            cap = int(capacity)
        except Exception:
            cap = 256
        if cap < 0:
            cap = 0
        self._capacity = cap
        self._lock = threading.RLock()
        self._by_id: Dict[str, InterestSignal] = {}
        self._by_topic: Dict[str, List[str]] = {}
        self._insertion_order: list = []
        self._dropped = 0
        self._added = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._insertion_order)

    @property
    def added_count(self) -> int:
        with self._lock:
            return self._added

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped

    def __len__(self) -> int:
        return self.size

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def add(self, signal: Any) -> bool:
        if not isinstance(signal, InterestSignal):
            with self._lock:
                self._dropped += 1
            return False
        if not signal.is_valid():
            with self._lock:
                self._dropped += 1
            return False
        with self._lock:
            # 容量限制
            if self._capacity > 0 and len(self._insertion_order) >= self._capacity:
                # 淘汰最早的
                try:
                    oldest_id = self._insertion_order.pop(0)
                except Exception:
                    oldest_id = ""
                if oldest_id:
                    self._remove_locked(oldest_id)
                    self._dropped += 1
            self._by_id[signal.signal_id] = signal
            self._insertion_order.append(signal.signal_id)
            topic = signal.topic
            lst = self._by_topic.setdefault(topic, [])
            if signal.signal_id not in lst:
                lst.append(signal.signal_id)
            self._added += 1
        return True

    def _remove_locked(self, signal_id: str) -> bool:
        sig = self._by_id.pop(signal_id, None)
        if sig is None:
            return False
        try:
            self._insertion_order.remove(signal_id)
        except Exception:
            pass
        topic = sig.topic
        lst = self._by_topic.get(topic, [])
        if signal_id in lst:
            try:
                lst.remove(signal_id)
            except Exception:
                pass
        if not lst and topic in self._by_topic:
            try:
                del self._by_topic[topic]
            except Exception:
                pass
        return True

    def remove(self, signal_id: str) -> bool:
        if not signal_id:
            return False
        with self._lock:
            return self._remove_locked(str(signal_id))

    def clear(self) -> int:
        with self._lock:
            n = len(self._insertion_order)
            self._by_id.clear()
            self._by_topic.clear()
            self._insertion_order.clear()
            return n

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get(self, signal_id: str) -> Optional[InterestSignal]:
        if not signal_id:
            return None
        with self._lock:
            return self._by_id.get(str(signal_id))

    def find_by_topic(self, topic: str) -> List[InterestSignal]:
        if not topic:
            return []
        with self._lock:
            ids = list(self._by_topic.get(topic, []))
            return [self._by_id[i] for i in ids if i in self._by_id]

    def find_by_trend(self, trend: str) -> List[InterestSignal]:
        if trend not in ALL_INTEREST_TRENDS:
            return []
        with self._lock:
            return [s for s in self._by_id.values() if s.trend == trend]

    def find_by_strength(self, min_strength: float = 0.0) -> List[InterestSignal]:
        try:
            t = float(min_strength)
        except Exception:
            t = 0.0
        with self._lock:
            return [s for s in self._by_id.values() if s.strength >= t]

    def list(self, *, limit: Optional[int] = None) -> List[InterestSignal]:
        with self._lock:
            data = [self._by_id[i] for i in self._insertion_order if i in self._by_id]
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                data = data[:n]
            except Exception:
                pass
        return list(data)

    def has_topic(self, topic: str) -> bool:
        if not topic:
            return False
        with self._lock:
            return topic in self._by_topic

    def topic_count(self) -> int:
        with self._lock:
            return len(self._by_topic)

    def trend_counts(self) -> Dict[str, int]:
        with self._lock:
            d: Dict[str, int] = {t: 0 for t in ALL_INTEREST_TRENDS}
            for s in self._by_id.values():
                d[s.trend] = d.get(s.trend, 0) + 1
            return d

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def update_signal(
        self,
        signal_id: str,
        *,
        trend: Optional[str] = None,
        strength: Optional[float] = None,
        confidence: Optional[float] = None,
        append_event_id: Optional[str] = None,
        append_reflection_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        if not signal_id:
            return False
        with self._lock:
            sig = self._by_id.get(str(signal_id))
            if sig is None:
                return False
            if trend is not None and trend in ALL_INTEREST_TRENDS:
                sig.trend = str(trend)
            if strength is not None:
                sig.strength = _clip01(strength, default=sig.strength)
            if confidence is not None:
                sig.confidence = _clip01(confidence, default=sig.confidence)
            if append_event_id:
                if append_event_id not in sig.source_event_ids:
                    sig.source_event_ids.append(append_event_id)
                    if len(sig.source_event_ids) > MAX_SOURCES:
                        sig.source_event_ids = sig.source_event_ids[-MAX_SOURCES:]
            if append_reflection_id:
                if append_reflection_id not in sig.source_reflection_ids:
                    sig.source_reflection_ids.append(append_reflection_id)
                    if len(sig.source_reflection_ids) > MAX_SOURCES:
                        sig.source_reflection_ids = sig.source_reflection_ids[-MAX_SOURCES:]
            if now is not None:
                sig.last_seen_at = float(now)
        return True

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "capacity": self._capacity,
                "size": len(self._insertion_order),
                "added": self._added,
                "dropped": self._dropped,
                "topic_count": len(self._by_topic),
                "trend_counts": self.trend_counts(),
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InterestSignalRegistry(name={self._name!r}, "
                f"size={len(self._insertion_order)}/{self._capacity})"
            )


# ============================================================
# 工厂
# ============================================================
def build_default_interest_signal_registry(
    *,
    capacity: int = 256,
) -> InterestSignalRegistry:
    return InterestSignalRegistry(capacity=capacity)


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


def _safe_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return _clean_id_list(value, limit=MAX_SOURCES)
    return []


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): value[k] for k in list(value.keys())[:16]}
    return {}


__all__ = [
    # 常量
    "INTEREST_SIGNAL_SCHEMA_VERSION",
    "ALL_INTEREST_TRENDS",
    "INTEREST_TREND_NEW",
    "INTEREST_TREND_RISING",
    "INTEREST_TREND_STABLE",
    "INTEREST_TREND_FADING",
    "MIN_SOURCES_REQUIRED",
    # 数据类
    "InterestSignal",
    # 容器
    "InterestSignalRegistry",
    # 工厂
    "build_default_interest_signal_registry",
]
