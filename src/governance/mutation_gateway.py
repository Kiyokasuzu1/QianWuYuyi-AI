# -*- coding: utf-8 -*-
"""
P2.3-B.3 — MutationGateway（治理决策骨架）

职责（唯一）：
    MutationRequest → Check Pipeline → MutationDecision

执行顺序固定（B.3 任务书 / B.2 §3.5）：
    1 IdentityCheck → 2 BoundaryCheck → 3 EvidenceCheck
    → 4 ConflictCheck → 5 AuditCheck

裁决规则：
- 五道全过 → ACCEPT
- 首个未通过的检查决定最终 verdict：
  · 优先采纳该检查 metadata["verdict"]（REJECT / NEED_REVIEW / DEFER）
  · 缺失时按 DEFAULT_FAIL_VERDICT 兜底
- 首失败即停（不评估后续检查，B.2 §3.5 冻结顺序）

硬边界（B.3 任务书）：
- Gateway 不修改 personality/emotion/growth/memory/relationship/self_model
  任何状态；不持有任何 repo/store 写句柄。
- 不接入现有生产路径：本阶段无生产调用方，全部由测试与后续迁移接线。
- 不替换 ApprovalManager / EvolutionPipeline / ProposalManager——
  ACCEPT 只是"允许进入现有执行组件"的信号，执行仍由现有组件完成。
- 不开启自动 apply：Gateway 输出只有决策，无任何执行副作用。

审计接线（B.3 Phase 5）：可选注入 AuditWriter；record_request 在评估前
调用一次，record_decision 在决策定型后调用一次，返回的引用回填
decision.audit_reference。审计异常一律隔离（不影响决策返回）。

提案落账（B.10 Phase 3）：可选注入 ProposalStore（GovernanceProposalStore
等提供 record_pending(request, decision) 的协作件）。verdict 为 NEED_REVIEW
时自动调用 save_pending_proposal 双写落账：
  - 落账层：proposal_store.record_pending（append-only 持久化）
  - 内存层：各域 adapter 自己的 pending_proposals 槽位保持不动（旧 API 不变）
未注入 store 时零行为变化（不创建文件、不写 data/）。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List, Optional, Tuple

from src.governance.audit_writer import AuditWriter
from src.governance.checks import (
    AuditCheck,
    BoundaryCheck,
    ConflictCheck,
    EvidenceCheck,
    IdentityCheck,
)
from src.governance.mutation_contract import (
    CheckResult,
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
)

logger = logging.getLogger(__name__)

# 检查名 → 兜底 verdict（检查未在 metadata 中给出 verdict 建议时使用）
DEFAULT_FAIL_VERDICT: Dict[str, DecisionVerdict] = {
    "identity": DecisionVerdict.REJECT,
    "boundary": DecisionVerdict.REJECT,
    "evidence": DecisionVerdict.NEED_REVIEW,
    "conflict": DecisionVerdict.NEED_REVIEW,
    "audit": DecisionVerdict.DEFER,
}

# 检查执行顺序（冻结，禁止重排）
CHECK_ORDER: Tuple[str, ...] = (
    "identity", "boundary", "evidence", "conflict", "audit",
)


class MutationGateway:
    """治理决策骨架。五道检查可注入替换，缺省使用标准实现。"""

    def __init__(
        self,
        *,
        identity_check: Any = None,
        boundary_check: Any = None,
        evidence_check: Any = None,
        conflict_check: Any = None,
        audit_check: Any = None,
        audit_writer: Optional[AuditWriter] = None,
        proposal_store: Any = None,
    ) -> None:
        defaults: Dict[str, Any] = {
            "identity": IdentityCheck(),
            "boundary": BoundaryCheck(),
            "evidence": EvidenceCheck(),
            "conflict": ConflictCheck(),
            "audit": AuditCheck(),
        }
        injected = {
            "identity": identity_check,
            "boundary": boundary_check,
            "evidence": evidence_check,
            "conflict": conflict_check,
            "audit": audit_check,
        }
        self._checks: List[Tuple[str, Any]] = [
            (name, injected[name] or defaults[name]) for name in CHECK_ORDER
        ]
        self.audit_writer = audit_writer
        # B.10 Phase 3：提案落账协作件（提供 record_pending(request, decision)）。
        # 未注入 → 不落账（零行为变化，不创建文件）。
        self.proposal_store = proposal_store

    # ============================================================
    # 入口：evaluate
    # ============================================================
    def evaluate(self, request: MutationRequest) -> MutationDecision:
        """对变更请求执行五道检查，返回四态决策。无业务状态副作用。"""
        if not isinstance(request, MutationRequest):
            raise TypeError(
                f"evaluate 需要 MutationRequest，得到 {type(request).__name__}"
            )

        if self.audit_writer is not None:
            try:
                self.audit_writer.record_request(request)
            except Exception as exc:  # noqa: BLE001 审计异常隔离
                logger.warning("[mutation_gateway] record_request 失败（已隔离）: %s", exc)

        checks: Dict[str, Dict[str, Any]] = {}
        verdict = DecisionVerdict.ACCEPT
        reason = ""

        for name, check in self._checks:
            result = self._run_check(name, check, request)
            checks[name] = result.to_dict()
            if not result.passed:
                verdict = self._resolve_fail_verdict(name, result)
                reason = result.reason
                break  # 首失败即停（B.2 §3.5 冻结顺序）

        if verdict == DecisionVerdict.ACCEPT:
            reason = "all_checks_passed"

        decision = MutationDecision(
            mutation_id=request.mutation_id,
            decision=verdict,
            reason=reason,
            checks=checks,
            audit_reference="",
        )

        if self.audit_writer is not None:
            try:
                ref = self.audit_writer.record_decision(decision)
                if ref:
                    decision = dataclasses.replace(
                        decision, audit_reference=str(ref),
                    )
            except Exception as exc:  # noqa: BLE001 审计异常隔离
                logger.warning("[mutation_gateway] record_decision 失败（已隔离）: %s", exc)

        # B.10 Phase 3：NEED_REVIEW 自动落账双写（store 未注入时为 no-op）
        if verdict == DecisionVerdict.NEED_REVIEW:
            self.save_pending_proposal(request, decision)

        return decision

    # ============================================================
    # 提案落账（B.10 Phase 3）
    # ============================================================
    def save_pending_proposal(
        self,
        request: MutationRequest,
        decision: MutationDecision,
    ) -> Optional[str]:
        """把 NEED_REVIEW 决策落账到注入的 ProposalStore，返回 proposal_id。

        - 未注入 proposal_store → 返回 None（no-op；不创建任何文件）
        - 落账异常一律隔离（不影响 verdict 返回，仅告警）
        - 内存缓存（各域 adapter 的 pending_proposals 槽位）由调用方
          adapter 的 route() 维护，本方法不触碰——双写语义：
          落账持久化（本方法） + 内存缓存（adapter 现有逻辑），旧 API 不变。
        """
        if self.proposal_store is None:
            return None
        record_pending = getattr(self.proposal_store, "record_pending", None)
        if not callable(record_pending):
            logger.warning(
                "[mutation_gateway] proposal_store 缺少 record_pending 协作方法，"
                "已跳过落账"
            )
            return None
        try:
            return str(record_pending(request, decision))
        except Exception as exc:  # noqa: BLE001 落账异常隔离
            logger.warning("[mutation_gateway] save_pending_proposal 失败（已隔离）: %s", exc)
            return None

    # ============================================================
    # 内部辅助
    # ============================================================
    @staticmethod
    def _run_check(name: str, check: Any, request: MutationRequest) -> CheckResult:
        try:
            return check.check(request)
        except Exception as exc:  # noqa: BLE001 检查异常 fail-closed
            logger.exception("[mutation_gateway] %s check 异常，按 REJECT 处理", name)
            return CheckResult(
                passed=False,
                reason=f"{name}_check_error: {exc!r}",
                metadata={"verdict": "REJECT", "error": str(exc)},
            )

    @staticmethod
    def _resolve_fail_verdict(name: str, result: CheckResult) -> DecisionVerdict:
        suggested = result.metadata.get("verdict")
        valid = {"REJECT", "NEED_REVIEW", "DEFER"}
        if suggested in valid:
            return DecisionVerdict(suggested)
        return DEFAULT_FAIL_VERDICT.get(name, DecisionVerdict.REJECT)


__all__ = ["MutationGateway", "CHECK_ORDER", "DEFAULT_FAIL_VERDICT"]
