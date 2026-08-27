# -*- coding: utf-8 -*-
"""v1.5-T4: 事件 schema 扩展测试。

验证 interaction_recorder 写出的记忆记录携带四元元数据
（origin / event_class / self_involvement / authored），
且不改变既有字段与写入行为。
"""
import sys
import types

import pytest

from src.runtime.interaction_recorder import InteractionRecorder


class _FakeCtx:
    def __init__(self, user_message, reply):
        self.outputs = {
            "snapshot": {
                "user_message": user_message,
                "reply": reply,
            }
        }
        self.lifecycle_id = "test-lifecycle"
        self.session_id = "test-session"
        self.state = "success"
        self.inputs = {"user_id": "366648462"}


@pytest.fixture(autouse=True)
def _patch_bridge_unavailable(monkeypatch):
    """照旧测试模式：替换 src.runtime.runtime_bridge 模块，使 recorder 走 Provider fallback。"""

    def _bad_import(*_a, **_k):
        raise RuntimeError("v1.5-T4: RuntimeBridge disabled")

    fake_bridge = types.ModuleType("src.runtime.runtime_bridge")
    fake_bridge.get_runtime_bridge = _bad_import
    fake_bridge.RuntimeBridge = type("RuntimeBridge", (), {})
    monkeypatch.setitem(sys.modules, "src.runtime.runtime_bridge", fake_bridge)


def _record_and_fetch():
    recorder = InteractionRecorder()
    ctx = _FakeCtx(
        user_message="我最近开始学画画",
        reply="那太好了！画画是一种很好的表达方式。",
    )
    memory_id = recorder.record(ctx)
    assert memory_id is not None, "record() 必须返回 memory_id"

    from src.memory.memory_provider import MemoryProvider
    store = MemoryProvider.get_store()
    all_recs = store.load() if hasattr(store, "load") else []
    assert all_recs, "store 中应有记录"
    return all_recs[-1]


def test_event_schema_four_fields(monkeypatch):
    """四元元数据字段必须存在且取值正确。"""
    rec = _record_and_fetch()
    md = rec.get("metadata") or {}
    assert md.get("origin") == "user"
    assert md.get("event_class") == "interaction"
    assert md.get("self_involvement") == "witness"
    assert md.get("authored") is True


def test_event_schema_existing_fields_preserved(monkeypatch):
    """既有字段不受影响：role/content/user_id/memory_type/source 保持不变。"""
    rec = _record_and_fetch()
    assert rec.get("role") == "user"
    assert rec.get("user_id") == "366648462"
    assert "画画" in rec.get("content", "")
    md = rec.get("metadata") or {}
    assert md.get("memory_type") == "user_experience"
    assert md.get("source") == "runtime_pipeline"


def test_event_schema_record_still_persists(monkeypatch):
    """schema 扩展不破坏写入链路：record 仍返回 memory_id。"""
    recorder = InteractionRecorder()
    ctx = _FakeCtx("今天天气不错", "是呀，适合散步。")
    memory_id = recorder.record(ctx)
    assert memory_id is not None
    assert memory_id.startswith("mem_")
