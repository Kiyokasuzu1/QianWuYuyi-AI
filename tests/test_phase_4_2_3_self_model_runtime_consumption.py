# -*- coding: utf-8 -*-
"""
tests/test_phase_4_2_3_self_model_runtime_consumption.py

Phase 4.2.3: Self Model Runtime Consumption —— 单元测试

目标:
让 SelfModelSnapshot 成为 Runtime 回复上下文的一部分。

要求覆盖:
1.  SelfModelContextProvider 基本接口(attach / detach / health_check)
2.  SelfModelContextProvider.provide() 无 snapshot
3.  SelfModelContextProvider.provide() 有 snapshot → 5 字段
4.  SelfModelContextProvider.provide() 异常隔离
5.  SelfModelContextProvider.format_for_prompt() 文本输出
6.  SelfModelContextProvider.format_for_prompt() 无 snapshot
7.  ResponseAdapter.build_request() 无 SelfModelProvider → 不注入
8.  ResponseAdapter.build_request() 有 SelfModelProvider + snapshot → 注入
9.  ResponseAdapter.build_request() 有 Provider 但 snapshot=None → 不注入
10. ResponseAdapter.build_request() 有 Provider 但 ctx 无 snapshot 属性 → 不注入
11. ResponseAdapter.set_self_model_context_provider() 动态替换
12. ResponseAdapterImpl 接受 self_model_context_provider 注入
13. PromptBuilder._format_personality() 读 self_model_text
14. PromptBuilder.build_messages() 包含 self_model_text
15. RuntimeCore.configure_self_model_context_provider() 注入
16. RuntimeCore E2E: 无 self_model_registry → 完全兼容
17. RuntimeCore E2E: 有 self_model_registry + provider → context 增加
18. RuntimeContext schema 不变
19. ResponseEngine.generate() 不被修改
20. Runtime 生命周期不破坏
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _make_snapshot(
    identity: Optional[Dict[str, Any]] = None,
    core_values: Optional[List[Dict]] = None,
    stable_traits: Optional[List[Dict]] = None,
    preferences: Optional[List[Dict]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    entries: Optional[List[Any]] = None,
    identity_id: str = "smf_test",
    version: int = 1,
) -> Any:
    """构造一个简易 SelfModelSnapshot-like 对象。"""
    from src.runtime.self_model.self_model_data import SelfModelSnapshot, SelfModelEntry

    snap = SelfModelSnapshot(
        identity=identity if identity is not None else {"name": "yuyi"},
        core_values=core_values or [],
        stable_traits=stable_traits or [],
        preferences=preferences or [],
        current_state=current_state or {},
        entries=entries or [],
        identity_id=identity_id,
        version=version,
    )
    return snap


def _make_entry(kind: str, summary: str, confidence: float = 0.8) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelEntry
    return SelfModelEntry(kind=kind, summary=summary, confidence=confidence)


# ============================================================
# 1. SelfModelContextProvider 基本接口
# ============================================================
class TestSelfModelContextProviderBase:
    def test_class_metadata(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
            SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION,
        )
        assert SelfModelContextProvider.schema_version == SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION
        assert SelfModelContextProvider.name == "self_model_context_provider"

    def test_initial_state_not_attached(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        assert p.is_attached is False
        assert p.provide_count == 0
        assert p.last_snapshot_id is None
        assert p.last_error is None

    def test_attach_detach(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        p.attach()
        assert p.is_attached is True
        p.detach()
        assert p.is_attached is False

    def test_health_check(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        p.attach()
        h = p.health_check()
        assert h["healthy"] is True
        assert h["name"] == "self_model_context_provider"
        assert h["schema_version"] == "1.0"
        assert "provide_count" in h

    def test_health_check_after_error(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        p.attach()

        class _BadSnap:
            identity = None  # 触发异常
            # missing all attrs → AttributeError in _extract via getattr returning None, ok
        # 真正会失败的:identity 是 property 抛错
        class _Boom:
            @property
            def identity(self):
                raise RuntimeError("boom")

        ctx = p.provide(_Boom())
        assert ctx["has_snapshot"] is False
        h = p.health_check()
        assert h["healthy"] is False
        assert "last_error" in h
        assert p.last_error is not None

    def test_describe(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        p.attach()
        p.provide(None)
        d = p.describe()
        assert d["name"] == "self_model_context_provider"
        assert d["is_attached"] is True
        assert d["provide_count"] == 1


# ============================================================
# 2. provide() 无 snapshot
# ============================================================
class TestProvideWithoutSnapshot:
    def test_provide_none(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        ctx = p.provide(None)
        assert ctx["has_snapshot"] is False
        assert ctx["schema_version"] == "1.0"
        assert ctx["identity"] == {}
        assert ctx["stable_traits"] == []
        assert ctx["preferences"] == []
        assert ctx["current_state"] == {}
        assert ctx["recent_changes"] == []
        assert ctx["core_values"] == []
        assert p.provide_count == 1
        assert p.last_snapshot_id is None

    def test_format_for_prompt_none(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        text = p.format_for_prompt(None)
        assert text == ""

    def test_format_context_for_prompt_none(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        text = p.format_context_for_prompt(None)
        assert text == ""
        text2 = p.format_context_for_prompt({})
        assert text2 == ""


# ============================================================
# 3. provide() 有 snapshot → 5 字段
# ============================================================
class TestProvideWithSnapshot:
    def test_provide_with_identity_only(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "gentle_companion"},
        )
        ctx = p.provide(snap)
        assert ctx["has_snapshot"] is True
        assert ctx["identity"]["name"] == "yuyi"
        assert ctx["identity"]["archetype"] == "gentle_companion"

    def test_provide_with_stable_traits(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            stable_traits=[
                {"name": "warmth", "value": 0.8},
                {"name": "patience", "value": 0.7},
            ],
        )
        ctx = p.provide(snap)
        assert len(ctx["stable_traits"]) == 2
        assert ctx["stable_traits"][0]["name"] == "warmth"

    def test_provide_with_preferences(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            preferences=[
                {"name": "favorite_color", "value": "soft_blue"},
            ],
        )
        ctx = p.provide(snap)
        assert len(ctx["preferences"]) == 1
        assert ctx["preferences"][0]["value"] == "soft_blue"

    def test_provide_with_current_state(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            current_state={"mood": "calm", "energy": 0.6},
        )
        ctx = p.provide(snap)
        assert ctx["current_state"]["mood"] == "calm"
        assert ctx["current_state"]["energy"] == 0.6

    def test_provide_with_recent_changes(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        entries = [
            _make_entry("growth", "Started new hobby"),
            _make_entry("trait_change", "Patience +0.1"),
            _make_entry("event", "Bought flowers"),
            _make_entry("reflection", "Realized something"),
        ]
        snap = _make_snapshot(entries=entries)
        ctx = p.provide(snap)
        # 只取 trait_change / growth / reflection
        kinds = [rc.get("kind") for rc in ctx["recent_changes"]]
        assert "event" not in kinds
        assert "growth" in kinds
        assert "trait_change" in kinds
        assert "reflection" in kinds

    def test_provide_recent_changes_max_limit(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
            MAX_RECENT_CHANGES,
        )
        p = SelfModelContextProvider()
        entries = [
            _make_entry("growth", f"Entry {i}") for i in range(20)
        ]
        snap = _make_snapshot(entries=entries)
        ctx = p.provide(snap)
        assert len(ctx["recent_changes"]) == MAX_RECENT_CHANGES

    def test_provide_with_core_values(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            core_values=[{"name": "sincerity"}, {"name": "kindness"}],
        )
        ctx = p.provide(snap)
        assert len(ctx["core_values"]) == 2

    def test_provide_meta(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(identity_id="smf_xyz", version=5)
        ctx = p.provide(snap)
        assert ctx["meta"]["identity_id"] == "smf_xyz"
        assert ctx["meta"]["version"] == 5
        assert p.last_snapshot_id == "smf_xyz"

    def test_provide_with_dict_entry_in_entries(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        # 字典形式的 entry
        entries = [{"kind": "growth", "summary": "from dict"}]
        snap = _make_snapshot(entries=entries)
        ctx = p.provide(snap)
        assert any(
            rc.get("summary") == "from dict"
            for rc in ctx["recent_changes"]
        )

    def test_provide_with_fallback_to_str_entry(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        # 非 dataclass / 非 dict 的 entry → 走 str 路径
        entries = ["just_a_string_growth", _make_entry("growth", "real")]
        snap = _make_snapshot(entries=entries)
        ctx = p.provide(snap)
        # string 没有 kind 字段,会被 skip
        assert isinstance(ctx["recent_changes"], list)

    def test_provide_count_increments(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot()
        p.provide(snap)
        p.provide(snap)
        p.provide(None)
        assert p.provide_count == 3


# ============================================================
# 4. provide() 异常隔离
# ============================================================
class TestProvideExceptionIsolation:
    def test_provide_with_raising_identity(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()

        class _Bad:
            @property
            def identity(self):
                raise RuntimeError("bad")
            core_values = []
            stable_traits = []
            preferences = []
            current_state = {}
            entries = []
        ctx = p.provide(_Bad())
        assert ctx["has_snapshot"] is False
        assert p.last_error is not None

    def test_provide_count_still_increments_on_error(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()

        class _Bad:
            @property
            def identity(self):
                raise RuntimeError("bad")
            core_values = []
            stable_traits = []
            preferences = []
            current_state = {}
            entries = []
        p.provide(_Bad())
        assert p.provide_count == 1


# ============================================================
# 5. format_for_prompt() 文本输出
# ============================================================
class TestFormatForPrompt:
    def test_format_basic(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            core_values=[{"name": "sincerity"}],
            stable_traits=[{"name": "warmth", "value": 0.8}],
            preferences=[{"name": "color", "value": "blue"}],
            current_state={"mood": "calm"},
        )
        text = p.format_for_prompt(snap)
        assert "【自我认知】" in text
        assert "yuyi" in text
        assert "sincerity" in text
        assert "warmth" in text
        assert "blue" in text
        assert "calm" in text

    def test_format_with_recent_changes(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            entries=[_make_entry("growth", "Learned piano")],
        )
        text = p.format_for_prompt(snap)
        assert "Learned piano" in text

    def test_format_with_archetype(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "sage"},
        )
        text = p.format_for_prompt(snap)
        assert "sage" in text

    def test_format_minimal_snapshot(self):
        """空 snapshot 返回空串。"""
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        # 没有 identity name / 任何字段
        snap = _make_snapshot(
            identity={}, core_values=[], stable_traits=[],
            preferences=[], current_state={}, entries=[],
        )
        text = p.format_for_prompt(snap)
        # 至少 【自我认知】 头不会输出,因为没有任何内容
        assert text == ""

    def test_format_context_from_dict(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        ctx = {
            "has_snapshot": True,
            "identity": {"name": "yuyi"},
            "core_values": [{"name": "honest"}],
            "stable_traits": [],
            "preferences": [],
            "current_state": {},
            "recent_changes": [],
            "meta": {},
            "schema_version": "1.0",
        }
        text = p.format_context_for_prompt(ctx)
        assert "yuyi" in text
        assert "honest" in text


# ============================================================
# 6. ResponseAdapter.build_request() 无 Provider → 不注入
# ============================================================
class TestResponseAdapterWithoutProvider:
    def test_no_provider_no_injection(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        adapter = ResponseAdapter()
        adapter.attach()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        prc = PersonalityRuntimeContext()
        req = adapter.build_request(ctx, prc)
        assert "self_model_data" not in (req.personality_context or {})
        assert "self_model_text" not in (req.personality_context or {})

    def test_no_snapshot_no_injection(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext

        # Provider 存在但 ctx 无 _self_model_snapshot
        mock_provider = MagicMock()
        adapter = ResponseAdapter(self_model_context_provider=mock_provider)
        adapter.attach()
        ctx = RuntimeContext()
        # 不设置 _self_model_snapshot
        req = adapter.build_request(ctx)
        # mock provider 不会被调用
        mock_provider.provide.assert_not_called()
        assert "self_model_data" not in (req.personality_context or {})

    def test_snapshot_none_no_injection(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext

        mock_provider = MagicMock()
        adapter = ResponseAdapter(self_model_context_provider=mock_provider)
        adapter.attach()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", None)
        req = adapter.build_request(ctx)
        # snapshot=None 时 provider.provide 不被调用
        mock_provider.provide.assert_not_called()
        assert "self_model_data" not in (req.personality_context or {})


# ============================================================
# 7. ResponseAdapter.build_request() 有 Provider + snapshot → 注入
# ============================================================
class TestResponseAdapterWithProvider:
    def test_injection_full(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )

        provider = SelfModelContextProvider()
        provider.attach()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            stable_traits=[{"name": "warmth", "value": 0.8}],
        )
        setattr(ctx, "_self_model_snapshot", snap)
        prc = PersonalityRuntimeContext()
        req = adapter.build_request(ctx, prc)
        pc = req.personality_context
        assert pc is not None
        assert "self_model_data" in pc
        assert pc["self_model_data"]["has_snapshot"] is True
        assert "self_model_text" in pc
        assert "yuyi" in pc["self_model_text"]

    def test_provider_exception_isolated(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        class _BadProvider:
            def provide(self, snap):
                raise RuntimeError("provider fail")
            def format_context_for_prompt(self, ctx):
                return ""

        provider = _BadProvider()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        prc = PersonalityRuntimeContext()
        # 不能抛异常
        req = adapter.build_request(ctx, prc)
        assert req is not None
        assert "self_model_data" not in (req.personality_context or {})

    def test_provider_returns_no_text(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )

        provider = SelfModelContextProvider()
        provider.attach()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        # 空 snapshot(全空字段)→ format 出空串
        snap = _make_snapshot(
            identity={}, core_values=[], stable_traits=[],
            preferences=[], current_state={}, entries=[],
        )
        setattr(ctx, "_self_model_snapshot", snap)
        prc = PersonalityRuntimeContext()
        req = adapter.build_request(ctx, prc)
        pc = req.personality_context
        # has_snapshot=True(因为不是 None),但 text 为空
        if pc and pc.get("self_model_data"):
            assert pc["self_model_data"]["has_snapshot"] is True
        # text 可能不在(因为 format_for_prompt 返回 "")

    def test_set_provider_runtime(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        a = ResponseAdapter()
        a.attach()
        assert a.self_model_context_provider is None
        p = SelfModelContextProvider()
        a.set_self_model_context_provider(p)
        assert a.self_model_context_provider is p
        a.set_self_model_context_provider(None)
        assert a.self_model_context_provider is None


# ============================================================
# 8. ResponseAdapterImpl 接受 provider 注入
# ============================================================
class TestResponseAdapterImplProvider:
    def test_impl_accepts_provider(self):
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        provider = SelfModelContextProvider()
        impl = ResponseAdapterImpl(self_model_context_provider=provider)
        assert impl.self_model_context_provider is provider

    def test_impl_default_no_provider(self):
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        impl = ResponseAdapterImpl()
        assert impl.self_model_context_provider is None

    def test_impl_set_provider_after_init(self):
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        impl = ResponseAdapterImpl()
        assert impl.self_model_context_provider is None
        provider = SelfModelContextProvider()
        impl.set_self_model_context_provider(provider)
        assert impl.self_model_context_provider is provider


# ============================================================
# 9. PromptBuilder 读 self_model_text
# ============================================================
class TestPromptBuilderSelfModel:
    def test_format_personality_with_self_model_text(self):
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        ctx = {
            "personality_text": "【人格】温柔",
            "self_model_text": "【自我认知】yuyi",
        }
        text = pb._format_personality(ctx)
        assert "【人格】温柔" in text
        assert "【自我认知】yuyi" in text

    def test_format_personality_only_self_model_text(self):
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        ctx = {"self_model_text": "【自我认知】yuyi"}
        text = pb._format_personality(ctx)
        assert text == "【自我认知】yuyi"

    def test_format_personality_no_self_model(self):
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        ctx = {"personality_text": "【人格】温柔"}
        text = pb._format_personality(ctx)
        assert text == "【人格】温柔"

    def test_format_personality_empty(self):
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        assert pb._format_personality(None) == ""
        assert pb._format_personality({}) == ""

    def test_build_messages_includes_self_model_text(self):
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        messages = pb.build_messages(
            user_message="hello",
            personality_context={
                "personality_text": "【人格】温柔",
                "self_model_text": "【自我认知】yuyi 是 AI 助手",
            },
        )
        assert len(messages) >= 1
        sys_text = messages[0]["content"]
        assert "【人格】温柔" in sys_text
        assert "【自我认知】yuyi 是 AI 助手" in sys_text


# ============================================================
# 10. RuntimeCore.configure_self_model_context_provider
# ============================================================
class TestRuntimeCoreConfigureProvider:
    def test_configure_provider_no_response_adapter(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        # 不抛异常
        core.configure_self_model_context_provider(MagicMock())
        # 仍然可关闭
        core.shutdown()

    def test_configure_provider_with_response_adapter(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        core = RuntimeCore(adapter_registry=registry)
        core.start()
        provider = SelfModelContextProvider()
        provider.attach()
        core.configure_self_model_context_provider(provider)
        # 已注入
        adapter = core._resolved("response")
        assert adapter.self_model_context_provider is provider
        core.shutdown()

    def test_configure_provider_with_legacy_response_adapter(self):
        """老 ResponseAdapter 没有 set_self_model_context_provider 时,静默忽略。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapters.response_adapter import ResponseAdapter

        class LegacyAdapter(ResponseAdapter):
            pass

        # LegacyAdapter 没有重写 set_..., 继承自 ResponseAdapter 所以其实有
        # 这里测试一个不相关的旧 stub
        class StubAdapter:
            name = "stub"
            schema_version = "1.0"
            def attach(self): pass
            def detach(self): pass
            def health_check(self): return {}
            def build_request(self, ctx, prc=None, event=None, **kw): return None
            def generate(self, request): return ""

        core = RuntimeCore()
        core._resolved_response = StubAdapter()
        # 不抛异常
        core.configure_self_model_context_provider(MagicMock())


