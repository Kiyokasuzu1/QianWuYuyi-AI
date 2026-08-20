# -*- coding: utf-8 -*-
"""
P2.3-B.13 — SelfModelMutationAdapter（Self Model mutation 治理迁移层）

定位（B.13 任务书 Phase 1/2；依据 B.12 审计 SM-01~SM-22）：
    Self Model 的多源直接写入状态 → MutationGateway 治理后的单入口变化模型。
    本 Adapter 是**纯转换层 + 裁决执行约定层**：

      SelfObservation / GrowthRecord / RelationshipEvent / EmotionEvent
          ↓ 统一转换（Phase 1 设计）
      SelfModelMutationRequest（10 字段冻结）
          ↓ to_mutation_request()
      MutationRequest（9 字段契约）
          ↓ MutationGateway（五道检查）
      MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）
          ↓（ACCEPT 且外部注入 apply_route）
      SelfModelApplyAdapter → SelfModelStore（Phase 3，本文件禁止执行）

SelfModelMutationRequest 字段（任务书 Phase 1 冻结，10 字段）：
    mutation_id / domain / target_path / change_type / before /
    proposed_after / evidence_refs / confidence / actor_identity / risk_level

红线（B.12 Phase 4 分类落地）：
    - 绝对身份字段（self_model.identity_name / identity_summary /
      creator / origin / manifesto 及 "self_model.identity." 前缀）：
      build_request 一律 ValueError（REJECT 级，禁止构造）
    - core_values 权重（"self_model.core_values." 前缀）：可构造可评估，
      但 route() 对 Gateway ACCEPT **强制改写为 NEED_REVIEW**（B.12 B-4：
      identity/milestone GrowthRecord 直改价值观权重——治理后永不自动应用）

硬边界（任务书 Phase 2 冻结）：
    - 本文件只做 Event → MutationRequest 转换与 Gateway 路由；
      禁止 save / update / apply：不 import SelfModelStore 写方法、
      不持有任何 store 写句柄（apply 由 Phase 3 ApplyAdapter 经
      apply_route 注入执行）
    - 默认关闭（self_model_mutation_gateway_enabled = False）：
      旧生产路径字节不变；开启后才接管 B.12 审计的 P0 直写路径
    - 不自动批准任何 SelfModel 变化：NEED_REVIEW 一律入
      pending_proposals（+ Gateway 注入 ProposalStore 时自动落账，B.10）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

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
_self_model_mutation_gateway_enabled: bool = False

# B.13 范围：self_model 域（命名空间与 BoundaryCheck 同源）
ALLOWED_TARGET_DOMAINS: frozenset = frozenset({"self_model"})

# 治理链接键（与 B.5/B.7/B.9 约定一致）
GOVERNANCE_LINKAGE_KEYS: tuple = ("mutation_id", "request_id", "trace_id", "evidence")

# 任务书规定标准 actor（IdentityCheck 白名单已扩展）
DEFAULT_ACTOR_IDENTITY: str = "self_model_system"

# 绝对身份字段：REJECT 级（构造即拒，B.12 Phase 4 REJECT 分类）
ABSOLUTE_IDENTITY_PATHS: frozenset = frozenset({
    "self_model.identity_name",
    "self_model.identity_summary",
    "self_model.creator",
    "self_model.origin",
    "self_model.manifesto",
})
ABSOLUTE_IDENTITY_PREFIXES: tuple = ("self_model.identity.",)

# core_values 邻域：NEED_REVIEW 级（可评估，永不自动应用）
CORE_VALUES_PREFIX: str = "self_model.core_values."


def is_self_model_mutation_gateway_enabled() -> bool:
    """迁移开关状态（默认 False → 旧路径）。"""
    return _self_model_mutation_gateway_enabled


def set_self_model_mutation_gateway_enabled(enabled: bool) -> None:
    """切换迁移开关（True → B.12 审计的 P0 直写路径改经 Gateway）。"""
    global _self_model_mutation_gateway_enabled
    _self_model_mutation_gateway_enabled = bool(enabled)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


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


# ============================================================
# Phase 1 设计落地：SelfModelMutationRequest（10 字段冻结）
# ============================================================
@dataclass(frozen=True)
class SelfModelMutationRequest:
    """四源统一转换后的 Self Model 变更意图（任务书 Phase 1 冻结，10 字段）。

    - mutation_id     ：治理链唯一 id（mut_ 前缀）
    - domain          ：恒为 "self_model"
    - target_path     ：self_model.* 命名空间路径
    - change_type     ：narrative_append / preference_update /
                        core_value_weight / belief_append / rebuild
    - before / proposed_after：变更前后值（数值或 JSON 安全标量）
    - evidence_refs   ：证据引用列表（ref 字符串）
    - confidence      ：[0,1] 置信度
    - actor_identity  ：触发者身份
    - risk_level      ：low / medium / high
    """

    mutation_id: str
    domain: str = "self_model"
    target_path: str = ""
    change_type: str = ""
    before: Any = None
    proposed_after: Any = None
    evidence_refs: List[str] = field(default_factory=list)
    confidence: float = 0.5
    actor_identity: str = DEFAULT_ACTOR_IDENTITY
    risk_level: str = "low"

    def __post_init__(self) -> None:
        if not isinstance(self.mutation_id, str) or not self.mutation_id.strip():
            raise ValueError("mutation_id 必须是非空 str")
        if self.domain != "self_model":
            raise ValueError(f"domain 必须是 self_model，得到 {self.domain!r}")
        if not isinstance(self.target_path, str) or not self.target_path.startswith(
            ("self_model.", "selfmodel."),
        ):
            raise ValueError(
                "target_path 必须以 'self_model.' 开头，"
                f"得到 {self.target_path!r}"
            )
        if self.target_path in ABSOLUTE_IDENTITY_PATHS or any(
            self.target_path.startswith(p) for p in ABSOLUTE_IDENTITY_PREFIXES
        ):
            raise ValueError(
                f"identity 红线：{self.target_path!r} 禁止构造变更请求"
                "（绝对身份字段，B.12 Phase 4 REJECT 分类）"
            )
        if not self.evidence_refs:
            raise ValueError("evidence_refs 必须非空（SelfModel 变更必须有证据）")
        if isinstance(self.confidence, bool) or not (
            0.0 <= float(self.confidence) <= 1.0
        ):
            raise ValueError(f"confidence 必须在 [0,1]，得到 {self.confidence!r}")
        object.__setattr__(self, "confidence", float(self.confidence))
        if self.risk_level not in ("low", "medium", "high"):
            raise ValueError(f"risk_level 非法：{self.risk_level!r}")

    def to_mutation_request(
        self,
        *,
        source_event: Optional[Dict[str, Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
    ) -> MutationRequest:
        """→ 标准 9 字段 MutationRequest（进 Gateway 的唯一投影）。"""
        snapshot = dict(context_snapshot or {})
        for key, value in _gen_linkage_ids().items():
            if key == "mutation_id":
                snapshot.setdefault(key, self.mutation_id)
            else:
                snapshot.setdefault(key, value)
        snapshot.setdefault("change_type", self.change_type)
        snapshot.setdefault("source", "self_model_mutation_adapter")

        before = _to_float(self.before)
        after = _to_float(self.proposed_after)
        delta = (
            round(after - before, 6)
            if (before is not None and after is not None)
            else None
        )
        proposed_change: Dict[str, Any] = {
            "path": self.target_path,
            "change_type": self.change_type,
            "before": before if before is not None else self.before,
            "after": after if after is not None else self.proposed_after,
            "delta": delta,
            "confidence": self.confidence,
        }
        event = dict(source_event or {})
        event.setdefault("type", self.change_type or "self_model_mutation")
        event.setdefault("source", "self_model_mutation_adapter")
        event.setdefault("confidence", self.confidence)

        return MutationRequest(
            mutation_id=self.mutation_id,
            source_event=event,
            actor_identity=self.actor_identity,
            target_domain="self_model",
            target_path=self.target_path,
            proposed_change=proposed_change,
            evidence=[{"ref": str(r), "type": "self_model_source"} for r in self.evidence_refs],
            context_snapshot=snapshot,
            risk_level=self.risk_level,
        )


# ============================================================
# 四源统一转换（任务书 Phase 1：SelfObservation/GrowthRecord/
# RelationshipEvent/EmotionEvent → SelfModelMutationRequest）
# ============================================================
def from_self_observation(
    observation: Any,
    *,
    target_path: str,
    change_type: str,
    before: Any = None,
    proposed_after: Any = None,
    confidence: Optional[float] = None,
    risk_level: str = "low",
) -> Optional[SelfModelMutationRequest]:
    """SelfObservation（B.11）→ SelfModelMutationRequest。

    evidence 取 observation.evidence_refs；confidence 缺省取观察置信度。
    observation 为 None 或无证据 → None（不转换，B.12 Phase 5 判定：
    无证据的观察永远不能改变 SelfModel）。
    """
    if observation is None:
        return None
    refs = list(getattr(observation, "evidence_refs", None) or [])
    if not refs:
        return None
    conf = (
        float(confidence)
        if confidence is not None
        else float(getattr(observation, "confidence", 0.5) or 0.5)
    )
    return SelfModelMutationRequest(
        mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
        target_path=target_path,
        change_type=change_type,
        before=before,
        proposed_after=proposed_after,
        evidence_refs=[str(r) for r in refs],
        confidence=conf,
        actor_identity=DEFAULT_ACTOR_IDENTITY,
        risk_level=risk_level,
    )


def from_growth_record(
    record: Dict[str, Any],
    *,
    target_path: str,
    change_type: str,
    before: Any = None,
    proposed_after: Any = None,
    risk_level: str = "low",
) -> Optional[SelfModelMutationRequest]:
    """GrowthRecord → SelfModelMutationRequest（B.12 SM-01/SM-09 治理入口）。

    evidence 取 source_event_id + record_id；confidence 取记录置信度。
    """
    if not isinstance(record, dict):
        return None
    refs = [
        str(r) for r in (
            [record.get("source_event_id"), record.get("record_id")]
            + list(record.get("evidence_ids") or [])
        ) if r
    ]
    if not refs:
        return None
    return SelfModelMutationRequest(
        mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
        target_path=target_path,
        change_type=change_type,
        before=before,
        proposed_after=proposed_after,
        evidence_refs=refs,
        confidence=float(record.get("confidence", 0.5) or 0.5),
        actor_identity=DEFAULT_ACTOR_IDENTITY,
        risk_level=risk_level,
    )


def from_relationship_event(
    key: str,
    value: Any,
    *,
    before: Any = None,
    confidence: float = 0.5,
    evidence_refs: Optional[List[str]] = None,
    risk_level: str = "medium",
) -> Optional[SelfModelMutationRequest]:
    """RelationshipEvent 数值 → SelfModelMutationRequest（B.12 SM-10/SM-11 治理入口）。

    跨域（relationship → self_model）一律 risk_level=medium 且
    target_path 落在 preferences 邻域。
    """
    refs = [str(r) for r in (evidence_refs or []) if r]
    if not refs:
        refs = [f"relationship_snapshot::{key}"]
    return SelfModelMutationRequest(
        mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
        target_path=f"self_model.preferences.relationship.{key}",
        change_type="preference_update",
        before=before,
        proposed_after=value,
        evidence_refs=refs,
        confidence=confidence,
        actor_identity="relationship_system",
        risk_level=risk_level,
    )


def from_emotion_event(
    belief_text: str,
    *,
    confidence: float = 0.5,
    evidence_refs: Optional[List[str]] = None,
    risk_level: str = "medium",
) -> Optional[SelfModelMutationRequest]:
    """EmotionEvent / EmotionBelief → SelfModelMutationRequest（B.12 SM-14 治理入口）。

    情绪信念进入 SelfModel 一律 medium 风险（B.12 B-2）。
    """
    if not belief_text:
        return None
    refs = [str(r) for r in (evidence_refs or []) if r]
    if not refs:
        return None  # 情绪信念无来源 → 不转换（B.12 Phase 4 Beliefs 红线：需 provenance）
    return SelfModelMutationRequest(
        mutation_id=f"mut_{uuid.uuid4().hex[:12]}",
        target_path="self_model.beliefs.emotion",
        change_type="belief_append",
        before=None,
        proposed_after=belief_text,
        evidence_refs=refs,
        confidence=confidence,
        actor_identity="emotion_system",
        risk_level=risk_level,
    )


# ============================================================
# SelfModelMutationAdapter
# ============================================================
class SelfModelMutationAdapter:
    """四源 intent → SelfModelMutationRequest → Gateway → 裁决执行约定。

    禁止 save / update / apply：ACCEPT 的执行经 apply_route 注入
    （Phase 3 SelfModelApplyAdapter），本 Adapter 不持有任何写句柄。
    """

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
        self.outcomes: List[Dict[str, Any]] = []
        self.deferred: List[Dict[str, Any]] = []
        self.pending_proposals: List[Dict[str, Any]] = []
        # Phase 5：统一审计留痕（mutation_id/request_id/trace_id/decision/reason）
        self.audit_records: List[Dict[str, Any]] = []

    # ============================================================
    # 转换：intent → MutationRequest（target_domain="self_model"）
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
    ) -> MutationRequest:
        """构造标准 9 字段 MutationRequest（target_domain 恒为 "self_model"）。

        红线前置守卫（B.12 Phase 4）：
        - target_path 必须位于 self_model 命名空间
        - 绝对身份字段（identity_name/summary/creator/origin/manifesto/
          "self_model.identity." 前缀）→ ValueError（REJECT 级）
        """
        if not str(target_path).startswith(("self_model.", "selfmodel.")):
            raise ValueError(
                "SelfModelMutationAdapter 只处理 self_model 域路径"
                f"（target_path 必须以 'self_model.' 开头），得到 {target_path!r}"
            )
        if target_path in ABSOLUTE_IDENTITY_PATHS or any(
            target_path.startswith(p) for p in ABSOLUTE_IDENTITY_PREFIXES
        ):
            raise ValueError(
                f"identity 红线：{target_path!r} 禁止构造变更请求（绝对身份字段）"
            )
        kwargs: Dict[str, Any] = {
            "source_event": dict(source_event),
            "actor_identity": actor_identity,
            "target_domain": "self_model",
            "target_path": target_path,
            "proposed_change": dict(proposed_change),
            "evidence": list(evidence),
            "context_snapshot": dict(context_snapshot or {}),
            "risk_level": risk_level,
        }
        if mutation_id:
            kwargs["mutation_id"] = mutation_id
        return MutationRequest(**kwargs)

    # ============================================================
    # 治理：MutationRequest → Gateway → 裁决执行约定
    # ============================================================
    def route(
        self,
        request: MutationRequest,
        *,
        apply_route: Optional[Callable[[MutationRequest, MutationDecision], bool]] = None,
    ) -> Dict[str, Any]:
        """执行治理链，返回 envelope（11 键，与 B.5/B.7/B.9 一致）。

        裁决执行约定：
        - ACCEPT 且注入 apply_route → 调用（Phase 3 ApplyAdapter）
        - ACCEPT 未注入 → applied=False（禁止自动 apply）
        - core_values 邻域：即使 Gateway ACCEPT 也强制改写 NEED_REVIEW
          （B.12 B-4 红线：价值观权重永不自动应用，必须人工复核）
        - REJECT / NEED_REVIEW / DEFER → 不修改任何状态
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

        # core_values 邻域强制复核（Gateway ACCEPT 也不自动应用）
        core_values_domain = request.target_path.startswith(CORE_VALUES_PREFIX)

        if decision.decision == DecisionVerdict.ACCEPT:
            if core_values_domain:
                self._park_pending(request, envelope, decision, forced=True)
            elif apply_route is not None:
                try:
                    envelope["applied"] = bool(apply_route(request, decision))
                    envelope["note"] = (
                        "accepted_applied_via_apply_adapter"
                        if envelope["applied"]
                        else "accepted_apply_adapter_declined"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[self_model_mutation_adapter] apply_route 异常（已隔离）: %s",
                        exc,
                    )
                    envelope["note"] = f"apply_adapter_error: {exc!r}"
            else:
                envelope["note"] = "accepted_no_apply_route（禁止自动 apply）"
        elif decision.decision == DecisionVerdict.NEED_REVIEW:
            self._park_pending(request, envelope, decision, forced=False)
        elif decision.decision == DecisionVerdict.DEFER:
            self.deferred.append({
                "request_id": request.mutation_id,
                "mutation_id": request.mutation_id,
                "target_path": request.target_path,
                "decision": DecisionVerdict.DEFER.value,
                "reason": decision.reason,
                "timestamp": _now_iso(),
            })
            envelope["note"] = "verdict_defer_no_state_change"
        else:
            envelope["note"] = (
                f"verdict_{decision.decision.value.lower()}_no_state_change"
            )

        self._append_audit_record(request, envelope)
        self.outcomes.append(envelope)
        return envelope

    def _park_pending(
        self,
        request: MutationRequest,
        envelope: Dict[str, Any],
        decision: MutationDecision,
        *,
        forced: bool,
    ) -> None:
        """NEED_REVIEW 入内存槽位（Gateway 注入 ProposalStore 时同步落账）。"""
        if forced:
            # core_values 强制复核：Gateway 返回的是 ACCEPT（未触发 B.10
            # 自动落账），这里显式以 NEED_REVIEW 决策落账，供 B.11 消费链
            import dataclasses

            review_decision = dataclasses.replace(
                decision,
                decision=DecisionVerdict.NEED_REVIEW,
                reason=(
                    "core_values_requires_review_no_auto_apply: "
                    "价值观权重变更必须进入人工复核队列（B.12 B-4 红线）"
                ),
            )
            try:
                self.gateway.save_pending_proposal(request, review_decision)
            except Exception:  # noqa: BLE001 落账异常隔离（可能未注入 store）
                pass
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
            "reason": (
                "core_values_requires_review_no_auto_apply"
                if forced
                else decision.reason
            ),
            "audit_reference": decision.audit_reference or "",
            "created_at": _now_iso(),
        })
        if forced:
            envelope["decision"] = DecisionVerdict.NEED_REVIEW.value
            envelope["reason"] = (
                "core_values_requires_review_no_auto_apply: "
                "价值观权重变更必须进入人工复核队列（B.12 B-4 红线）"
            )
        envelope["note"] = "verdict_needs_review_pending_proposal_no_state_change"

    # ============================================================
    # Phase 5：MutationAuditRecord 留痕（5 字段）
    # ============================================================
    def _append_audit_record(
        self, request: MutationRequest, envelope: Dict[str, Any],
    ) -> None:
        snapshot = request.context_snapshot or {}
        self.audit_records.append({
            "mutation_id": request.mutation_id,
            "request_id": str(snapshot.get("request_id") or request.mutation_id),
            "trace_id": str(snapshot.get("trace_id") or ""),
            "decision": str(envelope.get("decision") or ""),
            "reason": str(envelope.get("reason") or ""),
            "domain": request.target_domain,
            "actor": request.actor_identity,
            "timestamp": _now_iso(),
        })

    def audit_trace(self) -> List[Dict[str, Any]]:
        """全部 SelfModel mutation 审计留痕（mutation_id/request_id/
        trace_id/decision/reason 五键齐全，Phase 5 冻结）。"""
        return [dict(r) for r in self.audit_records]


# ============================================================
# 模块单例（治理接线方获取；与 B.9 模式一致）
# ============================================================
_self_model_mutation_adapter: Optional[SelfModelMutationAdapter] = None


def get_self_model_mutation_adapter() -> SelfModelMutationAdapter:
    """惰性单例（P0 接线点共用同一 adapter 实例与审计留痕）。"""
    global _self_model_mutation_adapter
    if _self_model_mutation_adapter is None:
        _self_model_mutation_adapter = SelfModelMutationAdapter()
    return _self_model_mutation_adapter


__all__ = [
    "SelfModelMutationAdapter",
    "SelfModelMutationRequest",
    "from_self_observation",
    "from_growth_record",
    "from_relationship_event",
    "from_emotion_event",
    "is_self_model_mutation_gateway_enabled",
    "set_self_model_mutation_gateway_enabled",
    "get_self_model_mutation_adapter",
    "GOVERNANCE_LINKAGE_KEYS",
    "ALLOWED_TARGET_DOMAINS",
    "DEFAULT_ACTOR_IDENTITY",
    "ABSOLUTE_IDENTITY_PATHS",
    "CORE_VALUES_PREFIX",
]
