"""
Phase 4.0-R2.3.1 Gate Tests — Memory Authority Provider
=========================================================

目标（Memory Identity Gate）：
    1. MemoryProvider 是进程单例：连续 N 次 get_store()  返回同一对象
    2. 第一批 3 个模块（MemoryService / MemorySystem / EventExtractor）
       在 RuntimeBridge 不可用时，fallback 到 MemoryProvider.get_store()
       的**同一共享实例**，而不是各自 MemoryStore() 自建。
    3. 生产代码绝对不能再走「自己 new MemoryStore()」的孤岛模式。

红线（R2.3 Review 约束）：
    - 不修改 MemoryStore 定义（class MemoryStore 本体在 memory_store.py 保持不变）
    - 不修改 tests 下其他调用（本文件是新增 gate，不碰现有 memory 测试）
    - 不迁移 Legacy RuntimeCore（runtime_core.py / adapters/memory* 不纳入本批）
    - 不改行为：只改「store 的来源」，业务方法（add / load / search）不影响
"""

from __future__ import annotations

from typing import Any

import pytest


# ===========================================================================
# 重置工具（每次测试前清掉 MemoryProvider 单例，防串扰）
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_memory_provider_each_test():
    from src.memory.memory_provider import MemoryProvider

    MemoryProvider.reset_for_testing()
    yield
    MemoryProvider.reset_for_testing()


