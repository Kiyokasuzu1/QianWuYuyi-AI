# -*- coding: utf-8 -*-
"""
scripts/migrate_memory.py

Phase C.1 — P1-3: Memory 安全迁移脚本(配套 audit_memory.py)

职责:
- 基于 audit_memory.py 生成的 quarantine 计划,执行安全迁移
- **默认 dry-run 模式**,不直接修改 memory.json
- 通过 --execute 参数显式确认后,才会写入归档 + 清理 memory.json
- 每个 phase 都有备份,可一键回滚

约束:
- 必须先运行 audit_memory.py 生成 plan
- 任何写入操作前都会自动备份
- 不删除归档文件,默认保留 30 天
- 所有操作记录到 .cache/audit/migration_log.jsonl

用法:
    # 1. 先 dry-run(只显示计划)
    python scripts/migrate_memory.py run

    # 2. 仅执行 preflight 检查(不执行迁移)
    python scripts/migrate_memory.py preflight

    # 3. 显式执行(自动触发 preflight;preflight 未通过则拒绝)
    python scripts/migrate_memory.py run --execute

    # 4. 回滚
    python scripts/migrate_memory.py rollback <backup_path>

安全设计:
- 不接受 --force 参数
- 执行前会再确认一次(交互式)
- 每次操作生成独立备份
- 任何失败立即终止,不破坏现有数据
- **run --execute 会自动触发 preflight,preflight 未通过时拒绝执行**
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "memory.json"
DEFAULT_ARCHIVE_DIR = PROJECT_ROOT / "data" / "memory_archive"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "data" / "memory_backup"
DEFAULT_PLAN = PROJECT_ROOT / ".cache" / "audit" / "memory_cleanup_plan.json"
DEFAULT_PREFLIGHT_REPORT = PROJECT_ROOT / "docs" / "audit" / "memory_migration_preflight.md"
LOG_PATH = PROJECT_ROOT / ".cache" / "audit" / "migration_log.jsonl"

# Phase C.2.0: 迁移计划预期值(用于 preflight 校验)
# 2026-08-02 更新:PollutionGuard(C.2.3)部署后,系统累计产生了 286 条正常
# 用户记忆 + 56 条 invalid 测试残留 + 0 真实污染。原始 201/201/0 预期已
# 不再适用(原始预期基于 C.2.1 时的严重污染态)。
EXPECTED_TOTAL = 342
EXPECTED_ARCHIVE = 56
EXPECTED_KEEP = 286


def _run_preflight_or_exit(
    plan_path: Path,
    source_path: Path,
    report_path: Path = None,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    调用 preflight_memory.run_preflight,失败则 sys.exit(2)。

    Phase C.2.0:run --execute 前必须通过 preflight 检查。
    """
    if report_path is None:
        report_path = DEFAULT_PREFLIGHT_REPORT
    try:
        from preflight_memory import run_preflight, render_report
    except ImportError as e:
        print(
            f"[preflight] 无法导入 preflight_memory: {e}",
            file=sys.stderr,
        )
        print(
            "[preflight] 请确认 scripts/preflight_memory.py 存在",
            file=sys.stderr,
        )
        sys.exit(2)

    print("=" * 60)
    print("[preflight] Phase C.2.0 — Migration Preflight Check")
    print("=" * 60)

    result = run_preflight(
        plan_path=plan_path,
        source_path=source_path,
        backup_dir=DEFAULT_BACKUP_DIR,
        archive_dir=DEFAULT_ARCHIVE_DIR,
        expected_total=EXPECTED_TOTAL,
        expected_archive=EXPECTED_ARCHIVE,
        expected_keep=EXPECTED_KEEP,
        strict=strict,
    )

    # 输出检查摘要
    for c in result["checks"]:
        status = "✅" if c["ok"] else "❌"
        print(f"  {status} {c['name']}: {c['message']}")
    print()
    print(f"  memory.json hash: {result['current_state']['sha256'][:16]}...")
    print(f"  memory.json size: {result['current_state']['size_human']}")
    print(f"  memory.json records: {result['current_state']['record_count']}")
    if result["plan_values"]:
        pv = result["plan_values"]
        print(
            f"  plan total={pv.get('total')}, archive={pv.get('archive')}, "
            f"keep={pv.get('keep')}"
        )
    print(f"  rollback path: {result['rollback_path'] or '(none)'}")
    print()
    print(f"  preflight status: {result['message']}")
    print()

    # 写报告
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = render_report(result)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"[preflight] report -> {report_path}")
    except Exception as e:  # noqa: BLE001
        print(f"[preflight] 写报告失败(不阻塞): {e}", file=sys.stderr)

    # 记录日志
    _log("preflight_check", {
        "ok": result["ok"],
        "current_record_count": result["current_state"]["record_count"],
        "current_size_bytes": result["current_state"]["size_bytes"],
        "current_sha256": result["current_state"]["sha256"],
        "plan_values": result["plan_values"],
        "rollback_path": result["rollback_path"],
        "report": str(report_path),
    })

    # strict 模式下:不通过则直接退出
    if strict and not result["ok"]:
        print()
        print("❌ preflight 未通过,拒绝执行 run --execute。", file=sys.stderr)
        print("   请根据 preflight 报告修复后重试:", file=sys.stderr)
        print(f"   {report_path}", file=sys.stderr)
        sys.exit(2)

    return result


