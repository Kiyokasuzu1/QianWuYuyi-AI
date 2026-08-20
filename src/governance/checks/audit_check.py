# -*- coding: utf-8 -*-
"""
AuditCheck —— 第 5 道：是否生成完整链路（B.2 §3.4.5）

本阶段只检查"可审计性"，不执行落账（落账由 AuditWriter 在 Gateway
层完成，B.3 Phase 5）。判定规则：

1. 重复落账：journal 协作件（需提供 has_mutation(mutation_id) -> bool）
   命中同 id → REJECT（duplicate_mutation_id）；协作件未注入时跳过。
2. 链路关联键：context_snapshot 必须携带 request_id + trace_id
   （对齐 runtime_context_contract §3.7"所有审计写入必须携带
   request_id + trace_id"）→ 缺失 DEFER（可补后重提）。
3. 通过 → 返回链路元数据（供 Gateway 落账 AuditWriter 使用）。

注意：本检查不 import、不修改 MutationJournal / AuditTrail / AuditLink
（B.3 任务书禁止触碰现有 Audit 数据结构）。
"""
from __future__ import annotations

from typing import Any

from src.governance.checks.base import BaseCheck, fail, ok
from src.governance.mutation_contract import CheckResult, MutationRequest


class AuditCheck(BaseCheck):
    name = "audit"

    def __init__(self, journal: Any = None) -> None:
        # 协作件：需提供 has_mutation(mutation_id) -> bool
        self.journal = journal

    def check(self, request: MutationRequest) -> CheckResult:
        # 1) 重复落账防护
        if self.journal is not None and callable(getattr(self.journal, "has_mutation", None)):
            if self.journal.has_mutation(request.mutation_id):
                return fail(
                    f"duplicate_mutation_id: {request.mutation_id} 已落账，禁止重复评估",
                    verdict="REJECT",
                    metadata={"mutation_id": request.mutation_id},
                )

        # 2) 链路关联键（request_id + trace_id，契约 §3.7）
        snapshot = request.context_snapshot
        request_id = str(snapshot.get("request_id") or "")
        trace_id = str(snapshot.get("trace_id") or "")
        if not request_id or not trace_id:
            return fail(
                "audit_linkage_missing: context_snapshot 缺少 request_id/trace_id，"
                "无法重建审计链路",
                verdict="DEFER",
                metadata={
                    "request_id_present": bool(request_id),
                    "trace_id_present": bool(trace_id),
                },
            )

        return ok(
            "audit_ok",
            {
                "request_id": request_id,
                "trace_id": trace_id,
                "linkage": "ok",
                "duplicate_checked": self.journal is not None,
            },
        )
