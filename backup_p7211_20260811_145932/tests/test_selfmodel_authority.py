# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_authority.py

Phase 4.2.2 SelfModel Authority 测试套件。

覆盖目标：
1. RuntimeCore 与 Orchestrator 使用同一个 SelfModelStore
2. SelfModel 更新后 Prompt 能读取（修复双 Store 分裂）
3. SelfModelContextProvider 绑定正确实例
4. Fallback 兼容性
5. Phase 4.1/4.2.1 回归保护

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
# 模块级 mock 设置（与 Phase 4.1/4.2.1 测试一致）
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
        return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

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
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_selfmodel_auth_")
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
    # 重置 fake ChromaDB collection
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


def _clear_selfmodel_files():
    """清除相关状态文件。"""
    for path in [Path("data/emotion_state.json"),
                Path("data/emotion_traces.json"),
                Path("data/emotion_analysis_counter.json"),
                Path("data/runtime_state.json")]:
        if path.exists():
            path.unlink()


# =====================================================================
# 1. RuntimeCore 与 Orchestrator 使用同一个 SelfModelStore
# =====================================================================
class TestSelfModelStoreShared:
    """验证 RuntimeCore 与 Orchestrator 引用同一个 SelfModelStore 实例。"""

    def test_orchestrator_uses_runtime_core_self_model_store(self):
        """当 RuntimeBridge 已初始化时，Orchestrator 应使用 RuntimeCore 的 SelfModelStore。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        assert runtime_core is not None

        core_store = runtime_core.get_self_model_store()
        assert core_store is not None, "RuntimeCore.get_self_model_store() 应返回非 None"

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.self_model_store is not None, "Orchestrator 应获取到 SelfModelStore"
        assert orch.self_model_store is core_store, \
            "Orchestrator 的 SelfModelStore 应与 RuntimeCore 的是同一实例（is 比较）"

    def test_runtime_bridge_get_self_model_store_returns_core_instance(self):
        """RuntimeBridge.get_self_model_store() 应返回 RuntimeCore 的实例。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        bridge_store = bridge.get_self_model_store()
        core_store = runtime_core.get_self_model_store()

        assert bridge_store is not None
        assert bridge_store is core_store, "Bridge 应返回 RuntimeCore 持有的实例"

    def test_self_model_store_identity_across_multiple_orchestrators(self):
        """多个 Orchestrator 实例应共享同一个 SelfModelStore。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        assert orch1.self_model_store is not None
        assert orch1.self_model_store is orch2.self_model_store, \
            "两个 Orchestrator 实例应共享同一个 SelfModelStore"

    def test_context_provider_binds_to_shared_store(self):
        """SelfModelContextProvider 应绑定到共享的 SelfModelStore。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.self_model_context_provider is not None
        assert orch.self_model_context_provider.store is orch.self_model_store, \
            "SelfModelContextProvider 应绑定到 Orchestrator 的 SelfModelStore"


# =====================================================================
# 2. SelfModel 更新后 Prompt 能读取（核心修复）
# =====================================================================
class TestSelfModelUpdateReachesPrompt:
    """验证 SelfModel 更新后能通过 Provider 读取（修复双 Store 分裂）。"""

    def test_resolve_updates_shared_store(self):
        """personality_resolver.resolve() 应更新共享的 SelfModelStore。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 获取共享的 store
        shared_store = orch.self_model_store

        # resolve 前，store 应该是空的（_current_model 为 None）
        assert shared_store.get() is None, "resolve 前 _current_model 应为 None"

        # 执行 resolve
        orch.personality_resolver.resolve()

        # resolve 后，共享 store 应被更新（因为 Provider 绑定的就是 resolver 内部的 store）
        updated_model = shared_store.get()
        assert updated_model is not None, \
            "resolve 后共享 SelfModelStore 应被更新（_current_model 非 None）"

    def test_context_provider_reads_after_resolve(self):
        """resolve() 后 SelfModelContextProvider 应能读取到非空内容。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # resolve 前应为空
        ctx_before = orch.self_model_context_provider.get_context()
        assert ctx_before == "", "resolve 前 context 应为空字符串"

        # 执行 resolve
        orch.personality_resolver.resolve()

        # resolve 后应有内容
        ctx_after = orch.self_model_context_provider.get_context()
        assert ctx_after != "", \
            "resolve 后 context 应为非空（修复双 Store 分裂后 Provider 能读取到内容）"
        assert "自我认知参考" in ctx_after, "context 应包含【自我认知参考】段"

    def test_engine_receives_non_empty_self_model_context(self):
        """engine.generate() 应收到非空的 self_model_context。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "prompt_test_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你真棒")

        assert "self_model_context" in captured
        ctx = captured["self_model_context"]
        # 修复后应为非空（resolve() 已更新共享 store）
        assert ctx != "", \
            "engine.generate 应收到非空的 self_model_context（修复双 Store 分裂）"

    def test_resolve_then_context_contains_identity(self):
        """resolve() 后 context 应包含身份信息。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.personality_resolver.resolve()

        ctx = orch.self_model_context_provider.get_context()
        assert "身份" in ctx, "context 应包含身份信息"