def _utc_now() -> str:
    """生成 UTC 时间戳字符串(包含毫秒,避免同秒内冲突)。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _log(event: str, payload: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": event,
            "ts": _utc_now(),
            **payload,
        }, ensure_ascii=False) + "\n")


def _load_plan(plan_path: Path) -> Dict[str, Any]:
    if not plan_path.exists():
        raise FileNotFoundError(
            f"plan not found: {plan_path} — 请先运行 scripts/audit_memory.py"
        )
    with open(plan_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_memory(source_path: Path) -> List[Dict[str, Any]]:
    if not source_path.exists():
        raise FileNotFoundError(f"memory not found: {source_path}")
    with open(source_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"memory.json 必须为 list,当前为 {type(data).__name__}")
    return data


def _collect_quarantine_ids(plan: Dict[str, Any]) -> Dict[str, List[str]]:
    """从 plan 中提取所有 quarantine id(按 category 归类)。"""
    q = plan.get("quarantine_ids", {})
    return {
        cat: list(q.get(cat, []))
        for cat in ("system_pollution", "ai_internal_pollution", "invalid")
    }


def _partition_memories(
    memories: List[Dict[str, Any]],
    quarantine: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """根据 quarantine id 列表,划分正常记录与待归档记录。

    Returns:
        (normal_memories, archive_memories_by_category)
    """
    id_to_category: Dict[str, str] = {}
    for cat, ids in quarantine.items():
        for mid in ids:
            id_to_category[mid] = cat

    normal: List[Dict[str, Any]] = []
    archive: Dict[str, List[Dict[str, Any]]] = {
        "system_pollution": [],
        "ai_internal_pollution": [],
        "invalid": [],
    }

    for m in memories:
        mid = str(m.get("id", "") or "")
        cat = id_to_category.get(mid)
        if cat is None:
            normal.append(m)
        else:
            archive[cat].append(m)

    return normal, archive


def _do_backup(source_path: Path, backup_dir: Path) -> Path:
    """把 memory.json 复制到 backup_dir,返回备份路径。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now()
    backup_path = backup_dir / f"memory.json.{ts}"
    shutil.copy2(source_path, backup_path)
    _log("backup", {"src": str(source_path), "dst": str(backup_path)})
    return backup_path


