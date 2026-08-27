# -*- coding: utf-8 -*-
"""
tests/test_temporal_core.py

Phase B1a — Temporal Core 单元测试 + 防御测试 + 纯函数边界检查。

覆盖（v1.4 Temporal B1a Implementation Plan §6）：
    - 时区转换 / naive 处理 / epoch / 非法输入
    - relative 边界 / 未来时间防御
    - timeline 乱序 / 空数据 / 错误数据降级 / 上限
    - 静态 import 边界（纯函数锁定：不 import 任何 src 业务模块 / LLM / IO）
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.temporal import (
    Duration,
    build_timeline,
    duration_between,
    normalize_time,
    relative_time,
    render_user_tz,
    to_utc_iso,
)

NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def _future(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


# ═════════════════════════════════════════════════════════
# normalize_time：时区转换 / naive / epoch / 非法输入
# ═════════════════════════════════════════════════════════
class TestNormalize:
    def test_utc_iso_with_z(self):
        assert normalize_time("2026-08-24T08:00:00Z") == datetime(
            2026, 8, 24, 8, 0, tzinfo=UTC
        )

    def test_cst_iso_converts_to_utc(self):
        assert normalize_time("2026-08-24T08:00:00+08:00") == datetime(
            2026, 8, 24, 0, 0, tzinfo=UTC
        )

    def test_aware_datetime_normalized_to_utc(self):
        shanghai = timezone(timedelta(hours=8))
        assert normalize_time(datetime(2026, 8, 24, 8, 0, tzinfo=shanghai)) == datetime(
            2026, 8, 24, 0, 0, tzinfo=UTC
        )

    def test_naive_uses_default_assume_tz(self):
        # 缺省按 DEFAULT_NAIVE_ASSUME_TZ（Asia/Shanghai）解释
        assert normalize_time("2026-08-24 08:00:00") == datetime(
            2026, 8, 24, 0, 0, tzinfo=UTC
        )

    def test_naive_assume_tz_override(self):
        assert normalize_time(
            "2026-08-24 08:00:00", assume_tz="UTC"
        ) == datetime(2026, 8, 24, 8, 0, tzinfo=UTC)

    def test_naive_datetime_object(self):
        shanghai = timezone(timedelta(hours=8))
        dt = datetime(2026, 8, 24, 8, 0, tzinfo=shanghai)
        assert normalize_time(datetime(2026, 8, 24, 8, 0)) == dt

    def test_epoch_int(self):
        assert normalize_time(1756051200) == datetime.fromtimestamp(
            1756051200, tz=UTC
        )

    def test_epoch_float(self):
        assert normalize_time(1756051200.5) == datetime.fromtimestamp(
            1756051200.5, tz=UTC
        )

    def test_epoch_out_of_range_returns_default(self):
        assert normalize_time(1e15) is None
        assert normalize_time(-50) is None

    def test_bool_is_not_epoch(self):
        assert normalize_time(True) is None

    def test_none_and_empty(self):
        assert normalize_time(None) is None
        assert normalize_time("") is None
        assert normalize_time("   ") is None

    def test_invalid_formats_return_default(self):
        assert normalize_time("not-a-time") is None
        assert normalize_time("2026-13-45") is None
        assert normalize_time("2026-08-24T99:00:00Z") is None
        assert normalize_time({}) is None
        assert normalize_time([]) is None

    def test_custom_default(self):
        assert normalize_time("bad", default="FB") == "FB"

    def test_never_raises_on_garbage(self):
        # 防御：任何输入都不允许抛异常
        for bad in ("", None, "x", [], {}, 1e18, object(), "2026/08/24"):
            try:
                normalize_time(bad)
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(f"normalize_time({bad!r}) raised {exc}")


# ═════════════════════════════════════════════════════════
# to_utc_iso / render_user_tz
# ═════════════════════════════════════════════════════════
class TestRender:
    def test_to_utc_iso(self):
        assert to_utc_iso("2026-08-24T08:00:00+08:00") == "2026-08-24T00:00:00+00:00"

    def test_to_utc_iso_invalid_returns_empty(self):
        assert to_utc_iso("bad") == ""

    def test_render_user_tz_default(self):
        # 默认用户时区 Asia/Shanghai
        assert render_user_tz(datetime(2026, 8, 24, 0, 0, tzinfo=UTC)) == "2026-08-24 08:00"

    def test_render_user_tz_override(self):
        assert render_user_tz(
            datetime(2026, 8, 24, 0, 0, tzinfo=UTC), tz="UTC"
        ) == "2026-08-24 00:00"

    def test_render_invalid_returns_empty(self):
        assert render_user_tz(None) == ""


# ═════════════════════════════════════════════════════════
# relative_time：分级边界
# ═════════════════════════════════════════════════════════
class TestRelativeBoundaries:
    def _rel(self, seconds):
        return relative_time(_ago(seconds), now=NOW)

    def test_just_now_and_minutes(self):
        assert self._rel(59) == "刚刚"
        assert self._rel(60) == "1分钟前"
        assert self._rel(59 * 60) == "59分钟前"

    def test_hours(self):
        assert self._rel(60 * 60) == "1小时前"
        assert self._rel(23 * 3600) == "23小时前"

    def test_days(self):
        assert self._rel(24 * 3600) == "1天前"
        assert self._rel(3 * 86400) == "3天前"
        assert self._rel(29 * 86400) == "29天前"

    def test_months_and_years(self):
        assert self._rel(30 * 86400) == "1个月前"
        assert self._rel(364 * 86400) == "12个月前"
        assert self._rel(365 * 86400) == "1年前"

    def test_invalid_returns_empty(self):
        assert relative_time(None, now=NOW) == ""
        assert relative_time("garbage", now=NOW) == ""

    def test_now_param_as_string(self):
        # now 也走 normalize：naive 按缺省时区解释
        result = relative_time(
            "2026-08-24 11:00:00", now="2026-08-24 12:00:00"
        )
        assert result == "1小时前"


# ═════════════════════════════════════════════════════════
# relative_time：未来时间防御（防幻觉）
# ═════════════════════════════════════════════════════════
class TestFutureDefense:
    def test_future_within_tolerance_is_just_now(self):
        assert relative_time(_future(30), now=NOW) == "刚刚"

    def test_future_beyond_tolerance_is_unknown(self):
        assert relative_time(_future(61), now=NOW) == "时间未知"
        assert relative_time(_future(86400), now=NOW) == "时间未知"

    def test_no_negative_time_text(self):
        out = relative_time(_future(3600), now=NOW)
        assert "前" not in out
        assert out == "时间未知"


# ═════════════════════════════════════════════════════════
# duration_between：跨天 / 跨月 / 跨年 / 反向 / 无效
# ═════════════════════════════════════════════════════════
class TestDuration:
    def test_same_day(self):
        d = duration_between(
            datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 9, 30, tzinfo=UTC),
        )
        assert d == Duration(hours=1, minutes=30, total_seconds=5400.0)

    def test_cross_day(self):
        d = duration_between(
            datetime(2026, 8, 24, 23, 50, tzinfo=UTC),
            datetime(2026, 8, 25, 0, 10, tzinfo=UTC),
        )
        assert d == Duration(minutes=20, total_seconds=1200.0)

    def test_cross_month(self):
        d = duration_between(
            datetime(2026, 1, 31, 0, 0, tzinfo=UTC),
            datetime(2026, 2, 28, 0, 0, tzinfo=UTC),
        )
        assert d == Duration(days=28, total_seconds=28 * 86400.0)

    def test_leap_year_feb29(self):
        d = duration_between(
            datetime(2024, 2, 28, 0, 0, tzinfo=UTC),
            datetime(2024, 2, 29, 0, 0, tzinfo=UTC),
        )
        assert d == Duration(days=1, total_seconds=86400.0)

    def test_cross_year(self):
        d = duration_between(
            datetime(2026, 12, 31, 23, 0, tzinfo=UTC),
            datetime(2027, 1, 1, 1, 0, tzinfo=UTC),
        )
        assert d == Duration(hours=2, total_seconds=7200.0)

    def test_reversed_keeps_negative_total(self):
        d = duration_between(
            datetime(2026, 8, 25, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 0, 0, tzinfo=UTC),
        )
        assert d.is_future is True
        assert d.total_seconds == -86400.0
        assert d.days == 1  # 分解值为绝对值

    def test_invalid_input(self):
        d = duration_between("bad", datetime(2026, 8, 24, tzinfo=UTC))
        assert d.is_valid is False


# ═════════════════════════════════════════════════════════
# build_timeline：乱序 / 空 / 降级 / 上限 / 时区分桶
# ═════════════════════════════════════════════════════════
class TestTimeline:
    T1 = datetime(2026, 7, 10, 0, 0, tzinfo=UTC)
    T2 = datetime(2026, 8, 5, 0, 0, tzinfo=UTC)
    T3 = datetime(2026, 8, 20, 0, 0, tzinfo=UTC)
    # UTC 2026-07-31 20:00 = 上海 2026-08-01 04:00 → 分桶应为 "2026-08"
    T4 = datetime(2026, 7, 31, 20, 0, tzinfo=UTC)

    def test_empty(self):
        assert build_timeline([]) == []
        assert build_timeline(None) == []

    def test_out_of_order_sorted_ascending(self):
        entries = build_timeline([(self.T3, "c", "3"), (self.T1, "a", "1"), (self.T2, "b", "2")])
        ids = [e.item_id for e in entries]
        assert ids == ["1", "2", "3"]

    def test_month_grouping(self):
        entries = build_timeline([(self.T1, "a", "1"), (self.T2, "b", "2")])
        assert entries[0].group_label == "2026-07"
        assert entries[1].group_label == "2026-08"

    def test_group_label_uses_user_timezone(self):
        # 月末 UTC 跨月 → 用户时区（Asia/Shanghai）分桶
        entries = build_timeline([(self.T4, "x", "4")])
        assert entries[0].group_label == "2026-08"

    def test_max_items_keeps_newest(self):
        items = [(NOW - timedelta(days=i), f"d{i}", str(i)) for i in range(12)]
        entries = build_timeline(items, max_items=10)
        assert len(entries) == 10
        # 升序排序后保留最新 10 条：最老的 d11/d10 被丢弃
        assert entries[0].item_id == "9"
        assert entries[-1].item_id == "0"

    def test_invalid_items_degraded(self):
        entries = build_timeline(
            [("garbage", "bad", "x"), (self.T1, "a", "1"), (None, "nil", "n")]
        )
        assert len(entries) == 1
        assert entries[0].item_id == "1"

    def test_dict_records(self):
        entries = build_timeline(
            [
                {"timestamp": "2026-08-20T00:00:00Z", "content": "加入时间感知", "id": "m1"},
                {"created_at": "2026-07-10T00:00:00Z", "label": "设计记忆系统", "id": "m2"},
            ]
        )
        assert [e.item_id for e in entries] == ["m2", "m1"]
        assert entries[1].label == "加入时间感知"

    def test_time_utc_normalized(self):
        entries = build_timeline([("2026-08-20T08:00:00+08:00", "x", "1")])
        assert entries[0].time_utc == "2026-08-20T00:00:00+00:00"

    def test_never_raises_on_garbage_records(self):
        try:
            build_timeline([123, "abc", None, {}, ("2026-08-20T00:00:00Z", "ok", "1")])
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"build_timeline raised {exc}")


# ═════════════════════════════════════════════════════════
# 纯函数边界：静态 import 检查（锁定零外部依赖）
# ═════════════════════════════════════════════════════════
class TestPureFunctionBoundary:
    _FORBIDDEN_IMPORT = re.compile(
        r"^\s*(from|import)\s+(src\.|openai|yaml|httpx|requests|json|pathlib)",
        re.MULTILINE,
    )

    def test_no_business_or_llm_imports(self):
        src = (
            Path(__file__).resolve().parents[1] / "src" / "temporal" / "temporal_core.py"
        ).read_text(encoding="utf-8")
        matches = self._FORBIDDEN_IMPORT.findall(src)
        assert not matches, f"temporal_core 出现了禁止的 import: {matches}"

    def test_no_file_io(self):
        src = (
            Path(__file__).resolve().parents[1] / "src" / "temporal" / "temporal_core.py"
        ).read_text(encoding="utf-8")
        assert "open(" not in src
        assert ".read(" not in src
        assert ".write(" not in src