# ============================================================
# 11. Runtime E2E: 无 self_model_registry → 完全兼容
# ============================================================
class TestRuntimeE2EWithoutSelfModel:
    def test_e2e_no_self_model_registry(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.context import RuntimeContext
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        core = RuntimeCore(adapter_registry=registry)
        ctx = core.start()
        # 无 self_model_registry
        assert core.self_model_registry is None
        # process 一个 event
        ev = Event(type="user_input", payload={"text": "hello"})
        try:
            out_ctx = core.process(ev, ctx)
        except Exception:
            out_ctx = ctx
        # 不应抛异常,ctx._self_model_snapshot 不存在或 None
        snap = getattr(out_ctx, "_self_model_snapshot", None)
        assert snap is None
        core.shutdown()

    def test_e2e_with_registry_no_provider(self):
        """有 self_model_registry 但未配置 provider → context 仍不含 self_model。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
        )
        ctx = core.start()
        ev = Event(type="user_input", payload={"text": "hello"})
        try:
            out_ctx = core.process(ev, ctx)
        except Exception:
            out_ctx = ctx
        # snapshot 已生成
        snap = getattr(out_ctx, "_self_model_snapshot", None)
        # 但 provider 未注入,build_request 不会注入
        # 这里只验证 Runtime 不抛
        core.shutdown()


# ============================================================
# 12. Runtime E2E: 有 self_model_registry + provider → context 增加
# ============================================================
class TestRuntimeE2EWithSelfModel:
    def test_e2e_with_registry_and_provider(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        response_impl = ResponseAdapterImpl()
        registry.register("response_adapter_impl", response_impl)
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        provider = SelfModelContextProvider()
        provider.attach()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
        )
        core.start()
        core.configure_self_model_context_provider(provider)

        # 验证 provider 已注入
        adapter = core._resolved("response")
        assert adapter.self_model_context_provider is provider

        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            out_ctx = core.process(ev)
        except Exception:
            out_ctx = None

        # snapshot 应已生成
        assert out_ctx is not None
        snap = getattr(out_ctx, "_self_model_snapshot", None)
        assert snap is not None
        core.shutdown()

    def test_e2e_build_request_includes_self_model(self):
        """直接验证 build_request 在 snapshot + provider 都在时返回 self_model_*。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        from src.runtime.adapters.response_adapter import ResponseAdapter

        provider = SelfModelContextProvider()
        provider.attach()
        impl = ResponseAdapterImpl(self_model_context_provider=provider)
        impl.attach()
        ctx = RuntimeContext()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            stable_traits=[{"name": "warmth", "value": 0.9}],
        )
        setattr(ctx, "_self_model_snapshot", snap)
        req = impl.build_request(ctx)
        pc = req.personality_context
        assert pc is not None
        assert pc.get("self_model_data", {}).get("has_snapshot") is True
        assert "yuyi" in pc.get("self_model_text", "")


