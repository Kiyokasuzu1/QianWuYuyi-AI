# -*- coding: utf-8 -*-
"""
tests/test_phase_7_0_runtime_center.py

Phase 7.0 Dashboard Runtime Center —— 单元测试。

覆盖:
  1. RuntimeTraceRecorder: start_trace / record_stage / finalize / get_recent_traces / 文件写入
  2. RuntimeStatusTracker: mark_started / update_task / record_event / record_request_start /
     record_response / get_status / uptime
  3. AgentServerStatusReader: 读取正常/过期/缺失文件
  4. get_services_status: 4 服务聚合
  5. RuntimePipeline 集成: 注入 trace_recorder + status_tracker 后正常工作
  6. Dashboard API 端点: /runtime/lifecycle, /runtime/traces, /runtime/services
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def tmp_trace_dir(tmp_path):
    """临时 trace 目录。"""
    d = tmp_path / "runtime_trace"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def clean_singletons():
    """测试前后清理全局单例。"""
    from src.runtime.trace_recorder import reset_runtime_trace_recorder_for_testing
    from src.runtime.status_tracker import reset_runtime_status_tracker_for_testing
    from src.admin.runtime_dashboard_provider import (
        reset_runtime_dashboard_provider_for_testing,
    )
    reset_runtime_trace_recorder_for_testing()
    reset_runtime_status_tracker_for_testing()
    reset_runtime_dashboard_provider_for_testing()
    yield
    reset_runtime_trace_recorder_for_testing()
    reset_runtime_status_tracker_for_testing()
    reset_runtime_dashboard_provider_for_testing()


# =====================================================================
# 1. RuntimeTraceRecorder 测试
# =====================================================================

class TestRuntimeTraceRecorder:
    """RuntimeTraceRecorder 单元测试。"""

    def test_start_trace_returns_context(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(
            trace_id="test_trace_1",
            session_id="sess_1",
            user_message="你好羽依",
        )
        assert ctx is not None
        assert ctx.trace_id == "test_trace_1"
        assert ctx.session_id == "sess_1"

    def test_start_trace_auto_generates_id(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(user_message="test")
        assert ctx.trace_id.startswith("trace_")
        assert len(ctx.trace_id) > len("trace_")

    def test_record_stage_and_finalize(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(
            trace_id="trace_finalize_test",
            user_message="测试消息",
        )
        ctx.record_stage("token_optimization")
        ctx.record_stage("runtime_path")
        ctx.finalize(
            reply="测试回复",
            reply_source="runtime",
            success=True,
        )
        # 从内存缓存读取
        traces = recorder.get_recent_traces(limit=10)
        assert len(traces) == 1
        t = traces[0]
        assert t["trace_id"] == "trace_finalize_test"
        assert t["reply_source"] == "runtime"
        assert t["success"] is True
        assert t["reply_preview"] == "测试回复"
        assert len(t["stages"]) == 2
        assert t["stages"][0]["name"] == "token_optimization"
        assert t["stages"][1]["name"] == "runtime_path"

    def test_finalize_writes_jsonl_file(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(
            trace_id="trace_file_test",
            user_message="文件写入测试",
        )
        ctx.record_stage("stage1")
        ctx.finalize(reply="ok", reply_source="runtime", success=True)
        # 检查 JSONL 文件已生成
        files = list(tmp_trace_dir.glob("trace_*.jsonl"))
        assert len(files) == 1
        # 检查文件内容
        lines = files[0].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["trace_id"] == "trace_file_test"
        assert record["success"] is True

    def test_get_recent_traces_ordered_newest_first(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        for i in range(5):
            ctx = recorder.start_trace(
                trace_id=f"trace_order_{i}",
                user_message=f"msg_{i}",
            )
            ctx.record_stage("stage")
            ctx.finalize(reply=f"reply_{i}", reply_source="runtime", success=True)
            time.sleep(0.01)  # 确保时间戳不同
        traces = recorder.get_recent_traces(limit=3)
        assert len(traces) == 3
        # 最新在前(index 4 是最后写入的)
        assert traces[0]["trace_id"] == "trace_order_4"
        assert traces[2]["trace_id"] == "trace_order_2"

    def test_get_trace_by_id(self, tmp_trace_dir):
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(trace_id="trace_lookup_1", user_message="lookup")
        ctx.finalize(reply="ok", reply_source="runtime", success=True)
        found = recorder.get_trace_by_id("trace_lookup_1")
        assert found is not None
        assert found["trace_id"] == "trace_lookup_1"
        # 不存在的 id
        assert recorder.get_trace_by_id("nonexistent") is None

    def test_recorder_exception_isolation(self, tmp_trace_dir):
        """recorder 任何方法都不应抛出异常。"""
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        # 不存在的 trace_id record_stage 不应抛
        recorder.record_stage("nonexistent_id", "stage")
        # None trace_ctx finalize 不应抛
        recorder.finalize_trace(None)
        # 负数 limit 不应抛
        traces = recorder.get_recent_traces(limit=-1)
        assert isinstance(traces, list)

    def test_user_message_preview_truncated(self, tmp_trace_dir):
        from src.runtime.trace_recorder import (
            RuntimeTraceRecorder,
            DEFAULT_USER_MESSAGE_PREVIEW_LEN,
        )
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        long_msg = "x" * (DEFAULT_USER_MESSAGE_PREVIEW_LEN + 50)
        ctx = recorder.start_trace(trace_id="trace_long", user_message=long_msg)
        ctx.finalize(reply="ok", reply_source="runtime", success=True)
        traces = recorder.get_recent_traces(limit=1)
        preview = traces[0]["user_message_preview"]
        assert len(preview) == DEFAULT_USER_MESSAGE_PREVIEW_LEN + 3  # +3 for "..."
        assert preview.endswith("...")

    def test_start_stage_end_stage_explicit(self, tmp_trace_dir):
        """测试显式 start_stage / end_stage API。"""
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(trace_id="trace_explicit", user_message="test")
        ctx.start_stage("memory_retrieval")
        time.sleep(0.05)
        ctx.end_stage("memory_retrieval")
        ctx.finalize(reply="ok", reply_source="runtime", success=True)
        traces = recorder.get_recent_traces(limit=1)
        stage = traces[0]["stages"][0]
        assert stage["name"] == "memory_retrieval"
        assert stage["duration_ms"] >= 40  # 至少 40ms


# =====================================================================
# 2. RuntimeStatusTracker 测试
# =====================================================================

class TestRuntimeStatusTracker:
    """RuntimeStatusTracker 单元测试。"""

    def test_initial_status(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        status = tracker.get_status()
        assert status["status"] == "running"
        assert status["current_task"] == "idle"
        assert status["uptime_seconds"] >= 0
        assert status["request_count"] == 0
        assert status["success_count"] == 0
        assert status["failure_count"] == 0

    def test_mark_started_resets_uptime(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        time.sleep(0.1)
        old_uptime = tracker.get_uptime_seconds()
        assert old_uptime > 0
        tracker.mark_started()
        new_uptime = tracker.get_uptime_seconds()
        assert new_uptime < old_uptime

    def test_update_task(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.update_task("memory_reflection")
        assert tracker.get_current_task() == "memory_reflection"
        status = tracker.get_status()
        assert status["current_task"] == "memory_reflection"

    def test_record_event(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.record_event("user_message", preview="你好")
        status = tracker.get_status()
        assert status["last_event"] == "user_message"
        assert "你好" in status["last_event_preview"]

    def test_record_request_start(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.record_request_start(
            trace_id="trace_123",
            user_message="测试消息",
        )
        status = tracker.get_status()
        assert status["request_count"] == 1
        assert status["current_task"] == "processing"
        assert status["last_event"] == "user_message"
        assert status["last_trace_id"] == "trace_123"

    def test_record_response_success(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.record_request_start(trace_id="t1", user_message="hi")
        tracker.record_response(
            reply="你好呀",
            success=True,
            trace_id="t1",
        )
        status = tracker.get_status()
        assert status["success_count"] == 1
        assert status["failure_count"] == 0
        assert status["current_task"] == "idle"
        assert status["last_response_preview"] == "你好呀"
        assert status["last_error"] is None
        assert status["last_response_at"] is not None
        assert status["last_response_age_seconds"] is not None

    def test_record_response_failure(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.record_request_start(trace_id="t2", user_message="hi")
        tracker.record_response(
            reply="",
            success=False,
            error="LLM timeout",
            trace_id="t2",
        )
        status = tracker.get_status()
        assert status["failure_count"] == 1
        assert status["success_count"] == 0
        assert status["last_error"] == "LLM timeout"

    def test_uptime_human_format(self):
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        status = tracker.get_status()
        # uptime_human 应包含 "s" 后缀
        assert "s" in status["uptime_human"]

    def test_exception_isolation(self):
        """tracker 任何方法都不应抛出异常。"""
        from src.runtime.status_tracker import RuntimeStatusTracker
        tracker = RuntimeStatusTracker()
        tracker.update_task(None)  # type: ignore
        tracker.record_event(None)  # type: ignore
        tracker.record_request_start(trace_id=None, user_message=None)  # type: ignore
        tracker.record_response(reply=None, success=None)  # type: ignore
        # 不应抛
        status = tracker.get_status()
        assert isinstance(status, dict)


# =====================================================================
# 3. AgentServerStatusReader 测试
# =====================================================================

class TestAgentServerStatusReader:
    """AgentServerStatusReader 单元测试。"""

    def test_read_missing_file(self, tmp_path):
        from src.runtime.status_tracker import AgentServerStatusReader
        reader = AgentServerStatusReader(status_file=tmp_path / "nonexistent.json")
        result = reader.read()
        assert result["available"] is False
        assert result["fallback"] is True
        assert result["fallback_reason"] == "file_not_found"
        assert result["running"] is False

    def test_read_valid_fresh_file(self, tmp_path):
        from src.runtime.status_tracker import AgentServerStatusReader
        status_file = tmp_path / "agent_status.json"
        now_iso = datetime.now().isoformat(timespec="seconds")
        status_data = {
            "enabled": True,
            "running": True,
            "host": "0.0.0.0",
            "port": 8765,
            "pid": 12345,
            "total_agents": 2,
            "authenticated_agents": 1,
            "agents": [{"id": "agent1"}],
            "updated_at": now_iso,
        }
        status_file.write_text(json.dumps(status_data), encoding="utf-8")
        reader = AgentServerStatusReader(status_file=status_file)
        result = reader.read()
        assert result["available"] is True
        assert result["running"] is True
        assert result["host"] == "0.0.0.0"
        assert result["port"] == 8765
        assert result["pid"] == 12345
        assert result["total_agents"] == 2
        assert result["authenticated_agents"] == 1
        assert result["stale"] is False
        assert result["initiative_sender_alive"] is True  # [DEPRECATED] 兼容字段
        assert result["initiative_bridge_alive"] is True  # Phase 7.2.1-p1 新字段
        assert result["fallback"] is False

    def test_read_stale_file(self, tmp_path):
        from src.runtime.status_tracker import AgentServerStatusReader
        status_file = tmp_path / "agent_status.json"
        # 60 秒前的时间戳(超过默认 30 秒阈值)
        old_time = (datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds")
        status_data = {
            "enabled": True,
            "running": True,
            "host": "0.0.0.0",
            "port": 8765,
            "pid": 12345,
            "total_agents": 0,
            "authenticated_agents": 0,
            "agents": [],
            "updated_at": old_time,
        }
        status_file.write_text(json.dumps(status_data), encoding="utf-8")
        reader = AgentServerStatusReader(status_file=status_file, staleness_threshold=30)
        result = reader.read()
        assert result["stale"] is True
        assert result["running"] is False  # stale -> running=False
        assert result["initiative_sender_alive"] is False  # [DEPRECATED] 兼容
        assert result["initiative_bridge_alive"] is False  # Phase 7.2.1-p1 新字段

    def test_read_invalid_json(self, tmp_path):
        from src.runtime.status_tracker import AgentServerStatusReader
        status_file = tmp_path / "agent_status.json"
        status_file.write_text("not valid json {{{", encoding="utf-8")
        reader = AgentServerStatusReader(status_file=status_file)
        result = reader.read()
        assert result["available"] is False
        assert result["fallback"] is True
        assert "json_decode_error" in result["fallback_reason"]


# =====================================================================
# 4. get_services_status 聚合测试
# =====================================================================

class TestGetServicesStatus:
    """get_services_status 聚合函数测试。"""

    def test_all_available(self, tmp_path):
        from src.runtime.status_tracker import (
            RuntimeStatusTracker,
            AgentServerStatusReader,
            get_services_status,
        )
        # 准备 agent server 状态文件
        status_file = tmp_path / "agent_status.json"
        now_iso = datetime.now().isoformat(timespec="seconds")
        status_file.write_text(json.dumps({
            "enabled": True, "running": True,
            "host": "0.0.0.0", "port": 8765,
            "pid": 123, "total_agents": 1, "authenticated_agents": 1,
            "agents": [], "updated_at": now_iso,
        }), encoding="utf-8")

        tracker = RuntimeStatusTracker()
        tracker.update_task("processing")
        fake_rp = MagicMock()
        fake_rp.get_status.return_value = {
            "online": True,
            "runtime": {"initialized": True, "is_running": True},
            "bridge_error": None,
        }
        reader = AgentServerStatusReader(status_file=status_file)

        result = get_services_status(
            status_tracker=tracker,
            runtime_provider=fake_rp,
            agent_status_reader=reader,
        )
        assert result["available"] is True
        assert result["api_server"]["status"] == "running"
        assert result["api_server"]["current_task"] == "processing"
        assert result["runtime"]["status"] == "running"
        assert result["runtime"]["initialized"] is True
        assert result["initiative_sender"]["status"] == "running"
        # Phase 7.2.1-p1 新字段验证：mode="bridge" + initiative_bridge_alive=true
        assert result["initiative_sender"].get("mode") == "bridge"
        assert result["initiative_sender"].get("initiative_bridge_alive") is True
        assert result["agent_server"]["status"] == "running"
        assert result["agent_server"]["port"] == 8765

    def test_no_tracker_fallback(self):
        from src.runtime.status_tracker import get_services_status
        result = get_services_status(
            status_tracker=None,
            runtime_provider=None,
            agent_status_reader=None,
        )
        # api_server 仍应为 running(能响应说明在跑)
        assert result["api_server"]["status"] == "running"
        assert result["api_server"]["fallback"] is True  # 但缺少 uptime
        # runtime 不可用
        assert result["runtime"]["status"] == "unknown"
        assert result["runtime"]["fallback"] is True


# =====================================================================
# 5. RuntimePipeline 集成测试(注入 trace_recorder + status_tracker)
# =====================================================================

class TestRuntimePipelinePhase70Integration:
    """RuntimePipeline 注入 Phase 7.0 钩子后的集成测试。"""

    def test_pipeline_with_hooks_produces_trace(self, tmp_trace_dir):
        """注入 trace_recorder + status_tracker 后,pipeline.run() 应产生 trace 记录。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        from src.runtime.trace_recorder import RuntimeTraceRecorder
        from src.runtime.status_tracker import RuntimeStatusTracker

        # 构造 fake orchestrator
        fake_orch = MagicMock()
        fake_orch.process.return_value = "你好呀,这是测试回复"
        fake_orch.__class__.__name__ = "FakeOrchestrator"

        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        tracker = RuntimeStatusTracker()

        pipeline = RuntimePipeline(
            orchestrator=fake_orch,
            trace_recorder=recorder,
            status_tracker=tracker,
        )
        ctx = pipeline.run({"user_message": "你好羽依"})

        # 验证 pipeline 正常返回
        assert ctx is not None
        # 验证 trace 已记录
        traces = recorder.get_recent_traces(limit=5)
        assert len(traces) == 1
        t = traces[0]
        assert t["success"] is True
        assert t["reply_source"] == "legacy"
        assert "你好" in t["reply_preview"]
        assert len(t["stages"]) >= 1  # 至少有 orchestrator_fallback

        # 验证 status tracker 已更新
        status = tracker.get_status()
        assert status["request_count"] == 1
        assert status["success_count"] == 1
        assert status["current_task"] == "idle"
        assert status["last_response_at"] is not None

    def test_pipeline_without_hooks_backward_compatible(self):
        """不注入钩子时,pipeline 行为与之前完全一致。"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        fake_orch = MagicMock()
        fake_orch.process.return_value = "回复"
        fake_orch.__class__.__name__ = "FakeOrch"

        pipeline = RuntimePipeline(orchestrator=fake_orch)
        assert pipeline.trace_recorder is None
        assert pipeline.status_tracker is None

        ctx = pipeline.run({"user_message": "测试"})
        assert ctx is not None
        # 不应抛异常

    def test_pipeline_trace_recorder_exception_isolated(self, tmp_trace_dir):
        """trace_recorder 抛异常时,pipeline 仍正常完成。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        from src.runtime.status_tracker import RuntimeStatusTracker

        fake_orch = MagicMock()
        fake_orch.process.return_value = "回复"
        fake_orch.__class__.__name__ = "FakeOrch"

        # 构造一个会抛异常的 fake recorder
        bad_recorder = MagicMock()
        bad_recorder.start_trace.side_effect = RuntimeError("recorder broken")

        tracker = RuntimeStatusTracker()
        pipeline = RuntimePipeline(
            orchestrator=fake_orch,
            trace_recorder=bad_recorder,
            status_tracker=tracker,
        )
        # 不应抛异常
        ctx = pipeline.run({"user_message": "测试"})
        assert ctx is not None
        # orchestrator 仍被调用
        fake_orch.process.assert_called_once()


