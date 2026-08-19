# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_apply_validation.py

Phase C.4.6.4.3 — SelfModel Apply Validation 测试

目标:
验证 SelfModel 写入记录符合可追溯要求:
- 有 source_event_id
- 有 timestamp
- 有 proposal_id
- 有 evidence(可追溯)
- 禁止: 没有 evidence 的写入

覆盖:
- approved proposal 可生成可追溯 record
- record 必填字段缺失应被拒绝
- record 必填字段完整应通过
- 没有 evidence 的 record 必须被拒绝
- build_selfmodel_record_from_proposal 自动添加可追溯字段
- 批量校验
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YUYI_LLM_MOCK", "1")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def validator():
    from src.growth.selfmodel_apply_validator import SelfModelApplyValidator
    return SelfModelApplyValidator()


@pytest.fixture
def approved_proposal():
    """一个已 approved 的 proposal(可直接用于构建 record)。"""
    return {
        "id": "prop_apply_001",
        "proposal_id": "prop_apply_001",
        "source_event_id": "mem_evt_apply_001",
        "evidence_ids": ["mem_evt_apply_001", "mem_evt_apply_002"],
        "status": "approved",
        "decision": "approved",
        "reviewer_id": "alice",
        "review_comment": "证据充分",
        "reviewed_at": "2026-08-02T11:00:00Z",
        "user_id": "user_yuyi",
    }


@pytest.fixture
def valid_belief_record(approved_proposal):
    """一个合法的 belief record(由 approved proposal 派生)。"""
    return {
        "kind": "belief",
        "source_event_id": approved_proposal["source_event_id"],
        "timestamp": "2026-08-02T11:00:00Z",
        "proposal_id": approved_proposal["id"],
        "evidence_ids": approved_proposal["evidence_ids"],
        "reviewer_id": "alice",
        "payload": {
            "belief": "用户喜欢文学类作品",
            "confidence": 0.85,
        },
    }


# ============================================================
# 1. 必填字段校验
# ============================================================

class TestRequiredFields:
    """验证 record 必填字段。"""

    def test_valid_record_passes(self, validator, valid_belief_record):
        """合法 record 应通过校验。"""
        result = validator.validate_record(valid_belief_record)
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["record_kind"] == "belief"
        # trace 应包含所有可追溯字段
        assert result["trace"]["source_event_id"] == "mem_evt_apply_001"
        assert result["trace"]["proposal_id"] == "prop_apply_001"
        assert result["trace"]["timestamp"] == "2026-08-02T11:00:00Z"

    def test_missing_source_event_id_rejected(self, validator, valid_belief_record):
        """缺少 source_event_id 必须被拒绝。"""
        record = valid_belief_record.copy()
        del record["source_event_id"]
        result = validator.validate_record(record)
        assert result["valid"] is False
        assert any("source_event_id" in e for e in result["errors"])

    def test_missing_timestamp_rejected(self, validator, valid_belief_record):
        """缺少 timestamp 必须被拒绝。"""
        record = valid_belief_record.copy()
        del record["timestamp"]
        result = validator.validate_record(record)
        assert result["valid"] is False
        assert any("timestamp" in e for e in result["errors"])

    def test_missing_proposal_id_rejected(self, validator, valid_belief_record):
        """缺少 proposal_id 必须被拒绝。"""
        record = valid_belief_record.copy()
        del record["proposal_id"]
        result = validator.validate_record(record)
        assert result["valid"] is False
        assert any("proposal_id" in e for e in result["errors"])

    def test_empty_source_event_id_rejected(self, validator, valid_belief_record):
        """空 source_event_id 必须被拒绝。"""
        record = valid_belief_record.copy()
        record["source_event_id"] = ""
        result = validator.validate_record(record)
        assert result["valid"] is False

    def test_null_proposal_id_rejected(self, validator, valid_belief_record):
        """null proposal_id 必须被拒绝。"""
        record = valid_belief_record.copy()
        record["proposal_id"] = None
        result = validator.validate_record(record)
        assert result["valid"] is False

    def test_non_dict_record_rejected(self, validator):
        """非 dict record 必须被拒绝。"""
        for bad in [None, "string", 123, [], 1.5]:
            result = validator.validate_record(bad)
            assert result["valid"] is False


# ============================================================
# 2. Evidence 必填校验(关键约束)
# ============================================================

