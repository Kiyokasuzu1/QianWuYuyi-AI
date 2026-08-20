# -*- coding: utf-8 -*-
"""
P2.3-B.13 Phase 3 — SelfModelApplyAdapter（SelfModel 唯一域状态执行件）

定位（B.13 任务书 Phase 3）：
    MutationGateway ACCEPT 之后的唯一执行通道：

      MutationRequest ──(Gateway ACCEPT)──> SelfModelApplyAdapter ──> SelfModelStore
                                              （禁止绕过本通道直写 Store）

设计（与 B.9 apply_route 约定配套）：
    - apply(request, decision)：校验 decision.decision == ACCEPT 后，
      按 request.change_type / target_path 分发到注入的执行件
    - 执行件由外部注入（store / manager 引用），本 Adapter 自身不创建
      store、不持久化、不读取 data/——它只是"ACCEPT 后的受控执行壳"
    - 拒绝非 ACCEPT：REJECT / NEED_REVIEW / DEFER 一律 raise（fail-closed）

硬边界：
    - 仅处理 self_model 域；identity 红线路径（self_model.identity.* /
      绝对身份字段）即使带 ACCEPT 也拒绝执行（双保险——构造层已在
      SelfModelMutationAdapter 拦截）
    - core_values 邻域执行必须有 proposal 已批准凭证（decision.reason
      携带 "approved:" 前缀或 approved_proposal 注入）——对齐 B.11
      ProposalManager approve → mark_applied 语义（approve 后才能 apply）
    - 默认不接线：flag（self_model_mutation_gateway_enabled）关闭时，
      生产路径不经过本 Adapter（legacy 保持）
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.governance.mutation_contract import (
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
)

logger = logging.getLogger(__name__)

# 与 SelfModelMutationAdapter 红线定义同源（重复声明避免循环 import）
_ABSOLUTE_IDENTITY_PATHS = frozenset({
    "self_model.identity_name",
    "self_model.identity_summary",
    "self_model.creator",
    "self_model.origin",
    "self_model.manifesto",
})
_ABSOLUTE_IDENTITY_PREFIXES = ("self_model.identity.",)
_CORE_VALUES_PREFIX = "self_model.core_values."

APPROVED_MARKER = "approved:"


class ApplyRejected(RuntimeError):
    """ApplyAdapter 拒绝执行（fail-closed，不修改任何状态）。"""


class SelfModelApplyAdapter:
    """ACCEPT 裁决的受控执行壳（唯一写通道；执行件外部注入）。"""

    def __init__(
        self,
        *,
        narrative_appender: Optional[Callable[[MutationRequest], bool]] = None,
        preference_writer: Optional[Callable[[MutationRequest], bool]] = None,
        belief_appender: Optional[Callable[[MutationRequest], bool]] = None,
        rebuild_executor: Optional[Callable[[MutationRequest], bool]] = None,
        fallback_executor: Optional[Callable[[MutationRequest], bool]] = None,
    ) -> None:
        """执行件注入（全部可选；缺省返回 False = 拒绝执行）。

        - narrative_appender：growth_narratives 追加（change_type=narrative_append）
        - preference_writer：preferences 写入（preference_update）
        - belief_appender：beliefs 追加（belief_append）
        - rebuild_executor：全模型重建（rebuild，B.12 SM-01 场景）
        - fallback_executor：其余路径兜底
        """
        self.narrative_appender = narrative_appender
        self.preference_writer = preference_writer
        self.belief_appender = belief_appender
        self.rebuild_executor = rebuild_executor
        self.fallback_executor = fallback_executor

    # ============================================================
    # 唯一入口
    # ============================================================
    def apply(
        self,
        request: MutationRequest,
        decision: MutationDecision,
    ) -> bool:
        """执行已被 ACCEPT 的变更；返回是否成功写入。

        fail-closed 守卫：
        1. decision 非 ACCEPT → ApplyRejected
        2. 非 self_model 域 → ApplyRejected
        3. identity 红线路径 → ApplyRejected（双保险）
        4. core_values 路径无批准凭证 → ApplyRejected
           （凭证：decision.reason 以 "approved:" 开头——由 B.11
           ProposalManager.approve 后的 apply 调用方注入）
        """
        if not isinstance(decision, MutationDecision):
            raise ApplyRejected(f"decision 必须是 MutationDecision，得到 {type(decision).__name__}")
        if decision.decision != DecisionVerdict.ACCEPT:
            raise ApplyRejected(
                f"仅 ACCEPT 可执行，得到 {decision.decision.value}（{decision.reason}）"
            )
        if request.target_domain != "self_model":
            raise ApplyRejected(
                f"仅处理 self_model 域，得到 {request.target_domain!r}"
            )
        path = request.target_path
        if path in _ABSOLUTE_IDENTITY_PATHS or any(
            path.startswith(p) for p in _ABSOLUTE_IDENTITY_PREFIXES
        ):
            raise ApplyRejected(
                f"identity 红线：{path!r} 即使 ACCEPT 也禁止执行（绝对身份字段）"
            )
        if path.startswith(_CORE_VALUES_PREFIX):
            reason = str(decision.reason or "")
            if not reason.startswith(APPROVED_MARKER):
                raise ApplyRejected(
                    "core_values 执行必须携带批准凭证（decision.reason 以 "
                    f"'{APPROVED_MARKER}' 开头，来自 B.11 ProposalManager.approve；"
                    "禁止未批准应用）"
                )

        change_type = str(request.proposed_change.get("change_type", "") or "")
        executor = self._resolve_executor(path, change_type)
        if executor is None:
            logger.warning(
                "[self_model_apply_adapter] 无可用执行件：%s (change_type=%s)",
                path, change_type,
            )
            return False
        try:
            return bool(executor(request))
        except Exception as exc:  # noqa: BLE001 执行异常隔离
            logger.warning(
                "[self_model_apply_adapter] 执行件异常（已隔离）: %s", exc,
            )
            return False

    def _resolve_executor(
        self, path: str, change_type: str,
    ) -> Optional[Callable[[MutationRequest], bool]]:
        if change_type == "narrative_append":
            return self.narrative_appender
        if change_type == "preference_update" or path.startswith("self_model.preferences."):
            return self.preference_writer
        if change_type == "belief_append" or path.startswith("self_model.beliefs."):
            return self.belief_appender
        if change_type == "rebuild" or path == "self_model.update":
            return self.rebuild_executor
        return self.fallback_executor

    # ============================================================
    # apply_route 适配（供 SelfModelMutationAdapter.route 注入）
    # ============================================================
    def as_apply_route(self) -> Callable[[MutationRequest, MutationDecision], bool]:
        """包装为 apply_route(request, decision) 回调。"""

        def _apply_route(
            request: MutationRequest, decision: MutationDecision,
        ) -> bool:
            return self.apply(request, decision)

        return _apply_route

    # ============================================================
    # 审计凭证构造（B.11 approve → apply 桥）
    # ============================================================
    @staticmethod
    def approved_decision(
        decision: MutationDecision, reviewer: str,
    ) -> MutationDecision:
        """把 ProposalManager.approve 后的决策打上批准凭证。

        供"approve 后才能 apply"链路：NEED_REVIEW 落账 proposal →
        ProposalManager.approve(reviewer=...) → 本方法注入凭证 →
        ApplyAdapter.apply 执行 core_values 等复核邻域。
        """
        import dataclasses

        return dataclasses.replace(
            decision,
            decision=DecisionVerdict.ACCEPT,
            reason=f"{APPROVED_MARKER}{reviewer}: {decision.reason}",
        )


__all__ = ["SelfModelApplyAdapter", "ApplyRejected", "APPROVED_MARKER"]