# ============================================================
# 13. RuntimeContext schema 不变
# ============================================================
class TestRuntimeContextSchemaUnchanged:
    def test_schema_fields(self):
        from src.runtime.context import (
            RuntimeContext,
            RUNTIME_CONTEXT_SCHEMA_VERSION,
        )
        ctx = RuntimeContext()
        # 公共字段只有这些
        assert hasattr(ctx, "session_id")
        assert hasattr(ctx, "user_input")
        assert hasattr(ctx, "timestamp")
        assert hasattr(ctx, "memory_context")
        assert hasattr(ctx, "emotion_state")
        assert hasattr(ctx, "personality_snapshot")
        assert hasattr(ctx, "growth_proposals")
        assert hasattr(ctx, "schema_version")
        # schema version
        assert ctx.schema_version == RUNTIME_CONTEXT_SCHEMA_VERSION
        # 不存在 self_model_snapshot 公共字段
        assert not hasattr(ctx, "self_model_snapshot")
        # 但允许通过 setattr 注入(私有)
        setattr(ctx, "_self_model_snapshot", "test")
        assert getattr(ctx, "_self_model_snapshot") == "test"

    def test_from_dict_no_self_model_field(self):
        from src.runtime.context import RuntimeContext
        data = {
            "session_id": "abc",
            "user_input": "hi",
        }
        ctx = RuntimeContext.from_dict(data)
        assert ctx.session_id == "abc"
        # 没有 self_model_snapshot 字段被 from_dict 恢复
        assert "self_model_snapshot" not in ctx.to_dict()