# =====================================================================
# 3. SelfModelContextProvider 绑定验证
# =====================================================================
class TestSelfModelContextProviderBinding:
    """验证 SelfModelContextProvider 绑定到正确的 SelfModelStore。"""

    def test_provider_store_is_resolver_store(self):
        """Provider 的 store 应与 PersonalityResolver 的 self_model_store 相同。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        resolver_store = orch.personality_resolver.self_model_store
        provider_store = orch.self_model_context_provider.store

        assert provider_store is resolver_store, \
            "Provider 的 store 应与 PersonalityResolver 的 self_model_store 是同一实例"

    def test_provider_store_is_orchestrator_store(self):
        """Provider 的 store 应与 Orchestrator 的 self_model_store 相同。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.self_model_context_provider.store is orch.self_model_store

    def test_no_dual_store_split(self):
        """验证不存在双 Store 分裂（核心修复验证）。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 修改前：orchestrator.self_model_store ≠ personality_resolver.self_model_store
        # 修改后：两者应是同一实例
        assert orch.self_model_store is orch.personality_resolver.self_model_store, \
            "修复后 Orchestrator 的 self_model_store 应与 PersonalityResolver 的是同一实例"


# =====================================================================
# 4. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """验证 RuntimeBridge 不可用时 Orchestrator 的 fallback 行为。"""

    def test_orchestrator_fallback_to_resolver_store(self):
        """RuntimeBridge 未初始化时，应 fallback 到 personality_resolver.self_model_store。"""
        _clear_selfmodel_files()
        # 不初始化 RuntimeBridge
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.self_model_store is not None
        # 应使用 personality_resolver 的 store（修复双 Store 分裂）
        assert orch.self_model_store is orch.personality_resolver.self_model_store

    def test_orchestrator_fallback_does_not_crash(self):
        """Fallback 不应导致崩溃，聊天功能应正常。"""
        _clear_selfmodel_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        reply = orch.process("你好")
        assert reply == "测试回复"
        assert orch.self_model_store is not None

    def test_fallback_context_non_empty_after_resolve(self):
        """Fallback 模式下 resolve() 后 context 仍应为非空。"""
        _clear_selfmodel_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        orch.personality_resolver.resolve()
        ctx = orch.self_model_context_provider.get_context()
        assert ctx != "", "Fallback 模式下 resolve() 后 context 应为非空"


# =====================================================================
# 5. Phase 4.1/4.2.1 回归保护
# =====================================================================
class TestPhaseRegression:
    """验证 Phase 4.1/4.2.1 的功能在 SelfModel Authority 后仍正常。"""

    def test_emotion_manager_still_shared(self):
        """Phase 4.2.1 的 EmotionManager 共享仍正常。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_em = bridge.get_runtime_core().get_emotion_manager()
        assert orch.emotion_manager is not None
        assert orch.emotion_manager is core_em, \
            "EmotionManager 共享应不受 SelfModel Authority 影响"

    def test_emotion_context_reaches_engine(self):
        """Phase 4.1 的情绪闭环仍正常。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "regression_emotion_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你真棒，谢谢")

        assert "emotion_context" in captured
        assert isinstance(captured["emotion_context"], dict)

    def test_runtime_context_keys_still_complete(self):
        """Phase 4.1 的 RuntimeContext key 完整性仍正常。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()

        from src.runtime.runtime_context import RuntimeContext
        rc = RuntimeContext()
        ctx = rc.assemble_context(
            user_id="test_user",
            conversation={"recent_turns": []},
        )
        # 旧 key 应存在
        for key in ("system_messages", "prompt_blocks", "conversation",
                    "self_model", "memory_summary", "relationship", "trace"):
            assert key in ctx
        # 新 key 应存在
        for key in ("personality_context", "emotion_manager", "emotion_context",
                    "relationship_profile", "screen_context", "user_context",
                    "token_context"):
            assert key in ctx

    def test_vector_memory_still_works(self):
        """Phase 4.1 的向量索引仍正常。"""
        _clear_selfmodel_files()
        from src.memory.vector import VectorMemory
        vm = VectorMemory()
        vm.add_memory({
            "id": "test_regression_mem",
            "content": "回归测试记忆内容_独特标记",
            "timestamp": "2026-07-30T12:00:00",
            "role": "user",
            "user_id": "regression_user",
        })
        # 验证记忆被写入（count 应为 1）
        assert vm.count() >= 1, "向量索引应包含刚写入的记忆"


# =====================================================================
# 6. RuntimeCore get_self_model_store() lazy 创建验证
# =====================================================================
class TestRuntimeCoreLazyCreation:
    """验证 RuntimeCore.get_self_model_store() 的 lazy 创建行为。"""

    def test_lazy_creates_when_adapters_disabled(self):
        """adapters_enabled=False 时应 lazy 创建基础 SelfModelStore。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        # adapters_enabled=False，personality_resolver 应为 None
        assert runtime_core.personality_resolver is None

        # get_self_model_store 应 lazy 创建
        store = runtime_core.get_self_model_store()
        assert store is not None
        assert hasattr(store, "get_active_self_model")

    def test_lazy_creates_same_instance_on_multiple_calls(self):
        """多次调用应返回同一实例（缓存）。"""
        _clear_selfmodel_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        store1 = runtime_core.get_self_model_store()
        store2 = runtime_core.get_self_model_store()
        assert store1 is store2, "多次调用应返回同一实例"

    def test_returns_none_on_failure(self):
        """创建失败时应返回 None。"""
        from src.runtime.runtime_core import RuntimeCore

        # 创建一个 RuntimeCore 实例，模拟 lazy 创建失败
        core = RuntimeCore(config={"autostart": False})

        # mock SelfModelStore 导入失败
        with patch("builtins.__import__", side_effect=ImportError("mocked")):
            store = core.get_self_model_store()
            assert store is None

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时应返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge()
        assert bridge.get_self_model_store() is None
