# -*- coding: utf-8 -*-
"""
tests/test_trace_envelope.py

Phase 5.0 Dashboard Upgrade Step 8.4.5 —— Trace Envelope & Data Confidence 测试。

覆盖:
  TestEnvelope           —— 响应外壳基础结构
  TestConfidence         —— 置信度计算
  TestSources            —— 数据来源追踪
  TestEvidence           —— 证据数量
  TestFallback           —— fallback 原因保留 / 异常响应
  TestProviderIntegration —— Provider 端到端 trace 验证
  TestIsolation          —— 不 import 业务模块 / 不调用 LLM

合计:25 个测试(>= 20 要求)。
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
# Helpers
# =====================================================================

class _FakeProvider:
    """模拟一个简单的 Provider,只暴露 get_state_summary。"""

    def __init__(self, payload: Dict[str, Any] = None, raise_exc: Exception = None) -> None:
        self._payload = payload or {"hello": "world"}
        self._raise = raise_exc

    def get_state_summary(self) -> Dict[str, Any]:
        if self._raise is not None:
            raise self._raise
        return self._payload


# =====================================================================
# Tests:TestEnvelope
# =====================================================================

class TestEnvelope:
    """响应外壳基础结构。"""

    def test_response_has_trace(self):
        """响应必须包含 trace 字段。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(data={"foo": "bar"})
        assert "trace" in env
        assert "sources" in env["trace"]
        assert "evidence_count" in env["trace"]
        assert "generated_at" in env["trace"]

    def test_data_has_no_trace_fields(self):
        """data 字段纯净:禁止出现 trace / confidence / source / _meta。"""
        from src.admin.dashboard.response import build_dashboard_response
        data_in = {"foo": "bar", "items": [1, 2, 3]}
        env = build_dashboard_response(data=data_in, host_provider="X", host_method="y")
        for forbidden in ("trace", "confidence", "source", "_meta", "fallback", "fallback_reason", "timestamp", "ok"):
            assert forbidden not in env["data"], f"data 禁止: {forbidden}"
        # data 必须保持原状
        assert env["data"]["foo"] == "bar"
        assert env["data"]["items"] == [1, 2, 3]

    def test_timestamp_exists(self):
        """响应必须包含 timestamp 字段(ISO 8601)。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(data={})
        assert "timestamp" in env
        # 粗略检查 ISO 格式
        ts = env["timestamp"]
        assert isinstance(ts, str)
        assert "T" in ts  # ISO 8601 必须有 T 分隔符
        assert ts.endswith("Z") or "+" in ts  # UTC 时区标识

    def test_ok_field_exists(self):
        """响应必须包含 ok 字段。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(data={})
        assert "ok" in env
        assert isinstance(env["ok"], bool)
        # 正常数据 → ok=True
        assert env["ok"] is True
        # fallback → ok=False
        env2 = build_dashboard_response(data={}, fallback=True, fallback_reason="x")
        assert env2["ok"] is False


# =====================================================================
# Tests:TestConfidence
# =====================================================================

class TestConfidence:
    """置信度计算规则。"""

    def test_confidence_range(self):
        """confidence 必须在 [0, 1] 之间。"""
        from src.admin.dashboard.trace import calculate_confidence
        # 各种边界
        cases = [
            (True, 0, False, False),
            (True, 100, False, False),
            (False, 0, True, True),
            (True, 0, True, False),
            (True, 5, False, True),
        ]
        for has_src, ev, fb, exc in cases:
            c = calculate_confidence(
                has_sources=has_src, evidence_count=ev, is_fallback=fb, is_exception=exc
            )
            assert 0.0 <= c <= 1.0, f"out of range: {c}"

    def test_full_data_confidence_high(self):
        """完整数据 → confidence 高(接近 1.0)。"""
        from src.admin.dashboard.trace import calculate_confidence
        c = calculate_confidence(
            has_sources=True, evidence_count=5, is_fallback=False, is_exception=False
        )
        assert c >= 0.7
        assert c <= 1.0

    def test_fallback_confidence_low(self):
        """fallback → confidence < 0.5。"""
        from src.admin.dashboard.trace import calculate_confidence
        # has_sources=True(有 host source), evidence=0, fallback=True
        # 1.0 - 0.2 (no evidence) - 0.5 (fallback) = 0.3 < 0.5
        c = calculate_confidence(
            has_sources=True, evidence_count=0, is_fallback=True, is_exception=False
        )
        assert c < 0.5
        # 严重 fallback:全部缺
        c2 = calculate_confidence(
            has_sources=False, evidence_count=0, is_fallback=True, is_exception=True
        )
        assert c2 < 0.5

    def test_missing_source_reduces_confidence(self):
        """来源缺失应降低 confidence(扣 0.3)。"""
        from src.admin.dashboard.trace import calculate_confidence
        with_src = calculate_confidence(has_sources=True, evidence_count=3)
        without_src = calculate_confidence(has_sources=False, evidence_count=3)
        # 差值应为 0.3
        assert abs((with_src - without_src) - 0.3) < 1e-6

    def test_zero_evidence_reduces_confidence(self):
        """证据=0 应降低 confidence(扣 0.2)。"""
        from src.admin.dashboard.trace import calculate_confidence
        with_ev = calculate_confidence(has_sources=True, evidence_count=3)
        without_ev = calculate_confidence(has_sources=True, evidence_count=0)
        # 差值应为 0.2
        assert abs((with_ev - without_ev) - 0.2) < 1e-6

    def test_exception_heavily_reduces(self):
        """Provider 异常 → confidence 大幅下降(扣 0.6)。"""
        from src.admin.dashboard.trace import calculate_confidence
        normal = calculate_confidence(has_sources=True, evidence_count=3)
        with_exc = calculate_confidence(has_sources=True, evidence_count=3, is_exception=True)
        # 差值约 0.6
        assert abs((normal - with_exc) - 0.6) < 1e-6

    def test_confidence_clipped_to_0(self):
        """多重扣减 → confidence 不低于 0。"""
        from src.admin.dashboard.trace import calculate_confidence
        c = calculate_confidence(
            has_sources=False, evidence_count=0, is_fallback=True, is_exception=True
        )
        assert c == 0.0


