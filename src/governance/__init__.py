# -*- coding: utf-8 -*-
"""
src/governance —— P2.3-B.3 Mutation Gateway 基础实现

本包只包含治理决策骨架（契约 / Gateway / 五道检查 / 审计接口）。
本阶段不接入任何生产路径，不修改任何域状态。
"""
from src.governance.mutation_contract import (
    CheckResult,
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
    RISK_LEVELS,
)
from src.governance.audit_writer import AuditWriter, InMemoryAuditWriter
from src.governance.mutation_gateway import MutationGateway

__all__ = [
    "DecisionVerdict",
    "MutationRequest",
    "MutationDecision",
    "CheckResult",
    "RISK_LEVELS",
    "AuditWriter",
    "InMemoryAuditWriter",
    "MutationGateway",
]
