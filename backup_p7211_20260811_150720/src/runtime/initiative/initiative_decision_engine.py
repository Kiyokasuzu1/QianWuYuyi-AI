# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_decision_engine.py

Phase C.9.0 Initiative Decision Engine.

只负责:
  - 接收 Runtime context(各类 snapshot,只读)
  - 调用 InitiativeTriggerScanner 扫描 trigger
  - 调用 InitiativePolicy 评估
  - 调用 InitiativeMessagePlanner 生成消息请求
  - 写入 InitiativeMemoryStore
  - 记录 audit

绝不负责:
  - 直接发送消息
  - 修改 Personality / SelfModel / Relationship / Memory
  - 绕过 Audit
"""
from __future__ import annotations

import copy
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .initiative_trigger import (
    InitiativeTriggerScanner,
    create_initiative_trigger_scanner,
    TRIGGER_NONE,
    ALL_TRIGGERS,
)
from .initiative_policy import (
    InitiativePolicy,
    create_initiative_policy,
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_DEFER,
)
from .initiative_memory import (
    InitiativeMemoryStore,
    create_initiative_memory_store,
    RESULT_INITIATED,
    RESULT_SUPPRESSED,
    RESULT_DEFERRED,
    RESULT_FAILED,
    RESULT_DEGRADED,
)
from .initiative_message_planner import (
    InitiativeMessagePlanner,
    create_initiative_message_planner,
    safe_plan_message,
    MSG_TYPE_NONE,
    ALL_MESSAGE_TYPES,
)

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION = "1.0"
INITIATIVE_DECISION_ENGINE_NAME = "initiative_decision_engine"
INITIATIVE_DECISION_ENGINE_VERSION = "1.0.0"

# Audit actions
AUDIT_ACTION_EVALUATED = "runtime_initiative_evaluated"
AUDIT_ACTION_SUPPRESSED = "runtime_initiative_suppressed"
AUDIT_ACTION_INITIATED = "runtime_initiative_initiated"
AUDIT_ACTION_FAILED = "runtime_initiative_failed"
AUDIT_COMPONENT = "runtime_initiative"

# Reason codes
REASON_OK = "ok"
REASON_NO_TRIGGER = "no_trigger"
REASON_DEGRADED = "degraded"
REASON_ERROR = "error"
REASON_DISABLED = "policy_disabled"

ALL_AUDIT_ACTIONS: FrozenSet[str] = frozenset({
    AUDIT_ACTION_EVALUATED,
    AUDIT_ACTION_SUPPRESSED,
    AUDIT_ACTION_INITIATED,
    AUDIT_ACTION_FAILED,
})

DEFAULT_ACTOR = "runtime"


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
        return f"ide_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"ide_{int(_now_ts() * 1000):x}"
        except Exception:
            return "ide_0000000000000000"


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


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:
        return []


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


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
# Audit utility
# ============================================================


def _record_audit_safely(
    audit: Any,
    action: str,
    detail: Dict[str, Any],
    result: str = "success",
) -> bool:
    try:
        if audit is None:
            return False
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=dict(detail) if isinstance(detail, dict) else {},
                    result=result,
                )
                return True
            except Exception:
                pass
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=dict(detail) if isinstance(detail, dict) else {},
                    result=result,
                )
                audit.save_audit_record(rec)
                return True
            except Exception:
                pass
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=dict(detail) if isinstance(detail, dict) else {},
                    result=result,
                )
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


# ============================================================
# InitiativeDecisionEngine
# ============================================================


class InitiativeDecisionEngine:
    """
    Initiative Decision Engine (Phase C.9.0 / v1.0).

    evaluate(context) -> {
      should_initiate: bool,
      priority: float,
      reason: [str, ...],
      trigger: str,
      confidence: float,
      timestamp: str,
      message_request: {...},   # 仅在 should_initiate=True 时
      decision: "allow" | "deny" | "defer",
      policy_result: {...},
      trigger_results: [...],
      degraded: bool,
      error: str,
      initiative_id: str,
    }
    """

    SCHEMA_VERSION = INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION
    NAME = INITIATIVE_DECISION_ENGINE_NAME
    VERSION = INITIATIVE_DECISION_ENGINE_VERSION

    def __init__(
        self,
        trigger_scanner: Any = None,
        policy: Any = None,
        memory_store: Any = None,
        message_planner: Any = None,
        audit: Any = None,
        default_actor: str = DEFAULT_ACTOR,
    ) -> None:
        self._audit = audit
        self._default_actor = _safe_str(default_actor, DEFAULT_ACTOR) or DEFAULT_ACTOR

        # trigger scanner
        if isinstance(trigger_scanner, InitiativeTriggerScanner):
            self._scanner = trigger_scanner
        elif trigger_scanner is None:
            self._scanner = create_initiative_trigger_scanner()
        else:
            if hasattr(trigger_scanner, "scan") and callable(getattr(trigger_scanner, "scan")):
                self._scanner = trigger_scanner  # type: ignore[assignment]
            else:
                self._scanner = create_initiative_trigger_scanner()

        # policy
        if isinstance(policy, InitiativePolicy):
            self._policy = policy
        elif policy is None:
            self._policy = create_initiative_policy()
        else:
            if hasattr(policy, "evaluate") and callable(getattr(policy, "evaluate")):
                self._policy = policy  # type: ignore[assignment]
            else:
                self._policy = create_initiative_policy()

        # memory
        if isinstance(memory_store, InitiativeMemoryStore):
            self._memory = memory_store
        elif memory_store is None:
            self._memory = create_initiative_memory_store()
        else:
            if hasattr(memory_store, "record") and hasattr(memory_store, "get_history"):
                self._memory = memory_store  # type: ignore[assignment]
            else:
                self._memory = create_initiative_memory_store()

        # message planner
        if isinstance(message_planner, InitiativeMessagePlanner):
            self._planner = message_planner
        elif message_planner is None:
            self._planner = create_initiative_message_planner()
        else:
            if hasattr(message_planner, "plan") and callable(getattr(message_planner, "plan")):
                self._planner = message_planner  # type: ignore[assignment]
            else:
                self._planner = create_initiative_message_planner()

        self._lock = threading.RLock()
        # statistics
        self._evaluate_count: int = 0
        self._initiated_count: int = 0
        self._suppressed_count: int = 0
        self._deferred_count: int = 0
        self._failed_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def evaluate(
        self,
        context: Any = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """评估是否应该主动发起一次交流。"""
        try:
            with self._lock:
                self._evaluate_count += 1
                act = _safe_str(actor, self._default_actor) or self._default_actor
                initiative_id = _new_id()
                ctx = self._normalize_context(context)
                user_id = _safe_str(ctx.get("user_id"), "")
                personality_snap = _safe_dict(ctx.get("personality"))
                self_model_snap = _safe_dict(ctx.get("self_model"))
                # 1. scan triggers
                try:
                    trigger_results = self._safe_scan(ctx)
                except Exception as exc:
                    self._failed_count += 1
                    self._last_error = f"scan failed: {exc}"
                    return self._degraded_result(
                        initiative_id=initiative_id,
                        reason=REASON_ERROR,
                        error=str(exc),
                        user_id=user_id,
                        actor=act,
                    )
                # 2. pick top trigger
                top_trigger, top_priority, top_confidence = self._scanner.pick_top_trigger(
                    trigger_results
                )
                if top_trigger == TRIGGER_NONE or top_priority <= 0.0:
                    self._suppressed_count += 1
                    self._memory.record(
                        initiative_id=initiative_id,
                        trigger=TRIGGER_NONE,
                        reason=[REASON_NO_TRIGGER],
                        priority=0.0,
                        result=RESULT_SUPPRESSED,
                        user_id=user_id,
                        actor=act,
                    )
                    self._record_audit(
                        action=AUDIT_ACTION_SUPPRESSED,
                        detail={
                            "initiative_id": initiative_id,
                            "trigger": TRIGGER_NONE,
                            "priority": 0.0,
                            "decision": "suppressed",
                            "timestamp": _now_iso(),
                            "user_id": user_id,
                            "actor": act,
                        },
                        result="success",
                    )
                    return self._build_result(
                        success=True,
                        should_initiate=False,
                        degraded=False,
                        error="",
                        initiative_id=initiative_id,
                        priority=0.0,
                        reason=[REASON_NO_TRIGGER],
                        trigger=TRIGGER_NONE,
                        confidence=0.0,
                        message_request=None,
                        decision=DECISION_DENY,
                        policy_result={},
                        trigger_results=trigger_results,
                        timestamp=_now_iso(),
                        actor=act,
                    )
                # 3. policy evaluate
                history_stats = self._safe_history_stats(ctx)
                try:
                    rel_level = _safe_float(
                        _safe_dict(ctx.get("relationship")).get("level"),
                        0.5,
                    )
                except Exception:
                    rel_level = 0.5
                user_pref = _safe_str(
                    _safe_dict(ctx.get("user_preference"), {}).get("preference")
                    if isinstance(ctx.get("user_preference"), dict)
                    else ctx.get("user_preference"),
                    "neutral",
                )
                try:
                    policy_result = self._policy.evaluate(
                        proposed={
                            "trigger": top_trigger,
                            "priority": top_priority,
                            "confidence": top_confidence,
                        },
                        history_stats=history_stats,
                        relationship_level=rel_level,
                        user_preference=user_pref,
                    )
                except Exception as exc:
                    self._failed_count += 1
                    self._last_error = f"policy failed: {exc}"
                    return self._degraded_result(
                        initiative_id=initiative_id,
                        reason=REASON_ERROR,
                        error=str(exc),
                        user_id=user_id,
                        actor=act,
                    )
                decision = _safe_str(policy_result.get("decision"), DECISION_DEFER)
                reasons = _safe_list(policy_result.get("reasons"))
                adjusted_priority = _safe_float(
                    policy_result.get("adjusted_priority"), top_priority,
                )
                # 4. decision handling
                if decision == DECISION_DENY:
                    self._suppressed_count += 1
                    self._memory.record(
                        initiative_id=initiative_id,
                        trigger=top_trigger,
                        reason=reasons,
                        priority=adjusted_priority,
                        result=RESULT_SUPPRESSED,
                        confidence=top_confidence,
                        user_id=user_id,
                        actor=act,
                    )
                    self._record_audit(
                        action=AUDIT_ACTION_SUPPRESSED,
                        detail={
                            "initiative_id": initiative_id,
                            "trigger": top_trigger,
                            "priority": adjusted_priority,
                            "decision": "deny",
                            "reasons": list(reasons),
                            "timestamp": _now_iso(),
                            "user_id": user_id,
                            "actor": act,
                        },
                        result="success",
                    )
                    return self._build_result(
                        success=True,
                        should_initiate=False,
                        degraded=False,
                        error="",
                        initiative_id=initiative_id,
                        priority=adjusted_priority,
                        reason=list(reasons),
                        trigger=top_trigger,
                        confidence=top_confidence,
                        message_request=None,
                        decision=decision,
                        policy_result=policy_result,
                        trigger_results=trigger_results,
                        timestamp=_now_iso(),
                        actor=act,
                    )
                if decision == DECISION_DEFER:
                    self._deferred_count += 1
                    self._memory.record(
                        initiative_id=initiative_id,
                        trigger=top_trigger,
                        reason=reasons,
                        priority=adjusted_priority,
                        result=RESULT_DEFERRED,
                        confidence=top_confidence,
                        user_id=user_id,
                        actor=act,
                    )
                    self._record_audit(
                        action=AUDIT_ACTION_SUPPRESSED,
                        detail={
                            "initiative_id": initiative_id,
                            "trigger": top_trigger,
                            "priority": adjusted_priority,
                            "decision": "defer",
                            "reasons": list(reasons),
                            "timestamp": _now_iso(),
                            "user_id": user_id,
                            "actor": act,
                        },
                        result="success",
                    )
                    return self._build_result(
                        success=True,
                        should_initiate=False,
                        degraded=False,
                        error="",
                        initiative_id=initiative_id,
                        priority=adjusted_priority,
                        reason=list(reasons),
                        trigger=top_trigger,
                        confidence=top_confidence,
                        message_request=None,
                        decision=decision,
                        policy_result=policy_result,
                        trigger_results=trigger_results,
                        timestamp=_now_iso(),
                        actor=act,
                    )
                # decision == allow
                # 5. build message request
                evidence = self._pick_evidence(trigger_results, top_trigger)
                try:
                    msg = self._planner.plan(
                        trigger=top_trigger,
                        trigger_evidence=evidence,
                        personality_snapshot=personality_snap,
                        user_id=user_id,
                    )
                except Exception as exc:
                    self._failed_count += 1
                    self._last_error = f"planner failed: {exc}"
                    msg = safe_plan_message(
                        None,
                        trigger=top_trigger,
                        trigger_evidence=evidence,
                        personality_snapshot=personality_snap,
                        user_id=user_id,
                    )
                self._initiated_count += 1
                self._memory.record(
                    initiative_id=initiative_id,
                    trigger=top_trigger,
                    reason=list(reasons) + ["decision_allow"],
                    priority=adjusted_priority,
                    result=RESULT_INITIATED,
                    confidence=top_confidence,
                    user_id=user_id,
                    actor=act,
                    extra={"message_type": _safe_str(msg.get("type"), "")},
                )
                self._record_audit(
                    action=AUDIT_ACTION_INITIATED,
                    detail={
                        "initiative_id": initiative_id,
                        "trigger": top_trigger,
                        "priority": adjusted_priority,
                        "decision": "allow",
                        "reasons": list(reasons),
                        "timestamp": _now_iso(),
                        "user_id": user_id,
                        "actor": act,
                        "message_type": _safe_str(msg.get("type"), ""),
                    },
                    result="success",
                )
                # always record an evaluated audit for traceability
                self._record_audit(
                    action=AUDIT_ACTION_EVALUATED,
                    detail={
                        "initiative_id": initiative_id,
                        "trigger": top_trigger,
                        "priority": adjusted_priority,
                        "decision": decision,
                        "timestamp": _now_iso(),
                        "user_id": user_id,
                        "actor": act,
                    },
                    result="success",
                )
                return self._build_result(
                    success=True,
                    should_initiate=True,
                    degraded=False,
                    error="",
                    initiative_id=initiative_id,
                    priority=adjusted_priority,
                    reason=list(reasons),
                    trigger=top_trigger,
                    confidence=top_confidence,
                    message_request=msg,
                    decision=decision,
                    policy_result=policy_result,
                    trigger_results=trigger_results,
                    timestamp=_now_iso(),
                    actor=act,
                )
        except Exception as exc:
            self._failed_count += 1
            self._last_error = str(exc)
            logger.warning("[InitiativeDecisionEngine] evaluate failed: %s", exc)
            return self._degraded_result(
                initiative_id="",
                reason=REASON_ERROR,
                error=str(exc),
                user_id="",
                actor=self._default_actor,
            )

    def get_history(
        self,
        limit: Optional[int] = None,
        trigger: Optional[str] = None,
        result: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            return self._memory.get_history(limit=limit, trigger=trigger, result=result)
        except Exception:
            return []

    def get_latest(self) -> Optional[Dict[str, Any]]:
        try:
            return self._memory.get_latest()
        except Exception:
            return None

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "evaluate_count": int(self._evaluate_count),
                    "initiated_count": int(self._initiated_count),
                    "suppressed_count": int(self._suppressed_count),
                    "deferred_count": int(self._deferred_count),
                    "failed_count": int(self._failed_count),
                    "memory": self._memory.get_stats(),
                    "last_error": self._last_error,
                }
        except Exception:
            return {
                "evaluate_count": 0,
                "initiated_count": 0,
                "suppressed_count": 0,
                "deferred_count": 0,
                "failed_count": 0,
                "memory": {},
                "last_error": "stats_unavailable",
            }

    def reset(self) -> None:
        with self._lock:
            self._evaluate_count = 0
            self._initiated_count = 0
            self._suppressed_count = 0
            self._deferred_count = 0
            self._failed_count = 0
            self._last_error = None
            try:
                self._memory.reset()
            except Exception:
                pass

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    def _normalize_context(self, context: Any) -> Dict[str, Any]:
        try:
            if context is None:
                return {}
            if isinstance(context, dict):
                return dict(context)
            if hasattr(context, "to_dict") and callable(getattr(context, "to_dict")):
                d = context.to_dict()
                if isinstance(d, dict):
                    return d
            if hasattr(context, "as_dict") and callable(getattr(context, "as_dict")):
                d = context.as_dict()
                if isinstance(d, dict):
                    return d
            return _safe_dict(context)
        except Exception:
            return {}

    def _safe_scan(self, ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
        try:
            return self._scanner.scan(ctx)
        except Exception as exc:
            logger.warning("[InitiativeDecisionEngine] scan exception: %s", exc)
            return []

    def _safe_history_stats(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            cooldown = _safe_float(
                self._policy.get_stats().get("cooldown_seconds"), 0.0,
            )
            win = _safe_float(
                self._policy.get_stats().get("frequency_window_seconds"), 86400.0,
            )
            latest_init = self._memory.get_latest_initiated()
            last_ts = 0.0
            if isinstance(latest_init, dict):
                ts = _safe_str(latest_init.get("timestamp"), "")
                if ts:
                    try:
                        text = ts
                        if text.endswith("Z"):
                            text = text[:-1] + "+00:00"
                        dt = datetime.fromisoformat(text)
                        if dt.tzinfo is not None:
                            last_ts = dt.timestamp()
                        else:
                            last_ts = dt.timestamp()
                    except Exception:
                        last_ts = 0.0
            init_count = self._memory.get_initiated_count_in_window(win)
            return {
                "last_initiated_ts": last_ts,
                "initiated_count_in_window": init_count,
                "decision_count_in_window": self._memory.get_decision_count_in_window(win),
                "cooldown_seconds": cooldown,
            }
        except Exception:
            return {
                "last_initiated_ts": 0.0,
                "initiated_count_in_window": 0,
                "decision_count_in_window": 0,
                "cooldown_seconds": 0.0,
            }

    def _pick_evidence(
        self,
        results: List[Dict[str, Any]],
        trigger: str,
    ) -> Dict[str, Any]:
        try:
            for r in results or []:
                if not isinstance(r, dict):
                    continue
                if _safe_str(r.get("trigger")) == trigger and r.get("active"):
                    return _safe_dict(r.get("evidence"))
            return {}
        except Exception:
            return {}

    def _record_audit(
        self,
        action: str,
        detail: Dict[str, Any],
        result: str = "success",
    ) -> None:
        try:
            _record_audit_safely(
                self._audit,
                action=action,
                detail=dict(detail) if isinstance(detail, dict) else {},
                result=result,
            )
        except Exception:
            pass

    def _degraded_result(
        self,
        initiative_id: str,
        reason: str,
        error: str,
        user_id: str = "",
        actor: str = DEFAULT_ACTOR,
    ) -> Dict[str, Any]:
        # 记录失败 audit
        self._record_audit(
            action=AUDIT_ACTION_FAILED,
            detail={
                "initiative_id": initiative_id,
                "reason": reason,
                "error": error,
                "user_id": user_id,
                "actor": actor,
                "timestamp": _now_iso(),
            },
            result="failed",
        )
        # 记录 memory
        try:
            self._memory.record(
                initiative_id=initiative_id or _new_id(),
                trigger=TRIGGER_NONE,
                reason=[reason, "degraded"],
                priority=0.0,
                result=RESULT_DEGRADED,
                user_id=user_id,
                actor=actor,
                extra={"error": error},
            )
        except Exception:
            pass
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": False,
            "should_initiate": False,
            "degraded": True,
            "error": _safe_str(error, ""),
            "initiative_id": _safe_str(initiative_id, ""),
            "priority": 0.0,
            "reason": [reason],
            "trigger": TRIGGER_NONE,
            "confidence": 0.0,
            "message_request": None,
            "decision": DECISION_DENY,
            "policy_result": {},
            "trigger_results": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, DEFAULT_ACTOR) or DEFAULT_ACTOR,
            "engine": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    def _build_result(
        self,
        success: bool,
        should_initiate: bool,
        degraded: bool,
        error: str,
        initiative_id: str,
        priority: float,
        reason: List[str],
        trigger: str,
        confidence: float,
        message_request: Optional[Dict[str, Any]],
        decision: str,
        policy_result: Dict[str, Any],
        trigger_results: List[Dict[str, Any]],
        timestamp: str,
        actor: str,
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "should_initiate": bool(should_initiate),
            "degraded": bool(degraded),
            "error": _safe_str(error, ""),
            "initiative_id": _safe_str(initiative_id, ""),
            "priority": _safe_float(priority, 0.0),
            "reason": _safe_list(reason),
            "trigger": _safe_str(trigger, TRIGGER_NONE),
            "confidence": _safe_float(confidence, 0.0),
            "message_request": _safe_deepcopy(message_request) if isinstance(message_request, dict) else None,
            "decision": _safe_str(decision, DECISION_DENY),
            "policy_result": _safe_dict(policy_result),
            "trigger_results": _safe_list(trigger_results),
            "timestamp": _safe_str(timestamp, "") or _now_iso(),
            "actor": _safe_str(actor, DEFAULT_ACTOR) or DEFAULT_ACTOR,
            "engine": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }


# ============================================================
# Factory + safe wrapper
# ============================================================


def create_initiative_decision_engine(
    trigger_scanner: Any = None,
    policy: Any = None,
    memory_store: Any = None,
    message_planner: Any = None,
    audit: Any = None,
    default_actor: str = DEFAULT_ACTOR,
) -> InitiativeDecisionEngine:
    return InitiativeDecisionEngine(
        trigger_scanner=trigger_scanner,
        policy=policy,
        memory_store=memory_store,
        message_planner=message_planner,
        audit=audit,
        default_actor=default_actor,
    )


def safe_initiative_evaluate(
    engine: Any,
    context: Any = None,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    if engine is None or not hasattr(engine, "evaluate"):
        return {
            "schema_version": INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION,
            "success": False,
            "should_initiate": False,
            "degraded": True,
            "error": "engine unavailable",
            "initiative_id": "",
            "priority": 0.0,
            "reason": [REASON_DEGRADED],
            "trigger": TRIGGER_NONE,
            "confidence": 0.0,
            "message_request": None,
            "decision": DECISION_DENY,
            "policy_result": {},
            "trigger_results": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, DEFAULT_ACTOR) or DEFAULT_ACTOR,
            "engine": {
                "name": INITIATIVE_DECISION_ENGINE_NAME,
                "version": INITIATIVE_DECISION_ENGINE_VERSION,
                "schema_version": INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION,
            },
        }
    try:
        return engine.evaluate(context, actor=actor)
    except Exception as exc:
        return {
            "schema_version": INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION,
            "success": False,
            "should_initiate": False,
            "degraded": True,
            "error": str(exc),
            "initiative_id": "",
            "priority": 0.0,
            "reason": [REASON_ERROR],
            "trigger": TRIGGER_NONE,
            "confidence": 0.0,
            "message_request": None,
            "decision": DECISION_DENY,
            "policy_result": {},
            "trigger_results": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, DEFAULT_ACTOR) or DEFAULT_ACTOR,
            "engine": {
                "name": INITIATIVE_DECISION_ENGINE_NAME,
                "version": INITIATIVE_DECISION_ENGINE_VERSION,
                "schema_version": INITIATIVE_DECISION_ENGINE_SCHEMA_VERSION,
            },
        }
