# -*- coding: utf-8 -*-
"""
tests/test_phase_7_1_observation.py

Phase 7.1 Runtime Intelligence 单元测试。

覆盖:
    Class TestRuntimeEvent            —— RuntimeEvent 数据结构(schema_version / runtime.* 命名空间)
    Class TestObservationConfig       —— ObservationConfig 从 dict 加载(默认持久化关闭)
    Class TestEventQueue              —— EventQueue 队列/订阅/Last-Event-ID/慢消费者
    Class TestObservationEventSink    —— Sink 持久化开关/异常隔离/NoOpSink
    Class TestPipelinePhase71Hooks    —— RuntimePipeline event_sink 钩子集成
    Class TestRuntimeEventsEndpoints  —— /events/recent API + 蓝图注册(local-only)

重要策略:
    - 所有测试严格隔离: observer 单例 fixture 前后 reset
    - 不触碰 Memory/Growth/SelfModel/EventBus/Emotion/Personality 核心模块
    - 不要求 config.yaml / API Key
    - 全部 stdlib + pytest fixtures 完成
"""
from __future__ import annotations

import json
import time
import threading
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.runtime.observer import (
    RUNTIME_EVENT_SCHEMA_VERSION,
    RuntimeEvent,
    RuntimeEventType,
    EVENT_LEVEL_INFO,
    EVENT_LEVEL_ERROR,
    ObservationConfig,
    EventQueue,
    ObservationEventSink,
    NoOpObservationSink,
    safe_emit,
    safe_emit_stage,
    reset_observation_singletons_for_tests,
    init_observation_layer,
    get_event_queue,
    get_observation_event_sink,
)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _reset_observation_singletons():
    """每个测试前/后重置单例,避免测试间污染。"""
    reset_observation_singletons_for_tests()
    yield
    reset_observation_singletons_for_tests()


@pytest.fixture
def tmp_events_dir(tmp_path: Path):
    """临时持久化目录。"""
    return tmp_path / "runtime_events"


# ============================================================
# Class 1: RuntimeEvent
# ============================================================
class TestRuntimeEvent:
    def test_default_schema_version_is_1_0(self):
        ev = RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_STARTED,
            trace_id="t1",
            session_id="s1",
        )
        assert ev.schema_version == "1.0"
        assert ev.schema_version == RUNTIME_EVENT_SCHEMA_VERSION

    def test_event_id_auto_populated_with_evt_prefix(self):
        ev = RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_STARTED,
            trace_id="t1", session_id="s1",
        )
        assert ev.event_id.startswith("evt_")
        assert len(ev.event_id) > len("evt_")

    def test_event_type_namespace_prefix(self):
        """所有 RuntimeEventType 必须以 runtime. 开头。"""
        for name in RuntimeEventType._ALL:
            assert name.startswith("runtime."), (
                f"事件类型 {name!r} 缺少 runtime. 命名空间"
            )

    def test_is_valid_recognizes_declared_types(self):
        assert RuntimeEventType.is_valid(RuntimeEventType.RESPONSE_SENT) is True
        assert RuntimeEventType.is_valid("not.declared.type") is False
        assert RuntimeEventType.is_valid(None) is False

    def test_to_dict_and_from_dict_roundtrip(self):
        ev1 = RuntimeEvent(
            event_type=RuntimeEventType.USER_MESSAGE_RECEIVED,
            trace_id="t_roundtrip",
            session_id="s_roundtrip",
            stage="receive",
            level=EVENT_LEVEL_INFO,
            data={"preview": "你好羽依"},
        )
        d = ev1.to_dict()
        assert isinstance(d, dict)
        assert d["event_type"] == RuntimeEventType.USER_MESSAGE_RECEIVED
        ev2 = RuntimeEvent.from_dict(d)
        assert ev2.event_id == ev1.event_id
        assert ev2.schema_version == ev1.schema_version
        assert ev2.data["preview"] == "你好羽依"

    def test_non_dict_data_normalized_to_dict_with_raw_key(self):
        ev = RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_STARTED,
            trace_id="t1", session_id="s1",
            data="not_a_dict",  # 非法 payload
        )
        assert isinstance(ev.data, dict)
        assert ev.data["raw"] == "not_a_dict"

    def test_level_property(self):
        ev = RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_ERROR,
            trace_id="t1", session_id="s1",
            level=EVENT_LEVEL_ERROR,
        )
        assert ev.is_error is True
        # short_type 去 prefix
        assert not ev.short_type.startswith("runtime.")


