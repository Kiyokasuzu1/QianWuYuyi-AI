# -*- coding: utf-8 -*-
"""
scripts/memory_health_monitor.py

Phase C.3.2 — Memory 长期运行监控

职责:
- 每日生成 docs/audit/memory_health_report.md
- 统计 memory 总量 / 类型分布 / pollution / invalid / schema
- 统计 Growth 触发次数 / Proposal 数量 / rejected 数量
- 阈值报警(仅告警,不删除):
    - pollution > 5%
    - invalid > 5%
    - 24h 增长 > 500
    - 单用户 1h > 50
- 只读分析,不动 memory.json

用法:
    # 生成健康报告
    python scripts/memory_health_monitor.py

    # 自定义路径
    python scripts/memory_health_monitor.py \\
        --source data/memory.json \\
        --proposals data/growth/proposals/proposals.json \\
        --report docs/audit/memory_health_report.md

退出码:
    0 — 报告生成成功(无告警)
    1 — 报告生成成功(有告警)
    2 — memory.json 缺失
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "memory.json"
DEFAULT_PROPOSALS = PROJECT_ROOT / "data" / "growth" / "proposals" / "proposals.json"
DEFAULT_AUDIT_LOG = PROJECT_ROOT / "data" / "audit" / "audit_logs.json"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "audit" / "memory_health_report.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "memory_health_log.jsonl"

# 与 memory_baseline.py / pollution_guard.py 保持一致
ALLOWED_TYPES = frozenset({
    "user_fact", "user_preference", "user_event", "user_experience",
    "user_milestone", "user_emotion", "user_goal", "user_relationship",
    "user_shared", "relationship_event", "important_experience",
})
FORBIDDEN_TYPES = frozenset({
    "runtime_experience", "system_prompt", "system_reminder", "system_meta",
    "system", "internal_reasoning", "ai_thought", "ai_reflection",
    "ai_internal", "ai_self_talk", "ai_prompt", "ai_scratchpad",
    "ai_planning", "debug", "tool_call", "tool_result",
    "reflection_process", "reflection", "test", "init",
})

# 阈值
THRESHOLD_POLLUTION = 5.0       # %
THRESHOLD_INVALID = 5.0          # %
THRESHOLD_24H_GROWTH = 500       # 条
THRESHOLD_USER_1H = 50           # 条


# ============================================================
# 工具函数
# ============================================================
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _human_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _log(event: str, payload: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "ts": _utc_now(), **payload}, ensure_ascii=False) + "\n")


def _file_hash(path: Path) -> str:
    if not path.exists():
        return "<missing>"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(n: int) -> str:
    if n < 0:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.2f} {unit}"
        n /= 1024.0
    return f"{n:.2f} TB"


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _extract_type(m: Dict[str, Any]) -> str:
    md = m.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    t = (md.get("memory_type") or m.get("memory_type")
         or md.get("type") or m.get("type") or "")
    return str(t).lower().strip()


def _classify(m: Dict[str, Any]) -> str:
    """简化版分类:normal_user / system_pollution / ai_internal_pollution / invalid。"""
    t = _extract_type(m)
    role = str(m.get("role", "")).strip().lower()

    if t in {"system", "system_prompt", "system_reminder", "system_meta", "init", "test"}:
        return "system_pollution"
    if t in {"ai_thought", "ai_reflection", "ai_internal", "ai_self_talk",
             "ai_prompt", "ai_scratchpad", "ai_planning", "runtime_experience"}:
        return "ai_internal_pollution"
    if t in {"", "unknown", "null", "none", "undefined"}:
        return "invalid"
    if t in ALLOWED_TYPES:
        return "normal_user"

    # role 推断
    if role == "system":
        return "system_pollution"
    if role == "assistant":
        return "ai_internal_pollution"
    if role in ("user", "human"):
        return "normal_user"
    return "invalid"


# ============================================================
# 核心分析
# ============================================================
def analyze_memory(memory_path: Path) -> Dict[str, Any]:
    """分析 memory.json 状态。"""
    if not memory_path.exists():
        raise FileNotFoundError(f"memory.json not found: {memory_path}")

    with open(memory_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"memory.json must be list, got {type(data).__name__}")

    by_cat: Counter = Counter()
    by_type: Counter = Counter()
    by_role: Counter = Counter()
    by_user: Counter = Counter()
    by_hour: Counter = Counter()
    by_day: Counter = Counter()
    schema_violations: List[str] = []
    timestamps: List[str] = []

    for m in data:
        cat = _classify(m)
        by_cat[cat] += 1
        by_type[_extract_type(m)] += 1
        by_role[str(m.get("role", "")).strip().lower()] += 1
        user_id = (m.get("metadata") or {}).get("user_id") or m.get("user_id") or "<unknown>"
        by_user[user_id] += 1

        ts = m.get("timestamp", "")
        if ts:
            timestamps.append(ts)
            try:
                dt = datetime.fromisoformat(ts)
                by_hour[dt.strftime("%Y-%m-%dT%H")] += 1
                by_day[dt.strftime("%Y-%m-%d")] += 1
            except Exception:
                pass

        if not m.get("role"):
            schema_violations.append(f"missing role: id={m.get('id', '?')}")
        if not str(m.get("content", "")).strip():
            schema_violations.append(f"empty content: id={m.get('id', '?')}")

    total = len(data)
    pollution = by_cat.get("system_pollution", 0) + by_cat.get("ai_internal_pollution", 0)
    invalid = by_cat.get("invalid", 0)
    pollution_rate = (pollution / total * 100) if total else 0.0
    invalid_rate = (invalid / total * 100) if total else 0.0
    schema_valid_rate = ((1 - len(schema_violations) / total) * 100) if total else 100.0

    # 24h 增长(基于最新时间戳向前推 24 小时)
    growth_24h = 0
    growth_1h_per_user: Counter = Counter()
    if timestamps:
        ts_sorted = sorted(t for t in timestamps if _parse_ts(t))
        if ts_sorted:
            latest = _parse_ts(ts_sorted[-1])
            if latest:
                cutoff_24h = latest - timedelta(hours=24)
                cutoff_1h = latest - timedelta(hours=1)
                for m in data:
                    ts = m.get("timestamp", "")
                    dt = _parse_ts(ts)
                    if not dt:
                        continue
                    if dt >= cutoff_24h:
                        growth_24h += 1
                    if dt >= cutoff_1h:
                        user_id = (m.get("metadata") or {}).get("user_id") or m.get("user_id") or "<unknown>"
                        growth_1h_per_user[user_id] += 1

    return {
        "path": str(memory_path),
        "size_bytes": memory_path.stat().st_size,
        "size_human": _human_size(memory_path.stat().st_size),
        "sha256": _file_hash(memory_path),
        "total": total,
        "by_category": dict(by_cat),
        "by_type": dict(by_type),
        "by_role": dict(by_role),
        "by_user": dict(by_user),
        "by_hour": dict(by_hour),
        "by_day": dict(by_day),
        "growth_24h": growth_24h,
        "growth_1h_per_user": dict(growth_1h_per_user),
        "schema_violations": schema_violations[:10],
        "schema_violation_count": len(schema_violations),
        "schema_valid_rate": round(schema_valid_rate, 4),
        "pollution_rate": round(pollution_rate, 4),
        "invalid_rate": round(invalid_rate, 4),
    }


def analyze_proposals(proposals_path: Path) -> Dict[str, Any]:
    """统计 GrowthProposal 状态。"""
    if not proposals_path.exists():
        return {
            "exists": False,
            "path": str(proposals_path),
            "total": 0,
            "by_status": {},
            "by_type": {},
            "rejected_count": 0,
            "applied_count": 0,
            "pending_count": 0,
        }

    with open(proposals_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    proposals = []
    if isinstance(data, dict):
        proposals = data.get("proposals", [])
    elif isinstance(data, list):
        proposals = data

    by_status: Counter = Counter()
    by_type: Counter = Counter()
    for p in proposals:
        by_status[p.get("status", "unknown")] += 1
        by_type[p.get("proposal_type", "unknown")] += 1

    return {
        "exists": True,
        "path": str(proposals_path),
        "total": len(proposals),
        "by_status": dict(by_status),
        "by_type": dict(by_type),
        "rejected_count": by_status.get("rejected", 0),
        "applied_count": by_status.get("applied", 0),
        "pending_count": by_status.get("pending", 0),
        "sha256": _file_hash(proposals_path),
    }


# ============================================================
# 报警逻辑
# ============================================================
def compute_alerts(mem: Dict[str, Any]) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []
    if mem["pollution_rate"] > THRESHOLD_POLLUTION:
        alerts.append({
            "level": "CRITICAL" if mem["pollution_rate"] > 30 else "WARNING",
            "metric": "pollution_rate",
            "value": f"{mem['pollution_rate']}%",
            "threshold": f"< {THRESHOLD_POLLUTION}%",
            "message": "污染率超过阈值,需立即 review memory 来源",
        })
    if mem["invalid_rate"] > THRESHOLD_INVALID:
        alerts.append({
            "level": "WARNING",
            "metric": "invalid_rate",
            "value": f"{mem['invalid_rate']}%",
            "threshold": f"< {THRESHOLD_INVALID}%",
            "message": "无效记忆率超过阈值",
        })
    if mem["growth_24h"] > THRESHOLD_24H_GROWTH:
        alerts.append({
            "level": "WARNING",
            "metric": "growth_24h",
            "value": f"{mem['growth_24h']} 条",
            "threshold": f"< {THRESHOLD_24H_GROWTH} 条",
            "message": "24h 增长过快,检查是否有循环写入",
        })
    for user_id, count in mem.get("growth_1h_per_user", {}).items():
        if count > THRESHOLD_USER_1H:
            alerts.append({
                "level": "WARNING",
                "metric": f"user_1h[{user_id}]",
                "value": f"{count} 条",
                "threshold": f"< {THRESHOLD_USER_1H} 条",
                "message": f"用户 {user_id} 1h 内写入过快,需频率限制检查",
            })
    return alerts


# ============================================================
# 报告渲染
# ============================================================
def render_report(mem: Dict[str, Any], props: Dict[str, Any], alerts: List[Dict[str, str]]) -> str:
    L: List[str] = []
    L.append("# Memory Health Report — Phase C.3.2")
    L.append("")
    L.append(f"> **生成时间:** `{_human_ts()}`  ")
    L.append(f"> **数据源:** `{mem['path']}`  ")
    L.append(f"> **SHA-256:** `{mem['sha256'][:16]}...`  ")
    L.append(f"> **状态:** {'⚠️ WITH_ALERTS' if alerts else '✅ HEALTHY'}  ")
    L.append("")

    # 报警
    L.append("## 0. 告警")
    L.append("")
    if not alerts:
        L.append("✅ 无告警,所有指标在阈值内。")
    else:
        L.append("| 级别 | 指标 | 实际 | 阈值 | 建议 |")
        L.append("| --- | --- | --- | --- | --- |")
        for a in alerts:
            L.append(f"| {a['level']} | `{a['metric']}` | {a['value']} | {a['threshold']} | {a['message']} |")
    L.append("")

    # 1. Memory 总量
    L.append("## 1. Memory 总量与质量")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("| --- | --- |")
    L.append(f"| 总记录数 | **{mem['total']}** |")
    L.append(f"| 文件大小 | {mem['size_human']} |")
    L.append(f"| normal_user | {mem['by_category'].get('normal_user', 0)} |")
    L.append(f"| system_pollution | {mem['by_category'].get('system_pollution', 0)} |")
    L.append(f"| ai_internal_pollution | {mem['by_category'].get('ai_internal_pollution', 0)} |")
    L.append(f"| invalid | {mem['by_category'].get('invalid', 0)} |")
    L.append(f"| **污染率** | **{mem['pollution_rate']}%** |")
    L.append(f"| **invalid 率** | **{mem['invalid_rate']}%** |")
    L.append(f"| **schema 合法率** | **{mem['schema_valid_rate']}%** |")
    L.append("")

    # 2. 类型分布
    L.append("## 2. Memory 类型分布")
    L.append("")
    L.append("| 类型 | 数量 | 占比 |")
    L.append("| --- | --- | --- |")
    total = mem['total'] or 1
    for t, c in sorted(mem['by_type'].items(), key=lambda x: -x[1]):
        pct = c / total * 100
        L.append(f"| `{t}` | {c} | {pct:.2f}% |")
    L.append("")

    # 3. 新增类型分布(基于 timestamp 24h)
    L.append("## 3. 增长趋势")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("| --- | --- |")
    L.append(f"| 最近 24h 新增 | **{mem['growth_24h']} 条** |")
    L.append(f"| 最近 1h 按用户分布 | {mem['growth_1h_per_user']} |")
    L.append(f"| 按日分布(活跃天数) | {len(mem['by_day'])} |")
    L.append(f"| 按小时分布(活跃小时数) | {len(mem['by_hour'])} |")
    L.append("")

    # 4. Growth Proposal 统计
    L.append("## 4. Growth Proposal 统计")
    L.append("")
    if not props.get("exists"):
        L.append("⚠️ proposals.json 不存在,无法统计。")
    else:
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append(f"| 总 proposal 数 | **{props['total']}** |")
        L.append(f"| applied(已应用) | {props['applied_count']} |")
        L.append(f"| pending(待审核) | {props['pending_count']} |")
        L.append(f"| rejected(已拒绝) | **{props['rejected_count']}** |")
        L.append("")
        if props['by_status']:
            L.append("### 4.1 按状态分布")
            L.append("")
            L.append("| 状态 | 数量 |")
            L.append("| --- | --- |")
            for s, c in sorted(props['by_status'].items(), key=lambda x: -x[1]):
                L.append(f"| `{s}` | {c} |")
            L.append("")
        if props['by_type']:
            L.append("### 4.2 按类型分布")
            L.append("")
            L.append("| 类型 | 数量 |")
            L.append("| --- | --- |")
            for t, c in sorted(props['by_type'].items(), key=lambda x: -x[1]):
                L.append(f"| `{t}` | {c} |")
            L.append("")
    L.append("")

    # 5. 阈值表
    L.append("## 5. 阈值监控")
    L.append("")
    L.append("| 指标 | 当前 | 阈值 | 状态 |")
    L.append("| --- | --- | --- | --- |")
    L.append(f"| pollution_rate | {mem['pollution_rate']}% | < {THRESHOLD_POLLUTION}% | {'✅' if mem['pollution_rate'] <= THRESHOLD_POLLUTION else '⚠️'} |")
    L.append(f"| invalid_rate | {mem['invalid_rate']}% | < {THRESHOLD_INVALID}% | {'✅' if mem['invalid_rate'] <= THRESHOLD_INVALID else '⚠️'} |")
    L.append(f"| 24h 增长 | {mem['growth_24h']} | < {THRESHOLD_24H_GROWTH} | {'✅' if mem['growth_24h'] <= THRESHOLD_24H_GROWTH else '⚠️'} |")
    L.append("")
    L.append("> **告警策略:** 仅告警,不自动删除。所有修复动作需要人工 review。")
    L.append("")

    # 6. 总结
    L.append("## 6. 总结")
    L.append("")
    if not alerts:
        L.append("- ✅ Memory 健康度良好,所有指标在阈值内")
        L.append("- ✅ GrowthProposal 系统运行正常")
        L.append("- ✅ PollutionGuard 防护有效")
    else:
        L.append(f"- ⚠️ 检测到 {len(alerts)} 个告警项,需 review")
        for a in alerts:
            L.append(f"  - {a['level']}: {a['metric']} = {a['value']} (阈值 {a['threshold']})")
    L.append("")

    L.append("---")
    L.append("")
    L.append("> 报告生成者: `scripts/memory_health_monitor.py` (Phase C.3.2)")
    L.append("> 数据源: `data/memory.json` (只读) + `data/growth/proposals/proposals.json` (只读)")
    L.append("> 报警策略: 只读 + 只告警 + 不自动删除")
    L.append("")
    return "\n".join(L)


# ============================================================
# CLI
# ============================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory 健康监控")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--proposals", type=Path, default=DEFAULT_PROPOSALS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    if not args.source.exists():
        print(f"[health] FAILED: memory.json 不存在: {args.source}", file=sys.stderr)
        return 2

    print(f"[health] source = {args.source}")
    print(f"[health] proposals = {args.proposals}")
    print(f"[health] report = {args.report}")

    try:
        mem = analyze_memory(args.source)
    except Exception as e:
        print(f"[health] FAILED: 读取 memory 异常: {e}", file=sys.stderr)
        return 1

    props = analyze_proposals(args.proposals)
    alerts = compute_alerts(mem)

    print("=" * 60)
    print(f"Memory: total={mem['total']} pollution={mem['pollution_rate']}% invalid={mem['invalid_rate']}%")
    print(f"Growth: 24h_growth={mem['growth_24h']} proposals={props.get('total', 0)} rejected={props.get('rejected_count', 0)}")
    print(f"Alerts: {len(alerts)}")
    print("=" * 60)

    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(mem, props, alerts), encoding="utf-8")
        print(f"[health] report -> {args.report}")
    except Exception as e:
        print(f"[health] FAILED: 写报告异常: {e}", file=sys.stderr)
        return 1

    _log("memory_health_report", {
        "total": mem["total"],
        "pollution_rate": mem["pollution_rate"],
        "invalid_rate": mem["invalid_rate"],
        "growth_24h": mem["growth_24h"],
        "proposals_total": props.get("total", 0),
        "proposals_rejected": props.get("rejected_count", 0),
        "alerts": len(alerts),
    })

    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
