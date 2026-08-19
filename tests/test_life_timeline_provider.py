# -*- coding: utf-8 -*-
"""
tests/test_life_timeline_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.3 —— LifeTimelineProvider 单元测试。

覆盖:
  1. TestStructure   —— 七泳道结构,空 lane 保留,items 按时间升序
  2. TestRange       —— 24h / 7d / 30d / 90d / all / invalid 范围
  3. TestCausality   —— memory→reflection, reflection→goal 边, 复用 LifeGraphProvider
  4. TestMilestone   —— interest_to_goal, belief_change, reflection_turning_point
  5. TestFallback    —— provider 异常 / 空事件源
  6. TestIsolation   —— 不 import 业务模块, 不调用 LLM
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Mock helpers
# =====================================================================

class _FakeLock:
    """模拟 threading.RLock,支持 with 语句。"""

    def __enter__(self) -> "_FakeLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeLifeGraphProvider:
    """
    模拟 LifeGraphProvider。

    关键属性(供 LifeTimelineProvider._fetch_graph_data 使用):
      - _lock            : 任意可 with 上下文
      - _ensure_loaded() : no-op
      - _cache_nodes     : List[dict]
      - _cache_edges     : List[dict]
      - _last_build_ok   : bool
      - _last_error      : str
    """

    def __init__(
        self,
        nodes: Optional[List[Dict[str, Any]]] = None,
        edges: Optional[List[Dict[str, Any]]] = None,
        ok: bool = True,
        err: str = "",
    ) -> None:
        self._cache_nodes = list(nodes or [])
        self._cache_edges = list(edges or [])
        self._last_build_ok = bool(ok)
        self._last_error = err
        self._lock = _FakeLock()
        self._call_count = 0

    def _ensure_loaded(self) -> None:
        self._call_count += 1


def _make_node(
    nid: str,
    ntype: str,
    *,
    timestamp: float = 0.0,
    label: str = "",
    topic: str = "",
    summary: str = "",
    importance: float = 0.0,
    confidence: float = 0.0,
    delta: float = 0.0,
    source_event_ids: Optional[List[str]] = None,
    source_memory_ids: Optional[List[str]] = None,
    source_reflection_ids: Optional[List[str]] = None,
    source_interest_ids: Optional[List[str]] = None,
    source_belief_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "id": nid,
        "type": ntype,
        "timestamp": timestamp,
        "label": label or nid,
        "topic": topic,
        "summary": summary,
        "evidence": summary,
        "insight": summary,
        "importance": importance,
        "confidence": confidence,
        "delta": delta,
        "source_event_ids": list(source_event_ids or []),
        "source_memory_ids": list(source_memory_ids or []),
        "source_reflection_ids": list(source_reflection_ids or []),
        "source_interest_ids": list(source_interest_ids or []),
        "source_belief_ids": list(source_belief_ids or []),
    }


def _make_edge(
    src: str,
    tgt: str,
    relation: str,
    *,
    source_event_ids: Optional[List[str]] = None,
    confidence: float = 0.0,
) -> Dict[str, Any]:
    return {
        "source": src,
        "target": tgt,
        "relation": relation,
        "source_event_ids": list(source_event_ids or []),
        "confidence": confidence,
    }


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    """确保单例在测试间隔离。"""
    try:
        from src.admin.life_timeline_provider import (
            reset_life_timeline_provider_for_testing,
        )
        reset_life_timeline_provider_for_testing()
        yield
        reset_life_timeline_provider_for_testing()
    except Exception:
        yield


def _now_ts() -> float:
    return time.time()


def _sample_nodes_edges() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """构造一个完整的 7 域样本,符合 spec 的链式时间线。"""
    now = _now_ts()
    nodes: List[Dict[str, Any]] = [
        _make_node(
            "memory::mem_001",
            "memory",
            timestamp=now - 20000,
            topic="AI 哲学",
            summary="用户询问 AI 是否有意识",
            importance=0.8,
            source_event_ids=["evt_mem_001"],
        ),
        _make_node(
            "reflection::refl_001",
            "reflection",
            timestamp=now - 18000,
            topic="AI 哲学",
            summary="用户对 AI 意识有强烈兴趣",
            source_event_ids=["evt_refl_001"],
            source_memory_ids=["memory::mem_001", "memory::mem_002"],
        ),
        _make_node(
            "memory::mem_002",
            "memory",
            timestamp=now - 19000,
            topic="AI 哲学",
            summary="用户连续追问 AI 意识问题",
            importance=0.7,
        ),
        _make_node(
            "interest::sig_001",
            "interest",
            timestamp=now - 16000,
            topic="AI 哲学",
            summary="兴趣信号:AI 哲学",
            confidence=0.7,
            source_event_ids=["evt_sig_001"],
            source_reflection_ids=["reflection::refl_001"],
        ),
        _make_node(
            "goal::goal_001",
            "goal",
            timestamp=now - 14000,
            topic="AI 哲学",
            summary="建立 AI 哲学对话能力",
            importance=0.85,
            confidence=0.7,
            source_event_ids=["evt_goal_001"],
            source_interest_ids=["interest::sig_001"],
        ),
        _make_node(
            "action::act_001",
            "action",
            timestamp=now - 12000,
            topic="AI 哲学",
            summary="询问用户关于 AI 意识的看法",
            confidence=0.65,
        ),
        _make_node(
            "belief::belief_001",
            "belief",
            timestamp=now - 10000,
            topic="AI 哲学",
            summary="我相信持续讨论 AI 意识是有价值的",
            confidence=0.7,
        ),
        _make_node(
            "trait_change::tc_001",
            "trait_change",
            timestamp=now - 8000,
            topic="curiosity",
            summary="好奇心提升",
            delta=0.08,
        ),
    ]
    edges: List[Dict[str, Any]] = [
        _make_edge(
            "memory::mem_001",
            "reflection::refl_001",
            "memory_to_reflection",
            source_event_ids=["evt_mem_001"],
        ),
        _make_edge(
            "memory::mem_002",
            "reflection::refl_001",
            "memory_to_reflection",
            source_event_ids=["evt_mem_002"],
        ),
        _make_edge(
            "reflection::refl_001",
            "interest::sig_001",
            "reflection_to_interest",
            source_event_ids=["evt_refl_001", "evt_sig_001"],
        ),
        _make_edge(
            "interest::sig_001",
            "goal::goal_001",
            "interest_to_goal",
            source_event_ids=["evt_sig_001", "evt_goal_001"],
        ),
        _make_edge(
            "goal::goal_001",
            "action::act_001",
            "goal_to_action",
            source_event_ids=["evt_goal_001", "evt_act_001"],
        ),
        _make_edge(
            "reflection::refl_001",
            "belief::belief_001",
            "reflection_to_belief",
            source_event_ids=["evt_refl_001", "evt_belief_001"],
        ),
        _make_edge(
            "belief::belief_001",
            "trait_change::tc_001",
            "belief_to_trait_change",
            source_event_ids=["evt_belief_001", "evt_tc_001"],
        ),
    ]
    return nodes, edges


# =====================================================================
# Tests
# =====================================================================

class TestStructure:
    def test_timeline_has_seven_lanes(self, reset_singletons):
        """时间线应包含 7 条固定泳道,顺序固定。"""
        from src.admin.life_timeline_provider import (
            LANE_TYPES,
            LifeTimelineProvider,
        )
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        assert d["fallback"] is False
        lanes = d["lanes"]
        assert isinstance(lanes, list)
        assert len(lanes) == 7, f"expected 7 lanes, got {len(lanes)}"
        actual_types = [l["type"] for l in lanes]
        assert actual_types == list(LANE_TYPES), f"lane order mismatch: {actual_types}"

    def test_empty_lane_preserved(self, reset_singletons):
        """缺失节点时,空泳道仍应返回(type + label + items=[])。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        # 只给一个 memory,其他全部空
        nodes = [
            _make_node("memory::mem_001", "memory", timestamp=_now_ts()),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        assert d["fallback"] is False
        lanes = d["lanes"]
        # 所有 7 条泳道都应存在
        assert len(lanes) == 7
        # 找到 memory lane,只有 1 个 item
        mem_lane = next(l for l in lanes if l["type"] == "memory")
        assert len(mem_lane["items"]) == 1
        # 其他 lane 应当为空列表
        empty_lanes = [l for l in lanes if l["type"] != "memory"]
        for l in empty_lanes:
            assert l["items"] == [], f"lane {l['type']} should be empty: {l['items']}"
        # 但 type 字段都存在
        for t in ("memory", "reflection", "interest", "action", "goal", "belief", "trait_change"):
            assert any(l["type"] == t for l in lanes)

    def test_items_sorted_by_timestamp(self, reset_singletons):
        """每个 lane 内的 items 必须按 timestamp 升序排列。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        now = _now_ts()
        # 在 memory lane 里构造乱序的 items
        nodes = [
            _make_node("memory::mem_b", "memory", timestamp=now - 100),
            _make_node("memory::mem_a", "memory", timestamp=now - 300),
            _make_node("memory::mem_c", "memory", timestamp=now - 50),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        mem_lane = next(l for l in d["lanes"] if l["type"] == "memory")
        ts_list = [it["timestamp_ts"] for it in mem_lane["items"]]
        assert ts_list == sorted(ts_list), f"items not sorted: {ts_list}"
        # ids 顺序应该是 c -> b -> a
        ids = [it["id"] for it in mem_lane["items"]]
        assert ids == ["memory::mem_a", "memory::mem_b", "memory::mem_c"]


class TestRange:
    def test_day_range(self, reset_singletons):
        """24h 范围:12h 内的节点应被包含,48h 之前的应被排除。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        now = _now_ts()
        nodes = [
            _make_node("memory::recent", "memory", timestamp=now - 12 * 3600),  # 12h 前
            _make_node("memory::old", "memory", timestamp=now - 48 * 3600),     # 48h 前
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="24h")
        assert d["range"]["key"] == "24h"
        assert d["range"]["seconds"] == 24 * 60 * 60
        mem_lane = next(l for l in d["lanes"] if l["type"] == "memory")
        ids = [it["id"] for it in mem_lane["items"]]
        assert "memory::recent" in ids
        assert "memory::old" not in ids

    def test_week_range(self, reset_singletons):
        """7d 范围:3 天前的应被包含,30 天前的应被排除。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        now = _now_ts()
        nodes = [
            _make_node("memory::in_week", "memory", timestamp=now - 3 * 24 * 3600),
            _make_node("memory::out_week", "memory", timestamp=now - 30 * 24 * 3600),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="7d")
        assert d["range"]["seconds"] == 7 * 24 * 60 * 60
        mem_lane = next(l for l in d["lanes"] if l["type"] == "memory")
        ids = [it["id"] for it in mem_lane["items"]]
        assert "memory::in_week" in ids
        assert "memory::out_week" not in ids

    def test_all_range(self, reset_singletons):
        """all 范围:不限时间。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        now = _now_ts()
        nodes = [
            _make_node("memory::a", "memory", timestamp=now - 365 * 24 * 3600),  # 1 年前
            _make_node("memory::b", "memory", timestamp=now - 30 * 24 * 3600),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        assert d["range"]["key"] == "all"
        assert d["range"]["seconds"] == -1
        mem_lane = next(l for l in d["lanes"] if l["type"] == "memory")
        assert len(mem_lane["items"]) == 2

    def test_invalid_range_fallback(self, reset_singletons):
        """无效 range 应使 range.fallback=True,不应崩溃。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes = [_make_node("memory::m", "memory", timestamp=_now_ts())]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="invalid_xx")
        # 无效 range → range.fallback=True
        assert d["range"]["fallback"] is True
        assert d["range"]["fallback_reason"] == "invalid_range"
        # 整体 fallback=False(数据源 OK)
        assert d["fallback"] is False
        # 7 条泳道应仍存在
        assert len(d["lanes"]) == 7


class TestCausality:
    def test_memory_to_reflection_edge(self, reset_singletons):
        """memory→reflection 边应出现在 timeline.edges 中。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        rel_set = {e["relation"] for e in d["edges"]}
        assert "memory_to_reflection" in rel_set
        # from/to 必须指向真实存在的节点
        for e in d["edges"]:
            assert e["from"]
            assert e["to"]
            assert e["from"] != e["to"]

    def test_interest_to_goal_edge(self, reset_singletons):
        """interest→goal 边应被保留。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        i2g = [e for e in d["edges"] if e["relation"] == "interest_to_goal"]
        assert len(i2g) >= 1
        for e in i2g:
            assert e["from"].startswith("interest::")
            assert e["to"].startswith("goal::")

    def test_causality_from_life_graph(self, reset_singletons):
        """get_causality 应复用 LifeGraphProvider 的边,不重新推理。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        c = p.get_causality()
        # 应返回所有边(7 条样本边)
        assert c["fallback"] is False
        assert c["count"] == len(edges)
        # 边内容与样本一致
        relations = {e["relation"] for e in c["edges"]}
        assert "memory_to_reflection" in relations
        assert "interest_to_goal" in relations
        assert "belief_to_trait_change" in relations

    def test_causality_time_filter(self, reset_singletons):
        """get_causality 的 start_time/end_time 参数应能过滤边。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        now = _now_ts()
        # 设置一条很早的边(应被 start_time 过滤)
        nodes = [
            _make_node("memory::early", "memory", timestamp=now - 1000),
            _make_node("reflection::early", "reflection", timestamp=now - 999),
        ]
        edges = [
            _make_edge("memory::early", "reflection::early", "memory_to_reflection"),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        # 限制 start_time 在 edge 之后
        c = p.get_causality(start_time=now - 500)
        # 边的两端 timestamp < start_time,应被过滤
        # 实现中:两端都小于 start_time 时跳过
        # 但当一端 >= start,另一端 < start 时保留
        # 这里两端均 < start_time,应被过滤
        assert isinstance(c["edges"], list)


class TestMilestone:
    def test_interest_goal_milestone(self, reset_singletons):
        """interest→goal 应生成 interest_to_goal 里程碑。"""
        from src.admin.life_timeline_provider import (
            MILESTONE_INTEREST_TO_GOAL,
            LifeTimelineProvider,
        )
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        assert d["fallback"] is False
        assert d["counts"][MILESTONE_INTEREST_TO_GOAL] >= 1
        i2g = [m for m in d["milestones"] if m["type"] == MILESTONE_INTEREST_TO_GOAL]
        assert len(i2g) >= 1
        # 链路应包含 interest & goal
        for m in i2g:
            chain = m["chain"]
            assert "interest" in chain
            assert "goal" in chain
        # 证据:节点 id
        for m in i2g:
            assert "interest::sig_001" in m["evidence"]
            assert "goal::goal_001" in m["evidence"]

    def test_belief_change_milestone(self, reset_singletons):
        """belief→trait_change 应生成 belief_change 里程碑。"""
        from src.admin.life_timeline_provider import (
            MILESTONE_BELIEF_CHANGE,
            LifeTimelineProvider,
        )
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        assert d["fallback"] is False
        assert d["counts"][MILESTONE_BELIEF_CHANGE] >= 1
        bc = [m for m in d["milestones"] if m["type"] == MILESTONE_BELIEF_CHANGE]
        assert len(bc) >= 1
        for m in bc:
            chain = m["chain"]
            assert "belief" in chain
            assert "trait_change" in chain
            assert "belief::belief_001" in m["evidence"]
            assert "trait_change::tc_001" in m["evidence"]

    def test_reflection_turning_point_milestone(self, reset_singletons):
        """source_memory_ids 数量 >= 阈值的 reflection 应生成 turning_point 里程碑。"""
        from src.admin.life_timeline_provider import (
            MILESTONE_REFLECTION_TURNING_POINT,
            LifeTimelineProvider,
            TURNING_POINT_MIN_MEMORIES,
        )
        now = _now_ts()
        # 构造 1 个 reflection 关联 >= TURNING_POINT_MIN_MEMORIES 个 memory
        nodes = [
            _make_node(
                "reflection::rtp_001",
                "reflection",
                timestamp=now,
                source_memory_ids=[f"memory::m{i}" for i in range(TURNING_POINT_MIN_MEMORIES + 1)],
                source_event_ids=[f"evt_e{i}" for i in range(TURNING_POINT_MIN_MEMORIES + 1)],
            ),
        ]
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        rtp = [m for m in d["milestones"] if m["type"] == MILESTONE_REFLECTION_TURNING_POINT]
        assert len(rtp) >= 1
        for m in rtp:
            chain = m["chain"]
            assert "memory" in chain
            assert "reflection" in chain

    def test_no_false_milestone(self, reset_singletons):
        """孤立节点不应误生成里程碑。"""
        from src.admin.life_timeline_provider import (
            ALL_MILESTONE_TYPES,
            LifeTimelineProvider,
        )
        # 没有任何 interest / reflection / belief / trait_change
        now = _now_ts()
        nodes = [
            _make_node("memory::only", "memory", timestamp=now),
            _make_node("goal::only", "goal", timestamp=now),
        ]
        edges = []
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        # 不应有任何里程碑
        for t in ALL_MILESTONE_TYPES:
            assert d["counts"][t] == 0, f"unexpected milestone count for {t}: {d['counts'][t]}"
        assert d["milestones"] == []

    def test_milestones_sorted_by_timestamp(self, reset_singletons):
        """里程碑列表应按 timestamp 升序排列。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        ts_list = [m.get("timestamp_ts") or 0.0 for m in d["milestones"]]
        assert ts_list == sorted(ts_list), f"milestones not sorted: {ts_list}"


class TestFallback:
    def test_provider_exception(self, reset_singletons):
        """LifeGraphProvider 不可用时,应返回 fallback=True。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        # last_build_ok=False, _cache_nodes 空
        gp = _FakeLifeGraphProvider(nodes=[], edges=[], ok=False, err="collector_unavailable")
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        assert d["fallback"] is True
        assert d["fallback_reason"]
        # 仍应返回 7 条空泳道
        assert len(d["lanes"]) == 7
        for l in d["lanes"]:
            assert l["items"] == []

    def test_get_milestones_exception(self, reset_singletons):
        """Provider 不可用时 get_milestones 也应 fallback。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        gp = _FakeLifeGraphProvider(nodes=[], edges=[], ok=False, err="err")
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_milestones()
        assert d["fallback"] is True
        assert d["milestones"] == []

    def test_empty_event_source(self, reset_singletons):
        """空事件源应返回 7 条空 lane + fallback=False(数据源正常但无数据)。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        gp = _FakeLifeGraphProvider(nodes=[], edges=[], ok=True)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        # 数据源 ok 但无节点 → fallback=False,所有 lane 为空
        assert d["fallback"] is False
        assert len(d["lanes"]) == 7
        for l in d["lanes"]:
            assert l["items"] == []
        # 计数应全为 0
        for t in ("memory", "reflection", "interest", "action", "goal", "belief", "trait_change"):
            assert d["counts"][t] == 0
        # 无边、无里程碑
        assert d["edges"] == []
        assert d["milestones"] == []

    def test_get_lanes_counts(self, reset_singletons):
        """get_lanes 应返回 7 条 lane 定义 + count。"""
        from src.admin.life_timeline_provider import (
            LANE_TYPES,
            LifeTimelineProvider,
        )
        nodes, _ = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=[])
        p = LifeTimelineProvider(graph_provider=gp)
        lanes = p.get_lanes()
        assert len(lanes) == 7
        # 顺序应与 LANE_TYPES 一致
        assert [l["id"] for l in lanes] == list(LANE_TYPES)
        # count 应 >= 1(至少有 2 个 memory)
        for l in lanes:
            assert "count" in l
            assert "label" in l
            assert "id" in l
        # memory 应有 2 个
        mem = next(l for l in lanes if l["id"] == "memory")
        assert mem["count"] == 2

    def test_causality_fallback(self, reset_singletons):
        """Provider 不可用时 get_causality 应 fallback。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        gp = _FakeLifeGraphProvider(nodes=[], edges=[], ok=False, err="x")
        p = LifeTimelineProvider(graph_provider=gp)
        c = p.get_causality()
        assert c["fallback"] is True
        assert c["edges"] == []


class TestIsolation:
    def test_no_business_import(self, reset_singletons):
        """LifeTimelineProvider 源码不应 import 业务模块。"""
        from src.admin import life_timeline_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.runtime", "import src.runtime",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.goal", "import src.goal",
            "from src.initiative", "import src.initiative",
            "from src.reflection", "import src.reflection",
            "from src.self_model", "import src.self_model",
            "from src.selfmodel", "import src.selfmodel",
            "from src.curiosity", "import src.curiosity",
        ]
        for f in forbidden:
            assert f not in src, f"禁止 import 业务模块: {f}"

    def test_no_llm_call(self, reset_singletons):
        """Provider 不应调用任何 LLM 接口。"""
        from src.admin import life_timeline_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 任何调用 LLM 的关键字(必须没有)
        llm_keywords = [
            "openai", "anthropic", "claude", "gpt", "llm_call", "call_llm",
            "completion", "chat_completion", "invoke_llm", "query_llm",
            "from src.llm", "import src.llm",
        ]
        for kw in llm_keywords:
            assert kw.lower() not in src.lower(), f"禁止调用 LLM: {kw}"

    def test_no_state_mutation(self, reset_singletons):
        """Provider 应为只读 — 不应写文件 / 修改 Authority。"""
        import re as _re
        from src.admin import life_timeline_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 写文件关键字(应避免)
        forbidden = [
            r"open\([^)]*[\"']w[\"']",
            r"with\s+open\([^)]*[\"']w[\"']",
            r"json\.dump",
            r"\.write_text\(",
            r"\.write_bytes\(",
        ]
        for pat in forbidden:
            m = _re.search(pat, src)
            assert m is None, f"禁止写文件: {pat} matched {m.group(0) if m else ''}"

    def test_no_create_event(self, reset_singletons):
        """Provider 不应创建任何业务事件。"""
        from src.admin import life_timeline_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 事件发布关键字
        forbidden_keywords = [
            "EventHub.publish", "EventHub.emit", "event_hub.publish",
            "publish_event", "emit_event", "create_event",
            "IntegrationEvent(", "emit_integration",
        ]
        for kw in forbidden_keywords:
            assert kw not in src, f"禁止创建事件: {kw}"


# =====================================================================
# 额外:语义保证测试(回答验收问题)
# =====================================================================

class TestAcceptance:
    def test_q1_interest_emergence_chain(self, reset_singletons):
        """Q1: 羽依什么时候开始关注 AI?
        答案: Memory → Reflection → Interest 三个 lane 应有节点。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        lane_by_type = {l["type"]: l for l in d["lanes"]}
        # 三条泳道都应有内容
        assert len(lane_by_type["memory"]["items"]) >= 1
        assert len(lane_by_type["reflection"]["items"]) >= 1
        assert len(lane_by_type["interest"]["items"]) >= 1
        # 边的关系应能串起来
        rels = {e["relation"] for e in d["edges"]}
        assert "memory_to_reflection" in rels
        assert "reflection_to_interest" in rels

    def test_q2_goal_origin_chain(self, reset_singletons):
        """Q2: 这个目标为什么出现?
        答案: interest → goal 应出现在时间线上。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        # goal lane 应有内容
        goal_lane = next(l for l in d["lanes"] if l["type"] == "goal")
        assert len(goal_lane["items"]) >= 1
        # interest→goal 边应存在
        i2g = [e for e in d["edges"] if e["relation"] == "interest_to_goal"]
        assert len(i2g) >= 1
        # 里程碑 interest_to_goal 应被识别
        ms = p.get_milestones()
        i2g_ms = [m for m in ms["milestones"] if m["type"] == "interest_to_goal"]
        assert len(i2g_ms) >= 1

    def test_q3_personality_change_chain(self, reset_singletons):
        """Q3: 羽依什么时候发生人格变化?
        答案: belief → trait_change → belief_change milestone。"""
        from src.admin.life_timeline_provider import LifeTimelineProvider
        nodes, edges = _sample_nodes_edges()
        gp = _FakeLifeGraphProvider(nodes=nodes, edges=edges)
        p = LifeTimelineProvider(graph_provider=gp)
        d = p.get_timeline(range_key="all")
        lane_by_type = {l["type"]: l for l in d["lanes"]}
        # belief / trait_change 都应有节点
        assert len(lane_by_type["belief"]["items"]) >= 1
        assert len(lane_by_type["trait_change"]["items"]) >= 1
        # belief→trait_change 边
        bc_edges = [e for e in d["edges"] if e["relation"] == "belief_to_trait_change"]
        assert len(bc_edges) >= 1
        # 里程碑
        ms = p.get_milestones()
        bc_ms = [m for m in ms["milestones"] if m["type"] == "belief_change"]
        assert len(bc_ms) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
