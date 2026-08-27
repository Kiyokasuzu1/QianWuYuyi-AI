# -*- coding: utf-8 -*-
"""
tests/test_goal_aging.py

Phase B1c.3 — Goal Aging 测试。

覆盖：
    - ISO UTC / naive ISO / epoch
    - 非法时间 / None
    - 未来时间防御（无负"未更新"）
    - 刚刚/小时/天/月 分级
    - max_chars 截断
    - off 逐字节兼容 / active 注入 / shadow 仅日志
    - 输入不可变 / goal 排序不变
"""
from __future__ import annotations

import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from src.goal.goal_resolver import format_goal_context
from src.temporal.goal_aging import format_goal_age

UTC = timezone.utc
NOW = datetime.now(UTC)
GOAL_LOGGER = "src.goal.goal_resolver"


def _iso(dt):
    return dt.isoformat()


def _goal(created, updated=None, goal_id="g1"):
    return {
        "goal_id": goal_id,
        "description": "描述",
        "reason": "缘由",
        "source_refs": [{"source_type": "memory", "source_id": "m1"}],
        "evidence_total_count": 1,
        "priority": "high",
        "confidence": 0.8,
        "proposal_id": "p1",
        "created_at": created,
        "updated_at": updated or created,
    }


def _set_mode(monkeypatch, mode):
    import src.config as cfg

    monkeypatch.setattr(
        cfg, "_config", {"temporal": {"goal_aging": {"mode": mode}}}
    )


# ═════════════════════════════════════════════════════════
# 纯函数
# ═════════════════════════════════════════════════════════
class TestFormatGoalAge:
    def test_iso_utc(self):
        out = format_goal_age(
            _iso(NOW - timedelta(days=17)), _iso(NOW - timedelta(days=5)), now=NOW
        )
        assert out == "创建：17天前 未更新：5天"

    def test_naive_iso(self):
        # naive 按 Asia/Shanghai 解释：无时区后缀的本地字符串
        naive_now = NOW.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S")
        naive_created = (NOW - timedelta(days=17)).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S")
        naive_updated = (NOW - timedelta(days=5)).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S")
        out = format_goal_age(naive_created, naive_updated, now=naive_now)
        assert out == "创建：17天前 未更新：5天"

    def test_epoch(self):
        created = (NOW - timedelta(days=17)).timestamp()
        updated = (NOW - timedelta(days=5)).timestamp()
        out = format_goal_age(created, updated, now=NOW)
        assert "创建：17天前" in out
        assert "未更新：5天" in out

    def test_invalid_returns_empty(self):
        assert format_goal_age("garbage", _iso(NOW), now=NOW) == ""
        assert format_goal_age(_iso(NOW), "garbage", now=NOW) == ""

    def test_none_returns_empty(self):
        assert format_goal_age(None, _iso(NOW), now=NOW) == ""
        assert format_goal_age(_iso(NOW), None, now=NOW) == ""

    def test_future_created_returns_empty(self):
        out = format_goal_age(
            _iso(NOW + timedelta(hours=2)), _iso(NOW), now=NOW
        )
        assert out == ""

    def test_future_updated_no_negative(self):
        # updated 在未来（容差外）→ 按 now 处理 → "未更新：刚刚"，绝无负时间
        out = format_goal_age(
            _iso(NOW - timedelta(days=17)), _iso(NOW + timedelta(hours=2)), now=NOW
        )
        assert "未更新：刚刚" in out
        assert "-" not in out.split("未更新：")[-1]

    def test_stale_gradations(self):
        cases = [
            (timedelta(seconds=30), "刚刚"),
            (timedelta(hours=3), "3小时"),
            (timedelta(days=9), "9天"),
            (timedelta(days=45), "1个月"),
            (timedelta(days=400), "1年"),
        ]
        for delta, expected in cases:
            out = format_goal_age(
                _iso(NOW - timedelta(days=17)), _iso(NOW - delta), now=NOW
            )
            assert f"未更新：{expected}" in out, f"{delta} → {out}"

    def test_max_chars_truncate(self):
        out = format_goal_age(
            _iso(NOW - timedelta(days=17)), _iso(NOW - timedelta(days=5)), now=NOW, max_chars=10
        )
        assert len(out) <= 10


# ═════════════════════════════════════════════════════════
# 接入：format_goal_context 三态
# ═════════════════════════════════════════════════════════
class TestGoalContextIntegration:
    def _ctx(self):
        return {
            "active_goals": [
                _goal(_iso(NOW - timedelta(days=17)), _iso(NOW - timedelta(days=5)), "g1"),
                _goal(_iso(NOW - timedelta(days=40)), _iso(NOW - timedelta(days=3)), "g2"),
            ],
            "schema_version": "1",
        }

    def test_off_byte_compatible(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        ctx = self._ctx()
        text_off = format_goal_context(ctx)
        assert "创建：" not in text_off
        assert "关注方向(g1)" in text_off
        # 与完全无 goal_aging 配置的输出逐字节一致
        import src.config as cfg

        monkeypatch.setattr(cfg, "_config", {"temporal": {}})
        text_noconf = format_goal_context(ctx)
        assert text_off == text_noconf

    def test_active_injects_aging(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        text = format_goal_context(self._ctx())
        assert "创建：" in text
        assert "未更新：" in text

    def test_active_preserves_goal_order(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        text = format_goal_context(self._ctx())
        assert text.index("g1") < text.index("g2")

    def test_shadow_logs_only(self, monkeypatch, caplog):
        _set_mode(monkeypatch, "shadow")
        caplog.set_level(logging.INFO, logger=GOAL_LOGGER)
        text = format_goal_context(self._ctx())
        assert "[GoalAging][shadow]" in caplog.text
        assert "创建：" not in text  # Prompt 不注入

    def test_input_not_mutated(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        ctx = self._ctx()
        snapshot = deepcopy(ctx)
        format_goal_context(ctx)
        assert ctx == snapshot

    def test_budget_respected(self, monkeypatch):
        _set_mode(monkeypatch, "active")
        # 小预算下仍走既有截断路径，不抛异常
        text = format_goal_context(self._ctx(), max_chars=250)
        assert "创建：" in text
