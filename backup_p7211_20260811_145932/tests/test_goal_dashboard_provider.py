# -*- coding: utf-8 -*-
"""
tests/test_goal_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 8.1 —— GoalDashboardProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块(goal / initiative / personality)
  2. 只读访问(不调用任何写方法)
  3. Goal 读取成功(summary / current / list / detail)
  4. History 读取成功
  5. fallback 路径(Provider 不可用 / 异常 / 空数据 / 找不到)
  6. API 响应结构
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

class _FakeGoalSource:
    """模拟 Goal Source,仅暴露 read-only 接口。"""

    def __init__(self, events: List[Dict[str, Any]]):
        self._events = list(events)
        self._calls: List[str] = []

    def list_goal_events(self, limit: int = 200):
        self._calls.append("list_goal_events")
        return list(self._events)


def _make_event(
    goal_id: str,
    *,
    event_type: str = "integration.goal.created",
    timestamp: float = 0.0,
    goal_type: str = "personal_growth",
    status: str = "candidate",
    title: str = "",
    description: str = "",
    priority: float = 0.5,
    importance: float = 0.5,
    confidence: float = 0.5,
    lifecycle: str = "created",
    reason: str = "",
    source_event_ids: List[str] = None,
    source_desire_ids: List[str] = None,
) -> Dict[str, Any]:
    """构造一个 IntegrationEvent 的 dict 形式。"""
    payload = {
        "goal_id": goal_id,
        "title": title,
        "description": description,
        "goal_type": goal_type,
        "status": status,
        "priority": priority,
        "importance": importance,
        "confidence": confidence,
        "lifecycle": lifecycle,
        "reason": reason,
        "source_event_ids": list(source_event_ids or []),
        "source_desire_ids": list(source_desire_ids or []),
        "created_at": timestamp,
    }
    return {
        "event_id": f"evt_{goal_id}_{event_type.split('.')[-1]}",
        "event_type": event_type,
        "source": "goal",
        "timestamp": timestamp,
        "payload": payload,
        "related_ids": [goal_id] + list(source_event_ids or []),
        "metadata": {"schema_version": "1.0"},
    }


def _make_plan_event(
    goal_id: str,
    *,
    plan_id: str = "plan_001",
    timestamp: float = 0.0,
    step_count: int = 5,
    progress: float = 0.4,
    title: str = "计划标题",
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_{goal_id}_plan",
        "event_type": "integration.goal.plan_created",
        "source": "goal",
        "timestamp": timestamp,
        "payload": {
            "plan_id": plan_id,
            "goal_id": goal_id,
            "title": title,
            "step_count": step_count,
            "progress": progress,
            "step_ids": [f"step_{i}" for i in range(step_count)],
            "step_titles": [f"步骤{i+1}" for i in range(step_count)],
        },
        "related_ids": [plan_id, goal_id],
        "metadata": {"schema_version": "1.0"},
    }


def _make_events():
    """构造一组混合状态/类型的 goal events。"""
    now = time.time()
    return [
        _make_event(
            "goal_active_001",
            event_type="integration.goal.created",
            timestamp=now - 3600,
            goal_type="personal_growth",
            status="active",
            title="学会弹一首完整的钢琴曲",
            description="用户希望学会弹奏一首完整曲目",
            priority=0.85,
            importance=0.9,
            confidence=0.75,
            lifecycle="created",
            reason="用户表达了学习钢琴的愿望",
            source_event_ids=["evt_signal_1", "evt_signal_2"],
            source_desire_ids=["desire_001"],
        ),
        _make_event(
            "goal_active_002",
            event_type="integration.goal.updated",
            timestamp=now - 1800,
            goal_type="learning",
            status="active",
            title="坚持每日阅读 30 分钟",
            description="培养每日阅读习惯",
            priority=0.7,
            importance=0.75,
            confidence=0.8,
            lifecycle="activated",
            reason="基于用户历史行为激活",
            source_event_ids=["evt_signal_3"],
        ),
        _make_event(
            "goal_paused_001",
            event_type="integration.goal.updated",
            timestamp=now - 7200,
            goal_type="creative",
            status="paused",
            title="尝试写短篇小说",
            priority=0.4,
            importance=0.5,
            confidence=0.6,
            lifecycle="paused",
            reason="用户近期忙碌",
            source_event_ids=["evt_signal_4"],
        ),
        _make_event(
            "goal_completed_001",
            event_type="integration.goal.created",
            timestamp=now - 86400,
            goal_type="learning",
            status="completed",
            title="完成 Python 入门学习",
            priority=0.6,
            importance=0.7,
            confidence=0.95,
            lifecycle="completed",
            reason="已完成",
            source_event_ids=["evt_signal_5"],
        ),
        _make_plan_event(
            "goal_active_001",
            plan_id="plan_piano_001",
            timestamp=now - 3000,
            step_count=8,
            progress=0.25,
            title="钢琴学习 8 步计划",
        ),
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.goal_dashboard_provider import reset_goal_dashboard_provider_for_testing
    reset_goal_dashboard_provider_for_testing()
    yield
    reset_goal_dashboard_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import goal_dashboard_provider as mod
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
        """只调用 list_goal_events,不调用任何写方法。"""
        from src.admin.goal_dashboard_provider import GoalDashboardProvider

        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        p.get_summary()
        p.get_current()
        p.list_goals(limit=5)
        p.list_goals(limit=5, status="active")
        p.get_goal("goal_active_001")
        p.get_history(limit=10)

        for call in source._calls:
            assert call == "list_goal_events", f"unexpected call: {call}"

        # Provider 自身不应有写方法
        forbidden_methods = [
            "_save", "_write", "_append", "_store", "_update", "_delete", "_remove", "_modify",
            "create_goal", "update_goal", "delete_goal", "pause_goal", "complete_goal",
        ]
        for name in dir(p):
            for f in forbidden_methods:
                if name.startswith(f) and callable(getattr(p, name, None)):
                    # 允许只读 _update_cache 等
                    if name in ("_update_cache",):
                        continue
                    pytest.fail(f"unexpected write-like method: {name}")


class TestSummary:
    def test_real_data(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        # 4 个唯一 goal_id(plan 事件不计入主 cache)
        assert d["total_count"] == 4
        by_status = d["by_status"]
        assert by_status["active"] == 2
        assert by_status["paused"] == 1
        assert by_status["completed"] == 1
        assert d["active_count"] == 2
        # 最新:goal_active_002(ts 最大)
        assert d["last_goal_id"] == "goal_active_002"

    def test_empty_returns_not_fallback(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource([])
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 0
        assert d["active_count"] == 0
        assert d["last_goal_id"] is None


class TestCurrent:
    def test_pick_active_with_highest_priority(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_current()
        assert d["available"] is True
        assert d["fallback"] is False
        cur = d["current"]
        assert cur is not None
        # goal_active_001 priority=0.85 高于 goal_active_002 的 0.7
        assert cur["goal_id"] == "goal_active_001"
        assert cur["status"] == "active"
        assert cur["priority"] == 0.85
        assert "title" in cur
        assert cur["title"] == "学会弹一首完整的钢琴曲"

    def test_no_active_returns_fallback(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        # 只有 paused / completed
        events = [
            _make_event("goal_paused_x", status="paused", timestamp=time.time()),
            _make_event("goal_completed_x", status="completed", timestamp=time.time()),
        ]
        source = _FakeGoalSource(events)
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_current()
        # 有数据但没有 active,降级为 fallback + 最近非终止态
        assert d["available"] is True
        assert d["fallback"] is True
        assert d["fallback_reason"] == "no_active_goal_fallback_to_recent"
        assert d["current"] is not None
        assert d["current"]["status"] == "paused"

    def test_completely_empty(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource([])
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_current()
        assert d["available"] is True
        assert d["current"] is None
        assert d["fallback"] is False
        assert d["fallback_reason"] == "no_active_goal"


class TestList:
    def test_default_list(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.list_goals(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total"] == 4
        assert d["total_unfiltered"] == 4
        assert len(d["items"]) == 4
        first = d["items"][0]
        for k in ("goal_id", "title", "status", "goal_type", "created_at", "priority"):
            assert k in first, f"missing field: {k}"

    def test_filter_by_status_active(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.list_goals(limit=10, status="active")
        assert d["fallback"] is False
        assert d["total"] == 2
        assert d["filter_status"] == "active"
        assert all(it["status"] == "active" for it in d["items"])

    def test_filter_by_status_completed(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.list_goals(limit=10, status="completed")
        assert d["total"] == 1
        assert d["items"][0]["goal_id"] == "goal_completed_001"

    def test_invalid_status_falls_back_to_all(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.list_goals(limit=10, status="invalid_status")
        assert d["filter_status"] is None
        assert d["total"] == 4

    def test_limit_clamp(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.list_goals(limit=99999)
        assert len(d["items"]) == 4


class TestDetail:
    def test_get_existing_with_plan(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_goal("goal_active_001")
        assert d["available"] is True
        assert d["fallback"] is False
        g = d["goal"]
        assert g["goal_id"] == "goal_active_001"
        assert g["title"] == "学会弹一首完整的钢琴曲"
        assert g["status"] == "active"
        assert g["goal_type"] == "personal_growth"
        assert abs(g["priority"] - 0.85) < 0.001
        assert abs(g["confidence"] - 0.75) < 0.001
        assert g["has_plan"] is True
        assert g["plan"]["plan_id"] == "plan_piano_001"
        assert g["plan"]["step_count"] == 8
        assert abs(g["plan"]["progress"] - 0.25) < 0.001
        assert len(g["source_event_ids"]) == 2
        assert len(g["source_desire_ids"]) == 1

    def test_get_existing_without_plan(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_goal("goal_paused_001")
        assert d["available"] is True
        assert d["fallback"] is False
        g = d["goal"]
        assert g["has_plan"] is False
        assert "plan" not in g

    def test_get_nonexistent_returns_fallback(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_goal("goal_nonexistent")
        assert d["fallback"] is True
        assert d["goal"] is None
        assert d["fallback_reason"] == "goal_not_found"

    def test_invalid_id_returns_fallback(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_goal("")
        assert d["fallback"] is True
        assert d["fallback_reason"] == "invalid_goal_id"


class TestHistory:
    def test_history_real_data(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_history(limit=20)
        assert d["available"] is True
        assert d["fallback"] is False
        # 4 个 goal 事件 + 1 个 plan 事件 = 5
        assert d["total"] == 5
        assert len(d["items"]) == 5
        first = d["items"][0]
        for k in ("event_id", "event_type", "goal_id", "timestamp"):
            assert k in first, f"missing field: {k}"

    def test_history_filter_types(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_history(limit=20)
        event_types = {it["event_type"] for it in d["items"]}
        assert "integration.goal.created" in event_types
        assert "integration.goal.updated" in event_types
        assert "integration.goal.plan_created" in event_types

    def test_history_chronological_desc(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        source = _FakeGoalSource(_make_events())
        p = GoalDashboardProvider(goal_source=source)
        d = p.get_history(limit=20)
        # 按 timestamp 倒序
        ts_list = [it["timestamp_ts"] for it in d["items"]]
        assert ts_list == sorted(ts_list, reverse=True)

    def test_history_nonexistent_source(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider

        class _BrokenSource:
            def list_goal_events(self, limit=200):
                return []

        p = GoalDashboardProvider(goal_source=_BrokenSource())
        d = p.get_history(limit=10)
        # 缓存为空,source 也不返回
        assert d["total"] == 0
        assert d["items"] == []


class TestFallback:
    def test_source_unavailable(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider

        class _BrokenSource:
            def list_goal_events(self, limit=200):
                raise RuntimeError("source down")

        p = GoalDashboardProvider(goal_source=_BrokenSource())
        for d in (
            p.get_summary(),
            p.get_current(),
            p.list_goals(),
            p.get_goal("x"),
            p.get_history(),
        ):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_source_returns_non_list(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider

        class _WeirdSource:
            def list_goal_events(self, limit=200):
                return {"not": "a list"}

        p = GoalDashboardProvider(goal_source=_WeirdSource())
        d = p.get_summary()
        assert d["fallback"] is True
        assert d["fallback_reason"] == "invalid_source_payload"

    def test_source_returns_empty(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider
        p = GoalDashboardProvider(goal_source=_FakeGoalSource([]))
        d = p.get_summary()
        # 有 source 返回数据(虽然空),不应被判定为 fallback
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 0

    def test_source_returns_invalid_event(self, reset_singletons):
        from src.admin.goal_dashboard_provider import GoalDashboardProvider

        class _WeirdSource:
            def list_goal_events(self, limit=200):
                return [
                    "not_a_dict",
                    None,
                    123,
                    {"event_id": "x", "event_type": "integration.goal.created", "payload": {"goal_id": "g1", "status": "active"}},
                ]

        p = GoalDashboardProvider(goal_source=_WeirdSource())
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 1


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.goal_router import goal_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(goal_v2_bp)
        return app.test_client()

    def test_summary_endpoint(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total_count"] == 4
        assert body["data"]["by_status"]["active"] == 2

    def test_current_endpoint(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/current")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["current"] is not None
        assert body["data"]["current"]["status"] == "active"

    def test_list_endpoint(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/list?limit=10&status=active")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 2
        assert body["data"]["filter_status"] == "active"

    def test_detail_endpoint(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/goal_active_001")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["goal"]["goal_id"] == "goal_active_001"
        assert body["data"]["goal"]["has_plan"] is True

    def test_history_endpoint(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/history?limit=20")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 5

    def test_fallback_envelope_shape(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod

        class _BrokenSource:
            def list_goal_events(self, limit=200):
                raise RuntimeError("broken")

        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=_BrokenSource())
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/summary")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert "fallback_reason" in body

    def test_notfound_returns_fallback(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/goal_nonexistent")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert body["fallback_reason"] == "goal_not_found"

    def test_invalid_status_falls_back(self, reset_singletons):
        from src.admin import goal_dashboard_provider as pmod
        source = _FakeGoalSource(_make_events())
        pmod._provider_instance = pmod.GoalDashboardProvider(goal_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/goal/list?status=invalid")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["filter_status"] is None
        assert body["data"]["total"] == 4
