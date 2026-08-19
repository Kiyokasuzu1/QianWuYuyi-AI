# -*- coding: utf-8 -*-
"""
src/runtime/reflection/reflection_history.py

Phase 5.0-D3-B: Reflection 历史记录。

职责:
- 持久化每次 ReflectionResult 摘要(ReflectionRecord)
- 提供查询 / 清理 / 容量限制 (FIFO)
- 线程安全

约束:
- 不依赖业务模块
- 仅依赖 Python 标准库
- 不调用 LLM / DB / Network
- 容量限制 (默认 128,最大 4096)
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from src.runtime.reflection.reflection_result import (
    ALL_REFLECTION_TYPES,
    ReflectionResult,
)


# ============================================================
# 常量
# ============================================================
REFLECTION_HISTORY_SCHEMA_VERSION = "1.0"
DEFAULT_REFLECTION_HISTORY_CAPACITY = 128
MAX_REFLECTION_HISTORY_CAPACITY = 4096


# ============================================================
# 异常
# ============================================================
class ReflectionHistoryError(Exception):
    """ReflectionHistory 错误基类。"""


# ============================================================
# ReflectionRecord
# ============================================================
@dataclass
class ReflectionRecord:
    """一次反思的历史记录(摘要)。

    字段:
    - reflection_id:      str
    - timestamp:          float
    - type:               str
    - source_event_ids:   List[str]
    - result_summary:     Dict[str, Any]
    - confidence:         float
    - insight_count:      int
    - suggestion_count:   int
    - evidence_strength:  float
    """

    reflection_id: str = ""
    timestamp: float = 0.0
    type: str = "daily"
    source_event_ids: List[str] = field(default_factory=list)
    result_summary: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    insight_count: int = 0
    suggestion_count: int = 0
    evidence_strength: float = 0.5

    def __post_init__(self) -> None:
        self.reflection_id = str(self.reflection_id or "")
        if self.type not in ALL_REFLECTION_TYPES:
            self.type = "daily"
        try:
            self.timestamp = float(self.timestamp)
        except Exception:
            self.timestamp = 0.0
        if not isinstance(self.source_event_ids, list):
            try:
                self.source_event_ids = list(self.source_event_ids) if self.source_event_ids else []
            except Exception:
                self.source_event_ids = []
        # 去重
        seen = set()
        out: List[str] = []
        for s in self.source_event_ids:
            if s is None:
                continue
            v = str(s)
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(v)
            if len(out) >= 256:
                break
        self.source_event_ids = out
        if not isinstance(self.result_summary, dict):
            try:
                self.result_summary = dict(self.result_summary) if self.result_summary else {}
            except Exception:
                self.result_summary = {}
        # 截断
        if len(self.result_summary) > 16:
            keys = list(self.result_summary.keys())[:16]
            self.result_summary = {k: self.result_summary[k] for k in keys}
        try:
            self.confidence = float(self.confidence)
        except Exception:
            self.confidence = 0.5
        if self.confidence != self.confidence:
            self.confidence = 0.5
        if self.confidence < 0.0:
            self.confidence = 0.0
        if self.confidence > 1.0:
            self.confidence = 1.0
        try:
            self.insight_count = int(self.insight_count)
        except Exception:
            self.insight_count = 0
        if self.insight_count < 0:
            self.insight_count = 0
        try:
            self.suggestion_count = int(self.suggestion_count)
        except Exception:
            self.suggestion_count = 0
        if self.suggestion_count < 0:
            self.suggestion_count = 0
        try:
            self.evidence_strength = float(self.evidence_strength)
        except Exception:
            self.evidence_strength = 0.5
        if self.evidence_strength != self.evidence_strength:
            self.evidence_strength = 0.5
        if self.evidence_strength < 0.0:
            self.evidence_strength = 0.0
        if self.evidence_strength > 1.0:
            self.evidence_strength = 1.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["schema_version"] = REFLECTION_HISTORY_SCHEMA_VERSION
        return d

    @classmethod
    def from_result(
        cls,
        result: ReflectionResult,
        *,
        timestamp: Optional[float] = None,
    ) -> "ReflectionRecord":
        """从 ReflectionResult 派生一个 Record。"""
        if not isinstance(result, ReflectionResult):
            return cls(reflection_id="", timestamp=float(timestamp or 0.0))
        return cls(
            reflection_id=result.reflection_id,
            timestamp=float(timestamp if timestamp is not None else result.triggered_at),
            type=result.reflection_type,
            source_event_ids=list(result.source_event_ids),
            result_summary=result.summary(),
            confidence=result.confidence,
            insight_count=result.insight_count,
            suggestion_count=result.suggestion_count,
            evidence_strength=result.evidence_strength,
        )

    def is_valid(self) -> bool:
        return bool(self.reflection_id)

    def __repr__(self) -> str:
        return (
            f"ReflectionRecord(id={self.reflection_id!r}, "
            f"type={self.type!r}, insights={self.insight_count}, "
            f"suggestions={self.suggestion_count})"
        )


# ============================================================
# ReflectionHistory
# ============================================================
class ReflectionHistory:
    """反思历史记录(线程安全,FIFO 容量限制)。

    设计:
    - 内部使用 deque(maxlen=capacity) 自动 FIFO 淘汰
    - 持有 RLock 保护所有读写
    - 容量 0 时不保留任何记录(append 仍可调用,仅累加统计)
    - 任何异常被隔离(silent)
    """

    def __init__(
        self,
        *,
        name: str = "reflection_history",
        capacity: int = DEFAULT_REFLECTION_HISTORY_CAPACITY,
    ) -> None:
        self._name = str(name or "reflection_history")
        cap = int(capacity or 0)
        if cap < 0:
            cap = 0
        if cap > MAX_REFLECTION_HISTORY_CAPACITY:
            cap = MAX_REFLECTION_HISTORY_CAPACITY
        self._capacity = cap
        self._lock = threading.RLock()
        self._buffer: deque = deque(maxlen=cap) if cap > 0 else deque()
        # 统计
        self._total_appended = 0
        self._total_dropped = 0
        self._last_id: str = ""
        self._last_type: str = ""
        self._last_at: float = 0.0
        self._last_error: str = ""
        self._closed = False

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def total_appended(self) -> int:
        with self._lock:
            return self._total_appended

    @property
    def total_dropped(self) -> int:
        with self._lock:
            return self._total_dropped

    @property
    def last_id(self) -> str:
        with self._lock:
            return self._last_id

    @property
    def last_type(self) -> str:
        with self._lock:
            return self._last_type

    @property
    def last_at(self) -> float:
        with self._lock:
            return self._last_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def __len__(self) -> int:
        return self.size

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(
        self,
        record: Any,
        *,
        silent: bool = True,
    ) -> bool:
        """追加一个 ReflectionRecord。"""
        try:
            if not isinstance(record, ReflectionRecord):
                if silent:
                    with self._lock:
                        self._total_dropped += 1
                        self._last_error = "append 收到非 ReflectionRecord"
                    return False
                raise ReflectionHistoryError(
                    f"append 需要 ReflectionRecord,实际: {type(record).__name__}"
                )
            with self._lock:
                if self._closed:
                    self._total_dropped += 1
                    self._last_error = "history 已关闭"
                    return False
                if self._capacity > 0:
                    self._buffer.append(record)
                self._total_appended += 1
                self._last_id = record.reflection_id
                self._last_type = record.type
                self._last_at = float(record.timestamp)
            return True
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._total_dropped += 1
                self._last_error = f"append 失败: {exc}"
            if silent:
                return False
            raise

    def append_result(
        self,
        result: Any,
        *,
        timestamp: Optional[float] = None,
    ) -> bool:
        """从 ReflectionResult 派生一个 Record 并追加。"""
        if not isinstance(result, ReflectionResult):
            return False
        return self.append(
            ReflectionRecord.from_result(result, timestamp=timestamp),
            silent=True,
        )

    def extend(self, records: List[Any]) -> int:
        """批量追加,返回成功数量。"""
        if records is None:
            return 0
        success = 0
        for r in records:
            if self.append(r, silent=True):
                success += 1
        return success

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def list(self, *, limit: Optional[int] = None) -> List[ReflectionRecord]:
        """返回所有记录(按插入顺序,时间正序)。"""
        with self._lock:
            data = list(self._buffer)
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                data = data[-n:]
            except Exception:
                pass
        return list(data)

    def latest(self, limit: int = 10) -> List[ReflectionRecord]:
        """返回最近 N 条(按插入顺序的尾部)。

        非法 limit 一律视为 0,返回空列表。
        """
        with self._lock:
            buf = list(self._buffer)
        try:
            n = int(limit)
        except Exception:
            n = 0
        if n <= 0:
            return []
        return buf[-n:]

    def get(self, reflection_id: str) -> Optional[ReflectionRecord]:
        """按 reflection_id 查找。"""
        if not reflection_id:
            return None
        with self._lock:
            for r in self._buffer:
                if r.reflection_id == reflection_id:
                    return r
        return None

    def count_by_type(self, type_name: str) -> int:
        if type_name not in ALL_REFLECTION_TYPES:
            return 0
        with self._lock:
            return sum(1 for r in self._buffer if r.type == type_name)

    def type_counts(self) -> Dict[str, int]:
        with self._lock:
            d: Dict[str, int] = {}
            for r in self._buffer:
                d[r.type] = d.get(r.type, 0) + 1
            return d

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> int:
        """清空,返回清空前数量。"""
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
            return n

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
        return True

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "capacity": self._capacity,
                "size": len(self._buffer),
                "total_appended": self._total_appended,
                "total_dropped": self._total_dropped,
                "last_id": self._last_id,
                "last_type": self._last_type,
                "last_at": self._last_at,
                "last_error": self._last_error,
                "is_closed": self._closed,
                "type_counts": self.type_counts(),
            }

    def __repr__(self) -> str:
        return (
            f"ReflectionHistory(name={self._name!r}, "
            f"size={self.size}/{self._capacity}, "
            f"appended={self.total_appended})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_reflection_history(
    *,
    capacity: int = DEFAULT_REFLECTION_HISTORY_CAPACITY,
) -> ReflectionHistory:
    """构造默认 ReflectionHistory。"""
    return ReflectionHistory(capacity=capacity)


__all__ = [
    # 常量
    "REFLECTION_HISTORY_SCHEMA_VERSION",
    "DEFAULT_REFLECTION_HISTORY_CAPACITY",
    "MAX_REFLECTION_HISTORY_CAPACITY",
    # 异常
    "ReflectionHistoryError",
    # 数据类
    "ReflectionRecord",
    # 容器
    "ReflectionHistory",
    # 工厂
    "build_default_reflection_history",
]
