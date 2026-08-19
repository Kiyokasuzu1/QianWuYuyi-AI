# -*- coding: utf-8 -*-
"""
scripts/audit_memory.py

Phase C.1 — P1-3: Memory 质量审计脚本

职责:
- 扫描 data/memory.json
- 分类记录:正常用户记忆 / AI 内部提示污染 / 系统消息污染 / 无效记录
- 输出审计报告到 docs/audit/memory_cleanup_report.md
- 不直接删除数据,只生成"安全迁移计划"

用法:
    python scripts/audit_memory.py [--source PATH] [--out PATH]

约束:
- 只读 memory.json,不修改
- 不与 Online Phase 抢资源
- 输出报告 + JSON 计划文件(.cache/audit/memory_cleanup_plan.json)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

# 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "memory.json"
DEFAULT_OUT = PROJECT_ROOT / "docs" / "audit" / "memory_cleanup_report.md"
DEFAULT_PLAN = PROJECT_ROOT / ".cache" / "audit" / "memory_cleanup_plan.json"


# ============================================================
# 分类规则(与 memory_dashboard_provider 保持一致)
# ============================================================
_VALID_USER_TYPES = {
    "user_message", "user_fact", "user_event", "user_experience",
    "user_preference", "user_milestone", "user_emotion",
    "user_goal", "user_relationship", "user_shared",
}
_SYSTEM_TYPES = {
    "system", "system_prompt", "system_reminder", "system_meta", "init",
    "test",  # 测试记录也算 system 类别(需 review)
}
_AI_INTERNAL_TYPES = {
    "ai_thought", "ai_reflection", "ai_internal", "ai_self_talk",
    "ai_prompt", "ai_scratchpad", "ai_planning",
    "runtime_experience",  # 运行时经验(被错误存入 memory)
}
_INVALID_TYPES = {"", "unknown", "null", "none", "undefined"}


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


def _classify(m: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (category, reason)。

    category ∈ {"normal_user", "system_pollution", "ai_internal_pollution", "invalid"}
    """
    if not isinstance(m, dict):
        return ("invalid", "not_a_dict")
    t = _extract_type(m)
    role = _extract_role(m)
    content = _extract_content(m).strip()

    # 类型优先
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
        return ("system_pollution", f"role=system_no_type; content[:40]={content[:40]!r}")
    if role == "assistant":
        return ("ai_internal_pollution", f"role=assistant_no_type; content[:40]={content[:40]!r}")
    if role in ("user", "human"):
        return ("normal_user", f"role={role}_no_type")
    if not role and not content:
        return ("invalid", "empty_role_and_content")
    if not role:
        return ("invalid", "empty_role")
    if not content:
        return ("invalid", "empty_content")
    # 其他未知:保守归类为 invalid(需 review)
    return ("invalid", f"unknown_role={role!r}_type={t!r}")


