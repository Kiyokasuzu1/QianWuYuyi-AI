# -*- coding: utf-8 -*-
"""
tests/test_phase_3_8_5_orchestrator_runtime.py

Phase 3.8.5: Orchestrator Runtime Migration 测试

覆盖:
1. Orchestrator 注入 OrchestratorRuntimeBridge(使用 RuntimeCore)
2. Orchestrator.process() / _generate_reply() 优先走 RuntimeCore.process()
3. Event 正确创建(type=user_input, source=user, payload 包含 text/content)
4. RuntimeCore 异常 / 无 final_reply → fallback 到 legacy_generate()
5. final_reply 正确返回(非空字符串)
6. legacy_generate() / legacy_response() 路径不破坏
7. Runtime 不直接依赖 Orchestrator(单向依赖:Orchestrator → RuntimeCore)
8. Runtime 状态统计(runtime_call_count / legacy_call_count)
9. 集成:Orchestrator.process() 端到端返回 reply
"""
import sys
import os
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _build_orchestrator_with_mock_bridge(
    *,
    runtime_reply: str = "from runtime",
    raise_on_runtime: bool = False,
    return_empty: bool = False,
    legacy_reply: str = "legacy reply",
    raise_on_legacy: bool = False,
):
    """构造一个 Orchestrator,但只 mock 桥接和 engine.generate。

    Args:
        runtime_reply: 桥接返回的回复(当 raise_on_runtime=False 且 return_empty=False)
        raise_on_runtime: 桥接是否抛异常
        return_empty: 桥接返回空字符串
        legacy_reply: engine.generate 返回的回复(当 raise_on_legacy=False)
        raise_on_legacy: engine.generate 是否抛异常

    Returns:
        (orchestrator, runtime_mock, engine_mock)
    """
    from src.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    # 必填字段
    orch.config = {}
    orch.target_user_id = "user_test_001"
    orch.history = []
    orch.current_personality = None
    # Runtime 状态
    orch._runtime_call_count = 0
    orch._legacy_call_count = 0
    orch._last_runtime_error = None
    orch._last_legacy_reason = None
    orch._last_reply_source = None
    orch._runtime_status = "enabled"

    # Mock engine
    engine_mock = MagicMock()
    if raise_on_legacy:
        engine_mock.generate.side_effect = RuntimeError("legacy boom")
    else:
        engine_mock.generate.return_value = legacy_reply
    orch.engine = engine_mock

    # Mock 桥接
    bridge_mock = MagicMock()
    if raise_on_runtime:
        bridge_mock.handle_message.side_effect = RuntimeError("runtime boom")
        bridge_mock.last_runtime_error = "RuntimeError('runtime boom')"
        bridge_mock.last_legacy_reason = None
    elif return_empty:
        bridge_mock.handle_message.return_value = ""
        bridge_mock.last_runtime_error = "empty_final_reply"
        bridge_mock.last_legacy_reason = None
    else:
        bridge_mock.handle_message.return_value = runtime_reply
        bridge_mock.last_runtime_error = None
        bridge_mock.last_legacy_reason = None
    orch._runtime_bridge = bridge_mock

    return orch, bridge_mock, engine_mock


def _build_event(type_="user_input", text="hi"):
    from src.runtime.events import Event
    return Event(
        type=type_,
        source="user",
        payload={"text": text, "content": text},
    )


def _build_runtime_core_mock(final_reply: str = "from runtime"):
    """构造一个 Mock RuntimeCore,使其 process(event) 返回带 _final_reply 的 ctx。"""
    core = MagicMock()
    ctx = MagicMock()
    setattr(ctx, "_final_reply", final_reply)
    core.process.return_value = ctx
    return core


