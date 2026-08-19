# -*- coding: utf-8 -*-
"""
tests/test_personality_authority.py

Phase 4.2.3 PersonalityResolver Authority 测试套件。

覆盖目标：
1. RuntimeCore 与 Orchestrator 使用同一个 PersonalityResolver
2. Personality 更新后能正确传递到 Prompt（TraitState 一致）
3. SelfModel 功能在共享架构下仍正常工作
4. Fallback 兼容性
5. Phase 4.2.1 Emotion Authority 回归保护
6. Phase 4.2.2 SelfModel Authority 回归保护

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
# 模块级 mock 设置（与 Phase 4.1/4.2.1/4.2.2 测试一致）
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
# 临时工作目录与重置 fixture
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    """切换到临时工作目录，避免污染真实数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_personality_auth_")
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
# 测试辅助函数
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


def _clear_personality_files():
    """清理人格相关的持久化文件。"""
    for filename in ["growth_state.json", "relationship_state.json", "emotion_state.json"]:
        p = Path("data") / filename
        if p.exists():
            p.unlink()


# =====================================================================
# 测试类
# =====================================================================

class TestPersonalityResolverShared:
    """测试 RuntimeCore 与 Orchestrator 共享同一 PersonalityResolver 实例。"""

    def test_orchestrator_uses_runtime_core_resolver(self):
        """Orchestrator 应使用 RuntimeCore 的 PersonalityResolver。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        core_resolver = runtime_core.get_personality_resolver()
        assert core_resolver is not None, "RuntimeCore 应能获取 PersonalityResolver"

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        assert orch.personality_resolver is core_resolver, \
            "Orchestrator 的 PersonalityResolver 应与 RuntimeCore 的是同一实例"

    def test_multiple_orchestrators_share_same_resolver(self):
        """多个 Orchestrator 实例应共享同一 PersonalityResolver。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        core_resolver = runtime_core.get_personality_resolver()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()
        assert orch1.personality_resolver is core_resolver
        assert orch2.personality_resolver is core_resolver
        assert orch1.personality_resolver is orch2.personality_resolver, \
            "多个 Orchestrator 应共享同一 PersonalityResolver 实例"

    def test_runtime_bridge_forwards_resolver(self):
        """RuntimeBridge 应正确转发 PersonalityResolver。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        core_resolver = runtime_core.get_personality_resolver()

        bridge_resolver = bridge.get_personality_resolver()
        assert bridge_resolver is core_resolver, \
            "RuntimeBridge 应返回 RuntimeCore 的 PersonalityResolver"

    def test_lazy_creates_resolver_when_adapters_disabled(self):
        """adapters_enabled=False 时应 lazy 创建 PersonalityResolver。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        
        # adapters_enabled 默认 False，此时 personality_resolver 属性为 None
        # get_personality_resolver() 应 lazy 创建
        resolver = runtime_core.get_personality_resolver()
        assert resolver is not None, "应 lazy 创建 PersonalityResolver"
        
        # 多次调用应返回同一实例
        resolver2 = runtime_core.get_personality_resolver()
        assert resolver is resolver2, "多次调用应返回同一实例"


class TestTraitStateConsistency:
    """测试多次 resolve() 后 TraitState 的一致性。"""

    def test_trait_states_consistent_after_multiple_resolves(self):
        """多次 resolve() 后 TraitState 应一致。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        orch.personality_resolver.resolve()
        states_1 = orch.personality_resolver.get_trait_states()

        orch.personality_resolver.resolve()
        states_2 = orch.personality_resolver.get_trait_states()

        # 相同实例的 trait_states 应保持一致（除非 GrowthState 变化）
        for dim in states_1:
            assert states_1[dim]["current_value"] == states_2[dim]["current_value"], \
                f"TraitState {dim} 在两次 resolve 后应保持一致"

    def test_shared_resolver_trait_states_consistent(self):
        """共享 PersonalityResolver 的 TraitState 应在多个 Orchestrator 间一致。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        orch1.personality_resolver.resolve()
        states_1 = orch1.personality_resolver.get_trait_states()

        orch2.personality_resolver.resolve()
        states_2 = orch2.personality_resolver.get_trait_states()

        # 两个 Orchestrator 共享同一实例，TraitState 应一致
        for dim in states_1:
            assert states_1[dim]["current_value"] == states_2[dim]["current_value"], \
                f"共享实例的 TraitState {dim} 应一致"

    def test_resolver_updates_self_model(self):
        """resolve() 应更新 SelfModel。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        shared_store = orch.self_model_store

        assert shared_store.get() is None, "初始 SelfModel 应为空"

        orch.personality_resolver.resolve()
        updated_model = shared_store.get()
        assert updated_model is not None, "resolve() 后 SelfModel 应被更新"


class TestPersonalityReachesPrompt:
    """测试 Personality 更新能正确传递到 Prompt。"""

    def test_resolve_then_context_provider_non_empty(self):
        """resolve() 后 SelfModelContextProvider 应返回非空内容。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # resolve() 前应返回空
        assert orch.self_model_context_provider.get_context() == "", \
            "resolve() 前 context 应为空"

        orch.personality_resolver.resolve()
        context = orch.self_model_context_provider.get_context()
        assert context != "", "resolve() 后 context 应非空"
        assert "【自我认知参考】" in context, "context 应包含【自我认知参考】标题"

    def test_context_contains_identity_after_resolve(self):
        """resolve() 后 context 应包含身份信息。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.personality_resolver.resolve()

        context = orch.self_model_context_provider.get_context()
        assert "身份" in context, "context 应包含身份字段"

    def test_engine_receives_personality_context(self):
        """engine.generate() 应收到非空的 self_model_context。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.personality_resolver.resolve()

        # 模拟 engine.generate 调用，检查传入参数
        with patch.object(orch.engine, 'generate', return_value="测试回复") as mock_generate:
            orch.process("你好，羽依")
            mock_generate.assert_called_once()
            call_kwargs = mock_generate.call_args[1]
            assert "self_model_context" in call_kwargs, \
                "engine.generate 应收到 self_model_context 参数"
            assert call_kwargs["self_model_context"] != "", \
                "self_model_context 应非空"


