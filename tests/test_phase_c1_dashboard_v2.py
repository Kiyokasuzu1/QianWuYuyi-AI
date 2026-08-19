# -*- coding: utf-8 -*-
"""
tests/test_phase_c1_dashboard_v2.py

Phase C.1 — P1-2: Dashboard V2 新增接口测试

覆盖:
- /api/dashboard/v2/selfmodel/*  6 个 state 端点
- /api/dashboard/v2/memory/*     quality / type-stats / combined
- /api/dashboard/v2/growth/*     summary / recent / evolution-history / combined
- Dashboard 只读约束:不允许修改 / 不允许触发成长

设计原则:
- 通过 mock Provider 注入测试,避免依赖真实 RuntimeBridge
- 所有断言基于 Provider 层的返回数据,跳过 Flask 路由层
- 同时验证 Flask Blueprint 路由可正常 import 与注册
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Fixtures: 重置 Provider 单例 + 提供 mock 自模型/关系/growth 数据
# =====================================================================

@pytest.fixture(autouse=True)
def _reset_providers():
    """每个测试前重置所有 Dashboard Provider 单例,避免状态污染。"""
    from src.admin.selfmodel_dashboard_provider import reset_selfmodel_dashboard_provider_for_testing
    from src.admin.memory_dashboard_provider import reset_memory_dashboard_provider_for_testing
    from src.admin.growth_dashboard_provider import reset_growth_dashboard_provider_for_testing
    reset_selfmodel_dashboard_provider_for_testing()
    reset_memory_dashboard_provider_for_testing()
    reset_growth_dashboard_provider_for_testing()
    yield
    reset_selfmodel_dashboard_provider_for_testing()
    reset_memory_dashboard_provider_for_testing()
    reset_growth_dashboard_provider_for_testing()


class _FakeSMP:
    """Mock SelfModelProvider(满足 SelfModelDashboardProvider 所需接口)。"""

    def __init__(self, *, identity=None, beliefs=None, timeline=None, health=None):
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
            "available": True, "total": 2, "active": 2, "inactive": 0,
            "by_domain": {"trait": 2},
            "items": [
                {"belief_id": "b1", "domain": "trait", "content": "温柔",
                 "confidence": 0.9, "version": 1, "evidence_count": 5, "active": True},
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

    def get_identity(self):
        return dict(self._identity)

    def list_beliefs(self, **kw):
        return dict(self._beliefs)

    def get_evolution_timeline(self, **kw):
        return dict(self._timeline)

    def get_health_report(self):
        return dict(self._health)


class _FakeSMPWithActiveModel(_FakeSMP):
    """扩展 mock:支持 get_active_self_model + get_self_model_store。"""

    def __init__(self, *, sms=None, **kw):
        super().__init__(**kw)
        self._sms = sms  # Optional[_FakeSMS]

    @property
    def _runtime_provider(self):
        return None  # 触发 fallback path


class _FakeSMS:
    """Mock SelfModelStore。"""

    def __init__(self, *, active_model=None, growth_history=None, evolution_history=None,
                 personality_state=None, capability_boundary=None):
        self._active = active_model
        self._growth = growth_history
        self._evolution = evolution_history
        self._personality = personality_state
        self._capability = capability_boundary

    def get_active_self_model(self):
        return self._active

    def get_growth_history(self, **kw):
        if self._growth is None:
            return {"available": True, "records": []}
        return self._growth


class _FakeResolver:
    """Mock PersonalityResolver(供 relationship_state fallback 用)。"""

    def __init__(self, relationship_state=None):
        self.relationship_state = relationship_state
        self.state = None


class _FakeRuntimeProvider:
    """Mock RuntimeProvider。"""

    def __init__(self, *, resolver=None, sms=None):
        self._resolver = resolver
        self._sms = sms

    def get_personality_resolver(self):
        return self._resolver

    def get_self_model_store(self):
        return self._sms


class _FakeStore:
    """Mock MemoryStore。"""

    def __init__(self, memories=None):
        self._memories = memories or []

    def load(self):
        return list(self._memories)

    def get_by_id(self, memory_id):
        for m in self._memories:
            if m.get("id") == memory_id:
                return m
        return None


class _FakeProposal:
    """Mock GrowthProposal(支持 to_dict)。"""

    def __init__(self, d):
        self._d = d

    def to_dict(self):
        return dict(self._d)


class _FakeProposalStorage:
    """Mock ProposalStorage。"""

    def __init__(self, proposals=None):
        self._items = list(proposals or [])

    def list_all(self, limit=50, offset=0):
        items = sorted(self._items, key=lambda p: p.to_dict().get("timestamp", ""), reverse=True)
        return items[offset:offset + limit]


def _make_proposal(pid, status, ts, ptype="personality", confidence=0.7, reason="test", affected=None):
    return _FakeProposal({
        "proposal_id": pid,
        "status": status,
        "timestamp": ts,
        "proposal_type": ptype,
        "confidence": confidence,
        "reason": reason,
        "affected_dimensions": affected or {},
        "after_state": affected or {},
        "source": "test",
    })


# =====================================================================
# Test: selfmodel_dashboard_provider 新方法
# =====================================================================

class TestSelfModelDashboardStateMethods:
    """测试 selfmodel_dashboard_provider 新增的 6 个 state 方法。"""

    def test_get_identity_state_returns_full_view(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        result = provider.get_identity_state()
        assert result["available"] is True
        assert result["identity_name"] == "浅雾羽依"
        assert result["identity_id"] == "sm-1"
        assert "温柔" in result["core_values"]
        assert result["anchor"] == "Yuyi-Anchor"
        assert result["stable"] is True
        assert result["version"] == "v3"

    def test_get_identity_state_fallback_when_smp_none(self):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        provider = SelfModelDashboardProvider(self_model_provider=None)
        result = provider.get_identity_state()
        assert result["available"] is False
        assert result["fallback"] is True
        assert result["identity_name"] == "浅雾羽依"  # 兜底名
        assert "fallback_reason" in result

    def test_get_personality_state_fallback_when_unavailable(self, monkeypatch):
        """当 SelfModelStore + Resolver 都拿不到时,应返回 fallback。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        # monkeypatch runtime_provider 让所有尝试都返回 None
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_personality_state()
        # 期望 fallback(因为我们无法直接 mock SelfModelStore)
        assert "available" in result
        assert "fallback" in result
        assert "traits" in result
        assert isinstance(result["traits"], dict)

    def test_get_relationship_state_fallback(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_relationship_state()
        assert result["available"] is False
        assert result["fallback"] is True
        assert "trust" in result
        assert "familiarity" in result
        assert "bond_strength" in result
        assert "milestones" in result

    def test_get_relationship_state_from_resolver(self, monkeypatch):
        """当 resolver 提供 relationship_state 时,应正确读取。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        resolver = _FakeResolver(relationship_state={
            "trust": 0.55, "familiarity": 0.4, "bond_strength": 0.3,
            "shared_history": 0.2, "promise_level": 0.1, "activity_level": 0.6,
            "milestones": [{"event_id": "m1", "topic": "first_meet"}],
            "last_updated": "2025-08-01T00:00:00",
        })
        rp = _FakeRuntimeProvider(resolver=resolver)
        smp._runtime_provider = rp  # 注入到 _FakeSMP
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        # monkeypatch global
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: rp,
        )
        result = provider.get_relationship_state()
        assert result["available"] is True
        assert result["trust"] == 0.55
        assert result["bond_strength"] == 0.3
        assert result["milestones_count"] == 1

    def test_get_capability_boundary_returns_baseline(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_capability_boundary()
        assert result["available"] is True
        assert isinstance(result["static_limitations"], list)
        assert isinstance(result["runtime_capabilities"], list)
        assert isinstance(result["known_uncertainties"], list)
        # 至少给一个 baseline capability
        assert len(result["runtime_capabilities"]) >= 1
        # Phase C.1 边界:不允许新增主动意识/自主行动
        assert "no_self_initiative" in result["static_limitations"]
        assert "no_agent_autonomy" in result["static_limitations"]
        assert "no_dashboard_mutation" in result["static_limitations"]

    def test_get_growth_history_fallback(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_growth_history(limit=10)
        assert "available" in result
        assert "items" in result
        assert isinstance(result["items"], list)

    def test_get_personality_evolution_history_fallback(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_personality_evolution_history(limit=10)
        assert "available" in result
        assert "items" in result
        assert "applied_count" in result
        assert "rolled_back_count" in result
        assert "current_personality_state" in result

    def test_get_full_state_contains_all_six(self, monkeypatch):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        provider = SelfModelDashboardProvider(self_model_provider=smp)
        monkeypatch.setattr(
            "src.admin.runtime_provider.get_runtime_provider",
            lambda: None,
        )
        result = provider.get_full_state()
        assert "identity_state" in result
        assert "personality_state" in result
        assert "growth_history" in result
        assert "relationship_state" in result
        assert "capability_boundary" in result
        assert "personality_evolution_history" in result


# =====================================================================
# Test: memory_dashboard_provider 新方法
# =====================================================================

class TestMemoryDashboardQualityMethods:
    """测试 memory_dashboard_provider 新增的 type_stats / quality / combined。"""

    def _make_provider(self, memories):
        from src.admin.memory_dashboard_provider import MemoryDashboardProvider
        store = _FakeStore(memories=memories)
        rp = type("FakeRP", (), {"get_memory_store": lambda self: store})()
        provider = MemoryDashboardProvider(runtime_provider=rp)
        return provider

    def test_type_stats_normal_user(self):
        mems = [
            {"id": "1", "content": "今天心情不错", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "我喜欢音乐", "role": "user",
             "metadata": {"memory_type": "user_preference"}, "timestamp": "2025-01-02"},
        ]
        p = self._make_provider(mems)
        result = p.get_type_stats()
        assert result["available"] is True
        assert result["total"] == 2
        assert result["by_category"]["normal_user"] == 2
        assert result["pollution_count"] == 0
        assert result["invalid_count"] == 0

    def test_type_stats_system_pollution(self):
        mems = [
            {"id": "1", "content": "you are a helpful assistant", "role": "system",
             "metadata": {"memory_type": "system_prompt"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "system reminder: do not say X", "role": "system",
             "metadata": {"memory_type": "system_reminder"}, "timestamp": "2025-01-02"},
            {"id": "3", "content": "正常的用户消息", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-03"},
        ]
        p = self._make_provider(mems)
        result = p.get_type_stats()
        assert result["by_category"]["system_pollution"] == 2
        assert result["pollution_count"] == 2
        assert result["by_category"]["normal_user"] == 1

    def test_type_stats_ai_internal_pollution(self):
        mems = [
            {"id": "1", "content": "<scratchpad>思考中...</scratchpad>", "role": "assistant",
             "metadata": {"memory_type": "ai_scratchpad"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "我需要反思这个对话", "role": "assistant",
             "metadata": {"memory_type": "ai_reflection"}, "timestamp": "2025-01-02"},
            {"id": "3", "content": "user message", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-03"},
        ]
        p = self._make_provider(mems)
        result = p.get_type_stats()
        assert result["by_category"]["ai_internal_pollution"] == 2
        assert result["pollution_count"] == 2

    def test_type_stats_invalid(self):
        mems = [
            {"id": "1", "content": "missing type", "role": "user",
             "metadata": {}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "unknown type", "role": "user",
             "metadata": {"memory_type": "unknown"}, "timestamp": "2025-01-02"},
            {"id": "3", "content": "valid", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-03"},
        ]
        p = self._make_provider(mems)
        result = p.get_type_stats()
        assert result["by_category"]["invalid"] == 2
        assert result["invalid_count"] == 2

    def test_quality_healthy(self):
        mems = [
            {"id": "1", "content": "今天天气很好", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "我喜欢音乐", "role": "user",
             "metadata": {"memory_type": "user_preference"}, "timestamp": "2025-01-02"},
        ]
        p = self._make_provider(mems)
        result = p.get_quality()
        assert result["available"] is True
        assert result["status"] == "healthy"
        assert result["total"] == 2
        assert result["normal_count"] == 2
        assert result["pollution_count"] == 0
        assert result["normal_ratio"] == 1.0

    def test_quality_warning_pollution(self):
        mems = [
            {"id": "1", "content": "system prompt X", "role": "system",
             "metadata": {"memory_type": "system_prompt"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "user1", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-02"},
            {"id": "3", "content": "user2", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-03"},
        ]
        p = self._make_provider(mems)
        result = p.get_quality()
        # 1 pollution / 3 total = 0.333 > 0.3 -> critical
        assert result["status"] == "critical"

    def test_quality_warning_missing_content(self):
        mems = [
            {"id": "1", "content": "", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-01"},
            {"id": "2", "content": "正常消息", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-02"},
        ]
        p = self._make_provider(mems)
        result = p.get_quality()
        assert result["status"] == "warning"
        assert result["missing_content_count"] == 1

    def test_quality_unknown_when_empty(self):
        p = self._make_provider([])
        result = p.get_quality()
        assert result["status"] == "unknown"
        assert result["total"] == 0

    def test_get_combined(self):
        mems = [
            {"id": "1", "content": "ok", "role": "user",
             "metadata": {"memory_type": "user_message"}, "timestamp": "2025-01-01"},
        ]
        p = self._make_provider(mems)
        result = p.get_combined(recent_limit=5)
        assert "summary" in result
        assert "type_stats" in result
        assert "quality" in result
        assert "recent" in result


# =====================================================================
# Test: growth_dashboard_provider
# =====================================================================

class TestGrowthDashboardProvider:
    """测试 growth_dashboard_provider 全部方法。"""

    def _make_provider(self, proposals=None, sm_dp=None):
        from src.admin.growth_dashboard_provider import GrowthDashboardProvider
        storage = _FakeProposalStorage(proposals=proposals or [])
        provider = GrowthDashboardProvider(
            proposal_storage=storage,
            self_model_dashboard_provider=sm_dp,
        )
        return provider

    def test_summary_empty(self):
        p = self._make_provider()
        result = p.get_summary()
        assert result["available"] is True
        assert result["total"] == 0
        assert result["pending"] == 0
        assert result["review"] == 0

    def test_summary_counts_by_status(self):
        proposals = [
            _make_proposal("p1", "pending", "2025-01-01T00:00:00"),
            _make_proposal("p2", "pending", "2025-01-02T00:00:00"),
            _make_proposal("p3", "approved", "2025-01-03T00:00:00"),
            _make_proposal("p4", "rejected", "2025-01-04T00:00:00"),
            _make_proposal("p5", "applied", "2025-01-05T00:00:00"),
        ]
        p = self._make_provider(proposals=proposals)
        result = p.get_summary()
        assert result["total"] == 5
        assert result["pending"] == 2
        assert result["approved"] == 1
        assert result["rejected"] == 1
        assert result["applied"] == 1
        assert result["review"] == 2  # pending 即 review

    def test_summary_by_type(self):
        proposals = [
            _make_proposal("p1", "pending", "2025-01-01", ptype="personality"),
            _make_proposal("p2", "pending", "2025-01-02", ptype="relationship"),
            _make_proposal("p3", "pending", "2025-01-03", ptype="personality"),
        ]
        p = self._make_provider(proposals=proposals)
        result = p.get_summary()
        assert result["by_type"]["personality"] == 2
        assert result["by_type"]["relationship"] == 1

    def test_recent_returns_sorted(self):
        proposals = [
            _make_proposal("p1", "pending", "2025-01-01"),
            _make_proposal("p2", "applied", "2025-01-03"),
            _make_proposal("p3", "rejected", "2025-01-02"),
        ]
        p = self._make_provider(proposals=proposals)
        result = p.get_recent(limit=10)
        assert result["total"] == 3
        assert len(result["items"]) == 3
        # 按 timestamp 倒序
        ts_list = [item["timestamp"] for item in result["items"]]
        assert ts_list == sorted(ts_list, reverse=True)
        # item 应包含 kind 字段
        kinds = {item["kind"] for item in result["items"]}
        assert "proposal" in kinds

    def test_recent_with_growth_records(self):
        """如果注入了 selfmodel_dp,recent 应合并 growth_record。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        sm_dp = SelfModelDashboardProvider(self_model_provider=smp)
        # monkeypatch 通过 get_growth_history 返回带 records 的数据
        original = sm_dp.get_growth_history
        sm_dp.get_growth_history = lambda limit=50: {
            "available": True,
            "items": [
                {
                    "record_id": "gr1", "growth_signal": "creative",
                    "created_at": "2025-01-05", "growth_level": "trait",
                    "source_type": "creation", "applied": True,
                    "affected_dimensions": {"creativity": 0.01},
                    "confidence": 0.8, "reason": "creative work",
                }
            ],
        }
        proposals = [_make_proposal("p1", "applied", "2025-01-03")]
        p = self._make_provider(proposals=proposals, sm_dp=sm_dp)
        result = p.get_recent(limit=10)
        assert result["total"] >= 2
        kinds = {item["kind"] for item in result["items"]}
        assert "proposal" in kinds
        assert "growth_record" in kinds

    def test_evolution_history_without_sm_dp_fallback(self):
        p = self._make_provider()
        result = p.get_evolution_history(limit=10)
        assert result["available"] is False
        assert result["fallback"] is True
        assert "fallback_reason" in result

    def test_evolution_history_includes_applied_proposals(self):
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        smp = _FakeSMP()
        sm_dp = SelfModelDashboardProvider(self_model_provider=smp)
        # 让 evolution_history 返回空
        sm_dp.get_personality_evolution_history = lambda limit=50: {
            "available": True,
            "total": 0, "applied_count": 0, "rolled_back_count": 0,
            "items": [], "current_personality_state": {},
        }
        proposals = [
            _make_proposal("p1", "applied", "2025-01-01", affected={"creativity": 0.01}),
        ]
        p = self._make_provider(proposals=proposals, sm_dp=sm_dp)
        result = p.get_evolution_history(limit=10)
        assert result["available"] is True
        assert result["applied_count"] == 1  # 含 applied proposal
        assert len(result["items"]) == 1
        assert result["items"][0]["status"] == "applied"

    def test_get_combined(self):
        p = self._make_provider()
        result = p.get_combined()
        assert "summary" in result
        assert "recent" in result
        assert "evolution_history" in result


# =====================================================================
# Test: Blueprint 注册
# =====================================================================

class TestBlueprintRegistration:
    """验证 3 个新模块的 Blueprint 可正常 import。"""

    def _get_registered_rules(self, bp):
        """通过把 Blueprint 注册到一个临时 Flask app,获取真实路由名。"""
        from flask import Flask
        app = Flask(__name__)
        # 临时修改 url_prefix 为空,便于检查 endpoint 名(避开 prefix 重复)
        original_prefix = bp.url_prefix
        bp.url_prefix = "/__test_prefix__"
        try:
            app.register_blueprint(bp)
            rules = list(app.url_map.iter_rules())
        finally:
            bp.url_prefix = original_prefix
        # 只保留本 Blueprint 的 rules
        bp_rules = [r for r in rules if r.endpoint.startswith(bp.name + ".")]
        return bp_rules

    def test_selfmodel_router_imports(self):
        from src.admin.dashboard.selfmodel_router import selfmodel_v2_bp
        assert selfmodel_v2_bp is not None
        rules = self._get_registered_rules(selfmodel_v2_bp)
        endpoints = [r.endpoint.split(".")[-1] for r in rules]
        # endpoint 形如 selfmodel_identity_state,需要去掉 selfmodel_ 前缀
        short = [e.replace("selfmodel_", "") for e in endpoints]
        # Phase C.1 P1-1 新增 7 个 state 端点
        for name in (
            "identity_state",
            "personality_state",
            "growth_history",
            "relationship_state",
            "capability_boundary",
            "personality_evolution_history",
            "full_state",
        ):
            assert name in short, f"missing endpoint: {name}; got: {short}"

    def test_memory_router_imports(self):
        from src.admin.dashboard.memory_router import memory_v2_bp
        assert memory_v2_bp is not None
        rules = self._get_registered_rules(memory_v2_bp)
        endpoints = [r.endpoint.split(".")[-1] for r in rules]
        # endpoint 形如 memory_type_stats,需要去掉 memory_ 前缀
        short = [e.replace("memory_", "") for e in endpoints]
        for name in ("type_stats", "quality", "combined"):
            assert name in short, f"missing endpoint: {name}; got: {short}"

    def test_growth_router_imports(self):
        from src.admin.dashboard.growth_router import growth_v2_bp
        assert growth_v2_bp is not None
        rules = self._get_registered_rules(growth_v2_bp)
        endpoints = [r.endpoint.split(".")[-1] for r in rules]
        short = [e.replace("growth_", "") for e in endpoints]
        for name in ("summary", "recent", "evolution_history", "combined"):
            assert name in short, f"missing endpoint: {name}; got: {short}"
        # 全部应是 GET 方法
        for r in rules:
            assert "GET" in r.methods or "HEAD" in r.methods, \
                f"endpoint {r.endpoint} 包含非 GET 方法: {r.methods}"

    def test_growth_dashboard_provider_module(self):
        from src.admin.growth_dashboard_provider import (
            GrowthDashboardProvider,
            get_growth_dashboard_provider,
            reset_growth_dashboard_provider_for_testing,
        )
        p = get_growth_dashboard_provider()
        assert isinstance(p, GrowthDashboardProvider)
        reset_growth_dashboard_provider_for_testing()


# =====================================================================
# Test: 只读约束
# =====================================================================

class TestReadOnlyConstraints:
    """验证 Dashboard 接口不允许修改人格 / 触发成长。"""

    def test_selfmodel_dashboard_has_no_mutation_methods(self):
        """SelfModelDashboardProvider 不应暴露 set / save / update / apply 方法。"""
        from src.admin.selfmodel_dashboard_provider import SelfModelDashboardProvider
        forbidden = ["set_identity", "update_traits", "apply_growth",
                     "save", "mutate", "delete", "update", "add"]
        public_methods = [m for m in dir(SelfModelDashboardProvider)
                          if not m.startswith("_") and callable(getattr(SelfModelDashboardProvider, m, None))]
        for f in forbidden:
            assert f not in public_methods, f"{f} 出现在只读 Provider 中"

    def test_growth_dashboard_has_no_apply_methods(self):
        """GrowthDashboardProvider 不应暴露 apply / approve / reject 方法。"""
        from src.admin.growth_dashboard_provider import GrowthDashboardProvider
        forbidden = ["apply", "approve", "reject", "save", "delete",
                     "update", "add", "set"]
        public_methods = [m for m in dir(GrowthDashboardProvider)
                          if not m.startswith("_") and callable(getattr(GrowthDashboardProvider, m, None))]
        for f in forbidden:
            assert f not in public_methods, f"{f} 出现在只读 Provider 中"

    def test_growth_router_only_get(self):
        """growth_router 只接受 GET 方法(通过注册的 url_map 验证)。"""
        from src.admin.dashboard.growth_router import growth_v2_bp
        from flask import Flask
        app = Flask(__name__)
        original_prefix = growth_v2_bp.url_prefix
        growth_v2_bp.url_prefix = "/__t__"
        try:
            app.register_blueprint(growth_v2_bp)
            rules = list(app.url_map.iter_rules())
        finally:
            growth_v2_bp.url_prefix = original_prefix
        bp_rules = [r for r in rules if r.endpoint.startswith(growth_v2_bp.name + ".")]
        for r in bp_rules:
            assert set(r.methods or set()).issubset({"GET", "HEAD", "OPTIONS"}), \
                f"endpoint {r.endpoint} 含非 GET 方法: {r.methods}"


if __name__ == "__main__":
    import subprocess
    subprocess.run(["pytest", __file__, "-v", "--tb=short"])
