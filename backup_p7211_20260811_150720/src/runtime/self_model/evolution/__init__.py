# -*- coding: utf-8 -*-
"""
src/runtime/self_model/evolution/__init__.py

Phase 4.5: Self Model Evolution Engine —— 自我模型演化层

职责:
- 在 SelfModelFoundation / SelfReflection / SelfIdentityRuntime 之上,
  提供受策略约束的"自我演化"能力。
- 接收 GrowthProposal / ReflectionRecord 作为输入,
  生成 EvolutionRecord,可选地产生新的 SelfModelSnapshot。

核心约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 不直接修改 src.runtime.self_model.foundation (单向依赖:Evolution -> Foundation)

子模块:
- evolution_record:        EvolutionRecord / SelfModelChange / EvolutionPolicyResult
- evolution_policy:        EvolutionPolicy(允许/谨慎/禁止 + 规则评估)
- self_model_evolution_engine: SelfModelEvolutionEngine(协调器)
"""

from src.runtime.self_model.evolution.evolution_record import (
    SelfModelChange,
    EvolutionRecord,
    EvolutionSourceType,
    SelfModelEvolutionResult,
    EVOLUTION_RECORD_SCHEMA_VERSION,
)
from src.runtime.self_model.evolution.evolution_policy import (
    EvolutionPolicy,
    EvolutionVerdict,
    EvolutionPolicyDecision,
    ALLOWED_FIELDS,
    CAUTIOUS_FIELDS,
    FORBIDDEN_FIELDS,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_CAUTIOUS_MIN_CONFIDENCE,
    EVOLUTION_POLICY_SCHEMA_VERSION,
)
from src.runtime.self_model.evolution.self_model_evolution_engine import (
    SelfModelEvolutionEngine,
    SELF_MODEL_EVOLUTION_ENGINE_SCHEMA_VERSION,
)


__all__ = [
    # evolution_record
    "SelfModelChange",
    "EvolutionRecord",
    "EvolutionSourceType",
    "SelfModelEvolutionResult",
    "EVOLUTION_RECORD_SCHEMA_VERSION",
    # evolution_policy
    "EvolutionPolicy",
    "EvolutionVerdict",
    "EvolutionPolicyDecision",
    "ALLOWED_FIELDS",
    "CAUTIOUS_FIELDS",
    "FORBIDDEN_FIELDS",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_CAUTIOUS_MIN_CONFIDENCE",
    "EVOLUTION_POLICY_SCHEMA_VERSION",
    # self_model_evolution_engine
    "SelfModelEvolutionEngine",
    "SELF_MODEL_EVOLUTION_ENGINE_SCHEMA_VERSION",
]
