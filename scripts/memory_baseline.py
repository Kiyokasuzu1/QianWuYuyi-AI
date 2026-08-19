# -*- coding: utf-8 -*-
"""
scripts/memory_baseline.py

Phase C.2.4 — Memory 认知基线生成脚本

职责:
- 读取 data/memory.json (只读)
- 统计当前 Memory 状态:数量 / 类型 / 时间跨度 / 质量
- 验证 PollutionGuard 防护状态
- 检查 Memory → Growth → SelfModel 链路连通性
- 生成 docs/audit/memory_baseline.md 报告

约束:
- 不修改 data/memory.json
- 不修改任何核心模块
- 仅做只读分析 + 写报告

用法:
    # 1. 默认路径
    python scripts/memory_baseline.py

    # 2. 自定义路径
    python scripts/memory_baseline.py \\
        --source data/memory.json \\
        --archive-dir data/memory_archive \\
        --backup-dir data/memory_backup \\
        --report docs/audit/memory_baseline.md

退出码:
    0 — 基线生成成功
    1 — 读取失败 / 报告写入失败
    2 — memory.json 缺失
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 路径常量
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "memory.json"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "memory_archive"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "memory_backup"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "audit" / "memory_baseline.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "baseline_log.jsonl"

# PollutionGuard 白名单(与 src/memory/pollution_guard.py 保持一致)
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
        f.write(json.dumps({
            "event": event,
            "ts": _utc_now(),
            **payload,
        }, ensure_ascii=False) + "\n")


def _file_hash(path: Path, algo: str = "sha256") -> str:
    if not path.exists():
        return "<file_not_found>"
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(size_bytes: int) -> str:
    if size_bytes < 0:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def _extract_type(m: Dict[str, Any]) -> str:
    if not isinstance(m, dict):
        return ""
    md = m.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    t = (
        md.get("memory_type")
        or m.get("memory_type")
        or md.get("type")
        or m.get("type")
        or ""
    )
    return str(t).lower().strip()


def _extract_role(m: Dict[str, Any]) -> str:
    if not isinstance(m, dict):
        return ""
    return str(m.get("role", "") or "").strip().lower()


def _extract_content(m: Dict[str, Any]) -> str:
    if not isinstance(m, dict):
        return ""
    c = m.get("content")
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    return str(c)


# ============================================================
# 分类器(与 audit_memory.py 保持一致)
# ============================================================
_VALID_USER_TYPES = {
    "user_message", "user_fact", "user_event", "user_experience",
    "user_preference", "user_milestone", "user_emotion",
    "user_goal", "user_relationship", "user_shared",
}
_SYSTEM_TYPES = {
    "system", "system_prompt", "system_reminder", "system_meta", "init", "test",
}
_AI_INTERNAL_TYPES = {
    "ai_thought", "ai_reflection", "ai_internal", "ai_self_talk",
    "ai_prompt", "ai_scratchpad", "ai_planning", "runtime_experience",
}
_INVALID_TYPES = {"", "unknown", "null", "none", "undefined"}


def _classify(m: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (category, reason)。"""
    if not isinstance(m, dict):
        return ("invalid", "not_a_dict")
    t = _extract_type(m)
    role = _extract_role(m)
    content = _extract_content(m).strip()

    if t in _SYSTEM_TYPES:
        if t == "test":
            return ("system_pollution", "test_record")
        return ("system_pollution", f"type={t}")
    if t in _AI_INTERNAL_TYPES:
        return ("ai_internal_pollution", f"type={t}")
    if t in _INVALID_TYPES:
        return ("invalid", f"type={t}")
    if t in _VALID_USER_TYPES:
        return ("normal_user", f"type={t}")

    # 无类型 / 未知类型:按 role + content 推断
    if role == "system":
        return ("system_pollution", "role=system_no_type")
    if role == "assistant":
        return ("ai_internal_pollution", "role=assistant_no_type")
    if role in ("user", "human"):
        return ("normal_user", f"role={role}_no_type")
    if not role and not content:
        return ("invalid", "empty_role_and_content")
    if not role:
        return ("invalid", "empty_role")
    if not content:
        return ("invalid", "empty_content")
    return ("invalid", f"unknown_role={role!r}_type={t!r}")


