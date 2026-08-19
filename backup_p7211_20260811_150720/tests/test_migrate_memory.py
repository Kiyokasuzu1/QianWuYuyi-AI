# -*- coding: utf-8 -*-
"""
tests/test_migrate_memory.py

Phase C.1 — P1-3: 验证 scripts/migrate_memory.py 的安全迁移逻辑。

覆盖:
- 加载 plan / 解析 quarantine id
- 划分 normal / archive
- dry-run 不修改文件
- backup / archive / rewrite 顺序
- rollback 恢复
- 空 / 缺失 / 异常输入不破坏数据
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS))

import migrate_memory as mm  # noqa: E402


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    """提供独立的 memory.json / plan / archive / backup 目录。"""
    src = tmp_path / "memory.json"
    archive = tmp_path / "memory_archive"
    backup = tmp_path / "memory_backup"
    plan = tmp_path / "plan.json"
    log = tmp_path / "migration_log.jsonl"

    # 重定向模块级常量
    monkeypatch.setattr(mm, "DEFAULT_ARCHIVE_DIR", archive)
    monkeypatch.setattr(mm, "DEFAULT_BACKUP_DIR", backup)
    monkeypatch.setattr(mm, "LOG_PATH", log)

    return {
        "src": src,
        "archive": archive,
        "backup": backup,
        "plan": plan,
        "log": log,
        "root": tmp_path,
    }


def _write_memory(path: Path, items):
    path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _write_plan(path: Path, quarantine: dict, summary: dict | None = None):
    payload = {
        "phase": "C.1 P1-3",
        "generated_at": "2026-08-02T10:00:00Z",
        "source": "test",
        "total": sum(len(v) for v in quarantine.values()),
        "summary": summary or {
            "normal_user": 0,
            "system_pollution": len(quarantine.get("system_pollution", [])),
            "ai_internal_pollution": len(quarantine.get("ai_internal_pollution", [])),
            "invalid": len(quarantine.get("invalid", [])),
        },
        "phases": [],
        "quarantine_ids": quarantine,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


# ============================================================
# 1. 基础解析
# ============================================================

class TestCollectQuarantineIds:
    def test_collects_all_three_categories(self):
        plan = _write_plan(
            Path("/tmp/plan_test.json") if False else PROJECT_ROOT / ".cache" / "audit" / "plan_unit_test.json",
            quarantine={
                "system_pollution": ["m1", "m2"],
                "ai_internal_pollution": ["m3"],
                "invalid": ["m4", "m5", "m6"],
            },
        )
        result = mm._collect_quarantine_ids(plan)
        assert result["system_pollution"] == ["m1", "m2"]
        assert result["ai_internal_pollution"] == ["m3"]
        assert result["invalid"] == ["m4", "m5", "m6"]

    def test_handles_missing_categories(self):
        plan = _write_plan(
            PROJECT_ROOT / ".cache" / "audit" / "plan_unit_test2.json",
            quarantine={"system_pollution": ["m1"]},
        )
        result = mm._collect_quarantine_ids(plan)
        assert result["system_pollution"] == ["m1"]
        assert result["ai_internal_pollution"] == []
        assert result["invalid"] == []


class TestPartitionMemories:
    def test_partitions_by_id(self):
        memories = [
            {"id": "a", "content": "normal-1"},
            {"id": "b", "content": "normal-2"},
            {"id": "c", "content": "sys"},
            {"id": "d", "content": "ai"},
            {"id": "e", "content": "invalid"},
        ]
        q = {
            "system_pollution": ["c"],
            "ai_internal_pollution": ["d"],
            "invalid": ["e"],
        }
        normal, archive = mm._partition_memories(memories, q)
        assert len(normal) == 2
        assert {m["id"] for m in normal} == {"a", "b"}
        assert len(archive["system_pollution"]) == 1
        assert archive["system_pollution"][0]["id"] == "c"
        assert len(archive["ai_internal_pollution"]) == 1
        assert archive["ai_internal_pollution"][0]["id"] == "d"
        assert len(archive["invalid"]) == 1
        assert archive["invalid"][0]["id"] == "e"

    def test_records_without_id_kept_as_normal(self):
        memories = [{"id": "", "content": "no-id"}]
        q = {"system_pollution": ["x"], "ai_internal_pollution": [], "invalid": []}
        normal, archive = mm._partition_memories(memories, q)
        assert len(normal) == 1
        assert all(len(v) == 0 for v in archive.values())


# ============================================================
# 2. Backup / Archive / Rewrite
# ============================================================

class TestBackup:
    def test_creates_backup_file(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        bp = mm._do_backup(tmp_workspace["src"], tmp_workspace["backup"])
        assert bp.exists()
        assert bp.read_text(encoding="utf-8") == tmp_workspace["src"].read_text(encoding="utf-8")
        # 命名格式 memory.json.YYYYMMDDTHHMMSSZ
        assert bp.name.startswith("memory.json.")
        assert "T" in bp.name and bp.name.endswith("Z")


class TestArchive:
    def test_writes_archive_with_summary(self, tmp_workspace):
        archive_payload = {
            "system_pollution": [{"id": "a", "content": "sys"}],
            "ai_internal_pollution": [{"id": "b"}, {"id": "c"}],
            "invalid": [],
        }
        ap = mm._do_archive(tmp_workspace["archive"], archive_payload)
        assert ap.exists()
        data = json.loads(ap.read_text(encoding="utf-8"))
        assert data["summary"]["system_pollution"] == 1
        assert data["summary"]["ai_internal_pollution"] == 2
        assert data["summary"]["invalid"] == 0
        assert data["summary"]["total"] == 3
        assert data["data"]["system_pollution"][0]["id"] == "a"


class TestRewrite:
    def test_rewrites_memory_with_atomic_write(self, tmp_workspace):
        normal = [{"id": "a"}, {"id": "b"}]
        mm._do_rewrite(tmp_workspace["src"], normal)
        assert tmp_workspace["src"].exists()
        data = json.loads(tmp_workspace["src"].read_text(encoding="utf-8"))
        assert data == normal

    def test_rewrite_creates_no_temp_file_on_success(self, tmp_workspace):
        mm._do_rewrite(tmp_workspace["src"], [{"id": "a"}])
        assert not (tmp_workspace["src"].with_suffix(".tmp")).exists()


# ============================================================
# 3. cmd_run dry-run 不修改
# ============================================================

class TestDryRun:
    def test_dry_run_does_not_modify_files(self, tmp_workspace, capsys):
        _write_memory(tmp_workspace["src"], [
            {"id": "a", "content": "normal"},
            {"id": "b", "content": "system-x", "metadata": {"memory_type": "system"}},
        ])
        _write_plan(tmp_workspace["plan"], quarantine={"system_pollution": ["b"], "ai_internal_pollution": [], "invalid": []})

        before = tmp_workspace["src"].read_text(encoding="utf-8")
        rc = mm.cmd_run(tmp_workspace["plan"], tmp_workspace["src"], execute=False)
        after = tmp_workspace["src"].read_text(encoding="utf-8")

        assert rc == 0
        assert before == after  # 源文件未变
        assert not tmp_workspace["backup"].exists()  # 无备份
        assert not tmp_workspace["archive"].exists()  # 无归档

        captured = capsys.readouterr()
        assert "DRY-RUN" in captured.out


# ============================================================
# 4. cmd_rollback
# ============================================================

class TestRollback:
    def test_rollback_restores_memory(self, tmp_workspace):
        # 准备:备份当前 src
        original = [{"id": "a"}, {"id": "b"}]
        _write_memory(tmp_workspace["src"], original)
        backup_path = mm._do_backup(tmp_workspace["src"], tmp_workspace["backup"])

        # 修改 src
        _write_memory(tmp_workspace["src"], [{"id": "modified"}])

        # 回滚
        rc = mm.cmd_rollback(backup_path, tmp_workspace["src"])
        assert rc == 0
        data = json.loads(tmp_workspace["src"].read_text(encoding="utf-8"))
        assert data == original

    def test_rollback_missing_file_returns_1(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [{"id": "a"}])
        rc = mm.cmd_rollback(tmp_workspace["backup"] / "non_existent.json", tmp_workspace["src"])
        assert rc == 1


# ============================================================
# 5. 边界 / 异常
# ============================================================

class TestEdgeCases:
    def test_load_plan_missing_file(self):
        with pytest.raises(FileNotFoundError):
            mm._load_plan(PROJECT_ROOT / "non_existent_plan_xyz.json")

    def test_load_memory_missing_file(self, tmp_workspace):
        with pytest.raises(FileNotFoundError):
            mm._load_memory(tmp_workspace["src"])

    def test_load_memory_not_a_list(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], {"not": "a list"})
        with pytest.raises(ValueError):
            mm._load_memory(tmp_workspace["src"])

    def test_empty_memory(self, tmp_workspace):
        _write_memory(tmp_workspace["src"], [])
        memories = mm._load_memory(tmp_workspace["src"])
        normal, archive = mm._partition_memories(memories, {"system_pollution": [], "ai_internal_pollution": [], "invalid": []})
        assert normal == []
        assert all(len(v) == 0 for v in archive.values())


# ============================================================
# 6. 与 audit_memory.py 一致性
# ============================================================

class TestConsistencyWithAudit:
    """验证 migrate_memory 的分类规则与 audit_memory 一致。"""

    def test_runtime_experience_classified_as_ai_internal(self):
        # 模拟 audit_memory 的分类结果
        from scripts.audit_memory import _classify
        m = {"id": "x", "role": "system", "metadata": {"memory_type": "runtime_experience"}, "content": "[RuntimeExperience] ..."}
        cat, _ = _classify(m)
        assert cat == "ai_internal_pollution"

    def test_system_reminder_classified_as_system_pollution(self):
        from scripts.audit_memory import _classify
        m = {"id": "x", "role": "system", "metadata": {"memory_type": "system_reminder"}, "content": "reminder"}
        cat, _ = _classify(m)
        assert cat == "system_pollution"

    def test_user_fact_classified_as_normal(self):
        from scripts.audit_memory import _classify
        m = {"id": "x", "role": "user", "metadata": {"memory_type": "user_fact"}, "content": "I like cats"}
        cat, _ = _classify(m)
        assert cat == "normal_user"