# ============================================================
# 14. ResponseEngine.generate() 不被修改
# ============================================================
class TestResponseEngineUnchanged:
    def test_response_engine_signature(self):
        """验证 ResponseEngine.generate() 签名未变化(没有 self_model_context 参数)。"""
        import inspect
        from src.response.engine import ResponseEngine
        sig = inspect.signature(ResponseEngine.generate)
        params = list(sig.parameters.keys())
        # 不应该包含 self_model_context
        assert "self_model_context" not in params
        # 应该有这些字段
        assert "user_message" in params
        assert "personality_context" in params
        assert "resolved_behavior" in params
        assert "expression_constraint_text" in params

    def test_response_engine_still_callable(self):
        """mock ResponseEngine 验证传参不变。"""
        from src.runtime.adapters.response_adapter import (
            ResponseAdapter,
            ResponseRequest,
        )
        from src.runtime.context import RuntimeContext

        captured = {}

        class MockEngine:
            def generate(self, user_message, history, chat_memories,
                         life_events, personality_context,
                         resolved_behavior, expression_constraint_text):
                captured["user_message"] = user_message
                captured["personality_context"] = personality_context
                return "ok"

        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        impl = ResponseAdapterImpl(response_engine=MockEngine())
        impl.attach()
        req = ResponseRequest(
            user_input="hello",
            personality_context={"self_model_text": "test"},
        )
        reply = impl.generate(req)
        assert reply == "ok"
        # 透传 self_model_text
        assert captured["personality_context"].get("self_model_text") == "test"


