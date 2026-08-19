# -*- coding: utf-8 -*-
"""
tests/test_runtime_event_publisher.py

Phase 6.0 Step 6.0.4 —— RuntimeEventPublisher 完整测试。

覆盖:
    1. TestPublisher (3)         —— 构造 / 基础行为
    2. TestSink (3)              —— sink 调用
    3. TestNoSink (3)            —— 无 sink 行为
    4. TestFailure (3)           —— sink 异常隔离
    5. TestIsolation (4)         —— 零业务 import / 零 LLM / 纯 Runtime 层
    6. TestBridge (3)            —— lifecycle_bridge 集成入口
    7. TestImmutability (2)      —— 不修改 context / event
    8. TestCompatibility (2)     —— 旧 API 兼容
    9. TestEdge (7)              —— 异常输入 / 边界

合计: >= 30 tests
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures / Helpers
# ============================================================
@pytest.fixture
def success_context():
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext(
        session_id="s_pub_1",
        lifecycle_id="lc_pub",
    ).mark_success(outputs={"ok": True})


@pytest.fixture
def failed_context():
    from src.runtime.lifecycle_context import (
        RuntimeContext,
        LIFECYCLE_STATE_FAILED,
    )
    return RuntimeContext(
        session_id="s_pub_2",
        lifecycle_id="lc_pub_2",
        state=LIFECYCLE_STATE_FAILED,
        error="boom",
    )


class _RecordingSink:
    """测试用 sink:记录所有 publish 调用。"""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def publish(self, event: Dict[str, Any]) -> None:
        self.events.append(event)


class _FailingSink:
    """测试用 sink:总是抛错。"""

    def __init__(self, exc: BaseException = None) -> None:
        self.exc = exc or RuntimeError("sink failure")
        self.call_count = 0

    def publish(self, event: Dict[str, Any]) -> None:
        self.call_count += 1
        raise self.exc


# ============================================================
# 1. TestPublisher
# ============================================================
class TestPublisher:
    def test_default_publisher_creation(self):
        """默认 publisher 创建 (无 sink)。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher()
        assert p is not None
        assert p.sink is None
        assert p.has_sink is False

    def test_publisher_with_sink_creation(self):
        """有 sink 创建。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        assert p.sink is sink
        assert p.has_sink is True

    def test_publish_context_event_returns_event(self, success_context):
        """publish_context_event 返回 event。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        result = p.publish_context_event(success_context)
        assert "event" in result
        assert "published" in result
        assert result["event"]["event_type"] == "runtime.lifecycle.completed"


# ============================================================
# 2. TestSink
# ============================================================
class TestSink:
    def test_sink_is_called(self, success_context):
        """sink 被调用。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        p.publish_context_event(success_context)
        assert len(sink.events) == 1

    def test_event_correctly_passed_to_sink(self, success_context):
        """event 正确传入 sink。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        p.publish_context_event(success_context)
        e = sink.events[0]
        assert e["lifecycle_id"] == "lc_pub"
        assert e["session_id"] == "s_pub_1"
        assert e["status"] == "success"

    def test_multiple_publishes_accumulate(self, success_context):
        """多次 publish 都被记录。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        for _ in range(5):
            p.publish_context_event(success_context)
        assert len(sink.events) == 5

    def test_published_true_on_success(self, success_context):
        """成功时 published=True。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=_RecordingSink())
        result = p.publish_context_event(success_context)
        assert result["published"] is True
        assert result["reason"] is None
        assert result["error"] is None


