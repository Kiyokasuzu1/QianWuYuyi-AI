# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_memory.py

Phase C.9.0 Initiative Memory.

只负责:
  - 记录 InitiativeEngine 决策历史(initiative_id / trigger / reason / priority /
    result / timestamp)
  - 提供 record() / get_history() / get_latest() 等查询接口
  - 提供频率统计接口(get_decision_count_in_window / get_initiated_count_in_window)
  - 不修改主 Memory System
  - 不调用 LLM
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

INITIATIVE_MEMORY_SCHEMA_VERSION = "1.0"
INITIATIVE_MEMORY_NAME = "initiative_memory"
INITIATIVE_MEMORY_VERSION = "1.0.0"

# Result types
RESULT_INITIATED = "initiated"
RESULT_SUPPRESSED = "suppressed"
RESULT_DEFERRED = "deferred"
RESULT_FAILED = "failed"
RESULT_DEGRADED = "degraded"

ALL_RESULTS: FrozenSet[str] = frozenset({
    RESULT_INITIATED,
    RESULT_SUPPRESSED,
    RESULT_DEFERRED,
    RESULT_FAILED,
    RESULT_DEGRADED,
})

# Default
DEFAULT_MAX_HISTORY = 1000
DEFAULT_TRIGGER = "none"


# ============================================================
# Utilities
# ============================================================


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:
            return "1970-01-01T00:00:00Z"


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _new_id() -> str:
    try:
        return f"init_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"init_{int(_now_ts() * 1000):x}"
        except Exception:
            return "init_0000000000000000"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except Exception:
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:
        return []


def _safe_deepcopy(v: Any) -> Any:
    try:
        return copy.deepcopy(v)
    except Exception:
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# InitiativeMemoryRecord
# ============================================================


