# -*- coding: utf-8 -*-
"""
v1.1 Phase 2.1 验收测试: Reflection 周期任务接线。

覆盖:
1. tick 触发: RuntimeCore._tick_background_host 惰性构造宿主并 tick
   （config 默认关闭 → 不触发）。
2. Reflection 正常运行: 注入事件 → 反思产生结果事件（reflection_id/insight_count）。
3. Reflection 失败 fail-soft: adapter 抛错 → 错误事件, 不向上抛。
4. 无事件 skip: 空缓冲 → skip 事件, 计数正确。
5. 不产生未经治理的状态修改: 全程无 state_mutations 写入。
6. 默认任务工厂注册 ReflectionLifecycleTask。

隔离: 假 adapter / 假宿主 / 审计 env 重定向; 遵守 conftest 陷阱规则。
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


class _FakeResult:
    def __init__(self, reflection_id="refl_1"):
        self.reflection_id = reflection_id
        self.reflection_type = "event"
        self.source_event_ids = ["evt_1"]
        self.insight_count = 2
        self.suggestion_count = 1
        self.confidence = 0.8
        self.evidence_strength = 0.7


from src.runtime.integration.adapters.base import BaseAdapter


class _SpyAdapter(BaseAdapter):
    def __init__(self, results=None, raise_exc=None):
        super().__init__(name="spy_reflection", owner="test")
        self.results = results if results is not None else [_FakeResult()]
        self.raise_exc = raise_exc
        self.calls = []

    def reflect(self, events, reflection_type=None):
        self.calls.append(list(events))
        if self.raise_exc is not None:
            raise self.raise_exc
        return list(self.results)


def _make_task(adapter):
    from src.runtime.integration.tasks.reflection_lifecycle_task import (
        ReflectionLifecycleTask,
    )

    return ReflectionLifecycleTask(adapter=adapter)


def _make_event(event_id="evt_1"):
    from src.runtime.integration.integration_event import make_integration_event

    return make_integration_event(
        event_type="memory.created",
        source="test",
        payload={"event_id": event_id},
        related_ids=[event_id],
    )


# ------------------------------------------------------------
# 1. tick 触发（默认关闭 + 显式开启）
# ------------------------------------------------------------
def test_background_host_tick_disabled_by_default(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    inst = _rcls.__new__(_rcls)
    inst.config = {}

    class _FakeHost:
        def start(self):
            return True

        def tick(self):
            raise AssertionError("默认关闭时不得 tick")

    host_mod = importlib.import_module("src.runtime.integration.runtime_integration_host")
    monkeypatch.setattr(host_mod, "RuntimeIntegrationHost", lambda **kw: _FakeHost())
    inst._tick_background_host()
    assert not hasattr(inst, "_integration_host"), "默认关闭时不得构造宿主"


def test_background_host_tick_triggers(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    inst = _rcls.__new__(_rcls)
    inst.config = {"integration_host_enabled": True}

    class _FakeHost:
        def __init__(self):
            self.start_calls = 0
            self.tick_calls = 0

        def start(self):
            self.start_calls += 1
            return True

        def tick(self):
            self.tick_calls += 1
            return []

    host_mod = importlib.import_module("src.runtime.integration.runtime_integration_host")
    fake_host = _FakeHost()
    monkeypatch.setattr(host_mod, "RuntimeIntegrationHost", lambda **kw: fake_host)

    inst._tick_background_host()
    inst._tick_background_host()
    assert fake_host.start_calls == 1, "宿主应惰性构造一次"
    assert fake_host.tick_calls == 2, "每次 tick 应触发宿主 tick"


def test_background_host_tick_fail_soft(monkeypatch):
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    inst = _rcls.__new__(_rcls)
    inst.config = {"integration_host_enabled": True}

    class _BoomHost:
        def start(self):
            return True

        def tick(self):
            raise RuntimeError("background boom")

    host_mod = importlib.import_module("src.runtime.integration.runtime_integration_host")
    monkeypatch.setattr(host_mod, "RuntimeIntegrationHost", lambda **kw: _BoomHost())
    inst._tick_background_host()  # 不得抛异常（隔离）


# ------------------------------------------------------------
# 2. Reflection 正常运行 + 3. 失败 fail-soft + 4. skip
# ------------------------------------------------------------
def test_reflection_task_runs_with_events(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    adapter = _SpyAdapter()
    task = _make_task(adapter)
    e1 = _make_event("evt_1")
    e2 = _make_event("evt_2")
    assert task.push_events([e1, e2]) == 2

    event = task._do_execute(SimpleNamespace(tick=1))
    assert event.event_type == "integration.reflection.completed"
    assert len(adapter.calls) == 1
    assert len(adapter.calls[0]) == 2, "注入的事件应全部进入反思"
    assert {e.event_id for e in adapter.calls[0]} == {e1.event_id, e2.event_id}
    assert event.payload["reflection_id"] == "refl_1"
    assert event.payload["insight_count"] == 2
    assert event.payload["suggestion_count"] == 1
    assert task.tick_reflection_count == 1
    assert task.tick_skipped_count == 0
    # 不产生未经治理的状态修改
    assert not (tmp_path / "audit.jsonl").exists()


def test_reflection_task_skips_without_events():
    task = _make_task(_SpyAdapter())
    event = task._do_execute(SimpleNamespace(tick=2))
    assert event.payload.get("status") == "skip"
    assert event.payload.get("reason") == "no_events"
    assert task.tick_skipped_count == 1
    assert task.tick_reflection_count == 0


def test_reflection_task_error_is_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    adapter = _SpyAdapter(raise_exc=RuntimeError("reflection boom"))
    task = _make_task(adapter)
    task.push_events([_make_event("evt_1")])
    event = task._do_execute(SimpleNamespace(tick=3))
    assert event.payload.get("status") == "error"
    assert "reflection boom" in event.payload.get("error", "")
    assert task.tick_error_count == 1
    assert not (tmp_path / "audit.jsonl").exists()


# ------------------------------------------------------------
# 5. 默认任务工厂注册 ReflectionLifecycleTask
# ------------------------------------------------------------
def test_default_factory_registers_reflection_task():
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    from src.runtime.lifecycle.internal.clock import FrozenClock

    mgr = LifecycleManager(clock=FrozenClock(initial=0.0), name="v21_host")
    host = RuntimeIntegrationHost(lifecycle_manager=mgr)
    count = host.register_default_tasks()
    assert count >= 7
    ids = set(mgr.registry.task_ids())
    assert "reflection_lifecycle_task" in ids
    assert "emotion.reflection" in ids
