# -*- coding: utf-8 -*-
"""
src/experience/route_decision.py

Phase 4.0-R2.5.1: ExperienceRouteDecision 结构化对象 + AuditEvent 桥接。

职责：
    1. 定义 ExperienceRouteDecision TypedDict（Decision 结构契约）
    2. 提供 emit_route_audit(decision) → 写入 AuditEvent 体系（record_audit_log）
    3. 不包含任何业务分流逻辑（分流在 experience_bridge.py）

审计目标：
    "为什么羽依会变成这样？" → 所有经历分诊必须可追溯。
    每条 Decision 生成一条 operation_type="experience_route" 的审计记录。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Literal, Optional, TypedDict


logger = logging.getLogger(__name__)


# ============================================================
# 1. 决策分类枚举（ExperienceBridge 三通道 + 未来扩展位）
# ============================================================
# R2.5.1 三通道正式枚举：
RouteClassification = Literal[
    "growth_candidate",   # 通道 B：GrowthCandidate（送 GrowthIntegrationService）
    "relationship_memory",  # 通道 A：RelationshipMemory（R2.5.1 审计占位，R2.5.2 真写）
    "memory_only",         # 通道 C：只留记忆，不进任何下游系统（默认分支）
]

# 未来预留（R2.5.x 可扩展）
#   "world_knowledge"
#   "self_reflection_candidate"
#   "emotion_memory"
#   "curiosity_seed"


# ============================================================
# 2. Decision 契约
# ============================================================
class ExperienceRouteDecision(TypedDict, total=False):
    """ExperienceBridge.process() 的结构化返回对象。

    R2.5.1 保证字段：
      - memory_id: str (必有)
      - user_id: str (必有)
      - classified_to: RouteClassification (必有)
      - rule_triggered: str (必有, 命中的规则名，如 "EARLY_EXIT_TOO_SHORT" / "GC_IMPORTANCE_FLOOR")
      - timestamp_iso: str (UTC isoformat，必有)
      - route_result: dict | None (GrowthCandidate 时填充 process_event 返回)
      - duration_ms: int (Bridge process 总耗时)
      - error: str | None (异常隔离时填充错误信息)
      - decision_id: str (uuid，用于审计关联)
    """
    # 身份
    decision_id: str
    memory_id: str
    user_id: str

    # 分流结果（核心验收字段）
    classified_to: RouteClassification
    rule_triggered: str

    # 时间 & 性能
    timestamp_iso: str
    duration_ms: int

    # 下游结果 / 异常
    route_result: Optional[Dict[str, Any]]
    error: Optional[str]


# ============================================================
# 3. 决策工厂（保证字段完整性，避免 Bridge 散落 dict 构造）
# ============================================================
def create_decision(
    *,
    memory_id: str,
    user_id: str,
    classified_to: RouteClassification,
    rule_triggered: str,
    duration_ms: int,
    route_result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    timestamp_iso: Optional[str] = None,
    decision_id: Optional[str] = None,
) -> ExperienceRouteDecision:
    """规范化 Decision 工厂。ExperienceBridge 只通过此函数产出 Decision。"""
    ts: str = timestamp_iso
    if not ts:
        try:
            ts = datetime.now().isoformat()
        except Exception:
            ts = str(int(time.time()))
    dec: ExperienceRouteDecision = {
        "decision_id": decision_id or f"dec_{uuid.uuid4().hex[:16]}",
        "memory_id": memory_id,
        "user_id": user_id,
        "classified_to": classified_to,
        "rule_triggered": rule_triggered,
        "timestamp_iso": ts,
        "duration_ms": max(0, int(duration_ms)),
        "route_result": route_result,
        "error": error,
    }
    return dec


# ============================================================
# 4. AuditEvent 桥接：Decision → record_audit_log
# ============================================================
def emit_route_audit(decision: ExperienceRouteDecision) -> None:
    """把 Decision 写入 AuditEvent 体系（失败隔离，绝不阻塞主流程）。

    审计字段映射：
      operation_type = "experience_route"   (固定，未来可检索所有经历分诊)
      source = "experience_bridge"          (固定，标明来源模块)
      action = classified_to                (三通道分流结果)
      user_id = decision.user_id
      detail = {decision_id, rule_triggered, memory_id, route_result 摘要, error 摘要}
      result = "success" 或 "failure"（当 decision.error 有值时 failure）
      error_message = decision.error
    """
    if not isinstance(decision, dict):
        # 防御：调用方误传非 dict。静默 warning。
        try:
            logger.warning(
                "[ExperienceBridge] emit_route_audit 收到非 dict decision: %r",
                type(decision).__name__,
            )
        except Exception:  # noqa: BLE001
            # 即便是 logger 挂了也要不抛
            pass
        return
    try:
        from src.audit.record import record_audit_log

        classified: str = decision.get("classified_to", "") or ""
        rule: str = decision.get("rule_triggered", "") or ""
        route_result = decision.get("route_result")
        detail: Dict[str, Any] = {
            "decision_id": decision.get("decision_id"),
            "memory_id": decision.get("memory_id"),
            "rule_triggered": rule,
            "timestamp_iso": decision.get("timestamp_iso"),
            "duration_ms": decision.get("duration_ms"),
        }
        if route_result:
            # 只存关键审计字段，避免 detail 膨胀
            detail["route_result"] = {
                "pipeline_state": route_result.get("pipeline_state"),
                "proposal_id": route_result.get("proposal_id"),
                "proposal_status": (
                    route_result.get("proposal", {}).get("status")
                    if isinstance(route_result.get("proposal"), dict)
                    else None
                ),
                "deduped": route_result.get("pipeline_state") == "deduped",
            }

        error_val = decision.get("error")
        result_str: str = "failure" if error_val else "success"

        record_audit_log(
            operation_type="experience_route",
            source="experience_bridge",
            action=classified,
            user_id=decision.get("user_id", "") or "",
            detail=detail,
            result=result_str,
            error_message=str(error_val) if error_val else "",
        )
    except Exception as exc:  # noqa: BLE001
        # Audit 写入失败绝对不能影响主流程；只打 warning log 就结束。
        try:
            decision_id = decision.get("decision_id") if isinstance(decision, dict) else None
        except Exception:  # noqa: BLE001
            decision_id = None
        try:
            logger.warning(
                "[ExperienceBridge] emit_route_audit 失败(已隔离): %s | decision_id=%s",
                exc,
                decision_id,
            )
        except Exception:  # noqa: BLE001
            # 绝对不能从这个函数抛任何异常到 Runtime
            pass
