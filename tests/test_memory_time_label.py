# -*- coding: utf-8 -*-
"""
tests/test_memory_time_label.py

Phase B1c.1 — 记忆时间标签渲染测试。

覆盖：
    A. 开关：off 输出与旧版本逐字节一致 / on 出现时间标签
    B. 时间：1分钟前 / 3天前 / 1个月前 / 非法 / None / 未来
    C. 防御：不改原 memory 内容、不 crash、无 schema 写入
    D. 边界：多条 / 空 / 超长 content
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.temporal.temporal_context import format_memory_time_label

UTC = timezone.utc
NOW = datetime.now(UTC)


def _ts(offset: timedelta) -> str:
    return (NOW - offset).isoformat()


def _set_mode(monkeypatch, mode: str) -> None:
    import src.config as cfg

    monkeypatch.setattr(cfg, "_config", {"temporal": {"memory_time_labels": mode}})


# ═════════════════════════════════════════════════════════
# B. 纯函数时间测试
# ═════════════════════════════════════════════════════════
class TestFormatLabel:
    def test_minutes(self):
        assert format_memory_time_label(_ts(timedelta(minutes=1)), now=NOW) == "[1分钟前] "

    def test_days(self):
        assert format_memory_time_label(_ts(timedelta(days=3)), now=NOW) == "[3天前] "

    def test_months(self):
        assert format_memory_time_label(_ts(timedelta(days=30)), now=NOW) == "[1个月前] "

    def test_invalid(self):
        assert format_memory_time_label("garbage", now=NOW) == ""

    def test_none(self):
        assert format_memory_time_label(None, now=NOW) == ""

    def test_future_beyond_tolerance_no_label(self):
        assert format_memory_time_label((NOW + timedelta(hours=2)).isoformat(), now=NOW) == ""

    def test_future_within_tolerance(self):
        # 容差内（时钟抖动）→ "刚刚"，非未来描述
        assert format_memory_time_label((NOW + timedelta(seconds=30)).isoformat(), now=NOW) == "[刚刚] "


# ═════════════════════════════════════════════════════════
# A/C/D. engine.py 生产渲染点
# ═════════════════════════════════════════════════════════
class TestEngineRender:
    @staticmethod
    def _system(chat_memories):
        from src.engine import ResponseEngine

        eng = ResponseEngine.__new__(ResponseEngine)
        msgs = eng._build_messages_original(
            user_message="你好",
            history=[],
            chat_memories=chat_memories,
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
        )
        return msgs[0]["content"]

    def test_off_exact_old_output(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        mems = [{"role": "user", "content": "今天学习了AI项目", "timestamp": _ts(timedelta(days=3))}]
        system = self._system(mems)
        assert "【相关记忆】\n  用户: 今天学习了AI项目" in system
        assert "[3天前]" not in system

    def test_on_label_appears(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        mems = [{"role": "user", "content": "今天学习了AI项目", "timestamp": _ts(timedelta(days=3))}]
        system = self._system(mems)
        assert "【相关记忆】\n  [3天前] 用户: 今天学习了AI项目" in system

    def test_multiple_memories_each_labeled(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        mems = [
            {"role": "user", "content": "A", "timestamp": _ts(timedelta(days=3))},
            {"role": "user", "content": "B", "timestamp": _ts(timedelta(days=30))},
            {"role": "assistant", "content": "C", "timestamp": _ts(timedelta(minutes=1))},
        ]
        system = self._system(mems)
        assert "[3天前] 用户: A" in system
        assert "[1个月前] 用户: B" in system
        assert "[1分钟前] 羽依: C" in system

    def test_empty_memories_no_section(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        system = self._system([])
        assert "【相关记忆】" not in system

    def test_long_content_truncation_unchanged(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        long_content = "x" * 300
        mems = [{"role": "user", "content": long_content, "timestamp": _ts(timedelta(days=3))}]
        system = self._system(mems)
        truncated = long_content[:150]
        assert f"[3天前] 用户: {truncated}" in system
        assert "x" * 151 not in system

    def test_memory_dicts_not_mutated(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        mems = [{"role": "user", "content": "原文", "timestamp": _ts(timedelta(days=3))}]
        snapshot = dict(mems[0])
        self._system(mems)
        assert mems[0] == snapshot  # 渲染不修改原 memory 内容

    def test_invalid_timestamp_on_renders_no_label(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        mems = [{"role": "user", "content": "OK", "timestamp": "not-a-time"}]
        system = self._system(mems)
        assert "  用户: OK" in system
        assert "[" not in system.split("【相关记忆】")[1].split("\n")[0]


# ═════════════════════════════════════════════════════════
# A/C. prompt_builder 渲染点
# ═════════════════════════════════════════════════════════
class TestPromptBuilderRender:
    @staticmethod
    def _render(chat_memories):
        from src.response.prompt_builder import PromptBuilder

        return PromptBuilder()._format_chat_memories(chat_memories)

    def test_off_no_brackets(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        text = self._render(
            [{"role": "user", "content": "今天学习了AI项目", "timestamp": _ts(timedelta(days=3))}]
        )
        assert "今天学习了AI项目" in text
        assert "[" not in text

    def test_on_label_appears(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        text = self._render(
            [{"role": "user", "content": "今天学习了AI项目", "timestamp": _ts(timedelta(days=3))}]
        )
        assert "[3天前] " in text
        assert "今天学习了AI项目" in text

    def test_empty_memories(self, monkeypatch):
        _set_mode(monkeypatch, "on")
        assert self._render([]) == ""
        assert self._render(None) == ""


# ═════════════════════════════════════════════════════════
# C. 防御：无 schema 写入（渲染纯文本，无存储交互）
# ═════════════════════════════════════════════════════════
class TestDefense:
    def test_label_is_pure_text(self):
        # 标签只调用 temporal_core.relative_time；输出仅字符串，无副作用对象
        out = format_memory_time_label(_ts(timedelta(days=3)), now=NOW)
        assert isinstance(out, str)
        assert len(out) <= 12  # 长度受控（"[N个月前] " 上限）
