"""
Phase 6.3: Runtime SelfModel Audit 测试

验证：
- record_self_model_read / write 留痕
- record_pcr_applied / external_change / rollback 留痕
- query_self_model_audit 检索正确
- trace_self_model_evolution 可追溯
- 异常隔离（不阻塞主流程）
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.audit.self_model_audit import (
    record_self_model_read,
    record_self_model_write,
    record_pcr_applied,
    record_external_change,
    record_rollback,
    record_bootstrap,
    query_self_model_audit,
    get_self_model_audit_summary,
    trace_self_model_evolution,
    SELF_MODEL_READ,
    SELF_MODEL_WRITE,
    SELF_MODEL_PCR_APPLIED,
    SELF_MODEL_EXTERNAL_CHANGE,
    SELF_MODEL_ROLLBACK,
    SELF_MODEL_BOOTSTRAP,
    SOURCE_ADAPTER,
    SOURCE_RUNTIME,
    SOURCE_ORCHESTRATOR,
)


@pytest.fixture
def tmp_audit_dir(monkeypatch):
    """使用临时目录作为 audit 数据目录"""
    d = tempfile.mkdtemp(prefix="sm_audit_")
    monkeypatch.setenv("YUYI_AUDIT_DIR", d)
    # 重新初始化 audit storage
    from src.audit import storage as storage_module
    # 重置全局单例
    if hasattr(storage_module, "_audit_storage_instance"):
        storage_module._audit_storage_instance = None
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================
# 1. 读事件测试
# ============================================================

class TestReadAudit:
    def test_01_record_read(self, tmp_audit_dir):
        """记录 SelfModel 读事件"""
        rec = record_self_model_read(
            source=SOURCE_ORCHESTRATOR,
            context_type="combined",
            detail={"beliefs_count": 3},
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_READ
        assert rec.source == SOURCE_ORCHESTRATOR
        assert rec.action == "read_combined"

    def test_02_record_read_with_error(self, tmp_audit_dir):
        """记录读事件（带错误）"""
        rec = record_self_model_read(
            source=SOURCE_ORCHESTRATOR,
            context_type="legacy",
            result="failure",
            error_message="store unavailable",
        )
        assert rec is not None
        assert rec.result == "failure"
        assert rec.error_message == "store unavailable"


# ============================================================
# 2. 写事件测试
# ============================================================

class TestWriteAudit:
    def test_01_record_write(self, tmp_audit_dir):
        """记录 SelfModel 写事件"""
        rec = record_self_model_write(
            source=SOURCE_ADAPTER,
            proposal_id="prop_001",
            reason="emotion_pattern",
            confidence=0.7,
            detail={"beliefs_added": 2},
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_WRITE
        assert rec.source == SOURCE_ADAPTER
        assert rec.detail["proposal_id"] == "prop_001"
        assert rec.detail["beliefs_added"] == 2

    def test_02_record_pcr_applied(self, tmp_audit_dir):
        """记录 PCR 应用审计"""
        rec = record_pcr_applied(
            proposal_id="prop_002",
            pcr_id="pcr_002",
            reason="personality_change",
            confidence=0.8,
            beliefs_added=1,
            beliefs_reinforced=0,
            affected_traits={"openness": 0.05},
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_PCR_APPLIED
        assert rec.detail["beliefs_added"] == 1
        assert rec.detail["affected_traits"]["openness"] == 0.05

    def test_03_record_external_change(self, tmp_audit_dir):
        """记录外部修改事件"""
        rec = record_external_change(
            change_type="emotion",
            source="emotion_growth_service",
            reason="emotion_pattern_analysis",
            confidence=0.5,
            proposal_id="prop_003",
            beliefs_added=1,
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_EXTERNAL_CHANGE
        assert rec.detail["beliefs_added"] == 1

    def test_04_record_rollback(self, tmp_audit_dir):
        """记录 rollback 审计"""
        rec = record_rollback(
            snapshot_id="snap_001",
            reason="contradiction detected",
            actor="guardian",
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_ROLLBACK
        assert rec.detail["snapshot_id"] == "snap_001"

    def test_05_record_bootstrap(self, tmp_audit_dir):
        """记录 bootstrap 审计"""
        rec = record_bootstrap(
            data_dir="data/self_model",
            load_counts={"beliefs": 3, "history": 5, "reflections": 1},
        )
        assert rec is not None
        assert rec.operation_type == SELF_MODEL_BOOTSTRAP
        assert rec.detail["load_counts"]["beliefs"] == 3


# ============================================================
# 3. 查询接口测试
# ============================================================

class TestQueryAudit:
    def test_01_query_by_operation_type(self, tmp_audit_dir):
        """按 operation_type 查询"""
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="combined")
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="legacy")
        record_self_model_write(source=SOURCE_ADAPTER, proposal_id="p1", reason="r", confidence=0.5)
        results = query_self_model_audit(operation_type=SELF_MODEL_READ)
        # 至少有 2 条 read 记录
        assert len(results) >= 2
        for rec in results:
            assert rec.operation_type == SELF_MODEL_READ

    def test_02_query_by_source(self, tmp_audit_dir):
        """按 source 查询"""
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="combined")
        record_self_model_read(source=SOURCE_ADAPTER, context_type="runtime")
        results = query_self_model_audit(source=SOURCE_ADAPTER)
        # 所有返回记录的 source 应为 adapter
        for rec in results:
            assert rec.source == SOURCE_ADAPTER

    def test_03_query_by_proposal_id(self, tmp_audit_dir):
        """按 proposal_id 查询"""
        record_self_model_write(
            source=SOURCE_ADAPTER, proposal_id="prop_unique_1",
            reason="r", confidence=0.5,
        )
        record_self_model_write(
            source=SOURCE_ADAPTER, proposal_id="prop_unique_2",
            reason="r", confidence=0.5,
        )
        results = query_self_model_audit(proposal_id="prop_unique_1")
        # 应只返回 1 条
        assert len(results) >= 1
        for rec in results:
            assert rec.detail.get("proposal_id") == "prop_unique_1"

    def test_04_query_limit(self, tmp_audit_dir):
        """limit 生效"""
        for i in range(5):
            record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="combined")
        results = query_self_model_audit(operation_type=SELF_MODEL_READ, limit=2)
        assert len(results) <= 2


# ============================================================
# 4. 摘要测试
# ============================================================

class TestAuditSummary:
    def test_01_summary(self, tmp_audit_dir):
        """获取审计摘要"""
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="combined")
        record_self_model_write(source=SOURCE_ADAPTER, proposal_id="p1", reason="r", confidence=0.5)
        summary = get_self_model_audit_summary()
        assert "total_records" in summary
        assert "by_operation" in summary
        assert "by_source" in summary
        assert "recent_records" in summary
        # 至少包含上面两条
        assert summary["total_records"] >= 2

    def test_02_summary_by_operation(self, tmp_audit_dir):
        """按 operation 分组"""
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="x")
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="y")
        record_self_model_write(source=SOURCE_ADAPTER, proposal_id="p1", reason="r", confidence=0.5)
        summary = get_self_model_audit_summary()
        assert summary["by_operation"].get(SELF_MODEL_READ, 0) >= 2
        assert summary["by_operation"].get(SELF_MODEL_WRITE, 0) >= 1


# ============================================================
# 5. 追溯测试（trace_self_model_evolution）
# ============================================================

class TestTraceEvolution:
    def test_01_trace(self, tmp_audit_dir):
        """追溯 SelfModel 演化"""
        record_pcr_applied(
            proposal_id="trace_p1", pcr_id="p1", reason="r", confidence=0.6,
            beliefs_added=2,
        )
        record_external_change(
            change_type="emotion", source="emotion", reason="r",
            confidence=0.5, proposal_id="trace_p1", beliefs_added=1,
        )
        results = trace_self_model_evolution(proposal_id="trace_p1")
        assert len(results) >= 2
        # 时间倒序
        for r in results:
            assert r["proposal_id"] == "trace_p1"

    def test_02_trace_all(self, tmp_audit_dir):
        """追溯所有 self_model 事件（不指定 proposal_id）"""
        record_self_model_read(source=SOURCE_ORCHESTRATOR, context_type="combined")
        record_self_model_write(source=SOURCE_ADAPTER, proposal_id="p2", reason="r", confidence=0.5)
        results = trace_self_model_evolution(limit=10)
        # 至少有 2 条
        assert len(results) >= 2


# ============================================================
# 6. 异常隔离测试
# ============================================================

class TestExceptionIsolation:
    def test_01_record_read_with_invalid_storage(self, monkeypatch):
        """storage 抛异常时 record 仍返回 None 不抛"""
        from src.audit import self_model_audit as sma
        # Monkey-patch record_audit_log to raise
        def _bad_log(*args, **kwargs):
            raise RuntimeError("storage broken")
        monkeypatch.setattr(sma, "record_audit_log", _bad_log)
        # Should not raise
        rec = sma.record_self_model_read(source="x", context_type="y")
        assert rec is None

    def test_02_query_with_empty_records(self, monkeypatch):
        """空 records 列表查询返回空"""
        from src.audit import storage as storage_module
        class _EmptyStorage:
            def load(self, limit=100, offset=0):
                return []
        monkeypatch.setattr(storage_module, "get_audit_storage", lambda: _EmptyStorage())
        results = query_self_model_audit()
        assert isinstance(results, list)
        assert len(results) == 0

    def test_03_summary_with_error(self, monkeypatch):
        """错误时 summary 仍返回结构（不抛异常）"""
        from src.audit import storage as storage_module
        def _broken():
            raise RuntimeError("boom")
        monkeypatch.setattr(storage_module, "get_audit_storage", _broken)
        # Should not raise
        summary = get_self_model_audit_summary()
        # 返回结构（即使有 error）
        assert isinstance(summary, dict)
        assert "total_records" in summary
        assert summary["total_records"] == 0