def build_initiative_memory_record(
    initiative_id: str,
    trigger: str,
    reason: List[str],
    priority: float,
    result: str,
    timestamp: str,
    confidence: float = 0.0,
    user_id: str = "",
    actor: str = "runtime",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构造一条 InitiativeMemoryRecord。"""
    return {
        "schema_version": INITIATIVE_MEMORY_SCHEMA_VERSION,
        "initiative_id": _safe_str(initiative_id, ""),
        "trigger": _safe_str(trigger, DEFAULT_TRIGGER),
        "reason": _safe_list(reason),
        "priority": _safe_float(priority, 0.0),
        "result": _safe_str(result, RESULT_SUPPRESSED),
        "timestamp": _safe_str(timestamp, "") or _now_iso(),
        "confidence": _safe_float(confidence, 0.0),
        "user_id": _safe_str(user_id, ""),
        "actor": _safe_str(actor, "runtime") or "runtime",
        "extra": _safe_deepcopy(extra) if isinstance(extra, dict) else {},
    }


# ============================================================
# InitiativeMemoryStore
# ============================================================


class InitiativeMemoryStore:
    """
    Initiative Memory Store (Phase C.9.0 / v1.0).

    只写自己,绝不修改主 Memory System。
    """

    SCHEMA_VERSION = INITIATIVE_MEMORY_SCHEMA_VERSION
    NAME = INITIATIVE_MEMORY_NAME
    VERSION = INITIATIVE_MEMORY_VERSION

    def __init__(
        self,
        max_history: int = DEFAULT_MAX_HISTORY,
        external_store: Any = None,
    ) -> None:
        self._max_history = max(1, int(max_history))
        self._records: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._external = external_store
        # 触发器统计:trigger -> count
        self._trigger_count: Dict[str, int] = {}

    def record(
        self,
        initiative_id: str,
        trigger: str,
        reason: List[str],
        priority: float,
        result: str,
        timestamp: Optional[str] = None,
        confidence: float = 0.0,
        user_id: str = "",
        actor: str = "runtime",
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """记录一次决策。"""
        try:
            with self._lock:
                rid = _safe_str(initiative_id, "") or _new_id()
                ts = _safe_str(timestamp, "") or _now_iso()
                rec = build_initiative_memory_record(
                    initiative_id=rid,
                    trigger=trigger,
                    reason=reason,
                    priority=priority,
                    result=result,
                    timestamp=ts,
                    confidence=confidence,
                    user_id=user_id,
                    actor=actor,
                    extra=extra,
                )
                self._records.append(rec)
                # 触发器统计
                t = _safe_str(trigger, DEFAULT_TRIGGER)
                self._trigger_count[t] = self._trigger_count.get(t, 0) + 1
                # 截断
                if len(self._records) > self._max_history:
                    self._records = self._records[-self._max_history:]
                # 外部存储
                self._write_external(rec)
                return True
        except Exception as exc:
            logger.warning("[InitiativeMemory] record failed: %s", exc)
            return False

    def get_history(
        self,
        limit: Optional[int] = None,
        trigger: Optional[str] = None,
        result: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取历史(只读副本)。"""
        try:
            with self._lock:
                items = list(self._records)
                if trigger:
                    items = [
                        r for r in items
                        if isinstance(r, dict) and r.get("trigger") == trigger
                    ]
                if result:
                    items = [
                        r for r in items
                        if isinstance(r, dict) and r.get("result") == result
                    ]
                if user_id:
                    items = [
                        r for r in items
                        if isinstance(r, dict) and r.get("user_id") == user_id
                    ]
                if limit is not None and limit >= 0:
                    items = items[-int(limit):]
                return [_safe_deepcopy(r) for r in items if isinstance(r, dict)]
        except Exception:
            return []

    def get_latest(
        self,
        trigger: Optional[str] = None,
        result: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取最近一条匹配记录。"""
        try:
            with self._lock:
                items = list(self._records)
                for r in reversed(items):
                    if not isinstance(r, dict):
                        continue
                    if trigger and r.get("trigger") != trigger:
                        continue
                    if result and r.get("result") != result:
                        continue
                    return _safe_deepcopy(r)
                return None
        except Exception:
            return None

    def get_latest_initiated(self) -> Optional[Dict[str, Any]]:
        """最近一次 result=initiated 的记录。"""
        return self.get_latest(result=RESULT_INITIATED)

    def get_decision_count_in_window(
        self,
        window_seconds: float,
        now_ts: Optional[float] = None,
    ) -> int:
        """统计最近 window_seconds 内的决策总数。"""
        try:
            with self._lock:
                ts = float(now_ts) if now_ts is not None else _now_ts()
                cut = ts - float(window_seconds)
                count = 0
                for r in self._records:
                    if not isinstance(r, dict):
                        continue
                    rts = self._parse_ts(r.get("timestamp"))
                    if rts is not None and rts >= cut:
                        count += 1
                return count
        except Exception:
            return 0

    def get_initiated_count_in_window(
        self,
        window_seconds: float,
        now_ts: Optional[float] = None,
    ) -> int:
        """统计最近 window_seconds 内 initiated 的次数。"""
        try:
            with self._lock:
                ts = float(now_ts) if now_ts is not None else _now_ts()
                cut = ts - float(window_seconds)
                count = 0
                for r in self._records:
                    if not isinstance(r, dict):
                        continue
                    if r.get("result") != RESULT_INITIATED:
                        continue
                    rts = self._parse_ts(r.get("timestamp"))
                    if rts is not None and rts >= cut:
                        count += 1
                return count
        except Exception:
            return 0

    def count(self) -> int:
        try:
            with self._lock:
                return len(self._records)
        except Exception:
            return 0

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "total_records": len(self._records),
                    "max_history": self._max_history,
                    "trigger_counts": dict(self._trigger_count),
                    "result_counts": self._result_counts(),
                }
        except Exception:
            return {
                "total_records": 0,
                "max_history": self._max_history,
                "trigger_counts": {},
                "result_counts": {},
            }

    def _result_counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for r in self._records:
            if not isinstance(r, dict):
                continue
            res = _safe_str(r.get("result"), "")
            if not res:
                continue
            out[res] = out.get(res, 0) + 1
        return out

    def reset(self) -> None:
        with self._lock:
            self._records.clear()
            self._trigger_count.clear()

    def _parse_ts(self, ts: Any) -> Optional[float]:
        try:
            if not ts:
                return None
            s = str(ts)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.timestamp()
            return dt.timestamp()
        except Exception:
            return None

    def _write_external(self, record: Dict[str, Any]) -> None:
        if self._external is None:
            return
        try:
            fn = getattr(self._external, "save_initiative_record", None)
            if callable(fn):
                try:
                    fn(_safe_deepcopy(record))
                except Exception:
                    pass
        except Exception:
            pass


def create_initiative_memory_store(
    max_history: int = DEFAULT_MAX_HISTORY,
    external_store: Any = None,
) -> InitiativeMemoryStore:
    return InitiativeMemoryStore(max_history=max_history, external_store=external_store)
