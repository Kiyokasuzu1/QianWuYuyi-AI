# -*- coding: utf-8 -*-
"""
IdentityCheck —— 第 1 道：谁触发 / 是否允许修改（B.2 §3.4.1）

复用已有组件（禁止重新实现规则）：
- src/security/permission.py 五扇门（can_modify_* / can_trigger_growth），
  鸭子类型读取 identity.permission，永不抛异常、fail-closed。
- B.2 §3.4.1 语义：
  · sandbox / unknown（含无法解析的身份）→ REJECT
  · user → 按域门判定（全部域允许，语义与五扇门一致）
  · admin / system → 不受 user 门限制，但必须走审计（metadata 标记
    requires_audit，由 AuditCheck 的链路要求兜底）

self_model 域不在 PERMISSION_TARGETS 五扇门清单内，按 fail-closed 语义
借用 can_modify_personality（五扇门实现共享同一 _can_modify 判定，
personality 门语义与 self_model 需求一致：仅 user 放行）。
"""
from __future__ import annotations

from typing import Any, Dict

from src.governance.checks.base import BaseCheck, fail, ok
from src.governance.mutation_contract import CheckResult, MutationRequest
from src.security.permission import (
    can_modify_emotion,
    can_modify_memory,
    can_modify_personality,
    can_modify_relationship,
    can_trigger_growth,
)

# 身份白名单（admin/system：非 user 门对象，但审计强制）
# P2.3-B.5: 追加 growth_system —— 成长子系统内部身份（GrowthMutationAdapter 的
# 标准 actor），语义与 system/gateway 同级：放行但 requires_audit=True 审计强制。
# P2.3-B.7: 追加 emotion_system —— 情绪子系统内部身份（EmotionMutationAdapter 的
# 标准 actor），语义与 system/gateway 同级：放行但 requires_audit=True 审计强制。
# P2.3-B.9: 追加 relationship_system —— 关系子系统内部身份（RelationshipMutationAdapter
# 的标准 actor），语义与 system/gateway 同级：放行但 requires_audit=True 审计强制。
# P2.3-B.13: 追加 self_model_system —— SelfModel 治理子系统内部身份
# （SelfModelMutationAdapter 的标准 actor），语义与 system/gateway 同级：
# 放行但 requires_audit=True 审计强制。
PRIVILEGED_ACTORS = frozenset({
    "admin", "system", "auto_accept", "gateway", "growth_system",
    "emotion_system", "relationship_system", "self_model_system",
})
SANDBOX_ACTORS = frozenset({"sandbox", "_unknown_sender", "unknown"})

# 域 → 权限门函数（self_model 借 personality 门，见模块 docstring）
DOMAIN_GATES: Dict[str, Any] = {
    "memory": can_modify_memory,
    "emotion": can_modify_emotion,
    "personality": can_modify_personality,
    "growth": can_trigger_growth,
    "relationship": can_modify_relationship,
    "self_model": can_modify_personality,
}


class _ActorProbe:
    """把 actor_identity 字符串包装为鸭子类型 identity（.permission 字段）。"""

    def __init__(self, permission: str) -> None:
        self.permission = permission


class IdentityCheck(BaseCheck):
    name = "identity"

    def check(self, request: MutationRequest) -> CheckResult:
        actor = request.actor_identity.strip()
        domain = request.target_domain

        if actor in SANDBOX_ACTORS:
            return fail(
                f"unauthorized_actor: {actor!r} 为沙盒/未解析身份，禁止修改 {domain}",
                verdict="REJECT",
                metadata={"actor": actor, "domain": domain},
            )

        if actor in PRIVILEGED_ACTORS:
            return ok(
                f"privileged_actor: {actor!r} 放行（审计强制）",
                {"actor": actor, "domain": domain, "requires_audit": True},
            )

        gate = DOMAIN_GATES[domain]
        probe = _ActorProbe(permission="user" if actor == "user" else actor)
        allowed = bool(gate(probe))  # 五扇门永不抛异常；非 user 一律 fail-closed
        if allowed:
            return ok(
                f"gate_allowed: {gate.__name__}({actor!r})=True",
                {"actor": actor, "domain": domain, "gate": gate.__name__, "allowed": True},
            )
        return fail(
            f"unauthorized_actor: {actor!r} 无 {domain} 域修改权限（fail-closed）",
            verdict="REJECT",
            metadata={"actor": actor, "domain": domain, "gate": gate.__name__, "allowed": False},
        )