# =====================================================================
# Tests:TestSources
# =====================================================================

class TestSources:
    """数据来源追踪。"""

    def test_source_provider_recorded(self):
        """每个 source 必须有 provider 字段。"""
        from src.admin.dashboard.trace import TraceContext
        ctx = TraceContext(provider_name="")
        ctx.add_source("MyProvider", "get_thing", ok=True)
        assert len(ctx.sources) == 1
        assert ctx.sources[0]["provider"] == "MyProvider"

    def test_source_method_recorded(self):
        """每个 source 必须有 method 字段。"""
        from src.admin.dashboard.trace import TraceContext
        ctx = TraceContext(provider_name="")
        ctx.add_source("MyProvider", "do_work", ok=True, duration_ms=10)
        src = ctx.sources[0]
        assert src["method"] == "do_work"
        assert src["duration_ms"] == 10
        assert src["ok"] is True

    def test_multiple_sources_supported(self):
        """支持多 source 记录。"""
        from src.admin.dashboard.trace import TraceContext
        ctx = TraceContext(provider_name="")
        ctx.add_source("ProviderA", "method_a", ok=True)
        ctx.add_source("ProviderB", "method_b", ok=False, error="TimeoutError")
        ctx.add_source("ProviderC", "method_c", ok=True, duration_ms=5)
        assert len(ctx.sources) == 3
        # 错误 source 应被记录
        assert ctx.sources[1]["ok"] is False
        assert ctx.sources[1]["error"] == "TimeoutError"
        # 异常被 mark
        assert ctx.exception_seen is True

    def test_source_appears_in_envelope(self):
        """sources 在最终 envelope 的 trace.sources 中。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(
            data={"foo": "bar"},
            sources=[
                {"provider": "A", "method": "ma", "ok": True},
                {"provider": "B", "method": "mb", "ok": True, "duration_ms": 8},
            ],
            evidence_count=2,
            host_provider="Host",
            host_method="hm",
        )
        # host + 2 sources = 3
        assert len(env["trace"]["sources"]) == 3
        providers = {s["provider"] for s in env["trace"]["sources"]}
        assert "A" in providers
        assert "B" in providers
        assert "Host" in providers


# =====================================================================
# Tests:TestEvidence
# =====================================================================

class TestEvidence:
    """证据数量。"""

    def test_evidence_count_exists(self):
        """envelope.trace.evidence_count 必须存在。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(data={}, evidence_count=5)
        assert env["trace"]["evidence_count"] == 5

    def test_zero_evidence_handled(self):
        """evidence_count=0 是合法状态。"""
        from src.admin.dashboard.response import build_dashboard_response
        # 必须传 host_provider,否则 has_sources=False 也会扣分
        env = build_dashboard_response(data={}, evidence_count=0, host_provider="X", host_method="y")
        assert env["trace"]["evidence_count"] == 0
        # confidence = 1.0 - 0.2 (no evidence) = 0.8
        assert env["confidence"] == pytest.approx(0.8, abs=0.01)

    def test_evidence_increment(self):
        """add_evidence 支持累加。"""
        from src.admin.dashboard.trace import TraceContext
        ctx = TraceContext(provider_name="")
        ctx.add_evidence(3)
        ctx.add_evidence(2)
        assert ctx.evidence_count == 5
        # 负数 / 0 忽略
        ctx.add_evidence(-1)
        ctx.add_evidence(0)
        assert ctx.evidence_count == 5


