# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/__init__.py

Phase 4.2.4: Self Model History & Audit —— 模块入口

职责:
- 历史追踪 / 版本差异 / 成长审计

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext / Fact / Observation schema
- 不修改 ResponseEngine.generate()
- Runtime → Service → Snapshot 单向依赖
"""

from src.runtime.self_model.audit.self_model_history_store import (
    SelfModelHistoryStore,
    SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION,
    DEFAULT_HISTORY_LIMIT,
)
from src.runtime.self_model.audit.snapshot_archive import (
    SnapshotArchive,
    SNAPSHOT_ARCHIVE_SCHEMA_VERSION,
)
from src.runtime.self_model.audit.snapshot_diff_engine import (
    SnapshotDiffEngine,
    SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION,
)
from src.runtime.self_model.audit.growth_audit_record import (
    GrowthAuditRecord,
    GROWTH_AUDIT_RECORD_SCHEMA_VERSION,
    AuditSeverity,
    AuditCategory,
)
from src.runtime.self_model.audit.audit_chain import (
    AuditChain,
    AUDIT_CHAIN_SCHEMA_VERSION,
)


__all__ = [
    # Store
    "SelfModelHistoryStore",
    "SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION",
    "DEFAULT_HISTORY_LIMIT",
    # Archive
    "SnapshotArchive",
    "SNAPSHOT_ARCHIVE_SCHEMA_VERSION",
    # Diff
    "SnapshotDiffEngine",
    "SNAPSHOT_DIFF_ENGINE_SCHEMA_VERSION",
    # Audit
    "GrowthAuditRecord",
    "GROWTH_AUDIT_RECORD_SCHEMA_VERSION",
    "AuditSeverity",
    "AuditCategory",
    # Chain
    "AuditChain",
    "AUDIT_CHAIN_SCHEMA_VERSION",
]
