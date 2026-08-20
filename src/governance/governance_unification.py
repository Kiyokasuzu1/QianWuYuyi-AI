# -*- coding: utf-8 -*-
"""
P2.6 治理统一开关（Phase A 占位）

- governance_unification_enabled 默认 False
- Phase A：仅占位，不读取、不改变任何逻辑（审计探针的记录与该开关无关）
- Phase B（未来）：开启后 legacy 路径改为生成 Proposal + MutationGateway 路由
"""
from __future__ import annotations

_governance_unification_enabled: bool = False


def is_governance_unification_enabled() -> bool:
    """Phase A 占位开关状态（默认 False）。"""
    return _governance_unification_enabled


def set_governance_unification_enabled(enabled: bool) -> None:
    """设置开关（测试/未来灰度用）。Phase A 无任何逻辑读取它。"""
    global _governance_unification_enabled
    _governance_unification_enabled = bool(enabled)