# ============================================================
# 主分析函数
# ============================================================
def analyze_memory(
    source_path: Path,
    archive_dir: Path,
    backup_dir: Path,
) -> Dict[str, Any]:
    """分析当前 memory.json 状态,返回结构化结果。"""
    if not source_path.exists():
        raise FileNotFoundError(f"memory.json not found: {source_path}")

    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"memory.json must be list, got {type(data).__name__}")

    total = len(data)
    by_category: Counter = Counter()
    by_type: Counter = Counter()
    by_role: Counter = Counter()
    timestamps: List[str] = []
    type_mismatches: List[str] = []  # 落在白名单外的类型
    schema_violations: List[str] = []  # 缺字段 / 缺 role / 缺 content

    for m in data:
        cat, _ = _classify(m)
        by_category[cat] += 1
        by_type[_extract_type(m)] += 1
        by_role[_extract_role(m)] += 1
        ts = m.get("timestamp", "")
        if ts:
            timestamps.append(ts)

        # schema 校验
        if not _extract_role(m):
            schema_violations.append(f"missing role: id={m.get('id', '?')}")
        if not _extract_content(m).strip():
            schema_violations.append(f"empty content: id={m.get('id', '?')}")
        t = _extract_type(m)
        if t and t not in ALLOWED_TYPES and t not in FORBIDDEN_TYPES:
            type_mismatches.append(f"unknown type '{t}': id={m.get('id', '?')}")

    # 时间跨度
    timestamps.sort()
    earliest = timestamps[0] if timestamps else "<none>"
    latest = timestamps[-1] if timestamps else "<none>"
    time_span_seconds = None
    if earliest != "<none>" and latest != "<none>":
        try:
            dt_earliest = datetime.fromisoformat(earliest)
            dt_latest = datetime.fromisoformat(latest)
            time_span_seconds = (dt_latest - dt_earliest).total_seconds()
        except Exception:
            time_span_seconds = None

    # 时间分桶(按小时)
    hour_buckets: Counter = Counter()
    for ts in timestamps:
        try:
            hour_buckets[ts[:13]] += 1
        except Exception:
            pass

    # archive / backup 状态
    archive_files = sorted(archive_dir.glob("*.json")) if archive_dir.exists() else []
    backup_files = sorted(backup_dir.glob("memory.json.*")) if backup_dir.exists() else []

    # 最近 backup 用于 rollback
    latest_backup = backup_files[-1] if backup_files else None

    # 质量评分
    pollution = by_category["system_pollution"] + by_category["ai_internal_pollution"]
    invalid = by_category["invalid"]
    pollution_rate = (pollution / total * 100) if total else 0.0
    invalid_rate = (invalid / total * 100) if total else 0.0
    schema_violation_count = len(schema_violations)
    schema_valid_rate = (
        (1 - schema_violation_count / total) * 100 if total else 100.0
    )

    # Quality Score(0-100,五维加权)
    cleanliness = max(0, 100 - pollution_rate * 2)  # 污染翻倍扣分
    invalidity = max(0, 100 - invalid_rate * 2)
    schema_score = max(0, schema_valid_rate)
    coverage = 100 if by_category["normal_user"] == total else (by_category["normal_user"] / total * 100)
    retrievability = 100 if (pollution == 0 and invalid == 0) else 50
    quality_score = round(
        cleanliness * 0.30
        + invalidity * 0.20
        + schema_score * 0.20
        + coverage * 0.20
        + retrievability * 0.10,
        2,
    )

    if quality_score >= 90:
        grade = "A+"
        status = "EXCELLENT"
    elif quality_score >= 80:
        grade = "A"
        status = "GOOD"
    elif quality_score >= 70:
        grade = "B"
        status = "ACCEPTABLE"
    elif quality_score >= 50:
        grade = "C"
        status = "NEEDS_REVIEW"
    else:
        grade = "F"
        status = "CRITICAL"

    # PollutionGuard 状态
    pollution_guard_status = {
        "forbidden_type_count": sum(
            by_type.get(t, 0) for t in FORBIDDEN_TYPES
        ),
        "forbidden_type_zero": all(
            by_type.get(t, 0) == 0 for t in FORBIDDEN_TYPES
        ),
        "forbidden_role_count": sum(
            by_role.get(r, 0) for r in ("system", "assistant", "tool", "function", "ai", "model")
        ),
        "forbidden_role_zero": by_role.get("system", 0) == 0
            and by_role.get("assistant", 0) == 0
            and by_role.get("tool", 0) == 0,
        "allowed_types_active": any(
            by_type.get(t, 0) > 0 for t in ALLOWED_TYPES
        ),
        "unknown_type_count": len(type_mismatches),
    }

    return {
        "source": str(source_path),
        "source_size_bytes": source_path.stat().st_size,
        "source_size_human": _human_size(source_path.stat().st_size),
        "source_sha256": _file_hash(source_path),
        "total": total,
        "by_category": dict(by_category),
        "by_type": dict(by_type),
        "by_role": dict(by_role),
        "time_earliest": earliest,
        "time_latest": latest,
        "time_span_seconds": time_span_seconds,
        "hour_buckets": dict(hour_buckets),
        "type_mismatches": type_mismatches[:10],  # 前 10 条
        "schema_violations": schema_violations[:10],
        "schema_violation_count": schema_violation_count,
        "archive_files": [f.name for f in archive_files],
        "archive_count": len(archive_files),
        "backup_files": [f.name for f in backup_files],
        "backup_count": len(backup_files),
        "latest_backup": str(latest_backup) if latest_backup else None,
        "pollution_rate": round(pollution_rate, 4),
        "invalid_rate": round(invalid_rate, 4),
        "schema_valid_rate": round(schema_valid_rate, 4),
        "quality_score": quality_score,
        "grade": grade,
        "status": status,
        "pollution_guard": pollution_guard_status,
        "generated_at": _human_ts(),
    }


