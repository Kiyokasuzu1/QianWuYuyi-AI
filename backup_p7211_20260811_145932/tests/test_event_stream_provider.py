# -*- coding: utf-8 -*-
"""
tests/test_event_stream_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.4 —— EventStreamProvider 单元测试。

覆盖:
  1. TestBasic       —— list_events 返回结构 / 必填字段
  2. TestFilter      —— types / keyword / since_ts 三种过滤
  3. TestLimit       —— limit 默认 / 上限 / 非法
  4. TestSummary     —— summary 提取规则(payload.summary → title → event_type)
  5. TestTrace       —— trace.source_refs / evidence_count
  6. TestFallback    —— EventHub 不可用 / 非法事件形状
  7. TestIsolation   —— 不 import 业务模块 / 不调用 LLM
  8. TestAPI         —— /event-stream / /types / /health 三个端点

合计:21 个测试(>= 18 要求)。
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
    模拟 EventHubDomainCollector.list_events。
    返回一组预设事件。
    """

    def __init__(self, events: List[Dict[str, Any]] = None) -> None:
        self._events = list(events or [])

    def list_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        return list(self._events[:limit])


class _FakeEventHub:
    """模拟 dashboard.event_hub.DashboardEventHub.poll_recent / subscribe。"""

    def __init__(self, events: List[Dict[str, Any]] = None) -> None:
        self._events = list(events or [])
        self._subscribers: List[Any] = []

    def poll_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._events[-limit:])

    def subscribe(self, callback) -> bool:
        if callable(callback) and callback not in self._subscribers:
            self._subscribers.append(callback)
        return True


def _make_memory_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    summary: str = "",
    topic: str = "",
    title: str = "",
    source_event_ids: List[str] = None,
    confidence: float = 0.0,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "memory_id": f"mem_{event_id}",
        "topic": topic,
        "summary": summary,
        "title": title,
        "content": summary or title,
        "importance": 0.5,
        "source_event_ids": list(source_event_ids or []),
    }
    if confidence:
        payload["confidence"] = confidence
    return {
        "event_id": f"evt_mem_{event_id}",
        "event_type": "integration.memory.created",
        "source": "memory",
        "timestamp": timestamp,
        "payload": payload,
    }


def _make_reflection_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    topic: str = "AI 哲学",
    summary: str = "",
    insight: str = "",
    source_event_ids: List[str] = None,
    source_memory_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_refl_{event_id}",
        "event_type": "integration.reflection.completed",
        "source": "reflection",
        "timestamp": timestamp,
        "payload": {
            "reflection_id": f"refl_{event_id}",
            "topic": topic,
            "summary": summary,
            "insight": insight,
            "content": insight or summary,
            "source_event_ids": list(source_event_ids or []),
            "source_memory_ids": list(source_memory_ids or []),
        },
    }


def _make_goal_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    title: str = "建立 AI 哲学对话能力",
    topic: str = "",
    source_event_ids: List[str] = None,
    source_interest_ids: List[str] = None,
    confidence: float = 0.0,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "goal_id": f"goal_{event_id}",
        "title": title,
        "topic": topic or title,
        "status": "active",
        "source_event_ids": list(source_event_ids or []),
        "source_interest_ids": list(source_interest_ids or []),
    }
    if confidence:
        payload["confidence"] = confidence
    return {
        "event_id": f"evt_goal_{event_id}",
        "event_type": "integration.goal.created",
        "source": "goal",
        "timestamp": timestamp,
        "payload": payload,
    }


def _make_emotion_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    label: str = "joy",
    intensity: float = 0.5,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_emo_{event_id}",
        "event_type": "integration.emotion.changed",
        "source": "emotion",
        "timestamp": timestamp,
        "payload": {
            "emotion_id": f"emo_{event_id}",
            "label": label,
            "intensity": intensity,
            "content": f"情感变化:{label}",
        },
    }


def _make_growth_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    trait_name: str = "curiosity",
    delta: float = 0.05,
    source_belief_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_grow_{event_id}",
        "event_type": "integration.growth.recorded",
        "source": "growth",
        "timestamp": timestamp,
        "payload": {
            "growth_id": f"grow_{event_id}",
            "trait_name": trait_name,
            "delta": delta,
            "description": f"{trait_name} 提升 {delta}",
            "source_belief_ids": list(source_belief_ids or []),
        },
    }


