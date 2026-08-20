# -*- coding: utf-8 -*-
"""
tests/test_context_v2_capability_gates.py

P2.3-A.2.6 Phase 2 —— 四处 isinstance 硬门能力化改造的验证。

覆盖:
    1. persistence_hook：v2 不再被静默跳过；v1 行为不变；垃圾输入照旧拒绝
    2. event_adapter：v2 可转事件；垃圾输入照旧抛 EventAdapterError
    3. lifecycle_bridge：v2 可走完整生命周期；垃圾输入照旧抛 LifecycleBridgeError
    4. 能力判断边界：schema_version / to_dict / with_update 任一缺失即拒绝
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Helpers / Fixtures
# ============================================================
def _make_v2(**overrides):
    """构造一个 v2 上下文（默认 pending 状态）。"""
    from src.runtime.request_context import RuntimeContext
    kwargs = {
        "session_id": "s_v2_gate",
        "lifecycle_id": "v2_gate_001",
        "inputs": {"user_message": "你好", "user_id": "123456"},
    }
    kwargs.update(overrides)
    return RuntimeContext(**kwargs)


def _make_v1(**overrides):
    """构造一个 lifecycle v1 上下文。"""
    from src.runtime.lifecycle_context import RuntimeContext
    kwargs = {
        "session_id": "s_v1_gate",
        "lifecycle_id": "v1_gate_001",
    }
    kwargs.update(overrides)
    return RuntimeContext(**kwargs)


class FakeStorage:
    """记录 save 调用的最小 fake（满足 StorageLike 协议）。"""

    def __init__(self, result=None, raise_exc=None):
        self.result = result or {"saved": True, "lifecycle_id": "", "reason": "ok"}
        self.raise_exc = raise_exc
        self.call_count = 0
        self.last_context = None

    def save(self, context):
        self.call_count += 1
        self.last_context = context
        if self.raise_exc is not None:
            raise self.raise_exc
        r = dict(self.result)
        r["lifecycle_id"] = str(getattr(context, "lifecycle_id", "") or "")
        return r


@pytest.fixture
def bridge_manager():
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    from src.runtime.lifecycle.lifecycle_task import SimpleTask
    m = LifecycleManager(name="gate_bridge_test")
    m.start()
    m.register(SimpleTask(task_id="t.1", owner="test", action=lambda ctx: None))
    return m


# ============================================================
# 1. persistence_hook
# ============================================================
class TestPersistenceHook:
    def test_v2_persists_instead_of_silent_skip(self):
        """v2 上下文不再被 isinstance 硬门静默跳过，真正交给 storage。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(_make_v2())
        assert result["saved"] is True
        assert result["reason"] == "ok"
        assert storage.call_count == 1
        assert storage.last_context.schema_version == "2.0"

    def test_v1_behavior_unchanged(self):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(_make_v1())
        assert result["saved"] is True
        assert storage.call_count == 1

    def test_garbage_still_rejected(self):
        """垃圾输入照旧 saved=False，reason 含 'invalid'（既有契约）。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist("not a context")
        assert result["saved"] is False
        assert "invalid" in result["reason"]
        assert storage.call_count == 0
        result_none = hook.persist(None)
        assert result_none["saved"] is False
        assert storage.call_count == 0

    def test_fake_schema_version_without_to_dict_rejected(self):
        """有 schema_version 但无 to_dict 能力的对象仍被拒绝。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook

        class HalfContext:
            schema_version = "2.0"

        storage = FakeStorage()
        hook = RuntimePersistenceHook(storage)
        result = hook.persist(HalfContext())
        assert result["saved"] is False
        assert "invalid" in result["reason"]
        assert storage.call_count == 0