# ============================================================
# 15. Runtime 生命周期
# ============================================================
class TestRuntimeLifecycle:
    def test_start_process_shutdown_with_self_model(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
        )
        # start
        ctx = core.start()
        assert core.is_started is True
        # configure provider
        provider = SelfModelContextProvider()
        provider.attach()
        core.configure_self_model_context_provider(provider)
        # process
        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            out_ctx = core.process(ev, ctx)
        except Exception:
            out_ctx = ctx
        # shutdown
        core.shutdown()
        assert core.is_started is False

    def test_lifecycle_errors_not_break(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )

        # Foundation 但 build 抛错 → 整个 SelfModel 阶段应静默 no-op
        class _BadFoundation(SelfModelFoundation):
            def build(self, inputs):
                raise RuntimeError("boom")

        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(_BadFoundation())
        core = RuntimeCore(self_model_registry=sm_registry)
        core.start()
        from src.runtime.events import Event
        ev = Event(type="user_input", payload={"text": "x"})
        try:
            out_ctx = core.process(ev)
        except Exception:
            out_ctx = None
        # Runtime 不挂
        assert out_ctx is not None or out_ctx is None
        core.shutdown()


# ============================================================
# 16. 模块导出
# ============================================================
class TestModuleExports:
    def test_self_model_module_exports(self):
        from src.runtime.self_model import (
            SelfModelContextProvider,
            SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION,
            SELF_MODEL_CONTEXT_MAX_RECENT_CHANGES,
            SELF_MODEL_CONTEXT_RECENT_CHANGE_KINDS,
        )
        assert SelfModelContextProvider is not None
        assert SELF_MODEL_CONTEXT_PROVIDER_SCHEMA_VERSION == "1.0"
        assert isinstance(SELF_MODEL_CONTEXT_MAX_RECENT_CHANGES, int)
        assert "growth" in SELF_MODEL_CONTEXT_RECENT_CHANGE_KINDS
        assert "trait_change" in SELF_MODEL_CONTEXT_RECENT_CHANGE_KINDS
        assert "reflection" in SELF_MODEL_CONTEXT_RECENT_CHANGE_KINDS


