"""
Phase 4.0-R2.4.2 Gate Tests — Memory API Contract Fix
========================================================

问题：
    MemoryStore.add() 之前只支持：
        1. add(memory_dict)
        2. add(user_id, content, role, metadata)  # 位置参数
    但 MemorySystem.add() 和 YuyiCore.chat() 用的是：
        add(user_id=..., content=..., role=..., metadata=...)  # kwargs
    结果: len(args)==0 走 else: return None → 静默失败。

修复：
    MemoryStore.add() 第 1 优先级判断：
        if len(args) == 0 and kwargs: memory = dict(kwargs)

4 个 Gate（Review 指定）：
  Gate 1: Kwargs 写入成功（MemorySystem.add → 真实写入 store）
  Gate 2: 旧接口兼容（dict / 位置参数 仍然通过）
  Gate 3: RuntimePipeline 不受影响（R2.4.1 Gate 4 duplicate 继续通过）
  Gate 4: Identity（MemorySystem.store is MemoryProvider.get_store()）
"""

from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime
from typing import Any

import pytest


# ===========================================================================
# 重置工具（每次测试前后清 MemoryProvider 单例）
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_memory_provider_each_test():
    from src.memory.memory_provider import MemoryProvider

    MemoryProvider.reset_for_testing()
    yield
    MemoryProvider.reset_for_testing()


# ===========================================================================
# Patch helpers（与 R2.3 / R2.4.1 gate 同源策略）
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


# ===========================================================================
# 工具：从 store 中按关键字搜索内容（模拟 search，不依赖 VectorMemory）
# ===========================================================================
def _store_contains_content(store: Any, keyword: str) -> bool:
    return any(keyword in (m.get("content") or "") for m in store.load())


# ===========================================================================
# Gate 1: Kwargs 写入成功（MemorySystem.add(kwargs) → 真实写入）
# ===========================================================================
def test_memory_kwargs_write_via_memory_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 1: MemorySystem.add() 用 kwargs 调 store.add()，必须真实写入。

    修复前：MemorySystem.add(user_id=..., role=..., content=..., metadata=...)
          → store.add(user_id=..., role=..., content=..., metadata=...)
          → len(args)==0, kwargs 非空
          → 走 return None → 静默失败。

    修复后：len(args)==0 and kwargs → memory = dict(kwargs) → 正常写入。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_system import MemorySystem
    from src.memory.memory_provider import MemoryProvider

    ms = MemorySystem()
    store = MemoryProvider.get_store()

    before_count = len(store.load())

    # 复现 MemorySystem.add() 的 kwargs 调用契约
    # 注意：PollutionGuard 需要 memory_type 在白名单中，所以在 metadata 里补 memory_type
    ms.add(
        user_id="r2_4_2_test_user",
        role="user",
        content="我正在学习AI绘画，喜欢用扩散模型生成插画",
        metadata={"source": "gate_test", "memory_type": "user_experience"},
    )

    after_count = len(store.load())
    assert after_count == before_count + 1, (
        f"MemorySystem.add(kwargs) 应写入 1 条（before={before_count}, after={after_count}）。"
        f" 若为 0，说明 kwargs 契约仍静默失败。"
    )
    assert _store_contains_content(store, "AI绘画"), (
        "store.load() 中应能找到含'AI绘画'的内容"
    )


# ===========================================================================
# Gate 1b（补充）: 直接用 store.add(kwargs) 也能写 — 覆盖 YuyiCore.chat()
# ===========================================================================
def test_memory_store_direct_kwargs_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 1b: store.add(user_id=..., content=..., role=...) 直接 kwargs 调用也成功。

    覆盖 YuyiCore.chat() L191/L218 的 self.memory.add(user_id=..., role=..., content=..., metadata=...)
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_provider import MemoryProvider

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    # 复现 YuyiCore.chat() 的调用契约（补 PollutionGuard 所需的 memory_type）
    store.add(
        user_id="r2_4_2_yuyi_core",
        role="user",
        content="今天用 Midjourney 跑了一组赛博朋克风格的图",
        metadata={"source": "terminal", "memory_type": "user_experience"},
    )

    after_count = len(store.load())
    assert after_count == before_count + 1, (
        "store.add(kwargs) 应写入 1 条（YuyiCore 调用契约）"
    )
    assert _store_contains_content(store, "赛博朋克"), (
        "store.load() 中应能找到含'赛博朋克'的内容"
    )


# ===========================================================================
# Gate 2: 旧接口兼容（dict / 位置参数 仍然通过）
# ===========================================================================
def test_memory_store_dict_format_backward_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 2a: add(dict) — InteractionRecorder / Orchestrator 主路径，必须保持通过。"""
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_provider import MemoryProvider

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    memory_id = f"mem_r242_dict_{uuid.uuid4().hex[:8]}"
    store.add({
        "id": memory_id,
        "content": "dict格式测试：完成了R2.4.2的API契约修复",
        "timestamp": datetime.now().isoformat(),
        "user_id": "dict_user",
        "role": "user",
        "importance": 0.5,
        "metadata": {"memory_type": "user_experience", "source": "gate_test"},
    })

    after_count = len(store.load())
    assert after_count == before_count + 1, "add(dict) 格式必须保持通过"
    assert store.get_by_id(memory_id) is not None, "add(dict) 后 get_by_id 应能查到"


def test_memory_store_positional_args_backward_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 2b: add(user_id, content, role, metadata) — 旧位置参数格式，必须保持通过。"""
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_provider import MemoryProvider

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    store.add(
        "pos_user_123",                                                           # user_id
        "位置参数格式测试：旧调用方式不能被破坏",                                   # content
        "user",                                                                   # role
        {"source": "legacy_test", "memory_type": "user_experience"},              # metadata（补 PollutionGuard 契约）
    )

    after_count = len(store.load())
    assert after_count == before_count + 1, "add(user_id, content, role, metadata) 必须保持通过"
    assert _store_contains_content(store, "位置参数格式"), (
        "store.load() 中应能找到位置参数写入的内容"
    )

    # 验证字段被正确映射
    last = store.load()[-1]
    assert last.get("user_id") == "pos_user_123"
    assert last.get("role") == "user"


