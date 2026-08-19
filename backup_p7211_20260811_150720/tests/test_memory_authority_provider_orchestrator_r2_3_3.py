"""
Phase 4.0-R2.3.3 Gate Tests — Orchestrator Memory Authority 最终收尾
========================================================================

目标（7/7 B1 主路径 Memory Authority 最终闭环）：
    1. Orchestrator 最后一块 fallback 从 MemoryStore() 自建 → MemoryProvider 单例
    2. 最终大闭环断言：
       MemoryService / MemorySystem / EventExtractor / ContextManager /
       EventHistoryMatcher / SelfChecker / Orchestrator
       = 7 个模块的 memory store 全部 `is` 同一个 MemoryProvider.get_store()

红线（Review 约束最后重申）：
    - 未改 MemoryStore 定义（memory_store.py 不变）
    - 未碰 Legacy RuntimeCore（B2 全部保留）
    - Orchestrator 保留 RuntimeBridge > MemoryProvider 优先级（只改 fallback 来源）
    - 不改 process() / personality / growth 任何行为
"""

from __future__ import annotations

from typing import Any

import pytest


# ===========================================================================
# 重置：每次测试前后清 MemoryProvider 单例，防串扰
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_memory_provider_each_test():
    from src.memory.memory_provider import MemoryProvider

    MemoryProvider.reset_for_testing()
    yield
    MemoryProvider.reset_for_testing()


# ===========================================================================
# 测试专用 patch helper
# ===========================================================================
def _patch_runtime_bridge_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """终极方案：直接用假模块替换掉整个 src.runtime.runtime_bridge，
    彻底避免 runtime_bridge.py 真实 import 时触发的子模块导入链
    （例如 src.personality.personality_adapter）。

    原因：RuntimeBridge 模块加载时（import 阶段）就会 import 大量子模块，
    而这些子模块在缺依赖 / 假模块不全时会炸。我们的 gate 测试里
    RuntimeBridge 永远不会被真实使用（get_runtime_bridge 直接抛错触发
    Provider fallback），所以整个换成假模块最干净。
    """
    import sys
    import types

    def _bad_import(*_a: Any, **_k: Any) -> None:  # noqa: WPS430
        raise RuntimeError("Gate test: RuntimeBridge disabled on purpose")

    fake_bridge_module = types.ModuleType("src.runtime.runtime_bridge")
    fake_bridge_module.get_runtime_bridge = _bad_import  # type: ignore[attr-defined]
    fake_bridge_module.RuntimeBridge = type(  # type: ignore[attr-defined]
        "RuntimeBridge",
        (),
        {},
    )
    monkeypatch.setitem(sys.modules, "src.runtime.runtime_bridge", fake_bridge_module)

    # 同时把其他子模块里直接 import 的属性引用也替换（双重保险）
    for attr_path in (
        "src.memory.memory_service.get_runtime_bridge",
        "src.memory.memory_system.get_runtime_bridge",
        "src.context.context_manager.get_runtime_bridge",
        "src.growth.topic_tracker.get_runtime_bridge",
        "src.growth.event_extractor.get_runtime_bridge",
        "src.thinking.self_check.get_runtime_bridge",
        "src.orchestrator.get_runtime_bridge",
    ):
        monkeypatch.setattr(attr_path, _bad_import, raising=False)


def _patch_vector_memory_to_null(monkeypatch: pytest.MonkeyPatch) -> None:
    """防止 VectorMemory fallback 触发 chromadb/sentence_transformers 缺包。"""
    import sys
    import types

    class _NullVectorMemory:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def search(self, query: str, top_k: int = 5) -> list:  # pragma: no cover
            return []

    fake_vector_module = types.ModuleType("src.memory.vector")
    fake_vector_module.VectorMemory = _NullVectorMemory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.memory.vector", fake_vector_module)
    # 子模块覆盖（attr）
    for attr_path in (
        "src.memory.memory_service.VectorMemory",
        "src.memory.memory_system.VectorMemory",
        "src.orchestrator.VectorMemory",
        "src.context.context_manager.VectorMemory",
    ):
        monkeypatch.setattr(attr_path, _NullVectorMemory, raising=False)


def _patch_event_extractor_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """防止 EventExtractor 导入触发 openai 缺包。"""
    import sys
    import types

    fake_llm_module = types.ModuleType("src.response.llm")

    class _DummyLLMClient:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def generate(self, *_a: Any, **_k: Any) -> str:  # pragma: no cover
            return ""

    fake_llm_module.LLMClient = _DummyLLMClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.response.llm", fake_llm_module)


