"""
羽依记忆闭环验收测试
"""

from src.memory.memory_extractor import MemoryExtractor
from src.memory.memory_verifier import MemoryVerifier
from src.memory.memory_store import MemoryStore
from src.memory.memory_retriever import MemoryRetriever
from src.memory.context_builder import ContextBuilder


def test_full_pipeline_preference():
    """偏好记忆闭环"""
    extractor = MemoryExtractor()
    verifier = MemoryVerifier()
    store = MemoryStore("data/test_pipeline.json")
    store.clear()

    raw = {"role": "user", "content": "我喜欢AI绘画"}
    candidates = extractor.extract([raw])
    assert len(candidates) > 0

    for cand in candidates:
        verified = verifier.verify(cand.to_dict())
        assert verified["memory_class"] == "preference"
        store.add(verified)

    retriever = MemoryRetriever(store)
    results = retriever.search("陪我聊AI绘画")
    assert len(results) > 0
    assert any("AI绘画" in r.get("content", "") for r in results)

    builder = ContextBuilder()
    prompt = builder.build(results)
    assert "AI绘画" in prompt


def test_pipeline_identity_and_relation():
    """身份 + 关系闭环"""
    extractor = MemoryExtractor()
    verifier = MemoryVerifier()
    store = MemoryStore("data/test_pipeline.json")
    store.clear()

    messages = [
        {"role": "user", "content": "我叫清夏铃"},
        {"role": "user", "content": "我是程序员"},
        {"role": "user", "content": "我是羽依的朋友"},
        {"role": "user", "content": "以后叫我清清"},
    ]

    for msg in messages:
        candidates = extractor.extract([msg])
        for cand in candidates:
            verified = verifier.verify(cand.to_dict())
            store.add(verified)

    retriever = MemoryRetriever(store)
    results = retriever.search("清夏铃是谁")
    builder = ContextBuilder()
    prompt = builder.build(results)
    assert "清夏铃" in prompt


def test_assistant_not_stored():
    """AI 回复不会被保存"""
    extractor = MemoryExtractor()
    verifier = MemoryVerifier()
    store = MemoryStore("data/test_pipeline.json")
    store.clear()

    raw = {"role": "assistant", "content": "你喜欢AI绘画对吗"}
    candidates = extractor.extract([raw])
    assert len(candidates) == 0

    verified = verifier.verify(raw)
    assert verified["memory_class"] == "assistant_output"
    assert verified["truth"] == 0.0
    store.add(verified)
    all_memories = store.load()
    assert all_memories == []


def test_growth_context_isolation():
    """污染路径被切断：玩笑和 AI 回复不影响成长"""
    verifier = MemoryVerifier()

    joke = verifier.verify({"role": "user", "content": "我是外星人"})
    assert joke["memory_class"] == "user_statement"
    assert "growth" not in joke["usage"]
    assert "persona" not in joke["usage"]

    ai = verifier.verify({"role": "assistant", "content": "你是一个孤独的人"})
    assert ai["memory_class"] == "assistant_output"
    assert "growth" not in ai["usage"]


def test_assistant_truth_zero_block():
    """assistant 记忆因 truth=0 被 Store 拒绝"""
    verifier = MemoryVerifier()
    store = MemoryStore("data/test_pipeline.json")
    store.clear()

    ai_mem = verifier.verify({"role": "assistant", "content": "你喜欢猫"})
    assert ai_mem["truth"] == 0.0
    store.add(ai_mem)

    memories = store.load()
    assert len(memories) == 0


def test_user_statement_no_growth_permission():
    """用户玩笑话 (user_statement) 不授予 growth 权限"""
    verifier = MemoryVerifier()
    mem = verifier.verify({"role": "user", "content": "我是外星人"})
    assert mem["memory_class"] == "user_statement"
    assert "growth" not in mem["usage"]
    assert "persona" not in mem["usage"]