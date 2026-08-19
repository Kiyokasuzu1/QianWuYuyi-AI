# -*- coding: utf-8 -*-
"""
tests/test_reflection_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.4 —— ReflectionDashboardProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块(reflection / growth / memory / personality)
  2. 只读访问(不调用写方法)
  3. Reflection 读取成功
  4. Insight 读取成功(summary + 完整列表)
  5. Evidence Chain 完整
  6. fallback 路径(Provider 不可用 / 异常 / 空数据 / 找不到)
  7. API 响应结构
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

class _FakeReflectionSource:
    """模拟 Reflection Source,仅暴露 read-only 接口。"""

    def __init__(self, events: List[Dict[str, Any]]):
        self._events = list(events)
        self._calls: List[str] = []

    def list_reflection_events(self, limit: int = 200):
        self._calls.append("list_reflection_events")
        return list(self._events)


def _make_event(
    reflection_id: str,
    *,
    reflection_type: str = "daily",
    timestamp: float = 0.0,
    source_event_ids: List[str] = None,
    insight_count: int = 0,
    suggestion_count: int = 0,
    confidence: float = 0.5,
    evidence_strength: float = 0.5,
    summary: str = "",
    content: str = "",
    related_memory: List[Any] = None,
    suggested_changes: List[Dict[str, Any]] = None,
    insights: List[Dict[str, Any]] = None,
    event_type: str = None,
) -> Dict[str, Any]:
    """构造一个 IntegrationEvent 的 dict 形式。"""
    if event_type is None:
        # 默认 event_type
        if reflection_type == "daily":
            event_type = "integration.reflection.daily_completed"
        elif reflection_type == "growth":
            event_type = "integration.reflection.growth_completed"
        elif reflection_type == "event":
            event_type = "integration.reflection.event_completed"
        else:
            event_type = "integration.reflection.completed"
    payload = {
        "reflection_id": reflection_id,
        "reflection_type": reflection_type,
        "source_event_ids": list(source_event_ids or []),
        "insight_count": insight_count,
        "suggestion_count": suggestion_count,
        "confidence": confidence,
        "evidence_strength": evidence_strength,
        "summary": summary,
        "content": content,
        "triggered_at": timestamp,
    }
    if related_memory is not None:
        payload["related_memory"] = related_memory
    if suggested_changes is not None:
        payload["suggested_changes"] = suggested_changes
    if insights is not None:
        payload["insights"] = insights
    return {
        "event_id": f"evt_{reflection_id}",
        "event_type": event_type,
        "source": "reflection",
        "timestamp": timestamp,
        "payload": payload,
        "related_ids": [reflection_id] + list(source_event_ids or []),
        "metadata": {"schema_version": "1.0"},
    }


def _make_events():
    """构造一组混合类型/时间的 reflection events。"""
    now = time.time()
    return [
        _make_event(
            "ref_daily_001",
            reflection_type="daily",
            timestamp=now - 60,
            source_event_ids=["evt_1", "evt_2"],
            insight_count=3,
            suggestion_count=1,
            confidence=0.85,
            evidence_strength=0.7,
            summary="今日与用户交流愉快",
            content="今天的对话感觉自然,用户提到了旅行计划。",
        ),
        _make_event(
            "ref_event_001",
            reflection_type="event",
            timestamp=now - 30,
            source_event_ids=["evt_3"],
            insight_count=2,
            suggestion_count=0,
            confidence=0.75,
            evidence_strength=0.65,
            summary="事件:用户分享了一张照片",
        ),
        _make_event(
            "ref_growth_001",
            reflection_type="growth",
            timestamp=now,
            source_event_ids=["evt_4", "evt_5", "evt_6"],
            insight_count=5,
            suggestion_count=2,
            confidence=0.9,
            evidence_strength=0.8,
            summary="成长:本周的共情能力提升",
            content="羽依发现自己更擅长捕捉用户的情绪变化。",
            related_memory=[
                {"id": "mem_001", "summary": "用户提到学钢琴", "importance": 0.8},
                {"id": "mem_002", "summary": "用户喜欢秋天", "importance": 0.7},
            ],
            suggested_changes=[
                {
                    "target_field": "empathy",
                    "delta": 0.05,
                    "reason": "本周共情表现稳定",
                    "evidence_event_ids": ["evt_4", "evt_5"],
                    "confidence": 0.85,
                },
            ],
            insights=[
                {
                    "insight_id": "ins_001",
                    "category": "pattern",
                    "description": "羽依在处理情绪类对话时更得心应手",
                    "supporting_event_ids": ["evt_4", "evt_5", "evt_6"],
                    "confidence": 0.85,
                },
                {
                    "insight_id": "ins_002",
                    "category": "preference",
                    "description": "用户倾向于简短回答",
                    "supporting_event_ids": ["evt_6"],
                    "confidence": 0.7,
                },
            ],
        ),
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.reflection_dashboard_provider import reset_reflection_dashboard_provider_for_testing
    reset_reflection_dashboard_provider_for_testing()
    yield
    reset_reflection_dashboard_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.runtime.reflection", "import src.runtime.reflection",
            "from src.runtime.growth", "import src.runtime.growth",
            "from src.runtime.personality", "import src.runtime.personality",
            "from src.runtime.memory", "import src.runtime.memory",
            "from src.memory", "import src.memory",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.emotion", "import src.emotion",
            "from src.relationship", "import src.relationship",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

        # 同时禁止直接 import 三个核心类
        forbidden_classes = [
            "ReflectionEngine", "GrowthEngine", "MemoryStore",
        ]
        for f in forbidden_classes:
            # 检查 class name 直接出现(避免误判字符串)
            lines = [ln for ln in src.split("\n") if f in ln and not ln.strip().startswith("#")]
            for ln in lines:
                # 允许出现在注释/字符串/docstring 中(已经在 # 处理了)
                # 不允许出现"import" / "from" 形式
                if "import" in ln.lower():
                    assert False, f"禁止 import: {ln.strip()}"
            # 无报错即为通过

    def test_no_writes_called(self, reset_singletons):
        """只调用 list_reflection_events,不调用任何写方法。"""
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider

        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        p.get_summary()
        p.list_reflections(limit=5)
        p.list_reflections(limit=5, type="daily")
        p.get_reflection("ref_daily_001")
        p.get_insights("ref_growth_001")
        p.get_evidence_chain("ref_growth_001")

        # 任何 _calls 中的方法都不是写方法
        for call in source._calls:
            assert call == "list_reflection_events", f"unexpected call: {call}"

        # Provider 自身不应有 _save / _write / _append / _store 等写方法被调用
        forbidden_methods = [
            "_save", "_write", "_append", "_store", "_update", "_delete", "_remove", "_modify"
        ]
        for name in dir(p):
            for f in forbidden_methods:
                if name.startswith(f) and callable(getattr(p, name, None)):
                    pytest.fail(f"unexpected write-like method: {name}")


class TestSummary:
    def test_real_data(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 3
        by_type = d["by_type"]
        assert by_type["daily"] == 1
        assert by_type["event"] == 1
        assert by_type["growth"] == 1
        assert d["last_reflection_id"] == "ref_growth_001"  # timestamp 最大
        assert d["last_reflection_at"] is not None

    def test_empty_returns_not_fallback(self, reset_singletons):
        """空 source:不返回 fallback(因为有数据,只是为空)。"""
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource([])
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 0
        assert d["by_type"] == {"daily": 0, "event": 0, "growth": 0}
        assert d["last_reflection_id"] is None


class TestList:
    def test_default_list(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.list_reflections(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total"] == 3
        assert d["total_unfiltered"] == 3
        assert len(d["items"]) == 3
        # 按 timestamp 倒序:第一条是 ref_growth_001
        assert d["items"][0]["reflection_id"] == "ref_growth_001"
        # 字段
        first = d["items"][0]
        for k in ("reflection_id", "type", "created_at", "summary", "status"):
            assert k in first, f"missing field: {k}"

    def test_filter_by_type_daily(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.list_reflections(limit=10, type="daily")
        assert d["fallback"] is False
        assert d["total"] == 1
        assert d["filter_type"] == "daily"
        assert d["items"][0]["type"] == "daily"

    def test_filter_by_type_growth(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.list_reflections(limit=10, type="growth")
        assert d["total"] == 1
        assert d["items"][0]["reflection_id"] == "ref_growth_001"

    def test_invalid_type_falls_back_to_all(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.list_reflections(limit=10, type="invalid_type")
        assert d["filter_type"] is None
        assert d["total"] == 3

    def test_limit_clamp(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.list_reflections(limit=99999)
        # 实际 limit 被 clamp 到 MAX_LIMIT=200
        assert len(d["items"]) == 3


class TestDetail:
    def test_get_existing(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_reflection("ref_growth_001")
        assert d["available"] is True
        assert d["fallback"] is False
        r = d["reflection"]
        assert r["reflection_id"] == "ref_growth_001"
        assert r["type"] == "growth"
        assert r["summary"] == "成长:本周的共情能力提升"
        assert r["content"] == "羽依发现自己更擅长捕捉用户的情绪变化。"
        assert len(r["source_events"]) == 3
        assert abs(r["confidence"] - 0.9) < 0.001
        assert abs(r["evidence_strength"] - 0.8) < 0.001
        assert r["insight_count"] == 5
        assert r["suggestion_count"] == 2

    def test_get_nonexistent_returns_fallback(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_reflection("ref_nonexistent")
        assert d["fallback"] is True
        assert d["reflection"] is None
        assert d["fallback_reason"] == "reflection_not_found"

    def test_invalid_id_returns_fallback(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_reflection("")
        assert d["fallback"] is True
        assert d["fallback_reason"] == "invalid_reflection_id"


class TestInsights:
    def test_get_insights_with_full_items(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_insights("ref_growth_001")
        assert d["available"] is True
        # payload 中有完整 insights
        assert d["fallback"] is False
        assert d["total"] == 2
        items = d["items"]
        assert len(items) == 2
        for it in items:
            for k in ("insight_id", "category", "description", "confidence", "evidence_count"):
                assert k in it, f"missing field: {k}"
        # confidence 排序不要求,只验证存在
        assert items[0]["insight_id"] == "ins_001"
        assert items[0]["evidence_count"] == 3

    def test_get_insights_summary_only(self, reset_singletons):
        """当 payload 仅有 insight_count 没有 insights 列表时,降级为 summary + fallback。"""
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())  # _make_events 中 daily 没用 insights 列表
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_insights("ref_daily_001")
        # payload 中没有 insights 字段
        assert d["available"] is True
        assert d["fallback"] is True
        assert d["fallback_reason"] == "insight_detail_unavailable"
        assert d["total"] == 3  # 从 insight_count
        assert d["items"] == []  # 不伪造
        # summary 存在
        assert d["summary"]["insight_count"] == 3
        assert d["summary"]["suggestion_count"] == 1

    def test_get_insights_nonexistent(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_insights("ref_nonexistent")
        assert d["fallback"] is True
        assert d["fallback_reason"] == "reflection_not_found"


class TestEvidenceChain:
    def test_get_evidence_chain_with_full_data(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_evidence_chain("ref_growth_001")
        assert d["available"] is True
        assert d["fallback"] is False
        chain = d["chain"]
        # reflection 节点
        assert chain["reflection"] is not None
        assert chain["reflection"]["reflection_id"] == "ref_growth_001"
        # events 节点
        assert len(chain["events"]) == 3
        event_ids = {e["event_id"] for e in chain["events"]}
        assert event_ids == {"evt_4", "evt_5", "evt_6"}
        # memories 节点
        assert len(chain["memories"]) == 2
        mem_ids = {m["memory_id"] for m in chain["memories"]}
        assert mem_ids == {"mem_001", "mem_002"}
        # evidence 节点(从 suggested_changes.evidence_event_ids 聚合 + source_event_ids)
        evi_ids = {e["evidence_id"] for e in chain["evidence"]}
        # source_event_ids 是 evt_4/5/6,evidence_event_ids 是 evt_4/5(去重后 = evt_4/5/6)
        assert evi_ids == {"evt_4", "evt_5", "evt_6"}
        # links
        assert d["links"][0]["from"] == "reflection"
        assert d["links"][0]["to"] == "events"

    def test_get_evidence_chain_no_evidence(self, reset_singletons):
        """当 reflection 没有 source_events / memories / suggested_changes 时,链仍可用。"""
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        events = [
            _make_event(
                "ref_solo",
                reflection_type="daily",
                timestamp=time.time(),
                source_event_ids=[],
                insight_count=0,
            ),
        ]
        source = _FakeReflectionSource(events)
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_evidence_chain("ref_solo")
        assert d["available"] is True
        assert d["fallback"] is False
        chain = d["chain"]
        assert chain["reflection"] is not None
        assert chain["events"] == []
        assert chain["memories"] == []
        assert chain["evidence"] == []

    def test_get_evidence_chain_nonexistent(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        source = _FakeReflectionSource(_make_events())
        p = ReflectionDashboardProvider(reflection_source=source)
        d = p.get_evidence_chain("ref_nonexistent")
        assert d["fallback"] is True
        assert d["fallback_reason"] == "reflection_not_found"


class TestFallback:
    def test_source_unavailable(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider

        class _BrokenSource:
            def list_reflection_events(self, limit=200):
                raise RuntimeError("source down")

        p = ReflectionDashboardProvider(reflection_source=_BrokenSource())
        for d in (
            p.get_summary(),
            p.list_reflections(),
            p.get_reflection("x"),
            p.get_insights("x"),
            p.get_evidence_chain("x"),
        ):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_source_returns_non_list(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider

        class _WeirdSource:
            def list_reflection_events(self, limit=200):
                return {"not": "a list"}

        p = ReflectionDashboardProvider(reflection_source=_WeirdSource())
        d = p.get_summary()
        assert d["fallback"] is True

    def test_source_returns_empty(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider
        p = ReflectionDashboardProvider(reflection_source=_FakeReflectionSource([]))
        d = p.get_summary()
        # 有 source 返回数据(虽然空),不应被判定为 fallback
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 0

    def test_source_returns_invalid_event(self, reset_singletons):
        from src.admin.reflection_dashboard_provider import ReflectionDashboardProvider

        class _WeirdSource:
            def list_reflection_events(self, limit=200):
                return ["not_a_dict", None, 123, {"event_id": "x", "event_type": "integration.reflection.completed", "payload": {}}]

        p = ReflectionDashboardProvider(reflection_source=_WeirdSource())
        d = p.get_summary()
        # 应当不报错,且 fallback=False
        assert d["available"] is True
        assert d["fallback"] is False
        # 至少有一个 event 被正确解析
        assert d["total_count"] == 1


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.reflection_router import reflection_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(reflection_v2_bp)
        return app.test_client()

    def test_summary_endpoint(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total_count"] == 3
        assert body["data"]["by_type"]["daily"] == 1

    def test_list_endpoint(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/list?limit=10&type=daily")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 1
        assert body["data"]["filter_type"] == "daily"

    def test_detail_endpoint(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/ref_growth_001")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["reflection"]["reflection_id"] == "ref_growth_001"

    def test_insights_endpoint(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/ref_growth_001/insights")
        body = resp.get_json()
        assert body["ok"] is True
        # payload 里有完整 insights
        assert body["data"]["fallback"] is False
        assert body["data"]["total"] == 2

    def test_evidence_endpoint(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/ref_growth_001/evidence")
        body = resp.get_json()
        assert body["ok"] is True
        chain = body["data"]["chain"]
        assert chain["reflection"] is not None
        assert len(chain["events"]) == 3
        assert len(chain["memories"]) == 2

    def test_fallback_envelope_shape(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod

        class _BrokenSource:
            def list_reflection_events(self, limit=200):
                raise RuntimeError("broken")

        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=_BrokenSource())
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/summary")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert "fallback_reason" in body

    def test_notfound_returns_fallback(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/ref_nonexistent")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert body["fallback_reason"] == "reflection_not_found"

    def test_invalid_type_falls_back(self, reset_singletons):
        from src.admin import reflection_dashboard_provider as pmod
        source = _FakeReflectionSource(_make_events())
        pmod._provider_instance = pmod.ReflectionDashboardProvider(reflection_source=source)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/reflection/list?type=invalid")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["filter_type"] is None  # 非法 type 降级为 None
        assert body["data"]["total"] == 3  # 返回全部
