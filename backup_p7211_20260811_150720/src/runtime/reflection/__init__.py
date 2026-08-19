# -*- coding: utf-8 -*-
"""
src/runtime/reflection/__init__.py

Phase 5.0-D3-B: Reflection System

本包实现"反思能力":
- 从过去事件中提取模式
- 形成自我认知建议 (StateChangeSuggestion)
- 不直接修改 SelfModel,只通过 Adapter 通信

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 仅使用 Python 标准库 + Runtime 内部模块
- 通过 Clock 注入获取时间
- 所有 Insight / Suggestion 必含 evidence_event_ids
"""
from __future__ import annotations

from src.runtime.reflection.reflection_result import (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
    ALL_REFLECTION_TYPES,
    INSIGHT_CATEGORY_PATTERN,
    INSIGHT_CATEGORY_PREFERENCE,
    INSIGHT_CATEGORY_GROWTH,
    INSIGHT_CATEGORY_CONCERN,
    ALL_INSIGHT_CATEGORIES,
    Insight,
    StateChangeSuggestion,
    ReflectionResult,
    build_empty_reflection_result,
)
from src.runtime.reflection.reflection_history import (
    ReflectionRecord,
    ReflectionHistory,
    DEFAULT_REFLECTION_HISTORY_CAPACITY,
    build_default_reflection_history,
)
from src.runtime.reflection.reflection_strategy import (
    ReflectionStrategy,
    ReflectionContext,
)
from src.runtime.reflection.daily_reflection import DailyReflection
from src.runtime.reflection.event_reflection import EventReflection
from src.runtime.reflection.growth_reflection import GrowthReflection
from src.runtime.reflection.reflection_engine import (
    ReflectionEngine,
    ReflectionEngineError,
    build_default_reflection_engine,
)

__all__ = [
    # 常量
    "REFLECTION_TYPE_DAILY",
    "REFLECTION_TYPE_EVENT",
    "REFLECTION_TYPE_GROWTH",
    "ALL_REFLECTION_TYPES",
    "INSIGHT_CATEGORY_PATTERN",
    "INSIGHT_CATEGORY_PREFERENCE",
    "INSIGHT_CATEGORY_GROWTH",
    "INSIGHT_CATEGORY_CONCERN",
    "ALL_INSIGHT_CATEGORIES",
    "DEFAULT_REFLECTION_HISTORY_CAPACITY",
    # 数据类
    "Insight",
    "StateChangeSuggestion",
    "ReflectionResult",
    "ReflectionRecord",
    # 容器
    "ReflectionHistory",
    # 策略
    "ReflectionStrategy",
    "ReflectionContext",
    "DailyReflection",
    "EventReflection",
    "GrowthReflection",
    # 引擎
    "ReflectionEngine",
    "ReflectionEngineError",
    # 工厂
    "build_empty_reflection_result",
    "build_default_reflection_history",
    "build_default_reflection_engine",
]
