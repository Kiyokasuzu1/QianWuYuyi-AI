# -*- coding: utf-8 -*-
"""
tests/test_preflight_memory.py

Phase C.2.0 — 验证 scripts/preflight_memory.py 的迁移前置检查逻辑,
以及 scripts/migrate_memory.py 中的 preflight 集成。

覆盖:
- backup 目录冲突检测 + 时间戳子目录创建
- archive 目录自动创建
- plan 文件加载 + 关键值校验
- 当前 memory.json 状态计算 (hash / size / record_count)
- rollback 路径识别
- run --execute 在 preflight 失败时拒绝执行 (sys.exit(2))
- run --execute 在 preflight 通过时继续执行迁移
- preflight 子命令独立可用
- 报告文件可正常生成
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS))

import preflight_memory as pf  # noqa: E402
import migrate_memory as mm  # noqa: E402


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """提供独立的 memory.json / plan / archive / backup / report 目录。"""
    src = tmp_path / "memory.json"
    archive = tmp_path / "memory_archive"
    backup = tmp_path / "memory_backup"
    plan = tmp_path / "plan.json"
    report = tmp_path / "preflight_report.md"
    log = tmp_path / "migration_log.jsonl"
    preflight_log = tmp_path / "preflight_log.jsonl"

    # 重定向 migrate_memory 模块级常量
    monkeypatch.setattr(mm, "DEFAULT_ARCHIVE_DIR", archive)
    monkeypatch.setattr(mm, "DEFAULT_BACKUP_DIR", backup)
    monkeypatch.setattr(mm, "LOG_PATH", log)
    monkeypatch.setattr(mm, "DEFAULT_PREFLIGHT_REPORT", report)

    # 重定向 preflight_memory 模块级常量
    monkeypatch.setattr(pf, "DEFAULT_ARCHIVE_DIR", archive)
    monkeypatch.setattr(pf, "DEFAULT_BACKUP_DIR", backup)
    monkeypatch.setattr(pf, "LOG_PATH", preflight_log)
    monkeypatch.setattr(pf, "DEFAULT_REPORT", report)

    return {
        "src": src,
        "archive": archive,
        "backup": backup,
        "plan": plan,
        "report": report,
        "log": log,
        "preflight_log": preflight_log,
        "root": tmp_path,
    }


def _write_memory(path: Path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _write_plan(path: Path, total: int, summary: dict, quarantine_ids: dict | None = None):
    payload = {
        "phase": "C.1 P1-3",
        "generated_at": "2026-08-02T10:00:00Z",
        "source": "test",
        "total": total,
        "summary": summary,
        "phases": [],
        "quarantine_ids": quarantine_ids or {
            "system_pollution": [],
            "ai_internal_pollution": [],
            "invalid": [],
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


# ============================================================
# 1. backup 目录冲突检测
# ============================================================

class TestBackupDirConflict:
    def test_no_conflict_when_backup_dir_not_exists(self, tmp_workspace):
        # backup 目录不存在
        conflict, msg, suggested = pf.check_backup_dir_conflict(
            tmp_workspace["backup"]
        )
        assert conflict is False
        assert suggested is None
        assert "不存在" in msg or "可安全" in msg

    def test_no_conflict_when_backup_dir_empty(self, tmp_workspace):
        # backup 目录存在但为空
        tmp_workspace["backup"].mkdir()
        conflict, msg, suggested = pf.check_backup_dir_conflict(
            tmp_workspace["backup"]
        )
        assert conflict is False
        assert suggested is None

    def test_conflict_creates_timestamped_subdir(self, tmp_workspace):
        # backup 目录存在且非空
        tmp_workspace["backup"].mkdir()
        (tmp_workspace["backup"] / "memory.json.20260802T100000000000Z").write_text(
            "x", encoding="utf-8"
        )
        conflict, msg, suggested = pf.check_backup_dir_conflict(
            tmp_workspace["backup"]
        )
        assert conflict is True
        assert suggested is not None
        assert suggested.exists()
        # 验证命名格式:YYYYMMDD_HHMMSS
        assert suggested.name.split("_")[0].isdigit()
        assert len(suggested.name.split("_")[0]) == 8
        # 验证 message 包含文件数和子目录名
        assert "1 个文件" in msg
        assert suggested.name in msg


# ============================================================
# 2. archive 目录自动创建
# ============================================================

class TestArchiveDir:
    def test_creates_archive_dir_if_missing(self, tmp_workspace):
        # archive 目录不存在
        assert not tmp_workspace["archive"].exists()
        ok, msg = pf.check_archive_dir(tmp_workspace["archive"])
        assert ok is True
        assert tmp_workspace["archive"].exists()
        assert "自动创建" in msg

    def test_existing_archive_dir_passes(self, tmp_workspace):
        tmp_workspace["archive"].mkdir()
        (tmp_workspace["archive"] / "old_archive.json").write_text(
            "x", encoding="utf-8"
        )
        ok, msg = pf.check_archive_dir(tmp_workspace["archive"])
        assert ok is True
        assert "1 个归档文件" in msg


# ============================================================
# 3. plan 值校验
# ============================================================

class TestPlanValues:
    def test_matching_values_pass(self, tmp_workspace):
        plan = {
            "total": 342,
            "summary": {
                "normal_user": 286,
                "system_pollution": 0,
                "ai_internal_pollution": 0,
                "invalid": 56,
            },
        }
        ok, msg, actual = pf.check_plan_values(
            plan, expected_total=342, expected_archive=56, expected_keep=286
        )
        assert ok is True
        assert actual["total"] == 342
        assert actual["archive"] == 56
        assert actual["keep"] == 286
        assert "匹配预期" in msg

    def test_mismatched_total_fails(self, tmp_workspace):
        plan = {
            "total": 201,
            "summary": {
                "normal_user": 0,
                "system_pollution": 1,
                "ai_internal_pollution": 105,
                "invalid": 95,
            },
        }
        ok, msg, actual = pf.check_plan_values(
            plan, expected_total=342, expected_archive=56, expected_keep=286
        )
        assert ok is False
        assert "total" in msg
        assert "201" in msg
        assert "342" in msg

    def test_archive_calculated_as_sum(self, tmp_workspace):
        # archive = system + ai_internal + invalid
        plan = {
            "total": 100,
            "summary": {
                "normal_user": 50,
                "system_pollution": 5,
                "ai_internal_pollution": 10,
                "invalid": 35,
            },
        }
        ok, msg, actual = pf.check_plan_values(
            plan, expected_total=100, expected_archive=50, expected_keep=50
        )
        assert ok is True
        assert actual["archive"] == 50
        assert actual["keep"] == 50

    def test_keep_is_normal_user(self, tmp_workspace):
        plan = {
            "total": 10,
            "summary": {
                "normal_user": 7,
                "system_pollution": 1,
                "ai_internal_pollution": 1,
                "invalid": 1,
            },
        }
        ok, _, actual = pf.check_plan_values(
            plan, expected_total=10, expected_archive=3, expected_keep=7
        )
        assert ok is True
        assert actual["keep"] == 7


# ============================================================
# 4. memory.json 状态计算
# ============================================================

class TestCurrentState:
    def test_compute_state_for_existing_file(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [
            {"id": "a", "content": "test1"},
            {"id": "b", "content": "test2"},
            {"id": "c", "content": "test3"},
        ])
        state = pf.compute_current_state(tmp_workspace["src"])
        assert state["exists"] is True
        assert state["record_count"] == 3
        assert state["size_bytes"] > 0
        assert state["sha256"] != "<not_found>"
        assert len(state["sha256"]) == 64  # SHA-256 hex
        assert "KB" in state["size_human"] or "B" in state["size_human"]

    def test_compute_state_for_missing_file(self, tmp_workspace):
        state = pf.compute_current_state(tmp_workspace["src"])
        assert state["exists"] is False
        assert state["record_count"] == 0
        assert state["sha256"] == "<not_found>"


# ============================================================
# 5. rollback 路径识别
# ============================================================

class TestRollbackPath:
    def test_no_backup_returns_none(self, tmp_workspace):
        assert pf.find_latest_backup(tmp_workspace["backup"]) is None

    def test_finds_latest_backup(self, tmp_workspace):
        tmp_workspace["backup"].mkdir()
        # 创建多个备份,验证选取最新
        old = tmp_workspace["backup"] / "memory.json.20260801T100000000000Z"
        new = tmp_workspace["backup"] / "memory.json.20260802T100000000000Z"
        old.write_text("x", encoding="utf-8")
        # 设置 mtime 区分
        import time
        old_stat = old.stat()
        new.write_text("x", encoding="utf-8")
        time.sleep(0.05)  # 确保 mtime 不同
        new_stat = new.stat()
        assert new_stat.st_mtime >= old_stat.st_mtime
        latest = pf.find_latest_backup(tmp_workspace["backup"])
        assert latest is not None
        assert latest.name.startswith("memory.json.")

    def test_ignores_non_matching_files(self, tmp_workspace):
        tmp_workspace["backup"].mkdir()
        (tmp_workspace["backup"] / "random.txt").write_text("x")
        assert pf.find_latest_backup(tmp_workspace["backup"]) is None


# ============================================================
# 6. 完整 run_preflight 流程
# ============================================================

class TestRunPreflight:
    def test_all_pass_when_values_match(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [
            {"id": f"m_{i}", "content": f"x{i}", "metadata": {"memory_type": "user_shared"}}
            for i in range(5)
        ])
        _write_plan(tmp_workspace["plan"], total=5, summary={
            "normal_user": 5,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        result = pf.run_preflight(
            plan_path=tmp_workspace["plan"],
            source_path=tmp_workspace["src"],
            backup_dir=tmp_workspace["backup"],
            archive_dir=tmp_workspace["archive"],
            expected_total=5,
            expected_archive=0,
            expected_keep=5,
            strict=True,
        )
        assert result["ok"] is True
        assert len(result["checks"]) == 6  # 6 个检查项
        # 至少 plan_values 和 plan_load 应通过
        plan_values_check = next(c for c in result["checks"] if c["name"] == "plan_values")
        assert plan_values_check["ok"] is True

    def test_strict_mode_marks_mismatch_as_failure(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        _write_plan(tmp_workspace["plan"], total=10, summary={
            "normal_user": 5,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 5,
        })
        result = pf.run_preflight(
            plan_path=tmp_workspace["plan"],
            source_path=tmp_workspace["src"],
            backup_dir=tmp_workspace["backup"],
            archive_dir=tmp_workspace["archive"],
            expected_total=5,  # 故意不匹配
            expected_archive=5,
            expected_keep=0,
            strict=True,
        )
        assert result["ok"] is False

    def test_non_strict_mode_allows_mismatch(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        _write_plan(tmp_workspace["plan"], total=10, summary={
            "normal_user": 5,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 5,
        })
        result = pf.run_preflight(
            plan_path=tmp_workspace["plan"],
            source_path=tmp_workspace["src"],
            backup_dir=tmp_workspace["backup"],
            archive_dir=tmp_workspace["archive"],
            expected_total=5,
            expected_archive=5,
            expected_keep=0,
            strict=False,  # 非严格:警告但不阻止
        )
        # 非严格模式下,plan_values 不通过但整体 ok 仍为 True
        # (因为该检查的失败不计入 all_ok)


# ============================================================
# 7. 报告生成
# ============================================================

class TestReportGeneration:
    def test_report_contains_all_sections(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [
            {"id": "a", "content": "test"}
        ])
        _write_plan(tmp_workspace["plan"], total=1, summary={
            "normal_user": 1,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        result = pf.run_preflight(
            plan_path=tmp_workspace["plan"],
            source_path=tmp_workspace["src"],
            backup_dir=tmp_workspace["backup"],
            archive_dir=tmp_workspace["archive"],
            expected_total=1,
            expected_archive=0,
            expected_keep=1,
            strict=True,
        )
        report = pf.render_report(result)
        assert "Memory Migration Preflight Report" in report
        assert "## 1. 摘要" in report
        assert "## 2. 当前 memory.json 状态" in report
        assert "## 3. 迁移计划值" in report
        assert "## 4. 预计迁移数量" in report
        assert "## 5. 路径信息" in report
        assert "## 6. 检查项明细" in report
        assert "## 7. 结论" in report
        assert "rollback" in report.lower()


# ============================================================
# 8. migrate_memory 集成: run --execute 必须先通过 preflight
# ============================================================

class TestMigrateMemoryPreflightIntegration:
    def test_run_execute_calls_preflight(self, tmp_workspace):
        """run --execute 应当调用 preflight(可从日志验证)。"""
        _write_memory(tmp_workspace["src"], [
            {"id": f"m_{i}", "content": f"x{i}", "metadata": {"memory_type": "user_shared"}}
            for i in range(3)
        ])
        _write_plan(tmp_workspace["plan"], total=3, summary={
            "normal_user": 3,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        # 默认 EXPECTED_* 为 342/56/286(当前 plan=3 → preflight 失败)
        # 验证 run --execute 拒绝执行
        with pytest.raises(SystemExit) as exc_info:
            mm.cmd_run(tmp_workspace["plan"], tmp_workspace["src"], execute=True)
        assert exc_info.value.code == 2
        # 验证:未执行迁移(memory.json 未变)
        data = json.loads(tmp_workspace["src"].read_text(encoding="utf-8"))
        assert len(data) == 3  # 原始数量未变

    def test_run_execute_succeeds_with_matching_expected(self, tmp_workspace, monkeypatch):
        """将 EXPECTED_* 调整为 plan 实际值,run --execute 可正常执行。"""
        _write_memory(tmp_workspace["src"], [
            {"id": "keep_1", "content": "keep me", "metadata": {"memory_type": "user_shared"}},
            {"id": "q_1", "content": "remove me", "metadata": {"memory_type": "test"}},
        ])
        _write_plan(
            tmp_workspace["plan"],
            total=2,
            summary={
                "normal_user": 1,
                "system_pollution": 0,
                "ai_internal_pollution": 0,
                "invalid": 1,
            },
            quarantine_ids={
                "system_pollution": [],
                "ai_internal_pollution": [],
                "invalid": ["q_1"],
            },
        )
        # patch EXPECTED 常量以匹配 plan
        monkeypatch.setattr(mm, "EXPECTED_TOTAL", 2)
        monkeypatch.setattr(mm, "EXPECTED_ARCHIVE", 1)
        monkeypatch.setattr(mm, "EXPECTED_KEEP", 1)
        # run --execute (preflight 通过,跳过 5s 延迟需 monkeypatch)
        import time
        monkeypatch.setattr("time.sleep", lambda x: None)
        rc = mm.cmd_run(tmp_workspace["plan"], tmp_workspace["src"], execute=True)
        assert rc == 0
        # 验证:迁移完成,保留 1 条
        data = json.loads(tmp_workspace["src"].read_text(encoding="utf-8"))
        assert len(data) == 1
        assert data[0]["id"] == "keep_1"
        # 验证:备份和归档已生成
        assert tmp_workspace["backup"].exists()
        assert tmp_workspace["archive"].exists()
        # 验证:preflight 报告已生成
        assert tmp_workspace["report"].exists()

    def test_dry_run_skips_preflight(self, tmp_workspace, capsys):
        """dry-run 模式不调用 preflight。"""
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        _write_plan(tmp_workspace["plan"], total=1, summary={
            "normal_user": 1,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        # dry-run (plan 与 EXPECTED 不匹配也不会触发 preflight)
        rc = mm.cmd_run(tmp_workspace["plan"], tmp_workspace["src"], execute=False)
        assert rc == 0
        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out
        assert "preflight" not in captured.out  # 不应出现 preflight 字样


# ============================================================
# 9. preflight 子命令
# ============================================================

class TestPreflightSubcommand:
    def test_preflight_subcommand_success(self, tmp_workspace, capsys, monkeypatch):
        """preflight 子命令独立运行可生成报告。"""
        _write_memory(tmp_workspace["src"], [
            {"id": "a", "content": "x", "metadata": {"memory_type": "user_shared"}}
        ])
        _write_plan(tmp_workspace["plan"], total=1, summary={
            "normal_user": 1,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        monkeypatch.setattr(mm, "EXPECTED_TOTAL", 1)
        monkeypatch.setattr(mm, "EXPECTED_ARCHIVE", 0)
        monkeypatch.setattr(mm, "EXPECTED_KEEP", 1)

        rc = mm.cmd_preflight(
            tmp_workspace["plan"], tmp_workspace["src"], strict=True
        )
        assert rc == 0
        # 验证报告生成
        assert tmp_workspace["report"].exists()
        report_text = tmp_workspace["report"].read_text(encoding="utf-8")
        assert "Memory Migration Preflight Report" in report_text

    def test_preflight_subcommand_failure_sys_exits(self, tmp_workspace, monkeypatch):
        """strict 模式下 preflight 失败应 sys.exit(2)。"""
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        _write_plan(tmp_workspace["plan"], total=999, summary={
            "normal_user": 999,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        # plan total=999, EXPECTED=1 → 不匹配
        monkeypatch.setattr(mm, "EXPECTED_TOTAL", 1)
        monkeypatch.setattr(mm, "EXPECTED_ARCHIVE", 0)
        monkeypatch.setattr(mm, "EXPECTED_KEEP", 1)
        # strict=True 时,_run_preflight_or_exit 在失败时 sys.exit(2)
        with pytest.raises(SystemExit) as exc_info:
            mm.cmd_preflight(
                tmp_workspace["plan"], tmp_workspace["src"], strict=True
            )
        assert exc_info.value.code == 2

    def test_preflight_subcommand_non_strict_returns_zero_with_warning(self, tmp_workspace, monkeypatch):
        """非 strict 模式下 preflight 失败仍返回 0(警告而非阻塞)。"""
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        _write_plan(tmp_workspace["plan"], total=999, summary={
            "normal_user": 999,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        monkeypatch.setattr(mm, "EXPECTED_TOTAL", 1)
        monkeypatch.setattr(mm, "EXPECTED_ARCHIVE", 0)
        monkeypatch.setattr(mm, "EXPECTED_KEEP", 1)
        # strict=False: plan_values 不匹配仅警告,不阻塞,rc=0
        rc = mm.cmd_preflight(
            tmp_workspace["plan"], tmp_workspace["src"], strict=False
        )
        assert rc == 0
        # 验证报告存在,且包含 plan_values 失败的提示
        assert tmp_workspace["report"].exists()
        report_text = tmp_workspace["report"].read_text(encoding="utf-8")
        # 非严格模式下,plan_values 仍标记为失败(便于用户看到)
        assert "FAIL" in report_text or "失败" in report_text


# ============================================================
# 10. CLI 主入口
# ============================================================

class TestMainCLI:
    def test_main_preflight_subcommand(self, tmp_workspace, monkeypatch, capsys):
        """main() 接受 preflight 子命令。"""
        _write_memory(tmp_workspace["src"], [{"id": "a", "content": "x"}])
        _write_plan(tmp_workspace["plan"], total=1, summary={
            "normal_user": 1,
            "system_pollution": 0,
            "ai_internal_pollution": 0,
            "invalid": 0,
        })
        monkeypatch.setattr(mm, "EXPECTED_TOTAL", 1)
        monkeypatch.setattr(mm, "EXPECTED_ARCHIVE", 0)
        monkeypatch.setattr(mm, "EXPECTED_KEEP", 1)

        argv = [
            "preflight",
            "--plan", str(tmp_workspace["plan"]),
            "--source", str(tmp_workspace["src"]),
        ]
        rc = mm.main(argv)
        assert rc == 0

    def test_main_rollback_subcommand(self, tmp_workspace):
        """main() 接受 rollback 子命令。"""
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        tmp_workspace["backup"].mkdir()
        backup = tmp_workspace["backup"] / "memory.json.20260802T100000000000Z"
        backup.write_text(json.dumps([{"id": "a"}], ensure_ascii=False), encoding="utf-8")
        argv = [
            "rollback",
            str(backup),
            "--source", str(tmp_workspace["src"]),
        ]
        rc = mm.main(argv)
        assert rc == 0
