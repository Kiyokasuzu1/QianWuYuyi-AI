# -*- coding: utf-8 -*-
"""用户身份权限门（P2.1.3-A Permission Gate Foundation / P2.1.3-R Relationship Gate）。

职责（单一）：
    基于 Identity 判断"该身份是否允许修改羽依的某类状态"。
    为 P2.1.3 后续在 memory / emotion / self_model / growth / relationship
    状态更新链接入沙盒隔离提供统一判断层。

概念区分（防混淆）：
    src/permission/     = 羽依能力阶段治理（Phase 3.5：羽依自身成长阶段
                          允许什么类型的操作，持久化于 data/permission/）
    src/security/permission.py = 用户身份权限门（本模块：来访身份
                          是否允许修改羽依状态，纯函数、无状态）

最小策略（P2.1.3-A / P2.1.3-R）：
    permission == "user"    → 允许 memory / emotion / personality / growth /
                              relationship
    permission == "sandbox" → 全部禁止
    其他任何值（含 "unknown"）→ 按 sandbox 处理（fail-closed）
    "admin" 为预留通道：状态修改权限同样 fail-closed（管理操作走
    admin API 自己的鉴权，不属状态修改语义）

设计约束：
    - 纯工具模块：不依赖 Runtime / Flask / Memory / Emotion / Growth /
      Personality / IdentityResolver（仅消费 Identity 的鸭子类型字段）
    - 永不抛异常：任何输入（None / 缺字段 / 任意对象）都返回安全结果
    - fail-close：无法确认 → 拒绝
    - 不修改 IdentityResolver 的任何行为
"""
from __future__ import annotations

from typing import Any, Dict

__all__ = [
    "PERMISSION_TARGETS",
    "can_modify_memory",
    "can_modify_emotion",
    "can_modify_personality",
    "can_trigger_growth",
    "can_modify_relationship",
    "get_permissions",
    "is_sandbox_like",
]

#: 权限门覆盖的状态目标（完整清单；get_permissions 的键与之严格一致）
PERMISSION_TARGETS = ("memory", "emotion", "personality", "growth", "relationship")

#: 允许修改状态的身份权限等级（白名单：仅 "user"）
_ALLOWED_STATE_PERMISSIONS = frozenset({"user"})


def _identity_permission(identity: Any) -> Any:
    """安全读取 identity.permission 字段；任何异常返回 None。"""
    try:
        return getattr(identity, "permission", None)
    except Exception:  # noqa: BLE001
        return None


def is_sandbox_like(identity: Any) -> bool:
    """是否应按沙盒处理（非 user 一律视为沙盒样本身份，fail-closed）。"""
    return _identity_permission(identity) not in _ALLOWED_STATE_PERMISSIONS


def _can_modify(identity: Any) -> bool:
    """统一判定：仅 permission=='user' 允许修改状态。"""
    try:
        return not is_sandbox_like(identity)
    except Exception:  # noqa: BLE001
        return False


def can_modify_memory(identity: Any) -> bool:
    """该身份是否允许写入/修改记忆。永不抛异常。"""
    return _can_modify(identity)


def can_modify_emotion(identity: Any) -> bool:
    """该身份是否允许触发情绪状态变更。永不抛异常。"""
    return _can_modify(identity)


def can_modify_personality(identity: Any) -> bool:
    """该身份是否允许影响人格状态。永不抛异常。"""
    return _can_modify(identity)


def can_trigger_growth(identity: Any) -> bool:
    """该身份是否允许触发成长评估（growth pipeline）。永不抛异常。"""
    return _can_modify(identity)


def can_modify_relationship(identity: Any) -> bool:
    """该身份是否允许创建/修改关系状态（trust/familiarity 等）。永不抛异常。"""
    return _can_modify(identity)


def get_permissions(identity: Any) -> Dict[str, bool]:
    """返回完整权限表（键 = PERMISSION_TARGETS，与各判定函数严格一致）。"""
    allowed = _can_modify(identity)
    return {
        "memory": allowed,
        "emotion": allowed,
        "personality": allowed,
        "growth": allowed,
        "relationship": allowed,
    }
