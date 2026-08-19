# -*- coding: utf-8 -*-
"""
src/runtime/evolution/personality_evolution_record.py

Phase C.8.6 Personality Evolution Record.

只负责:
  - 构造 PersonalityEvolutionRecord 数据结构
  - 通过 PersonalityEvolutionStore 持久化/读取 evolution 记录
  - 通过 PersonalityVersionStore 维护 personality 版本
  - 生成回滚 audit 元数据

绝不负责:
  - 修改 PersonalityState
  - 调用 PersonalityResolver
  - 调用 SelfModelStore
  - 自动 apply / commit
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

PERSONALITY_EVOLUTION_RECORD_SCHEMA_VERSION = "1.0"
PERSONALITY_EVOLUTION_RECORD_NAME = "personality_evolution_record"
PERSONALITY_EVOLUTION_RECORD_VERSION = "1.0.0"

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

# SelfModel impact
SELF_MODEL_IMPACT_PENDING = "pending"
SELF_MODEL_IMPACT_NONE = "none"
ALL_SELF_MODEL_IMPACTS: FrozenSet[str] = frozenset({
    SELF_MODEL_IMPACT_PENDING,
    SELF_MODEL_IMPACT_NONE,
})

# Default actor
ACTOR_RUNTIME = "runtime"
ACTOR_SYSTEM = "system"
ACTOR_DEFAULT = ACTOR_RUNTIME

# Reasons
REASON_INVALID_INPUT = "invalid_input"
REASON_INVALID_STATE = "invalid_evolution_state"
REASON_SNAPSHOT_ERROR = "snapshot_error"
REASON_RECORD_ERROR = "record_error"
REASON_VERSION_ERROR = "version_error"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"
REASON_ROLLBACK_NOT_FOUND = "rollback_not_found"
REASON_ROLLBACK_ERROR = "rollback_error"
REASON_OK = "ok"
REASON_NONE = ""

# Required EvolutionRequest status
REQUIRED_REQUEST_STATUS = "approved"
REQUIRED_PROPOSAL_STATUS = "ready_for_apply"


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


def _new_evolution_id() -> str:
    try:
        return f"ev_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"ev_{int(_now_ts() * 1000):x}"
        except Exception:
            return "ev_0000000000000000"


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
# PersonalityEvolutionRecord
# ============================================================


def build_personality_evolution_record(
    evolution_id: str,
    request_id: str,
    proposal_id: str,
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
    self_model_impact: str = SELF_MODEL_IMPACT_PENDING,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构造一份标准的 PersonalityEvolutionRecord(纯数据,不可变副本)。

    返回的 dict 是可被 deep-copy 安全的演化记录,包含所有关键字段。
    """
    return {
        "schema_version": PERSONALITY_EVOLUTION_RECORD_SCHEMA_VERSION,
        "evolution_id": _safe_str(evolution_id, ""),
        "request_id": _safe_str(request_id, ""),
        "proposal_id": _safe_str(proposal_id, ""),
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
        "self_model_impact": _safe_str(self_model_impact, SELF_MODEL_IMPACT_PENDING),
        "rolled_back_by": "",
        "rollback_of": "",
        "extra": _safe_deepcopy(extra) if isinstance(extra, dict) else {},
    }


# ============================================================
# PersonalityEvolutionStore (in-memory + pluggable)
# ============================================================


