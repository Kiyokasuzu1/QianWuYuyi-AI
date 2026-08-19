# -*- coding: utf-8 -*-
"""
src/runtime/self_model/change_log.py

Phase 5.0-D3-A: Self Model System —— ChangeLog

职责:
- 保存 SelfModel 所有 ChangeRecord
- 线程安全(RLock)
- 容量限制 + FIFO 淘汰
- 提供按 target / kind / source / 时间窗口 / evidence 过滤的查询
- 不写文件,纯内存;序列化由调用方负责

约束:
- 仅依赖 Python 标准库
- 不 import 任何业务模块
- 不调用 LLM / DB / Network
- 时间戳由调用方通过 Clock 注入生成
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.runtime.self_model.self_state import (
    ChangeRecord,
    VALID_CHANGE_KINDS,
    VALID_CHANGE_SOURCES,
)


# ============================================================
# 常量
# ============================================================
CHANGE_LOG_SCHEMA_VERSION = "1.0"

# 默认容量
DEFAULT_CHANGE_LOG_CAPACITY = 1024
# 上限(防止异常配置导致内存爆炸)
MAX_CHANGE_LOG_CAPACITY = 65536


# ============================================================
# ChangeLog 异常
# ============================================================
class ChangeLogError(Exception):
    """ChangeLog 错误基类。"""


# ============================================================
# 工具
# ============================================================
def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:  # NaN
        return default
    return v


def _clip_capacity(value: Any) -> int:
    n = _safe_int(value, 0)
    if n < 0:
        return 0
    if n > MAX_CHANGE_LOG_CAPACITY:
        return MAX_CHANGE_LOG_CAPACITY
    return n


# ============================================================
# ChangeLog
# ============================================================
class ChangeLog:
    """SelfModel 变化日志(线程安全、容量限制)。

    字段:
    - name:           str                # 日志名
    - capacity:       int                # 容量上限(0=无限制,但 <= MAX_CHANGE_LOG_CAPACITY)
    - closed:         bool               # 是否关闭
    - buffer:         deque              # 内部缓冲(LIFO/FIFO 都支持,但 append 时按 FIFO 淘汰)

    方法:
    - append(record) -> bool
    - extend(records) -> int
    - records() -> List[ChangeRecord]
    - latest(limit=10) -> List[ChangeRecord]
    - query(target=None, kind=None, source=None, since=0.0, until=0.0,
            target_id=None, evidence=None) -> List[ChangeRecord]
    - by_target(target) -> List[ChangeRecord]
    - by_evidence(event_id) -> List[ChangeRecord]
    - by_target_id(target, target_id) -> List[ChangeRecord]
    - since(timestamp) -> List[ChangeRecord]
    - clear() -> None
    - close() -> None
    - describe() / to_dict()
    """

    def __init__(
        self,
        *,
        name: str = "self_model_change_log",
        capacity: int = DEFAULT_CHANGE_LOG_CAPACITY,
    ) -> None:
        self._name = str(name or "self_model_change_log")
        self._capacity = _clip_capacity(capacity)
        self._lock = threading.RLock()
        if self._capacity > 0:
            self._buffer: deque = deque(maxlen=self._capacity)
        else:
            self._buffer = deque()
        self._closed = False
        # 统计
        self._total_appended = 0
        self._total_dropped = 0
        self._total_evicted = 0
        self._total_queried = 0
        self._last_appended_id: str = ""
        self._last_appended_at: float = 0.0
        self._last_error: str = ""
        # 索引(可选,加速查询)
        self._by_target_index: Dict[str, List[int]] = {}
        self._by_evidence_index: Dict[str, List[int]] = {}

    # --------------------------------------------------------
    # 基础属性
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
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def total_appended(self) -> int:
        with self._lock:
            return self._total_appended

    @property
    def total_dropped(self) -> int:
        with self._lock:
            return self._total_dropped

    @property
    def total_evicted(self) -> int:
        with self._lock:
            return self._total_evicted

    @property
    def total_queried(self) -> int:
        with self._lock:
            return self._total_queried

    @property
    def last_appended_id(self) -> str:
        with self._lock:
            return self._last_appended_id

    @property
    def last_appended_at(self) -> float:
        with self._lock:
            return self._last_appended_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def __len__(self) -> int:
        return self.size

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(self, record: Any, *, silent: bool = True) -> bool:
        """追加一条 ChangeRecord。

        - 失败时: silent=True 静默返回 False(并增加 total_dropped)
                   silent=False 抛 ChangeLogError
        - 容量超限: FIFO 淘汰最早的
        """
        if not isinstance(record, ChangeRecord):
            self._record_error(f"need ChangeRecord, got {type(record).__name__}")
            if silent:
                with self._lock:
                    self._total_dropped += 1
                return False
            raise ChangeLogError(
                f"需要 ChangeRecord,实际: {type(record).__name__}"
            )
        if not record.is_valid():
            self._record_error("record not valid")
            if silent:
                with self._lock:
                    self._total_dropped += 1
                return False
            raise ChangeLogError("ChangeRecord 缺少必填字段")

        with self._lock:
            if self._closed:
                self._total_dropped += 1
                self._record_error("log closed")
                if silent:
                    return False
                raise ChangeLogError("ChangeLog 已关闭")

            # 计算可能的淘汰
            if self._capacity > 0 and len(self._buffer) >= self._capacity:
                # deque(maxlen=capacity) 会自动淘汰,这里计数
                self._total_evicted += 1
                # 同时清理索引:在追加新条目时整体重建(简单可靠)
                # 简化:在淘汰时 lazy 重建
            self._buffer.append(record)
            self._total_appended += 1
            self._last_appended_id = record.change_id
            self._last_appended_at = record.timestamp
            self._maybe_rebuild_indices_locked()
            return True

    def extend(self, records: Iterable[Any], *, silent: bool = True) -> int:
        """批量追加,返回成功数量。"""
        n = 0
        if records is None:
            return 0
        try:
            for r in records:
                if self.append(r, silent=silent):
                    n += 1
        except Exception as exc:
            self._record_error(f"extend failed: {exc}")
            if not silent:
                raise
        return n

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def records(self) -> List[ChangeRecord]:
        """返回所有 ChangeRecord 列表(浅拷贝,不可修改内部 buffer)。"""
        with self._lock:
            self._total_queried += 1
            return list(self._buffer)

    def latest(self, limit: int = 10) -> List[ChangeRecord]:
        """返回最近的 N 条记录(按 timestamp 降序)。"""
        n = _safe_int(limit, 0)
        if n <= 0:
            return []
        with self._lock:
            self._total_queried += 1
            ordered = sorted(
                self._buffer,
                key=lambda r: (r.timestamp, r.change_id),
                reverse=True,
            )
            return list(ordered[:n])

    def query(
        self,
        *,
        target: Optional[str] = None,
        kind: Optional[str] = None,
        source: Optional[str] = None,
        since: float = 0.0,
        until: float = 0.0,
        target_id: Optional[str] = None,
        evidence: Optional[str] = None,
        limit: int = 0,
    ) -> List[ChangeRecord]:
        """综合查询。

        - target:      按 target 过滤(trait/capability/interest/identity 等)
        - kind:        按 change_kind 过滤
        - source:      按 change_source 过滤
        - since:       timestamp >= since
        - until:       timestamp <= until (0 表示无上限)
        - target_id:   按 target_id 过滤
        - evidence:    任一 evidence_event_id 命中
        - limit:       返回数量上限(0=无)
        """
        since_v = _safe_float(since, 0.0)
        until_v = _safe_float(until, 0.0)
        limit_v = _safe_int(limit, 0)

        with self._lock:
            self._total_queried += 1
            out: List[ChangeRecord] = []
            for r in self._buffer:
                if target is not None and r.target != target:
                    continue
                if kind is not None and r.change_kind != kind:
                    continue
                if source is not None and r.change_source != source:
                    continue
                if target_id is not None and r.target_id != target_id:
                    continue
                if since_v > 0 and r.timestamp < since_v:
                    continue
                if until_v > 0 and r.timestamp > until_v:
                    continue
                if evidence is not None:
                    if evidence not in r.evidence_event_ids:
                        continue
                out.append(r)
            # 按 timestamp 升序
            out.sort(key=lambda r: (r.timestamp, r.change_id))
            if limit_v > 0:
                out = out[-limit_v:]
            return out

    def by_target(self, target: str) -> List[ChangeRecord]:
        """按 target 查询。"""
        return self.query(target=target)

    def by_target_id(self, target: str, target_id: str) -> List[ChangeRecord]:
        """按 (target, target_id) 查询。"""
        return self.query(target=target, target_id=target_id)

    def by_evidence(self, event_id: str) -> List[ChangeRecord]:
        """按 evidence_event_id 查询。"""
        if not event_id:
            return []
        with self._lock:
            self._total_queried += 1
            out: List[ChangeRecord] = []
            for r in self._buffer:
                if event_id in r.evidence_event_ids:
                    out.append(r)
            out.sort(key=lambda r: (r.timestamp, r.change_id))
            return out

    def by_kind(self, kind: str) -> List[ChangeRecord]:
        """按 change_kind 查询。"""
        if kind not in VALID_CHANGE_KINDS:
            return []
        return self.query(kind=kind)

    def by_source(self, source: str) -> List[ChangeRecord]:
        """按 change_source 查询。"""
        if source not in VALID_CHANGE_SOURCES:
            return []
        return self.query(source=source)

    def since(self, timestamp: float) -> List[ChangeRecord]:
        """返回 timestamp >= 阈值的记录(按时间升序)。"""
        return self.query(since=timestamp)

    def between(self, since: float, until: float) -> List[ChangeRecord]:
        """返回 [since, until] 时间窗口内的记录(按时间升序)。"""
        return self.query(since=since, until=until)

    def count_by_target(self, target: str) -> int:
        return len(self.by_target(target))

    def count_by_kind(self, kind: str) -> int:
        return len(self.by_kind(kind))

    def count_by_evidence(self, event_id: str) -> int:
        return len(self.by_evidence(event_id))

    def count_by_source(self, source: str) -> int:
        return len(self.by_source(source))

    def contains_evidence(self, event_id: str) -> bool:
        if not event_id:
            return False
        with self._lock:
            for r in self._buffer:
                if event_id in r.evidence_event_ids:
                    return True
        return False

    # --------------------------------------------------------
    # 索引管理(简化版:append 时全量重建)
    # --------------------------------------------------------
    def _maybe_rebuild_indices_locked(self) -> None:
        """在锁内被调用:简化策略,按需重建。

        为降低开销,仅在 _total_appended % 64 == 0 时重建;
        否则提供基于线性扫描的兼容路径。
        """
        # 当前简化:不使用复杂索引,所有查询走线性扫描
        # 但提供索引占位字段,供后续扩展
        return None

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> int:
        """清空日志,返回清空条数。"""
        with self._lock:
            n = len(self._buffer)
            self._buffer.clear()
            self._by_target_index.clear()
            self._by_evidence_index.clear()
            return n

    def close(self) -> None:
        """关闭日志(关闭后 append 会被拒绝,但仍可读)。"""
        with self._lock:
            self._closed = True

    def is_empty(self) -> bool:
        return self.size == 0

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "schema_version": CHANGE_LOG_SCHEMA_VERSION,
                "capacity": self._capacity,
                "size": len(self._buffer),
                "is_closed": self._closed,
                "total_appended": self._total_appended,
                "total_dropped": self._total_dropped,
                "total_evicted": self._total_evicted,
                "total_queried": self._total_queried,
                "last_appended_id": self._last_appended_id,
                "last_appended_at": self._last_appended_at,
                "last_error": self._last_error,
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "schema_version": CHANGE_LOG_SCHEMA_VERSION,
                "capacity": self._capacity,
                "size": len(self._buffer),
                "is_closed": self._closed,
                "total_appended": self._total_appended,
                "total_dropped": self._total_dropped,
                "total_evicted": self._total_evicted,
                "records": [r.to_dict() for r in self._buffer],
            }

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _record_error(self, msg: str) -> None:
        with self._lock:
            self._last_error = str(msg or "")


# ============================================================
# 工厂
# ============================================================
def build_default_change_log(
    *,
    name: str = "self_model_change_log",
    capacity: int = DEFAULT_CHANGE_LOG_CAPACITY,
) -> ChangeLog:
    """构造默认 ChangeLog。"""
    return ChangeLog(name=name, capacity=capacity)


__all__ = [
    "CHANGE_LOG_SCHEMA_VERSION",
    "DEFAULT_CHANGE_LOG_CAPACITY",
    "MAX_CHANGE_LOG_CAPACITY",
    "ChangeLogError",
    "ChangeLog",
    "build_default_change_log",
]
