# -*- coding: utf-8 -*-
"""
tests/test_initiative_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 8.2 —— InitiativeDashboardProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块(initiative / goal / personality)
  2. 只读访问(不调用任何写方法)
  3. Summary / Interests / Actions / Filtered / History 读取成功
  4. fallback 路径(Provider 不可用 / 异常 / 空数据)
  5. API 响应结构
  6. 数据来源可追溯(event_id / event_type / source_event_ids)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers
# =====================================================================

class _FakeInitiativeSource:
    """模拟 Initiative Source,仅暴露 read-only 接口。"""

    def __init__(self, events: List[Dict[str, Any]]):
        self._events = list(events)
        self._calls: List[str] = []

    def list_initiative_events(self, limit: int = 200):
        self._calls.append("list_initiative_events")
        return list(self._events)


def _make_signal_event(
    signal_id: str,
    *,
    event_type: str = "integration.interest_signal.created",
    timestamp: float = 0.0,
    topic: str = "",
    trend: str = "new",
    strength: float = 0.5,
    confidence: float = 0.5,
    rationale: str = "",
    source_event_ids: List[str] = None,
    source_reflection_ids: List[str] = None,
) -> Dict[str, Any]:
    """构造一个 InterestSignal IntegrationEvent 的 dict 形式。"""
    payload = {
        "signal_id": signal_id,
        "topic": topic,
        "trend": trend,
        "strength": strength,
        "confidence": confidence,
        "rationale": rationale,
        "source_event_ids": list(source_event_ids or []),
        "source_reflection_ids": list(source_reflection_ids or []),
        "created_at": timestamp,
    }
    return {
        "event_id": f"evt_sig_{signal_id}",
        "event_type": event_type,
        "source": "initiative",
        "timestamp": timestamp,
        "payload": payload,
        "related_ids": [signal_id] + list(source_event_ids or []),
        "metadata": {"schema_version": "1.0"},
    }


def _make_action_event(
    action_id: str,
    *,
    event_type: str = "integration.initiative.created",
    timestamp: float = 0.0,
    action_type: str = "observe",
    topic: str = "",
    status: str = "",
    urgency: str = "normal",
    effort_estimate: str = "low",
    expected_value: float = 0.5,
    confidence: float = 0.5,
    priority: float = 0.5,
    supporting_signal_ids: List[str] = None,
    rationale: str = "",
    filter_reason: str = "",
    rule_applied: str = "",
) -> Dict[str, Any]:
    """构造一个 PossibleAction IntegrationEvent 的 dict 形式。"""
    payload = {
        "action_id": action_id,
        "action_type": action_type,
        "topic": topic,
        "status": status,
        "urgency": urgency,
        "effort_estimate": effort_estimate,
        "expected_value": expected_value,
        "confidence": confidence,
        "priority": priority,
        "supporting_signal_ids": list(supporting_signal_ids or []),
        "rationale": rationale,
        "filter_reason": filter_reason,
        "rule_applied": rule_applied,
        "created_at": timestamp,
    }
    return {
        "event_id": f"evt_act_{action_id}",
        "event_type": event_type,
        "source": "initiative",
        "timestamp": timestamp,
        "payload": payload,
        "related_ids": [action_id] + list(supporting_signal_ids or []),
        "metadata": {"schema_version": "1.0"},
    }


def _make_events():
    """构造一组覆盖各类 initiative event 的样本数据。"""
    now = time.time()
    return [
        # InterestSignal 1: 新兴兴趣
        _make_signal_event(
            "sig_001",
            timestamp=now - 7200,
            topic="AI 哲学",
            trend="new",
            strength=0.65,
            confidence=0.7,
            rationale="用户多次提及 AI 意识",
            source_event_ids=["evt_a1", "evt_a2"],
            source_reflection_ids=["refl_001"],
        ),
        # InterestSignal 2: 上升中兴趣
        _make_signal_event(
            "sig_002",
            timestamp=now - 3600,
            topic="钢琴",
            trend="rising",
            strength=0.85,
            confidence=0.8,
            rationale="用户每天都在练琴",
            source_event_ids=["evt_a3"],
        ),
        # InterestSignal 3: 衰退兴趣
        _make_signal_event(
            "sig_003",
            timestamp=now - 86400,
            topic="旧游戏",
            trend="fading",
            strength=0.3,
            confidence=0.5,
            rationale="已经很久没玩",
            source_event_ids=["evt_a4"],
        ),
        # PossibleAction pending
        _make_action_event(
            "act_001",
            event_type="integration.initiative.created",
            timestamp=now - 1800,
            action_type="observe",
            topic="钢琴练习",
            status="pending",
            urgency="normal",
            expected_value=0.65,
            confidence=0.75,
            priority=0.7,
            supporting_signal_ids=["sig_002"],
            rationale="观察用户练琴进度",
        ),
        # PossibleAction pending 2
        _make_action_event(
            "act_002",
            event_type="integration.initiative.created",
            timestamp=now - 900,
            action_type="ask",
            topic="AI 哲学",
            status="pending",
            urgency="low",
            expected_value=0.55,
            confidence=0.65,
            priority=0.5,
            supporting_signal_ids=["sig_001"],
            rationale="询问对 AI 意识的想法",
        ),
        # PossibleAction filtered (低置信度)
        _make_action_event(
            "act_003",
            event_type="integration.initiative.filtered",
            timestamp=now - 600,
            action_type="recommend",
            topic="旧游戏",
            status="filtered",
            urgency="low",
            expected_value=0.15,
            confidence=0.25,
            priority=0.2,
            supporting_signal_ids=["sig_003"],
            rationale="不推荐用户已不感兴趣的话题",
            filter_reason="confidence below threshold (0.25 < 0.30)",
            rule_applied="low_confidence",
        ),
        # PossibleAction deferred (低 expected_value)
        _make_action_event(
            "act_004",
            event_type="integration.initiative.filtered",
            timestamp=now - 300,
            action_type="remind",
            topic="喝水",
            status="deferred",
            urgency="low",
            expected_value=0.1,
            confidence=0.8,
            priority=0.4,
            supporting_signal_ids=[],
            rationale="提醒用户喝水",
            filter_reason="expected value too low (0.10 < 0.20)",
            rule_applied="low_expected_value",
        ),
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.initiative_dashboard_provider import (
        reset_initiative_dashboard_provider_for_testing,
    )
    reset_initiative_dashboard_provider_for_testing()
    yield
    reset_initiative_dashboard_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.runtime.goal", "import src.runtime.goal",
            "from src.runtime.initiative", "import src.runtime.initiative",
            "from src.runtime.personality", "import src.runtime.personality",
            "from src.runtime.reflection", "import src.runtime.reflection",
            "from src.runtime.memory", "import src.runtime.memory",
            "from src.goal", "import src.goal",
            "from src.personality", "import src.personality",
            "from src.initiative", "import src.initiative",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_no_writes_called(self, reset_singletons):
        """只调用 list_initiative_events,不调用任何写方法。"""
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider

        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        p.get_summary()
        p.list_interests(limit=5)
        p.list_interests(limit=5, trend="new")
        p.list_actions(limit=5)
        p.list_actions(limit=5, status="pending")
        p.list_filtered(limit=5)
        p.get_history(limit=10)

        for call in source._calls:
            assert call == "list_initiative_events", f"unexpected call: {call}"

        # Provider 自身不应有写方法
        forbidden_methods = [
            "_save", "_write", "_append", "_store", "_update", "_delete", "_remove", "_modify",
            "create_interest", "create_action", "filter_action",
            "add_interest", "add_action", "update_interest", "update_action",
            "remove_interest", "remove_action",
        ]
        for name in dir(p):
            for f in forbidden_methods:
                if name.startswith(f) and callable(getattr(p, name, None)):
                    pytest.fail(f"unexpected write-like method: {name}")


class TestSummary:
    def test_real_data(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["interest_count"] == 3
        assert d["possible_action_count"] == 4
        assert d["filtered_count"] == 2  # act_003 (filtered) + act_004 (deferred)
        # 趋势分布
        by_trend = d["by_trend"]
        assert by_trend["new"] == 1
        assert by_trend["rising"] == 1
        assert by_trend["fading"] == 1
        # action 状态分布
        by_status = d["by_action_status"]
        assert by_status["pending"] == 2
        assert by_status["filtered"] == 1
        assert by_status["deferred"] == 1
        # 最后一个 signal: sig_002(ts 最大)
        assert d["last_interest_id"] == "sig_002"
        # 最后一个 action: act_004(ts 最大)
        assert d["last_action_id"] == "act_004"

    def test_empty_returns_not_fallback(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource([])
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["interest_count"] == 0
        assert d["possible_action_count"] == 0
        assert d["filtered_count"] == 0
        assert d["last_interest_id"] is None
        assert d["last_action_id"] is None


class TestInterests:
    def test_default_list(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_interests(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total"] == 3
        assert d["total_unfiltered"] == 3
        assert len(d["items"]) == 3
        first = d["items"][0]
        for k in (
            "signal_id", "topic", "trend", "strength", "confidence",
            "source_event_ids", "created_at",
        ):
            assert k in first, f"missing field: {k}"
        # source_event_ids 可追溯
        assert first["source_event_ids"] == ["evt_a1", "evt_a2"] or \
               first["source_event_ids"] == ["evt_a3"] or \
               first["source_event_ids"] == ["evt_a4"]

    def test_filter_by_trend_rising(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_interests(limit=10, trend="rising")
        assert d["fallback"] is False
        assert d["total"] == 1
        assert d["filter_trend"] == "rising"
        assert d["items"][0]["topic"] == "钢琴"
        assert all(it["trend"] == "rising" for it in d["items"])

    def test_filter_by_trend_new(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_interests(limit=10, trend="new")
        assert d["total"] == 1
        assert d["items"][0]["topic"] == "AI 哲学"

    def test_invalid_trend_falls_back_to_all(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_interests(limit=10, trend="invalid_trend")
        assert d["filter_trend"] is None
        assert d["total"] == 3

    def test_limit_clamp(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_interests(limit=99999)
        assert len(d["items"]) == 3


class TestActions:
    def test_default_list(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_actions(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total"] == 4
        assert d["total_unfiltered"] == 4
        assert len(d["items"]) == 4
        first = d["items"][0]
        for k in (
            "action_id", "action_type", "topic", "status", "urgency",
            "expected_value", "confidence", "priority", "supporting_signal_ids",
            "source_interest", "created_at",
        ):
            assert k in first, f"missing field: {k}"
        # source_interest 应取自 supporting_signal_ids 第一个
        assert first["source_interest"] in (
            "sig_001", "sig_002", "sig_003", ""
        )

    def test_filter_by_status_pending(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_actions(limit=10, status="pending")
        assert d["fallback"] is False
        assert d["total"] == 2
        assert d["filter_status"] == "pending"
        assert all(it["status"] == "pending" for it in d["items"])

    def test_filter_by_status_filtered(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_actions(limit=10, status="filtered")
        assert d["total"] == 1
        assert d["items"][0]["action_id"] == "act_003"
        assert d["items"][0]["filter_policy"] == "low_confidence"

    def test_filter_by_status_deferred(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_actions(limit=10, status="deferred")
        assert d["total"] == 1
        assert d["items"][0]["action_id"] == "act_004"
        assert d["items"][0]["filter_policy"] == "low_expected_value"

    def test_invalid_status_falls_back_to_all(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_actions(limit=10, status="invalid_status")
        assert d["filter_status"] is None
        assert d["total"] == 4


class TestFiltered:
    def test_default_list(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_filtered(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        # act_003 (filtered) + act_004 (deferred)
        assert d["total"] == 2
        assert len(d["items"]) == 2
        for it in d["items"]:
            assert it["status"] != "pending"
            for k in ("action_id", "filter_reason", "filter_policy"):
                assert k in it, f"missing field: {k}"

    def test_contains_filter_reason_and_policy(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_filtered(limit=10)
        items_by_id = {it["action_id"]: it for it in d["items"]}
        assert "confidence below threshold" in items_by_id["act_003"]["filter_reason"]
        assert items_by_id["act_003"]["filter_policy"] == "low_confidence"
        assert "expected value too low" in items_by_id["act_004"]["filter_reason"]
        assert items_by_id["act_004"]["filter_policy"] == "low_expected_value"

    def test_empty(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        # 只有 pending,没有 filtered
        events = [
            _make_action_event(
                "act_001",
                event_type="integration.initiative.created",
                timestamp=time.time(),
                status="pending",
            ),
        ]
        source = _FakeInitiativeSource(events)
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.list_filtered(limit=10)
        assert d["available"] is True
        assert d["total"] == 0
        assert d["items"] == []


class TestHistory:
    def test_history_real_data(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_history(limit=20)
        assert d["available"] is True
        assert d["fallback"] is False
        # 3 signal + 4 action = 7
        assert d["total"] == 7
        assert len(d["items"]) == 7
        first = d["items"][0]
        for k in ("event_id", "event_type", "kind", "subject_id", "timestamp"):
            assert k in first, f"missing field: {k}"

    def test_history_filter_event_types(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_history(limit=20)
        kinds = {it["kind"] for it in d["items"]}
        assert "interest_signal" in kinds
        assert "possible_action" in kinds

    def test_history_chronological_desc(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_history(limit=20)
        ts_list = [it["timestamp_ts"] for it in d["items"]]
        assert ts_list == sorted(ts_list, reverse=True)

    def test_history_signal_event_shape(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_history(limit=20)
        sig_items = [it for it in d["items"] if it["kind"] == "interest_signal"]
        assert len(sig_items) == 3
        for it in sig_items:
            assert it["topic"]
            assert it["trend"] in (
                "new", "rising", "stable", "fading"
            )
            assert "strength" in it
            assert "confidence" in it

    def test_history_action_event_shape(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        source = _FakeInitiativeSource(_make_events())
        p = InitiativeDashboardProvider(initiative_source=source)
        d = p.get_history(limit=20)
        act_items = [it for it in d["items"] if it["kind"] == "possible_action"]
        assert len(act_items) == 4
        for it in act_items:
            assert it["action_type"]
            assert it["status"] in (
                "pending", "filtered", "deferred", "discarded"
            )

    def test_history_nonexistent_source(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider

        class _BrokenSource:
            def list_initiative_events(self, limit=200):
                return []

        p = InitiativeDashboardProvider(initiative_source=_BrokenSource())
        d = p.get_history(limit=10)
        assert d["total"] == 0
        assert d["items"] == []


class TestFallback:
    def test_source_unavailable(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider

        class _BrokenSource:
            def list_initiative_events(self, limit=200):
                raise RuntimeError("source down")

        p = InitiativeDashboardProvider(initiative_source=_BrokenSource())
        for d in (
            p.get_summary(),
            p.list_interests(),
            p.list_actions(),
            p.list_filtered(),
            p.get_history(),
        ):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_source_returns_non_list(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider

        class _WeirdSource:
            def list_initiative_events(self, limit=200):
                return {"not": "a list"}

        p = InitiativeDashboardProvider(initiative_source=_WeirdSource())
        d = p.get_summary()
        assert d["fallback"] is True
        assert d["fallback_reason"] == "invalid_source_payload"

    def test_source_returns_empty(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider
        p = InitiativeDashboardProvider(initiative_source=_FakeInitiativeSource([]))
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["interest_count"] == 0

    def test_source_returns_invalid_event(self, reset_singletons):
        from src.admin.initiative_dashboard_provider import InitiativeDashboardProvider

        class _WeirdSource:
            def list_initiative_events(self, limit=200):
                return [
                    "not_a_dict",
                    None,
                    123,
                    {
                        "event_id": "x",
                        "event_type": "integration.interest_signal.created",
                        "payload": {
                            "signal_id": "sig_x", "topic": "t", "trend": "new",
                        },
                    },
                ]

        p = InitiativeDashboardProvider(initiative_source=_WeirdSource())
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["interest_count"] == 1


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.initiative_router import initiative_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(initiative_v2_bp)
        return app.test_client()

    def test_summary_endpoint(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["interest_count"] == 3
        assert body["data"]["possible_action_count"] == 4
        assert body["data"]["filtered_count"] == 2

    def test_interests_endpoint(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/interests?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 3
        assert len(body["data"]["items"]) == 3

    def test_interests_endpoint_filter_trend(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/interests?trend=rising")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["total"] == 1
        assert body["data"]["items"][0]["topic"] == "钢琴"

    def test_actions_endpoint(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/actions?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 4
        assert len(body["data"]["items"]) == 4

    def test_actions_endpoint_filter_status(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/actions?status=pending")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["total"] == 2
        assert body["data"]["filter_status"] == "pending"

    def test_filtered_endpoint(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/filtered?limit=20")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 2
        items_by_id = {it["action_id"]: it for it in body["data"]["items"]}
        assert items_by_id["act_003"]["filter_policy"] == "low_confidence"
        assert items_by_id["act_004"]["filter_policy"] == "low_expected_value"

    def test_history_endpoint(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/history?limit=20")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 7

    def test_fallback_envelope_shape(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod

        class _BrokenSource:
            def list_initiative_events(self, limit=200):
                raise RuntimeError("broken")

        pmod._provider_instance = pmod.InitiativeDashboardProvider(
            initiative_source=_BrokenSource()
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/summary")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert "fallback_reason" in body

    def test_invalid_trend_falls_back(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/interests?trend=invalid")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["filter_trend"] is None
        assert body["data"]["total"] == 3

    def test_invalid_status_falls_back(self, reset_singletons):
        from src.admin import initiative_dashboard_provider as pmod
        source = _FakeInitiativeSource(_make_events())
        pmod._provider_instance = pmod.InitiativeDashboardProvider(initiative_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/initiative/actions?status=invalid")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["filter_status"] is None
        assert body["data"]["total"] == 4
