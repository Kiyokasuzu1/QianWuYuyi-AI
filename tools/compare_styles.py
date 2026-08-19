#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
tools/compare_styles.py

Phase 3.7.6：风格对比 CLI 工具

用法：
  # 今天 vs 昨天
  python tools/compare_styles.py --daily

  # 本周 vs 上周
  python tools/compare_styles.py --weekly

  # 近 7 天趋势
  python tools/compare_styles.py --trend

  # 自定义时间段
  python tools/compare_styles.py --period1 7,3 --period2 3,0

  # 输出 JSON
  python tools/compare_styles.py --daily --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="羽依风格对比工具（Phase 3.7.6）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python tools/compare_styles.py --daily      # 今天 vs 昨天
  python tools/compare_styles.py --weekly     # 本周 vs 上周
  python tools/compare_styles.py --trend      # 近 7 天趋势
  python tools/compare_styles.py --trend --days 14  # 近 14 天趋势
        """,
    )
    parser.add_argument("--daily", action="store_true", help="今天 vs 昨天")
    parser.add_argument("--weekly", action="store_true", help="本周 vs 上周")
    parser.add_argument("--trend", action="store_true", help="显示趋势图")
    parser.add_argument("--days", type=int, default=7, help="趋势图回溯天数（默认 7）")
    parser.add_argument("--period1", type=str, help="基准时间段，格式：start,end（如 7,3 表示 7天前～3天前）")
    parser.add_argument("--period2", type=str, help="对比时间段，格式：start,end（如 3,0 表示 3天前～现在）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--data-dir", type=str, default=None, help="数据目录（默认 data/）")

    args = parser.parse_args()

    from src.behavior.style_comparator import StyleComparator
    from src.behavior.response_style_monitor import ResponseStyleMonitor

    monitor = ResponseStyleMonitor(data_dir=args.data_dir)
    comp = StyleComparator(monitor=monitor)

    if args.trend:
        chart = comp.get_trend_chart(days=args.days)
        print(chart)
        return

    if args.period1 and args.period2:
        p1 = tuple(int(x.strip()) for x in args.period1.split(","))
        p2 = tuple(int(x.strip()) for x in args.period2.split(","))
        report = comp.compare_periods(
            period1_days=p1,
            period2_days=p2,
            period1_label=f"{p1[0]}天前~{p1[1]}天前",
            period2_label=f"{p2[0]}天前~{p2[1]}天前",
        )
    elif args.daily:
        report = comp.compare_daily()
    elif args.weekly:
        report = comp.compare_weekly()
    else:
        # 默认：今天 vs 昨天
        report = comp.compare_daily()

    if args.json:
        print(json.dumps({
            "period1_label": report.period1_label,
            "period2_label": report.period2_label,
            "period1_count": report.period1_count,
            "period2_count": report.period2_count,
            "timestamp": report.timestamp,
            "changes": [
                {
                    "metric": c.metric,
                    "label": c.label,
                    "before": c.before,
                    "after": c.after,
                    "delta": c.delta,
                    "significant": c.significant,
                    "direction": c.direction,
                }
                for c in report.changes
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(report.summary)


if __name__ == "__main__":
    main()