# ============================================================
# Class 2: ObservationConfig
# ============================================================
class TestObservationConfig:
    def test_default_persistence_disabled(self):
        """关键断言: 默认关闭持久化(避免默认写盘)。"""
        cfg = ObservationConfig()
        assert cfg.enabled is True
        assert cfg.persistence is False

    def test_from_dict_none_returns_defaults(self):
        cfg = ObservationConfig.from_dict(None)
        assert cfg.persistence is False
        assert cfg.enabled is True

    def test_from_dict_empty_dict_returns_defaults(self):
        cfg = ObservationConfig.from_dict({})
        assert cfg.persistence is False

    def test_from_dict_parses_runtime_observation_section(self):
        cfg = ObservationConfig.from_dict({
            "enabled": True,
            "persistence": True,
            "persistence_dir": "/tmp/foo_events",
            "persistence_max_days": 3,
            "max_history_len": 100,
        })
        assert cfg.persistence is True
        assert cfg.persistence_dir == "/tmp/foo_events"
        assert cfg.persistence_max_days == 3
        assert cfg.max_history_len == 100

    def test_from_dict_rejects_invalid_values_silently(self):
        cfg = ObservationConfig.from_dict({
            "persistence": "not_a_bool",  # 类型错 → 默认 False
            "max_history_len": "abc",     # 类型错 → 默认 500
            "persistence_max_days": -1,   # 小于 1 → 默认 7
        })
        assert cfg.persistence is False
        assert cfg.max_history_len == 500
        assert cfg.persistence_max_days == 7


# ============================================================
# Class 3: EventQueue
# ============================================================
class TestEventQueue:
    def _mk_ev(self, suffix: str = "0") -> RuntimeEvent:
        return RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_STARTED,
            trace_id="t_" + suffix,
            session_id="s_" + suffix,
        )

    # --- history ---
    def test_push_and_recent_returns_old_to_new(self):
        q = EventQueue(maxlen=10)
        e1 = self._mk_ev("a")
        e2 = self._mk_ev("b")
        time.sleep(0.001)
        q.push(e1)
        q.push(e2)
        recents = q.recent(limit=10)
        assert len(recents) == 2
        assert recents[0].event_id == e1.event_id  # 旧 → 新
        assert recents[1].event_id == e2.event_id

    def test_maxlen_drops_oldest(self):
        q = EventQueue(maxlen=3)
        e_ids = []
        for i in range(5):
            e = self._mk_ev(str(i))
            q.push(e)
            e_ids.append(e.event_id)
        # 只保留最后 3 条 = e_ids[2..4]
        recents = q.recent(limit=10)
        assert [e.event_id for e in recents] == e_ids[2:]
        assert q.history_count() == 3

    # --- subscribers dict 管理 ---
    def test_subscribe_returns_unique_sid_and_queue(self):
        q = EventQueue(maxlen=10)
        sid1, sub1, un1 = q.subscribe()
        sid2, sub2, un2 = q.subscribe()
        try:
            assert isinstance(sid1, str) and sid1.startswith("sub_")
            assert sid1 != sid2
            assert q.subscriber_count() == 2
            assert set(q.subscriber_ids()) == {sid1, sid2}
        finally:
            un1(); un2()

    def test_unsubscribe_stops_broadcast(self):
        q = EventQueue(maxlen=10)
        sid1, q1, un1 = q.subscribe()
        sid2, q2, un2 = q.subscribe()
        try:
            un1()  # 先踢掉 1
            q.push(self._mk_ev("broadcast"))
            # q1 已 unsub,没消息; q2 有 1 条
            assert q1.empty(), "已 unsubscribe 的队列不应该收到新消息"
            got = q2.get(timeout=1)
            assert got.event_type == RuntimeEventType.PIPELINE_STARTED
        finally:
            un2()

    def test_slow_consumer_full_queue_put_nowait_drops_without_block(self):
        """慢消费者队列满后,put_nowait 不阻塞 Pipeline。"""
        # sub 队列容量 3
        q = EventQueue(maxlen=10, subscriber_queue_len=3)
        _, sub_q, unsub = q.subscribe(queue_len=3)
        try:
            # push 6 条(满后应该自动丢 3 条,不阻塞)
            deadline = time.time() + 5  # 最多 5 秒,超时判阻塞
            for i in range(6):
                assert time.time() < deadline, "push 可能阻塞了慢消费者(超过 5s)"
                q.push(self._mk_ev("slow_" + str(i)))
            # sub 队列里只有前 3 条,后 3 条丢弃
            count = 0
            while not sub_q.empty() and count < 6:
                sub_q.get_nowait()
                count += 1
            assert count == 3, f"期望 3 条(队列满),实际 {count} 条(可能没有丢弃慢消费)"
        finally:
            unsub()

    # --- Last-Event-ID ---
    def test_recent_with_since_id_filters_before(self):
        q = EventQueue(maxlen=20)
        events = [self._mk_ev(str(i)) for i in range(5)]
        for e in events:
            q.push(e)
        # 指定 since_id = 事件 2,只返回 3、4
        result = q.recent(limit=10, since_id=events[2].event_id)
        ids = [e.event_id for e in result]
        assert events[2].event_id not in ids, "since_id 那条本身应被排除(不包含)"
        assert ids == [events[3].event_id, events[4].event_id]

    def test_contains_event_id_helper(self):
        q = EventQueue(maxlen=5)
        e = self._mk_ev("x")
        q.push(e)
        assert q.contains_event_id(e.event_id) is True
        assert q.contains_event_id("nonexistent") is False


