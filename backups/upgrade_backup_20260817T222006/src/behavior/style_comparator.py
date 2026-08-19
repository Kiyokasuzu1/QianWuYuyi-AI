# -*- coding: utf-8 -*-
"""
src/behavior/style_comparator.py

Phase 3.7.6：风格对比工具

职责：
  - 加载两个时间段的风格快照
  - 按天/周聚合风格指标
  - 生成可读的对比报告
  - 标记显著变化（超出阈值的指标）

用法：
  from src.behavior.style_comparator import StyleComparator
  comp = StyleComparator()
  report = comp.compare_periods(period1_days=(1, 0), period2_days=(2, 1))
  # 即：今天(0-1天前) vs 昨天(1-2天前)

约束：
  - 不调用 LLM
  - 只读分析
  - 不修改任何状态
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .response_style_monitor import ResponseStyleMonitor

# ============================================================
# 常量
# ============================================================

METRICS = ["warmth", "curiosity", "initiative", "formality", "playfulness"]
METRIC_LABELS = {
    "warmth": "温暖度",
    "curiosity": "好奇心",
    "initiative": "主动性",
    "formality": "正式度",
    "playfulness": "活泼度",
}

# 显著变化阈值（用于高亮标记）
SIGNIFICANT_DELTA = 0.15

# 箭头符号
_ARROW_UP = "↑"
_ARROW_DOWN = "↓"
_ARROW_FLAT = "→"


# ============================================================
# 数据结构
# ============================================================

@dataclass
class MetricChange:
    """单个指标的变化。"""
    metric: str
    label: str
    before: float
    after: float
    delta: float
    significant: bool
    direction: str  # "up" / "down" / "flat"


@dataclass
class ComparisonReport:
    """对比报告。"""
    period1_label: str  # 基准时间段描述
    period2_label: str  # 对比时间段描述
    period1_count: int  # 基准时间段快照数
    period2_count: int  # 对比时间段快照数
    changes: List[MetricChange] = field(default_factory=list)
    summary: str = ""
    timestamp: str = ""


# ============================================================
# 风格对比器
# ============================================================

class StyleComparator:
    """Phase 3.7.6：风格对比器。

    用法：
        comp = StyleComparator()
        # 今天 vs 昨天
        report = comp.compare_daily()
        print(report.summary)

        # 本周 vs 上周
        report = comp.compare_weekly()
        print(report.summary)

        # 自定义时间段
        report = comp.compare_periods(
            period1_days=(7, 3),   # 7天前～3天前
            period2_days=(3, 0),   # 3天前～现在
        )
    """

    def __init__(self, monitor: Optional[ResponseStyleMonitor] = None):
        self._monitor = monitor or ResponseStyleMonitor()

    def compare_daily(self) -> ComparisonReport:
        """今天 vs 昨天。"""
        return self.compare_periods(
            period1_days=(2, 1),  # 昨天
            period2_days=(1, 0),  # 今天
            period1_label="昨天",
            period2_label="今天",
        )

    def compare_weekly(self) -> ComparisonReport:
        """本周 vs 上周。"""
        today = datetime.now()
        days_since_monday = today.weekday()  # 0=周一
        days_this_week = days_since_monday + 1  # 本周已过天数

        return self.compare_periods(
            period1_days=(days_this_week + 7, days_this_week),  # 上周
            period2_days=(days_this_week, 0),  # 本周
            period1_label="上周",
            period2_label="本周",
        )

    def compare_periods(
        self,
        period1_days: Tuple[int, int] = (7, 3),
        period2_days: Tuple[int, int] = (3, 0),
        period1_label: str = "基准期",
        period2_label: str = "对比期",
    ) -> ComparisonReport:
        """对比两个时间段的风格指标。

        Args:
            period1_days: (start_days_ago, end_days_ago) 基准时间段
            period2_days: (start_days_ago, end_days_ago) 对比时间段
            period1_label: 基准期标签
            period2_label: 对比期标签

        Returns:
            ComparisonReport: 对比报告
        """
        p1_avgs, p1_count = self._get_period_averages(*period1_days)
        p2_avgs, p2_count = self._get_period_averages(*period2_days)

        changes = []
        for metric in METRICS:
            before = p1_avgs.get(metric, 0.0)
            after = p2_avgs.get(metric, 0.0)
            delta = after - before
            significant = abs(delta) >= SIGNIFICANT_DELTA

            if delta > 0.01:
                direction = "up"
            elif delta < -0.01:
                direction = "down"
            else:
                direction = "flat"

            changes.append(MetricChange(
                metric=metric,
                label=METRIC_LABELS.get(metric, metric),
                before=round(before, 3),
                after=round(after, 3),
                delta=round(delta, 3),
                significant=significant,
                direction=direction,
            ))

        # 生成摘要
        summary = self._build_summary(
            changes, p1_count, p2_count, period1_label, period2_label
        )

        return ComparisonReport(
            period1_label=period1_label,
            period2_label=period2_label,
            period1_count=p1_count,
            period2_count=p2_count,
            changes=changes,
            summary=summary,
            timestamp=datetime.now().isoformat(),
        )

    def _get_period_averages(
        self, start_days_ago: int, end_days_ago: int
    ) -> Tuple[Dict[str, float], int]:
        """获取指定时间段内各指标的平均值。"""
        now = time.time()
        period_start = now - start_days_ago * 86400
        period_end = now - end_days_ago * 86400

        records = self._monitor.load_all()
        values: Dict[str, List[float]] = {m: [] for m in METRICS}

        for rec in records:
            ts = rec.get("timestamp", "")
            if not ts:
                continue
            try:
                rec_time = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                if rec_time < period_start or rec_time >= period_end:
                    continue
            except (ValueError, TypeError):
                continue

            for metric in METRICS:
                val = rec.get(metric, 0.0)
                if isinstance(val, (int, float)):
                    values[metric].append(val)

        avgs = {}
        for metric in METRICS:
            vals = values[metric]
            avgs[metric] = round(sum(vals) / len(vals), 3) if vals else 0.0

        return avgs, len(values["warmth"])

    def _build_summary(
        self,
        changes: List[MetricChange],
        count1: int,
        count2: int,
        label1: str,
        label2: str,
    ) -> str:
        """生成可读的对比摘要。"""
        lines = []
        lines.append(f"羽依风格对比：{label1} → {label2}")
        lines.append(f"数据量：{label1} {count1}条 | {label2} {count2}条")
        lines.append("─" * 44)

        significant_changes = [c for c in changes if c.significant]

        if not significant_changes:
            lines.append("  ✅ 风格稳定，无显著变化。")
        else:
            for c in significant_changes:
                arrow = _ARROW_UP if c.direction == "up" else _ARROW_DOWN
                sign = "+" if c.delta > 0 else ""
                lines.append(
                    f"  ⚠ {c.label:　<6s} {c.before:.3f} → {c.after:.3f} ({sign}{c.delta:.3f}) {arrow}"
                )

        lines.append("─" * 44)

        # 详细指标
        lines.append("  详细指标：")
        for c in changes:
            arrow = _ARROW_UP if c.direction == "up" else (_ARROW_DOWN if c.direction == "down" else _ARROW_FLAT)
            flag = " ⚠" if c.significant else "  "
            lines.append(
                f"    {c.label:　<6s} {c.before:.3f} → {c.after:.3f} {arrow}{flag}"
            )

        # 判断
        if count1 < 5 or count2 < 5:
            lines.append("\n  💡 数据量较少（<5条），结论仅供参考。")

        return "\n".join(lines)

    def get_trend_chart(self, days: int = 7) -> str:
        """生成风格趋势图（ASCII 文本）。

        Args:
            days: 回溯天数

        Returns:
            str: ASCII 趋势图
        """
        daily = self._monitor.get_daily_averages(days=days)
        if not daily:
            return "暂无数据。\n"

        dates = sorted(daily.keys())
        lines = []
        lines.append(f"羽依风格趋势（近{days}天）")
        lines.append("─" * 60)

        # 表头
        header = f"{'日期':<12s}"
        for m in METRICS:
            header += f" {METRIC_LABELS.get(m, m):>6s}"
        lines.append(header)
        lines.append("─" * 60)

        for date_key in dates:
            row = f"{date_key:<12s}"
            for m in METRICS:
                val = daily[date_key].get(m, 0.0)
                row += f" {val:>6.3f}"
            lines.append(row)

        lines.append("─" * 60)

        # 趋势判断
        if len(dates) >= 2:
            first = daily[dates[0]]
            last = daily[dates[-1]]
            lines.append("\n  首尾变化：")
            for m in METRICS:
                delta = last.get(m, 0.0) - first.get(m, 0.0)
                arrow = _ARROW_UP if delta > 0.01 else (_ARROW_DOWN if delta < -0.01 else _ARROW_FLAT)
                sign = "+" if delta > 0 else ""
                lines.append(
                    f"    {METRIC_LABELS.get(m, m):　<6s} {first.get(m, 0.0):.3f} → {last.get(m, 0.0):.3f} ({sign}{delta:.3f}) {arrow}"
                )

        return "\n".join(lines)