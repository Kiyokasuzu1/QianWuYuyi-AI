# -*- coding: utf-8 -*-
"""
src/growth/state_machines.py

Phase 5.5.1 Hotfix [6]: 统一 Proposal 状态机规则

目的：
- 集中管理 Type A / Type B Proposal 状态转换规则
- 禁止非法状态迁移（如 approved → pending）
- 提供状态转换验证 API

设计原则：
- 不修改原有 PROPOSAL_STATUS 常量（保持向后兼容）
- 提供新的转换规则 API
- 集中校验逻辑
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Set, Tuple

# ============================================================
# Type A 状态机（Admin Governance）
# ============================================================

TYPE_A_STATES: FrozenSet[str] = frozenset({
    "pending", "approved", "rejected", "applied", "cancelled",
})

# Type A 合法状态转换表
# key: from_state, value: set of allowed to_state
TYPE_A_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "pending": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"applied", "cancelled"}),
    "rejected": frozenset(),  # 终态
    "applied": frozenset(),  # 终态
    "cancelled": frozenset(),  # 终态
}

# Type A 终态（不可再转换）
TYPE_A_TERMINAL_STATES: FrozenSet[str] = frozenset({"rejected", "applied", "cancelled"})

# ============================================================
# Type B 状态机（Runtime Growth）
# ============================================================

TYPE_B_STATES: FrozenSet[str] = frozenset({
    "proposed", "approved", "rejected",
})

TYPE_B_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "proposed": frozenset({"approved", "rejected"}),
    "approved": frozenset(),  # 终态（已 apply）
    "rejected": frozenset(),  # 终态
}

TYPE_B_TERMINAL_STATES: FrozenSet[str] = frozenset({"approved", "rejected"})


# ============================================================
# Action Scope（Phase 5.5.1 Hotfix [3]）
# ============================================================

ACTION_SCOPE_PERSONALITY = "personality"
ACTION_SCOPE_MEMORY = "memory"
ACTION_SCOPE_RELATIONSHIP = "relationship"
ACTION_SCOPE_SELF_MODEL = "self_model"

ACTION_SCOPES: FrozenSet[str] = frozenset({
    ACTION_SCOPE_PERSONALITY,
    ACTION_SCOPE_MEMORY,
    ACTION_SCOPE_RELATIONSHIP,
    ACTION_SCOPE_SELF_MODEL,
})


# ============================================================
# 状态机工具函数
# ============================================================

class IllegalStateTransition(Exception):
    """非法状态转换异常"""
    pass


def validate_type_a_transition(from_state: str, to_state: str) -> bool:
    """
    验证 Type A 状态转换是否合法。

    Args:
        from_state: 当前状态
        to_state: 目标状态

    Returns:
        是否合法

    Raises:
        IllegalStateTransition: 非法状态转换
    """
    if from_state not in TYPE_A_STATES:
        raise IllegalStateTransition(
            f"未知 Type A 状态: {from_state}（合法值: {sorted(TYPE_A_STATES)}）"
        )
    if to_state not in TYPE_A_STATES:
        raise IllegalStateTransition(
            f"未知 Type A 状态: {to_state}（合法值: {sorted(TYPE_A_STATES)}）"
        )
    if from_state == to_state:
        # 同状态视为合法（幂等操作）
        return True
    allowed = TYPE_A_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise IllegalStateTransition(
            f"非法 Type A 状态转换: {from_state} → {to_state}（合法目标: {sorted(allowed)}）"
        )
    return True


def validate_type_b_transition(from_state: str, to_state: str) -> bool:
    """
    验证 Type B 状态转换是否合法。
    """
    if from_state not in TYPE_B_STATES:
        raise IllegalStateTransition(
            f"未知 Type B 状态: {from_state}（合法值: {sorted(TYPE_B_STATES)}）"
        )
    if to_state not in TYPE_B_STATES:
        raise IllegalStateTransition(
            f"未知 Type B 状态: {to_state}（合法值: {sorted(TYPE_B_STATES)}）"
        )
    if from_state == to_state:
        return True
    allowed = TYPE_B_TRANSITIONS.get(from_state, frozenset())
    if to_state not in allowed:
        raise IllegalStateTransition(
            f"非法 Type B 状态转换: {from_state} → {to_state}（合法目标: {sorted(allowed)}）"
        )
    return True


def is_terminal_state(state: str, schema: str = "type_a") -> bool:
    """
    判断状态是否为终态。

    Args:
        state: 状态值
        schema: "type_a" 或 "type_b"
    """
    if schema == "type_a":
        return state in TYPE_A_TERMINAL_STATES
    elif schema == "type_b":
        return state in TYPE_B_TERMINAL_STATES
    raise ValueError(f"未知 schema: {schema}")


def get_state_machine_info() -> Dict[str, any]:
    """
    返回状态机信息（用于诊断与文档）。
    """
    return {
        "type_a": {
            "states": sorted(TYPE_A_STATES),
            "transitions": {
                k: sorted(v) for k, v in TYPE_A_TRANSITIONS.items()
            },
            "terminal_states": sorted(TYPE_A_TERMINAL_STATES),
        },
        "type_b": {
            "states": sorted(TYPE_B_STATES),
            "transitions": {
                k: sorted(v) for k, v in TYPE_B_TRANSITIONS.items()
            },
            "terminal_states": sorted(TYPE_B_TERMINAL_STATES),
        },
        "action_scopes": sorted(ACTION_SCOPES),
    }
