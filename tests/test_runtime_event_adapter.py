# -*- coding: utf-8 -*-
"""
tests/test_runtime_event_adapter.py

Phase 6.0 Step 6.0.3 —— RuntimeContext -> EventEnvelope 纯转换测试。

覆盖:
    1. TestConversion (4 tests)     —— 状态 -> 事件类型映射
    2. TestSchema (6 tests)        —— 事件 schema 字段完整性
    3. TestPayload (4 tests)       —— payload 字段保留
    4. TestIsolation (4 tests)     —— 零业务 import / 零 LLM / 纯函数
    5. TestImmutability (3 tests)  —— 不可变 / 深拷贝隔离
    6. TestEdge (6 tests)          —— 异常输入 / 边界条件
    7. TestBridge (3 tests)        —— lifecycle_bridge.build_lifecycle_event 入口

合计: >= 30 tests
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def success_context():
    """success 状态的 RuntimeContext(带 outputs / metadata / error=None)。"""
    from src.runtime.lifecycle_context import RuntimeContext
    return RuntimeContext(
        session_id="s_event_1",
        lifecycle_id="lc_event",
        inputs={"k": "v"},
    )


@pytest.fixture
def running_context():
    from src.runtime.lifecycle_context import (
        RuntimeContext,
        LIFECYCLE_STATE_RUNNING,
    )
    return RuntimeContext(
        session_id="s_run_1",
        lifecycle_id="lc_run",
        state=LIFECYCLE_STATE_RUNNING,
    )


@pytest.fixture
def failed_context():
    from src.runtime.lifecycle_context import (
        RuntimeContext,
        LIFECYCLE_STATE_FAILED,
    )
    return RuntimeContext(
        session_id="s_fail_1",
        lifecycle_id="lc_fail",
        state=LIFECYCLE_STATE_FAILED,
        error="some error happened",
    )


@pytest.fixture
def cancelled_context():
    from src.runtime.lifecycle_context import (
        RuntimeContext,
        LIFECYCLE_STATE_CANCELLED,
    )
    return RuntimeContext(
        session_id="s_cancel_1",
        lifecycle_id="lc_cancel",
        state=LIFECYCLE_STATE_CANCELLED,
    )


# ============================================================
# 1. TestConversion —— 状态 -> 事件类型映射
# ============================================================
class TestConversion:
    def test_success_context_yields_completed(self, success_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_COMPLETED,
        )
        c = success_context.mark_success(outputs={"ok": True})
        e = context_to_event(c)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_COMPLETED

    def test_failed_context_yields_failed(self, failed_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_FAILED,
        )
        e = context_to_event(failed_context)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_FAILED

    def test_cancelled_context_yields_cancelled(self, cancelled_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_CANCELLED,
        )
        e = context_to_event(cancelled_context)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_CANCELLED

    def test_running_context_yields_started(self, running_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_STARTED,
        )
        e = context_to_event(running_context)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_STARTED

    def test_pending_context_yields_started_fallback(self):
        """pending 状态映射为 started (fallback 行为)。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_PENDING,
        )
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_STARTED,
        )
        c = RuntimeContext(
            session_id="s_p",
            lifecycle_id="lc_p",
            state=LIFECYCLE_STATE_PENDING,
        )
        e = context_to_event(c)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_STARTED


