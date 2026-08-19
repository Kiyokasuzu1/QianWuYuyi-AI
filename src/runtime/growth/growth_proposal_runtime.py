# -*- coding: utf-8 -*-
"""
src/runtime/growth/growth_proposal_runtime.py

Phase C.8.0 Growth Proposal Lifecycle Runtime Integration —— GrowthProposalRuntime

=========================
目标
=========================
建立 Runtime 到 GrowthProposal 的安全生命周期入口。

允许:
  Event
   ↓
  Evaluation
   ↓
  Proposal(保存 + Audit)

禁止:
  Proposal
   ↓
  Personality
   ↓
  SelfModel

禁止:
  - 自动 accept / reject / apply
  - 任何人格变化
  - 任何 SelfModel 变化
  - 修改 src/personality/** / src/runtime/self_model/**

=========================
硬约束
=========================
禁止调用:
  - ProposalManager.accept_proposal()
  - ProposalManager.reject_proposal()
  - ProposalManager.apply_proposal()
  - PersonalityResolver.resolve()
  - PersonalityAdapter.apply_proposal()
  - TraitStateUpdater.apply()
  - SelfModelStore.update()

允许调用(单一写入口):
  - ProposalManager.create_proposal()
  - AuditStorage.save() / record_audit_log()

=========================
异常处理
=========================
process_growth_signal() 永不抛异常。
任何异常 → 返回:
  {
    "success": False,
    "degraded": True,
    "error": "..."
  }
Runtime cycle 不崩溃。
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本 + 常量
# ============================================================

GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION = "1.0"
GROWTH_PROPOSAL_RUNTIME_NAME = "growth_proposal_runtime"
GROWTH_PROPOSAL_RUNTIME_VERSION = "1.0.0"

# 默认 confidence 门槛(冻结)
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

# Audit action 名称
AUDIT_ACTION_PROPOSAL_CREATED = "runtime_growth_proposal_created"
AUDIT_COMPONENT = "runtime_growth"
AUDIT_ACTOR = "growth_proposal_runtime"

# 写状态机(proposal.status 合法值)
PROPOSAL_STATUS_PENDING = "pending"
PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE = "rejected_low_confidence"
PROPOSAL_STATUS_REJECTED_NO_EVIDENCE = "rejected_no_evidence"
PROPOSAL_STATUS_DEDUPED = "deduped"
PROPOSAL_STATUS_CREATED = "created"
PROPOSAL_STATUS_ERROR = "error"

ALL_PROPOSAL_STATUSES: List[str] = [
    PROPOSAL_STATUS_PENDING,
    PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
    PROPOSAL_STATUS_REJECTED_NO_EVIDENCE,
    PROPOSAL_STATUS_DEDUPED,
    PROPOSAL_STATUS_CREATED,
    PROPOSAL_STATUS_ERROR,
]

# 禁止的 ProposalManager 方法(防误用)
FORBIDDEN_PROPOSAL_MANAGER_METHODS = frozenset({
    "accept_proposal",
    "reject_proposal",
    "apply_proposal",
})


# ============================================================
# 时间工具
# ============================================================


def _now_iso() -> str:
    """ISO 8601 UTC timestamp(fail-soft)"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            return datetime.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return "1970-01-01T00:00:00Z"