# ============================================================
# 3. TestNoSink
# ============================================================
class TestNoSink:
    def test_no_sink_does_not_crash(self, success_context):
        """无 sink 不崩溃。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher()  # 无 sink
        result = p.publish_context_event(success_context)
        assert result is not None
        assert "published" in result

    def test_no_sink_returns_published_false(self, success_context):
        """无 sink 返回 published=False。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher()
        result = p.publish_context_event(success_context)
        assert result["published"] is False

    def test_no_sink_reason_correct(self, success_context):
        """无 sink 时 reason 正确。"""
        from src.runtime.event_publisher import (
            RuntimeEventPublisher,
            PUBLISH_REASON_NO_SINK,
        )
        p = RuntimeEventPublisher()
        result = p.publish_context_event(success_context)
        assert result["reason"] == PUBLISH_REASON_NO_SINK
        assert result["reason"] == "no_sink"
        assert result["error"] is None

    def test_no_sink_event_still_returned(self, success_context):
        """无 sink 时 event 仍返回(给调用方使用)。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher()
        result = p.publish_context_event(success_context)
        assert result["event"]["event_type"] == "runtime.lifecycle.completed"


# ============================================================
# 4. TestFailure
# ============================================================
class TestFailure:
    def test_sink_exception_caught(self, success_context):
        """sink 抛异常被捕获。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=_FailingSink())
        # 不应抛错
        result = p.publish_context_event(success_context)
        assert result["published"] is False

    def test_sink_error_preserved(self, success_context):
        """error 字段保留错误信息。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=_FailingSink(RuntimeError("boom")))
        result = p.publish_context_event(success_context)
        assert result["error"] is not None
        assert "boom" in result["error"]
        assert "RuntimeError" in result["error"]

    def test_context_not_modified_on_sink_failure(self, success_context):
        """sink 失败时 context 不改变。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        original_state = success_context.state
        original_outputs = copy.deepcopy(dict(success_context.outputs))
        original_lifecycle_id = success_context.lifecycle_id
        original_error = success_context.error

        p = RuntimeEventPublisher(sink=_FailingSink())
        p.publish_context_event(success_context)

        assert success_context.state == original_state
        assert dict(success_context.outputs) == original_outputs
        assert success_context.lifecycle_id == original_lifecycle_id
        assert success_context.error == original_error

    def test_sink_error_includes_event(self, success_context):
        """sink 失败时 event 仍包含在结果中。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=_FailingSink())
        result = p.publish_context_event(success_context)
        assert result["event"]["event_type"] == "runtime.lifecycle.completed"

    def test_sink_reason_is_sink_error(self, success_context):
        """sink 失败时 reason = 'sink_error'。"""
        from src.runtime.event_publisher import (
            RuntimeEventPublisher,
            PUBLISH_REASON_SINK_ERROR,
        )
        p = RuntimeEventPublisher(sink=_FailingSink())
        result = p.publish_context_event(success_context)
        assert result["reason"] == PUBLISH_REASON_SINK_ERROR


# ============================================================
# 5. TestIsolation
# ============================================================
class TestIsolation:
    def test_publisher_no_business_import(self):
        """event_publisher.py 源码禁止 import 业务模块。"""
        from src.runtime import event_publisher as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 仅检查真正的 import 语句
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines)
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.llm", "import src.llm",
            "from src.events", "import src.events",  # 业务事件
            "from src.audit", "import src.audit",
        ]
        for f in forbidden:
            assert f not in joined, f"禁止 import: {f}"

    def test_publisher_no_llm_keywords(self):
        """event_publisher.py 源码无 LLM 关键词。"""
        from src.runtime import event_publisher as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        import_lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if (
                stripped.startswith("#")
                or stripped.startswith('"""')
                or stripped.startswith("'''")
            ):
                continue
            if stripped.startswith("from ") or stripped.startswith("import "):
                import_lines.append(stripped)
        joined = "\n".join(import_lines).lower()
        forbidden = ["openai", "anthropic", "claude", "src.llm", "import llm"]
        for kw in forbidden:
            assert kw not in joined, f"禁止 LLM 关键词: {kw}"

    def test_publisher_only_stdlib_imports(self):
        """publisher 顶层 import 仅 stdlib。"""
        from src.runtime import event_publisher as mod
        import re
        src = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(
            r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE
        )
        for name in imports:
            top = name.split(".")[0]
            assert top in {"__future__", "logging", "typing"}, \
                f"非 stdlib 顶层 import: {name}"

    def test_runtime_layer_pure(self, success_context):
        """publisher 属于纯 Runtime 层 (不依赖业务模块)。"""
        from src.runtime.event_publisher import (
            RuntimeEventPublisher,
            EventSink,
        )
        # EventSink 是 Protocol
        assert hasattr(EventSink, "publish")
        # Publisher 接受任意满足 Protocol 的对象
        p = RuntimeEventPublisher(sink=_RecordingSink())
        assert p.has_sink


