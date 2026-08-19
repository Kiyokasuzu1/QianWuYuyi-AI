# -*- coding: utf-8 -*-
"""
tests/test_runtime_integration.py

Phase 4.5 Runtime Integration Smoke Test。

目标：
验证 RuntimeCore Authority 架构在完整对话生命周期中是否真正连通。

完整链路：
    User Input
        ↓
    Orchestrator
        ↓
    RuntimeCore（Authority 唯一持有者）
        ↓
    MemoryStore / VectorMemory / EmotionManager
        ↓
    PersonalityResolver（共享 GrowthState）
        ↓
    GrowthState（共享实例）
        ↓
    Response Context

覆盖目标：
A. 单次生命周期中所有 Authority 实例唯一
B. Orchestrator 与 RuntimeCore Authority 实例完全一致
C. PersonalityResolver.state 与 RuntimeCore.get_growth_state() 同一实例
D. 模拟真实对话：Memory 写入 → Emotion 更新 → Growth 应用 → Personality resolve → Context 生成
E. Fallback 兼容性：RuntimeBridge 不可用时各模块仍可工作
"""

import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Module-scoped: 设置测试用假 API Key（Orchestrator 实例化 ResponseEngine 需要）
# =====================================================================
os.environ.setdefault("DEEPSEEK_API_KEY", "test-fake-key-for-integration-tests")


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_runtime_intg_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_runtime_bridge():
    """每个测试前后重置 RuntimeBridge 单例。"""
    try:
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
    except Exception:
        pass
    yield
    try:
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
    except Exception:
        pass


def _init_runtime_bridge():
    """初始化 RuntimeBridge 与 RuntimeCore。"""
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={
        "emotion_enabled": False,
        "adapters_enabled": False,
        "autostart": False,
    })
    bridge.initialize()
    return bridge


# =====================================================================
# A. 单次生命周期：Authority 实例唯一性
# =====================================================================
class TestLifecycleAuthorityUniqueness:
    """单次生命周期中，RuntimeCore 管理的所有 Authority 实例应唯一。"""

    def test_all_authority_components_are_unique_instances(self):
        """MemoryStore / VectorMemory / GrowthState / EmotionManager 在 RuntimeCore 中唯一。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        # 第一次获取
        m1 = rc.get_memory_store()
        v1 = rc.get_vector_memory()
        g1 = rc.get_growth_state()
        e1 = rc.get_emotion_manager()
        p1 = rc.get_personality_resolver()

        # 第二次获取
        m2 = rc.get_memory_store()
        v2 = rc.get_vector_memory()
        g2 = rc.get_growth_state()
        e2 = rc.get_emotion_manager()
        p2 = rc.get_personality_resolver()

        # 唯一性验证
        assert m1 is m2, "MemoryStore 应为单例"
        assert v1 is v2, "VectorMemory 应为单例"
        assert g1 is g2, "GrowthState 应为单例"
        assert e1 is e2, "EmotionManager 应为单例"
        assert p1 is p2, "PersonalityResolver 应为单例"

    def test_orchestrator_uses_same_authority_instances(self):
        """Orchestrator 创建后，所有组件应与 RuntimeCore 同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 核心 Authority 一致性
        assert orch.memory_store is rc.get_memory_store()
        assert orch.vector_memory is rc.get_vector_memory()
        assert orch.personality_resolver is rc.get_personality_resolver()
        assert orch.emotion_manager is rc.get_emotion_manager()
        assert orch.self_model_store is rc.get_self_model_store()

    def test_personality_resolver_shares_growth_state(self):
        """PersonalityResolver.state 应与 RuntimeCore.get_growth_state() 同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        # 触发 lazy 创建
        resolver = rc.get_personality_resolver()
        gs = rc.get_growth_state()

        assert resolver.state is gs, (
            "PersonalityResolver.state 必须与 RuntimeCore.get_growth_state() 同一实例"
        )

    def test_multiple_orchestrators_share_same_authority(self):
        """多个 Orchestrator 实例应共享 RuntimeCore Authority。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        assert orch1.memory_store is orch2.memory_store
        assert orch1.vector_memory is orch2.vector_memory
        assert orch1.personality_resolver is orch2.personality_resolver
        assert orch1.emotion_manager is orch2.emotion_manager


