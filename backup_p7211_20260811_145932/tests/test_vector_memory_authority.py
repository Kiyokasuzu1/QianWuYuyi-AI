# -*- coding: utf-8 -*-
"""
tests/test_vector_memory_authority.py

Phase 4.3.2 VectorMemory Authority 测试套件。

覆盖目标：
1. RuntimeCore 与 Orchestrator 使用同一个 VectorMemory 实例
2. ContextManager 与 RuntimeCore 使用同一个 VectorMemory 实例
3. Lazy 创建测试
4. VectorMemory 基础功能回归（add_memory → search）
5. Fallback 兼容性
6. Phase 4.3.1/4.2.x 回归保护

设计原则：
- 不依赖外部 API
- 使用临时工作目录避免污染真实数据
- 优雅降级：若某些模块导入失败，对应测试用 skip 标记
"""
import sys
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =====================================================================
# 模块级 mock 设置（与 Phase 4.1/4.2.x/4.3.1 测试一致）
# =====================================================================

class _FakeChromaCollection:
    def __init__(self):
        self._data = {}

    def upsert(self, documents=None, metadatas=None, ids=None):
        if not documents:
            return
        for i, doc in enumerate(documents):
            mid = ids[i] if i < len(ids) else f"auto_{i}"
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            self._data[mid] = {"document": doc, "metadata": dict(meta)}

    def query(self, query_texts=None, n_results=10):
        # 简单匹配：返回所有数据作为候选
        docs = [v["document"] for v in self._data.values()]
        metas = [v["metadata"] for v in self._data.values()]
        # 距离固定为 0.5（relevance = 0.5）
        dists = [0.5] * len(self._data)
        return {
            "documents": [docs[:n_results]],
            "metadatas": [metas[:n_results]],
            "distances": [dists[:n_results]],
        }

    def count(self):
        return len(self._data)

    def delete_collection(self, name):
        self._data.clear()


class _FakeChromaClient:
    def __init__(self):
        self._collection = _FakeChromaCollection()

    def get_collection(self, name):
        return self._collection

    def create_collection(self, name, embedding_function=None):
        return self._collection

    def delete_collection(self, name):
        self._collection._data.clear()


_fake_client = _FakeChromaClient()

if "openai" not in sys.modules:
    sys.modules["openai"] = MagicMock()

_chromadb_mock = MagicMock()
_chromadb_mock.PersistentClient.side_effect = lambda **kwargs: _fake_client
if "chromadb" not in sys.modules:
    sys.modules["chromadb"] = _chromadb_mock
else:
    sys.modules["chromadb"].PersistentClient.side_effect = lambda **kwargs: _fake_client

if "chromadb.utils" not in sys.modules:
    sys.modules["chromadb.utils"] = MagicMock()

if "chromadb.utils.embedding_functions" not in sys.modules:
    sys.modules["chromadb.utils.embedding_functions"] = MagicMock(
        SentenceTransformerEmbeddingFunction=lambda **kw: MagicMock()
    )

if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = MagicMock()


# =====================================================================
# 临时工作目录 fixture
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_vector_auth_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_runtime_bridge():
    """每个测试前重置 RuntimeBridge 单例和 fake collection。"""
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass
    _fake_client._collection._data.clear()
    yield
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass
    _fake_client._collection._data.clear()


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


def _clear_chroma_files():
    """清除 ChromaDB 相关文件。"""
    for path in [Path("data/chroma_db")]:
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