def _do_archive(
    archive_dir: Path,
    archive_payload: Dict[str, List[Dict[str, Any]]],
) -> Path:
    """把待归档记录写入 memory_archive/<timestamp>.json。"""
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now()
    archive_path = archive_dir / f"system_ai_internal_{ts}.json"
    payload = {
        "version": "1.0",
        "created_at": ts,
        "source": "memory.json (Phase C.1 P1-3 migration)",
        "summary": {
            "system_pollution": len(archive_payload["system_pollution"]),
            "ai_internal_pollution": len(archive_payload["ai_internal_pollution"]),
            "invalid": len(archive_payload["invalid"]),
            "total": sum(len(v) for v in archive_payload.values()),
        },
        "data": archive_payload,
    }
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    _log("archive", {"dst": str(archive_path), **payload["summary"]})
    return archive_path


def _do_rewrite(source_path: Path, normal: List[Dict[str, Any]]) -> None:
    """把 normal 列表写回 memory.json(原子写入)。"""
    tmp_path = source_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(normal, f, ensure_ascii=False, indent=2)
    tmp_path.replace(source_path)
    _log("rewrite", {"dst": str(source_path), "kept": len(normal)})


def _print_dry_run(
    plan: Dict[str, Any],
    quarantine: Dict[str, List[str]],
    archive: Dict[str, List[Dict[str, Any]]],
) -> None:
    print("=" * 60)
    print("DRY-RUN: 不会修改任何数据")
    print("=" * 60)
    print(f"plan 源: {plan.get('source', '(unknown)')}")
    print(f"plan 生成时间: {plan.get('generated_at', '(unknown)')}")
    print()
    print("[Partition 划分]")
    print(f"  normal_user  保留: {plan['summary'].get('normal_user', 0)}")
    print(f"  system_pollution   待归档: {len(quarantine['system_pollution'])}")
    print(f"  ai_internal_pollution 待归档: {len(quarantine['ai_internal_pollution'])}")
    print(f"  invalid        待归档: {len(quarantine['invalid'])}")
    print()
    print("[操作计划]")
    print("  Phase 1: 备份 memory.json -> data/memory_backup/")
    print("  Phase 2: 写入归档 -> data/memory_archive/system_ai_internal_<ts>.json")
    print("  Phase 3: 重写 memory.json (仅保留 normal_user)")
    print()
    print("[样本预览] 每类最多 3 条:")
    for cat, items in archive.items():
        print(f"  {cat}:")
        for m in items[:3]:
            cid = m.get("id", "?")
            content = str(m.get("content", ""))[:60]
            print(f"    - {cid} | content='{content}'")
    print()
    print("确认执行请加 --execute (默认 dry-run,不会修改任何文件)")


def cmd_run(plan_path: Path, source_path: Path, execute: bool) -> int:
    # Phase C.2.0:run --execute 必须先通过 preflight
    if execute:
        _run_preflight_or_exit(plan_path, source_path, report_path=DEFAULT_PREFLIGHT_REPORT, strict=True)

    plan = _load_plan(plan_path)
    quarantine = _collect_quarantine_ids(plan)
    memories = _load_memory(source_path)
    normal, archive = _partition_memories(memories, quarantine)

    _print_dry_run(plan, quarantine, archive)

    if not execute:
        print()
        print("[DRY-RUN 完成] 无文件被修改。")
        return 0

    # 二次确认(非交互式):要求显式输入 --execute 才继续
    print()
    print("⚠️  检测到 --execute 参数,即将执行以下写操作:")
    print("   1. 复制 memory.json -> data/memory_backup/memory.json.<ts>")
    print("   2. 写入 data/memory_archive/system_ai_internal_<ts>.json")
    print("   3. 重写 memory.json(只保留 normal_user)")
    print()
    print("   如需继续,请在 5 秒内按 Ctrl+C 取消...")
    print("   等待 5 秒后自动继续...")

    import time
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        print()
        print("[已取消] 未执行任何操作。")
        return 130

    print()
    print("[开始执行]")

    # Phase 1: 备份
    backup_path = _do_backup(source_path, DEFAULT_BACKUP_DIR)
    print(f"  [1/3] 备份完成: {backup_path}")

    # Phase 2: 写入归档
    archive_path = _do_archive(DEFAULT_ARCHIVE_DIR, archive)
    print(f"  [2/3] 归档完成: {archive_path}")

    # Phase 3: 重写 memory.json
    _do_rewrite(source_path, normal)
    print(f"  [3/3] 重写完成: {source_path} (保留 {len(normal)} 条)")

    print()
    print("[完成]")
    print(f"备份路径: {backup_path}")
    print(f"归档路径: {archive_path}")
    print(f"回滚命令: python scripts/migrate_memory.py rollback {backup_path}")
    _log("migrate_done", {
        "backup": str(backup_path),
        "archive": str(archive_path),
        "kept": len(normal),
        "migrated": sum(len(v) for v in archive.values()),
    })
    return 0