def _patch_orchestrator_heavy_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Orchestrator 顶层 import 大量依赖，gate 测试只关心 memory_store
    初始化路径。把最重、最容易缺包的子模块换成哑对象（sys.modules 假模块），
    避免 Orchestrator import 阶段就因为缺 openai / sentence_transformers /
    personality/relationship 深层依赖而炸。
    """
    import sys
    import types

    # ------------------------------------------------------------------
    # 1) src.engine → ResponseEngine（最重：import openai）
    # ------------------------------------------------------------------
    fake_engine_module = types.ModuleType("src.engine")

    class _DummyResponseEngine:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def generate_response(self, *_a: Any, **_k: Any) -> dict:  # pragma: no cover
            return {"text": "", "reply": "", "final_reply": ""}

    fake_engine_module.ResponseEngine = _DummyResponseEngine  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.engine", fake_engine_module)

    # ------------------------------------------------------------------
    # 2) src.personality / src.identity / src.relationship / src.recovery
    #    只 patch 具体子模块，不碰包本身（包是真实 directory package，
    #    覆盖成 ModuleType 会导致子模块导入报 "xxx is not a package"）。
    # ------------------------------------------------------------------
    _empty_module_names = [
        "src.personality.personality_resolver",
        "src.personality.self_model_context_provider",
        "src.personality.self_model_store",
        "src.identity.user_context",
        "src.identity.user_resolver",
        "src.relationship.relationship_event",
        "src.relationship.relationship_evaluator",
        "src.personality.relationship_state",
        "src.recovery.experience_loader",
        "src.recovery.experience_cache",
        "src.recovery.recovery_marker",
        "src.recovery.experience_extractor",
    ]

    class _EmptyDummy:  # 通用空壳：接受任意参数任意调用返回 None / 空字符串
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def __getattr__(self, name: str) -> Any:  # pragma: no cover - fallback
            def _any_callable(*_a: Any, **_k: Any) -> "_EmptyDummy":
                return _EmptyDummy()

            return _any_callable()

    for mod_name in _empty_module_names:
        mod = types.ModuleType(mod_name)
        # 给每个模块塞一个和最后一段同名的空壳类（这样 from X.Y.Z import ZClass 能拿到东西）
        leaf_cls_name = mod_name.split(".")[-1]
        setattr(mod, leaf_cls_name, _EmptyDummy)
        # 特殊名字处理
        extra_symbols = {
            "src.personality.personality_resolver": ["PersonalityResolver"],
            "src.personality.self_model_store": ["SelfModelStore"],
            "src.personality.self_model_context_provider": ["SelfModelContextProvider"],
            "src.personality.relationship_state": ["RelationshipState"],
            "src.identity.user_context": ["UserContext"],
            "src.identity.user_resolver": ["UserResolver"],
            "src.relationship.relationship_event": ["RelationshipEvent"],
            "src.relationship.relationship_evaluator": ["RelationshipEvaluator"],
            "src.recovery.experience_loader": ["ExperienceLoader"],
            "src.recovery.experience_cache": ["ExperienceCache"],
            "src.recovery.recovery_marker": ["RecoveryMarker"],
            "src.recovery.experience_extractor": ["ExperienceExtractor"],
        }
        for sym in extra_symbols.get(mod_name, []):
            setattr(mod, sym, _EmptyDummy)
        monkeypatch.setitem(sys.modules, mod_name, mod)

    # 注意：不 patch 父包名 src.personality / src.relationship 等，
    # 因为它们是真实 package，不能覆盖成 ModuleType 空壳
    # （否则会有 "is not a package" 错误）。

    # ------------------------------------------------------------------
    # 3) src.runtime.runtime_context → RuntimeContext（简单空壳）
    # ------------------------------------------------------------------
    fake_rc_mod = types.ModuleType("src.runtime.runtime_context")
    fake_rc_mod.RuntimeContext = _EmptyDummy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "src.runtime.runtime_context", fake_rc_mod)


# ===========================================================================
# 用例 1: Orchestrator fallback → MemoryProvider 单例
# ===========================================================================
def test_orchestrator_fallbacks_to_provider_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.3 gate: Orchestrator 不再自建 MemoryStore，fallback 到 Provider 单例。

    顺序：先 patch 全部重依赖 → 再 import Orchestrator → 再实例化 → 断言。
    """
    # ---------- 先 patch 所有重依赖 ----------
    _patch_orchestrator_heavy_dependencies(monkeypatch)
    _patch_event_extractor_llm(monkeypatch)  # growth 相关间接导入
    _patch_runtime_bridge_unavailable(monkeypatch)
    _patch_vector_memory_to_null(monkeypatch)

    # ---------- 现在才 import Orchestrator ----------
    from src.orchestrator import Orchestrator  # noqa: WPS433
    from src.memory.memory_provider import MemoryProvider

    orch = Orchestrator()
    expected = MemoryProvider.get_store()

    assert (
        orch.memory_store is expected
    ), "Orchestrator.memory_store 必须就是 MemoryProvider.get_store() 同一实例"


