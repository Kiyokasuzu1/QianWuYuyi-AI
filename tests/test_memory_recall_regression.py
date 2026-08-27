# -*- coding: utf-8 -*-
"""v1.5.5 Memory Recall Fix: 核心回归测试（永久防回归）。

场景：5 条新记忆 + 1 条 7 月的旧相关记忆（semantic 召回）
→ selection → orchestrator → engine → 最终 prompt 必须包含旧记忆。

若未来有人把 selection 改回"纯最新优先 + [:5]"，本测试立即失败。
"""
import pytest


def _rec(mid, content, ts):
    return {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "user_id": "366648462",
        "role": "user",
        "importance": 0.5,
        "metadata": {"memory_type": "user_experience"},
    }


class _FakeVector:
    """返回固定 semantic 候选（旧相关记忆 + relevance）。"""

    def __init__(self, results):
        self._results = results

    def search(self, query, top_k=5, user_id=None):
        return self._results


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def load(self):
        return list(self._records)

    def get_by_id(self, mem_id):
        for r in self._records:
            if r.get("id") == mem_id:
                return r
        return None

    def get_by_user(self, uid):
        return [r for r in self._records if r.get("user_id") == uid]


def _make_orchestrator(store, vector, monkeypatch):
    """轻量 Orchestrator：手工装配 _collect_chat_memories 所需属性。"""
    from src.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vector
    orch.target_user_id = "366648462"
    # 兼容 memory_scope 可能需要的其他属性（若 collect_allowed_records 抛异常走 fallback）
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    return orch


# ============================================================
# 1. 候选集合正确 + semantic 候选被保留（端到端到 orchestrator）
# ============================================================
def test_orchestrator_keeps_old_relevant(monkeypatch):
    recents = [_rec(f"R{i}", f"最近记忆 {i}", f"2026-08-2{i}T10:00:00") for i in range(5)]
    old = _rec("OLD_RELEVANT", "我们第一次见面时聊过的事情", "2026-07-16T10:00:00")
    store = _FakeStore(recents + [old])

    # 模拟 vector 检索：query 命中旧记忆，relevance 0.85
    vec = _FakeVector([{
        "content": old["content"],
        "role": "user",
        "timestamp": old["timestamp"],
        "relevance": 0.85,
        "mem_id": "OLD_RELEVANT",
        "user_id": "366648462",
    }])

    orch = _make_orchestrator(store, vec, monkeypatch)
    final = orch._collect_chat_memories("366648462", query="我们第一次见面聊了什么")

    ids = [r.get("id") for r in final]
    assert "OLD_RELEVANT" in ids  # 旧相关记忆进入 final
    assert len(final) <= 6  # 总数不超
    assert len(set(ids)) == len(ids)  # 无重复


# ============================================================
# 2. duplicate 正确去除（同一记忆出现在 semantic + recent）
# ============================================================
def test_dedupe_in_orchestrator(monkeypatch):
    dup = _rec("DUP", "重复的记忆", "2026-07-01T10:00:00")
    recents = [_rec("R1", "r1", "2026-08-26T10:00:00"), dict(dup)]
    store = _FakeStore(recents)

    vec = _FakeVector([{
        "content": dup["content"], "role": "user", "timestamp": dup["timestamp"],
        "relevance": 0.9, "mem_id": "DUP", "user_id": "366648462",
    }])
    orch = _make_orchestrator(store, vec, monkeypatch)
    final = orch._collect_chat_memories("366648462", query="q")
    ids = [r.get("id") for r in final]
    assert ids.count("DUP") == 1


# ============================================================
# 3. 无高质量 semantic 时 recent 补足
# ============================================================
def test_recent_fills_when_no_semantic(monkeypatch):
    recents = [_rec(f"R{i}", f"r{i}", f"2026-08-2{i}T10:00:00") for i in range(6)]
    store = _FakeStore(recents)
    vec = _FakeVector([])  # 无 semantic 结果
    orch = _make_orchestrator(store, vec, monkeypatch)
    final = orch._collect_chat_memories("366648462", query="q")
    assert len(final) <= 6
    assert len(final) >= 1


# ============================================================
# 4. 最终 prompt 确实包含旧记忆（engine 端到端）
# ============================================================
def test_engine_prompt_contains_old_relevant(monkeypatch):
    recents = [_rec(f"R{i}", f"最近记忆 {i}", f"2026-08-2{i}T10:00:00") for i in range(5)]
    old = _rec("OLD_RELEVANT", "我们第一次见面时聊过的事情", "2026-07-16T10:00:00")
    store = _FakeStore(recents + [old])
    vec = _FakeVector([{
        "content": old["content"], "role": "user", "timestamp": old["timestamp"],
        "relevance": 0.85, "mem_id": "OLD_RELEVANT", "user_id": "366648462",
    }])
    orch = _make_orchestrator(store, vec, monkeypatch)
    final = orch._collect_chat_memories("366648462", query="我们第一次见面聊了什么")

    # 走顶层 engine 的 original 路径（生产主链）
    from src.engine import ResponseEngine
    eng = ResponseEngine.__new__(ResponseEngine)
    eng.api_key = "sk"
    eng._client = None
    eng.model = "deepseek-v4-pro"
    eng.mock_mode = True
    eng._empty_retry_max = 0

    msgs = eng._build_messages_original(
        user_message="我们第一次见面聊了什么",
        history=[], chat_memories=final, life_events=[],
        personality_context="", self_model_context={},
        emotion_context={}, relationship_context={},
        context_prompt_blocks=[],
    )
    system_content = msgs[0]["content"]
    assert "OLD_RELEVANT" not in system_content  # id 不应出现（渲染的是 content）
    assert "我们第一次见面时聊过的事情" in system_content  # 旧记忆内容进入最终 prompt
    assert "最近记忆 0" in system_content  # recent 也正常注入
