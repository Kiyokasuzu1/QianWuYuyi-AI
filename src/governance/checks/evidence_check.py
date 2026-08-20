# -*- coding: utf-8 -*-
"""
EvidenceCheck —— 第 3 道：是否有事实依据 / 是否只是单次事件（B.2 §3.4.3）

复用已有组件（禁止重新实现规则）：
- src.approval.approval_policy 阈值常量：
    DEFAULT_MIN_EVIDENCE_COUNT（3）/ DEFAULT_MIN_EVALUATOR_CONFIDENCE（0.75）/
    DEFAULT_MIN_OCCURRENCE_COUNT（2）
- src.growth.growth_schema 常量：
    SOURCE_RELIABILITY（user_behavior=1.0 > user_statement=0.8 >
    llm_inference=0.4 > context_guess=0.2）
    MAX_SINGLE_EVENT_DELTA（0.01）

判定规则（证据不足 → NEED_REVIEW，与 B.3 任务书测试 #8 一致；
B.2 §3.4.3 原为 DEFER，差异见 implementation_report §3）：
1. 去重证据 ref 数 < 3 → NEED_REVIEW
2. 证据类型全部属于 {llm_inference, context_guess} → NEED_REVIEW
   （LLM 推断不能单独构成依据，对齐 B.2 §6 第 5 条禁止项）
3. 单次事件（occurrence_count < 2）且 risk_level != low → NEED_REVIEW
4. confidence 低于 0.75 → NEED_REVIEW
5. |delta| 超过单次事件上限 0.01 → NEED_REVIEW
"""
from __future__ import annotations

from typing import Any, Optional, Set

from src.approval.approval_policy import (
    DEFAULT_MIN_EVALUATOR_CONFIDENCE,
    DEFAULT_MIN_EVIDENCE_COUNT,
    DEFAULT_MIN_OCCURRENCE_COUNT,
)
from src.governance.checks.base import BaseCheck, fail, ok
from src.governance.mutation_contract import CheckResult, MutationRequest
from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA, SOURCE_RELIABILITY

# LLM/猜测类证据不可单独成立（SOURCE_RELIABILITY 中低可信来源）
WEAK_EVIDENCE_TYPES: Set[str] = {"llm_inference", "context_guess"}


def _evidence_types(evidence: Any) -> Set[str]:
    types: Set[str] = set()
    for item in evidence or []:
        if isinstance(item, dict):
            t = item.get("type")
            if isinstance(t, str) and t:
                types.add(t)
    return types


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class EvidenceCheck(BaseCheck):
    name = "evidence"

    def check(self, request: MutationRequest) -> CheckResult:
        evidence = request.evidence
        refs = [
            str(item.get("ref"))
            for item in evidence
            if isinstance(item, dict) and item.get("ref")
        ]
        distinct_refs = len(set(refs))

        # 1) 证据数量
        if distinct_refs < DEFAULT_MIN_EVIDENCE_COUNT:
            return fail(
                f"insufficient_evidence_needs_review: 去重证据 {distinct_refs} < "
                f"{DEFAULT_MIN_EVIDENCE_COUNT}",
                verdict="NEED_REVIEW",
                metadata={"evidence_count": distinct_refs,
                          "min_required": DEFAULT_MIN_EVIDENCE_COUNT},
            )

        # 2) LLM 推断不可单独成立
        types = _evidence_types(evidence)
        if types and types.issubset(WEAK_EVIDENCE_TYPES):
            return fail(
                f"llm_evidence_cannot_stand_alone: 证据类型 {sorted(types)} 均为低可信来源，"
                "需要行为/陈述类证据佐证",
                verdict="NEED_REVIEW",
                metadata={"evidence_types": sorted(types)},
            )

        # 3) 单次事件
        occurrence_raw = (
            request.source_event.get("occurrence_count")
            or request.context_snapshot.get("occurrence_count")
        )
        occurrence = int(occurrence_raw) if occurrence_raw is not None else 1
        if occurrence < DEFAULT_MIN_OCCURRENCE_COUNT and request.risk_level != "low":
            return fail(
                f"single_event_evidence_needs_review: occurrence={occurrence} < "
                f"{DEFAULT_MIN_OCCURRENCE_COUNT} 且 risk_level={request.risk_level}",
                verdict="NEED_REVIEW",
                metadata={"occurrence_count": occurrence},
            )

        # 4) 置信度
        confidence = _to_float(
            request.proposed_change.get("confidence")
            or request.source_event.get("confidence")
        )
        if confidence is not None and confidence < DEFAULT_MIN_EVALUATOR_CONFIDENCE:
            return fail(
                f"confidence_below_threshold_needs_review: confidence={confidence:.3f} < "
                f"{DEFAULT_MIN_EVALUATOR_CONFIDENCE}",
                verdict="NEED_REVIEW",
                metadata={"confidence": confidence},
            )

        # 5) 单次事件变化幅度
        delta = _to_float(request.proposed_change.get("delta"))
        if delta is not None and abs(delta) > MAX_SINGLE_EVENT_DELTA:
            return fail(
                f"delta_oversized_needs_review: |delta|={abs(delta):.4f} > "
                f"单次事件上限 {MAX_SINGLE_EVENT_DELTA}",
                verdict="NEED_REVIEW",
                metadata={"delta": delta, "max_single_event_delta": MAX_SINGLE_EVENT_DELTA},
            )

        return ok(
            "evidence_ok",
            {
                "evidence_count": distinct_refs,
                "evidence_types": sorted(types),
                "occurrence_count": occurrence,
                "confidence": confidence,
                "delta": delta,
                "reliability_weights": dict(SOURCE_RELIABILITY),
            },
        )
