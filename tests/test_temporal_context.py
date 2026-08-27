# -*- coding: utf-8 -*-
"""
tests/test_temporal_context.py

Phase B1b — temporal_context 组装 + 三态决策 + 接入点行为测试。

覆盖：
    - 当前时间生成（星期/时区标注）
    - last_interaction / 无历史用户 / 离线间隔计算
    - 未来时间防御
    - 400 字符预算
    - off / shadow / active 三态行为（纯函数 + engine/prompt_builder 接入 + orchestrator 静态接线）
    - 纯函数边界（静态 import / IO 检查）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from src.temporal.temporal_context import (
    DEFAULT_TEMPORAL_CONTEXT_BUDGET,
    build_temporal_context,
    resolve_temporal_context,
)

UTC = timezone.utc
# 2026-08-24 = 星期一；UTC 02:03 = CST 10:03
NOW = datetime(2026, 8, 24, 2, 3, 0, tzinfo=UTC)


# ═════════════════════════════════════════════════════════
# build_temporal_context
# ═════════════════════════════════════════════════════════
class TestBuildContext:
    def test_current_time_line_with_weekday_and_tz_label(self):
        text = build_temporal_context(NOW, None)
        assert text.startswith("当前时间：2026-08-24 10:03 (CST) 星期一")
        assert len(text.splitlines()) == 1  # 无用户最近一次消息 → 只保留当前时间行

    def test_with_last_interaction(self):
        # now = 02:03 UTC（10:03 CST）；用户最近一次消息 = 前一日 23:03 UTC = 当日 07:03 CST
        last = datetime(2026, 8, 23, 23, 3, 0, tzinfo=UTC)
        text = build_temporal_context(NOW, last)
        lines = text.splitlines()
        assert lines[1] == "用户最近一次消息：2026-08-24 07:03 (CST)"
        assert lines[2] == "离线间隔：3小时"

    def test_old_wording_absent(self):
        # B1c.5：旧文案"上次互动"不得出现在注入文本中（主体歧义已修正）
        last = datetime(2026, 8, 23, 23, 3, 0, tzinfo=UTC)
        text = build_temporal_context(NOW, last)
        assert "用户最近一次消息：" in text
        assert "上次互动" not in text

    def test_no_history_user(self):
        text = build_temporal_context(NOW, None)
        assert "用户最近一次消息" not in text
        assert "离线间隔" not in text

    def test_invalid_last_interaction(self):
        text = build_temporal_context(NOW, "garbage")
        assert "用户最近一次消息" not in text
        assert "离线间隔" not in text

    def test_offline_gap_granularity(self):
        for minutes, expected in ((5, "5分钟"), (150, "2小时"), (2880, "2天"), (64800, "1个月")):
            last = NOW - __import__("datetime").timedelta(minutes=minutes)
            text = build_temporal_context(NOW, last)
            assert f"离线间隔：{expected}" in text, f"{minutes}min → {text}"

    def test_future_defense(self):
        # 未来 1 小时 → 省略两行（防未来时间幻觉）
        text = build_temporal_context(NOW, datetime(2026, 8, 24, 3, 3, 0, tzinfo=UTC))
        assert "用户最近一次消息" not in text
        # 未来 30 秒（容差内）→ 保留，间隔显示"不到1分钟"
        text = build_temporal_context(NOW, datetime(2026, 8, 24, 2, 3, 30, tzinfo=UTC))
        assert "离线间隔：不到1分钟" in text

    def test_budget_guard(self):
        text = build_temporal_context(NOW, NOW, budget=30)
        assert len(text) <= 30
        assert text.startswith("当前时间：")

    def test_default_budget_is_400(self):
        assert DEFAULT_TEMPORAL_CONTEXT_BUDGET == 400
        text = build_temporal_context(NOW, NOW)
        assert len(text) <= 400

    def test_tz_override(self):
        text = build_temporal_context(NOW, None, tz="UTC")
        assert text.startswith("当前时间：2026-08-24 02:03 (UTC) 星期一")

    def test_naive_now_interpreted_as_shanghai(self):
        text = build_temporal_context("2026-08-24 10:03", None)
        assert text.startswith("当前时间：2026-08-24 10:03 (CST)")


# ═════════════════════════════════════════════════════════
# resolve_temporal_context：三态决策
# ═════════════════════════════════════════════════════════
class TestResolveModes:
    def test_off_returns_nothing(self):
        assert resolve_temporal_context("off", NOW, NOW) == (None, None)
        assert resolve_temporal_context("OFF", NOW, NOW) == (None, None)

    def test_shadow_logs_but_not_inject(self):
        inject, shadow = resolve_temporal_context("shadow", NOW, NOW)
        assert inject is None
        assert shadow and shadow.startswith("当前时间：")

    def test_active_injects(self):
        inject, shadow = resolve_temporal_context("active", NOW, NOW)
        assert inject and inject.startswith("当前时间：")
        assert shadow is None

    def test_unknown_mode_safe(self):
        assert resolve_temporal_context("banana", NOW, NOW) == (None, None)
        assert resolve_temporal_context(None, NOW, NOW) == (None, None)
        assert resolve_temporal_context("", NOW, NOW) == (None, None)


# ═════════════════════════════════════════════════════════
# 接入点行为：engine.py（ResponseEngine 两条链）
# ═════════════════════════════════════════════════════════
class TestEngineIntegration:
    @staticmethod
    def _engine():
        from src.engine import ResponseEngine

        return ResponseEngine.__new__(ResponseEngine)  # 绕过 __init__，不建 LLM client

    @staticmethod
    def _args(temporal_context=None):
        return dict(
            user_message="你好",
            history=[],
            chat_memories=[],
            life_events=[],
            personality_context="",
            self_model_context={},
            emotion_context={},
            relationship_context={},
            temporal_context=temporal_context,
        )

    def _system(self, messages):
        return messages[0]["content"]

    def test_original_chain_off_keeps_old_line(self):
        messages = self._engine()._build_messages_original(**self._args(None))
        assert "当前时间：" in self._system(messages)

    def test_original_chain_active_injects_block(self):
        messages = self._engine()._build_messages_original(
            **self._args("测试时间块")
        )
        system = self._system(messages)
        assert "测试时间块" in system
        assert "当前时间：" not in system

    def test_opt_chain_off_keeps_old_line(self):
        messages = self._engine()._build_messages_opt(**self._args(None))
        assert "当前时间：" in self._system(messages)

    def test_opt_chain_active_injects_block(self):
        messages = self._engine()._build_messages_opt(**self._args("测试时间块"))
        system = self._system(messages)
        assert "测试时间块" in system
        assert "当前时间：" not in system


# ═════════════════════════════════════════════════════════
# 接入点行为：PromptBuilder.build_messages
# ═════════════════════════════════════════════════════════
class TestPromptBuilderIntegration:
    def _messages(self, temporal_context):
        from src.response.prompt_builder import PromptBuilder

        return PromptBuilder().build_messages(
            user_message="你好", temporal_context=temporal_context
        )

    def test_off_keeps_old_line(self):
        system = self._messages(None)[0]["content"]
        assert "当前时间：" in system

    def test_active_injects_block(self):
        system = self._messages("测试时间块")[0]["content"]
        assert "测试时间块" in system
        assert "当前时间：" not in system


# ═════════════════════════════════════════════════════════
# orchestrator 接线（静态源码断言）
# ═════════════════════════════════════════════════════════
class TestOrchestratorWiring:
    def test_wiring_present(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "orchestrator.py").read_text(
            encoding="utf-8"
        )
        assert "resolve_temporal_context" in src
        assert "temporal.temporal_context_mode" in src
        assert "[TemporalContext][shadow]" in src
        assert "temporal_context=" in src  # 传给 engine.generate

    def test_config_key_present(self):
        cfg = (Path(__file__).resolve().parents[1] / "config.yaml").read_text(
            encoding="utf-8"
        )
        assert "temporal_context_mode:" in cfg
        assert "temporal_context_budget:" in cfg
        assert 'temporal_context_mode: "off"' in cfg


# ═════════════════════════════════════════════════════════
# 纯函数边界（静态检查）
# ═════════════════════════════════════════════════════════
class TestPureBoundary:
    _FORBIDDEN_IMPORT = re.compile(
        r"^\s*(from|import)\s+(src\.(?!temporal)|openai|yaml|httpx|requests|json|pathlib)",
        re.MULTILINE,
    )

    def test_no_io_or_business_imports(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src" / "temporal" / "temporal_context.py"
        ).read_text(encoding="utf-8")
        matches = self._FORBIDDEN_IMPORT.findall(src)
        assert not matches, f"temporal_context 出现禁止 import: {matches}"
        assert "open(" not in src
        assert ".read(" not in src
        assert ".write(" not in src
