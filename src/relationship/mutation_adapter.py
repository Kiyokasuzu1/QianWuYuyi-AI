# -*- coding: utf-8 -*-
"""
P2.3-B.9 — RelationshipMutationAdapter（关系 mutation 治理迁移层）

定位（B.9 任务书 Phase 2）：
    旧关系系统产生的 mutation intent（RelationshipEvent → 计划增量）
    先转换、后治理。本 Adapter 是转换层 + 裁决执行约定层：

      RelationshipEvent → MutationRequest（9 字段契约）
                        → MutationGateway（五道检查）
                        → MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）

裁决执行约定（B.9 Phase 2）：
    - ACCEPT       才允许进入现有 Apply 方法（RelationshipIntelligenceEngine
                   .process_interaction 的 allowed_dimensions 执行件，
                   由 RuntimeCore 注入 apply 编排；本 Adapter 注入的
                   apply_route 仅用于测试 / 灰度接线）；
                   未注入 → 不自动 apply
    - REJECT       不改变状态（该变更被丢弃，仅审计留痕）
    - NEED_REVIEW  生成 pending proposal（本 Adapter 内存槽位 pending_proposals，
                   带治理链接键；不落盘、不写 data/）
    - DEFER        进入延迟队列（本 Adapter 的 deferred 槽位，RuntimeCore 消费）

Relationship Apply Boundary（B.8 审计 §5.1）：
    - 关系域状态执行件 = RelationshipIntelligenceEngine.process_interaction
      （apply）+ RelationshipRepository.save_state/save_relationship_model
      （persist）——保留，ACCEPT 后经 RuntimeCore 编排进入。
    - 本 Adapter 禁止直接修改 RelationshipState / 禁止直接调用
      repository.save——只产生 MutationRequest 与裁决执行约定。

审计接线（B.9 Phase 2，对齐 B.7）：
    每次 route 产生 request_id / mutation_id / trace_id / decision /
    target_path / evidence_reference / apply_result；拒绝与失败同样记录
    （默认挂 InMemoryAuditWriter，为 MutationJournal 投影预留，B.3 约定）。

跨域接线（B.9 Phase 4）：
    relationship → SelfIdentity.preferences 的写入在
    relationship_self_model_gateway_enabled=True 时改经
    route_self_model_influence()：target_domain="self_model" 的
    MutationRequest 走完整五道检查，且跨域目标一律强制 NEED_REVIEW
    （任务书冻结：「如果目标跨域：标记 NEED_REVIEW」）。

重复应用防护（B.9 Phase 5）：
    route() 记录已应用的 mutation_id（applied_mutation_ids）；同一
    mutation_id 二次 route 直接 DEFER，不重复调用 apply_route / 不重复
    变更状态——为 R-04/R-05 legacy fallback 与 Runtime 链的「双写收敛」
    提供去重机制（见 LEGACY_MUTATION_SOURCES 登记）。

硬边界（禁止）：
    - Adapter 不直接修改任何领域状态；不持有任何 store 写句柄。
    - 默认关闭（relationship_mutation_gateway_enabled = False）：旧生产
      路径字节不变；开启后才接管 record_relationship_interaction 直写。
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

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
_relationship_mutation_gateway_enabled: bool = False
_relationship_self_model_gateway_enabled: bool = False
_flags_lock = threading.Lock()

# B.9 范围：relationship 主域（route_self_model_influence 的跨域请求除外）
ALLOWED_TARGET_DOMAINS: frozenset = frozenset({"relationship"})

# RelationshipState（v3.5.27）维度 + 计划变更维度（与
# src/relationship/relationship_model.py _plan_deltas 对齐，禁止增删维度）
RELATIONSHIP_DIMENSIONS: tuple = (
    "trust", "familiarity", "collaboration", "interaction_frequency",
    "bond", "relationship_stage",
)

# 所有新产生 pending proposal 必须携带的治理链接键
GOVERNANCE_LINKAGE_KEYS: tuple = ("mutation_id", "request_id", "trace_id", "evidence")

# 任务书规定标准 actor（内部子系统身份；B.3 IdentityCheck 白名单已扩展）
DEFAULT_ACTOR_IDENTITY: str = "relationship_system"

# ============================================================
# Phase 5：legacy mutation sources 登记（B.8 审计 R-04/R-05）
# ============================================================
# 登记 B.8 审计发现的 growth 链 legacy fallback 直写点（不删除、不改动，
# 仅登记来源元数据；未来接线时同一 mutation_id 经 route() 去重防双写）。
LEGACY_MUTATION_SOURCES: Dict[str, Dict[str, str]] = {
    "R-04": {
        "file": "src/growth/growth_engine.py:336-389",
        "function": "GrowthEngine.apply_relationship",
        "target": "v0.6 RelationshipState（data/relationship_state.json）",
        "governance": "legacy_fallback_ungoverned",
        "note": "proposal 路径（pipeline.py:615）上游已治理；legacy fallback（633/786）无 Proposal",
    },
    "R-05": {
        "file": "src/growth/pipeline.py:615,633,786",
        "function": "GrowthPipeline 调用点",
        "target": "v0.6 RelationshipState",
        "governance": "legacy_fallback_ungoverned",
        "note": "B.5 gateway 只裁决 GrowthState 提案，不覆盖关系增量",
    },
}


def is_relationship_mutation_gateway_enabled() -> bool:
    """迁移开关状态（默认 False → 旧路径）。"""
    return _relationship_mutation_gateway_enabled


def set_relationship_mutation_gateway_enabled(enabled: bool) -> None:
    """切换迁移开关（True → record_relationship_interaction 直写改经 Gateway）。"""
    global _relationship_mutation_gateway_enabled
    with _flags_lock:
        _relationship_mutation_gateway_enabled = bool(enabled)


def is_relationship_self_model_gateway_enabled() -> bool:
    """B.9 Phase 4 开关：relationship → SelfIdentity.preferences 是否经治理。"""
    return _relationship_self_model_gateway_enabled


def set_relationship_self_model_gateway_enabled(enabled: bool) -> None:
    """切换 B.9 Phase 4 开关（True → 跨域写入强制 NEED_REVIEW，不直写偏好）。"""
    global _relationship_self_model_gateway_enabled
    with _flags_lock:
        _relationship_self_model_gateway_enabled = bool(enabled)


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


def _event_field(event: Any, name: str, default: Any = "") -> Any:
    """同时兼容 RelationshipEvent TypedDict / dict 输入的字段读取。"""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _default_evidence(
    event_type: str,
    content: str,
    source_memory_id: Optional[str] = None,
    evidence_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从 RelationshipEvent 构造证据链（确定性规则，不依赖 LLM）。

    - 事件内容（若存在）→ user_statement
    - 事件类型 → 结构化事实
    - evidence_ids / source_memory_id / evidence_id → 记忆关联
    去重 ref ≥ 3 时可通过 EvidenceCheck 数量关。
    """
    evidence: List[Dict[str, Any]] = []
    if str(content or "").strip():
        evidence.append({
            "ref": f"msg_{uuid.uuid4().hex[:8]}",
            "type": "user_statement",
        })
    evidence.append({
        "ref": f"evt_{event_type or 'unknown'}",
        "type": "event_type",
    })
    memory_refs: List[str] = []
    if source_memory_id:
        memory_refs.append(str(source_memory_id))
    if evidence_id and str(evidence_id) not in memory_refs:
        memory_refs.append(str(evidence_id))
    for ref in memory_refs:
        evidence.append({"ref": ref, "type": "memory_link"})
    return evidence


