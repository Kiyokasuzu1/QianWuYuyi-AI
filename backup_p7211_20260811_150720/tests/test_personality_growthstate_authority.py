# -*- coding: utf-8 -*-
"""
tests/test_personality_growthstate_authority.py

Phase 4.4.1 PersonalityResolver × GrowthState Authority 修复测试。

验证 RuntimeCore 创建的 PersonalityResolver 与 GrowthState 使用同一实例。

覆盖目标：
A. RuntimeCore: get_growth_state() is get_personality_resolver().state
B. RuntimeBridge: bridge.get_growth_state() is bridge.get_personality_resolver().state
C. GrowthEngine 注入后 engine.state is resolver.state
D. Fallback: PersonalityResolver() 无参仍可正常创建
E. 跨 Phase 回归保护
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest


# =====================================================================
# 临时工作目录 fixture
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_pg_auth_")
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
# A. RuntimeCore: GrowthState 与 PersonalityResolver.state 共享
# =====================================================================
class TestRuntimeCoreSharedGrowthState:

    def test_growth_state_is_resolver_state(self):
        """RuntimeCore.get_growth_state() is get_personality_resolver().state"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        gs = rc.get_growth_state()
        resolver = rc.get_personality_resolver()

        assert gs is not None
        assert resolver is not None
        assert resolver.state is gs

    def test_multiple_calls_return_same_pair(self):
        """多次调用返回同一对实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        gs1 = rc.get_growth_state()
        resolver1 = rc.get_personality_resolver()
        gs2 = rc.get_growth_state()
        resolver2 = rc.get_personality_resolver()

        assert gs1 is gs2
        assert resolver1 is resolver2
        assert resolver1.state is gs1


# =====================================================================
# B. RuntimeBridge: 转发后仍共享
# =====================================================================
class TestRuntimeBridgeSharedGrowthState:

    def test_bridge_growth_state_is_resolver_state(self):
        """bridge.get_growth_state() is bridge.get_personality_resolver().state"""
        bridge = _init_runtime_bridge()

        gs = bridge.get_growth_state()
        resolver = bridge.get_personality_resolver()

        assert gs is not None
        assert resolver is not None
        assert resolver.state is gs

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge(config={"autostart": False})
        # 不调用 initialize()
        assert bridge.get_growth_state() is None
        assert bridge.get_personality_resolver() is None


# =====================================================================
# C. GrowthEngine 注入后与 Resolver 共享
# =====================================================================
class TestGrowthEngineSharedWithResolver:

    def test_engine_state_is_resolver_state(self):
        """GrowthEngine 注入 RuntimeCore GrowthState 后，engine.state is resolver.state"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        shared_gs = rc.get_growth_state()
        from src.growth.growth_engine import GrowthEngine
        engine = GrowthEngine(state=shared_gs)

        resolver = rc.get_personality_resolver()

        assert engine.state is resolver.state

    def test_pipeline_shares_state_through_runtime_core(self):
        """GrowthPipeline 注入 RuntimeCore GrowthState 后，engine.state 与 resolver.state 共享。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        shared_gs = rc.get_growth_state()
        from src.growth.pipeline import GrowthPipeline
        pipeline = GrowthPipeline(growth_state=shared_gs)

        resolver = rc.get_personality_resolver()

        assert pipeline.growth_engine.state is resolver.state


# =====================================================================
# D. Fallback: PersonalityResolver 无参仍可正常创建
# =====================================================================
class TestFallbackCompatibility:

    def test_resolver_no_args_works(self):
        """PersonalityResolver() 无参仍可正常创建。"""
        from src.personality.personality_resolver import PersonalityResolver

        resolver = PersonalityResolver()
        assert resolver.state is not None
        state = resolver.state.get()
        assert "metrics" in state

    def test_resolver_none_state_falls_back(self):
        """PersonalityResolver(state=None) 等价于无参。"""
        from src.personality.personality_resolver import PersonalityResolver

        resolver = PersonalityResolver(state=None)
        assert resolver.state is not None

    def test_growth_engine_no_args_works(self):
        """GrowthEngine() 无参仍可正常创建。"""
        from src.growth.growth_engine import GrowthEngine

        engine = GrowthEngine()
        assert engine.state is not None


# =====================================================================
# E. 跨 Phase 回归保护
# =====================================================================
class TestCrossPhaseRegression:

    def test_emotion_manager_still_works(self):
        """EmotionManager Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        em = bridge.get_emotion_manager()
        # emotion_enabled=False 时可能为 None
        rc = bridge._runtime_core
        em_rc = rc.get_emotion_manager()
        assert em is em_rc

    def test_self_model_store_still_works(self):
        """SelfModelStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        sms1 = bridge.get_self_model_store()
        sms2 = bridge._runtime_core.get_self_model_store()
        assert sms1 is sms2

    def test_personality_resolver_still_works(self):
        """PersonalityResolver Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        pr1 = bridge.get_personality_resolver()
        pr2 = bridge._runtime_core.get_personality_resolver()
        assert pr1 is pr2

    def test_memory_store_still_works(self):
        """MemoryStore Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        ms1 = bridge.get_memory_store()
        ms2 = bridge._runtime_core.get_memory_store()
        assert ms1 is ms2

    def test_vector_memory_still_works(self):
        """VectorMemory Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        vm1 = bridge.get_vector_memory()
        vm2 = bridge._runtime_core.get_vector_memory()
        assert vm1 is vm2

    def test_growth_state_still_works(self):
        """GrowthState Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        gs1 = bridge.get_growth_state()
        gs2 = bridge._runtime_core.get_growth_state()
        assert gs1 is gs2
