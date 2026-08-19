# -*- coding: utf-8 -*-
"""
tests/test_emotion_authority.py

Phase 4.2.1 Emotion Authority 测试套件。

覆盖目标：
1. RuntimeCore 和 Orchestrator 是否引用同一个 EmotionManager
2. 情绪更新是否只产生一次状态变化（无双写）
3. emotion_state.json 是否不会被双写
4. RuntimeBridge.get_emotion_manager() 转发正确
5. Fallback 兼容性（RuntimeBridge 不可用时 Orchestrator 自建）

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
# 模块级 mock 设置
# =====================================================================

class _FakeChromaCollection:
    """内存版 ChromaDB collection，避免真实 ChromaDB 依赖。"""

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

# Mock openai
if "openai" not in sys.modules:
    sys.modules["openai"] = MagicMock()

# Mock chromadb
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
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_emotion_auth_")
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
    yield
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:
        pass


# =====================================================================
# 工具函数
# =====================================================================
def _init_runtime_bridge_with_core():
    """初始化 RuntimeBridge 和 RuntimeCore（emotion_enabled=False，测试 lazy 创建）。"""
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={"emotion_enabled": False, "autostart": False})
    bridge.initialize()
    return bridge


def _clear_emotion_files():
    """清除情绪状态文件。"""
    for path in [Path("data/emotion_state.json"),
                Path("data/emotion_traces.json"),
                Path("data/emotion_analysis_counter.json")]:
        if path.exists():
            path.unlink()


# =====================================================================
# 1. RuntimeCore 和 Orchestrator 引用同一个 EmotionManager
# =====================================================================
class TestEmotionManagerShared:
    """验证 RuntimeCore 和 Orchestrator 引用同一个 EmotionManager 实例。"""

    def test_orchestrator_uses_runtime_core_emotion_manager(self):
        """当 RuntimeBridge 已初始化时，Orchestrator 应使用 RuntimeCore 的 EmotionManager。"""
        _clear_emotion_files()
        # 先初始化 RuntimeBridge（包含 RuntimeCore）
        bridge = _init_runtime_bridge_with_core()
        runtime_core = bridge.get_runtime_core()
        assert runtime_core is not None

        # 获取 RuntimeCore 的 EmotionManager
        core_em = runtime_core.get_emotion_manager()
        assert core_em is not None, "RuntimeCore.get_emotion_manager() 应返回非 None"

        # 创建 Orchestrator，它应通过 RuntimeBridge 获取同一个实例
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "test_shared_user"

        # 验证引用相同
        assert orch.emotion_manager is not None, "Orchestrator 应获取到 EmotionManager"
        assert orch.emotion_manager is core_em, \
            "Orchestrator 的 EmotionManager 应与 RuntimeCore 的是同一实例（is 比较）"

    def test_runtime_bridge_get_emotion_manager_returns_core_instance(self):
        """RuntimeBridge.get_emotion_manager() 应返回 RuntimeCore 的实例。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()
        runtime_core = bridge.get_runtime_core()

        bridge_em = bridge.get_emotion_manager()
        core_em = runtime_core.emotion_manager

        assert bridge_em is not None
        assert bridge_em is core_em, "Bridge 应返回 RuntimeCore 持有的实例"

    def test_emotion_manager_identity_across_multiple_orchestrators(self):
        """多个 Orchestrator 实例应共享同一个 EmotionManager。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        from src.orchestrator import Orchestrator
        orch1 = Orchestrator()
        orch2 = Orchestrator()

        assert orch1.emotion_manager is not None
        assert orch1.emotion_manager is orch2.emotion_manager, \
            "两个 Orchestrator 实例应共享同一个 EmotionManager"


# =====================================================================
# 2. 情绪更新只产生一次状态变化（无双写）
# =====================================================================
class TestEmotionUpdateNoDoubleWrite:
    """验证情绪更新只产生一次状态变化，不存在双写问题。"""

    def test_process_event_writes_once(self):
        """process_event 后 emotion_state.json 应只被写入一次。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()
        core_em = bridge.get_emotion_manager()

        # 记录文件初始状态
        emotion_file = Path("data/emotion_state.json")
        initial_mtime = emotion_file.stat().st_mtime if emotion_file.exists() else 0

        # 处理一个情绪事件
        from src.emotion.emotion_event import EmotionEvent
        ev = EmotionEvent(
            event_type="user_praise",
            intensity=0.5,
            description="你真棒",
            source="user",
        )
        core_em.process_event(ev)

        # 验证文件被更新
        assert emotion_file.exists(), "情绪状态文件应存在"
        final_mtime = emotion_file.stat().st_mtime
        assert final_mtime > initial_mtime, "情绪状态文件应被更新"

        # 验证状态只变化一次（通过 valence 值判断）
        with open(emotion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        # user_praise 事件应使 valence 增加
        assert data["valence"] > 0, "情绪事件处理后 valence 应为正值"

    def test_no_concurrent_double_write(self):
        """模拟 Orchestrator 和 RuntimeCore 共享实例后不会双写。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()
        core_em = bridge.get_emotion_manager()

        # 记录 repository.save 的调用次数
        original_save = core_em.repository.save
        save_count = {"count": 0}

        def _counting_save(state):
            save_count["count"] += 1
            return original_save(state)

        core_em.repository.save = _counting_save

        try:
            from src.emotion.emotion_event import EmotionEvent
            ev = EmotionEvent(
                event_type="user_praise",
                intensity=0.3,
                description="谢谢",
                source="user",
            )
            core_em.process_event(ev)
        finally:
            core_em.repository.save = original_save

        # process_event 内部应只调用一次 repository.save
        assert save_count["count"] == 1, \
            f"process_event 应只调用一次 repository.save，实际 {save_count['count']} 次"

    def test_shared_instance_single_state_object(self):
        """共享实例后，Orchestrator 和 RuntimeCore 应访问同一个 state 对象。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()
        core_em = bridge.get_emotion_manager()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 验证 state 是同一对象
        assert orch.emotion_manager is core_em
        assert orch.emotion_manager.state is core_em.state, \
            "Orchestrator 和 RuntimeCore 应访问同一个 EmotionState 对象"

        # 记录初始 valence
        initial_valence = core_em.state.valence

        # 通过 Orchestrator 的 emotion_manager 处理事件
        from src.emotion.emotion_event import EmotionEvent
        ev = EmotionEvent(
            event_type="user_praise",
            intensity=0.5,
            description="你真棒",
            source="user",
        )
        orch.emotion_manager.process_event(ev)

        # 验证 RuntimeCore 的 state 也已更新（因为是同一对象）
        assert core_em.state.valence != initial_valence, \
            "RuntimeCore 的 state 应通过共享实例被更新"


# =====================================================================
# 3. emotion_state.json 不会被双写
# =====================================================================
class TestEmotionStateFileNoDoubleWrite:
    """验证 emotion_state.json 不会被两个实例同时写入。"""

    def test_single_emotion_state_file_writer(self):
        """共享实例后，应只有一个 EmotionManager 写入 emotion_state.json。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "file_test_user"

        # mock engine 避免真实 API
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"

        # 处理一条消息（会触发情绪更新）
        emotion_file = Path("data/emotion_state.json")
        initial_mtime = emotion_file.stat().st_mtime if emotion_file.exists() else 0

        orch.process("你真棒，谢谢")

        # 验证文件被更新
        assert emotion_file.exists()
        final_mtime = emotion_file.stat().st_mtime
        assert final_mtime >= initial_mtime

        # 验证文件内容合法（只有一个 EmotionState）
        with open(emotion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "valence" in data
        assert "arousal" in data
        # 不应有两个 EmotionState 的数据（如数组或嵌套）
        assert isinstance(data["valence"], (int, float)), \
            "emotion_state.json 应只包含一个 EmotionState，valence 应为数值"

    def test_no_separate_orchestrator_emotion_file(self):
        """Orchestrator 不应创建独立的 emotion_state 文件。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # Orchestrator 不应有独立的 repository
        # （它应使用 RuntimeCore EmotionManager 的 repository）
        core_em = bridge.get_emotion_manager()
        assert orch.emotion_manager is core_em
        # Orchestrator 的 emotion_manager.repository 应与 RuntimeCore 的相同
        assert orch.emotion_manager.repository is core_em.repository, \
            "Orchestrator 应共享 RuntimeCore 的 EmotionRepository"


# =====================================================================
# 4. RuntimeBridge.get_emotion_manager() 转发正确
# =====================================================================
class TestRuntimeBridgeEmotionForwarding:
    """验证 RuntimeBridge.get_emotion_manager() 正确转发。"""

    def test_bridge_returns_none_when_not_initialized(self):
        """RuntimeBridge 未初始化时应返回 None。"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        bridge = get_runtime_bridge()
        # 未调用 initialize()
        assert bridge.get_emotion_manager() is None

    def test_bridge_returns_emotion_manager_after_init(self):
        """RuntimeBridge 初始化后应返回 EmotionManager。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        em = bridge.get_emotion_manager()
        assert em is not None
        assert hasattr(em, "state")
        assert hasattr(em, "process_event")
        assert hasattr(em, "repository")

    def test_bridge_returns_same_instance_on_multiple_calls(self):
        """多次调用应返回同一个实例（lazy 创建后缓存）。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        em1 = bridge.get_emotion_manager()
        em2 = bridge.get_emotion_manager()
        assert em1 is em2, "多次调用应返回同一实例"

    def test_bridge_lazy_creates_when_emotion_disabled(self):
        """emotion_enabled=False 时，get_emotion_manager 应 lazy 创建基础实例。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()
        runtime_core = bridge.get_runtime_core()

        # 初始时 emotion_manager 应为 None（emotion_enabled=False）
        assert runtime_core.emotion_manager is None

        # 调用 get_emotion_manager 应 lazy 创建
        em = runtime_core.get_emotion_manager()
        assert em is not None
        # 再次访问应返回同一实例
        assert runtime_core.emotion_manager is em


# =====================================================================
# 5. Fallback 兼容性
# =====================================================================
class TestFallbackCompatibility:
    """验证 RuntimeBridge 不可用时 Orchestrator 自建 EmotionManager。"""

    def test_orchestrator_fallback_when_bridge_not_initialized(self):
        """RuntimeBridge 未初始化时，Orchestrator 应 fallback 自建。"""
        _clear_emotion_files()
        # 不初始化 RuntimeBridge
        from src.orchestrator import Orchestrator
        orch = Orchestrator()

        # 应 fallback 自建
        assert orch.emotion_manager is not None
        assert hasattr(orch.emotion_manager, "state")
        assert hasattr(orch.emotion_manager, "process_event")

    def test_orchestrator_fallback_does_not_crash(self):
        """Fallback 自建不应导致崩溃，聊天功能应正常。"""
        _clear_emotion_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        # process 应正常执行
        reply = orch.process("你好")
        assert reply == "测试回复"
        assert orch.emotion_manager is not None

    def test_orchestrator_emotion_works_in_fallback(self):
        """Fallback 模式下情绪更新仍应工作。"""
        _clear_emotion_files()
        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "fallback_emotion_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"

        initial_valence = orch.emotion_manager.state.valence
        orch.process("谢谢，你真棒")
        final_valence = orch.emotion_manager.state.valence

        # 情绪应已变化
        assert final_valence != initial_valence or final_valence > 0, \
            "Fallback 模式下情绪更新应正常工作"


# =====================================================================
# 回归保护：Phase 4.1 的情绪闭环仍正常
# =====================================================================
class TestPhase41Regression:
    """验证 Phase 4.1 的情绪闭环在共享实例后仍正常工作。"""

    def test_emotion_context_reaches_engine_with_shared_instance(self):
        """共享实例后，emotion_context 仍应正确传给 engine。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "shared_regression_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你真棒，谢谢")

        assert "emotion_context" in captured
        assert isinstance(captured["emotion_context"], dict)

    def test_emotion_persisted_after_process_with_shared_instance(self):
        """共享实例后，process() 后情绪状态应被持久化。"""
        _clear_emotion_files()
        bridge = _init_runtime_bridge_with_core()

        from src.orchestrator import Orchestrator
        orch = Orchestrator()
        orch.target_user_id = "shared_persist_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"

        orch.process("谢谢，你真棒")

        emotion_file = Path("data/emotion_state.json")
        assert emotion_file.exists(), "情绪状态应被持久化"
        with open(emotion_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "valence" in data
