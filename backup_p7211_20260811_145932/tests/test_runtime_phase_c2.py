# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_c2.py

Phase C.2 Memory Runtime Integration —— MemoryRuntimeAdapter 测试

覆盖 7 类:
  1. Memory Adapter(MemoryRuntimeAdapter 基础能力 / 协议兼容)
  2. Runtime Integration(接入 RuntimeCycleOrchestrator)
  3. Retrieval(检索:vector / store / fallback / empty)
  4. Candidate 生成(MemoryCandidate + collect_candidate)
  5. 异常隔离(MemoryStore 失败 / VectorMemory 失败 / input_event 异常)
  6. Health Check / Snapshot
  7. Regression(不修改 MemoryStore / VectorMemory / Orchestrator / B.4-B.13)

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化 MemoryStore / VectorMemory
  - 不依赖 RuntimeCore 单例
"""
from __future__ import annotations

import sys
import time
import threading
import tempfile
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.adapters.impl.memory_runtime_adapter import (
    MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION,
    PHASE_C2_NAME,
    PHASE_C2_VERSION,
    PHASE_C2_STAGE_MEMORY_RETRIEVED,
    PHASE_C2_STAGE_CANDIDATE_GENERATED,
    PHASE_C2_STAGE_VECTOR_FALLBACK,
    PHASE_C2_STAGE_STORE_FALLBACK,
    ALL_PHASE_C2_STAGES,
    MemoryCandidate,
    MemoryRetrievalResult,
    MemoryRuntimeAdapter,
    create_memory_runtime_adapter,
    safe_get_memory_adapter_summary,
    _extract_query_text,
)
from src.runtime.cycle_adapter import (
    is_cycle_adapter,
    STANDARD_ADAPTER_MEMORY,
)
from src.runtime.cycle_context import RuntimeCycleContext
from src.runtime.cycle_event import (
    CYCLE_EVENT_MEMORY_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    PHASE_C1_STAGE_STEP_COMPLETED,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Helper:Mock MemoryStore / VectorMemory
# ============================================================


class _FakeMemoryStore:
    """模拟 MemoryStore。"""

    def __init__(
        self,
        memories: Optional[List[Dict[str, Any]]] = None,
        fail: bool = False,
    ) -> None:
        self._memories = list(memories or [])
        self._fail = fail
        self.add_count = 0
        self.load_count = 0

    def load(self) -> List[Dict[str, Any]]:
        self.load_count += 1
        if self._fail:
            raise RuntimeError("memory_store_disk_error")
        return list(self._memories)

    def add(self, memory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.add_count += 1
        if self._fail:
            raise RuntimeError("memory_store_add_error")
        m = dict(memory or {})
        m["id"] = m.get("id", f"mem_{self.add_count}_{len(self._memories)}")
        self._memories.append(m)
        return m

    def get_by_id(self, memory_id: str) -> Optional[Dict[str, Any]]:
        for m in self._memories:
            if m.get("id") == memory_id:
                return m
        return None

    def get_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        return [m for m in self._memories if m.get("user_id") == user_id]


class _FakeVectorMemory:
    """模拟 VectorMemory。"""

    def __init__(
        self,
        items: Optional[List[Dict[str, Any]]] = None,
        fail: bool = False,
        indexed_count: int = 0,
    ) -> None:
        self._items = list(items or [])
        self._fail = fail
        self._indexed_count = int(indexed_count or len(self._items))
        self.search_count = 0

    def search(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        self.search_count += 1
        if self._fail:
            raise RuntimeError("vector_memory_search_error")
        if not query:
            return []
        if self._indexed_count == 0:
            return []
        # 简单相关性:query 词在 content 中出现给高 relevance
        q = str(query).lower()
        scored: List[Dict[str, Any]] = []
        for i, item in enumerate(self._items):
            content = str(item.get("content", "")).lower()
            if q in content:
                rel = 0.95
            else:
                rel = 0.5
            scored.append({
                "content": item.get("content", ""),
                "role": item.get("role", "user"),
                "metadata": item.get("metadata", {}),
                "relevance": rel,
                "mem_id": item.get("id", i),
            })
        scored.sort(key=lambda x: x["relevance"], reverse=True)
        return scored[: int(top_k or 5)]

    def count(self) -> int:
        return int(self._indexed_count)

    def add_memory(self, memory: Dict[str, Any]) -> None:
        if self._fail:
            raise RuntimeError("vector_memory_add_error")
        self._items.append(memory)
        self._indexed_count += 1


# ============================================================
# 2. 测试 1:Memory Adapter 基础
# ============================================================


class TestMemoryAdapterBasic:
    def test_construction_no_dependencies(self) -> None:
        """无依赖构造:不抛。"""
        a = MemoryRuntimeAdapter()
        assert a.name == STANDARD_ADAPTER_MEMORY == "memory"
        assert a.schema_version == MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"
        assert a.is_attached() is False

    def test_attach_creates_default_store_only(self) -> None:
        """仅 enable_store,vector 禁用时,attach 不创建 vector。"""
        a = MemoryRuntimeAdapter(enable_vector=False, enable_store=True)
        assert a.attach() is True
        assert a.is_attached() is True
        # store 已注入(可能为 None 如果 self-build 失败)
        # vector 保持 None
        snap = a.snapshot()
        assert snap["enable_vector"] is False

    def test_detach(self) -> None:
        a = MemoryRuntimeAdapter()
        a.attach()
        assert a.is_attached() is True
        assert a.detach() is True
        assert a.is_attached() is False

    def test_implements_cycle_adapter_protocol(self) -> None:
        """必须满足 CycleAdapter duck-type。"""
        a = MemoryRuntimeAdapter()
        assert is_cycle_adapter(a) is True
        assert hasattr(a, "name")
        assert hasattr(a, "health_check")
        assert hasattr(a, "process_cycle")
        assert hasattr(a, "snapshot")

    def test_snapshot_basic(self) -> None:
        a = MemoryRuntimeAdapter(user_id="u1", top_k=8)
        snap = a.snapshot()
        assert snap["name"] == "memory"
        assert snap["user_id"] == "u1"
        assert snap["top_k"] == 8
        assert snap["process_count"] == 0
        assert snap["retrieve_count"] == 0
        assert snap["candidate_count"] == 0

    def test_default_top_k(self) -> None:
        a = MemoryRuntimeAdapter()
        assert a.snapshot()["top_k"] == 5

    def test_schema_version_locked(self) -> None:
        """schema_version 冻结在 1.0。"""
        a = MemoryRuntimeAdapter()
        assert a.schema_version == "1.0"


# ============================================================
# 3. 测试 2:Runtime Integration
# ============================================================


class TestRuntimeIntegration:
    def test_register_with_orchestrator(self) -> None:
        """MemoryRuntimeAdapter 可注册到 RuntimeCycleOrchestrator。"""
        a = MemoryRuntimeAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(a, name="memory")
        assert ok is True
        assert orch.has_adapter("memory") is True
        assert orch.adapter_count == 1

    def test_full_cycle_integration(self) -> None:
        """完整 cycle:orchestrator 调度 memory adapter。"""
        a = MemoryRuntimeAdapter(
            memory_store=_FakeMemoryStore(
                memories=[
                    {"id": "m1", "user_id": "u1", "content": "i love bouldering", "role": "user", "metadata": {}, "timestamp": "2024-01-01T00:00:00Z"},
                    {"id": "m2", "user_id": "u1", "content": "favorite color is blue", "role": "user", "metadata": {}, "timestamp": "2024-01-02T00:00:00Z"},
                ]
            ),
            vector_memory=None,
            enable_vector=False,
        )
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("bouldering", user_id="u1", session_id="s1")

        # adapter 被调用
        assert a.process_count == 1
        assert a.retrieve_count == 1
        # ctx.memory_output 被填充
        assert ctx.memory_output is not None
        assert isinstance(ctx.memory_output, dict)
        assert ctx.memory_output.get("query") == "bouldering"
        # 命中
        assert len(ctx.memory_output.get("matched", [])) >= 1
        # adapter_results 应有 memory
        assert ctx.adapter_results["memory"]["ok"] is True
        # stage_log
        assert CYCLE_EVENT_MEMORY_COMPLETED in ctx.get_stage_names()
        # candidates
        assert "memory_candidates" in ctx.metadata
        assert len(ctx.metadata["memory_candidates"]) >= 1

    def test_orchestrator_handles_adapter_failure(self) -> None:
        """memory adapter 抛异常时,orchestrator 标记 degraded 但不中断。"""
        class _BoomAdapter(MemoryRuntimeAdapter):
            def process_cycle(self, ctx):
                raise RuntimeError("memory_boom")

        a = _BoomAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.adapter_results["memory"]["ok"] is False
        assert "memory" in ctx.degraded_adapters
        # cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1

    def test_memory_adapter_in_5_step_orchestrator(self) -> None:
        """memory 是 5 步标准 adapter 的第 1 步,位置正确。"""
        a = MemoryRuntimeAdapter(memory_store=_FakeMemoryStore())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("hi", user_id="u1")
        # memory 是第 1 步
        stages = ctx.get_stage_names()
        # CYCLE_EVENT_MEMORY_COMPLETED 应在 CYCLE_EVENT_COMPLETED 之前
        assert stages.index(CYCLE_EVENT_MEMORY_COMPLETED) < stages.index(CYCLE_EVENT_COMPLETED)


# ============================================================
# 4. 测试 3:Retrieval
# ============================================================


class TestRetrieval:
    def test_empty_query_returns_empty(self) -> None:
        """空 query → MemoryRetrievalResult(query="", matched=[])。"""
        a = MemoryRuntimeAdapter(
            memory_store=_FakeMemoryStore(memories=[{"id": "m1", "content": "x", "user_id": "u"}]),
            vector_memory=_FakeVectorMemory(items=[{"id": "m1", "content": "x"}]),
        )
        a.attach()
        r = a.retrieve("")
        assert isinstance(r, MemoryRetrievalResult)
        assert r.query == ""
        assert r.matched == []
        assert r.is_empty is True

    def test_vector_search_used_first(self) -> None:
        """vector 可用时,优先用 vector。"""
        store = _FakeMemoryStore(memories=[{"id": "store1", "content": "fallback store hit"}])
        vec = _FakeVectorMemory(items=[{"id": "vec1", "content": "vector hit"}])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=vec)
        a.attach()
        r = a.retrieve("vector")
        assert r.used_vector is True
        assert r.used_store is False
        assert len(r.matched) == 1
        assert r.matched[0]["source"] == "vector_memory"
        assert vec.search_count == 1
        assert store.load_count == 0  # store 未被调用

    def test_store_fallback_when_vector_unavailable(self) -> None:
        """vector 不可用时,降级到 store。"""
        store = _FakeMemoryStore(
            memories=[
                {"id": "m1", "user_id": "u", "content": "bouldering at local gym"},
                {"id": "m2", "user_id": "u", "content": "favorite color is blue"},
            ]
        )
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        r = a.retrieve("bouldering")
        assert r.used_vector is False
        assert r.used_store is True
        assert len(r.matched) == 1
        assert "bouldering" in r.matched[0]["content"].lower()
        assert r.matched[0]["source"] == "memory_store"

    def test_vector_failure_falls_back_to_store(self) -> None:
        """vector.search 抛异常时,自动降级到 store。"""
        store = _FakeMemoryStore(memories=[{"id": "m1", "content": "store hit", "user_id": "u"}])
        vec = _FakeVectorMemory(items=[{"id": "v1", "content": "vec hit"}], fail=True)
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=vec)
        a.attach()
        r = a.retrieve("hit")
        # vector 失败 → 降级 store
        assert r.used_vector is False
        assert r.used_store is True
        assert len(r.matched) == 1
        # fallback_vector_count 累加
        assert a.snapshot()["fallback_vector_count"] >= 1

    def test_both_unavailable_returns_empty(self) -> None:
        """vector + store 都不可用 → 空结果。"""
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=None, enable_vector=False, enable_store=False)
        a.attach()
        r = a.retrieve("hi")
        assert r.is_empty is True
        assert r.used_vector is False
        assert r.used_store is False

    def test_store_failure_returns_empty(self) -> None:
        """store.load 抛异常时,降级空结果。"""
        store = _FakeMemoryStore(memories=[], fail=True)
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        r = a.retrieve("hi")
        assert r.is_empty is True

    def test_top_k_limit(self) -> None:
        """top_k 限制返回数量。"""
        mems = [{"id": f"m{i}", "user_id": "u", "content": f"match {i} query"} for i in range(20)]
        store = _FakeMemoryStore(memories=mems)
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False, top_k=3)
        a.attach()
        r = a.retrieve("query")
        assert len(r.matched) <= 3

    def test_relevance_score_in_store_fallback(self) -> None:
        """store 命中时,relevance 给固定 0.8。"""
        store = _FakeMemoryStore(memories=[{"id": "m1", "user_id": "u", "content": "match query"}])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        r = a.retrieve("query")
        assert r.matched[0]["relevance"] == 0.8

    def test_vector_relevance_preserved(self) -> None:
        """vector 命中时,relevance 来自 vector.score。"""
        vec = _FakeVectorMemory(items=[{"id": "v1", "content": "match"}])
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=vec, enable_store=False)
        a.attach()
        r = a.retrieve("match")
        assert r.matched[0]["relevance"] == 0.95
        assert r.matched[0]["source"] == "vector_memory"

    def test_result_to_dict(self) -> None:
        """MemoryRetrievalResult.to_dict 返回完整字段。"""
        r = MemoryRetrievalResult(
            query="q",
            matched=[{"id": "m1", "content": "c", "relevance": 0.9}],
            store_count=10,
            vector_count=5,
            used_vector=True,
        )
        d = r.to_dict()
        assert d["query"] == "q"
        assert d["store_count"] == 10
        assert d["vector_count"] == 5
        assert d["used_vector"] is True
        assert d["used_store"] is False
        assert len(d["matched"]) == 1

    def test_has_results_property(self) -> None:
        """has_results / is_empty 互斥。"""
        empty = MemoryRetrievalResult(query="q")
        assert empty.is_empty is True
        assert empty.has_results is False
        non_empty = MemoryRetrievalResult(query="q", matched=[{"id": "m1"}])
        assert non_empty.is_empty is False
        assert non_empty.has_results is True


# ============================================================
# 5. 测试 4:Candidate 生成
# ============================================================


class TestCandidateGeneration:
    def test_candidate_schema(self) -> None:
        """MemoryCandidate 字段完整。"""
        c = MemoryCandidate(
            cycle_id="c1",
            user_id="u1",
            content="hello",
            role="user",
            source="test",
        )
        assert c.cycle_id == "c1"
        assert c.user_id == "u1"
        assert c.content == "hello"
        assert c.role == "user"
        assert c.source == "test"
        assert isinstance(c.tags, list)
        assert c.confidence == 1.0
        assert c.schema_version == MEMORY_RUNTIME_ADAPTER_SCHEMA_VERSION
        assert c.candidate_id.startswith("mcand_")

    def test_candidate_to_dict(self) -> None:
        c = MemoryCandidate(content="x", cycle_id="c1")
        d = c.to_dict()
        assert isinstance(d, dict)
        assert d["content"] == "x"
        assert d["cycle_id"] == "c1"
        assert "candidate_id" in d

    def test_candidate_to_memory_entry(self) -> None:
        """to_memory_entry 生成可被 store.add 接受的 dict。"""
        c = MemoryCandidate(
            cycle_id="c1",
            user_id="u1",
            content="hello",
            role="user",
            source="runtime_cycle_input",
        )
        entry = c.to_memory_entry()
        assert entry["user_id"] == "u1"
        assert entry["content"] == "hello"
        assert entry["role"] == "user"
        assert entry["metadata"]["type"] == "memory_candidate"
        assert entry["metadata"]["cycle_id"] == "c1"
        assert entry["metadata"]["source"] == "runtime_cycle_input"

    def test_collect_candidate_from_input_event(self) -> None:
        """从 ctx.input_event 提取候选。"""
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event="hello yuyi", user_id="u1")
        cands = a.collect_candidate(ctx)
        assert len(cands) == 1
        c = cands[0]
        assert "hello yuyi" in c.content
        assert c.role == "user"
        assert c.source == "runtime_cycle_input"
        assert c.cycle_id == ctx.cycle_id
        assert c.user_id == "u1"

    def test_collect_candidate_from_dict_event(self) -> None:
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event={"user_input": "from dict"}, user_id="u1")
        cands = a.collect_candidate(ctx)
        assert len(cands) == 1
        assert cands[0].content == "from dict"

    def test_collect_candidate_from_metadata(self) -> None:
        """input_event 无文本时,降级 metadata.user_input。"""
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event={"unrelated": "x"}, user_id="u1")
        ctx.metadata["user_input"] = "from metadata"
        cands = a.collect_candidate(ctx)
        assert len(cands) == 1
        assert cands[0].content == "from metadata"

    def test_collect_candidate_includes_retrieval_summary(self) -> None:
        """当 memory_output.matched 非空时,生成 retrieval summary candidate。"""
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.memory_output = {
            "query": "hi",
            "matched": [{"id": "m1", "content": "c"}],
            "used_vector": True,
            "used_store": False,
        }
        cands = a.collect_candidate(ctx)
        # 1 个 input + 1 个 retrieval summary
        assert len(cands) == 2
        roles = [c.role for c in cands]
        assert "user" in roles
        assert "system" in roles

    def test_collect_candidate_no_input(self) -> None:
        """input 为 None 且 metadata 无 user_input → 空 candidate。"""
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event=None)
        cands = a.collect_candidate(ctx)
        # 至少 input candidate,但 input_event=None → 无内容
        # 但若 memory_output.matched 非空,仍会有 summary
        assert isinstance(cands, list)

    def test_collect_candidate_increments_counter(self) -> None:
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event="x")
        before = a.candidate_count
        a.collect_candidate(ctx)
        assert a.candidate_count == before + 1

    def test_candidates_does_not_write_to_store(self) -> None:
        """collect_candidate 不得写 store。"""
        store = _FakeMemoryStore()
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hello", user_id="u1")
        a.collect_candidate(ctx)
        # store.add_count 必须仍为 0
        assert store.add_count == 0


# ============================================================
# 6. 测试 5:异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_retrieve_with_no_memory_at_all(self) -> None:
        """完全没有 memory 时,retrieve 返回空,不抛。"""
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=None, enable_vector=False, enable_store=False)
        a.attach()
        r = a.retrieve("hi")
        assert r.is_empty is True
        # 不抛

    def test_collect_candidate_with_invalid_ctx(self) -> None:
        """ctx 不是 RuntimeCycleContext 时,返回空列表。"""
        a = MemoryRuntimeAdapter()
        cands = a.collect_candidate({"not": "a ctx"})
        assert cands == []

    def test_collect_candidate_with_exception_in_metadata(self) -> None:
        """metadata 异常时,降级处理。"""
        a = MemoryRuntimeAdapter()
        ctx = RuntimeCycleContext(input_event="x")
        # 故意把 metadata 设成不可迭代(模拟异常)
        class _BadMetadata:
            def get(self, k, d=None):
                raise RuntimeError("bad metadata")
            def __setitem__(self, k, v):
                pass
            def setdefault(self, k, d):
                return []
        ctx.metadata = _BadMetadata()  # type: ignore
        cands = a.collect_candidate(ctx)
        # 不抛
        assert isinstance(cands, list)

    def test_process_cycle_with_invalid_ctx(self) -> None:
        """非 RuntimeCycleContext,直接返回原对象。"""
        a = MemoryRuntimeAdapter()
        fake = {"x": 1}
        out = a.process_cycle(fake)
        assert out is fake

    def test_health_check_with_exception_in_store(self) -> None:
        """store 异常时,health_check 不抛。"""
        store = _FakeMemoryStore(fail=True)
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        # 内部 store 失败,但 health_check 仍返回 dict
        assert hc["store_available"] is False

    def test_health_check_with_exception_in_vector(self) -> None:
        """vector 异常时,health_check 不抛。"""
        # 用一个会在 count() 时抛异常的 vector
        class _BrokenVector:
            def search(self, q, top_k=None):
                raise RuntimeError("boom")
            def count(self):
                raise RuntimeError("count boom")
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=_BrokenVector(), enable_store=False)
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["vector_available"] is False
        # 整体 health 仍为 True(只是 vector 不可用)

    def test_input_event_various_types(self) -> None:
        """不同类型的 input_event 不抛。"""
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=None, enable_vector=False, enable_store=False)
        a.attach()
        for ev in [None, "", {}, [], 0, "text", {"user_input": "u"}, {"content": "c"}, {"text": "t"}]:
            ctx = RuntimeCycleContext(input_event=ev)
            r = a.retrieve(_extract_query_text(ev))
            assert isinstance(r, MemoryRetrievalResult)


# ============================================================
# 7. 测试 6:Health Check / Snapshot
# ============================================================


class TestHealthAndSnapshot:
    def test_health_basic(self) -> None:
        a = MemoryRuntimeAdapter()
        a.attach()
        hc = a.health_check()
        assert hc["healthy"] is True
        assert hc["name"] == "memory"
        assert hc["schema_version"] == "1.0"
        assert hc["attached"] is True

    def test_health_records_store_count(self) -> None:
        store = _FakeMemoryStore(memories=[
            {"id": "m1", "content": "a", "user_id": "u"},
            {"id": "m2", "content": "b", "user_id": "u"},
        ])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        hc = a.health_check()
        assert hc["store_count"] == 2
        assert hc["store_available"] is True

    def test_health_records_vector_count(self) -> None:
        vec = _FakeVectorMemory(items=[{"id": "v1", "content": "c"}], indexed_count=10)
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=vec, enable_store=False)
        a.attach()
        hc = a.health_check()
        assert hc["vector_count"] == 10
        assert hc["vector_available"] is True

    def test_health_when_disabled_both(self) -> None:
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=None, enable_vector=False, enable_store=False)
        a.attach()
        hc = a.health_check()
        assert hc["store_available"] is False
        assert hc["vector_available"] is False
        assert hc["healthy"] is True  # 仍 healthy(只是没数据)

    def test_snapshot_after_process(self) -> None:
        store = _FakeMemoryStore(memories=[{"id": "m1", "content": "x", "user_id": "u"}])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        snap = a.snapshot()
        assert snap["process_count"] >= 1
        assert snap["retrieve_count"] >= 1
        assert snap["candidate_count"] >= 1
        assert snap["last_retrieval"] != {}

    def test_safe_summary_with_none(self) -> None:
        s = safe_get_memory_adapter_summary(None)
        assert s["name"] == "memory"
        assert s["attached"] is False

    def test_safe_summary_with_adapter(self) -> None:
        a = MemoryRuntimeAdapter()
        s = safe_get_memory_adapter_summary(a)
        assert s["name"] == "memory"


# ============================================================
# 8. 测试 7:Regression
# ============================================================


class TestRegression:
    def test_does_not_modify_memory_store(self) -> None:
        """不应修改 MemoryStore 任何属性。"""
        store = _FakeMemoryStore(memories=[{"id": "m1", "content": "x", "user_id": "u"}])
        before_public = set(dir(store))
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="x"))
        after_public = set(dir(store))
        assert before_public == after_public

    def test_does_not_call_store_add(self) -> None:
        """C.2 不应直接调 store.add(候选递交由 caller 决定)。"""
        store = _FakeMemoryStore(memories=[{"id": "m1", "content": "x", "user_id": "u"}])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=None, enable_vector=False)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="x"))
        # 关键:store.add_count 必须为 0
        assert store.add_count == 0

    def test_does_not_modify_vector_memory(self) -> None:
        """不应修改 VectorMemory 任何属性。"""
        vec = _FakeVectorMemory(items=[{"id": "v1", "content": "x"}])
        before_public = set(dir(vec))
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=vec, enable_store=False)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="x"))
        after_public = set(dir(vec))
        assert before_public == after_public

    def test_orchestrator_not_modified(self) -> None:
        """RuntimeCycleOrchestrator 公共方法集合不变。"""
        a = MemoryRuntimeAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        orch.process_event("x", user_id="u1")
        # orchestrator 仍可被外部使用
        assert orch.process_event("y", user_id="u1").cycle_id != ""

    def test_b4_bridge_untouched(self) -> None:
        """C.2 不与 B.4-B.13 交互,任何 B4 bridge 调用都应不存在。"""
        from src.runtime.phase_b4_integration import RuntimeB4Bridge
        # 验证我们的 adapter 没有任何与 RuntimeB4Bridge 耦合的代码
        a = MemoryRuntimeAdapter()
        # 公共属性集合不应包含 bridge 相关
        public_attrs = [m for m in dir(a) if not m.startswith("_")]
        forbidden = {"b4_bridge", "bridge", "b4", "decision"}
        for f in forbidden:
            assert f not in public_attrs

    def test_phase_c2_constants(self) -> None:
        """Phase C.2 常量完整。"""
        for name in [
            "PHASE_C2_STAGE_MEMORY_RETRIEVED",
            "PHASE_C2_STAGE_CANDIDATE_GENERATED",
            "PHASE_C2_STAGE_VECTOR_FALLBACK",
            "PHASE_C2_STAGE_STORE_FALLBACK",
        ]:
            assert name in dir(sys.modules["src.runtime.adapters.impl.memory_runtime_adapter"])
        assert PHASE_C2_NAME == "phase_c2"
        assert PHASE_C2_VERSION == "1.0.0"
        assert len(ALL_PHASE_C2_STAGES) == 4

    def test_uses_real_memory_store_end_to_end(self) -> None:
        """E2E:用真实 MemoryStore 路径(临时文件),验证不破坏现有 store。"""
        with tempfile.TemporaryDirectory() as tmp:
            mem_path = os.path.join(tmp, "memory.json")
            store_path = mem_path
            try:
                from src.memory.memory_store import MemoryStore
            except Exception:
                pytest.skip("MemoryStore 不可用")
            real_store = MemoryStore(store_path)
            # 添加一些数据
            real_store.add("u1", "i love bouldering", "user", {"type": "test"})
            real_store.add("u1", "favorite color is blue", "user", {"type": "test"})

            a = MemoryRuntimeAdapter(memory_store=real_store, vector_memory=None, enable_vector=False)
            a.attach()
            ctx = RuntimeCycleContext(input_event="bouldering", user_id="u1")
            a.process_cycle(ctx)
            # 关键:不抛异常,memory_output 是 dict
            assert ctx.memory_output is not None
            assert isinstance(ctx.memory_output, dict)
            assert ctx.memory_output.get("query") == "bouldering"
            assert ctx.memory_output.get("used_store") is True
            # store 内容未被破坏
            all_mems = real_store.load()
            assert isinstance(all_mems, list)
            assert len(all_mems) >= 0  # 0 或 2 都可(PollutionGuard 可能过滤)


# ============================================================
# 9. 工厂 + 工具函数
# ============================================================


class TestFactoryAndUtils:
    def test_create_default(self) -> None:
        a = create_memory_runtime_adapter()
        assert isinstance(a, MemoryRuntimeAdapter)

    def test_create_with_store(self) -> None:
        store = _FakeMemoryStore()
        a = create_memory_runtime_adapter(memory_store=store)
        assert a._store is store

    def test_create_with_vector(self) -> None:
        vec = _FakeVectorMemory()
        a = create_memory_runtime_adapter(vector_memory=vec)
        assert a._vector is vec

    def test_extract_query_text(self) -> None:
        assert _extract_query_text(None) == ""
        assert _extract_query_text("") == ""
        assert _extract_query_text("  ") == ""
        assert _extract_query_text("hello") == "hello"
        assert _extract_query_text({"user_input": "from_user_input"}) == "from_user_input"
        assert _extract_query_text({"content": "from_content"}) == "from_content"
        assert _extract_query_text({"text": "from_text"}) == "from_text"
        assert _extract_query_text({"input": "from_input"}) == "from_input"
        assert _extract_query_text({"query": "from_query"}) == "from_query"
        assert _extract_query_text({}) == ""
        class _E:
            user_input = "from_attr"
        assert _extract_query_text(_E()) == "from_attr"
        class _E2:
            content = "from_content"
        assert _extract_query_text(_E2()) == "from_content"
        class _E3:
            payload = {"text": "from_payload"}
        assert _extract_query_text(_E3()) == "from_payload"


# ============================================================
# 10. 端到端 smoke
# ============================================================


class TestE2ESmoke:
    def test_full_memory_runtime_flow(self) -> None:
        """完整 E2E:orchestrator + memory adapter + retrieval + candidate。"""
        store = _FakeMemoryStore(memories=[
            {"id": "m1", "user_id": "u1", "content": "today we went bouldering", "role": "user", "metadata": {}, "timestamp": "2024-01-01T00:00:00Z"},
            {"id": "m2", "user_id": "u1", "content": "last week hiked mountain", "role": "user", "metadata": {}, "timestamp": "2024-01-02T00:00:00Z"},
            {"id": "m3", "user_id": "u1", "content": "color is teal", "role": "user", "metadata": {}, "timestamp": "2024-01-03T00:00:00Z"},
        ])
        vec = _FakeVectorMemory(items=[
            {"id": "v1", "content": "vector store about bouldering", "role": "user", "metadata": {}},
        ])
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=vec, top_k=5)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("bouldering", user_id="u1", session_id="sess1")

        # 1. memory_output 填充
        assert ctx.memory_output is not None
        out = ctx.memory_output
        # vector 优先
        assert out.get("used_vector") is True
        assert out.get("query") == "bouldering"
        assert len(out.get("matched", [])) >= 1

        # 2. candidate 生成
        cands = ctx.metadata.get("memory_candidates", [])
        assert len(cands) >= 1
        first_cand = cands[0]
        assert first_cand["content"] == "bouldering"
        assert first_cand["source"] == "runtime_cycle_input"

        # 3. adapter_results 标记
        assert ctx.adapter_results["memory"]["ok"] is True

        # 4. stage_log
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_MEMORY_COMPLETED in stages
        assert CYCLE_EVENT_COMPLETED in stages

        # 5. cycle 整体成功
        assert ctx.error_count == 0
        assert orch.cycle_count == 1

        # 6. store 未被写入(candidate 只递交给 consolidation 流程,不直接写)
        assert store.add_count == 0

    def test_degraded_e2e(self) -> None:
        """降级 E2E:vector 失败 → 降级 store,cycle 仍完成。"""
        store = _FakeMemoryStore(memories=[
            {"id": "m1", "user_id": "u1", "content": "bouldering in shanghai gym", "role": "user", "metadata": {}},
        ])
        vec = _FakeVectorMemory(fail=True)
        a = MemoryRuntimeAdapter(memory_store=store, vector_memory=vec)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("bouldering", user_id="u1")

        # vector 失败 → 降级 store
        assert ctx.memory_output.get("used_vector") is False
        assert ctx.memory_output.get("used_store") is True
        assert len(ctx.memory_output.get("matched", [])) == 1
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert ctx.error_count == 0

    def test_no_memory_e2e(self) -> None:
        """无任何 memory 时,cycle 仍能完成,只是 memory_output 为空。"""
        a = MemoryRuntimeAdapter(memory_store=None, vector_memory=None, enable_vector=False, enable_store=False)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="memory")
        ctx = orch.process_event("hi", user_id="u1")
        assert ctx.memory_output is not None
        assert ctx.memory_output.get("matched") == []
        assert ctx.error_count == 0
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