# ============================================================
# 2. event_adapter
# ============================================================
class TestEventAdapter:
    def test_v2_converts_to_event(self):
        from src.runtime.event_adapter import context_to_event
        from src.runtime.request_context import RuntimeContext
        v2 = RuntimeContext(
            session_id="s_v2_evt",
            lifecycle_id="v2_evt_001",
            state="success",
            outputs={"reply": "hi", "source": "runtime"},
        )
        e = context_to_event(v2)
        assert e["lifecycle_id"] == "v2_evt_001"
        assert e["session_id"] == "s_v2_evt"
        assert e["status"] == "success"
        assert e["event_type"] == "runtime.lifecycle.completed"
        assert e["payload"]["outputs"] == {"reply": "hi", "source": "runtime"}

    def test_v2_running_maps_to_started(self):
        from src.runtime.event_adapter import context_to_event
        from src.runtime.request_context import RuntimeContext
        v2 = RuntimeContext(
            session_id="s", lifecycle_id="lc", state="running",
        )
        e = context_to_event(v2)
        assert e["event_type"] == "runtime.lifecycle.started"
        assert e["payload"]["duration_ms"] is None  # v2 无 duration_ms → 兜底

    def test_garbage_still_raises_event_adapter_error(self):
        from src.runtime.event_adapter import context_to_event, EventAdapterError
        for bad in ("not a context", None, 123):
            with pytest.raises(EventAdapterError):
                context_to_event(bad)  # type: ignore[arg-type]


# ============================================================
# 3. lifecycle_bridge
# ============================================================
class TestLifecycleBridge:
    def test_v2_runs_full_lifecycle(self, bridge_manager):
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        result_ctx = run_with_context(
            bridge_manager, _make_v2(), lifecycle_name="v2_bridge",
        )
        assert result_ctx.schema_version == "2.0"
        assert result_ctx.state == "success"
        assert result_ctx.is_terminal
        assert result_ctx.outputs["lifecycle"]["name"] == "v2_bridge"
        assert result_ctx.outputs["lifecycle"]["status"] == "success"

    def test_v1_runs_full_lifecycle_unchanged(self, bridge_manager):
        from src.runtime.lifecycle.lifecycle_bridge import run_with_context
        result_ctx = run_with_context(
            bridge_manager, _make_v1(), lifecycle_name="v1_bridge",
        )
        assert result_ctx.schema_version == "1.0"
        assert result_ctx.state == "success"
        assert result_ctx.outputs["lifecycle"]["name"] == "v1_bridge"

    def test_garbage_still_raises_lifecycle_bridge_error(self, bridge_manager):
        from src.runtime.lifecycle.lifecycle_bridge import (
            LifecycleBridgeError,
            run_with_context,
        )
        with pytest.raises(LifecycleBridgeError):
            run_with_context(bridge_manager, "not a context")  # type: ignore[arg-type]

    def test_object_missing_mutation_capability_rejected(self, bridge_manager):
        """缺 mark_success/mark_failed 能力（仅有 schema_version）仍被拒绝。"""
        from src.runtime.lifecycle.lifecycle_bridge import (
            LifecycleBridgeError,
            run_with_context,
        )

        class ReadOnlyContext:
            schema_version = "2.0"

            def with_update(self, **kw):
                return self

        with pytest.raises(LifecycleBridgeError):
            run_with_context(bridge_manager, ReadOnlyContext())  # type: ignore[arg-type]


# ============================================================
# 4. context_storage 门禁（Phase 1 联动，此处锁定能力判断边界）
# ============================================================
class TestStorageGate:
    def test_save_accepts_both_schema_versions(self, tmp_path):
        from src.runtime.context_storage import RuntimeContextStorage
        storage = RuntimeContextStorage(base_dir=str(tmp_path / "gate_store"))
        r1 = storage.save(_make_v1())
        r2 = storage.save(_make_v2())
        assert r1["saved"] and r2["saved"]
        assert storage.load("v1_gate_001").schema_version == "1.0"
        assert storage.load("v2_gate_001").schema_version == "2.0"

    def test_save_object_with_schema_but_no_to_dict_rejected(self, tmp_path):
        from src.runtime.context_storage import RuntimeContextStorage

        class FakeV2:
            schema_version = "2.0"
            lifecycle_id = "fake_001"

        storage = RuntimeContextStorage(base_dir=str(tmp_path / "gate_store2"))
        with pytest.raises(ValueError):
            storage.save(FakeV2())  # type: ignore[arg-type]