def _make_initiative_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    action_type: str = "ask",
    topic: str = "AI 哲学",
    supporting_signal_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_init_{event_id}",
        "event_type": "integration.initiative.created",
        "source": "initiative",
        "timestamp": timestamp,
        "payload": {
            "action_id": f"act_{event_id}",
            "action_type": action_type,
            "topic": topic,
            "supporting_signal_ids": list(supporting_signal_ids or []),
        },
    }


def _make_self_model_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    belief_statement: str = "羽依关心 AI 意识",
    source_reflection_ids: List[str] = None,
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_sm_{event_id}",
        "event_type": "integration.self_model.belief_updated",
        "source": "self_model",
        "timestamp": timestamp,
        "payload": {
            "belief_id": f"belief_{event_id}",
            "statement": belief_statement,
            "domain": "identity",
            "source_reflection_ids": list(source_reflection_ids or []),
        },
    }


def _make_lifecycle_event(
    event_id: str,
    *,
    timestamp: float = 0.0,
    task_id: str = "lifecycle_tick",
) -> Dict[str, Any]:
    return {
        "event_id": f"evt_lc_{event_id}",
        "event_type": "integration.lifecycle.tick_complete",
        "source": "lifecycle",
        "timestamp": timestamp,
        "payload": {
            "task_id": task_id,
            "duration_ms": 50,
        },
    }


def _make_full_life_event_stream(now: float = None) -> List[Dict[str, Any]]:
    """构造一组完整的生命事件流(覆盖 7 大领域)。"""
    now = now if now is not None else time.time()
    return [
        # memory
        _make_memory_event(
            "001",
            timestamp=now - 10000,
            summary="用户问了 AI 意识",
            topic="AI 哲学",
            title="AI 意识对话",
            source_event_ids=[],
        ),
        # reflection
        _make_reflection_event(
            "001",
            timestamp=now - 8000,
            topic="AI 哲学",
            summary="发现用户对 AI 意识感兴趣",
            insight="可能是个新兴兴趣",
            source_event_ids=["evt_mem_001"],
            source_memory_ids=["mem_001"],
        ),
        # goal
        _make_goal_event(
            "001",
            timestamp=now - 6000,
            title="建立 AI 哲学对话能力",
            source_event_ids=["evt_refl_001"],
            source_interest_ids=["sig_001"],
            confidence=0.8,
        ),
        # emotion
        _make_emotion_event(
            "001",
            timestamp=now - 4000,
            label="curiosity",
            intensity=0.6,
        ),
        # growth
        _make_growth_event(
            "001",
            timestamp=now - 3000,
            trait_name="curiosity",
            delta=0.05,
            source_belief_ids=["belief_001"],
        ),
        # initiative
        _make_initiative_event(
            "001",
            timestamp=now - 2000,
            action_type="ask",
            topic="AI 哲学",
            supporting_signal_ids=["sig_001"],
        ),
        # self_model
        _make_self_model_event(
            "001",
            timestamp=now - 1000,
            belief_statement="羽依关心 AI 意识",
            source_reflection_ids=["refl_001"],
        ),
        # lifecycle
        _make_lifecycle_event(
            "001",
            timestamp=now - 500,
        ),
    ]


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_singletons():
    """每个测试前后重置 EventStreamProvider 单例,避免污染。"""
    from src.admin.event_stream_provider import reset_event_stream_provider_for_testing
    reset_event_stream_provider_for_testing()
    yield
    reset_event_stream_provider_for_testing()


# =====================================================================
# Tests
# =====================================================================

