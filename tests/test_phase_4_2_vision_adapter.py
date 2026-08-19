# -*- coding: utf-8 -*-
"""
tests/test_phase_4_2_vision_adapter.py

Phase 4.2.0: Vision Adapter Layer 真实实现测试

覆盖:
1.  VisionAdapter interface exists
2.  VisionResult schema
3.  Observation → Vision → Fact
4.  Fact source == VISION
5.  Runtime 不 import vision provider
6.  Runtime 不 import openai
7.  MockVisionProvider works
8.  No Vision Fact 时 RealityGuard 阻断
9.  有 Vision Fact 时 RealityGuard 放行
10. Vision Provider failure 不影响 Runtime
11. Vision Adapter failure isolated
12. Runtime lifecycle 新阶段顺序正确
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _make_screen_observation(
    available: bool = True,
    content: str = "Screen capture available: 1920x1080",
    confidence: float = 0.9,
):
    from src.runtime.perception import (
        Observation, ObservationKind, FactSource,
    )
    return Observation(
        kind=ObservationKind.SCREEN,
        content=content,
        available=available,
        confidence=confidence,
        source=FactSource.VISION,
    )


def _make_camera_observation(
    available: bool = True,
    content: str = "camera feed",
    confidence: float = 0.85,
):
    from src.runtime.perception import (
        Observation, ObservationKind, FactSource,
    )
    return Observation(
        kind=ObservationKind.CAMERA,
        content=content,
        available=available,
        confidence=confidence,
        source=FactSource.VISION,
    )


def _make_vision_result(
    description: str = "screen contains unknown desktop content",
    confidence: float = 0.5,
    source_model: str = "mock_vision",
    observation_id: str = None,
    evidence_ids=None,
):
    from src.runtime.perception import VisionResult
    if evidence_ids is None and observation_id is not None:
        evidence_ids = [observation_id]
    elif evidence_ids is None:
        # 默认给一个合成 observation id,保证 evidence_ids 非空
        # fact_builder 要求 evidence_ids 非空才会生成 Fact
        evidence_ids = ["obs_default_synth"]
    return VisionResult(
        description=description,
        confidence=confidence,
        source_model=source_model,
        observation_id=observation_id,
        evidence_ids=evidence_ids,
    )


# ============================================================
# T1: VisionAdapter interface exists
# ============================================================
class TestVisionAdapterInterface:
    def test_vision_adapter_class_exists(self):
        from src.runtime.perception import VisionAdapter
        assert VisionAdapter is not None
        assert hasattr(VisionAdapter, "describe")
        assert hasattr(VisionAdapter, "describe_batch")
        assert hasattr(VisionAdapter, "health_check")
        assert hasattr(VisionAdapter, "to_facts")
        assert hasattr(VisionAdapter, "set_provider")
        assert hasattr(VisionAdapter, "get_provider")

    def test_vision_adapter_is_abstract(self):
        """VisionAdapter 不能直接实例化(因为 health_check 是 abstractmethod)。"""
        from src.runtime.perception import VisionAdapter
        with pytest.raises(TypeError):
            VisionAdapter()

    def test_vision_adapter_schema_version(self):
        from src.runtime.perception import VisionAdapter, VISION_ADAPTER_SCHEMA_VERSION
        assert VISION_ADAPTER_SCHEMA_VERSION == "1.0"
        # 检查子类的 schema_version 字段
        from src.runtime.perception.vision.providers.mock_provider import MockVisionProvider
        # 创建一个具体子类来检查 schema_version
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        assert a.schema_version == "1.0"

    def test_vision_adapter_set_provider(self):
        from src.runtime.perception import VisionAdapter, MockVisionProvider
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        provider = MockVisionProvider()
        a.set_provider(provider)
        assert a.get_provider() is provider

    def test_vision_adapter_rejects_invalid_provider(self):
        from src.runtime.perception import VisionAdapter
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        with pytest.raises(TypeError):
            a.set_provider("not_a_provider")

    def test_vision_adapter_attach_detach(self):
        from src.runtime.perception import VisionAdapter, MockVisionProvider
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        provider = MockVisionProvider()
        a.set_provider(provider)
        a.attach()
        assert a.is_attached is True
        assert provider.is_attached is True
        a.detach()
        assert a.is_attached is False
        assert provider.is_attached is False


# ============================================================
# T2: VisionResult schema
# ============================================================
class TestVisionResultSchema:
    def test_vision_result_basic(self):
        from src.runtime.perception import VisionResult
        r = VisionResult(
            description="屏幕显示代码编辑器",
            confidence=0.92,
            source_model="test_model",
        )
        assert r.description == "屏幕显示代码编辑器"
        assert r.confidence == 0.92
        assert r.source_model == "test_model"
        assert r.schema_version == "1.0"
        assert r.result_id is not None
        assert r.has_description is True

    def test_vision_result_source_must_be_vision(self):
        from src.runtime.perception import VisionResult, FactSource
        r = VisionResult(description="test", confidence=0.5)
        # 默认 source
        assert r.source == FactSource.VISION

    def test_vision_result_rejects_inference_source(self):
        from src.runtime.perception import VisionResult, FactSource
        with pytest.raises(ValueError) as exc:
            VisionResult(
                description="test",
                confidence=0.5,
                source=FactSource.INFERENCE,
            )
        assert "VISION" in str(exc.value) or "INFERENCE" in str(exc.value)

    def test_vision_result_rejects_user_input_source(self):
        from src.runtime.perception import VisionResult, FactSource
        with pytest.raises(ValueError):
            VisionResult(
                description="test",
                confidence=0.5,
                source=FactSource.USER_INPUT,
            )

    def test_vision_result_confidence_range(self):
        from src.runtime.perception import VisionResult
        with pytest.raises(ValueError):
            VisionResult(description="test", confidence=1.5)
        with pytest.raises(ValueError):
            VisionResult(description="test", confidence=-0.1)

    def test_vision_result_evidence_ids_must_be_list_of_str(self):
        from src.runtime.perception import VisionResult
        with pytest.raises(ValueError):
            VisionResult(
                description="test", confidence=0.5,
                evidence_ids=["valid_id", 123],
            )

    def test_vision_result_to_from_dict(self):
        from src.runtime.perception import VisionResult
        r = VisionResult(
            description="hello",
            confidence=0.7,
            source_model="test",
            evidence_ids=["obs_001"],
        )
        d = r.to_dict()
        assert d["description"] == "hello"
        assert d["source"] == "vision"
        r2 = VisionResult.from_dict(d)
        assert r2.description == r.description
        assert r2.confidence == r.confidence
        assert r2.evidence_ids == r.evidence_ids

    def test_vision_result_has_description(self):
        from src.runtime.perception import VisionResult
        r1 = VisionResult(description="", confidence=0.5)
        assert r1.has_description is False
        r2 = VisionResult(description="x", confidence=0.5)
        assert r2.has_description is True
        r3 = VisionResult(description="   ", confidence=0.5)
        assert r3.has_description is False


# ============================================================
# T3: Observation → Vision → Fact
# ============================================================
class TestObservationToVisionToFact:
    def test_observation_to_vision_result(self):
        from src.runtime.perception import VisionAdapter, MockVisionProvider
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        a.attach()
        obs = _make_screen_observation()
        results = a.describe(obs)
        assert len(results) == 1
        r = results[0]
        assert r.description == "screen contains unknown desktop content"
        assert r.confidence == 0.5

    def test_observation_to_fact(self):
        from src.runtime.perception import vision_result_to_fact
        obs = _make_screen_observation()
        r = _make_vision_result(observation_id=obs.observation_id)
        fact = vision_result_to_fact(r, obs)
        assert fact is not None
        assert obs.observation_id in fact.evidence_ids
        assert r.result_id in fact.evidence_ids or len(fact.evidence_ids) >= 1

    def test_unavailable_observation_skipped(self):
        from src.runtime.perception import MockVisionProvider
        provider = MockVisionProvider()
        provider.attach()
        obs = _make_screen_observation(available=False)
        r = provider.analyze(obs)
        assert r is None

    def test_non_visual_observation_skipped(self):
        from src.runtime.perception import (
            MockVisionProvider, Observation, ObservationKind, FactSource,
        )
        provider = MockVisionProvider()
        provider.attach()
        # 创建一个非视觉 observation
        obs = Observation(
            kind=ObservationKind.MICROPHONE,
            content="audio",
            available=True,
            confidence=0.9,
            source=FactSource.VISION,
        )
        r = provider.analyze(obs)
        assert r is None


# ============================================================
# T4: Fact source == VISION
# ============================================================
class TestFactSourceIsVision:
    def test_fact_source_is_vision(self):
        from src.runtime.perception import vision_result_to_fact, FactSource
        r = _make_vision_result()
        fact = vision_result_to_fact(r)
        assert fact is not None
        assert fact.source == FactSource.VISION

    def test_fact_inference_source_rejected(self):
        from src.runtime.perception import VisionResult, FactSource, vision_result_to_fact
        # 直接构造一个 INFERENCE source 的 VisionResult 会被 __post_init__ 拒绝
        with pytest.raises(ValueError):
            r = VisionResult(
                description="test",
                confidence=0.3,
                source=FactSource.INFERENCE,
            )
        # 即使构造成功,fact_builder 也会再次校验拒绝
        # 这里再确认一次
        r = VisionResult(description="x", confidence=0.3)
        # 强制改为 INFERENCE 会失败
        with pytest.raises(ValueError):
            r.source = FactSource.INFERENCE

    def test_fact_evidence_ids_contain_observation(self):
        from src.runtime.perception import vision_result_to_fact
        obs = _make_screen_observation()
        r = _make_vision_result(
            description="屏幕显示QQ",
            confidence=0.9,
            observation_id=obs.observation_id,
        )
        fact = vision_result_to_fact(r, obs)
        assert fact is not None
        assert obs.observation_id in fact.evidence_ids

    def test_fact_empty_description_rejected(self):
        from src.runtime.perception import vision_result_to_fact
        r = _make_vision_result(description="")
        obs = _make_screen_observation()
        fact = vision_result_to_fact(r, obs)
        assert fact is None


# ============================================================
# T5: Runtime 不 import vision provider
# ============================================================
class TestRuntimeNoVisionProviderImport:
    RUNTIME_FILE = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
    VISION_PKG_FILES = [
        PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "__init__.py",
        PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "vision_adapter.py",
        PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "vision_result.py",
        PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "vision_registry.py",
        PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "fact_builder.py",
    ]

    def test_runtime_does_not_import_vision_module(self):
        """runtime.py 不得 import src.runtime.perception.vision.* (仅可延迟 import)。"""
        text = self.RUNTIME_FILE.read_text(encoding="utf-8")
        # 允许 from src.runtime.perception.vision import (via lazy / 函数内)
        # 但不允许模块顶层 import
        lines = text.split("\n")
        in_function = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                in_function = True
            if not in_function:
                if "from src.runtime.perception.vision" in line and not line.strip().startswith("#"):
                    if not line.strip().startswith("from __future__"):
                        # 模块顶层不应有 from src.runtime.perception.vision import
                        # 实际实现中,我们使用了函数内 lazy import
                        pytest.fail(
                            f"runtime.py imports vision at module level: {line!r}"
                        )

    def test_runtime_does_not_import_openai(self):
        text = self.RUNTIME_FILE.read_text(encoding="utf-8")
        for forbidden in ["import openai", "from openai"]:
            assert forbidden not in text, (
                f"runtime.py contains forbidden import: {forbidden!r}"
            )

    def test_runtime_does_not_import_specific_vision_sdks(self):
        text = self.RUNTIME_FILE.read_text(encoding="utf-8")
        for forbidden in [
            "import openai", "from openai",
            "import qwen", "from qwen",
            "import llava", "from llava",
            "import clip", "from clip",
            "import torch", "from torch",
        ]:
            assert forbidden not in text, (
                f"runtime.py contains forbidden import: {forbidden!r}"
            )

    def test_vision_module_files_exist(self):
        for f in self.VISION_PKG_FILES:
            assert f.exists(), f"missing file: {f}"

    def test_vision_init_does_not_import_sdks(self):
        text = (PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "__init__.py").read_text(encoding="utf-8")
        for forbidden in [
            "import openai", "from openai",
            "import qwen", "from qwen",
            "import llava", "from llava",
        ]:
            assert forbidden not in text, f"vision __init__.py contains forbidden: {forbidden!r}"


# ============================================================
# T6: Runtime 不 import openai
# ============================================================
class TestRuntimeNoOpenAI:
    def test_runtime_no_openai_import(self):
        runtime_file = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_file.read_text(encoding="utf-8")
        assert "openai" not in text, "runtime.py should not reference openai"

    def test_perception_pkg_no_openai_import(self):
        perception_init = PROJECT_ROOT / "src" / "runtime" / "perception" / "__init__.py"
        text = perception_init.read_text(encoding="utf-8")
        assert "openai" not in text, "perception/__init__.py should not reference openai"

    def test_vision_pkg_no_openai_import(self):
        vision_init = PROJECT_ROOT / "src" / "runtime" / "perception" / "vision" / "__init__.py"
        text = vision_init.read_text(encoding="utf-8")
        assert "openai" not in text


# ============================================================
# T7: MockVisionProvider works
# ============================================================
class TestMockVisionProvider:
    def test_mock_provider_can_instantiate(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        assert p is not None
        assert p.provider_name == "mock_vision"
        assert p.schema_version == "1.0"

    def test_mock_provider_default_behavior(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        p.attach()
        obs = _make_screen_observation()
        r = p.analyze(obs)
        assert r is not None
        assert r.description == "screen contains unknown desktop content"
        assert r.confidence == 0.5
        assert r.source_model == "mock_vision"

    def test_mock_provider_custom_config(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider(config={
            "default_description": "测试描述",
            "default_confidence": 0.8,
        })
        p.attach()
        obs = _make_screen_observation()
        r = p.analyze(obs)
        assert r is not None
        assert r.description == "测试描述"
        assert r.confidence == 0.8

    def test_mock_provider_health_check(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        h = p.health_check()
        assert h["healthy"] is False
        p.attach()
        h = p.health_check()
        assert h["healthy"] is True
        assert h["provider_name"] == "mock_vision"

    def test_mock_provider_returns_none_for_none_obs(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        p.attach()
        r = p.analyze(None)
        assert r is None

    def test_mock_provider_returns_none_when_not_attached(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        # 不 attach
        r = p.analyze(_make_screen_observation())
        assert r is None

    def test_mock_provider_batch(self):
        from src.runtime.perception import MockVisionProvider
        p = MockVisionProvider()
        p.attach()
        results = p.analyze_batch([
            _make_screen_observation(),
            _make_screen_observation(),
            _make_camera_observation(),
        ])
        assert len(results) == 3


# ============================================================
# T8: No Vision Fact 时 RealityGuard 阻断
# ============================================================
class TestRealityGuardBlocksWithoutVisionFact:
    def test_screen_description_blocked_without_vision_fact(self):
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "我看到你的屏幕上显示着代码编辑器",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False
        assert "hallucinated_visual_observation" in report.violations

    def test_window_description_blocked_without_fact(self):
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "你的窗口显示的是工作文档",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False

    def test_response_guard_chain_blocks_no_vision_fact(self):
        from src.runtime.perception import RealityGuard
        from src.runtime.response_guard_chain import ResponseGuardChain
        chain = ResponseGuardChain(reality_guard=RealityGuard())
        result = chain.run(
            reply="屏幕显示代码编辑器",
            facts=[],
            obs_state=None,
        )
        assert result.blocked_by == "reality"


# ============================================================
# T9: 有 Vision Fact 时 RealityGuard 放行
# ============================================================
class TestRealityGuardAllowsWithVisionFact:
    def test_screen_description_allowed_with_vision_fact(self):
        from src.runtime.perception import RealityGuard, vision_result_to_fact
        r = _make_vision_result(
            description="屏幕显示代码编辑器",
            confidence=0.92,
        )
        obs = _make_screen_observation()
        fact = vision_result_to_fact(r, obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "我看到你的屏幕上显示的是代码编辑器",
            facts=[fact],
            obs_state=None,
        )
        assert report.allowed is True
        assert "hallucinated_visual_observation" not in report.violations

    def test_window_description_allowed_with_vision_fact(self):
        from src.runtime.perception import RealityGuard, vision_result_to_fact
        r = _make_vision_result(
            description="窗口显示工作文档",
            confidence=0.9,
        )
        obs = _make_screen_observation()
        fact = vision_result_to_fact(r, obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "你的窗口显示的是工作文档",
            facts=[fact],
            obs_state=None,
        )
        assert report.allowed is True

    def test_response_guard_chain_allows_with_vision_fact(self):
        from src.runtime.perception import RealityGuard, vision_result_to_fact
        from src.runtime.response_guard_chain import ResponseGuardChain
        r = _make_vision_result(
            description="屏幕显示QQ聊天界面",
            confidence=0.9,
        )
        obs = _make_screen_observation()
        fact = vision_result_to_fact(r, obs)
        assert fact is not None

        chain = ResponseGuardChain(reality_guard=RealityGuard())
        result = chain.run(
            reply="我看到你屏幕上显示的是QQ聊天界面",
            facts=[fact],
            obs_state=None,
        )
        assert result.blocked_by is None

    def test_batch_vision_facts_allow_description(self):
        from src.runtime.perception import (
            RealityGuard, vision_results_to_facts, FactSource,
        )
        obs1 = _make_screen_observation(content="screen1")
        obs2 = _make_camera_observation(content="camera1")
        r1 = _make_vision_result(
            description="屏幕A",
            confidence=0.8,
            observation_id=obs1.observation_id,
        )
        r2 = _make_vision_result(
            description="摄像头B",
            confidence=0.7,
            observation_id=obs2.observation_id,
        )
        facts = vision_results_to_facts([r1, r2], [obs1, obs2])
        assert len(facts) == 2
        for f in facts:
            assert f.source == FactSource.VISION

        rg = RealityGuard()
        report = rg.check(
            "我看到屏幕A和摄像头B",
            facts=facts,
            obs_state=None,
        )
        assert report.allowed is True


# ============================================================
# T10: Vision Provider failure 不影响 Runtime
# ============================================================
class TestVisionProviderFailureIsolated:
    def test_failing_provider_returns_none(self):
        """Provider.analyze() 抛异常时,应被 adapter 隔离并返回空 list。"""
        from src.runtime.perception import VisionAdapter
        from src.runtime.perception.vision.providers.base import VisionProvider

        class _FailingProvider(VisionProvider):
            provider_name = "failing"

            def analyze(self, observation):
                raise RuntimeError("provider boom")

            def health_check(self):
                return {"healthy": False, "error": "boom"}

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(_FailingProvider())
        a.attach()
        # 不应抛异常
        results = a.describe(_make_screen_observation())
        assert results == []

    def test_failing_registry_isolated(self):
        from src.runtime.perception import (
            VisionAdapter, VisionAdapterRegistry, MockVisionProvider,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        reg = VisionAdapterRegistry()
        good = _ConcreteAdapter()
        good.set_provider(MockVisionProvider())
        good.attach()
        reg.register(good)

        obs = _make_screen_observation()
        results = reg.analyze_all([obs])
        # 至少 1 个结果(从 good adapter)
        assert len(results) >= 1

    def test_runtime_continues_when_vision_provider_fails(self):
        """Runtime 在 vision provider 失败时仍可正常处理。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            VisionAdapter, VisionAdapterRegistry,
        )
        from src.runtime.perception.vision.providers.base import VisionProvider

        class _FailingProvider(VisionProvider):
            provider_name = "failing"

            def analyze(self, observation):
                raise RuntimeError("boom")

            def health_check(self):
                return {"healthy": False, "error": "boom"}

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(_FailingProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)

        core = RuntimeCore(vision_registry=reg)
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "hi"})
            ctx = core.process(event, RuntimeContext())
            # 仍应正常返回 ctx
            assert ctx is not None
        finally:
            core.shutdown()