class TestSelfModelStillWorks:
    """确保 SelfModel 功能在共享 PersonalityResolver 架构下仍正常工作。"""

    def test_self_model_store_shared_with_resolver(self):
        """SelfModelStore 应与 PersonalityResolver 共享。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # SelfModelStore 应已注入到 PersonalityResolver
        assert orch.personality_resolver.self_model_store is orch.self_model_store, \
            "SelfModelStore 应注入到 PersonalityResolver"

    def test_provider_reads_after_resolve(self):
        """Provider 应在 resolve() 后能读取内容。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 验证 Provider 的 store 与 resolver 的 store 是同一实例
        provider_store = orch.self_model_context_provider.store
        resolver_store = orch.personality_resolver.self_model_store
        assert provider_store is resolver_store, \
            "Provider 和 Resolver 应使用同一 SelfModelStore"

        orch.personality_resolver.resolve()
        context = orch.self_model_context_provider.get_context()
        assert context != "", "resolve() 后 context 应非空"

    def test_no_dual_store_split_after_authority(self):
        """Phase 4.2.3 后不应存在双 Store 分裂。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 验证所有引用都是同一实例
        store_refs = [
            orch.self_model_store,
            orch.personality_resolver.self_model_store,
            orch.self_model_context_provider.store,
        ]
        assert len(set(id(ref) for ref in store_refs)) == 1, \
            "所有 SelfModelStore 引用应指向同一实例"


class TestFallbackCompatibility:
    """测试 Fallback 兼容性。"""

    def test_orchestrator_fallback_when_bridge_unavailable(self):
        """RuntimeBridge 不可用时 Orchestrator 应 fallback 自建。"""
        _clear_personality_files()
        
        # 模拟 RuntimeBridge 不可用
        with patch("src.runtime.runtime_bridge.get_runtime_bridge", return_value=None):
            from src.orchestrator import Orchestrator
            orch = Orchestrator()
            # 应 fallback 自建
            assert orch.personality_resolver is not None, \
                "RuntimeBridge 不可用时应 fallback 自建"

    def test_fallback_resolver_still_works(self):
        """Fallback 自建的 PersonalityResolver 仍应正常工作。"""
        _clear_personality_files()
        
        with patch("src.runtime.runtime_bridge.get_runtime_bridge", return_value=None):
            from src.orchestrator import Orchestrator
            orch = Orchestrator()
            
            personality = orch.personality_resolver.resolve()
            assert personality is not None, "Fallback resolver resolve() 应返回结果"
            # PersonalityVector 使用 get_all() 获取数据
            assert len(personality.get_all()) > 0, "返回值应包含人格数据"

    def test_fallback_context_provider_works(self):
        """Fallback 模式下 SelfModelContextProvider 仍应正常工作。"""
        _clear_personality_files()
        
        with patch("src.runtime.runtime_bridge.get_runtime_bridge", return_value=None):
            from src.orchestrator import Orchestrator
            orch = Orchestrator()
            orch.personality_resolver.resolve()
            
            context = orch.self_model_context_provider.get_context()
            assert context != "", "Fallback 模式下 resolve() 后 context 应非空"


class TestCrossPhaseRegression:
    """跨 Phase 回归测试，确保 4.2.1 和 4.2.2 的功能不受影响。"""

    # Phase 4.2.1 Emotion Authority 回归
    def test_emotion_manager_still_shared(self):
        """EmotionManager 共享逻辑不受 Phase 4.2.3 影响。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        core_em = runtime_core.get_emotion_manager()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        assert orch.emotion_manager is core_em, \
            "EmotionManager 应保持共享"

    def test_emotion_update_still_works(self):
        """情绪更新仍正常工作。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        
        # 使用 process_event 处理情绪事件
        from src.emotion.emotion_event import EmotionEvent
        event = EmotionEvent(
            event_type="user_praise",
            intensity=0.8,
            description="测试情绪事件",
            source="test"
        )
        orch.emotion_manager.process_event(event)
        state = orch.emotion_manager.state
        assert state is not None, "情绪状态应存在"

    # Phase 4.2.2 SelfModel Authority 回归
    def test_self_model_store_identity_shared(self):
        """SelfModelStore 在所有模块间一致（Phase 4.2.2 回归）。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        core_store = runtime_core.get_self_model_store()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        assert orch.self_model_store is core_store, \
            "SelfModelStore 应保持共享"

    def test_resolve_updates_shared_self_model(self):
        """resolve() 仍能更新共享 SelfModelStore（Phase 4.2.2 回归）。"""
        _clear_personality_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        shared_store = orch.self_model_store

        assert shared_store.get() is None, "初始应为空"
        orch.personality_resolver.resolve()
        assert shared_store.get() is not None, "resolve() 后应非空"