# ============================================================
# 2. TestSchema —— 事件 schema 字段完整性
# ============================================================
class TestSchema:
    def test_event_type_present(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert "event_type" in e
        assert isinstance(e["event_type"], str)
        assert e["event_type"].startswith("runtime.lifecycle.")

    def test_event_id_present(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert "event_id" in e
        assert isinstance(e["event_id"], str)
        assert len(e["event_id"]) > 0

    def test_timestamp_iso_format(self, success_context):
        from src.runtime.event_adapter import context_to_event
        from datetime import datetime
        c = success_context.mark_success()
        e = context_to_event(c)
        assert "timestamp" in e
        ts = e["timestamp"]
        assert isinstance(ts, str)
        assert ts.endswith("Z")
        # 可解析
        cleaned = ts[:-1]
        datetime.fromisoformat(cleaned)  # 不抛

    def test_source_is_runtime(self, success_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_EVENT_SOURCE,
        )
        c = success_context.mark_success()
        e = context_to_event(c)
        assert e["source"] == RUNTIME_EVENT_SOURCE
        assert e["source"] == "runtime"

    def test_lifecycle_id_preserved(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert e["lifecycle_id"] == "lc_event"

    def test_session_id_preserved(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert e["session_id"] == "s_event_1"

    def test_schema_version_present(self, success_context):
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_EVENT_ADAPTER_SCHEMA_VERSION,
        )
        c = success_context.mark_success()
        e = context_to_event(c)
        assert "schema_version" in e
        assert e["schema_version"] == RUNTIME_EVENT_ADAPTER_SCHEMA_VERSION
        assert e["schema_version"] == "1.0"

    def test_status_field_present(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert "status" in e
        assert e["status"] == "success"


# ============================================================
# 3. TestPayload —— payload 字段保留
# ============================================================
class TestPayload:
    def test_duration_ms_preserved(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(outputs={"x": 1})
        e = context_to_event(c)
        # success 状态下 ended_at 已填, duration_ms 应可计算
        assert "payload" in e
        assert "duration_ms" in e["payload"]
        # 至少为 None 或 int
        assert e["payload"]["duration_ms"] is None or isinstance(
            e["payload"]["duration_ms"], int
        )

    def test_outputs_preserved(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(outputs={"result": 42, "sub": {"k": "v"}})
        e = context_to_event(c)
        assert "outputs" in e["payload"]
        assert e["payload"]["outputs"]["result"] == 42
        assert e["payload"]["outputs"]["sub"]["k"] == "v"

    def test_error_preserved(self, failed_context):
        from src.runtime.event_adapter import context_to_event
        e = context_to_event(failed_context)
        assert "error" in e["payload"]
        assert e["payload"]["error"] == "some error happened"

    def test_error_none_on_success(self, success_context):
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success()
        e = context_to_event(c)
        assert e["payload"]["error"] is None

    def test_metadata_preserved(self):
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_adapter import context_to_event
        c = RuntimeContext(
            session_id="s_meta",
            lifecycle_id="lc_meta",
            metadata={"trace_id": "tr_xyz", "host": "test"},
        )
        c = c.mark_success(outputs={"ok": True})
        e = context_to_event(c)
        assert e["payload"]["metadata"]["trace_id"] == "tr_xyz"
        assert e["payload"]["metadata"]["host"] == "test"


# ============================================================
# 4. TestIsolation —— 零业务 import / 零 LLM / 纯函数
# ============================================================
class TestIsolation:
    def test_adapter_no_business_import(self):
        """event_adapter.py 源码禁止 import 业务模块。"""
        from src.runtime import event_adapter as mod
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

    def test_adapter_no_llm_keywords(self):
        """event_adapter.py 源码不包含 LLM 关键词。"""
        from src.runtime import event_adapter as mod
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

    def test_context_to_event_is_pure(self, success_context):
        """context_to_event 是纯函数:不修改 context。"""
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(outputs={"x": 1})
        snapshot_state = c.state
        snapshot_outputs = copy.deepcopy(dict(c.outputs))
        snapshot_metadata = copy.deepcopy(dict(c.metadata))
        # 调用多次
        e1 = context_to_event(c)
        e2 = context_to_event(c)
        # 1) context 不变
        assert c.state == snapshot_state
        assert dict(c.outputs) == snapshot_outputs
        assert dict(c.metadata) == snapshot_metadata
        # 2) 不同调用产生不同 event_id (每次都生成新的)
        assert e1["event_id"] != e2["event_id"]
        # 3) 其他字段一致
        assert e1["event_type"] == e2["event_type"]
        assert e1["status"] == e2["status"]

    def test_adapter_only_stdlib_imports(self):
        """adapter 顶层 import 仅 stdlib。"""
        from src.runtime import event_adapter as mod
        import re
        src = open(mod.__file__, "r", encoding="utf-8").read()
        imports = re.findall(
            r"^(?:from|import)\s+(\S+)", src, flags=re.MULTILINE
        )
        for name in imports:
            top = name.split(".")[0]
            assert top in {"__future__", "copy", "logging", "uuid", "datetime", "typing"}, \
                f"非 stdlib 顶层 import: {name}"


# ============================================================
# 5. TestImmutability —— 不可变 / 深拷贝隔离
# ============================================================
class TestImmutability:
    def test_modify_event_does_not_affect_context(self, success_context):
        """修改 event 不影响 context。"""
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(outputs={"k": "v"})
        e = context_to_event(c)
        # 修改 event
        e["event_type"] = "tampered"
        e["lifecycle_id"] = "tampered_id"
        e["payload"]["outputs"]["k"] = "tampered_value"
        e["payload"]["error"] = "tampered_error"
        # context 不变
        assert c.state == "success"
        assert c.lifecycle_id == "lc_event"
        assert c.outputs["k"] == "v"
        assert c.error is None

    def test_modify_payload_does_not_affect_outputs(self, success_context):
        """修改 payload.outputs 不影响 context.outputs。"""
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(
            outputs={"nested": {"inner": [1, 2, 3]}, "k": "v"}
        )
        e = context_to_event(c)
        # 修改 event 中的 payload
        e["payload"]["outputs"]["nested"]["inner"].append(999)
        e["payload"]["outputs"]["new_key"] = "new"
        # context.outputs 不变
        assert "new_key" not in c.outputs
        assert 999 not in c.outputs["nested"]["inner"]
        assert c.outputs["nested"]["inner"] == [1, 2, 3]

    def test_metadata_independent_copy(self, success_context):
        """metadata 独立复制。"""
        from src.runtime.event_adapter import context_to_event
        c = success_context.with_update(metadata={"a": 1})
        e = context_to_event(c)
        # 修改 event metadata
        e["payload"]["metadata"]["a"] = 999
        e["payload"]["metadata"]["b"] = "new"
        # context.metadata 不变
        assert c.metadata == {"a": 1}


# ============================================================
# 6. TestEdge —— 异常输入 / 边界条件
# ============================================================
class TestEdge:
    def test_empty_outputs(self):
        """outputs 为空 dict。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_adapter import context_to_event
        c = RuntimeContext(session_id="s1", lifecycle_id="lc1")
        c = c.mark_success()  # outputs 仍为 {}
        e = context_to_event(c)
        assert e["payload"]["outputs"] == {}

    def test_missing_metadata(self):
        """没有 metadata 字段也能正常转换。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_adapter import context_to_event
        c = RuntimeContext(session_id="s1", lifecycle_id="lc1")
        # 不显式提供 metadata (默认 {})
        c = c.mark_success()
        e = context_to_event(c)
        assert e["payload"]["metadata"] == {}

    def test_non_terminal_context(self):
        """非终态 context (running) 也能转换。"""
        from src.runtime.lifecycle_context import (
            RuntimeContext,
            LIFECYCLE_STATE_RUNNING,
        )
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_STARTED,
        )
        c = RuntimeContext(
            session_id="s1",
            lifecycle_id="lc1",
            state=LIFECYCLE_STATE_RUNNING,
        )
        e = context_to_event(c)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_STARTED
        assert e["status"] == "running"

    def test_invalid_context_raises(self):
        """非法 context 抛 EventAdapterError。"""
        from src.runtime.event_adapter import context_to_event, EventAdapterError
        with pytest.raises(EventAdapterError):
            context_to_event("not a context")  # type: ignore[arg-type]
        with pytest.raises(EventAdapterError):
            context_to_event(None)  # type: ignore[arg-type]
        with pytest.raises(EventAdapterError):
            context_to_event(123)  # type: ignore[arg-type]

    def test_very_long_outputs(self):
        """超长 outputs 也能正常处理。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_adapter import context_to_event
        big = {f"k_{i}": f"v_{i}" for i in range(1000)}
        c = RuntimeContext(
            session_id="s1", lifecycle_id="lc1", inputs=big
        )
        c = c.mark_success(outputs=big)
        e = context_to_event(c)
        assert len(e["payload"]["outputs"]) == 1000
        assert e["payload"]["outputs"]["k_999"] == "v_999"

    def test_timestamp_fallback(self, monkeypatch):
        """timestamp 缺失时使用 fallback (1970-01-01)。"""
        from src.runtime import event_adapter as adapter_mod
        from src.runtime.lifecycle_context import RuntimeContext

        # monkeypatch _now_iso 让其抛错
        def broken_now():
            raise RuntimeError("clock broken")

        monkeypatch.setattr(adapter_mod, "_now_iso", broken_now)
        c = RuntimeContext(session_id="s1", lifecycle_id="lc1")
        c = c.mark_success()
        e = adapter_mod.context_to_event(c)
        assert e["timestamp"] == "1970-01-01T00:00:00Z"

    def test_unknown_state_yields_unknown_event(self):
        """未知 state 映射为 runtime.lifecycle.unknown。"""
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.event_adapter import (
            context_to_event,
            RUNTIME_LIFECYCLE_EVENT_UNKNOWN,
        )
        c = RuntimeContext(
            session_id="s1", lifecycle_id="lc1", state="weird_state"
        )
        e = context_to_event(c)
        assert e["event_type"] == RUNTIME_LIFECYCLE_EVENT_UNKNOWN
        assert e["status"] == "weird_state"


# ============================================================
# 7. TestBridge —— lifecycle_bridge.build_lifecycle_event 入口
# ============================================================
class TestBridge:
    def test_build_lifecycle_event_delegates_to_adapter(self, success_context):
        """build_lifecycle_event 是 context_to_event 的统一入口。"""
        from src.runtime.lifecycle.lifecycle_bridge import build_lifecycle_event
        from src.runtime.event_adapter import context_to_event
        c = success_context.mark_success(outputs={"x": 1})
        e1 = build_lifecycle_event(c)
        e2 = context_to_event(c)
        # 字段一致 (event_id / timestamp 可能不同)
        assert e1["event_type"] == e2["event_type"]
        assert e1["lifecycle_id"] == e2["lifecycle_id"]
        assert e1["session_id"] == e2["session_id"]
        assert e1["status"] == e2["status"]
        assert e1["payload"]["outputs"] == e2["payload"]["outputs"]

    def test_build_lifecycle_event_raises_on_invalid(self):
        """build_lifecycle_event 对非法 context 抛错。"""
        from src.runtime.lifecycle.lifecycle_bridge import build_lifecycle_event
        from src.runtime.event_adapter import EventAdapterError
        with pytest.raises(EventAdapterError):
            build_lifecycle_event("not a context")  # type: ignore[arg-type]

    def test_bridge_does_not_send_event(self, success_context, monkeypatch):
        """build_lifecycle_event 不会发送事件(不发任何 side effect)。"""
        from src.runtime import event_adapter
        from src.runtime.lifecycle import lifecycle_bridge as bridge_mod
        sent = {"count": 0}

        def fake_send(*args, **kwargs):
            sent["count"] += 1
            return None

        # monkeypatch 所有可能的发送函数,验证无调用
        monkeypatch.setattr(event_adapter, "logger", type("L", (), {
            "info": fake_send, "debug": fake_send, "warning": fake_send,
            "error": fake_send, "exception": fake_send,
        })())
        c = success_context.mark_success()
        e = bridge_mod.build_lifecycle_event(c)
        assert e is not None
        # 没有任何 logger 发送动作被记为 send
        # (logger.info / .debug 不会被 fake_send 触发,仅当代码显式调 logger 才会)
        assert sent["count"] == 0


# ============================================================
# 8. TestIntegrationWithBridge —— 与 lifecycle_bridge.run_with_context 集成
# ============================================================
class TestIntegrationWithBridge:
    """验证: LifecycleManager.run(context) -> build_lifecycle_event(context)
    端到端可工作。"""

    def test_full_flow_run_then_build_event(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_bridge import (
            run_with_context,
            build_lifecycle_event,
        )
        from src.runtime.lifecycle_context import RuntimeContext

        manager = LifecycleManager(name="event_test")
        manager.register(SimpleTask(
            task_id="t.1", owner="test", action=lambda ctx: None
        ))

        ctx = RuntimeContext(session_id="s_full", lifecycle_id="lc_full")
        final_ctx = run_with_context(manager, ctx)
        # final_ctx 应为 success
        assert final_ctx.state == "success"
        # 现在构造事件
        e = build_lifecycle_event(final_ctx)
        assert e["event_type"] == "runtime.lifecycle.completed"
        assert e["status"] == "success"
        assert e["lifecycle_id"] == "lc_full"
        assert e["session_id"] == "s_full"
        assert "outputs" in e["payload"]
        assert e["payload"]["outputs"]["lifecycle"]["name"] == "event_test"

    def test_full_flow_failed_then_build_event(self):
        from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
        from src.runtime.lifecycle.lifecycle_task import SimpleTask
        from src.runtime.lifecycle.lifecycle_bridge import (
            run_with_context,
            build_lifecycle_event,
        )
        from src.runtime.lifecycle_context import RuntimeContext

        manager = LifecycleManager(name="event_fail_test")
        manager.register(SimpleTask(
            task_id="t.bad", owner="test",
            action=lambda ctx: (_ for _ in ()).throw(ValueError("oops")),
        ))

        ctx = RuntimeContext(session_id="s_full_f", lifecycle_id="lc_full_f")
        final_ctx = run_with_context(manager, ctx)
        assert final_ctx.state == "failed"
        e = build_lifecycle_event(final_ctx)
        assert e["event_type"] == "runtime.lifecycle.failed"
        assert e["status"] == "failed"
        assert e["payload"]["error"] is not None
        assert "oops" in e["payload"]["error"] or "ValueError" in e["payload"]["error"]
