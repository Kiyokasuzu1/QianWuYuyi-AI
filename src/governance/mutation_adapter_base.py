# -*- coding: utf-8 -*-
"""
P2.3-B.11 Phase 3 — MutationAdapterBase（统一 MutationAdapter 基础接口）

定位（B.11 任务书 Phase 3）：
    只做抽象，不迁移业务。为 Growth/Emotion/Relationship 三个既有
    MutationAdapter（B.5/B.7/B.9 复制演化产物）建立**未来迁移目标接口**。

    ⚠️ 本阶段三域 adapter 暂不继承本基类（任务书冻结）。
    既有 adapter 的旧 API 全部保持不变；本模块只作为契约声明存在，
    无任何生产调用方。

统一接口（任务书冻结五个方法）：
    - build_request()  域 intent → MutationRequest
    - validate()       构造前/路由前的域内校验（路径命名空间等）
    - route()          MutationRequest → Gateway → 裁决 envelope
    - apply()          ACCEPT 后经 apply_route 执行（禁止自动调用）
    - audit()          审计留痕查询

语义对齐说明（与三域现状的差异，Phase 1 审计 §5）：
    - 现状 route() 内联 apply_route 回调；基类把 apply 拆为独立方法，
      迁移时 route(apply_route=...) 语义由 route()+apply() 组合表达。
    - 现状 build_request 的域前缀守卫各自实现；基类 validate() 统一化。

硬边界：
    - 不导入、不修改任何域 adapter；不接 RuntimeCore；不写任何数据。
    - ABC 抽象方法不提供默认实现（防止半成品继承）。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from src.governance.mutation_contract import (
    MutationDecision,
    MutationRequest,
)


class MutationAdapterBase(ABC):
    """统一 MutationAdapter 基础接口（未来迁移目标；当前无实现方）。

    子类契约（未来三域迁移时生效）：
    1. build_request 产出标准 9 字段 MutationRequest（域前缀守卫必过）。
    2. validate 在构造前拒绝跨域路径（与 BoundaryCheck 域规则同源）。
    3. route 只做"请求 → Gateway → envelope"，无任何状态副作用。
    4. apply 仅在 decision == ACCEPT 时可被外部显式调用；禁止 route
       内部自动 apply（现状三域的 apply_route 注入语义由调用方组合）。
    5. audit 返回只读审计留痕；拒绝与失败同样留痕。
    """

    # 子类必须声明：本 adapter 管辖的 target_domain（单一域）
    target_domain: str = ""
    # 子类必须声明：合法 target_path 前缀（域命名空间，与 BoundaryCheck 同源）
    allowed_path_prefixes: tuple = ()

    @abstractmethod
    def validate(
        self,
        *,
        target_path: str,
        proposed_change: Dict[str, Any],
        evidence: List[Any],
    ) -> bool:
        """域内校验：target_path 是否属于本 adapter 命名空间。

        返回 False 的请求不得进入 build_request（前置守卫，语义与三域
        现状 ValueError 守卫一致——迁移时由子类决定抛错或返回 False）。
        """

    @abstractmethod
    def build_request(
        self,
        *,
        source_event: Dict[str, Any],
        target_path: str,
        proposed_change: Dict[str, Any],
        evidence: List[Any],
        context_snapshot: Optional[Dict[str, Any]] = None,
        risk_level: str = "low",
        actor_identity: str = "",
        mutation_id: Optional[str] = None,
    ) -> MutationRequest:
        """域 intent → 标准 9 字段 MutationRequest（target_domain 恒为本域）。"""

    @abstractmethod
    def route(
        self,
        request: MutationRequest,
    ) -> Dict[str, Any]:
        """执行治理链，返回裁决 envelope（11 键，与三域现状一致）。

        envelope：request_id / mutation_id / trace_id / target_domain /
        target_path / decision / audit_reference / applied / reason /
        note / evidence_refs。本方法不得产生任何状态副作用。
        """

    @abstractmethod
    def apply(
        self,
        request: MutationRequest,
        decision: MutationDecision,
    ) -> bool:
        """应用已被 ACCEPT 的变更（调用外部执行件）。

        守卫（子类必须实现）：
        - decision.decision != ACCEPT → 拒绝执行（返回 False 或抛错）
        - 禁止自动调用：本方法只能由外部（apply_route 组合方）显式触发
        """

    @abstractmethod
    def audit(self) -> Dict[str, List[Dict[str, Any]]]:
        """只读审计留痕（requests / decisions；拒绝与失败同样留痕）。"""


__all__ = ["MutationAdapterBase"]
