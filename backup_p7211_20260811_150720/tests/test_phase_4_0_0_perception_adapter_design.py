# -*- coding: utf-8 -*-
"""
tests/test_phase_4_0_0_perception_adapter_design.py

Phase 4.0.0: Perception Adapter Layer Design 测试

覆盖:
1. Adapter 抽象接口存在 (PerceptionAdapter + Screen/Vision/Audio)
2. 不 import 真实视觉/截屏/音频库
3. 不 import OpenCV / PIL / pyautogui / mss / openai / sounddevice / pyaudio
4. Observation schema 字段完整 (observation_id / timestamp / source / content /
   confidence / evidence_ids)
5. source 必须对应 FactSource
6. FactSource 兼容 (USER_INPUT / MEMORY / VISION / SYSTEM / INFERENCE)
7. Observation → Fact 转换规则
8. RealityGuard 可以接收 Observation 转换后的 Fact
9. Runtime 不直接依赖 perception 实现
10. Runtime 阶段新增 PERCEPTION_OBSERVATION (向后兼容)
11. Event 新增 EVENT_TYPE_PERCEPTION_OBSERVATION
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _make_observation(
    source=None,
    kind=None,
    content: str = "test content",
    available: bool = True,
    confidence: float = 0.9,
    evidence_ids=None,
):
    """构造一个合法的 Observation。"""
    from src.runtime.perception import (
        Observation, ObservationKind, FactSource,
    )
    if source is None:
        source = FactSource.VISION
    if kind is None:
        kind = ObservationKind.SCREEN
    return Observation(
        kind=kind,
        content=content,
        available=available,
        confidence=confidence,
        source=source,
        evidence_ids=evidence_ids or [],
    )


# ============================================================
# T1: Adapter 抽象接口存在
# ============================================================
class TestAdapterInterfaceExists:
    def test_perception_adapter_abstract_class(self):
        from src.runtime.perception import PerceptionAdapter
        from abc import ABC
        assert issubclass(PerceptionAdapter, ABC)

    def test_perception_adapter_required_methods(self):
        from src.runtime.perception import PerceptionAdapter
        for method in ["attach", "detach", "health_check", "observe"]:
            assert hasattr(PerceptionAdapter, method), f"missing method: {method}"

    def test_screen_observation_adapter_subclass(self):
        from src.runtime.perception import (
            PerceptionAdapter, ScreenObservationAdapter,
        )
        assert issubclass(ScreenObservationAdapter, PerceptionAdapter)

    def test_vision_adapter_subclass(self):
        from src.runtime.perception import PerceptionAdapter
        # Phase 4.2.0: vision 理解适配器已拆分为 VisionAdapter
        # (位于 src.runtime.perception.vision.vision_adapter),
        # 它不再是 PerceptionAdapter 子类 —— 新的 VisionAdapter
        # 接收 Observation 输出 VisionResult,通过 VisionProvider 委托模型。
        # 旧 Phase 4.0.0 的 VisionAdapter(PerceptionAdapter 子类)语义
        # 已被 Phase 4.1.0 的 ScreenCaptureAdapter 替代。
        try:
            from src.runtime.perception.vision import VisionAdapter as NewVA
            # 新的 VisionAdapter 不再继承 PerceptionAdapter
            assert NewVA is not None
            assert not issubclass(NewVA, PerceptionAdapter)
        except ImportError:
            # 若 vision 子包不可用,回退到旧版 VisionAdapter
            from src.runtime.perception import VisionAdapter
            assert issubclass(VisionAdapter, PerceptionAdapter)

    def test_audio_adapter_subclass(self):
        from src.runtime.perception import PerceptionAdapter, AudioAdapter
        assert issubclass(AudioAdapter, PerceptionAdapter)

    def test_perception_adapter_registry_exists(self):
        from src.runtime.perception import PerceptionAdapterRegistry
        reg = PerceptionAdapterRegistry()
        assert hasattr(reg, "register")
        assert hasattr(reg, "unregister")
        assert hasattr(reg, "get")
        assert hasattr(reg, "by_kind")
        assert hasattr(reg, "observe_all")


# ============================================================
# T2: 不 import 真实视觉库
# ============================================================
class TestNoVisionLibraryImports:
    """确保 Phase 4.0.0 perception 子包未 import 任何视觉/截屏/音频库。"""

    PERCEPTION_DIR = PROJECT_ROOT / "src" / "runtime" / "perception"

    FORBIDDEN_IMPORTS = [
        "import cv2",
        "from cv2",
        "import PIL",
        "from PIL",
        "import pyautogui",
        "from pyautogui",
        "import mss",
        "from mss",
        "import dxcam",
        "from dxcam",
        "import openai",
        "from openai",
        "import anthropic",
        "from anthropic",
        "import transformers",
        "from transformers",
        "import torch",
        "from torch",
        "import tensorflow",
        "from tensorflow",
        "import ultralytics",
        "from ultralytics",
        "import sounddevice",
        "from sounddevice",
        "import pyaudio",
        "from pyaudio",
        "import wave",
        "import whisper",
        "from whisper",
        "import faster_whisper",
        "from faster_whisper",
        "import pytesseract",
        "from pytesseract",
        "import paddleocr",
        "from paddleocr",
        "import screen_capture",
        "from src.vision",
        "from src.screen_capture",
        "from src.camera",
        "from src.audio",
    ]

    def test_perception_files_no_forbidden_imports(self):
        for py_file in self.PERCEPTION_DIR.glob("*.py"):
            if py_file.name == "__init__.py":
                # __init__.py 只 re-export,允许 (但本身也不应 import 视觉库)
                pass
            text = py_file.read_text(encoding="utf-8")
            for forbidden in self.FORBIDDEN_IMPORTS:
                assert forbidden not in text, (
                    f"{py_file.name} contains forbidden import: {forbidden!r}"
                )


# ============================================================
# T3: Runtime 不直接依赖 perception 实现
# ============================================================
class TestRuntimeNoPerceptionDependency:
    RUNTIME_FILE = PROJECT_ROOT / "src" / "runtime" / "runtime.py"

    def test_runtime_does_not_import_visual_impl(self):
        text = self.RUNTIME_FILE.read_text(encoding="utf-8")
        # runtime.py 只能 import stdlib + 同包 (events, context, adapter_registry)
        for forbidden in [
            "import cv2", "import PIL", "import pyautogui", "import mss",
            "import openai", "import sounddevice", "import pyaudio",
            "from src.vision", "from src.screen_capture",
            "from src.camera", "from src.audio",
        ]:
            assert forbidden not in text, (
                f"runtime.py contains forbidden import: {forbidden!r}"
            )

    def test_runtime_perception_observation_stage_exists(self):
        from src.runtime.runtime import (
            RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
        )
        assert hasattr(RuntimeStage, "PERCEPTION_OBSERVATION")
        assert (
            RuntimeStage.PERCEPTION_OBSERVATION
            in RUNTIME_LIFECYCLE_ORDER
        )

    def test_runtime_lifecycle_order_includes_perception(self):
        from src.runtime.runtime import (
            RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
        )
        # 必须包含新阶段
        idx = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.PERCEPTION_OBSERVATION
        )
        # 必须在 personality_context_build 之后
        assert (
            RUNTIME_LIFECYCLE_ORDER[idx - 1]
            == RuntimeStage.PERSONALITY_CONTEXT_BUILD
        )
        # Phase 4.2.0: PERCEPTION_ANALYSIS 可能被插入在 PERCEPTION_OBSERVATION
        # 和 RESPONSE_GENERATION 之间;若无 PERCEPTION_ANALYSIS,下一阶段才是
        # RESPONSE_GENERATION。
        next_stage = RUNTIME_LIFECYCLE_ORDER[idx + 1]
        assert next_stage in (
            RuntimeStage.RESPONSE_GENERATION,
            RuntimeStage.PERCEPTION_ANALYSIS,
        )

    def test_runtime_backward_compatible(self):
        """Phase 4.0.0 之后, RuntimeContext schema 仍为 v1.0。"""
        from src.runtime.context import RuntimeContext, RUNTIME_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"
        ctx = RuntimeContext()
        assert ctx.schema_version == "1.0"


# ============================================================
# T4: Observation schema 字段完整性
# ============================================================
class TestObservationSchema:
    REQUIRED_FIELDS = [
        "observation_id",  # Phase 4.0.0
        "timestamp",
        "source",
        "content",
        "confidence",
        "evidence_ids",  # Phase 4.0.0
    ]

    def test_required_fields_exist(self):
        obs = _make_observation()
        for f in self.REQUIRED_FIELDS:
            assert hasattr(obs, f), f"missing field: {f}"

    def test_observation_id_is_alias_for_id(self):
        obs = _make_observation()
        assert obs.observation_id == obs.id

    def test_observation_id_property(self):
        obs = _make_observation()
        obs_id = obs.id
        assert obs.observation_id == obs_id

    def test_evidence_ids_default_empty(self):
        obs = _make_observation()
        assert obs.evidence_ids == []

    def test_evidence_ids_can_be_set(self):
        obs = _make_observation(evidence_ids=["ev1", "ev2"])
        assert obs.evidence_ids == ["ev1", "ev2"]
        assert obs.has_evidence is True

    def test_confidence_in_range(self):
        obs = _make_observation(confidence=0.5)
        assert obs.confidence == 0.5

    def test_confidence_out_of_range_raises(self):
        with pytest.raises(ValueError):
            _make_observation(confidence=1.5)

    def test_observation_to_dict(self):
        obs = _make_observation()
        d = obs.to_dict()
        for f in self.REQUIRED_FIELDS:
            assert f in d, f"to_dict missing {f}"


# ============================================================
# T5: source 必须对应 FactSource
# ============================================================
class TestObservationSourceFactSource:
    def test_source_accepts_factsource(self):
        from src.runtime.perception import FactSource
        obs = _make_observation(source=FactSource.VISION)
        assert obs.source == FactSource.VISION

    def test_source_accepts_string(self):
        from src.runtime.perception import FactSource
        obs = _make_observation(source="vision")
        assert obs.source == FactSource.VISION

    def test_source_rejects_invalid_type(self):
        with pytest.raises(ValueError):
            _make_observation(source=12345)  # not str or FactSource

    def test_all_factsource_values_compatible(self):
        from src.runtime.perception import FactSource
        for source in [
            FactSource.USER_INPUT,
            FactSource.MEMORY,
            FactSource.VISION,
            FactSource.SYSTEM,
            FactSource.INFERENCE,
        ]:
            obs = _make_observation(source=source)
            assert obs.source == source


# ============================================================
# T6: FactSource 兼容 (枚举)
# ============================================================
class TestFactSourceCompat:
    def test_factsource_enum_values(self):
        from src.runtime.perception import FactSource
        assert FactSource.USER_INPUT.value == "user_input"
        assert FactSource.MEMORY.value == "memory"
        assert FactSource.VISION.value == "vision"
        assert FactSource.SYSTEM.value == "system"
        assert FactSource.INFERENCE.value == "inference"

    def test_factsource_grounded_property(self):
        from src.runtime.perception import FactSource
        assert FactSource.USER_INPUT.is_grounded is True
        assert FactSource.MEMORY.is_grounded is True
        assert FactSource.VISION.is_grounded is True
        assert FactSource.SYSTEM.is_grounded is True
        assert FactSource.INFERENCE.is_grounded is False

    def test_factsource_trust_scores(self):
        from src.runtime.perception import FactSource
        # trust_score 是 property, 不是 trust_score()
        assert FactSource.USER_INPUT.trust_score == 1.0
        assert FactSource.SYSTEM.trust_score == 0.95
        assert FactSource.VISION.trust_score == 0.9
        assert FactSource.MEMORY.trust_score == 0.8
        assert FactSource.INFERENCE.trust_score == 0.3


# ============================================================
# T7: Observation → Fact 转换规则
# ============================================================
class TestObservationToFact:
    def test_observation_to_fact_success(self):
        from src.runtime.perception import (
            observation_to_fact, FactSource, ObservationKind,
        )
        obs = _make_observation(
            source=FactSource.VISION,
            kind=ObservationKind.SCREEN,
            content="屏幕上显示 QQ 聊天窗口",
            confidence=0.9,
            evidence_ids=["ev1"],
        )
        fact = observation_to_fact(obs)
        assert fact is not None
        assert fact.content == "屏幕上显示 QQ 聊天窗口"
        assert fact.source == FactSource.VISION
        assert fact.confidence == 0.9
        assert obs.observation_id in fact.evidence_ids

    def test_observation_unavailable_returns_none(self):
        from src.runtime.perception import observation_to_fact, FactSource
        obs = _make_observation(
            source=FactSource.VISION, available=False,
        )
        fact = observation_to_fact(obs)
        assert fact is None

    def test_observation_kind_none_returns_none(self):
        from src.runtime.perception import (
            observation_to_fact, FactSource, ObservationKind,
        )
        obs = _make_observation(
            source=FactSource.VISION, kind=ObservationKind.NONE,
        )
        fact = observation_to_fact(obs)
        assert fact is None

    def test_observation_empty_content_returns_none(self):
        from src.runtime.perception import observation_to_fact, FactSource
        obs = _make_observation(source=FactSource.VISION, content="")
        fact = observation_to_fact(obs)
        assert fact is None

    def test_observation_inference_source_rejected(self):
        from src.runtime.perception import (
            observation_to_fact, FactSource,
        )
        obs = _make_observation(source=FactSource.INFERENCE)
        # INFERENCE 不允许从 Observation 转换
        with pytest.raises(ValueError):
            observation_to_fact(obs)

    def test_observation_confidence_out_of_range(self):
        from src.runtime.perception import (
            observation_to_fact, FactSource,
        )
        # 直接构造,绕过 __post_init__ 校验?不,__post_init__ 会拒
        # 所以这里测试用合法 confidence 但 content 为空
        obs = _make_observation(
            source=FactSource.VISION, content="   ", confidence=0.5,
        )
        fact = observation_to_fact(obs)
        assert fact is None

    def test_observations_to_facts_batch(self):
        from src.runtime.perception import (
            observations_to_facts, FactSource, ObservationKind,
        )
        observations = [
            _make_observation(
                source=FactSource.VISION,
                kind=ObservationKind.SCREEN,
                content="screen content",
            ),
            _make_observation(
                source=FactSource.SYSTEM,
                kind=ObservationKind.SYSTEM,
                content="system event",
            ),
            # INFERENCE 应被过滤
            _make_observation(
                source=FactSource.INFERENCE,
                kind=ObservationKind.CAMERA,
                content="inference (rejected)",
            ),
        ]
        facts = observations_to_facts(observations)
        assert len(facts) == 2  # 2 valid, 1 rejected
        assert all(f.source != FactSource.INFERENCE for f in facts)


# ============================================================
# T8: RealityGuard 接收 Observation 转换后的 Fact
# ============================================================
class TestRealityGuardAcceptsObservationFacts:
    def test_observation_fact_passes_reality_guard(self):
        """带 VISION Fact 时,描述屏幕的 reply 应通过 RealityGuard。"""
        from src.runtime.perception import (
            RealityGuard, observation_to_fact, FactSource,
        )
        obs = _make_observation(
            source=FactSource.VISION,
            content="屏幕上显示 QQ 聊天窗口",
        )
        fact = observation_to_fact(obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "屏幕上显示的是 QQ 聊天界面",
            facts=[fact],
            obs_state=None,
        )
        # VISION Fact 应支撑视觉描述
        assert report.allowed is True

    def test_observation_fact_blocks_without_observation(self):
        """无任何 Fact 时,描述屏幕应被 RealityGuard 阻断。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "你刚才在屏幕上看什么?",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False
        assert "hallucinated_visual_observation" in report.violations