# =====================================================================
# A. RuntimeCore 与 Orchestrator 使用同一个 VectorMemory 实例
# =====================================================================
class TestVectorMemorySharedWithOrchestrator:
    """验证 RuntimeCore 与 Orchestrator 引用同一个 VectorMemory 实例。"""

    def test_orchestrator_uses_runtime_core_vector_memory(self):
        """当 RuntimeBridge 已初始化时，Orchestrator 应使用 RuntimeCore 的 VectorMemory。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        assert runtime_core is not None

        core_vm = runtime_core.get_vector_memory()
        assert core_vm is not None, "RuntimeCore.get_vector_memory() 应返回非 None"

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.vector_memory is not None, "Orchestrator 应获取到 VectorMemory"
        assert orch.vector_memory is core_vm, \
            "Orchestrator 的 VectorMemory 应与 RuntimeCore 的是同一实例（is 比较）"

    def test_runtime_bridge_get_vector_memory_returns_core_instance(self):
        """RuntimeBridge.get_vector_memory() 应返回 RuntimeCore 的实例。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        bridge_vm = bridge.get_vector_memory()
        core_vm = runtime_core.get_vector_memory()

        assert bridge_vm is not None
        assert bridge_vm is core_vm, "Bridge 应返回 RuntimeCore 持有的实例"

    def test_vector_memory_identity_across_multiple_orchestrators(self):
        """多个 Orchestrator 实例应共享同一个 VectorMemory。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        assert orch1.vector_memory is not None
        assert orch1.vector_memory is orch2.vector_memory, \
            "两个 Orchestrator 实例应共享同一个 VectorMemory"


# =====================================================================
# B. ContextManager 与 RuntimeCore 使用同一个 VectorMemory 实例
# =====================================================================
class TestVectorMemorySharedWithContextManager:
    """验证 ContextManager 与 RuntimeCore 引用同一个 VectorMemory 实例。"""

    def test_context_manager_uses_runtime_core_vector_memory(self):
        """ContextManager 应通过 RuntimeBridge 获取共享的 VectorMemory。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        core_vm = runtime_core.get_vector_memory()
        assert core_vm is not None

        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")
        cm_vm = cm._get_vector_memory()

        assert cm_vm is not None, "ContextManager 应获取到 VectorMemory"
        assert cm_vm is core_vm, \
            "ContextManager 的 VectorMemory 应与 RuntimeCore 的是同一实例"

    def test_context_manager_same_instance_across_calls(self):
        """ContextManager 多次调用 _get_vector_memory 应返回同一实例。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")
        vm1 = cm._get_vector_memory()
        vm2 = cm._get_vector_memory()

        assert vm1 is not None
        assert vm1 is vm2, "多次调用应返回同一实例"


# =====================================================================
# C. Lazy 创建测试
# =====================================================================
class TestVectorMemoryLazyCreation:
    """验证 RuntimeCore.get_vector_memory() 的 lazy 创建行为。"""

    def test_lazy_creates_on_first_call(self):
        """首次调用前 _lazy_vector_memory 不存在，调用后存在。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        # 初始时 _lazy_vector_memory 不应存在或为 None
        lazy_attr = getattr(runtime_core, "_lazy_vector_memory", None)
        # 注意：如果之前测试已调用过，可能已创建。但 _reset_runtime_bridge 会重置单例
        # 实际上 VectorMemory 的 lazy 属性是 RuntimeCore 实例的属性，重置单例会销毁 RuntimeCore
        # 所以这里应该是 None 或未设置

        # 调用 get_vector_memory
        vm = runtime_core.get_vector_memory()
        assert vm is not None

        # 调用后 _lazy_vector_memory 应存在
        assert hasattr(runtime_core, "_lazy_vector_memory")
        assert runtime_core._lazy_vector_memory is vm

    def test_returns_same_instance_on_multiple_calls(self):
        """多次调用应返回同一实例（缓存）。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        vm1 = runtime_core.get_vector_memory()
        vm2 = runtime_core.get_vector_memory()
        assert vm1 is vm2, "多次调用应返回同一实例"

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时应返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge()
        assert bridge.get_vector_memory() is None

    def test_returns_none_on_failure(self):
        """VectorMemory 创建失败时应返回 None。"""
        from src.runtime.runtime_core import RuntimeCore
        core = RuntimeCore(config={"autostart": False})

        with patch("builtins.__import__", side_effect=ImportError("mocked")):
            vm = core.get_vector_memory()
            assert vm is None


# =====================================================================
# D. VectorMemory 基础功能回归
# =====================================================================
class TestVectorMemoryBasicFunctionality:
    """验证共享实例后 VectorMemory 的 add/search 功能仍正常。"""

    def test_add_memory_and_search(self):
        """add_memory 后 search 应能检索到。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()
        vm = bridge.get_vector_memory()
        assert vm is not None

        memory = {
            "id": "vm_test_001",
            "content": "向量记忆测试_独特标记789",
            "timestamp": "2026-07-30T12:00:00",
            "role": "user",
            "user_id": "test_user",
        }
        vm.add_memory(memory)

        # fake collection 中应存在该记忆
        assert vm.count() >= 1, "add_memory 后 count 应 >= 1"

        results = vm.search("独特标记789", top_k=5)
        assert len(results) >= 1, "search 应返回至少一条结果"

    def test_shared_instance_add_visible_to_all(self):
        """通过共享实例 add 的数据，所有消费者都能搜到。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        memory = {
            "id": "shared_vm_001",
            "content": "共享向量记忆测试_标记ABC",
            "timestamp": "2026-07-30T12:00:00",
            "role": "user",
            "user_id": "test_user",
        }
        orch.vector_memory.add_memory(memory)

        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")
        cm_vm = cm._get_vector_memory()

        # ContextManager 获取的应是同一实例
        assert cm_vm is orch.vector_memory

        results = cm_vm.search("标记ABC", top_k=5)
        assert len(results) >= 1, "共享实例 add 的数据应能被其他消费者搜到"


# =====================================================================
# E. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """验证 RuntimeBridge 不可用时 Orchestrator 和 ContextManager 的 fallback 行为。"""

    def test_orchestrator_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，Orchestrator 应 fallback 自建。"""
        _clear_chroma_files()
        # 不初始化 RuntimeBridge
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 应 fallback 自建
        assert orch.vector_memory is not None
        assert hasattr(orch.vector_memory, "add_memory")
        assert hasattr(orch.vector_memory, "search")

    def test_orchestrator_fallback_does_not_crash(self):
        """Fallback 自建不应导致崩溃，聊天功能应正常。"""
        _clear_chroma_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        reply = orch.process("你好")
        assert reply == "测试回复"
        assert orch.vector_memory is not None

    def test_context_manager_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，ContextManager 应 fallback 自建。"""
        _clear_chroma_files()
        from src.context.context_manager import ContextManager
        cm = ContextManager(user_id="test_user")
        vm = cm._get_vector_memory()

        assert vm is not None
        assert hasattr(vm, "add_memory")
        assert hasattr(vm, "search")


# =====================================================================
# F. 跨 Phase 回归保护
# =====================================================================
class TestCrossPhaseRegression:
    """验证 Phase 4.3.1/4.2.x 的功能在 VectorMemory Authority 后仍正常。"""

    def test_memory_store_still_shared(self):
        """Phase 4.3.1 的 MemoryStore 共享仍正常。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_store = bridge.get_runtime_core().get_memory_store()
        assert orch.memory_store is not None
        assert orch.memory_store is core_store, \
            "MemoryStore 共享应不受 VectorMemory Authority 影响"

    def test_emotion_manager_still_shared(self):
        """Phase 4.2.1 的 EmotionManager 共享仍正常。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_em = bridge.get_runtime_core().get_emotion_manager()
        assert orch.emotion_manager is not None
        assert orch.emotion_manager is core_em, \
            "EmotionManager 共享应不受 VectorMemory Authority 影响"

    def test_self_model_store_still_shared(self):
        """Phase 4.2.2 的 SelfModelStore 共享仍正常。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_store = bridge.get_runtime_core().get_self_model_store()
        assert orch.self_model_store is not None
        assert orch.self_model_store is core_store, \
            "SelfModelStore 共享应不受 VectorMemory Authority 影响"

    def test_personality_resolver_still_shared(self):
        """Phase 4.2.3 的 PersonalityResolver 共享仍正常。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_resolver = bridge.get_runtime_core().get_personality_resolver()
        assert orch.personality_resolver is not None
        assert orch.personality_resolver is core_resolver, \
            "PersonalityResolver 共享应不受 VectorMemory Authority 影响"

    def test_memory_authority_regression(self):
        """Phase 4.3.1 Memory Authority 测试场景仍通过。"""
        _clear_chroma_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 验证 MemoryStore 共享
        assert orch.memory_store is bridge.get_runtime_core().get_memory_store()
        # 验证 VectorMemory 共享
        assert orch.vector_memory is bridge.get_runtime_core().get_vector_memory()
        # 两者都应非 None
        assert orch.memory_store is not None
        assert orch.vector_memory is not None
