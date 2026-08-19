# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_runtime_monitor.py

Phase C.4.6.2 — SelfModel Activation Monitor 测试

覆盖:
- empty 目录
- 初始化状态
- active 状态
- stable 状态
- 文件损坏处理
- 空 jsonl 处理
- 一致性报告读取
- 告警检测
- 每日新增计算
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """创建临时 data/self_model/ 目录。"""
    data_dir = tmp_path / "data" / "self_model"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def tmp_report(tmp_path: Path) -> Path:
    """创建临时报告路径。"""
    report = tmp_path / "docs" / "audit" / "selfmodel_runtime_daily.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    return report


@pytest.fixture
def tmp_consistency_report(tmp_path: Path) -> Path:
    """创建临时一致性报告路径。"""
    cr = tmp_path / "docs" / "audit" / "selfmodel_consistency_report.md"
    cr.parent.mkdir(parents=True, exist_ok=True)
    return cr


# ============================================================
# 测试 1: 文件统计
# ============================================================
class TestFileStats:
    def test_missing_file_returns_empty_stats(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import _file_stats

        stats = _file_stats(tmp_data_dir / "beliefs.jsonl")
        assert stats["exists"] is False
        assert stats["size_bytes"] == 0
        assert stats["line_count"] == 0
        assert stats["record_count"] == 0
        assert stats["valid"] is True

    def test_existing_empty_file(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import _file_stats

        path = tmp_data_dir / "beliefs.jsonl"
        path.write_text("", encoding="utf-8")
        stats = _file_stats(path)
        assert stats["exists"] is True
        assert stats["size_bytes"] == 0
        assert stats["line_count"] == 0
        assert stats["record_count"] == 0
        assert stats["valid"] is True

    def test_existing_file_with_records(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import _file_stats

        path = tmp_data_dir / "beliefs.jsonl"
        records = [
            {"belief_id": f"b_{i}", "trait": "温柔", "value": 0.9}
            for i in range(5)
        ]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        stats = _file_stats(path)
        assert stats["exists"] is True
        assert stats["line_count"] == 5
        assert stats["record_count"] == 5
        assert stats["valid"] is True

    def test_corrupted_file_detected(self, tmp_data_dir: Path):
        """文件损坏:存在无法解析的行。"""
        from scripts.selfmodel_runtime_monitor import _file_stats

        path = tmp_data_dir / "beliefs.jsonl"
        path.write_text(
            '{"belief_id": "b_1"}\nINVALID_JSON_HERE\n{"belief_id": "b_2"}\n',
            encoding="utf-8",
        )
        stats = _file_stats(path)
        assert stats["exists"] is True
        assert stats["line_count"] == 3
        assert stats["record_count"] == 2  # INVALID 被跳过
        assert stats["valid"] is False  # 损坏检测


# ============================================================
# 测试 2: 一致性报告读取
# ============================================================
class TestReadConsistencyStatus:
    def test_consistency_report_not_exists(self, tmp_consistency_report: Path):
        from scripts.selfmodel_runtime_monitor import read_consistency_status

        # 不创建文件
        status = read_consistency_status(tmp_consistency_report)
        assert status["report_exists"] is False
        assert status["status"] == "NOT_RUN"
        assert status["issue_count"] == 0

    def test_consistency_report_consistent(self, tmp_consistency_report: Path):
        from scripts.selfmodel_runtime_monitor import read_consistency_status

        tmp_consistency_report.write_text(
            "# SelfModel Consistency Report — Phase C.3.4\n\n"
            "> **生成时间:** `2026-08-02T12:00:00Z`  \n"
            "> **整体状态:** **CONSISTENT**  \n\n"
            "```\nissue_count: 0\n```\n",
            encoding="utf-8",
        )
        status = read_consistency_report_status(tmp_consistency_report)
        assert status["report_exists"] is True
        assert status["status"] == "CONSISTENT"
        assert status["issue_count"] == 0
        assert status["last_run"] is not None

    def test_consistency_report_issues_detected(self, tmp_consistency_report: Path):
        from scripts.selfmodel_runtime_monitor import read_consistency_status

        tmp_consistency_report.write_text(
            "# SelfModel Consistency Report — Phase C.3.4\n\n"
            "> **生成时间:** `2026-08-02T12:00:00Z`  \n"
            "> **整体状态:** **ISSUES_DETECTED**  \n\n"
            "- issue_count: 3\n",
            encoding="utf-8",
        )
        status = read_consistency_report_status(tmp_consistency_report)
        assert status["report_exists"] is True
        assert status["status"] == "ISSUES_DETECTED"
        assert status["issue_count"] == 3


def read_consistency_report_status(path: Path):
    """Helper to avoid import in test bodies."""
    from scripts.selfmodel_runtime_monitor import read_consistency_status
    return read_consistency_status(path)


# ============================================================
# 测试 3: 状态判定
# ============================================================
class TestDetermineStatus:
    def test_empty_when_no_files(self):
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_EMPTY,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": False, "record_count": 0},
            "history.jsonl": {"exists": False, "record_count": 0},
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        consistency = {"status": "NOT_RUN"}
        assert determine_status(file_stats, consistency) == STATUS_EMPTY

    def test_empty_takes_precedence_over_consistency(self):
        """即使一致性报告存在,数据为空时仍为 EMPTY。"""
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_EMPTY,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": False, "record_count": 0},
            "history.jsonl": {"exists": False, "record_count": 0},
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        consistency = {"status": "CONSISTENT"}  # 即使如此
        assert determine_status(file_stats, consistency) == STATUS_EMPTY

    def test_initializing_when_core_files_below_threshold(self):
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_INITIALIZING,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 3},
            "history.jsonl": {"exists": True, "record_count": 5},
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        consistency = {"status": "NOT_RUN"}
        assert determine_status(file_stats, consistency) == STATUS_INITIALIZING

    def test_active_when_beliefs_above_threshold(self):
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_ACTIVE,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 11},  # > 10
            "history.jsonl": {"exists": True, "record_count": 5},
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        consistency = {"status": "NOT_RUN"}
        assert determine_status(file_stats, consistency) == STATUS_ACTIVE

    def test_active_when_history_above_threshold(self):
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_ACTIVE,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 5},
            "history.jsonl": {"exists": True, "record_count": 15},  # > 10
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        consistency = {"status": "ISSUES_DETECTED"}
        assert determine_status(file_stats, consistency) == STATUS_ACTIVE

    def test_stable_when_consistency_consistent(self):
        from scripts.selfmodel_runtime_monitor import (
            determine_status,
            STATUS_STABLE,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 5},
            "history.jsonl": {"exists": True, "record_count": 5},
            "reflection.jsonl": {"exists": False, "record_count": 0},
            "relationship.jsonl": {"exists": False, "record_count": 0},
        }
        # 即使行数 < 10,只要一致性检查通过,即为 STABLE
        consistency = {"status": "CONSISTENT"}
        assert determine_status(file_stats, consistency) == STATUS_STABLE


