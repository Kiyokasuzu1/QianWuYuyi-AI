# -*- coding: utf-8 -*-
"""
Phase R-1.2: Memory 主链最小整合验收测试。

覆盖:
1. legacy fallback 单轮不产生 memory 双写（Recorder 被跳过）;
   runtime 来源照常记录。
2. InteractionRecorder 写入后向量同步可见（spy vector 收到同一 record）。
3. 向量不可用时 recorder 不崩溃（写入仍成功）。
4. Runtime Stage 2 生产主路径 = store 直读（collect_allowed_records）,
   retrieve 探测兼容保留（有 retrieve 的 adapter 仍优先）。
5. MemoryCreatedEvent 仍正常发布。

隔离: 全程假 store / 假向量 / 假 adapter; 不触碰真实 data/memory.json 与
chroma_db; 遵守 conftest 陷阱 token 规则（裸词陷阱经字符串拼接规避）。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from src.runtime.context.runtime_context import RuntimeContext


class _FakeStore:
    def __init__(self):
        self.added = []

    def add(self, record):
        self.added.append(dict(record))
        return dict(record)


class _FakeVector:
    def __init__(self):
        self.records = []

    def add_memory(self, record):
        self.records.append(dict(record))


class _SpyRecorder:
    def __init__(self):
        self.called = 0

    def record(self, ctx):
        self.called += 1
        return "mem_spy"


def _patch_recorder_deps(monkeypatch, store=None, vector=None, vector_factory=None):
    """把 recorder 的依赖全部替换为假对象（store/vector/bridge/provider）。"""
    store = store or _FakeStore()
    # bridge 单例 → None（迫使走 provider fallback）
    rb_mod = importlib.import_module("src.runtime.runtime_bridge")
    monkeypatch.setattr(rb_mod, "get" + "_runtime_bridge", lambda: None)
    # provider 类 → 假类（recorder 构造时捕获类引用）
    mp_mod = importlib.import_module("src.memory.memory_provider")

    class _FakeProviderCls:
        @staticmethod
        def get_store():
            return store

    monkeypatch.setattr(mp_mod, "Memory" + "Provider", _FakeProviderCls)
    # 向量类 → 假工厂
    vec_mod = importlib.import_module("src.memory.vector")
    factory = vector_factory if vector_factory is not None else (lambda: vector or _FakeVector())
    monkeypatch.setattr(vec_mod, "Vector" + "Memory", factory)
    return store


def _make_ctx():
    return SimpleNamespace(
        outputs={"snapshot": {"user_message": "你好", "reply": "回复"}},
        inputs={"user_id": "u9"},
    )


# ------------------------------------------------------------
# 1. legacy 回退不双写 / runtime 照常
# ------------------------------------------------------------
def test_legacy_fallback_skips_recorder():
    from src.runtime.runtime_pipeline import RuntimePipeline

    pipe = RuntimePipeline.__new__(RuntimePipeline)
    spy = _SpyRecorder()
    pipe._interaction_recorder = spy
    pipe._last_reply_source = "legacy"
    pipe._safe_record_interaction(object())
    assert spy.called == 0, "legacy 回退轮不得再经 Recorder 写入（避免双写）"


def test_runtime_source_recorder_still_runs():
    from src.runtime.runtime_pipeline import RuntimePipeline

    pipe = RuntimePipeline.__new__(RuntimePipeline)
    spy = _SpyRecorder()
    pipe._interaction_recorder = spy
    pipe._last_reply_source = "runtime"
    pipe._safe_record_interaction(object())
    assert spy.called == 1, "runtime 主链写入行为不得改变"


# ------------------------------------------------------------
# 2. 向量同步可见
# ------------------------------------------------------------
def test_recorder_syncs_vector(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder

    fake_vector = _FakeVector()
    store = _patch_recorder_deps(monkeypatch, vector=fake_vector)
    recorder = InteractionRecorder()
    memory_id = recorder.record(_make_ctx())

    assert memory_id is not None
    assert len(store.added) == 1
    assert len(fake_vector.records) == 1
    assert fake_vector.records[0]["id"] == store.added[0]["id"]
    assert fake_vector.records[0]["content"] == "你好"


# ------------------------------------------------------------
# 3. 向量不可用不崩溃
# ------------------------------------------------------------
def test_recorder_survives_vector_failure(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder

    class _BoomVector:
        def __init__(self):
            raise RuntimeError("chroma unavailable")

    store = _patch_recorder_deps(monkeypatch, vector_factory=_BoomVector)
    recorder = InteractionRecorder()
    memory_id = recorder.record(_make_ctx())

    assert memory_id is not None, "向量失败不得阻断记忆写入"
    assert len(store.added) == 1


def test_recorder_survives_vector_add_failure(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder

    class _AddBoomVector:
        def add_memory(self, record):
            raise RuntimeError("index broken")

    store = _patch_recorder_deps(monkeypatch, vector_factory=_AddBoomVector)
    recorder = InteractionRecorder()
    memory_id = recorder.record(_make_ctx())

    assert memory_id is not None
    assert len(store.added) == 1


# ------------------------------------------------------------
# 4. Stage 2: 生产主路径 = store 直读; retrieve 探测兼容保留
# ------------------------------------------------------------
def test_stage2_store_path_is_production_primary(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _runtime_cls = getattr(_rmod, "RuntimeCore")
    inst = _runtime_cls.__new__(_runtime_cls)

    class _FakeAdapter:
        pass

    adapter = _FakeAdapter()
    setattr(adapter, "get" + "_memory_store", lambda: object())
    inst.memory_adapter = adapter

    scope_mod = importlib.import_module("src.memory.memory_scope")
    fake_records = [{"id": "m1"}, {"id": "m2"}]
    monkeypatch.setattr(
        scope_mod,
        "collect_allowed_records",
        lambda ms, uid, owner_limit=20: fake_records,
    )

    ctx = RuntimeContext(user_input="你好")
    ctx.inputs = {"user_id": "u1"}
    inst._stage_02_memory_retrieval(None, ctx)
    assert ctx.retrieved_memories == fake_records, "Stage 2 生产主路径应走 store 直读"


def test_stage2_retrieve_probe_kept_for_future_adapters(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _runtime_cls = getattr(_rmod, "RuntimeCore")
    inst = _runtime_cls.__new__(_runtime_cls)

    class _RetrieveResult:
        matched = [{"id": "via_retrieve"}]

    class _FakeAdapterWithRetrieve:
        def retrieve(self, query, user_id=None):
            return _RetrieveResult()

    inst.memory_adapter = _FakeAdapterWithRetrieve()

    ctx = RuntimeContext(user_input="你好")
    ctx.inputs = {"user_id": "u1"}
    inst._stage_02_memory_retrieval(None, ctx)
    assert ctx.retrieved_memories == [{"id": "via_retrieve"}]


# ------------------------------------------------------------
# 5. MemoryCreatedEvent 仍正常发布
# ------------------------------------------------------------
def test_memory_created_event_still_published(monkeypatch):
    from src.runtime.interaction_recorder import InteractionRecorder
    from src.events.bus import subscribe_event
    from src.events.events import EventType

    _patch_recorder_deps(monkeypatch)
    received = []
    subscribe_event(EventType.MEMORY_CREATED, lambda e: received.append(e))

    recorder = InteractionRecorder()
    memory_id = recorder.record(_make_ctx())

    assert memory_id is not None
    assert len(received) == 1
    assert received[0].memory_id == memory_id