class TestEvidenceRequired:
    """验证:没有 evidence 的写入必须被拒绝。"""

    def test_no_evidence_rejected(self, validator, valid_belief_record):
        """没有任何 evidence 字段的 record 必须被拒绝。"""
        record = valid_belief_record.copy()
        del record["evidence_ids"]
        result = validator.validate_record(record)
        assert result["valid"] is False
        assert any("evidence" in e.lower() for e in result["errors"])

    def test_empty_evidence_list_rejected(self, validator, valid_belief_record):
        """空 evidence 列表必须被拒绝。"""
        record = valid_belief_record.copy()
        record["evidence_ids"] = []
        result = validator.validate_record(record)
        assert result["valid"] is False

    def test_empty_evidence_string_rejected(self, validator, valid_belief_record):
        """空 evidence 字符串必须被拒绝。"""
        record = valid_belief_record.copy()
        record["evidence_ids"] = "  "
        # evidence 应该是 list 才合法
        result = validator.validate_record(record)
        assert result["valid"] is False

    def test_evidence_field_alias_supported(self, validator, valid_belief_record):
        """evidence 字段名别名(evidence / evidence_ids)都应被识别。"""
        record = valid_belief_record.copy()
        del record["evidence_ids"]
        record["evidence"] = ["mem_001", "mem_002"]
        result = validator.validate_record(record)
        assert result["valid"] is True

    def test_both_evidence_fields_present(self, validator, valid_belief_record):
        """两个 evidence 字段都存在应正常通过。"""
        record = valid_belief_record.copy()
        record["evidence"] = ["mem_001"]
        result = validator.validate_record(record)
        assert result["valid"] is True


# ============================================================
# 3. Record 类型推断
# ============================================================

class TestRecordKindInference:
    """验证 record 类型推断逻辑。"""

    def test_explicit_kind_field(self, validator, valid_belief_record):
        """显式 kind 字段应被优先使用。"""
        record = valid_belief_record.copy()
        record["kind"] = "history"
        result = validator.validate_record(record)
        assert result["record_kind"] == "history"

    def test_infer_belief_from_field(self, validator, valid_belief_record):
        """含 'belief' 字段应推断为 belief。"""
        record = valid_belief_record.copy()
        del record["kind"]
        record["belief"] = "用户偏好文学"
        result = validator.validate_record(record)
        assert result["record_kind"] == "belief"

    def test_infer_history_from_field(self, validator, valid_belief_record):
        """含 'history' 字段应推断为 history。"""
        record = valid_belief_record.copy()
        del record["kind"]
        record["history"] = ["event1"]
        result = validator.validate_record(record)
        assert result["record_kind"] == "history"

    def test_infer_relationship_from_field(self, validator, valid_belief_record):
        """含 'relationship' 字段应推断为 relationship。"""
        record = valid_belief_record.copy()
        del record["kind"]
        record["relationship"] = {"trust": 0.6}
        result = validator.validate_record(record)
        assert result["record_kind"] == "relationship"


# ============================================================
# 4. Timestamp 格式
# ============================================================

class TestTimestampFormat:
    """验证 timestamp 格式。"""

    def test_iso_8601_with_t_separator(self, validator, valid_belief_record):
        """ISO 8601 with 'T' separator 应被识别。"""
        record = valid_belief_record.copy()
        record["timestamp"] = "2026-08-02T11:00:00Z"
        result = validator.validate_record(record)
        assert result["valid"] is True
        # 不会产生 warning
        ts_warnings = [w for w in result["warnings"] if "timestamp" in w.lower()]
        assert len(ts_warnings) == 0

    def test_non_iso_8601_warning(self, validator, valid_belief_record):
        """非 ISO 8601 timestamp 应产生 warning(但不拒绝)。"""
        record = valid_belief_record.copy()
        record["timestamp"] = "2026-08-02 11:00:00"
        result = validator.validate_record(record)
        # 不拒绝(只 warning)
        assert result["valid"] is True
        ts_warnings = [w for w in result["warnings"] if "timestamp" in w.lower()]
        assert len(ts_warnings) >= 1


# ============================================================
# 5. build_selfmodel_record_from_proposal
# ============================================================

