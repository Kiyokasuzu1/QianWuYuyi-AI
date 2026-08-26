# -*- coding: utf-8 -*-
"""M2-1 宽召回（retrieval_broad_k）验收测试。

覆盖任务书 A-F：
A. retrieval_broad_k=20 时 vector search 实际收到 top_k=20
B. 20 个 semantic candidates 不会全部进入 prompt（注入 ≤ max_total）
C. injection_max_semantic 仍然生效
D. orchestrator 与 runtime_core 都传入相同 broad_k
E. _vector_relevance 只存在于运行时对象，不写入持久化 memory
F. M1 原有测试保持通过（回归在 test_memory_m1 / test_memory_selection 等）
"""
from __future__ import annotations

import pytest

from src.memory.memory_selection import select_injection_memories


def _rec(mid, content, ts="2026-08-20T10:00:00", importance=0.5):
    return {
        "id": mid,
        "content": content,
        "timestamp": ts,
        "user_id": "366648462",
        "role": "user",
        "importance": importance,
        "metadata": {"memory_type": "user_experience"},
    }


class _CaptureVector:
    """记录 search 收到的 top_k，并按固定 relevance 返回候选。"""

    def __init__(self, results=None):
        self._results = results or []
        self.last_top_k = None
        self.last_query = None

    def search(self, query, top_k=5, user_id=None):
        self.last_top_k = top_k
        self.last_query = query
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


# ============ A: broad_k 传给 vector.search ============
def test_orchestrator_passes_broad_k(monkeypatch):
    """retrieval_broad_k=20 → orchestrator 的 vector.search 收到 top_k=20。"""
    recs = [_rec("m1", "记录1")]
    vec = _CaptureVector([{"mem_id": "m1", "relevance": 0.8, "content": "记录1"}])
    store = _FakeStore(recs)

    from src.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr(
        "src.config.get",
        lambda path, default=None: 20 if path == "memory.retrieval_broad_k" else default,
    )
    orch._collect_chat_memories("366648462", query="测试")
    assert vec.last_top_k == 20, f"期望 top_k=20，实际 {vec.last_top_k}"


def test_broad_k_defaults_to_20(monkeypatch):
    """配置缺失时默认 20（fail-soft）。"""
    recs = [_rec("m1", "记录1")]
    vec = _CaptureVector([{"mem_id": "m1", "relevance": 0.8, "content": "记录1"}])
    store = _FakeStore(recs)

    from src.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    # config.get 抛异常 → 默认 20
    monkeypatch.setattr("src.config.get", lambda path, default=None: (_ for _ in ()).throw(RuntimeError()))
    orch._collect_chat_memories("366648462", query="测试")
    assert vec.last_top_k == 20


# ============ B: 20 个候选不会 20 个全进 prompt ============
def test_20_semantic_candidates_not_all_injected():
    """20 个 semantic 候选 → 注入总数 ≤ max_total（6）。"""
    recs = [_rec(f"m{i}", f"相关记忆{i}", f"2026-08-2{i%9}T10:00:00") for i in range(20)]
    semantic = [{"record": r, "relevance": 0.9 - i * 0.01} for i, r in enumerate(recs)]
    final = select_injection_memories(recs, semantic, query="")
    assert len(final) <= 6, f"注入 {len(final)} 条，应 ≤ 6"


# ============ C: injection_max_semantic 生效 ============
def test_injection_max_semantic_respected():
    """20 个 semantic 候选 → semantic 槽位 ≤ 3。"""
    recs = [_rec(f"m{i}", f"相关记忆{i}", f"2026-08-2{i%9}T10:00:00") for i in range(20)]
    semantic = [{"record": r, "relevance": 0.9 - i * 0.01} for i, r in enumerate(recs)]
    final = select_injection_memories(recs, semantic, query="")
    sem = [r for r in final if r.get("_semantic_relevance") is not None]
    assert len(sem) <= 3, f"semantic 槽 {len(sem)}，应 ≤ 3"


# ============ D: 双链同构（runtime_core 也传 broad_k） ============
def test_runtime_core_passes_broad_k(monkeypatch):
    """runtime_core 的 vm.search 也收到 top_k=20（与 orchestrator 同构）。"""
    from src.runtime.runtime_core import RuntimeCore

    class _Ctx:
        user_message = "测试"
        inputs = {"user_id": "366648462"}
        history = []
        retrieved_memories = []

    vec = _CaptureVector([{"mem_id": "m1", "relevance": 0.8, "content": "记录1"}])
    store = _FakeStore([_rec("m1", "记录1")])

    rc = RuntimeCore.__new__(RuntimeCore)
    rc.memory_adapter = None
    # memory_store 是只读 property → 经 _lazy_memory_store 注入
    rc._lazy_memory_store = store
    monkeypatch.setattr(rc, "get_vector_memory", lambda: vec)
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr(
        "src.config.get",
        lambda path, default=None: 20 if path == "memory.retrieval_broad_k" else default,
    )
    rc._stage_02_memory_retrieval(None, _Ctx())
    assert vec.last_top_k == 20, f"runtime_core 期望 top_k=20，实际 {vec.last_top_k}"


# ============ E: _vector_relevance 不持久化 ============
def test_vector_relevance_not_persisted(monkeypatch):
    """_vector_relevance 只挂在运行时 record，不写回 store 记录。"""
    recs = [_rec("m1", "记录1")]
    vec = _CaptureVector([{"mem_id": "m1", "relevance": 0.8, "content": "记录1"}])
    store = _FakeStore(recs)

    from src.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.memory_store = store
    orch.vector_memory = vec
    orch.target_user_id = "366648462"
    monkeypatch.setattr(
        "src.memory.memory_scope.collect_allowed_records",
        lambda store_, uid, owner_limit=None: store_.get_by_user(uid),
    )
    monkeypatch.setattr(
        "src.config.get",
        lambda path, default=None: 20 if path == "memory.retrieval_broad_k" else default,
    )
    final = orch._collect_chat_memories("366648462", query="测试")

    # 注入列表中的 record 是运行时对象（允许临时键），但 store 源记录无该键
    assert all("_vector_relevance" not in r for r in store._records)
    # 且注入列表内容不受污染（_vector_relevance 不参与渲染字段）
    for r in final:
        assert "content" in r and "id" in r
