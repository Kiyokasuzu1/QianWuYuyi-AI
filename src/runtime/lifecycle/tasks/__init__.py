# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/tasks/__init__.py

P2.7 Phase D-1 + P2.8 Phase D-2.0/D-4.0: 生命周期周期任务包。

约束(任务书红线):
- 任务只调用已有引擎的公开能力
- memory/emotion 只读; growth/self_model 只生成 pending 提案(零 apply)
- 不修改 MemoryStore / personality / trait / identity / self_model store
- 产出仅 LifecycleEvent 审计 + 快照/摘要/pending 提案
- 所有任务默认 disabled:本包不向任何生产入口注册任务,
  注册行为由显式装配方(测试/实验/未来接线层)通过
  LifecycleRuntime.register_task → LifecycleRegistry 完成
"""

from src.runtime.lifecycle.tasks.emotion_cycle import (
    DEFAULT_EMOTION_STATE_PATH,
    EmotionCycleTask,
)
from src.runtime.lifecycle.tasks.emotion_reflection import (
    EmotionReflectionTask,
    TASK_TYPE_EMOTION_REFLECTION,
)
from src.runtime.lifecycle.tasks.growth_cycle import (
    DEFAULT_BATCH_LIMIT,
    DEFAULT_BUDGET_MS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_PROPOSAL_STORE_PATH,
    GOVERNANCE_ORIGIN,
    GrowthCycleTask,
)
from src.runtime.lifecycle.tasks.memory_cycle import (
    DEFAULT_MEMORY_PATH,
    MemoryCycleTask,
)
from src.runtime.lifecycle.tasks.self_model_cycle import (
    DEFAULT_GROWTH_HISTORY_PATH,
    GOVERNANCE_ORIGIN as SELF_MODEL_GOVERNANCE_ORIGIN,
    SelfModelCycleTask,
    build_self_model_proposal,
)

__all__ = [
    "MemoryCycleTask",
    "DEFAULT_MEMORY_PATH",
    "EmotionCycleTask",
    "DEFAULT_EMOTION_STATE_PATH",
    "EmotionReflectionTask",
    "TASK_TYPE_EMOTION_REFLECTION",
    "GrowthCycleTask",
    "GOVERNANCE_ORIGIN",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_BUDGET_MS",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_PROPOSAL_STORE_PATH",
    "SelfModelCycleTask",
    "SELF_MODEL_GOVERNANCE_ORIGIN",
    "build_self_model_proposal",
    "DEFAULT_GROWTH_HISTORY_PATH",
]