# ============================================================
# T1: Orchestrator 注入 RuntimeCore
# ============================================================
class TestOrchestratorRuntimeInjection:
    def test_orchestrator_has_runtime_bridge_attribute(self):
        """Orchestrator 必须有 _runtime_bridge 属性,作为 RuntimeCore 入口。"""
        from src.orchestrator import Orchestrator
        assert hasattr(Orchestrator, "_generate_reply")
        assert hasattr(Orchestrator, "legacy_generate")
        assert hasattr(Orchestrator, "legacy_response")
        assert hasattr(Orchestrator, "is_runtime_enabled")
        assert hasattr(Orchestrator, "runtime_stats")

    def test_orchestrator_runtime_bridge_class_exists(self):
        """OrchestratorRuntimeBridge 类必须存在并暴露 handle_message。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
        bridge = OrchestratorRuntimeBridge.__new__(OrchestratorRuntimeBridge)
        assert hasattr(bridge, "handle_message")
        assert hasattr(bridge, "_try_runtime")
        assert hasattr(bridge, "_legacy_generate")
        assert hasattr(bridge, "set_runtime_core")
        assert hasattr(bridge, "set_guard_chain")

    def test_orchestrator_initializes_bridge_when_runtime_available(self):
        """RuntimeCore 可用时,Orchestrator 注入 OrchestratorRuntimeBridge。"""
        from src.orchestrator import Orchestrator
        # 设置 DEEPSEEK_API_KEY 避免 OpenAI 客户端初始化失败
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-for-init")

        runtime_mock = _build_runtime_core_mock("hello from runtime")
        # 通过直接 patch RuntimeBridge.get_runtime_core 返回 mock
        with patch(
            "src.runtime.runtime_bridge.get_runtime_bridge"
        ) as _:
            with patch(
                "src.orchestrator_runtime_bridge.OrchestratorRuntimeBridge"
            ) as bridge_cls:
                bridge_cls.return_value = MagicMock()
                orch = Orchestrator()
                assert orch._runtime_bridge is not None

    def test_orchestrator_initializes_bridge_failure_isolated(self):
        """桥接初始化失败不应阻塞 Orchestrator(允许 legacy 模式运行)。"""
        from src.orchestrator import Orchestrator
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-for-init")

        with patch(
            "src.orchestrator_runtime_bridge.OrchestratorRuntimeBridge",
            side_effect=RuntimeError("init failed"),
        ):
            orch = Orchestrator()
            # 桥接初始化失败 → 仍然构造成功
            assert orch._runtime_bridge is None
            # 状态被记录为 failed
            assert orch.get_runtime_status() == "failed"
            assert orch.get_last_runtime_error() is not None


# ============================================================
# T2: _generate_reply 调度
# ============================================================
class TestGenerateReplyDispatch:
    def test_runtime_success_path(self):
        """Runtime 成功 → 直接返回 runtime_reply,不走 engine.generate。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge(
            runtime_reply="runtime reply",
            legacy_reply="legacy reply",
        )
        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "runtime reply"
        assert orch._runtime_call_count == 1
        assert orch._legacy_call_count == 0
        assert orch._last_reply_source == "runtime"
        # engine.generate 不应被调用
        engine_mock.generate.assert_not_called()

    def test_runtime_failure_falls_back_to_legacy(self):
        """Runtime 抛异常 → fallback 到 legacy_generate。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge(
            raise_on_runtime=True,
            legacy_reply="legacy reply",
        )
        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "legacy reply"
        assert orch._runtime_call_count == 0
        assert orch._legacy_call_count == 1
        assert orch._last_reply_source == "legacy"
        # engine.generate 应被调用
        engine_mock.generate.assert_called_once()

    def test_runtime_empty_reply_falls_back(self):
        """Runtime 返回空 → fallback 到 legacy。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge(
            return_empty=True,
            legacy_reply="legacy reply",
        )
        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "legacy reply"
        assert orch._last_reply_source == "legacy"
        engine_mock.generate.assert_called_once()

    def test_no_runtime_bridge_falls_back_to_legacy(self):
        """_runtime_bridge 为 None → 直接走 legacy。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge()
        orch._runtime_bridge = None
        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "legacy reply"
        assert orch._legacy_call_count == 1
        engine_mock.generate.assert_called_once()

    def test_legacy_generate_failure_returns_fallback_text(self):
        """legacy_generate 自身失败 → 返回兜底文本,不抛异常。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge()
        engine_mock.generate.side_effect = RuntimeError("LLM down")
        reply = orch.legacy_generate(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
        )
        assert isinstance(reply, str)
        assert "抱歉" in reply
        # 计数 +1
        assert orch._legacy_call_count == 1

    def test_legacy_generate_empty_reply_returns_fallback_text(self):
        """legacy_generate 返回空字符串 → 返回兜底文本。"""
        orch, bridge_mock, engine_mock = _build_orchestrator_with_mock_bridge()
        engine_mock.generate.return_value = "   "
        reply = orch.legacy_generate(
            user_message="hi",
            chat_memories=[],
        )
        assert isinstance(reply, str)
        assert "抱歉" in reply


