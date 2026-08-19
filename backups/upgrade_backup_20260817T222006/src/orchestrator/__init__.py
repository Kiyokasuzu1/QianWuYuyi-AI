# -*- coding: utf-8 -*-
"""
src/orchestrator/__init__.py

Phase 5.0-A 兼容层:

历史原因 src/orchestrator.py (文件) 与 src/orchestrator/ (包) 同名。
Python 优先用包,因此在创建本包后,旧的
    from src.orchestrator import Orchestrator
会因命中本包(原本空)而失败。

本 __init__ 通过 importlib 显式加载 src/orchestrator.py 文件,
把 Orchestrator (以及 Phase 5.0-A 新增模块) 全部 re-export,
保持旧导入路径与新模块共存。
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _load_legacy_orchestrator_module() -> types.ModuleType:
    """加载 src/orchestrator.py 文件为独立模块,避免命名冲突。"""
    here = Path(__file__).resolve()
    target = here.parent.parent / "orchestrator.py"
    if not target.exists():
        raise ImportError(f"无法定位遗留的 orchestrator.py: {target}")

    cache_key = "src._legacy_orchestrator_file"
    cached = sys.modules.get(cache_key)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        cache_key, str(target),
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法为 {target} 创建 import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[cache_key] = module
    spec.loader.exec_module(module)
    return module


# 1) 加载遗留 Orchestrator 类
_legacy = _load_legacy_orchestrator_module()
Orchestrator = getattr(_legacy, "Orchestrator", None)
if Orchestrator is None:
    raise ImportError("src.orchestrator.py 中未找到 Orchestrator 类")


# 2) 暴露 Phase 5.0-A 新模块
from .self_model_orchestrator import (  # noqa: E402,F401
    SelfModelOrchestrator,
    SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION,
)
from .long_loop import (  # noqa: E402,F401
    LongLoop,
    LongLoopState,
    LONG_LOOP_SCHEMA_VERSION,
)
from .runtime_snapshot import (  # noqa: E402,F401
    RuntimeSnapshot,
    RuntimeSnapshotStore,
    RuntimeSnapshotBuilder,
    RUNTIME_SNAPSHOT_SCHEMA_VERSION,
    DEFAULT_RUNTIME_SNAPSHOT_PATH,
    DEFAULT_IDENTITY_ID,
    SOURCE_INITIAL,
    SOURCE_FRESH,
    SOURCE_RESTORE,
    SOURCE_PERIODIC,
    SOURCE_FINAL,
    SOURCE_CHECKPOINT,
)


__all__ = [
    "Orchestrator",
    "SelfModelOrchestrator",
    "LongLoop",
    "LongLoopState",
    "RuntimeSnapshot",
    "RuntimeSnapshotStore",
    "RuntimeSnapshotBuilder",
    "SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION",
    "LONG_LOOP_SCHEMA_VERSION",
    "RUNTIME_SNAPSHOT_SCHEMA_VERSION",
    "DEFAULT_RUNTIME_SNAPSHOT_PATH",
    "DEFAULT_IDENTITY_ID",
    "SOURCE_INITIAL",
    "SOURCE_FRESH",
    "SOURCE_RESTORE",
    "SOURCE_PERIODIC",
    "SOURCE_FINAL",
    "SOURCE_CHECKPOINT",
]