# =====================================================================
# Tests:TestFallback
# =====================================================================

class TestFallback:
    """fallback / 异常路径。"""

    def test_fallback_reason_preserved(self):
        """fallback_reason 必须在 envelope 中保留。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(
            data={},
            fallback=True,
            fallback_reason="event_hub_unavailable",
        )
        assert env["fallback"] is True
        assert env["fallback_reason"] == "event_hub_unavailable"
        assert env["ok"] is False

    def test_exception_response_trace(self):
        """Provider 异常的 envelope 必须有 trace 标记。"""
        from src.admin.dashboard.response import build_exception_response
        env = build_exception_response(
            error_name="ValueError",
            host_provider="X",
            host_method="y",
        )
        assert env["ok"] is False
        assert env["fallback"] is True
        assert "ValueError" in (env["fallback_reason"] or "")
        # trace 必须有 source(host)
        assert len(env["trace"]["sources"]) >= 1
        # host source 的 ok=False(因为 exception=True)
        host_src = env["trace"]["sources"][0]
        assert host_src["provider"] == "X"
        assert host_src["ok"] is False

    def test_fallback_response_default_data(self):
        """build_fallback_response 默认 data 是空 dict。"""
        from src.admin.dashboard.response import build_fallback_response
        env = build_fallback_response(reason="test_reason")
        assert env["data"] == {}
        assert env["fallback"] is True
        assert env["fallback_reason"] == "test_reason"


# =====================================================================
# Tests:TestProviderIntegration
# =====================================================================

class TestProviderIntegration:
    """与真实 Provider 集成。"""

    def test_life_state_trace(self):
        """LifeStateProvider.get_state_summary 返回 envelope 包含 trace。"""
        from src.admin.life_state_provider import LifeStateProvider

        class _StubSelf:
            def __init__(self):
                self._initiative = None
                self._selfmodel = None
                self._goal = None
                self._memory = None
                self._runtime = None
                self._graph = None
                self._event_hub = None

        p = LifeStateProvider()
        p._initiative = None
        p._selfmodel = None
        p._goal = None
        p._memory = None
        p._runtime = None
        p._graph = None
        p._event_hub = None
        env = p.get_state_summary()
        # 验证 envelope 结构
        for k in ("data", "trace", "confidence", "fallback", "timestamp"):
            assert k in env, f"envelope 缺字段: {k}"
        # trace.sources 至少有 1 条
        assert len(env["trace"]["sources"]) >= 1
        # 全部 Provider 都不可用 → fallback
        assert env["fallback"] is True
        # data 是 dict
        assert isinstance(env["data"], dict)

    def test_event_stream_trace(self):
        """EventStreamProvider.list_events 返回 envelope 包含 trace。"""
        from src.admin.event_stream_provider import EventStreamProvider

        class _FakeCollector:
            def list_events(self, limit: int = 200):
                return [
                    {
                        "event_id": "e1",
                        "event_type": "integration.goal.created",
                        "timestamp": time.time(),
                        "source": "goal",
                        "payload": {"goal_id": "g1", "title": "T"},
                    },
                    {
                        "event_id": "e2",
                        "event_type": "integration.reflection.completed",
                        "timestamp": time.time() - 100,
                        "source": "reflection",
                        "payload": {"reflection_id": "r1", "summary": "S"},
                    },
                ]

        p = EventStreamProvider(collector=_FakeCollector())
        # list_events 返回的是 (items, available_types, total, ...) 的 dict
        d = p.list_events(limit=10)
        # EventStreamProvider 不在每个 item 中放 trace.sources,而是顶层 list
        # 验证 items 包含 trace / confidence
        for ev in d["items"]:
            assert "trace" in ev
            assert "source_refs" in ev["trace"]
            assert "evidence_count" in ev["trace"]
            assert "confidence" in ev
            assert "traceable" in ev
        # available_types 是 list
        assert isinstance(d.get("available_types"), list)
        assert d["total"] == 2

    def test_life_graph_trace(self):
        """LifeGraphProvider.build_graph 返回 dict 包含 trace 概念(通过 build_envelope)。"""
        from src.admin.life_graph_provider import LifeGraphProvider

        class _FakeCollector:
            def list_event_nodes(self, limit: int = 500):
                return []
            def list_memory_nodes(self, limit: int = 50):
                return []
            def list_belief_nodes(self, limit: int = 50):
                return []
            def list_trait_change_nodes(self, limit: int = 50):
                return []

        p = LifeGraphProvider(collector=_FakeCollector())
        d = p.build_graph()
        # 验证基础字段
        for k in ("available", "fallback", "nodes", "edges", "node_count", "edge_count"):
            assert k in d, f"graph 缺字段: {k}"
        # 空数据时 available=True, fallback=False
        assert d["available"] is True

    def test_event_stream_health_envelope(self):
        """EventStream health 端点返回标准 envelope。"""
        from src.admin import event_stream_provider as pmod
        pmod._stream_instance = pmod.EventStreamProvider(collector=None, event_hub=None)
        from flask import Flask
        from src.admin.dashboard.event_stream_router import event_stream_v2_bp
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(event_stream_v2_bp)
        c = app.test_client()
        resp = c.get("/api/dashboard/v2/event-stream/health")
        assert resp.status_code == 200
        body = resp.get_json()
        # 标准 envelope 字段
        for k in ("ok", "data", "timestamp"):
            assert k in body, f"health 缺: {k}"


# =====================================================================
# Tests:TestIsolation
# =====================================================================

class TestIsolation:
    """隔离性:不 import 业务模块 / 不调用 LLM。"""

    def test_no_business_import(self):
        """trace.py / response.py 源码禁止 import 业务模块。"""
        from src.admin.dashboard import trace as trace_mod
        from src.admin.dashboard import response as resp_mod

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
        for mod in (trace_mod, resp_mod):
            src = open(mod.__file__, "r", encoding="utf-8").read()
            for f in forbidden:
                assert f not in src, f"{mod.__name__} 禁止: {f}"

    def test_no_llm_call(self):
        """trace.py / response.py 不得调用 LLM。"""
        from src.admin.dashboard import trace as trace_mod
        from src.admin.dashboard import response as resp_mod

        def _strip_docstring_and_comments(src: str) -> str:
            lines = []
            in_doc = False
            for line in src.splitlines():
                s = line.strip()
                if s.startswith('"""') or s.startswith("'''"):
                    in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                if s.startswith("#"):
                    continue
                lines.append(line)
            return "\n".join(lines).lower()

        for mod in (trace_mod, resp_mod):
            src = open(mod.__file__, "r", encoding="utf-8").read()
            code = _strip_docstring_and_comments(src)
            forbidden = [
                "openai", "anthropic", "claude",
                "chat_completion", "completion(",
                "from src.llm", "import src.llm",
                ".invoke_llm", "call_llm",
            ]
            for f in forbidden:
                assert f not in code, f"{mod.__name__} 禁止 LLM 关键字: {f}"

    def test_data_field_immutable(self):
        """build_dashboard_response 不修改输入 data。"""
        from src.admin.dashboard.response import build_dashboard_response
        original = {"foo": "bar", "items": [1, 2, 3]}
        snapshot = {"foo": "bar", "items": [1, 2, 3]}
        env = build_dashboard_response(
            data=original,
            sources=[{"provider": "A", "method": "ma"}],
            evidence_count=1,
            host_provider="Host",
            host_method="hm",
        )
        # data 必须原样
        assert env["data"] == snapshot
        # 原始 dict 不被改
        assert original == snapshot


