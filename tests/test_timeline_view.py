# -*- coding: utf-8 -*-
"""
tests/test_timeline_view.py

Phase B1c.2 — Narrative Timeline View 测试。

覆盖：
    - ISO naive / UTC Z / epoch / aware datetime
    - 月份分桶 / UTC 跨月用户时区转换 / 时间升序
    - max_items=10 / max_chars=600 硬截断
    - major_changes 原文保持 / narrative_text fallback（≤60）
    - 空列表 / 非法 timestamp 跳过 / 对象属性访问
    - 不修改输入对象 / 禁止文本（时间未知/推测）
    - import 边界检查（仅 stdlib + temporal_core）
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.temporal.timeline_view import build_narrative_timeline_view

UTC = timezone.utc


def _snap(ts, changes=None, narrative=None, version=1):
    return {
        "timestamp": ts,
        "version": version,
        "major_changes": changes,
        "narrative_text": narrative,
    }


# ═════════════════════════════════════════════════════════
class TestTimeFormats:
    def test_iso_naive(self):
        out = build_narrative_timeline_view(
            [_snap("2026-07-10 10:00:00", ["建立羽依人格模型"])]
        )
        assert "2026-07" in out
        assert "- 建立羽依人格模型" in out

    def test_utc_z(self):
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", ["完成 Memory System"])]
        )
        assert "2026-08" in out
        assert "- 完成 Memory System" in out

    def test_epoch(self):
        ts = datetime(2026, 8, 5, 0, 0, tzinfo=UTC).timestamp()
        out = build_narrative_timeline_view([_snap(ts, ["epoch 时间条目"])])
        assert "2026-08" in out
        assert "- epoch 时间条目" in out

    def test_aware_datetime_object(self):
        out = build_narrative_timeline_view(
            [_snap(datetime(2026, 7, 10, 0, 0, tzinfo=UTC), ["aware 条目"])]
        )
        assert "2026-07" in out


# ═════════════════════════════════════════════════════════
class TestBucketing:
    def test_month_buckets_ascending(self):
        snaps = [
            _snap("2026-08-20T00:00:00Z", ["八月事件"]),
            _snap("2026-07-10T00:00:00Z", ["七月事件"]),
        ]
        out = build_narrative_timeline_view(snaps)
        assert out.index("2026-07") < out.index("2026-08")

    def test_utc_cross_month_uses_user_tz(self):
        # UTC 2026-07-31 20:00 = 上海 2026-08-01 04:00 → 分桶 2026-08
        out = build_narrative_timeline_view(
            [_snap("2026-07-31T20:00:00Z", ["跨月事件"])]
        )
        assert "2026-08" in out
        assert "2026-07" not in out


# ═════════════════════════════════════════════════════════
class TestLimits:
    def test_max_items(self):
        snaps = [
            _snap(datetime(2026, 8, i + 1, tzinfo=UTC).isoformat(), [f"e{i}"])
            for i in range(12)
        ]
        out = build_narrative_timeline_view(snaps, max_items=10)
        assert out.count("- ") == 10

    def test_max_chars_hard_truncate(self):
        snaps = [_snap("2026-08-05T00:00:00Z", ["x" * 100])]
        out = build_narrative_timeline_view(snaps, max_chars=30)
        assert len(out) <= 30


# ═════════════════════════════════════════════════════════
class TestContentRules:
    def test_major_changes_verbatim(self):
        original = "原始文本不被改写，保持原样 XYZ"
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", [original])]
        )
        assert f"- {original}" in out

    def test_multiple_changes_per_snapshot(self):
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", ["事件A", "事件B"])]
        )
        assert "- 事件A" in out
        assert "- 事件B" in out

    def test_narrative_text_fallback_first_line(self):
        narrative = "第一行是摘要内容\n第二行不应出现"
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", None, narrative)]
        )
        assert "- 第一行是摘要内容" in out
        assert "第二行" not in out

    def test_narrative_fallback_capped_60(self):
        narrative = "L" * 80
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", [], narrative)]
        )
        label = [l for l in out.splitlines() if l.startswith("- ")][0]
        assert len(label) <= 2 + 60

    def test_no_changes_no_narrative_skipped(self):
        out = build_narrative_timeline_view(
            [_snap("2026-08-05T00:00:00Z", [], "")]
        )
        assert out == ""


# ═════════════════════════════════════════════════════════
class TestDefense:
    def test_empty_inputs(self):
        assert build_narrative_timeline_view([]) == ""
        assert build_narrative_timeline_view(None) == ""
        assert build_narrative_timeline_view([None, None]) == ""

    def test_invalid_timestamp_skipped(self):
        out = build_narrative_timeline_view(
            [_snap("garbage", ["坏时间"]), _snap("2026-08-05T00:00:00Z", ["好时间"])]
        )
        assert "- 好时间" in out
        assert "坏时间" not in out

    def test_input_not_mutated(self):
        snap = {
            "timestamp": "2026-08-05T00:00:00Z",
            "version": 3,
            "major_changes": ["原文"],
            "narrative_text": "n",
        }
        snapshot = dict(snap)
        snapshot["major_changes"] = list(snap["major_changes"])
        build_narrative_timeline_view([snap])
        assert snap == snapshot

    def test_object_attribute_access(self):
        obj = SimpleNamespace(
            timestamp="2026-08-05T00:00:00Z", version=2, major_changes=["对象条目"], narrative_text=""
        )
        out = build_narrative_timeline_view([obj])
        assert "- 对象条目" in out

    def test_no_forbidden_text(self):
        # 全非法输入 → 空串，绝不产生"时间未知"/推测文本
        out = build_narrative_timeline_view([_snap("bad", ["x"])])
        assert out == ""
        assert "时间未知" not in out
        assert "推测" not in out

    def test_no_crash_on_garbage(self):
        for bad in (123, "str", 3.14, {"no": "time"}):
            try:
                build_narrative_timeline_view([bad])
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(f"garbage {bad!r} raised {exc}")


# ═════════════════════════════════════════════════════════
class TestImportBoundary:
    _ALLOWED_IMPORT = re.compile(
        r"^\s*(from|import)\s+(__future__|typing|src\.temporal\.temporal_core)\b",
        re.MULTILINE,
    )
    _ANY_IMPORT = re.compile(r"^\s*(from|import)\s+", re.MULTILINE)

    def test_only_allowed_imports(self):
        src = (
            Path(__file__).resolve().parents[1] / "src" / "temporal" / "timeline_view.py"
        ).read_text(encoding="utf-8")
        for line in src.splitlines():
            if self._ANY_IMPORT.match(line):
                assert self._ALLOWED_IMPORT.match(line), f"禁止 import: {line!r}"

    def test_no_io(self):
        src = (
            Path(__file__).resolve().parents[1] / "src" / "temporal" / "timeline_view.py"
        ).read_text(encoding="utf-8")
        assert "open(" not in src
        assert ".read(" not in src
        assert ".write(" not in src