def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return default
    except Exception:  # noqa: BLE001
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        return bool(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:  # noqa: BLE001
        return []


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:  # noqa: BLE001
        return default


def _safe_deepcopy(v: Any) -> Any:
    """深拷贝(任何异常都返回原值或空容器)。"""
    try:
        return copy.deepcopy(v)
    except Exception:  # noqa: BLE001
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# Fingerprint 计算
# ============================================================


def _compute_event_fingerprint(signal: Dict[str, Any]) -> str:
    """根据 signal 关键字段计算 event fingerprint(用于重复检测)。

    基于:
      - source_event_id
      - event_type
      - canonical_topic
      - proposed_changes 路径序列
    """
    try:
        parts: List[str] = []
        # source event id
        sid = _safe_get(signal, "source_event_id", "")
        if not sid:
            sid = _safe_get(signal, "event_id", "")
        if not sid:
            sid = _safe_get(signal, "id", "")
        parts.append(str(sid or ""))
        # event type
        et = _safe_get(signal, "event_type", "")
        parts.append(str(et or ""))
        # canonical topic
        topic = _safe_get(signal, "canonical_topic", "")
        parts.append(str(topic or ""))
        # changes path
        changes = _safe_get(signal, "proposed_changes", [])
        if isinstance(changes, list):
            paths: List[str] = []
            for ch in changes:
                if isinstance(ch, dict):
                    p = _safe_get(ch, "path", "")
                    if p:
                        paths.append(str(p))
            paths.sort()
            for p in paths:
                parts.append(p)
        raw = "|".join(parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return ""


# ============================================================
# Audit 工具
# ============================================================


def _record_audit_safely(
    audit: Any,
    proposal_id: str,
    cycle_id: str,
    confidence: float,
    fingerprint: str,
    status: str,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """安全地记录 audit(任何异常都吸收)。"""
    try:
        if audit is None:
            return False
        # 优先调用 record_audit_log 风格(扁平 dict)
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=AUDIT_ACTION_PROPOSAL_CREATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_PROPOSAL_CREATED,
                    detail={
                        "proposal_id": str(proposal_id or ""),
                        "cycle_id": str(cycle_id or ""),
                        "timestamp": _now_iso(),
                        "confidence": float(confidence or 0.0),
                        "fingerprint": str(fingerprint or ""),
                        "status": str(status or ""),
                        "schema_version": GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
                        "actor": AUDIT_ACTOR,
                    },
                    result="success",
                )
                return True
            except Exception:  # noqa: BLE001
                pass
        # fallback:调用 save_audit_record / record_audit_log
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=AUDIT_ACTION_PROPOSAL_CREATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_PROPOSAL_CREATED,
                    detail={
                        "proposal_id": str(proposal_id or ""),
                        "cycle_id": str(cycle_id or ""),
                        "timestamp": _now_iso(),
                        "confidence": float(confidence or 0.0),
                        "fingerprint": str(fingerprint or ""),
                        "status": str(status or ""),
                        "schema_version": GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
                        "actor": AUDIT_ACTOR,
                    },
                    result="success",
                )
                audit.save_audit_record(rec)
                return True
            except Exception:  # noqa: BLE001
                pass
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=AUDIT_ACTION_PROPOSAL_CREATED,
                    source=AUDIT_COMPONENT,
                    action=AUDIT_ACTION_PROPOSAL_CREATED,
                    detail={
                        "proposal_id": str(proposal_id or ""),
                        "cycle_id": str(cycle_id or ""),
                        "timestamp": _now_iso(),
                        "confidence": float(confidence or 0.0),
                        "fingerprint": str(fingerprint or ""),
                        "status": str(status or ""),
                    },
                    result="success",
                )
                return True
            except Exception:  # noqa: BLE001
                pass
        return False
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# GrowthProposalRuntime
# ============================================================