class TestBasic:
    """基本行为:list_events 返回 / 事件必填字段。"""

    def test_list_events_returns_items(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(limit=20)
        assert isinstance(d, dict)
        assert "items" in d
        assert "available_types" in d
        assert "total" in d
        assert "fallback" in d
        assert isinstance(d["items"], list)
        assert d["fallback"] is False
        assert d["total"] >= 1
        assert len(d["items"]) == d["total"]
        # 至少 8 个事件
        assert len(d["items"]) >= 8

    def test_event_required_fields(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(limit=20)
        for ev in d["items"]:
            for k in (
                "event_id",
                "event_type",
                "source",
                "timestamp",
                "timestamp_iso",
                "summary",
                "payload",
                "trace",
                "confidence",
                "traceable",
            ):
                assert k in ev, f"event missing field: {k}"
            # trace 子字段
            tr = ev["trace"]
            assert "source_refs" in tr
            assert "evidence_count" in tr
            # payload 必须保留完整
            assert isinstance(ev["payload"], dict)
            # confidence ∈ [0, 1]
            assert 0.0 <= float(ev["confidence"]) <= 1.0
            # traceable 必须是 bool
            assert isinstance(ev["traceable"], bool)


class TestFilter:
    """过滤能力:types / keyword / since_ts。"""

    def test_type_filter(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(types="integration.goal.created", limit=50)
        for ev in d["items"]:
            assert ev["event_type"] == "integration.goal.created"
        # 至少有一个 goal
        assert d["total"] >= 1

    def test_type_filter_multiple(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(
            types="integration.goal.created,integration.reflection.completed",
            limit=50,
        )
        for ev in d["items"]:
            assert ev["event_type"] in (
                "integration.goal.created",
                "integration.reflection.completed",
            )

    def test_type_filter_wildcard(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        # "integration.memory.*" 通配符(必须带 integration. 前缀)
        d = p.list_events(types="integration.memory.*", limit=50)
        for ev in d["items"]:
            assert ev["event_type"].startswith("integration.memory.")

    def test_keyword_filter_event_type(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(keyword="goal", limit=50)
        assert d["total"] >= 1
        for ev in d["items"]:
            et = ev["event_type"].lower()
            sm = str(ev.get("summary", "")).lower()
            assert "goal" in et or "goal" in sm

    def test_keyword_filter_payload_topic(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        d = p.list_events(keyword="AI 哲学", limit=50)
        assert d["total"] >= 1
        for ev in d["items"]:
            # 至少 event_type / summary / payload.topic / title 任一命中
            et = str(ev.get("event_type", "")).lower()
            sm = str(ev.get("summary", "")).lower()
            payload = ev.get("payload") or {}
            topic = str(payload.get("topic", "")).lower()
            title = str(payload.get("title", "")).lower()
            joined = " ".join([et, sm, topic, title])
            assert "ai 哲学" in joined

    def test_since_filter(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        now = time.time()
        events = _make_full_life_event_stream(now=now)
        p = EventStreamProvider(collector=_FakeCollector(events))
        # since = 5 秒前(epoch 浮点)
        since = now - 5000
        d = p.list_events(since_ts=since, limit=50)
        for ev in d["items"]:
            ts = float(ev.get("timestamp_ts") or 0.0)
            # ts 必须 >= since(允许 0.0 跳过)
            assert ts == 0.0 or ts >= since
        # 比全量少
        d_all = p.list_events(limit=200)
        assert d["total"] <= d_all["total"]


class TestLimit:
    """limit 默认 / 上限 / 非法参数 fallback。"""

    def test_limit_default(self, reset_singletons):
        from src.admin.event_stream_provider import (
            DEFAULT_LIMIT,
            EventStreamProvider,
        )

        # 制造 > DEFAULT_LIMIT 个事件
        now = time.time()
        big = [
            _make_memory_event(f"e_{i:04d}", timestamp=now - i, summary=f"event {i}")
            for i in range(DEFAULT_LIMIT + 30)
        ]
        p = EventStreamProvider(collector=_FakeCollector(big))
        d = p.list_events()  # 不传 limit → 用默认
        assert d["fallback"] is False
        assert len(d["items"]) <= DEFAULT_LIMIT

    def test_limit_max_clamp(self, reset_singletons):
        from src.admin.event_stream_provider import (
            EventStreamProvider,
            MAX_LIMIT,
        )

        now = time.time()
        big = [
            _make_memory_event(f"e_{i:04d}", timestamp=now - i, summary=f"event {i}")
            for i in range(MAX_LIMIT + 50)
        ]
        p = EventStreamProvider(collector=_FakeCollector(big))
        # limit > MAX_LIMIT → 触发 fallback
        d = p.list_events(limit=MAX_LIMIT + 100)
        assert d["fallback"] is True
        assert d["fallback_reason"] == "invalid_limit"
        assert d["items"] == []
        # limit == MAX_LIMIT 合法
        d2 = p.list_events(limit=MAX_LIMIT)
        assert d2["fallback"] is False
        assert len(d2["items"]) <= MAX_LIMIT

    def test_invalid_limit_fallback(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        # 多种非法 limit
        # - "abc" / None / [] / {}  → int() 抛错 → fallback
        # - 0 / -1 → 越界 → fallback
        # - 1.5 → int() 成功(返回 1)→ 合法
        invalid_cases = [
            (0, "below_min"),
            (-1, "below_min"),
            (-100, "below_min"),
            ("abc", "not_int"),
            (None, "not_int"),
            ([], "not_int"),
            ({}, "not_int"),
        ]
        for bad, kind in invalid_cases:
            d = p.list_events(limit=bad)
            assert d["fallback"] is True, f"limit={bad!r} 应触发 fallback"
            assert d["fallback_reason"] == "invalid_limit"
            assert d["items"] == []

        # 越界上限也触发 fallback
        d2 = p.list_events(limit=99999)
        assert d2["fallback"] is True
        assert d2["fallback_reason"] == "invalid_limit"

        # 浮点 1.5 → int(1.5)=1 → 合法(允许)
        d3 = p.list_events(limit=1.5)
        assert d3["fallback"] is False

        # 浮点 1.9 → int(1.9)=1 → 合法
        d4 = p.list_events(limit=1.9)
        assert d4["fallback"] is False


class TestSummary:
    """summary 提取规则:payload.summary → title → event_type + timestamp。"""

    def test_summary_from_payload_summary(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        ev = _make_memory_event(
            "001",
            summary="用户问了一个问题",
            title="用户的提问",
        )
        p = EventStreamProvider(collector=_FakeCollector([ev]))
        d = p.list_events(limit=10)
        assert d["items"][0]["summary"] == "用户问了一个问题"

    def test_summary_from_title(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        # 没有 summary,但有 title
        ev = _make_memory_event("001", summary="", title="这是标题")
        p = EventStreamProvider(collector=_FakeCollector([ev]))
        d = p.list_events(limit=10)
        assert d["items"][0]["summary"] == "这是标题"

    def test_summary_fallback_event_type(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        # 都没有 summary / title / content
        ev = {
            "event_id": "evt_x",
            "event_type": "integration.lifecycle.tick_complete",
            "source": "lifecycle",
            "timestamp": 1234567890.0,
            "payload": {"task_id": "tick"},
        }
        p = EventStreamProvider(collector=_FakeCollector([ev]))
        d = p.list_events(limit=10)
        # fallback 到 event_type + timestamp
        s = d["items"][0]["summary"]
        assert "integration.lifecycle.tick_complete" in s
        # 包含时间戳字符串
        assert "1234567890" in s or "@" in s


class TestTrace:
    """trace.source_refs / evidence_count 正确性。"""

    def test_trace_source_refs(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        ev = _make_reflection_event(
            "001",
            source_event_ids=["evt_mem_001", "evt_mem_002"],
            source_memory_ids=["mem_001"],
        )
        p = EventStreamProvider(collector=_FakeCollector([ev]))
        d = p.list_events(limit=10)
        refs = d["items"][0]["trace"]["source_refs"]
        # 三个 id 都应被提取
        assert "evt_mem_001" in refs
        assert "evt_mem_002" in refs
        assert "mem_001" in refs
        # 去重
        assert len(refs) == 3

    def test_evidence_count(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        ev = _make_goal_event(
            "001",
            source_event_ids=["evt_refl_001", "evt_refl_002", "evt_refl_003"],
            source_interest_ids=["sig_001"],
        )
        p = EventStreamProvider(collector=_FakeCollector([ev]))
        d = p.list_events(limit=10)
        ev_count = d["items"][0]["trace"]["evidence_count"]
        # 应取 source_event_ids 的数量(最大者)
        assert ev_count == 3
        # 至少有一个 source_ref → traceable=True
        assert d["items"][0]["traceable"] is True


class TestFallback:
    """降级路径:EventHub 不可用 / 非法事件形状。"""

    def test_eventhub_unavailable(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        # collector=None 且 event_hub=None → 都没有 → 返回 fallback
        p = EventStreamProvider(collector=None, event_hub=None)
        d = p.list_events(limit=20)
        # 内部尝试 get_collector / get_event_hub 都失败 → no_events fallback
        assert d["fallback"] is True
        # 实际原因取决于实现:可能 invalid_limit/no_events,但 items 必须是 []
        assert d["items"] == []

    def test_eventhub_raises(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        class _BoomCollector:
            def list_events(self, limit: int = 200):
                raise RuntimeError("collector exploded")

        p = EventStreamProvider(collector=_BoomCollector())
        d = p.list_events(limit=20)
        # 内部已捕获异常,fallback
        assert d["fallback"] is True
        assert d["items"] == []

    def test_invalid_event_shape(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        class _BadEventsCollector:
            def list_events(self, limit: int = 200):
                # 多种非法形状混杂
                return [
                    None,
                    "not a dict",
                    42,
                    [],  # 没 event_id
                    {"event_id": "good_1", "event_type": "integration.lifecycle.tick_complete", "timestamp": 1.0, "payload": {}},
                    {"event_id": "good_2", "event_type": "integration.goal.created", "timestamp": 2.0, "payload": {}},
                ]

        p = EventStreamProvider(collector=_BadEventsCollector())
        d = p.list_events(limit=20)
        # 至少 good_1 / good_2 应被保留
        ids = {ev["event_id"] for ev in d["items"]}
        assert "good_1" in ids
        assert "good_2" in ids
        # 全部事件都有必填字段(已被 _normalize 校验)
        for ev in d["items"]:
            assert ev["event_id"]
            assert ev["event_type"]


class TestIsolation:
    """隔离性:不 import 业务模块 / 不调用 LLM。"""

    def test_no_business_module_import(self, reset_singletons):
        """Provider 源码禁止 import 业务模块。"""
        from src.admin import event_stream_provider as mod
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

        # Router 同样不能 import 业务模块
        from src.admin.dashboard import event_stream_router as router_mod
        rsrc = open(router_mod.__file__, "r", encoding="utf-8").read()
        for f in forbidden:
            assert f not in rsrc, f"Router 禁止: {f}"

    def test_no_llm_call(self, reset_singletons):
        """Provider 不得调用 LLM(检查源码中不能出现 LLM 调用痕迹)。"""
        from src.admin import event_stream_provider as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 去掉 docstring / 注释 后再检查
        lines = []
        in_docstring = False
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith("#"):
                continue
            lines.append(line)
        code = "\n".join(lines).lower()
        # 禁止 LLM 调用痕迹(代码层面)
        forbidden_patterns = [
            "openai",
            "anthropic",
            "claude",
            "gpt-3",
            "gpt-4",
            "chat_completion",
            "completion(",
            "from src.llm",
            "import src.llm",
            "self.llm",
            ".invoke_llm",
            "call_llm",
        ]
        for f in forbidden_patterns:
            assert f.lower() not in code, f"禁止 LLM 关键字: {f}"

        # 顶层导出也不应包含 LLM 相关符号
        assert "LLM" not in mod.__all__
        assert "llm" not in [x.lower() for x in mod.__all__]

    def test_no_write_methods(self, reset_singletons):
        """Provider 自身不能有"业务写"方法(内部 cache 维护除外)。"""
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        # 业务写方法必须是禁止;但内部 cache 维护(_update_cache) 是允许的
        forbidden_business_writes = [
            "_save", "_write_to", "_append", "_store", "_persist",
            "_delete", "_remove", "_modify",
            "create_event", "emit_event", "publish_event", "send_event", "trigger_event",
            "save_event", "store_event",
        ]
        for name in dir(p):
            for f in forbidden_business_writes:
                if name.startswith(f) and callable(getattr(p, name, None)):
                    pytest.fail(f"unexpected business write method: {name}")


class TestWS:
    """WebSocket 订阅 / 推送(无 WS 连接时的纯 in-process 订阅)。"""

    def test_ws_subscribe(self, reset_singletons):
        from src.admin.event_stream_provider import EventStreamProvider

        p = EventStreamProvider(collector=_FakeCollector())
        received: List[List[Dict[str, Any]]] = []

        def cb(events):
            received.append(list(events))

        assert p.subscribe(cb) is True
        # 二次订阅同 callback → True(幂等)
        assert p.subscribe(cb) is True
        # 推入一个事件
        ev = _make_memory_event("001", summary="ws push test")
        assert p.push_event(ev) is True
        # 取消订阅
        assert p.unsubscribe(cb) is True
        # 再次推入 → 收不到
        ev2 = _make_memory_event("002", summary="after unsubscribe")
        p.push_event(ev2)
        # 验证收到的批次数
        assert len(received) >= 1
        # 第一批必须包含 ev001
        first_batch = received[0]
        ids = {e.get("event_id") for e in first_batch}
        assert "evt_mem_001" in ids

    def test_polling_fallback(self, reset_singletons):
        """前端 polling 时,provider 通过 list_events + get_recent_cache 提供数据。"""
        from src.admin.event_stream_provider import EventStreamProvider

        events = _make_full_life_event_stream()
        p = EventStreamProvider(collector=_FakeCollector(events))
        # 第一次 list → 触发 cache 写入
        d = p.list_events(limit=10)
        assert d["fallback"] is False
        # 缓存里有数据
        cache = p.get_recent_cache(limit=10)
        assert len(cache) >= 1
        # 缓存中的事件已经被 enrich(有 trace / confidence)
        for ev in cache:
            assert "trace" in ev
            assert "confidence" in ev


class TestAPI:
    """Router API 端点测试。"""

    def _make_client(self):
        from flask import Flask
        from src.admin.dashboard.event_stream_router import event_stream_v2_bp
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(event_stream_v2_bp)
        return app.test_client()

    def test_event_stream_endpoint(self, reset_singletons):
        from src.admin import event_stream_provider as pmod
        events = _make_full_life_event_stream()
        pmod._stream_instance = pmod.EventStreamProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/event-stream?limit=20")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "data" in body
        data = body["data"]
        assert "items" in data
        assert "available_types" in data
        assert "total" in data
        # envelope 必备
        for k in ("ok", "data", "trace", "confidence", "fallback", "timestamp"):
            assert k in body, f"envelope missing: {k}"
        # data 内禁止 _meta / source / confidence
        for forbidden in ("_meta", "source", "confidence"):
            assert forbidden not in data, f"data 禁止: {forbidden}"

    def test_event_stream_endpoint_with_filters(self, reset_singletons):
        from src.admin import event_stream_provider as pmod
        events = _make_full_life_event_stream()
        pmod._stream_instance = pmod.EventStreamProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get(
            "/api/dashboard/v2/event-stream?types=integration.goal.created&keyword=哲学&limit=10"
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        for ev in data["items"]:
            assert ev["event_type"] == "integration.goal.created"

    def test_event_stream_invalid_limit_fallback(self, reset_singletons):
        from src.admin import event_stream_provider as pmod
        events = _make_full_life_event_stream()
        pmod._stream_instance = pmod.EventStreamProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/event-stream?limit=99999")
        assert resp.status_code == 200
        body = resp.get_json()
        # provider → fallback=true,envelope 也应是 fallback
        assert body["fallback"] is True
        assert body["fallback_reason"] == "invalid_limit"
        assert body["data"]["items"] == []

    def test_types_endpoint(self, reset_singletons):
        from src.admin import event_stream_provider as pmod
        events = _make_full_life_event_stream()
        pmod._stream_instance = pmod.EventStreamProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/event-stream/types")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "types" in body["data"]
        assert "count" in body["data"]
        # 类型列表非空
        assert body["data"]["count"] >= 1
        assert isinstance(body["data"]["types"], list)

    def test_health_endpoint(self, reset_singletons):
        from src.admin import event_stream_provider as pmod
        events = _make_full_life_event_stream()
        pmod._stream_instance = pmod.EventStreamProvider(
            collector=_FakeCollector(events)
        )
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/event-stream/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "data" in body
        data = body["data"]
        for k in (
            "status",
            "ok",
            "collector_available",
            "cache_size",
            "subscribers",
            "supported_prefixes",
        ):
            assert k in data, f"health missing: {k}"
        # supported_prefixes 至少包含 7 大领域
        prefixes = data["supported_prefixes"]
        assert any("memory" in p for p in prefixes)
        assert any("goal" in p for p in prefixes)
        assert any("reflection" in p for p in prefixes)

    def test_ws_info_endpoint(self, reset_singletons):
        c = self._make_client()
        resp = c.get("/api/dashboard/v2/event-stream/ws-info")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert data["channel"] == "dashboard.event_stream"
        assert data["fallback_polling_seconds"] == 5