# ============================================================
# 测试 4: 一致性触发
# ============================================================
class TestConsistencyTrigger:
    def test_no_trigger_when_below_threshold(self):
        from scripts.selfmodel_runtime_monitor import check_consistency_trigger

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 5},
            "history.jsonl": {"exists": True, "record_count": 8},
        }
        trigger = check_consistency_trigger(file_stats)
        assert trigger["triggered"] is False
        assert trigger["beliefs_count"] == 5
        assert trigger["history_count"] == 8

    def test_trigger_when_beliefs_above_threshold(self):
        from scripts.selfmodel_runtime_monitor import check_consistency_trigger

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 11},
            "history.jsonl": {"exists": True, "record_count": 5},
        }
        trigger = check_consistency_trigger(file_stats)
        assert trigger["triggered"] is True
        assert "beliefs=11" in trigger["reason"]

    def test_trigger_when_history_above_threshold(self):
        from scripts.selfmodel_runtime_monitor import check_consistency_trigger

        file_stats = {
            "beliefs.jsonl": {"exists": True, "record_count": 5},
            "history.jsonl": {"exists": True, "record_count": 12},
        }
        trigger = check_consistency_trigger(file_stats)
        assert trigger["triggered"] is True
        assert "history=12" in trigger["reason"]


# ============================================================
# 测试 5: 每日新增
# ============================================================
class TestDailyIncrement:
    def test_first_run_no_increment(self):
        """首次运行(无历史 log)时,所有增量为 0。"""
        from scripts.selfmodel_runtime_monitor import calc_daily_increment

        current = {
            "beliefs.jsonl": {"record_count": 5, "size_bytes": 100},
        }
        increment = calc_daily_increment(current, None)
        assert increment["beliefs.jsonl"]["record_delta"] == 0
        assert increment["beliefs.jsonl"]["size_delta"] == 0

    def test_increment_calculated_correctly(self):
        from scripts.selfmodel_runtime_monitor import calc_daily_increment

        current = {
            "beliefs.jsonl": {"record_count": 10, "size_bytes": 200},
            "history.jsonl": {"record_count": 5, "size_bytes": 100},
        }
        last = {
            "file_stats": {
                "beliefs.jsonl": {"record_count": 7, "size_bytes": 140},
                "history.jsonl": {"record_count": 5, "size_bytes": 100},
            }
        }
        increment = calc_daily_increment(current, last)
        assert increment["beliefs.jsonl"]["record_delta"] == 3
        assert increment["beliefs.jsonl"]["size_delta"] == 60
        assert increment["history.jsonl"]["record_delta"] == 0