# =====================================================================
# B. RuntimeBridge 转发一致性
# =====================================================================
class TestRuntimeBridgeForwarding:
    """RuntimeBridge 应转发 RuntimeCore 实例。"""

    def test_bridge_returns_same_instance_as_core(self):
        """RuntimeBridge.get_*( ) 应与 RuntimeCore.get_*( ) 返回同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        assert bridge.get_memory_store() is rc.get_memory_store()
        assert bridge.get_vector_memory() is rc.get_vector_memory()
        assert bridge.get_growth_state() is rc.get_growth_state()
        assert bridge.get_emotion_manager() is rc.get_emotion_manager()
        assert bridge.get_personality_resolver() is rc.get_personality_resolver()
        assert bridge.get_self_model_store() is rc.get_self_model_store()


# =====================================================================
# C. 完整对话链路模拟（Smoke Test）
# =====================================================================
class TestFullConversationLifecycle:
    """
    模拟一次完整的对话生命周期：
    User Input → Orchestrator.process → MemoryStore → EmotionManager →
    PersonalityResolver → GrowthState → Response Context
    """

    def test_lifecycle_step1_orchestrator_process_uses_authority(self):
        """Step 1: Orchestrator.process 调用时使用 RuntimeCore Authority 实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 替换 LLM 引擎，避免真实调用
        class _FakeEngine:
            def generate(self, **kwargs):
                return "fake reply"

            def generate_raw(self, prompt):
                return "{}"

        orch.engine = _FakeEngine()

        # 设置目标用户
        orch.target_user_id = "runtime_integration_user"

        # 获取 process 前的引用
        memory_before = orch.memory_store
        emotion_before = orch.emotion_manager
        resolver_before = orch.personality_resolver

        # 执行完整对话
        reply = orch.process("你好，羽依！")

        # Step 1.1: 回复非空
        assert isinstance(reply, str)
        assert len(reply) > 0

        # Step 1.2: 整个生命周期内 Authority 实例保持唯一
        assert orch.memory_store is memory_before
        assert orch.emotion_manager is emotion_before
        assert orch.personality_resolver is resolver_before

        # Step 1.3: Orchestrator 引用仍指向 RuntimeCore 持有的实例
        assert orch.memory_store is rc.get_memory_store()
        assert orch.emotion_manager is rc.get_emotion_manager()
        assert orch.personality_resolver is rc.get_personality_resolver()

    def test_lifecycle_step2_memory_write_visible_in_runtime_core(self):
        """Step 2: Orchestrator 写入的记忆在 RuntimeCore.get_memory_store() 中可见。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        class _FakeEngine:
            def generate(self, **kwargs):
                return "ok"

            def generate_raw(self, prompt):
                return "{}"

        orch.engine = _FakeEngine()
        orch.target_user_id = "runtime_intg_user_2"

        # 直接写入
        test_memory = {
            "id": f"mem_test_{uuid.uuid4().hex[:8]}",
            "content": "runtime integration test memory content",
            "timestamp": datetime.now().isoformat(),
            "user_id": "runtime_intg_user_2",
            "role": "user",
            "importance": 0.5,
        }
        orch.memory_store.add(test_memory)

        # 通过 RuntimeCore 获取的同一实例应能看到该记忆
        rc_store = rc.get_memory_store()
        assert orch.memory_store is rc_store

        # 加载数据验证可见
        loaded = rc_store.load()
        memory_ids = [m.get("id") for m in loaded]
        assert test_memory["id"] in memory_ids, (
            "通过 Orchestrator.memory_store 写入的数据，必须在 RuntimeCore 同一实例中可见"
        )

    def test_lifecycle_step3_growth_state_shared(self):
        """Step 3: GrowthState 跨模块一致，PersonalityResolver.state 与 RuntimeCore 一致。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # PersonalityResolver 持有的 state 必须与 RuntimeCore 共享 GrowthState
        rc_gs = rc.get_growth_state()
        resolver_state = orch.personality_resolver.state

        assert resolver_state is rc_gs, (
            "Orchestrator.personality_resolver.state 必须等于 RuntimeCore.get_growth_state()"
        )

        # 模拟修改 GrowthState，验证 PersonalityResolver 可见
        if hasattr(rc_gs, "total_growth"):
            original = rc_gs.total_growth
            try:
                rc_gs.total_growth = original + 1.0
                assert orch.personality_resolver.state.total_growth == original + 1.0
            finally:
                rc_gs.total_growth = original

    def test_lifecycle_step4_emotion_manager_state_visible(self):
        """Step 4: EmotionManager 状态在 Orchestrator 与 RuntimeCore 间共享。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        rc_em = rc.get_emotion_manager()
        orch_em = orch.emotion_manager

        assert rc_em is orch_em, (
            "Orchestrator.emotion_manager 必须等于 RuntimeCore.get_emotion_manager()"
        )


# =====================================================================
# D. 跨 Authority 引用关系图
# =====================================================================
class TestAuthorityReferenceGraph:
    """Authority 之间的引用关系应满足设计。"""

    def test_personality_resolver_uses_runtime_self_model_store(self):
        """PersonalityResolver 应使用 RuntimeCore 共享的 SelfModelStore。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        rc_sms = rc.get_self_model_store()
        orch_sms = orch.self_model_store

        assert orch_sms is rc_sms
        # PersonalityResolver 的 self_model_store 已被注入为共享实例
        assert orch.personality_resolver.self_model_store is rc_sms

    def test_orchestrator_self_model_context_provider_uses_shared_store(self):
        """Orchestrator.self_model_context_provider 应使用共享 SelfModelStore。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        provider = orch.self_model_context_provider
        rc_sms = rc.get_self_model_store()

        # Provider 持有共享 store
        assert hasattr(provider, "store")
        assert provider.store is rc_sms


# =====================================================================
# E. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """没有 RuntimeBridge 时各模块仍可工作（fallback）。"""

    def test_orchestrator_works_without_runtime_bridge(self):
        """RuntimeBridge 不可用时 Orchestrator 通过 fallback 自建实例。"""
        # 不初始化 RuntimeBridge
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.memory_store is not None
        assert orch.personality_resolver is not None

    def test_fallback_orchestrator_instances_are_independent(self):
        """Fallback 模式下每个 Orchestrator 自建独立实例（无 Authority 共享）。"""
        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        # fallback 模式下不共享（无 RuntimeCore 托管）
        # 这一行为是设计选择：仅在 RuntimeBridge 可用时才共享
        assert orch1.memory_store is not orch2.memory_store


# =====================================================================
# F. Runtime 状态快照
# =====================================================================
class TestRuntimeSnapshot:
    """RuntimeCore 应能提供状态快照用于调试与监控。"""

    def test_runtime_core_keeps_self_state(self):
        """RuntimeCore 应持有 self_state 用于决策。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        assert hasattr(rc, "self_state")
        assert rc.self_state is not None

    def test_runtime_core_keeps_world_state(self):
        """RuntimeCore 应持有 world_state。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        assert hasattr(rc, "world_state")
        assert rc.world_state is not None


# =====================================================================
# G. 集成回归（避免破坏 Phase 4.4 收口）
# =====================================================================
class TestPhase44Regression:
    """确保 Phase 4.5 不会破坏 Phase 4.4 收口。"""

    def test_event_extractor_uses_authority(self):
        """Phase 4.4.3 收口：EventExtractor 仍使用 Authority。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.growth.event_extractor import EventExtractor
        extractor = EventExtractor()

        assert extractor.store is rc.get_memory_store()

    def test_self_checker_uses_authority(self):
        """Phase 4.4.3 收口：SelfChecker 仍使用 Authority。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.thinking.self_check import SelfChecker
        checker = SelfChecker()

        assert checker._get_memory_store() is rc.get_memory_store()

    def test_event_history_matcher_uses_authority(self):
        """Phase 4.4.4 收口：EventHistoryMatcher 仍使用 Authority。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.growth.topic_tracker import EventHistoryMatcher
        matcher = EventHistoryMatcher()

        assert matcher.store is rc.get_memory_store()
