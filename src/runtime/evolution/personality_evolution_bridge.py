# -*- coding: utf-8 -*-
"""
src/runtime/evolution/personality_evolution_bridge.py

Phase C.8.5 Personality Evolution Bridge.
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# Schema version + constants
# ============================================================

PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION = "1.0"
PERSONALITY_EVOLUTION_BRIDGE_NAME = "personality_evolution_bridge"
PERSONALITY_EVOLUTION_BRIDGE_VERSION = "1.0.0"

TARGET_PERSONALITY = "personality"

STATUS_PENDING = "pending"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected"
STATUS_APPLIED = "applied"
ALL_STATUSES: FrozenSet[str] = frozenset({
    STATUS_PENDING,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_APPLIED,
})

CONFLICT_CORE_IDENTITY = "core_identity_conflict"
CONFLICT_IMMUTABLE_TRAIT = "immutable_trait_conflict"
CONFLICT_NONE = "none"

DEFAULT_CONFIDENCE = 0.5
MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0

REQUIRED_PROPOSAL_STATUS = "ready_for_apply"

REASON_INVALID_INPUT = "invalid_input"
REASON_INVALID_STATUS = "invalid_status"
REASON_PROPOSAL_NOT_FOUND = "proposal_not_found"
REASON_DUPLICATE = "duplicate_request"
REASON_SNAPSHOT_ERROR = "snapshot_error"
REASON_AUDIT_ERROR = "audit_error"
REASON_INTERNAL_ERROR = "internal_error"
REASON_DEGRADED = "degraded"
REASON_NONE = ""

AUDIT_ACTION_REQUESTED = "runtime_personality_evolution_requested"
AUDIT_COMPONENT = "runtime_evolution"
ACTOR_RUNTIME = "runtime"
ACTOR_DEFAULT = ACTOR_RUNTIME


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


def _new_request_id() -> str:
    try:
        return f"evreq_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"evreq_{int(datetime.now(timezone.utc).timestamp() * 1000):x}"
        except Exception:
            return "evreq_0000000000000000"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = DEFAULT_CONFIDENCE) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
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


def _clamp_confidence(v: float) -> float:
    try:
        if v < MIN_CONFIDENCE:
            return MIN_CONFIDENCE
        if v > MAX_CONFIDENCE:
            return MAX_CONFIDENCE
        return float(v)
    except Exception:
        return DEFAULT_CONFIDENCE


# ============================================================
# Audit utility
# ============================================================


def _record_audit_safely(
    audit: Any,
    request_id: str,
    proposal_id: str,
    timestamp: str,
    confidence: float,
    status: str,
) -> bool:
    try:
        if audit is None:
            return False
        detail: Dict[str, Any] = {
            "request_id": str(request_id or ""),
            "proposal_id": str(proposal_id or ""),
            "timestamp": str(timestamp or _now_iso()),
            "confidence": float(confidence) if confidence is not None else DEFAULT_CONFIDENCE,
            "status": str(status or STATUS_PENDING),
            "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
        }
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=AUDIT_ACTION_REQUESTED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_REQUESTED,
                    detail=detail,
                    result="success",
                )
                return True
            except Exception:
                pass
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=AUDIT_ACTION_REQUESTED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_REQUESTED,
                    detail=detail,
                    result="success",
                )
                audit.save_audit_record(rec)
                return True
            except Exception:
                pass
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=AUDIT_ACTION_REQUESTED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_REQUESTED,
                    detail=detail,
                    result="success",
                )
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


# ============================================================
# Personality snapshot reader (read-only)
# ============================================================


def _load_personality_snapshot(snapshot_provider: Any) -> Dict[str, Any]:
    if snapshot_provider is None:
        return {}
    try:
        snap: Any = None
        for attr in ("get_snapshot", "snapshot", "get", "read"):
            fn = getattr(snapshot_provider, attr, None)
            if callable(fn):
                try:
                    snap = fn()
                except Exception:
                    snap = None
                if snap is not None:
                    break
            elif attr == "snapshot":
                try:
                    snap = getattr(snapshot_provider, attr, None)
                except Exception:
                    snap = None
                if snap is not None:
                    break
        if snap is None:
            snap = snapshot_provider
        if not isinstance(snap, dict):
            for attr in ("to_dict", "as_dict", "model_dump"):
                fn = getattr(snap, attr, None)
                if callable(fn):
                    try:
                        d = fn()
                        if isinstance(d, dict):
                            snap = d
                            break
                    except Exception:
                        continue
        if not isinstance(snap, dict):
            return {}
        core_keys: List[str] = []
        immutable: List[str] = []
        ci = snap.get("core_identity")
        if isinstance(ci, dict):
            for k in ci.keys():
                core_keys.append(str(k))
        elif isinstance(ci, list):
            for k in ci:
                core_keys.append(str(k))
        it = snap.get("immutable_traits")
        if isinstance(it, list):
            for t in it:
                immutable.append(str(t))
        elif isinstance(it, dict):
            for k in it.keys():
                immutable.append(str(k))
        core = snap.get("core")
        if isinstance(core, dict):
            t = core.get("traits")
            if isinstance(t, list):
                for x in t:
                    s = _safe_str(x, "")
                    if s and s not in immutable:
                        immutable.append(s)
        return {
            "core_identity_keys": core_keys,
            "immutable_traits": immutable,
        }
    except Exception:
        return {}


# ============================================================
# Conflict check
# ============================================================


def _extract_change_keys(changes: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    keys: List[str] = []
    traits: List[str] = []
    for c in changes:
        if not isinstance(c, dict):
            continue
        k = c.get("key") or c.get("trait") or c.get("target") or c.get("field")
        if k is not None:
            keys.append(_safe_str(k, ""))
        t = c.get("trait") or c.get("name") or c.get("dimension")
        if t is not None:
            traits.append(_safe_str(t, ""))
    return [x for x in keys if x], [x for x in traits if x]


def _detect_conflicts(
    changes: List[Dict[str, Any]],
    snapshot: Dict[str, Any],
) -> List[str]:
    if not snapshot:
        return []
    keys, traits = _extract_change_keys(changes)
    core_keys = set(_safe_list(snapshot.get("core_identity_keys")))
    immutable = set(_safe_list(snapshot.get("immutable_traits")))
    conflicts: List[str] = []
    for k in keys:
        if k in core_keys:
            conflicts.append(CONFLICT_CORE_IDENTITY)
            break
    for t in traits:
        if t in immutable:
            conflicts.append(CONFLICT_IMMUTABLE_TRAIT)
            break
    for t in traits:
        if t in core_keys:
            conflicts.append(CONFLICT_CORE_IDENTITY)
            break
    return conflicts


# ============================================================
# RequestStore utilities
# ============================================================


def _save_request_to_store(
    store: Any,
    request_id: str,
    request_payload: Dict[str, Any],
) -> bool:
    if store is None:
        return True
    try:
        write_fn = getattr(store, "save_request", None) or getattr(store, "set_request", None)
        if callable(write_fn):
            try:
                write_fn(request_id, _safe_deepcopy(request_payload))
                return True
            except Exception:
                return False
    except Exception:
        pass
    return False


def _load_request_from_store(store: Any, request_id: str) -> Optional[Dict[str, Any]]:
    if store is None:
        return None
    try:
        load_fn = getattr(store, "load_request", None) or getattr(store, "get_request", None)
        if callable(load_fn):
            try:
                r = load_fn(request_id)
                if isinstance(r, dict):
                    return dict(r)
            except Exception:
                return None
    except Exception:
        return None
    return None


# ============================================================
# PersonalityEvolutionBridge
# ============================================================


class PersonalityEvolutionBridge:
    """Personality Evolution Bridge (Phase C.8.5 / v1.0)."""

    SCHEMA_VERSION = PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION
    NAME = PERSONALITY_EVOLUTION_BRIDGE_NAME
    VERSION = PERSONALITY_EVOLUTION_BRIDGE_VERSION

    def __init__(
        self,
        snapshot_provider: Any = None,
        request_store: Any = None,
        audit: Any = None,
        default_actor: str = ACTOR_DEFAULT,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._request_store = request_store
        self._audit = audit
        self._default_actor = str(default_actor or ACTOR_DEFAULT)
        self._lock = threading.RLock()

        self._requests: Dict[str, Dict[str, Any]] = {}
        self._proposal_index: Dict[str, str] = {}
        self._snapshot_cache: Dict[str, Any] = {}

        self._create_count: int = 0
        self._duplicate_count: int = 0
        self._conflict_count: int = 0
        self._rejected_count: int = 0
        self._degraded_count: int = 0
        self._last_error: Optional[str] = None

    def create_request(
        self,
        proposal: Any,
        history_ref: Optional[Dict[str, Any]] = None,
        approval_ref: Optional[Dict[str, Any]] = None,
        actor: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            with self._lock:
                self._create_count += 1
                if not isinstance(proposal, dict) and not hasattr(proposal, "__dict__"):
                    return self._build_result(
                        success=False,
                        degraded=True,
                        error="proposal must be dict or object",
                        proposal_id="",
                        status=STATUS_REJECTED,
                        conflicts=[],
                        reason=REASON_INVALID_INPUT,
                        confidence=0.0,
                    )
                pid = _safe_str(self._extract_proposal_id(proposal), "")
                if not pid:
                    self._degraded_count += 1
                    self._last_error = "proposal_id missing"
                    return self._build_result(
                        success=False,
                        degraded=True,
                        error="proposal_id missing",
                        proposal_id="",
                        status=STATUS_REJECTED,
                        conflicts=[],
                        reason=REASON_INVALID_INPUT,
                        confidence=0.0,
                    )
                pstatus = _safe_str(self._extract_proposal_status(proposal), "")
                if pstatus != REQUIRED_PROPOSAL_STATUS:
                    self._rejected_count += 1
                    self._last_error = REASON_INVALID_STATUS
                    return self._build_result(
                        success=False,
                        degraded=True,
                        error=f"proposal status must be '{REQUIRED_PROPOSAL_STATUS}', got '{pstatus}'",
                        proposal_id=pid,
                        status=STATUS_REJECTED,
                        conflicts=[],
                        reason=REASON_INVALID_STATUS,
                        confidence=0.0,
                    )
                if pid in self._proposal_index:
                    existing_id = self._proposal_index[pid]
                    self._duplicate_count += 1
                    self._last_error = REASON_DUPLICATE
                    existing = self._requests.get(existing_id)
                    if existing is None:
                        existing = _load_request_from_store(self._request_store, existing_id)
                    return self._build_result(
                        success=False,
                        degraded=True,
                        error=f"duplicate request for proposal {pid}",
                        proposal_id=pid,
                        status=STATUS_REJECTED,
                        conflicts=[],
                        reason=REASON_DUPLICATE,
                        confidence=_safe_float(existing.get("confidence") if existing else 0.0),
                        request_id=existing_id,
                    )
                changes = self._extract_proposal_changes(proposal)
                confidence = _clamp_confidence(
                    _safe_float(self._extract_proposal_confidence(proposal), DEFAULT_CONFIDENCE)
                )
                act = _safe_str(actor, self._default_actor) or self._default_actor
                ts = _now_iso()
                source_proposal = {
                    "proposal_id": pid,
                    "status": pstatus,
                    "changes": _safe_deepcopy(changes),
                    "confidence": confidence,
                    "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
                }
                history_ref_norm = _safe_deepcopy(history_ref) if isinstance(history_ref, dict) else {}
                approval_ref_norm = _safe_deepcopy(approval_ref) if isinstance(approval_ref, dict) else {}
                evidence = {
                    "source_proposal": source_proposal,
                    "history_reference": history_ref_norm,
                    "approval_reference": approval_ref_norm,
                }
                snapshot = self._get_snapshot_safe()
                conflicts = _detect_conflicts(changes, snapshot)
                if conflicts:
                    self._conflict_count += 1
                    req_status = STATUS_NEEDS_REVIEW
                else:
                    req_status = STATUS_PENDING
                request_id = _new_request_id()
                meta = _safe_deepcopy(metadata) if isinstance(metadata, dict) else {}
                req = {
                    "request_id": request_id,
                    "proposal_id": pid,
                    "target": TARGET_PERSONALITY,
                    "changes": _safe_deepcopy(changes),
                    "evidence": evidence,
                    "confidence": confidence,
                    "status": req_status,
                    "conflicts": list(conflicts),
                    "actor": act,
                    "timestamp": ts,
                    "metadata": meta,
                    "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
                }
                self._requests[request_id] = _safe_deepcopy(req)
                self._proposal_index[pid] = request_id
                if self._request_store is not None:
                    try:
                        _save_request_to_store(self._request_store, request_id, req)
                    except Exception:
                        pass
                audit_ok = _record_audit_safely(
                    self._audit, request_id, pid, ts, confidence, req_status
                )
                return self._build_result(
                    success=True,
                    degraded=False,
                    error="",
                    proposal_id=pid,
                    status=req_status,
                    conflicts=list(conflicts),
                    reason=REASON_NONE,
                    confidence=confidence,
                    request_id=request_id,
                    timestamp=ts,
                    changes=_safe_deepcopy(changes),
                    evidence=_safe_deepcopy(evidence),
                    actor=act,
                    audit_recorded=audit_ok,
                )
        except Exception as exc:
            self._degraded_count += 1
            self._last_error = str(exc)
            logger.warning("[PersonalityEvolutionBridge] create_request failed: %s", exc)
            return self._build_result(
                success=False,
                degraded=True,
                error=str(exc),
                proposal_id="",
                status=STATUS_REJECTED,
                conflicts=[],
                reason=REASON_INTERNAL_ERROR,
                confidence=0.0,
            )

    def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        try:
            rid = _safe_str(request_id, "")
            if not rid:
                return None
            with self._lock:
                r = self._requests.get(rid)
                if r is not None:
                    return _safe_deepcopy(r)
            return _load_request_from_store(self._request_store, rid)
        except Exception:
            return None

    def list_requests(
        self,
        proposal_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            with self._lock:
                items = list(self._requests.values())
            pid_filter = _safe_str(proposal_id, "") or None
            st_filter = _safe_str(status, "") or None
            out: List[Dict[str, Any]] = []
            for r in items:
                if not isinstance(r, dict):
                    continue
                if pid_filter and r.get("proposal_id") != pid_filter:
                    continue
                if st_filter and r.get("status") != st_filter:
                    continue
                out.append(_safe_deepcopy(r))
            if limit is not None:
                try:
                    lim = int(limit)
                    if lim >= 0:
                        out = out[:lim]
                except Exception:
                    pass
            return out
        except Exception:
            return []

    def detect_conflict(self, proposal: Any) -> List[str]:
        try:
            changes = self._extract_proposal_changes(proposal)
            snapshot = self._get_snapshot_safe()
            return _detect_conflicts(changes, snapshot)
        except Exception:
            return []

    def get_snapshot_summary(self) -> Dict[str, Any]:
        try:
            snap = self._get_snapshot_safe()
            if not snap:
                return {
                    "available": False,
                    "core_identity_keys": [],
                    "immutable_traits": [],
                }
            return {
                "available": True,
                "core_identity_keys": _safe_list(snap.get("core_identity_keys")),
                "immutable_traits": _safe_list(snap.get("immutable_traits")),
            }
        except Exception:
            return {
                "available": False,
                "core_identity_keys": [],
                "immutable_traits": [],
            }

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "create_count": int(self._create_count),
                    "duplicate_count": int(self._duplicate_count),
                    "conflict_count": int(self._conflict_count),
                    "rejected_count": int(self._rejected_count),
                    "degraded_count": int(self._degraded_count),
                    "total_requests": len(self._requests),
                    "last_error": self._last_error,
                }
        except Exception:
            return {
                "create_count": 0,
                "duplicate_count": 0,
                "conflict_count": 0,
                "rejected_count": 0,
                "degraded_count": 0,
                "total_requests": 0,
                "last_error": "stats_unavailable",
            }

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._proposal_index.clear()
            self._snapshot_cache.clear()
            self._create_count = 0
            self._duplicate_count = 0
            self._conflict_count = 0
            self._rejected_count = 0
            self._degraded_count = 0
            self._last_error = None

    def _extract_proposal_id(self, proposal: Any) -> str:
        try:
            if isinstance(proposal, dict):
                return _safe_str(proposal.get("proposal_id") or proposal.get("id"), "")
            return _safe_str(
                getattr(proposal, "proposal_id", None) or getattr(proposal, "id", None),
                "",
            )
        except Exception:
            return ""

    def _extract_proposal_status(self, proposal: Any) -> str:
        try:
            if isinstance(proposal, dict):
                return _safe_str(proposal.get("status"), "")
            return _safe_str(getattr(proposal, "status", None), "")
        except Exception:
            return ""

    def _extract_proposal_changes(self, proposal: Any) -> List[Dict[str, Any]]:
        try:
            if isinstance(proposal, dict):
                c = proposal.get("changes")
            else:
                c = getattr(proposal, "changes", None)
            if not isinstance(c, list):
                return []
            out: List[Dict[str, Any]] = []
            for item in c:
                if isinstance(item, dict):
                    out.append(dict(item))
                else:
                    out.append({"value": item})
            return out
        except Exception:
            return []

    def _extract_proposal_confidence(self, proposal: Any) -> float:
        try:
            if isinstance(proposal, dict):
                return _safe_float(proposal.get("confidence"), DEFAULT_CONFIDENCE)
            return _safe_float(getattr(proposal, "confidence", None), DEFAULT_CONFIDENCE)
        except Exception:
            return DEFAULT_CONFIDENCE

    def _get_snapshot_safe(self) -> Dict[str, Any]:
        try:
            if self._snapshot_provider is None:
                return {}
            cached = self._snapshot_cache.get("snap")
            if cached is not None:
                return cached
            snap = _load_personality_snapshot(self._snapshot_provider)
            self._snapshot_cache["snap"] = snap
            return snap
        except Exception:
            return {}

    def _build_result(
        self,
        success: bool,
        degraded: bool,
        error: str,
        proposal_id: str,
        status: str,
        conflicts: List[str],
        reason: str,
        confidence: float,
        request_id: str = "",
        timestamp: str = "",
        changes: Optional[List[Dict[str, Any]]] = None,
        evidence: Optional[Dict[str, Any]] = None,
        actor: str = "",
        audit_recorded: bool = False,
    ) -> Dict[str, Any]:
        return {
            "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "request_id": _safe_str(request_id, ""),
            "proposal_id": _safe_str(proposal_id, ""),
            "target": TARGET_PERSONALITY,
            "changes": _safe_list(changes) if changes is not None else [],
            "evidence": _safe_dict(evidence),
            "confidence": float(confidence) if confidence is not None else 0.0,
            "status": _safe_str(status, STATUS_REJECTED),
            "conflicts": _safe_list(conflicts),
            "reason": _safe_str(reason, REASON_NONE),
            "actor": _safe_str(actor, "") or self._default_actor,
            "timestamp": _safe_str(timestamp, "") or _now_iso(),
            "audit_recorded": bool(audit_recorded),
            "error": _safe_str(error, ""),
            "bridge": {
                "name": PERSONALITY_EVOLUTION_BRIDGE_NAME,
                "version": PERSONALITY_EVOLUTION_BRIDGE_VERSION,
                "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
            },
        }


# ============================================================
# Factory and safe wrapper
# ============================================================


def create_personality_evolution_bridge(
    snapshot_provider: Any = None,
    request_store: Any = None,
    audit: Any = None,
    default_actor: str = ACTOR_DEFAULT,
) -> PersonalityEvolutionBridge:
    return PersonalityEvolutionBridge(
        snapshot_provider=snapshot_provider,
        request_store=request_store,
        audit=audit,
        default_actor=default_actor,
    )


def safe_create_request(
    bridge: Any,
    proposal: Any,
    history_ref: Optional[Dict[str, Any]] = None,
    approval_ref: Optional[Dict[str, Any]] = None,
    actor: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        if bridge is None:
            return {
                "success": False,
                "degraded": True,
                "error": "bridge is None",
                "request_id": "",
                "proposal_id": "",
                "status": STATUS_REJECTED,
                "conflicts": [],
                "reason": REASON_DEGRADED,
                "confidence": 0.0,
                "timestamp": _now_iso(),
                "bridge": {
                    "name": PERSONALITY_EVOLUTION_BRIDGE_NAME,
                    "version": PERSONALITY_EVOLUTION_BRIDGE_VERSION,
                    "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
                },
            }
        if not hasattr(bridge, "create_request"):
            return {
                "success": False,
                "degraded": True,
                "error": "bridge missing create_request",
                "request_id": "",
                "proposal_id": "",
                "status": STATUS_REJECTED,
                "conflicts": [],
                "reason": REASON_INVALID_INPUT,
                "confidence": 0.0,
                "timestamp": _now_iso(),
                "bridge": {
                    "name": PERSONALITY_EVOLUTION_BRIDGE_NAME,
                    "version": PERSONALITY_EVOLUTION_BRIDGE_VERSION,
                    "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
                },
            }
        return bridge.create_request(
            proposal=proposal,
            history_ref=history_ref,
            approval_ref=approval_ref,
            actor=actor,
            metadata=metadata,
        )
    except Exception as exc:
        return {
            "success": False,
            "degraded": True,
            "error": str(exc),
            "request_id": "",
            "proposal_id": "",
            "status": STATUS_REJECTED,
            "conflicts": [],
            "reason": REASON_INTERNAL_ERROR,
            "confidence": 0.0,
            "timestamp": _now_iso(),
            "bridge": {
                "name": PERSONALITY_EVOLUTION_BRIDGE_NAME,
                "version": PERSONALITY_EVOLUTION_BRIDGE_VERSION,
                "schema_version": PERSONALITY_EVOLUTION_BRIDGE_SCHEMA_VERSION,
            },
        }
