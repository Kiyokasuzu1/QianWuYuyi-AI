# -*- coding: utf-8 -*-
"""src/governance/checks —— Mutation Gateway 五道检查。

本包只实现"检查"语义：输入 MutationRequest，输出 CheckResult。
禁止在本包内修改任何域状态（memory/emotion/growth/personality/relationship/self_model）。
"""
from src.governance.checks.base import BaseCheck
from src.governance.checks.identity_check import IdentityCheck
from src.governance.checks.boundary_check import BoundaryCheck
from src.governance.checks.evidence_check import EvidenceCheck
from src.governance.checks.conflict_check import ConflictCheck
from src.governance.checks.audit_check import AuditCheck

__all__ = [
    "BaseCheck",
    "IdentityCheck",
    "BoundaryCheck",
    "EvidenceCheck",
    "ConflictCheck",
    "AuditCheck",
]
