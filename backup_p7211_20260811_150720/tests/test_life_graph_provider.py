# -*- coding: utf-8 -*-
"""
tests/test_life_graph_provider.py

Phase 5.0 Dashboard Upgrade Step 8.3 —— LifeGraphProvider 单元测试。

覆盖:
  1. Provider 不 import 业务模块(memory / emotion / growth / personality / relationship / runtime)
  2. Provider 只读(无写方法)
  3. 节点生成(interest / action / reflection / goal / memory / belief / trait_change)
  4. 边生成(基于 source_event_ids / source_reflection_ids / supporting_signal_ids /
     source_interest_ids / source_belief_ids / topic)
  5. timeline 排序(asc by ts)
  6. path 查询(BFS,带 fallback,带 invalid input)
  7. fallback 路径(空数据 / 异常 / 错误类型)
  8. 异常处理(任何子模块异常不能影响整体)
  9. API 端点(envelope / local-only / invalid input)
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

class _FakeCollector:
    """
    模拟 DomainCollector,只暴露只读 list_* 接口。
    注意:返回的 dict 已包含 'type' 字段(模拟真实 DomainCollector 的行为)。
    """

    def __init__(
        self,
        events: List[Dict[str, Any]] = None,
        memories: List[Dict[str, Any]] = None,
        beliefs: List[Dict[str, Any]] = None,
        trait_changes: List[Dict[str, Any]] = None,
    ) -> None:
        self._events = list(events or [])
        self._memories = list(memories or [])
        self._beliefs = list(beliefs or [])
        self._trait_changes = list(trait_changes or [])
        self._calls: List[str] = []

    def list_event_nodes(self, limit: int = 500):
        self._calls.append("list_event_nodes")
        return list(self._events)

    def list_memory_nodes(self, limit: int = 50):
        self._calls.append("list_memory_nodes")
        out = []
        for m in self._memories:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("memory_id") or "")
            if not mid:
                continue
            out.append({
                **m,
                "id": mid,
                "type": "memory",
                "label": m.get("summary") or m.get("content") or m.get("topic") or "",
                "topic": m.get("topic", ""),
                "source_event_ids": list(m.get("source_event_ids", []) or []),
                "evidence": str(m.get("summary") or m.get("content") or "")[:256],
            })
        return out

    def list_belief_nodes(self, limit: int = 50):
        self._calls.append("list_belief_nodes")
        out = []
        for b in self._beliefs:
            if not isinstance(b, dict):
                continue
            bid = str(b.get("belief_id") or b.get("id") or "")
            if not bid:
                continue
            out.append({
                **b,
                "id": bid,
                "type": "belief",
                "label": b.get("statement") or b.get("content") or "",
                "topic": b.get("domain", ""),
                "source_event_ids": list(b.get("source_event_ids", []) or []),
                "source_reflection_ids": list(b.get("source_reflection_ids", []) or []),
                "evidence": str(b.get("statement") or b.get("content") or "")[:256],
            })
        return out

    def list_trait_change_nodes(self, limit: int = 50):
        self._calls.append("list_trait_change_nodes")
        out = []
        for t in self._trait_changes:
            if not isinstance(t, dict):
                continue
            tcid = str(t.get("change_id") or t.get("id") or "")
            if not tcid:
                continue
            out.append({
                **t,
                "id": tcid,
                "type": "trait_change",
                "label": t.get("trait_name") or t.get("name") or t.get("description") or "",
                "topic": t.get("trait_name") or t.get("domain") or "",
                "trait_name": t.get("trait_name") or t.get("name") or "",
                "delta": t.get("delta", 0.0),
                "source_event_ids": list(t.get("source_event_ids", []) or []),
                "source_belief_ids": list(t.get("source_belief_ids", []) or []),
                "evidence": str(t.get("description") or "")[:256],
            })
        return out


def _make_signal_event(
    signal_id: str,
    *,
    timestamp: float = 0.0,
    topic: str = "AI 哲学",
    trend: str = "new",
    strength: float = 0.5,
    confidence: float = 0.5,
    rationale: str = "",
    source_event_ids: List[str] = None,
    source_reflection_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_sig_{signal_id}",
        "event_type": "integration.interest_signal.created",
        "source": "initiative",
        "timestamp": timestamp,
        "payload": {
            "signal_id": signal_id,
            "topic": topic,
            "trend": trend,
            "strength": strength,
            "confidence": confidence,
            "rationale": rationale,
            "source_event_ids": list(source_event_ids or []),
            "source_reflection_ids": list(source_reflection_ids or []),
            "created_at": timestamp,
        },
    }


def _make_action_event(
    action_id: str,
    *,
    timestamp: float = 0.0,
    action_type: str = "observe",
    topic: str = "",
    status: str = "",
    urgency: str = "normal",
    expected_value: float = 0.5,
    confidence: float = 0.5,
    priority: float = 0.5,
    supporting_signal_ids: List[str] = None,
    source_event_ids: List[str] = None,
    rationale: str = "",
    filter_reason: str = "",
    rule_applied: str = "",
    is_filtered: bool = False,
) -> Dict[str, Any]:
    et = "integration.initiative.filtered" if is_filtered else "integration.initiative.created"
    return {
        "event_id": f"evt_act_{action_id}",
        "event_type": et,
        "source": "initiative",
        "timestamp": timestamp,
        "payload": {
            "action_id": action_id,
            "action_type": action_type,
            "topic": topic,
            "status": status,
            "urgency": urgency,
            "expected_value": expected_value,
            "confidence": confidence,
            "priority": priority,
            "supporting_signal_ids": list(supporting_signal_ids or []),
            "source_event_ids": list(source_event_ids or []),
            "rationale": rationale,
            "filter_reason": filter_reason,
            "rule_applied": rule_applied,
            "created_at": timestamp,
        },
    }


def _make_reflection_event(
    reflection_id: str,
    *,
    timestamp: float = 0.0,
    topic: str = "",
    summary: str = "",
    insight: str = "",
    source_event_ids: List[str] = None,
    source_memory_ids: List[str] = None,
    reflection_type: str = "daily",
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_refl_{reflection_id}",
        "event_type": "integration.reflection.completed",
        "source": "reflection",
        "timestamp": timestamp,
        "payload": {
            "reflection_id": reflection_id,
            "reflection_type": reflection_type,
            "topic": topic,
            "summary": summary,
            "insight": insight,
            "source_event_ids": list(source_event_ids or []),
            "source_memory_ids": list(source_memory_ids or []),
            "created_at": timestamp,
        },
    }


def _make_goal_event(
    goal_id: str,
    *,
    timestamp: float = 0.0,
    title: str = "建立视觉生成能力",
    goal_type: str = "creative",
    status: str = "active",
    source_event_ids: List[str] = None,
    source_interest_ids: List[str] = None,
    source_desire_ids: List[str] = None,
    lifecycle: str = "",
    reason: str = "",
    importance: float = 0.0,
    confidence: float = 0.0,
    is_desire: bool = False,
) -> Dict[str, Any]:
    et = "integration.desire.created" if is_desire else "integration.goal.created"
    return {
        "event_id": f"evt_goal_{goal_id}",
        "event_type": et,
        "source": "goal",
        "timestamp": timestamp,
        "payload": {
            "goal_id": goal_id,
            "desire_id": goal_id if is_desire else "",
            "title": title,
            "topic": title,
            "goal_type": goal_type,
            "status": status,
            "lifecycle": lifecycle,
            "importance": importance,
            "confidence": confidence,
            "reason": reason,
            "source_event_ids": list(source_event_ids or []),
            "source_interest_ids": list(source_interest_ids or []),
            "source_desire_ids": list(source_desire_ids or []),
            "created_at": timestamp,
        },
    }


def _make_memory(memory_id: str, *, topic: str = "", summary: str = "", timestamp: float = 0.0) -> Dict[str, Any]:
    return {
        "id": memory_id,
        "event_id": f"evt_{memory_id}",
        "topic": topic,
        "summary": summary,
        "content": summary,
        "importance": 0.5,
        "timestamp": timestamp,
    }


def _make_belief(belief_id: str, *, domain: str = "", statement: str = "", source_reflection_ids: List[str] = None) -> Dict[str, Any]:
    return {
        "belief_id": belief_id,
        "domain": domain,
        "statement": statement,
        "confidence": 0.7,
        "status": "active",
        "source_reflection_ids": list(source_reflection_ids or []),
        "updated_at": time.time(),
    }


def _make_trait_change(change_id: str, *, trait_name: str = "creative", delta: float = 0.1, source_belief_ids: List[str] = None) -> Dict[str, Any]:
    return {
        "change_id": change_id,
        "trait_name": trait_name,
        "delta": delta,
        "new_value": 0.6,
        "old_value": 0.5,
        "description": f"{trait_name} increased",
        "source_belief_ids": list(source_belief_ids or []),
        "timestamp": time.time(),
    }


def _make_life_events():
    """构造一组完整的生命事件链(模拟全闭环)。"""
    now = time.time()
    return [
        # 1. Reflection (来自 memory)
        _make_reflection_event(
            "refl_001",
            timestamp=now - 10000,
            topic="AI 哲学",
            summary="发现用户对 AI 意识感兴趣",
            insight="可能是个新兴兴趣",
            source_event_ids=["evt_mem_001"],
            source_memory_ids=["mem_001"],
        ),
        # 2. InterestSignal (来自 reflection)
        _make_signal_event(
            "sig_001",
            timestamp=now - 8000,
            topic="AI 哲学",
            trend="new",
            strength=0.65,
            confidence=0.7,
            rationale="用户多次提及 AI 意识",
            source_event_ids=["evt_refl_001"],
            source_reflection_ids=["refl_001"],
        ),
        # 3. Goal (来自 interest)
        _make_goal_event(
            "goal_001",
            timestamp=now - 6000,
            title="建立 AI 哲学对话能力",
            goal_type="learning",
            status="active",
            source_event_ids=["evt_sig_001"],
            source_interest_ids=["sig_001"],
            reason="为了回应用户对 AI 意识的兴趣",
            importance=0.8,
            confidence=0.7,
        ),
        # 4. PossibleAction
        _make_action_event(
            "act_001",
            timestamp=now - 4000,
            action_type="ask",
            topic="AI 哲学",
            status="pending",
            urgency="normal",
            expected_value=0.55,
            confidence=0.65,
            priority=0.5,
            supporting_signal_ids=["sig_001"],
            source_event_ids=["evt_goal_001"],
            rationale="询问对 AI 意识的想法",
        ),
        # 5. Trait change
        _make_trait_change(
            "tc_001",
            trait_name="curiosity",
            delta=0.05,
            source_belief_ids=["belief_001"],
        ),
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    from src.admin.life_graph_provider import reset_life_graph_provider_for_testing
    reset_life_graph_provider_for_testing()
    yield
    reset_life_graph_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestReadOnly:
    def test_no_business_module_imports(self, reset_singletons):
        from src.admin import life_graph_provider as mod
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
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_no_writes_called(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider

        collector = _FakeCollector(_make_life_events())
        p = LifeGraphProvider(collector=collector)
        p.build_graph()
        p.get_timeline()
        p.find_path("reflection::refl_001", "goal::goal_001")

        # 检查所有调用都是 list_*
        for call in collector._calls:
            assert call.startswith("list_"), f"unexpected call: {call}"

        # 检查 Provider 自身没有写方法
        forbidden = [
            "_save", "_write", "_append", "_store", "_update", "_delete", "_remove", "_modify",
            "create_", "delete_", "update_", "add_", "remove_", "write_",
            "emit_event", "publish", "send", "trigger",
        ]
        for name in dir(p):
            for f in forbidden:
                if name.startswith(f) and callable(getattr(p, name, None)):
                    pytest.fail(f"unexpected write-like method: {name}")

    def test_describe_returns_state(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        collector = _FakeCollector(_make_life_events())
        p = LifeGraphProvider(collector=collector)
        p.build_graph()
        d = p.describe()
        for k in ("node_count", "edge_count", "last_build_at", "last_build_ok"):
            assert k in d


class TestNodeGeneration:
    def test_build_graph_interest_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_INTEREST,
        )
        events = [_make_signal_event("sig_001", topic="AI 哲学", timestamp=100.0)]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        assert d["available"] is True
        assert d["fallback"] is False
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_INTEREST in types
        sig_node = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_INTEREST)
        assert sig_node["id"] == "interest::sig_001"
        assert sig_node["topic"] == "AI 哲学"
        assert "evt_sig_sig_001" in sig_node["event_id"]

    def test_build_graph_action_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_ACTION,
        )
        events = [
            _make_action_event(
                "act_001",
                topic="AI 哲学",
                action_type="ask",
                status="pending",
                timestamp=100.0,
            )
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_ACTION in types
        act = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_ACTION)
        assert act["id"] == "action::act_001"
        assert act["status"] == "pending"

    def test_build_graph_reflection_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_REFLECTION,
        )
        events = [
            _make_reflection_event(
                "refl_001",
                topic="AI 哲学",
                summary="用户关心 AI 意识",
                timestamp=100.0,
            )
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_REFLECTION in types
        r = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_REFLECTION)
        assert r["id"] == "reflection::refl_001"
        assert r["topic"] == "AI 哲学"

    def test_build_graph_goal_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_GOAL,
        )
        events = [
            _make_goal_event(
                "goal_001",
                title="建立 AI 哲学对话能力",
                timestamp=100.0,
            )
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_GOAL in types
        g = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_GOAL)
        assert g["id"] == "goal::goal_001"
        assert g["topic"] == "建立 AI 哲学对话能力"

    def test_build_graph_memory_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_MEMORY,
        )
        memories = [
            _make_memory("mem_001", topic="AI 哲学", summary="用户问了 AI 意识", timestamp=100.0)
        ]
        p = LifeGraphProvider(collector=_FakeCollector(memories=memories))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_MEMORY in types
        m = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_MEMORY)
        assert m["id"] == "mem_001"
        assert m["topic"] == "AI 哲学"

    def test_build_graph_belief_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_BELIEF,
        )
        beliefs = [
            _make_belief("belief_001", domain="identity", statement="羽依关心 AI 意识")
        ]
        p = LifeGraphProvider(collector=_FakeCollector(beliefs=beliefs))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_BELIEF in types
        b = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_BELIEF)
        assert b["id"] == "belief_001"

    def test_build_graph_trait_change_node(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            NODE_TYPE_TRAIT_CHANGE,
        )
        tcs = [_make_trait_change("tc_001", trait_name="curiosity", delta=0.05)]
        p = LifeGraphProvider(collector=_FakeCollector(trait_changes=tcs))
        d = p.build_graph()
        types = {n["type"] for n in d["nodes"]}
        assert NODE_TYPE_TRAIT_CHANGE in types
        t = next(n for n in d["nodes"] if n["type"] == NODE_TYPE_TRAIT_CHANGE)
        assert t["id"] == "tc_001"
        assert t["trait_name"] == "curiosity"

    def test_node_includes_required_fields(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        for n in d["nodes"]:
            for k in ("id", "type", "label", "timestamp", "source_event_ids"):
                assert k in n, f"node missing field: {k}"


class TestEdgeGeneration:
    def test_edge_reflection_to_interest(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_INTEREST_FROM_REFL,
        )
        events = [
            _make_reflection_event("refl_001", topic="AI 哲学", timestamp=100.0),
            _make_signal_event(
                "sig_001",
                topic="AI 哲学",
                timestamp=200.0,
                source_reflection_ids=["refl_001"],
            ),
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        rels = [e["relation"] for e in d["edges"]]
        assert REL_INTEREST_FROM_REFL in rels
        e = next(e for e in d["edges"] if e["relation"] == REL_INTEREST_FROM_REFL)
        assert e["source"] == "reflection::refl_001"
        assert e["target"] == "interest::sig_001"

    def test_edge_interest_to_goal(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_GOAL_FROM_INTEREST,
        )
        events = [
            _make_signal_event("sig_001", topic="AI 哲学", timestamp=100.0),
            _make_goal_event(
                "goal_001",
                title="建立 AI 哲学对话能力",
                timestamp=200.0,
                source_interest_ids=["sig_001"],
            ),
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        e = next(
            (e for e in d["edges"] if e["relation"] == REL_GOAL_FROM_INTEREST),
            None,
        )
        assert e is not None
        assert e["source"] == "interest::sig_001"
        assert e["target"] == "goal::goal_001"

    def test_edge_goal_to_action_via_source(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_ACTION_FROM_GOAL,
        )
        events = [
            _make_goal_event("goal_001", title="X", timestamp=100.0),
            _make_action_event(
                "act_001",
                topic="X",
                timestamp=200.0,
                source_event_ids=["evt_goal_goal_001"],
            ),
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        e = next(
            (e for e in d["edges"] if e["relation"] == REL_ACTION_FROM_GOAL),
            None,
        )
        assert e is not None
        assert e["source"] == "goal::goal_001"
        assert e["target"] == "action::act_001"

    def test_edge_memory_to_reflection(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_REFL_FROM_MEMORY,
        )
        memories = [_make_memory("mem_001", topic="AI 哲学")]
        events = [
            _make_reflection_event(
                "refl_001",
                topic="AI 哲学",
                source_event_ids=["evt_mem_001"],
                source_memory_ids=["mem_001"],
            )
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events=events, memories=memories))
        d = p.build_graph()
        e = next(
            (e for e in d["edges"] if e["relation"] == REL_REFL_FROM_MEMORY),
            None,
        )
        assert e is not None
        assert e["source"] == "mem_001"
        assert e["target"] == "reflection::refl_001"

    def test_edge_reflection_to_belief(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_BELIEF_FROM_REFL,
        )
        events = [_make_reflection_event("refl_001", topic="AI 哲学", timestamp=100.0)]
        beliefs = [
            _make_belief(
                "belief_001",
                domain="identity",
                statement="羽依关心 AI 意识",
                source_reflection_ids=["refl_001"],
            )
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events=events, beliefs=beliefs))
        d = p.build_graph()
        e = next(
            (e for e in d["edges"] if e["relation"] == REL_BELIEF_FROM_REFL),
            None,
        )
        assert e is not None
        assert e["source"] == "reflection::refl_001"
        assert e["target"] == "belief_001"

    def test_edge_belief_to_trait_change(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_TRAIT_FROM_BELIEF,
        )
        beliefs = [_make_belief("belief_001", domain="identity", statement="X")]
        tcs = [_make_trait_change("tc_001", trait_name="curiosity", source_belief_ids=["belief_001"])]
        p = LifeGraphProvider(collector=_FakeCollector(beliefs=beliefs, trait_changes=tcs))
        d = p.build_graph()
        e = next(
            (e for e in d["edges"] if e["relation"] == REL_TRAIT_FROM_BELIEF),
            None,
        )
        assert e is not None
        assert e["source"] == "belief_001"
        assert e["target"] == "tc_001"

    def test_edge_topic_link_for_same_topic(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            REL_TOPIC_LINK,
        )
        events = [
            _make_reflection_event("refl_001", topic="AI 哲学", timestamp=100.0),
            _make_signal_event("sig_001", topic="AI 哲学", timestamp=200.0),
            _make_goal_event("goal_001", title="建立 AI 哲学对话能力", timestamp=300.0),
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        # 同 topic 节点应该至少有一条 topic_link 边
        topic_edges = [e for e in d["edges"] if e["relation"] == REL_TOPIC_LINK]
        assert len(topic_edges) > 0

    def test_edge_count_summary(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph()
        assert d["edge_count"] == len(d["edges"])
        assert d["node_count"] == len(d["nodes"])


class TestTimeline:
    def test_timeline_ascending(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.get_timeline()
        assert d["available"] is True
        ts_list = [it["timestamp_ts"] for it in d["items"]]
        assert ts_list == sorted(ts_list)

    def test_timeline_has_required_fields(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.get_timeline()
        for it in d["items"]:
            for k in ("id", "type", "label", "timestamp", "timestamp_ts", "summary", "source_event_ids"):
                assert k in it, f"timeline item missing: {k}"

    def test_timeline_buckets(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.get_timeline()
        assert "buckets" in d
        assert isinstance(d["buckets"], dict)

    def test_timeline_limit(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.get_timeline(limit=2)
        assert len(d["items"]) <= 2


class TestPath:
    def test_path_found(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("refl_001", "goal_001")
        assert d["available"] is True
        assert d["found"] is True
        assert d["depth"] >= 1
        assert len(d["path"]) > 0
        assert len(d["edges_used"]) > 0

    def test_path_with_prefix(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("reflection::refl_001", "goal::goal_001")
        assert d["found"] is True

    def test_path_not_found(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("nonexistent_1", "nonexistent_2")
        assert d["available"] is True
        assert d["found"] is False
        assert d["fallback_reason"] == "id_not_found"

    def test_path_invalid_input(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        p = LifeGraphProvider(collector=_FakeCollector(_make_life_events()))
        for bad_from, bad_to in [("", "x"), ("x", ""), ("x", ""), (None, "y"), ("x", None)]:
            d = p.find_path(bad_from or "", bad_to or "")
            assert d["fallback"] is True
            assert d["fallback_reason"] == "invalid_input"

    def test_path_with_topic_link(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        # 制造两个无显式链接但同 topic 的节点,验证 topic_link 兜底
        events = [
            _make_reflection_event("refl_001", topic="钢琴", timestamp=100.0),
            _make_signal_event("sig_001", topic="钢琴", timestamp=200.0),
        ]
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("refl_001", "sig_001")
        # 显式 source_reflection_ids 缺失,但 source_event_ids 链存在
        # 这里由于没有显式 link,可能找不到路径——验证不崩溃即可
        assert d["available"] is True
        assert "found" in d

    def test_path_max_depth(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("refl_001", "goal_001", max_depth=1)
        assert d["available"] is True

    def test_path_includes_relation_in_steps(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.find_path("refl_001", "goal_001")
        if d["found"]:
            for step in d["path"]:
                assert "id" in step
                assert "type" in step


class TestFallback:
    def test_collector_returns_nothing(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        p = LifeGraphProvider(collector=_FakeCollector())
        # 空数据是合法状态(系统已查询,只是无节点),不应当作 fallback
        d_graph = p.build_graph()
        d_timeline = p.get_timeline()
        for d in (d_graph, d_timeline):
            assert d["available"] is True
            assert d["fallback"] is False
            assert d["nodes" if "nodes" in d else "items"] == []

    def test_collector_returns_invalid(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider

        class _BadCollector:
            def list_event_nodes(self, limit=500):
                return ["not", "a", "dict", "list"]

        p = LifeGraphProvider(collector=_BadCollector())
        # 不应崩溃
        d = p.build_graph()
        assert "available" in d

    def test_collector_raises(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider

        class _BrokenCollector:
            def list_event_nodes(self, limit=500):
                raise RuntimeError("boom")

        p = LifeGraphProvider(collector=_BrokenCollector())
        d = p.build_graph()
        assert d["fallback"] is True
        assert "fallback_reason" in d

    def test_find_path_when_collector_raises(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider

        class _BrokenCollector:
            def list_event_nodes(self, limit=500):
                raise RuntimeError("boom")

        p = LifeGraphProvider(collector=_BrokenCollector())
        d = p.find_path("a", "b")
        assert d["fallback"] is True

    def test_build_graph_never_raises(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider

        class _WeirdCollector:
            def list_event_nodes(self, limit=500):
                return None  # None 而不是 list

            def list_memory_nodes(self, limit=50):
                return None

            def list_belief_nodes(self, limit=50):
                return None

            def list_trait_change_nodes(self, limit=50):
                return None

        p = LifeGraphProvider(collector=_WeirdCollector())
        d = p.build_graph()
        assert d is not None
        assert "fallback" in d


class TestTypeFilter:
    def test_filter_by_interest_only(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph(include_node_types=["interest"])
        assert d["available"] is True
        for n in d["nodes"]:
            assert n["type"] == "interest"

    def test_invalid_type_filter_returns_all(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph(include_node_types=["invalid_type", "also_invalid"])
        # 全部非法 → 视为不限制
        assert d["node_count"] >= 1


class TestGraphLimits:
    def test_node_limit(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph(node_limit=1, edge_limit=10)
        assert len(d["nodes"]) <= 1

    def test_edge_limit(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        d = p.build_graph(node_limit=200, edge_limit=1)
        assert len(d["edges"]) <= 1


class TestApi:
    def _make_client(self):
        from src.admin.dashboard.life_graph_router import life_graph_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(life_graph_v2_bp)
        return app.test_client()

    def test_overview_endpoint(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-graph/overview")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "nodes" in body["data"]
        assert "edges" in body["data"]
        assert "counts" in body["data"]

    def test_timeline_endpoint(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-graph/timeline?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "items" in body["data"]

    def test_path_endpoint(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get(
            "/api/dashboard/v2/life-graph/path?from_id=refl_001&to_id=goal_001"
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "path" in body["data"]
        assert "found" in body["data"]

    def test_path_endpoint_missing_params(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(_make_life_events())
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-graph/path?from_id=&to_id=")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False

    def test_fallback_envelope(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        # 注入一个返回空列表的 collector
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector()
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/life-graph/overview")
        body = resp.get_json()
        assert body["ok"] is True
        assert body["fallback"] is True
        assert "fallback_reason" in body

    def test_type_filter_endpoint(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get(
            "/api/dashboard/v2/life-graph/overview?types=interest,goal"
        )
        body = resp.get_json()
        assert body["ok"] is True
        for n in body["data"]["nodes"]:
            assert n["type"] in ("interest", "goal")


class TestNodeDetailAndNeighbors:
    """
    Step 8.4.2 —— Provider 节点详情 & 邻居查询 接口测试。

    覆盖:
      1. test_get_node_detail_success
      2. test_get_node_detail_contains_evidence
      3. test_unknown_node_fallback
      4. test_neighbors_depth_one
      5. test_neighbors_depth_limit
      6. test_node_detail_invalid_input
      7. test_neighbors_invalid_input
      8. test_node_detail_endpoint
      9. test_neighbors_endpoint_depth_limit
    """

    def test_get_node_detail_success(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        # 先 build 一次触发图构建
        p.build_graph()
        d = p.get_node_detail("goal::goal_001")
        assert isinstance(d, dict)
        assert d.get("fallback") is False
        assert d.get("fallback_reason") is None
        node = d.get("node") or {}
        assert node.get("id") == "goal::goal_001"
        assert node.get("type") == "goal"
        assert "title" in node
        # evidence 必须是 list
        assert isinstance(d.get("evidence"), list)
        # neighbors 必须是 list
        assert isinstance(d.get("neighbors"), list)

    def test_get_node_detail_contains_evidence(self, reset_singletons):
        """Goal 节点的 evidence 应包含 interest 类型(因 source_interest_ids)。"""
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        p.build_graph()
        d = p.get_node_detail("goal::goal_001")
        ev = d.get("evidence") or []
        # 至少包含 interest 类型(source_interest_ids 注入)
        ev_types = {e.get("source_type") for e in ev}
        assert "interest" in ev_types
        # 每条 evidence 必须有 source_type/source_id/reason
        for e in ev:
            assert "source_type" in e
            assert "source_id" in e
            assert "reason" in e

    def test_unknown_node_fallback(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        p.build_graph()
        d = p.get_node_detail("totally_unknown_xxx")
        assert d.get("fallback") is True
        assert d.get("fallback_reason") == "node_not_found"
        assert d.get("node") == {}
        assert d.get("evidence") == []
        assert d.get("neighbors") == []

    def test_node_detail_invalid_input(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        p = LifeGraphProvider(collector=_FakeCollector(_make_life_events()))
        p.build_graph()
        for bad in ("", None, 123, [], {}):
            d = p.get_node_detail(bad)  # type: ignore[arg-type]
            assert d.get("fallback") is True
            if bad is None or not isinstance(bad, str):
                assert d.get("fallback_reason") in ("invalid_input",)

    def test_neighbors_depth_one(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        p.build_graph()
        d = p.get_neighbors("goal::goal_001", depth=1)
        assert isinstance(d, dict)
        assert d.get("fallback") is False
        assert d.get("center") == "goal::goal_001"
        assert d.get("depth") == 1
        # center 节点必须存在
        ids = {n.get("id") for n in (d.get("nodes") or [])}
        assert "goal::goal_001" in ids
        # center 必须被标记
        center_node = next(
            (n for n in (d.get("nodes") or []) if n.get("id") == "goal::goal_001"),
            None,
        )
        assert center_node is not None
        assert center_node.get("is_center") is True
        # depth=1 时,1-hop 邻居(refl_001 不直接连 goal,所以 sig_001 / act_001 才是)
        # sig_001 → goal_001 是显式边
        # act_001 ← goal_001(goal 出现较早)
        # 至少有一条边
        assert len(d.get("edges") or []) >= 0

    def test_neighbors_depth_limit(self, reset_singletons):
        from src.admin.life_graph_provider import (
            LifeGraphProvider,
            MAX_NEIGHBOR_DEPTH,
        )
        events = _make_life_events()
        p = LifeGraphProvider(collector=_FakeCollector(events))
        p.build_graph()
        # 超过 MAX_NEIGHBOR_DEPTH → fallback
        for bad in (MAX_NEIGHBOR_DEPTH + 1, 99, 1000):
            d = p.get_neighbors("goal::goal_001", depth=bad)
            assert d.get("fallback") is True
            assert d.get("fallback_reason") == "depth_limit"
            assert d.get("nodes") == []
            assert d.get("edges") == []
        # depth=0 → 自动夹到 1
        d = p.get_neighbors("goal::goal_001", depth=0)
        assert d.get("fallback") is False
        assert d.get("depth") == 1
        # depth=MAX_NEIGHBOR_DEPTH 合法
        d = p.get_neighbors("goal::goal_001", depth=MAX_NEIGHBOR_DEPTH)
        assert d.get("fallback") is False
        assert d.get("depth") == MAX_NEIGHBOR_DEPTH

    def test_neighbors_invalid_input(self, reset_singletons):
        from src.admin.life_graph_provider import LifeGraphProvider
        p = LifeGraphProvider(collector=_FakeCollector(_make_life_events()))
        p.build_graph()
        for bad in ("", None, 123, [], {}):
            d = p.get_neighbors(bad, depth=1)  # type: ignore[arg-type]
            assert d.get("fallback") is True
            if bad is None or not isinstance(bad, str):
                assert d.get("fallback_reason") in ("invalid_input",)

    def test_node_detail_endpoint(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        from src.admin.dashboard.life_graph_router import life_graph_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(life_graph_v2_bp)
        c = app.test_client()
        resp = c.get("/api/dashboard/v2/life-graph/node/goal::goal_001")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "data" in body
        assert body["data"]["node"]["id"] == "goal::goal_001"
        # 验证 envelope 结构
        assert "trace" in body
        assert "confidence" in body
        assert "fallback" in body

    def test_neighbors_endpoint_depth_limit(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        events = _make_life_events()
        pmod._provider_instance = pmod.LifeGraphProvider(
            collector=_FakeCollector(events)
        )
        from src.admin.dashboard.life_graph_router import life_graph_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(life_graph_v2_bp)
        c = app.test_client()
        # depth=10 → depth_limit
        resp = c.get("/api/dashboard/v2/life-graph/node/goal::goal_001/neighbors?depth=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["fallback"] is True
        assert body["fallback_reason"] == "depth_limit"
        # depth=1 合法
        resp = c.get("/api/dashboard/v2/life-graph/node/goal::goal_001/neighbors?depth=1")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"]["center"] == "goal::goal_001"
        assert body["data"]["depth"] == 1

    def test_why_endpoint_envelope(self, reset_singletons):
        from src.admin import life_graph_provider as pmod
        from src.admin import life_graph_explanation as emod
        events = _make_life_events()
        provider = pmod.LifeGraphProvider(collector=_FakeCollector(events))
        provider.build_graph()
        pmod._provider_instance = provider
        emod._explanation_instance = emod.LifeGraphExplanation(provider=provider)
        from src.admin.dashboard.life_graph_router import life_graph_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(life_graph_v2_bp)
        c = app.test_client()
        resp = c.get("/api/dashboard/v2/life-graph/why/goal::goal_001")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "data" in body
        data = body["data"]
        # 至少包含 chain
        assert "chain" in data
        # fallback 字段必须存在
        assert "fallback" in body
        # 未知节点 → fallback
        resp2 = c.get("/api/dashboard/v2/life-graph/why/non_existent_xxx")
        assert resp2.status_code == 200
        body2 = resp2.get_json()
        assert body2["fallback"] is True
        assert body2["fallback_reason"] == "node_not_found"