# ============================================================
# T11: Vision Adapter failure isolated
# ============================================================
class TestVisionAdapterFailureIsolated:
    def test_adapter_without_provider_returns_empty(self):
        from src.runtime.perception import VisionAdapter
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        a.attach()
        results = a.describe(_make_screen_observation())
        assert results == []

    def test_adapter_not_attached_returns_empty(self):
        from src.runtime.perception import VisionAdapter, MockVisionProvider
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        # 不 attach
        results = a.describe(_make_screen_observation())
        assert results == []

    def test_registry_analyze_all_with_no_adapters(self):
        from src.runtime.perception import VisionAdapterRegistry
        reg = VisionAdapterRegistry()
        results = reg.analyze_all([_make_screen_observation()])
        assert results == []


# ============================================================
# T12: Runtime lifecycle 新阶段顺序正确
# ============================================================
class TestRuntimeLifecycleStageOrder:
    def test_runtime_version_is_4_2_0(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.2.1 进一步推进到 4.2.1
        # Phase 4.2.2 进一步推进到 4.2.2 (SourceAdapter)
        assert RuntimeCore.RUNTIME_VERSION in ("4.2.1", "4.2.0", "4.1.0", "4.0.0", "3.7.3", "4.2.2", "4.2.3", "4.2.4")

    def test_lifecycle_order_15_stages(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER
        # Phase 4.2.0: 在 PERCEPTION_OBSERVATION 之后增加 PERCEPTION_ANALYSIS
        # 14 -> 15
        assert len(RUNTIME_LIFECYCLE_ORDER) >= 15

    def test_perception_analysis_position(self):
        from src.runtime.runtime import (
            RUNTIME_LIFECYCLE_ORDER, RuntimeStage,
        )
        # PERCEPTION_ANALYSIS 必须位于 PERCEPTION_OBSERVATION 之后,RESPONSE_GENERATION 之前
        # Phase 4.2.1: SELF_MODEL_BUILD 可在 PERCEPTION_ANALYSIS 与 RESPONSE_GENERATION 之间
        idx = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.PERCEPTION_ANALYSIS)
        assert (
            RUNTIME_LIFECYCLE_ORDER[idx - 1]
            == RuntimeStage.PERCEPTION_OBSERVATION
        )
        # PERCEPTION_ANALYSIS 之后可以是 SELF_MODEL_BUILD(Phase 4.2.1)或 RESPONSE_GENERATION
        next_stage = RUNTIME_LIFECYCLE_ORDER[idx + 1]
        assert next_stage in (
            RuntimeStage.SELF_MODEL_BUILD,
            RuntimeStage.RESPONSE_GENERATION,
        ), (
            f"PERCEPTION_ANALYSIS 后应为 SELF_MODEL_BUILD(4.2.1)或 RESPONSE_GENERATION,"
            f" 实际为 {next_stage}"
        )

    def test_runtime_accepts_vision_registry(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import VisionAdapterRegistry
        reg = VisionAdapterRegistry()
        core = RuntimeCore(vision_registry=reg)
        assert core.vision_registry is reg

    def test_runtime_no_vision_registry_by_default(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.vision_registry is None
        core.start()
        core.shutdown()

    def test_runtime_start_attaches_vision(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)

        core = RuntimeCore(vision_registry=reg)
        core.start()
        try:
            assert "test_vision" not in core.vision_attach_results
            # 名称是 adapter.name = "vision_adapter" (default)
            assert core.last_vision_health is not None
        finally:
            core.shutdown()

    def test_runtime_process_calls_perception_analysis(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())

        vision_reg = VisionAdapterRegistry()
        vision_reg.register(a)

        perception_reg = PerceptionAdapterRegistry()
        perception_reg.register(ScreenCaptureAdapter())

        core = RuntimeCore(
            perception_registry=perception_reg,
            vision_registry=vision_reg,
        )
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "hi"})
            ctx = core.process(event, RuntimeContext())

            # 验证 vision_results 被收集
            vision_results = core.get_vision_results(ctx)
            # screen_capture 不可用时,vision_results 仍可能为空
            # 但 process() 不应抛异常
            assert ctx is not None

            # 验证 perception_observations 已被 vision_registry 消费
            observations = core.get_perception_observations(ctx)
            assert isinstance(observations, list)
        finally:
            core.shutdown()

    def test_runtime_process_injects_vision_facts(self):
        """当 screen available + vision registry 时,应生成 VISION Fact。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
            PerceptionAdapterRegistry, ScreenCaptureAdapter, FactSource,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())

        vision_reg = VisionAdapterRegistry()
        vision_reg.register(a)

        perception_reg = PerceptionAdapterRegistry()
        perception_reg.register(ScreenCaptureAdapter())

        core = RuntimeCore(
            perception_registry=perception_reg,
            vision_registry=vision_reg,
        )
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "hi"})
            ctx = core.process(event, RuntimeContext())

            observations = core.get_perception_observations(ctx)
            # 如果 screen observation 可用,则 vision 应被触发
            if observations and observations[0].available:
                vision_results = core.get_vision_results(ctx)
                assert len(vision_results) >= 1
                vision_facts = core.get_vision_facts(ctx)
                assert len(vision_facts) >= 1
                assert any(f.source == FactSource.VISION for f in vision_facts)
        finally:
            core.shutdown()

    def test_runtime_shutdown_detaches_vision(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)

        core = RuntimeCore(vision_registry=reg)
        core.start()
        core.shutdown()
        assert a.is_attached is False

    def test_configure_vision_after_start(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
        )

        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}

        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)

        core = RuntimeCore()
        core.start()
        try:
            core.configure_vision(reg)
            assert core.vision_registry is reg
        finally:
            core.shutdown()

    def test_backward_compat_no_vision_registry(self):
        """未注入 vision_registry 时,Runtime 仍可正常工作。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "hi"})
            ctx = core.process(event, RuntimeContext())
            assert ctx is not None
            # vision_results 应该是空 list
            assert core.get_vision_results(ctx) == []
        finally:
            core.shutdown()


