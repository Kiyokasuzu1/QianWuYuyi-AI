# -*- coding: utf-8 -*-
"""P1 LLM Empty Content 稳定性修复专项测试（T1-T7）。

覆盖：
T1 正常 content → normal
T2 第一次 empty → retry → 正常回复
T3 连续 empty → raise LLMError(llm_empty_content)，无崩溃
T4 LLM timeout → raise LLMError(llm_timeout)（不误判 empty）
T5 HTTP 200 + malformed → raise LLMError(malformed_response)
T6 日志：LLM_EMPTY_CONTENT 写入配置的日志 handler
T7 回归：pipeline audit failure_reason 写入
"""
import logging
import logging.handlers
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------- mock SDK ----------
class _Msg:
    def __init__(self, content):
        self.content = content
        self.reasoning_content = None


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content=None, choices=None):
        self.choices = choices if choices is not None else ([_Choice(content)] if content is not None else [])


class _Completions:
    def __init__(self, contents):
        # contents: list of (value, kind) kind in ("content", "malformed", "raise_timeout")
        self._items = list(contents)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if not self._items:
            raise AssertionError("mock 队列耗尽")
        item = self._items.pop(0)
        kind, value = item
        if kind == "content":
            return _Resp(content=value)
        if kind == "malformed":
            return _Resp(choices=[])
        if kind == "raise_timeout":
            raise TimeoutError("upstream timeout")


class _FakeChat:
    def __init__(self, contents):
        self.completions = _Completions(contents)


class _FakeClient:
    def __init__(self, contents):
        self.chat = _FakeChat(contents)


def _calls(llm):
    return llm.client.chat.completions.calls


def _make_llm(contents, monkeypatch):
    monkeypatch.setattr("src.response.llm.get_api_key", lambda: "test-key")
    from src.response.llm import LLMClient
    llm = LLMClient()
    llm.client = _FakeClient(contents)
    return llm


# ---------- T1 ----------
def test_t1_normal_content(monkeypatch):
    from src.response.llm import LLMClient  # noqa: F401
    llm = _make_llm([("content", "hello")], monkeypatch)
    assert llm.generate([{"role": "user", "content": "hi"}]) == "hello"
    assert _calls(llm) == 1


# ---------- T2 ----------
def test_t2_retry_recovers(monkeypatch):
    llm = _make_llm([("content", ""), ("content", "recovered")], monkeypatch)
    assert llm.generate([{"role": "user", "content": "hi"}]) == "recovered"
    assert _calls(llm) == 2


# ---------- T3 ----------
def test_t3_all_empty_raises(monkeypatch):
    from src.response.llm import LLMError
    llm = _make_llm([("content", "")] * 5, monkeypatch)
    with pytest.raises(LLMError) as ei:
        llm.generate([{"role": "user", "content": "hi"}])
    assert ei.value.category == "llm_empty_content"
    assert _calls(llm) == 3  # max_retries=2 → 3 次尝试后 raise


# ---------- T4 ----------
def test_t4_timeout_not_misclassified(monkeypatch):
    from src.response.llm import LLMError
    llm = _make_llm([("raise_timeout", None)] * 5, monkeypatch)
    with pytest.raises(LLMError) as ei:
        llm.generate([{"role": "user", "content": "hi"}])
    assert ei.value.category == "llm_timeout"
    assert _calls(llm) == 3


# ---------- T5 ----------
def test_t5_malformed_response(monkeypatch):
    from src.response.llm import LLMError
    llm = _make_llm([("malformed", None)] * 5, monkeypatch)
    with pytest.raises(LLMError) as ei:
        llm.generate([{"role": "user", "content": "hi"}])
    assert ei.value.category == "malformed_response"


# ---------- T6 ----------
def test_t6_logging_writes_to_handler(monkeypatch, tmp_path):
    """production-equivalent：RotatingFileHandler + basicConfig，warning 必须落盘。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _file = logging.handlers.RotatingFileHandler(
        str(log_dir / "api_server.log"), maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding="utf-8")
    _file.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level
    root.handlers[:] = []
    root.addHandler(_file)
    root.setLevel(logging.INFO)
    try:
        from src.response.llm import LLMError
        llm = _make_llm([("content", "")] * 5, monkeypatch)
        with pytest.raises(LLMError):
            llm.generate([{"role": "user", "content": "hi"}])
    finally:
        root.handlers[:] = old_handlers
        root.setLevel(old_level)
        _file.close()
    content = (log_dir / "api_server.log").read_text(encoding="utf-8")
    assert "llm_empty_content" in content, "LLM_EMPTY_CONTENT 未写入日志 handler"
    assert "src.response.llm" in content


# ---------- T7 ----------
def test_t7_pipeline_audit_failure_reason():
    from src.runtime.runtime_pipeline import _runtime_path_audit_finalize
    audit = {"fallback": True, "runtime_attempted": True, "runtime_succeeded": False,
             "orchestrator_invoked": True}
    out = _runtime_path_audit_finalize(
        audit, reply_source=None, reply_empty=True, duration_ms=100,
        failure_reason="llm_empty_content")
    assert out["path"] == "empty_reply"
    assert out["failure_reason"] == "llm_empty_content"

    # 非空回复不写 failure_reason
    audit2 = {"fallback": False, "runtime_succeeded": True}
    out2 = _runtime_path_audit_finalize(
        audit2, reply_source="runtime", reply_empty=False, duration_ms=50,
        failure_reason="llm_empty_content")
    assert "failure_reason" not in out2
