# -*- coding: utf-8 -*-
"""
tests/test_phase_4_1_screen_perception.py

Phase 4.1.0: Screen Perception Adapter 真实实现测试

覆盖:
1. ScreenCaptureAdapter 初始化
2. health_check 正常
3. 无屏幕权限时返回 available=False
4. Observation source 必须为 VISION
5. Observation 必须经过 Fact 转换
6. Runtime 不直接 import mss / dxcam
7. RealityGuard 有真实 Observation 时允许描述
8. RealityGuard 无 Observation 时阻止视觉幻觉
9. PerceptionAdapterRegistry 集成
10. Runtime PERCEPTION_OBSERVATION 阶段激活
11. Runtime 生命周期向后兼容 (未注入 perception_registry 时仍可工作)
12. RuntimeContext schema 仍为 1.0
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
    available=True,
    source=None,
    kind=None,
    content: str = "Screen capture available: 1920x1080",
    confidence: float = 0.9,
):
    """构造一个合法的 SCREEN Observation (用于 RealityGuard 测试)。"""
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
    )


# ============================================================
# T1: ScreenCaptureAdapter 初始化
# ============================================================
class TestScreenCaptureAdapterInit:
    def test_can_instantiate(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        assert adapter is not None
        assert adapter.name == "screen_capture_adapter"
        assert adapter.observation_kind.value == "screen"
        assert adapter.schema_version == "1.0"

    def test_initially_detached(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        assert adapter.is_attached is False

    def test_initial_screen_unavailable(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        # attach() 之前 _screen_available 应为 False
        assert adapter._screen_available is False

    def test_subclass_of_perception_adapter(self):
        from src.runtime.perception import (
            ScreenCaptureAdapter, PerceptionAdapter,
        )
        assert issubclass(ScreenCaptureAdapter, PerceptionAdapter)

    def test_can_pass_config(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter(config={"key": "value"})
        assert adapter._config == {"key": "value"}


# ============================================================
# T2: health_check
# ============================================================
class TestHealthCheck:
    def test_health_check_returns_dict(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        health = adapter.health_check()
        assert isinstance(health, dict)
        assert "healthy" in health
        assert "name" in health
        assert "schema_version" in health
        assert "observation_kind" in health
        assert "details" in health

    def test_health_check_schema_version(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        health = adapter.health_check()
        assert health["schema_version"] == "1.0"
        assert health["observation_kind"] == "screen"

    def test_health_check_after_detach(self):
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        adapter.detach()
        health = adapter.health_check()
        assert health["healthy"] is False
        assert health["details"]["screen_available"] is False


# ============================================================
# T3: 无屏幕权限时返回 available=False
# ============================================================
class TestNoScreenPermission:
    def test_attach_when_detached_returns_none_on_observe(self):
        """未 attach 时,observe() 应返回 None,不应抛异常。"""
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        # 不调用 attach
        result = adapter.observe()
        assert result is None

    def test_no_double_attach(self):
        """重复 attach 不会出错。"""
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        adapter.attach()  # 第二次 attach
        assert adapter.is_attached is True

    def test_observe_does_not_raise(self):
        """observe() 在任何情况下都不抛异常(异常隔离)。"""
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        try:
            obs = adapter.observe()
            # 可能 None,可能是 Observation(available=True/False)
            # 但绝不应抛异常
        except Exception as exc:
            pytest.fail(f"observe() should not raise, got: {exc}")


# ============================================================
# T4: Observation source 必须为 VISION
# ============================================================
class TestObservationSource:
    def test_observation_source_is_vision(self):
        """observe() 返回的 Observation.source 应为 FactSource.VISION。"""
        from src.runtime.perception import (
            ScreenCaptureAdapter, FactSource,
        )
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        obs = adapter.observe()
        if obs is not None:
            assert obs.source == FactSource.VISION

    def test_observation_kind_is_screen(self):
        """observe() 返回的 Observation.kind 应为 ObservationKind.SCREEN。"""
        from src.runtime.perception import (
            ScreenCaptureAdapter, ObservationKind,
        )
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        obs = adapter.observe()
        if obs is not None:
            assert obs.kind == ObservationKind.SCREEN

    def test_observation_has_required_fields(self):
        """Observation 必须有所有 Phase 4.0.0 字段。"""
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        obs = adapter.observe()
        if obs is not None:
            for field in [
                "observation_id", "timestamp", "source", "content",
                "confidence", "evidence_ids", "kind", "available",
                "id", "meta",
            ]:
                assert hasattr(obs, field), f"missing field: {field}"

    def test_observation_meta_includes_screen_info(self):
        """当屏幕可用时,Observation.meta 应包含 width/height 等信息。"""
        from src.runtime.perception import ScreenCaptureAdapter
        adapter = ScreenCaptureAdapter()
        adapter.attach()
        obs = adapter.observe()
        if obs is not None and obs.available:
            assert "width" in obs.meta
            assert "height" in obs.meta
            assert obs.meta["width"] > 0
            assert obs.meta["height"] > 0
            assert "phase" in obs.meta
            assert obs.meta["phase"] == "4.1.0"


# ============================================================
# T5: Observation 必须经过 Fact 转换
# ============================================================
class TestObservationToFactConversion:
    def test_observation_can_be_converted_to_fact(self):
        """SCREEN Observation 应能通过 observation_to_fact 转换为 Fact。"""
        from src.runtime.perception import (
            observation_to_fact, FactSource,
        )
        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None
        assert fact.source == FactSource.VISION
        assert fact.content == obs.content
        assert fact.confidence == obs.confidence
        assert obs.observation_id in fact.evidence_ids

    def test_observation_meta_in_fact(self):
        """Fact.meta 应包含 observation 元信息。"""
        from src.runtime.perception import observation_to_fact
        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None
        assert "observation_id" in fact.meta
        assert "observation_kind" in fact.meta
        assert "observation_timestamp" in fact.meta

    def test_unavailable_observation_returns_none(self):
        """available=False 的 Observation 转换应返回 None。"""
        from src.runtime.perception import observation_to_fact
        obs = _make_observation(available=False)
        fact = observation_to_fact(obs)
        assert fact is None

    def test_batch_conversion(self):
        """observations_to_facts 应正确过滤 INFERENCE 等。"""
        from src.runtime.perception import (
            observations_to_facts, FactSource, ObservationKind,
        )
        observations = [
            _make_observation(source=FactSource.VISION, kind=ObservationKind.SCREEN),
            _make_observation(available=False),  # 不可用,被过滤
        ]
        facts = observations_to_facts(observations)
        assert len(facts) == 1
        assert facts[0].source == FactSource.VISION


# ============================================================
# T6: Runtime 不直接 import mss / dxcam
# ============================================================
class TestRuntimeNoDirectVisualImport:
    RUNTIME_FILE = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
    IMPL_FILE = PROJECT_ROOT / "src" / "runtime" / "perception" / "impl" / "screen_capture_adapter.py"

    def test_runtime_does_not_import_mss(self):
        """runtime.py 不得直接 import mss。"""
        text = self.RUNTIME_FILE.read_text(encoding="utf-8")
        for forbidden in ["import mss", "from mss", "import dxcam", "from dxcam"]:
            assert forbidden not in text, (
                f"runtime.py contains forbidden import: {forbidden!r}"
            )

    def test_perception_pkg_does_not_import_mss_at_module_level(self):
        """perception/__init__.py 不得在 module level 加载 mss。"""
        perception_init = (
            PROJECT_ROOT / "src" / "runtime" / "perception" / "__init__.py"
        )
        text = perception_init.read_text(encoding="utf-8")
        # 允许 import ScreenCaptureAdapter (它是 lazy import mss)
        # 但不允许顶层 import mss 本身
        assert "import mss" not in text, "perception __init__.py imports mss at module level"
        assert "from mss" not in text, "perception __init__.py imports mss at module level"

    def test_impl_uses_lazy_mss_import(self):
        """impl/screen_capture_adapter.py 的 mss 引用必须在函数体内(lazy)。"""
        text = self.IMPL_FILE.read_text(encoding="utf-8")
        # 必须有 lazy import 模式
        assert "import mss" in text, "ScreenCaptureAdapter should import mss"
        # 确保 mss 不在模块顶层 import
        # (仅检查模块级 vs 函数内)
        lines = text.split("\n")
        in_function = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("class "):
                in_function = True
            if not in_function and "import mss" in line and not line.strip().startswith("#"):
                pytest.fail(f"mss imported at module level: {line!r}")


# ============================================================
# T7: RealityGuard 有真实 Observation 时允许描述
# ============================================================
class TestRealityGuardWithScreenObservation:
    def test_screen_description_allowed_with_vision_fact(self):
        """有 VISION Fact 时,描述屏幕应被允许。"""
        from src.runtime.perception import (
            RealityGuard, observation_to_fact,
        )
        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "我看到你屏幕上显示的是 QQ 聊天界面",
            facts=[fact],
            obs_state=None,
        )
        assert report.allowed is True
        assert "hallucinated_visual_observation" not in report.violations

    def test_window_description_allowed_with_vision_fact(self):
        """有 VISION Fact 时,描述窗口应被允许。"""
        from src.runtime.perception import (
            RealityGuard, observation_to_fact,
        )
        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "你的窗口显示的是浏览器",
            facts=[fact],
            obs_state=None,
        )
        assert report.allowed is True

    def test_game_description_allowed_with_vision_fact(self):
        """有 VISION Fact 时,描述游戏场景应被允许。"""
        from src.runtime.perception import (
            RealityGuard, observation_to_fact,
        )
        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None

        rg = RealityGuard()
        report = rg.check(
            "你正在玩的是一个 RPG 游戏",
            facts=[fact],
            obs_state=None,
        )
        # 注意: "正在玩" 是视觉描述,有 VISION Fact 时应允许
        assert report.allowed is True

    def test_response_guard_chain_allows_with_screen_obs(self):
        """ResponseGuardChain 应让带屏幕 Observation 的回复通过。"""
        from src.runtime.perception import observation_to_fact
        from src.runtime.response_guard_chain import ResponseGuardChain

        obs = _make_observation()
        fact = observation_to_fact(obs)
        assert fact is not None

        chain = ResponseGuardChain()
        result = chain.run(
            reply="我看到你屏幕上显示的是 QQ 聊天界面",
            facts=[fact],
            obs_state=None,
        )
        assert result.blocked_by is None


# ============================================================
# T8: RealityGuard 无 Observation 时阻止视觉幻觉
# ============================================================
class TestRealityGuardBlocksHallucination:
    def test_screen_description_blocked_without_fact(self):
        """无 Fact 时,描述屏幕应被阻止。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "我看到你屏幕上显示的是 QQ 聊天界面",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False
        assert "hallucinated_visual_observation" in report.violations

    def test_game_description_blocked_without_fact(self):
        """无 Fact 时,描述游戏应被阻止。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "你刚才在玩 LOL 对吧",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False

    def test_window_description_blocked_without_fact(self):
        """无 Fact 时,描述窗口应被阻止。"""
        from src.runtime.perception import RealityGuard
        rg = RealityGuard()
        report = rg.check(
            "你的窗口显示的是工作文档",
            facts=[],
            obs_state=None,
        )
        assert report.allowed is False

    def test_response_guard_chain_blocks_hallucination(self):
        """ResponseGuardChain 应阻止无 Observation 的视觉幻觉。"""
        from src.runtime.response_guard_chain import ResponseGuardChain

        chain = ResponseGuardChain()
        result = chain.run(
            reply="我看到你正在玩 LOL",
            facts=[],
            obs_state=None,
        )
        assert result.blocked_by == "reality"
        # 应该是 refusal 文本
        assert "没有看到" in result.final_reply or "没有" in result.final_reply or \
               result.final_reply != "我看到你正在玩 LOL"


# ============================================================
# T9: PerceptionAdapterRegistry 集成
# ============================================================
class TestRegistryIntegration:
    def test_registry_can_register_screen_capture_adapter(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        adapter = ScreenCaptureAdapter()
        reg.register(adapter)
        assert "screen_capture_adapter" in reg

    def test_registry_attach_all(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        reg.attach_all()
        adapter = reg.get("screen_capture_adapter")
        assert adapter.is_attached is True

    def test_registry_health_check_all(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        reg.attach_all()
        results = reg.health_check_all()
        assert "screen_capture_adapter" in results
        assert results["screen_capture_adapter"]["name"] == "screen_capture_adapter"

    def test_registry_observe_all_returns_observations(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        reg.attach_all()
        observations = reg.observe_all()
        # 至少 1 个 Observation (即使 available=False)
        assert len(observations) >= 1
        for obs in observations:
            assert obs.kind.value == "screen"
            assert obs.source.value == "vision"

    def test_registry_detach_all(self):
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        reg.attach_all()
        reg.detach_all()
        adapter = reg.get("screen_capture_adapter")
        assert adapter.is_attached is False


# ============================================================
# T10: Runtime PERCEPTION_OBSERVATION 阶段激活
# ============================================================
class TestRuntimePerceptionStage:
    def test_runtime_accepts_perception_registry(self):
        """RuntimeCore 接受 perception_registry 参数(向后兼容)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import PerceptionAdapterRegistry
        reg = PerceptionAdapterRegistry()
        core = RuntimeCore(perception_registry=reg)
        assert core.perception_registry is reg

    def test_runtime_no_perception_registry_by_default(self):
        """未注入 perception_registry 时,Runtime 仍可启动(向后兼容)。"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.perception_registry is None
        # 启动不应报错
        core.start()
        core.shutdown()

    def test_runtime_start_attaches_perception(self):
        """Runtime.start() 会自动 attach 注入的 perception_registry。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        core = RuntimeCore(perception_registry=reg)
        core.start()
        try:
            assert "screen_capture_adapter" in core.perception_attach_results
            assert core.last_perception_health is not None
        finally:
            core.shutdown()

    def test_runtime_process_collects_observations(self):
        """Runtime.process() 应调用 PERCEPTION_OBSERVATION 阶段并收集 Observations。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        core = RuntimeCore(perception_registry=reg)
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "你好"})
            ctx = core.process(event, RuntimeContext())
            observations = core.get_perception_observations(ctx)
            # 至少 1 个 Observation
            assert len(observations) >= 1
        finally:
            core.shutdown()

    def test_runtime_process_injects_facts(self):
        """Runtime.process() 应将 Observation 转换的 Fact 注入 _draft_facts。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter, FactSource,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        core = RuntimeCore(perception_registry=reg)
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "你好"})
            ctx = core.process(event, RuntimeContext())
            facts = core.get_perception_facts(ctx)
            # 屏幕可用时,至少 1 个 VISION Fact
            if observations := core.get_perception_observations(ctx):
                if observations[0].available:
                    assert any(
                        f.source == FactSource.VISION for f in facts
                    )
        finally:
            core.shutdown()

    def test_runtime_process_creates_observation_state(self):
        """Runtime.process() 应创建 ObservationState(用于 RealityGuard)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        core = RuntimeCore(perception_registry=reg)
        core.start()
        try:
            event = Event(type="user_input", payload={"text": "你好"})
            ctx = core.process(event, RuntimeContext())
            state = core.get_perception_state(ctx)
            assert state is not None
        finally:
            core.shutdown()

    def test_runtime_shutdown_detaches_perception(self):
        """Runtime.shutdown() 应 detach perception_registry。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        reg = PerceptionAdapterRegistry()
        reg.register(ScreenCaptureAdapter())
        core = RuntimeCore(perception_registry=reg)
        core.start()
        core.shutdown()
        adapter = reg.get("screen_capture_adapter")
        assert adapter.is_attached is False

    def test_configure_perception_after_start(self):
        """Runtime.configure_perception() 允许运行时注入。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.perception import (
            PerceptionAdapterRegistry, ScreenCaptureAdapter,
        )
        core = RuntimeCore()
        core.start()
        try:
            reg = PerceptionAdapterRegistry()
            reg.register(ScreenCaptureAdapter())
            core.configure_perception(reg)
            assert core.perception_registry is reg
            # 注入后应自动 attach
            assert "screen_capture_adapter" in core.perception_attach_results
        finally:
            core.shutdown()


# ============================================================
# T11: Runtime 生命周期向后兼容
# ============================================================
class TestBackwardCompatibility:
    def test_runtime_context_schema_unchanged(self):
        """RuntimeContext schema 仍为 1.0。"""
        from src.runtime.context import RuntimeContext, RUNTIME_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"
        ctx = RuntimeContext()
        assert ctx.schema_version == "1.0"

    def test_event_schema_unchanged(self):
        """Event schema 字段未变化。"""
        from src.runtime.events import Event
        evt = Event()
        for field in [
            "id", "type", "source", "timestamp",
            "payload", "related_ids", "priority", "metadata",
        ]:
            assert hasattr(evt, field), f"missing field: {field}"

    def test_runtime_stage_count_unchanged(self):
        """RuntimeStage:
        - Phase 4.0.0 + Phase 4.1.0: 14 个
        - Phase 4.2.0: 15 个 (新增 PERCEPTION_ANALYSIS)
        - Phase 4.2.1: 16 个 (新增 SELF_MODEL_BUILD)
        """
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER
        assert len(RUNTIME_LIFECYCLE_ORDER) in (14, 15, 16)

    def test_no_perception_stage_in_lifecycle_order(self):
        """PERCEPTION_OBSERVATION 应位于 PERSONALITY_CONTEXT_BUILD 与 RESPONSE_GENERATION 之间。
        Phase 4.2.0: 中间可插入 PERCEPTION_ANALYSIS。
        """
        from src.runtime.runtime import (
            RUNTIME_LIFECYCLE_ORDER, RuntimeStage,
        )
        idx = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.PERCEPTION_OBSERVATION)
        assert (
            RUNTIME_LIFECYCLE_ORDER[idx - 1]
            == RuntimeStage.PERSONALITY_CONTEXT_BUILD
        )
        # Phase 4.2.0: PERCEPTION_ANALYSIS 可能插在 PERCEPTION_OBSERVATION 之后
        next_stage = RUNTIME_LIFECYCLE_ORDER[idx + 1]
        assert next_stage in (
            RuntimeStage.RESPONSE_GENERATION,
            RuntimeStage.PERCEPTION_ANALYSIS,
        )

    def test_old_adapter_registry_api_unchanged(self):
        """Phase 3.7.3 的 AdapterRegistry 注入仍可工作(向后兼容)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        reg = AdapterRegistry()
        core = RuntimeCore(adapter_registry=reg)
        assert core.adapter_registry is reg
        core.start()
        core.shutdown()

    def test_old_port_based_api_unchanged(self):
        """Phase 3.7.0 的 *_port 注入仍可工作(向后兼容)。"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore(
            memory_port=type("FakePort", (), {
                "retrieve": lambda ctx: None,
                "load_state": lambda: None,
                "save_state": lambda x: None,
            })(),
        )
        core.start()
        core.shutdown()


# ============================================================
# T12: 阶段总结
# ============================================================
def test_phase_4_1_0_summary():
    """Phase 4.1.0 阶段总结。"""
    from src.runtime.runtime import (
        RuntimeCore, RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
    )
    from src.runtime.perception import (
        PerceptionAdapter, PerceptionAdapterRegistry,
        ScreenCaptureAdapter, SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION,
        RealityGuard, observation_to_fact,
        FactSource, ObservationKind, Observation,
    )
    from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION

    # 1) Schema 版本
    assert SCREEN_CAPTURE_ADAPTER_SCHEMA_VERSION == "1.0"
    assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"

    # 2) ScreenCaptureAdapter 存在
    assert ScreenCaptureAdapter is not None
    assert issubclass(ScreenCaptureAdapter, PerceptionAdapter)

    # 3) RealityGuard 已支持 VISION Fact
    rg = RealityGuard()
    obs = _make_observation()
    fact = observation_to_fact(obs)
    assert fact is not None
    report = rg.check("我看到你屏幕上显示的是 QQ", [fact], None)
    assert report.allowed is True

    # 4) Runtime 接受 perception_registry
    reg = PerceptionAdapterRegistry()
    reg.register(ScreenCaptureAdapter())
    core = RuntimeCore(perception_registry=reg)
    core.start()
    try:
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        event = Event(type="user_input", payload={"text": "hi"})
        ctx = core.process(event, RuntimeContext())
        observations = core.get_perception_observations(ctx)
        assert len(observations) >= 1
    finally:
        core.shutdown()