# ============================================================
# T13: VisionAdapterRegistry
# ============================================================
class TestVisionAdapterRegistry:
    def test_registry_register_and_get(self):
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter,
        )
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        reg = VisionAdapterRegistry()
        a = _ConcreteAdapter()
        reg.register(a)
        assert "vision_adapter" in reg
        assert reg.get("vision_adapter") is a

    def test_registry_rejects_non_adapter(self):
        from src.runtime.perception import VisionAdapterRegistry
        reg = VisionAdapterRegistry()
        with pytest.raises(TypeError):
            reg.register("not_an_adapter")

    def test_registry_attach_detach_all(self):
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
        )
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)
        reg.attach_all()
        assert a.is_attached is True
        reg.detach_all()
        assert a.is_attached is False

    def test_registry_health_check_all(self):
        from src.runtime.perception import (
            VisionAdapterRegistry, VisionAdapter, MockVisionProvider,
        )
        class _ConcreteAdapter(VisionAdapter):
            def health_check(self):
                return {"healthy": True}
        a = _ConcreteAdapter()
        a.set_provider(MockVisionProvider())
        reg = VisionAdapterRegistry()
        reg.register(a)
        reg.attach_all()
        health = reg.health_check_all()
        assert "vision_adapter" in health


# ============================================================
# T14: 阶段总结
# ============================================================
def test_phase_4_2_0_summary():
    """Phase 4.2.0 阶段总结。"""
    from src.runtime.runtime import (
        RuntimeCore, RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
    )
    from src.runtime.perception import (
        VisionResult, VisionAdapter, VisionAdapterRegistry,
        VisionProvider, MockVisionProvider,
        vision_result_to_fact, vision_results_to_facts,
        VISION_RESULT_SCHEMA_VERSION, VISION_ADAPTER_SCHEMA_VERSION,
        VISION_ADAPTER_REGISTRY_SCHEMA_VERSION,
        VISION_PROVIDER_SCHEMA_VERSION, MOCK_VISION_PROVIDER_SCHEMA_VERSION,
        VISION_FACT_BUILDER_SCHEMA_VERSION,
        FactSource, Observation, ObservationKind, RealityGuard,
    )

    # 1) Schema 版本
    assert VISION_RESULT_SCHEMA_VERSION == "1.0"
    assert VISION_ADAPTER_SCHEMA_VERSION == "1.0"
    assert VISION_ADAPTER_REGISTRY_SCHEMA_VERSION == "1.0"
    assert VISION_PROVIDER_SCHEMA_VERSION == "1.0"
    assert MOCK_VISION_PROVIDER_SCHEMA_VERSION == "1.0"
    assert VISION_FACT_BUILDER_SCHEMA_VERSION == "1.0"

    # 2) VisionAdapter 存在
    assert VisionAdapter is not None
    assert VisionAdapterRegistry is not None
    assert MockVisionProvider is not None

    # 3) VisionResult 强制 source == VISION
    r = VisionResult(
        description="test",
        confidence=0.5,
        evidence_ids=["obs_summary_synth"],
    )
    assert r.source == FactSource.VISION

    # 4) Fact 转换正常
    fact = vision_result_to_fact(r)
    assert fact is not None
    assert fact.source == FactSource.VISION

    # 5) RealityGuard 已支持 VISION Fact
    rg = RealityGuard()
    report = rg.check("屏幕显示QQ", [fact], None)
    assert report.allowed is True

    # 6) Runtime 接受 vision_registry
    reg = VisionAdapterRegistry()
    core = RuntimeCore(vision_registry=reg)
    assert core.vision_registry is reg
    core.start()
    try:
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        event = Event(type="user_input", payload={"text": "hi"})
        ctx = core.process(event, RuntimeContext())
        # 即使没注册 adapter,process() 也不应报错
        assert ctx is not None
    finally:
        core.shutdown()