# ============================================================
# T3: Event 创建正确性
# ============================================================
class TestEventCreation:
    def test_bridge_creates_user_input_event(self):
        """OrchestratorRuntimeBridge 创建 type=user_input, source=user 的 Event。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
        from src.runtime.events import Event

        runtime = _build_runtime_core_mock("x")
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)

        # 替换 process 内部使用的 Event,捕捉构造参数
        original_event = Event
        captured = {}

        def _capture_event(*args, **kwargs):
            captured.update(kwargs)
            return original_event(**kwargs)

        with patch("src.runtime.events.Event", side_effect=_capture_event):
            bridge.handle_message("hello yuyi")
        # 验证 Event 被用 user_input 类型创建
        assert captured.get("type") == "user_input"
        assert captured.get("source") == "user"
        # payload 包含 text 和 content
        assert captured["payload"].get("text") == "hello yuyi"
        assert captured["payload"].get("content") == "hello yuyi"

    def test_event_payload_passes_user_message(self):
        """user_message 正确传入 event.payload。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        runtime = _build_runtime_core_mock("x")
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)

        # mock runtime.process 后,检查传入的 event
        captured_event = {}
        original_process = runtime.process

        def _capture(evt):
            captured_event.update({
                "type": evt.type,
                "source": evt.source,
                "payload": dict(evt.payload),
            })
            return original_process(evt)

        runtime.process = _capture
        bridge.handle_message("test message 123")
        assert captured_event["type"] == "user_input"
        assert captured_event["source"] == "user"
        assert captured_event["payload"].get("text") == "test message 123"

    def test_runtime_receives_event_with_correct_id(self):
        """Runtime 收到的 event 有 id。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        runtime = _build_runtime_core_mock("x")
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)
        bridge.handle_message("hi")
        # runtime.process 至少被调用 1 次
        assert runtime.process.call_count == 1
        evt = runtime.process.call_args[0][0]
        assert hasattr(evt, "id")
        assert evt.id is not None
        assert evt.id.startswith("evt_")


# ============================================================
# T4: final_reply 返回
# ============================================================
class TestFinalReply:
    def test_final_reply_returned_when_valid(self):
        """Runtime ctx._final_reply 非空 → 直接返回。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        runtime = _build_runtime_core_mock("我在这里陪你")
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)
        reply = bridge.handle_message("hi")
        assert reply == "我在这里陪你"
        assert bridge.last_reply_source == "runtime"
        assert bridge.runtime_call_count == 1

    def test_empty_final_reply_falls_back(self):
        """ctx._final_reply 为 None / 空 → fallback legacy。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        runtime = MagicMock()
        ctx = MagicMock()
        setattr(ctx, "_final_reply", None)
        runtime.process.return_value = ctx

        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)
        bridge._legacy_generate = MagicMock(return_value="legacy reply")

        reply = bridge.handle_message("hi")
        assert reply == "legacy reply"
        assert bridge.legacy_call_count == 1
        assert bridge.last_runtime_error == "empty_final_reply"

    def test_runtime_exception_falls_back(self):
        """Runtime.process 抛异常 → fallback legacy,记录错误。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        runtime = MagicMock()
        runtime.process.side_effect = RuntimeError("boom")

        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=runtime)
        bridge._legacy_generate = MagicMock(return_value="legacy reply")

        reply = bridge.handle_message("hi")
        assert reply == "legacy reply"
        assert bridge.legacy_call_count == 1
        assert "boom" in (bridge.last_runtime_error or "")

    def test_no_runtime_uses_legacy(self):
        """runtime_core = None → 直接走 legacy,reason=no_runtime_core。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock(), runtime_core=None)
        bridge._legacy_generate = MagicMock(return_value="legacy reply")

        reply = bridge.handle_message("hi")
        assert reply == "legacy reply"
        assert bridge.legacy_call_count == 1
        assert bridge.last_legacy_reason == "no_runtime_core"

    def test_no_runtime_no_orchestrator_returns_fallback(self):
        """runtime_core=None 且 orchestrator.engine=None → 返回兜底文本。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        orch = MagicMock()
        orch.engine = None
        bridge = OrchestratorRuntimeBridge(orchestrator=orch, runtime_core=None)
        reply = bridge.handle_message("hi")
        assert isinstance(reply, str)
        assert "抱歉" in reply