def audit(source_path: Path) -> Dict[str, Any]:
    """扫描 memory.json,返回审计结果。"""
    if not source_path.exists():
        return {
            "ok": False,
            "error": f"source not found: {source_path}",
        }
    try:
        with open(source_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {"ok": False, "error": f"load failed: {e}"}

    if not isinstance(data, list):
        return {
            "ok": False,
            "error": f"expected list, got {type(data).__name__}",
        }

    total = len(data)
    by_category: Dict[str, int] = {
        "normal_user": 0,
        "system_pollution": 0,
        "ai_internal_pollution": 0,
        "invalid": 0,
    }
    by_type: Counter = Counter()
    by_role: Counter = Counter()
    by_reason: Counter = Counter()
    polluted_ids: Dict[str, List[str]] = {
        "normal_user": [],
        "system_pollution": [],
        "ai_internal_pollution": [],
        "invalid": [],
    }
    # 抽样:对每个 category 取前 5 条样本
    samples: Dict[str, List[Dict[str, Any]]] = {
        "normal_user": [],
        "system_pollution": [],
        "ai_internal_pollution": [],
        "invalid": [],
    }
    SAMPLE_LIMIT = 5
    for m in data:
        cat, reason = _classify(m)
        by_category[cat] += 1
        by_type[_extract_type(m)] += 1
        by_role[_extract_role(m)] += 1
        by_reason[reason] += 1
        mid = str(m.get("id", "") or "")
        if cat != "normal_user":
            polluted_ids[cat].append(mid)
        if len(samples[cat]) < SAMPLE_LIMIT:
            samples[cat].append({
                "id": mid,
                "role": _extract_role(m),
                "type": _extract_type(m),
                "content_preview": _extract_content(m)[:120],
                "timestamp": m.get("timestamp", ""),
                "reason": reason,
            })

    return {
        "ok": True,
        "source": str(source_path),
        "total": total,
        "by_category": by_category,
        "by_type": dict(by_type),
        "by_role": dict(by_role),
        "by_reason": dict(by_reason),
        "samples": samples,
        "polluted_ids": {
            k: v for k, v in polluted_ids.items() if k != "normal_user"
        },
    }


def build_plan(result: Dict[str, Any]) -> Dict[str, Any]:
    """基于审计结果,生成"安全迁移计划"(不直接删除)。"""
    if not result.get("ok"):
        return {}
    return {
        "phase": "C.1 P1-3",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "source": result.get("source"),
        "total": result.get("total"),
        "summary": {
            "normal_user": result.get("by_category", {}).get("normal_user", 0),
            "system_pollution": result.get("by_category", {}).get("system_pollution", 0),
            "ai_internal_pollution": result.get("by_category", {}).get("ai_internal_pollution", 0),
            "invalid": result.get("by_category", {}).get("invalid", 0),
        },
        # 三阶段迁移计划:软隔离 -> 人工 review -> 删除/归档
        "phases": [
            {
                "phase": "phase_1_quarantine",
                "description": "把 system/ai_internal/invalid 记录的 id 写入 quarantine 列表(只读),不删除数据。",
                "action": "quarantine_only",
                "risk": "low",
                "id_count": (
                    len(result.get("polluted_ids", {}).get("system_pollution", []))
                    + len(result.get("polluted_ids", {}).get("ai_internal_pollution", []))
                    + len(result.get("polluted_ids", {}).get("invalid", []))
                ),
            },
            {
                "phase": "phase_2_backup",
                "description": "在执行任何修改前,先 cp memory.json 到 backup/memory.json.YYYYMMDD_HHMMSS。",
                "action": "backup_only",
                "risk": "low",
            },
            {
                "phase": "phase_3_human_review",
                "description": "由人工 review quarantine 列表(尤其是 'invalid' 类别),确认无误后再执行删除/归档。",
                "action": "manual_review",
                "risk": "medium",
                "requires_approval": True,
            },
            {
                "phase": "phase_4_migrate",
                "description": "把 system_pollution / ai_internal_pollution 迁移到 data/memory_archive/system_ai_internal.json,原 memory.json 保留 normal_user。",
                "action": "migrate_and_rewrite",
                "risk": "medium",
                "requires_approval": True,
            },
        ],
        "quarantine_ids": result.get("polluted_ids", {}),
        "rollback": {
            "method": "restore_from_backup",
            "note": "在执行 phase_4 前可随时从 backup/memory.json.YYYYMMDD_HHMMSS 还原。",
        },
    }


def render_report(result: Dict[str, Any], plan: Dict[str, Any]) -> str:
    """生成 Markdown 审计报告。"""
    if not result.get("ok"):
        return f"# Memory Cleanup Audit (FAILED)\n\n错误: {result.get('error')}\n"

    by_cat = result["by_category"]
    by_type = result["by_type"]
    by_role = result["by_role"]
    samples = result["samples"]
    polluted_ids = result["polluted_ids"]

    total = result["total"]
    normal = by_cat["normal_user"]
    sys_p = by_cat["system_pollution"]
    ai_p = by_cat["ai_internal_pollution"]
    invalid = by_cat["invalid"]
    polluted = sys_p + ai_p + invalid
    pollution_ratio = round(polluted / total, 4) if total else 0.0
    normal_ratio = round(normal / total, 4) if total else 0.0

    # 状态评估
    if pollution_ratio >= 0.3:
        status = "critical"
    elif pollution_ratio >= 0.1:
        status = "warning"
    else:
        status = "healthy"

    lines: List[str] = []
    lines.append("# Memory Cleanup Audit Report")
    lines.append("")
    lines.append(f"> Phase: **C.1 P1-3**  ")
    lines.append(f"> Source: `{result['source']}`  ")
    lines.append(f"> Generated at: `{datetime.utcnow().isoformat()}Z`  ")
    lines.append(f"> Status: **{status.upper()}**")
    lines.append("")
    lines.append("---")
    lines.append("")

    # 1. 总览
    lines.append("## 1. 总览")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 总记忆数 | {total} |")
    lines.append(f"| 正常用户记忆 | {normal} ({normal_ratio * 100:.2f}%) |")
    lines.append(f"| 系统消息污染 | {sys_p} ({round(sys_p / total, 4) * 100 if total else 0:.2f}%) |")
    lines.append(f"| AI 内部提示污染 | {ai_p} ({round(ai_p / total, 4) * 100 if total else 0:.2f}%) |")
    lines.append(f"| 无效记录 | {invalid} ({round(invalid / total, 4) * 100 if total else 0:.2f}%) |")
    lines.append(f"| **污染合计** | **{polluted} ({pollution_ratio * 100:.2f}%)** |")
    lines.append("")
    lines.append("> 状态判定规则: 污染比例 ≥ 30% → critical; ≥ 10% → warning; < 10% → healthy")
    lines.append("")

    # 2. 分类明细
    lines.append("## 2. 分类明细")
    lines.append("")
    lines.append("### 2.1 按 memory_type")
    lines.append("")
    lines.append("| memory_type | 数量 |")
    lines.append("| --- | --- |")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        lines.append(f"| `{t}` | {c} |")
    lines.append("")

    lines.append("### 2.2 按 role")
    lines.append("")
    lines.append("| role | 数量 |")
    lines.append("| --- | --- |")
    for r, c in sorted(by_role.items(), key=lambda x: -x[1]):
        lines.append(f"| `{r or '(empty)'}` | {c} |")
    lines.append("")

    # 3. 污染样本
    lines.append("## 3. 污染样本(每个 category 前 5 条)")
    lines.append("")

    def _cat_section(cat: str, title_zh: str) -> None:
        lines.append(f"### 3.{['normal_user', 'system_pollution', 'ai_internal_pollution', 'invalid'].index(cat) + 1} {title_zh} ({len(samples[cat])} 样本)")
        lines.append("")
        if not samples[cat]:
            lines.append("_(无)_")
            lines.append("")
            return
        for s in samples[cat]:
            lines.append(
                f"- `{s['id']}` | role=`{s['role'] or '(empty)'}` | type=`{s['type'] or '(empty)'}` | reason=`{s['reason']}`"
            )
            cp = s["content_preview"].replace("|", "\\|").replace("\n", " ")
            lines.append(f"  - content: `{cp}`")
            if s["timestamp"]:
                lines.append(f"  - timestamp: `{s['timestamp']}`")
        lines.append("")

    _cat_section("system_pollution", "系统消息污染")
    _cat_section("ai_internal_pollution", "AI 内部提示污染")
    _cat_section("invalid", "无效记录")
    _cat_section("normal_user", "正常用户记忆(对照)")

    # 4. 严重程度评估
    lines.append("## 4. 严重程度评估")
    lines.append("")
    lines.append(f"**当前状态: {status.upper()}**")
    lines.append("")
    if status == "critical":
        lines.append("- ⚠️ **污染比例 ≥ 30%**:memory 数据已被严重污染,系统检索/统计将产生大量错误结果。")
        lines.append("- ⚠️ 建议:立即执行安全迁移(见 §5)。")
    elif status == "warning":
        lines.append("- ⚠️ **污染比例 ≥ 10%**:memory 数据存在明显污染,需要清理。")
        lines.append("- 建议:按计划执行迁移(见 §5)。")
    else:
        lines.append("- ✅ **污染比例 < 10%**:memory 数据基本健康,可按需清理。")
    lines.append("")

    # 5. 安全迁移计划
    lines.append("## 5. 安全迁移计划")
    lines.append("")
    lines.append("**核心原则:不直接删除任何数据,所有操作分阶段执行,每阶段都可回滚。**")
    lines.append("")
    lines.append("### Phase 1: 软隔离 (Quarantine)")
    lines.append("")
    lines.append("- 把所有 pollution 记录的 id 写入 quarantine 列表(只读,保存在 `.cache/audit/memory_cleanup_plan.json`)。")
    lines.append("- 不删除任何数据;运行时的 memory 检索可以通过 `quarantine_ids` 过滤掉污染记录(选择性读取)。")
    lines.append(f"- 待隔离 id 总数: **{len(plan.get('quarantine_ids', {}).get('system_pollution', [])) + len(plan.get('quarantine_ids', {}).get('ai_internal_pollution', [])) + len(plan.get('quarantine_ids', {}).get('invalid', []))}**")
    lines.append("")

    lines.append("### Phase 2: 数据备份 (Backup)")
    lines.append("")
    lines.append("- 在执行任何修改前,先复制 `data/memory.json` 到 `data/memory.json.YYYYMMDD_HHMMSS`。")
    lines.append("- 备份命名规则:`memory.json.<UTC 时间戳>`。")
    lines.append("")

    lines.append("### Phase 3: 人工 review (Manual Review)")
    lines.append("")
    lines.append("- 由人工 review quarantine 列表(尤其是 `invalid` 类别)。")
    lines.append("- 复核完成后,在 `docs/audit/memory_cleanup_report.md` 中签字确认。")
    lines.append("- 建议 review 工具:`memory_audit.py --list` 命令(将提供)。")
    lines.append("")

    lines.append("### Phase 4: 安全迁移 (Migrate)")
    lines.append("")
    lines.append("- 把 `system_pollution` / `ai_internal_pollution` 记录从 `memory.json` 中剥离,写入 `data/memory_archive/system_ai_internal_<timestamp>.json`。")
    lines.append("- 原 `memory.json` 仅保留 `normal_user` 记录。")
    lines.append("- 写入前再次生成备份(覆盖 Phase 2 备份)。")
    lines.append("- 可用 `git diff data/memory.json` 复核改动。")
    lines.append("")

    lines.append("### 回滚策略")
    lines.append("")
    lines.append("- 任意阶段均可通过 `cp data/memory.json.<timestamp> data/memory.json` 一键回滚。")
    lines.append("- Phase 4 完成后,30 天内保留归档文件,之后方可清理。")
    lines.append("")

    # 6. 推荐的下一步
    lines.append("## 6. 推荐的下一步")
    lines.append("")
    lines.append("1. 运行 `python scripts/audit_memory.py --list`,查看完整 quarantine 列表(待实现)。")
    lines.append("2. 人工 review `invalid` 类别(占比高时尤其重要)。")
    lines.append("3. 确认无误后,运行 `python scripts/migrate_memory.py --execute`(待实现,默认 dry-run)。")
    lines.append("4. 在 CI / 测试 pipeline 中加入本审计脚本,防止未来再次污染。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 报告生成者: `scripts/audit_memory.py` (Phase C.1 P1-3)")
    lines.append("> 数据源: `data/memory.json` (只读)")
    lines.append("> 任何修改须经人工 review 确认,不接受自动删除操作。")
    lines.append("")
    return "\n".join(lines)


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory 质量审计")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="memory.json 路径(默认: data/memory.json)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="报告输出路径(默认: docs/audit/memory_cleanup_report.md)")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN,
                        help="迁移计划 JSON 路径(默认: .cache/audit/memory_cleanup_plan.json)")
    args = parser.parse_args(argv)

    print(f"[audit] source = {args.source}")
    result = audit(args.source)
    if not result.get("ok"):
        print(f"[audit] FAILED: {result.get('error')}", file=sys.stderr)
        return 1
    plan = build_plan(result)

    # 写报告
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(result, plan)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[audit] report -> {args.out}")

    # 写计划
    args.plan.parent.mkdir(parents=True, exist_ok=True)
    with open(args.plan, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"[audit] plan -> {args.plan}")

    # 摘要
    bc = result["by_category"]
    total = result["total"]
    print(f"[audit] total={total}  normal_user={bc['normal_user']}  "
          f"system_pollution={bc['system_pollution']}  "
          f"ai_internal_pollution={bc['ai_internal_pollution']}  "
          f"invalid={bc['invalid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
