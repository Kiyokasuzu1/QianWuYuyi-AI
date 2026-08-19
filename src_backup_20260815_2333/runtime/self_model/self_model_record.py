# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_record.py

Phase C.8.7 SelfModel Evolution Record.

只负责:
  - 构造 SelfModelEvolutionRecord 数据结构
  - SelfModelEvolutionStore 持久化 / 读取
  - SelfModelVersionStore 版本化保存
  - rollback 标记
  - reflection 内容生成辅助

绝不负责:
  - 修改 Personality
  - 修改 Growth / Relationship
  - 修改 core_identity
  - 删除历史
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

SELF_MODEL_RECORD_SCHEMA_VERSION = "1.0"
SELF_MODEL_RECORD_NAME = "self_model_evolution_record"
SELF_MODEL_RECORD_VERSION = "1.0.0"

# Status
RECORD_STATUS_PENDING = "pending"
RECORD_STATUS_APPLIED = "applied"
RECORD_STATUS_REJECTED = "rejected"
RECORD_STATUS_ROLLED_BACK = "rolled_back"
ALL_RECORD_STATUSES: FrozenSet[str] = frozenset({
    RECORD_STATUS_PENDING,
    RECORD_STATUS_APPLIED,
    RECORD_STATUS_REJECTED,
    RECORD_STATUS_ROLLED_BACK,
})

# Rollback status
ROLLBACK_STATUS_NONE = "none"
ROLLBACK_STATUS_ROLLED_BACK = "rolled_back"

# Default actor
ACTOR_RUNTIME = "runtime"
ACTOR_SYSTEM = "system"
ACTOR_DEFAULT = ACTOR_RUNTIME

# Reasons
REASON_INVALID_INPUT = "invalid_input"
REASON_INVALID_RECORD = "invalid_evolution_record"
REASON_SNAPSHOT_ERROR = "snapshot_error"
REASON_RECORD_ERROR = "record_error"
REASON_VERSION_ERROR = "version_error"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"
REASON_OK = "ok"
REASON_NONE = ""


# ============================================================
# Utility functions
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


def _new_reflection_id() -> str:
    try:
        return f"smr_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"smr_{int(_now_ts() * 1000):x}"
        except Exception:
            return "smr_0000000000000000"


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


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


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
# Reflection content helpers
# ============================================================


