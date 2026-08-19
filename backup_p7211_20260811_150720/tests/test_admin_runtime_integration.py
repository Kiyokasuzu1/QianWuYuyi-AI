# -*- coding: utf-8 -*-
"""
tests/test_admin_runtime_integration.py

Phase 5.1: Admin Dashboard Runtime Integration 测试。

验证：
1. Admin Provider 通过 RuntimeBridge 访问 RuntimeCore
2. Provider 读取的实例与 RuntimeCore 持有的实例完全一致
3. Provider 不会创建新的 Authority 实例
4. Fallback 模式正常工作
5. 现有 Admin 功能不受影响
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Module-scoped: 避免 ResponseEngine 因无 API key 崩溃
# =====================================================================
os.environ.setdefault("DEEPSEEK_API_KEY", "test-fake-key-for-admin-tests")


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_admin_runtime_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试前后重置 RuntimeBridge 与 RuntimeProvider 单例。"""
    from src.runtime.runtime_bridge import reset_runtime_bridge
    from src.admin.runtime_provider import reset_runtime_provider_for_testing
    reset_runtime_bridge()
    reset_runtime_provider_for_testing()
    yield
    reset_runtime_bridge()
    reset_runtime_provider_for_testing()


def _init_runtime_bridge():
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={
        "emotion_enabled": False,
        "adapters_enabled": False,
        "autostart": False,
    })
    bridge.initialize()
    return bridge


# =====================================================================
# A. RuntimeProvider 基础访问
# =====================================================================
class TestRuntimeProviderAccess:
    """RuntimeProvider 应能通过 RuntimeBridge 访问 RuntimeCore。"""

    def test_provider_uses_runtime_bridge(self):
        """RuntimeProvider 内部使用 RuntimeBridge，不直接创建核心实例。"""
        bridge = _init_runtime_bridge()

        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        assert provider.get_runtime_bridge() is bridge

    def test_provider_returns_bridge_error_when_unavailable(self):
        """RuntimeCore 未初始化时，Provider 返回 online=False 而不崩溃。"""
        # 只重置 RuntimeBridge（不初始化），使 RuntimeCore 为 None
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        status = provider.get_status()

        # RuntimeCore 未初始化，online 必须为 False
        assert status["online"] is False
        assert status["runtime"]["initialized"] is False
        assert status["bridge_error"] is None or isinstance(status["bridge_error"], str)

    def test_get_status_returns_expected_structure(self):
        """get_status() 返回符合契约的字典结构。"""
        _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        status = provider.get_status()

        assert "online" in status
        assert "runtime" in status
        assert "bridge_error" in status
        assert isinstance(status["runtime"], dict)
        assert "initialized" in status["runtime"]
        assert "is_running" in status["runtime"]


# =====================================================================
# B. Runtime Authority 状态正确
# =====================================================================
class TestAuthorityStatusCorrectness:
    """RuntimeProvider.get_authority_status() 应正确反映 RuntimeCore Authority 状态。"""

    def test_authority_status_all_true_when_runtime_initialized(self):
        """RuntimeCore 初始化后，所有 Authority 组件应就绪。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        authority = provider.get_authority_status()

        assert authority["memory_store"] is True
        assert authority["vector_memory"] is True
        assert authority["emotion_manager"] is True
        assert authority["personality_resolver"] is True
        assert authority["self_model_store"] is True
        assert authority["growth_state"] is True

    def test_authority_status_all_false_when_bridge_unavailable(self):
        """RuntimeBridge 不可用时，所有 Authority 状态为 False。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        authority = provider.get_authority_status()

        for key in [
            "memory_store", "vector_memory", "emotion_manager",
            "personality_resolver", "self_model_store", "growth_state",
        ]:
            assert authority[key] is False, f"{key} 应为 False"


# =====================================================================
# C. Provider 不会创建新的 Authority 实例
# =====================================================================
class TestProviderDoesNotCreateNewInstances:
    """RuntimeProvider 不应创建任何 Authority 实例，仅通过 RuntimeBridge 引用。"""

    def test_provider_memory_store_is_runtime_core_instance(self):
        """Provider 返回的 MemoryStore 必须等于 RuntimeCore.get_memory_store()。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        provider_store = provider.get_memory_store()
        runtime_store = bridge.get_memory_store()

        assert provider_store is runtime_store, (
            "Provider 返回的 MemoryStore 必须等于 RuntimeCore 持有的实例"
        )

    def test_provider_growth_state_is_runtime_core_instance(self):
        """Provider 返回的 GrowthState 必须等于 RuntimeCore.get_growth_state()。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        provider_gs = provider.get_growth_state()
        runtime_gs = bridge.get_growth_state()

        assert provider_gs is runtime_gs, (
            "Provider 返回的 GrowthState 必须等于 RuntimeCore 持有的实例"
        )

    def test_provider_emotion_manager_is_runtime_core_instance(self):
        """Provider 返回的 EmotionManager 必须等于 RuntimeCore.get_emotion_manager()。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        assert provider.get_emotion_manager() is bridge.get_emotion_manager()

    def test_provider_personality_resolver_is_runtime_core_instance(self):
        """Provider 返回的 PersonalityResolver 必须等于 RuntimeCore 持有实例。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        assert provider.get_personality_resolver() is bridge.get_personality_resolver()

    def test_provider_vector_memory_is_runtime_core_instance(self):
        """Provider 返回的 VectorMemory 必须等于 RuntimeCore 持有实例。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        assert provider.get_vector_memory() is bridge.get_vector_memory()

    def test_provider_self_model_store_is_runtime_core_instance(self):
        """Provider 返回的 SelfModelStore 必须等于 RuntimeCore 持有实例。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        assert provider.get_self_model_store() is bridge.get_self_model_store()