# =====================================================================
# Tests:TestQuickEnvelope
# =====================================================================

class TestQuickEnvelope:
    """quick_envelope 便捷函数。"""

    def test_quick_envelope_minimal(self):
        from src.admin.dashboard.trace import quick_envelope
        env = quick_envelope(data={"x": 1}, sources=[{"provider": "X", "method": "y"}])
        assert env["ok"] is True
        assert env["data"] == {"x": 1}
        assert env["fallback"] is False
        # has_sources=True(1 source), evidence_count=0(未传) → 1.0 - 0.2 = 0.8
        assert env["confidence"] == pytest.approx(0.8, abs=0.01)

    def test_quick_envelope_with_sources(self):
        from src.admin.dashboard.trace import quick_envelope
        env = quick_envelope(
            data={"x": 1},
            sources=[
                {"provider": "A", "method": "ma", "ok": True},
                {"provider": "B", "method": "mb", "ok": True},
            ],
            evidence_count=2,
        )
        assert len(env["trace"]["sources"]) == 2
        assert env["trace"]["evidence_count"] == 2
        # confidence = 1.0(no penalty)
        assert env["confidence"] == pytest.approx(1.0, abs=0.01)

    def test_quick_envelope_fallback(self):
        from src.admin.dashboard.trace import quick_envelope
        env = quick_envelope(
            data={},
            fallback=True,
            fallback_reason="test",
        )
        assert env["fallback"] is True
        assert env["ok"] is False
        assert env["fallback_reason"] == "test"
        # confidence < 0.5
        assert env["confidence"] < 0.5
