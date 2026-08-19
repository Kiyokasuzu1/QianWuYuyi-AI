# -*- coding: utf-8 -*-
"""
tests/test_runtime_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.1 —— RuntimeDashboardProvider 单元测试。

覆盖:
  1. 不修改 Runtime 状态(只读保证)
  2. 数据结构正确
  3. Runtime 不存在时 fallback
  4. API 响应正确
  5. 不 import 任何 src/runtime/** 业务模块
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers
# =====================================================================

class _FakeRuntimeProvider:
    def __init__(self, *, online: bool = True, runtime: Dict[str, bool] = None,
                 bridge_error: str = None):
        self._online = online
        self._runtime = runtime or {"initialized": online, "is_running": online}
        self._bridge_error = bridge_error
        self._calls: List[str] = []

    def get_status(self) -> Dict[str, Any]:
        self._calls.append("get_status")
        return {
            "online": self._online,
            "runtime": dict(self._runtime),
            "bridge_error": self._bridge_error,
        }


class _FakeAdapter:
    def __init__(self, name: str, available: bool = True, error_count: int = 0,
                 emitted_count: int = 0, last_error: str = "", last_event: str = ""):
        self._name = name
        self._available = available
        self._error_count = error_count
        self._emitted_count = emitted_count
        self._last_error = last_error
        self._last_event = last_event

    def describe(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "owner": "fake_owner",
            "available": self._available,
            "call_count": 0,
            "error_count": self._error_count,
            "emitted_count": self._emitted_count,
            "last_error": self._last_error,
            "last_emit_event_id": self._last_event,
        }


class _FakeHost:
    def __init__(self, *, state: str = "running", started_at: float = None,
                 tick_count: int = 0, adapters: Dict[str, Any] = None):
        self._state = state
        self._started_at = started_at
        self._tick_count = tick_count
        self._adapters = adapters or {}

    @property
    def state(self) -> str: return self._state

    @property
    def started_at(self): return self._started_at

    @property
    def tick_count(self) -> int: return self._tick_count

    @property
    def adapters(self) -> Dict[str, Any]: return dict(self._adapters)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.runtime_dashboard_provider import (
        reset_runtime_dashboard_provider_for_testing,
    )
    reset_runtime_dashboard_provider_for_testing()
    yield
    reset_runtime_dashboard_provider_for_testing()


# =====================================================================
# 1. Read-Only
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        """禁止 import src/runtime/** 业务模块。"""
        from src.admin import runtime_dashboard_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.runtime.core", "import src.runtime.core",
            "from src.runtime.self_model", "import src.runtime.self_model",
            "from src.runtime.lifecycle", "import src.runtime.lifecycle",
            "from src.runtime.reflection", "import src.runtime.reflection",
            "from src.runtime.initiative", "import src.runtime.initiative",
            "from src.runtime.goal", "import src.runtime.goal",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_provider_does_not_call_mutating_methods(self, reset_singletons):
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider

        rp = _FakeRuntimeProvider()
        host = _FakeHost(adapters={"a": _FakeAdapter("a")})
        provider = RuntimeDashboardProvider(runtime_provider=rp, host=host)
        provider.get_runtime_status()
        provider.get_lifecycle_tasks()
        provider.get_tick_history()

        # 检查没有调用任何 setter
        forbidden = [c for c in rp._calls if c.startswith(("set_", "update_", "save_", "delete_"))]
        assert not forbidden, f"RuntimeProvider 不应被写入: {forbidden}"


# =====================================================================
# 2. Data Structure
# =====================================================================

class TestDataStructure:
    def test_status_has_all_required_fields(self, reset_singletons):
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider

        rp = _FakeRuntimeProvider()
        host = _FakeHost(state="running", started_at=1700000000.0, tick_count=42)
        provider = RuntimeDashboardProvider(runtime_provider=rp, host=host)
        status = provider.get_runtime_status()
        for k in ("initialized", "running", "uptime", "current_state",
                  "online", "tick_count", "bridge_error", "available", "fallback"):
            assert k in status, f"missing: {k}"

    def test_tasks_has_all_required_fields(self, reset_singletons):
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider

        host = _FakeHost(adapters={
            "memory": _FakeAdapter("memory", available=True, emitted_count=5),
            "emotion": _FakeAdapter("emotion", available=False, error_count=2),
        })
        provider = RuntimeDashboardProvider(host=host)
        result = provider.get_lifecycle_tasks()
        assert "tasks" in result
        assert len(result["tasks"]) == 2
        for t in result["tasks"]:
            for k in ("task_name", "status", "last_tick_event_id",
                      "emitted_count", "error_count", "last_error", "owner", "fallback"):
                assert k in t, f"task missing: {k}"

    def test_tick_history_uses_hub(self, reset_singletons):
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider

        provider = RuntimeDashboardProvider()
        result = provider.get_tick_history(limit=5)
        assert "ticks" in result
        assert isinstance(result["ticks"], list)
        assert result["available"] is True


# =====================================================================
# 3. Fallback
# =====================================================================

class TestFallback:
    def test_runtime_unavailable_returns_fallback(self, reset_singletons):
        """RuntimeProvider 返回 offline 时,应进入 fallback 状态。"""
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider
        rp = _FakeRuntimeProvider(online=False, runtime={"initialized": False, "is_running": False})
        provider = RuntimeDashboardProvider(runtime_provider=rp, host=None)
        status = provider.get_runtime_status()
        assert status["online"] is False
        assert status["uptime"] is None

    def test_runtime_provider_lazy_load_failure_returns_fallback(self, reset_singletons):
        """注入非 RuntimeProvider 类型的 mock(返回 online=False)验证 fallback 链路。"""
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider
        rp = _FakeRuntimeProvider(online=False, runtime={"initialized": False, "is_running": False},
                                  bridge_error="bridge_missing")
        provider = RuntimeDashboardProvider(runtime_provider=rp, host=None)
        status = provider.get_runtime_status()
        assert status["online"] is False
        assert status["bridge_error"] == "bridge_missing"

    def test_host_unavailable_returns_fallback_for_tasks(self, reset_singletons):
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider
        provider = RuntimeDashboardProvider(runtime_provider=_FakeRuntimeProvider(), host=None)
        result = provider.get_lifecycle_tasks()
        assert result["fallback"] is True
        assert result["tasks"] == []


# =====================================================================
# 4. API Response
# =====================================================================

class TestApi:
    def _make_client(self):
        from src.admin.dashboard.runtime_router import runtime_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(runtime_v2_bp)
        return app.test_client()

    def test_status_endpoint(self, reset_singletons):
        from src.admin import runtime_dashboard_provider as pmod
        rp = _FakeRuntimeProvider()
        host = _FakeHost(state="running")
        pmod._provider_instance = pmod.RuntimeDashboardProvider(runtime_provider=rp, host=host)

        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "data" in body
        assert "timestamp" in body

    def test_tasks_endpoint(self, reset_singletons):
        from src.admin import runtime_dashboard_provider as pmod
        host = _FakeHost(adapters={"a": _FakeAdapter("a")})
        pmod._provider_instance = pmod.RuntimeDashboardProvider(host=host)

        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/tasks")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "tasks" in body["data"]

    def test_events_endpoint(self, reset_singletons):
        from src.admin import runtime_dashboard_provider as pmod
        pmod._provider_instance = pmod.RuntimeDashboardProvider()
        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/events?limit=5")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "ticks" in body["data"]

    def test_fallback_when_provider_raises(self, reset_singletons):
        from src.admin import runtime_dashboard_provider as pmod

        class _Boom:
            def get_runtime_status(self):
                raise RuntimeError("boom")
            def get_lifecycle_tasks(self):
                raise RuntimeError("boom")
            def get_tick_history(self, limit=20):
                raise RuntimeError("boom")
            def get_integration_events(self, limit=20):
                raise RuntimeError("boom")

        pmod._provider_instance = _Boom()
        client = self._make_client()
        for ep in ("/runtime/status", "/runtime/tasks", "/runtime/events"):
            resp = client.get("/api/dashboard/v2" + ep)
            assert resp.status_code == 200
            body = resp.get_json()
            assert body["ok"] is True
            assert body.get("fallback") is True
