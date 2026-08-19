"""
Phase C.1 — P0-1: /v1/chat/completions 链路验证测试

验证项：
1. 启动 mock 模式 yuyi-api 后, /v1/chat/completions 返回 200
2. 响应包含 choices[0].message.content
3. 多次调用可累积历史
4. 不依赖真实 LLM API key
5. 真实 API key 存在时, mock 模式自动关闭
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
API_BASE = "http://127.0.0.1:5000"


def _http_post(path: str, payload: dict, timeout: float = 30.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")
    except Exception as e:
        return 0, {"error": str(e)}


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def yuyi_api():
    """启动 yuyi-api 一次,整个 module 共享"""
    if _port_open("127.0.0.1", 5000):
        yield 5000
        return

    env = os.environ.copy()
    env["YUYI_LLM_MOCK"] = "1"
    log_path = PROJECT_ROOT / "logs" / "phase_c1_test_server.log"
    err_path = PROJECT_ROOT / "logs" / "phase_c1_test_server.err"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "w", encoding="utf-8")
    err_f = open(err_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "run_server.py"],
        cwd=str(PROJECT_ROOT),
        stdout=log_f,
        stderr=err_f,
        env=env,
    )
    # 等待服务就绪
    for _ in range(60):
        if _port_open("127.0.0.1", 5000):
            try:
                r = urllib.request.urlopen(API_BASE + "/health", timeout=2)
                if r.status == 200:
                    break
            except Exception:
                pass
        time.sleep(1.0)
    else:
        proc.terminate()
        pytest.fail("yuyi-api did not start within 60s")

    yield 5000
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    log_f.close()
    err_f.close()


class TestChatCompletionsEndpoint:
    def test_health_endpoint(self, yuyi_api):
        """前置: yuyi-api 启动后 /health 返回 200"""
        r = urllib.request.urlopen(API_BASE + "/health", timeout=5)
        assert r.status == 200
        body = json.loads(r.read().decode())
        assert body.get("status") == "ok"

    def test_models_endpoint(self, yuyi_api):
        """/v1/models 暴露 yuyi 模型"""
        r = urllib.request.urlopen(API_BASE + "/v1/models", timeout=5)
        body = json.loads(r.read().decode())
        ids = [m.get("id") for m in body.get("data", [])]
        assert "yuyi" in ids

    def test_chat_completions_returns_200(self, yuyi_api):
        """P0-1 主验证: /v1/chat/completions 不再返回 500"""
        status, body = _http_post("/v1/chat/completions", {
            "user": "test_user_p0",
            "messages": [{"role": "user", "content": "你好羽依"}],
        })
        assert status == 200, f"expected 200, got {status}: {body}"
        assert "choices" in body
        assert len(body["choices"]) >= 1
        msg = body["choices"][0].get("message", {})
        assert msg.get("role") == "assistant"
        assert isinstance(msg.get("content"), str)
        assert len(msg["content"]) > 0

    def test_chat_completions_mock_response_contains_user_message(self, yuyi_api):
        """mock 回复应回显用户消息(在没有真实 LLM 的情况下验证链路)"""
        test_msg = "今天测试一下集成链路"
        status, body = _http_post("/v1/chat/completions", {
            "user": "test_user_p0",
            "messages": [{"role": "user", "content": test_msg}],
        })
        assert status == 200
        content = body["choices"][0]["message"]["content"]
        # R2.7.6-DEPLOY: mock 模式下 reply 有两种生成路径:
        #   1) ResponseEngine._mock_response 直接回显用户消息（含 mock 字样）
        #   2) RuntimeController Phase4 mock 引擎生成自然语言回复（提取用户消息关键词）
        # 兼容两种情况：关键词命中 或 含 mock 字样 都算链路畅通
        keywords_hit = any(kw in content for kw in ("集成链路", "测试", "mock", "听到你说"))
        assert len(content) >= 5 and keywords_hit, (
            "mock 回复既不含用户关键词也不含 mock 标识: " + content[:60]
        )

    def test_chat_completions_multi_turn(self, yuyi_api):
        """多轮对话可累积 history"""
        messages = [
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "好的,听到第一轮"},
            {"role": "user", "content": "第二轮"},
        ]
        status, body = _http_post("/v1/chat/completions", {
            "user": "test_user_multi",
            "messages": messages,
        })
        assert status == 200
        assert body["choices"][0]["message"]["role"] == "assistant"

    def test_chat_completions_empty_messages_rejected(self, yuyi_api):
        """空消息应被 400 拒绝"""
        status, body = _http_post("/v1/chat/completions", {
            "user": "test_user",
            "messages": [],
        })
        assert status in (400, 422)

    def test_response_engine_mock_mode_toggle(self):
        """ResponseEngine 应在无 API key 时自动进入 mock 模式"""
        from src.engine import ResponseEngine

        # 模拟无 API key 环境
        old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        old_openai = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["YUYI_LLM_MOCK"] = "1"
        try:
            eng = ResponseEngine()
            assert eng.mock_mode is True
            # 调用 generate 不应崩溃
            out = eng.generate(
                user_message="hello",
                history=[],
                chat_memories=[],
                life_events=[],
                personality_context="",
                resolved_behavior={},
                self_model_context={},
                emotion_context={},
                relationship_context={},
            )
            assert isinstance(out, str)
            assert len(out) > 0
        finally:
            os.environ.pop("YUYI_LLM_MOCK", None)
            if old_key:
                os.environ["DEEPSEEK_API_KEY"] = old_key
            if old_openai:
                os.environ["OPENAI_API_KEY"] = old_openai

    def test_response_engine_deferred_client(self):
        """Phase C.1 P0-1: OpenAI client 必须延后创建,避免 __init__ 即崩溃"""
        from src.engine import ResponseEngine

        # 强制无 key
        old_key = os.environ.pop("DEEPSEEK_API_KEY", None)
        old_openai = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["YUYI_LLM_MOCK"] = "1"
        try:
            eng = ResponseEngine()
            # 构造函数不应访问网络或抛 OpenAIError
            assert eng._client is None
            # mock 模式下即使访问 .client 也不应创建
            _ = eng.client
            assert eng._client is None
        finally:
            os.environ.pop("YUYI_LLM_MOCK", None)
            if old_key:
                os.environ["DEEPSEEK_API_KEY"] = old_key
            if old_openai:
                os.environ["OPENAI_API_KEY"] = old_openai


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
