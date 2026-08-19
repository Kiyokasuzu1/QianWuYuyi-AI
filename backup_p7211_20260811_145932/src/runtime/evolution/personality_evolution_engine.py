# -*- coding: utf-8 -*-
"""
src/runtime/evolution/personality_evolution_engine.py

Phase C.8.6 Personality Evolution Engine.

只负责:
  - 接收 EvolutionRequest(已由 Bridge 创建,status == approved)
  - 验证 request/proposal 状态合法性
  - 读取当前 personality snapshot
  - 通过 PersonalityEvolutionPolicy 评估 changes
  - 计算 after_snapshot (new traits)
  - 通过 PersonalityVersionStore.create_version() 创建新版本
  - 通过 PersonalityEvolutionStore.save() 写入 EvolutionRecord
  - 记录 audit (runtime_personality_evolution_applied)
  - 提供 rollback(evolution_id) 恢复 before_snapshot

绝不负责:
  - 直接修改 PersonalityState
  - 修改 SelfModel
  - 修改 core_identity / immutable_traits
  - 删除历史 evolution 记录
  - 在没有 EvolutionRequest 的情况下写入人格
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .personality_evolution_policy import (
    PersonalityEvolutionPolicy,
    DECISION_ALLOW,
    DECISION_NEEDS_REVIEW,
    DECISION_DENY,
    REASON_CONFIDENCE_TOO_LOW,
    REASON_DELTA_TOO_LARGE,
    REASON_CORE_IDENTITY_CONFLICT,
    REASON_IMMUTABLE_TRAIT_CONFLICT,
    REASON_TRAIT_CONFLICT,
    REASON_REPEATED_CHANGE,
    REASON_OK,
    DEFAULT_CORE_IDENTITY_FIELDS,
    DEFAULT_IMMUTABLE_TRAITS,
    MAX_SINGLE_TRAIT_DELTA,
    MIN_CONFIDENCE_FOR_APPLY,
)
from .personality_evolution_record import (
    PERSONALITY_EVOLUTION_RECORD_SCHEMA_VERSION,
    PERSONALITY_EVOLUTION_RECORD_NAME,
    PERSONALITY_EVOLUTION_RECORD_VERSION,
    RECORD_STATUS_PENDING,
    RECORD_STATUS_APPLIED,
    RECORD_STATUS_REJECTED,
    RECORD_STATUS_ROLLED_BACK,
    SELF_MODEL_IMPACT_PENDING,
    SELF_MODEL_IMPACT_NONE,
    ACTOR_RUNTIME,
    ACTOR_DEFAULT,
    REASON_INVALID_INPUT,
    REASON_INVALID_STATE,
    REASON_SNAPSHOT_ERROR,
    REASON_RECORD_ERROR,
    REASON_VERSION_ERROR,
    REASON_INTERNAL_ERROR,
    REASON_DEGRADED,
    REASON_ROLLBACK_NOT_FOUND,
    REASON_ROLLBACK_ERROR,
    REASON_OK as RECORD_REASON_OK,
    REASON_NONE,
    REQUIRED_REQUEST_STATUS,
    REQUIRED_PROPOSAL_STATUS,
    build_personality_evolution_record,
    PersonalityEvolutionStore,
    PersonalityVersionStore,
    create_personality_evolution_record_store,
    create_personality_version_store,
    _now_iso,
    _now_ts,
    _safe_str,
    _safe_float,
    _safe_bool,
    _safe_dict,
    _safe_list,
    _safe_deepcopy,
)

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION = "1.0"
PERSONALITY_EVOLUTION_ENGINE_NAME = "personality_evolution_engine"
PERSONALITY_EVOLUTION_ENGINE_VERSION = "1.0.0"

# Audit actions
AUDIT_ACTION_APPLIED = "runtime_personality_evolution_applied"
AUDIT_ACTION_FAILED = "runtime_personality_evolution_failed"
AUDIT_ACTION_ROLLED_BACK = "runtime_personality_evolution_rolled_back"
AUDIT_COMPONENT = "runtime_evolution"

# Apply reasons
APPLY_REASON_OK = "applied"
APPLY_REASON_INVALID_STATE = "invalid_evolution_state"
APPLY_REASON_POLICY_DENIED = "policy_denied"
APPLY_REASON_POLICY_REVIEW = "policy_needs_review"
APPLY_REASON_DUPLICATE = "already_applied"
APPLY_REASON_INTEGRITY = "snapshot_integrity_violation"
APPLY_REASON_NONE = ""

ALL_AUDIT_ACTIONS: FrozenSet[str] = frozenset({
    AUDIT_ACTION_APPLIED,
    AUDIT_ACTION_FAILED,
    AUDIT_ACTION_ROLLED_BACK,
})


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
# Personality snapshot reader (read-only)
# ============================================================


def _load_personality_snapshot(snapshot_provider: Any) -> Dict[str, Any]:
    """
    从 snapshot_provider 读取当前 personality trait snapshot。
    返回 {"traits": {trait_name: float, ...}, "raw": 原 dict}
    """
    if snapshot_provider is None:
        return {"traits": {}, "raw": {}}
    try:
        snap: Any = None
        for attr in ("get_snapshot", "snapshot", "get_traits", "read"):
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
        # 试图 to_dict
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
            return {"traits": {}, "raw": {}}
        traits: Dict[str, float] = {}
        # 提取 traits
        # 1. 顶层 traits: dict
        t = snap.get("traits")
        if isinstance(t, dict):
            for k, v in t.items():
                try:
                    traits[str(k)] = float(v)
                except Exception:
                    continue
        # 2. 顶层 trait_states: dict
        ts = snap.get("trait_states")
        if isinstance(ts, dict) and not traits:
            for k, v in ts.items():
                try:
                    traits[str(k)] = float(v)
                except Exception:
                    continue
        # 3. 顶层直接 trait -> value
        if not traits:
            for k, v in snap.items():
                if k in ("core_identity", "immutable_traits", "name", "id"):
                    continue
                if isinstance(v, (int, float)):
                    traits[str(k)] = float(v)
        return {
            "traits": traits,
            "raw": _safe_deepcopy(snap),
        }
    except Exception:
        return {"traits": {}, "raw": {}}


# ============================================================
# Engine
# ============================================================


class PersonalityEvolutionEngine:
    """
    Personality Evolution Engine (Phase C.8.6 / v1.0).

    安全演化人格:
      - apply_request(request)         执行一次演化
      - rollback(evolution_id)         回滚指定演化
      - get_evolution(evolution_id)    查询
      - list_evolutions(...)          列表
      - get_stats()                    统计
    """

    SCHEMA_VERSION = PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION
    NAME = PERSONALITY_EVOLUTION_ENGINE_NAME
    VERSION = PERSONALITY_EVOLUTION_ENGINE_VERSION

    def __init__(
        self,
        snapshot_provider: Any = None,
        evolution_store: Any = None,
        version_store: Any = None,
        policy: Any = None,
        audit: Any = None,
        bridge: Any = None,
        initial_snapshot: Optional[Dict[str, Any]] = None,
        default_actor: str = ACTOR_DEFAULT,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._audit = audit
        self._bridge = bridge
        self._default_actor = str(default_actor or ACTOR_DEFAULT)

        # policy
        if isinstance(policy, PersonalityEvolutionPolicy):
            self._policy = policy
        elif policy is None:
            self._policy = PersonalityEvolutionPolicy()
        else:
            # 兼容 dict-like 或其他对象
            try:
                self._policy = PersonalityEvolutionPolicy()
                if hasattr(policy, "evaluate") and callable(getattr(policy, "evaluate")):
                    # wrap: 优先用外部 policy
                    self._policy = policy  # type: ignore[assignment]
            except Exception:
                self._policy = PersonalityEvolutionPolicy()

        # version store
        if isinstance(version_store, PersonalityVersionStore):
            self._version_store = version_store
        elif version_store is None:
            initial = initial_snapshot
            if initial is None:
                # 尝试从 snapshot_provider 读
                sp = _load_personality_snapshot(snapshot_provider)
                initial = sp.get("raw") or {}
            self._version_store = PersonalityVersionStore(initial_snapshot=initial)
        else:
            # 假设是有 create_version 接口的对象
            if hasattr(version_store, "create_version") and callable(getattr(version_store, "create_version")):
                self._version_store = version_store  # type: ignore[assignment]
            else:
                self._version_store = PersonalityVersionStore(initial_snapshot=initial_snapshot)

        # evolution store
        if isinstance(evolution_store, PersonalityEvolutionStore):
            self._evolution_store = evolution_store
        elif evolution_store is None:
            self._evolution_store = PersonalityEvolutionStore()
        else:
            # 兼容 duck-typing:有 save / get / list_all / mark_rolled_back 接口的对象
            if hasattr(evolution_store, "save") and hasattr(evolution_store, "get"):
                self._evolution_store = evolution_store  # type: ignore[assignment]
            else:
                self._evolution_store = PersonalityEvolutionStore()  # fallback

        self._lock = threading.RLock()
        # request_id -> evolution_id 防重
        self._applied: Dict[str, str] = {}
        # evolution_id -> rollback evolution_id
        self._rollback_index: Dict[str, str] = {}

        # 统计
        self._apply_count: int = 0
        self._rollback_count: int = 0
        self._degraded_count: int = 0
        self._denied_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def apply_request(
        self,
        request: Any,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行一次 EvolutionRequest。

        必须满足:
          - request.status == "approved"
          - request 中包含 proposal.status == "ready_for_apply"
          - policy.evaluate() == "allow"
        """
        try:
            with self._lock:
                self._apply_count += 1
                # 1. 输入校验
                if not self._is_valid_request(request):
                    return self._fail_result(
                        reason=REASON_INVALID_INPUT,
                        error="request invalid",
                        request_id=self._extract_request_id(request),
                    )
                rid = _safe_str(self._extract_request_id(request), "")
                pid = _safe_str(self._extract_proposal_id(request), "")
                # 2. request 状态校验
                rstatus = _safe_str(self._extract_request_status(request), "")
                if rstatus != REQUIRED_REQUEST_STATUS:
                    self._denied_count += 1
                    return self._fail_result(
                        reason=REASON_INVALID_STATE,
                        error=f"request status must be '{REQUIRED_REQUEST_STATUS}', got '{rstatus}'",
                        request_id=rid,
                        proposal_id=pid,
                    )
                # 3. proposal 状态校验
                pstatus = _safe_str(self._extract_proposal_status(request), "")
                if pstatus != REQUIRED_PROPOSAL_STATUS:
                    self._denied_count += 1
                    return self._fail_result(
                        reason=REASON_INVALID_STATE,
                        error=f"proposal status must be '{REQUIRED_PROPOSAL_STATUS}', got '{pstatus}'",
                        request_id=rid,
                        proposal_id=pid,
                    )
                # 4. 重复 apply 保护
                if rid in self._applied:
                    existing_eid = self._applied[rid]
                    self._denied_count += 1
                    return self._fail_result(
                        reason=APPLY_REASON_DUPLICATE,
                        error=f"request already applied: evolution_id={existing_eid}",
                        request_id=rid,
                        proposal_id=pid,
                        evolution_id=existing_eid,
                    )
                # 5. 解析 changes/confidence/evidence
                changes = self._extract_changes(request)
                confidence = self._extract_confidence(request)
                evidence = self._extract_evidence(request)
                act = _safe_str(actor, self._default_actor) or self._default_actor
                # 6. 读取当前 snapshot
                snapshot = self._get_snapshot_safe()
                before_traits = dict(snapshot.get("traits") or {})
                before_raw = dict(snapshot.get("raw") or {})
                # 7. 完整性校验:不能修改 core_identity
                violation = self._check_integrity(changes, before_traits)
                if violation:
                    self._denied_count += 1
                    self._record_failed(
                        action=AUDIT_ACTION_FAILED,
                        rid=rid,
                        pid=pid,
                        reason=APPLY_REASON_INTEGRITY,
                        error=violation,
                        actor=act,
                    )
                    return self._fail_result(
                        reason=APPLY_REASON_INTEGRITY,
                        error=violation,
                        request_id=rid,
                        proposal_id=pid,
                    )
                # 8. policy 评估
                policy_result = self._policy.evaluate(
                    changes=changes,
                    confidence=confidence,
                    snapshot=before_raw,
                )
                decision = _safe_str(policy_result.get("decision"), DECISION_NEEDS_REVIEW)
                reasons_list = _safe_list(policy_result.get("reasons"))
                if decision == DECISION_DENY:
                    self._denied_count += 1
                    self._record_failed(
                        action=AUDIT_ACTION_FAILED,
                        rid=rid,
                        pid=pid,
                        reason=APPLY_REASON_POLICY_DENIED,
                        error=";".join(reasons_list) or "policy_denied",
                        actor=act,
                    )
                    return self._fail_result(
                        reason=APPLY_REASON_POLICY_DENIED,
                        error=";".join(reasons_list) or "policy_denied",
                        request_id=rid,
                        proposal_id=pid,
                        policy_result=_safe_deepcopy(policy_result),
                    )
                if decision == DECISION_NEEDS_REVIEW:
                    self._denied_count += 1
                    self._record_failed(
                        action=AUDIT_ACTION_FAILED,
                        rid=rid,
                        pid=pid,
                        reason=APPLY_REASON_POLICY_REVIEW,
                        error=";".join(reasons_list) or "policy_needs_review",
                        actor=act,
                    )
                    return self._fail_result(
                        reason=APPLY_REASON_POLICY_REVIEW,
                        error=";".join(reasons_list) or "policy_needs_review",
                        request_id=rid,
                        proposal_id=pid,
                        policy_result=_safe_deepcopy(policy_result),
                    )
                # 9. 计算 after_traits
                after_traits = self._compute_after(
                    before_traits=before_traits,
                    changes=changes,
                    weighted=_safe_list(policy_result.get("weighted_changes")),
                )
                after_raw = dict(before_raw)
                after_raw["traits"] = _safe_deepcopy(after_traits)
                # 10. 写入版本
                old_version = self._version_store.get_current_version_id() or ""
                evolution_id = self._new_evolution_id()
                try:
                    new_version = self._version_store.create_version(
                        traits_snapshot=after_raw,
                        source_evolution_id=evolution_id,
                    )
                except Exception as exc:
                    self._degraded_count += 1
                    self._last_error = f"create_version failed: {exc}"
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error=str(exc),
                        request_id=rid,
                        proposal_id=pid,
                    )
                if not new_version:
                    self._degraded_count += 1
                    self._last_error = "create_version returned empty"
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error="create_version returned empty",
                        request_id=rid,
                        proposal_id=pid,
                    )
                # 11. 构造 record 并保存
                ts = _now_iso()
                applied_changes = self._build_applied_changes(
                    changes=changes,
                    weighted=_safe_list(policy_result.get("weighted_changes")),
                    before_traits=before_traits,
                    after_traits=after_traits,
                )
                record = build_personality_evolution_record(
                    evolution_id=evolution_id,
                    request_id=rid,
                    proposal_id=pid,
                    before_snapshot={"traits": before_traits, "raw": before_raw},
                    after_snapshot={"traits": after_traits, "raw": after_raw},
                    changes=applied_changes,
                    reason=APPLY_REASON_OK,
                    evidence=evidence,
                    confidence=confidence,
                    timestamp=ts,
                    old_version=old_version,
                    new_version=new_version,
                    actor=act,
                    status=RECORD_STATUS_APPLIED,
                    self_model_impact=SELF_MODEL_IMPACT_PENDING,
                    extra={
                        "policy_reasons": list(reasons_list),
                        "policy_decision": decision,
                    },
                )
                saved = self._evolution_store.save(record)
                if not saved:
                    self._degraded_count += 1
                    self._last_error = "evolution store save failed"
                    return self._fail_result(
                        reason=REASON_RECORD_ERROR,
                        error="evolution store save failed",
                        request_id=rid,
                        proposal_id=pid,
                        evolution_id=evolution_id,
                        old_version=old_version,
                        new_version=new_version,
                    )
                self._applied[rid] = evolution_id
                # 12. 通知 policy(用于重复检测)
                try:
                    for w in _safe_list(policy_result.get("weighted_changes")):
                        if isinstance(w, dict):
                            trait = _safe_str(w.get("trait") or w.get("key"), "")
                            delta = _safe_float(w.get("delta"), 0.0)
                            weight = _safe_float(w.get("weight"), 1.0)
                            if trait:
                                self._policy.record_applied_change(
                                    trait=trait,
                                    delta=delta,
                                    weight=weight,
                                )
                except Exception:
                    pass
                # 13. audit
                audit_ok = self._record_applied_audit(
                    rid=rid,
                    pid=pid,
                    evolution_id=evolution_id,
                    old_version=old_version,
                    new_version=new_version,
                    changes=applied_changes,
                    timestamp=ts,
                    actor=act,
                    confidence=confidence,
                )
                return self._build_result(
                    success=True,
                    degraded=False,
                    error="",
                    evolution_id=evolution_id,
                    request_id=rid,
                    proposal_id=pid,
                    old_version=old_version,
                    new_version=new_version,
                    changes_applied=applied_changes,
                    timestamp=ts,
                    actor=act,
                    audit_recorded=audit_ok,
                    policy_result=_safe_deepcopy(policy_result),
                    reason=APPLY_REASON_OK,
                )
        except Exception as exc:
            self._degraded_count += 1
            self._last_error = str(exc)
            logger.warning("[PersonalityEvolutionEngine] apply_request failed: %s", exc)
            return self._fail_result(
                reason=REASON_INTERNAL_ERROR,
                error=str(exc),
                request_id=self._extract_request_id(request),
                proposal_id=self._extract_proposal_id(request),
            )

    def rollback(
        self,
        evolution_id: str,
        actor: Optional[str] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        回滚到指定 evolution 的 before_snapshot。
        不删除任何历史。
        """
        try:
            with self._lock:
                self._rollback_count += 1
                eid = _safe_str(evolution_id, "")
                if not eid:
                    return self._fail_result(
                        reason=REASON_INVALID_INPUT,
                        error="evolution_id required",
                    )
                record = self._evolution_store.get(eid)
                if not isinstance(record, dict):
                    return self._fail_result(
                        reason=REASON_ROLLBACK_NOT_FOUND,
                        error=f"evolution not found: {eid}",
                        evolution_id=eid,
                    )
                if record.get("status") == RECORD_STATUS_ROLLED_BACK:
                    return self._fail_result(
                        reason=REASON_ROLLBACK_NOT_FOUND,
                        error=f"evolution already rolled back: {eid}",
                        evolution_id=eid,
                    )
                before_snapshot = _safe_dict(record.get("before_snapshot"))
                before_traits = _safe_dict(before_snapshot.get("traits"))
                if not before_traits:
                    return self._fail_result(
                        reason=REASON_ROLLBACK_ERROR,
                        error="before_snapshot empty or invalid",
                        evolution_id=eid,
                    )
                act = _safe_str(actor, self._default_actor) or self._default_actor
                # 创建新版本(回滚视为一次新版本)
                ts = _now_iso()
                old_version = self._version_store.get_current_version_id() or ""
                rollback_evo_id = self._new_evolution_id()
                # 还原 raw (使用 before_raw)
                before_raw = _safe_dict(before_snapshot.get("raw"))
                new_raw = dict(before_raw)
                new_raw["traits"] = _safe_deepcopy(before_traits)
                try:
                    new_version = self._version_store.create_version(
                        traits_snapshot=new_raw,
                        source_evolution_id=rollback_evo_id,
                    )
                except Exception as exc:
                    self._degraded_count += 1
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error=str(exc),
                        evolution_id=eid,
                    )
                if not new_version:
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error="create_version returned empty",
                        evolution_id=eid,
                    )
                # 生成 rollback record
                rollback_reason = _safe_str(reason, "") or "rollback"
                rollback_record = build_personality_evolution_record(
                    evolution_id=rollback_evo_id,
                    request_id=_safe_str(record.get("request_id"), ""),
                    proposal_id=_safe_str(record.get("proposal_id"), ""),
                    before_snapshot={
                        "traits": _safe_deepcopy(_safe_dict(self._get_current_traits())),
                        "raw": _safe_deepcopy(self._get_current_raw()),
                    },
                    after_snapshot={
                        "traits": _safe_deepcopy(before_traits),
                        "raw": _safe_deepcopy(new_raw),
                    },
                    changes=[
                        {
                            "key": "rollback",
                            "delta": 0.0,
                            "weight": 1.0,
                            "rollback_of": eid,
                        }
                    ],
                    reason=rollback_reason,
                    evidence={
                        "rollback_of": eid,
                        "rollback_reason": rollback_reason,
                    },
                    confidence=_safe_float(record.get("confidence"), 0.0),
                    timestamp=ts,
                    old_version=old_version,
                    new_version=new_version,
                    actor=act,
                    status=RECORD_STATUS_APPLIED,
                    self_model_impact=SELF_MODEL_IMPACT_PENDING,
                    extra={
                        "is_rollback": True,
                        "rollback_of": eid,
                    },
                )
                saved = self._evolution_store.save(rollback_record)
                if not saved:
                    self._degraded_count += 1
                    return self._fail_result(
                        reason=REASON_RECORD_ERROR,
                        error="rollback record save failed",
                        evolution_id=eid,
                    )
                # 标记原 evolution 为 rolled_back
                audit_detail = {
                    "evolution_id": eid,
                    "rollback_evolution_id": rollback_evo_id,
                    "old_version": old_version,
                    "new_version": new_version,
                    "actor": act,
                    "timestamp": ts,
                    "reason": rollback_reason,
                }
                self._evolution_store.mark_rolled_back(
                    evolution_id=eid,
                    by_evolution_id=rollback_evo_id,
                    audit_detail=audit_detail,
                )
                self._rollback_index[eid] = rollback_evo_id
                # audit
                audit_ok = _record_audit_safely(
                    self._audit,
                    action=AUDIT_ACTION_ROLLED_BACK,
                    detail=audit_detail,
                    result="success",
                )
                return self._build_result(
                    success=True,
                    degraded=False,
                    error="",
                    evolution_id=rollback_evo_id,
                    request_id=_safe_str(record.get("request_id"), ""),
                    proposal_id=_safe_str(record.get("proposal_id"), ""),
                    old_version=old_version,
                    new_version=new_version,
                    changes_applied=[
                        {"key": "rollback", "delta": 0.0, "rollback_of": eid}
                    ],
                    timestamp=ts,
                    actor=act,
                    audit_recorded=audit_ok,
                    policy_result={},
                    reason="rolled_back",
                    rollback_of=eid,
                )
        except Exception as exc:
            self._degraded_count += 1
            self._last_error = str(exc)
            logger.warning("[PersonalityEvolutionEngine] rollback failed: %s", exc)
            return self._fail_result(
                reason=REASON_INTERNAL_ERROR,
                error=str(exc),
                evolution_id=evolution_id if isinstance(evolution_id, str) else "",
            )

    def get_evolution(self, evolution_id: str) -> Optional[Dict[str, Any]]:
        try:
            eid = _safe_str(evolution_id, "")
            if not eid:
                return None
            return self._evolution_store.get(eid)
        except Exception:
            return None

    def list_evolutions(
        self,
        request_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            items: List[Dict[str, Any]] = []
            if request_id:
                items = self._evolution_store.list_by_request(request_id)
            elif proposal_id:
                items = self._evolution_store.list_by_proposal(proposal_id)
            else:
                items = self._evolution_store.list_all()
            if limit is not None:
                try:
                    lim = int(limit)
                    if lim >= 0:
                        items = items[:lim]
                except Exception:
                    pass
            return items
        except Exception:
            return []

    def get_current_version(self) -> Optional[Dict[str, Any]]:
        try:
            return self._version_store.get_current()
        except Exception:
            return None

    def get_stats(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "apply_count": int(self._apply_count),
                    "rollback_count": int(self._rollback_count),
                    "degraded_count": int(self._degraded_count),
                    "denied_count": int(self._denied_count),
                    "applied_request_count": len(self._applied),
                    "evolution_record_count": self._evolution_store.get_stats().get("total_records", 0),
                    "version_count": self._version_store.count(),
                    "current_version": self._version_store.get_current_version_id(),
                    "last_error": self._last_error,
                }
        except Exception:
            return {
                "apply_count": 0,
                "rollback_count": 0,
                "degraded_count": 0,
                "denied_count": 0,
                "applied_request_count": 0,
                "evolution_record_count": 0,
                "version_count": 0,
                "current_version": "",
                "last_error": "stats_unavailable",
            }

    def reset(self) -> None:
        with self._lock:
            self._applied.clear()
            self._rollback_index.clear()
            self._apply_count = 0
            self._rollback_count = 0
            self._degraded_count = 0
            self._denied_count = 0
            self._last_error = None
            try:
                self._evolution_store.reset()
            except Exception:
                pass
            try:
                self._version_store.reset()
            except Exception:
                pass

    # --------------------------------------------------------
    # Internal helpers
    # --------------------------------------------------------

    def _new_evolution_id(self) -> str:
        try:
            return f"ev_{uuid.uuid4().hex[:16]}"
        except Exception:
            try:
                return f"ev_{int(_now_ts() * 1000):x}"
            except Exception:
                return "ev_0000000000000000"

    def _is_valid_request(self, request: Any) -> bool:
        if request is None:
            return False
        if isinstance(request, dict):
            return True
        if hasattr(request, "request_id") or hasattr(request, "__dict__"):
            return True
        return False

    def _extract_request_id(self, request: Any) -> str:
        try:
            if isinstance(request, dict):
                return _safe_str(request.get("request_id"), "")
            return _safe_str(getattr(request, "request_id", None), "")
        except Exception:
            return ""

    def _extract_request_status(self, request: Any) -> str:
        try:
            if isinstance(request, dict):
                return _safe_str(request.get("status"), "")
            return _safe_str(getattr(request, "status", None), "")
        except Exception:
            return ""

    def _extract_proposal_id(self, request: Any) -> str:
        try:
            if isinstance(request, dict):
                ev = request.get("evidence") or {}
                src = ev.get("source_proposal") or {}
                if isinstance(src, dict) and src.get("proposal_id"):
                    return _safe_str(src.get("proposal_id"), "")
                return _safe_str(request.get("proposal_id"), "")
            ev = getattr(request, "evidence", None)
            if isinstance(ev, dict):
                src = ev.get("source_proposal") or {}
                if isinstance(src, dict) and src.get("proposal_id"):
                    return _safe_str(src.get("proposal_id"), "")
            return _safe_str(getattr(request, "proposal_id", None), "")
        except Exception:
            return ""

    def _extract_proposal_status(self, request: Any) -> str:
        try:
            if isinstance(request, dict):
                ev = request.get("evidence") or {}
                src = ev.get("source_proposal") or {}
                if isinstance(src, dict) and src.get("status"):
                    return _safe_str(src.get("status"), "")
                return _safe_str(request.get("proposal_status"), "")
            ev = getattr(request, "evidence", None)
            if isinstance(ev, dict):
                src = ev.get("source_proposal") or {}
                if isinstance(src, dict) and src.get("status"):
                    return _safe_str(src.get("status"), "")
            return _safe_str(getattr(request, "proposal_status", None), "")
        except Exception:
            return ""

    def _extract_changes(self, request: Any) -> List[Dict[str, Any]]:
        try:
            if isinstance(request, dict):
                c = request.get("changes")
            else:
                c = getattr(request, "changes", None)
            if not isinstance(c, list):
                return []
            out: List[Dict[str, Any]] = []
            for item in c:
                if isinstance(item, dict):
                    out.append(dict(item))
            return out
        except Exception:
            return []

    def _extract_confidence(self, request: Any) -> float:
        try:
            if isinstance(request, dict):
                return _safe_float(request.get("confidence"), 0.0)
            return _safe_float(getattr(request, "confidence", None), 0.0)
        except Exception:
            return 0.0

    def _extract_evidence(self, request: Any) -> Dict[str, Any]:
        try:
            if isinstance(request, dict):
                ev = request.get("evidence")
            else:
                ev = getattr(request, "evidence", None)
            return _safe_deepcopy(ev) if isinstance(ev, dict) else {}
        except Exception:
            return {}

    def _get_snapshot_safe(self) -> Dict[str, Any]:
        try:
            return _load_personality_snapshot(self._snapshot_provider)
        except Exception:
            return {"traits": {}, "raw": {}}

    def _get_current_traits(self) -> Dict[str, float]:
        try:
            v = self._version_store.get_current()
            if isinstance(v, dict):
                snap = v.get("snapshot") or {}
                if isinstance(snap, dict):
                    t = snap.get("traits")
                    if isinstance(t, dict):
                        return {k: float(val) for k, val in t.items()}
            return {}
        except Exception:
            return {}

    def _get_current_raw(self) -> Dict[str, Any]:
        try:
            v = self._version_store.get_current()
            if isinstance(v, dict):
                snap = v.get("snapshot")
                if isinstance(snap, dict):
                    return _safe_deepcopy(snap)
            return {}
        except Exception:
            return {}

    def _check_integrity(
        self,
        changes: List[Dict[str, Any]],
        before_traits: Dict[str, float],
    ) -> str:
        """
        完整性校验:不能修改 core_identity 字段。
        若尝试修改 immutable_traits → 拒绝(由 policy 处理,这里再保险一次)。
        """
        try:
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                key = _safe_str(ch.get("key") or ch.get("trait")
                                or ch.get("target") or ch.get("field"), "")
                if not key:
                    continue
                if key in DEFAULT_CORE_IDENTITY_FIELDS:
                    return f"core_identity_field_forbidden:{key}"
                if key in DEFAULT_IMMUTABLE_TRAITS:
                    return f"immutable_trait_forbidden:{key}"
        except Exception:
            pass
        return ""

    def _compute_after(
        self,
        before_traits: Dict[str, float],
        changes: List[Dict[str, Any]],
        weighted: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """
        计算 after_traits。
        - 优先使用 weighted(已含 weight)
        - 否则按 changes 应用
        - 所有 trait clamp 到 [0, 1]
        """
        after = dict(before_traits)
        try:
            # 优先 weighted
            if weighted:
                for w in weighted:
                    if not isinstance(w, dict):
                        continue
                    key = _safe_str(w.get("trait") or w.get("key"), "")
                    if not key:
                        continue
                    delta = _safe_float(w.get("delta"), 0.0)
                    weight = _safe_float(w.get("weight"), 1.0)
                    if key in DEFAULT_CORE_IDENTITY_FIELDS or key in DEFAULT_IMMUTABLE_TRAITS:
                        continue
                    cur = after.get(key, 0.5)
                    new_val = cur + delta * weight
                    if new_val < 0.0:
                        new_val = 0.0
                    elif new_val > 1.0:
                        new_val = 1.0
                    after[key] = round(new_val, 6)
                return after
            # 否则 raw changes
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                key = _safe_str(ch.get("key") or ch.get("trait")
                                or ch.get("target") or ch.get("field"), "")
                if not key:
                    continue
                if key in DEFAULT_CORE_IDENTITY_FIELDS or key in DEFAULT_IMMUTABLE_TRAITS:
                    continue
                delta = _safe_float(ch.get("delta"), 0.0)
                cur = after.get(key, 0.5)
                new_val = cur + delta
                if new_val < 0.0:
                    new_val = 0.0
                elif new_val > 1.0:
                    new_val = 1.0
                after[key] = round(new_val, 6)
        except Exception:
            pass
        return after

    def _build_applied_changes(
        self,
        changes: List[Dict[str, Any]],
        weighted: List[Dict[str, Any]],
        before_traits: Dict[str, float],
        after_traits: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                key = _safe_str(ch.get("key") or ch.get("trait")
                                or ch.get("target") or ch.get("field"), "")
                if not key:
                    continue
                before = float(before_traits.get(key, 0.5))
                after = float(after_traits.get(key, before))
                # 找 weight
                w = 1.0
                for wch in weighted or []:
                    if isinstance(wch, dict):
                        wk = _safe_str(wch.get("trait") or wch.get("key"), "")
                        if wk == key:
                            w = _safe_float(wch.get("weight"), 1.0)
                            break
                out.append({
                    "key": key,
                    "trait": key,
                    "delta": round(after - before, 6),
                    "weight": w,
                    "before": round(before, 6),
                    "after": round(after, 6),
                })
        except Exception:
            pass
        return out

    def _record_applied_audit(
        self,
        rid: str,
        pid: str,
        evolution_id: str,
        old_version: str,
        new_version: str,
        changes: List[Dict[str, Any]],
        timestamp: str,
        actor: str,
        confidence: float,
    ) -> bool:
        try:
            detail = {
                "evolution_id": evolution_id,
                "request_id": rid,
                "proposal_id": pid,
                "old_version": old_version,
                "new_version": new_version,
                "changes": _safe_list(changes),
                "timestamp": timestamp,
                "actor": actor,
                "confidence": float(confidence) if confidence is not None else 0.0,
                "self_model_impact": SELF_MODEL_IMPACT_PENDING,
                "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            }
            return _record_audit_safely(
                self._audit,
                action=AUDIT_ACTION_APPLIED,
                detail=detail,
                result="success",
            )
        except Exception:
            return False

    def _record_failed(
        self,
        action: str,
        rid: str,
        pid: str,
        reason: str,
        error: str,
        actor: str,
    ) -> None:
        try:
            detail = {
                "request_id": rid,
                "proposal_id": pid,
                "reason": reason,
                "error": error,
                "actor": actor,
                "timestamp": _now_iso(),
                "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            }
            _record_audit_safely(
                self._audit,
                action=action,
                detail=detail,
                result="failed",
            )
        except Exception:
            pass

    def _fail_result(
        self,
        reason: str,
        error: str,
        request_id: str = "",
        proposal_id: str = "",
        evolution_id: str = "",
        old_version: str = "",
        new_version: str = "",
        policy_result: Optional[Dict[str, Any]] = None,
        rollback_of: str = "",
    ) -> Dict[str, Any]:
        return self._build_result(
            success=False,
            degraded=True,
            error=error,
            evolution_id=evolution_id,
            request_id=request_id,
            proposal_id=proposal_id,
            old_version=old_version,
            new_version=new_version,
            changes_applied=[],
            timestamp=_now_iso(),
            actor=self._default_actor,
            audit_recorded=False,
            policy_result=policy_result or {},
            reason=reason,
            rollback_of=rollback_of,
        )

    def _build_result(
        self,
        success: bool,
        degraded: bool,
        error: str,
        evolution_id: str,
        request_id: str,
        proposal_id: str,
        old_version: str,
        new_version: str,
        changes_applied: List[Dict[str, Any]],
        timestamp: str,
        actor: str,
        audit_recorded: bool,
        policy_result: Dict[str, Any],
        reason: str,
        rollback_of: str = "",
    ) -> Dict[str, Any]:
        return {
            "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "error": _safe_str(error, ""),
            "evolution_id": _safe_str(evolution_id, ""),
            "request_id": _safe_str(request_id, ""),
            "proposal_id": _safe_str(proposal_id, ""),
            "old_version": _safe_str(old_version, ""),
            "new_version": _safe_str(new_version, ""),
            "changes_applied": _safe_list(changes_applied),
            "timestamp": _safe_str(timestamp, "") or _now_iso(),
            "actor": _safe_str(actor, "") or self._default_actor,
            "audit_recorded": bool(audit_recorded),
            "policy_result": _safe_dict(policy_result),
            "reason": _safe_str(reason, APPLY_REASON_NONE),
            "rollback_of": _safe_str(rollback_of, ""),
            "self_model_impact": SELF_MODEL_IMPACT_PENDING,
            "engine": {
                "name": PERSONALITY_EVOLUTION_ENGINE_NAME,
                "version": PERSONALITY_EVOLUTION_ENGINE_VERSION,
                "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            },
        }


# ============================================================
# Factory + safe wrapper
# ============================================================


def create_personality_evolution_engine(
    snapshot_provider: Any = None,
    evolution_store: Any = None,
    version_store: Any = None,
    policy: Any = None,
    audit: Any = None,
    bridge: Any = None,
    initial_snapshot: Optional[Dict[str, Any]] = None,
    default_actor: str = ACTOR_DEFAULT,
) -> PersonalityEvolutionEngine:
    return PersonalityEvolutionEngine(
        snapshot_provider=snapshot_provider,
        evolution_store=evolution_store,
        version_store=version_store,
        policy=policy,
        audit=audit,
        bridge=bridge,
        initial_snapshot=initial_snapshot,
        default_actor=default_actor,
    )


def safe_apply_request(
    engine: Any,
    request: Any,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    if engine is None or not hasattr(engine, "apply_request"):
        return {
            "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "error": "engine unavailable",
            "evolution_id": "",
            "request_id": "",
            "proposal_id": "",
            "old_version": "",
            "new_version": "",
            "changes_applied": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, ACTOR_DEFAULT) or ACTOR_DEFAULT,
            "audit_recorded": False,
            "policy_result": {},
            "reason": REASON_DEGRADED,
            "rollback_of": "",
            "self_model_impact": SELF_MODEL_IMPACT_PENDING,
            "engine": {
                "name": PERSONALITY_EVOLUTION_ENGINE_NAME,
                "version": PERSONALITY_EVOLUTION_ENGINE_VERSION,
                "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            },
        }
    try:
        return engine.apply_request(request, actor=actor)
    except Exception as exc:
        return {
            "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "error": str(exc),
            "evolution_id": "",
            "request_id": "",
            "proposal_id": "",
            "old_version": "",
            "new_version": "",
            "changes_applied": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, ACTOR_DEFAULT) or ACTOR_DEFAULT,
            "audit_recorded": False,
            "policy_result": {},
            "reason": REASON_INTERNAL_ERROR,
            "rollback_of": "",
            "self_model_impact": SELF_MODEL_IMPACT_PENDING,
            "engine": {
                "name": PERSONALITY_EVOLUTION_ENGINE_NAME,
                "version": PERSONALITY_EVOLUTION_ENGINE_VERSION,
                "schema_version": PERSONALITY_EVOLUTION_ENGINE_SCHEMA_VERSION,
            },
        }
