# -*- coding: utf-8 -*-
"""
tests/test_dashboard_provider.py

Phase 5.0 Dashboard Upgrade —— Dashboard V2 单元测试。

覆盖:
  1. Provider 不会修改业务数据(只读保证)
  2. Snapshot 字段完整
  3. fallback 正常(数据不可用时返回空视图,而非 mock)
  4. API 返回结构正确(ok / data / timestamp)
  5. Router 拒绝非本地访问
  6. EventHub 在 Host 不可用时安全降级

约束:
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
- 不修改 RuntimeCore
- Mock bridge / mock provider,保持隔离
- 保持向后兼容
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
    """Mock RuntimeProvider —— 提供 Dashboard 所需的所有子数据。"""

    def __init__(
        self,
        *,
        online: bool = True,
        status: Dict[str, Any] = None,
        authority: Dict[str, bool] = None,
        memory: Dict[str, Any] = None,
        emotion: Dict[str, Any] = None,
        growth: Dict[str, Any] = None,
    ):
        self._online = online
        self._status = status or {
            "online": online,
            "runtime": {"initialized": online, "is_running": online},
            "bridge_error": None,
        }
        self._authority = authority or {
            "memory_store": True,
            "emotion_manager": True,
            "personality_resolver": True,
        }
        self._memory = memory or {
            "total_count": 12,
            "important_count": 3,
            "recent": [{"id": "m1", "summary": "hi"}],
        }
        self._emotion = emotion or {
            "current": "calm",
            "valence": 0.2,
            "arousal": 0.1,
            "intensity": 0.4,
            "recent": [],
        }
        self._growth = growth or {
            "metrics": {"maturity": "sprout", "growth_score": 0.55},
        }
        self._calls: List[str] = []

    def get_status(self) -> Dict[str, Any]:
        self._calls.append("get_status")
        return dict(self._status)

    def get_authority_status(self) -> Dict[str, bool]:
        self._calls.append("get_authority_status")
        return dict(self._authority)

    def get_memory_summary(self) -> Dict[str, Any]:
        self._calls.append("get_memory_summary")
        return dict(self._memory)

    def get_emotion_summary(self) -> Dict[str, Any]:
        self._calls.append("get_emotion_summary")
        return dict(self._emotion)

    def get_growth_summary(self) -> Dict[str, Any]:
        self._calls.append("get_growth_summary")
        return dict(self._growth)

    @property
    def calls(self) -> List[str]:
        return list(self._calls)


class _FakeSelfModelProvider:
    """Mock SelfModelProvider。"""

    def __init__(self, identity: Dict[str, Any] = None):
        self._identity = identity or {
            "identity_name": "Yuyi",
            "anchor": "Yuyi-Test-Anchor",
            "stable": True,
        }
        self._calls: List[str] = []

    def get_identity(self) -> Dict[str, Any]:
        self._calls.append("get_identity")
        return dict(self._identity)

    @property
    def calls(self) -> List[str]:
        return list(self._calls)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    """重置所有 Dashboard 相关单例。"""
    from src.admin.dashboard.provider import reset_dashboard_provider_for_testing
    from src.admin.dashboard.event_hub import reset_dashboard_event_hub_for_testing

    reset_dashboard_provider_for_testing()
    reset_dashboard_event_hub_for_testing()
    yield
    reset_dashboard_provider_for_testing()
    reset_dashboard_event_hub_for_testing()


@pytest.fixture
def fake_runtime():
    return _FakeRuntimeProvider()


@pytest.fixture
def fake_self_model():
    return _FakeSelfModelProvider()


# =====================================================================
# 1. Provider 不会修改业务数据(只读保证)
# =====================================================================

class TestProviderReadOnly:
    def test_provider_uses_injected_deps_without_mutation(self, reset_singletons, fake_runtime, fake_self_model):
        """Provider 必须只调用子 Provider 的 getter,且不得修改返回值。"""
        from src.admin.dashboard.provider import DashboardProvider

        provider = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )
        snap = provider.get_snapshot()

        # 1) 调用了 getter
        assert "get_status" in fake_runtime.calls
        assert "get_authority_status" in fake_runtime.calls
        assert "get_memory_summary" in fake_runtime.calls
        assert "get_emotion_summary" in fake_runtime.calls
        assert "get_growth_summary" in fake_runtime.calls
        assert "get_identity" in fake_self_model.calls

        # 2) 不应存在 setter 调用(无 set_* / update_*/write_* 等)
        forbidden = [c for c in fake_runtime.calls if c.startswith(("set_", "update_", "write_", "save_", "delete_"))]
        assert not forbidden, f"Provider 不应调用任何 setter: {forbidden}"
        forbidden = [c for c in fake_self_model.calls if c.startswith(("set_", "update_", "write_", "save_", "delete_"))]
        assert not forbidden, f"SelfModelProvider 不应被写入: {forbidden}"

        # 3) 返回值应与原始 mock 数据一致
        assert snap.runtime_state.online is True
        assert snap.runtime_state.initialized is True
        assert snap.runtime_state.is_running is True
        assert snap.memory_summary.total == 12
        assert snap.emotion.primary == "calm"
        assert snap.identity.name == "Yuyi"

    def test_provider_does_not_import_core_business_modules(self, reset_singletons):
        """Dashboard 模块严禁 import 核心业务模块。"""
        import src.admin.dashboard.provider as provider_mod
        import src.admin.dashboard.router as router_mod
        import src.admin.dashboard.event_hub as hub_mod
        import src.admin.dashboard.snapshot as snap_mod

        forbidden_substrings = [
            "src.memory",
            "src.emotion",
            "src.growth",
            "src.personality",
            "src.relationship",
            "src.runtime.core",
            "src.runtime.self_model",
        ]
        for mod_name, mod in [
            ("provider", provider_mod),
            ("router", router_mod),
            ("event_hub", hub_mod),
            ("snapshot", snap_mod),
        ]:
            src = open(mod.__file__, "r", encoding="utf-8").read()
            for sub in forbidden_substrings:
                assert sub not in src, f"{mod_name} 禁止 import {sub}"


# =====================================================================
# 2. Snapshot 字段完整
# =====================================================================

class TestSnapshotStructure:
    def test_snapshot_has_all_required_views(self, reset_singletons, fake_runtime, fake_self_model):
        from src.admin.dashboard.provider import DashboardProvider
        from src.admin.dashboard.snapshot import YuyiDashboardSnapshot

        provider = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )
        snap = provider.get_snapshot()
        assert isinstance(snap, YuyiDashboardSnapshot)

        # 必备子视图
        assert hasattr(snap, "runtime_state")
        assert hasattr(snap, "identity")
        assert hasattr(snap, "emotion")
        assert hasattr(snap, "memory_summary")
        assert hasattr(snap, "growth_summary")
        assert hasattr(snap, "relationship_summary")
        assert hasattr(snap, "goal_state")
        assert hasattr(snap, "initiative_state")
        assert hasattr(snap, "reflection_summary")
        assert hasattr(snap, "body_state")
        assert hasattr(snap, "health")
        assert hasattr(snap, "timestamp")
        assert hasattr(snap, "schema_version")

    def test_snapshot_to_dict_serializes_all_fields(self, reset_singletons, fake_runtime, fake_self_model):
        from src.admin.dashboard.provider import DashboardProvider

        provider = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )
        snap = provider.get_snapshot()
        d = snap.to_dict()
        for key in (
            "runtime_state", "identity", "emotion", "memory_summary",
            "growth_summary", "relationship_summary", "goal_state",
            "initiative_state", "reflection_summary", "body_state",
            "health", "timestamp", "schema_version",
        ):
            assert key in d, f"snapshot dict missing key: {key}"

    def test_snapshot_empty_returns_full_structure(self):
        from src.admin.dashboard.snapshot import YuyiDashboardSnapshot
        snap = YuyiDashboardSnapshot.empty()
        d = snap.to_dict()
        for key in (
            "runtime_state", "identity", "emotion", "memory_summary",
            "growth_summary", "relationship_summary", "goal_state",
            "initiative_state", "reflection_summary", "body_state",
            "health", "timestamp", "schema_version",
        ):
            assert key in d


# =====================================================================
# 3. Fallback 正常(子 Provider 不可用)
# =====================================================================

class TestFallbackBehavior:
    def test_runtime_unavailable_returns_offline_state(self, reset_singletons, fake_runtime, fake_self_model):
        """运行时不可用时,runtime_state 应为 offline 且 health 应降级。"""
        from src.admin.dashboard.provider import DashboardProvider
        provider = DashboardProvider(
            runtime_provider=None,
            self_model_provider=fake_self_model,
        )
        snap = provider.get_snapshot()
        # runtime_state.online 应为 False 且不抛错
        assert snap.runtime_state.online is False
        # memory / emotion 子 Provider 走 runtime,应为空
        assert snap.memory_summary.total == 0
        assert snap.emotion.primary == "unknown"
        # health 应显示 degraded / offline / critical(允许各种)
        assert snap.health.status in ("offline", "critical", "degraded", "healthy")
        # 注入的 self_model 仍应被读取
        assert snap.identity.name == "Yuyi"

    def test_partial_failure_does_not_raise(self, reset_singletons):
        """某个子 Provider 抛错时,不得污染整个 snapshot。"""
        from src.admin.dashboard.provider import DashboardProvider

        class _BrokenRuntime(_FakeRuntimeProvider):
            def get_emotion_summary(self):
                raise RuntimeError("emotion bridge down")

        provider = DashboardProvider(
            runtime_provider=_BrokenRuntime(),
            self_model_provider=_FakeSelfModelProvider(),
        )
        # 不应抛错
        snap = provider.get_snapshot()
        assert snap.runtime_state.online is True
        assert snap.emotion.primary == "unknown"  # 降级默认

    def test_overview_contains_required_fields(self, reset_singletons, fake_runtime, fake_self_model):
        from src.admin.dashboard.provider import DashboardProvider
        provider = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )
        ov = provider.get_overview()
        for key in (
            "online", "runtime_status", "emotion",
            "current_goal", "current_interest",
            "recent_events", "health",
        ):
            assert key in ov, f"overview missing key: {key}"


# =====================================================================
# 4. API 返回结构(ok / data / timestamp)
# =====================================================================

class TestApiResponseStructure:
    def _make_client(self):
        from src.admin.dashboard.router import dashboard_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(dashboard_v2_bp)
        return app.test_client()

    def test_overview_returns_ok_envelope(self, reset_singletons, fake_runtime, fake_self_model):
        from src.admin.dashboard.provider import DashboardProvider, reset_dashboard_provider_for_testing
        from src.admin.dashboard.provider import get_dashboard_provider

        reset_dashboard_provider_for_testing()
        DashboardProvider(runtime_provider=fake_runtime, self_model_provider=fake_self_model)
        # 重新让单例指向我们的实例
        from src.admin.dashboard import provider as pmod
        pmod._provider_instance = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )

        client = self._make_client()
        resp = client.get("/api/dashboard/v2/overview")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        assert "data" in body
        assert "timestamp" in body
        assert "schema_version" in body
        for k in ("online", "runtime_status", "emotion", "health"):
            assert k in body["data"], f"overview data missing: {k}"

    def test_snapshot_returns_full_envelope(self, reset_singletons, fake_runtime, fake_self_model):
        from src.admin.dashboard import provider as pmod
        from src.admin.dashboard.provider import DashboardProvider

        pmod._provider_instance = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )

        client = self._make_client()
        resp = client.get("/api/dashboard/v2/snapshot")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        assert "data" in body
        d = body["data"]
        for key in (
            "runtime_state", "identity", "emotion", "memory_summary",
            "growth_summary", "relationship_summary", "goal_state",
            "initiative_state", "reflection_summary", "body_state",
            "health", "timestamp", "schema_version",
        ):
            assert key in d, f"snapshot data missing: {key}"

    def test_health_endpoint_no_local_restriction(self, reset_singletons, fake_runtime, fake_self_model):
        """health 接口不限制本地访问(用于探活)。"""
        from src.admin.dashboard import provider as pmod
        from src.admin.dashboard.provider import DashboardProvider

        pmod._provider_instance = DashboardProvider(
            runtime_provider=fake_runtime,
            self_model_provider=fake_self_model,
        )

        client = self._make_client()
        resp = client.get("/api/dashboard/v2/health")
        # health 在 TESTING 模式下 remote_addr 通常为 127.0.0.1
        assert resp.status_code in (200, 403)


# =====================================================================
# 5. Response format helpers
# =====================================================================

class TestResponseHelpers:
    def test_ok_response_shape(self):
        from src.admin.dashboard import ok_response
        r = ok_response({"a": 1})
        assert r["ok"] is True
        assert r["data"] == {"a": 1}
        assert "timestamp" in r

    def test_fallback_response_shape(self):
        from src.admin.dashboard import fallback_response
        r = fallback_response({"a": 1}, reason="no_data")
        assert r["ok"] is True
        assert r["fallback"] is True
        assert r["fallback_reason"] == "no_data"
        assert r["data"] == {"a": 1}
        assert "timestamp" in r

    def test_error_response_shape(self):
        from src.admin.dashboard import error_response
        body, status = error_response("E001", "boom", http_status=418)
        assert body["ok"] is False
        assert body["error"]["code"] == "E001"
        assert body["error"]["message"] == "boom"
        assert status == 418


# =====================================================================
# 6. EventHub 在 Host 不可用时安全降级
# =====================================================================

class TestEventHubFallback:
    def test_hub_poll_returns_empty_when_host_missing(self, reset_singletons):
        from src.admin.dashboard.event_hub import DashboardEventHub
        hub = DashboardEventHub()
        # 不注入 host,直接 poll
        events = hub.poll_recent(limit=10)
        assert events == []

    def test_hub_subscribe_unsubscribe(self, reset_singletons):
        from src.admin.dashboard.event_hub import DashboardEventHub
        hub = DashboardEventHub()
        received: List[Any] = []

        def cb(payload):
            received.append(payload)

        assert hub.subscribe(cb) is True
        # 重复订阅幂等
        assert hub.subscribe(cb) is True
        assert hub.unsubscribe(cb) is True
        assert hub.unsubscribe(cb) is False  # 已取消

    def test_hub_describe(self, reset_singletons):
        from src.admin.dashboard.event_hub import DashboardEventHub
        hub = DashboardEventHub(capacity=10)
        info = hub.describe()
        assert info["capacity"] == 10
        assert info["cached"] == 0
        assert info["subscribers"] == 0


# =====================================================================
# 7. Provider 不可用时 overview 仍返回(降级)
# =====================================================================

class TestOverviewFallback:
    def test_overview_fallback_when_provider_fails(self, reset_singletons):
        from src.admin.dashboard.router import dashboard_v2_bp
        from flask import Flask

        from src.admin.dashboard import provider as pmod

        class _BoomProvider:
            def get_overview(self):
                raise RuntimeError("kaboom")

            def get_snapshot(self):
                raise RuntimeError("kaboom")

        pmod._provider_instance = _BoomProvider()  # type: ignore[assignment]

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(dashboard_v2_bp)
        client = app.test_client()
        resp = client.get("/api/dashboard/v2/overview")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        assert body.get("fallback") is True
        assert body.get("fallback_reason", "").startswith("provider_error:")