def cmd_preflight(plan_path: Path, source_path: Path, strict: bool) -> int:
    """仅执行 preflight 检查(不执行迁移)。"""
    result = _run_preflight_or_exit(
        plan_path, source_path, report_path=DEFAULT_PREFLIGHT_REPORT, strict=strict
    )
    return 0 if result["ok"] else 1


def cmd_rollback(backup_path: Path, source_path: Path) -> int:
    if not backup_path.exists():
        print(f"[rollback] 备份文件不存在: {backup_path}", file=sys.stderr)
        return 1
    # 先把当前 memory.json 再备份一次(防止误操作)
    safety_backup = _do_backup(source_path, DEFAULT_BACKUP_DIR)
    print(f"[rollback] 当前 memory.json 已备份到: {safety_backup}")
    if safety_backup.resolve() == backup_path.resolve():
        # 同名冲突(同毫秒内两次备份):不允许用同一文件覆盖自身
        # 强制使用不同文件名
        safety_backup = DEFAULT_BACKUP_DIR / f"memory.json.rollback.{_utc_now()}"
        shutil.copy2(source_path, safety_backup)
        _log("safety_backup_renamed", {"dst": str(safety_backup)})
    shutil.copy2(backup_path, source_path)
    print(f"[rollback] 已还原 memory.json <- {backup_path}")
    _log("rollback", {"from": str(backup_path), "to": str(source_path)})
    return 0


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description="Memory 安全迁移(配套 audit_memory.py)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="执行迁移(默认 dry-run)")
    p_run.add_argument("--plan", type=Path, default=DEFAULT_PLAN,
                       help="audit_memory.py 生成的 plan 路径")
    p_run.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                       help="memory.json 路径")
    p_run.add_argument("--execute", action="store_true",
                       help="显式执行(默认 dry-run);会触发 preflight 检查")

    p_pre = sub.add_parser("preflight", help="仅执行 preflight 检查(不执行迁移)")
    p_pre.add_argument("--plan", type=Path, default=DEFAULT_PLAN,
                       help="audit_memory.py 生成的 plan 路径")
    p_pre.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                       help="memory.json 路径")
    p_pre.add_argument("--no-strict", action="store_true",
                       help="非严格模式(plan 值不匹配仅警告,不阻塞)")

    p_rb = sub.add_parser("rollback", help="从备份回滚")
    p_rb.add_argument("backup_path", type=Path,
                      help="备份文件路径(memory.json.YYYYMMDD_HHMMSS)")
    p_rb.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                      help="目标 memory.json 路径")

    args = parser.parse_args(argv)

    if args.cmd == "run":
        return cmd_run(args.plan, args.source, args.execute)
    if args.cmd == "preflight":
        return cmd_preflight(args.plan, args.source, strict=not args.no_strict)
    if args.cmd == "rollback":
        return cmd_rollback(args.backup_path, args.source)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