class PersonalityEvolutionStore:
    """
    Personality Evolution Store (Phase C.8.6 / v1.0).

    提供:
      - save(record)                  保存一条 evolution 记录
      - get(evolution_id)             读取一条记录
      - list_by_request(request_id)   按 request_id 查找
      - list_by_proposal(proposal_id) 按 proposal_id 查找
      - list_all()                    全部
      - mark_rolled_back(evolution_id, by_evolution_id, audit) 标记回滚
    """

    SCHEMA_VERSION = PERSONALITY_EVOLUTION_RECORD_SCHEMA_VERSION
    NAME = PERSONALITY_EVOLUTION_RECORD_NAME
    VERSION = PERSONALITY_EVOLUTION_RECORD_VERSION

    def __init__(self, external_store: Any = None) -> None:
        self._external = external_store
        self._records: Dict[str, Dict[str, Any]] = {}
        self._request_index: Dict[str, List[str]] = {}
        self._proposal_index: Dict[str, List[str]] = {}

    def save(self, record: Dict[str, Any]) -> bool:
        if not isinstance(record, dict):
            return False
        eid = _safe_str(record.get("evolution_id"), "")
        if not eid:
            return False
        try:
            snapshot = _safe_deepcopy(record)
            self._records[eid] = snapshot
            rid = _safe_str(record.get("request_id"), "")
            if rid:
                self._request_index.setdefault(rid, []).append(eid)
            pid = _safe_str(record.get("proposal_id"), "")
            if pid:
                self._proposal_index.setdefault(pid, []).append(eid)
            # 外部持久化
            self._write_external(eid, snapshot)
            return True
        except Exception as exc:
            logger.warning("[PersonalityEvolutionStore] save failed: %s", exc)
            return False

    def get(self, evolution_id: str) -> Optional[Dict[str, Any]]:
        eid = _safe_str(evolution_id, "")
        if not eid:
            return None
        try:
            r = self._records.get(eid)
            if r is not None:
                return _safe_deepcopy(r)
            return self._read_external(eid)
        except Exception:
            return None

    def list_by_request(self, request_id: str) -> List[Dict[str, Any]]:
        rid = _safe_str(request_id, "")
        if not rid:
            return []
        eids = list(self._request_index.get(rid, []))
        out: List[Dict[str, Any]] = []
        for eid in eids:
            r = self._records.get(eid)
            if r is not None:
                out.append(_safe_deepcopy(r))
        return out

    def list_by_proposal(self, proposal_id: str) -> List[Dict[str, Any]]:
        pid = _safe_str(proposal_id, "")
        if not pid:
            return []
        eids = list(self._proposal_index.get(pid, []))
        out: List[Dict[str, Any]] = []
        for eid in eids:
            r = self._records.get(eid)
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
        evolution_id: str,
        by_evolution_id: str,
        audit_detail: Optional[Dict[str, Any]] = None,
    ) -> bool:
        eid = _safe_str(evolution_id, "")
        by = _safe_str(by_evolution_id, "")
        if not eid:
            return False
        try:
            r = self._records.get(eid)
            if r is None:
                return False
            r["status"] = RECORD_STATUS_ROLLED_BACK
            r["rolled_back_by"] = by
            r["rollback_audit"] = _safe_deepcopy(audit_detail) if isinstance(audit_detail, dict) else {}
            self._records[eid] = r
            self._write_external(eid, _safe_deepcopy(r))
            return True
        except Exception as exc:
            logger.warning("[PersonalityEvolutionStore] mark_rolled_back failed: %s", exc)
            return False

    def get_stats(self) -> Dict[str, Any]:
        try:
            return {
                "total_records": len(self._records),
                "request_index_size": len(self._request_index),
                "proposal_index_size": len(self._proposal_index),
                "rolled_back_count": sum(
                    1 for r in self._records.values()
                    if isinstance(r, dict) and r.get("status") == RECORD_STATUS_ROLLED_BACK
                ),
            }
        except Exception:
            return {
                "total_records": 0,
                "request_index_size": 0,
                "proposal_index_size": 0,
                "rolled_back_count": 0,
            }

    def reset(self) -> None:
        self._records.clear()
        self._request_index.clear()
        self._proposal_index.clear()

    # --------------------------------------------------------
    # External persistence helpers
    # --------------------------------------------------------

    def _write_external(self, evolution_id: str, record: Dict[str, Any]) -> None:
        if self._external is None:
            return
        try:
            fn = getattr(self._external, "save_evolution", None) \
                or getattr(self._external, "save_record", None)
            if callable(fn):
                try:
                    fn(evolution_id, _safe_deepcopy(record))
                except Exception:
                    pass
        except Exception:
            pass

    def _read_external(self, evolution_id: str) -> Optional[Dict[str, Any]]:
        if self._external is None:
            return None
        try:
            fn = getattr(self._external, "load_evolution", None) \
                or getattr(self._external, "get_record", None)
            if callable(fn):
                try:
                    r = fn(evolution_id)
                    if isinstance(r, dict):
                        return dict(r)
                except Exception:
                    return None
        except Exception:
            return None
        return None


# ============================================================
# PersonalityVersionStore (versioning)
# ============================================================


class PersonalityVersionStore:
    """
    Personality Version Store (Phase C.8.6 / v1.0).

    负责:
      - create_version(traits_snapshot, source_evolution_id) -> new_version
      - get_version(version_id) -> snapshot
      - get_current() -> latest version snapshot
      - list_versions() -> all versions
      - rollback_to(version_id) -> current version

    不负责:
      - 直接修改 PersonalityState
      - 调用任何 personality resolver
    """

    SCHEMA_VERSION = PERSONALITY_EVOLUTION_RECORD_SCHEMA_VERSION
    NAME = "personality_version_store"
    VERSION = "1.0.0"

    def __init__(self, initial_snapshot: Optional[Dict[str, Any]] = None) -> None:
        # version_id -> {"snapshot": dict, "source_evolution_id": str, "timestamp": str, "version_index": int}
        self._versions: Dict[str, Dict[str, Any]] = {}
        # 顺序索引
        self._order: List[str] = []
        # 当前版本
        self._current: str = ""
        # 初始化 baseline 版本
        if isinstance(initial_snapshot, dict) and initial_snapshot:
            self._init_baseline(initial_snapshot)

    def _init_baseline(self, snapshot: Dict[str, Any]) -> None:
        baseline_id = "v0"
        entry = {
            "version_id": baseline_id,
            "version_index": 0,
            "snapshot": _safe_deepcopy(snapshot),
            "source_evolution_id": "",
            "timestamp": _now_iso(),
            "is_baseline": True,
        }
        self._versions[baseline_id] = entry
        self._order.append(baseline_id)
        self._current = baseline_id

    def create_version(
        self,
        traits_snapshot: Dict[str, Any],
        source_evolution_id: str = "",
    ) -> str:
        """
        创建新版本。
        返回 new_version_id。
        """
        if not isinstance(traits_snapshot, dict):
            raise ValueError("traits_snapshot must be dict")
        try:
            new_idx = len(self._order)
            new_id = f"v{new_idx}"
            entry = {
                "version_id": new_id,
                "version_index": new_idx,
                "snapshot": _safe_deepcopy(traits_snapshot),
                "source_evolution_id": _safe_str(source_evolution_id, ""),
                "timestamp": _now_iso(),
                "is_baseline": False,
            }
            self._versions[new_id] = entry
            self._order.append(new_id)
            self._current = new_id
            return new_id
        except Exception as exc:
            logger.warning("[PersonalityVersionStore] create_version failed: %s", exc)
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
        """
        回滚 current 指向指定版本(不删除历史)。
        返回该版本的 snapshot。
        """
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


def create_personality_evolution_record_store(
    external_store: Any = None,
) -> PersonalityEvolutionStore:
    return PersonalityEvolutionStore(external_store=external_store)


def create_personality_version_store(
    initial_snapshot: Optional[Dict[str, Any]] = None,
) -> PersonalityVersionStore:
    return PersonalityVersionStore(initial_snapshot=initial_snapshot)
