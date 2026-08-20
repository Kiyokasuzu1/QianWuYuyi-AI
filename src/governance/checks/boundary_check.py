# -*- coding: utf-8 -*-
"""
BoundaryCheck —— 第 2 道：修改目标是否合法（B.2 §3.4.2 + §4 六域规则）

复用已有组件（禁止重新实现规则）：
- src.approval.approval_policy.FORBIDDEN_IDENTITY_PATH_PREFIXES
  （identity.core. / identity.origin. / manifesto. 硬拒前缀，唯一事实来源）

本道规则（B.2 §4 六域表契约化）：
1. 身份锚点前缀 → REJECT（identity_violation_rejected）
2. 跨域路径（target_path 前缀与 target_domain 命名空间不符）→ REJECT
3. 域红线：
   - memory      ：删除核心记忆（action=delete 且命中 .core）→ REJECT
   - emotion     ：永久化变更（permanent=True）→ REJECT
   - relationship：直改 trust/bond → NEED_REVIEW（必须走复核队列）
   - growth      ：bypass_proposal 标记 → REJECT
   - personality / self_model：无额外红线（锚点前缀已覆盖）
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from src.approval.approval_policy import FORBIDDEN_IDENTITY_PATH_PREFIXES
from src.governance.checks.base import BaseCheck, fail, ok
from src.governance.mutation_contract import CheckResult, MutationRequest

# 六域合法路径命名空间（B.2 §4）
DOMAIN_NAMESPACES: Dict[str, Tuple[str, ...]] = {
    "memory": ("memory.", "memories."),
    "emotion": ("emotion.",),
    "relationship": ("relationship.",),
    "growth": ("growth.",),
    "personality": ("personality.",),
    "self_model": ("self_model.", "selfmodel."),
}


class BoundaryCheck(BaseCheck):
    name = "boundary"

    def check(self, request: MutationRequest) -> CheckResult:
        path = request.target_path
        domain = request.target_domain
        change = request.proposed_change

        # 1) 身份锚点硬拒（复用既有前缀清单）
        for prefix in FORBIDDEN_IDENTITY_PATH_PREFIXES:
            if path.startswith(prefix):
                return fail(
                    f"identity_violation_rejected: {path!r} 命中禁止前缀 {prefix!r}",
                    verdict="REJECT",
                    metadata={"path": path, "forbidden_prefix": prefix},
                )

        # 2) 跨域检查
        namespaces = DOMAIN_NAMESPACES[domain]
        if not any(path.startswith(ns) for ns in namespaces):
            return fail(
                f"cross_domain_target_forbidden: {path!r} 不属于 {domain} 域命名空间 {namespaces}",
                verdict="REJECT",
                metadata={"path": path, "domain": domain, "namespaces": list(namespaces)},
            )

        # 3) 域红线
        action = str(change.get("action", "") or "")
        if domain == "memory" and action == "delete" and ".core" in path:
            return fail(
                f"core_memory_delete_forbidden: 禁止删除核心记忆 {path!r}",
                verdict="REJECT",
                metadata={"path": path, "action": action},
            )
        if domain == "emotion" and change.get("permanent") is True:
            return fail(
                "emotion_permanent_personality_forbidden: 情绪变更不得永久化（禁止永久人格改变）",
                verdict="REJECT",
                metadata={"path": path},
            )
        if domain == "relationship" and (".trust" in path or ".bond" in path):
            return fail(
                f"relationship_trust_bond_requires_review: {path!r} 直改 trust/bond 禁止，"
                "必须进入关系提案复核队列",
                verdict="NEED_REVIEW",
                metadata={"path": path},
            )
        if domain == "growth" and change.get("bypass_proposal") is True:
            return fail(
                "growth_bypass_proposal_forbidden: 禁止绕过 Proposal 链的成长直通",
                verdict="REJECT",
                metadata={"path": path},
            )

        matched = next(ns for ns in namespaces if path.startswith(ns))
        return ok(
            f"boundary_ok: {path!r} 属于 {domain} 域",
            {"path": path, "domain": domain, "namespace": matched},
        )