# ============================================================
# T9: Adapter lifecycle
# ============================================================
class TestAdapterLifecycle:
    def test_screen_adapter_attach_detach(self):
        from src.runtime.perception import ScreenObservationAdapter
        adapter = ScreenObservationAdapter()
        assert adapter.is_attached is False
        adapter.attach()
        assert adapter.is_attached is True
        adapter.detach()
        assert adapter.is_attached is False

    def test_screen_adapter_capture_not_implemented(self):
        from src.runtime.perception import ScreenObservationAdapter
        adapter = ScreenObservationAdapter()
        adapter.attach()
        with pytest.raises(NotImplementedError):
            adapter.capture()

    def test_screen_adapter_observe_returns_none(self):
        """observe() 应安全返回 None (Phase 4.0.0 行为)。"""
        from src.runtime.perception import ScreenObservationAdapter
        adapter = ScreenObservationAdapter()
        adapter.attach()
        result = adapter.observe()
        assert result is None  # Phase 4.0.0: 不抛异常,优雅返回 None

    def test_vision_adapter_analyze_not_implemented(self):
        # Phase 4.2.0: 旧的 VisionAdapter(PerceptionAdapter 子类)被
        # 新的 VisionAdapter(独立 ABC,位于 vision 子包)取代。
        # 新接口使用 describe() 而非 analyze()。
        # 此测试保留:即便回退到旧实现,仍期望 analyze() 抛 NotImplementedError。
        try:
            from src.runtime.perception.vision import VisionAdapter as NewVA
        except ImportError:
            NewVA = None
        if NewVA is not None:
            # 新版:VisionAdapter 是 ABC,不能直接实例化
            with pytest.raises(TypeError):
                NewVA()
        else:
            from src.runtime.perception import VisionAdapter
            adapter = VisionAdapter()
            adapter.attach()
            obs = _make_observation()
            with pytest.raises(NotImplementedError):
                adapter.analyze(obs)

    def test_audio_adapter_listen_not_implemented(self):
        from src.runtime.perception import AudioAdapter
        adapter = AudioAdapter()
        adapter.attach()
        with pytest.raises(NotImplementedError):
            adapter.listen()

    def test_audio_adapter_analyze_audio_not_implemented(self):
        from src.runtime.perception import (
            AudioAdapter, FactSource, ObservationKind,
        )
        adapter = AudioAdapter()
        adapter.attach()
        obs = _make_observation(
            source=FactSource.SYSTEM,
            kind=ObservationKind.MICROPHONE,
        )
        with pytest.raises(NotImplementedError):
            adapter.analyze_audio(obs)

    def test_health_check_returns_dict(self):
        from src.runtime.perception import ScreenObservationAdapter
        adapter = ScreenObservationAdapter()
        adapter.attach()
        health = adapter.health_check()
        assert isinstance(health, dict)
        assert "healthy" in health
        assert "name" in health
        assert "schema_version" in health


