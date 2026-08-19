# -*- coding: utf-8 -*-
"""
tests/test_memory_authority.py

Phase 4.3.1 MemoryStore Authority 测试套件。

覆盖目标：
1. RuntimeCore 与 Orchestrator 使用同一个 MemoryStore
2. MemoryStore add/save/load 正常工作
3. MemoryStore 数据能进入 Context（process 流程）
4. RuntimeBridge.get_memory_store() 转发正确
5. Fallback 兼容性（RuntimeBridge 不可用时 Orchestrator 自建）
6. Phase 4.2.1/4.2.2/4.2.3 回归保护

设计原则：
- 不依赖外部 API
- 使用临时工作目录避免污染真实数据
- 优雅降级：若某些模块导入失败，对应测试用 skip 标记
"""
import sys
import os
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =====================================================================
# 模块级 mock 设置（与 Phase 4.1/4.2.x 测试一致）
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
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_memory_auth_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_runtime_bridge():
    """每个测试前重置 RuntimeBridge 单例，避免测试间状态泄漏。"""
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


def _clear_memory_files():
    """清除记忆文件。"""
    for path in [Path("data/memory.json")]:
        if path.exists():
            path.unlink()


# =====================================================================
# 1. RuntimeCore 与 Orchestrator 使用同一个 MemoryStore
# =====================================================================
class TestMemoryStoreShared:
    """验证 RuntimeCore 与 Orchestrator 引用同一个 MemoryStore 实例。"""

    def test_orchestrator_uses_runtime_core_memory_store(self):
        """当 RuntimeBridge 已初始化时，Orchestrator 应使用 RuntimeCore 的 MemoryStore。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()
        assert runtime_core is not None

        core_store = runtime_core.get_memory_store()
        assert core_store is not None, "RuntimeCore.get_memory_store() 应返回非 None"

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        assert orch.memory_store is not None, "Orchestrator 应获取到 MemoryStore"
        assert orch.memory_store is core_store, \
            "Orchestrator 的 MemoryStore 应与 RuntimeCore 的是同一实例（is 比较）"

    def test_runtime_bridge_get_memory_store_returns_core_instance(self):
        """RuntimeBridge.get_memory_store() 应返回 RuntimeCore 的实例。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        bridge_store = bridge.get_memory_store()
        core_store = runtime_core.get_memory_store()

        assert bridge_store is not None
        assert bridge_store is core_store, "Bridge 应返回 RuntimeCore 持有的实例"

    def test_memory_store_identity_across_multiple_orchestrators(self):
        """多个 Orchestrator 实例应共享同一个 MemoryStore。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        assert orch1.memory_store is not None
        assert orch1.memory_store is orch2.memory_store, \
            "两个 Orchestrator 实例应共享同一个 MemoryStore"

    def test_lazy_creates_same_instance_on_multiple_calls(self):
        """多次调用 RuntimeCore.get_memory_store() 应返回同一实例（缓存）。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        store1 = runtime_core.get_memory_store()
        store2 = runtime_core.get_memory_store()
        assert store1 is store2, "多次调用应返回同一实例"


# =====================================================================
# 2. MemoryStore add/save/load 正常
# =====================================================================
class TestMemoryPersistence:
    """验证 MemoryStore 的持久化操作正常工作。"""

    def test_add_and_load_memory(self):
        """add 后 load 应能读取到刚添加的记忆。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        store = bridge.get_memory_store()

        memory = {
            "content": "测试记忆内容_独特标记123",
            "user_id": "test_user",
            "role": "user",
            "metadata": {"memory_type": "user_fact"},  # Phase C.2.3: 显式标注 memory_type
        }
        result = store.add(memory)
        assert result is not None, "add 应返回记忆对象"
        assert "id" in result, "add 应自动生成 id"

        loaded = store.load()
        assert len(loaded) >= 1, "load 应返回至少一条记忆"
        assert any(m.get("content") == "测试记忆内容_独特标记123" for m in loaded), \
            "load 应包含刚添加的记忆"

    def test_memory_persisted_to_file(self):
        """add 后数据应写入 data/memory.json。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        store = bridge.get_memory_store()

        store.add({
            "content": "持久化测试",
            "user_id": "persist_user",
            "role": "user",
            "metadata": {"memory_type": "user_fact"},  # Phase C.2.3
        })

        memory_file = Path("data/memory.json")
        assert memory_file.exists(), "data/memory.json 应存在"

        with open(memory_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list), "memory.json 应为列表"
        assert any(m.get("content") == "持久化测试" for m in data), \
            "memory.json 应包含刚添加的记忆"

    def test_get_by_user_filters_correctly(self):
        """get_by_user 应按 user_id 正确过滤。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        store = bridge.get_memory_store()

        # Phase C.2.3: 显式标注 memory_type 通过 PollutionGuard
        for content, user in [("A", "user_a"), ("B", "user_b"), ("C", "user_a")]:
            store.add({
                "content": content,
                "user_id": user,
                "role": "user",
                "metadata": {"memory_type": "user_fact"},
            })

        user_a_memories = store.get_by_user("user_a")
        assert len(user_a_memories) == 2, "user_a 应有 2 条记忆"
        assert all(m["user_id"] == "user_a" for m in user_a_memories)

    def test_count_returns_correct_number(self):
        """count 应返回正确的记忆数量。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        store = bridge.get_memory_store()

        initial_count = store.count()
        store.add({
            "content": "count_test",
            "user_id": "count_user",
            "role": "user",
            "metadata": {"memory_type": "user_fact"},  # Phase C.2.3
        })
        assert store.count() == initial_count + 1, "count 应增加 1"