# ============================================================
# Class 4: ObservationEventSink
# ============================================================
class TestObservationEventSink:
    def test_default_persistence_off_skips_file_write(self, tmp_events_dir):
        """关键断言: 默认配置下 emit() 不写磁盘。"""
        q = EventQueue(maxlen=50)
        cfg = ObservationConfig()  # 默认: persistence=False
        sink = ObservationEventSink(queue=q, config=cfg, project_root=tmp_events_dir.parent)
        sink.emit(
            RuntimeEventType.USER_MESSAGE_RECEIVED,
            trace_id="t1", session_id="s1",
            data={"preview": "hi"},
        )
        # 队列里有(事件进入了内存),但目录下没文件
        assert q.history_count() == 1
        persisted = list(tmp_events_dir.glob("*.jsonl"))
        assert persisted == [], f"默认关持久化,不应写文件;发现: {persisted}"

    def test_persistence_on_writes_jsonl(self, tmp_events_dir):
        q = EventQueue(maxlen=50)
        cfg = ObservationConfig(persistence=True, persistence_dir=str(tmp_events_dir))
        sink = ObservationEventSink(queue=q, config=cfg, project_root=tmp_events_dir.parent)
        sink.emit(
            RuntimeEventType.RESPONSE_SENT,
            trace_id="t_ok", session_id="s_ok",
            data={"reply_preview": "你好呀", "length_chars": 3},
        )
        # 等一下磁盘 flush
        deadline = time.time() + 3
        found = None
        while time.time() < deadline:
            files = list(tmp_events_dir.glob("events_*.jsonl"))
            if files:
                found = files[0]
                break
            time.sleep(0.05)
        assert found is not None, "持久化开启时应生成 jsonl"
        content = found.read_text(encoding="utf-8")
        assert "RESPONSE_SENT" in content or "response.sent" in content
        # JSON 每行必须可解析
        line = content.strip().splitlines()[0]
        parsed = json.loads(line)
        assert parsed["schema_version"] == RUNTIME_EVENT_SCHEMA_VERSION

    def test_noop_sink_is_fully_silent(self):
        sink = NoOpObservationSink()
        assert sink.enabled is False
        assert sink.persistence_enabled is False
        # 任何参数都不抛
        sink.emit("x", trace_id="", session_id="")
        sink.emit_event(None)
        s = sink.stats()
        assert s["noop"] is True

    def test_sink_enabled_false_runs_nothing(self, tmp_events_dir):
        q = EventQueue(maxlen=50)
        cfg = ObservationConfig(enabled=False)
        sink = ObservationEventSink(queue=q, config=cfg, project_root=tmp_events_dir.parent)
        sink.emit(
            RuntimeEventType.PIPELINE_STARTED,
            trace_id="x", session_id="y",
        )
        # 队列里也没东西(enabled=False 严格 No-Op)
        assert q.history_count() == 0
        # 统计字段暴露
        assert sink.stats()["enabled"] is False

    def test_safe_emit_with_none_sink_is_noop(self):
        """pipeline_hooks.safe_emit(None) 不抛异常。"""
        safe_emit(
            None, RuntimeEventType.PIPELINE_STARTED,
            trace_id="t", session_id="s",
        )
        # 到达此处 = 测试通过(不抛)

    def test_bad_event_does_not_raise(self):
        """任何构造失败都不冒泡。"""
        q = EventQueue(maxlen=50)
        sink = ObservationEventSink(queue=q, config=ObservationConfig())
        # 传奇怪参数 —— 不抛
        sink.emit("", trace_id=123, session_id=None, data=object())  # type: ignore
        # q 里可能有或没有事件,但一定不抛


