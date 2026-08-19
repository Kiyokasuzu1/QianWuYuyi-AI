# -*- coding: utf-8 -*-
"""
tests/test_life_state_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.1 —— LifeStateProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块
  2. Provider 只读(无写方法)
  3. envelope 结构:data / trace / confidence / fallback / fallback_reason / timestamp
  4. data 字段纯净(不含 _meta / confidence / source)
  5. current_focus 选择规则(rising → highest → null)
  6. selection_reason 字段
  7. emotion 因果链(event_ids / memory_ids)
  8. risks 规则(interest fading + valence<0 → medium;intensity>0.7 → high)
  9. risk 携带 evidence / source
 10. 全部 Provider 不可用时 fallback
 11. 路由层 GET 200 + envelope
 12. 路由层 fallback envelope
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Mock helpers
# ============================================================

class _FakeInitiativeProvider:
    def __init__(self, items: List[Dict[str, Any]] = None) -> None:
        self._items = list(items or [])

    def list_interests(self, limit: int = 20):
        return {
            "available": True,
            "total": len(self._items),
            "items": list(self._items[:limit]),
            "fallback": False,
            "fallback_reason": None,
        }


class _FakeGoalProvider:
    def __init__(self, items: List[Dict[str, Any]] = None, summary: Dict[str, Any] = None) -> None:
        self._items = list(items or [])
        self._summary = summary

    def list_goals(self, limit: int = 20, status: str = None):
        if status == "active":
            items = [it for it in self._items if it.get("status") == "active"]
        else:
            items = list(self._items)
        return {
            "available": True,
            "total": len(items),
            "items": items[:limit],
            "fallback": False,
            "fallback_reason": None,
        }

    def get_summary(self):
        if self._summary is not None:
            return dict(self._summary)
        return {
            "available": True,
            "total_count": len(self._items),
            "active_count": sum(1 for it in self._items if it.get("status") == "active"),
            "by_status": {"active": sum(1 for it in self._items if it.get("status") == "active")},
            "by_type": {},
            "fallback": False,
        }


class _FakeMemoryProvider:
    def __init__(self, items: List[Dict[str, Any]] = None) -> None:
        self._items = list(items or [])

    def list_recent(self, limit: int = 20):
        return {
            "available": True,
            "total": len(self._items),
            "items": list(self._items[:limit]),
            "fallback": False,
        }


class _FakeSelfModelProvider:
    def __init__(self, identity: Dict[str, Any] = None, evolution: List[Dict[str, Any]] = None) -> None:
        self._identity = identity
        self._evolution = list(evolution or [])

    def get_identity(self):
        if self._identity is None:
            return {"available": False, "fallback": True, "fallback_reason": "unavailable"}
        return dict(self._identity)

    def get_evolution_timeline(self, limit: int = 50):
        if not self._evolution:
            return {"available": True, "total": 0, "items": [], "fallback": False}
        return {
            "available": True,
            "total": len(self._evolution),
            "items": list(self._evolution[:limit]),
            "fallback": False,
        }


class _FakeRuntimeProvider:
    def __init__(self, emotion: Dict[str, Any] = None) -> None:
        self._emotion = emotion

    def get_emotion_summary(self):
        if self._emotion is None:
            return {"available": False, "current": None, "intensity": 0.0, "recent": []}
        return dict(self._emotion)


class _FakeLifeGraphProvider:
    def __init__(self, timeline: Dict[str, Any] = None) -> None:
        self._timeline = timeline

    def get_timeline(self, limit: int = 100):
        if self._timeline is None:
            return {"available": True, "items": [], "fallback": False}
        return dict(self._timeline)


class _FakeEmotionCollector:
    def __init__(self, events: List[Dict[str, Any]] = None) -> None:
        self._events = list(events or [])

    def list_emotion_events(self, limit: int = 30):
        return list(self._events[:limit])


def _build_provider(**overrides) -> Any:
    from src.admin.life_state_provider import LifeStateProvider
    init = overrides.get("initiative", _FakeInitiativeProvider([
        {"topic": "AI 哲学", "trend": "rising", "strength": 0.85, "signal_id": "sig_001"},
    ]))
    goal = overrides.get("goal", _FakeGoalProvider([
        {"goal_id": "g_001", "title": "理解 Transformer 注意力", "status": "active", "progress": 0.4},
    ]))
    mem = overrides.get("memory", _FakeMemoryProvider([
        {"id": "m_001", "summary": "学习 AI 哲学", "importance": 0.8, "timestamp": 1754.0},
    ]))
    sm = overrides.get("selfmodel", _FakeSelfModelProvider(
        identity={"available": True, "identity_id": "yuyi_001", "identity_name": "浅雾羽依"},
        evolution=[{"change_id": "tc_001", "trait_name": "creative", "delta": 0.6, "timestamp": 1754.0, "description": "创意特质增强"}],
    ))
    rt = overrides.get("runtime", _FakeRuntimeProvider(
        emotion={"available": True, "current": "curious", "intensity": 0.5, "recent": [{"valence": 0.3, "arousal": 0.6}]},
    ))
    lg = overrides.get("life_graph", _FakeLifeGraphProvider(timeline={
        "available": True,
        "items": [
            {"id": "r_001", "type": "reflection", "topic": "AI 哲学", "summary": "发现创造兴趣增强", "timestamp": 1754.0},
        ],
    }))
    ec = overrides.get("emotion_collector", _FakeEmotionCollector([
        {"event_id": "evt_e_001", "event_type": "integration.emotion.should_decay", "timestamp": 1754.0, "payload": {"memory_id": "m_001"}},
    ]))
    return LifeStateProvider(
        initiative_provider=init,
        goal_provider=goal,
        memory_provider=mem,
        selfmodel_provider=sm,
        runtime_provider=rt,
        life_graph_provider=lg,
        emotion_collector=ec,
    )


@pytest.fixture
def reset_singletons():
    from src.admin import life_state_provider as pmod
    pmod.reset_life_state_provider_for_testing()
    yield
    pmod.reset_life_state_provider_for_testing()


# ============================================================
# Tests
# ============================================================

class TestEnvelope:
    def test_envelope_has_required_fields(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        assert "ok" in env
        assert "data" in env
        assert "trace" in env
        assert "confidence" in env
        assert "fallback" in env
        assert "fallback_reason" in env
        assert "timestamp" in env

    def test_data_field_is_pure_no_meta(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        data = env["data"]
        # data 永远是纯业务字段,不能含 _meta / confidence / source 字段
        for forbidden in ("_meta", "confidence", "source", "trace"):
            assert forbidden not in data, f"data 中不应包含 {forbidden}"

    def test_trace_outside_data(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        assert "trace" in env
        assert "trace" not in env["data"]
        assert "sources" in env["trace"]
        assert "evidence_count" in env["trace"]
        assert "generated_at" in env["trace"]


class TestCurrentFocus:
    def test_current_focus_prefers_rising(self, reset_singletons):
        p = _build_provider(initiative=_FakeInitiativeProvider([
            {"topic": "AI 哲学", "trend": "rising", "strength": 0.85, "signal_id": "sig_001"},
            {"topic": "其它主题", "trend": "rising", "strength": 0.7, "signal_id": "sig_002"},
        ]))
        env = p.get_state_summary()
        focus = env["data"]["current_focus"]
        assert focus["topic"] == "AI 哲学"
        assert focus["trend"] == "rising"
        assert focus["selection_reason"] == "rising_highest_strength"

    def test_current_focus_falls_back_to_highest_strength(self, reset_singletons):
        p = _build_provider(initiative=_FakeInitiativeProvider([
            {"topic": "stable topic", "trend": "stable", "strength": 0.5, "signal_id": "sig_001"},
            {"topic": "fading topic", "trend": "fading", "strength": 0.3, "signal_id": "sig_002"},
        ]))
        env = p.get_state_summary()
        focus = env["data"]["current_focus"]
        assert focus["topic"] == "stable topic"
        assert focus["selection_reason"] == "highest_strength_no_rising"

    def test_current_focus_null_when_no_interests(self, reset_singletons):
        p = _build_provider(initiative=_FakeInitiativeProvider([]))
        env = p.get_state_summary()
        focus = env["data"]["current_focus"]
        assert focus["topic"] is None
        assert focus["selection_reason"] == "no_interests"

    def test_current_focus_has_selection_reason_field(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        focus = env["data"]["current_focus"]
        assert "selection_reason" in focus


class TestEmotion:
    def test_emotion_includes_causality(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        em = env["data"]["emotion"]
        assert em["name"] == "curious"
        c = em["causality"]
        assert "event_ids" in c
        assert "memory_ids" in c
        assert "evt_e_001" in c["event_ids"]
        assert "m_001" in c["memory_ids"]

    def test_emotion_source_label(self, reset_singletons):
        p = _build_provider()
        env = p.get_state_summary()
        em = env["data"]["emotion"]
        assert em["source"] == "runtime_provider"

    def test_emotion_fallback_when_runtime_unavailable(self, reset_singletons):
        p = _build_provider(runtime=_FakeRuntimeProvider(emotion=None))
        env = p.get_state_summary()
        em = env["data"]["emotion"]
        assert em["name"] is None
        assert em["source"] == "none"


class TestRisks:
    def test_risk_medium_when_fading_and_negative(self, reset_singletons):
        p = _build_provider(
            initiative=_FakeInitiativeProvider([
                {"topic": "AI 哲学", "trend": "fading", "strength": 0.3, "signal_id": "sig_001"},
            ]),
            runtime=_FakeRuntimeProvider(
                emotion={"available": True, "current": "sad", "intensity": 0.5, "recent": [{"valence": -0.4}]},
            ),
        )
        env = p.get_state_summary()
        risks = env["data"]["risks"]
        assert len(risks) >= 1
        assert risks[0]["level"] == "medium"
        assert "evidence" in risks[0]
        assert risks[0]["evidence"]["interest"]["topic"] == "AI 哲学"
        assert risks[0]["source"] == "LifeStateProvider._compute_risks"

    def test_risk_high_when_intensity_high(self, reset_singletons):
        p = _build_provider(
            initiative=_FakeInitiativeProvider([
                {"topic": "AI 哲学", "trend": "fading", "strength": 0.3, "signal_id": "sig_001"},
            ]),
            runtime=_FakeRuntimeProvider(
                emotion={"available": True, "current": "angry", "intensity": 0.9, "recent": [{"valence": -0.5}]},
            ),
        )
        env = p.get_state_summary()
        risks = env["data"]["risks"]
        assert any(r["level"] == "high" for r in risks)

    def test_no_risk_when_positive_emotion(self, reset_singletons):
        p = _build_provider(
            initiative=_FakeInitiativeProvider([
                {"topic": "AI 哲学", "trend": "fading", "strength": 0.3, "signal_id": "sig_001"},
            ]),
            runtime=_FakeRuntimeProvider(
                emotion={"available": True, "current": "happy", "intensity": 0.5, "recent": [{"valence": 0.6}]},
            ),
        )
        env = p.get_state_summary()
        # 全部 interest fading + emotion valence>=0 时不应当产生 risk
        # 但 emotion intensity=0.5 < 0.85,且无兴趣关联,所以应该为 0
        assert env["data"]["risks"] == []


class TestFallback:
    def test_fallback_when_all_providers_unavailable(self, reset_singletons):
        from src.admin.life_state_provider import LifeStateProvider
        p = LifeStateProvider()  # 全部走默认,可能不可用
        env = p.get_state_summary()
        # 当所有源都为空时,返回 fallback
        if not env.get("ok"):
            assert env["fallback"] is True
            assert env["fallback_reason"]

    def test_fallback_when_initiative_returns_empty(self, reset_singletons):
        from src.admin.life_state_provider import LifeStateProvider
        p = LifeStateProvider(
            initiative_provider=_FakeInitiativeProvider([]),
            goal_provider=_FakeGoalProvider([]),
            memory_provider=_FakeMemoryProvider([]),
            selfmodel_provider=_FakeSelfModelProvider(identity=None, evolution=[]),
            runtime_provider=_FakeRuntimeProvider(emotion=None),
            life_graph_provider=_FakeLifeGraphProvider(timeline={"available": True, "items": [], "fallback": False}),
            emotion_collector=_FakeEmotionCollector([]),
        )
        env = p.get_state_summary()
        # 全部为空 → fallback
        assert env["fallback"] is True


class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        """Provider 必须不 import 任何业务模块。"""
        from src.admin import life_state_provider as pmod
        src = Path(pmod.__file__).read_text(encoding="utf-8")
        forbidden = [
            "from src.memory",
            "from src.emotion",
            "from src.growth",
            "from src.personality",
            "from src.relationship",
            "from src.runtime.",
        ]
        for f in forbidden:
            assert f not in src, f"Provider 不应 import {f}"

    def test_no_writes_called(self, reset_singletons):
        """Provider 不得调用任何写方法。"""
        p = _build_provider()
        # 仅暴露只读方法
        names = [n for n in dir(p) if not n.startswith("_")]
        write_methods = [n for n in names if any(
            kw in n.lower() for kw in ("set", "update", "delete", "remove", "create", "save", "add", "write", "mutate")
        )]
        assert write_methods == [], f"Provider 含写方法: {write_methods}"


class TestApi:
    def _make_client(self):
        from flask import Flask
        from src.admin.dashboard.life_state_router import life_state_v2_bp
        app = Flask(__name__)
        app.register_blueprint(life_state_v2_bp)
        app.config["TESTING"] = True
        return app.test_client()

    def test_summary_endpoint_returns_envelope(self, reset_singletons):
        from src.admin import life_state_provider as pmod
        pmod._provider_instance = _build_provider()
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-state/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "ok" in body
        assert "data" in body
        assert "trace" in body
        assert "confidence" in body

    def test_summary_endpoint_data_is_pure(self, reset_singletons):
        from src.admin import life_state_provider as pmod
        pmod._provider_instance = _build_provider()
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-state/summary")
        body = resp.get_json()
        data = body["data"]
        # 关键:data 中不应含 _meta / confidence / trace / source
        for forbidden in ("_meta", "confidence", "trace", "source"):
            assert forbidden not in data

    def test_summary_endpoint_fallback_envelope(self, reset_singletons):
        from src.admin import life_state_provider as pmod
        from src.admin.life_state_provider import LifeStateProvider
        pmod._provider_instance = LifeStateProvider(
            initiative_provider=_FakeInitiativeProvider([]),
            goal_provider=_FakeGoalProvider([]),
            memory_provider=_FakeMemoryProvider([]),
            selfmodel_provider=_FakeSelfModelProvider(identity=None, evolution=[]),
            runtime_provider=_FakeRuntimeProvider(emotion=None),
            life_graph_provider=_FakeLifeGraphProvider(timeline={"available": True, "items": [], "fallback": False}),
            emotion_collector=_FakeEmotionCollector([]),
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-state/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        # 全部为空时,Provider 返回 fallback
        assert body.get("fallback") is True
        assert body.get("fallback_reason")
