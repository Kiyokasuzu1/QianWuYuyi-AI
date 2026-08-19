# -*- coding: utf-8 -*-
"""
tests/test_growth_state_authority.py

Phase 4.3.3 GrowthState Authority 测试套件。

覆盖目标：
A. RuntimeCore.get_growth_state() lazy 创建测试
B. RuntimeCore 与 RuntimeBridge 返回同一个实例
C. GrowthEngine 注入测试
D. PersonalityResolver 注入测试
E. GrowthPipeline 内 GrowthEngine 与 PersonalityResolver 共享同一 GrowthState
F. Fallback 兼容性（GrowthEngine 无参仍可运行）
G. 跨 Phase 回归保护
"""
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# =====================================================================
# 临时工作目录 fixture
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_growth_auth_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_runtime_bridge():
    """每个测试前重置 RuntimeBridge 单例。"""
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass
    yield
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass


# =====================================================================
# 工具函数
# =====================================================================
def _init_runtime_bridge():
    """初始化 RuntimeBridge 和 RuntimeCore。"""
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={
        "emotion_enabled": False,
        "adapters_enabled": False,
        "autostart": False,
    })
    bridge.initialize()
    return bridge


# =====================================================================
# A. RuntimeCore.get_growth_state() lazy 创建测试
# =====================================================================
class TestGrowthStateLazyCreation:

    def test_lazy_creates_on_first_call(self):
        """首次调用 get_growth_state() 应创建实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        # 初始状态：_lazy_growth_state 不存在或为 None
        assert not hasattr(rc, "_lazy_growth_state") or rc._lazy_growth_state is None

        gs = rc.get_growth_state()
        assert gs is not None
        assert rc._lazy_growth_state is gs

    def test_returns_same_instance_on_multiple_calls(self):
        """多次调用返回同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        gs1 = rc.get_growth_state()
        gs2 = rc.get_growth_state()
        assert gs1 is gs2

    def test_returns_none_on_failure(self):
        """RuntimeCore 创建失败时返回 None。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        # 确保属性存在且为 None
        rc._lazy_growth_state = None
        with patch("src.growth.growth_state.GrowthState.__init__",
                   side_effect=RuntimeError("mock failure")):
            result = rc.get_growth_state()
            assert result is None


# =====================================================================
# B. RuntimeCore 与 RuntimeBridge 返回同一个实例
# =====================================================================
class TestRuntimeBridgeForwarding:

    def test_bridge_returns_core_instance(self):
        """RuntimeBridge.get_growth_state() 返回 RuntimeCore 的实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        gs_core = rc.get_growth_state()
        gs_bridge = bridge.get_growth_state()

        assert gs_core is gs_bridge

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge(config={"autostart": False})
        # 不调用 initialize()
        assert bridge.get_growth_state() is None


# =====================================================================
# C. GrowthEngine 注入测试
# =====================================================================
class TestGrowthEngineInjection:

    def test_engine_uses_injected_state(self):
        """GrowthEngine 使用注入的 GrowthState 实例。"""
        from src.growth.growth_state import GrowthState
        from src.growth.growth_engine import GrowthEngine

        gs = GrowthState()
        engine = GrowthEngine(state=gs)

        assert engine.state is gs

    def test_engine_fallback_creates_own_state(self):
        """GrowthEngine 无参时自建 GrowthState（fallback）。"""
        from src.growth.growth_engine import GrowthEngine

        engine = GrowthEngine()
        assert engine.state is not None

    def test_engine_none_param_falls_back(self):
        """GrowthEngine(state=None) 等价于无参。"""
        from src.growth.growth_engine import GrowthEngine

        engine = GrowthEngine(state=None)
        assert engine.state is not None