# ============================================================
# 测试 6: 告警
# ============================================================
class TestDetectWarnings:
    def test_no_warnings_for_clean_state(self):
        from scripts.selfmodel_runtime_monitor import (
            detect_warnings,
            STATUS_EMPTY,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": False, "valid": True, "record_count": 0},
        }
        increment = {"beliefs.jsonl": {"record_delta": 0, "size_delta": 0}}
        consistency = {"status": "NOT_RUN"}
        warnings = detect_warnings(file_stats, increment, consistency, STATUS_EMPTY)
        assert warnings == []

    def test_corruption_warning(self):
        from scripts.selfmodel_runtime_monitor import detect_warnings

        file_stats = {
            "beliefs.jsonl": {"exists": True, "valid": False, "record_count": 2},
        }
        increment = {"beliefs.jsonl": {"record_delta": 0, "size_delta": 0}}
        consistency = {"status": "NOT_RUN"}
        warnings = detect_warnings(file_stats, increment, consistency, "INITIALIZING")
        assert len(warnings) >= 1
        assert any("file_corruption" in w["indicator"] for w in warnings)

    def test_active_consistency_pending_info(self):
        from scripts.selfmodel_runtime_monitor import (
            detect_warnings,
            STATUS_ACTIVE,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "valid": True, "record_count": 12},
        }
        increment = {"beliefs.jsonl": {"record_delta": 0, "size_delta": 0}}
        consistency = {"status": "NOT_RUN"}
        warnings = detect_warnings(file_stats, increment, consistency, STATUS_ACTIVE)
        assert any("consistency_check_pending" in w["indicator"] for w in warnings)

    def test_active_with_issues_warning(self):
        from scripts.selfmodel_runtime_monitor import (
            detect_warnings,
            STATUS_ACTIVE,
        )

        file_stats = {
            "beliefs.jsonl": {"exists": True, "valid": True, "record_count": 12},
        }
        increment = {"beliefs.jsonl": {"record_delta": 0, "size_delta": 0}}
        consistency = {"status": "ISSUES_DETECTED", "issue_count": 3}
        warnings = detect_warnings(file_stats, increment, consistency, STATUS_ACTIVE)
        assert any("consistency_issues" in w["indicator"] for w in warnings)