# ============================================================
# Class 5: RuntimePipeline event_sink 钩子集成
# ============================================================
class TestPipelinePhase71Hooks:
    def _fake_pipeline(self, event_sink=None):
        """构造最小 RuntimePipeline(依赖 MagicMock orchestrator,避免加载核心模块)。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = MagicMock()
        orch.process.return_value = "你好呀这是测试回复"
        orch.__class__.__name__ = "FakeOrchestrator"
        return RuntimePipeline(
            orchestrator=orch,
            event_sink=event_sink,
        )

    def test_pipeline_emits_events_per_stage(self):
        q = EventQueue(maxlen=50)
        sink = ObservationEventSink(queue=q, config=ObservationConfig())
        pipe = self._fake_pipeline(event_sink=sink)
        ctx = pipe.run({"user_message": "你好羽依"})
        assert ctx is not None
        # 至少应该有: user.message.received, pipeline.started,
        # token_optimization.done, orchestrator_fallback.triggered,
        # response.sent, pipeline.finished —— 共 6 个
        events = q.recent(limit=50)
        types = [e.event_type for e in events]
        assert RuntimeEventType.USER_MESSAGE_RECEIVED in types, types
        assert RuntimeEventType.PIPELINE_STARTED in types, types
        # 由于 fake orchestrator 直接返回,会走 orchestrator_fallback(没有 Runtime)
        assert RuntimeEventType.ORCHESTRATOR_FALLBACK_TRIGGERED in types, types
        assert RuntimeEventType.RESPONSE_SENT in types, types
        assert RuntimeEventType.PIPELINE_FINISHED in types, types

    def test_pipeline_without_event_sink_is_fully_backward_compatible(self):
        """event_sink=None 时行为不变,没有任何副作用。"""
        pipe = self._fake_pipeline(event_sink=None)
        assert pipe.event_sink is None
        ctx = pipe.run({"user_message": "abc"})
        # 有 ctx = 成功;不关心 reply 内容
        assert ctx is not None

    def test_sink_exception_does_not_break_chat(self):
        """Sink 抛任何异常也不影响主链路返回字符串。"""
        class BadSink(ObservationEventSink):
            def emit(self, *a, **kw):
                raise RuntimeError("observer exploded")

        q = EventQueue(maxlen=50)
        bad = BadSink(queue=q, config=ObservationConfig())
        pipe = self._fake_pipeline(event_sink=bad)
        # 必须仍返回有效 context(不抛),这是观察层最硬的约束
        ctx = pipe.run({"user_message": "must not throw"})
        assert ctx is not None

    def test_pipeline_error_path_emits_pipeline_error_event(self):
        """mark_failed 路径发射 PIPELINE_ERROR。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = MagicMock()
        orch.process.return_value = ""  # 空 reply → fallback → empty_reply → err="empty reply"
        orch.__class__.__name__ = "FakeEmptyOrch"
        q = EventQueue(maxlen=50)
        sink = ObservationEventSink(queue=q, config=ObservationConfig())
        pipe = RuntimePipeline(orchestrator=orch, event_sink=sink)
        pipe.run({"user_message": "空回复请"})
        types = [e.event_type for e in q.recent(limit=50)]
        assert RuntimeEventType.PIPELINE_ERROR in types, f"期望 PIPELINE_ERROR: {types}"
        # 必须至少一条 level=error
        assert any(e.level == EVENT_LEVEL_ERROR for e in q.recent(limit=50))


