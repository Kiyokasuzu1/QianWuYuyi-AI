# -*- coding: utf-8 -*-
"""
src/runtime/perception/__init__.py

Phase 3.8.x/3.9.0/4.0.0: Perception 模块入口。

公开 API：
- FactSource         枚举（USER_INPUT / MEMORY / VISION / SYSTEM / INFERENCE）
- Fact               一条带来源标记的信息
- PerceptionGuard    回复前来源审计器
- GuardReport        审计结果
- Observation        一次感知设备读数（Phase 3.9.0 / 4.0.0 增强）
- ObservationKind    观察类型枚举（Phase 3.9.0）
- ObservationState   当前可感知状态快照（Phase 3.9.0）
- RealityGuard       现实感知边界审计器（Phase 3.9.0）
- RealityGuardReport RealityGuard 审计结果（Phase 3.9.0）
- PerceptionAdapter  感知适配器抽象基类（Phase 4.0.0）
- PerceptionAdapterRegistry 感知适配器注册表（Phase 4.0.0）
- ScreenObservationAdapter 屏幕感知适配器骨架（Phase 4.0.0）
- VisionAdapter      视觉理解适配器骨架（Phase 4.0.0）
- AudioAdapter       音频感知适配器骨架（Phase 4.0.0）
- observation_to_fact Observation → Fact 转换函数（Phase 4.0.0）
"""
from src.runtime.perception.fact_source import (
    FactSource,
    USER_INPUT,
    MEMORY,
    VISION,
    SYSTEM,
    INFERENCE,
)
from src.runtime.perception.fact import Fact, FACT_SCHEMA_VERSION
from src.runtime.perception.perception_guard import (
    PerceptionGuard,
    GuardReport,
)
from src.runtime.perception.observation import (
    Observation,
    ObservationKind,
    OBSERVATION_SCHEMA_VERSION,
    NONE_OBS,
    SCREEN_OBS,
    CAMERA_OBS,
    MICROPHONE_OBS,
    SYSTEM_OBS,
)
from src.runtime.perception.observation_state import (
    ObservationState,
    OBSERVATION_STATE_SCHEMA_VERSION,
)
from src.runtime.perception.reality_guard import (
    RealityGuard,
    RealityGuardReport,
    REALITY_GUARD_SCHEMA_VERSION,
)

# Phase 4.0.0: Perception Adapter Layer
from src.runtime.perception.adapter import (
    PerceptionAdapter,
    PerceptionAdapterRegistry,
    PERCEPTION_ADAPTER_SCHEMA_VERSION,
    ALLOWED_OBS_TO_FACT_SOURCES,
    observation_to_fact,
    observations_to_facts,
)
from src.runtime.perception.screen_adapter import (
    ScreenObservationAdapter,
    SCREEN_ADAPTER_SCHEMA_VERSION,
)
from src.runtime.perception.vision_adapter import (
    VisionAdapter,
    VISION_ADAPTER_SCHEMA_VERSION,
)
from src.runtime.perception.audio_adapter import (
    AudioAdapter,
    AUDIO_ADAPTER_SCHEMA_VERSION,
)
# Phase 4.1.0: 真实感知实现层
from src.runtime.perception.impl import (
    ScreenCaptureAdapter,
    SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION,
)
# Phase 4.2.0: Vision Adapter Layer
from src.runtime.perception import vision as _vision_pkg
from src.runtime.perception.vision import (
    VisionResult,
    VISION_RESULT_SCHEMA_VERSION,
    VisionAdapter,
    VISION_ADAPTER_SCHEMA_VERSION,
    VisionAdapterRegistry,
    VISION_ADAPTER_REGISTRY_SCHEMA_VERSION,
    vision_result_to_fact,
    vision_results_to_facts,
    VISION_FACT_BUILDER_SCHEMA_VERSION,
    VisionProvider,
    VISION_PROVIDER_SCHEMA_VERSION,
    MockVisionProvider,
    MOCK_VISION_PROVIDER_SCHEMA_VERSION,
)

__all__ = [
    # Phase 3.8.x
    "FactSource",
    "USER_INPUT",
    "MEMORY",
    "VISION",
    "SYSTEM",
    "INFERENCE",
    "Fact",
    "FACT_SCHEMA_VERSION",
    "PerceptionGuard",
    "GuardReport",
    # Phase 3.9.0
    "Observation",
    "ObservationKind",
    "OBSERVATION_SCHEMA_VERSION",
    "NONE_OBS",
    "SCREEN_OBS",
    "CAMERA_OBS",
    "MICROPHONE_OBS",
    "SYSTEM_OBS",
    "ObservationState",
    "OBSERVATION_STATE_SCHEMA_VERSION",
    "RealityGuard",
    "RealityGuardReport",
    "REALITY_GUARD_SCHEMA_VERSION",
    # Phase 4.0.0
    "PerceptionAdapter",
    "PerceptionAdapterRegistry",
    "PERCEPTION_ADAPTER_SCHEMA_VERSION",
    "ALLOWED_OBS_TO_FACT_SOURCES",
    "observation_to_fact",
    "observations_to_facts",
    "ScreenObservationAdapter",
    "SCREEN_ADAPTER_SCHEMA_VERSION",
    "VisionAdapter",
    "VISION_ADAPTER_SCHEMA_VERSION",
    "AudioAdapter",
    "AUDIO_ADAPTER_SCHEMA_VERSION",
    # Phase 4.1.0: 真实感知实现层
    "ScreenCaptureAdapter",
    "SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION",
    # Phase 4.2.0: Vision Adapter Layer
    "VisionResult",
    "VISION_RESULT_SCHEMA_VERSION",
    "VisionAdapter",
    "VISION_ADAPTER_SCHEMA_VERSION",
    "VisionAdapterRegistry",
    "VISION_ADAPTER_REGISTRY_SCHEMA_VERSION",
    "vision_result_to_fact",
    "vision_results_to_facts",
    "VISION_FACT_BUILDER_SCHEMA_VERSION",
    "VisionProvider",
    "VISION_PROVIDER_SCHEMA_VERSION",
    "MockVisionProvider",
    "MOCK_VISION_PROVIDER_SCHEMA_VERSION",
]
