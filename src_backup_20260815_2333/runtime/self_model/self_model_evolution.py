# -*- coding: utf-8 -*-
"""
src/runtime/self_model/self_model_evolution.py

Phase C.8.7 SelfModel Evolution Engine.

只负责:
  - 接收 PersonalityEvolutionRecord
  - 验证 evolution.success == True
  - 读取当前 self_model snapshot
  - 通过 SelfModelEvolutionPolicy 评估 changes
  - 生成 reflection 内容(self_description / growth_understanding / capability_boundary)
  - 计算 after_snapshot
  - 通过 SelfModelVersionStore.create_version() 创建新版本
  - 通过 SelfModelEvolutionStore.save() 写入 SelfModelEvolutionRecord
  - 记录 audit
  - 提供 rollback(reflection_id) 恢复 before_snapshot

绝不负责:
  - 直接修改 Personality
  - 修改 Growth / Relationship
  - 修改 core_identity
  - 删除历史 reflection
  - 在没有 PersonalityEvolutionRecord 的情况下写入 self_model
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .self_model_policy import (
    SelfModelEvolutionPolicy,
    create_self_model_evolution_policy,
    DECISION_ALLOW,
    DECISION_NEEDS_REVIEW,
    DECISION_DENY,
    REASON_CONFIDENCE_TOO_LOW,
    REASON_IDENTITY_PROTECTED,
    REASON_CHANGE_RATIO_EXCEEDED,
    REASON_REPEATED_REFLECTION,
    REASON_OK,
    DEFAULT_IDENTITY_PROTECTED_FIELDS,
    DEFAULT_EVOLVABLE_FIELDS,
    MAX_CHANGE_RATIO,
    MIN_CONFIDENCE_FOR_APPLY,
)
from .self_model_record import (
    SELF_MODEL_RECORD_SCHEMA_VERSION,
    SELF_MODEL_RECORD_NAME,
    SELF_MODEL_RECORD_VERSION,
    RECORD_STATUS_PENDING,
    RECORD_STATUS_APPLIED,
    RECORD_STATUS_REJECTED,
    RECORD_STATUS_ROLLED_BACK,
    ROLLBACK_STATUS_NONE,
    ROLLBACK_STATUS_ROLLED_BACK,
    ACTOR_RUNTIME,
    ACTOR_DEFAULT,
    REASON_INVALID_INPUT,
    REASON_INVALID_RECORD,
    REASON_SNAPSHOT_ERROR,
    REASON_RECORD_ERROR,
    REASON_VERSION_ERROR,
    REASON_INTERNAL_ERROR,
    REASON_DEGRADED,
    REASON_OK as RECORD_REASON_OK,
    REASON_NONE,
    build_self_model_evolution_record,
    build_self_description_change,
    build_growth_understanding,
    build_capability_boundary,
    SelfModelEvolutionStore,
    SelfModelVersionStore,
    create_self_model_evolution_record_store,
    create_self_model_version_store,
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

SELF_MODEL_EVOLUTION_SCHEMA_VERSION = "1.0"
SELF_MODEL_EVOLUTION_NAME = "self_model_evolution_engine"
SELF_MODEL_EVOLUTION_VERSION = "1.0.0"

# Audit actions
AUDIT_ACTION_CREATED = "runtime_self_model_evolution_created"
AUDIT_ACTION_FAILED = "runtime_self_model_evolution_failed"
AUDIT_ACTION_ROLLED_BACK = "runtime_self_model_evolution_rollback"
AUDIT_COMPONENT = "runtime_self_model_evolution"

# Reflection reasons
REFLECT_REASON_OK = "reflected"
REFLECT_REASON_INVALID_RECORD = "invalid_evolution_record"
REFLECT_REASON_POLICY_DENIED = "policy_denied"
REFLECT_REASON_POLICY_REVIEW = "policy_needs_review"
REFLECT_REASON_DUPLICATE = "already_reflected"
REFLECT_REASON_NONE = ""

ALL_AUDIT_ACTIONS: FrozenSet[str] = frozenset({
    AUDIT_ACTION_CREATED,
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
# SelfModel snapshot reader (read-only)
# ============================================================


def _load_self_model_snapshot(snapshot_provider: Any) -> Dict[str, Any]:
    """
    从 snapshot_provider 读取当前 self_model snapshot。
    返回 {"fields": {...}, "raw": 原 dict}
    """
    if snapshot_provider is None:
        return {"fields": {}, "raw": {}}
    try:
        snap: Any = None
        for attr in ("get_snapshot", "snapshot", "get_self_model", "read"):
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
            return {"fields": {}, "raw": {}}
        # 解析 self_model 字段
        fields: Dict[str, Any] = {}
        sm = snap.get("self_model")
        if isinstance(sm, dict):
            for k, v in sm.items():
                fields[str(k)] = v
        else:
            for k, v in snap.items():
                if k in DEFAULT_IDENTITY_PROTECTED_FIELDS:
                    continue
                fields[str(k)] = v
        return {
            "fields": fields,
            "raw": _safe_deepcopy(snap),
        }
    except Exception:
        return {"fields": {}, "raw": {}}


# ============================================================
# Reflection content generator
# ============================================================


def _build_reflection_changes(
    personality_evolution: Dict[str, Any],
    policy_weighted: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    根据 PersonalityEvolutionRecord 构造 self_model reflection changes。

    包括:
      - self_description  (基于 changes)
      - growth_understanding (基于 evolution 原因)
      - capability_boundary (基于 evolution 类型)
    """
    changes_out: List[Dict[str, Any]] = []
    try:
        # 1. self_description
        ev_changes = personality_evolution.get("changes") or []
        if isinstance(ev_changes, list) and ev_changes:
            traits_bumped: List[str] = []
            for c in ev_changes:
                if not isinstance(c, dict):
                    continue
                key = _safe_str(c.get("key") or c.get("trait"), "")
                delta = _safe_float(c.get("delta"), 0.0)
                if key:
                    if delta > 0:
                        traits_bumped.append(f"加强 {key}")
                    elif delta < 0:
                        traits_bumped.append(f"降低 {key}")
            if traits_bumped:
                before_desc = f"我倾向于保持稳定的特质状态。"
                after_desc = (
                    f"我刚刚经历了一些特质变化:{', '.join(traits_bumped)}。"
                    f"我愿意将这种变化纳入自我理解。"
                )
                changes_out.append(build_self_description_change(
                    before=before_desc,
                    after=after_desc,
                    context={"source": "personality_evolution"},
                ))

        # 2. growth_understanding
        reason = _safe_str(personality_evolution.get("reason"), "")
        evidence = _safe_dict(personality_evolution.get("evidence"))
        triggers: List[str] = []
        if reason:
            triggers.append(reason)
        if isinstance(evidence, dict):
            src = evidence.get("source_proposal") or {}
            if isinstance(src, dict) and src.get("proposal_id"):
                triggers.append(f"proposal:{src.get('proposal_id')}")
        if not triggers:
            triggers.append("personality_evolution")
        changes_out.append({
            "field": "growth_understanding",
            "before": "我还在持续观察自己的成长方向。",
            "after": build_growth_understanding(
                summary="我刚刚理解到自己因为一些经历而发生了人格演化。",
                triggers=triggers,
                related_changes=[
                    _safe_str(c.get("key") or c.get("trait"))
                    for c in ev_changes
                    if isinstance(c, dict)
                ],
            ),
        })

        # 3. capability_boundary(基于 weighted changes 评估)
        capabilities: List[str] = []
        for w in policy_weighted or []:
            if not isinstance(w, dict):
                continue
            f = _safe_str(w.get("field") or w.get("key"), "")
            if f:
                capabilities.append(f"可以调整 {f}")
        changes_out.append({
            "field": "capability_boundary",
            "before": {"capabilities": [], "limits": []},
            "after": build_capability_boundary(
                capabilities=capabilities,
                limits=["不修改核心身份", "不删除历史"],
            ),
        })
    except Exception:
        pass
    return changes_out


