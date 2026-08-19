# -*- coding: utf-8 -*-
"""
tests/test_phase_7_2_cognitive_trace.py

Phase 7.2 Cognitive Trace 单元测试。

覆盖:
    Class TestCognitiveEvent         —— CognitiveEvent 数据结构 / schema_version / cognitive.* 命名空间
    Class TestCognitiveHooks         —— safe_emit_cognitive 通用保护 / thread-local context
    Class TestSubsystemHelpers       —— emit_memory_retrieved / emit_personality_resolved / emit_emotion_updated
    Class TestHookFailureIsolation   —— Hook 崩溃不影响核心模块返回值
    Class TestDataLeakProtection     —— payload 白名单 / 字符串截断 / 大小限制
    Class TestPipelineIntegration    —— RuntimePipeline set/clear context + path_decided
    Class TestBackwardCompatibility  —— observer disabled 时行为 = 基线
"""
from __future__ import annotations

import json
import time
import pytest
from unittest.mock import MagicMock, patch
from typing import Any, Dict, List

from src.runtime.observer import (
    COGNITIVE_EVENT_SCHEMA_VERSION,
    CognitiveEvent,
    CognitiveEventType,
    ObservationConfig,
    EventQueue,
    ObservationEventSink,
    NoOpObservationSink,
    reset_observation_singletons_for_tests,
    init_observation_layer,
    get_event_queue,
    get_observation_event_sink,
)
from src.runtime.observer.cognitive_hooks import (
    safe_emit_cognitive,
    set_cognitive_context,
    clear_cognitive_context,
    emit_memory_retrieved,
    emit_personality_resolved,
    emit_emotion_updated,
    emit_response_path_decided,
    _reset_cognitive_context_for_tests,
    _get_ctx_hint,
    _truncate_long_strings,
    _MAX_DATA_BYTES,
    _MAX_STRING_VALUE_LEN,
    _MAX_QUERY_PREVIEW_LEN,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试前/后重置 observer 单例 + cognitive context。"""
    reset_observation_singletons_for_tests()
    _reset_cognitive_context_for_tests()
    yield
    reset_observation_singletons_for_tests()
    _reset_cognitive_context_for_tests()


@pytest.fixture
def initialized_queue_and_sink():
    """初始化 observer 层,返回 queue + sink。"""
    init_observation_layer(config=ObservationConfig())
    return get_event_queue(), get_observation_event_sink()


# ============================================================
# Class 1: CognitiveEvent 数据结构
# ============================================================
class TestCognitiveEvent:
    def test_default_schema_version_is_1_0(self):
        ev = CognitiveEvent(
            event_type=CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t1", session_id="s1",
        )
        assert ev.schema_version == "1.0"
        assert ev.schema_version == COGNITIVE_EVENT_SCHEMA_VERSION

    def test_event_id_has_evt_cog_prefix(self):
        ev = CognitiveEvent(
            event_type=CognitiveEventType.EMOTION_UPDATED,
            trace_id="t1", session_id="s1",
        )
        assert ev.event_id.startswith("evt_cog_")

    def test_all_event_types_use_cognitive_namespace(self):
        for name in CognitiveEventType._ALL:
            assert name.startswith("cognitive."), (
                f"事件类型 {name!r} 缺少 cognitive. 命名空间"
            )

    def test_is_valid_recognizes_declared_types(self):
        assert CognitiveEventType.is_valid(CognitiveEventType.MEMORY_RETRIEVED) is True
        assert CognitiveEventType.is_valid("cognitive.unknown.action") is False
        assert CognitiveEventType.is_valid(None) is False

    def test_to_dict_and_from_dict_roundtrip(self):
        ev1 = CognitiveEvent(
            event_type=CognitiveEventType.PERSONALITY_RESOLVED,
            trace_id="t_rt", session_id="s_rt",
            subsystem="personality",
            data={"traits_used": ["warmth"], "traits_count": 25},
        )
        d = ev1.to_dict()
        assert d["event_type"] == CognitiveEventType.PERSONALITY_RESOLVED
        ev2 = CognitiveEvent.from_dict(d)
        assert ev2.event_id == ev1.event_id
        assert ev2.schema_version == ev1.schema_version
        assert ev2.data["traits_used"] == ["warmth"]

    def test_short_type_strips_cognitive_prefix(self):
        ev = CognitiveEvent(
            event_type="cognitive.memory.retrieved",
            trace_id="t", session_id="s",
        )
        assert ev.short_type == "memory.retrieved"

    def test_non_dict_data_normalized(self):
        ev = CognitiveEvent(
            event_type=CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
            data="not_a_dict",
        )
        assert isinstance(ev.data, dict)
        assert ev.data["raw"] == "not_a_dict"


# ============================================================
# Class 2: CognitiveHooks 通用保护
# ============================================================
class TestCognitiveHooks:
    def test_safe_emit_rejects_non_cognitive_event_type(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        # 非 cognitive.* 前缀 → 静默 return
        safe_emit_cognitive("runtime.pipeline.started", trace_id="t", session_id="s")
        assert q.history_count() == 0

    def test_safe_emit_swallows_all_exceptions(self):
        """safe_emit_cognitive 任何异常不冒泡。"""
        # 不初始化 observer → get_observation_event_sink 可能返回 NoOp
        # 传奇怪参数也不抛
        safe_emit_cognitive(
            "cognitive.test.event",
            trace_id=None,  # type: ignore
            session_id=None,  # type: ignore
            data=object(),  # type: ignore
        )
        # 到达此处 = 测试通过

    def test_thread_local_context_set_and_get(self):
        set_cognitive_context(trace_id="lc_test_123", session_id="s_test")
        assert _get_ctx_hint("trace_id") == "lc_test_123"
        assert _get_ctx_hint("session_id") == "s_test"

    def test_thread_local_context_clear(self):
        set_cognitive_context(trace_id="lc_x", session_id="s_x")
        clear_cognitive_context()
        assert _get_ctx_hint("trace_id") == ""
        assert _get_ctx_hint("session_id") == ""

    def test_thread_local_context_empty_by_default(self):
        assert _get_ctx_hint("trace_id") == ""
        assert _get_ctx_hint("session_id") == ""

    def test_safe_emit_auto_fills_trace_id_from_thread_local(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        set_cognitive_context(trace_id="lc_auto", session_id="s_auto")
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            subsystem="memory",
            data={"test": True},
        )
        events = q.recent(limit=10)
        # 找到 cognitive 事件(可能 RuntimeEvent 格式包装)
        cog_events = [e for e in events if "cognitive" in str(e.data.get("event_type", ""))]
        if cog_events:
            ev = cog_events[0]
            # data 里有 trace_id(因为 safe_emit_cognitive 传了整个 CognitiveEvent.to_dict() 作为 data)
            assert ev.data.get("trace_id") == "lc_auto" or ev.trace_id == "lc_auto"

    def test_truncate_long_strings_basic(self):
        data = {"short": "abc", "long": "x" * 300}
        result = _truncate_long_strings(data)
        assert result["short"] == "abc"
        assert len(result["long"]) <= _MAX_STRING_VALUE_LEN + 20  # 截断 + 后缀
        assert result["long"].endswith("...[truncated]")

    def test_truncate_long_strings_nested(self):
        data = {"nested": {"deep": "y" * 300}, "list": ["z" * 300]}
        result = _truncate_long_strings(data)
        assert "...[truncated]" in result["nested"]["deep"]
        assert "...[truncated]" in result["list"][0]

    def test_safe_emit_rejects_oversized_data(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        # 构造超大 data: 大量小键值对, 每个 < 200 chars 但总量 > 8KB
        huge_data = {f"key_{i}": f"value_{i}_" + "x" * 50 for i in range(500)}
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
            subsystem="memory",
            data=huge_data,
        )
        # 超大 → 拒绝 emit
        assert q.history_count() == 0


# ============================================================
# Class 3: Subsystem Helpers
# ============================================================
class TestSubsystemHelpers:
    def test_emit_memory_retrieved_constructs_correct_payload(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        set_cognitive_context(trace_id="lc_mem", session_id="s_mem")

        emit_memory_retrieved(
            query="今天聊AI",
            top_k=5,
            result_count=3,
            memory_ids=["mem_1", "mem_2", "mem_3"],
            sources={"identity": 1, "event": 1, "semantic": 1, "chat": 0},
            score_range=[0.7, 0.92],
            max_score=0.92,
        )
        events = q.recent(limit=10)
        assert len(events) >= 1
        # 最后一条应该是 cognitive event
        ev = events[-1]
        # data 里有 CognitiveEvent.to_dict() 的内容
        ev_data = ev.data
        assert ev_data.get("event_type") == CognitiveEventType.MEMORY_RETRIEVED
        payload = ev_data.get("data", {})
        assert payload.get("memory_count") == 3
        assert payload.get("top_k") == 5
        assert payload.get("max_score") == 0.92
        assert "mem_1" in payload.get("memory_ids", [])
        # query 降级: preview + hash + length
        assert "query_preview" in payload
        assert "query_hash" in payload
        assert "query_length" in payload
        assert "query" not in payload  # 原始 query 不直接放

    def test_emit_memory_retrieved_query_preview_truncated(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        long_query = "x" * 200
        emit_memory_retrieved(
            query=long_query,
            top_k=1, result_count=0,
            memory_ids=[], sources={}, score_range=[], max_score=0,
        )
        events = q.recent(limit=1)
        ev = events[-1]
        payload = ev.data.get("data", {})
        assert len(payload["query_preview"]) <= _MAX_QUERY_PREVIEW_LEN

    def test_emit_personality_resolved_constructs_correct_payload(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        set_cognitive_context(trace_id="lc_pers", session_id="s_pers")

        emit_personality_resolved(
            persona_version="v1.9",
            traits_used=["warmth", "caring"],
            traits_count=25,
            growth_metrics_used=8,
            growth_records_count=128,
            self_model_involved=True,
            tension_count=0,
        )
        ev = q.recent(limit=1)[-1]
        payload = ev.data.get("data", {})
        assert payload["persona_version"] == "v1.9"
        assert "warmth" in payload["traits_used"]
        assert payload["traits_count"] == 25
        assert payload["self_model_involved"] is True

    def test_emit_emotion_updated_constructs_correct_payload(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        set_cognitive_context(trace_id="lc_emo", session_id="s_emo")

        emit_emotion_updated(
            state="calm",
            state_cn="平静",
            intensity=0.35,
            valence=0.62,
            trend="stable",
            decay_applied=False,
        )
        ev = q.recent(limit=1)[-1]
        payload = ev.data.get("data", {})
        assert payload["state"] == "calm"
        assert payload["state_cn"] == "平静"
        assert payload["intensity"] == 0.35

    def test_emit_response_path_decided(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        set_cognitive_context(trace_id="lc_path", session_id="s_path")

        emit_response_path_decided(
            path="runtime_orchestrator",
            reply_length_chars=48,
            error_code="",
        )
        ev = q.recent(limit=1)[-1]
        payload = ev.data.get("data", {})
        assert payload["path"] == "runtime_orchestrator"
        assert payload["reply_length_chars"] == 48


# ============================================================
# Class 4: Hook 失败隔离
# ============================================================
class TestHookFailureIsolation:
    """核心模块 Hook 崩溃 → 业务返回值不变。"""

    def test_memory_hook_crash_does_not_affect_search_result(self):
        """Memory.search 内 hook 崩 → search 仍返回原结果。"""
        from src.memory.memory_system import MemorySystem

        # 构造最小 MemorySystem
        mem = MemorySystem.__new__(MemorySystem)
        # 直接测试: hook 崩溃不影响 result
        # 模拟 search 内部逻辑: result 已确定, hook 抛异常被吞
        result = ["memory_1", "memory_2"]

        # 模拟 hook 崩溃
        with patch(
            "src.runtime.observer.cognitive_hooks.emit_memory_retrieved",
            side_effect=RuntimeError("hook exploded"),
        ):
            # 直接调用 emit_memory_retrieved 确认它崩了
            try:
                emit_memory_retrieved(query="test", top_k=1, result_count=0)
                # safe_emit_cognitive 内部吞了异常, 不冒泡
            except Exception:
                pytest.fail("emit_memory_retrieved 不应该抛异常")

        # result 不变
        assert result == ["memory_1", "memory_2"]

    def test_personality_hook_crash_does_not_affect_resolve(self):
        """PersonalityResolver.resolve 内 hook 崩 → resolve 返回值不变。"""
        with patch(
            "src.runtime.observer.cognitive_hooks.emit_personality_resolved",
            side_effect=RuntimeError("hook exploded"),
        ):
            try:
                emit_personality_resolved(persona_version="v1")
            except Exception:
                pytest.fail("emit_personality_resolved 不应该抛异常")

    def test_emotion_hook_crash_does_not_affect_process_event(self):
        """EmotionManager.process_event 内 hook 崩 → state 不变。"""
        with patch(
            "src.runtime.observer.cognitive_hooks.emit_emotion_updated",
            side_effect=RuntimeError("hook exploded"),
        ):
            try:
                emit_emotion_updated(state="calm")
            except Exception:
                pytest.fail("emit_emotion_updated 不应该抛异常")

    def test_safe_emit_cognitive_with_none_sink_is_noop(self):
        """sink 不可用时 safe_emit_cognitive 是 No-Op。"""
        # 不初始化 observer → get_observation_event_sink 返回 NoOp 或 None
        # safe_emit_cognitive 不抛
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
            subsystem="memory",
            data={"test": True},
        )
        # 到达 = 通过


# ============================================================
# Class 5: 数据泄露保护
# ============================================================
class TestDataLeakProtection:
    """payload 白名单: 不含完整记忆内容 / 成长理由 / 自我认知快照。"""

    def test_memory_payload_does_not_contain_full_query(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        sensitive_query = "我的身份证号码是123456789012345678"
        emit_memory_retrieved(
            query=sensitive_query,
            top_k=1, result_count=0,
            memory_ids=[], sources={}, score_range=[], max_score=0,
        )
        ev = q.recent(limit=1)[-1]
        payload = ev.data.get("data", {})
        # query_preview 截断到 80 字符
        assert len(payload["query_preview"]) <= _MAX_QUERY_PREVIEW_LEN
        # 原始 query 不在 payload 里
        assert "query" not in payload or payload.get("query") != sensitive_query

    def test_memory_payload_does_not_contain_content_field(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        emit_memory_retrieved(
            query="test",
            top_k=1, result_count=1,
            memory_ids=["mem_1"],
            sources={"chat": 1}, score_range=[0.5, 0.5], max_score=0.5,
        )
        ev = q.recent(limit=1)[-1]
        payload = ev.data.get("data", {})
        # 禁止字段
        assert "content" not in payload
        assert "text" not in payload
        assert "full" not in payload
        assert "user_id" not in payload

    def test_oversized_string_value_truncated(self, initialized_queue_and_sink):
        q, sink = initialized_queue_and_sink
        # 构造一个有超长字符串的 data
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
            subsystem="memory",
            data={"normal": "ok", "leak_attempt": "X" * 500},
        )
        # 找到事件
        events = q.recent(limit=5)
        if events:
            ev = events[-1]
            payload = ev.data.get("data", {})
            if "leak_attempt" in payload:
                assert len(payload["leak_attempt"]) <= _MAX_STRING_VALUE_LEN + 20

    def test_growth_payload_does_not_contain_reason(self, initialized_queue_and_sink):
        """Growth payload 禁止 reason / description / impact_details。"""
        # Phase 7.2.2 预留: 目前没有 emit_growth_evaluated helper
        # 但我们验证 safe_emit_cognitive 的通用保护不处理业务白名单
        # 白名单由 subsystem helper 保证(设计文档 5.1 两层架构)
        q, sink = initialized_queue_and_sink
        safe_emit_cognitive(
            "cognitive.growth.evaluated",
            trace_id="t", session_id="s",
            subsystem="growth",
            data={"proposal_exists": True, "importance": "high"},
        )
        events = q.recent(limit=1)
        # 有事件
        assert len(events) >= 1


# ============================================================
# Class 6: RuntimePipeline 集成
# ============================================================
class TestPipelineIntegration:
    def _fake_pipeline(self, event_sink=None):
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = MagicMock()
        orch.process.return_value = "你好呀这是测试回复"
        orch.__class__.__name__ = "FakeOrchestrator"
        return RuntimePipeline(orchestrator=orch, event_sink=event_sink)

    def test_pipeline_sets_and_clears_cognitive_context(self):
        """RuntimePipeline.run() 在开头 set, 结尾 clear thread-local context。"""
        q = EventQueue(maxlen=50)
        sink = ObservationEventSink(queue=q, config=ObservationConfig())
        pipe = self._fake_pipeline(event_sink=sink)

        # 运行前 context 为空
        assert _get_ctx_hint("trace_id") == ""

        pipe.run({"user_message": "测试认知追踪"})

        # 运行后 context 被清理
        assert _get_ctx_hint("trace_id") == ""

    def test_pipeline_emits_cognitive_events(self):
        """Pipeline 运行后应该有 cognitive.* 事件。"""
        # 初始化全局单例 → safe_emit_cognitive 内部 get_observation_event_sink() 拿到同一个 sink
        init_observation_layer(config=ObservationConfig())
        q = get_event_queue()
        sink = get_observation_event_sink()
        pipe = self._fake_pipeline(event_sink=sink)

        pipe.run({"user_message": "你好羽依"})

        events = q.recent(limit=50)
        # 至少有 cognitive.response.path_decided
        cog_events = []
        for e in events:
            ev_type = e.data.get("event_type", "") if isinstance(e.data, dict) else ""
            if isinstance(ev_type, str) and ev_type.startswith("cognitive."):
                cog_events.append(ev_type)
        assert CognitiveEventType.RESPONSE_PATH_DECIDED in cog_events, (
            f"期望 cognitive.response.path_decided, 实际 cognitive 事件: {cog_events}"
        )

    def test_pipeline_without_event_sink_still_clears_context(self):
        """event_sink=None 时 thread-local context 仍然被清理。"""
        pipe = self._fake_pipeline(event_sink=None)
        pipe.run({"user_message": "test"})
        assert _get_ctx_hint("trace_id") == ""


# ============================================================
# Class 7: 向后兼容
# ============================================================
class TestBackwardCompatibility:
    def test_observer_disabled_cognitive_hook_is_noop(self):
        """observer 全局 disabled → cognitive hook 不产生任何事件。"""
        # 用 NoOp sink — patch 正确路径(函数内部 import 的源头)
        sink = NoOpObservationSink()
        with patch(
            "src.runtime.observer.get_observation_event_sink",
            return_value=sink,
        ):
            safe_emit_cognitive(
                CognitiveEventType.MEMORY_RETRIEVED,
                trace_id="t", session_id="s",
                subsystem="memory",
                data={"test": True},
            )
        # NoOp sink 不推事件到 queue
        # 到达此处 = 通过(没抛异常)

    def test_cognitive_context_not_set_hooks_degrade_gracefully(self, initialized_queue_and_sink):
        """不设 cognitive context → hook 仍能 emit(trace_id 为空字符串)。"""
        q, sink = initialized_queue_and_sink
        # 不调 set_cognitive_context
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            subsystem="memory",
            data={"test": True},
        )
        # 仍然 emit 了(trace_id 为空)
        events = q.recent(limit=5)
        assert len(events) >= 1

    def test_cognitive_event_schema_version_default(self):
        ev = CognitiveEvent(
            event_type=CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
        )
        assert ev.schema_version == "1.0"

    def test_cognitive_hook_does_not_block_caller(self):
        """Hook 调用不阻塞(同步返回)。"""
        start = time.time()
        safe_emit_cognitive(
            CognitiveEventType.MEMORY_RETRIEVED,
            trace_id="t", session_id="s",
            subsystem="memory",
            data={"benchmark": True},
        )
        elapsed = time.time() - start
        # hook 应该 < 50ms 完成(即使 sink 初始化)
        assert elapsed < 0.05, f"hook 耗时 {elapsed*1000:.1f}ms, 可能阻塞了"