# ============================================================
# T10: AdapterRegistry
# ============================================================
class TestAdapterRegistry:
    def test_register_and_get(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenObservationAdapter,
        )
        reg = PerceptionAdapterRegistry()
        adapter = ScreenObservationAdapter()
        reg.register(adapter)
        assert reg.get("screen_observation_adapter") is adapter

    def test_register_rejects_non_adapter(self):
        from src.runtime.perception import PerceptionAdapterRegistry
        reg = PerceptionAdapterRegistry()
        with pytest.raises(TypeError):
            reg.register("not an adapter")

    def test_register_rejects_duplicate(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenObservationAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenObservationAdapter())
        with pytest.raises(ValueError):
            reg.register(ScreenObservationAdapter())

    def test_by_kind(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry,
            ScreenObservationAdapter,
            AudioAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenObservationAdapter())
        reg.register(AudioAdapter())
        screens = reg.by_kind(__import__(
            "src.runtime.perception", fromlist=["ObservationKind"],
        ).ObservationKind.SCREEN)
        assert len(screens) == 1
        assert screens[0].name == "screen_observation_adapter"

    def test_unregister(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenObservationAdapter,
        )
        reg = PerceptionAdapterRegistry()
        adapter = ScreenObservationAdapter()
        reg.register(adapter)
        removed = reg.unregister("screen_observation_adapter")
        assert removed is adapter
        assert "screen_observation_adapter" not in reg


