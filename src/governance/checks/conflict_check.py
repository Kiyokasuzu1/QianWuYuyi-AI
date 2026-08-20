# -*- coding: utf-8 -*-
"""
ConflictCheck —— 第 4 道：是否与已有人格/已受理提案冲突（B.2 §3.4.4）

复用已有组件（禁止重新实现规则）：
- src.approval.approval_policy 阈值常量：
    DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD（0.15）/
    DEFAULT_CONFLICT_REVERSAL_MIDPOINT（0.5）
- src.growth.growth_schema.MAX_GROWTH_PER_DIMENSION（0.15，年度上限）
- src.growth.proposal_store.compute_fingerprint（纯函数）+ 注入的
  proposal_store.exists_similar（重复提案检测，B.1 去重语义）

判定规则：
1. 倒转冲突：before/after 跨越 0.5 中点且 |Δ|≥0.15 → NEED_REVIEW
2. 年度上限：context_snapshot.annual_delta + |Δ| > 0.15 → DEFER
3. 重复提案：source_event_id + fingerprint 命中已存提案 → DEFER
   （proposal_store 协作件未注入时跳过去重，不视为失败）

注意：本检查不实例化任何真实存储；proposal_store 由未来接线方注入，
避免在治理层产生 data/proposals 文件副作用。
"""
from __future__ import annotations

from typing import Any, Optional

from src.approval.approval_policy import (
    DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD,
    DEFAULT_CONFLICT_REVERSAL_MIDPOINT,
)
from src.governance.checks.base import BaseCheck, fail, ok
from src.governance.mutation_contract import CheckResult, MutationRequest
from src.growth.growth_schema import MAX_GROWTH_PER_DIMENSION
from src.growth.proposal_store import compute_fingerprint


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ConflictCheck(BaseCheck):
    name = "conflict"

    def __init__(self, proposal_store: Any = None) -> None:
        # 协作件：需提供 exists_similar(source_event_id, fingerprint) -> Optional[str]
        self.proposal_store = proposal_store

    def check(self, request: MutationRequest) -> CheckResult:
        change = request.proposed_change
        before = _to_float(change.get("before"))
        after = _to_float(change.get("after"))
        numeric = before is not None and after is not None
        delta = (after - before) if numeric else None

        # 1) 倒转冲突
        if numeric and delta is not None:
            cross_midpoint = (
                (before < DEFAULT_CONFLICT_REVERSAL_MIDPOINT <= after)
                or (before > DEFAULT_CONFLICT_REVERSAL_MIDPOINT >= after)
            )
            if cross_midpoint and abs(delta) >= DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD:
                return fail(
                    f"conflict_with_existing_trait_reversal: {request.target_path} "
                    f"{before:.3f}→{after:.3f} 跨越中点且 |Δ|={abs(delta):.3f} ≥ "
                    f"{DEFAULT_CONFLICT_REVERSAL_DELTA_THRESHOLD}",
                    verdict="NEED_REVIEW",
                    metadata={"path": request.target_path, "before": before,
                              "after": after, "delta": delta},
                )

            # 2) 年度上限
            annual = _to_float(request.context_snapshot.get("annual_delta"))
            if annual is not None and abs(delta) + annual > MAX_GROWTH_PER_DIMENSION:
                return fail(
                    f"annual_limit_exceeded_deferred: |Δ|={abs(delta):.3f} + "
                    f"年度累计 {annual:.3f} > {MAX_GROWTH_PER_DIMENSION}",
                    verdict="DEFER",
                    metadata={"path": request.target_path, "delta": delta,
                              "annual_delta": annual},
                )

        # 3) 重复提案检测（复用 compute_fingerprint + exists_similar）
        event_id = str(
            request.source_event.get("event_id")
            or request.source_event.get("id")
            or ""
        )
        dedupe_checked = self.proposal_store is not None and bool(event_id)
        if dedupe_checked:
            fingerprint = compute_fingerprint({
                "source_event_id": event_id,
                "proposed_changes": [{
                    "path": request.target_path,
                    "before": before if numeric else change.get("before"),
                    "after": after if numeric else change.get("after"),
                }],
            })
            existing = self.proposal_store.exists_similar(event_id, fingerprint)
            if existing:
                return fail(
                    f"duplicate_proposal: 与已受理提案 {existing} 重复",
                    verdict="DEFER",
                    metadata={"existing_id": existing, "source_event_id": event_id},
                )

        return ok(
            "conflict_ok",
            {
                "numeric_check": numeric,
                "delta": delta,
                "dedupe_checked": dedupe_checked,
            },
        )