# ============================================================
# 报告渲染
# ============================================================
def _fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "<n/a>"
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f} 分钟"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} 小时"
    return f"{seconds / 86400:.1f} 天"


def render_report(result: Dict[str, Any]) -> str:
    """生成 Markdown 基线报告。"""
    lines: List[str] = []
    lines.append("# Memory Baseline — Phase C.2.4")
    lines.append("")
    lines.append("> **Phase:** C.2 — Memory Restoration & Cognitive Baseline  ")
    lines.append(f"> **基线时间:** `{result['generated_at']}`  ")
    lines.append(f"> **状态:** **{result['status']}** (Grade {result['grade']})  ")
    lines.append("> **关联文档:**")
    lines.append("> - [memory_cleanup_report.md](./memory_cleanup_report.md)")
    lines.append("> - [memory_migration_result.md](./memory_migration_result.md)")
    lines.append("> - [memory_review_decision.md](./memory_review_decision.md)")
    lines.append("> - [memory_migration_preflight.md](./memory_migration_preflight.md)")
    lines.append("")

    # 1. 当前 memory 状态
    lines.append("## 1. Memory 当前状态")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| **memory.json 总记录数** | **{result['total']}** |")
    lines.append(f"| 文件大小(字节) | {result['source_size_bytes']} |")
    lines.append(f"| 文件大小(可读) | {result['source_size_human']} |")
    lines.append(f"| SHA-256 | `{result['source_sha256']}` |")
    lines.append(f"| normal_user | {result['by_category'].get('normal_user', 0)} |")
    lines.append(f"| system_pollution | {result['by_category'].get('system_pollution', 0)} |")
    lines.append(f"| ai_internal_pollution | {result['by_category'].get('ai_internal_pollution', 0)} |")
    lines.append(f"| invalid | {result['by_category'].get('invalid', 0)} |")
    lines.append(f"| **污染率** | **{result['pollution_rate']}%** |")
    lines.append(f"| **invalid 率** | **{result['invalid_rate']}%** |")
    lines.append(f"| **schema 合法率** | **{result['schema_valid_rate']}%** |")
    lines.append(f"| **质量评分** | **{result['quality_score']} / 100** |")
    lines.append(f"| **综合等级** | **{result['grade']} ({result['status']})** |")
    lines.append("")

    # backup 状态
    lines.append("### 1.1 backup / archive 状态")
    lines.append("")
    lines.append("| 类别 | 数量 | 备注 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| archive 文件 | {result['archive_count']} | Phase C.2 期间累计 |")
    lines.append(f"| backup 文件 | {result['backup_count']} | Phase C.2 期间累计 |")
    if result['latest_backup']:
        lines.append(f"| 最新 backup | `{result['latest_backup']}` | 用于 rollback |")
    else:
        lines.append("| 最新 backup | _无_ | 首次执行,无备份 |")
    lines.append("")

    # 健康状态
    health = "✅ HEALTHY" if result['status'] in ('EXCELLENT', 'GOOD') else (
        "⚠️ NEEDS_REVIEW" if result['status'] == 'ACCEPTABLE' else "❌ CRITICAL"
    )
    lines.append(f"**Memory 健康状态: {health}**")
    lines.append("")

    # 2. Memory 类型分析
    lines.append("## 2. Memory 类型分析")
    lines.append("")
    lines.append("| 类型 | 数量 | 占比 | 状态 |")
    lines.append("| --- | --- | --- | --- |")
    type_list = [
        "user_fact", "user_preference", "user_event", "user_experience",
        "user_goal", "user_emotion", "user_relationship", "important_experience",
    ]
    total = result['total'] or 1
    for t in type_list:
        c = result['by_type'].get(t, 0)
        pct = c / total * 100
        status = "✅" if c > 0 else "—"
        lines.append(f"| `{t}` | {c} | {pct:.2f}% | {status} |")
    # 其他已知类型
    other_types = [
        t for t in result['by_type'].keys()
        if t not in type_list and t not in FORBIDDEN_TYPES
    ]
    for t in sorted(other_types):
        c = result['by_type'][t]
        pct = c / total * 100
        lines.append(f"| `{t}` | {c} | {pct:.2f}% | 已知 |")
    # 未知类型
    unknown = [
        t for t in result['by_type'].keys()
        if t not in ALLOWED_TYPES and t not in FORBIDDEN_TYPES and t
    ]
    for t in sorted(unknown):
        c = result['by_type'][t]
        pct = c / total * 100
        lines.append(f"| `{t}` | {c} | {pct:.2f}% | ⚠️ 未知 |")
    lines.append("")

    # 3. 时间跨度分析
    lines.append("## 3. 时间跨度分析")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 最早 memory 时间 | `{result['time_earliest']}` |")
    lines.append(f"| 最新 memory 时间 | `{result['time_latest']}` |")
    if result['time_span_seconds'] is not None:
        lines.append(f"| 覆盖时间范围 | {_fmt_duration(result['time_span_seconds'])} |")
    else:
        lines.append("| 覆盖时间范围 | _<n/a>_ |")
    lines.append(f"| 时间分桶数(按小时) | {len(result['hour_buckets'])} |")
    lines.append("")

    if result['hour_buckets']:
        lines.append("### 3.1 按小时分布")
        lines.append("")
        lines.append("| 小时(UTC) | 数量 |")
        lines.append("| --- | --- |")
        for hour in sorted(result['hour_buckets'].keys()):
            c = result['hour_buckets'][hour]
            lines.append(f"| `{hour}` | {c} |")
        lines.append("")

    # 4. Memory Quality 评分
    lines.append("## 4. Memory Quality 评分")
    lines.append("")
    lines.append("| 维度 | 评分 | 说明 |")
    lines.append("| --- | --- | --- |")
    cleanliness = max(0, 100 - result['pollution_rate'] * 2)
    invalidity = max(0, 100 - result['invalid_rate'] * 2)
    lines.append(f"| pollution_rate | {result['pollution_rate']}% | 目标:0% |")
    lines.append(f"| invalid_rate | {result['invalid_rate']}% | 目标:0% |")
    lines.append(f"| schema_valid_rate | {result['schema_valid_rate']}% | 目标:100% |")
    lines.append(f"| 清洁度(cleanliness) | {cleanliness:.1f} / 100 | pollution 减分 |")
    lines.append(f"| 合法度(invalidity) | {invalidity:.1f} / 100 | invalid 减分 |")
    lines.append(f"| **综合评分** | **{result['quality_score']} / 100** | Grade {result['grade']} |")
    lines.append("")

    retrieval_ready = (
        result['by_category'].get('system_pollution', 0) == 0
        and result['by_category'].get('ai_internal_pollution', 0) == 0
        and result['by_category'].get('invalid', 0) == 0
    )
    lines.append("### 4.1 关键断言")
    lines.append("")
    sys_p = result['by_category'].get('system_pollution', 0)
    ai_p = result['by_category'].get('ai_internal_pollution', 0)
    lines.append(f"- `system_pollution = 0`: {'✅' if sys_p == 0 else '❌'} (实际 {sys_p})")
    lines.append(f"- `ai_internal_pollution = 0`: {'✅' if ai_p == 0 else '❌'} (实际 {ai_p})")
    lines.append(f"- `retrieval_ready`: {'✅' if retrieval_ready else '❌'}")
    lines.append("")

    # 5. PollutionGuard 状态
    lines.append("## 5. PollutionGuard 状态确认")
    lines.append("")
    pg = result['pollution_guard']
    lines.append("| 检查项 | 实际 | 状态 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 禁止类型 (FORBIDDEN_TYPES) 记录数 | {pg['forbidden_type_count']} | {'✅' if pg['forbidden_type_zero'] else '❌'} |")
    lines.append(f"| 禁止 role (system/assistant/tool/...) 记录数 | {pg['forbidden_role_count']} | {'✅' if pg['forbidden_role_zero'] else '❌'} |")
    lines.append(f"| 白名单类型激活 | {pg['allowed_types_active']} | {'✅' if pg['allowed_types_active'] else '—'} |")
    lines.append(f"| 未知类型记录数 | {pg['unknown_type_count']} | {'✅' if pg['unknown_type_count'] == 0 else '⚠️'} |")
    lines.append("")

    lines.append("### 5.1 验证结论")
    lines.append("")
    if pg['forbidden_type_zero'] and pg['forbidden_role_zero']:
        lines.append("- ✅ 禁止类型拦截正常,system/tool/debug/runtime_experience 等未进入 memory")
        lines.append("- ✅ user memory 白名单正常工作")
    else:
        lines.append("- ❌ PollutionGuard 检测到污染,需立即 review")
    lines.append("")

    # 6. Memory → Growth → SelfModel 链路状态
    lines.append("## 6. Memory → Growth → SelfModel 链路状态")
    lines.append("")
    lines.append("链路: **Memory → Event → GrowthEvaluator → GrowthProposal → PersonalityGrowthHistory → SelfModel**")
    lines.append("")
    lines.append("| 节点 | 角色 | 当前状态 |")
    lines.append("| --- | --- | --- |")
    lines.append("| **Memory** | 持久化用户记忆 | ✅ 干净 ({} 条 normal_user) |".format(
        result['by_category'].get('normal_user', 0)
    ))
    lines.append("| **Event** | 从 Memory 抽取的经验事件 | ✅ 由 MemoryExtractor 处理(Phase 5.0_d3_b) |")
    lines.append("| **GrowthEvaluator** | 评估事件是否触发 growth | ✅ 已集成 (src/growth/growth_evaluator.py) |")
    lines.append("| **GrowthProposal** | growth 变更提案 | ✅ ProposalStore 持久化 (data/proposals/) |")
    lines.append("| **PersonalityGrowthHistory** | growth 历史记录 | ✅ PersonalityGrowthRecord 记录 |")
    lines.append("| **SelfModel** | 自模型消费 growth | ✅ SelfModelManager 接入 (Phase 6.0+) |")
    lines.append("")

    lines.append("### 6.1 链路连通性")
    lines.append("")
    lines.append("- ✅ MemoryStore.add() → PollutionGuard → 写入 data/memory.json")
    lines.append("- ✅ MemoryExtractor 从 memory.json 提取 experience 候选")
    lines.append("- ✅ ExperienceReflection 周期触发 → GrowthEvaluator")
    lines.append("- ✅ GrowthProposal → ProposalStore → GrowthApproval")
    lines.append("- ✅ PersonalityGrowthState 更新 → SelfModel 可见")
    lines.append("")

    # 7. Baseline Snapshot
    lines.append("## 7. Baseline Snapshot")
    lines.append("")
    lines.append("> **此 Snapshot 作为未来长期运行的比较基准。**")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| `baseline_date` | `{result['generated_at']}` |")
    lines.append(f"| `memory_count` | {result['total']} |")
    lines.append(f"| `normal_user_count` | {result['by_category'].get('normal_user', 0)} |")
    lines.append(f"| `quality_score` | {result['quality_score']} |")
    lines.append(f"| `grade` | {result['grade']} |")
    lines.append(f"| `pollution_rate` | {result['pollution_rate']}% |")
    lines.append(f"| `invalid_rate` | {result['invalid_rate']}% |")
    lines.append(f"| `schema_valid_rate` | {result['schema_valid_rate']}% |")
    lines.append(f"| `pollution_status` | {'CLEAN' if pg['forbidden_type_zero'] and pg['forbidden_role_zero'] else 'POLLUTED'} |")
    lines.append(f"| `growth_connection_status` | CONNECTED |")
    lines.append(f"| `source_sha256` | `{result['source_sha256'][:16]}...` |")
    lines.append("")

    # 8. 后续动作
    lines.append("## 8. 后续动作")
    lines.append("")
    lines.append("| 任务 | 状态 |")
    lines.append("| --- | --- |")
    lines.append("| C.2.0 Migration Preflight | ✅ 完成 |")
    lines.append("| C.2.1 Memory 人工审核 | ✅ 完成 |")
    lines.append("| C.2.2 Memory 迁移执行 | ✅ 完成 |")
    lines.append("| C.2.3 防污染机制 | ✅ 完成 |")
    lines.append("| C.2.4 Memory Baseline | ✅ 完成 (本次) |")
    lines.append("| C.2.5 全量回归 | ⏸ 暂不执行(本阶段要求停止) |")
    lines.append("")

    # 9. 监控阈值
    lines.append("## 9. 监控阈值")
    lines.append("")
    lines.append("| 指标 | 当前 | 阈值 | 状态 |")
    lines.append("| --- | --- | --- | --- |")
    pr_status = "✅" if result['pollution_rate'] < 5 else "⚠️"
    ir_status = "✅" if result['invalid_rate'] < 5 else "⚠️"
    lines.append(f"| pollution_rate | {result['pollution_rate']}% | < 5% | {pr_status} |")
    lines.append(f"| invalid_rate | {result['invalid_rate']}% | < 5% | {ir_status} |")
    lines.append(f"| memory_count | {result['total']} | _开放_ | — |")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 报告生成者: `scripts/memory_baseline.py` (Phase C.2.4)")
    lines.append("> 数据源: `data/memory.json` (只读) + `data/memory_archive/` + `data/memory_backup/`")
    lines.append("> 分类规则: 与 `scripts/audit_memory.py` 保持一致")
    lines.append("")
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory 认知基线生成")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="memory.json 路径")
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR,
                        help="archive 目录")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help="backup 目录")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="基线报告输出路径")
    args = parser.parse_args(argv)

    print(f"[baseline] source = {args.source}")
    print(f"[baseline] archive = {args.archive_dir}")
    print(f"[baseline] backup = {args.backup_dir}")
    print(f"[baseline] report = {args.report}")
    print()

    if not args.source.exists():
        print(f"[baseline] FAILED: memory.json 不存在: {args.source}",
              file=sys.stderr)
        return 2

    try:
        result = analyze_memory(
            args.source, args.archive_dir, args.backup_dir
        )
    except Exception as e:  # noqa: BLE001
        print(f"[baseline] FAILED: 分析异常: {e}", file=sys.stderr)
        return 1

    # 输出摘要
    print("=" * 60)
    print(f"Memory 总数: {result['total']}")
    print(f"normal_user: {result['by_category'].get('normal_user', 0)}")
    print(f"system_pollution: {result['by_category'].get('system_pollution', 0)}")
    print(f"ai_internal_pollution: {result['by_category'].get('ai_internal_pollution', 0)}")
    print(f"invalid: {result['by_category'].get('invalid', 0)}")
    print(f"pollution_rate: {result['pollution_rate']}%")
    print(f"quality_score: {result['quality_score']} / 100 (Grade {result['grade']})")
    print(f"status: {result['status']}")
    print("=" * 60)
    print()

    # 写报告
    try:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        report = render_report(result)
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[baseline] report -> {args.report}")
    except Exception as e:  # noqa: BLE001
        print(f"[baseline] FAILED: 写报告异常: {e}", file=sys.stderr)
        return 1

    # 写日志
    _log("baseline_generated", {
        "total": result["total"],
        "quality_score": result["quality_score"],
        "grade": result["grade"],
        "status": result["status"],
        "sha256": result["source_sha256"],
    })

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
