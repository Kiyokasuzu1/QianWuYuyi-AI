# -*- coding: utf-8 -*-
"""
P2.3-B.4 — PersonalityMutationAdapter（人格 mutation 治理迁移层）

定位（B.4 任务书 Phase 2）：
    旧系统产生的 mutation intent（PersonalityResolver 的 trait 漂移 /
    RuntimeController 的消息增量）先转换、后治理。本 Adapter 是
    转换层 + 裁决执行约定层：

      Legacy intent → MutationRequest（9 字段契约）
                    → MutationGateway（五道检查）
                    → MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）

裁决执行约定（B.2 §3.3 + B.4 Phase 4）：
    - ACCEPT       才允许进入 apply adapter（apply_route 回调，由下游
                    Proposal / Approval 链注入）；未注入 → 不自动 apply
    - REJECT / NEED_REVIEW / DEFER → 不修改任何状态，仅审计留痕
    - p_rt_* / a_rt_* 伪审批 ID 一律拒绝（B.1 B3 红线修复）

审计接线（B.4 Phase 5）：
    每次 route 产生 request_id / mutation_id / decision / audit_reference，
    拒绝请求同样可追踪（默认挂 InMemoryAuditWriter）。

硬边界（禁止）：
    - Adapter 不直接修改 personality / self_model / memory / emotion /
      growth 任何状态；不持有任何 store 写句柄。
    - 不替代 ApprovalManager / EvolutionPipeline：ACCEPT 只是放行信号，
      执行仍由现有治理组件完成。
    - 默认关闭（personality_mutation_gateway_enabled = False）：
      旧生产路径字节不变；开启后才接管 Resolver / RuntimeController 直写。
"""
from __future__ import annotations

import logging
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
_personality_mutation_gateway_enabled: bool = False

# B.1 B3 红线：RuntimeController 伪造的 p_rt_*/a_rt_* 不是合法审批
FORGED_APPROVAL_ID_PREFIXES: tuple = ("p_rt_", "a_rt_")

# B.4 范围：personality 主域 + Resolver 触及的 self_model 次域
ALLOWED_TARGET_DOMAINS: frozenset = frozenset({"personality", "self_model"})


def is_personality_mutation_gateway_enabled() -> bool:
    """迁移开关状态（默认 False → 旧路径）。"""
    return _personality_mutation_gateway_enabled


def set_personality_mutation_gateway_enabled(enabled: bool) -> None:
    """切换迁移开关（True → Resolver / RuntimeController 直写改经 Gateway）。"""
    global _personality_mutation_gateway_enabled
    _personality_mutation_gateway_enabled = bool(enabled)


def is_forged_approval_id(value: Any) -> bool:
    """p_rt_*/a_rt_* 前缀视为伪审批 ID（非 Proposal/Approval 链路产物）。"""
    return isinstance(value, str) and value.startswith(FORGED_APPROVAL_ID_PREFIXES)


def _forged_approval_in(containers: tuple) -> Optional[str]:
    """在若干 mapping 中查找携带审批标识且值为伪审批 ID 的字段。

    返回 "key=value" 描述；无则 None。
    """
    for mapping in containers:
        for key, value in (mapping or {}).items():
            if ("approval" in key or "proposal" in key) and is_forged_approval_id(value):
                return f"{key}={value!r}"
    return None


