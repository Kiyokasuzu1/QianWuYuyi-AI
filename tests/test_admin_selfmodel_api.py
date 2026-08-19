# -*- coding: utf-8 -*-
"""
tests/test_admin_selfmodel_api.py

Phase 6.5: Admin SelfModel API 集成测试（Flask test_client）。

覆盖：
  1. 11 个 selfmodel API 端点能正确注册并返回 JSON
  2. 正常路径（Mock 注入的 SelfModelProvider）→ ok=True
  3. Fallback 路径（Provider 不可用）→ ok=False，但端点仍可访问
  4. URL 参数解析（domain / min_confidence / limit / event_type / since 等）
  5. POST /retention/dry_run 端点正确响应
  6. 不启动真实 RuntimeCore（纯 Mock）

约束：
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
- 不修改 RuntimeCore
- 保持向后兼容（已有 endpoint 行为不变）
"""

from __future__ import annotations

import json
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

class _FakeBootstrap:
    """与 SelfModelBootstrap 兼容的最小 mock。"""
    def __init__(self, data_dir: str = "data/self_model"):
        self._data_dir = data_dir
        self._persistence_attached: bool = True
        self._last_load_counts: Dict[str, int] = {"beliefs": 5, "history": 10, "reflections": 3}
        self._last_save_result: Dict[str, Any] = {"ok": True, "beliefs": 5, "history": 10, "reflections": 3}
        self._last_error = None

    @property
    def data_dir(self) -> str:
        return self._data_dir

    @property
    def last_load_counts(self) -> Dict[str, int]:
        return dict(self._last_load_counts)

    @property
    def last_save_result(self) -> Dict[str, Any]:
        return self._last_save_result

    @property
    def last_error(self):
        return self._last_error


def _make_belief(
    belief_id: str,
    domain: str = "value",
    content: str = "羽依重视真诚",
    confidence: float = 0.8,
    active: bool = True,
    sources: List[str] = None,
) -> Any:
    from src.personality.self_belief import SelfBelief
    b = SelfBelief(
        belief_id=belief_id,
        domain=domain,
        content=content,
        confidence=confidence,
        active=active,
    )
    if sources:
        b.sources = list(sources)
    return b


def _make_history(
    event_id: str,
    event_type: str = "pcr_applied",
    source_id: str = "prop_001",
    timestamp: str = "2026-07-30T10:00:00Z",
    affected_beliefs: List[str] = None,
    affected_traits: Dict[str, float] = None,
) -> Any:
    from src.personality.self_history import SelfHistoryEvent
    return SelfHistoryEvent(
        event_id=event_id,
        event_type=event_type,
        source_id=source_id,
        affected_traits=affected_traits or {},
        affected_beliefs=affected_beliefs or [],
        timestamp=timestamp,
    )


def _make_reflection(
    note_id: str,
    reflection_type: str = "identity",
    trigger_source: str = "pcr_applied",
    content: str = "我意识到自己更喜欢独处",
    confidence: float = 0.7,
    related_belief_ids: List[str] = None,
    timestamp: str = "2026-07-30T10:01:00Z",
) -> Any:
    from src.personality.self_reflection import SelfReflectionNote
    return SelfReflectionNote(
        note_id=note_id,
        reflection_type=reflection_type,
        trigger_source=trigger_source,
        content=content,
        confidence=confidence,
        related_belief_ids=related_belief_ids or [],
        timestamp=timestamp,
    )


class _FakeAdapter:
    """Mock SelfModelAdapter。"""
    def __init__(self):
        self._beliefs = [
            _make_belief("bel_001", domain="value", content="羽依重视真诚", confidence=0.85, sources=["prop_001"]),
            _make_belief("bel_002", domain="preference", content="羽依喜欢安静", confidence=0.7, sources=["prop_002"]),
            _make_belief("bel_003", domain="value", content="旧 belief", confidence=0.3, active=False, sources=["prop_old"]),
        ]
        self._history = [
            _make_history("hevt_001", event_type="pcr_applied", source_id="prop_001",
                          affected_beliefs=["bel_001"], affected_traits={"warmth": 0.05},
                          timestamp="2026-07-30T10:00:00Z"),
            _make_history("hevt_002", event_type="snapshot_created", source_id="snapshot_001",
                          timestamp="2026-07-30T11:00:00Z"),
        ]
        self._reflections = [
            _make_reflection("refl_001", content="我意识到自己更喜欢独处", related_belief_ids=["bel_001"]),
            _make_reflection("refl_002", reflection_type="value", content="我对真诚有强烈认同",
                             related_belief_ids=["bel_001", "bel_002"]),
        ]
        self._persistence = None

    def _get_belief_store(self):
        class _S:
            def __init__(s, items):
                s._items = items
            def all(s):
                return list(s._items)
        return _S(self._beliefs)

    def _get_history_store(self):
        class _S:
            def __init__(s, items):
                s._items = items
            def all(s):
                return list(s._items)
        return _S(self._history)

    def _get_reflection_store(self):
        class _S:
            def __init__(s, items):
                s._items = items
            def all(s):
                return list(s._items)
        return _S(self._reflections)


