# -*- coding: utf-8 -*-
"""
scripts/preflight_memory.py

Phase C.2.0 (Preflight) — Memory 迁移前置检查脚本

职责:
- 在执行 migrate_memory.py --execute 前,验证所有前置条件
- 检查 backup 目录冲突并创建带时间戳的子目录(如需)
- 检查 archive 目录是否存在,不存在则自动创建
- 验证迁移计划文件的结构与关键值
- 计算当前 memory.json 的 hash / 大小 / 记录数
- 生成可审计的 preflight 报告
- preflight 未通过时拒绝执行(供 migrate_memory.py 调用)

用法:
    # 1. 仅运行 preflight 检查(不执行迁移)
    python scripts/preflight_memory.py

    # 2. 指定 plan / memory.json 路径
    python scripts/preflight_memory.py --plan .cache/audit/memory_cleanup_plan.json

    # 3. 生成 preflight 报告(默认行为)
    python scripts/preflight_memory.py --report docs/audit/memory_migration_preflight.md

    # 4. 严格模式:plan 不匹配预期值时直接失败
    python scripts/preflight_memory.py --strict

    # 5. 自定义预期值(覆盖 Phase C.2.0 默认值)
    python scripts/preflight_memory.py --expected-total 201 --expected-archive 201 --expected-keep 0

退出码:
    0 — preflight 通过
    1 — preflight 失败
    2 — 参数错误

约束:
- 不修改 memory.json
- 不修改 plan
- 仅在 data/memory_backup/ 下创建时间戳子目录(如检测到冲突)
- 自动创建 data/memory_archive/(如不存在)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
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
DEFAULT_PLAN = PROJECT_ROOT / ".cache" / "audit" / "memory_cleanup_plan.json"
DEFAULT_REPORT = PROJECT_ROOT / "docs" / "audit" / "memory_migration_preflight.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "preflight_log.jsonl"


# ============================================================
# 工具函数
# ============================================================
def _utc_now() -> str:
    """生成 UTC 时间戳字符串(包含毫秒)。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _human_ts() -> str:
    """生成人类可读的 UTC 时间戳。"""
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
    """计算文件 hash(支持大文件,分块读取)。"""
    if not path.exists():
        return "<file_not_found>"
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _human_size(size_bytes: int) -> str:
    """将字节数转为人类可读格式。"""
    if size_bytes < 0:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


# ============================================================
# 加载器
# ============================================================
def _load_plan(plan_path: Path) -> Dict[str, Any]:
    """加载迁移计划文件。"""
    if not plan_path.exists():
        raise FileNotFoundError(
            f"plan not found: {plan_path} — 请先运行 scripts/audit_memory.py"
        )
    with open(plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_memory(source_path: Path) -> List[Dict[str, Any]]:
    """加载 memory.json。"""
    if not source_path.exists():
        raise FileNotFoundError(f"memory not found: {source_path}")
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"memory.json 必须为 list,当前为 {type(data).__name__}")
    return data


# ============================================================
# 检查项
# ============================================================
def check_backup_dir_conflict(
    backup_dir: Path,
) -> Tuple[bool, str, Optional[Path]]:
    """
    检查 backup 目录是否存在冲突。

    冲突定义:backup 目录已存在且非空。

    Returns:
        (conflict_exists, message, suggested_subdir)
    """
    if not backup_dir.exists():
        return (False, f"backup 目录不存在(首次执行,可安全创建): {backup_dir}", None)

    # 目录存在,检查是否为空
    entries = [e for e in backup_dir.iterdir() if e.name not in (".gitkeep",)]
    if not entries:
        return (False, f"backup 目录为空(可安全使用): {backup_dir}", None)

    # 存在冲突,创建时间戳子目录
    ts_dir_name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suggested = backup_dir / ts_dir_name
    # 避免重名:加三位随机后缀
    if suggested.exists():
        import secrets
        suggested = backup_dir / f"{ts_dir_name}_{secrets.token_hex(2)}"
    suggested.mkdir(parents=True, exist_ok=True)

    return (
        True,
        f"backup 目录已存在且非空(已包含 {len(entries)} 个文件)。已创建时间戳子目录: {suggested.name}",
        suggested,
    )


