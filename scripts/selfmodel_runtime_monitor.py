# -*- coding: utf-8 -*-
"""
scripts/selfmodel_runtime_monitor.py

Phase C.4.6.2 — SelfModel Activation Monitor (Controlled Real Runtime Trial)

职责:
- 每日检查 data/self_model/ 下的 4 个 jsonl 文件
- 输出 docs/audit/selfmodel_runtime_daily.md
- 计算状态: EMPTY / INITIALIZING / ACTIVE / STABLE
- 从 docs/audit/selfmodel_consistency_report.md 读取一致性检查结果
- 统计每日新增数量(基于历史 log)
- 生成告警

约束:
- 只读监控,不修改任何核心模块
- 不调用 LLM,无副作用
- 所有数据来自文件(只读)
- 仅生成报告,不改 SelfModel 数据
- 不预填任何 SelfModel 数据

用法:
    python scripts/selfmodel_runtime_monitor.py
    python scripts/selfmodel_runtime_monitor.py \\
        --data-dir data/self_model \\
        --report docs/audit/selfmodel_runtime_daily.md \\
        --consistency-report docs/audit/selfmodel_consistency_report.md

退出码:
    0 — 报告生成成功(无告警)
    1 — 报告生成成功(有告警)
    2 — 关键异常
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "self_model"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "audit" / "selfmodel_runtime_daily.md"
DEFAULT_CONSISTENCY_REPORT = PROJECT_ROOT / "docs" / "audit" / "selfmodel_consistency_report.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "selfmodel_runtime_log.jsonl"

# 监控的 4 个 SelfModel 文件
SELF_MODEL_FILES = [
    "beliefs.jsonl",
    "history.jsonl",
    "reflection.jsonl",
    "relationship.jsonl",
]

# 核心文件(影响 ACTIVE 状态判定)
CORE_FILES = {"beliefs.jsonl", "history.jsonl"}

# 状态机
STATUS_EMPTY = "EMPTY"
STATUS_INITIALIZING = "INITIALIZING"
STATUS_ACTIVE = "ACTIVE"
STATUS_STABLE = "STABLE"

# 一致性检查触发阈值
CONSISTENCY_TRIGGER_THRESHOLD = 10

# 告警阈值
ALERT_DAILY_GROWTH_BURST = 100   # 单文件单日增长 > 100 视为爆发
ALERT_SIZE_GROWTH_BURST = 524288  # 512 KB


# ============================================================
# 工具函数
# ============================================================
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _human_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _log(event: str, payload: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "ts": _utc_now(), **payload}, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 jsonl 文件,跳过空行和解析错误。"""
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []
    return items


def _file_stats(path: Path) -> Dict[str, Any]:
    """获取文件统计信息(只读)。"""
    if not path.exists():
        return {
            "exists": False,
            "size_bytes": 0,
            "line_count": 0,
            "record_count": 0,
            "valid": True,
        }
    try:
        size = path.stat().st_size
        # 统计行数(快速)
        line_count = 0
        with open(path, "r", encoding="utf-8") as f:
            for _ in f:
                line_count += 1
        # 解析有效记录
        records = _read_jsonl(path)
        # 文件损坏检测:行数 - 有效记录数 > 0 且 size > 0
        valid = (line_count == len(records)) or (line_count == 0 and size == 0)
        return {
            "exists": True,
            "size_bytes": size,
            "line_count": line_count,
            "record_count": len(records),
            "valid": valid,
        }
    except Exception as e:
        return {
            "exists": False,
            "error": str(e),
            "size_bytes": 0,
            "line_count": 0,
            "record_count": 0,
            "valid": False,
        }


def _human_size(n: int) -> str:
    if n < 0:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


# ============================================================
# 一致性检查报告读取
# ============================================================
def read_consistency_status(consistency_report: Path) -> Dict[str, Any]:
    """从 selfmodel_consistency_report.md 读取最近一次一致性检查状态。"""
    if not consistency_report.exists():
        return {
            "report_exists": False,
            "status": "NOT_RUN",
            "issue_count": 0,
            "last_run": None,
        }
    try:
        content = consistency_report.read_text(encoding="utf-8")
        # 解析 markdown 中的状态(支持 `**CONSISTENT**` / `**ISSUES_DETECTED**` / `**UNKNOWN**`)
        status_match = re.search(r"\*\*整体状态:\*\*\s*\*\*([A-Z_]+)\*\*", content)
        status = status_match.group(1) if status_match else "UNKNOWN"
        # 解析 issue count
        issue_match = re.search(r"issue_count[^\d]*(\d+)", content)
        issue_count = int(issue_match.group(1)) if issue_match else 0
        # 解析生成时间(兼容 `**生成时间:** \`xxx\`` 与 `生成时间: \`xxx\`` 两种格式)
        ts_match = re.search(r"生成时间:.*?`([^`]+)`", content)
        last_run = ts_match.group(1) if ts_match else None
        return {
            "report_exists": True,
            "status": status,
            "issue_count": issue_count,
            "last_run": last_run,
        }
    except Exception as e:
        return {
            "report_exists": True,
            "status": "PARSE_ERROR",
            "error": str(e),
            "issue_count": 0,
            "last_run": None,
        }