# ============================================================
# Class 6: Runtime Events API 端点
# ============================================================
class TestRuntimeEventsEndpoints:
    @pytest.fixture
    def flask_app(self):
        """构造带 events 蓝图的最小 Flask app。"""
        from flask import Flask
        from src.admin.dashboard.runtime_events_router import runtime_events_bp
        app = Flask(__name__)
        app.register_blueprint(runtime_events_bp)
        # 确保观察层初始化(但进程级单例 = autouse fixture 每次 reset,这里懒 init)
        init_observation_layer(config=ObservationConfig())
        return app

    def test_recent_returns_ok_with_events(self, flask_app):
        # 先手动推 1 条事件到 queue
        q = get_event_queue()
        assert q is not None, "单例 fixture 没 init 好"
        e = RuntimeEvent(
            event_type=RuntimeEventType.PIPELINE_STARTED,
            trace_id="t_api", session_id="s_api",
        )
        q.push(e)
        # /events/recent (local-only: Flask test client 默认 127.0.0.1)
        with flask_app.test_client() as client:
            resp = client.get("/api/dashboard/v2/runtime/events/recent?limit=10")
        assert resp.status_code == 200, resp.data
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert data["count"] >= 1
        event_types = [x["event_type"] for x in data["events"]]
        assert RuntimeEventType.PIPELINE_STARTED in event_types

    def test_recent_rejects_non_local(self, flask_app):
        with flask_app.test_client() as client:
            # 用 environ_base 设置 REMOTE_ADDR 模拟外网(绕过 test client 默认 127.0.0.1)
            resp = client.get(
                "/api/dashboard/v2/runtime/events/recent",
                environ_base={"REMOTE_ADDR": "8.8.8.8"},
            )
        assert resp.status_code == 403, f"expect 403, got {resp.status_code}: {resp.data}"
        j = resp.get_json()
        assert j["error"]["code"] == "dashboard_local_only"

    def test_sink_process_singleton_factory(self):
        """单例工厂不抛异常,返回兼容对象。"""
        init_observation_layer(config=ObservationConfig())
        sink = get_observation_event_sink()
        assert sink is not None
        # emit 能被调用(不会抛异常)
        sink.emit(
            RuntimeEventType.PIPELINE_STARTED,
            trace_id="singleton_test", session_id="s",
        )


# ============================================================
# 最后: 5 个用户审核调整点的单独断言测试(确保真的实现了)
# ============================================================
class TestPhase71AuditChecks:
    """必改调整 1-5 全部实现的验证。"""

    # 必改 1: EventQueue subscriber 用 dict 而非 list
    def test_subscribers_storage_is_dict(self):
        q = EventQueue(maxlen=10)
        # 检查内部存储类型
        assert isinstance(q._subscribers, dict), (
            "必改调整 1: subscribers 必须是 dict,现在是 "
            + type(q._subscribers).__name__
        )

    # 必改 2: RuntimeEvent 有 schema_version 且默认 1.0
    def test_event_has_schema_version(self):
        ev = RuntimeEvent(
            event_type="runtime.user.message.received",
            trace_id="t", session_id="s",
        )
        assert hasattr(ev, "schema_version")
        assert ev.schema_version == "1.0"

    # 必改 3: 持久化默认关闭
    def test_default_persistence_disabled_audit_check(self):
        cfg = ObservationConfig.from_dict({})
        assert cfg.persistence is False, "必改调整 3: 持久化必须默认关闭"

    # 必改 4: 事件类型 runtime.xxx 命名空间
    def test_event_type_namespace_audit(self):
        sample = RuntimeEventType.RESPONSE_SENT
        assert sample.startswith("runtime.")

    # 必改 5: SSE 端点包含 retry header + id 行
    def test_sse_format_includes_id_and_retry(self, tmp_path):
        """SSE 格式化辅助函数包含 id: 和全局 Retry 头。"""
        from src.admin.dashboard.runtime_events_router import (
            _format_sse_data,
            SSE_RECONNECT_MS,
        )
        out = _format_sse_data({"event_id": "evt_abc", "foo": 1})
        assert "id:evt_abc" in out, "必改调整 5: SSE 必须写 id:event_id 行"
        assert "event:runtime" in out
        assert 'data:{"event_id": "evt_abc"' in out or 'data:{"foo": 1' in out
        # 全局值 3000ms
        assert SSE_RECONNECT_MS == 3000