def build_self_description_change(
    before: str,
    after: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造自我描述变化。
    """
    return {
        "field": "self_description",
        "before": _safe_str(before, ""),
        "after": _safe_str(after, ""),
        "reason": _safe_str((context or {}).get("reason"), "personality_evolution_reflection"),
        "context": _safe_deepcopy(context) if isinstance(context, dict) else {},
    }


def build_growth_understanding(
    summary: str,
    triggers: Optional[List[str]] = None,
    related_changes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    构造 growth_understanding 字段内容。
    """
    return {
        "summary": _safe_str(summary, ""),
        "triggers": _safe_list(triggers),
        "related_changes": _safe_list(related_changes),
        "generated_at": _now_iso(),
    }


def build_capability_boundary(
    capabilities: List[str],
    limits: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    构造 capability_boundary 字段内容。
    """
    return {
        "capabilities": _safe_list(capabilities),
        "limits": _safe_list(limits),
        "generated_at": _now_iso(),
    }


# ============================================================
# SelfModelEvolutionRecord
# ============================================================


def build_self_model_evolution_record(
    reflection_id: str,
    evolution_id: str,
    before_snapshot: Dict[str, Any],
    after_snapshot: Dict[str, Any],
    changes: List[Dict[str, Any]],
    reason: str,
    evidence: Dict[str, Any],
    confidence: float,
    timestamp: str,
    old_version: str = "",
    new_version: str = "",
    actor: str = ACTOR_DEFAULT,
    status: str = RECORD_STATUS_APPLIED,
    rollback_status: str = ROLLBACK_STATUS_NONE,
    rollback_of: str = "",
    rolled_back_by: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造一份标准的 SelfModelEvolutionRecord(纯数据,不可变副本)。
    """
    return {
        "schema_version": SELF_MODEL_RECORD_SCHEMA_VERSION,
        "reflection_id": _safe_str(reflection_id, ""),
        "evolution_id": _safe_str(evolution_id, ""),
        "before_snapshot": _safe_deepcopy(before_snapshot),
        "after_snapshot": _safe_deepcopy(after_snapshot),
        "changes": _safe_deepcopy(changes) if changes is not None else [],
        "reason": _safe_str(reason, REASON_NONE),
        "evidence": _safe_deepcopy(evidence) if evidence is not None else {},
        "confidence": _safe_float(confidence, 0.0),
        "timestamp": _safe_str(timestamp, "") or _now_iso(),
        "old_version": _safe_str(old_version, ""),
        "new_version": _safe_str(new_version, ""),
        "actor": _safe_str(actor, ACTOR_DEFAULT) or ACTOR_DEFAULT,
        "status": _safe_str(status, RECORD_STATUS_APPLIED),
        "rollback_status": _safe_str(rollback_status, ROLLBACK_STATUS_NONE),
        "rollback_of": _safe_str(rollback_of, ""),
        "rolled_back_by": _safe_str(rolled_back_by, ""),
        "extra": _safe_deepcopy(extra) if isinstance(extra, dict) else {},
    }


# ============================================================
# SelfModelEvolutionStore
# ============================================================


class SelfModelEvolutionStore:
    """
    SelfModel Evolution Store (Phase C.8.7 / v1.0).

    提供:
      - save(record)                  保存一条 reflection 记录
      - get(reflection_id)            按 reflection_id 读取
      - list_by_evolution(evolution_id)
      - list_all()
      - mark_rolled_back(reflection_id, by_reflection_id, audit)
    """

    SCHEMA_VERSION = SELF_MODEL_RECORD_SCHEMA_VERSION
    NAME = SELF_MODEL_RECORD_NAME
    VERSION = SELF_MODEL_RECORD_VERSION

    def __init__(self, external_store: Any = None) -> None:
        self._external = external_store
        self._records: Dict[str, Dict[str, Any]] = {}
        self._evolution_index: Dict[str, List[str]] = {}

    def save(self, record: Dict[str, Any]) -> bool:
        if not isinstance(record, dict):
            return False
        rid = _safe_str(record.get("reflection_id"), "")
        if not rid:
            return False
        try:
            snapshot = _safe_deepcopy(record)
            self._records[rid] = snapshot
            eid = _safe_str(record.get("evolution_id"), "")
            if eid:
                self._evolution_index.setdefault(eid, []).append(rid)
            self._write_external(rid, snapshot)
            return True
        except Exception as exc:
            logger.warning("[SelfModelEvolutionStore] save failed: %s", exc)
            return False

    def get(self, reflection_id: str) -> Optional[Dict[str, Any]]:
        rid = _safe_str(reflection_id, "")
        if not rid:
            return None
        try:
            r = self._records.get(rid)
            if r is not None:
                return _safe_deepcopy(r)
            return self._read_external(rid)
        except Exception:
            return None

    def list_by_evolution(self, evolution_id: str) -> List[Dict[str, Any]]:
        eid = _safe_str(evolution_id, "")
        if not eid:
            return []
        rids = list(self._evolution_index.get(eid, []))
        out: List[Dict[str, Any]] = []
        for rid in rids:
            r = self._records.get(rid)
            if r is not None:
                out.append(_safe_deepcopy(r))
        return out

    def list_all(self) -> List[Dict[str, Any]]:
        try:
            return [_safe_deepcopy(r) for r in self._records.values()]
        except Exception:
            return []

    def mark_rolled_back(
        self,
        reflection_id: str,
        by_reflection_id: str,
        audit_detail: Optional[Dict[str, Any]] = None,
    ) -> bool:
        rid = _safe_str(reflection_id, "")
        by = _safe_str(by_reflection_id, "")
        if not rid:
            return False
        try:
            r = self._records.get(rid)
            if r is None:
                return False
            r["status"] = RECORD_STATUS_ROLLED_BACK
            r["rollback_status"] = ROLLBACK_STATUS_ROLLED_BACK
            r["rolled_back_by"] = by
            r["rollback_audit"] = _safe_deepcopy(audit_detail) if isinstance(audit_detail, dict) else {}
            self._records[rid] = r
            self._write_external(rid, _safe_deepcopy(r))
            return True
        except Exception as exc:
            logger.warning("[SelfModelEvolutionStore] mark_rolled_back failed: %s", exc)
            return False

    def get_stats(self) -> Dict[str, Any]:
        try:
            return {
                "total_records": len(self._records),
                "evolution_index_size": len(self._evolution_index),
                "rolled_back_count": sum(
                    1 for r in self._records.values()
                    if isinstance(r, dict) and r.get("status") == RECORD_STATUS_ROLLED_BACK
                ),
            }
        except Exception:
            return {
                "total_records": 0,
                "evolution_index_size": 0,
                "rolled_back_count": 0,
            }

    def reset(self) -> None:
        self._records.clear()
        self._evolution_index.clear()

    # --------------------------------------------------------
    # External persistence helpers
    # --------------------------------------------------------

    def _write_external(self, reflection_id: str, record: Dict[str, Any]) -> None:
        if self._external is None:
            return
        try:
            fn = getattr(self._external, "save_reflection", None) \
                or getattr(self._external, "save_record", None)
            if callable(fn):
                try:
                    fn(reflection_id, _safe_deepcopy(record))
                except Exception:
                    pass
        except Exception:
            pass

    def _read_external(self, reflection_id: str) -> Optional[Dict[str, Any]]:
        if self._external is None:
            return None
        try:
            fn = getattr(self._external, "load_reflection", None) \
                or getattr(self._external, "get_record", None)
            if callable(fn):
                try:
                    r = fn(reflection_id)
                    if isinstance(r, dict):
                        return dict(r)
                except Exception:
                    return None
        except Exception:
            return None
        return None


# ============================================================
# SelfModelVersionStore
# ============================================================


class SelfModelVersionStore:
    """
    SelfModel Version Store (Phase C.8.7 / v1.0).

    负责:
      - create_version(snapshot, source_reflection_id) -> new_version
      - get_version(version_id)
      - get_current()
      - list_versions()
      - rollback_to(version_id) -> 恢复 current 指向
    """

    SCHEMA_VERSION = SELF_MODEL_RECORD_SCHEMA_VERSION
    NAME = "self_model_version_store"
    VERSION = "1.0.0"

    def __init__(self, initial_snapshot: Optional[Dict[str, Any]] = None) -> None:
        self._versions: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        self._current: str = ""
        if isinstance(initial_snapshot, dict) and initial_snapshot:
            self._init_baseline(initial_snapshot)

    def _init_baseline(self, snapshot: Dict[str, Any]) -> None:
        baseline_id = "v0"
        entry = {
            "version_id": baseline_id,
            "version_index": 0,
            "snapshot": _safe_deepcopy(snapshot),
            "source_reflection_id": "",
            "timestamp": _now_iso(),
            "is_baseline": True,
        }
        self._versions[baseline_id] = entry
        self._order.append(baseline_id)
        self._current = baseline_id

    def create_version(
        self,
        snapshot: Dict[str, Any],
        source_reflection_id: str = "",
    ) -> str:
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot must be dict")
        try:
            new_idx = len(self._order)
            new_id = f"v{new_idx}"
            entry = {
                "version_id": new_id,
                "version_index": new_idx,
                "snapshot": _safe_deepcopy(snapshot),
                "source_reflection_id": _safe_str(source_reflection_id, ""),
                "timestamp": _now_iso(),
                "is_baseline": False,
            }
            self._versions[new_id] = entry
            self._order.append(new_id)
            self._current = new_id
            return new_id
        except Exception as exc:
            logger.warning("[SelfModelVersionStore] create_version failed: %s", exc)
            return ""

    def get_version(self, version_id: str) -> Optional[Dict[str, Any]]:
        vid = _safe_str(version_id, "")
        if not vid:
            return None
        v = self._versions.get(vid)
        if not isinstance(v, dict):
            return None
        return _safe_deepcopy(v)

    def get_snapshot(self, version_id: str) -> Optional[Dict[str, Any]]:
        v = self.get_version(version_id)
        if v is None:
            return None
        return _safe_deepcopy(v.get("snapshot", {}))

    def get_current(self) -> Optional[Dict[str, Any]]:
        return self.get_version(self._current)

    def get_current_version_id(self) -> str:
        return _safe_str(self._current, "")

    def list_versions(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for vid in self._order:
            v = self._versions.get(vid)
            if isinstance(v, dict):
                out.append(_safe_deepcopy(v))
        return out

    def rollback_to(self, version_id: str) -> Optional[Dict[str, Any]]:
        vid = _safe_str(version_id, "")
        if not vid or vid not in self._versions:
            return None
        self._current = vid
        return self.get_snapshot(vid)

    def count(self) -> int:
        return len(self._order)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "version_count": len(self._order),
            "current_version": self._current,
            "baseline_version": self._order[0] if self._order else "",
        }

    def reset(self, initial_snapshot: Optional[Dict[str, Any]] = None) -> None:
        self._versions.clear()
        self._order.clear()
        self._current = ""
        if isinstance(initial_snapshot, dict) and initial_snapshot:
            self._init_baseline(initial_snapshot)


# ============================================================
# Factory
# ============================================================


def create_self_model_evolution_record_store(
    external_store: Any = None,
) -> SelfModelEvolutionStore:
    return SelfModelEvolutionStore(external_store=external_store)


def create_self_model_version_store(
    initial_snapshot: Optional[Dict[str, Any]] = None,
) -> SelfModelVersionStore:
    return SelfModelVersionStore(initial_snapshot=initial_snapshot)