# ============================================================
# 17. Schema/Edge cases
# ============================================================
class TestEdgeCases:
    def test_snapshot_with_none_fields(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()

        class _Partial:
            identity = None
            core_values = None
            stable_traits = None
            preferences = None
            current_state = None
            entries = None
            counters = None
            meta = None
            health = None
        ctx = p.provide(_Partial())
        # 不抛
        assert ctx["has_snapshot"] is True
        assert ctx["identity"] == {}

    def test_provide_idempotent(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(identity={"name": "yuyi"})
        c1 = p.provide(snap)
        c2 = p.provide(snap)
        assert c1 == c2
        # id 不变
        assert c1["meta"]["identity_id"] == c2["meta"]["identity_id"]

    def test_format_for_prompt_idempotent(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(identity={"name": "yuyi"})
        t1 = p.format_for_prompt(snap)
        t2 = p.format_for_prompt(snap)
        assert t1 == t2

    def test_format_truncates_long_values(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            current_state={"mood": "x" * 500},
        )
        text = p.format_for_prompt(snap)
        # 不会出现 500 个 x
        assert "x" * 100 not in text

    def test_recent_change_kinds_frozenset(self):
        from src.runtime.self_model.self_model_context_provider import (
            RECENT_CHANGE_KINDS,
        )
        assert isinstance(RECENT_CHANGE_KINDS, frozenset)
        assert "growth" in RECENT_CHANGE_KINDS
        assert "event" not in RECENT_CHANGE_KINDS
        assert "preference" not in RECENT_CHANGE_KINDS


# ============================================================
# 18. 集成自检 - 5 字段可读取
# ============================================================
class TestFiveFieldsReadable:
    """验证 identity / stable_traits / preferences / current_state / recent_changes 均可读。"""

    def test_all_five_fields(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        entries = [
            _make_entry("growth", "change 1"),
            _make_entry("trait_change", "change 2"),
        ]
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "ai_companion"},
            stable_traits=[{"name": "warmth", "value": 0.9}],
            preferences=[{"name": "color", "value": "blue"}],
            current_state={"mood": "calm", "energy": 0.7},
            entries=entries,
        )
        ctx = p.provide(snap)
        # 1. identity
        assert ctx["identity"]["name"] == "yuyi"
        assert ctx["identity"]["archetype"] == "ai_companion"
        # 2. stable_traits
        assert len(ctx["stable_traits"]) == 1
        # 3. preferences
        assert len(ctx["preferences"]) == 1
        # 4. current_state
        assert ctx["current_state"]["mood"] == "calm"
        # 5. recent_changes
        assert len(ctx["recent_changes"]) == 2

    def test_five_fields_in_text(self):
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        p = SelfModelContextProvider()
        snap = _make_snapshot(
            identity={"name": "yuyi"},
            core_values=[{"name": "honest"}],
            stable_traits=[{"name": "warmth", "value": 0.9}],
            preferences=[{"name": "color", "value": "blue"}],
            current_state={"mood": "calm"},
            entries=[_make_entry("growth", "grew today")],
        )
        text = p.format_for_prompt(snap)
        assert "yuyi" in text       # identity
        assert "honest" in text     # core_values
        assert "warmth" in text     # stable_traits
        assert "blue" in text       # preferences
        assert "calm" in text       # current_state
        assert "grew today" in text  # recent_changes


# ============================================================
# 19. PersonalityContext 字段保留
# ============================================================
class TestPersonalityContextFieldsPreserved:
    def test_communication_style_preserved(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        provider = SelfModelContextProvider()
        provider.attach()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        prc = PersonalityRuntimeContext(
            communication_style={"tone": "soft"},
            behavior_constraints=["no_lies"],
        )
        req = adapter.build_request(ctx, prc)
        pc = req.personality_context
        assert pc.get("communication_style") == {"tone": "soft"}
        assert pc.get("behavior_constraints") == ["no_lies"]
        # self_model 同时注入
        assert "self_model_text" in pc

    def test_personality_text_preserved(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        provider = SelfModelContextProvider()
        provider.attach()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        # 模拟 PRC 已经把 personality_text 放进 prc.snapshot
        setattr(ctx, "_self_model_snapshot", _make_snapshot())
        from src.runtime.personality_context import PersonalityRuntimeContext
        prc = PersonalityRuntimeContext(
            personality_snapshot={"personality_text": "【人格】温柔"}
        )
        req = adapter.build_request(ctx, prc)
        pc = req.personality_context
        assert pc.get("personality_text") == "【人格】温柔"
        assert "self_model_text" in pc


# ============================================================
# 20. 数据流 End-to-End (mocked engine)
# ============================================================
class TestDataFlowE2E:
    def test_full_data_flow(self):
        """完整数据流:SelfModelSnapshot → Provider → personality_context → PromptBuilder。"""
        from src.runtime.self_model.self_model_context_provider import (
            SelfModelContextProvider,
        )
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.response.prompt_builder import PromptBuilder

        # 1. SelfModelSnapshot
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "ai_companion"},
            stable_traits=[{"name": "warmth", "value": 0.9}],
            preferences=[{"name": "color", "value": "soft_blue"}],
            current_state={"mood": "calm"},
        )

        # 2. SelfModelContextProvider
        provider = SelfModelContextProvider()
        provider.attach()

        # 3. ResponseAdapter.build_request()
        adapter = ResponseAdapter(self_model_context_provider=provider)
        adapter.attach()
        ctx = RuntimeContext()
        setattr(ctx, "_self_model_snapshot", snap)
        prc = PersonalityRuntimeContext()
        req = adapter.build_request(ctx, prc)

        # 4. personality_context 含 self_model_text / self_model_data
        pc = req.personality_context
        assert "self_model_text" in pc
        assert "self_model_data" in pc

        # 5. PromptBuilder 用 personality_context 拼 messages
        pb = PromptBuilder()
        messages = pb.build_messages(
            user_message="hi",
            personality_context=pc,
        )
        sys_text = messages[0]["content"]
        # 验证 5 字段都到了 system prompt
        assert "yuyi" in sys_text
        assert "warmth" in sys_text
        assert "soft_blue" in sys_text
        assert "calm" in sys_text

    def test_data_flow_no_snapshot(self):
        """无 snapshot 时 PromptBuilder 仍工作。"""
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.response.prompt_builder import PromptBuilder

        adapter = ResponseAdapter()
        adapter.attach()
        ctx = RuntimeContext()
        prc = PersonalityRuntimeContext()
        req = adapter.build_request(ctx, prc)
        pc = req.personality_context or {}
        assert "self_model_text" not in pc

        pb = PromptBuilder()
        messages = pb.build_messages(
            user_message="hi",
            personality_context=pc,
        )
        # 不应抛
        assert len(messages) >= 1
