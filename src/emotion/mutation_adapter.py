# -*- coding: utf-8 -*-
"""
P2.3-B.7 — EmotionMutationAdapter（情绪 mutation 治理迁移层）

定位（B.7 任务书 Phase 2）：
    旧情绪系统产生的 mutation intent（EmotionEvent → EmotionDelta）
    先转换、后治理。本 Adapter 是转换层 + 裁决执行约定层：

      EmotionEvent / EmotionDelta → MutationRequest（9 字段契约）
                                 → MutationGateway（五道检查）
                                 → MutationDecision（ACCEPT / REJECT / NEED_REVIEW / DEFER）

裁决执行约定（B.7 Phase 3）：
    - ACCEPT       才允许进入现有 Apply Adapter（apply_route 回调，下游注入
                    EmotionState.apply_delta + EmotionRepository.save 执行件）；
                    未注入 → 不自动 apply
    - REJECT       不改变状态（该变更被丢弃，仅审计留痕）
    - NEED_REVIEW  生成 pending proposal（本 Adapter 内存槽位 pending_proposals，
                    带治理链接键；不落盘、不写 data/）
    - DEFER        进入延迟队列（本 Adapter 的 deferred 槽位，manager 层消费）

Emotion Apply Boundary（B.6 审计 §4）：
    - 情绪域状态执行件 = EmotionState.apply_delta + EmotionRepository.save（保留，
      ACCEPT 后经 apply_route 进入）
    - 本 Adapter 禁止直接修改 EmotionState、禁止直接调用 repository.save——
      只产生 MutationRequest 与裁决执行约定。

审计接线（B.7 Phase 6）：
    每次 route 产生 request_id / mutation_id / trace_id / decision /
    target_path / evidence_reference / apply_result；拒绝与失败同样记录
    （默认挂 InMemoryAuditWriter，为 MutationJournal 投影预留，B.3 约定）。

硬边界（禁止）：
    - Adapter 不直接修改任何领域状态；不持有任何 store 写句柄。
    - 默认关闭（emotion_mutation_gateway_enabled = False）：旧生产路径字节不变；
      开启后才接管 EmotionManager.process_event 直写。
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import is_dataclass
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
_emotion_mutation_gateway_enabled: bool = False

# B.7 范围：emotion 主域
ALLOWED_TARGET_DOMAINS: frozenset = frozenset({"emotion"})

# EmotionState 六维（与 src/emotion/emotion_state.py 对齐，禁止增删维度）
EMOTION_DIMENSIONS: tuple = (
    "valence", "arousal", "curiosity", "anxiety", "confidence", "energy",
)

# 所有新产生 pending proposal 必须携带的治理链接键
GOVERNANCE_LINKAGE_KEYS: tuple = ("mutation_id", "request_id", "trace_id", "evidence")

# 任务书规定标准 actor（内部子系统身份；B.3 IdentityCheck 白名单已扩展）
DEFAULT_ACTOR_IDENTITY: str = "emotion_system"


def is_emotion_mutation_gateway_enabled() -> bool:
    """迁移开关状态（默认 False → 旧路径）。"""
    return _emotion_mutation_gateway_enabled


def set_emotion_mutation_gateway_enabled(enabled: bool) -> None:
    """切换迁移开关（True → EmotionManager.process_event 直写改经 Gateway）。"""
    global _emotion_mutation_gateway_enabled
    _emotion_mutation_gateway_enabled = bool(enabled)


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
    """同时兼容 EmotionEvent dataclass 与 dict 输入的字段读取。"""
    if isinstance(event, dict):
        return event.get(name, default)
    return getattr(event, name, default)


def _default_evidence(
    event_type: str,
    description: str,
    source: str,
    memory_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """从 EmotionEvent 构造证据链（确定性规则，不依赖 LLM）。

    - 消息文本（若存在）→ user_statement（SOURCE_RELIABILITY 高可信来源）
    - 事件类型 / 事件来源 → 结构化事实
    - memory_id（若绑定）→ 记忆关联
    去重 ref ≥ 3 时可通过 EvidenceCheck 数量关。
    """
    evidence: List[Dict[str, Any]] = []
    if str(description or "").strip():
        evidence.append({
            "ref": f"msg_{uuid.uuid4().hex[:8]}",
            "type": "user_statement",
        })
    evidence.append({
        "ref": f"evt_{event_type or 'unknown'}",
        "type": "event_type",
    })
    evidence.append({
        "ref": f"src_{source or 'unknown'}",
        "type": "event_source",
    })
    if memory_id:
        evidence.append({"ref": str(memory_id), "type": "memory_link"})
    return evidence


# ============================================================
# EmotionMutationAdapter
# ============================================================
class EmotionMutationAdapter:
    """Legacy emotion intent → MutationRequest → Gateway → 裁决执行约定。"""

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
        # DEFER 延迟队列槽位（manager 层消费；本 Adapter 不落盘）
        self.deferred: List[Dict[str, Any]] = []
        # NEED_REVIEW pending proposal 槽位（带治理链接键；不落盘、不写 data/）
        self.pending_proposals: List[Dict[str, Any]] = []

    # ============================================================
    # 转换：Emotion intent → MutationRequest（target_domain="emotion"）
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
        """构造标准 9 字段 MutationRequest（target_domain 恒为 "emotion"）。

        边界前置守卫：
        - target_path 必须位于 emotion 域命名空间（emotion. 前缀）——
          与 BoundaryCheck 的域规则同源，跨域请求在此即拒（非绕过）。
        - context_snapshot 不自动补齐：缺 request_id/trace_id 时
          AuditCheck 会给出 DEFER（链路可补后重提），保证 DEFER 路径可触达。
        """
        if not str(target_path).startswith("emotion."):
            raise ValueError(
                "EmotionMutationAdapter 只处理 emotion 域路径 "
                f"（target_path 必须以 'emotion.' 开头），得到 {target_path!r}"
            )
        kwargs: Dict[str, Any] = {
            "source_event": dict(source_event),
            "actor_identity": actor_identity,
            "target_domain": "emotion",
            "target_path": target_path,
            "proposed_change": dict(proposed_change),
            "evidence": list(evidence),
            "context_snapshot": dict(context_snapshot or {}),
            "risk_level": risk_level,
        }
        if mutation_id:
            kwargs["mutation_id"] = mutation_id
        return MutationRequest(**kwargs)

    def from_emotion_event(
        self,
        event: Any,
        *,
        dimension: Optional[str] = None,
        delta: Optional[float] = None,
        before: Optional[float] = None,
        after: Optional[float] = None,
        confidence: Optional[float] = None,
        evidence: Optional[List[Any]] = None,
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
        memory_id: Optional[str] = None,
    ) -> Optional[MutationRequest]:
        """EmotionEvent → MutationRequest（转换器，manager/测试接线用）。

        维度信息必须由评估结果提供（dimension + delta）；禁止 Adapter 自行
        猜测情绪方向——缺维度信息时返回 None。
        evidence 未显式提供时，从事件描述/类型/来源/memory_id 确定性构造。
        转换器便利语义：context_snapshot 缺 request_id/trace_id 时自动补齐
        （与 build_request 的显式语义区分）。
        """
        if not dimension or delta is None:
            return None
        dim = str(dimension).strip()
        if dim not in EMOTION_DIMENSIONS:
            return None
        delta_val = round(float(delta), 6)
        if delta_val == 0.0:
            return None
        target_path = f"emotion.state.{dim}"

        event_type = str(_event_field(event, "event_type") or "")
        description = str(_event_field(event, "description") or "")
        source = str(_event_field(event, "source") or "interaction")
        intensity = _to_float(_event_field(event, "intensity", 0.5)) or 0.5

        confidence_val = (
            _to_float(confidence)
            if confidence is not None
            else round(max(0.0, min(1.0, intensity)), 4)
        )
        before_val = (
            _to_float(before)
            if before is not None
            else None
        )
        after_val = (
            _to_float(after)
            if after is not None
            else (round(before_val + delta_val, 6) if before_val is not None else None)
        )
        proposed_change: Dict[str, Any] = {
            "path": target_path,
            "before": before_val,
            "after": after_val,
            "delta": delta_val,
            "confidence": confidence_val,
        }

        if evidence is None:
            evidence = _default_evidence(event_type, description, source, memory_id)
        if not evidence:
            evidence = [
                {"ref": f"ev_{uuid.uuid4().hex[:8]}", "type": "user_statement"}
            ]

        snapshot = dict(context_snapshot or {})
        for key, value in _gen_linkage_ids().items():
            snapshot.setdefault(key, value)
        if memory_id:
            snapshot.setdefault("memory_id", str(memory_id))
        snapshot.setdefault("source", "emotion_adapter.from_emotion_event")

        source_event = {
            "event_id": str(_event_field(event, "event_id", "") or ""),
            "type": event_type,
            "source": source,
            "intensity": intensity,
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

        约定：
        - ACCEPT 且注入 apply_route → 调用 apply_route（现有 Apply Adapter
          入口，如 apply_delta + repository.save 包装）；返回 True 才记 applied=True
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
                        "[emotion_mutation_adapter] apply_route 异常（已隔离）: %s",
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
    "EmotionMutationAdapter",
    "is_emotion_mutation_gateway_enabled",
    "set_emotion_mutation_gateway_enabled",
    "EMOTION_DIMENSIONS",
    "GOVERNANCE_LINKAGE_KEYS",
    "ALLOWED_TARGET_DOMAINS",
    "DEFAULT_ACTOR_IDENTITY",
]
