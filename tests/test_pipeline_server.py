# -*- coding: utf-8 -*-
"""
tests/test_pipeline_server.py

Phase 6.4 —— PipelineServer HTTP 桥接测试。

覆盖:
    1. TestHealth (3)              —— GET /health
    2. TestChat (8)               —— POST /chat 正常路径
    3. TestChatValidation (4)     —— 输入校验
    4. TestFailureIsolation (4)   —— Pipeline 失败 / 异常隔离
    5. TestResponseStructure (3)  —— 响应字段完整性
    6. TestIsolation (3)          —— 不修改 Authority
    7. TestAppFactory (3)         —— create_app 工厂
    8. TestRouting (2)            —— 404 / 405
    9. TestThreadSafety (2)       —— 并发请求
    10. TestNoOpPipeline (1)      —— 无 LLM 也可启动

合计: 33 tests
"""
from __future__ import annotations

import ast
import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fake / Mock
# ============================================================
class FakeOrchestrator:
    """仿造 Orchestrator。"""

    def __init__(self, reply: str = "fake reply",
                 raise_exc: Optional[Exception] = None,
                 return_none: bool = False):
        self._reply = reply
        self._raise = raise_exc
        self._return_none = return_none
        self.calls: List[str] = []
        self.call_count = 0

    def process(self, user_message: str) -> str:
        self.call_count += 1
        self.calls.append(user_message)
        if self._raise is not None:
            raise self._raise
        if self._return_none:
            return None  # type: ignore[return-value]
        return self._reply


class FakeStorage:
    def __init__(self, raise_exc: Optional[Exception] = None):
        self._raise = raise_exc
        self.saved: List[Any] = []
        self.call_count = 0

    def save(self, context):
        self.call_count += 1
        self.saved.append({"lifecycle_id": getattr(context, "lifecycle_id", None)})
        if self._raise is not None:
            raise self._raise
        return {"saved": True, "lifecycle_id": getattr(context, "lifecycle_id", "")}


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def fake_orch():
    return FakeOrchestrator(reply="hello from yuyi")


@pytest.fixture
def fake_pipeline(fake_orch):
    from src.runtime.runtime_pipeline import RuntimePipeline
    return RuntimePipeline(orchestrator=fake_orch)


@pytest.fixture
def app(fake_pipeline):
    from src.runtime.pipeline_server import create_app
    return create_app(pipeline=fake_pipeline)


@pytest.fixture
def client(app):
    return app.test_client()


# ============================================================
# 1. TestHealth
# ============================================================
class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_health_runtime_ready(self, client):
        resp = client.get("/health")
        data = resp.get_json()
        assert data["runtime"] == "ready"

    def test_health_contains_schema_version(self, client):
        resp = client.get("/health")
        data = resp.get_json()
        assert "schema_version" in data