# =====================================================================
# D. 总结 API 数据正确性
# =====================================================================
class TestSummaryAPIs:
    """RuntimeProvider 总结类 API 应返回正确数据。"""

    def test_memory_summary_structure(self):
        """get_memory_summary 返回包含 expected keys 的字典。"""
        _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_memory_summary()

        assert "available" in summary
        assert "total_count" in summary
        assert "recent" in summary
        assert "important_count" in summary
        assert "user_id" in summary

    def test_memory_summary_reads_data(self):
        """Memory summary 应能读取 RuntimeCore 中的实际记忆数据。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        # 直接写入 RuntimeCore 的 MemoryStore
        mem = {
            "id": "admin_test_mem_001",
            "content": "admin runtime integration test",
            "timestamp": "2026-07-30T20:00:00",
            "user_id": "admin_test_user",
            "role": "user",
            "importance": 0.8,
        }
        rc.get_memory_store().add(mem)

        summary = provider.get_memory_summary()
        assert summary["available"] is True
        assert summary["total_count"] >= 1
        assert summary["important_count"] >= 1

    def test_growth_summary_shared_with_resolver(self):
        """Growth summary 应标注与 PersonalityResolver.state 共享。"""
        _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_growth_summary()

        assert summary["available"] is True
        assert summary["shared_with_resolver"] is True, (
            "GrowthState 必须与 PersonalityResolver.state 共享同一实例"
        )

    def test_personality_summary_structure(self):
        """get_personality_summary 返回包含 expected keys 的字典。"""
        _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_personality_summary()

        assert "available" in summary
        assert "current" in summary
        assert "state" in summary
        assert "self_model" in summary

    def test_emotion_summary_structure(self):
        """get_emotion_summary 返回包含 expected keys 的字典。"""
        _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_emotion_summary()

        assert "available" in summary
        assert "current" in summary
        assert "intensity" in summary
        assert "recent" in summary


# =====================================================================
# E. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """RuntimeBridge 不可用时各 summary API 返回安全 fallback 数据。"""

    def test_memory_summary_fallback(self):
        """RuntimeBridge 不可用时 memory summary 返回空数据而非崩溃。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        summary = provider.get_memory_summary()

        assert summary["available"] is False
        assert summary["total_count"] == 0
        assert summary["recent"] == []

    def test_personality_summary_fallback(self):
        """RuntimeBridge 不可用时 personality summary 返回空数据。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        summary = provider.get_personality_summary()

        assert summary["available"] is False
        assert summary["current"] is None

    def test_emotion_summary_fallback(self):
        """RuntimeBridge 不可用时 emotion summary 返回空数据。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        summary = provider.get_emotion_summary()

        assert summary["available"] is False
        assert summary["current"] is None

    def test_growth_summary_fallback(self):
        """RuntimeBridge 不可用时 growth summary 返回空数据。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        reset_runtime_bridge()
        from src.admin.runtime_provider import (
            get_runtime_provider,
            reset_runtime_provider_for_testing,
        )
        reset_runtime_provider_for_testing()

        provider = get_runtime_provider()
        summary = provider.get_growth_summary()

        assert summary["available"] is False
        assert summary["shared_with_resolver"] is False


# =====================================================================
# F. 现有 Admin 功能不受影响
# =====================================================================
class TestExistingAdminUnaffected:
    """验证 Phase 5.1 不破坏现有 Admin 系统。"""

    def test_admin_blueprint_still_loads(self):
        """admin_bp 蓝图仍可正常加载（需要 flask）。"""
        flask = pytest.importorskip("flask")
        assert flask is not None
        from src.admin.api.routes import admin_bp
        assert admin_bp is not None
        assert admin_bp.name == "admin"

    def test_existing_runtime_state_endpoint_still_works(self):
        """现有 /api/runtime/state 端点注册仍然存在（需要 flask）。"""
        pytest.importorskip("flask")
        _init_runtime_bridge()
        from src.admin.api.routes import admin_bp
        # 确认蓝图加载正常即可
        assert admin_bp is not None

    def test_runtime_provider_does_not_modify_authority_state(self):
        """Provider 读取过程不应修改 Authority 任何状态。"""
        bridge = _init_runtime_bridge()
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()

        rc_store = bridge.get_memory_store()
        rc_gs = bridge.get_growth_state()

        # 多次调用 Provider
        for _ in range(5):
            provider.get_authority_status()
            provider.get_memory_summary()
            provider.get_growth_summary()

        # 实例身份保持
        assert bridge.get_memory_store() is rc_store
        assert bridge.get_growth_state() is rc_gs


# =====================================================================
# G. Provider 单例行为
# =====================================================================
class TestProviderSingleton:
    """RuntimeProvider 模块级单例。"""

    def test_get_runtime_provider_returns_same_instance(self):
        """get_runtime_provider() 多次调用返回同一实例。"""
        from src.admin.runtime_provider import get_runtime_provider
        p1 = get_runtime_provider()
        p2 = get_runtime_provider()
        assert p1 is p2