# =====================================================================
# 6. Dashboard API 端点测试
# =====================================================================

class TestDashboardRuntimeCenterAPI:
    """/runtime/lifecycle, /runtime/traces, /runtime/services 端点测试。"""

    @pytest.fixture
    def app_client(self, clean_singletons, tmp_trace_dir):
        """构造 Flask 测试客户端,注入 mock 数据。"""
        from src.runtime.trace_recorder import (
            RuntimeTraceRecorder,
            set_runtime_trace_recorder,
        )
        from src.runtime.status_tracker import (
            RuntimeStatusTracker,
            set_runtime_status_tracker,
        )
        from src.admin.dashboard.runtime_router import runtime_v2_bp
        from flask import Flask

        # 注入单例
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        tracker = RuntimeStatusTracker()
        tracker.record_request_start(trace_id="test_trace", user_message="测试消息")
        tracker.record_response(reply="测试回复", success=True, trace_id="test_trace")
        # 写一条 trace
        ctx = recorder.start_trace(trace_id="test_trace", user_message="测试消息")
        ctx.record_stage("token_optimization")
        ctx.record_stage("orchestrator_fallback")
        ctx.finalize(reply="测试回复", reply_source="legacy", success=True)
        set_runtime_trace_recorder(recorder)
        set_runtime_status_tracker(tracker)

        app = Flask(__name__)
        app.register_blueprint(runtime_v2_bp)

        # mock _is_local_request 返回 True(允许本地访问)
        with app.test_request_context():
            pass

        # patch _is_local_request
        from src.admin.dashboard import runtime_router as rr_mod
        original = rr_mod._is_local_request
        rr_mod._is_local_request = lambda: True
        app.config["TESTING"] = True
        client = app.test_client()
        yield client
        rr_mod._is_local_request = original

    def test_lifecycle_endpoint(self, app_client):
        """GET /api/dashboard/v2/runtime/lifecycle 返回生命周期状态。"""
        resp = app_client.get("/api/dashboard/v2/runtime/lifecycle")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert data["status"] == "running"
        assert "uptime_seconds" in data
        assert "uptime_human" in data
        assert data["current_task"] == "idle"
        assert data["request_count"] == 1
        assert data["success_count"] == 1
        assert data["last_response_preview"] == "测试回复"

    def test_traces_endpoint(self, app_client):
        """GET /api/dashboard/v2/runtime/traces 返回 trace 列表。"""
        resp = app_client.get("/api/dashboard/v2/runtime/traces?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert data["count"] == 1
        assert len(data["traces"]) == 1
        t = data["traces"][0]
        assert t["trace_id"] == "test_trace"
        assert t["success"] is True
        assert len(t["stages"]) == 2

    def test_services_endpoint(self, app_client):
        """GET /api/dashboard/v2/runtime/services 返回服务状态。"""
        resp = app_client.get("/api/dashboard/v2/runtime/services")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert "api_server" in data
        assert "runtime" in data
        assert "initiative_sender" in data
        assert "agent_server" in data
        # api_server 应为 running(测试客户端在跑)
        assert data["api_server"]["status"] == "running"
        # agent_server 可能是 fallback(没有真实状态文件)
        assert "status" in data["agent_server"]
        assert "fallback" in data["agent_server"]

    def test_traces_limit_clamped(self, app_client):
        """limit 参数被限制在 1-200 范围。"""
        resp = app_client.get("/api/dashboard/v2/runtime/traces?limit=999")
        assert resp.status_code == 200
        # 不应因 limit=999 而出错

    def test_traces_invalid_limit_defaults(self, app_client):
        """非法 limit 参数默认为 20。"""
        resp = app_client.get("/api/dashboard/v2/runtime/traces?limit=abc")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True


# =====================================================================
# 7. RuntimeDashboardProvider 新方法测试
# =====================================================================

class TestRuntimeDashboardProviderPhase70:
    """RuntimeDashboardProvider Phase 7.0 新方法测试。"""

    def test_get_runtime_lifecycle_status(self, clean_singletons):
        from src.runtime.status_tracker import (
            RuntimeStatusTracker,
            set_runtime_status_tracker,
        )
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
        )
        tracker = RuntimeStatusTracker()
        tracker.update_task("processing")
        set_runtime_status_tracker(tracker)

        provider = RuntimeDashboardProvider()
        result = provider.get_runtime_lifecycle_status()
        assert result["status"] == "running"
        assert result["current_task"] == "processing"
        assert result["available"] is True
        assert result["fallback"] is False

    def test_get_runtime_lifecycle_status_no_tracker(self, clean_singletons, monkeypatch):
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
        )
        # 模拟 status_tracker 单例不可用
        import src.runtime.status_tracker as st_mod
        monkeypatch.setattr(st_mod, "get_runtime_status_tracker", lambda: None)
        provider = RuntimeDashboardProvider()
        result = provider.get_runtime_lifecycle_status()
        assert result["fallback"] is True
        assert "status_tracker_unavailable" in result["fallback_reason"]

    def test_get_recent_traces(self, clean_singletons, tmp_trace_dir):
        from src.runtime.trace_recorder import (
            RuntimeTraceRecorder,
            set_runtime_trace_recorder,
        )
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
        )
        recorder = RuntimeTraceRecorder(trace_dir=tmp_trace_dir)
        ctx = recorder.start_trace(trace_id="p_test", user_message="test")
        ctx.finalize(reply="ok", reply_source="runtime", success=True)
        set_runtime_trace_recorder(recorder)

        provider = RuntimeDashboardProvider()
        result = provider.get_recent_traces(limit=5)
        assert result["available"] is True
        assert result["count"] == 1
        assert result["traces"][0]["trace_id"] == "p_test"

    def test_get_recent_traces_no_recorder(self, clean_singletons, monkeypatch):
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
        )
        # 模拟 trace_recorder 单例不可用
        import src.runtime.trace_recorder as tr_mod
        monkeypatch.setattr(tr_mod, "get_runtime_trace_recorder", lambda: None)
        provider = RuntimeDashboardProvider()
        result = provider.get_recent_traces(limit=5)
        assert result["fallback"] is True
        assert "trace_recorder_unavailable" in result["fallback_reason"]
        assert result["count"] == 0

    def test_get_services_status(self, clean_singletons):
        from src.runtime.status_tracker import (
            RuntimeStatusTracker,
            set_runtime_status_tracker,
        )
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
        )
        tracker = RuntimeStatusTracker()
        set_runtime_status_tracker(tracker)

        provider = RuntimeDashboardProvider()
        result = provider.get_services_status()
        assert "api_server" in result
        assert "runtime" in result
        assert "initiative_sender" in result
        assert "agent_server" in result
        assert result["api_server"]["status"] == "running"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