# ============================================================
# T5: Legacy 路径完整性
# ============================================================
class TestLegacyPath:
    def test_legacy_generate_signature_unchanged(self):
        """legacy_generate 签名应保持兼容(允许 user_message 必填,其他可选)。"""
        import inspect
        from src.orchestrator import Orchestrator
        sig = inspect.signature(Orchestrator.legacy_generate)
        params = list(sig.parameters.keys())
        assert "user_message" in params
        # 其他参数可选
        for opt in ["chat_memories", "personality_context", "emotion_ctx",
                    "relationship_ctx", "prompt_blocks", "conversation_id"]:
            assert opt in params, f"missing param: {opt}"

    def test_legacy_response_basic(self):
        """legacy_response 简单调用应不抛异常。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge(
            legacy_reply="legacy response"
        )
        # mock memory_store.load
        orch.memory_store = MagicMock()
        orch.memory_store.load.return_value = []
        reply = orch.legacy_response("hi")
        assert reply == "legacy response"

    def test_legacy_response_failure_isolated(self):
        """legacy_response 失败时返回兜底文本。"""
        orch, _, engine_mock = _build_orchestrator_with_mock_bridge()
        orch.memory_store = MagicMock()
        orch.memory_store.load.side_effect = RuntimeError("memory broken")
        engine_mock.generate.side_effect = RuntimeError("engine broken")
        reply = orch.legacy_response("hi")
        assert isinstance(reply, str)
        assert "抱歉" in reply

    def test_orchestrator_engine_attribute_still_required(self):
        """legacy_generate 仍使用 self.engine,确保 engine 属性存在。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge()
        assert orch.engine is not None
        assert hasattr(orch.engine, "generate")


# ============================================================
# T6: Runtime 不直接依赖 Orchestrator
# ============================================================
class TestRuntimeIndependence:
    def test_runtime_does_not_import_orchestrator(self):
        """Runtime 顶层模块不应 import src.orchestrator。"""
        # 核心 runtime 模块
        runtime_path = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_path.read_text(encoding="utf-8")
        assert "from src.orchestrator" not in text
        assert "import src.orchestrator" not in text

    def test_bridge_does_not_create_orchestrator(self):
        """OrchestratorRuntimeBridge 不应主动创建 Orchestrator 实例。"""
        bridge_path = PROJECT_ROOT / "src" / "orchestrator_runtime_bridge.py"
        text = bridge_path.read_text(encoding="utf-8")
        assert "Orchestrator(" not in text
        assert "Orchestrator ()" not in text

    def test_bridge_only_receives_orchestrator_via_constructor(self):
        """OrchestratorRuntimeBridge 只通过 __init__ 接收 orchestrator 引用。"""
        bridge_path = PROJECT_ROOT / "src" / "orchestrator_runtime_bridge.py"
        text = bridge_path.read_text(encoding="utf-8")
        # 允许 OrchestratorRuntimeBridge 类定义,但不应有 import Orchestrator
        assert "from src.orchestrator import" not in text
        assert "import src.orchestrator" not in text

    def test_runtime_core_constructable_without_orchestrator(self):
        """RuntimeCore 不需要 Orchestrator 即可构造。"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core is not None
        # 不应有任何对 Orchestrator 的引用
        assert not hasattr(core, "orchestrator")
        assert not hasattr(core, "_orch")


# ============================================================
# T7: Runtime 状态查询
# ============================================================
class TestRuntimeStatus:
    def test_runtime_stats_fields(self):
        """runtime_stats 返回完整字段。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge()
        stats = orch.runtime_stats()
        for key in [
            "status", "runtime_call_count", "legacy_call_count",
            "last_reply_source", "last_runtime_error", "last_legacy_reason",
        ]:
            assert key in stats, f"missing stat: {key}"

    def test_runtime_call_count_increments(self):
        """每次 Runtime 成功调用,count +1。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge(
            runtime_reply="rt"
        )
        assert orch.get_runtime_call_count() == 0
        orch._generate_reply("a", [], "", {}, {}, [], "c")
        orch._generate_reply("b", [], "", {}, {}, [], "c")
        assert orch.get_runtime_call_count() == 2

    def test_legacy_call_count_increments(self):
        """每次 Legacy 调用,count +1。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge(
            raise_on_runtime=True
        )
        assert orch.get_legacy_call_count() == 0
        orch._generate_reply("a", [], "", {}, {}, [], "c")
        orch._generate_reply("b", [], "", {}, {}, [], "c")
        assert orch.get_legacy_call_count() == 2

    def test_last_reply_source_tracks(self):
        """_last_reply_source 跟踪最近一次来源。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge(
            runtime_reply="rt"
        )
        orch._generate_reply("a", [], "", {}, {}, [], "c")
        assert orch.get_last_reply_source() == "runtime"
        # 模拟 runtime 失败
        orch._runtime_bridge.handle_message.side_effect = RuntimeError("x")
        orch._generate_reply("b", [], "", {}, {}, [], "c")
        assert orch.get_last_reply_source() == "legacy"

    def test_runtime_error_recorded_on_failure(self):
        """Runtime 失败时,last_runtime_error 记录。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge(
            raise_on_runtime=True
        )
        orch._generate_reply("a", [], "", {}, {}, [], "c")
        err = orch.get_last_runtime_error()
        assert err is not None
        assert "boom" in err

    def test_is_runtime_enabled_when_status_enabled(self):
        """status=enabled 时,is_runtime_enabled() 返回 True。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge()
        assert orch.is_runtime_enabled() is True

    def test_is_runtime_disabled_when_status_failed(self):
        """status=failed 时,is_runtime_enabled() 返回 False。"""
        orch, _, _ = _build_orchestrator_with_mock_bridge()
        orch._runtime_status = "failed"
        assert orch.is_runtime_enabled() is False


# ============================================================
# T8: Bridge 状态查询
# ============================================================
class TestBridgeState:
    def test_bridge_exposes_state_properties(self):
        """Bridge 暴露完整的 state 属性。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock())
        for prop in [
            "runtime_call_count", "legacy_call_count",
            "last_runtime_error", "last_legacy_reason",
            "last_ctx", "last_reply_source",
        ]:
            assert hasattr(bridge, prop), f"missing: {prop}"

    def test_bridge_set_runtime_core(self):
        """运行时可注入 runtime_core。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock())
        assert bridge._runtime is None
        bridge.set_runtime_core("dummy")
        assert bridge._runtime == "dummy"

    def test_bridge_set_guard_chain(self):
        """运行时可注入 guard_chain。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
        bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock())
        assert bridge._guard_chain is None
        bridge.set_guard_chain("chain")
        assert bridge._guard_chain == "chain"


