"""RuntimeStage 枚举与 RUNTIME_LIFECYCLE_ORDER 常量。

Phase 4.0.1 RuntimeCore 统一：将阶段定义从 runtime.py 提取为独立文件，
runtime_core.py 与 runtime.py 两处都从此文件 re-export，避免重复定义。

生命周期阶段定义（与 docs/runtime.md §4 一致,经多次扩展冻结）:
- Phase 3.7.3: 基础 7 阶段 (Receive Event → Response)
- Phase 3.8.0: 新增 PERSONALITY_CONTEXT_BUILD
- Phase 3.8.4: 新增 RESPONSE_GENERATION + GUARD_CHAIN
- Phase 4.1.0~4.7: Perception / Self Model 系列阶段(默认 no-op)
"""
from __future__ import annotations

from enum import Enum
from typing import List


class RuntimeStage(str, Enum):
    """Runtime 生命周期阶段(v1.0 冻结,经多次扩展)。

    说明:
    - START/LOAD_STATE/PERSISTENCE/SHUTDOWN 为生命周期元阶段,不在 process() 遍历中;
    - process() 真正跑的阶段从 RECEIVE_EVENT 到 RESPONSE(共 17 个有效阶段);
    - PERCEPTION_* / SELF_MODEL_* 默认 no-op,未注入对应 registry/adapter 时跳过。
    """

    START = "start"
    LOAD_STATE = "load_state"
    CONTROL_CHECK = "control_check"
    RECEIVE_EVENT = "receive_event"
    MEMORY_RETRIEVAL = "memory_retrieval"
    EMOTION_UPDATE = "emotion_update"
    GROWTH_EVALUATION = "growth_evaluation"
    PERSONALITY_UPDATE = "personality_update"
    PERSONALITY_CONTEXT_BUILD = "personality_context_build"
    PERCEPTION_OBSERVATION = "perception_observation"
    PERCEPTION_ANALYSIS = "perception_analysis"
    SELF_MODEL_BUILD = "self_model_build"
    SELF_MODEL_EVOLUTION = "self_model_evolution"
    SELF_MODEL_REFLECTION = "self_model_reflection"
    SELF_MODEL_VALIDATION = "self_model_validation"
    SELF_MODEL_PERSISTENCE = "self_model_persistence"
    RESPONSE_GENERATION = "response_generation"
    GUARD_CHAIN = "guard_chain"
    RESPONSE = "response"
    PERSISTENCE = "persistence"
    SHUTDOWN = "shutdown"


# process(event, ctx) 遍历的阶段顺序(共 18 个,含 CONTROL_CHECK 前置)
# spec.md §2.2 定义的 17 阶段调度(此处包含 CONTROL_CHECK 作为 stage 0)
RUNTIME_LIFECYCLE_ORDER: List[RuntimeStage] = [
    RuntimeStage.CONTROL_CHECK,
    RuntimeStage.RECEIVE_EVENT,
    RuntimeStage.MEMORY_RETRIEVAL,
    RuntimeStage.EMOTION_UPDATE,
    RuntimeStage.GROWTH_EVALUATION,
    RuntimeStage.PERSONALITY_UPDATE,
    RuntimeStage.PERSONALITY_CONTEXT_BUILD,
    RuntimeStage.PERCEPTION_OBSERVATION,
    RuntimeStage.PERCEPTION_ANALYSIS,
    RuntimeStage.SELF_MODEL_BUILD,
    RuntimeStage.SELF_MODEL_EVOLUTION,
    RuntimeStage.SELF_MODEL_REFLECTION,
    RuntimeStage.SELF_MODEL_VALIDATION,
    RuntimeStage.SELF_MODEL_PERSISTENCE,
    RuntimeStage.RESPONSE_GENERATION,
    RuntimeStage.GUARD_CHAIN,
    RuntimeStage.RESPONSE,
]


# 与 runtime.py L110 原常量保持兼容:start/load_state/persistence/shutdown
# 保留供 runtime.py 兼容壳层自建模式使用(参考 spec 要求 runtime.py 原有代码不删除)
RUNTIME_LIFECYCLE_ORDER_FULL: List[RuntimeStage] = [
    RuntimeStage.START,
    RuntimeStage.LOAD_STATE,
] + RUNTIME_LIFECYCLE_ORDER + [
    RuntimeStage.PERSISTENCE,
    RuntimeStage.SHUTDOWN,
]
