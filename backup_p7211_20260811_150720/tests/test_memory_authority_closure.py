# -*- coding: utf-8 -*-
"""
tests/test_memory_authority_closure.py

Phase 4.4.2 Memory Authority 收口测试。

验证 MemorySystem、MemoryService、ContextManager 使用 RuntimeCore Authority 的共享实例。

覆盖目标：
A. RuntimeCore.get_memory_store() 与 MemorySystem.store 共享实例
B. RuntimeCore.get_vector_memory() 与 MemorySystem.vector 共享实例
C. MemoryService 同样共享
D. ContextManager 同样共享
E. Fallback 模式测试
F. 跨 Phase 回归保护
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
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_mem_closure_")
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
# A. MemorySystem.store 与 RuntimeCore.get_memory_store() 共享
# =====================================================================
class TestMemorySystemSharedMemoryStore:

    def test_memory_system_uses_runtime_core_memory_store(self):
        """MemorySystem.store 与 RuntimeCore.get_memory_store() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.memory.memory_system import MemorySystem
        ms = MemorySystem()

        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert ms.store is rc_store

    def test_multiple_memory_systems_share_same_store(self):
        """多个 MemorySystem 实例共享同一 MemoryStore。"""
        bridge = _init_runtime_bridge()

        from src.memory.memory_system import MemorySystem
        ms1 = MemorySystem()
        ms2 = MemorySystem()

        assert ms1.store is ms2.store


# =====================================================================
# B. MemorySystem.vector 与 RuntimeCore.get_vector_memory() 共享
# =====================================================================
class TestMemorySystemSharedVectorMemory:

    def test_memory_system_uses_runtime_core_vector_memory(self):
        """MemorySystem.vector 与 RuntimeCore.get_vector_memory() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.memory.memory_system import MemorySystem
        ms = MemorySystem()

        rc_vm = rc.get_vector_memory()
        assert rc_vm is not None
        assert ms.vector is rc_vm

    def test_multiple_memory_systems_share_same_vector_memory(self):
        """多个 MemorySystem 实例共享同一 VectorMemory。"""
        bridge = _init_runtime_bridge()

        from src.memory.memory_system import MemorySystem
        ms1 = MemorySystem()
        ms2 = MemorySystem()

        assert ms1.vector is ms2.vector


# =====================================================================
# C. MemoryService 同样共享
# =====================================================================
class TestMemoryServiceShared:

    def test_memory_service_uses_runtime_core_memory_store(self):
        """MemoryService.store 与 RuntimeCore.get_memory_store() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.memory.memory_service import MemoryService
        svc = MemoryService()

        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert svc.store is rc_store

    def test_memory_service_uses_runtime_core_vector_memory(self):
        """MemoryService.vector 与 RuntimeCore.get_vector_memory() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.memory.memory_service import MemoryService
        svc = MemoryService()

        rc_vm = rc.get_vector_memory()
        assert rc_vm is not None
        assert svc.vector is rc_vm

    def test_memory_service_with_explicit_store_param(self):
        """MemoryService 显式传入 MemoryStore 时不从 RuntimeBridge 获取。"""
        from src.memory.memory_store import MemoryStore
        explicit_store = MemoryStore()

        bridge = _init_runtime_bridge()
        from src.memory.memory_service import MemoryService
        svc = MemoryService(user_context=explicit_store)

        # 显式传入的实例应被直接使用
        assert svc.store is explicit_store


# =====================================================================
# D. ContextManager 同样共享
# =====================================================================
class TestContextManagerShared:

    def test_context_manager_uses_runtime_core_memory_store(self):
        """ContextManager._get_memory_store() 与 RuntimeCore.get_memory_store() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")

        cm_store = cm._get_memory_store()
        rc_store = rc.get_memory_store()
        assert rc_store is not None
        assert cm_store is rc_store

    def test_context_manager_uses_runtime_core_vector_memory(self):
        """ContextManager._get_vector_memory() 与 RuntimeCore.get_vector_memory() 是同一实例。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")

        cm_vm = cm._get_vector_memory()
        rc_vm = rc.get_vector_memory()
        assert rc_vm is not None
        assert cm_vm is rc_vm


# =====================================================================
# E. Fallback 模式测试
# =====================================================================
class TestFallbackCompatibility:

    def test_memory_system_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，MemorySystem 自建 MemoryStore。"""
        from src.memory.memory_system import MemorySystem
        ms = MemorySystem()

        assert ms.store is not None
        assert ms.vector is not None

    def test_memory_service_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，MemoryService 自建实例。"""
        from src.memory.memory_service import MemoryService
        svc = MemoryService()

        assert svc.store is not None
        assert svc.vector is not None

    def test_context_manager_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，ContextManager 自建实例。"""
        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")

        assert cm._get_memory_store() is not None
        assert cm._get_vector_memory() is not None


# =====================================================================
# F. 跨 Phase 回归保护
# =====================================================================
class TestCrossPhaseRegression:

    def test_emotion_manager_still_works(self):
        """EmotionManager Authority 不受影响。"""
        bridge = _init_runtime_bridge()
        em = bridge.get_emotion_manager()
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

    def test_all_authority_components_share_same_memory_store(self):
        """所有 Authority 组件使用同一 MemoryStore。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        rc_store = rc.get_memory_store()
        from src.memory.memory_system import MemorySystem
        from src.memory.memory_service import MemoryService
        from src.context.context_manager import ContextManager

        ms = MemorySystem()
        svc = MemoryService()
        cm = ContextManager(user_id="test_user")

        assert ms.store is rc_store
        assert svc.store is rc_store
        assert cm._get_memory_store() is rc_store

    def test_all_authority_components_share_same_vector_memory(self):
        """所有 Authority 组件使用同一 VectorMemory。"""
        bridge = _init_runtime_bridge()
        rc = bridge._runtime_core

        rc_vm = rc.get_vector_memory()
        from src.memory.memory_system import MemorySystem
        from src.memory.memory_service import MemoryService
        from src.context.context_manager import ContextManager

        ms = MemorySystem()
        svc = MemoryService()
        cm = ContextManager(user_id="test_user")

        assert ms.vector is rc_vm
        assert svc.vector is rc_vm
        assert cm._get_vector_memory() is rc_vm