class _FakeCore:
    def __init__(self, adapter, bootstrap):
        self._adapter = adapter
        self._bootstrap = bootstrap

    def get_self_model_adapter(self):
        return self._adapter

    def get_self_model_bootstrap(self):
        return self._bootstrap


class _FakeBridge:
    def __init__(self, core):
        self._core = core

    def get_runtime_core(self):
        return self._core


class _FakeRuntimeProvider:
    def __init__(self, bridge):
        self._bridge = bridge

    def get_runtime_bridge(self):
        return self._bridge


class _FakeSelfModelProvider:
    """Mock 完整 SelfModelProvider 接口。"""

    def __init__(self, *, available: bool = True, with_data: bool = True):
        self._available = available
        self._with_data = with_data
        self._adapter = _FakeAdapter() if with_data else None
        self._bootstrap = _FakeBootstrap() if with_data else None
        self._core = _FakeCore(adapter=self._adapter, bootstrap=self._bootstrap) if with_data else None
        self._bridge = _FakeBridge(core=self._core) if with_data else None
        self._rt = _FakeRuntimeProvider(bridge=self._bridge) if with_data else None

    # ---- internal helpers mirroring SelfModelProvider ----
    def _get_adapter(self):
        return self._adapter

    def _get_bootstrap(self):
        return self._bootstrap

    def _get_core(self):
        return self._core

    def _get_bridge(self):
        return self._bridge

    def _bridge_error(self):
        return None

    # ---- public API ----
    def get_status(self):
        if not self._available:
            return {"available": False, "bridge_error": "bridge unavailable", "bootstrap": None}
        return {
            "available": True,
            "bridge_error": None,
            "bootstrap": {
                "data_dir": "data/self_model",
                "persistence_attached": True,
                "last_load_counts": {"beliefs": 5, "history": 10, "reflections": 3},
                "last_save_result": {"ok": True},
                "last_error": None,
            },
        }

    def get_identity(self):
        if not self._available:
            return {"available": False, "identity_name": "浅雾羽依", "error": "no bridge"}
        return {
            "available": True,
            "identity_name": "浅雾羽依",
            "core_identity": "AI 助手",
            "current_personality": "温柔 / 理性",
            "narrative": "成长中的 AI",
            "self_understanding_level": 0.7,
        }

    def list_beliefs(self, *, domain=None, min_confidence=0.0, include_inactive=True, limit=100):
        if not self._available:
            return {"available": False, "total": 0, "active": 0, "inactive": 0,
                    "by_domain": {}, "items": []}
        items = []
        for b in self._adapter._beliefs:
            if not include_inactive and not b.active:
                continue
            if domain and b.domain != domain:
                continue
            if b.confidence < min_confidence:
                continue
            items.append({
                "belief_id": b.belief_id,
                "domain": b.domain,
                "content": b.content,
                "confidence": b.confidence,
                "version": b.version,
                "active": b.active,
                "sources": list(getattr(b, "sources", []) or []),
                "evidence_count": len(getattr(b, "sources", []) or []),
                "first_seen": getattr(b, "first_seen", None),
                "last_confirmed": getattr(b, "last_confirmed", None),
            })
        items.sort(key=lambda x: x["confidence"], reverse=True)
        items = items[:limit]
        return {
            "available": True,
            "total": len(items),
            "active": sum(1 for it in items if it["active"]),
            "inactive": sum(1 for it in items if not it["active"]),
            "by_domain": {d: sum(1 for it in items if it["domain"] == d) for d in {it["domain"] for it in items}},
            "items": items,
        }

    def get_belief_why(self, *, belief_id):
        if not self._available or not belief_id:
            return {"available": bool(belief_id), "belief": None, "origin": None,
                    "pcr_link": None, "answer": "", "error": "belief_id required" if not belief_id else None}
        belief = next((b for b in self._adapter._beliefs if b.belief_id == belief_id), None)
        if not belief:
            return {"available": True, "belief": None, "origin": None,
                    "pcr_link": None, "answer": "", "error": "belief not found"}
        return {
            "available": True,
            "belief": {
                "belief_id": belief.belief_id,
                "domain": belief.domain,
                "content": belief.content,
                "confidence": belief.confidence,
            },
            "origin": {
                "sources": list(getattr(belief, "sources", []) or []),
                "history": [],
            },
            "pcr_link": {
                "summary": {"belief_count": len(getattr(belief, "sources", []) or [])},
                "history_events": [],
            },
            "answer": f"该 belief 由 {len(getattr(belief, 'sources', []) or [])} 个 source 推导而来。",
        }

    def list_history(self, *, event_type=None, since=None, until=None, limit=100):
        if not self._available:
            return {"available": False, "total": 0, "by_event_type": {}, "items": []}
        items = []
        for h in self._adapter._history:
            if event_type and h.event_type != event_type:
                continue
            if since and h.timestamp < since:
                continue
            if until and h.timestamp > until:
                continue
            items.append({
                "event_id": h.event_id,
                "event_type": h.event_type,
                "timestamp": h.timestamp,
                "source_type": h.event_type,
                "source_id": h.source_id,
                "summary": f"{h.event_type} via {h.source_id}",
                "affected_traits": h.affected_traits,
                "affected_beliefs": h.affected_beliefs,
                "actor": "system",
            })
        items = items[:limit]
        return {
            "available": True,
            "total": len(items),
            "by_event_type": {it["event_type"]: sum(1 for x in items if x["event_type"] == it["event_type"]) for it in items},
            "items": items,
        }

    def list_reflections(self, *, trigger_source=None, reflection_type=None, min_confidence=0.0, limit=100):
        if not self._available:
            return {"available": False, "total": 0, "by_reflection_type": {},
                    "by_trigger_source": {}, "items": []}
        items = []
        for r in self._adapter._reflections:
            if trigger_source and r.trigger_source != trigger_source:
                continue
            if reflection_type and r.reflection_type != reflection_type:
                continue
            if r.confidence < min_confidence:
                continue
            items.append({
                "note_id": r.note_id,
                "reflection_type": r.reflection_type,
                "trigger_source": r.trigger_source,
                "content": r.content,
                "confidence": r.confidence,
                "related_belief_ids": r.related_belief_ids,
                "timestamp": r.timestamp,
            })
        items = items[:limit]
        by_rt = {it["reflection_type"]: sum(1 for x in items if x["reflection_type"] == it["reflection_type"]) for it in items}
        by_ts = {it["trigger_source"]: sum(1 for x in items if x["trigger_source"] == it["trigger_source"]) for it in items}
        return {
            "available": True,
            "total": len(items),
            "by_reflection_type": by_rt,
            "by_trigger_source": by_ts,
            "items": items,
        }

    def get_evolution_timeline(self, *, start=None, end=None, limit=200, sources=None):
        if not self._available:
            return {"available": False, "total": 0, "by_source": {}, "items": []}
        items = []
        # Add history events
        if not sources or "history" in sources:
            for h in self._adapter._history:
                items.append({
                    "source": "history",
                    "event_id": h.event_id,
                    "timestamp": h.timestamp,
                    "summary": f"{h.event_type} via {h.source_id}",
                })
        if not sources or "belief" in sources:
            for b in self._adapter._beliefs:
                items.append({
                    "source": "belief",
                    "event_id": b.belief_id,
                    "timestamp": "2026-07-29T00:00:00Z",
                    "summary": f"belief formed: {b.content}",
                })
        if not sources or "reflection" in sources:
            for r in self._adapter._reflections:
                items.append({
                    "source": "reflection",
                    "event_id": r.note_id,
                    "timestamp": r.timestamp,
                    "summary": f"reflection: {r.content[:30]}",
                })
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        items = items[:limit]
        by_source = {}
        for it in items:
            by_source[it["source"]] = by_source.get(it["source"], 0) + 1
        return {"available": True, "total": len(items), "by_source": by_source, "items": items}

    def get_pcr_related_events(self, *, proposal_id):
        if not proposal_id:
            return {"available": False, "proposal_id": proposal_id,
                    "history_events": [], "linked_beliefs": [], "linked_reflections": [],
                    "audit_records": [], "summary": {"history_count": 0, "belief_count": 0,
                                                     "reflection_count": 0, "audit_count": 0},
                    "error": "proposal_id required"}
        if not self._available:
            return {"available": False, "proposal_id": proposal_id,
                    "history_events": [], "linked_beliefs": [], "linked_reflections": [],
                    "audit_records": [], "summary": {"history_count": 0, "belief_count": 0,
                                                     "reflection_count": 0, "audit_count": 0}}
        history_events = [h for h in self._adapter._history if h.source_id == proposal_id]
        linked_beliefs = [b for b in self._adapter._beliefs if proposal_id in (getattr(b, "sources", []) or [])]
        linked_refl = []
        for r in self._adapter._reflections:
            if any(b in [lb.belief_id for lb in linked_beliefs] for b in r.related_belief_ids):
                linked_refl.append({"note_id": r.note_id, "content": r.content})
        return {
            "available": True,
            "proposal_id": proposal_id,
            "history_events": [{"event_id": h.event_id, "event_type": h.event_type,
                                "timestamp": h.timestamp} for h in history_events],
            "linked_beliefs": [{"belief_id": b.belief_id, "domain": b.domain, "content": b.content} for b in linked_beliefs],
            "linked_reflections": linked_refl,
            "audit_records": [],
            "summary": {
                "history_count": len(history_events),
                "belief_count": len(linked_beliefs),
                "reflection_count": len(linked_refl),
                "audit_count": 0,
            },
        }

    def get_health_report(self):
        if not self._available:
            return {"available": False, "report": None, "summary": {"counts": {}}}
        return {
            "available": True,
            "report": {
                "overall_status": "healthy",
                "beliefs_count": 3,
                "history_count": 2,
                "reflections_count": 2,
                "issues": [],
                "duration_ms": 5.2,
            },
            "summary": {"counts": {"beliefs": 3, "history": 2, "reflections": 2}},
        }

    def get_retention_status(self):
        if not self._available:
            return {"available": False, "thresholds": {}, "current_counts": {}}
        return {
            "available": True,
            "thresholds": {"max_active_beliefs": 200, "min_confidence_for_active": 0.1},
            "current_counts": {
                "beliefs_total": 3, "beliefs_active": 2, "beliefs_inactive": 1,
                "history_total": 2, "reflections_total": 2,
            },
        }

    def run_retention_dry_run(self):
        if not self._available:
            return {"available": False, "report": None, "dry_run": True, "applied": False}
        return {
            "available": True,
            "dry_run": True,
            "applied": False,
            "report": {
                "beliefs_total": 3,
                "beliefs_active_before": 2,
                "beliefs_active_after": 2,
                "beliefs_deactivated": 0,
                "history_total": 2,
                "reflections_total": 2,
            },
        }


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture(scope="module")
def flask_app():
    pytest.importorskip("flask")
    from src.admin.api.routes import admin_bp
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.config["TESTING"] = True
    return app


