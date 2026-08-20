# -*- coding: utf-8 -*-
"""
P2.3-B.5 — GrowthMutationAdapter（成长 mutation 治理迁移层）

定位（B.5 任务书 Phase 2）：
    旧成长系统产生的 mutation intent（GrowthEvent / GrowthProposal）
    先转换、后治理。本 Adapter 是转换层 + 裁决执行约定层：

      GrowthEvent / GrowthProposal → MutationRequest（9 字段契约）
                                  → MutationGateway（五道检查）
                                  → MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）

裁决执行约定（B.5 Phase 3）：
    - ACCEPT       才允许进入现有 Apply Adapter（apply_route 回调，下游注入
                    GrowthEngine.apply_proposal 等执行件）；未注入 → 不自动 apply
    - REJECT       不改变状态（该变更被丢弃，仅审计留痕）
    - NEED_REVIEW  保存 proposal 等待审核（由 pipeline 层持久化，本 Adapter 不持有写句柄）
    - DEFER        进入延迟队列（本 Adapter 的 deferred 槽位，pipeline 层消费）

Growth Apply Boundary（registry §4）：
    - 成长域状态执行件 = GrowthEngine.apply_proposal（保留，ACCEPT 后经 apply_route 进入）
    - 人格域状态执行件 = PersonalityAdapter / TraitStateUpdater（保留，本 Adapter 不调用）
    - 本 Adapter 禁止直接 update trait / save self_model / apply evolution /
      写 GrowthState——只产生 MutationRequest 与裁决执行约定。

审计接线（B.5 Phase 5）：
    每次 route 产生 request_id / mutation_id / decision / target_path /
    evidence_reference / apply_result；拒绝与失败同样记录（默认挂 InMemoryAuditWriter）。

硬边界（禁止）：
    - Adapter 不直接修改任何领域状态；不持有任何 store 写句柄。
    - 默认关闭（growth_mutation_gateway_enabled = False）：旧生产路径字节不变；
      开启后才接管 GrowthPipeline.incremental_update 直写。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.contracts import growth_schema
from src.governance.audit_writer import AuditWriter, InMemoryAuditWriter
from src.governance.mutation_contract import (
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
)
from src.governance.mutation_gateway import MutationGateway

logger = logging.getLogger(__name__)

# ============================================================
# 迁移开关（默认关闭 = 旧行为；测试 / 灰度显式开启）
# ============================================================
_growth_mutation_gateway_enabled: bool = False

# B.5 范围：growth 主域（Growth 不得直接向 personality/self_model 发请求）
ALLOWED_TARGET_DOMAINS: frozenset = frozenset({"growth"})

# Phase 4：所有新产生 proposal 必须携带的治理链接键
GOVERNANCE_LINKAGE_KEYS: tuple = ("mutation_id", "request_id", "trace_id", "evidence")

# Phase 4：legacy（Track B，proposal/proposal.py schema）→ canonical 状态映射
# 只读转换，不改变任何存储的状态语义（registry §3.3）
LEGACY_STATUS_TO_CANONICAL: Dict[str, str] = {
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "applied": "applied",
    "cancelled": "rejected",
}


def is_growth_mutation_gateway_enabled() -> bool:
    """迁移开关状态（默认 False → 旧路径）。"""
    return _growth_mutation_gateway_enabled


def set_growth_mutation_gateway_enabled(enabled: bool) -> None:
    """切换迁移开关（True → GrowthPipeline 直写改经 Gateway）。"""
    global _growth_mutation_gateway_enabled
    _growth_mutation_gateway_enabled = bool(enabled)


def _gen_linkage_ids() -> Dict[str, str]:
    mutation_id = f"mut_{uuid.uuid4().hex[:12]}"
    return {
        "mutation_id": mutation_id,
        "request_id": f"req_{uuid.uuid4().hex[:12]}",
        "trace_id": f"trace_{uuid.uuid4().hex[:12]}",
    }


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_metric_name(path: Any) -> str:
    """剥离路径前缀得到成长指标名：growth.metrics.trust / personality.trust → trust。"""
    text = str(path or "")
    for prefix in ("growth.metrics.", "growth.", "personality.", "trait."):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text or "growth"


# ============================================================
# Phase 4：Proposal 统一转换（不删除旧 API，只增加转换层）
# ============================================================
def attach_governance_linkage(
    proposal: Any,
    *,
    mutation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    evidence: Optional[List[Any]] = None,
    decision: Optional[str] = None,
) -> Any:
    """把治理链接键写入 proposal 的 evaluator_meta["_governance"]。

    兼容 canonical GrowthProposal dataclass 与 dict 输入；不删除任何旧字段。
    返回原 proposal（dataclass 原地更新 evaluator_meta；dict 原地更新键）。
    """
    em = dict(getattr(proposal, "evaluator_meta", None) or {})
    em["_governance"] = {
        "mutation_id": mutation_id,
        "request_id": request_id,
        "trace_id": trace_id,
        "decision": decision,
        "evidence": list(evidence or []),
    }
    if isinstance(proposal, dict):
        proposal["evaluator_meta"] = em
    else:
        try:
            proposal.evaluator_meta = em
        except Exception as exc:  # noqa: BLE001 只读输入降级
            logger.debug("[attach_governance_linkage] 输入不可写，已跳过: %s", exc)
    return proposal


def to_mutation_proposal(
    legacy: Any,
    *,
    mutation_id: Optional[str] = None,
    request_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    evidence: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """Legacy proposal（Track B schema）→ canonical dict + 治理链接键。

    输入：src.growth.proposal.proposal.GrowthProposal（deprecated schema）的
    dataclass 或 dict。输出 canonical GrowthProposal.to_dict() 形状的 dict，
    顶层额外携带 mutation_id / request_id / trace_id / evidence。
    ProposalStore.save 对未知顶层键安全（append 原样落盘），
    contracts.GrowthProposal.from_dict 忽略未知键（往返无损）。

    状态按 LEGACY_STATUS_TO_CANONICAL 映射（cancelled → rejected 最接近终态）。
    """
    source: Dict[str, Any] = {}
    if is_dataclass(legacy) and not isinstance(legacy, type):
        source = asdict(legacy)
    elif isinstance(legacy, dict):
        source = dict(legacy)
    else:
        try:
            source = dict(vars(legacy))
        except Exception:  # noqa: BLE001
            source = {}

    proposal_id = (
        str(source.get("proposal_id") or source.get("id") or "")
        or f"prop_{uuid.uuid4().hex[:12]}"
    )

    # 维度变化：affected_dimensions + before/after state
    affected = source.get("affected_dimensions") or {}
    before_state = source.get("before_state") or {}
    after_state = source.get("after_state") or {}
    reason = str(source.get("reason") or "")
    proposed_changes: List[Dict[str, Any]] = []
    for dim in affected:
        proposed_changes.append({
            "path": str(dim),
            "before": before_state.get(dim),
            "after": after_state.get(dim),
            "reason": reason,
        })

    evidence_ids = [str(e) for e in (source.get("evidence") or []) if e]

    linkage = {
        "mutation_id": mutation_id,
        "request_id": request_id,
        "trace_id": trace_id,
        "evidence": list(evidence or []),
    }
    evaluator_meta = dict(source.get("metadata") or {})
    evaluator_meta["_governance"] = dict(linkage)
    evaluator_meta["_source_schema"] = "proposal.proposal.GrowthProposal(legacy)"
    evaluator_meta["_converted_by"] = "growth.mutation_adapter.to_mutation_proposal"

    canonical: Dict[str, Any] = {
        "id": proposal_id,
        "source_event_id": source.get("source_event_id"),
        "proposed_changes": proposed_changes,
        "confidence": _to_float(source.get("confidence")) or 0.0,
        "evidence_ids": evidence_ids,
        "evaluator_meta": evaluator_meta,
        "timestamp": source.get("timestamp")
        or source.get("reviewed_at")
        or datetime.utcnow().isoformat() + "Z",
        "status": LEGACY_STATUS_TO_CANONICAL.get(
            str(source.get("status") or ""), "pending"
        ),
        "accepted_at": source.get("accepted_at"),
        "rejected_at": source.get("rejected_at"),
        "schema_version": growth_schema.CANONICAL_SCHEMA_VERSION,
    }
    # Phase 4 契约：所有新产生 proposal 必须带治理链接键（顶层冗余 + meta 双写）
    canonical["mutation_id"] = mutation_id
    canonical["request_id"] = request_id
    canonical["trace_id"] = trace_id
    canonical["evidence"] = list(evidence or [])
    return canonical


# ============================================================
# GrowthMutationAdapter
# ============================================================
class GrowthMutationAdapter:
    """Legacy growth intent → MutationRequest → Gateway → 裁决执行约定。"""

    def __init__(
        self,
        *,
        gateway: Optional[MutationGateway] = None,
        audit_writer: Optional[AuditWriter] = None,
        conflict_store: Any = None,
    ) -> None:
        if gateway is None:
            if conflict_store is not None:
                # Phase 6 测试 #6：注入 exists_similar 协作件启用重复提案拦截
                from src.governance.checks import ConflictCheck

                gateway = MutationGateway(
                    conflict_check=ConflictCheck(proposal_store=conflict_store),
                )
            else:
                gateway = MutationGateway()
        self.gateway = gateway
        self.audit_writer = audit_writer if audit_writer is not None else InMemoryAuditWriter()
        if getattr(self.gateway, "audit_writer", None) is None:
            self.gateway.audit_writer = self.audit_writer
        # 每次 route 的裁决留痕（request_id / decision / audit_reference / applied）
        self.outcomes: List[Dict[str, Any]] = []
        # Phase 3 DEFER 延迟队列槽位（pipeline 层消费；本 Adapter 不落盘）
        self.deferred: List[Dict[str, Any]] = []

    # ============================================================
    # 转换：Growth intent → MutationRequest（target_domain="growth"）
    # ============================================================
    def build_request(
        self,
        *,
        source_event: Dict[str, Any],
        target_path: str,
        proposed_change: Dict[str, Any],
        evidence: List[Any],
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
        actor_identity: str = "growth_system",
        mutation_id: Optional[str] = None,
    ) -> MutationRequest:
        """构造标准 9 字段 MutationRequest（target_domain 恒为 "growth"）。

        边界前置守卫：
        - target_path 必须位于 growth 域命名空间（growth. 前缀）——
          与 BoundaryCheck 的域规则同源，跨域请求在此即拒（非绕过）。
        - context_snapshot 不自动补齐：缺 request_id/trace_id 时
          AuditCheck 会给出 DEFER（链路可补后重提），保证 DEFER 路径可触达。
        """
        if not str(target_path).startswith("growth."):
            raise ValueError(
                "GrowthMutationAdapter 只处理 growth 域路径 "
                f"（target_path 必须以 'growth.' 开头），得到 {target_path!r}"
            )
        kwargs: Dict[str, Any] = {
            "source_event": dict(source_event),
            "actor_identity": actor_identity,
            "target_domain": "growth",
            "target_path": target_path,
            "proposed_change": dict(proposed_change),
            "evidence": list(evidence),
            "context_snapshot": dict(context_snapshot or {}),
            "risk_level": risk_level,
        }
        if mutation_id:
            kwargs["mutation_id"] = mutation_id
        return MutationRequest(**kwargs)

    def from_growth_event(
        self,
        event: Dict[str, Any],
        *,
        primary_dimension: Optional[str] = None,
        delta: Optional[float] = None,
        before: Optional[float] = None,
        after: Optional[float] = None,
        confidence: Optional[float] = None,
        evidence: Optional[List[Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
    ) -> Optional[MutationRequest]:
        """GrowthEvent → MutationRequest（转换器，测试/接线用）。

        维度信息必须由评估结果提供（primary_dimension / delta 至少其一）；
        禁止 Adapter 自行猜测成长方向——缺维度信息时返回 None。
        evidence 未显式提供时，从 event.source_ids（用户消息来源）构造，
        类型记为 user_behavior（SOURCE_RELIABILITY 最高可信来源）。

        转换器便利语义：context_snapshot 缺 request_id/trace_id 时自动补齐
        （与 build_request 的显式语义区分，见 build_request 注释）。
        """
        dimension = primary_dimension or event.get("primary_dimension") or ""
        if not dimension or delta is None:
            return None
        dim = _normalize_metric_name(dimension)
        target_path = f"growth.metrics.{dim}"

        confidence_val = (
            _to_float(confidence)
            if confidence is not None
            else _to_float(event.get("confidence")) or 0.5
        )
        before_val = (
            _to_float(before)
            if before is not None
            else _to_float(event.get("metric_before"))
        )
        after_val = (
            _to_float(after)
            if after is not None
            else (round(before_val + delta, 6) if before_val is not None else None)
        )
        proposed_change: Dict[str, Any] = {
            "path": target_path,
            "before": before_val,
            "after": after_val,
            "delta": round(float(delta), 6),
            "confidence": confidence_val,
        }

        if evidence is None:
            source_ids = event.get("source_ids") or []
            evidence = [
                {"ref": str(sid), "type": "user_behavior"} for sid in source_ids
            ]
        if not evidence:
            evidence = [
                {"ref": f"ev_{event.get('event_id', 'unknown')}", "type": "user_behavior"}
            ]

        snapshot = dict(context_snapshot or {})
        for key, value in _gen_linkage_ids().items():
            snapshot.setdefault(key, value)

        source_event = {
            "event_id": event.get("event_id", ""),
            "type": event.get("event_type", ""),
            "canonical_topic": event.get("canonical_topic", ""),
            "occurrence_count": event.get("occurrence_count", 1),
            "confidence": confidence_val,
        }
        return self.build_request(
            source_event=source_event,
            target_path=target_path,
            proposed_change=proposed_change,
            evidence=evidence,
            context_snapshot=snapshot,
            risk_level=risk_level,
        )

    def from_proposal(
        self,
        proposal: Any,
        source_event: Optional[Dict[str, Any]] = None,
        *,
        current_metrics: Optional[Dict[str, float]] = None,
        evidence: Optional[List[Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
    ) -> List[MutationRequest]:
        """canonical GrowthProposal → 每个 ChangeItem 一个 MutationRequest。

        - target_path = growth.metrics.<dim>（personality./trait. 前缀剥离）
        - before 缺省时从 current_metrics 取当前成长指标值
        - 转换器便利语义：context_snapshot 缺 request_id/trace_id 时自动补齐
        """
        if not hasattr(proposal, "proposed_changes") and not isinstance(proposal, dict):
            return []
        raw_changes = proposal.proposed_changes if not isinstance(proposal, dict) else proposal.get("proposed_changes", [])
        confidence = _to_float(getattr(proposal, "confidence", 0.5)) or 0.5
        if evidence is None:
            evidence_ids = getattr(proposal, "evidence_ids", []) if not isinstance(proposal, dict) else proposal.get("evidence_ids", [])
            evidence = [
                {"ref": str(eid), "type": "proposal_evidence"} for eid in evidence_ids
            ]

        requests: List[MutationRequest] = []
        for ci in raw_changes:
            ci_dict = (
                asdict(ci)
                if is_dataclass(ci) and not isinstance(ci, type)
                else dict(ci) if isinstance(ci, dict) else {}
            )
            dim = _normalize_metric_name(ci_dict.get("path") or "")
            target_path = f"growth.metrics.{dim}"
            before_val = _to_float(ci_dict.get("before"))
            after_val = _to_float(ci_dict.get("after"))
            if before_val is None and current_metrics is not None:
                before_val = _to_float(current_metrics.get(dim))
            delta_val = (
                round(after_val - before_val, 6)
                if (before_val is not None and after_val is not None)
                else None
            )
            proposed_change: Dict[str, Any] = {
                "path": target_path,
                "before": before_val,
                "after": after_val,
                "delta": delta_val,
                "confidence": confidence,
            }
            snapshot = dict(context_snapshot or {})
            for key, value in _gen_linkage_ids().items():
                snapshot.setdefault(key, value)
            base_event = source_event if source_event is not None else {
                "event_id": getattr(proposal, "source_event_id", "") or "",
            }
            requests.append(self.build_request(
                source_event=base_event,
                target_path=target_path,
                proposed_change=proposed_change,
                evidence=evidence,
                context_snapshot=snapshot,
                risk_level=risk_level,
            ))
        return requests

    # ============================================================
    # 治理：MutationRequest → Gateway 决策 → 裁决执行约定
    # ============================================================
    def route(
        self,
        request: MutationRequest,
        *,
        apply_route: Optional[Callable[[MutationRequest, MutationDecision], bool]] = None,
    ) -> Dict[str, Any]:
        """执行治理链，返回 envelope：

        {
          "request_id": str,       # = mutation_id
          "mutation_id": str,
          "target_domain": str,
          "target_path": str,
          "decision": "ACCEPT" | "REJECT" | "NEED_REVIEW" | "DEFER",
          "audit_reference": str,  # 拒绝同样有审计引用（可追踪）
          "applied": bool,
          "reason": str,
          "note": str,
          "evidence_refs": [str],  # Phase 5：证据引用
        }

        约定：
        - ACCEPT 且注入 apply_route → 调用 apply_route（现有 Apply Adapter
          入口，如 GrowthEngine.apply_proposal 包装）；返回 True 才记 applied=True
        - ACCEPT 但未注入 apply_route → applied=False（禁止自动 apply）
        - REJECT / NEED_REVIEW → 不调用 apply_route、不修改任何状态
        - DEFER → 追加延迟队列槽位（self.deferred），不修改任何状态
        """
        if not isinstance(request, MutationRequest):
            raise TypeError(
                f"route 需要 MutationRequest，得到 {type(request).__name__}"
            )

        evidence_refs = [
            str(item.get("ref"))
            for item in request.evidence
            if isinstance(item, dict) and item.get("ref")
        ]
        envelope: Dict[str, Any] = {
            "request_id": request.mutation_id,
            "mutation_id": request.mutation_id,
            "trace_id": request.context_snapshot.get("trace_id"),
            "target_domain": request.target_domain,
            "target_path": request.target_path,
            "decision": None,
            "audit_reference": None,
            "applied": False,
            "reason": "",
            "note": "",
            "evidence_refs": evidence_refs,
        }

        decision = self.gateway.evaluate(request)
        envelope["decision"] = decision.decision.value
        envelope["reason"] = decision.reason
        envelope["audit_reference"] = decision.audit_reference or None

        if decision.decision == DecisionVerdict.ACCEPT:
            if apply_route is not None:
                try:
                    envelope["applied"] = bool(apply_route(request, decision))
                    envelope["note"] = (
                        "accepted_applied_via_apply_adapter"
                        if envelope["applied"]
                        else "accepted_apply_adapter_declined"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[growth_mutation_adapter] apply_route 异常（已隔离）: %s",
                        exc,
                    )
                    envelope["note"] = f"apply_adapter_error: {exc!r}"
            else:
                envelope["note"] = "accepted_no_apply_route（禁止自动 apply）"
        elif decision.decision == DecisionVerdict.DEFER:
            self.deferred.append({
                "request_id": request.mutation_id,
                "mutation_id": request.mutation_id,
                "target_path": request.target_path,
                "decision": DecisionVerdict.DEFER.value,
                "reason": decision.reason,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
            envelope["note"] = "verdict_defer_no_state_change"
        else:
            envelope["note"] = (
                f"verdict_{decision.decision.value.lower()}_no_state_change"
            )

        self.outcomes.append(envelope)
        return envelope

    # ============================================================
    # 审计可观测
    # ============================================================
    def audit_trace(self) -> Dict[str, List[Dict[str, Any]]]:
        """返回审计留痕（requests / decisions），供调试与测试追溯。"""
        writer = self.audit_writer
        if isinstance(writer, InMemoryAuditWriter):
            return {
                "requests": [dict(r) for r in writer.requests],
                "decisions": [dict(d) for d in writer.decisions],
            }
        return {"requests": [], "decisions": []}


__all__ = [
    "GrowthMutationAdapter",
    "is_growth_mutation_gateway_enabled",
    "set_growth_mutation_gateway_enabled",
    "attach_governance_linkage",
    "to_mutation_proposal",
    "LEGACY_STATUS_TO_CANONICAL",
    "GOVERNANCE_LINKAGE_KEYS",
    "ALLOWED_TARGET_DOMAINS",
]
