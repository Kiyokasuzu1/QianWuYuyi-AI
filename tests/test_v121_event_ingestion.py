# -*- coding: utf-8 -*-
"""
v1.2.1 Production Hardening — 测试 B: 业务事件 EventLog 接线。

验证:
  真实用户交互(publish_event: message.received / memory.created)
    → RuntimeBridge._on_any_event
    → RuntimeIntegrationHost.publish_business_event
    → Host EventLog → Reflection Cycle → Narrative

Test:
1. 关注事件（memory.created / message.received）被转发到 Host EventLog。
2. 非关注事件不转发。
3. 无 IntegrationHost 时静默跳过（fail-soft）。
4. 转发 payload 结构完整（event_type + payload 内容保留）。
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

from src.events.events import MemoryCreatedEvent, MessageReceivedEvent


class _FakeHost:
    def __init__(self):
        self.received = []

    def publish_business_event(self, raw_event):
        self.received.append(raw_event)
        return SimpleNamespace(event_type="integration.memory.created")


class _FakeCore:
    def __init__(self, host):
        self._integration_host = host
        self.injected = []

    def inject_event(self, event_type, event_data):
        self.injected.append((event_type, event_data))


class _FakeBus:
    """捕获 subscribe_all 注册的处理器。"""

    def __init__(self):
        self.handler = None

    def subscribe_all(self, handler):
        self.handler = handler


def _make_bridge(monkeypatch, core):
    mod = importlib.import_module("src.runtime.runtime_bridge")
    bus = _FakeBus()
    monkeypatch.setattr(mod, "get_event_bus", lambda: bus)
    bridge = mod.RuntimeBridge()
    bridge._runtime_core = core
    bridge._register_event_handlers()
    return bridge, bus.handler


# ------------------------------------------------------------
# Test 1: 关注事件转发
# ------------------------------------------------------------
def test_interesting_event_forwarded_to_host(monkeypatch):
    host = _FakeHost()
    core = _FakeCore(host)
    bridge, handler = _make_bridge(monkeypatch, core)
    ev = MemoryCreatedEvent(
        event_type="memory.created",
        user_id="u1",
        content="用户喜欢科幻",
        source="orchestrator",
    )
    handler(ev)
    assert len(host.received) == 1
    fwd = host.received[0]
    assert isinstance(fwd, dict)
    assert fwd["event_type"] == "memory.created"
    assert fwd["payload"].get("content") == "用户喜欢科幻", "payload 内容必须保留"
    assert core.injected, "原有 inject_event 行为不变"


# ------------------------------------------------------------
# Test 2: 非关注事件不转发
# ------------------------------------------------------------
def test_uninteresting_event_not_forwarded(monkeypatch):
    host = _FakeHost()
    core = _FakeCore(host)
    bridge, handler = _make_bridge(monkeypatch, core)
    handler(SimpleNamespace(event_type="unrelated.event"))
    assert host.received == []
    assert core.injected == []


# ------------------------------------------------------------
# Test 3: 无 Host 时 fail-soft
# ------------------------------------------------------------
def test_no_host_forward_fail_soft(monkeypatch):
    core = _FakeCore(None)
    bridge, handler = _make_bridge(monkeypatch, core)
    handler(MemoryCreatedEvent(event_type="memory.created", user_id="u1", content="x"))
    assert core.injected, "无 Host 时 inject_event 仍正常"


# ------------------------------------------------------------
# Test 4: 转发异常被隔离（宿主方法抛错不影响 inject_event）
# ------------------------------------------------------------
def test_forward_exception_isolated(monkeypatch):
    class _BoomHost:
        def publish_business_event(self, raw_event):
            raise RuntimeError("boom")

    core = _FakeCore(_BoomHost())
    bridge, handler = _make_bridge(monkeypatch, core)
    handler(MessageReceivedEvent(event_type="message.received", user_id="u1", content="hi"))
    assert core.injected, "转发异常不得影响 inject_event"


# ------------------------------------------------------------
# Test 5: on_user_message 转发 message.received 到 Host
# ------------------------------------------------------------
def test_on_user_message_forwards_to_host(monkeypatch):
    import importlib
    from types import SimpleNamespace

    mod = importlib.import_module("src.runtime.runtime_bridge")
    host = _FakeHost()
    core = _FakeCore(host)
    bridge = mod.RuntimeBridge()
    bridge._runtime_core = core
    bridge.on_user_message("u1", "晚上好")
    assert core.injected and core.injected[0][0] == "user.input"
    assert len(host.received) == 1
    fwd = host.received[0]
    assert fwd["event_type"] == "message.received"
    assert fwd["payload"]["content"] == "晚上好"
    assert fwd["payload"]["user_id"] == "u1"


# ------------------------------------------------------------
# Test 6: on_user_message 无 Host 时 fail-soft
# ------------------------------------------------------------
def test_on_user_message_no_host_fail_soft(monkeypatch):
    import importlib

    mod = importlib.import_module("src.runtime.runtime_bridge")
    core = _FakeCore(None)
    bridge = mod.RuntimeBridge()
    bridge._runtime_core = core
    bridge.on_user_message("u1", "hi")
    assert core.injected, "无 Host 时 user.input 注入不受影响"


# ------------------------------------------------------------
# Test 7: Host 侧 publish_business_event → EventLog（端到端前半段）
# ------------------------------------------------------------
def test_host_publish_business_event_reaches_event_log():
    import time
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    host = RuntimeIntegrationHost(
        name="v121_host", auto_create_manager=False, auto_register_tasks=False,
        enable_persistence=False,
    )
    host.start()
    host.publish_business_event({
        "event_id": "", "event_type": "message.received", "source": "orchestrator",
        "timestamp": float(time.time()), "payload": {"content": "hi"}, "metadata": {},
    })
    types = [e.event_type for e in host._event_log.events()]
    assert "message.received" in types, "业务事件必须进入 Host EventLog"
    host.stop()
