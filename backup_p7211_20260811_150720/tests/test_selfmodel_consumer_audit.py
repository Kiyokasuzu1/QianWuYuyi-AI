# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_consumer_audit.py

Phase 3.5.3 Step 2: SelfModel Consumer 审计层测试

覆盖：
- T1: 空 audit 文件
- T2: 写入成功记录
- T3: 查询已处理 proposal
- T4: 重复 proposal 检测
- T5: 损坏 JSONL 容错
- T6: 无 Runtime/Growth/Personality 依赖

约束：
- 不修改 src/runtime/* / src/orchestrator.py / src/growth/* / src/personality/*
- 不修改 selfmodel_consumer.py 主逻辑
"""

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 反依赖基线
# ============================================================

FORBIDDEN_RUNTIME = {
    "src.runtime.runtime_core",
    "src.runtime.runtime_bridge",
    "src.orchestrator",
}


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def temp_audit_dir():
    """独立临时目录作为 audit 存储。"""
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_5_3_audit_test_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def temp_consumer_dir():
    tmp = Path(tempfile.mkdtemp(prefix="phase_3_5_3_consumer_test_"))
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def valid_proposal_a():
    return {
        "id": "audit_a_001",
        "proposed_changes": [
            {"path": "personality.traits.curiosity", "before": 0.5, "after": 0.55},
        ],
        "confidence": 0.85,
        "evidence_ids": ["e1"],
        "evaluator_meta": {"reason_summary": "audit_test_a"},
    }


@pytest.fixture
def valid_proposal_b():
    return {
        "proposal_id": "audit_b_001",
        "affected_dimensions": {"warmth": 0.03},
        "confidence": 0.78,
        "evidence": ["inter_x"],
        "reason": "audit_test_b",
    }


# ============================================================
# T1: 空 audit 文件
# ============================================================

class TestEmptyAudit:
    """audit 文件不存在 / 刚创建时，统计应全 0。"""

    def test_empty_audit_returns_zero_stats(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            stats = audit.get_stats()
            assert stats["total_processed"] == 0
            assert stats["success_count"] == 0
            assert stats["failed_count"] == 0
            assert stats["dedup_skipped_count"] == 0
            assert stats["last_processed_time"] is None
            assert stats["first_processed_time"] is None
            assert stats["unique_proposal_ids"] == 0
            assert stats["audit_file_exists"] is False
            assert stats["audit_file_size_bytes"] == 0
        finally:
            audit.close()

    def test_has_processed_false_for_empty_audit(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            assert audit.has_processed("any_id") is False
            assert audit.has_succeeded("any_id") is False
        finally:
            audit.close()

    def test_audit_file_created_on_demand(self, temp_audit_dir):
        """第一次 record 后 audit 文件应被创建。"""
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            assert not audit.audit_path.exists()
            audit.record({"proposal_id": "first", "pcr_generated": True, "selfmodel_updated": True})
            assert audit.audit_path.exists()
        finally:
            audit.close()


# ============================================================
# T2: 写入成功记录
# ============================================================

class TestWriteSuccess:
    """写入成功记录 → 统计 / 查询 / 文件内容应正确。"""

    def test_record_writes_one_line(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            ok = audit.record({
                "proposal_id": "audit_a_001",
                "pcr_generated": True,
                "selfmodel_updated": True,
                "error": None,
                "files_written": {"beliefs": "/tmp/x.jsonl", "history": "/tmp/y.jsonl", "reflection": None},
            })
            assert ok is True
            with open(audit.audit_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            assert len(lines) == 1
            obj = json.loads(lines[0])
            assert obj["proposal_id"] == "audit_a_001"
            assert obj["success"] is True
            assert obj["pcr_generated"] is True
            assert obj["selfmodel_updated"] is True
            assert obj["error"] is None
            assert obj["files_written"]["beliefs"] == "/tmp/x.jsonl"
        finally:
            audit.close()

    def test_stats_after_one_success(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({
                "proposal_id": "ok_1",
                "pcr_generated": True,
                "selfmodel_updated": True,
            })
            stats = audit.get_stats()
            assert stats["total_processed"] == 1
            assert stats["success_count"] == 1
            assert stats["failed_count"] == 0
            assert stats["unique_proposal_ids"] == 1
            assert stats["last_processed_time"] is not None
            assert stats["first_processed_time"] is not None
        finally:
            audit.close()

    def test_record_failure_marks_failed(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({
                "proposal_id": "fail_1",
                "pcr_generated": False,
                "selfmodel_updated": False,
                "error": "translate_failed: empty proposal",
            })
            stats = audit.get_stats()
            assert stats["success_count"] == 0
            assert stats["failed_count"] == 1
            assert stats["total_processed"] == 1
        finally:
            audit.close()

    def test_multiple_records_incremental_stats(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            for i in range(5):
                audit.record({
                    "proposal_id": f"ok_{i}",
                    "pcr_generated": True,
                    "selfmodel_updated": True,
                })
            for i in range(3):
                audit.record({
                    "proposal_id": f"fail_{i}",
                    "pcr_generated": False,
                    "selfmodel_updated": False,
                    "error": "x",
                })
            stats = audit.get_stats()
            assert stats["total_processed"] == 8
            assert stats["success_count"] == 5
            assert stats["failed_count"] == 3
            assert stats["unique_proposal_ids"] == 8
        finally:
            audit.close()

    def test_record_dedup_skipped(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({
                "proposal_id": "dup_1",
                "pcr_generated": True,
                "selfmodel_updated": False,  # 因 dedup 未真正 applied
                "dedup_skipped": True,
            })
            stats = audit.get_stats()
            assert stats["dedup_skipped_count"] == 1
            assert stats["success_count"] == 0  # dedup 不算 success
            assert stats["failed_count"] == 1
        finally:
            audit.close()


# ============================================================
# T3: 查询已处理 proposal
# ============================================================

class TestQueryProcessed:
    """has_processed / has_succeeded / list_records 应正确返回。"""

    def test_has_processed_after_record(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({
                "proposal_id": "q_001",
                "pcr_generated": True,
                "selfmodel_updated": True,
            })
            assert audit.has_processed("q_001") is True
            assert audit.has_processed("not_exists") is False
        finally:
            audit.close()

    def test_has_succeeded_only_for_success(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({
                "proposal_id": "success_one",
                "pcr_generated": True,
                "selfmodel_updated": True,
            })
            audit.record({
                "proposal_id": "failed_one",
                "pcr_generated": False,
                "selfmodel_updated": False,
                "error": "x",
            })
            assert audit.has_succeeded("success_one") is True
            assert audit.has_succeeded("failed_one") is False
            # has_processed 不区分成功失败
            assert audit.has_processed("failed_one") is True
        finally:
            audit.close()

    def test_has_processed_persists_across_instances(self, temp_audit_dir):
        """第二次创建 audit 实例时，已写入的记录应仍能被查到。"""
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        a1 = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        a1.record({
            "proposal_id": "persist_001",
            "pcr_generated": True,
            "selfmodel_updated": True,
        })
        a1.close()

        a2 = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            assert a2.has_processed("persist_001") is True
            assert a2.has_succeeded("persist_001") is True
            assert a2.get_stats()["total_processed"] == 1
        finally:
            a2.close()

    def test_has_processed_empty_string(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            assert audit.has_processed("") is False
            assert audit.has_processed(None) is False  # type: ignore
        finally:
            audit.close()

    def test_list_records_filtered(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            for i in range(3):
                audit.record({"proposal_id": f"s_{i}", "pcr_generated": True, "selfmodel_updated": True})
            for i in range(2):
                audit.record({"proposal_id": f"f_{i}", "pcr_generated": False, "selfmodel_updated": False, "error": "x"})
            all_recs = audit.list_records(success_only=False)
            ok_recs = audit.list_records(success_only=True)
            assert len(all_recs) == 5
            assert len(ok_recs) == 3
        finally:
            audit.close()


# ============================================================
# T4: 重复 proposal 检测
# ============================================================

class TestDuplicateDetection:
    """同一 proposal_id 多次写入 → 审计文件中应有 2 条（append-only），has_processed 仍 True。"""

    def test_same_id_recorded_twice_in_audit(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({"proposal_id": "dup", "pcr_generated": True, "selfmodel_updated": True})
            audit.record({"proposal_id": "dup", "pcr_generated": True, "selfmodel_updated": True})
            with open(audit.audit_path, "r", encoding="utf-8") as f:
                lines = [l for l in f.read().splitlines() if l.strip()]
            assert len(lines) == 2  # append-only：两条都保留
            # 但 unique_proposal_ids 应为 1
            stats = audit.get_stats()
            assert stats["unique_proposal_ids"] == 1
            assert stats["total_processed"] == 2
        finally:
            audit.close()

    def test_audited_wrapper_records_every_call(self, temp_audit_dir, temp_consumer_dir, valid_proposal_a):
        """wrapper 模式下，每次 process 都会写一条 audit 记录。"""
        from src.admin.selfmodel_consumer_audit import (
            SelfModelConsumerAudit, AuditedSelfModelConsumer
        )
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_consumer_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audited = AuditedSelfModelConsumer(consumer, audit)
            audited.process(valid_proposal_a)
            audited.process(valid_proposal_a)  # dedup 触发但不阻断
            stats = audit.get_stats()
            # 两条都写入 audit
            assert stats["total_processed"] == 2
            # 第二次是 dedup_skipped
            assert stats["dedup_skipped_count"] == 1
        finally:
            audited.close()


# ============================================================
# T5: 损坏 JSONL 容错
# ============================================================

class TestCorruptedJsonl:
    """audit 文件中混入坏行，统计 / 查询应跳过坏行。"""

    def test_corrupted_lines_skipped(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        # 预写损坏文件
        audit_path = temp_audit_dir / "consumer_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            f.write('{"proposal_id": "ok_1", "success": true}\n')
            f.write("{not valid json\n")
            f.write("\n")
            f.write('{"proposal_id": "ok_2", "success": false}\n')
            f.write("[1, 2, 3]\n")  # 非 dict
            f.write("plain text\n")
            f.write('{"proposal_id": "ok_3", "success": true}\n')

        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            stats = audit.get_stats()
            # 3 条合法 dict
            assert stats["total_processed"] == 3
            assert stats["success_count"] == 2
            assert stats["failed_count"] == 1
            assert stats["unique_proposal_ids"] == 3
            # 查询仍能找到合法记录
            assert audit.has_processed("ok_1") is True
            assert audit.has_processed("ok_2") is True
            assert audit.has_processed("ok_3") is True
        finally:
            audit.close()

    def test_all_corrupted_returns_zero(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit_path = temp_audit_dir / "consumer_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            f.write("garbage 1\n")
            f.write("garbage 2\n")
            f.write("\n")

        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            stats = audit.get_stats()
            assert stats["total_processed"] == 0
            assert audit.has_processed("anything") is False
        finally:
            audit.close()

    def test_empty_audit_file(self, temp_audit_dir):
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit_path = temp_audit_dir / "consumer_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("", encoding="utf-8")

        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            stats = audit.get_stats()
            assert stats["total_processed"] == 0
            assert stats["audit_file_size_bytes"] == 0
        finally:
            audit.close()

    def test_audit_record_continues_after_corruption(self, temp_audit_dir):
        """预存损坏文件后，写新记录应正常 append。"""
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit_path = temp_audit_dir / "consumer_audit.jsonl"
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_path, "w", encoding="utf-8") as f:
            f.write("garbage line\n")

        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audit.record({"proposal_id": "after_corrupt", "pcr_generated": True, "selfmodel_updated": True})
            stats = audit.get_stats()
            # 损坏行跳过，新记录成功
            assert stats["total_processed"] == 1
            assert stats["success_count"] == 1
        finally:
            audit.close()


# ============================================================
# T6: 无 Runtime/Growth/Personality 依赖
# ============================================================

class TestNoForbiddenDependency:
    """audit 模块不应 import 受保护模块。"""

    def test_source_does_not_import_runtime(self):
        from src.admin import selfmodel_consumer_audit as mod
        text = Path(mod.__file__).read_text(encoding="utf-8")
        imports: List[str] = []
        for m in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+", text, re.MULTILINE):
            imports.append(m.group(1))
        for m in re.finditer(r"^\s*import\s+([\w.]+)", text, re.MULTILINE):
            imports.append(m.group(1))
        for mod_name in imports:
            for forbidden in (
                "src.runtime.runtime_core",
                "src.runtime.runtime_bridge",
                "src.orchestrator",
                "src.growth",
                "src.personality",
            ):
                assert not mod_name.startswith(forbidden), (
                    f"selfmodel_consumer_audit.py 不应 import {forbidden}（发现 {mod_name!r}）"
                )

    def test_create_audit_does_not_load_runtime(self, temp_audit_dir):
        import sys
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_RUNTIME or m.startswith("src.growth") or m.startswith("src.personality"):
                del sys.modules[m]
        from src.admin.selfmodel_consumer_audit import SelfModelConsumerAudit
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            loaded = set(sys.modules.keys())
            for forbidden in FORBIDDEN_RUNTIME:
                assert forbidden not in loaded
        finally:
            audit.close()

    def test_wrapper_does_not_load_runtime(self, temp_audit_dir, temp_consumer_dir):
        import sys
        for m in list(sys.modules.keys()):
            if m in FORBIDDEN_RUNTIME or m.startswith("src.growth") or m.startswith("src.personality"):
                del sys.modules[m]
        from src.admin.selfmodel_consumer_audit import (
            SelfModelConsumerAudit, AuditedSelfModelConsumer
        )
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_consumer_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        try:
            audited = AuditedSelfModelConsumer(consumer, audit)
            # 跑一次
            audited.process({
                "id": "dep_test",
                "proposed_changes": [{"path": "p.t.curiosity", "before": 0.5, "after": 0.55}],
                "confidence": 0.8,
                "evidence_ids": ["e1"],
            })
            loaded = set(sys.modules.keys())
            for forbidden in FORBIDDEN_RUNTIME:
                assert forbidden not in loaded, (
                    f"运行 audited.process() 不应加载 {forbidden}"
                )
        finally:
            audited.close()


# ============================================================
# T7: Wrapper 端到端
# ============================================================

class TestAuditedWrapperEndToEnd:
    """wrapper 接入完整流程。"""

    def test_wrapper_full_pipeline(self, temp_audit_dir, temp_consumer_dir, valid_proposal_a):
        from src.admin.selfmodel_consumer_audit import create_audited_consumer
        audited = create_audited_consumer(
            data_dir=str(temp_consumer_dir),
            audit_dir=str(temp_audit_dir),
        )
        try:
            result = audited.process(valid_proposal_a)
            assert result["pcr_generated"] is True
            assert result["selfmodel_updated"] is True
            # audit 应有 1 条
            stats = audited.audit.get_stats()
            assert stats["total_processed"] == 1
            assert stats["success_count"] == 1
            # has_processed
            assert audited.audit.has_processed("audit_a_001") is True
            assert audited.audit.has_succeeded("audit_a_001") is True
        finally:
            audited.close()

    def test_wrapper_batch(self, temp_audit_dir, temp_consumer_dir, valid_proposal_a, valid_proposal_b):
        from src.admin.selfmodel_consumer_audit import create_audited_consumer
        audited = create_audited_consumer(
            data_dir=str(temp_consumer_dir),
            audit_dir=str(temp_audit_dir),
        )
        try:
            results = audited.process_batch([valid_proposal_a, valid_proposal_b])
            assert len(results) == 2
            stats = audited.audit.get_stats()
            assert stats["total_processed"] == 2
            assert stats["unique_proposal_ids"] == 2
        finally:
            audited.close()

    def test_wrapper_audit_failure_isolated(self, temp_audit_dir, temp_consumer_dir, valid_proposal_a, monkeypatch):
        """audit 写盘失败时主流程应不崩。"""
        from src.admin.selfmodel_consumer_audit import (
            SelfModelConsumerAudit, AuditedSelfModelConsumer
        )
        from src.admin.selfmodel_consumer import SelfModelConsumer
        consumer = SelfModelConsumer(data_dir=str(temp_consumer_dir))
        audit = SelfModelConsumerAudit(audit_dir=str(temp_audit_dir))
        # 强制 audit.record 抛异常
        def boom(_self, *args, **kwargs):
            raise RuntimeError("disk full")
        monkeypatch.setattr(SelfModelConsumerAudit, "record", boom)
        audited = AuditedSelfModelConsumer(consumer, audit)
        try:
            # 不应抛异常
            result = audited.process(valid_proposal_a)
            assert result["pcr_generated"] is True
        finally:
            audited.close()
