# -*- coding: utf-8 -*-
"""v1.4 Phase A: LLM Reliability Hardening 测试(测试先行)。

验收场景(任务书):
1. 长回复测试: 长内容完整透传
2. DeepSeek 慢响应测试: 首次超时/连接异常 → 自动重试成功
3. 空 content 测试: 首次空 → 重试成功; 连续空 → 空串 + 分类日志
4. LLM 错误分类日志: timeout / connection / http / unknown 分类

红线: 不碰 Prompt/Memory/Temporal/Goal; 失败不再向用户泄露错误字符串。
"""

import ast
import os
import py_compile
from pathlib import Path

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[1])


# ============================================================
# 假客户端
# ============================================================
class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)  # ["ok:...", "timeout", "empty", ...]
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = self.script.pop(0) if self.script else "ok:fallback"
        if step == "timeout":
            raise _fake_timeout()
        if step == "conn":
            raise _fake_conn()
        if step == "http500":
            raise _fake_status(500)
        if step == "empty":
            return _FakeResponse("")
        return _FakeResponse(step)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeOpenAI:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


class _FakeTimeoutError(Exception):
    pass


class _FakeConnError(Exception):
    pass


class _FakeStatusError(Exception):
    def __init__(self, code):
        self.status_code = code
        super().__init__(f"http {code}")


def _fake_timeout():
    # 复用 openai SDK 异常类型(若可用); 不可用则用假类
    try:
        import openai

        return openai.APITimeoutError(request=None)
    except Exception:
        return _FakeTimeoutError()


def _fake_conn():
    try:
        import openai

        return openai.APIConnectionError(request=None)
    except Exception:
        return _FakeConnError()


def _fake_status(code):
    try:
        import openai

        return openai.APIStatusError("msg", response=None, body=None)
    except Exception:
        return _FakeStatusError(code)


def _make_client(script, timeout=None, max_retries=None):
    from src.response.llm import LLMClient

    # 注入式构造: 不触真实 OpenAI/API Key
    llm = LLMClient.__new__(LLMClient)
    llm.client = _FakeOpenAI(_FakeCompletions(script))
    llm.model = "deepseek-v4-pro"
    llm.temperature = 0.7
    llm.max_tokens = 4096
    if timeout is not None:
        llm.timeout = timeout
    else:
        llm.timeout = 120
    if max_retries is not None:
        llm.max_retries = max_retries
    else:
        llm.max_retries = 2
    return llm


# ============================================================
# 1. 长回复完整透传
# ============================================================
def test_long_reply_passed_through_intact():
    _long = "羽依" * 2000  # 4000 字符长回复
    _llm = _make_client([f"ok:{_long}"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == f"ok:{_long}"
    assert len(_out) > 4000


# ============================================================
# 2. 慢响应/超时 → 自动重试成功
# ============================================================
def test_timeout_retry_then_success():
    _llm = _make_client(["timeout", "ok:恢复成功"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == "ok:恢复成功"
    assert _llm.client.chat.completions.calls.__len__() == 2  # 两次调用


def test_connection_error_retry_then_success():
    _llm = _make_client(["conn", "ok:连接恢复"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == "ok:连接恢复"


def test_http5xx_retry_then_success():
    _llm = _make_client(["http500", "ok:服务恢复"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == "ok:服务恢复"


# ============================================================
# 3. 空 content 恢复
# ============================================================
def test_empty_content_retry_then_success():
    _llm = _make_client(["empty", "ok:第二次有内容"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == "ok:第二次有内容"


def test_persistent_empty_returns_empty_string(caplog):
    _llm = _make_client(["empty", "empty", "empty"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == ""  # 不再返回假回复, 上层走兜底
    assert any("llm_empty_content" in r.message for r in caplog.records)


# ============================================================
# 4. 错误分类日志
# ============================================================
def test_timeout_classified_log(caplog):
    _llm = _make_client(["timeout", "timeout", "timeout"])
    _llm.generate([{"role": "user", "content": "hi"}])
    assert any("llm_timeout" in r.message for r in caplog.records)


def test_unknown_error_classified_log(caplog):
    class _Weird(Exception):
        pass

    def _raise_weird(**kwargs):
        raise _Weird("奇怪错误")

    _llm = _make_client(["ok:x"])
    _llm.client.chat.completions.create = _raise_weird
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert _out == ""
    assert any("llm_unknown" in r.message for r in caplog.records)


def test_failure_never_leaks_error_text_to_user(caplog):
    # 失败回复不得包含异常信息(此前"走神了...错误:xxx"会泄露内部错误)
    _llm = _make_client(["timeout", "timeout", "timeout"])
    _out = _llm.generate([{"role": "user", "content": "hi"}])
    assert "Error" not in _out
    assert "超时" not in _out  # 内部错误词不出现
    assert _out == ""


# ============================================================
# Python 3.11
# ============================================================
_PA_MODULE_FILES = ("src/response/llm.py",)


def test_py_compile_llm_module():
    py_compile.compile(os.path.join(_REPO_ROOT, "src/response/llm.py"), doraise=True)


def test_llm_module_python311_grammar():
    with open(os.path.join(_REPO_ROOT, "src/response/llm.py"), "r", encoding="utf-8") as f:
        _src = f.read()
    ast.parse(_src, filename="src/response/llm.py", feature_version=(3, 11))