# =====================================================================
# 3. MemoryStore 数据能进入 Context
# =====================================================================
class TestMemoryReachesContext:
    """验证 MemoryStore 数据能通过 process() 进入生成上下文。"""

    def test_memory_reaches_engine_generate(self):
        """process() 时 engine.generate 应收到 chat_memories。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        # 预置记忆
        store = bridge.get_memory_store()
        store.add({
            "content": "记忆上下文测试_独特标记456",
            "user_id": "context_user",
            "role": "user",
            "metadata": {"memory_type": "user_fact"},  # Phase C.2.3
        })

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "context_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你好")

        assert "chat_memories" in captured, "engine.generate 应收到 chat_memories"
        memories = captured["chat_memories"]
        assert any(
            isinstance(m, dict) and m.get("content") == "记忆上下文测试_独特标记456"
            for m in memories
        ), "chat_memories 应包含预置记忆"

    def test_new_memory_saved_during_process(self):
        """process() 后新记忆应被保存到共享 MemoryStore。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        orch.process("保存测试消息")

        # 验证记忆被保存（user_id 会被 UserResolver 解析为配置中的默认值）
        memories = orch.memory_store.load()
        assert any(
            m.get("content") == "保存测试消息"
            for m in memories
        ), "process() 后用户消息应被保存为记忆"


# =====================================================================
# 4. RuntimeBridge.get_memory_store() 转发正确
# =====================================================================
class TestRuntimeBridgeMemoryForwarding:
    """验证 RuntimeBridge.get_memory_store() 正确转发。"""

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时应返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge()
        assert bridge.get_memory_store() is None

    def test_bridge_returns_memory_store_after_init(self):
        """RuntimeBridge 初始化后应返回 MemoryStore。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        ms = bridge.get_memory_store()
        assert ms is not None
        assert hasattr(ms, "add")
        assert hasattr(ms, "load")
        assert hasattr(ms, "_save")

    def test_bridge_returns_same_instance_on_multiple_calls(self):
        """多次调用应返回同一个实例（lazy 创建后缓存）。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        ms1 = bridge.get_memory_store()
        ms2 = bridge.get_memory_store()
        assert ms1 is ms2, "多次调用应返回同一实例"

    def test_lazy_creates_when_adapters_disabled(self):
        """adapters_enabled=False 时，get_memory_store 应 lazy 创建基础实例。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()
        runtime_core = bridge.get_runtime_core()

        # adapters_enabled=False，memory_adapter 应为 None
        assert runtime_core.memory_adapter is None

        # 调用 get_memory_store 应 lazy 创建
        ms = runtime_core.get_memory_store()
        assert ms is not None
        # 再次访问应返回同一实例
        assert runtime_core.get_memory_store() is ms


# =====================================================================
# 5. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """验证 RuntimeBridge 不可用时 Orchestrator 自建 MemoryStore。"""

    def test_orchestrator_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，Orchestrator 应 fallback 自建。"""
        _clear_memory_files()
        # 不初始化 RuntimeBridge
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 应 fallback 自建
        assert orch.memory_store is not None
        assert hasattr(orch.memory_store, "add")
        assert hasattr(orch.memory_store, "load")

    def test_orchestrator_fallback_does_not_crash(self):
        """Fallback 自建不应导致崩溃，聊天功能应正常。"""
        _clear_memory_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        # process 应正常执行
        reply = orch.process("你好")
        assert reply == "测试回复"
        assert orch.memory_store is not None

    def test_fallback_memory_persistence_works(self):
        """Fallback 模式下记忆持久化仍应工作。"""
        _clear_memory_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_persist_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"

        orch.process("fallback 测试消息")

        memories = orch.memory_store.load()
        assert any(
            m.get("content") == "fallback 测试消息"
            for m in memories
        ), "Fallback 模式下记忆应被保存"


# =====================================================================
# 6. Phase 4.2.1/4.2.2/4.2.3 回归保护
# =====================================================================
class TestCrossPhaseRegression:
    """验证 Phase 4.2.x 的功能在 Memory Authority 后仍正常。"""

    def test_emotion_manager_still_shared(self):
        """Phase 4.2.1 的 EmotionManager 共享仍正常。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_em = bridge.get_runtime_core().get_emotion_manager()
        assert orch.emotion_manager is not None
        assert orch.emotion_manager is core_em, \
            "EmotionManager 共享应不受 Memory Authority 影响"

    def test_self_model_store_still_shared(self):
        """Phase 4.2.2 的 SelfModelStore 共享仍正常。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_store = bridge.get_runtime_core().get_self_model_store()
        assert orch.self_model_store is not None
        assert orch.self_model_store is core_store, \
            "SelfModelStore 共享应不受 Memory Authority 影响"

    def test_personality_resolver_still_shared(self):
        """Phase 4.2.3 的 PersonalityResolver 共享仍正常。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        core_resolver = bridge.get_runtime_core().get_personality_resolver()
        assert orch.personality_resolver is not None
        assert orch.personality_resolver is core_resolver, \
            "PersonalityResolver 共享应不受 Memory Authority 影响"

    def test_emotion_context_reaches_engine(self):
        """Phase 4.1 的情绪闭环仍正常。"""
        _clear_memory_files()
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

    def test_self_model_context_reaches_engine(self):
        """Phase 4.2.2 的自我认知上下文仍正常传递到 engine。"""
        _clear_memory_files()
        bridge = _init_runtime_bridge()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "regression_self_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你好")

        assert "self_model_context" in captured
        # 修复后应为非空
        assert captured["self_model_context"] != "", \
            "self_model_context 应为非空（SelfModel Authority 修复后）"