# ===========================================================================
# 防御用 helper：模拟 RuntimeBridge 返回 None → 强制触发 fallback 到
# MemoryProvider。
# 注意：get_runtime_bridge() 本身可能成功，但 bridge.get_memory_store()
# 在 bridge 未 initialize 时返回 None；为了稳定地测试 fallback 分支，
# 直接 patch get_runtime_bridge → 抛异常。
# ===========================================================================
def _patch_runtime_bridge_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad_import(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Gate test: RuntimeBridge disabled on purpose")

    # MemoryService / MemorySystem / EventExtractor 三家都是这样写的：
    #   from src.runtime.runtime_bridge import get_runtime_bridge
    #   _bridge = get_runtime_bridge()
    # 所以我们 patch src.runtime.runtime_bridge.get_runtime_bridge 即可。
    monkeypatch.setattr(
        "src.runtime.runtime_bridge.get_runtime_bridge",
        _bad_import,
    )


def _patch_vector_memory_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """MemoryService.__init__ 在 RuntimeBridge 不可用时会 fallback 到
    `VectorMemory()`，这个构造会触发 chromadb/sentence_transformers 依赖导入。
    gate 测试不用真的向量库，返回 Null 对象就行。
    """
    import sys
    import types

    class _NullVectorMemory:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def search(self, query: str, top_k: int = 5) -> list:  # pragma: no cover
            return []

    # 1) 先造一个假模块 src.memory.vector，里面 VectorMemory = _NullVectorMemory
    fake_vector_module = types.ModuleType("src.memory.vector")
    fake_vector_module.VectorMemory = _NullVectorMemory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.memory.vector", fake_vector_module)

    # 2) 如果有人直接 attr 访问（memory_service 里直接 import），也一起覆盖
    monkeypatch.setattr(
        "src.memory.memory_service.VectorMemory",
        _NullVectorMemory,
        raising=False,
    )
    monkeypatch.setattr(
        "src.memory.memory_system.VectorMemory",
        _NullVectorMemory,
        raising=False,
    )


def _patch_event_extractor_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Import 前**注入一个假的 src.response.llm 模块，避免 EventExtractor
    的顶层 `from src.response.llm import LLMClient` 触发 real openai import。

    原理：直接塞一个假模块对象到 sys.modules['src.response.llm']，
    让 import 系统认为它已经加载好，不再 import real llm.py。
    """
    import sys
    import types

    # --- 假模块，里面有假的 LLMClient ---
    fake_llm_module = types.ModuleType("src.response.llm")

    class _DummyLLMClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def generate(self, *_a: Any, **_k: Any) -> str:  # pragma: no cover
            return ""

    fake_llm_module.LLMClient = _DummyLLMClient  # type: ignore[attr-defined]
    # 如果有其他子依赖（本例没有）也可一起 patch。

    monkeypatch.setitem(sys.modules, "src.response.llm", fake_llm_module)


# ===========================================================================
# 用例 1: Provider 本身一定是单例（进程内同一实例）
# ===========================================================================
def test_memory_provider_get_store_returns_same_instance() -> None:
    """R2.3 gate: MemoryProvider 是单例。"""
    from src.memory.memory_provider import MemoryProvider

    store_a = MemoryProvider.get_store()
    store_b = MemoryProvider.get_store()
    store_c = MemoryProvider.get_store()

    assert store_a is store_b, "两次 get_store() 必须是同一个对象"
    assert store_b is store_c, "三次 get_store() 必须是同一个对象"


# ===========================================================================
# 用例 2: MemoryService 在 RuntimeBridge 缺失时 → MemoryProvider 单例
# ===========================================================================
def test_memory_service_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3 gate: MemoryService 不再自建 MemoryStore，fallback 到 Provider。"""
    from src.memory.memory_service import MemoryService
    from src.memory.memory_provider import MemoryProvider

    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    svc = MemoryService()
    expected = MemoryProvider.get_store()

    assert (
        svc.store is expected
    ), "MemoryService.store 必须就是 MemoryProvider.get_store() 同一实例"


# ===========================================================================
# 用例 3: MemorySystem 在 RuntimeBridge 缺失时 → MemoryProvider 单例
# ===========================================================================
def test_memory_system_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3 gate: MemorySystem 不再自建 MemoryStore，fallback 到 Provider。"""
    from src.memory.memory_system import MemorySystem
    from src.memory.memory_provider import MemoryProvider

    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    sys = MemorySystem()
    expected = MemoryProvider.get_store()

    assert (
        sys.store is expected
    ), "MemorySystem.store 必须就是 MemoryProvider.get_store() 同一实例"


# ===========================================================================
# 用例 4: EventExtractor 在 RuntimeBridge 缺失时 → MemoryProvider 单例
# ===========================================================================
def test_event_extractor_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3 gate: EventExtractor 不再自建 MemoryStore，fallback 到 Provider。"""
    # 注意顺序必须：先 patch llm sys.modules → 再 import EventExtractor
    _patch_event_extractor_llm(monkeypatch)
    _patch_runtime_bridge_unavailable(monkeypatch)

    from src.growth.event_extractor import EventExtractor  # noqa: WPS433 (import inside func)
    from src.memory.memory_provider import MemoryProvider

    ex = EventExtractor()
    expected = MemoryProvider.get_store()

    assert (
        ex.store is expected
    ), "EventExtractor.store 必须就是 MemoryProvider.get_store() 同一实例"


# ===========================================================================
# 用例 5 (Memory Identity Gate 核心): 三者共用同一实例
# ===========================================================================
def test_memory_identity_gate_three_modules_share_one_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3 Memory Identity Gate（Review 指定加项）：

    三个模块（MemoryService / MemorySystem / EventExtractor）的 store
    必须 all `is` 同一个对象 —— 这证明"记忆孤岛"在第一批已经被收口。
    """
    # 必须先 patch，再 import EventExtractor（否则 import 级触发 openai）
    _patch_event_extractor_llm(monkeypatch)
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    from src.memory.memory_service import MemoryService
    from src.memory.memory_system import MemorySystem
    from src.growth.event_extractor import EventExtractor  # noqa: WPS433
    from src.memory.memory_provider import MemoryProvider

    svc = MemoryService()
    sys = MemorySystem()
    ex = EventExtractor()
    authority = MemoryProvider.get_store()

    # 三项身份断言（Review 验收标准）
    assert svc.store is authority, "MemoryService.store ≠ Provider 单例"
    assert sys.store is authority, "MemorySystem.store ≠ Provider 单例"
    assert ex.store is authority, "EventExtractor.store ≠ Provider 单例"

    # 交叉互证：三者互相也是同一个
    assert svc.store is sys.store, "Service / System 不是同一个 MemoryStore"
    assert sys.store is ex.store, "System / EventExtractor 不是同一个 MemoryStore"
    assert ex.store is svc.store, "EventExtractor / Service 不是同一个 MemoryStore"


# ===========================================================================
# R2.3.2 新增用例（第二批：Context / Topic / SelfCheck）
# ===========================================================================

# ---------------------------------------------------------------------------
# 用例 7: ContextManager 在 RuntimeBridge 缺失时 → MemoryProvider 单例
# ---------------------------------------------------------------------------
def test_context_manager_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.2 gate: ContextManager 不再自建 MemoryStore，fallback 到 Provider。"""
    from src.context.context_manager import ContextManager
    from src.memory.memory_provider import MemoryProvider

    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    cm = ContextManager()
    actual = cm._get_memory_store()
    expected = MemoryProvider.get_store()

    assert (
        actual is expected
    ), "ContextManager._memory_store 必须就是 MemoryProvider.get_store() 同一实例"


# ---------------------------------------------------------------------------
# 用例 8: EventHistoryMatcher (TopicTracker 模块) → MemoryProvider 单例
# ---------------------------------------------------------------------------
def test_topic_tracker_event_history_matcher_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.2 gate: topic_tracker.py 内的 EventHistoryMatcher fallback 到 Provider。"""
    # EventHistoryMatcher 顶部不直接 import LLM，但 from src.growth.event_extractor import EventExtractor
    # → 需要先 patch llm sys.modules。
    _patch_event_extractor_llm(monkeypatch)
    _patch_runtime_bridge_unavailable(monkeypatch)

    from src.growth.topic_tracker import EventHistoryMatcher  # noqa: WPS433
    from src.memory.memory_provider import MemoryProvider

    matcher = EventHistoryMatcher()
    expected = MemoryProvider.get_store()

    assert (
        matcher.store is expected
    ), "EventHistoryMatcher.store 必须就是 MemoryProvider.get_store() 同一实例"


# ---------------------------------------------------------------------------
# 用例 9: SelfChecker → MemoryProvider 单例
# ---------------------------------------------------------------------------
def test_self_checker_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.2 gate: SelfChecker 不再自建 MemoryStore，fallback 到 Provider。"""
    from src.thinking.self_check import SelfChecker
    from src.memory.memory_provider import MemoryProvider

    _patch_runtime_bridge_unavailable(monkeypatch)

    sc = SelfChecker()
    actual = sc._get_memory_store()
    expected = MemoryProvider.get_store()

    assert (
        actual is expected
    ), "SelfChecker._memory_store 必须就是 MemoryProvider.get_store() 同一实例"


# ===========================================================================
# 用例 10 (R2.3.2 Memory Identity Gate 核心): 6 模块全部同实例
# ===========================================================================
def test_memory_identity_gate_six_modules_share_one_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.2 Memory Identity Gate（6/7 B1 候选点收敛完成）：

    第二批加入后，六个核心模块的 memory store 必须 all `is` 同一个
    MemoryProvider 单例对象：
      MemoryService / MemorySystem / EventExtractor /
      ContextManager / EventHistoryMatcher / SelfChecker
    """
    # ---------- 前置 patch（避免外部依赖炸 import） ----------
    _patch_event_extractor_llm(monkeypatch)
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    # ---------- 延迟 import，确保 patch 先生效 ----------
    from src.memory.memory_service import MemoryService
    from src.memory.memory_system import MemorySystem
    from src.growth.event_extractor import EventExtractor  # noqa: WPS433
    from src.context.context_manager import ContextManager
    from src.growth.topic_tracker import EventHistoryMatcher  # noqa: WPS433
    from src.thinking.self_check import SelfChecker
    from src.memory.memory_provider import MemoryProvider

    # ---------- 构造全部 6 个模块 ----------
    svc = MemoryService()
    mem_sys = MemorySystem()
    evt = EventExtractor()
    cm = ContextManager()
    matcher = EventHistoryMatcher()
    sc = SelfChecker()

    authority = MemoryProvider.get_store()

    # ---------- 延迟 getter 的要先触发加载 ----------
    cm_store = cm._get_memory_store()
    sc_store = sc._get_memory_store()

    # ---------- 1. 所有模块必须 `is` MemoryProvider 单例 ----------
    assert svc.store is authority, "❌ MemoryService.store ≠ MemoryProvider 权威"
    assert mem_sys.store is authority, "❌ MemorySystem.store ≠ MemoryProvider 权威"
    assert evt.store is authority, "❌ EventExtractor.store ≠ MemoryProvider 权威"
    assert cm_store is authority, "❌ ContextManager._memory_store ≠ MemoryProvider 权威"
    assert matcher.store is authority, "❌ EventHistoryMatcher.store ≠ MemoryProvider 权威"
    assert sc_store is authority, "❌ SelfChecker._memory_store ≠ MemoryProvider 权威"

    # ---------- 2. 交叉互证（任意一对都 `is`） ----------
    pair_list = [
        (svc.store, mem_sys.store, "MemoryService <-> MemorySystem"),
        (mem_sys.store, evt.store, "MemorySystem <-> EventExtractor"),
        (evt.store, cm_store, "EventExtractor <-> ContextManager"),
        (cm_store, matcher.store, "ContextManager <-> EventHistoryMatcher"),
        (matcher.store, sc_store, "EventHistoryMatcher <-> SelfChecker"),
        (sc_store, svc.store, "SelfChecker <-> MemoryService"),
        (mem_sys.store, cm_store, "MemorySystem <-> ContextManager"),
        (evt.store, matcher.store, "EventExtractor <-> EventHistoryMatcher"),
    ]
    for a, b, label in pair_list:
        assert a is b, f"❌ 交叉失败：{label} 不是同一个 MemoryStore"


# ===========================================================================
# 用例 6: MemoryProvider.reset_for_testing 确实能清掉单例
# ===========================================================================
def test_memory_provider_reset_for_testing_resets_singleton() -> None:
    """R2.3 gate 防御性测试：reset_for_testing 不会卡死单例。"""
    from src.memory.memory_provider import MemoryProvider

    first = MemoryProvider.get_store()
    MemoryProvider.reset_for_testing()
    second = MemoryProvider.get_store()

    assert first is not second, "reset_for_testing 之后应得到新的单例实例"