class TestBuildRecordFromProposal:
    """验证从 approved proposal 构建 record 的便捷函数。"""

    def test_build_belief_record(self, approved_proposal):
        """从 approved proposal 构建 belief record。"""
        from src.growth.selfmodel_apply_validator import (
            build_selfmodel_record_from_proposal,
        )

        record = build_selfmodel_record_from_proposal(
            proposal=approved_proposal,
            record_kind="belief",
            record_payload={"belief": "用户偏好文学", "confidence": 0.85},
        )

        # 必填字段必须存在
        assert record["source_event_id"] == "mem_evt_apply_001"
        assert record["proposal_id"] == "prop_apply_001"
        assert "timestamp" in record
        assert "T" in record["timestamp"]
        # evidence 必须保留
        assert "mem_evt_apply_001" in record["evidence_ids"]
        assert "mem_evt_apply_002" in record["evidence_ids"]
        # 审计字段
        assert record["reviewer_id"] == "alice"
        assert record["decision"] == "approved"
        # payload 保留
        assert record["payload"]["belief"] == "用户偏好文学"
        # kind
        assert record["kind"] == "belief"

    def test_build_record_passes_validation(self, approved_proposal):
        """通过 build 生成的 record 必须通过 validate。"""
        from src.growth.selfmodel_apply_validator import (
            build_selfmodel_record_from_proposal,
            SelfModelApplyValidator,
        )

        for kind in ["belief", "history", "reflection", "relationship"]:
            record = build_selfmodel_record_from_proposal(
                proposal=approved_proposal,
                record_kind=kind,
                record_payload={},
            )
            validator = SelfModelApplyValidator()
            result = validator.validate_record(record)
            assert result["valid"] is True, f"{kind} record failed: {result['errors']}"

    def test_built_record_traceable(self, approved_proposal):
        """build 生成的 record 可通过 source_event_id/proposal_id 追溯。"""
        from src.growth.selfmodel_apply_validator import (
            build_selfmodel_record_from_proposal,
        )

        record = build_selfmodel_record_from_proposal(
            proposal=approved_proposal,
            record_kind="history",
            record_payload={"event": "user_read_book"},
        )

        # 1. 可追溯到 source event
        assert record["source_event_id"] == approved_proposal["source_event_id"]
        # 2. 可追溯到 proposal
        assert record["proposal_id"] == approved_proposal["id"]
        # 3. 可追溯到 reviewer(审计)
        assert record["reviewer_id"] == approved_proposal["reviewer_id"]
        # 4. 可追溯到 evidence
        assert set(record["evidence_ids"]) == set(approved_proposal["evidence_ids"])

    def test_build_proposal_without_evidence_ids_fails_validation(self):
        """没有 evidence_ids 的 proposal 构建的 record 仍应被拒绝。"""
        from src.growth.selfmodel_apply_validator import (
            build_selfmodel_record_from_proposal,
            SelfModelApplyValidator,
        )

        bad_proposal = {
            "id": "prop_bad_001",
            "source_event_id": "evt_bad",
            "evidence_ids": [],  # 空 evidence
            "user_id": "u1",
        }
        record = build_selfmodel_record_from_proposal(
            proposal=bad_proposal,
            record_kind="belief",
            record_payload={},
        )
        validator = SelfModelApplyValidator()
        result = validator.validate_record(record)
        # 应被拒绝(evidence 缺失)
        assert result["valid"] is False


# ============================================================
# 6. 批量校验
# ============================================================

class TestBatchValidation:
    """验证批量校验逻辑。"""

    def test_batch_all_valid(self, validator, valid_belief_record):
        """批量全合法应通过。"""
        records = [valid_belief_record.copy() for _ in range(3)]
        result = validator.validate_batch(records)
        assert result["all_valid"] is True
        assert result["total"] == 3
        assert result["valid_count"] == 3
        assert result["invalid_count"] == 0

    def test_batch_with_one_invalid(self, validator, valid_belief_record):
        """批量中含 1 条非法应正确报告。"""
        records = [valid_belief_record.copy() for _ in range(3)]
        # 第 2 条缺少 source_event_id
        del records[1]["source_event_id"]

        result = validator.validate_batch(records)
        assert result["all_valid"] is False
        assert result["total"] == 3
        assert result["valid_count"] == 2
        assert result["invalid_count"] == 1
        assert result["invalid_records"][0]["index"] == 1

    def test_empty_batch(self, validator):
        """空 batch 应视为 all_valid。"""
        result = validator.validate_batch([])
        assert result["all_valid"] is True
        assert result["total"] == 0
        assert result["valid_count"] == 0


# ============================================================
# 7. Convenience function
# ============================================================

class TestConvenienceFunction:
    """验证便捷函数 validate_selfmodel_record。"""

    def test_validate_selfmodel_record_true(self, valid_belief_record):
        """合法 record 应返回 True。"""
        from src.growth.selfmodel_apply_validator import validate_selfmodel_record
        assert validate_selfmodel_record(valid_belief_record) is True

    def test_validate_selfmodel_record_false(self, valid_belief_record):
        """非法 record 应返回 False。"""
        from src.growth.selfmodel_apply_validator import validate_selfmodel_record
        bad = valid_belief_record.copy()
        del bad["source_event_id"]
        assert validate_selfmodel_record(bad) is False
