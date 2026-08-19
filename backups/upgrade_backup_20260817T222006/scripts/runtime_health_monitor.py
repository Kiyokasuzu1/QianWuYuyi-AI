# -*- coding: utf-8 -*-
"""
scripts/runtime_health_monitor.py

Phase C.4.1 — Runtime 观察层 (Real Runtime Validation)

职责:
- 每日生成 docs/audit/runtime_health_report.md
- 监控真实运行数据(只读):
  * 每日对话数量
  * Memory 写入数量
  * Memory 类型变化
  * Growth Proposal 数量
  * rejected proposal 原因
  * SelfModel 变化次数
  * CoreIdentity 变化尝试次数
  * LLM 错误次数

约束:
- 只读监控,不修改任何核心模块
- 不调用 LLM,无副作用
- 所有数据来自文件(只读)
- 仅生成报告,不改数据

用法:
    python scripts/runtime_health_monitor.py
    python scripts/runtime_health_monitor.py \\
        --data-dir data \\
        --report docs/audit/runtime_health_report.md

退出码:
    0 — 报告生成成功(健康)
    1 — 报告生成成功(有告警)
    2 — 关键数据缺失
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "audit" / "runtime_health_report.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "runtime_health_log.jsonl"

# 阈值(每日)
THRESHOLD_DAILY_DIALOGS_LOW = 1       # < 1 视为无活动
THRESHOLD_DAILY_DIALOGS_HIGH = 5000   # > 5000 视为异常高
THRESHOLD_LLM_ERROR_RATE = 0.05       # > 5% 视为异常
THRESHOLD_CORE_IDENTITY_ATTEMPTS = 1  # > 0 即告警(identity 不应被尝试改变)


# ============================================================
# 工具函数
# ============================================================
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _human_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        return datetime.fromisoformat(ts.replace("Z", ""))
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def _read_json(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================
# 1. 对话数量统计(从 memory.json 推断)
# ============================================================
def analyze_dialog_count(memory_path: Path) -> Dict[str, Any]:
    """
    通过 memory.json 中的 role=user 记录数估算对话数量。
    注:这是一个近似值,因为同一对话可能产生多条 user memory。
    """
    data = _read_json(memory_path)
    if not isinstance(data, list):
        return {"available": False, "error": "memory.json 格式错误", "total_dialogs": 0}

    user_msgs = [m for m in data if m.get("role") == "user"]
    return {
        "available": True,
        "total_user_turns": len(user_msgs),
        "total_turns": len(data),
        # 估算:每条 user memory ≈ 1 个对话轮次(简化假设)
        "estimated_dialogs": len(user_msgs),
    }


# ============================================================
# 2. Memory 写入与类型变化
# ============================================================
def analyze_memory_writes(memory_path: Path) -> Dict[str, Any]:
    """分析 Memory 写入情况。"""
    data = _read_json(memory_path)
    if not isinstance(data, list):
        return {"available": False, "error": "memory.json 格式错误", "total": 0}

    by_type: Counter = Counter()
    by_day: Counter = Counter()
    by_hour: Counter = Counter()
    by_user: Counter = Counter()

    for m in data:
        md = m.get("metadata") or {}
        t = md.get("memory_type") or m.get("memory_type") or "unknown"
        by_type[t] += 1

        user_id = md.get("user_id") or m.get("user_id") or "<unknown>"
        by_user[user_id] += 1

        ts = m.get("timestamp", "")
        if not ts and isinstance(m.get("metadata"), dict):
            ts = m["metadata"].get("timestamp", "")
        if ts:
            try:
                dt = _parse_ts(ts)
                if dt:
                    by_day[dt.strftime("%Y-%m-%d")] += 1
                    by_hour[dt.strftime("%Y-%m-%dT%H")] += 1
            except Exception:
                pass

    return {
        "available": True,
        "total": len(data),
        "by_type": dict(by_type),
        "by_day": dict(by_day),
        "by_hour": dict(by_hour),
        "by_user": dict(by_user),
        "active_days": len(by_day),
        "active_hours": len(by_hour),
    }


# ============================================================
# 3. Growth Proposal 统计
# ============================================================
def analyze_proposals(proposals_path: Path) -> Dict[str, Any]:
    """统计 GrowthProposal 状态。"""
    # 兼容两种存储:proposals.json (dict/proposals) 和 proposals.jsonl (jsonl)
    proposals: List[Dict[str, Any]] = []

    if proposals_path.exists():
        if proposals_path.suffix == ".jsonl":
            proposals = _read_jsonl(proposals_path)
        else:
            data = _read_json(proposals_path)
            if isinstance(data, dict) and "proposals" in data:
                proposals = data["proposals"]
            elif isinstance(data, list):
                proposals = data

    if not proposals:
        return {
            "available": False,
            "path": str(proposals_path),
            "total": 0,
            "by_status": {},
            "by_type": {},
            "rejected_count": 0,
        }

    by_status: Counter = Counter()
    by_type: Counter = Counter()
    rejected_reasons: Counter = Counter()

    for p in proposals:
        # 兼容两种 schema (v1 with proposal_id, v2 with id)
        status = p.get("status", "unknown")
        ptype = p.get("proposal_type") or p.get("type", "unknown")
        by_status[status] += 1
        by_type[ptype] += 1

        if status == "rejected":
            # 收集拒绝原因
            reason = p.get("reason") or p.get("review_comment") or p.get("rejection_reason") or "unspecified"
            rejected_reasons[reason] += 1

    return {
        "available": True,
        "path": str(proposals_path),
        "total": len(proposals),
        "by_status": dict(by_status),
        "by_type": dict(by_type),
        "rejected_count": by_status.get("rejected", 0),
        "rejected_reasons": dict(rejected_reasons),
        "pending_count": by_status.get("pending", 0) + by_status.get("proposed", 0),
        "applied_count": by_status.get("applied", 0) + by_status.get("accepted", 0),
    }


# ============================================================
# 4. SelfModel 变化统计
# ============================================================
def analyze_selfmodel(selfmodel_dir: Path) -> Dict[str, Any]:
    """检查 SelfModel 数据变化情况。"""
    if not selfmodel_dir.exists():
        return {
            "available": False,
            "path": str(selfmodel_dir),
            "data_status": "not_initialized",
        }

    beliefs_path = selfmodel_dir / "beliefs.jsonl"
    history_path = selfmodel_dir / "history.jsonl"
    reflection_path = selfmodel_dir / "reflection.jsonl"

    beliefs = _read_jsonl(beliefs_path)
    history = _read_jsonl(history_path)
    reflection = _read_jsonl(reflection_path)

    return {
        "available": True,
        "path": str(selfmodel_dir),
        "data_status": "initialized" if (beliefs or history or reflection) else "empty",
        "beliefs_count": len(beliefs),
        "history_count": len(history),
        "reflection_count": len(reflection),
    }


# ============================================================
# 5. CoreIdentity 变化尝试次数
# ============================================================
def analyze_core_identity_attempts(proposals_path: Path) -> Dict[str, Any]:
    """
    统计尝试改变 CoreIdentity 的 proposal 数。
    任何 proposal 描述包含 CoreIdentity 禁止关键词的,都计入 attempts。
    """
    forbidden_keywords = ["变得冷漠", "变得攻击性", "失去温柔", "完全改变人格"]

    proposals: List[Dict[str, Any]] = []
    if proposals_path.exists():
        if proposals_path.suffix == ".jsonl":
            proposals = _read_jsonl(proposals_path)
        else:
            data = _read_json(proposals_path)
            if isinstance(data, dict) and "proposals" in data:
                proposals = data["proposals"]
            elif isinstance(data, list):
                proposals = data

    attempts: List[Dict[str, Any]] = []
    for p in proposals:
        # 搜索可能包含描述的字段
        text = json.dumps(p, ensure_ascii=False)
        for kw in forbidden_keywords:
            if kw in text:
                attempts.append({
                    "proposal_id": p.get("id") or p.get("proposal_id"),
                    "matched_keyword": kw,
                    "status": p.get("status", "unknown"),
                })
                break

    return {
        "total_attempts": len(attempts),
        "attempts": attempts[:20],  # 最多展示 20 条
        "forbidden_keywords": forbidden_keywords,
    }


# ============================================================
# 6. LLM 错误次数
# ============================================================
def analyze_llm_errors(llm_failures_path: Path) -> Dict[str, Any]:
    """统计 LLM 错误。"""
    if not llm_failures_path.exists():
        return {
            "available": False,
            "path": str(llm_failures_path),
            "total_errors": 0,
            "by_type": {},
        }

    errors = _read_jsonl(llm_failures_path)
    by_type: Counter = Counter()
    for e in errors:
        err_type = e.get("error_type") or e.get("type") or "unknown"
        by_type[err_type] += 1

    return {
        "available": True,
        "path": str(llm_failures_path),
        "total_errors": len(errors),
        "by_type": dict(by_type),
    }


# ============================================================
# 报警逻辑
# ============================================================
def compute_alerts(
    dialog: Dict[str, Any],
    memory: Dict[str, Any],
    proposals: Dict[str, Any],
    identity: Dict[str, Any],
    llm_errors: Dict[str, Any],
) -> List[Dict[str, str]]:
    alerts: List[Dict[str, str]] = []

    # 对话数告警
    if dialog.get("available"):
        n = dialog.get("estimated_dialogs", 0)
        if n < THRESHOLD_DAILY_DIALOGS_LOW:
            alerts.append({
                "level": "INFO",
                "metric": "daily_dialogs",
                "value": f"{n} 条",
                "threshold": f">= {THRESHOLD_DAILY_DIALOGS_LOW} 条",
                "message": "对话数低于基线,可能系统未在运行",
            })
        elif n > THRESHOLD_DAILY_DIALOGS_HIGH:
            alerts.append({
                "level": "WARNING",
                "metric": "daily_dialogs",
                "value": f"{n} 条",
                "threshold": f"< {THRESHOLD_DAILY_DIALOGS_HIGH} 条",
                "message": "对话数异常高,检查是否有循环",
            })

    # Proposal 累积
    if proposals.get("available"):
        pending = proposals.get("pending_count", 0)
        if pending > 100:
            alerts.append({
                "level": "WARNING",
                "metric": "proposals_pending",
                "value": f"{pending} 条",
                "threshold": "< 100 条",
                "message": "Pending proposal 累积过多,需人工 review (Phase C.4.3 提供 review 工具)",
            })

    # CoreIdentity 变化尝试
    if identity.get("total_attempts", 0) >= THRESHOLD_CORE_IDENTITY_ATTEMPTS:
        attempts = identity.get("total_attempts", 0)
        alerts.append({
            "level": "CRITICAL",
            "metric": "core_identity_attempts",
            "value": f"{attempts} 次",
            "threshold": f"= 0 次",
            "message": "检测到尝试改变 CoreIdentity 的 proposal,需立即 review",
        })

    # LLM 错误率
    if llm_errors.get("available") and dialog.get("available"):
        total_d = dialog.get("estimated_dialogs", 0)
        total_e = llm_errors.get("total_errors", 0)
        if total_d > 0:
            err_rate = total_e / total_d * 100
            if err_rate > THRESHOLD_LLM_ERROR_RATE * 100:
                alerts.append({
                    "level": "WARNING",
                    "metric": "llm_error_rate",
                    "value": f"{err_rate:.2f}%",
                    "threshold": f"< {THRESHOLD_LLM_ERROR_RATE * 100}%",
                    "message": "LLM 错误率超阈值,检查网络/API",
                })

    return alerts


# ============================================================
# 报告渲染
# ============================================================
def render_report(
    dialog: Dict[str, Any],
    memory: Dict[str, Any],
    proposals: Dict[str, Any],
    selfmodel: Dict[str, Any],
    identity: Dict[str, Any],
    llm_errors: Dict[str, Any],
    alerts: List[Dict[str, str]],
) -> str:
    L: List[str] = []
    L.append("# Runtime Health Report — Phase C.4.1")
    L.append("")
    L.append(f"> **生成时间:** `{_human_ts()}`  ")
    L.append(f"> **状态:** {'⚠️ WITH_ALERTS' if alerts else '✅ HEALTHY'}  ")
    L.append("")

    # 0. 告警
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

    # 1. 对话数量
    L.append("## 1. 对话数量")
    L.append("")
    if dialog.get("available"):
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append(f"| 估算对话轮次(基于 user role) | **{dialog['estimated_dialogs']}** |")
        L.append(f"| user turn 数 | {dialog['total_user_turns']} |")
        L.append(f"| 总 turn 数 | {dialog['total_turns']} |")
    else:
        L.append("⚠️ 对话数据不可用。")
    L.append("")

    # 2. Memory 写入与类型
    L.append("## 2. Memory 写入与类型")
    L.append("")
    if memory.get("available"):
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append(f"| 总记忆数 | **{memory['total']}** |")
        L.append(f"| 活跃天数 | {memory['active_days']} |")
        L.append(f"| 活跃小时数 | {memory['active_hours']} |")
        L.append(f"| 用户数 | {len(memory['by_user'])} |")
        L.append("")
        L.append("### 2.1 类型分布")
        L.append("")
        L.append("| 类型 | 数量 | 占比 |")
        L.append("| --- | --- | --- |")
        total = memory['total'] or 1
        for t, c in sorted(memory['by_type'].items(), key=lambda x: -x[1]):
            pct = c / total * 100
            L.append(f"| `{t}` | {c} | {pct:.2f}% |")
    else:
        L.append("⚠️ Memory 数据不可用。")
    L.append("")

    # 3. Growth Proposal
    L.append("## 3. Growth Proposal 状态")
    L.append("")
    if proposals.get("available"):
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append(f"| 总 proposal | **{proposals['total']}** |")
        L.append(f"| pending(待审核) | {proposals['pending_count']} |")
        L.append(f"| applied/accepted(已应用) | {proposals['applied_count']} |")
        L.append(f"| rejected(已拒绝) | **{proposals['rejected_count']}** |")
        L.append("")
        if proposals.get('rejected_reasons'):
            L.append("### 3.1 拒绝原因分布")
            L.append("")
            L.append("| 原因 | 数量 |")
            L.append("| --- | --- |")
            for r, c in sorted(proposals['rejected_reasons'].items(), key=lambda x: -x[1]):
                L.append(f"| `{r}` | {c} |")
            L.append("")
        if proposals.get('by_status'):
            L.append("### 3.2 按状态分布")
            L.append("")
            L.append("| 状态 | 数量 |")
            L.append("| --- | --- |")
            for s, c in sorted(proposals['by_status'].items(), key=lambda x: -x[1]):
                L.append(f"| `{s}` | {c} |")
            L.append("")
        if proposals.get('by_type'):
            L.append("### 3.3 按类型分布")
            L.append("")
            L.append("| 类型 | 数量 |")
            L.append("| --- | --- |")
            for t, c in sorted(proposals['by_type'].items(), key=lambda x: -x[1]):
                L.append(f"| `{t}` | {c} |")
            L.append("")
    else:
        L.append("⚠️ proposals 数据不可用。")
    L.append("")

    # 4. SelfModel 变化
    L.append("## 4. SelfModel 变化")
    L.append("")
    if not selfmodel.get("available"):
        L.append("⚠️ SelfModel 数据目录未初始化。")
        L.append("")
        L.append("> SelfModel 数据将在真实交互后自然生成。")
        L.append("> 检查脚本: `scripts/selfmodel_consistency_check.py` (Phase C.3.4)")
    else:
        L.append(f"| 指标 | 数值 |")
        L.append(f"| --- | --- |")
        L.append(f"| 数据状态 | {selfmodel['data_status']} |")
        L.append(f"| beliefs 记录数 | {selfmodel['beliefs_count']} |")
        L.append(f"| history 记录数 | {selfmodel['history_count']} |")
        L.append(f"| reflection 记录数 | {selfmodel['reflection_count']} |")
    L.append("")

    # 5. CoreIdentity 变化尝试
    L.append("## 5. CoreIdentity 变化尝试")
    L.append("")
    L.append(f"| 指标 | 数值 |")
    L.append(f"| --- | --- |")
    L.append(f"| 禁止关键词 | {', '.join(identity['forbidden_keywords'])} |")
    L.append(f"| 检测到尝试数 | **{identity['total_attempts']}** |")
    L.append("")
    if identity.get('attempts'):
        L.append("### 5.1 尝试详情(最多 20 条)")
        L.append("")
        L.append("| proposal_id | matched_keyword | status |")
        L.append("| --- | --- | --- |")
        for a in identity['attempts']:
            L.append(f"| `{a['proposal_id']}` | {a['matched_keyword']} | {a['status']} |")
        L.append("")
    else:
        L.append("✅ 未检测到尝试改变 CoreIdentity 的 proposal。")
        L.append("")

    # 6. LLM 错误
    L.append("## 6. LLM 错误统计")
    L.append("")
    if llm_errors.get("available"):
        L.append("| 指标 | 数值 |")
        L.append("| --- | --- |")
        L.append(f"| 总错误数 | **{llm_errors['total_errors']}** |")
        L.append("")
        if llm_errors.get('by_type'):
            L.append("### 6.1 错误类型分布")
            L.append("")
            L.append("| 类型 | 数量 |")
            L.append("| --- | --- |")
            for t, c in sorted(llm_errors['by_type'].items(), key=lambda x: -x[1]):
                L.append(f"| `{t}` | {c} |")
            L.append("")
    else:
        L.append("✅ 无 LLM 错误记录(可能: 1) 暂无错误日志文件; 2) 真实 LLM 调用尚未发生)。")
    L.append("")

    # 7. 总结
    L.append("## 7. 总结")
    L.append("")
    if not alerts:
        L.append("- ✅ Runtime 健康,所有指标在阈值内")
        L.append("- ✅ 无 CoreIdentity 改变尝试")
        L.append("- ✅ Memory / Growth / SelfModel 数据流正常")
    else:
        L.append(f"- ⚠️ 检测到 {len(alerts)} 个告警项,需 review")
        for a in alerts:
            L.append(f"  - {a['level']}: {a['metric']} = {a['value']} (阈值 {a['threshold']})")
    L.append("")

    L.append("---")
    L.append("")
    L.append("> 报告生成者: `scripts/runtime_health_monitor.py` (Phase C.4.1)")
    L.append("> 策略: 只读监控 + 不修改核心逻辑 + 仅告警")
    L.append("")
    return "\n".join(L)


# ============================================================
# CLI
# ============================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Runtime 健康监控")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    data_dir = args.data_dir
    memory_path = data_dir / "memory.json"
    proposals_path = data_dir / "growth" / "proposals" / "proposals.json"
    selfmodel_dir = data_dir / "self_model"
    llm_failures_path = data_dir / "llm_failures" / "failures.jsonl"

    print(f"[runtime] data_dir = {data_dir}")
    print(f"[runtime] report = {args.report}")

    if not memory_path.exists():
        print(f"[runtime] FAILED: memory.json 缺失: {memory_path}", file=sys.stderr)
        return 2

    # 1. 对话统计
    dialog = analyze_dialog_count(memory_path)
    # 2. Memory 写入
    memory = analyze_memory_writes(memory_path)
    # 3. Proposal
    proposals = analyze_proposals(proposals_path)
    # 4. SelfModel
    selfmodel = analyze_selfmodel(selfmodel_dir)
    # 5. CoreIdentity attempts
    identity = analyze_core_identity_attempts(proposals_path)
    # 6. LLM 错误
    llm_errors = analyze_llm_errors(llm_failures_path)

    # 报警
    alerts = compute_alerts(dialog, memory, proposals, identity, llm_errors)

    print("=" * 60)
    print(f"Dialogs: estimated={dialog.get('estimated_dialogs', 0)}")
    print(f"Memory: total={memory.get('total', 0)}")
    print(f"Proposals: total={proposals.get('total', 0)} rejected={proposals.get('rejected_count', 0)}")
    print(f"SelfModel: {selfmodel.get('data_status', 'unknown')}")
    print(f"CoreIdentity attempts: {identity.get('total_attempts', 0)}")
    print(f"LLM errors: {llm_errors.get('total_errors', 0)}")
    print(f"Alerts: {len(alerts)}")
    print("=" * 60)

    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            render_report(dialog, memory, proposals, selfmodel, identity, llm_errors, alerts),
            encoding="utf-8",
        )
        print(f"[runtime] report -> {args.report}")
    except Exception as e:
        print(f"[runtime] FAILED: 写报告异常: {e}", file=sys.stderr)
        return 1

    _log("runtime_health_report", {
        "dialogs": dialog.get("estimated_dialogs", 0),
        "memory_total": memory.get("total", 0),
        "proposals_total": proposals.get("total", 0),
        "proposals_rejected": proposals.get("rejected_count", 0),
        "selfmodel_status": selfmodel.get("data_status", "unknown"),
        "core_identity_attempts": identity.get("total_attempts", 0),
        "llm_errors": llm_errors.get("total_errors", 0),
        "alerts": len(alerts),
    })

    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())
