# -*- coding: utf-8 -*-
"""
tests/test_memory_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.3 —— MemoryDashboardProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块
  2. 只读访问(不调用 add / add_many / delete 等)
  3. 真实数据返回(summary / recent / important / timeline / detail)
  4. fallback 路径(Provider 不可用 / 异常 / 空数据)
  5. 空 Memory 状态
  6. API 响应结构
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers
# =====================================================================

class _FakeStore:
    """模拟 MemoryStore,仅暴露 read-only 接口。"""

    def __init__(self, memories: List[Dict[str, Any]]):
        self._memories = list(memories)
        self._calls: List[str] = []

    def load(self) -> List[Dict[str, Any]]:
        self._calls.append("load")
        return list(self._memories)

    def get_by_id(self, memory_id: str):
        self._calls.append("get_by_id")
        for m in self._memories:
            if m.get("id") == memory_id:
                return m
        return None

    def count(self) -> int:
        return len(self._memories)


class _FakeRuntimeProvider:
    def __init__(self, store: Any):
        self._store = store

    def get_memory_store(self):
        return self._store


def _make_memories():
    """构造一组混合重要度 / 时间戳的记忆。"""
    now = datetime.now()
    old = (now - timedelta(days=20)).isoformat()
    week = (now - timedelta(days=3)).isoformat()
    today = now.isoformat()
    return [
        {
            "id": "mem_001",
            "user_id": "u1",
            "role": "user",
            "content": "今天天气真好,适合散步。",
            "timestamp": today,
            "metadata": {"importance": 0.3, "memory_type": "casual", "owner": "u1"},
        },
        {
            "id": "mem_002",
            "user_id": "u1",
            "role": "user",
            "content": "我最近在学钢琴,进步很大。",
            "timestamp": today,
            "metadata": {"importance": 0.85, "memory_type": "achievement", "owner": "u1"},
        },
        {
            "id": "mem_003",
            "user_id": "u1",
            "role": "assistant",
            "content": "你已经坚持学琴三周了,很棒!",
            "timestamp": week,
            "metadata": {"importance": 0.6, "memory_type": "encouragement", "owner": "u1"},
        },
        {
            "id": "mem_004",
            "user_id": "u1",
            "role": "user",
            "content": "我最喜欢的季节是秋天。",
            "timestamp": week,
            "metadata": {"importance": 0.75, "memory_type": "preference", "owner": "u1"},
        },
        {
            "id": "mem_005",
            "user_id": "u1",
            "role": "user",
            "content": "去年这个时候我在准备考试。",
            "timestamp": old,
            "metadata": {"importance": 0.2, "memory_type": "casual", "owner": "u1"},
        },
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.memory_dashboard_provider import reset_memory_dashboard_provider_for_testing
    reset_memory_dashboard_provider_for_testing()
    yield
    reset_memory_dashboard_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import memory_dashboard_provider as mod
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

    def test_no_writes_called(self, reset_singletons):
        """只调用 load / get_by_id,不调用 add / add_many / delete。"""
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider

        store = _FakeStore(_make_memories())
        rp = _FakeRuntimeProvider(store)
        p = MemoryDashboardProvider(runtime_provider=rp)
        p.get_summary()
        p.list_recent(5)
        p.list_important(5)
        p.get_timeline("7d")
        p.get_memory("mem_001")
        # 任何 *_calls 中的方法都不是写方法
        for call in store._calls:
            assert call in ("load", "get_by_id"), f"unexpected call: {call}"


class TestSummary:
    def test_real_data(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_summary()
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total_count"] == 5
        # 重要:>= 0.7 的 = mem_002(0.85),mem_004(0.75) = 2
        assert d["important_count"] == 2
        # recent_count:7 天内 = today(2) + week(2) = 4
        assert d["recent_count"] == 4
        # last_update:最大的 timestamp
        assert d["last_update"] is not None


class TestRecent:
    def test_sorted_by_timestamp_desc(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.list_recent(limit=3)
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["total"] == 5
        assert len(d["items"]) == 3
        # 倒序:第一条应该是今天的两条之一
        first_id = d["items"][0]["id"]
        assert first_id in ("mem_001", "mem_002")

    def test_limit_clamp(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.list_recent(limit=99999)
        # 实际 limit 被 clamp 到 MAX_LIMIT=200,5 条全返回
        assert len(d["items"]) == 5

    def test_invalid_limit_falls_back_to_default(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.list_recent(limit="abc")
        # 仍能正常返回
        assert d["fallback"] is False
        assert isinstance(d["items"], list)


class TestImportant:
    def test_only_high_importance(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.list_important(limit=10)
        assert d["available"] is True
        assert d["fallback"] is False
        # total = 全部 >=0.7 的数量
        assert d["total"] == 2
        assert len(d["items"]) == 2
        # 都应该 >= 0.7
        for it in d["items"]:
            assert float(it["importance"]) >= 0.7
        # 第一条 importance 最高
        assert d["items"][0]["importance"] >= d["items"][-1]["importance"]


class TestTimeline:
    def test_daily_buckets(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_timeline("7d")
        assert d["available"] is True
        assert d["fallback"] is False
        assert d["range"] == "7d"
        # 7 天内 = 4 条(today + week)
        assert d["total"] == 4
        # 至少一个 bucket
        assert len(d["buckets"]) >= 1
        # 每个 bucket 有 date / count / important_count
        for b in d["buckets"]:
            assert "date" in b
            assert "count" in b
            assert "important_count" in b

    def test_range_parsing(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_timeline("30d")
        assert d["range"] == "30d"
        # 30d 内应该所有 5 条都包含
        assert d["total"] == 5

    def test_invalid_range_defaults_to_7d(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_timeline("invalid")
        assert d["range"] == "7d"
        assert d["fallback"] is False


class TestMemoryDetail:
    def test_get_existing(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_memory("mem_002")
        assert d["available"] is True
        assert d["fallback"] is False
        m = d["memory"]
        assert m["id"] == "mem_002"
        assert m["memory_type"] == "achievement"
        assert abs(float(m["importance"]) - 0.85) < 0.001

    def test_get_nonexistent_returns_fallback(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_memory("mem_999")
        assert d["fallback"] is True
        assert d["memory"] is None

    def test_invalid_id_returns_fallback(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        p = MemoryDashboardProvider(runtime_provider=rp)
        d = p.get_memory("")
        assert d["fallback"] is True


class TestFallback:
    def test_runtime_provider_unavailable(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider

        class _BrokenRP:
            def get_memory_store(self):
                raise RuntimeError("rp down")

        p = MemoryDashboardProvider(runtime_provider=_BrokenRP())
        for d in (p.get_summary(), p.list_recent(), p.list_important(),
                  p.get_timeline(), p.get_memory("x")):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_store_unavailable(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider

        class _NoStoreRP:
            def get_memory_store(self):
                return None

        p = MemoryDashboardProvider(runtime_provider=_NoStoreRP())
        for d in (p.get_summary(), p.list_recent(), p.list_important(),
                  p.get_timeline(), p.get_memory("x")):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_store_load_raises(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider

        class _BadStore:
            def load(self):
                raise RuntimeError("fs down")

        rp = _FakeRuntimeProvider(_BadStore())
        p = MemoryDashboardProvider(runtime_provider=rp)
        for d in (p.get_summary(), p.list_recent(), p.list_important(), p.get_timeline()):
            assert d.get("fallback") is True, f"expected fallback: {d}"

    def test_empty_memory_state(self, reset_singletons):
        """空 Memory 状态:不返回 fallback(因为有数据,只是为空)。"""
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        rp = _FakeRuntimeProvider(_FakeStore([]))
        p = MemoryDashboardProvider(runtime_provider=rp)
        s = p.get_summary()
        assert s["fallback"] is False
        assert s["total_count"] == 0
        r = p.list_recent()
        assert r["fallback"] is False
        assert r["items"] == []
        i = p.list_important()
        assert i["fallback"] is False
        assert i["items"] == []
        t = p.get_timeline("7d")
        assert t["fallback"] is False
        assert t["buckets"] == []

    def test_store_returns_non_list(self, reset_singletons):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider

        class _WeirdStore:
            def load(self):
                return {"not": "a list"}

        rp = _FakeRuntimeProvider(_WeirdStore())
        p = MemoryDashboardProvider(runtime_provider=rp)
        s = p.get_summary()
        assert s["fallback"] is True


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.memory_router import memory_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(memory_v2_bp)
        return app.test_client()

    def test_summary_endpoint(self, reset_singletons):
        from src.admin import memory_dashboard_provider as pmod
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=rp)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total_count"] == 5
        assert body["data"]["important_count"] == 2

    def test_recent_endpoint(self, reset_singletons):
        from src.admin import memory_dashboard_provider as pmod
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=rp)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/recent?limit=3")
        body = resp.get_json()
        assert body["ok"] is True
        assert len(body["data"]["items"]) == 3
        assert body["data"]["total"] == 5

    def test_important_endpoint(self, reset_singletons):
        from src.admin import memory_dashboard_provider as pmod
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=rp)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/important?limit=10")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["total"] == 2
        assert body["data"]["threshold"] == 0.7

    def test_timeline_endpoint(self, reset_singletons):
        from src.admin import memory_dashboard_provider as pmod
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=rp)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/timeline?range=7d")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["range"] == "7d"
        assert body["data"]["total"] == 4

    def test_detail_endpoint(self, reset_singletons):
        from src.admin import memory_dashboard_provider as pmod
        rp = _FakeRuntimeProvider(_FakeStore(_make_memories()))
        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=rp)
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/mem_002")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["memory"]["id"] == "mem_002"

    def test_fallback_envelope_shape(self, reset_singletons):
        """当 Provider fallback 时,响应必须包含 fallback=true。"""
        from src.admin import memory_dashboard_provider as pmod

        class _BrokenRP:
            def get_memory_store(self):
                return None

        pmod._provider_instance = pmod.MemoryDashboardProvider(runtime_provider=_BrokenRP())
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/memory/summary")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert "fallback_reason" in body
