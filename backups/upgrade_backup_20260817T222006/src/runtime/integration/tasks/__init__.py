# -*- coding: utf-8 -*-
"""src/runtime/integration/tasks package.

Phase 5.0-D2 Step 3: LifecycleTask 适配器层。

职责:
- 把已有 Adapter(Memory/Emotion/Growth/Personality/Relationship)接入 Lifecycle Core
- 每个 Task 继承 BaseLifecycleTask,通过 Adapter 触发 IntegrationEvent
- 不调用业务模块;不实现主动行为;不调用外部工具

约束:
- 不修改 src/runtime/lifecycle/**
- 不修改 src/memory/**、src/growth/**、src/personality/**、src/emotion/**、src/relationship/**
- Task 只通过 Adapter 通信
- Task 不持有业务模块引用
"""
from src.runtime.integration.tasks.base_integration_task import (
    BaseIntegrationTask,
    IntegrationTaskError,
)
from src.runtime.integration.tasks.emotion_lifecycle_task import EmotionLifecycleTask
from src.runtime.integration.tasks.growth_lifecycle_task import GrowthLifecycleTask
from src.runtime.integration.tasks.memory_lifecycle_task import MemoryLifecycleTask
from src.runtime.integration.tasks.personality_lifecycle_task import (
    PersonalityLifecycleTask,
)
from src.runtime.integration.tasks.reflection_lifecycle_task import (
    ReflectionLifecycleTask,
)
from src.runtime.integration.tasks.initiative_lifecycle_task import (
    InitiativeLifecycleTask,
)
from src.runtime.integration.tasks.goal_lifecycle_task import (
    GoalLifecycleTask,
    build_default_goal_lifecycle_task,
)
from src.runtime.integration.tasks.relationship_adapter import (
    RelationshipAdapter,
    build_default_relationship_adapter,
)
from src.runtime.integration.tasks.relationship_lifecycle_task import (
    RelationshipLifecycleTask,
)


__all__ = [
    # Base
    "BaseIntegrationTask",
    "IntegrationTaskError",
    # Adapters (Relationship 在 Step 3 阶段补全)
    "RelationshipAdapter",
    "build_default_relationship_adapter",
    # Tasks
    "MemoryLifecycleTask",
    "EmotionLifecycleTask",
    "GrowthLifecycleTask",
    "PersonalityLifecycleTask",
    "RelationshipLifecycleTask",
    "ReflectionLifecycleTask",
    "InitiativeLifecycleTask",
    "GoalLifecycleTask",
    "build_default_goal_lifecycle_task",
]
