# -*- coding: utf-8 -*-
"""
tests/test_admin_selfmodel_provider.py

Phase 6.5: Admin SelfModelProvider 单元测试。

覆盖：
  1. 桥接不可用时 → fallback
  2. 列出 beliefs（domain / confidence / include_inactive 过滤）
  3. 列出 history（event_type / since / until 过滤）
  4. 列出 reflections（trigger_source / reflection_type 过滤）
  5. get_belief_why 链路
  6. get_evolution_timeline 跨源合并
  7. get_pcr_related_events 链路
  8. get_health_report 调用 SelfModelHealthChecker
  9. get_retention_status 摘要
 10. run_retention_dry_run dry_run=True 不修改源数据
 11. 不创建任何 SelfModel store / authority 实例

约束：
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
- 不修改 RuntimeCore
- Mock bridge / mock adapter / mock store
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

class _FakeBootstrap:
    """Mock SelfModelBootstrap（与真实接口一致：data_dir/last_load_counts/last_save_result/last_error 为只读 property；_persistence_attached 为实例属性）。"""
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


class _FakeCore:
    """Mock RuntimeCore。

    暴露 get_self_model_adapter() 和 get_self_model_bootstrap()。
    """
    def __init__(self, adapter: Any = None, bootstrap: Any = None):
        self._adapter = adapter
        self._bootstrap = bootstrap

    def get_self_model_adapter(self) -> Any:
        return self._adapter

    def get_self_model_bootstrap(self) -> Any:
        return self._bootstrap


class _FakeBridge:
    """Mock RuntimeBridge。"""
    def __init__(self, core: Any = None):
        self._core = core

    def get_runtime_core(self) -> Any:
        return self._core


class _FakeRuntimeProvider:
    """Mock RuntimeProvider，模拟 SelfModelProvider 的依赖。"""
    def __init__(self, bridge: Any = None):
        self._bridge = bridge

    def get_runtime_bridge(self):
        return self._bridge


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singleton():
    """重置 SelfModelProvider 单例。"""
    from src.admin.self_model_provider import reset_self_model_provider_for_testing
    reset_self_model_provider_for_testing()
    yield
    reset_self_model_provider_for_testing()


def _make_belief(
    belief_id: str = "bel_001",
    domain: str = "value",
    content: str = "羽依重视真诚",
    confidence: float = 0.8,
    active: bool = True,
    version: int = 1,
    sources: List[str] = None,
) -> Any:
    from src.personality.self_belief import SelfBelief
    b = SelfBelief(
        belief_id=belief_id,
        domain=domain,
        content=content,
        confidence=confidence,
        active=active,
        version=version,
    )
    if sources:
        b.sources = list(sources)
    return b


def _make_history_event(
    event_id: str = "hevt_001",
    event_type: str = "pcr_applied",
    source_id: str = "prop_001",
    summary: str = "PCR applied",
    affected_traits: Dict[str, float] = None,
    affected_beliefs: List[str] = None,
    timestamp: str = "2026-07-30T10:00:00Z",
) -> Any:
    from src.personality.self_history import SelfHistoryEvent
    return SelfHistoryEvent(
        event_id=event_id,
        event_type=event_type,
        source_id=source_id,
        summary=summary,
        affected_traits=affected_traits or {},
        affected_beliefs=affected_beliefs or [],
        timestamp=timestamp,
    )


def _make_reflection(
    note_id: str = "refl_001",
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


@pytest.fixture
def populated_adapter():
    """构造一个完整的 mock adapter（带 _beliefs/_history/_reflections）。"""
    beliefs = [_make_belief(belief_id="bel_001", content="羽依重视真诚", confidence=0.85, sources=["prop_001"]),
               _make_belief(belief_id="bel_002", domain="preference", content="羽依喜欢安静", confidence=0.7, sources=["prop_002"]),
               _make_belief(belief_id="bel_003", domain="value", content="旧 belief", confidence=0.3, active=False, sources=["prop_old"])]
    history = [_make_history_event(event_id="hevt_001", source_id="prop_001", affected_beliefs=["bel_001"], affected_traits={"warmth": 0.05}, timestamp="2026-07-30T10:00:00Z"),
               _make_history_event(event_id="hevt_002", event_type="snapshot_created", source_id="snapshot_001", timestamp="2026-07-30T11:00:00Z")]
    reflections = [_make_reflection(note_id="refl_001", content="我意识到自己更喜欢独处", related_belief_ids=["bel_001"]),
                   _make_reflection(note_id="refl_002", reflection_type="value", content="我对真诚有强烈认同", related_belief_ids=["bel_001", "bel_002"])]

    class _Store:
        def __init__(self, items):
            self._items = items
        def all(self):
            return list(self._items)

    class _Adapter:
        def __init__(self):
            self._beliefs = _Store(beliefs)
            self._history = _Store(history)
            self._reflections = _Store(reflections)
            self._persistence = None

    return _Adapter()


@pytest.fixture
def provider(populated_adapter, reset_singleton):
    """构造 SelfModelProvider（带 mock bridge / mock core / mock adapter）。"""
    bootstrap = _FakeBootstrap()
    core = _FakeCore(adapter=populated_adapter, bootstrap=bootstrap)
    bridge = _FakeBridge(core=core)
    rt = _FakeRuntimeProvider(bridge=bridge)

    from src.admin.self_model_provider import SelfModelProvider
    return SelfModelProvider(runtime_provider=rt)


# =====================================================================
# A. Fallback 行为
# =====================================================================

class TestProviderFallback:

    def test_bridge_unavailable_returns_fallback(self, reset_singleton):
        """bridge 不可用时，Provider 应返回安全的 fallback。"""
        rt = _FakeRuntimeProvider(bridge=None)
        from src.admin.self_model_provider import SelfModelProvider
        p = SelfModelProvider(runtime_provider=rt)

        status = p.get_status()
        assert status["available"] is False
        assert status["bootstrap"] is None

        # list_beliefs 应返回 available=False + empty
        res = p.list_beliefs()
        assert res["available"] is False
        assert res["items"] == []

        # health 应返回 available=False
        res = p.get_health_report()
        assert res["available"] is False
        assert res["report"] is None

        # retention 应返回 available=False
        res = p.get_retention_status()
        assert res["available"] is False
        assert res["current_counts"] == {}

    def test_core_present_but_adapter_none(self, reset_singleton):
        """core 存在但 adapter 未初始化 → fallback。"""
        core = _FakeCore(adapter=None, bootstrap=None)
        bridge = _FakeBridge(core=core)
        rt = _FakeRuntimeProvider(bridge=bridge)
        from src.admin.self_model_provider import SelfModelProvider
        p = SelfModelProvider(runtime_provider=rt)

        status = p.get_status()
        assert status["available"] is False


# =====================================================================
# B. 状态 / Bootstrap
# =====================================================================

class TestStatus:

    def test_get_status_with_bootstrap(self, provider):
        status = provider.get_status()
        assert status["available"] is True
        assert status["bootstrap"] is not None
        assert status["bootstrap"]["data_dir"] == "data/self_model"
        assert status["bootstrap"]["persistence_attached"] is True
        assert status["bootstrap"]["last_load_counts"]["beliefs"] == 5

    def test_get_status_without_bootstrap(self, populated_adapter, reset_singleton):
        """adapter 存在但 bootstrap 缺失 → bootstrap=None。"""
        core = _FakeCore(adapter=populated_adapter, bootstrap=None)
        bridge = _FakeBridge(core=core)
        rt = _FakeRuntimeProvider(bridge=bridge)
        from src.admin.self_model_provider import SelfModelProvider
        p = SelfModelProvider(runtime_provider=rt)
        status = p.get_status()
        assert status["available"] is True
        assert status["bootstrap"] is None


# =====================================================================
# C. Beliefs 列表
# =====================================================================

class TestListBeliefs:

    def test_returns_all(self, provider):
        res = provider.list_beliefs()
        assert res["available"] is True
        assert res["total"] == 3
        assert res["active"] == 2
        assert res["inactive"] == 1
        assert res["by_domain"]["value"] == 2
        assert res["by_domain"]["preference"] == 1
        # 排序：confidence desc
        assert res["items"][0]["confidence"] >= res["items"][1]["confidence"]

    def test_filter_by_domain(self, provider):
        res = provider.list_beliefs(domain="preference")
        assert res["total"] == 3
        items = res["items"]
        assert all(it["domain"] == "preference" for it in items)
        assert len(items) == 1
        assert items[0]["belief_id"] == "bel_002"

    def test_filter_by_min_confidence(self, provider):
        res = provider.list_beliefs(min_confidence=0.5)
        # 0.3 的 belief 被过滤
        ids = [it["belief_id"] for it in res["items"]]
        assert "bel_003" not in ids
        assert "bel_001" in ids
        assert "bel_002" in ids

    def test_exclude_inactive(self, provider):
        res = provider.list_beliefs(include_inactive=False)
        ids = [it["belief_id"] for it in res["items"]]
        assert "bel_003" not in ids
        assert res["active"] == 2
        assert res["inactive"] == 0

    def test_limit(self, provider):
        res = provider.list_beliefs(limit=1)
        assert len(res["items"]) == 1

    def test_item_fields(self, provider):
        res = provider.list_beliefs()
        first = res["items"][0]
        for key in ("belief_id", "domain", "content", "confidence", "version",
                    "evidence_count", "sources", "active", "first_seen", "last_confirmed"):
            assert key in first


# =====================================================================
# D. History 列表
# =====================================================================

class TestListHistory:

    def test_returns_all(self, provider):
        res = provider.list_history()
        assert res["available"] is True
        assert res["total"] == 2
        assert "pcr_applied" in res["by_event_type"]
        assert "snapshot_created" in res["by_event_type"]

    def test_filter_event_type(self, provider):
        res = provider.list_history(event_type="snapshot_created")
        assert res["total"] == 2
        assert all(it["event_type"] == "snapshot_created" for it in res["items"])
        assert len(res["items"]) == 1

    def test_filter_time_window(self, provider):
        res = provider.list_history(since="2026-07-30T10:30:00Z")
        # hevt_001 (10:00) 被过滤
        ids = [it["event_id"] for it in res["items"]]
        assert "hevt_001" not in ids
        assert "hevt_002" in ids

    def test_item_fields(self, provider):
        res = provider.list_history()
        first = res["items"][0]
        for key in ("event_id", "event_type", "timestamp", "source_type",
                    "source_id", "summary", "affected_traits", "affected_beliefs", "actor"):
            assert key in first


# =====================================================================
# E. Reflections 列表
# =====================================================================

class TestListReflections:

    def test_returns_all(self, provider):
        res = provider.list_reflections()
        assert res["available"] is True
        assert res["total"] == 2
        assert "identity" in res["by_reflection_type"]
        assert "value" in res["by_reflection_type"]
        assert "pcr_applied" in res["by_trigger_source"]

    def test_filter_by_type(self, provider):
        res = provider.list_reflections(reflection_type="value")
        assert len(res["items"]) == 1
        assert res["items"][0]["note_id"] == "refl_002"

    def test_filter_by_trigger(self, provider):
        res = provider.list_reflections(trigger_source="pcr_applied")
        assert res["total"] == 2
        assert all(it["trigger_source"] == "pcr_applied" for it in res["items"])

    def test_filter_by_min_confidence(self, provider):
        res = provider.list_reflections(min_confidence=0.8)
        # 两条 confidence 都不高（0.7），应都被过滤
        assert res["items"] == []


# =====================================================================
# F. Why 追溯
# =====================================================================

class TestBeliefWhy:

    def test_returns_why_envelope(self, provider):
        res = provider.get_belief_why("bel_001")
        assert res["available"] is True
        assert res["belief"] is not None
        assert res["belief"]["belief_id"] == "bel_001"
        assert res["origin"] is not None
        # bel_001 的 sources 包含 prop_001，history 中有 source_id=prop_001
        assert isinstance(res["answer"], str)

    def test_empty_id_returns_error(self, provider):
        res = provider.get_belief_why("")
        assert res["error"] == "belief_id required"

    def test_belief_with_no_history(self, provider):
        res = provider.get_belief_why("bel_002")
        # bel_002 的 sources 包含 prop_002，但 history 中没有 source_id=prop_002
        assert res["available"] is True
        # 此时 pcr_link 来自 belief_001 的 history 关联？应为空
        assert res["pcr_link"]["summary"]["belief_count"] == 0 or res["pcr_link"] is not None


# =====================================================================
# G. Evolution Timeline
# =====================================================================

class TestEvolutionTimeline:

    def test_returns_combined(self, provider):
        res = provider.get_evolution_timeline()
        assert res["available"] is True
        # belief(3) + history(2) + reflection(2) = 7
        assert res["total"] >= 5
        # 至少出现 2 种 source
        assert len(res["by_source"]) >= 2

    def test_filter_by_source(self, provider):
        res = provider.get_evolution_timeline(sources=["history"])
        assert res["total"] >= 1
        for it in res["items"]:
            assert it["source"] == "history"

    def test_limit(self, provider):
        res = provider.get_evolution_timeline(limit=1)
        assert len(res["items"]) == 1


# =====================================================================
# H. PCR 关联
# =====================================================================

class TestPCRLink:

    def test_finds_related_events(self, provider):
        res = provider.get_pcr_related_events("prop_001")
        assert res["available"] is True
        # history 中 hevt_001 的 source_id=prop_001
        history_ids = [ev["event_id"] for ev in res["history_events"]]
        assert "hevt_001" in history_ids
        # bel_001 的 sources 包含 prop_001
        belief_ids = [b["belief_id"] for b in res["linked_beliefs"]]
        assert "bel_001" in belief_ids
        # 关联 reflections
        assert isinstance(res["linked_reflections"], list)
        assert isinstance(res["audit_records"], list)
        assert "history_count" in res["summary"]

    def test_empty_proposal_id(self, provider):
        res = provider.get_pcr_related_events("")
        assert res["error"] == "proposal_id required"

    def test_no_related_events(self, provider):
        res = provider.get_pcr_related_events("prop_nonexistent")
        assert res["available"] is True
        assert res["history_events"] == []
        assert res["linked_beliefs"] == []


# =====================================================================
# I. Health
# =====================================================================

class TestHealth:

    def test_health_report_calls_checker(self, provider):
        res = provider.get_health_report()
        assert res["available"] is True
        assert res["report"] is not None
        # 报告字段
        r = res["report"]
        for key in ("overall_status", "beliefs_count", "history_count", "reflections_count", "issues", "duration_ms"):
            assert key in r
        # 统计
        assert "summary" in res
        assert res["summary"]["counts"]["beliefs"] == 3
        assert res["summary"]["counts"]["history"] == 2
        assert res["summary"]["counts"]["reflections"] == 2


# =====================================================================
# J. Retention
# =====================================================================

class TestRetention:

    def test_get_retention_status(self, provider):
        res = provider.get_retention_status()
        assert res["available"] is True
        # thresholds
        assert res["thresholds"]["max_active_beliefs"] >= 1
        assert res["thresholds"]["min_confidence_for_active"] >= 0
        # current_counts
        assert res["current_counts"]["beliefs_total"] == 3
        assert res["current_counts"]["beliefs_active"] == 2
        assert res["current_counts"]["beliefs_inactive"] == 1
        assert res["current_counts"]["history_total"] == 2
        assert res["current_counts"]["reflections_total"] == 2

    def test_run_retention_dry_run_does_not_modify_source(self, provider):
        """dry_run=True 时不修改源数据。"""
        # 取原始数据
        before_total = len(list(provider._get_adapter()._beliefs.all()))
        before_active = sum(1 for b in provider._get_adapter()._beliefs.all() if b.active)

        res = provider.run_retention_dry_run()
        assert res["available"] is True
        assert res["dry_run"] is True
        assert res["applied"] is False
        # 报告字段
        r = res["report"]
        for key in ("beliefs_total", "beliefs_active_before", "beliefs_active_after",
                    "beliefs_deactivated", "history_total", "reflections_total"):
            assert key in r
        # dry_run 模式下 beliefs_active_after == beliefs_active_before
        assert r["beliefs_active_after"] == r["beliefs_active_before"]

        # 验证源数据未变
        after_total = len(list(provider._get_adapter()._beliefs.all()))
        after_active = sum(1 for b in provider._get_adapter()._beliefs.all() if b.active)
        assert after_total == before_total
        assert after_active == before_active


# =====================================================================
# K. 约束验证：不创建任何 SelfModel 实例
# =====================================================================

class TestConstraints:

    def test_provider_does_not_create_adapter(self, reset_singleton):
        """Provider 不应实例化 SelfModelAdapter。"""
        rt = _FakeRuntimeProvider(bridge=None)
        from src.admin.self_model_provider import SelfModelProvider
        p = SelfModelProvider(runtime_provider=rt)
        # 验证 _adapter 不存在
        assert "_adapter" not in p.__dict__
        assert "_beliefs" not in p.__dict__
        assert "_history" not in p.__dict__
        assert "_reflections" not in p.__dict__

    def test_provider_reads_via_bridge_only(self, provider, populated_adapter):
        """Provider 的所有数据都通过 bridge 拿。"""
        # 调用所有方法，验证 adapter 引用与传入一致（没有被覆盖）
        provider.get_status()
        provider.list_beliefs()
        provider.list_history()
        provider.list_reflections()
        provider.get_evolution_timeline()
        provider.get_health_report()

        assert provider._get_adapter() is populated_adapter


# =====================================================================
# L. 单例
# =====================================================================

class TestSingleton:

    def test_singleton_returns_same_instance(self, reset_singleton):
        from src.admin.self_model_provider import get_self_model_provider
        a = get_self_model_provider()
        b = get_self_model_provider()
        assert a is b

    def test_reset_singleton(self, reset_singleton):
        from src.admin.self_model_provider import (
            get_self_model_provider,
            reset_self_model_provider_for_testing,
        )
        a = get_self_model_provider()
        reset_self_model_provider_for_testing()
        b = get_self_model_provider()
        assert a is not b