# ===========================================================================
# 用例 2 (最终 Memory Identity Gate): 7/7 B1 模块全同实例
# ===========================================================================
def test_memory_identity_gate_seven_modules_full_closure_r2_3_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R2.3.3 FINAL GATE —— 7/7 B1 主路径模块全部 `is` 同一个 MemoryStore。

    这是 R2.3 Memory Authority 收敛的最终验收用例。
    7 个模块清单（B1）：
      1. MemoryService          (memory/memory_service.py)
      2. MemorySystem           (memory/memory_system.py)
      3. EventExtractor         (growth/event_extractor.py)
      4. ContextManager         (context/context_manager.py)
      5. EventHistoryMatcher    (growth/topic_tracker.py)
      6. SelfChecker            (thinking/self_check.py)
      7. Orchestrator           (orchestrator.py)   ← R2.3.3 新增最后一块

    验收标准：
        七个模块的 memory store 全部与 MemoryProvider.get_store() is 同一个。
        且任意两两交叉 is 成立（证明没有任何孤岛）。
    """

    # ---------- Patch 环境（按依赖轻重顺序） ----------
    _patch_orchestrator_heavy_dependencies(monkeypatch)  # Orchestrator 最重
    _patch_event_extractor_llm(monkeypatch)               # LLM → openai
    _patch_runtime_bridge_unavailable(monkeypatch)        # 强制触发 Provider fallback
    _patch_vector_memory_to_null(monkeypatch)             # VectorMemory 不真实构造

    # ---------- 延迟导入（确保 patch 全生效） ----------
    from src.memory.memory_service import MemoryService
    from src.memory.memory_system import MemorySystem
    from src.growth.event_extractor import EventExtractor  # noqa: WPS433
    from src.context.context_manager import ContextManager
    from src.growth.topic_tracker import EventHistoryMatcher  # noqa: WPS433
    from src.thinking.self_check import SelfChecker
    from src.orchestrator import Orchestrator  # noqa: WPS433
    from src.memory.memory_provider import MemoryProvider

    # ---------- 构造全部 7 个模块 ----------
    svc = MemoryService()
    mem_sys = MemorySystem()
    evt = EventExtractor()
    cm = ContextManager()
    matcher = EventHistoryMatcher()
    sc = SelfChecker()
    orch = Orchestrator()

    authority = MemoryProvider.get_store()

    # ---------- 延迟 getter 需要先触发加载 ----------
    cm_store = cm._get_memory_store()
    sc_store = sc._get_memory_store()

    # ---------- 1) 每一个都 `is` Authority Provider 单例 ----------
    assertions = [
        (svc.store, authority, "MemoryService.store"),
        (mem_sys.store, authority, "MemorySystem.store"),
        (evt.store, authority, "EventExtractor.store"),
        (cm_store, authority, "ContextManager._memory_store"),
        (matcher.store, authority, "EventHistoryMatcher.store"),
        (sc_store, authority, "SelfChecker._memory_store"),
        (orch.memory_store, authority, "Orchestrator.memory_store"),
    ]
    for actual, expected, label in assertions:
        assert actual is expected, (
            f"❌ FINAL GATE FAIL：{label} 不是 MemoryProvider 权威单例"
        )

    # ---------- 2) 全交叉验证（任意 2 个模块都互相 is） —— 证明 0 孤岛 ----------
    seven_stores = [
        svc.store,
        mem_sys.store,
        evt.store,
        cm_store,
        matcher.store,
        sc_store,
        orch.memory_store,
    ]
    labels = [
        "MemoryService",
        "MemorySystem",
        "EventExtractor",
        "ContextManager",
        "EventHistoryMatcher",
        "SelfChecker",
        "Orchestrator",
    ]
    for i in range(len(seven_stores)):
        for j in range(i + 1, len(seven_stores)):
            a = seven_stores[i]
            b = seven_stores[j]
            lab_a = labels[i]
            lab_b = labels[j]
            assert a is b, (
                f"❌ FINAL GATE FAIL（交叉）：{lab_a} != {lab_b}，"
                "仍存在记忆孤岛"
            )