# ============================================================
# T9: 端到端 process() 集成
# ============================================================
class TestProcessIntegration:
    def test_process_uses_runtime_path_when_available(self):
        """process() 端到端:runtime 可用时,reply 来源为 runtime。"""
        from src.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = {}
        orch.target_user_id = "u1"
        orch.history = []
        orch.current_personality = None
        orch._runtime_call_count = 0
        orch._legacy_call_count = 0
        orch._last_runtime_error = None
        orch._last_legacy_reason = None
        orch._last_reply_source = None
        orch._runtime_status = "enabled"
        # engine mock
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "legacy"
        # bridge mock — runtime 成功
        bridge = MagicMock()
        bridge.handle_message.return_value = "runtime reply"
        bridge.last_runtime_error = None
        bridge.last_legacy_reason = None
        orch._runtime_bridge = bridge
        # 调用 _generate_reply（这是 process() 内部使用的方法）
        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "runtime reply"
        assert orch._last_reply_source == "runtime"
        assert orch._runtime_call_count == 1
        orch.engine.generate.assert_not_called()

    def test_process_falls_back_when_runtime_fails(self):
        """process() 端到端:runtime 失败时,fallback legacy。"""
        from src.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch.config = {}
        orch.target_user_id = "u1"
        orch.history = []
        orch.current_personality = None
        orch._runtime_call_count = 0
        orch._legacy_call_count = 0
        orch._last_runtime_error = None
        orch._last_legacy_reason = None
        orch._last_reply_source = None
        orch._runtime_status = "enabled"
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "legacy reply"
        bridge = MagicMock()
        bridge.handle_message.side_effect = RuntimeError("runtime down")
        bridge.last_runtime_error = "RuntimeError('runtime down')"
        bridge.last_legacy_reason = None
        orch._runtime_bridge = bridge

        reply = orch._generate_reply(
            user_message="hi",
            chat_memories=[],
            personality_context="",
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="c1",
        )
        assert reply == "legacy reply"
        assert orch._last_reply_source == "legacy"
        assert orch._legacy_call_count == 1
        assert orch._runtime_call_count == 0
        orch.engine.generate.assert_called_once()

    def test_real_runtime_core_orchestrator_bridge_integration(self):
        """真实 RuntimeCore 与 OrchestratorRuntimeBridge 集成。"""
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl, EmotionAdapterImpl,
            GrowthAdapterImpl, PersonalityAdapterImpl,
            ResponseAdapterImpl,
        )
        from src.runtime.runtime import RuntimeCore
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        reg = AdapterRegistry()
        reg.register("memory_adapter_impl", MemoryAdapterImpl())
        reg.register("emotion_adapter_impl", EmotionAdapterImpl())
        reg.register("growth_adapter_impl", GrowthAdapterImpl())
        reg.register("personality_adapter_impl", PersonalityAdapterImpl())
        reg.register("response_adapter_impl", ResponseAdapterImpl())

        core = RuntimeCore(
            adapter_registry=reg,
            response_guard_chain=ResponseGuardChain(),
        )
        core.start()

        # mock adapter.generate
        adapter = reg.get("response_adapter_impl")
        with patch.object(adapter, "generate", return_value="真 runtime 回复"):
            orch_mock = MagicMock()
            orch_mock.engine = MagicMock()
            orch_mock.engine.generate.return_value = "legacy fallback"
            bridge = OrchestratorRuntimeBridge(orchestrator=orch_mock, runtime_core=core)
            reply = bridge.handle_message("hi")
            assert reply == "真 runtime 回复"
            assert bridge.last_reply_source == "runtime"
            assert bridge.runtime_call_count == 1
        core.shutdown()