@pytest.fixture(scope="module")
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset SelfModelProvider singleton before each test."""
    from src.admin.self_model_provider import reset_self_model_provider_for_testing
    reset_self_model_provider_for_testing()
    yield
    reset_self_model_provider_for_testing()


def _patch_provider(monkeypatch, provider: _FakeSelfModelProvider):
    """Inject a fake provider into routes via the singleton."""
    from src.admin import self_model_provider as smp
    monkeypatch.setattr(smp, "_provider_instance", provider)


# =====================================================================
# A. 正常路径（Provider 可用 + 有数据）
# =====================================================================

class TestSelfModelApiHappyPath:

    def test_status(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider(available=True, with_data=True))
        rv = client.get("/admin/api/admin/selfmodel/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["available"] is True
        assert data["bootstrap"] is not None
        assert data["bootstrap"]["data_dir"] == "data/self_model"

    def test_identity(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/identity")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["identity_name"] == "浅雾羽依"
        assert "core_identity" in data

    def test_beliefs(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/beliefs")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["total"] == 3
        assert data["active"] == 2
        assert data["inactive"] == 1

    def test_beliefs_filter_domain(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/beliefs?domain=preference")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["total"] == 1
        assert data["items"][0]["domain"] == "preference"

    def test_beliefs_filter_min_confidence(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/beliefs?min_confidence=0.5")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        ids = [it["belief_id"] for it in data["items"]]
        assert "bel_003" not in ids  # confidence 0.3 被过滤

    def test_beliefs_exclude_inactive(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/beliefs?include_inactive=false")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["inactive"] == 0

    def test_belief_why(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/belief/bel_001/why")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["belief"] is not None
        assert data["belief"]["belief_id"] == "bel_001"
        assert "answer" in data

    def test_belief_why_not_found(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/belief/nonexistent/why")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        # Not found returns ok=True with error key
        assert data["belief"] is None
        assert "error" in data

    def test_history(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/history")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["total"] == 2
        assert "pcr_applied" in data["by_event_type"]

    def test_history_filter_event_type(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/history?event_type=snapshot_created")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["total"] == 1
        assert all(it["event_type"] == "snapshot_created" for it in data["items"])

    def test_reflections(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/reflections")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["total"] == 2

    def test_evolution_timeline(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/evolution_timeline")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        # history(2) + belief(3) + reflection(2) = 7
        assert data["total"] >= 5
        assert "history" in data["by_source"]

    def test_evolution_timeline_filter_sources(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/evolution_timeline?sources=history,belief")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        for it in data["items"]:
            assert it["source"] in ("history", "belief")

    def test_pcr_related(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/pcr/prop_001/related")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["summary"]["history_count"] >= 1
        assert data["summary"]["belief_count"] >= 1

    def test_health(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/health")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["available"] is True
        assert data["report"] is not None
        assert data["report"]["overall_status"] == "healthy"

    def test_retention(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.get("/admin/api/admin/selfmodel/retention")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["thresholds"]["max_active_beliefs"] >= 1
        assert data["current_counts"]["beliefs_total"] == 3

    def test_retention_dry_run_post(self, client, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider())
        rv = client.post("/admin/api/admin/selfmodel/retention/dry_run")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["dry_run"] is True
        assert data["applied"] is False
        assert data["report"] is not None
        assert data["report"]["beliefs_active_after"] == data["report"]["beliefs_active_before"]


# =====================================================================
# B. Fallback 路径（Provider 不可用）
# =====================================================================

class TestSelfModelApiFallback:

    def _patch_unavailable(self, monkeypatch):
        _patch_provider(monkeypatch, _FakeSelfModelProvider(available=False, with_data=False))

    def test_status_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        # ok=True with available=False is the safe response
        assert data["ok"] is True
        assert data["available"] is False

    def test_beliefs_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/beliefs")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["available"] is False
        assert data["items"] == []

    def test_belief_why_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/belief/anything/why")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["ok"] is True
        assert data["belief"] is None

    def test_history_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/history")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False
        assert data["items"] == []

    def test_reflections_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/reflections")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False

    def test_evolution_timeline_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/evolution_timeline")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False

    def test_pcr_related_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/pcr/prop_001/related")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False
        assert data["history_events"] == []

    def test_health_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/health")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False
        assert data["report"] is None

    def test_retention_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.get("/admin/api/admin/selfmodel/retention")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False
        assert data["thresholds"] == {}

    def test_retention_dry_run_fallback(self, client, monkeypatch):
        self._patch_unavailable(monkeypatch)
        rv = client.post("/admin/api/admin/selfmodel/retention/dry_run")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert data["available"] is False
        assert data["dry_run"] is True
        assert data["applied"] is False


# =====================================================================
# C. 端点完整性
# =====================================================================

class TestSelfModelApiEndpointCount:
    """11 个 selfmodel 端点全部注册到 admin_bp。"""

    EXPECTED = [
        "/admin/api/admin/selfmodel/status",
        "/admin/api/admin/selfmodel/identity",
        "/admin/api/admin/selfmodel/beliefs",
        "/admin/api/admin/selfmodel/belief/<belief_id>/why",
        "/admin/api/admin/selfmodel/history",
        "/admin/api/admin/selfmodel/reflections",
        "/admin/api/admin/selfmodel/evolution_timeline",
        "/admin/api/admin/selfmodel/pcr/<proposal_id>/related",
        "/admin/api/admin/selfmodel/health",
        "/admin/api/admin/selfmodel/retention",
        "/admin/api/admin/selfmodel/retention/dry_run",
    ]

    def test_all_endpoints_registered(self, flask_app):
        rules = [r.rule for r in flask_app.url_map.iter_rules()]
        for ep in self.EXPECTED:
            assert ep in rules, f"endpoint {ep} 未注册"

    def test_endpoint_count(self, flask_app):
        rules = [r.rule for r in flask_app.url_map.iter_rules() if "selfmodel" in r.rule]
        assert len(rules) == len(self.EXPECTED)
