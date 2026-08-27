# -*- coding: utf-8 -*-
"""
tests/test_memory_render_order.py

Phase B1c.4 — 记忆渲染排序修正测试。

覆盖：
    - 5 旧 + 5 新 → 渲染 top-5 必为最新 5 条
    - 无序 timestamp → 正确排序
    - 非法 timestamp → 不 crash（置后）
    - 缺 timestamp → 不 crash（置后）
    - memory_time_labels=off 下逐字节兼容（旧行格式不变）
    - content 截断长度保持不变
    - 纯函数不修改输入
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.temporal.temporal_context import sort_memories_recent_first

UTC = timezone.utc
NOW = datetime.now(UTC)


def _mem(ts, content):
    return {"role": "user", "content": content, "timestamp": ts}


def _iso(dt):
    return dt.isoformat()


# ═════════════════════════════════════════════════════════
# 纯函数排序
# ═════════════════════════════════════════════════════════
class TestSortFunction:
    def test_old_plus_new_returns_newest_first(self):
        old = [_mem(_iso(NOW - timedelta(days=d)), f"old{d}") for d in range(10, 5, -1)]
        new = [_mem(_iso(NOW - timedelta(hours=h)), f"new{h}") for h in range(5, 0, -1)]
        ordered = sort_memories_recent_first(old + new)
        first5 = [m["content"] for m in ordered[:5]]
        assert first5 == ["new1", "new2", "new3", "new4", "new5"]

    def test_unsorted_input_sorted(self):
        mems = [
            _mem(_iso(NOW - timedelta(days=3)), "d3"),
            _mem(_iso(NOW - timedelta(days=1)), "d1"),
            _mem(_iso(NOW - timedelta(days=5)), "d5"),
        ]
        ordered = sort_memories_recent_first(mems)
        assert [m["content"] for m in ordered] == ["d1", "d3", "d5"]

    def test_invalid_timestamp_goes_last_no_crash(self):
        mems = [
            _mem(_iso(NOW - timedelta(days=2)), "ok2"),
            _mem("garbage", "bad"),
            _mem(_iso(NOW - timedelta(days=1)), "ok1"),
        ]
        ordered = sort_memories_recent_first(mems)
        contents = [m["content"] for m in ordered]
        assert contents == ["ok1", "ok2", "bad"]

    def test_missing_timestamp_goes_last_no_crash(self):
        mems = [
            {"role": "user", "content": "no-ts"},
            _mem(_iso(NOW - timedelta(days=1)), "ok1"),
        ]
        ordered = sort_memories_recent_first(mems)
        assert [m["content"] for m in ordered] == ["ok1", "no-ts"]

    def test_same_timestamp_stable_order(self):
        ts = _iso(NOW - timedelta(days=1))
        mems = [_mem(ts, "a"), _mem(ts, "b"), _mem(ts, "c")]
        ordered = sort_memories_recent_first(mems)
        assert [m["content"] for m in ordered] == ["a", "b", "c"]

    def test_input_not_mutated(self):
        mems = [_mem(_iso(NOW - timedelta(days=2)), "x"), _mem(_iso(NOW - timedelta(days=1)), "y")]
        snapshot = [dict(m) for m in mems]
        sort_memories_recent_first(mems)
        assert mems == snapshot

    def test_empty(self):
        assert sort_memories_recent_first([]) == []
        assert sort_memories_recent_first(None) == []


# ═════════════════════════════════════════════════════════
# engine.py 渲染（生产链）
# ═════════════════════════════════════════════════════════
class TestEngineRenderOrder:
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

    def test_top5_are_newest(self):
        # v1.5.5 Memory Recall Fix: engine 不再隐式排序——原样渲染 selection 传入顺序，
        # 仅硬上限 6 截断（新1-5 + 旧10 补第 6 位；旧9 起被截）。
        old = [_mem(_iso(NOW - timedelta(days=d)), f"旧记忆{d}") for d in range(10, 5, -1)]
        new = [_mem(_iso(NOW - timedelta(hours=h)), f"新记忆{h}") for h in range(5, 0, -1)]
        system = self._system(new + old)
        for h in range(1, 6):
            assert f"新记忆{h}" in system
        assert "旧记忆9" not in system  # 第 7 位起被硬上限截掉
        assert "旧记忆6" not in system

    def test_off_state_old_line_format(self, monkeypatch):
        # memory_time_labels=off（默认）→ 行格式与旧版一致（无时间标签）
        import src.config as cfg

        monkeypatch.setattr(cfg, "_config", {"temporal": {"memory_time_labels": "off"}})
        old = [_mem(_iso(NOW - timedelta(days=d)), f"旧内容{d}") for d in range(10, 5, -1)]
        new = [_mem(_iso(NOW - timedelta(hours=h)), f"新内容{h}") for h in range(5, 0, -1)]
        system = self._system(new + old)
        for h in range(1, 6):
            assert f"  用户: 新内容{h}" in system
        assert "旧内容9" not in system
        assert "旧内容6" not in system
        assert "[" not in system.split("【相关记忆】")[1]

    def test_invalid_timestamp_no_crash(self):
        mems = [
            _mem("bad-ts", "非法时间记忆"),
            _mem(_iso(NOW - timedelta(hours=1)), "正常记忆"),
        ]
        system = self._system(mems)
        assert "正常记忆" in system

    def test_content_truncation_unchanged(self):
        long_content = "y" * 300
        mems = [_mem(_iso(NOW - timedelta(hours=1)), long_content)]
        system = self._system(mems)
        assert long_content[:150] in system
        assert "y" * 151 not in system


# ═════════════════════════════════════════════════════════
# prompt_builder 备链
# ═════════════════════════════════════════════════════════
class TestPromptBuilderOrder:
    def test_top5_are_newest(self):
        # v1.5.5 Memory Recall Fix: prompt_builder 不再隐式排序——原样渲染传入顺序，
        # 仅硬上限 6 截断。
        from src.response.prompt_builder import PromptBuilder

        old = [_mem(_iso(NOW - timedelta(days=d)), f"旧{d}") for d in range(10, 5, -1)]
        new = [_mem(_iso(NOW - timedelta(hours=h)), f"新{h}") for h in range(5, 0, -1)]
        text = PromptBuilder()._format_chat_memories(new + old)
        for h in range(1, 6):
            assert f"新{h}" in text
        assert "旧9" not in text
        assert "旧6" not in text

    def test_invalid_timestamp_no_crash(self):
        from src.response.prompt_builder import PromptBuilder

        text = PromptBuilder()._format_chat_memories(
            [_mem("bad", "非法"), _mem(_iso(NOW - timedelta(hours=1)), "正常")]
        )
        assert "正常" in text
