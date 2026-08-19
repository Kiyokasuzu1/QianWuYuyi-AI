"""
Reflection System Package

羽依自我反思系统

使用方式：
    from src.reflection_system import ReflectionSystem, ReflectionType

    reflection_system = ReflectionSystem()
    reflection_system.start()

    # 执行每日反思
    report = reflection_system.perform_daily_reflection()

    # 触发事件反思
    reflection_system.perform_event_reflection(
        trigger_event_id="evt_001",
        focus_areas=[ReflectionFocusArea.GOALS],
    )
"""

from src.reflection_system.reflection_system import (
    BehaviorEvaluation,
    ReflectionFocusArea,
    ReflectionLayer,
    ReflectionReport,
    ReflectionSystem,
    ReflectionType,
    SelfModel,
    get_reflection_system,
)

__all__ = [
    "BehaviorEvaluation",
    "ReflectionFocusArea",
    "ReflectionLayer",
    "ReflectionReport",
    "ReflectionSystem",
    "ReflectionType",
    "SelfModel",
    "get_reflection_system",
]