# ============================================================
# 历史 log 读取(每日新增)
# ============================================================
def read_last_log_for(data_dir: Path) -> Optional[Dict[str, Any]]:
    """从 selfmodel_runtime_log.jsonl 读取最近一次(同 data_dir)的监控记录。"""
    if not LOG_PATH.exists():
        return None
    target_dir = str(data_dir)
    last_entry: Optional[Dict[str, Any]] = None
    try:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("data_dir") == target_dir:
                        last_entry = entry
                except json.JSONDecodeError:
                    continue
    except Exception:
        return None
    return last_entry


def calc_daily_increment(
    current: Dict[str, Dict[str, Any]],
    last: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """计算每日新增数量(对比上一次 log)。

    当 last=None 时(首次运行),所有增量为 0(无历史基线)。
    """
    result: Dict[str, Dict[str, int]] = {}
    if last is None:
        for fname in SELF_MODEL_FILES:
            result[fname] = {"record_delta": 0, "size_delta": 0}
        return result
    last_stats = last.get("file_stats", {})
    for fname in SELF_MODEL_FILES:
        cur_count = current.get(fname, {}).get("record_count", 0)
        cur_size = current.get(fname, {}).get("size_bytes", 0)
        prev = last_stats.get(fname, {})
        prev_count = prev.get("record_count", 0)
        prev_size = prev.get("size_bytes", 0)
        result[fname] = {
            "record_delta": cur_count - prev_count,
            "size_delta": cur_size - prev_size,
        }
    return result


# ============================================================
# 状态判定
# ============================================================
def determine_status(
    file_stats: Dict[str, Dict[str, Any]],
    consistency: Dict[str, Any],
) -> str:
    """根据 4 个文件的统计信息和一致性检查状态判定 SelfModel 状态。

    规则:
    - 所有文件都不存在 → EMPTY
    - 任意文件存在但所有核心文件 < 10 → INITIALIZING
    - 任意核心文件 ≥ 10 + 一致性检查未通过 → ACTIVE
    - 一致性检查最近状态为 CONSISTENT → STABLE
    """
    existing_files = [s for s in file_stats.values() if s.get("exists")]
    if not existing_files:
        return STATUS_EMPTY

    # 检查 STABLE:一致性检查最近一次为 CONSISTENT
    if consistency.get("status") == "CONSISTENT":
        return STATUS_STABLE

    # 检查 ACTIVE:任一核心文件 ≥ 10
    max_core = 0
    for fname in CORE_FILES:
        s = file_stats.get(fname, {})
        if s.get("exists"):
            max_core = max(max_core, s.get("record_count", 0))
    if max_core >= CONSISTENCY_TRIGGER_THRESHOLD:
        return STATUS_ACTIVE

    # 否则 INITIALIZING
    return STATUS_INITIALIZING


def check_consistency_trigger(file_stats: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """检查是否应触发一致性检查。"""
    beliefs = file_stats.get("beliefs.jsonl", {})
    history = file_stats.get("history.jsonl", {})
    beliefs_count = beliefs.get("record_count", 0)
    history_count = history.get("record_count", 0)
    triggered = beliefs_count > CONSISTENCY_TRIGGER_THRESHOLD or history_count > CONSISTENCY_TRIGGER_THRESHOLD
    return {
        "triggered": triggered,
        "beliefs_count": beliefs_count,
        "history_count": history_count,
        "threshold": CONSISTENCY_TRIGGER_THRESHOLD,
        "reason": (
            f"beliefs={beliefs_count} > {CONSISTENCY_TRIGGER_THRESHOLD}"
            if beliefs_count > CONSISTENCY_TRIGGER_THRESHOLD
            else f"history={history_count} > {CONSISTENCY_TRIGGER_THRESHOLD}"
            if history_count > CONSISTENCY_TRIGGER_THRESHOLD
            else "尚未达到触发阈值"
        ),
    }


def detect_warnings(
    file_stats: Dict[str, Dict[str, Any]],
    increment: Dict[str, Dict[str, int]],
    consistency: Dict[str, Any],
    status: str,
) -> List[Dict[str, str]]:
    """检测告警。"""
    warnings: List[Dict[str, str]] = []
    # 1. 文件损坏
    for fname, s in file_stats.items():
        if s.get("exists") and not s.get("valid", True):
            warnings.append({
                "level": "ERROR",
                "indicator": f"file_corruption:{fname}",
                "detail": f"{fname} 存在无法解析的行(可能损坏)",
            })
    # 2. 增长爆发
    for fname, inc in increment.items():
        if inc.get("record_delta", 0) > ALERT_DAILY_GROWTH_BURST:
            warnings.append({
                "level": "WARNING",
                "indicator": f"growth_burst:{fname}",
                "detail": f"{fname} 单日新增 {inc['record_delta']} 条(>{ALERT_DAILY_GROWTH_BURST})",
            })
        if inc.get("size_delta", 0) > ALERT_SIZE_GROWTH_BURST:
            warnings.append({
                "level": "WARNING",
                "indicator": f"size_burst:{fname}",
                "detail": f"{fname} 单日增长 {_human_size(inc['size_delta'])}",
            })
    # 3. ACTIVE 但未跑一致性检查
    if status == STATUS_ACTIVE and consistency.get("status") == "NOT_RUN":
        warnings.append({
            "level": "INFO",
            "indicator": "consistency_check_pending",
            "detail": "已触发 ACTIVE 状态但尚未运行 selfmodel_consistency_check.py",
        })
    # 4. ACTIVE 但最近一次一致性检查为 ISSUES_DETECTED
    if status == STATUS_ACTIVE and consistency.get("status") == "ISSUES_DETECTED":
        warnings.append({
            "level": "WARNING",
            "indicator": "consistency_issues",
            "detail": f"最近一致性检查发现 {consistency.get('issue_count', 0)} 个问题,需 review",
        })
    return warnings


# ============================================================
# 报告渲染
# ============================================================
def render_report(
    data_dir: Path,
    file_stats: Dict[str, Dict[str, Any]],
    increment: Dict[str, Dict[str, int]],
    status: str,
    trigger: Dict[str, Any],
    consistency: Dict[str, Any],
    warnings: List[Dict[str, str]],
) -> str:
    L: List[str] = []
    L.append("# SelfModel Runtime Daily Report — Phase C.4.6.2")
    L.append("")
    L.append(f"> **生成时间:** `{_human_ts()}`  ")
    L.append(f"> **数据源:** `{data_dir}`  ")
    L.append(f"> **当日状态:** **{status}**  ")
    L.append(f"> **一致性检查最近状态:** **{consistency.get('status', 'NOT_RUN')}**  ")
    L.append("")

    # 0. 状态概览
    L.append("## 0. 状态概览")
    L.append("")
    L.append(f"- **当前状态:** `{status}`")
    L.append(f"- **触发一致性检查:** {'✅ 是' if trigger['triggered'] else '❌ 否'} ({trigger['reason']})")
    L.append(f"- **一致性检查报告存在:** {'✅ 是' if consistency.get('report_exists') else '❌ 否'}")
    if consistency.get("last_run"):
        L.append(f"- **最近一致性检查时间:** `{consistency['last_run']}`")
    L.append(f"- **告警数:** {len(warnings)}")
    L.append("")

    # 1. 文件统计
    L.append("## 1. SelfModel 文件统计")
    L.append("")
    L.append("| 文件 | 存在 | 大小 | 行数 | 有效记录 | 状态 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for fname in SELF_MODEL_FILES:
        s = file_stats.get(fname, {})
        exists = "✅" if s.get("exists") else "❌"
        size = s.get("size_bytes", 0)
        size_str = _human_size(size)
        line_count = s.get("line_count", 0)
        record_count = s.get("record_count", 0)
        if s.get("exists") and not s.get("valid", True):
            file_status = "⚠️ 损坏"
        elif s.get("exists"):
            file_status = "✅ 正常"
        else:
            file_status = "—"
        L.append(f"| `{fname}` | {exists} | {size_str} | {line_count} | {record_count} | {file_status} |")
    L.append("")

    # 2. 每日新增趋势
    L.append("## 2. 每日新增趋势")
    L.append("")
    if not increment or all(inc.get("record_delta", 0) == 0 and inc.get("size_delta", 0) == 0
                            for inc in increment.values()):
        L.append("⏳ 暂无历史数据(首次运行或无上次 log)。")
    else:
        L.append("| 文件 | 记录新增 | 大小新增 |")
        L.append("| --- | --- | --- |")
        for fname in SELF_MODEL_FILES:
            inc = increment.get(fname, {})
            record_delta = inc.get("record_delta", 0)
            size_delta = inc.get("size_delta", 0)
            sign = "+" if record_delta >= 0 else ""
            L.append(f"| `{fname}` | {sign}{record_delta} | {sign}{_human_size(size_delta)} |")
    L.append("")

    # 3. 一致性检查状态
    L.append("## 3. Consistency 状态")
    L.append("")
    L.append(f"| 指标 | 数值 |")
    L.append(f"| --- | --- |")
    L.append(f"| 一致性检查报告存在 | {'✅' if consistency.get('report_exists') else '❌'} |")
    L.append(f"| 最近状态 | **{consistency.get('status', 'NOT_RUN')}** |")
    if consistency.get("issue_count") is not None:
        L.append(f"| 最近 issue 数 | {consistency['issue_count']} |")
    if consistency.get("last_run"):
        L.append(f"| 最近检查时间 | `{consistency['last_run']}` |")
    L.append("")
    if consistency.get("status") == "CONSISTENT":
        L.append("✅ 最近一致性检查通过 — SelfModel 进入 STABLE 状态。")
    elif consistency.get("status") == "ISSUES_DETECTED":
        L.append("⚠️ 最近一致性检查发现问题,需 review `selfmodel_consistency_report.md`。")
    elif consistency.get("status") == "NOT_RUN":
        L.append("⏳ 一致性检查尚未运行 — 数据累积至触发阈值后会自动建议。")
    L.append("")

    # 4. 状态机
    L.append("## 4. 状态机")
    L.append("")
    L.append("| 状态 | 含义 | 当前? |")
    L.append("| --- | --- | --- |")
    L.append(f"| `EMPTY` | 所有文件均不存在 | {'✅' if status == STATUS_EMPTY else '⬜'} |")
    L.append(f"| `INITIALIZING` | 文件存在但所有核心文件 < 10 条 | {'✅' if status == STATUS_INITIALIZING else '⬜'} |")
    L.append(f"| `ACTIVE` | 任一核心文件 ≥ 10 条,一致性未通过 | {'✅' if status == STATUS_ACTIVE else '⬜'} |")
    L.append(f"| `STABLE` | 最近一致性检查为 CONSISTENT | {'✅' if status == STATUS_STABLE else '⬜'} |")
    L.append("")

    # 5. 一致性触发条件
    L.append("## 5. 一致性检查触发条件")
    L.append("")
    L.append("| 条件 | 阈值 | 实际 | 状态 |")
    L.append("| --- | --- | --- | --- |")
    L.append(f"| `beliefs.jsonl` 行数 | > {CONSISTENCY_TRIGGER_THRESHOLD} | {trigger['beliefs_count']} | {'✅' if trigger['beliefs_count'] > CONSISTENCY_TRIGGER_THRESHOLD else '❌'} |")
    L.append(f"| `history.jsonl` 行数 | > {CONSISTENCY_TRIGGER_THRESHOLD} | {trigger['history_count']} | {'✅' if trigger['history_count'] > CONSISTENCY_TRIGGER_THRESHOLD else '❌'} |")
    L.append("")
    if trigger["triggered"]:
        L.append("⚠️ **建议立即执行一致性检查:**")
        L.append("")
        L.append("```bash")
        L.append("python scripts/selfmodel_consistency_check.py \\")
        L.append("    --data-dir data/self_model \\")
        L.append("    --report docs/audit/selfmodel_consistency_report.md")
        L.append("```")
        L.append("")
    else:
        L.append("⏳ 等待数据累积(当前触发条件未满足)。")
        L.append("")

    # 6. 告警
    L.append("## 6. 告警 / Warning")
    L.append("")
    if not warnings:
        L.append("✅ 当前无告警。")
    else:
        L.append(f"**共 {len(warnings)} 条告警:**")
        L.append("")
        for w in warnings:
            icon = {"ERROR": "❌", "WARNING": "⚠️", "INFO": "ℹ️"}.get(w["level"], "•")
            L.append(f"- {icon} **{w['level']}** `{w['indicator']}`: {w['detail']}")
    L.append("")

    # 7. 总体进度
    L.append("## 7. 总体进度")
    L.append("")
    total_records = sum(s.get("record_count", 0) for s in file_stats.values())
    total_size = sum(s.get("size_bytes", 0) for s in file_stats.values())
    L.append(f"- **SelfModel 总记录数:** {total_records}")
    L.append(f"- **SelfModel 总大小:** {_human_size(total_size)}")
    L.append(f"- **当日日期:** {_today_str()}")
    L.append("")

    # 8. 边界声明
    L.append("## 8. 边界声明")
    L.append("")
    L.append("- ✅ 仅审计,未修改任何 SelfModel 数据")
    L.append("- ✅ 未预填 beliefs / history / reflection / relationship")
    L.append("- ✅ 未触发一致性检查(本脚本不调用 consistency_check)")
    L.append("- ✅ 状态由文件实际内容 + 一致性报告决定")
    L.append("- ✅ 未修改 CoreIdentity / Growth / Memory")
    L.append("")

    L.append("---")
    L.append("")
    L.append("> **报告生成者:** `scripts/selfmodel_runtime_monitor.py` (Phase C.4.6.2)")
    L.append(f"> **生成时间:** {_human_ts()}")
    L.append("> **数据源:** `data/self_model/` (只读)")
    L.append("> **策略:** 只读监控 + 不修改核心逻辑 + 不预填数据")
    L.append("")

    return "\n".join(L)


# ============================================================
# 主流程
# ============================================================
def run_monitor(
    data_dir: Path,
    report: Path,
    consistency_report: Optional[Path] = None,
) -> Dict[str, Any]:
    """执行监控并生成报告。

    返回:状态摘要 dict
    """
    if consistency_report is None:
        consistency_report = DEFAULT_CONSISTENCY_REPORT

    # 1. 收集 4 个文件的统计信息
    file_stats: Dict[str, Dict[str, Any]] = {}
    for fname in SELF_MODEL_FILES:
        file_stats[fname] = _file_stats(data_dir / fname)

    # 2. 读取一致性检查状态
    consistency = read_consistency_status(consistency_report)

    # 3. 判定状态
    status = determine_status(file_stats, consistency)

    # 4. 检查一致性触发条件
    trigger = check_consistency_trigger(file_stats)

    # 5. 计算每日新增(对比上一次 log)
    last_log = read_last_log_for(data_dir)
    increment = calc_daily_increment(file_stats, last_log)

    # 6. 检测告警
    warnings = detect_warnings(file_stats, increment, consistency, status)

    # 7. 渲染报告
    report.parent.mkdir(parents=True, exist_ok=True)
    content = render_report(data_dir, file_stats, increment, status, trigger, consistency, warnings)
    report.write_text(content, encoding="utf-8")

    # 8. 记录日志
    summary = {
        "data_dir": str(data_dir),
        "status": status,
        "trigger": trigger,
        "file_stats": {
            fname: {
                "exists": s.get("exists"),
                "size_bytes": s.get("size_bytes", 0),
                "record_count": s.get("record_count", 0),
                "valid": s.get("valid", True),
            }
            for fname, s in file_stats.items()
        },
        "consistency": consistency,
        "warnings_count": len(warnings),
    }
    _log("selfmodel_runtime_monitor", summary)
    return summary


# ============================================================
# CLI
# ============================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SelfModel 运行时监控")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--consistency-report",
        type=Path,
        default=DEFAULT_CONSISTENCY_REPORT,
        help="一致性检查报告路径(用于 STABLE 状态判定)",
    )
    args = parser.parse_args(argv)

    print(f"[selfmodel-monitor] data_dir          = {args.data_dir}")
    print(f"[selfmodel-monitor] report            = {args.report}")
    print(f"[selfmodel-monitor] consistency_report= {args.consistency_report}")

    summary = run_monitor(args.data_dir, args.report, args.consistency_report)

    print("=" * 60)
    print(f"status: {summary['status']}")
    print(f"consistency: {summary['consistency'].get('status', 'NOT_RUN')}")
    print(f"trigger: {summary['trigger']['reason']}")
    print(f"warnings: {summary['warnings_count']}")
    for fname, s in summary["file_stats"].items():
        print(f"  {fname}: exists={s['exists']} records={s['record_count']} valid={s.get('valid', True)}")
    print(f"report: {args.report}")
    print("=" * 60)

    return 1 if summary["warnings_count"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