class GrowthProposalRuntime:
    """
    Growth Proposal Runtime Lifecycle Integration (Phase C.8.0 / v1.0)

    安全生命周期入口:
      - 读 signal
      - 调 GrowthEvaluator.evaluate() 评估
      - 调 ProposalManager.create_proposal() 创建 proposal(单一写入口)
      - 记录 audit

    严格禁止:
      - accept_proposal / reject_proposal / apply_proposal
      - 任何 PersonalityAdapter / SelfModel 调用
      - 任何业务状态修改

    Schema 契约(冻结于 v1.0):
      {
        "schema_version": "1.0",
        "success": bool,
        "degraded": bool,
        "status": str,                  # pending|rejected_low_confidence|...
        "proposal_id": Optional[str],    # created 时存在
        "proposal": Optional[dict],      # 完整 proposal dict
        "confidence": float,
        "fingerprint": str,
        "audit_recorded": bool,
        "error": Optional[str],
        "timestamp": str,
        "runtime": {
          "name": "growth_proposal_runtime",
          "version": "1.0.0",
          "schema_version": "1.0",
        }
      }
    """

    SCHEMA_VERSION = GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION
    NAME = GROWTH_PROPOSAL_RUNTIME_NAME
    VERSION = GROWTH_PROPOSAL_RUNTIME_VERSION

    def __init__(
        self,
        growth_evaluator: Any = None,
        proposal_manager: Any = None,
        audit: Any = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        """
        Args:
            growth_evaluator: GrowthEvaluator 实例(可选;None 时惰性自建)
            proposal_manager: ProposalManager 实例(可选;None 时惰性自建)
            audit: AuditStorage / 审计写入对象(可选;None 时不写 audit 但不报错)
            confidence_threshold: confidence 门槛(< 则拒绝;默认 0.7)
        """
        self._evaluator = growth_evaluator
        self._proposal_manager = proposal_manager
        self._audit = audit
        # 允许用户显式传 0(完全禁用);0.0 也合法(任何 confidence > 0 通过)
        try:
            self._confidence_threshold = float(confidence_threshold)
        except (TypeError, ValueError):
            self._confidence_threshold = DEFAULT_CONFIDENCE_THRESHOLD
        # 0.0~1.0 范围限制
        if self._confidence_threshold < 0.0:
            self._confidence_threshold = 0.0
        if self._confidence_threshold > 1.0:
            self._confidence_threshold = 1.0

        self._lock = threading.RLock()
        self._attached: bool = False
        # 内部统计
        self._process_count: int = 0
        self._created_count: int = 0
        self._deduped_count: int = 0
        self._rejected_count: int = 0
        self._error_count: int = 0
        self._audit_recorded_count: int = 0
        self._last_error: Optional[str] = None
        self._last_process_ts: Optional[str] = None
        # 内部去重表(本实例内)
        self._seen_fingerprints: Dict[str, str] = {}

    # --------------------------------------------------------
    # 依赖注入
    # --------------------------------------------------------

    def set_evaluator(self, evaluator: Any) -> None:
        with self._lock:
            self._evaluator = evaluator

    def set_proposal_manager(self, manager: Any) -> None:
        with self._lock:
            self._proposal_manager = manager

    def set_audit(self, audit: Any) -> None:
        with self._lock:
            self._audit = audit

    def attach(
        self,
        evaluator: Any = None,
        proposal_manager: Any = None,
        audit: Any = None,
    ) -> bool:
        """附加依赖(便于链式注入)。"""
        with self._lock:
            if evaluator is not None:
                self._evaluator = evaluator
            if proposal_manager is not None:
                self._proposal_manager = proposal_manager
            if audit is not None:
                self._audit = audit
            self._attached = True
        return True

    def detach(self) -> bool:
        with self._lock:
            self._attached = False
        return True

    def is_attached(self) -> bool:
        with self._lock:
            return self._attached

    # --------------------------------------------------------
    # 主入口:process_growth_signal
    # --------------------------------------------------------

    def process_growth_signal(
        self,
        signal: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        处理一个 growth signal(永不抛异常)。

        Args:
            signal: dict,包含
              {
                "event": dict,             # 原始事件
                "evidence": list,          # 证据
                "source_ids": list,        # 证据 ID
                "importance_score": float, # 重要性
                "cycle_id": str,           # 来源 cycle id
                "id"/"event_id": str,      # source event id
                "event_type": str,         # 类型(可选)
                "canonical_topic": str,    # 主题(可选)
                "proposed_changes": list,  # 候选 changes(可选)
                "confidence": float,       # 已评估的 confidence(可选,缺省时 evaluator 重新评估)
                "history": list,           # 历史同类事件(可选)
              }

        Returns:
            dict(见 Schema 契约)
        """
        try:
            with self._lock:
                self._process_count += 1
                self._last_process_ts = _now_iso()

                # 1) 解析 signal
                if not isinstance(signal, dict):
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=0.0,
                        fingerprint="",
                        audit_recorded=False,
                        error="signal_invalid_not_dict",
                    )

                cycle_id = _safe_str(_safe_get(signal, "cycle_id", ""), "")
                source_event = _safe_get(signal, "event", None)
                if not isinstance(source_event, dict):
                    # 退化:把 signal 本身当作 event
                    source_event = {
                        "id": _safe_get(signal, "id", _safe_get(signal, "event_id", "")),
                        "event": _safe_get(signal, "event", ""),
                        "event_type": _safe_get(signal, "event_type", ""),
                        "canonical_topic": _safe_get(signal, "canonical_topic", ""),
                        "evidence": _safe_get(signal, "evidence", []),
                        "importance": _safe_get(signal, "importance_score", 0.0),
                    }

                # 2) 调 evaluator(若 evaluator 存在)
                confidence = _safe_float(_safe_get(signal, "confidence", None), -1.0)
                evaluator_meta: Dict[str, Any] = {}
                evaluator = self._evaluator
                if confidence < 0.0 and evaluator is not None:
                    try:
                        if hasattr(evaluator, "evaluate") and callable(getattr(evaluator, "evaluate")):
                            history = _safe_list(_safe_get(signal, "history", []))
                            try:
                                evaluated = evaluator.evaluate(source_event, history=history)
                            except TypeError:
                                # 不支持 history kwarg
                                evaluated = evaluator.evaluate(source_event)
                            if isinstance(evaluated, dict):
                                confidence = _safe_float(
                                    _safe_get(evaluated, "confidence", 0.0), 0.0
                                )
                                evaluator_meta = dict(evaluated)
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = repr(exc)
                        logger.debug(f"[phase_c8_0] evaluator 异常(已隔离): {exc}")

                if confidence < 0.0:
                    confidence = 0.0

                # 3) 证据
                evidence = _safe_list(_safe_get(signal, "evidence", []))
                source_ids = _safe_list(_safe_get(signal, "source_ids", []))
                # 兜底:从 evidence 提取 id
                if not source_ids and evidence:
                    for ev in evidence:
                        if isinstance(ev, dict):
                            eid = _safe_get(ev, "id", None)
                            if eid:
                                source_ids.append(str(eid))

                # 4) 计算 fingerprint
                sig_for_fp = dict(signal)
                sig_for_fp["evidence"] = evidence
                sig_for_fp["proposed_changes"] = _safe_list(
                    _safe_get(signal, "proposed_changes", [])
                )
                if source_event:
                    sig_for_fp["source_event_id"] = _safe_get(source_event, "id", "")
                fingerprint = _compute_event_fingerprint(sig_for_fp)

                # 5) 重复检测(本实例内)
                if fingerprint and fingerprint in self._seen_fingerprints:
                    existing_id = self._seen_fingerprints[fingerprint]
                    self._deduped_count += 1
                    # audit(失败也允许)
                    audit_ok = _record_audit_safely(
                        self._audit,
                        proposal_id=existing_id,
                        cycle_id=cycle_id,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        status=PROPOSAL_STATUS_DEDUPED,
                    )
                    if audit_ok:
                        with self._lock:
                            self._audit_recorded_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_DEDUPED,
                        proposal_id=existing_id,
                        proposal={"id": existing_id, "status": "pending"},
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=audit_ok,
                        error=None,
                        existing=True,
                    )

                # 6) 置信度门槛(硬阈值)
                if confidence < self._confidence_threshold:
                    self._rejected_count += 1
                    # audit(失败也允许)
                    audit_ok = _record_audit_safely(
                        self._audit,
                        proposal_id="",
                        cycle_id=cycle_id,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        status=PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
                    )
                    if audit_ok:
                        with self._lock:
                            self._audit_recorded_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=audit_ok,
                        error=None,
                        reason=f"confidence {confidence:.3f} < threshold {self._confidence_threshold:.3f}",
                    )

                # 7) 证据检查(>=1 即可)
                if not source_ids:
                    self._rejected_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_REJECTED_NO_EVIDENCE,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error=None,
                        reason="no evidence_ids",
                    )

                # 8) 准备 proposed_changes
                proposed_changes = _safe_list(_safe_get(signal, "proposed_changes", []))
                if not proposed_changes:
                    # 兜底:从 evaluator_meta 提取
                    candidates = _safe_list(_safe_get(evaluator_meta, "target_candidates", []))
                    for c in candidates:
                        if isinstance(c, str):
                            proposed_changes.append({
                                "path": c,
                                "before": None,
                                "after": None,
                                "reason": "inferred_from_evaluator",
                            })
                if not proposed_changes:
                    # 兜底:用 canonical_topic 作为占位 change
                    topic = _safe_str(_safe_get(source_event, "canonical_topic", ""), "")
                    if not topic:
                        topic = _safe_str(_safe_get(signal, "canonical_topic", ""), "growth_signal")
                    proposed_changes.append({
                        "path": f"growth.{topic}",
                        "before": None,
                        "after": None,
                        "reason": "placeholder_proposal",
                    })

                # 9) 调 ProposalManager.create_proposal()(唯一写入口)
                pm = self._proposal_manager
                if pm is None:
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error="proposal_manager_not_attached",
                    )

                if not hasattr(pm, "create_proposal") or not callable(getattr(pm, "create_proposal")):
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error="proposal_manager_invalid",
                    )

                # 构造 evaluator_meta(支持 schema 元数据)
                meta = {
                    "phase": "c8_0",
                    "actor": AUDIT_ACTOR,
                    "schema_version": GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
                    "source": "runtime_growth",
                    "timestamp": _now_iso(),
                }
                # 合并 evaluator 输出(可选)
                for k in (
                    "growth_allowed",
                    "growth_level",
                    "growth_domain",
                    "growth_signal",
                    "stability",
                    "consistency",
                    "impact",
                    "source_reliability",
                    "evidence_quality",
                ):
                    v = _safe_get(evaluator_meta, k, None)
                    if v is not None:
                        meta[k] = v

                # 构造 ChangeItem 对象(若 contracts 可用)
                try:
                    from src.contracts.growth_schema import ChangeItem
                    change_items: List[Any] = []
                    for ch in proposed_changes:
                        if isinstance(ch, dict):
                            try:
                                change_items.append(ChangeItem(
                                    path=str(_safe_get(ch, "path", "") or ""),
                                    before=_safe_get(ch, "before", None),
                                    after=_safe_get(ch, "after", None),
                                    reason=_safe_str(_safe_get(ch, "reason", ""), "") or None,
                                ))
                            except Exception:  # noqa: BLE001
                                continue
                except Exception:  # noqa: BLE001
                    # fallback:用 dict 形式
                    change_items = [ch for ch in proposed_changes if isinstance(ch, dict)]

                # 调 create_proposal(只调这一个写方法)
                try:
                    create_result = pm.create_proposal(
                        source_event=source_event,
                        proposed_changes=change_items,
                        confidence=float(confidence),
                        evidence_ids=list(source_ids),
                        evaluator_meta=meta,
                    )
                except Exception as exc:  # noqa: BLE001
                    self._error_count += 1
                    self._last_error = repr(exc)
                    logger.debug(f"[phase_c8_0] create_proposal 异常(已隔离): {exc}")
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error=f"create_proposal_failed: {repr(exc)}",
                    )

                if not isinstance(create_result, dict):
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error="create_proposal_returned_non_dict",
                    )

                inner_status = _safe_str(_safe_get(create_result, "status", ""), "")
                proposal_obj = _safe_get(create_result, "proposal", None)
                existing_id = _safe_get(create_result, "existing_id", None)
                inner_reason = _safe_get(create_result, "reason", None)

                # 10) 处理 create_proposal 内部返回的多种状态
                # 只透传:created / deduped / rejected_low_confidence / rejected_no_evidence / store_failed
                if inner_status == "created":
                    # 提取 proposal_id
                    proposal_id = self._extract_proposal_id(proposal_obj)
                    # 记 fingerprint
                    if fingerprint and proposal_id:
                        with self._lock:
                            self._seen_fingerprints[fingerprint] = str(proposal_id)
                    self._created_count += 1
                    # 写 audit
                    audit_ok = _record_audit_safely(
                        self._audit,
                        proposal_id=str(proposal_id or ""),
                        cycle_id=cycle_id,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        status=PROPOSAL_STATUS_CREATED,
                    )
                    if audit_ok:
                        with self._lock:
                            self._audit_recorded_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_CREATED,
                        proposal_id=str(proposal_id) if proposal_id else None,
                        proposal=self._proposal_to_dict(proposal_obj),
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=audit_ok,
                        error=None,
                    )

                if inner_status == "deduped":
                    self._deduped_count += 1
                    if fingerprint and existing_id:
                        with self._lock:
                            self._seen_fingerprints[fingerprint] = str(existing_id)
                    audit_ok = _record_audit_safely(
                        self._audit,
                        proposal_id=str(existing_id or ""),
                        cycle_id=cycle_id,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        status=PROPOSAL_STATUS_DEDUPED,
                    )
                    if audit_ok:
                        with self._lock:
                            self._audit_recorded_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_DEDUPED,
                        proposal_id=str(existing_id) if existing_id else None,
                        proposal={"id": existing_id, "status": "pending"} if existing_id else None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=audit_ok,
                        error=None,
                        existing=True,
                    )

                if inner_status == "rejected_low_confidence":
                    self._rejected_count += 1
                    audit_ok = _record_audit_safely(
                        self._audit,
                        proposal_id="",
                        cycle_id=cycle_id,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        status=PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
                    )
                    if audit_ok:
                        with self._lock:
                            self._audit_recorded_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=audit_ok,
                        error=None,
                        reason=_safe_str(inner_reason, "rejected_low_confidence"),
                    )

                if inner_status == "rejected_no_evidence":
                    self._rejected_count += 1
                    return self._build_report(
                        success=True,
                        degraded=False,
                        status=PROPOSAL_STATUS_REJECTED_NO_EVIDENCE,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error=None,
                        reason=_safe_str(inner_reason, "rejected_no_evidence"),
                    )

                if inner_status == "store_failed":
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=self._extract_proposal_id(proposal_obj),
                        proposal=self._proposal_to_dict(proposal_obj),
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error=_safe_str(inner_reason, "store_failed"),
                    )

                if inner_status == "adapter_failed":
                    self._error_count += 1
                    return self._build_report(
                        success=False,
                        degraded=True,
                        status=PROPOSAL_STATUS_ERROR,
                        proposal_id=None,
                        proposal=None,
                        confidence=confidence,
                        fingerprint=fingerprint,
                        audit_recorded=False,
                        error=_safe_str(inner_reason, "adapter_failed"),
                    )

                # 未知状态:降级返回
                self._error_count += 1
                return self._build_report(
                    success=False,
                    degraded=True,
                    status=PROPOSAL_STATUS_ERROR,
                    proposal_id=None,
                    proposal=None,
                    confidence=confidence,
                    fingerprint=fingerprint,
                    audit_recorded=False,
                    error=f"unknown_create_status: {inner_status}",
                )

        except Exception as exc:  # noqa: BLE001
            # 兜底:任何未捕获异常
            with self._lock:
                self._last_error = repr(exc)
                self._error_count += 1
            logger.debug(f"[phase_c8_0] process_growth_signal 异常(已隔离): {exc}")
            return self._build_report(
                success=False,
                degraded=True,
                status=PROPOSAL_STATUS_ERROR,
                proposal_id=None,
                proposal=None,
                confidence=0.0,
                fingerprint="",
                audit_recorded=False,
                error=repr(exc),
            )

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------

    def _extract_proposal_id(self, proposal: Any) -> Optional[str]:
        try:
            if isinstance(proposal, dict):
                pid = _safe_get(proposal, "id", None) or _safe_get(proposal, "proposal_id", None)
                if pid:
                    return str(pid)
            else:
                pid = getattr(proposal, "id", None) or getattr(proposal, "proposal_id", None)
                if pid:
                    return str(pid)
        except Exception:  # noqa: BLE001
            pass
        return None

    def _proposal_to_dict(self, proposal: Any) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(proposal, dict):
                return _safe_deepcopy(proposal)
            if hasattr(proposal, "to_dict") and callable(getattr(proposal, "to_dict")):
                try:
                    return _safe_deepcopy(proposal.to_dict())
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        return None

    def _build_report(
        self,
        success: bool,
        degraded: bool,
        status: str,
        proposal_id: Optional[str],
        proposal: Optional[Dict[str, Any]],
        confidence: float,
        fingerprint: str,
        audit_recorded: bool,
        error: Optional[str],
        reason: Optional[str] = None,
        existing: bool = False,
    ) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "success": bool(success),
            "degraded": bool(degraded),
            "status": str(status or PROPOSAL_STATUS_ERROR),
            "proposal_id": proposal_id,
            "proposal": proposal,
            "confidence": float(confidence or 0.0),
            "fingerprint": str(fingerprint or ""),
            "audit_recorded": bool(audit_recorded),
            "error": error,
            "reason": reason,
            "existing": bool(existing),
            "timestamp": _now_iso(),
            "runtime": {
                "name": self.NAME,
                "version": self.VERSION,
                "schema_version": self.SCHEMA_VERSION,
            },
        }

    # --------------------------------------------------------
    # 状态查询(只读)
    # --------------------------------------------------------

    @property
    def process_count(self) -> int:
        with self._lock:
            return int(self._process_count)

    @property
    def created_count(self) -> int:
        with self._lock:
            return int(self._created_count)

    @property
    def deduped_count(self) -> int:
        with self._lock:
            return int(self._deduped_count)

    @property
    def rejected_count(self) -> int:
        with self._lock:
            return int(self._rejected_count)

    @property
    def error_count(self) -> int:
        with self._lock:
            return int(self._error_count)

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "process_count": int(self._process_count),
                "created_count": int(self._created_count),
                "deduped_count": int(self._deduped_count),
                "rejected_count": int(self._rejected_count),
                "error_count": int(self._error_count),
                "audit_recorded_count": int(self._audit_recorded_count),
                "last_error": self._last_error,
                "last_process_ts": self._last_process_ts,
                "confidence_threshold": float(self._confidence_threshold),
                "has_evaluator": self._evaluator is not None,
                "has_proposal_manager": self._proposal_manager is not None,
                "has_audit": self._audit is not None,
            }

    def health_check(self) -> Dict[str, Any]:
        """只读健康检查。"""
        with self._lock:
            healthy = (
                self._proposal_manager is not None
                and hasattr(self._proposal_manager, "create_proposal")
                and self._last_error is None
            )
            return {
                "healthy": bool(healthy),
                "degraded": not bool(healthy),
                "attached": self._attached,
                "has_evaluator": self._evaluator is not None,
                "has_proposal_manager": self._proposal_manager is not None,
                "has_audit": self._audit is not None,
                "confidence_threshold": float(self._confidence_threshold),
                "error_count": int(self._error_count),
                "last_error": self._last_error,
            }


# ============================================================
# 工厂 / 辅助 API
# ============================================================


def create_growth_proposal_runtime(
    growth_evaluator: Any = None,
    proposal_manager: Any = None,
    audit: Any = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> GrowthProposalRuntime:
    """工厂函数:创建一个 GrowthProposalRuntime。"""
    return GrowthProposalRuntime(
        growth_evaluator=growth_evaluator,
        proposal_manager=proposal_manager,
        audit=audit,
        confidence_threshold=confidence_threshold,
    )


def safe_process_growth_signal(
    runtime: Optional[GrowthProposalRuntime],
    signal: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """全局安全 process_growth_signal(任何异常都吸收)"""
    if runtime is None:
        runtime = create_growth_proposal_runtime()
    try:
        return runtime.process_growth_signal(signal) or {}
    except Exception:  # noqa: BLE001
        return {
            "schema_version": GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
            "success": False,
            "degraded": True,
            "status": PROPOSAL_STATUS_ERROR,
            "proposal_id": None,
            "proposal": None,
            "confidence": 0.0,
            "fingerprint": "",
            "audit_recorded": False,
            "error": "safe_process_exception",
            "timestamp": _now_iso(),
            "runtime": {
                "name": GROWTH_PROPOSAL_RUNTIME_NAME,
                "version": GROWTH_PROPOSAL_RUNTIME_VERSION,
                "schema_version": GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION,
            },
        }


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "GROWTH_PROPOSAL_RUNTIME_SCHEMA_VERSION",
    "GROWTH_PROPOSAL_RUNTIME_NAME",
    "GROWTH_PROPOSAL_RUNTIME_VERSION",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "AUDIT_ACTION_PROPOSAL_CREATED",
    "AUDIT_COMPONENT",
    "AUDIT_ACTOR",
    "PROPOSAL_STATUS_PENDING",
    "PROPOSAL_STATUS_REJECTED_LOW_CONFIDENCE",
    "PROPOSAL_STATUS_REJECTED_NO_EVIDENCE",
    "PROPOSAL_STATUS_DEDUPED",
    "PROPOSAL_STATUS_CREATED",
    "PROPOSAL_STATUS_ERROR",
    "ALL_PROPOSAL_STATUSES",
    "FORBIDDEN_PROPOSAL_MANAGER_METHODS",
    "GrowthProposalRuntime",
    "create_growth_proposal_runtime",
    "safe_process_growth_signal",
    "_compute_event_fingerprint",
    "_record_audit_safely",
]
