# -*- coding: utf-8 -*-
"""
src/runtime/self_model/persistence/__init__.py

Phase 4.6: Self Model Persistence & Evolution History Layer —— 模块入口。

职责:
- 把 SelfModelSnapshot 持久化到磁盘(默认 JSON 后端, atomic write)
- 把 SelfModelEvolutionRecord 追加到 JSONL 风格 history(append-only)
- 提供 snapshot checkpoint / rollback 能力
- 启动时 restore_on_startup 恢复最新 SelfModel

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 不修改 RuntimeContext.schema_version
- 不修改 ResponseEngine.generate()
- 保持单向依赖:
    Runtime
      ↓
    SelfModel Persistence Service
      ↓
    SelfModel Snapshot / EvolutionRecord
- 所有新代码均带单元测试

兼容性:
- 未注入 persistence_runtime 时 SELF_MODEL_PERSISTENCE 阶段完全 no-op
- 未注入 persistence_runtime 时 restore_on_startup 返回 None
"""

from __future__ import annotations

from src.runtime.self_model.persistence.self_model_store import (
    SelfModelStore,
    SELF_MODEL_STORE_SCHEMA_VERSION,
    DEFAULT_SELF_MODEL_DIR,
    MAX_SNAPSHOT_FILE_BYTES,
)
from src.runtime.self_model.persistence.evolution_history_store import (
    EvolutionHistoryStore,
    EVOLUTION_HISTORY_STORE_SCHEMA_VERSION,
    DEFAULT_EVOLUTION_HISTORY_DIR,
    MAX_HISTORY_FILE_BYTES,
)
from src.runtime.self_model.persistence.snapshot_manager import (
    SnapshotManager,
    SNAPSHOT_MANAGER_SCHEMA_VERSION,
    CHECKPOINT_PREFIX,
    CHECKPOINT_FORMAT_VERSION,
)
from src.runtime.self_model.persistence.persistence_runtime import (
    PersistenceRuntime,
    PERSISTENCE_RUNTIME_SCHEMA_VERSION,
)


__all__ = [
    # Store
    "SelfModelStore",
    "SELF_MODEL_STORE_SCHEMA_VERSION",
    "DEFAULT_SELF_MODEL_DIR",
    "MAX_SNAPSHOT_FILE_BYTES",
    # History
    "EvolutionHistoryStore",
    "EVOLUTION_HISTORY_STORE_SCHEMA_VERSION",
    "DEFAULT_EVOLUTION_HISTORY_DIR",
    "MAX_HISTORY_FILE_BYTES",
    # Manager
    "SnapshotManager",
    "SNAPSHOT_MANAGER_SCHEMA_VERSION",
    "CHECKPOINT_PREFIX",
    "CHECKPOINT_FORMAT_VERSION",
    # Coordinator
    "PersistenceRuntime",
    "PERSISTENCE_RUNTIME_SCHEMA_VERSION",
]
