# -*- coding: utf-8 -*-
"""
tests/test_memory_baseline.py

Phase C.2.4 — Memory Baseline Generation 测试

覆盖:
1. baseline 文件生成成功
2. memory.json 不被修改
3. 数量统计正确(total / normal_user / system_pollution / ai_internal_pollution)
4. pollution 统计正确(pollution_rate = 0)
5. 类型统计正确(by_type 与 memory.json 实际类型分布一致)
6. 输出字段完整(quality_score / grade / status / sha256 / hour_buckets / snapshot)
7. 报告文件可被渲染 / 包含关键 section
8. 污染样本能正确识别为 pollution,正常样本为 normal_user

约束:
- 不修改 data/memory.json
- 使用 tmp_path 隔离工作区
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS))

import memory_baseline as mb  # noqa: E402


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """提供独立的 source / archive / backup / report 目录。"""
    src = tmp_path / "memory.json"
    archive = tmp_path / "memory_archive"
    backup = tmp_path / "memory_backup"
    report = tmp_path / "memory_baseline.md"
    log = tmp_path / "baseline_log.jsonl"

    # 重定向模块级常量
    monkeypatch.setattr(mb, "LOG_PATH", log)

    return {
        "src": src,
        "archive": archive,
        "backup": backup,
        "report": report,
        "log": log,
        "root": tmp_path,
    }


def _write_memory(path: Path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _make_normal_user(idx: int, ts: str = "2026-08-02T19:36:08.763951") -> dict:
    """构造一条干净 normal_user 记忆。"""
    return {
        "id": f"mem_{idx:05d}",
        "role": "user",
        "content": f"测试记忆 {idx}",
        "timestamp": ts,
        "metadata": {
            "memory_type": "user_shared",
            "source": "user_chat",
        },
    }


def _make_polluted_system(idx: int) -> dict:
    return {
        "id": f"sys_{idx:05d}",
        "role": "system",
        "content": f"<system_reminder>污染 {idx}</system_reminder>",
        "timestamp": "2026-08-02T19:36:08.763951",
        "metadata": {"memory_type": "system_prompt"},
    }


def _make_polluted_ai(idx: int) -> dict:
    return {
        "id": f"ai_{idx:05d}",
        "role": "assistant",
        "content": f"AI 内部思考 {idx}",
        "timestamp": "2026-08-02T19:36:08.763951",
        "metadata": {"memory_type": "ai_thought"},
    }


# ============================================================
# 1. baseline 文件生成成功
# ============================================================

class TestBaselineFileGeneration:
    def test_analyze_returns_dict(self, tmp_workspace):
        """analyze_memory 应返回结构化 dict 结果。"""
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert isinstance(result, dict)
        assert result["total"] == 3
        assert result["by_category"]["normal_user"] == 3

    def test_render_report_returns_markdown(self, tmp_workspace):
        """render_report 应生成可读 markdown。"""
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        md = mb.render_report(result)
        assert isinstance(md, str)
        assert "# Memory Baseline — Phase C.2.4" in md
        assert "## 1. Memory 当前状态" in md
        assert "## 7. Baseline Snapshot" in md

    def test_main_generates_report_file(self, tmp_workspace, capsys):
        """main() 应将报告写入指定路径。"""
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)

        rc = mb.main([
            "--source", str(tmp_workspace["src"]),
            "--archive-dir", str(tmp_workspace["archive"]),
            "--backup-dir", str(tmp_workspace["backup"]),
            "--report", str(tmp_workspace["report"]),
        ])
        assert rc == 0
        assert tmp_workspace["report"].exists()
        content = tmp_workspace["report"].read_text(encoding="utf-8")
        assert "Memory Baseline" in content
        assert "EXCELLENT" in content or "GOOD" in content

    def test_main_returns_2_when_source_missing(self, tmp_workspace):
        """source 不存在时 main 应返回 2。"""
        rc = mb.main([
            "--source", str(tmp_workspace["root"] / "missing.json"),
            "--archive-dir", str(tmp_workspace["archive"]),
            "--backup-dir", str(tmp_workspace["backup"]),
            "--report", str(tmp_workspace["report"]),
        ])
        assert rc == 2


# ============================================================
# 2. memory.json 不被修改
# ============================================================

class TestMemoryJsonUnchanged:
    def test_analyze_does_not_modify_source(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(5)]
        _write_memory(tmp_workspace["src"], items)

        before_bytes = tmp_workspace["src"].read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()
        before_size = len(before_bytes)

        mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )

        after_bytes = tmp_workspace["src"].read_bytes()
        after_hash = hashlib.sha256(after_bytes).hexdigest()

        assert before_hash == after_hash, "analyze_memory 不应修改 memory.json"
        assert len(after_bytes) == before_size

    def test_main_does_not_modify_source(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(5)]
        _write_memory(tmp_workspace["src"], items)

        before_bytes = tmp_workspace["src"].read_bytes()
        before_hash = hashlib.sha256(before_bytes).hexdigest()

        mb.main([
            "--source", str(tmp_workspace["src"]),
            "--archive-dir", str(tmp_workspace["archive"]),
            "--backup-dir", str(tmp_workspace["backup"]),
            "--report", str(tmp_workspace["report"]),
        ])

        after_bytes = tmp_workspace["src"].read_bytes()
        after_hash = hashlib.sha256(after_bytes).hexdigest()

        assert before_hash == after_hash, "main() 不应修改 memory.json"


# ============================================================
# 3. 数量统计正确
# ============================================================

class TestCountStatistics:
    def test_total_count_matches(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(10)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["total"] == 10
        assert result["by_category"]["normal_user"] == 10

    def test_mixed_categories_counted_separately(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(7)]
            + [_make_polluted_system(i) for i in range(2)]
            + [_make_polluted_ai(i) for i in range(1)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["total"] == 10
        assert result["by_category"]["normal_user"] == 7
        assert result["by_category"]["system_pollution"] == 2
        assert result["by_category"]["ai_internal_pollution"] == 1

    def test_archive_count_detected(self, tmp_workspace):
        """archive 目录里的 .json 文件应被计入 archive_count。"""
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)
        tmp_workspace["archive"].mkdir()
        (tmp_workspace["archive"] / "archive_001.json").write_text("[]")
        (tmp_workspace["archive"] / "archive_002.json").write_text("[]")

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["archive_count"] == 2
        assert "archive_001.json" in result["archive_files"]

    def test_backup_count_detected(self, tmp_workspace):
        """backup 目录里的 memory.json.* 文件应被计入 backup_count。"""
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)
        tmp_workspace["backup"].mkdir()
        (tmp_workspace["backup"] / "memory.json.001").write_text("[]")
        (tmp_workspace["backup"] / "memory.json.002").write_text("[]")
        (tmp_workspace["backup"] / "memory.json.003").write_text("[]")

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["backup_count"] == 3
        assert result["latest_backup"] is not None


# ============================================================
# 4. pollution 统计正确
# ============================================================

class TestPollutionStatistics:
    def test_zero_pollution_yields_zero_rates(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(20)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["pollution_rate"] == 0.0
        assert result["invalid_rate"] == 0.0
        assert result["schema_valid_rate"] == 100.0
        assert result["quality_score"] == 100.0
        assert result["grade"] == "A+"
        assert result["status"] == "EXCELLENT"

    def test_system_pollution_counted(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(8)]
            + [_make_polluted_system(i) for i in range(2)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        # pollution = system(2) + ai(0) = 2, total=10, rate=20%
        assert result["pollution_rate"] == pytest.approx(20.0)
        assert result["by_category"]["system_pollution"] == 2

    def test_ai_internal_pollution_counted(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(9)]
            + [_make_polluted_ai(i) for i in range(1)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["pollution_rate"] == pytest.approx(10.0)
        assert result["by_category"]["ai_internal_pollution"] == 1

    def test_critical_status_when_severely_polluted(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(1)]
            + [_make_polluted_system(i) for i in range(9)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["status"] in ("NEEDS_REVIEW", "CRITICAL")
        assert result["quality_score"] < 50

    def test_retrieval_ready_flag(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(5)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        # 关键断言:retrieval_ready = pollution==0 and invalid==0
        assert result["by_category"].get("system_pollution", 0) == 0
        assert result["by_category"].get("ai_internal_pollution", 0) == 0
        assert result["by_category"].get("invalid", 0) == 0


# ============================================================
# 5. 类型统计正确
# ============================================================

class TestTypeStatistics:
    def test_user_shared_counted(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(5)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["by_type"].get("user_shared", 0) == 5
        # 占比 100%
        assert result["by_category"]["normal_user"] == 5

    def test_type_distribution_preserved(self, tmp_workspace):
        """混合 memory_type 时,by_type 应忠实反映分布。"""
        items = []
        for i in range(3):
            m = _make_normal_user(i)
            m["metadata"]["memory_type"] = "user_fact"
            items.append(m)
        for i in range(2):
            m = _make_normal_user(10 + i)
            m["metadata"]["memory_type"] = "user_preference"
            items.append(m)
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["by_type"]["user_fact"] == 3
        assert result["by_type"]["user_preference"] == 2

    def test_pollution_types_appear_in_by_type(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(2)]
            + [_make_polluted_system(0), _make_polluted_ai(0)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["by_type"].get("system_prompt", 0) == 1
        assert result["by_type"].get("ai_thought", 0) == 1

    def test_role_distribution_preserved(self, tmp_workspace):
        items = (
            [_make_normal_user(i) for i in range(3)]
            + [_make_polluted_system(0)]
        )
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["by_role"].get("user", 0) == 3
        assert result["by_role"].get("system", 0) == 1


# ============================================================
# 6. 输出字段完整
# ============================================================

class TestOutputFieldsComplete:
    REQUIRED_FIELDS = [
        "source", "source_size_bytes", "source_size_human",
        "source_sha256", "total", "by_category", "by_type", "by_role",
        "time_earliest", "time_latest", "time_span_seconds", "hour_buckets",
        "type_mismatches", "schema_violations", "schema_violation_count",
        "archive_files", "archive_count", "backup_files", "backup_count",
        "latest_backup",
        "pollution_rate", "invalid_rate", "schema_valid_rate",
        "quality_score", "grade", "status",
        "pollution_guard", "generated_at",
    ]

    def test_all_required_fields_present(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        for field in self.REQUIRED_FIELDS:
            assert field in result, f"missing field: {field}"

    def test_pollution_guard_subfields(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        pg = result["pollution_guard"]
        assert "forbidden_type_count" in pg
        assert "forbidden_type_zero" in pg
        assert "forbidden_role_count" in pg
        assert "forbidden_role_zero" in pg
        assert "allowed_types_active" in pg
        assert "unknown_type_count" in pg

    def test_sha256_is_stable_hex(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)

        r1 = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        r2 = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert r1["source_sha256"] == r2["source_sha256"]
        assert len(r1["source_sha256"]) == 64  # sha256 hex length

    def test_quality_score_in_range(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(2)]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert 0.0 <= result["quality_score"] <= 100.0
        assert result["grade"] in ("A+", "A", "B", "C", "F")
        assert result["status"] in (
            "EXCELLENT", "GOOD", "ACCEPTABLE", "NEEDS_REVIEW", "CRITICAL"
        )

    def test_time_span_computed(self, tmp_workspace):
        items = [
            _make_normal_user(0, "2026-08-02T19:00:00.000000"),
            _make_normal_user(1, "2026-08-02T20:00:00.000000"),
            _make_normal_user(2, "2026-08-02T21:00:00.000000"),
        ]
        _write_memory(tmp_workspace["src"], items)

        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        assert result["time_earliest"] == "2026-08-02T19:00:00.000000"
        assert result["time_latest"] == "2026-08-02T21:00:00.000000"
        assert result["time_span_seconds"] == pytest.approx(7200.0, abs=1.0)
        assert len(result["hour_buckets"]) == 3


# ============================================================
# 7. 报告渲染完整性
# ============================================================

class TestReportRender:
    def test_report_contains_all_sections(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        md = mb.render_report(result)
        for section in [
            "## 1. Memory 当前状态",
            "## 2. Memory 类型分析",
            "## 3. 时间跨度分析",
            "## 4. Memory Quality 评分",
            "## 5. PollutionGuard 状态确认",
            "## 6. Memory → Growth → SelfModel 链路状态",
            "## 7. Baseline Snapshot",
        ]:
            assert section in md, f"missing section: {section}"

    def test_report_snapshot_contains_required_fields(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        md = mb.render_report(result)
        for key in [
            "baseline_date", "memory_count", "quality_score",
            "pollution_status", "growth_connection_status",
        ]:
            assert key in md, f"missing snapshot key: {key}"

    def test_report_shows_pollution_status(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        md = mb.render_report(result)
        assert "CLEAN" in md or "POLLUTED" in md

    def test_report_marks_health_status(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        md = mb.render_report(result)
        assert "HEALTHY" in md or "NEEDS_REVIEW" in md or "CRITICAL" in md


# ============================================================
# 8. PollutionGuard 防护状态验证(报告级)
# ============================================================

class TestPollutionGuardReport:
    def test_forbidden_type_zero_reported(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        pg = result["pollution_guard"]
        assert pg["forbidden_type_zero"] is True
        assert pg["forbidden_type_count"] == 0

    def test_forbidden_role_zero_reported(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        pg = result["pollution_guard"]
        assert pg["forbidden_role_zero"] is True
        assert pg["forbidden_role_count"] == 0

    def test_allowed_types_active(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        pg = result["pollution_guard"]
        assert pg["allowed_types_active"] is True

    def test_no_unknown_types(self, tmp_workspace):
        items = [_make_normal_user(i) for i in range(3)]
        _write_memory(tmp_workspace["src"], items)
        result = mb.analyze_memory(
            tmp_workspace["src"],
            tmp_workspace["archive"],
            tmp_workspace["backup"],
        )
        pg = result["pollution_guard"]
        assert pg["unknown_type_count"] == 0
        assert result["type_mismatches"] == []


# ============================================================
# 9. 分类器单元测试(辅助,验证 _classify 行为)
# ============================================================

class TestClassifier:
    def test_user_fact_classified_normal(self):
        m = {
            "id": "x", "role": "user", "content": "hi",
            "metadata": {"memory_type": "user_fact"},
        }
        cat, _ = mb._classify(m)
        assert cat == "normal_user"

    def test_user_preference_classified_normal(self):
        m = {
            "id": "x", "role": "user", "content": "hi",
            "metadata": {"memory_type": "user_preference"},
        }
        cat, _ = mb._classify(m)
        assert cat == "normal_user"

    def test_system_prompt_classified_system_pollution(self):
        m = {
            "id": "x", "role": "system", "content": "sys",
            "metadata": {"memory_type": "system_prompt"},
        }
        cat, _ = mb._classify(m)
        assert cat == "system_pollution"

    def test_ai_thought_classified_ai_internal(self):
        m = {
            "id": "x", "role": "assistant", "content": "think",
            "metadata": {"memory_type": "ai_thought"},
        }
        cat, _ = mb._classify(m)
        assert cat == "ai_internal_pollution"

    def test_runtime_experience_blocked(self):
        m = {
            "id": "x", "role": "assistant", "content": "exp",
            "metadata": {"memory_type": "runtime_experience"},
        }
        cat, _ = mb._classify(m)
        assert cat == "ai_internal_pollution"

    def test_system_role_classified_system_pollution(self):
        """role=system + type=未知 → system_pollution(按 role 推断)。"""
        m = {
            "id": "x", "role": "system", "content": "hi",
            "metadata": {"memory_type": "mystery_type"},
        }
        cat, _ = mb._classify(m)
        assert cat == "system_pollution"

    def test_assistant_role_classified_ai_internal(self):
        """role=assistant + type=未知 → ai_internal_pollution(按 role 推断)。"""
        m = {
            "id": "x", "role": "assistant", "content": "hi",
            "metadata": {"memory_type": "mystery_type"},
        }
        cat, _ = mb._classify(m)
        assert cat == "ai_internal_pollution"

    def test_human_role_classified_normal(self):
        """role=human + type=未知 → normal_user。"""
        m = {
            "id": "x", "role": "human", "content": "hi",
            "metadata": {"memory_type": "mystery_type"},
        }
        cat, _ = mb._classify(m)
        assert cat == "normal_user"

    def test_empty_type_with_role_user_classified_invalid(self):
        """无 type(role=user, type="") → invalid(type 在 _INVALID_TYPES 中)。"""
        m = {"id": "x", "role": "user", "content": "hi", "metadata": {}}
        cat, _ = mb._classify(m)
        assert cat == "invalid"

    def test_unknown_type_with_user_role_classified_normal(self):
        """type=未知字符串 且 role=user → normal_user(role 推断)。"""
        m = {
            "id": "x", "role": "user", "content": "hi",
            "metadata": {"memory_type": "totally_unknown_xyz"},
        }
        cat, _ = mb._classify(m)
        assert cat == "normal_user"

    def test_unknown_type_with_assistant_role_classified_ai(self):
        """type=未知字符串 且 role=assistant → ai_internal_pollution。"""
        m = {
            "id": "x", "role": "assistant", "content": "hi",
            "metadata": {"memory_type": "totally_unknown_xyz"},
        }
        cat, _ = mb._classify(m)
        assert cat == "ai_internal_pollution"

    def test_explicit_user_type_classified_normal(self):
        """显式 type=user_fact → normal_user。"""
        m = {
            "id": "x", "role": "user", "content": "hi",
            "metadata": {"memory_type": "user_fact"},
        }
        cat, _ = mb._classify(m)
        assert cat == "normal_user"
