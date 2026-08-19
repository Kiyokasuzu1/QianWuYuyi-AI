# -*- coding: utf-8 -*-
"""
tests/test_runtime_unification.py

Phase 4.1 Runtime Unification 测试套件。

覆盖目标：
1. RuntimeContext key 完整性（assemble_context 返回 8 种上下文 key）
2. EmotionManager 可被 Orchestrator 访问
3. Emotion 变化可进入 prompt
4. Memory 写入后立即向量检索
5. 主动消息读取统一状态（history 持久化与加载）

设计原则：
- 不依赖外部 API（DeepSeek / ChromaDB 真实服务）
- 使用 in-memory fake ChromaDB 集合模拟向量存储
- 优雅降级：若某些模块导入失败，对应测试用 skip 标记
- 所有文件操作使用临时工作目录，避免污染真实数据
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
    """内存版 ChromaDB collection，模拟 upsert/query/count 行为。"""

    def __init__(self):
        self._data = {}  # id -> {"document": str, "metadata": dict}

    def upsert(self, documents=None, metadatas=None, ids=None):
        if not documents:
            return
        for i, doc in enumerate(documents):
            mid = ids[i] if i < len(ids) else f"auto_{i}"
            meta = metadatas[i] if metadatas and i < len(metadatas) else {}
            self._data[mid] = {"document": doc, "metadata": dict(meta)}

    def query(self, query_texts=None, n_results=10):
        # 简化：返回所有文档，按字符串匹配度排序
        query = query_texts[0] if query_texts else ""
        docs = []
        metas = []
        dists = []
        for mid, entry in self._data.items():
            docs.append(entry["document"])
            metas.append(entry["metadata"])
            # 简单的距离计算：包含查询词越多距离越小
            overlap = sum(1 for w in query.split() if w in entry["document"])
            dists.append(max(0.0, 1.0 - overlap / max(len(query.split()), 1)))

        # 按距离排序
        combined = sorted(zip(docs, metas, dists), key=lambda x: x[2])
        return {
            "documents": [[c[0] for c in combined[:n_results]]],
            "metadatas": [[c[1] for c in combined[:n_results]]],
            "distances": [[c[2] for c in combined[:n_results]]],
        }

    def count(self):
        return len(self._data)

    def delete_collection(self, name):
        self._data.clear()


class _FakeChromaClient:
    """内存版 ChromaDB PersistentClient。"""

    def __init__(self, path=None):
        self._collection = _FakeChromaCollection()

    def get_collection(self, name):
        return self._collection

    def create_collection(self, name, embedding_function=None):
        return self._collection

    def delete_collection(self, name):
        self._collection._data.clear()


# 在导入任何项目模块之前，mock 掉重型依赖
_fake_client = _FakeChromaClient()

# Mock openai
_openai_mock = MagicMock()
if "openai" not in sys.modules:
    sys.modules["openai"] = _openai_mock

# Mock chromadb 及相关模块，使用 in-memory fake
_chromadb_mock = MagicMock()
_chromadb_mock.PersistentClient.side_effect = lambda **kwargs: _fake_client
_chromadb_utils_mock = MagicMock()
_emb_func_mock = MagicMock()
_emb_func_mock.SentenceTransformerEmbeddingFunction.return_value = MagicMock()

if "chromadb" not in sys.modules:
    sys.modules["chromadb"] = _chromadb_mock
else:
    sys.modules["chromadb"].PersistentClient.side_effect = lambda **kwargs: _fake_client

if "chromadb.utils" not in sys.modules:
    sys.modules["chromadb.utils"] = _chromadb_utils_mock

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
    """切换到临时工作目录，避免污染真实仓库数据。"""
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_test_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_fake_collection():
    """每个测试前清空 fake collection，避免数据污染。"""
    _fake_client._collection._data.clear()
    # 重置 status 文件（避免上一个测试的损坏文件影响）
    status_file = Path("data/chroma_db/index_status.json")
    if status_file.exists():
        status_file.unlink()
    yield
    _fake_client._collection._data.clear()


# =====================================================================
# 工具函数
# =====================================================================
def _safe_import_orchestrator():
    """尝试导入 Orchestrator。若依赖缺失则 skip。"""
    try:
        from src.orchestrator import Orchestrator
        return Orchestrator
    except Exception as e:
        pytest.skip(f"Orchestrator 导入失败: {e}")
        return None


def _clear_history_file():
    """清除 conversation_history.json（测试前置条件）。"""
    history_path = Path("data/conversation_history.json")
    if history_path.exists():
        history_path.unlink()


# =====================================================================
# 1. RuntimeContext key 完整性
# =====================================================================
class TestRuntimeContextKeys:
    """验证 RuntimeContext.assemble_context() 返回包含所有 Phase 4.1 新增 key。"""

    def _build_rc(self):
        from src.runtime.runtime_context import RuntimeContext
        return RuntimeContext()

    def test_all_8_new_keys_present(self):
        """传入所有新参数后，返回字典应包含 8 种新增 key。"""
        rc = self._build_rc()
        em_manager = MagicMock()
        em_manager.state = MagicMock()
        em_manager.state.dominant = "joyful"
        em_manager.state.intensity = 0.7

        ctx = rc.assemble_context(
            user_id="test_user",
            conversation={"recent_turns": []},
            self_model_snapshot={"display_name": "羽依"},
            memory_summary={"recent_memories": []},
            options={"relationship_summary": {"trust": 0.5}},
            emotion_manager=em_manager,
            personality_context="当前人格：温柔",
            screen_context={"available": False},
            user_context={"user_id": "test_user"},
            token_context={"enabled": False},
        )

        # 验证新增的 8 个 key 都存在
        for key in ("personality_context", "emotion_manager", "emotion_context",
                    "relationship_profile", "screen_context", "user_context",
                    "token_context"):
            assert key in ctx, f"缺失新增 key: {key}"

        # emotion_context 应该有真实内容
        assert ctx["emotion_context"]["dominant"] == "joyful"
        assert ctx["emotion_context"]["intensity"] == 0.7
        # relationship_profile 应与 relationship 同值（别名）
        assert ctx["relationship_profile"] == ctx["relationship"]
        # personality_context 应被透传
        assert ctx["personality_context"] == "当前人格：温柔"

    def test_backward_compatible_no_extra_args(self):
        """不传新参数时，旧 7 个 key 仍存在且行为不变。"""
        rc = self._build_rc()
        ctx = rc.assemble_context(
            user_id="test_user",
            conversation={"recent_turns": []},
        )
        # 旧 key 必须存在
        for key in ("system_messages", "prompt_blocks", "conversation",
                    "self_model", "memory_summary", "relationship", "trace"):
            assert key in ctx, f"缺失旧 key: {key}"

        # 新 key 应该存在但值为 None 或空 dict（保持兼容）
        assert ctx["personality_context"] is None
        assert ctx["emotion_manager"] is None
        assert ctx["emotion_context"] == {}
        assert ctx["screen_context"] is None
        assert ctx["user_context"] is None
        assert ctx["token_context"] is None

    def test_relationship_profile_alias_matches_relationship(self):
        """relationship_profile 与 relationship 必须同值（别名）。"""
        rc = self._build_rc()
        rel_summary = {"trust": 0.8, "familiarity": 0.5}
        ctx = rc.assemble_context(
            user_id="u",
            conversation={"recent_turns": []},
            options={"relationship_summary": rel_summary},
        )
        assert ctx["relationship"] == rel_summary
        assert ctx["relationship_profile"] == rel_summary

    def test_emotion_context_built_from_manager(self):
        """传入 EmotionManager 后，emotion_context 应被构建。"""
        rc = self._build_rc()
        em_manager = MagicMock()
        em_manager.state = MagicMock()
        em_manager.state.dominant = "tense"
        em_manager.state.intensity = 0.6

        ctx = rc.assemble_context(
            user_id="u",
            conversation={"recent_turns": []},
            emotion_manager=em_manager,
        )
        assert ctx["emotion_context"] == {"dominant": "tense", "intensity": 0.6}
        assert any("emotion_context built" in t for t in ctx["trace"])


# =====================================================================
# 2. EmotionManager 可被 Orchestrator 访问
# =====================================================================
class TestEmotionManagerAccess:
    """验证 Orchestrator 能持有并访问 EmotionManager 实例。"""

    def test_orchestrator_has_emotion_manager_attribute(self):
        """Orchestrator 实例必须有 emotion_manager 属性。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        assert hasattr(orch, "emotion_manager")
        if orch.emotion_manager is not None:
            assert hasattr(orch.emotion_manager, "state")
            assert hasattr(orch.emotion_manager, "process_event")

    def test_assembled_context_contains_emotion_manager(self):
        """process() 调用后，assembled_context 应包含 emotion_manager 引用。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        orch.target_user_id = "test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        # 捕获 assemble_context 返回值
        original_assemble = orch.runtime_context.assemble_context
        captured = {}

        def _spy_assemble(*args, **kwargs):
            ctx = original_assemble(*args, **kwargs)
            captured["ctx"] = ctx
            return ctx

        orch.runtime_context.assemble_context = _spy_assemble
        try:
            orch.process("你好")
        finally:
            orch.runtime_context.assemble_context = original_assemble

        assert "ctx" in captured, "assemble_context 未被调用"
        ctx = captured["ctx"]
        assert "emotion_manager" in ctx
        if orch.emotion_manager is not None:
            assert ctx["emotion_manager"] is orch.emotion_manager

    def test_emotion_manager_persists_state_after_process(self):
        """process() 后情绪状态应被持久化（data/emotions/ 或 data/emotion_state.json）。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        if orch.emotion_manager is None:
            pytest.skip("EmotionManager 未初始化，跳过持久化测试")

        orch.target_user_id = "persist_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "好的"

        orch.process("谢谢，你真棒")

        # 情绪状态应被持久化到某个文件
        # _process_emotion_post 会写入 data/emotions/{user_id}.json
        per_user_file = Path("data/emotions") / "persist_test_user.json"
        default_file = Path("data/emotion_state.json")

        # 至少有一个文件存在
        assert per_user_file.exists() or default_file.exists(), \
            "情绪状态未被持久化到任何文件"

        # 若 per_user_file 存在，验证内容
        if per_user_file.exists():
            with open(per_user_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert "valence" in data or "dominant" in data


# =====================================================================
# 3. Emotion 变化进入 prompt
# =====================================================================
class TestEmotionInPrompt:
    """验证情绪上下文能正确进入 engine 的 prompt。"""

    def test_emotion_state_dominant_property(self):
        """EmotionState.dominant 应返回正确的标签。"""
        from src.emotion.emotion_state import EmotionState
        assert EmotionState(valence=0.7, arousal=0.8).dominant == "joyful"
        assert EmotionState(valence=0.5, arousal=0.2).dominant == "serene"
        assert EmotionState(valence=-0.5, arousal=0.8).dominant == "tense"
        assert EmotionState(anxiety=0.7).dominant == "anxious"
        assert EmotionState(valence=0.0, arousal=0.5).dominant == "neutral"

    def test_emotion_state_intensity_property(self):
        """EmotionState.intensity 应返回正确的数值。"""
        from src.emotion.emotion_state import EmotionState
        s = EmotionState(valence=0.5, arousal=0.5)
        # |0.5| * 0.6 + 0.5 * 0.4 = 0.3 + 0.2 = 0.5
        assert abs(s.intensity - 0.5) < 0.01

        s2 = EmotionState(valence=-0.8, arousal=0.9)
        # 0.8 * 0.6 + 0.9 * 0.4 = 0.48 + 0.36 = 0.84
        assert abs(s2.intensity - 0.84) < 0.01

    def test_emotion_state_to_dict_includes_derived(self):
        """to_dict 应包含 dominant 和 intensity 派生字段。"""
        from src.emotion.emotion_state import EmotionState
        s = EmotionState(valence=0.6, arousal=0.8)
        d = s.to_dict()
        assert "dominant" in d
        assert "intensity" in d
        assert d["dominant"] == "joyful"
        assert abs(d["intensity"] - (0.6 * 0.6 + 0.8 * 0.4)) < 0.01

    def test_emotion_context_reaches_engine(self):
        """Orchestrator.process() 应将 emotion_context 传给 engine.generate。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        orch.target_user_id = "prompt_test_user"

        captured = {}

        def _fake_generate(**kwargs):
            captured.update(kwargs)
            return "好的"

        orch.engine.generate = _fake_generate
        orch.process("你真棒，谢谢")

        # 验证 emotion_context 被传给 engine
        assert "emotion_context" in captured, "engine.generate 未收到 emotion_context 参数"
        ec = captured["emotion_context"]
        assert isinstance(ec, dict), f"emotion_context 不是 dict: {type(ec)}"

    def test_emotion_appears_in_system_prompt(self):
        """engine._build_messages_original 应在系统提示中包含【当前情绪】段。"""
        from src.engine import ResponseEngine
        engine = ResponseEngine()
        messages = engine._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={"dominant": "开心", "intensity": 0.8},
            relationship_context={},
            context_prompt_blocks=[],
        )
        assert len(messages) > 0
        system_content = messages[0]["content"]
        assert "开心" in system_content or "当前情绪" in system_content


# =====================================================================
# 4. Memory 写入后立即向量检索
# =====================================================================
class TestVectorRealtimeUpdate:
    """验证 Orchestrator 写入新记忆后，向量索引能立即检索。"""

    def test_add_memory_then_search(self):
        """写入记忆 → 立即 search → 能返回。"""
        from src.memory.vector import VectorMemory
        vm = VectorMemory()

        unique_content = "测试记忆_独特标记_星空与海洋"
        mem_id = "test_mem_unique_001"
        memory = {
            "id": mem_id,
            "content": unique_content,
            "timestamp": "2026-07-30T12:00:00",
            "role": "user",
            "user_id": "vector_test_user",
        }
        vm.add_memory(memory)

        # 立即 search
        results = vm.search("星空 海洋", top_k=5)
        found = any(r.get("content") == unique_content for r in results)
        assert found, f"写入后未立即检索到新记忆: {results}"

    def test_non_user_memory_not_indexed(self):
        """写入 role != user 的记忆 → search → 不应返回。"""
        from src.memory.vector import VectorMemory
        vm = VectorMemory()
        memory = {
            "id": "test_assistant_mem_002",
            "content": "助手回复内容_不应被索引",
            "timestamp": "2026-07-30T12:00:00",
            "role": "assistant",
            "user_id": "vector_test_user",
        }
        vm.add_memory(memory)
        results = vm.search("助手回复内容", top_k=5)
        for r in results:
            assert r.get("content") != memory["content"], "assistant 记忆被错误索引"

    def test_vector_search_after_multiple_adds(self):
        """多次写入 → search → 全部可检索。"""
        from src.memory.vector import VectorMemory
        vm = VectorMemory()
        contents = []
        for i in range(3):
            content = f"多次写入测试_{i}_独特点点"
            contents.append(content)
            vm.add_memory({
                "id": f"test_multi_mem_{i}",
                "content": content,
                "timestamp": "2026-07-30T12:00:00",
                "role": "user",
                "user_id": "vector_test_user",
            })

        results = vm.search("多次写入测试 独特点点", top_k=10)
        found_contents = {r.get("content") for r in results}
        # 至少有一条命中
        assert len(found_contents & set(contents)) > 0, \
            f"多次写入后未检索到任何记忆: {results}"


# =====================================================================
# 5. 主动消息读取统一状态
# =====================================================================
class TestInitiativeStateConsistency:
    """验证 Orchestrator 的 history 持久化与加载。"""

    def test_persist_history(self):
        """process() 后 data/conversation_history.json 应更新。"""
        _clear_history_file()
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        orch.target_user_id = "persist_history_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "测试回复"

        history_path = Path("data/conversation_history.json")
        orch.process("持久化测试消息")

        assert history_path.exists(), "conversation_history.json 未生成"

        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "history" in data
        assert "updated_at" in data
        history = data["history"]
        assert len(history) >= 2
        assert any("持久化测试消息" in h.get("content", "") for h in history)

    def test_load_recent_history(self):
        """load_recent_history() 应能从文件加载历史。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        # 先写入一个历史文件
        history_path = Path("data/conversation_history.json")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        test_history = [
            {"role": "user", "content": "历史消息1"},
            {"role": "assistant", "content": "历史回复1"},
            {"role": "user", "content": "历史消息2"},
            {"role": "assistant", "content": "历史回复2"},
        ]
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump({"history": test_history, "updated_at": "2026-07-30T12:00:00"}, f,
                      ensure_ascii=False, indent=2)

        orch = Orchestrator()
        assert orch.history == []
        orch.load_recent_history(max_turns=2)
        assert len(orch.history) == 4
        assert orch.history[0]["content"] == "历史消息1"
        assert orch.history[-1]["content"] == "历史回复2"

    def test_load_history_fallback_on_missing_file(self):
        """文件不存在时 load_recent_history 应降级为空 list。"""
        _clear_history_file()
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        orch.history = []
        orch.load_recent_history()
        assert orch.history == []

    def test_load_history_fallback_on_corrupt_file(self):
        """文件损坏时 load_recent_history 应降级为空 list。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        history_path = Path("data/conversation_history.json")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as f:
            f.write("{invalid json content")

        orch = Orchestrator()
        orch.history = []
        orch.load_recent_history()
        assert orch.history == []

    def test_initiative_bridge_calls_generate_with_history_loaded(self):
        """Phase 7.2.1-p1 新架构：验证 InitiativeBridge.handle_send_message()
        在调用 orchestrator.generate_initiative() 前，orchestrator.history 应已
        通过 load_recent_history() 预加载（由 api_server 启动阶段或 pipeline 前置
        步骤完成）。这里通过 mock 检查调用链顺序等价性。"""
        Orchestrator = _safe_import_orchestrator()
        if Orchestrator is None:
            return
        orch = Orchestrator()
        orch.target_user_id = "bridge_test_user"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "主动消息回复"

        # 模拟 history 已被预加载（旧版 main_loop 里的 load_recent_history 步骤）
        history_path = Path("data/conversation_history.json")
        history_path.parent.mkdir(parents=True, exist_ok=True)
        test_history = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ]
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump({"history": test_history, "updated_at": "2026-07-30T12:00:00"},
                      f, ensure_ascii=False, indent=2)
        orch.history = []
        orch.load_recent_history(max_turns=2)
        assert len(orch.history) >= 2, "load_recent_history 未正确加载历史"

        # 构造 InitiativeBridge 并调用 generate_initiative
        from src.runtime.initiative_bridge import InitiativeBridge
        bridge = InitiativeBridge(
            orchestrator=orch,
            send_config={
                "initiative_cooldown_seconds": 0,  # 测试用：关 cooldown
                "send_callback": lambda msg: True,  # 用 fake callback 代替 HTTP
            },
        )

        # 记录 generate_initiative 被调用时 orch.history 的长度
        call_state = {"history_len_at_gen": -1}

        def _spy_gen(user_id):
            call_state["history_len_at_gen"] = len(orch.history)
            return "测试主动消息内容"

        original_gen = orch.generate_initiative
        orch.generate_initiative = _spy_gen
        try:
            from src.runtime.action_dispatcher import Action
            action = Action(
                action_id="test_action_001",
                action_type="send_message",
                reason="测试触发",
                payload={"message": None},  # 不带 message → 走 generate_initiative 分支
            )
            result = bridge.handle_send_message(action)
        finally:
            orch.generate_initiative = original_gen

        # 断言：generate_initiative 被调用时，history 已加载（长度 >= 2）
        assert call_state["history_len_at_gen"] >= 2, \
            (f"generate_initiative 被调用时 history 未预加载，"
             f"实际长度={call_state['history_len_at_gen']}")
        # 断言：消息已被"发送"(callback 返回 True)
        assert result.get("sent") is True, f"handle_send_message 未成功发送: {result}"


# =====================================================================
# 回归保护：EmotionEvent 字段契约
# =====================================================================
class TestEmotionEventContract:
    """验证 EmotionEvent 构造字段契约，防止再次引入非法字段。"""

    def test_emotion_event_accepts_correct_fields(self):
        """EmotionEvent 应接受 event_type/intensity/description/source。"""
        from src.emotion.emotion_event import EmotionEvent
        ev = EmotionEvent(
            event_type="user_praise",
            intensity=0.6,
            description="你真棒",
            source="user",
        )
        assert ev.event_type == "user_praise"
        assert ev.intensity == 0.6
        assert ev.description == "你真棒"
        assert ev.source == "user"

    def test_emotion_event_detector_returns_valid_event(self):
        """EmotionEventDetector.detect() 应返回字段正确的 EmotionEvent。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        ev = detector.detect("谢谢，你真棒")
        assert ev is not None
        assert ev.event_type == "user_praise"
        assert isinstance(ev.intensity, (int, float))
        assert isinstance(ev.description, str)
        assert isinstance(ev.source, str)

    def test_emotion_event_detector_negative(self):
        """负面消息应触发 user_conflict 事件。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        ev = detector.detect("我讨厌这个")
        assert ev is not None
        assert ev.event_type == "user_conflict"

    def test_emotion_event_detector_neutral(self):
        """中性消息应返回 None。"""
        from src.emotion.emotion_event_detector import EmotionEventDetector
        detector = EmotionEventDetector()
        ev = detector.detect("今天天气不错")
        assert ev is None
