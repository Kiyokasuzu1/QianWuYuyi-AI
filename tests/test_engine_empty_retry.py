# -*- coding: utf-8 -*-
"""v1.5-T1: 顶层 ResponseEngine 空回复有界重试测试。

只测 HTTP 200 但 content 为空的场景；异常路径不在本测试范围。
"""
import time

import pytest

from src.engine import ResponseEngine


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _FakeMsg(content)
        self.finish_reason = finish_reason


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 20
    total_tokens = 30


class _FakeResponse:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_FakeChoice(content, finish_reason)]
        self.model = "deepseek-v4-pro"
        self.usage = _FakeUsage()


class _FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeChat:
    def __init__(self, responses):
        self.completions = _FakeCompletions(responses)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


def _make_engine(retry):
    eng = ResponseEngine.__new__(ResponseEngine)
    eng.api_key = "sk-fake"
    eng._client = None
    eng.model = "deepseek-v4-pro"
    eng.mock_mode = False
    eng._empty_retry_max = retry
    return eng


def _attach_client(eng, fake):
    """client 是只读 property，注入其底层字段 _client 即可生效。"""
    eng._client = fake
    return eng


_BASE_KW = {
    "user_message": "你好",
    "history": [],
    "chat_memories": [],
    "life_events": [],
    "personality_context": "",
    "resolved_behavior": {},
    "self_model_context": {},
    "emotion_context": {},
    "relationship_context": {},
}


def test_retries_then_returns_non_empty(monkeypatch):
    """前 2 次空 content、第 3 次非空 → 应重试 2 次并返回非空内容。"""
    eng = _make_engine(retry=2)
    fake = _FakeClient([_FakeResponse(""), _FakeResponse(""), _FakeResponse("你好")])
    _attach_client(eng, fake)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    out = eng.generate(**_BASE_KW)
    assert out == "你好"
    assert fake.chat.completions.calls == 3


def test_exhausted_returns_empty_string(monkeypatch):
    """全部返回空 → 重试耗尽后返回空串（不抛异常）。"""
    eng = _make_engine(retry=2)
    fake = _FakeClient([_FakeResponse(""), _FakeResponse(""), _FakeResponse("")])
    _attach_client(eng, fake)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    out = eng.generate(**_BASE_KW)
    assert out == ""
    assert fake.chat.completions.calls == 3


def test_retry_zero_means_single_call(monkeypatch):
    """llm.empty_content_retry=0 → 只调用一次即返回（等价旧行为）。"""
    eng = _make_engine(retry=0)
    fake = _FakeClient([_FakeResponse("")])
    _attach_client(eng, fake)
    monkeypatch.setattr(time, "sleep", lambda _: None)

    out = eng.generate(**_BASE_KW)
    assert out == ""
    assert fake.chat.completions.calls == 1


def test_first_attempt_success_no_retry(monkeypatch):
    """首次即非空 → 只调用一次。"""
    eng = _make_engine(retry=2)
    fake = _FakeClient([_FakeResponse("直接成功")])
    _attach_client(eng, fake)

    out = eng.generate(**_BASE_KW)
    assert out == "直接成功"
    assert fake.chat.completions.calls == 1


def test_exception_still_propagates_to_outer(monkeypatch):
    """异常路径保持旧行为：不重试，返回错误文本而非抛出。"""
    eng = _make_engine(retry=2)
    fake = _FakeClient([RuntimeError("boom")])
    _attach_client(eng, fake)

    out = eng.generate(**_BASE_KW)
    assert "抱歉" in out
    assert fake.chat.completions.calls == 1