def check_archive_dir(archive_dir: Path) -> Tuple[bool, str]:
    """
    检查 archive 目录是否存在,不存在则自动创建。

    Returns:
        (ok, message)
    """
    if archive_dir.exists() and archive_dir.is_dir():
        # 目录存在
        entries = [e for e in archive_dir.iterdir() if e.name not in (".gitkeep",)]
        return (True, f"archive 目录已存在(包含 {len(entries)} 个归档文件): {archive_dir}")

    # 不存在,自动创建
    archive_dir.mkdir(parents=True, exist_ok=True)
    return (True, f"archive 目录不存在,已自动创建: {archive_dir}")


def check_plan_values(
    plan: Dict[str, Any],
    expected_total: int = 342,
    expected_archive: int = 56,
    expected_keep: int = 286,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    验证迁移计划的关键值是否匹配预期。

    Returns:
        (ok, message, actual_values)
    """
    summary = plan.get("summary", {}) or {}
    actual_total = plan.get("total", -1)
    actual_normal = summary.get("normal_user", -1)
    actual_system = summary.get("system_pollution", -1)
    actual_ai = summary.get("ai_internal_pollution", -1)
    actual_invalid = summary.get("invalid", -1)

    # 计算 archive 总数(system + ai_internal + invalid)
    if actual_system >= 0 and actual_ai >= 0 and actual_invalid >= 0:
        actual_archive = actual_system + actual_ai + actual_invalid
    else:
        actual_archive = -1

    actual_keep = actual_normal if actual_normal >= 0 else -1

    actual_values = {
        "total": actual_total,
        "archive": actual_archive,
        "keep": actual_keep,
        "normal_user": actual_normal,
        "system_pollution": actual_system,
        "ai_internal_pollution": actual_ai,
        "invalid": actual_invalid,
    }

    # 校验
    issues: List[str] = []
    if actual_total != expected_total:
        issues.append(f"total: plan={actual_total} expected={expected_total}")
    if actual_archive != expected_archive:
        issues.append(f"archive: plan={actual_archive} expected={expected_archive}")
    if actual_keep != expected_keep:
        issues.append(f"keep: plan={actual_keep} expected={expected_keep}")

    if issues:
        msg = "plan 值与预期不匹配: " + "; ".join(issues)
        return (False, msg, actual_values)
    return (True, f"plan 值匹配预期 (total={expected_total}, archive={expected_archive}, keep={expected_keep})", actual_values)


def compute_current_state(source_path: Path) -> Dict[str, Any]:
    """计算当前 memory.json 的状态。"""
    if not source_path.exists():
        return {
            "exists": False,
            "size_bytes": -1,
            "size_human": "<not_found>",
            "sha256": "<not_found>",
            "record_count": 0,
            "last_modified": "<not_found>",
        }

    stat = source_path.stat()
    record_count = 0
    try:
        data = _load_memory(source_path)
        record_count = len(data)
    except Exception as e:
        record_count = -1

    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "size_human": _human_size(stat.st_size),
        "sha256": _file_hash(source_path, "sha256"),
        "record_count": record_count,
        "last_modified": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def find_latest_backup(backup_dir: Path) -> Optional[Path]:
    """查找最新的备份文件。"""
    if not backup_dir.exists():
        return None
    candidates = sorted(
        [p for p in backup_dir.iterdir() if p.is_file() and p.name.startswith("memory.json.")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ============================================================
# 主入口
# ============================================================
def run_preflight(
    plan_path: Path = DEFAULT_PLAN,
    source_path: Path = DEFAULT_SOURCE,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    archive_dir: Path = DEFAULT_ARCHIVE_DIR,
    expected_total: int = 342,
    expected_archive: int = 56,
    expected_keep: int = 286,
    strict: bool = False,
) -> Dict[str, Any]:
    """
    执行 preflight 检查,返回结构化结果。

    Returns:
        {
            "ok": bool,
            "checks": [每个检查项的结果],
            "current_state": {...},
            "plan_values": {...},
            "backup_path": Optional[Path],
            "archive_dir": Path,
            "rollback_path": Optional[Path],
            "message": str,
        }
    """
    checks: List[Dict[str, Any]] = []
    all_ok = True

    # 1. backup 目录冲突检查
    try:
        conflict, msg, suggested = check_backup_dir_conflict(backup_dir)
        # 冲突本身是 warning(已自动处理),不影响 ok 状态
        checks.append({
            "name": "backup_dir_conflict",
            "ok": True,
            "message": msg,
            "conflict": conflict,
            "suggested_subdir": str(suggested) if suggested else None,
        })
    except Exception as e:
        all_ok = False
        checks.append({
            "name": "backup_dir_conflict",
            "ok": False,
            "message": f"检查失败: {e}",
        })

    # 2. archive 目录检查
    try:
        ok, msg = check_archive_dir(archive_dir)
        checks.append({
            "name": "archive_dir",
            "ok": ok,
            "message": msg,
        })
        if not ok:
            all_ok = False
    except Exception as e:
        all_ok = False
        checks.append({
            "name": "archive_dir",
            "ok": False,
            "message": f"检查失败: {e}",
        })

    # 3. plan 加载
    plan: Optional[Dict[str, Any]] = None
    try:
        plan = _load_plan(plan_path)
        checks.append({
            "name": "plan_load",
            "ok": True,
            "message": f"plan 加载成功: {plan_path} (generated_at={plan.get('generated_at', '?')})",
        })
    except FileNotFoundError as e:
        all_ok = False
        checks.append({
            "name": "plan_load",
            "ok": False,
            "message": str(e),
        })
    except Exception as e:
        all_ok = False
        checks.append({
            "name": "plan_load",
            "ok": False,
            "message": f"plan 解析失败: {e}",
        })

    # 4. plan 值校验
    plan_values: Dict[str, Any] = {}
    if plan is not None:
        try:
            ok, msg, actual = check_plan_values(
                plan, expected_total, expected_archive, expected_keep
            )
            checks.append({
                "name": "plan_values",
                "ok": ok,
                "message": msg,
                "actual": actual,
                "expected": {
                    "total": expected_total,
                    "archive": expected_archive,
                    "keep": expected_keep,
                },
            })
            plan_values = actual
            if not ok and strict:
                all_ok = False
            elif not ok:
                # 非严格模式:warning,但不阻止
                pass
        except Exception as e:
            all_ok = False
            checks.append({
                "name": "plan_values",
                "ok": False,
                "message": f"plan 值校验异常: {e}",
            })

    # 5. 当前 memory.json 状态
    current_state = compute_current_state(source_path)
    checks.append({
        "name": "memory_state",
        "ok": current_state["exists"],
        "message": f"memory.json 存在={current_state['exists']}, "
                   f"size={current_state['size_human']}, "
                   f"records={current_state['record_count']}",
        "state": current_state,
    })

    # 6. rollback 路径
    latest_backup = find_latest_backup(backup_dir)
    rollback_path = str(latest_backup) if latest_backup else None
    checks.append({
        "name": "rollback_path",
        "ok": True,
        "message": f"rollback 路径: {rollback_path or '(尚无备份)'}",
        "rollback_path": rollback_path,
    })

    # 汇总消息
    failed = [c for c in checks if not c["ok"]]
    if failed:
        summary_msg = f"preflight FAILED: {len(failed)} 项检查不通过"
    else:
        summary_msg = "preflight PASSED: 全部检查通过"

    return {
        "ok": all_ok,
        "checks": checks,
        "current_state": current_state,
        "plan_values": plan_values,
        "archive_dir": str(archive_dir),
        "backup_dir": str(backup_dir),
        "rollback_path": rollback_path,
        "message": summary_msg,
        "expected": {
            "total": expected_total,
            "archive": expected_archive,
            "keep": expected_keep,
        },
    }


# ============================================================
# 报告生成
# ============================================================
def render_report(result: Dict[str, Any]) -> str:
    """生成 preflight Markdown 报告。"""
    lines: List[str] = []
    lines.append("# Memory Migration Preflight Report")
    lines.append("")
    lines.append(f"> Phase: **C.2.0 (Preflight)**  ")
    lines.append(f"> Generated at: `{_human_ts()}`  ")
    lines.append(f"> Status: **{'PASS' if result['ok'] else 'FAIL'}**")
    lines.append("")

    # 1. 摘要
    lines.append("## 1. 摘要")
    lines.append("")
    lines.append(f"**{result['message']}**")
    lines.append("")
    total_checks = len(result["checks"])
    passed = sum(1 for c in result["checks"] if c["ok"])
    lines.append(f"- 通过: **{passed} / {total_checks}**")
    lines.append(f"- 整体状态: **{'✅ PASS' if result['ok'] else '❌ FAIL'}**")
    lines.append("")

    # 2. 当前 memory.json 状态
    lines.append("## 2. 当前 memory.json 状态")
    lines.append("")
    cs = result["current_state"]
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 存在 | {cs['exists']} |")
    lines.append(f"| 大小(字节) | {cs['size_bytes']} |")
    lines.append(f"| 大小(可读) | {cs['size_human']} |")
    lines.append(f"| 记录数量 | {cs['record_count']} |")
    lines.append(f"| SHA-256 | `{cs['sha256']}` |")
    lines.append(f"| 最后修改 | `{cs['last_modified']}` |")
    lines.append("")

    # 3. 计划值
    lines.append("## 3. 迁移计划值")
    lines.append("")
    if result["plan_values"]:
        pv = result["plan_values"]
        exp = result["expected"]
        lines.append("| 指标 | 实际 | 预期 | 状态 |")
        lines.append("| --- | --- | --- | --- |")
        for k, exp_v in exp.items():
            act = pv.get(k, "?")
            status = "✅" if act == exp_v else "❌"
            lines.append(f"| {k} | {act} | {exp_v} | {status} |")
    else:
        lines.append("_(plan 加载失败,无数据)_")
    lines.append("")

    # 4. 预计迁移数量
    lines.append("## 4. 预计迁移数量")
    lines.append("")
    pv = result["plan_values"]
    if pv:
        lines.append("| 类别 | 数量 |")
        lines.append("| --- | --- |")
        lines.append(f"| system_pollution | {pv.get('system_pollution', '?')} |")
        lines.append(f"| ai_internal_pollution | {pv.get('ai_internal_pollution', '?')} |")
        lines.append(f"| invalid | {pv.get('invalid', '?')} |")
        lines.append(f"| **archive 合计** | **{pv.get('archive', '?')}** |")
        lines.append("| normal_user(保留) | " + str(pv.get("keep", "?")) + " |")
    else:
        lines.append("_(无 plan 数据)_")
    lines.append("")

    # 5. 路径
    lines.append("## 5. 路径信息")
    lines.append("")
    lines.append("| 项目 | 路径 |")
    lines.append("| --- | --- |")
    lines.append(f"| backup 根目录 | `{result['backup_dir']}` |")
    lines.append(f"| archive 目录 | `{result['archive_dir']}` |")
    lines.append(f"| **rollback 路径** | `{result['rollback_path'] or '(尚无备份)'}` |")
    lines.append("")

    # 6. 检查项明细
    lines.append("## 6. 检查项明细")
    lines.append("")
    for i, c in enumerate(result["checks"], 1):
        status = "✅" if c["ok"] else "❌"
        lines.append(f"### 6.{i} {c['name']} {status}")
        lines.append("")
        lines.append(f"- **状态:** {'PASS' if c['ok'] else 'FAIL'}")
        lines.append(f"- **消息:** {c['message']}")
        # 附加信息
        for k in ("conflict", "suggested_subdir", "actual", "expected",
                  "state", "rollback_path"):
            if k in c:
                v = c[k]
                if isinstance(v, (dict, list)):
                    lines.append(f"- **{k}:**")
                    lines.append("  ```json")
                    lines.append("  " + json.dumps(v, ensure_ascii=False, indent=2).replace("\n", "\n  "))
                    lines.append("  ```")
                else:
                    lines.append(f"- **{k}:** `{v}`")
        lines.append("")

    # 7. 结论
    lines.append("## 7. 结论")
    lines.append("")
    if result["ok"]:
        lines.append("✅ **preflight 全部通过,可以执行迁移。**")
        lines.append("")
        lines.append("执行命令:")
        lines.append("```bash")
        lines.append("python scripts/migrate_memory.py run --execute")
        lines.append("```")
    else:
        lines.append("❌ **preflight 未通过,不能执行迁移。**")
        lines.append("")
        lines.append("请根据上述失败项修复后重新运行 preflight。")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> 报告生成者: `scripts/preflight_memory.py` (Phase C.2.0)")
    lines.append("> 数据源: `data/memory.json` (只读) + `.cache/audit/memory_cleanup_plan.json` (只读)")
    lines.append("")
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================
def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory 迁移前置检查")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN,
                        help="迁移计划 JSON 路径")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="memory.json 路径")
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
                        help="backup 目录")
    parser.add_argument("--archive-dir", type=Path, default=DEFAULT_ARCHIVE_DIR,
                        help="archive 目录")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                        help="preflight 报告输出路径")
    parser.add_argument("--expected-total", type=int, default=342,
                        help="预期 plan.total (Phase C.2.0 2026-08-02 更新)")
    parser.add_argument("--expected-archive", type=int, default=56,
                        help="预期 archive 总数 (Phase C.2.0 2026-08-02 更新)")
    parser.add_argument("--expected-keep", type=int, default=286,
                        help="预期 keep(保留)数量 (Phase C.2.0 2026-08-02 更新)")
    parser.add_argument("--strict", action="store_true",
                        help="严格模式:plan 值不匹配预期时直接失败")
    parser.add_argument("--quiet", action="store_true",
                        help="静默模式(只输出关键信息)")
    args = parser.parse_args(argv)

    print(f"[preflight] plan = {args.plan}")
    print(f"[preflight] source = {args.source}")
    print(f"[preflight] expected = total={args.expected_total}, "
          f"archive={args.expected_archive}, keep={args.expected_keep}")
    print()

    result = run_preflight(
        plan_path=args.plan,
        source_path=args.source,
        backup_dir=args.backup_dir,
        archive_dir=args.archive_dir,
        expected_total=args.expected_total,
        expected_archive=args.expected_archive,
        expected_keep=args.expected_keep,
        strict=args.strict,
    )

    # 输出结构化摘要
    if not args.quiet:
        print("=" * 60)
        print(result["message"])
        print("=" * 60)
        for c in result["checks"]:
            status = "✅" if c["ok"] else "❌"
            print(f"  {status} {c['name']}: {c['message']}")
        print()
        print(f"memory.json  hash: {result['current_state']['sha256'][:16]}...")
        print(f"memory.json  size: {result['current_state']['size_human']}")
        print(f"memory.json  records: {result['current_state']['record_count']}")
        if result["plan_values"]:
            pv = result["plan_values"]
            print(f"plan total: {pv.get('total')}, archive: {pv.get('archive')}, keep: {pv.get('keep')}")
        print(f"rollback path: {result['rollback_path'] or '(none)'}")
        print()

    # 写报告
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(result)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[preflight] report -> {args.report}")

    # 记录日志
    _log("preflight_done", {
        "ok": result["ok"],
        "current_record_count": result["current_state"]["record_count"],
        "current_size_bytes": result["current_state"]["size_bytes"],
        "current_sha256": result["current_state"]["sha256"],
        "plan_values": result["plan_values"],
        "rollback_path": result["rollback_path"],
    })

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