# ============================================================
# RelationshipMutationAdapter
# ============================================================
class RelationshipMutationAdapter:
    """Legacy relationship intent → MutationRequest → Gateway → 裁决执行约定。"""

    def __init__(
        self,
        *,
        gateway: Optional[MutationGateway] = None,
        audit_writer: Optional[AuditWriter] = None,
    ) -> None:
        self.gateway = gateway if gateway is not None else MutationGateway()
        self.audit_writer = (
            audit_writer if audit_writer is not None else InMemoryAuditWriter()
        )
        if getattr(self.gateway, "audit_writer", None) is None:
            self.gateway.audit_writer = self.audit_writer
        # 每次 route 的裁决留痕（request_id / decision / audit_reference / applied）
        self.outcomes: List[Dict[str, Any]] = []
        # DEFER 延迟队列槽位（RuntimeCore 层消费；本 Adapter 不落盘）
        self.deferred: List[Dict[str, Any]] = []
        # NEED_REVIEW pending proposal 槽位（带治理链接键；不落盘、不写 data/）
        self.pending_proposals: List[Dict[str, Any]] = []
        # Phase 5：已应用 mutation_id 去重集（同一 mutation_id 不得重复应用）
        self.applied_mutation_ids: Set[str] = set()

    # ============================================================
    # 转换：Relationship intent → MutationRequest（target_domain="relationship"）
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
        actor_identity: str = DEFAULT_ACTOR_IDENTITY,
        mutation_id: Optional[str] = None,
        mutation_source: str = "",
    ) -> MutationRequest:
        """构造标准 9 字段 MutationRequest（target_domain 恒为 "relationship"）。

        边界前置守卫：
        - target_path 必须位于 relationship 域命名空间（relationship. 前缀）——
          与 BoundaryCheck 的域规则同源，跨域请求在此即拒（非绕过）。
        - mutation_source（Phase 5 元数据）写入 context_snapshot，供
          未来审计区分 runtime_stage03 / growth_pipeline_legacy 等来源。
        - context_snapshot 不自动补齐：缺 request_id/trace_id 时
          AuditCheck 会给出 DEFER（链路可补后重提），保证 DEFER 路径可触达。
        """
        if not str(target_path).startswith("relationship."):
            raise ValueError(
                "RelationshipMutationAdapter 只处理 relationship 域路径 "
                f"（target_path 必须以 'relationship.' 开头），得到 {target_path!r}"
            )
        snapshot = dict(context_snapshot or {})
        if mutation_source:
            snapshot.setdefault("mutation_source", str(mutation_source))
        kwargs: Dict[str, Any] = {
            "source_event": dict(source_event),
            "actor_identity": actor_identity,
            "target_domain": "relationship",
            "target_path": target_path,
            "proposed_change": dict(proposed_change),
            "evidence": list(evidence),
            "context_snapshot": snapshot,
            "risk_level": risk_level,
        }
        if mutation_id:
            kwargs["mutation_id"] = mutation_id
        return MutationRequest(**kwargs)

    def from_relationship_event(
        self,
        event: Any,
        *,
        dimension: str,
        delta: float,
        before: Optional[float] = None,
        after: Optional[float] = None,
        confidence: Optional[float] = None,
        evidence: Optional[List[Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
        evidence_id: Optional[str] = None,
        mutation_source: str = "relationship_system",
    ) -> Optional[MutationRequest]:
        """RelationshipEvent → MutationRequest（转换器，RuntimeCore/测试接线用）。

        维度与增量必须由评估结果提供（dimension + delta）；禁止 Adapter 自行
        猜测关系方向——缺维度信息或增量归零时返回 None。
        evidence 未显式提供时，从事件内容/类型/记忆锚点确定性构造。
        转换器便利语义：context_snapshot 缺 request_id/trace_id 时自动补齐
        （与 build_request 的显式语义区分）。
        """
        if not dimension:
            return None
        dim = str(dimension).strip()
        if dim not in RELATIONSHIP_DIMENSIONS:
            return None
        delta_val = round(float(delta), 6)
        if delta_val == 0.0:
            return None
        target_path = f"relationship.state.{dim}"

        event_type = str(_event_field(event, "type") or "")
        content = str(_event_field(event, "content") or "")
        source_memory_id = str(_event_field(event, "source_memory_id") or "")
        event_confidence = _to_float(_event_field(event, "confidence", 0.5)) or 0.5
        evidence_ids = [
            str(item) for item in (_event_field(event, "evidence_ids", []) or [])
        ]

        confidence_val = (
            _to_float(confidence)
            if confidence is not None
            else round(max(0.0, min(1.0, event_confidence)), 4)
        )
        before_val = _to_float(before) if before is not None else None
        after_val = (
            _to_float(after)
            if after is not None
            else (round(before_val + delta_val, 6) if before_val is not None else None)
        )
        proposed_change: Dict[str, Any] = {
            "path": target_path,
            "dimension": dim,
            "before": before_val,
            "after": after_val,
            "delta": delta_val,
            "confidence": confidence_val,
        }

        if evidence is None:
            evidence = _default_evidence(
                event_type,
                content,
                source_memory_id or None,
                evidence_id or (evidence_ids[0] if evidence_ids else None),
            )
        if not evidence:
            evidence = [
                {"ref": f"ev_{uuid.uuid4().hex[:8]}", "type": "user_statement"}
            ]

        snapshot = dict(context_snapshot or {})
        for key, value in _gen_linkage_ids().items():
            snapshot.setdefault(key, value)
        for ref in evidence_ids:
            snapshot.setdefault("memory_id", ref)
        snapshot.setdefault("source", "relationship_adapter.from_relationship_event")

        source_event = {
            "event_id": str(_event_field(event, "id", "") or ""),
            "type": event_type,
            "occurrence_count": 1,
            "confidence": confidence_val,
        }
        return self.build_request(
            source_event=source_event,
            target_path=target_path,
            proposed_change=proposed_change,
            evidence=evidence,
            context_snapshot=snapshot,
            risk_level=risk_level,
            mutation_source=mutation_source,
        )

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
          "trace_id": str,
          "target_domain": str,
          "target_path": str,
          "decision": "ACCEPT" | "REJECT" | "NEED_REVIEW" | "DEFER",
          "audit_reference": str,  # 拒绝同样有审计引用（可追踪）
          "applied": bool,
          "reason": str,
          "note": str,
          "evidence_refs": [str],
        }

        约定（B.9 Phase 2）：
        - 同一 mutation_id 二次 route → 直接 DEFER（duplicate_mutation_id），
          不再调用 Gateway / apply_route（Phase 5 双写收敛防护）
        - ACCEPT 且注入 apply_route → 调用 apply_route（现有 Apply 方法入口）；
          返回 True 才记 applied=True 并登记 applied_mutation_ids
        - ACCEPT 但未注入 apply_route → applied=False（禁止自动 apply）
        - REJECT → 不调用 apply_route、不修改任何状态
        - NEED_REVIEW → 生成 pending proposal（治理链接键齐全）入槽位，不修改状态
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

        # Phase 5：重复 mutation_id 防护（同一变更不得二次应用）
        if request.mutation_id in self.applied_mutation_ids:
            envelope["decision"] = DecisionVerdict.DEFER.value
            envelope["reason"] = (
                f"duplicate_mutation_id: {request.mutation_id} 已应用，禁止重复应用"
            )
            envelope["note"] = "verdict_defer_duplicate_mutation_id_no_apply"
            self.outcomes.append(envelope)
            return envelope

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
                    if envelope["applied"]:
                        self.applied_mutation_ids.add(request.mutation_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[relationship_mutation_adapter] apply_route 异常（已隔离）: %s",
                        exc,
                    )
                    envelope["note"] = f"apply_adapter_error: {exc!r}"
            else:
                envelope["note"] = "accepted_no_apply_route（禁止自动 apply）"
        elif decision.decision == DecisionVerdict.NEED_REVIEW:
            self.pending_proposals.append({
                "proposal_id": f"prop_{request.mutation_id[4:]}",
                "status": "pending",
                "mutation_id": request.mutation_id,
                "request_id": request.mutation_id,
                "trace_id": request.context_snapshot.get("trace_id"),
                "target_domain": request.target_domain,
                "target_path": request.target_path,
                "proposed_change": dict(request.proposed_change),
                "evidence": list(request.evidence),
                "risk_level": request.risk_level,
                "reason": decision.reason,
                "audit_reference": decision.audit_reference or "",
                "created_at": datetime.utcnow().isoformat() + "Z",
            })
            envelope["note"] = "verdict_needs_review_pending_proposal_no_state_change"
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
    # Phase 4：relationship → self_model 跨域影响（强制 NEED_REVIEW）
    # ============================================================
    def route_self_model_influence(
        self,
        key: str,
        value: float,
        *,
        confidence: float = 0.5,
        evidence: Optional[List[Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """把 relationship 数值 → SelfIdentity.preferences 的写入意图转为
        target_domain="self_model" 的 MutationRequest 并走完整五道检查。

        任务书冻结：「如果目标跨域：标记 NEED_REVIEW」——
        Gateway 判定 ACCEPT 时强制改写为 NEED_REVIEW（跨域复核队列），
        绝不自动直写 self_model 偏好；REJECT / DEFER 原样透传。
        返回 envelope（与 route() 同构），applied 恒为 False。
        """
        linkage = _gen_linkage_ids()
        snapshot = dict(context_snapshot or {})
        for k, v in linkage.items():
            snapshot.setdefault(k, v)
        snapshot.setdefault("source", "relationship_adapter.route_self_model_influence")
        snapshot.setdefault("mutation_source", "relationship_system_cross_domain")

        key = str(key).strip()
        value = float(value)
        evidence = list(evidence or [])
        if not evidence:
            evidence = [{
                "ref": f"rel_snapshot_{uuid.uuid4().hex[:8]}",
                "type": "user_behavior",
            }]

        request = MutationRequest(
            mutation_id=linkage["mutation_id"],
            source_event={
                "event_id": f"rel_self_model_{uuid.uuid4().hex[:8]}",
                "type": "relationship_snapshot",
                "occurrence_count": 1,
                "confidence": float(confidence),
            },
            actor_identity=DEFAULT_ACTOR_IDENTITY,
            target_domain="self_model",
            target_path=f"self_model.preferences.relationship.{key}",
            proposed_change={
                "path": f"self_model.preferences.relationship.{key}",
                "key": key,
                "value": value,
                "confidence": float(confidence),
            },
            evidence=evidence,
            context_snapshot=snapshot,
            risk_level="medium",
        )

        envelope = self.route(request, apply_route=None)

        # 跨域强制复核：即使 Gateway 五道全过，relationship → self_model
        # 也不得自动应用（B.9 Phase 4 冻结）
        if envelope.get("decision") == DecisionVerdict.ACCEPT.value:
            self.pending_proposals.append({
                "proposal_id": f"prop_{request.mutation_id[4:]}",
                "status": "pending",
                "mutation_id": request.mutation_id,
                "request_id": request.mutation_id,
                "trace_id": request.context_snapshot.get("trace_id"),
                "target_domain": request.target_domain,
                "target_path": request.target_path,
                "proposed_change": dict(request.proposed_change),
                "evidence": list(request.evidence),
                "risk_level": request.risk_level,
                "reason": "cross_domain_relationship_to_self_model_requires_review",
                "audit_reference": envelope.get("audit_reference") or "",
                "created_at": datetime.utcnow().isoformat() + "Z",
            })
            envelope["decision"] = DecisionVerdict.NEED_REVIEW.value
            envelope["reason"] = (
                "cross_domain_relationship_to_self_model_requires_review: "
                "relationship → self_model 跨域写入必须进入复核队列"
            )
            envelope["note"] = "verdict_needs_review_cross_domain_no_state_change"

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


# ============================================================
# 模块级默认单例（跨域接线 / 测试共享；惰性创建，构造零写入）
# ============================================================
_default_adapter: Optional[RelationshipMutationAdapter] = None
_default_adapter_lock = threading.Lock()


def get_relationship_mutation_adapter() -> RelationshipMutationAdapter:
    """惰性单例（供 SelfModelManager 等跨层接线；构造不产生任何写入）。"""
    global _default_adapter
    if _default_adapter is None:
        with _default_adapter_lock:
            if _default_adapter is None:
                _default_adapter = RelationshipMutationAdapter()
    return _default_adapter


__all__ = [
    "RelationshipMutationAdapter",
    "get_relationship_mutation_adapter",
    "is_relationship_mutation_gateway_enabled",
    "set_relationship_mutation_gateway_enabled",
    "is_relationship_self_model_gateway_enabled",
    "set_relationship_self_model_gateway_enabled",
    "RELATIONSHIP_DIMENSIONS",
    "GOVERNANCE_LINKAGE_KEYS",
    "ALLOWED_TARGET_DOMAINS",
    "DEFAULT_ACTOR_IDENTITY",
    "LEGACY_MUTATION_SOURCES",
]