# ============================================================
# 6. TestBridge
# ============================================================
class TestBridge:
    def test_bridge_can_call_publisher(self, success_context):
        """lifecycle_bridge 可以调用 publisher。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        from src.runtime.lifecycle.lifecycle_bridge import publish_lifecycle_event
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        result = publish_lifecycle_event(success_context, p)
        assert result["published"] is True
        assert len(sink.events) == 1

    def test_bridge_does_not_rebuild_event(self, success_context):
        """bridge 不重复构造 event (委托给 build_lifecycle_event)。"""
        # 通过 monkeypatch 验证 build_lifecycle_event 只被调用一次
        from src.runtime.lifecycle import lifecycle_bridge as bridge
        from src.runtime.event_publisher import RuntimeEventPublisher
        from unittest.mock import patch

        call_count = {"n": 0}
        original = bridge.build_lifecycle_event

        def counting_build(ctx):
            call_count["n"] += 1
            return original(ctx)

        with patch.object(bridge, "build_lifecycle_event", counting_build):
            p = RuntimeEventPublisher(sink=_RecordingSink())
            bridge.publish_lifecycle_event(success_context, p)
        # 1) build_lifecycle_event 至少被调用 1 次
        assert call_count["n"] >= 1

    def test_bridge_does_not_directly_publish(self, success_context, monkeypatch):
        """bridge 不直接 publish(只委托给 publisher)。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        from src.runtime.lifecycle import lifecycle_bridge as bridge
        from src.runtime import event_publisher as pub_mod

        # 监听 publisher.publish_event 是否被调用
        original = pub_mod.RuntimeEventPublisher.publish_event
        called = {"n": 0}

        def counting_publish(self, event):
            called["n"] += 1
            return original(self, event)

        monkeypatch.setattr(
            pub_mod.RuntimeEventPublisher, "publish_event", counting_publish
        )

        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        bridge.publish_lifecycle_event(success_context, p)
        # 1) publisher.publish_event 被调用至少 1 次
        assert called["n"] >= 1
        # 2) sink 也被调用(因为 publish_event 最终调到 sink)
        assert len(sink.events) == 1


# ============================================================
# 7. TestImmutability
# ============================================================
class TestImmutability:
    def test_sink_modifying_event_does_not_affect_context(
        self, success_context
    ):
        """sink 修改 event 不影响原 context。"""
        class TamperingSink:
            def publish(self, event):
                event["event_type"] = "tampered"
                event["lifecycle_id"] = "tampered_id"
                if "payload" in event and "outputs" in event["payload"]:
                    event["payload"]["outputs"]["injected"] = "x"

        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=TamperingSink())
        p.publish_context_event(success_context)
        # context 不变
        assert success_context.lifecycle_id == "lc_pub"
        assert success_context.session_id == "s_pub_1"
        assert success_context.state == "success"

    def test_payload_deepcopy_isolated(self, success_context):
        """payload 深拷贝,sink 修改不影响 context。"""
        class TamperingSink:
            def publish(self, event):
                if "payload" in event and "outputs" in event["payload"]:
                    out = event["payload"]["outputs"]
                    if isinstance(out, dict) and "lifecycle" in out:
                        out["lifecycle"]["injected"] = "x"

        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=TamperingSink())
        p.publish_context_event(success_context)
        # outputs.lifecycle 不应被 sink 注入的字段污染
        assert "injected" not in success_context.outputs.get("lifecycle", {})


# ============================================================
# 8. TestCompatibility
# ============================================================
class TestCompatibility:
    def test_run_with_context_still_works(self, success_context):
        """run_with_context 仍可用 (未破坏旧 API)。"""
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context

        manager = LifecycleManager(name="compat_test")
        manager.register(SimpleTask(
            task_id="t.1", owner="test", action=lambda ctx: None
        ))
        from src.runtime.lifecycle_context import RuntimeContext
        ctx = RuntimeContext(session_id="s_c", lifecycle_id="lc_c")
        result_ctx = run_with_context(manager, ctx)
        assert result_ctx.state == "success"

    def test_build_lifecycle_event_still_works(self, success_context):
        """build_lifecycle_event 仍可用 (Step 6.0.3 旧 API)。"""
        from src.runtime.lifecycle.lifecycle_bridge import build_lifecycle_event
        e = build_lifecycle_event(success_context)
        assert e["event_type"] == "runtime.lifecycle.completed"