# ============================================================
# 测试 7: 报告渲染
# ============================================================
class TestReportRender:
    def test_report_contains_status(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import render_report

        file_stats = {
            "beliefs.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "history.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "reflection.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "relationship.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
        }
        trigger = {
            "triggered": False,
            "beliefs_count": 0,
            "history_count": 0,
            "threshold": 10,
            "reason": "尚未达到触发阈值",
        }
        consistency = {"report_exists": False, "status": "NOT_RUN", "issue_count": 0}
        increment = {fname: {"record_delta": 0, "size_delta": 0} for fname in file_stats}
        warnings = []
        report = render_report(tmp_data_dir, file_stats, increment, "EMPTY", trigger, consistency, warnings)
        assert "EMPTY" in report
        assert "NOT_RUN" in report
        assert "尚未达到触发阈值" in report
        assert "未预填" in report

    def test_report_contains_active_trigger(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import render_report

        file_stats = {
            "beliefs.jsonl": {"exists": True, "size_bytes": 1024, "line_count": 12, "record_count": 12, "valid": True},
            "history.jsonl": {"exists": True, "size_bytes": 512, "line_count": 5, "record_count": 5, "valid": True},
            "reflection.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "relationship.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
        }
        trigger = {
            "triggered": True,
            "beliefs_count": 12,
            "history_count": 5,
            "threshold": 10,
            "reason": "beliefs=12 > 10",
        }
        consistency = {"report_exists": False, "status": "NOT_RUN", "issue_count": 0}
        increment = {fname: {"record_delta": 0, "size_delta": 0} for fname in file_stats}
        warnings = []
        report = render_report(tmp_data_dir, file_stats, increment, "ACTIVE", trigger, consistency, warnings)
        assert "ACTIVE" in report
        assert "建议立即执行一致性检查" in report

    def test_report_contains_stable_marker(self, tmp_data_dir: Path):
        from scripts.selfmodel_runtime_monitor import render_report

        file_stats = {
            "beliefs.jsonl": {"exists": True, "size_bytes": 1024, "line_count": 12, "record_count": 12, "valid": True},
            "history.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "reflection.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
            "relationship.jsonl": {"exists": False, "size_bytes": 0, "line_count": 0, "record_count": 0, "valid": True},
        }
        trigger = {
            "triggered": True,
            "beliefs_count": 12,
            "history_count": 0,
            "threshold": 10,
            "reason": "beliefs=12 > 10",
        }
        consistency = {"report_exists": True, "status": "CONSISTENT", "issue_count": 0, "last_run": "2026-08-02T12:00:00Z"}
        increment = {fname: {"record_delta": 0, "size_delta": 0} for fname in file_stats}
        warnings = []
        report = render_report(tmp_data_dir, file_stats, increment, "STABLE", trigger, consistency, warnings)
        assert "STABLE" in report
        assert "进入 STABLE 状态" in report


# ============================================================
# 测试 8: 主流程 run_monitor
# ============================================================
class TestRunMonitor:
    def test_run_monitor_empty(
        self, tmp_data_dir: Path, tmp_report: Path, tmp_consistency_report: Path
    ):
        from scripts.selfmodel_runtime_monitor import run_monitor

        summary = run_monitor(tmp_data_dir, tmp_report, tmp_consistency_report)
        assert summary["status"] == "EMPTY"
        assert tmp_report.exists()
        content = tmp_report.read_text(encoding="utf-8")
        assert "EMPTY" in content

    def test_run_monitor_active(
        self, tmp_data_dir: Path, tmp_report: Path, tmp_consistency_report: Path
    ):
        from scripts.selfmodel_runtime_monitor import run_monitor

        path = tmp_data_dir / "beliefs.jsonl"
        records = [{"belief_id": f"b_{i}", "trait": "温柔"} for i in range(12)]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )

        summary = run_monitor(tmp_data_dir, tmp_report, tmp_consistency_report)
        assert summary["status"] == "ACTIVE"
        assert summary["trigger"]["triggered"] is True

    def test_run_monitor_stable_via_consistency_report(
        self, tmp_data_dir: Path, tmp_report: Path, tmp_consistency_report: Path
    ):
        """STABLE 状态从一致性报告判定,不需要 CLI 标志。"""
        from scripts.selfmodel_runtime_monitor import run_monitor

        # 写一些 beliefs(< 10 也可,因为 consistency 已通过)
        path = tmp_data_dir / "beliefs.jsonl"
        records = [{"belief_id": f"b_{i}", "trait": "温柔"} for i in range(5)]
        path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
            encoding="utf-8",
        )
        # 写入 CONSISTENT 报告
        tmp_consistency_report.write_text(
            "# SelfModel Consistency Report\n\n"
            "> **生成时间:** `2026-08-02T12:00:00Z`  \n"
            "> **整体状态:** **CONSISTENT**  \n\n"
            "issue_count: 0\n",
            encoding="utf-8",
        )

        summary = run_monitor(tmp_data_dir, tmp_report, tmp_consistency_report)
        assert summary["status"] == "STABLE"
        assert summary["consistency"]["status"] == "CONSISTENT"

    def test_run_monitor_does_not_modify_data(
        self, tmp_data_dir: Path, tmp_report: Path, tmp_consistency_report: Path
    ):
        """验证 run_monitor 不修改 SelfModel 数据(只读性)。"""
        from scripts.selfmodel_runtime_monitor import run_monitor

        path = tmp_data_dir / "beliefs.jsonl"
        original_content = '{"belief_id": "b_1", "trait": "温柔"}\n'
        path.write_text(original_content, encoding="utf-8")
        original_size = path.stat().st_size

        run_monitor(tmp_data_dir, tmp_report, tmp_consistency_report)

        # 数据文件应保持不变
        assert path.read_text(encoding="utf-8") == original_content
        assert path.stat().st_size == original_size

    def test_run_monitor_corruption_detected(
        self, tmp_data_dir: Path, tmp_report: Path, tmp_consistency_report: Path
    ):
        """文件损坏时,run_monitor 应能正常生成报告并加入告警。"""
        from scripts.selfmodel_runtime_monitor import run_monitor

        path = tmp_data_dir / "beliefs.jsonl"
        path.write_text(
            '{"belief_id": "b_1"}\nINVALID\n{"belief_id": "b_2"}\n',
            encoding="utf-8",
        )

        summary = run_monitor(tmp_data_dir, tmp_report, tmp_consistency_report)
        assert summary["status"] == "INITIALIZING"
        # 损坏告警
        assert summary["warnings_count"] >= 1
        assert summary["file_stats"]["beliefs.jsonl"]["valid"] is False