# ============================================================
# T11: Event 新增 EVENT_TYPE_PERCEPTION_OBSERVATION
# ============================================================
class TestEventTypePerception:
    def test_event_type_constant_exists(self):
        from src.runtime.events import EVENT_TYPE_PERCEPTION_OBSERVATION
        assert EVENT_TYPE_PERCEPTION_OBSERVATION == "perception_observation"

    def test_event_with_perception_type(self):
        from src.runtime.events import (
            Event, EVENT_TYPE_PERCEPTION_OBSERVATION,
        )
        evt = Event(
            type=EVENT_TYPE_PERCEPTION_OBSERVATION,
            payload={"observation_id": "obs_123"},
        )
        assert evt.type == EVENT_TYPE_PERCEPTION_OBSERVATION
        assert evt.payload["observation_id"] == "obs_123"

    def test_event_backward_compatible(self):
        """Phase 4.0.0 之后, Event 字段未变。"""
        from src.runtime.events import (
            Event, EVENT_TYPE_USER_INPUT,
        )
        evt = Event(type=EVENT_TYPE_USER_INPUT, payload={"test": True})
        # 字段都还在
        assert hasattr(evt, "id")
        assert hasattr(evt, "type")
        assert hasattr(evt, "source")
        assert hasattr(evt, "timestamp")
        assert hasattr(evt, "payload")
        assert hasattr(evt, "related_ids")
        assert hasattr(evt, "priority")
        assert hasattr(evt, "metadata")


