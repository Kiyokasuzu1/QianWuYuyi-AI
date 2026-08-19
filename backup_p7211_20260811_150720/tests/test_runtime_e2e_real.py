# -*- coding: utf-8 -*-
"""
tests/test_runtime_e2e_real.py

Phase 6.6 —— Real Runtime E2E Validation(真实运行闭环验证)。

目标:让 Runtime 从"架构完成"进入"真实可运行版本"。
不新增大型模块、不扩展架构 —— 验证已有链路在真实运行场景下稳定。

覆盖维度:
    1. TestPipelineRun            (5)  RuntimePipeline.run() 真实运行
    2. TestTokenOptimizerE2E      (4)  Token 优化端到端
    3. TestPersistenceE2E         (3)  持久化钩子真实落盘
    4. TestEventSinkE2E           (3)  事件发布
    5. TestHTTPChatE2E            (4)  Flask /chat 真实请求
    6. TestRuntimeStatusEndpoint  (4)  /runtime/status 端点
    7. TestSmokeMessage           (2)  "你好羽依,介绍一下自己"
    8. TestProductionStartup      (2)  python -m src.runtime.pipeline_server 启动验证
    9. TestIsolationGuarantees    (3)  runtime isolation / no llm replacement /
                                       persistence fallback / token fallback

合计: 30 tests(>= 20)
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fake / Mock(与已有 test_runtime_pipeline.py 风格一致)
# ============================================================
class _FakeOrchestrator:
    """真实 E2E 用 orchestrator:记录所有调用,返回可读 reply。"""

    def __init__(self, reply: str = "你好,我是羽依。", raise_exc: Optional[Exception] = None):
        self._reply = reply
        self._raise = raise_exc
        self.calls: List[str] = []
        self.call_count: int = 0

    def process(self, user_message: str) -> str:
        self.call_count += 1
        self.calls.append(user_message)
        if self._raise is not None:
            raise self._raise
        return self._reply


class _FakeStorage:
    """真实落盘 storage:用 tmp 目录,验证文件真的被写出。"""

    def __init__(self, base_dir: str, *, raise_exc: Optional[Exception] = None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._raise = raise_exc
        self.saved: List[Dict[str, Any]] = []
        self.call_count: int = 0

    def save(self, context):
        self.call_count += 1
        # 把 to_dict 写入文件,验证真实落盘
        try:
            d = context.to_dict() if hasattr(context, "to_dict") else dict(context.__dict__)
        except Exception:
            d = {"lifecycle_id": getattr(context, "lifecycle_id", "")}
        path = self.base_dir / f"{d.get('lifecycle_id', 'x')}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001
            if self._raise is None:
                self._raise = exc
        self.saved.append({
            "lifecycle_id": d.get("lifecycle_id"),
            "path": str(path),
            "state": d.get("state"),
        })
        if self._raise is not None:
            raise self._raise
        return {"saved": True, "lifecycle_id": d.get("lifecycle_id", ""), "path": str(path)}


class _FakeEventSink:
    def __init__(self, *, raise_exc: Optional[Exception] = None):
        self.events: List[Dict[str, Any]] = []
        self._raise = raise_exc
        self.call_count: int = 0

    def emit(self, event):
        self.call_count += 1
        self.events.append(dict(event))
        if self._raise is not None:
            raise self._raise


class _FakeTokenOptimizer:
    """模拟真实 token 优化器,产出真实可比较的 token_usage。"""

    def __init__(self, payload: Optional[Dict[str, Any]] = None, raise_exc: Optional[Exception] = None):
        self._payload = payload
        self._raise = raise_exc
        self.calls: List[Any] = []

    def optimize(self, user_message, history=None, memories=None):
        self.calls.append({"user_message": user_message, "history": history, "memories": memories})
        if self._raise is not None:
            raise self._raise
        if self._payload is not None:
            return dict(self._payload)
        return {
            "content": f"OPT::{user_message}",
            "before_tokens": 200,
            "after_tokens": 100,
            "saved_tokens": 100,
            "compression_ratio": 0.5,
            "applied": True,
            "fallback_reason": None,
        }


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def orch():
    return _FakeOrchestrator(reply="你好,我是羽依。")


@pytest.fixture
def tmp_storage_path():
    with tempfile.TemporaryDirectory(prefix="yuyi_runtime_e2e_") as d:
        yield d


@pytest.fixture
def storage(tmp_storage_path):
    return _FakeStorage(base_dir=tmp_storage_path)


@pytest.fixture
def sink():
    return _FakeEventSink()


@pytest.fixture
def token_opt():
    return _FakeTokenOptimizer()


@pytest.fixture
def pipeline_factory(orch, storage, sink, token_opt):
    """构造一个完整 Pipeline,带 storage / sink / token_optimizer。"""
    from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
    from src.runtime.runtime_pipeline import RuntimePipeline

    hook = RuntimePersistenceHook(storage)
    return RuntimePipeline(
        orchestrator=orch,
        persistence_hook=hook,
        event_sink=sink,
        token_optimizer=token_opt,
    )


# ============================================================
# 1. TestPipelineRun —— RuntimePipeline.run() 真实运行
# ============================================================
class TestPipelineRun:
    def test_pipeline_run_returns_runtime_context(self, orch):
        from src.runtime.lifecycle_context import RuntimeContext
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "你好"})
        assert isinstance(ctx, RuntimeContext)
        assert ctx.state == "success"
        assert orch.call_count == 1

    def test_pipeline_run_increments_count(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        for _ in range(3):
            p.run({"user_message": "hi"})
        assert p.run_count == 3
        assert p.success_count == 3

    def test_pipeline_run_context_to_json(self, orch):
        """RuntimeContext 可序列化为 json,验证真实生成能力。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "hi"})
        d = ctx.to_dict()
        j = json.dumps(d, ensure_ascii=False, default=str)
        loaded = json.loads(j)
        assert loaded["lifecycle_id"] == ctx.lifecycle_id
        assert loaded["state"] == "success"
        assert "outputs" in loaded

    def test_pipeline_run_preserves_user_message(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "introduce yourself"})
        assert ctx.inputs["user_message"] == "introduce yourself"
        assert ctx.outputs["snapshot"]["user_message"] == "introduce yourself"

    def test_pipeline_run_lifecycle_id_unique(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        ids = {p.run({"user_message": f"m{i}"}).lifecycle_id for i in range(5)}
        assert len(ids) == 5


# ============================================================
# 2. TestTokenOptimizerE2E —— Token 优化端到端
# ============================================================
class TestTokenOptimizerE2E:
    def test_token_optimizer_integration_success(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = _FakeTokenOptimizer(payload={
            "content": "OPTIMIZED",
            "before_tokens": 100, "after_tokens": 50,
            "saved_tokens": 50, "compression_ratio": 0.5,
        })
        p = RuntimePipeline(orchestrator=orch, token_optimizer=opt)
        ctx = p.run({"user_message": "raw"})
        # Orchestrator 收到的是优化后内容
        assert orch.calls == ["OPTIMIZED"]
        # token_usage 写入 outputs
        assert "token_usage" in ctx.outputs["snapshot"]

    def test_token_optimizer_disabled_default(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch)
        ctx = p.run({"user_message": "raw"})
        assert "token_usage" not in ctx.outputs.get("snapshot", {})
        # 原始 user_message 直送 orchestrator
        assert orch.calls == ["raw"]

    def test_token_optimizer_fallback_on_exception(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = _FakeTokenOptimizer(raise_exc=RuntimeError("boom"))
        p = RuntimePipeline(orchestrator=orch, token_optimizer=opt)
        ctx = p.run({"user_message": "raw"})
        # 异常 -> 原始 user_message 直送
        assert orch.calls == ["raw"]
        # token_usage 仍写入,记录 fallback_reason
        usage = ctx.outputs["snapshot"].get("token_usage", {})
        assert usage.get("applied") is False
        assert "optimizer_exception" in (usage.get("fallback_reason") or "")

    def test_token_optimizer_history_memories_passed_through(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = _FakeTokenOptimizer()
        p = RuntimePipeline(orchestrator=orch, token_optimizer=opt)
        p.run({
            "user_message": "m",
            "history": [{"role": "user", "content": "old"}],
            "memories": [{"content": "mem1"}],
        })
        assert len(opt.calls) == 1
        call = opt.calls[0]
        assert call["user_message"] == "m"
        assert call["history"] == [{"role": "user", "content": "old"}]
        assert call["memories"] == [{"content": "mem1"}]


# ============================================================
# 3. TestPersistenceE2E —— 持久化钩子真实落盘
# ============================================================
class TestPersistenceE2E:
    def test_persistence_real_file_written(self, orch, storage, tmp_storage_path):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        hook = RuntimePersistenceHook(storage)
        p = RuntimePipeline(orchestrator=orch, persistence_hook=hook)
        ctx = p.run({"user_message": "persist me"})
        # 文件真实存在
        saved = storage.saved[0]
        path = Path(saved["path"])
        assert path.exists()
        # 文件内容可读且包含 lifecycle_id
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["lifecycle_id"] == ctx.lifecycle_id
        assert data["state"] == "success"

    def test_persistence_includes_token_usage_when_enabled(
        self, orch, storage, tmp_storage_path,
    ):
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        hook = RuntimePersistenceHook(storage)
        opt = _FakeTokenOptimizer()
        p = RuntimePipeline(
            orchestrator=orch, persistence_hook=hook, token_optimizer=opt,
        )
        ctx = p.run({"user_message": "hi"})
        path = Path(storage.saved[0]["path"])
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 持久化中保留 token_usage
        assert "token_usage" in data.get("outputs", {}).get("snapshot", {})

    def test_persistence_failure_does_not_break_pipeline(self, orch):
        """persistence 抛错,Pipeline 仍正常返回 success(persistence fallback)。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        bad = _FakeStorage(
            base_dir=str(PROJECT_ROOT / "data" / "_e2e_tmp_should_not_exist"),
            raise_exc=RuntimeError("disk full"),
        )
        hook = RuntimePersistenceHook(bad)
        p = RuntimePipeline(orchestrator=orch, persistence_hook=hook)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert hook.failure_count == 1


# ============================================================
# 4. TestEventSinkE2E —— 事件发布
# ============================================================
class TestEventSinkE2E:
    def test_event_sink_receives_completion_event(self, orch, sink):
        from src.runtime.runtime_pipeline import RuntimePipeline
        p = RuntimePipeline(orchestrator=orch, event_sink=sink)
        ctx = p.run({"user_message": "hi"})
        assert sink.call_count == 1
        ev = sink.events[0]
        assert ev["event_type"] == "runtime.pipeline.completed"
        assert ev["lifecycle_id"] == ctx.lifecycle_id
        assert ev["state"] == "success"

    def test_event_sink_failure_does_not_break_pipeline(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline
        sink = _FakeEventSink(raise_exc=RuntimeError("sink down"))
        p = RuntimePipeline(orchestrator=orch, event_sink=sink)
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"

    def test_event_sink_missing_emit_is_skipped(self, orch):
        from src.runtime.runtime_pipeline import RuntimePipeline

        class _BadSink:
            pass

        p = RuntimePipeline(orchestrator=orch, event_sink=_BadSink())
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"


# ============================================================
# 5. TestHTTPChatE2E —— Flask /chat 真实请求
# ============================================================
class TestHTTPChatE2E:
    def _build_app(self, pipeline):
        from src.runtime.pipeline_server import create_app
        return create_app(pipeline=pipeline)

    def test_http_chat_returns_reply(self, pipeline_factory):
        app = self._build_app(pipeline_factory)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "你好羽依"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reply"]
        assert data["runtime_state"] == "success"
        assert "lifecycle_id" in data

    def test_http_chat_returns_lifecycle_id(self, pipeline_factory):
        app = self._build_app(pipeline_factory)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        data = resp.get_json()
        assert data["lifecycle_id"].startswith("pipeline_")

    def test_http_chat_includes_token_usage(self, pipeline_factory):
        app = self._build_app(pipeline_factory)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "x" * 200})
        data = resp.get_json()
        # Phase 6.5: token_usage 透传到 HTTP 响应
        assert "token_usage" in data
        usage = data["token_usage"]
        assert usage["before_tokens"] == 200
        assert usage["saved_tokens"] == 100

    def test_http_chat_validation_rejects_missing_message(self, pipeline_factory):
        app = self._build_app(pipeline_factory)
        client = app.test_client()
        resp = client.post("/chat", json={})
        assert resp.status_code == 400


# ============================================================
# 6. TestRuntimeStatusEndpoint —— /runtime/status
# ============================================================
class TestRuntimeStatusEndpoint:
    def test_status_returns_4_keys(self, pipeline_factory):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=pipeline_factory)
        client = app.test_client()
        resp = client.get("/runtime/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert set(data.keys()) == {
            "runtime", "pipeline", "persistence", "token_optimizer",
        }

    def test_status_all_true_when_fully_wired(self, pipeline_factory):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=pipeline_factory)
        client = app.test_client()
        data = client.get("/runtime/status").get_json()
        assert data == {
            "runtime": "ready",
            "pipeline": True,
            "persistence": True,
            "token_optimizer": True,
        }

    def test_status_pipeline_true_even_without_extras(self, orch):
        """最简 pipeline(只带 orchestrator) -> pipeline=true,其他 false。"""
        from src.runtime.pipeline_server import create_app
        from src.runtime.runtime_pipeline import RuntimePipeline
        pl = RuntimePipeline(orchestrator=orch)
        app = create_app(pipeline=pl)
        client = app.test_client()
        data = client.get("/runtime/status").get_json()
        assert data["pipeline"] is True
        assert data["persistence"] is False
        assert data["token_optimizer"] is False

    def test_status_contains_no_business_data(self, pipeline_factory):
        """/runtime/status 不应包含任何业务数据(只暴露 4 个 bool/ready 字段)。"""
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=pipeline_factory)
        client = app.test_client()
        data = client.get("/runtime/status").get_json()
        # 严禁包含:memory / personality / growth / emotion / relationship / user 字段
        forbidden = [
            "memories", "personality", "growth", "emotion",
            "relationship", "user_message", "reply", "self_model",
            "history", "user_id",
        ]
        for f in forbidden:
            assert f not in data, f"/runtime/status 不应暴露: {f}"


# ============================================================
# 7. TestSmokeMessage —— 真实聊天链路 smoke
# ============================================================
class TestSmokeMessage:
    """最小 smoke test:真实聊天链路对"你好羽依,介绍一下自己"的响应。"""

    SMOKE_MESSAGE = "你好羽依，介绍一下自己"

    def test_smoke_via_pipeline(self, pipeline_factory):
        """通过 RuntimePipeline.run() 验证 smoke 流程。"""
        orch = pipeline_factory.orchestrator  # type: ignore[attr-defined]
        # 强制 orchestrator 返回值
        orch._reply = "你好呀~ 我是羽依,一个温柔可爱的 AI 伙伴~"
        ctx = pipeline_factory.run({"user_message": self.SMOKE_MESSAGE})
        # reply 非空
        assert ctx.outputs["snapshot"]["reply"], "reply 应非空"
        # runtime_state == success
        assert ctx.state == "success"
        # lifecycle_id 存在
        assert ctx.lifecycle_id
        assert ctx.lifecycle_id.startswith("pipeline_")
        # runtime_context json 可生成
        j = json.dumps(ctx.to_dict(), ensure_ascii=False, default=str)
        assert ctx.lifecycle_id in j
        # token_usage 存在(pipeline_factory 注入了 token_opt)
        assert "token_usage" in ctx.outputs["snapshot"]
        # 旧 API 不受影响(inputs.user_message 仍保留)
        assert ctx.inputs["user_message"] == self.SMOKE_MESSAGE

    def test_smoke_via_http(self, pipeline_factory):
        """通过 HTTP /chat 验证 smoke 流程。"""
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=pipeline_factory)
        client = app.test_client()
        resp = client.post("/chat", json={"message": self.SMOKE_MESSAGE})
        assert resp.status_code == 200
        data = resp.get_json()
        # 返回 reply 非空
        assert data["reply"]
        # runtime_state == success
        assert data["runtime_state"] == "success"
        # lifecycle_id 存在
        assert data["lifecycle_id"].startswith("pipeline_")
        # token_usage 存在(启用时)
        assert "token_usage" in data
        # 不影响旧 API(reply 字段一直在)
        assert "reply" in data
        assert "lifecycle_id" in data
        assert "runtime_state" in data
        assert "timestamp" in data


# ============================================================
# 8. TestProductionStartup —— 生产启动验证
# ============================================================
class TestProductionStartup:
    def test_pipeline_server_module_imports(self):
        """pipeline_server 模块可被独立 import(不依赖业务 Authority)。"""
        # 动态 import 验证模块可加载
        mod = importlib.import_module("src.runtime.pipeline_server")
        assert hasattr(mod, "create_app")
        assert hasattr(mod, "run")
        assert hasattr(mod, "default_pipeline_factory")

    def test_pipeline_server_cli_help(self):
        """python -m src.runtime.pipeline_server --help 可运行。"""
        result = subprocess.run(
            [sys.executable, "-m", "src.runtime.pipeline_server", "--help"],
            capture_output=True, text=True, timeout=20,
            cwd=str(PROJECT_ROOT),
        )
        # argparse 退出码 0
        assert result.returncode == 0, (
            f"--help 失败: stdout={result.stdout}, stderr={result.stderr}"
        )
        # 应输出 usage 信息
        assert "usage" in result.stdout.lower() or "options" in result.stdout.lower()


# ============================================================
# 9. TestIsolationGuarantees —— 隔离/降级保证
# ============================================================
class TestIsolationGuarantees:
    """Phase 6.6 关键保证:runtime isolation / no llm replacement /
    persistence fallback / token fallback。"""

    def test_runtime_isolation_no_authority_import(self):
        """Runtime 编排层不 import 业务 Authority。"""
        from src.runtime import runtime_pipeline as mod
        import ast
        tree = ast.parse(open(mod.__file__, "r", encoding="utf-8").read())
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
        joined = "\n".join(names)
        for forbidden in [
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship", "src.audit",
        ]:
            assert forbidden not in joined, (
                f"runtime_pipeline 仍不 import: {forbidden}"
            )

    def test_no_llm_replacement_in_pipeline(self):
        """runtime_pipeline 不调用 openai / anthropic / claude / gpt 等 LLM 客户端。"""
        from src.runtime import runtime_pipeline as mod
        import ast
        tree = ast.parse(open(mod.__file__, "r", encoding="utf-8").read())
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    names.append(cur.id)
        joined = "\n".join(names).lower()
        for forbidden in ["openai", "anthropic", "claude", "chatgpt", "gpt-4", "gpt-3"]:
            assert forbidden not in joined, f"runtime_pipeline 禁止 LLM 关键词: {forbidden}"

    def test_persistence_fallback_does_not_crash(self, orch):
        """persistence 抛错时,Pipeline 仍正常返回 success,失败被隔离。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        # 构造一个必定抛错的 storage
        class _BoomStorage:
            def save(self, context):
                raise IOError("disk full simulation")

        hook = RuntimePersistenceHook(_BoomStorage())
        p = RuntimePipeline(orchestrator=orch, persistence_hook=hook)
        ctx = p.run({"user_message": "x"})
        assert ctx.state == "success"
        # 失败计数 1
        assert hook.failure_count == 1

    def test_token_optimizer_fallback_returns_raw_message(self, orch):
        """token_optimizer 抛错时,Orchestrator 收到原始 user_message(不破主流程)。"""
        from src.runtime.runtime_pipeline import RuntimePipeline
        opt = _FakeTokenOptimizer(raise_exc=ValueError("optimizer crash"))
        p = RuntimePipeline(orchestrator=orch, token_optimizer=opt)
        ctx = p.run({"user_message": "original message"})
        # 主流程成功
        assert ctx.state == "success"
        # 原始 message 直送
        assert orch.calls == ["original message"]
        # token_usage 记录 fallback
        usage = ctx.outputs["snapshot"]["token_usage"]
        assert usage["applied"] is False
        assert "optimizer" in (usage.get("fallback_reason") or "").lower()

    def test_combined_failure_only_marks_runtime_failed(self, tmp_storage_path):
        """orchestrator 失败 + persistence 失败 + token 失败 -> 仍返回 failed context。"""
        from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = _FakeOrchestrator(reply="")
        storage = _FakeStorage(
            base_dir=tmp_storage_path, raise_exc=RuntimeError("disk"),
        )
        opt = _FakeTokenOptimizer(raise_exc=RuntimeError("token"))
        p = RuntimePipeline(
            orchestrator=orch,
            persistence_hook=RuntimePersistenceHook(storage),
            token_optimizer=opt,
        )
        ctx = p.run({"user_message": "y"})
        assert ctx.state == "failed"