# ============================================================
# 测试 9: 边界
# ============================================================
class TestBoundaries:
    def test_data_dir_not_exists(
        self, tmp_path: Path, tmp_consistency_report: Path
    ):
        """数据目录不存在时,应仍能生成报告(状态 = EMPTY)。"""
        from scripts.selfmodel_runtime_monitor import run_monitor

        nonexistent = tmp_path / "nonexistent"
        report = tmp_path / "report.md"
        summary = run_monitor(nonexistent, report, tmp_consistency_report)
        assert summary["status"] == "EMPTY"
        assert report.exists()

    def test_4_files_monitored(self):
        from scripts.selfmodel_runtime_monitor import SELF_MODEL_FILES

        assert set(SELF_MODEL_FILES) == {
            "beliefs.jsonl",
            "history.jsonl",
            "reflection.jsonl",
            "relationship.jsonl",
        }

    def test_4_states_defined(self):
        from scripts.selfmodel_runtime_monitor import (
            STATUS_EMPTY,
            STATUS_INITIALIZING,
            STATUS_ACTIVE,
            STATUS_STABLE,
        )

        assert STATUS_EMPTY == "EMPTY"
        assert STATUS_INITIALIZING == "INITIALIZING"
        assert STATUS_ACTIVE == "ACTIVE"
        assert STATUS_STABLE == "STABLE"

    def test_threshold_is_10(self):
        from scripts.selfmodel_runtime_monitor import CONSISTENCY_TRIGGER_THRESHOLD

        assert CONSISTENCY_TRIGGER_THRESHOLD == 10

    def test_core_files_correct(self):
        from scripts.selfmodel_runtime_monitor import CORE_FILES

        assert CORE_FILES == {"beliefs.jsonl", "history.jsonl"}