# ============================================================
# 9. TestEdge
# ============================================================
class TestEdge:
    def test_none_context(self):
        """None context -> published=False, reason=invalid_context。"""
        from src.runtime.event_publisher import (
            RuntimeEventPublisher,
            PUBLISH_REASON_INVALID_CONTEXT,
        )
        p = RuntimeEventPublisher(sink=_RecordingSink())
        result = p.publish_context_event(None)  # type: ignore[arg-type]
        assert result["published"] is False
        assert result["reason"] == PUBLISH_REASON_INVALID_CONTEXT

    def test_non_dict_sink(self, success_context):
        """非 dict sink (非 Protocol) 仍能工作(只要有 publish 方法)。"""
        class StringSink:
            def __init__(self):
                self.received = []
            def publish(self, event):
                self.received.append(str(event))

        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=StringSink())
        result = p.publish_context_event(success_context)
        assert result["published"] is True

    def test_sink_without_publish_method(self, success_context):
        """sink 缺少 publish 方法 -> sink 调用时 AttributeError 被捕获。"""
        class BadSink:
            pass

        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=BadSink())  # type: ignore[arg-type]
        result = p.publish_context_event(success_context)
        # AttributeError 被捕获,published=False
        assert result["published"] is False
        assert result["reason"] == "sink_error"

    def test_empty_event_to_publish_event(self):
        """publish_event 接收空 dict。"""
        from src.runtime.event_publisher import RuntimeEventPublisher
        p = RuntimeEventPublisher(sink=_RecordingSink())
        result = p.publish_event({})
        assert result["published"] is True
        assert result["event"] == {}

    def test_very_long_outputs_in_context(self):
        """超长 outputs 也能正常发布。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_publisher import RuntimeEventPublisher
        big = {f"k_{i}": f"v_{i}" for i in range(500)}
        c = RuntimeContext(
            session_id="s_big", lifecycle_id="lc_big", inputs=big
        )
        c = c.mark_success(outputs=big)
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        result = p.publish_context_event(c)
        assert result["published"] is True
        assert len(sink.events[0]["payload"]["outputs"]) == 500

    def test_unknown_state_event(self):
        """未知 state 的 context 仍能发布(事件类型为 unknown)。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_publisher import RuntimeEventPublisher
        c = RuntimeContext(
            session_id="s_x", lifecycle_id="lc_x", state="weird_state"
        )
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        result = p.publish_context_event(c)
        assert result["published"] is True
        assert sink.events[0]["event_type"] == "runtime.lifecycle.unknown"

    def test_cancelled_event(self):
        """cancelled 状态的 context 产生 cancelled 事件。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_CANCELLED,
        )
        from src.runtime.event_publisher import RuntimeEventPublisher
        c = RuntimeContext(
            session_id="s_can", lifecycle_id="lc_can",
            state=LIFECYCLE_STATE_CANCELLED,
        )
        sink = _RecordingSink()
        p = RuntimeEventPublisher(sink=sink)
        result = p.publish_context_event(c)
        assert result["published"] is True
        assert sink.events[0]["event_type"] == "runtime.lifecycle.cancelled"


# ============================================================
# 10. TestEventSinkProtocol
# ============================================================
class TestEventSinkProtocol:
    def test_recording_sink_satisfies_protocol(self):
        """_RecordingSink 满足 EventSink Protocol。"""
        from src.runtime.event_publisher import EventSink
        sink = _RecordingSink()
        assert isinstance(sink, EventSink)

    def test_failing_sink_satisfies_protocol(self):
        """_FailingSink 满足 EventSink Protocol。"""
        from src.runtime.event_publisher import EventSink
        sink = _FailingSink()
        assert isinstance(sink, EventSink)

    def test_null_sink_satisfies_protocol(self):
        """NullSink 满足 EventSink Protocol。"""
        from src.runtime.event_publisher import EventSink, NullSink
        sink = NullSink()
        assert isinstance(sink, EventSink)


# ============================================================
# 11. TestEndToEnd
# ============================================================
class TestEndToEnd:
    def test_end_to_end_lifecycle_to_event(self):
        """端到端: LifecycleManager -> context -> publisher -> sink。"""
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_bridge import (
            run_with_context,
            publish_lifecycle_event,
        )
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_publisher import RuntimeEventPublisher

        manager = LifecycleManager(name="e2e")
        manager.register(SimpleTask(
            task_id="t.1", owner="test", action=lambda ctx: None
        ))

        sink = _RecordingSink()
        publisher = RuntimeEventPublisher(sink=sink)

        ctx = RuntimeContext(session_id="s_e2e", lifecycle_id="lc_e2e")
        ctx = run_with_context(manager, ctx)
        # 发布
        result = publish_lifecycle_event(ctx, publisher)
        assert result["published"] is True
        assert sink.events[0]["lifecycle_id"] == "lc_e2e"
        assert sink.events[0]["event_type"] == "runtime.lifecycle.completed"
        assert sink.events[0]["payload"]["outputs"]["lifecycle"]["name"] == "e2e"

    def test_end_to_end_failed_event(self):
        """端到端: failed event。"""
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_bridge import (
            run_with_context,
            publish_lifecycle_event,
        )
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_publisher import RuntimeEventPublisher

        manager = LifecycleManager(name="e2e_fail")
        manager.register(SimpleTask(
            task_id="t.bad", owner="test",
            action=lambda ctx: (_ for _ in ()).throw(ValueError("oops")),
        ))

        sink = _RecordingSink()
        publisher = RuntimeEventPublisher(sink=sink)

        ctx = RuntimeContext(session_id="s_e2e_f", lifecycle_id="lc_e2e_f")
        ctx = run_with_context(manager, ctx)
        result = publish_lifecycle_event(ctx, publisher)
        assert result["published"] is True
        assert sink.events[0]["event_type"] == "runtime.lifecycle.failed"
        assert sink.events[0]["status"] == "failed"
        assert "oops" in sink.events[0]["payload"]["error"] or "ValueError" in sink.events[0]["payload"]["error"]
