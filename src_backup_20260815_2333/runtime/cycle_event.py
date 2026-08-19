# -*- coding: utf-8 -*-
"""
src/runtime/cycle_event.py

Phase C.1 Runtime Core Integration Layer —— Cycle Event 常量

定义 Runtime Cycle 编排层专用的事件类型。
不修改 src/runtime/events.py 的已有 EVENT_TYPE_*。

约束:
  - 全部常量
  - 全部为字符串(可直接发到 EventBus)
  - 提供 ALL_CYCLE_EVENTS 元组 + STAGE_TO_EVENT 映射
  - 工具函数:is_cycle_event / normalize_cycle_event
"""
from __future__ import annotations

from typing import Dict, Tuple


# ============================================================
# Cycle 事件类型(Phase C.1)
# ============================================================

CYCLE_EVENT_STARTED = "cycle_started"
CYCLE_EVENT_MEMORY_COMPLETED = "cycle_memory_completed"
CYCLE_EVENT_EMOTION_COMPLETED = "cycle_emotion_completed"
CYCLE_EVENT_PERSONALITY_COMPLETED = "cycle_personality_completed"
CYCLE_EVENT_RELATIONSHIP_COMPLETED = "cycle_relationship_completed"
CYCLE_EVENT_GROWTH_COMPLETED = "cycle_growth_completed"
CYCLE_EVENT_DECISION_COMPLETED = "cycle_decision_completed"
CYCLE_EVENT_COMPLETED = "cycle_completed"
CYCLE_EVENT_FAILED = "cycle_failed"

# Optional aliases(便于其他模块对齐 B 阶段命名)
CYCLE_EVENT_MEMORY_RETRIEVED = "cycle_memory_completed"
CYCLE_EVENT_EMOTION_UPDATED = "cycle_emotion_completed"
CYCLE_EVENT_PERSONALITY_UPDATED = "cycle_personality_completed"
CYCLE_EVENT_RELATIONSHIP_UPDATED = "cycle_relationship_completed"
CYCLE_EVENT_GROWTH_TRIGGERED = "cycle_growth_completed"
CYCLE_EVENT_DECISION_CREATED = "cycle_decision_completed"
CYCLE_EVENT_OUTCOME_RECEIVED = "cycle_completed"


ALL_CYCLE_EVENTS: Tuple[str, ...] = (
    CYCLE_EVENT_STARTED,
    CYCLE_EVENT_MEMORY_COMPLETED,
    CYCLE_EVENT_EMOTION_COMPLETED,
    CYCLE_EVENT_PERSONALITY_COMPLETED,
    CYCLE_EVENT_RELATIONSHIP_COMPLETED,
    CYCLE_EVENT_GROWTH_COMPLETED,
    CYCLE_EVENT_DECISION_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    CYCLE_EVENT_FAILED,
)


# ============================================================
# Persistence stage 常量(对接 B.5 ActionPersistenceManager)
# ============================================================

PHASE_C1_STAGE_CYCLE_STARTED = "phase_c1_cycle_started"
PHASE_C1_STAGE_CYCLE_COMPLETED = "phase_c1_cycle_completed"
PHASE_C1_STAGE_CYCLE_FAILED = "phase_c1_cycle_failed"
PHASE_C1_STAGE_STEP_COMPLETED = "phase_c1_step_completed"
PHASE_C1_STAGE_ADAPTER_FAILED = "phase_c1_adapter_failed"

ALL_PHASE_C1_STAGES: Tuple[str, ...] = (
    PHASE_C1_STAGE_CYCLE_STARTED,
    PHASE_C1_STAGE_CYCLE_COMPLETED,
    PHASE_C1_STAGE_CYCLE_FAILED,
    PHASE_C1_STAGE_STEP_COMPLETED,
    PHASE_C1_STAGE_ADAPTER_FAILED,
)


# ============================================================
# Standard step → event 映射(5 步标准 cycle)
# ============================================================

# 与 src/runtime/cycle_adapter.py 的 STANDARD_ADAPTERS_IN_ORDER 对齐
STANDARD_STEPS_IN_ORDER: Tuple[str, ...] = (
    "memory",
    "emotion",
    "personality",
    "relationship",
    "growth",
)

STEP_TO_EVENT: Dict[str, str] = {
    "memory": CYCLE_EVENT_MEMORY_COMPLETED,
    "emotion": CYCLE_EVENT_EMOTION_COMPLETED,
    "personality": CYCLE_EVENT_PERSONALITY_COMPLETED,
    "relationship": CYCLE_EVENT_RELATIONSHIP_COMPLETED,
    "growth": CYCLE_EVENT_GROWTH_COMPLETED,
    "decision": CYCLE_EVENT_DECISION_COMPLETED,
}


# ============================================================
# Schema 版本
# ============================================================

CYCLE_EVENT_SCHEMA_VERSION = "1.0"
PHASE_C1_NAME = "phase_c1"
PHASE_C1_VERSION = "1.0.0"


# ============================================================
# 工具
# ============================================================

def is_cycle_event(event_type: str) -> bool:
    """判断一个事件类型是否是 cycle 事件"""
    return str(event_type or "") in ALL_CYCLE_EVENTS


def normalize_cycle_event(event_type: str) -> str:
    """归一化:空 / None → 空字符串;其他返回原值(便于 EventBus 路由)"""
    return str(event_type or "")


def get_event_for_step(step_name: str) -> str:
    """根据 step 名取对应 event(找不到返回空串)"""
    return STEP_TO_EVENT.get(str(step_name or ""), "")


__all__ = [
    "CYCLE_EVENT_STARTED",
    "CYCLE_EVENT_MEMORY_COMPLETED",
    "CYCLE_EVENT_EMOTION_COMPLETED",
    "CYCLE_EVENT_PERSONALITY_COMPLETED",
    "CYCLE_EVENT_RELATIONSHIP_COMPLETED",
    "CYCLE_EVENT_GROWTH_COMPLETED",
    "CYCLE_EVENT_DECISION_COMPLETED",
    "CYCLE_EVENT_COMPLETED",
    "CYCLE_EVENT_FAILED",
    "CYCLE_EVENT_MEMORY_RETRIEVED",
    "CYCLE_EVENT_EMOTION_UPDATED",
    "CYCLE_EVENT_PERSONALITY_UPDATED",
    "CYCLE_EVENT_RELATIONSHIP_UPDATED",
    "CYCLE_EVENT_GROWTH_TRIGGERED",
    "CYCLE_EVENT_DECISION_CREATED",
    "CYCLE_EVENT_OUTCOME_RECEIVED",
    "ALL_CYCLE_EVENTS",
    "PHASE_C1_STAGE_CYCLE_STARTED",
    "PHASE_C1_STAGE_CYCLE_COMPLETED",
    "PHASE_C1_STAGE_CYCLE_FAILED",
    "PHASE_C1_STAGE_STEP_COMPLETED",
    "PHASE_C1_STAGE_ADAPTER_FAILED",
    "ALL_PHASE_C1_STAGES",
    "STANDARD_STEPS_IN_ORDER",
    "STEP_TO_EVENT",
    "CYCLE_EVENT_SCHEMA_VERSION",
    "PHASE_C1_NAME",
    "PHASE_C1_VERSION",
    "is_cycle_event",
    "normalize_cycle_event",
    "get_event_for_step",
]