# ============================================================
# T10: 回归保护 —— 旧版 process() 接口兼容
# ============================================================
class TestBackwardCompatibility:
    def test_process_method_signature_unchanged(self):
        """Orchestrator.process() 签名保持不变(对外 contract)。"""
        import inspect
        from src.orchestrator import Orchestrator
        sig = inspect.signature(Orchestrator.process)
        params = list(sig.parameters.keys())
        assert params == ["self", "user_message"], f"unexpected params: {params}"

    def test_process_returns_string(self):
        """process() 必须返回 str(向后兼容 contract)。"""
        from src.orchestrator import Orchestrator
        sig = inspect.signature(Orchestrator.process)
        return_annotation = sig.return_annotation
        # annotation 可能是 'str' 字符串或 <class 'str'>
        assert return_annotation in ("str", str), (
            f"process() return annotation should be 'str', got {return_annotation}"
        )

    def test_engine_generate_signature_preserved(self):
        """ResponseEngine.generate 签名未被 Phase 3.8.5 修改。"""
        import inspect
        from src.engine import ResponseEngine
        sig = inspect.signature(ResponseEngine.generate)
        params = list(sig.parameters.keys())
        # 关键字段
        for required in [
            "user_message", "history", "chat_memories",
            "personality_context", "self_model_context",
            "emotion_context", "relationship_context",
        ]:
            assert required in params, f"engine.generate missing: {required}"


# ============================================================
# T11: 阶段总结
# ============================================================
def test_phase_3_8_5_summary():
    """Phase 3.8.5 阶段总结测试。"""
    from src.orchestrator import Orchestrator
    from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge
    from src.runtime.runtime import RuntimeCore, RuntimeStage, RUNTIME_LIFECYCLE_ORDER

    # 1. 关键类存在
    assert Orchestrator is not None
    assert OrchestratorRuntimeBridge is not None
    assert RuntimeCore is not None

    # 2. Orchestrator 提供 Runtime 推荐入口 + Legacy fallback
    assert hasattr(Orchestrator, "_generate_reply")
    assert hasattr(Orchestrator, "legacy_generate")
    assert hasattr(Orchestrator, "legacy_response")

    # 3. Runtime 生命周期 14 阶段 (Phase 4.0.0: PERCEPTION_OBSERVATION 插入)
    # Phase 4.2.0: 15 阶段 (新增 PERCEPTION_ANALYSIS)
    # Phase 4.2.1: 16 阶段 (新增 SELF_MODEL_BUILD)
    assert len(RUNTIME_LIFECYCLE_ORDER) in (14, 15, 16)
    assert RuntimeStage.RESPONSE_GENERATION in RUNTIME_LIFECYCLE_ORDER
    assert RuntimeStage.GUARD_CHAIN in RUNTIME_LIFECYCLE_ORDER
    assert RuntimeStage.PERCEPTION_OBSERVATION in RUNTIME_LIFECYCLE_ORDER

    # 4. Bridge 暴露完整状态
    bridge = OrchestratorRuntimeBridge(orchestrator=MagicMock())
    assert bridge.runtime_call_count == 0
    assert bridge.legacy_call_count == 0
    assert bridge.last_runtime_error is None
    assert bridge.last_legacy_reason is None
    assert bridge.last_reply_source is None

    # 5. 桥接的 runtime 注入与 guard_chain 注入可独立
    bridge.set_runtime_core("rt")
    bridge.set_guard_chain("gc")
    assert bridge._runtime == "rt"
    assert bridge._guard_chain == "gc"
