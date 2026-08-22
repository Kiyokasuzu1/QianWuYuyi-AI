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
from src.governance.write_path_registry import (
    DEPRECATED_WRITE_PATHS,
    LEGAL_WRITE_PATHS,
    DeprecatedWritePath,
    LegalWritePath,
    is_deprecated_write,
    is_legal_write,
    warn_deprecated_once,
)
from src.governance.state_mutation_audit import (
    DEFAULT_AUDIT_PATH,
    SCHEMA_VERSION,
    append_entry,
    read_entries,
    record_state_mutation,
)

__all__ = [
    "DecisionVerdict",
    "MutationRequest",
    "MutationDecision",
    "CheckResult",
    "RISK_LEVELS",
    "AuditWriter",
    "InMemoryAuditWriter",
    "MutationGateway",
    "LegalWritePath",
    "DeprecatedWritePath",
    "LEGAL_WRITE_PATHS",
    "DEPRECATED_WRITE_PATHS",
    "is_legal_write",
    "is_deprecated_write",
    "warn_deprecated_once",
    "DEFAULT_AUDIT_PATH",
    "SCHEMA_VERSION",
    "record_state_mutation",
    "append_entry",
    "read_entries",
]