# ===========================================================================
# Gate 2c: args + kwargs 混用 → 返回 None（防止歧义）
# ===========================================================================
def test_memory_store_mixed_args_kwargs_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 2c: add(dict, extra_kwarg=...) 或 add(user_id, content, extra=...) 等混合调用必须被拒绝。

    防止因参数歧义写入脏数据；返回 None 让调用方知道失败（与 PollutionGuard 拒绝语义一致）。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_provider import MemoryProvider

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    # 混用 1: dict + kwargs
    result1 = store.add(
        {"content": "mixed1", "user_id": "u1", "role": "user"},
        extra="should_fail",
    )
    assert result1 is None, "dict + kwargs 混用应返回 None"

    # 混用 2: 位置参数 + kwargs
    result2 = store.add("u2", "hello", "user", {}, extra="should_fail")
    assert result2 is None, "位置参数 + kwargs 混用应返回 None"

    after_count = len(store.load())
    assert after_count == before_count, (
        "混合调用不应写入任何数据（before={}, after={}）".format(before_count, after_count)
    )


# ===========================================================================
# Gate 3: RuntimePipeline 不受影响（R2.4.1 Gate 4 duplicate 继续通过）
# ===========================================================================
def test_runtime_pipeline_duplicate_guard_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 3: R2.4.1 Gate 4 duplicate guard 必须继续通过。

    修复 add() 的 kwargs 契约后，InteractionRecorder 仍然只写 1 条，没有多余写入。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.runtime.runtime_pipeline import RuntimePipeline
    from src.runtime.interaction_recorder import InteractionRecorder
    from src.memory.memory_provider import MemoryProvider

    class _FakeOrchestrator:
        def __init__(self) -> None:
            self.memory_store = None

        def process(self, user_message: str, user_id: str = "") -> str:
            # R-1.2 起 legacy 回退轮的记忆写入由 Orchestrator 负责
            # （RuntimePipeline 跳过 Recorder 以免双写），fake 需忠实模拟。
            if self.memory_store is None:
                from src.memory.memory_provider import MemoryProvider

                self.memory_store = MemoryProvider.get_store()
            self.memory_store.add(
                user_id=user_id or "default_user",
                role="user",
                content=user_message,
                metadata={"source": "gate3_fake", "memory_type": "user_experience"},
            )
            return "好的，我收到了"

    recorder = InteractionRecorder()
    pipeline = RuntimePipeline(
        orchestrator=_FakeOrchestrator(),
        runtime=None,
        interaction_recorder=recorder,
    )

    store = MemoryProvider.get_store()
    before_count = len(store.load())

    pipeline.run({"user_message": "R2.4.2 合同修复完成，重复防护验证"})

    after_count = len(store.load())
    delta = after_count - before_count
    assert delta == 1, (
        f"R2.4.2 修复后，一次 pipeline.run() 仍应只+1条memory，"
        f"实际+{delta}条（before={before_count}, after={after_count}）"
    )


# ===========================================================================
# Gate 4: Identity — MemorySystem.store is MemoryProvider.get_store()
# ===========================================================================
def test_memory_system_store_is_provider_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 4: MemorySystem 使用的 store 必须就是 MemoryProvider 权威单例。

    R2.3 已确认此不变量；R2.4.2 只修 add() 契约，不应破坏身份一致性。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_system import MemorySystem
    from src.memory.memory_provider import MemoryProvider

    ms = MemorySystem()
    authority = MemoryProvider.get_store()

    assert ms.store is authority, (
        "MemorySystem.store 必须就是 MemoryProvider.get_store() 同一实例（R2.3 Identity）"
    )


# ===========================================================================
# Gate 4b: 端到端契约验证 — MemorySystem.add(kwargs) 写入后，Provider 视角可见
# ===========================================================================
def test_memory_system_kwargs_writes_visible_via_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """R2.4.2 Gate 4b: MemorySystem.add(kwargs) 写入后，MemoryProvider.get_store() 视角可见。

    验证：R2.3 Identity + R2.4.2 Contract 同时成立 → 写入路径真正打通。
    """
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_system import MemorySystem
    from src.memory.memory_provider import MemoryProvider

    ms = MemorySystem()
    store = MemoryProvider.get_store()

    unique_keyword = f"R242E2E_{uuid.uuid4().hex[:6]}"
    ms.add(
        user_id="e2e_user",
        role="user",
        content=f"端到端验证 {unique_keyword} 记忆契约打通",
        metadata={"source": "e2e_gate", "memory_type": "user_fact"},
    )

    # 通过 Provider 单例 load()，应该能看到
    provider_view = store.load()
    found = any(unique_keyword in (m.get("content") or "") for m in provider_view)
    assert found, (
        "MemorySystem.add(kwargs) 写入的数据，通过 MemoryProvider.get_store().load() 必须可见。"
        " 若找不到，说明 Contract 修复后还有写入路径问题。"
    )