# ============================================================
# SelfModelEvolutionEngine
# ============================================================


class SelfModelEvolutionEngine:
    """
    [DEPRECATED][Authority Registry v1.0] SelfModelEvolutionEngine（self_model_evolution.py 版本）。

    DEPRECATED SINCE: Phase 4.0-R1
    **HIGH RISK**:  与 `src/runtime/self_model/evolution/self_model_evolution_engine.py`
                  中的权威 SelfModelEvolutionEngine **同名不同实现**，极易造成调用方
                  错 import 后 self-model 演化逻辑混乱。
    CANONICAL:    `src/runtime/self_model/evolution/self_model_evolution_engine.py::SelfModelEvolutionEngine`
                  （Canonical SelfModel 体系内版本，五层闭环实现）。
    MIGRATION:    Phase 4.0-R3 改类名为 `LegacySelfModelEvolutionEngine` 并改为对 Canonical 的薄转发包装。

    SelfModel Evolution Engine (Phase C.8.7 / v1.0) — Legacy 版本。
    让 self_model 与 personality evolution 保持同步理解（deprecated，内部转发至 Canonical 版本）。
    """

    SCHEMA_VERSION = SELF_MODEL_EVOLUTION_SCHEMA_VERSION
    NAME = SELF_MODEL_EVOLUTION_NAME
    VERSION = SELF_MODEL_EVOLUTION_VERSION

    def __init__(
        self,
        snapshot_provider: Any = None,
        evolution_store: Any = None,
        version_store: Any = None,
        policy: Any = None,
        audit: Any = None,
        initial_snapshot: Optional[Dict[str, Any]] = None,
        default_actor: str = ACTOR_DEFAULT,
    ) -> None:
        self._snapshot_provider = snapshot_provider
        self._audit = audit
        self._default_actor = str(default_actor or ACTOR_DEFAULT)

        # policy
        if isinstance(policy, SelfModelEvolutionPolicy):
            self._policy = policy
        elif policy is None:
            self._policy = SelfModelEvolutionPolicy()
        else:
            if hasattr(policy, "evaluate") and callable(getattr(policy, "evaluate")):
                self._policy = policy  # type: ignore[assignment]
            else:
                self._policy = SelfModelEvolutionPolicy()

        # version store
        if isinstance(version_store, SelfModelVersionStore):
            self._version_store = version_store
        elif version_store is None:
            initial = initial_snapshot
            if initial is None:
                sp = _load_self_model_snapshot(snapshot_provider)
                initial = sp.get("raw") or {}
            self._version_store = SelfModelVersionStore(initial_snapshot=initial)
        else:
            if hasattr(version_store, "create_version") and callable(getattr(version_store, "create_version")):
                self._version_store = version_store  # type: ignore[assignment]
            else:
                self._version_store = SelfModelVersionStore(initial_snapshot=initial_snapshot)

        # evolution store
        if isinstance(evolution_store, SelfModelEvolutionStore):
            self._evolution_store = evolution_store
        elif evolution_store is None:
            self._evolution_store = SelfModelEvolutionStore()
        else:
            if hasattr(evolution_store, "save") and hasattr(evolution_store, "get"):
                self._evolution_store = evolution_store  # type: ignore[assignment]
            else:
                self._evolution_store = SelfModelEvolutionStore()

        self._lock = threading.RLock()
        # evolution_id -> reflection_id 防重
        self._reflected: Dict[str, str] = {}
        # reflection_id -> rollback reflection_id
        self._rollback_index: Dict[str, str] = {}

        # 统计
        self._reflect_count: int = 0
        self._rollback_count: int = 0
        self._degraded_count: int = 0
        self._denied_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def reflect_evolution(
        self,
        evolution_record: Any,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        对一次 PersonalityEvolutionRecord 进行 reflection。

        入口限制:
          - 必须能取到 evolution_id
          - evolution_record.success == True
          - 否则返回 {success: false, reason: "invalid_evolution_record"}
        """
        try:
            with self._lock:
                self._reflect_count += 1
                # 1. 输入校验
                if not self._is_valid_record(evolution_record):
                    return self._fail_result(
                        reason=REASON_INVALID_INPUT,
                        error="evolution_record invalid",
                    )
                eid = _safe_str(self._extract_evolution_id(evolution_record), "")
                if not eid:
                    return self._fail_result(
                        reason=REASON_INVALID_RECORD,
                        error="evolution_id missing",
                    )
                # 2. success 检查
                rec_success = self._extract_evolution_success(evolution_record)
                if not rec_success:
                    self._denied_count += 1
                    return self._fail_result(
                        reason=REFLECT_REASON_INVALID_RECORD,
                        error="evolution_record.success is not True",
                        evolution_id=eid,
                    )
                # 3. 重复 reflection 保护
                if eid in self._reflected:
                    existing_rid = self._reflected[eid]
                    self._denied_count += 1
                    return self._fail_result(
                        reason=REFLECT_REASON_DUPLICATE,
                        error=f"evolution already reflected: reflection_id={existing_rid}",
                        evolution_id=eid,
                        reflection_id=existing_rid,
                    )
                # 4. 提取 confidence / evidence
                confidence = self._extract_confidence(evolution_record)
                evidence = self._extract_evidence(evolution_record)
                reason = self._extract_reason(evolution_record)
                act = _safe_str(actor, self._default_actor) or self._default_actor
                # 5. 读取当前 self_model snapshot
                snap = self._get_snapshot_safe()
                before_fields = dict(snap.get("fields") or {})
                before_raw = dict(snap.get("raw") or {})
                # 6. 生成 reflection changes
                weighted: List[Dict[str, Any]] = []
                changes = _build_reflection_changes(
                    personality_evolution=evolution_record if isinstance(evolution_record, dict) else {},
                    policy_weighted=weighted,
                )
                # 7. policy 评估
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
                        eid=eid,
                        reason=REFLECT_REASON_POLICY_DENIED,
                        error=";".join(reasons_list) or "policy_denied",
                        actor=act,
                    )
                    return self._fail_result(
                        reason=REFLECT_REASON_POLICY_DENIED,
                        error=";".join(reasons_list) or "policy_denied",
                        evolution_id=eid,
                        policy_result=_safe_deepcopy(policy_result),
                    )
                if decision == DECISION_NEEDS_REVIEW:
                    # Phase C.8.7 默认将 needs_review 视为拒绝(避免低置信演化)
                    self._denied_count += 1
                    self._record_failed(
                        action=AUDIT_ACTION_FAILED,
                        eid=eid,
                        reason=REFLECT_REASON_POLICY_REVIEW,
                        error=";".join(reasons_list) or "policy_needs_review",
                        actor=act,
                    )
                    return self._fail_result(
                        reason=REFLECT_REASON_POLICY_REVIEW,
                        error=";".join(reasons_list) or "policy_needs_review",
                        evolution_id=eid,
                        policy_result=_safe_deepcopy(policy_result),
                    )
                # 8. 计算 after
                after_fields = self._compute_after(before_fields, changes)
                after_raw = dict(before_raw)
                # 同步写入顶层 / self_model 子 dict
                if isinstance(after_raw.get("self_model"), dict):
                    after_raw["self_model"] = _safe_deepcopy(after_fields)
                else:
                    # 写入 evolvable 字段到顶层
                    for k, v in after_fields.items():
                        if k in DEFAULT_EVOLVABLE_FIELDS:
                            after_raw[k] = v
                # 9. 写入版本
                old_version = self._version_store.get_current_version_id() or ""
                reflection_id = self._new_reflection_id()
                try:
                    new_version = self._version_store.create_version(
                        snapshot=after_raw,
                        source_reflection_id=reflection_id,
                    )
                except Exception as exc:
                    self._degraded_count += 1
                    self._last_error = f"create_version failed: {exc}"
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error=str(exc),
                        evolution_id=eid,
                    )
                if not new_version:
                    self._degraded_count += 1
                    self._last_error = "create_version returned empty"
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error="create_version returned empty",
                        evolution_id=eid,
                    )
                # 10. 构造 record 并保存
                ts = _now_iso()
                record = build_self_model_evolution_record(
                    reflection_id=reflection_id,
                    evolution_id=eid,
                    before_snapshot={
                        "fields": before_fields,
                        "raw": before_raw,
                    },
                    after_snapshot={
                        "fields": after_fields,
                        "raw": after_raw,
                    },
                    changes=changes,
                    reason=REFLECT_REASON_OK,
                    evidence=evidence,
                    confidence=confidence,
                    timestamp=ts,
                    old_version=old_version,
                    new_version=new_version,
                    actor=act,
                    status=RECORD_STATUS_APPLIED,
                    rollback_status=ROLLBACK_STATUS_NONE,
                    extra={
                        "policy_reasons": list(reasons_list),
                        "policy_decision": decision,
                        "change_ratio": policy_result.get("change_ratio", 0.0),
                    },
                )
                saved = self._evolution_store.save(record)
                if not saved:
                    self._degraded_count += 1
                    self._last_error = "evolution store save failed"
                    return self._fail_result(
                        reason=REASON_RECORD_ERROR,
                        error="evolution store save failed",
                        evolution_id=eid,
                        reflection_id=reflection_id,
                        old_version=old_version,
                        new_version=new_version,
                    )
                self._reflected[eid] = reflection_id
                # 11. 通知 policy
                try:
                    for ch in changes:
                        if isinstance(ch, dict):
                            f = _safe_str(ch.get("field"), "")
                            if f:
                                self._policy.record_applied_reflection(
                                    field=f,
                                    weight=1.0,
                                )
                except Exception:
                    pass
                # 12. audit
                audit_ok = self._record_created_audit(
                    rid=reflection_id,
                    eid=eid,
                    old_version=old_version,
                    new_version=new_version,
                    changes=changes,
                    timestamp=ts,
                    actor=act,
                    confidence=confidence,
                )
                return self._build_result(
                    success=True,
                    degraded=False,
                    error="",
                    reflection_id=reflection_id,
                    evolution_id=eid,
                    old_version=old_version,
                    new_version=new_version,
                    changes=changes,
                    timestamp=ts,
                    actor=act,
                    audit_recorded=audit_ok,
                    policy_result=_safe_deepcopy(policy_result),
                    reason=REFLECT_REASON_OK,
                )
        except Exception as exc:
            self._degraded_count += 1
            self._last_error = str(exc)
            logger.warning("[SelfModelEvolutionEngine] reflect_evolution failed: %s", exc)
            return self._fail_result(
                reason=REASON_INTERNAL_ERROR,
                error=str(exc),
                evolution_id=self._safe_extract_id(evolution_record),
            )

    def rollback(
        self,
        reflection_id: str,
        actor: Optional[str] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """回滚到指定 reflection 的 before_snapshot。"""
        try:
            with self._lock:
                self._rollback_count += 1
                rid = _safe_str(reflection_id, "")
                if not rid:
                    return self._fail_result(
                        reason=REASON_INVALID_INPUT,
                        error="reflection_id required",
                    )
                record = self._evolution_store.get(rid)
                if not isinstance(record, dict):
                    return self._fail_result(
                        reason="rollback_not_found",
                        error=f"reflection not found: {rid}",
                        reflection_id=rid,
                    )
                if record.get("status") == RECORD_STATUS_ROLLED_BACK:
                    return self._fail_result(
                        reason="rollback_not_found",
                        error=f"reflection already rolled back: {rid}",
                        reflection_id=rid,
                    )
                before_snapshot = _safe_dict(record.get("before_snapshot"))
                before_fields = _safe_dict(before_snapshot.get("fields"))
                if not before_fields:
                    return self._fail_result(
                        reason="rollback_error",
                        error="before_snapshot empty or invalid",
                        reflection_id=rid,
                    )
                act = _safe_str(actor, self._default_actor) or self._default_actor
                # 创建新版本
                ts = _now_iso()
                old_version = self._version_store.get_current_version_id() or ""
                rollback_rid = self._new_reflection_id()
                before_raw = _safe_dict(before_snapshot.get("raw"))
                new_raw = dict(before_raw)
                # 把 before_fields 写回 self_model
                if isinstance(new_raw.get("self_model"), dict):
                    new_raw["self_model"] = _safe_deepcopy(before_fields)
                else:
                    for k, v in before_fields.items():
                        if k in DEFAULT_EVOLVABLE_FIELDS:
                            new_raw[k] = v
                try:
                    new_version = self._version_store.create_version(
                        snapshot=new_raw,
                        source_reflection_id=rollback_rid,
                    )
                except Exception as exc:
                    self._degraded_count += 1
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error=str(exc),
                        reflection_id=rid,
                    )
                if not new_version:
                    return self._fail_result(
                        reason=REASON_VERSION_ERROR,
                        error="create_version returned empty",
                        reflection_id=rid,
                    )
                # rollback record
                rollback_reason = _safe_str(reason, "") or "rollback"
                rollback_record = build_self_model_evolution_record(
                    reflection_id=rollback_rid,
                    evolution_id=_safe_str(record.get("evolution_id"), ""),
                    before_snapshot={
                        "fields": _safe_deepcopy(self._get_current_fields()),
                        "raw": _safe_deepcopy(self._get_current_raw()),
                    },
                    after_snapshot={
                        "fields": _safe_deepcopy(before_fields),
                        "raw": _safe_deepcopy(new_raw),
                    },
                    changes=[
                        {
                            "field": "rollback",
                            "before": None,
                            "after": None,
                            "rollback_of": rid,
                        }
                    ],
                    reason=rollback_reason,
                    evidence={"rollback_of": rid, "rollback_reason": rollback_reason},
                    confidence=_safe_float(record.get("confidence"), 0.0),
                    timestamp=ts,
                    old_version=old_version,
                    new_version=new_version,
                    actor=act,
                    status=RECORD_STATUS_APPLIED,
                    rollback_status=ROLLBACK_STATUS_ROLLED_BACK,
                    rollback_of=rid,
                    extra={"is_rollback": True, "rollback_of": rid},
                )
                saved = self._evolution_store.save(rollback_record)
                if not saved:
                    self._degraded_count += 1
                    return self._fail_result(
                        reason=REASON_RECORD_ERROR,
                        error="rollback record save failed",
                        reflection_id=rid,
                    )
                # 标记原 reflection 为 rolled_back
                audit_detail = {
                    "reflection_id": rid,
                    "rollback_reflection_id": rollback_rid,
                    "old_version": old_version,
                    "new_version": new_version,
                    "actor": act,
                    "timestamp": ts,
                    "reason": rollback_reason,
                }
                self._evolution_store.mark_rolled_back(
                    reflection_id=rid,
                    by_reflection_id=rollback_rid,
                    audit_detail=audit_detail,
                )
                self._rollback_index[rid] = rollback_rid
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
                    reflection_id=rollback_rid,
                    evolution_id=_safe_str(record.get("evolution_id"), ""),
                    old_version=old_version,
                    new_version=new_version,
                    changes=[
                        {"field": "rollback", "rollback_of": rid}
                    ],
                    timestamp=ts,
                    actor=act,
                    audit_recorded=audit_ok,
                    policy_result={},
                    reason="rolled_back",
                    rollback_of=rid,
                )
        except Exception as exc:
            self._degraded_count += 1
            self._last_error = str(exc)
            logger.warning("[SelfModelEvolutionEngine] rollback failed: %s", exc)
            return self._fail_result(
                reason=REASON_INTERNAL_ERROR,
                error=str(exc),
                reflection_id=reflection_id if isinstance(reflection_id, str) else "",
            )

    def get_reflection(self, reflection_id: str) -> Optional[Dict[str, Any]]:
        try:
            rid = _safe_str(reflection_id, "")
            if not rid:
                return None
            return self._evolution_store.get(rid)
        except Exception:
            return None

    def list_reflections(
        self,
        evolution_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        try:
            items: List[Dict[str, Any]] = []
            if evolution_id:
                items = self._evolution_store.list_by_evolution(evolution_id)
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
                    "reflect_count": int(self._reflect_count),
                    "rollback_count": int(self._rollback_count),
                    "degraded_count": int(self._degraded_count),
                    "denied_count": int(self._denied_count),
                    "reflected_evolution_count": len(self._reflected),
                    "reflection_record_count": self._evolution_store.get_stats().get("total_records", 0),
                    "version_count": self._version_store.count(),
                    "current_version": self._version_store.get_current_version_id(),
                    "last_error": self._last_error,
                }
        except Exception:
            return {
                "reflect_count": 0,
                "rollback_count": 0,
                "degraded_count": 0,
                "denied_count": 0,
                "reflected_evolution_count": 0,
                "reflection_record_count": 0,
                "version_count": 0,
                "current_version": "",
                "last_error": "stats_unavailable",
            }

    def reset(self) -> None:
        with self._lock:
            self._reflected.clear()
            self._rollback_index.clear()
            self._reflect_count = 0
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

    def _new_reflection_id(self) -> str:
        try:
            return f"smr_{uuid.uuid4().hex[:16]}"
        except Exception:
            try:
                return f"smr_{int(_now_ts() * 1000):x}"
            except Exception:
                return "smr_0000000000000000"

    def _is_valid_record(self, record: Any) -> bool:
        if record is None:
            return False
        if isinstance(record, dict):
            return True
        if hasattr(record, "evolution_id") or hasattr(record, "__dict__"):
            return True
        return False

    def _safe_extract_id(self, record: Any) -> str:
        try:
            return self._extract_evolution_id(record)
        except Exception:
            return ""

    def _extract_evolution_id(self, record: Any) -> str:
        try:
            if isinstance(record, dict):
                return _safe_str(record.get("evolution_id"), "")
            return _safe_str(getattr(record, "evolution_id", None), "")
        except Exception:
            return ""

    def _extract_evolution_success(self, record: Any) -> bool:
        try:
            if isinstance(record, dict):
                v = record.get("success", record.get("applied", record.get("status")))
                if isinstance(v, bool):
                    return v
                if isinstance(v, str):
                    return v in ("applied", "success", "ok", "true", "True")
                return _safe_bool(v, False)
            v = getattr(record, "success", None)
            if v is None:
                v = getattr(record, "applied", None)
            if v is None:
                v = getattr(record, "status", None)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v in ("applied", "success", "ok", "true", "True")
            return _safe_bool(v, False)
        except Exception:
            return False

    def _extract_confidence(self, record: Any) -> float:
        try:
            if isinstance(record, dict):
                return _safe_float(record.get("confidence"), 0.0)
            return _safe_float(getattr(record, "confidence", None), 0.0)
        except Exception:
            return 0.0

    def _extract_evidence(self, record: Any) -> Dict[str, Any]:
        try:
            if isinstance(record, dict):
                ev = record.get("evidence")
            else:
                ev = getattr(record, "evidence", None)
            return _safe_deepcopy(ev) if isinstance(ev, dict) else {}
        except Exception:
            return {}

    def _extract_reason(self, record: Any) -> str:
        try:
            if isinstance(record, dict):
                return _safe_str(record.get("reason"), "")
            return _safe_str(getattr(record, "reason", None), "")
        except Exception:
            return ""

    def _get_snapshot_safe(self) -> Dict[str, Any]:
        """
        读取当前 self_model snapshot。

        优先级:
          1. snapshot_provider (如果提供)
          2. SelfModelVersionStore 当前版本 (作为 source of truth)
          3. 空 dict
        """
        try:
            sp = _load_self_model_snapshot(self._snapshot_provider)
            if isinstance(sp, dict) and (sp.get("fields") or sp.get("raw")):
                return sp
        except Exception:
            pass
        try:
            current = self._version_store.get_current()
            if isinstance(current, dict):
                snap = current.get("snapshot")
                if isinstance(snap, dict):
                    fields: Dict[str, Any] = {}
                    sm = snap.get("self_model")
                    if isinstance(sm, dict):
                        for k, v in sm.items():
                            fields[str(k)] = v
                    else:
                        for k, v in snap.items():
                            if k in DEFAULT_EVOLVABLE_FIELDS or k in DEFAULT_IDENTITY_PROTECTED_FIELDS:
                                fields[str(k)] = v
                    return {
                        "fields": fields,
                        "raw": _safe_deepcopy(snap),
                    }
        except Exception:
            pass
        return {"fields": {}, "raw": {}}

    def _get_current_fields(self) -> Dict[str, Any]:
        try:
            v = self._version_store.get_current()
            if isinstance(v, dict):
                snap = v.get("snapshot") or {}
                if isinstance(snap, dict):
                    sm = snap.get("self_model")
                    if isinstance(sm, dict):
                        return {k: _safe_deepcopy(val) for k, val in sm.items()}
                    return {k: _safe_deepcopy(val) for k, val in snap.items()
                            if k in DEFAULT_EVOLVABLE_FIELDS}
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

    def _compute_after(
        self,
        before_fields: Dict[str, Any],
        changes: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        计算 after_fields。直接应用 changes 列表中的 after 值。
        """
        after = dict(before_fields)
        try:
            for ch in changes or []:
                if not isinstance(ch, dict):
                    continue
                field = _safe_str(ch.get("field"), "")
                if not field:
                    continue
                if field in DEFAULT_IDENTITY_PROTECTED_FIELDS:
                    continue
                if field not in DEFAULT_EVOLVABLE_FIELDS:
                    continue
                after[field] = _safe_deepcopy(ch.get("after"))
        except Exception:
            pass
        return after

    def _record_created_audit(
        self,
        rid: str,
        eid: str,
        old_version: str,
        new_version: str,
        changes: List[Dict[str, Any]],
        timestamp: str,
        actor: str,
        confidence: float,
    ) -> bool:
        try:
            detail = {
                "reflection_id": rid,
                "evolution_id": eid,
                "old_version": old_version,
                "new_version": new_version,
                "changes": _safe_list(changes),
                "timestamp": timestamp,
                "actor": actor,
                "confidence": float(confidence) if confidence is not None else 0.0,
                "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            }
            return _record_audit_safely(
                self._audit,
                action=AUDIT_ACTION_CREATED,
                detail=detail,
                result="success",
            )
        except Exception:
            return False

    def _record_failed(
        self,
        action: str,
        eid: str,
        reason: str,
        error: str,
        actor: str,
    ) -> None:
        try:
            detail = {
                "evolution_id": eid,
                "reason": reason,
                "error": error,
                "actor": actor,
                "timestamp": _now_iso(),
                "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
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
        evolution_id: str = "",
        reflection_id: str = "",
        old_version: str = "",
        new_version: str = "",
        policy_result: Optional[Dict[str, Any]] = None,
        rollback_of: str = "",
    ) -> Dict[str, Any]:
        return self._build_result(
            success=False,
            degraded=True,
            error=error,
            reflection_id=reflection_id,
            evolution_id=evolution_id,
            old_version=old_version,
            new_version=new_version,
            changes=[],
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
        reflection_id: str,
        evolution_id: str,
        old_version: str,
        new_version: str,
        changes: List[Dict[str, Any]],
        timestamp: str,
        actor: str,
        audit_recorded: bool,
        policy_result: Dict[str, Any],
        reason: str,
        rollback_of: str = "",
    ) -> Dict[str, Any]:
        return {
            "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "error": _safe_str(error, ""),
            "reflection_id": _safe_str(reflection_id, ""),
            "evolution_id": _safe_str(evolution_id, ""),
            "old_version": _safe_str(old_version, ""),
            "new_version": _safe_str(new_version, ""),
            "changes": _safe_list(changes),
            "timestamp": _safe_str(timestamp, "") or _now_iso(),
            "actor": _safe_str(actor, "") or self._default_actor,
            "audit_recorded": bool(audit_recorded),
            "policy_result": _safe_dict(policy_result),
            "reason": _safe_str(reason, REFLECT_REASON_NONE),
            "rollback_of": _safe_str(rollback_of, ""),
            "engine": {
                "name": SELF_MODEL_EVOLUTION_NAME,
                "version": SELF_MODEL_EVOLUTION_VERSION,
                "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            },
        }


# ============================================================
# Factory + safe wrapper
# ============================================================


def create_self_model_evolution_engine(
    snapshot_provider: Any = None,
    evolution_store: Any = None,
    version_store: Any = None,
    policy: Any = None,
    audit: Any = None,
    initial_snapshot: Optional[Dict[str, Any]] = None,
    default_actor: str = ACTOR_DEFAULT,
) -> SelfModelEvolutionEngine:
    return SelfModelEvolutionEngine(
        snapshot_provider=snapshot_provider,
        evolution_store=evolution_store,
        version_store=version_store,
        policy=policy,
        audit=audit,
        initial_snapshot=initial_snapshot,
        default_actor=default_actor,
    )


def safe_reflect_evolution(
    engine: Any,
    evolution_record: Any,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    if engine is None or not hasattr(engine, "reflect_evolution"):
        return {
            "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "error": "engine unavailable",
            "reflection_id": "",
            "evolution_id": "",
            "old_version": "",
            "new_version": "",
            "changes": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, ACTOR_DEFAULT) or ACTOR_DEFAULT,
            "audit_recorded": False,
            "policy_result": {},
            "reason": REASON_DEGRADED,
            "rollback_of": "",
            "engine": {
                "name": SELF_MODEL_EVOLUTION_NAME,
                "version": SELF_MODEL_EVOLUTION_VERSION,
                "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            },
        }
    try:
        return engine.reflect_evolution(evolution_record, actor=actor)
    except Exception as exc:
        return {
            "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "error": str(exc),
            "reflection_id": "",
            "evolution_id": "",
            "old_version": "",
            "new_version": "",
            "changes": [],
            "timestamp": _now_iso(),
            "actor": _safe_str(actor, ACTOR_DEFAULT) or ACTOR_DEFAULT,
            "audit_recorded": False,
            "policy_result": {},
            "reason": REASON_INTERNAL_ERROR,
            "rollback_of": "",
            "engine": {
                "name": SELF_MODEL_EVOLUTION_NAME,
                "version": SELF_MODEL_EVOLUTION_VERSION,
                "schema_version": SELF_MODEL_EVOLUTION_SCHEMA_VERSION,
            },
        }