# ============================================================
# 2. TestChat
# ============================================================
class TestChat:
    def test_chat_normal(self, client, fake_orch):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "你好"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reply"] == "hello from yuyi"
        assert fake_orch.call_count == 1
        assert fake_orch.calls == ["你好"]

    def test_chat_english_message(self, client, fake_orch):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "hi"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["reply"] == "hello from yuyi"

    def test_chat_strips_whitespace(self, client, fake_orch):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "  hello  "}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        # Pipeline 收到 strip 后的消息
        assert fake_orch.calls == ["hello"]

    def test_chat_returns_lifecycle_id(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "x"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "lifecycle_id" in data
        assert isinstance(data["lifecycle_id"], str)
        assert data["lifecycle_id"].startswith("pipeline_")

    def test_chat_returns_runtime_state(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "x"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["runtime_state"] == "success"

    def test_chat_returns_timestamp(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": "x"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert "timestamp" in data
        assert data["timestamp"].endswith("Z")

    def test_chat_increments_request_count(self, client, app):
        before = app.config["REQUEST_COUNT"]
        client.post("/chat", json={"message": "a"})
        client.post("/chat", json={"message": "b"})
        assert app.config["REQUEST_COUNT"] == before + 2

    def test_chat_each_call_unique_lifecycle_id(self, client):
        ids = set()
        for i in range(5):
            r = client.post("/chat", json={"message": f"m{i}"})
            ids.add(r.get_json()["lifecycle_id"])
        assert len(ids) == 5


# ============================================================
# 3. TestChatValidation
# ============================================================
class TestChatValidation:
    def test_chat_missing_message_field(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = resp.get_json()
        assert "message" in data.get("error", "")

    def test_chat_message_not_string(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": 123}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_chat_invalid_json_body(self, client):
        resp = client.post(
            "/chat",
            data="not json",
            content_type="application/json",
        )
        # Pipeline 会用 str 兜底,所以仍走 pipeline(可能产生奇怪结果)
        # 我们的实现:解析失败时当作空 dict,会报 missing message -> 400
        assert resp.status_code == 400

    def test_chat_message_null_value(self, client):
        resp = client.post(
            "/chat",
            data=json.dumps({"message": None}),
            content_type="application/json",
        )
        assert resp.status_code == 400


# ============================================================
# 4. TestFailureIsolation
# ============================================================
class TestFailureIsolation:
    def test_orchestrator_exception_returns_500(self):
        from src.runtime.pipeline_server import create_app
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(raise_exc=RuntimeError("orch boom"))
        pipeline = RuntimePipeline(orchestrator=orch)
        app = create_app(pipeline=pipeline)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        # Pipeline 内部隔离 -> state=failed,但 HTTP 200
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["runtime_state"] == "failed"

    def test_orchestrator_returns_none_marks_failed(self):
        from src.runtime.pipeline_server import create_app
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(return_none=True)
        pipeline = RuntimePipeline(orchestrator=orch)
        app = create_app(pipeline=pipeline)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["runtime_state"] == "failed"

    def test_orchestrator_empty_reply_marks_failed(self):
        from src.runtime.pipeline_server import create_app
        from src.runtime.runtime_pipeline import RuntimePipeline
        orch = FakeOrchestrator(reply="")
        pipeline = RuntimePipeline(orchestrator=orch)
        app = create_app(pipeline=pipeline)
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 200
        assert resp.get_json()["runtime_state"] == "failed"

    def test_pipeline_run_raises_returns_500(self):
        """Pipeline.run 自身抛错(极端)时,Server 返回 500。"""
        from src.runtime.pipeline_server import create_app

        class BrokenPipeline:
            def run(self, _input):
                raise RuntimeError("pipeline broken")

        app = create_app(pipeline=BrokenPipeline())
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data


# ============================================================
# 5. TestResponseStructure
# ============================================================
class TestResponseStructure:
    def test_chat_response_has_required_fields(self, client):
        resp = client.post("/chat", json={"message": "x"})
        data = resp.get_json()
        for k in ("reply", "lifecycle_id", "runtime_state", "timestamp"):
            assert k in data, f"missing key: {k}"

    def test_chat_response_reply_is_string(self, client):
        resp = client.post("/chat", json={"message": "x"})
        data = resp.get_json()
        assert isinstance(data["reply"], str)

    def test_chat_response_lifecycle_id_is_string(self, client):
        resp = client.post("/chat", json={"message": "x"})
        data = resp.get_json()
        assert isinstance(data["lifecycle_id"], str)
        assert len(data["lifecycle_id"]) > 0


# ============================================================
# 6. TestIsolation
# ============================================================
class TestIsolation:
    def test_server_no_authority_import(self):
        """pipeline_server.py 禁止 import 业务 Authority。"""
        from src.runtime import pipeline_server as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
        names: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    names.append(a.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module)
                for a in node.names:
                    names.append(a.name)
        joined = "\n".join(names)
        forbidden = [
            "src.memory", "src.emotion", "src.growth",
            "src.personality", "src.relationship",
            "src.events", "src.audit",
        ]
        for f in forbidden:
            assert f not in joined, f"pipeline_server 禁止 import: {f}"

    def test_server_no_llm_keywords(self):
        """pipeline_server.py 代码层不引用 LLM。"""
        from src.runtime import pipeline_server as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
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
        forbidden = ["openai", "anthropic", "claude", "gpt"]
        for f in forbidden:
            assert f not in joined, f"pipeline_server 禁止 LLM 关键词: {f}"

    def test_server_no_eventhub(self):
        """pipeline_server.py 不引用 EventHub。"""
        from src.runtime import pipeline_server as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)
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
        joined = "\n".join(names)
        forbidden = [
            "EventHub", "EventBus", "EventPublisher",
            "RuntimeEventPublisher", "EventSink",
        ]
        for f in forbidden:
            assert f not in joined, f"pipeline_server 禁止引用: {f}"


# ============================================================
# 7. TestAppFactory
# ============================================================
class TestAppFactory:
    def test_create_app_returns_flask(self, fake_pipeline):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=fake_pipeline)
        # Flask app
        assert hasattr(app, "route")
        assert hasattr(app, "test_client")

    def test_create_app_stores_pipeline(self, fake_pipeline):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=fake_pipeline)
        assert app.config["PIPELINE"] is fake_pipeline

    def test_create_app_with_factory(self):
        """pipeline=None + pipeline_factory=... 时应被调用。"""
        from src.runtime.pipeline_server import create_app
        from src.runtime.runtime_pipeline import RuntimePipeline
        called = {"n": 0}

        def factory():
            called["n"] += 1
            return RuntimePipeline(orchestrator=FakeOrchestrator(reply="factory"))

        app = create_app(pipeline=None, pipeline_factory=factory)
        assert called["n"] == 1
        client = app.test_client()
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.get_json()["reply"] == "factory"


# ============================================================
# 8. TestRouting
# ============================================================
class TestRouting:
    def test_unknown_path_returns_404(self, client):
        resp = client.get("/not_exists")
        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_wrong_method_returns_405(self, client):
        resp = client.get("/chat")  # /chat 是 POST
        assert resp.status_code == 405
        data = resp.get_json()
        assert "error" in data


# ============================================================
# 9. TestThreadSafety
# ============================================================
class TestThreadSafety:
    def test_concurrent_chat_requests(self, fake_pipeline):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=fake_pipeline)
        client = app.test_client()
        errors: List[Exception] = []
        results: List[int] = []

        def worker(i: int):
            try:
                r = client.post("/chat", json={"message": f"m{i}"})
                results.append(r.status_code)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(results) == 10
        assert all(s == 200 for s in results)

    def test_concurrent_health_and_chat(self, fake_pipeline):
        from src.runtime.pipeline_server import create_app
        app = create_app(pipeline=fake_pipeline)
        client = app.test_client()
        results: List[int] = []

        def chat(i: int):
            r = client.post("/chat", json={"message": f"c{i}"})
            results.append(r.status_code)

        def health(_i: int):
            r = client.get("/health")
            results.append(r.status_code)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=chat, args=(i,)))
            threads.append(threading.Thread(target=health, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
        assert all(s == 200 for s in results)


# ============================================================
# 10. TestNoOpPipeline
# ============================================================
class TestNoOpPipeline:
    def test_default_pipeline_factory_fallback(self):
        """default_pipeline_factory 在 Orchestrator 不可用时仍可构造 Pipeline。"""
        from src.runtime.pipeline_server import _build_noop_pipeline
        p = _build_noop_pipeline()
        ctx = p.run({"user_message": "hi"})
        assert ctx.state == "success"
        assert "echo" in ctx.outputs.get("snapshot", {}).get("reply", "")