# =====================================================================
# D. PersonalityResolver 注入测试
# =====================================================================
class TestPersonalityResolverInjection:

    def test_resolver_uses_injected_state(self):
        """PersonalityResolver 使用注入的 GrowthState 实例。"""
        from src.growth.growth_state import GrowthState
        from src.personality.personality_resolver import PersonalityResolver

        gs = GrowthState()
        resolver = PersonalityResolver(state=gs)

        assert resolver.state is gs

    def test_resolver_fallback_creates_own_state(self):
        """PersonalityResolver 无参时自建 GrowthState（fallback）。"""
        from src.personality.personality_resolver import PersonalityResolver

        resolver = PersonalityResolver()
        assert resolver.state is not None


# =====================================================================
# E. GrowthPipeline 内共享同一 GrowthState
# =====================================================================
class TestGrowthPipelineSharedState:

    def test_pipeline_shares_state_between_engine_and_resolver(self):
        """GrowthPipeline 内 GrowthEngine.state 和 PersonalityResolver.state 是同一实例。"""
        from src.growth.pipeline import GrowthPipeline

        pipeline = GrowthPipeline()

        assert pipeline.growth_engine.state is pipeline.resolver.state

    def test_pipeline_with_injected_state_shares_everywhere(self):
        """GrowthPipeline 注入 GrowthState 后，GrowthEngine 和 PersonalityResolver 共享。"""
        from src.growth.growth_state import GrowthState
        from src.growth.pipeline import GrowthPipeline

        gs = GrowthState()
        pipeline = GrowthPipeline(growth_state=gs)

        assert pipeline.growth_engine.state is gs
        assert pipeline.resolver.state is gs

    def test_pipeline_no_injection_still_works(self):
        """GrowthPipeline 无注入时仍正常工作（fallback）。"""
        from src.growth.pipeline import GrowthPipeline

        pipeline = GrowthPipeline()
        assert pipeline.growth_engine.state is not None
        assert pipeline.resolver.state is not None
        assert pipeline.growth_engine.state is pipeline.resolver.state


# =====================================================================
# F. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:

    def test_growth_engine_no_args_works(self):
        """GrowthEngine() 无参仍可独立运行。"""
        from src.growth.growth_engine import GrowthEngine

        engine = GrowthEngine()
        state = engine.get_state()
        assert "metrics" in state

    def test_growth_pipeline_no_args_works(self):
        """GrowthPipeline() 无参仍可独立运行。"""
        from src.growth.pipeline import GrowthPipeline

        pipeline = GrowthPipeline()
        assert pipeline.growth_engine is not None
        assert pipeline.resolver is not None

    def test_runtime_core_growth_state_works_after_init(self):
        """RuntimeCore 初始化后 get_growth_state() 可正常返回。"""
        bridge = _init_runtime_bridge()
        gs = bridge.get_growth_state()
        assert gs is not None
        state = gs.get()
        assert "metrics" in state


# =====================================================================
# G. 跨 Phase 回归保护
# =====================================================================
class TestCrossPhaseRegression:

    def test_emotion_manager_still_shared(self):
        """EmotionManager Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        em1 = bridge.get_emotion_manager()
        em2 = bridge._runtime_core.get_emotion_manager()
        assert em1 is em2 or em1 is None  # emotion_enabled=False 时可能为 None

    def test_self_model_store_still_shared(self):
        """SelfModelStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        sms1 = bridge.get_self_model_store()
        sms2 = bridge._runtime_core.get_self_model_store()
        assert sms1 is sms2

    def test_personality_resolver_still_shared(self):
        """PersonalityResolver Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        pr1 = bridge.get_personality_resolver()
        pr2 = bridge._runtime_core.get_personality_resolver()
        assert pr1 is pr2

    def test_memory_store_still_shared(self):
        """MemoryStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        ms1 = bridge.get_memory_store()
        ms2 = bridge._runtime_core.get_memory_store()
        assert ms1 is ms2

    def test_vector_memory_still_shared(self):
        """VectorMemory Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        vm1 = bridge.get_vector_memory()
        vm2 = bridge._runtime_core.get_vector_memory()
        assert vm1 is vm2