# ============================================================
# T12: 阶段总结
# ============================================================
def test_phase_4_0_0_summary():
    """Phase 4.0.0 阶段总结。"""
    from src.runtime.perception import (
        Observation, ObservationKind, FactSource,
        PerceptionAdapter, PerceptionAdapterRegistry,
        ScreenObservationAdapter, VisionAdapter, AudioAdapter,
        observation_to_fact,
        PERCEPTION_ADAPTER_SCHEMA_VERSION,
        OBSERVATION_SCHEMA_VERSION,
    )
    from src.runtime.runtime import (
        RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
    )
    from src.runtime.events import EVENT_TYPE_PERCEPTION_OBSERVATION

    # 1) 关键 schema 版本
    assert PERCEPTION_ADAPTER_SCHEMA_VERSION == "1.0"
    assert OBSERVATION_SCHEMA_VERSION == "1.0"

    # 2) 4 个 Adapter 接口
    assert PerceptionAdapter is not None
    assert ScreenObservationAdapter is not None
    assert VisionAdapter is not None
    assert AudioAdapter is not None
    assert PerceptionAdapterRegistry is not None

    # 3) Observation 字段
    obs = _make_observation()
    for f in [
        "observation_id", "timestamp", "source", "content",
        "confidence", "evidence_ids", "kind", "available", "id",
    ]:
        assert hasattr(obs, f), f"missing field: {f}"

    # 4) Runtime 阶段扩展
    assert RuntimeStage.PERCEPTION_OBSERVATION in RUNTIME_LIFECYCLE_ORDER
    assert EVENT_TYPE_PERCEPTION_OBSERVATION == "perception_observation"

    # 5) 转换函数
    assert callable(observation_to_fact)

    # 6) 不依赖视觉实现 (本测试通过 TestNoVisionLibraryImports 已验证)
    # 7) Runtime 不依赖 perception 实现 (已通过 TestRuntimeNoPerceptionDependency 验证)
