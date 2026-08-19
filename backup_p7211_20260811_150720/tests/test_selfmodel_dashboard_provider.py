# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.2 —— SelfModelDashboardProvider 单元测试。

覆盖:
  1. SelfModel 读取正常
  2. 无业务模块 import
  3. fallback 正常
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers
# =====================================================================

class _FakeSMP:
    def __init__(
        self,
        *,
        identity: Dict[str, Any] = None,
        beliefs: Dict[str, Any] = None,
        timeline: Dict[str, Any] = None,
        health: Dict[str, Any] = None,
    ):
        self._identity = identity or {
            "available": True,
            "identity_name": "浅雾羽依",
            "identity_id": "sm-1",
            "core_values": ["温柔", "陪伴", "诚实"],
            "version": "v3",
            "source": "self_model_v3",
            "anchor": "Yuyi-Anchor",
            "stable": True,
        }
        self._beliefs = beliefs or {
            "available": True,
            "total": 4,
            "active": 3,
            "inactive": 1,
            "by_domain": {"trait": 2, "capability": 2},
            "items": [
                {"belief_id": "b1", "domain": "trait", "content": "温柔", "confidence": 0.9,
                 "version": 1, "evidence_count": 5, "active": True},
                {"belief_id": "b2", "domain": "value", "content": "诚实", "confidence": 0.85,
                 "version": 1, "evidence_count": 3, "active": True},
                {"belief_id": "b3", "domain": "capability", "content": "理解用户情绪",
                 "confidence": 0.7, "version": 1, "evidence_count": 2, "active": True},
                {"belief_id": "b4", "domain": "skill", "content": "音乐欣赏",
                 "confidence": 0.6, "version": 1, "evidence_count": 1, "active": False},
            ],
        }
        self._timeline = timeline or {
            "available": True, "total": 1,
            "items": [{"event_id": "h1", "event_type": "belief_formed", "timestamp": "2025-01-01"}],
        }
        self._health = health or {
            "available": True, "summary": "ok",
            "report": {"status": "healthy", "score": 95, "issues": []},
        }
        self._calls: List[str] = []

    def get_identity(self):
        self._calls.append("get_identity")
        return dict(self._identity)

    def list_beliefs(self, **kw):
        self._calls.append("list_beliefs")
        return dict(self._beliefs)

    def get_evolution_timeline(self, **kw):
        self._calls.append("get_evolution_timeline")
        return dict(self._timeline)

    def get_health_report(self):
        self._calls.append("get_health_report")
        return dict(self._health)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.selfmodel_dashboard_provider import reset_selfmodel_dashboard_provider_for_testing
    reset_selfmodel_dashboard_provider_for_testing()
    yield
    reset_selfmodel_dashboard_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import selfmodel_dashboard_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.runtime.core", "import src.runtime.core",
            "from src.runtime.self_model", "import src.runtime.self_model",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"


class TestDataAccess:
    def test_identity(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        p = SelfModelDashboardProvider(self_model_provider=smp)
        d = p.get_identity()
        assert d["identity_name"] == "浅雾羽依"
        assert d["source"] == "self_model_v3"
        assert d["fallback"] is False
        assert "get_identity" in smp._calls

    def test_traits_derived_from_beliefs(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        p = SelfModelDashboardProvider(self_model_provider=smp)
        d = p.get_traits()
        # 我们的 mock beliefs 中 trait / value 域 = 2 条
        assert d["total"] == 2
        assert all(it["domain"] in {"trait", "value", "preference", "personality", "identity"} for it in d["items"])

    def test_capabilities_derived_from_beliefs(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        p = SelfModelDashboardProvider(self_model_provider=smp)
        d = p.get_capabilities()
        assert d["total"] == 2  # capability + skill
        assert all(it["domain"] in {"capability", "skill", "ability"} for it in d["items"])

    def test_beliefs(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        p = SelfModelDashboardProvider(self_model_provider=_FakeSMP())
        d = p.get_beliefs(limit=10)
        assert d["total"] == 4
        assert d["active"] == 3
        assert d["inactive"] == 1

    def test_evolution_timeline(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        p = SelfModelDashboardProvider(self_model_provider=_FakeSMP())
        d = p.get_evolution_timeline(limit=10)
        assert d["total"] == 1
        assert d["items"][0]["event_type"] == "belief_formed"

    def test_health(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        p = SelfModelDashboardProvider(self_model_provider=_FakeSMP())
        d = p.get_health()
        assert d["status"] == "healthy"
        assert d["score"] == 95


class TestFallback:
    def test_provider_unavailable_returns_fallback(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider

        class _Bad:
            def get_identity(self): raise RuntimeError("x")
            def list_beliefs(self, **kw): raise RuntimeError("x")
            def get_evolution_timeline(self, **kw): raise RuntimeError("x")
            def get_health_report(self): raise RuntimeError("x")

        p = SelfModelDashboardProvider(self_model_provider=_Bad())
        for d in (p.get_identity(), p.get_traits(), p.get_capabilities(),
                  p.get_beliefs(), p.get_evolution_timeline(), p.get_health()):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_empty_payload_returns_fallback(self, reset_singletons):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider

        class _Empty:
            def get_identity(self): return {}
            def list_beliefs(self, **kw): return {}
            def get_evolution_timeline(self, **kw): return {}
            def get_health_report(self): return {}

        p = SelfModelDashboardProvider(self_model_provider=_Empty())
        # identity 不可用时返回 fallback=True
        ident = p.get_identity()
        assert ident["fallback"] is True
        # 列表型接口返回空 items,available=False
        traits = p.get_traits()
        assert traits["items"] == []
        assert traits["available"] is False
        caps = p.get_capabilities()
        assert caps["items"] == []
        assert caps["available"] is False


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.selfmodel_router import selfmodel_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(selfmodel_v2_bp)
        return app.test_client()

    def test_identity_endpoint(self, reset_singletons):
        from src.admin import selfmodel_dashboard_provider as pmod
        pmod._provider_instance = pmod.SelfModelDashboardProvider(self_model_provider=_FakeSMP())
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/selfmodel/identity")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["identity_name"] == "浅雾羽依"

    def test_traits_endpoint(self, reset_singletons):
        from src.admin import selfmodel_dashboard_provider as pmod
        pmod._provider_instance = pmod.SelfModelDashboardProvider(self_model_provider=_FakeSMP())
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/selfmodel/traits")
        body = resp.get_json()
        assert body["ok"] is True
        assert "items" in body["data"]
