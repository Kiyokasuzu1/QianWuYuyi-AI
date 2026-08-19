"""
Phase 4.0-R2.4.1 Gate Tests — InteractionRecorder 最小经历闭环
=================================================================

4 个 Gate（Review 指定）：
  1. Identity: InteractionRecorder.store is MemoryProvider.get_store()
  2. Persist: pipeline.run() → memory.search() 能找到
  3. Event: MemoryCreatedEvent 被发布
  4. Duplicate Guard: 一次 pipeline.run() memory 数量增加 == 1

红线：
  - 不改 MemoryStore 定义
  - 不改 Growth / Personality
  - 只验证"经历进入 Memory"这个最小闭环
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest


# ===========================================================================
# 重置：每次测试前后清 MemoryProvider 单例
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_memory_provider_each_test():
    from src.memory.memory_provider import MemoryProvider

    MemoryProvider.reset_for_testing()
    yield
    MemoryProvider.reset_for_testing()


# ===========================================================================
# Patch helpers（与 R2.3 gate 同源策略）
# ===========================================================================
def _patch_runtime_bridge_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """用假模块替换 src.runtime.runtime_bridge，避免深层 import 链。"""
    def _bad_import(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("Gate test: RuntimeBridge disabled")

    fake_bridge = types.ModuleType("src.runtime.runtime_bridge")
    fake_bridge.get_runtime_bridge = _bad_import  # type: ignore[attr-defined]
    fake_bridge.RuntimeBridge = type("RuntimeBridge", (), {})  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.runtime.runtime_bridge", fake_bridge)


def _patch_vector_memory_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """防止 VectorMemory fallback 触发 chromadb 缺包。"""
    class _NullVectorMemory:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def search(self, query: str, top_k: int = 5) -> list:
            return []

    fake_vector = types.ModuleType("src.memory.vector")
    fake_vector.VectorMemory = _NullVectorMemory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.memory.vector", fake_vector)


def _make_fake_orchestrator(reply: str = "好的，我知道了"):
    """构造一个最小 FakeOrchestrator，只实现 process(user_message) -> str。"""
    class _FakeOrchestrator:
        def __init__(self) -> None:
            self._reply = reply
            self.memory_store = None  # 有字段就行，不会被真正使用

        def process(self, user_message: str) -> str:
            return self._reply

    return _FakeOrchestrator()


def _make_fake_context(user_message: str, reply: str) -> Any:
    """构造一个最小的 fake RuntimeContext（只需要 outputs 属性）。"""
    class _FakeCtx:
        def __init__(self) -> None:
            self.outputs = {
                "snapshot": {
                    "user_message": user_message,
                    "reply": reply,
                }
            }
            self.lifecycle_id = "test-lifecycle"
            self.session_id = "test-session"
            self.state = "success"

    return _FakeCtx()


# ===========================================================================
# Gate 1: Identity — InteractionRecorder.store is MemoryProvider.get_store()
# ===========================================================================
def test_interaction_recorder_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.1 Gate 1: InteractionRecorder 使用的 store 就是 MemoryProvider 权威。"""
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.runtime.interaction_recorder import InteractionRecorder
    from src.memory.memory_provider import MemoryProvider

    recorder = InteractionRecorder()
    authority = MemoryProvider.get_store()

    assert recorder.store is authority, (
        "InteractionRecorder.store 必须就是 MemoryProvider.get_store() 同一实例"
    )


# ===========================================================================
# Gate 2: Persist — record() → memory 能检索到
# ===========================================================================
def test_interaction_recorder_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.1 Gate 2: 一次交互记录后，MemoryStore 能检索到这条经历。"""
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.runtime.interaction_recorder import InteractionRecorder
    from src.memory.memory_provider import MemoryProvider

    recorder = InteractionRecorder()
    ctx = _make_fake_context(
        user_message="我最近开始学画画",
        reply="那太好了！画画是一种很好的表达方式。",
    )

    # 记录前：搜索应该找不到"画画"
    store = MemoryProvider.get_store()
    before = store.load()
    before_count = len(before)

    # 执行记录
    memory_id = recorder.record(ctx)
    assert memory_id is not None, "record() 必须返回 memory_id"

    # 记录后：搜索应该能找到"画画"
    after = store.load()
    after_count = len(after)

    assert after_count == before_count + 1, (
        f"记录后 memory 数量应 +1（before={before_count}, after={after_count}）"
    )

    # 验证内容确实写进去了
    found = False
    for mem in after:
        content = mem.get("content", "")
        if "画画" in content:
            found = True
            break
    assert found, "MemoryStore.load() 中应能找到包含'画画'的记录"


# ===========================================================================
# Gate 3: Event — MemoryCreatedEvent 被发布
# ===========================================================================
def test_interaction_recorder_event_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.1 Gate 3: record() 必须发布 MemoryCreatedEvent 到全局 EventBus。"""
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.runtime.interaction_recorder import InteractionRecorder
    from src.events.bus import get_event_bus
    from src.events.events import EventType

    # 收集所有发布的事件
    published_events: list = []
    bus = get_event_bus()

    def _capture(event: Any) -> None:
        published_events.append(event)

    bus.subscribe_all(_capture)
    # 清掉可能残留的事件（防止其他测试干扰）
    published_events.clear()

    try:
        recorder = InteractionRecorder()
        ctx = _make_fake_context(
            user_message="今天天气真好",
            reply="是啊，适合出去走走。",
        )
        recorder.record(ctx)
    finally:
        bus.unsubscribe_all(_capture)

    # 验证至少有一个 MemoryCreatedEvent
    memory_events = [
        e for e in published_events
        if getattr(e, "event_type", "") == EventType.MEMORY_CREATED
    ]
    assert len(memory_events) >= 1, (
        f"应至少发布 1 个 MemoryCreatedEvent，实际发布了 {len(memory_events)} 个"
    )


# ===========================================================================
# Gate 4: Duplicate Guard — 一次 pipeline.run() memory 数量增加 == 1
# ===========================================================================
def test_interaction_recorder_duplicate_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.1 Gate 4: 一次 pipeline.run() 只增加 1 条 memory（防重复写入）。

    场景：RuntimePipeline 跑完 → InteractionRecorder 写 1 条。
    如果 Orchestrator fallback 也写了 Memory（L866），可能产生重复。
    本测试验证：通过 Pipeline 跑一次，Memory 数量精确 +1。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.runtime.runtime_pipeline import RuntimePipeline
    from src.memory.memory_provider import MemoryProvider

    # 使用 FakeOrchestrator（不会自己写 Memory，只有 InteractionRecorder 写）
    orch = _make_fake_orchestrator(reply="好的")

    # 显式注入 InteractionRecorder（不用延迟创建，确保可控）
    from src.runtime.interaction_recorder import InteractionRecorder
    recorder = InteractionRecorder()

    pipeline = RuntimePipeline(
        orchestrator=orch,
        runtime=None,
        interaction_recorder=recorder,
    )

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    # 跑一次 Pipeline
    pipeline.run({"user_message": "测试重复防护"})

    after_count = len(store.load())
    delta = after_count - before_count

    assert delta == 1, (
        f"一次 pipeline.run() 应只增加 1 条 memory，"
        f"实际增加 {delta} 条（before={before_count}, after={after_count}）。"
        f"可能存在 RuntimePipeline + Orchestrator 双写问题。"
    )