class PersonalityMutationAdapter:
    """Legacy mutation intent → MutationRequest → Gateway → 裁决执行约定。"""

    def __init__(
        self,
        *,
        gateway: Optional[MutationGateway] = None,
        audit_writer: Optional[AuditWriter] = None,
    ) -> None:
        self.gateway = gateway if gateway is not None else MutationGateway()
        self.audit_writer = audit_writer if audit_writer is not None else InMemoryAuditWriter()
        # 审计强制：若外部注入的 gateway 未挂 writer，则挂本 Adapter 的 writer
        if getattr(self.gateway, "audit_writer", None) is None:
            self.gateway.audit_writer = self.audit_writer
        # 每次 route 的裁决留痕（request_id / decision / audit_reference / applied）
        self.outcomes: List[Dict[str, Any]] = []

    # ============================================================
    # 转换：Legacy intent → MutationRequest
    # ============================================================
    def build_request(
        self,
        *,
        source_event: Dict[str, Any],
        actor_identity: str,
        target_path: str,
        proposed_change: Dict[str, Any],
        evidence: List[Any],
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
        target_domain: str = "personality",
        mutation_id: Optional[str] = None,
    ) -> MutationRequest:
        """构造标准 9 字段 MutationRequest。

        边界：
        - target_domain 仅限 personality / self_model（B.4 范围）
        - 任何字段携带 p_rt_*/a_rt_* 伪审批 ID → ValueError（伪造防线）
        """
        if target_domain not in ALLOWED_TARGET_DOMAINS:
            raise ValueError(
                "PersonalityMutationAdapter 只处理 "
                f"{sorted(ALLOWED_TARGET_DOMAINS)} 域，得到 {target_domain!r}"
            )
        forged = _forged_approval_in(
            (source_event, context_snapshot or {}, proposed_change)
        )
        if forged is not None:
            raise ValueError(
                f"伪审批 ID 禁止进入 MutationRequest：{forged}"
                "（p_rt_*/a_rt_* 不是合法审批）"
            )
        kwargs: Dict[str, Any] = {
            "source_event": dict(source_event),
            "actor_identity": actor_identity,
            "target_domain": target_domain,
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
          "target_domain": str,
          "target_path": str,
          "decision": "ACCEPT" | "REJECT" | "NEED_REVIEW" | "DEFER" | None,
          "audit_reference": str,  # 拒绝同样有审计引用（可追踪）
          "applied": bool,
          "reason": str,
          "note": str,
        }

        约定：
        - ACCEPT 且注入 apply_route → 调用 apply_route（下游 Proposal/Approval
          链入口）；返回 True 才记 applied=True
        - ACCEPT 但未注入 apply_route → applied=False（禁止自动 apply）
        - 其余裁决 → 不调用 apply_route、不修改任何状态
        - 伪审批 ID（绕过 build_request 手工构造的请求）→ 直接 REJECT，
          不进入 Gateway 放行逻辑
        """
        if not isinstance(request, MutationRequest):
            raise TypeError(
                f"route 需要 MutationRequest，得到 {type(request).__name__}"
            )

        envelope: Dict[str, Any] = {
            "request_id": request.mutation_id,
            "target_domain": request.target_domain,
            "target_path": request.target_path,
            "decision": None,
            "audit_reference": None,
            "applied": False,
            "reason": "",
            "note": "",
        }

        # 伪审批 ID 防线（build_request 已拒；此处兜底防手工构造绕过）
        forged = _forged_approval_in(
            (request.source_event, request.context_snapshot, request.proposed_change)
        )
        if forged is not None:
            decision = MutationDecision(
                mutation_id=request.mutation_id,
                decision=DecisionVerdict.REJECT,
                reason=f"forged_approval_id_rejected: {forged}",
                checks={
                    "adapter": {
                        "passed": False,
                        "reason": "forged_approval_id_rejected",
                        "metadata": {"forged": forged},
                    }
                },
            )
            envelope["decision"] = DecisionVerdict.REJECT.value
            envelope["reason"] = decision.reason
            envelope["note"] = "forged_approval_id_blocked_before_gateway"
            envelope["audit_reference"] = self._record_rejection(request, decision)
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
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[personality_mutation_adapter] apply_route 异常（已隔离）: %s",
                        exc,
                    )
                    envelope["note"] = f"apply_adapter_error: {exc!r}"
            else:
                envelope["note"] = "accepted_no_apply_route（禁止自动 apply）"
        else:
            envelope["note"] = (
                f"verdict_{decision.decision.value.lower()}_no_state_change"
            )

        self.outcomes.append(envelope)
        return envelope

    # ============================================================
    # 审计可观测
    # ============================================================
    def _record_rejection(
        self,
        request: MutationRequest,
        decision: MutationDecision,
    ) -> Optional[str]:
        """拒绝留痕：record_request + record_decision，返回 audit_reference。"""
        try:
            self.audit_writer.record_request(request)
        except Exception as exc:  # noqa: BLE001 审计异常隔离
            logger.warning(
                "[personality_mutation_adapter] record_request 失败（已隔离）: %s", exc
            )
        try:
            return self.audit_writer.record_decision(decision)
        except Exception as exc:  # noqa: BLE001 审计异常隔离
            logger.warning(
                "[personality_mutation_adapter] record_decision 失败（已隔离）: %s", exc
            )
            return None

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
    "PersonalityMutationAdapter",
    "is_personality_mutation_gateway_enabled",
    "set_personality_mutation_gateway_enabled",
    "is_forged_approval_id",
    "FORGED_APPROVAL_ID_PREFIXES",
    "ALLOWED_TARGET_DOMAINS",
]
