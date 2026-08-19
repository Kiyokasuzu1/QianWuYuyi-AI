# -*- coding: utf-8 -*-
"""
src/growth/selfmodel_apply_validator.py

Phase C.4.6.4.3 — SelfModel Apply Validation 校验器

职责:
- 验证 SelfModel 写入记录(records)是否符合可追溯要求
- 不实际写入 SelfModel 数据(只校验 record 内容)
- 强制每条 record 必须包含可追溯字段

每条 SelfModel 写入必须包含:
- source_event_id: 触发该写入的 source event id
- timestamp: ISO 8601 时间戳
- proposal_id: 对应的 proposal id
- evidence_ids / evidence: 证据引用(可追溯)

禁止:
- 写入没有 evidence 的 record
- 写入没有 source_event_id 的 record
- 写入没有 proposal_id 的 record
- 写入没有 timestamp 的 record

约束:
- 不修改任何核心模块
- 不实际写入 data/self_model/*.jsonl
- 仅做 record 字段校验(纯函数)
- 可被 ProposalManager.apply_proposal 调用做 record validation

用法:
    from src.growth.selfmodel_apply_validator import SelfModelApplyValidator

    validator = SelfModelApplyValidator()
    result = validator.validate_record(record)
    if not result["valid"]:
        # 拒绝写入
        ...
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


# 必须存在的字段
REQUIRED_RECORD_FIELDS = (
    "source_event_id",
    "timestamp",
    "proposal_id",
)

# 至少存在一个的 evidence 字段名(兼容 v1/v2 schema)
EVIDENCE_FIELD_NAMES = ("evidence_ids", "evidence")


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class SelfModelApplyValidator:
    """SelfModel 写入记录的字段校验器。

    验证单条 record 是否具备可追溯性。
    """

    def __init__(self) -> None:
        # 不持有任何状态(纯函数)
        pass

    def validate_record(
        self,
        record: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """校验单条 SelfModel 写入记录。

        Args:
            record: 拟写入的 record dict

        Returns:
            dict:
                - valid: bool
                - errors: List[str](所有错误)
                - warnings: List[str](所有警告)
                - record_kind: 推断的 record 类型(belief / history / reflection / relationship)
                - trace: dict(提取的可追溯字段)
        """
        errors: List[str] = []
        warnings: List[str] = []

        if not isinstance(record, dict):
            return {
                "valid": False,
                "errors": ["record must be a dict"],
                "warnings": [],
                "record_kind": "unknown",
                "trace": {},
            }

        # 1. 必备字段校验
        for field_name in REQUIRED_RECORD_FIELDS:
            if field_name not in record or record[field_name] in (None, "", []):
                errors.append(f"missing required field: {field_name}")

        # 2. evidence 字段校验(至少存在一个)
        has_evidence = False
        evidence_value: Any = None
        for ev_name in EVIDENCE_FIELD_NAMES:
            if ev_name in record:
                ev = record[ev_name]
                if ev:
                    if isinstance(ev, list) and len(ev) == 0:
                        continue
                    if isinstance(ev, str) and not ev.strip():
                        continue
                    has_evidence = True
                    evidence_value = ev
                    break
        if not has_evidence:
            errors.append(
                f"missing evidence field (one of {EVIDENCE_FIELD_NAMES} required, "
                "no evidence-less writes allowed)"
            )

        # 3. timestamp 格式校验(基本)
        ts = record.get("timestamp")
        if ts and isinstance(ts, str):
            if "T" not in ts:
                warnings.append(f"timestamp '{ts}' may not be ISO 8601 format")

        # 4. 推断 record 类型
        record_kind = self._infer_record_kind(record)

        # 5. 提取可追溯信息
        trace = {
            "source_event_id": record.get("source_event_id"),
            "timestamp": record.get("timestamp"),
            "proposal_id": record.get("proposal_id"),
            "evidence_value": evidence_value,
            "record_kind": record_kind,
        }

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "record_kind": record_kind,
            "trace": trace,
        }

    def validate_batch(
        self,
        records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """批量校验。

        Returns:
            dict:
                - all_valid: bool
                - total: int
                - valid_count: int
                - invalid_count: int
                - invalid_records: List[Dict](每条含 index + 错误信息)
        """
        invalid_records: List[Dict[str, Any]] = []
        valid_count = 0

        for idx, record in enumerate(records):
            result = self.validate_record(record)
            if result["valid"]:
                valid_count += 1
            else:
                invalid_records.append({
                    "index": idx,
                    "record": record,
                    "errors": result["errors"],
                })

        return {
            "all_valid": len(invalid_records) == 0,
            "total": len(records),
            "valid_count": valid_count,
            "invalid_count": len(invalid_records),
            "invalid_records": invalid_records,
        }

    def _infer_record_kind(self, record: Dict[str, Any]) -> str:
        """推断 record 类型。"""
        # 显式字段
        kind = record.get("kind") or record.get("record_type") or record.get("type")
        if kind:
            return str(kind)

        # 启发式:根据字段
        if "belief" in record or "beliefs" in record:
            return "belief"
        if "history" in record or "event" in record:
            return "history"
        if "reflection" in record or "reflect" in record:
            return "reflection"
        if "relationship" in record or "trust" in record:
            return "relationship"

        return "unknown"


# ============================================================
# 便捷函数(供 tests 与其他模块调用)
# ============================================================

def validate_selfmodel_record(record: Dict[str, Any]) -> bool:
    """便捷函数:返回 record 是否有效。"""
    return SelfModelApplyValidator().validate_record(record)["valid"]


def build_selfmodel_record_from_proposal(
    proposal: Dict[str, Any],
    record_kind: str,
    record_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """从 approved proposal 构建可追溯的 SelfModel 写入 record。

    强制包含:
    - source_event_id (from proposal)
    - timestamp (now)
    - proposal_id (from proposal)
    - evidence_ids (from proposal)
    - record_kind

    Args:
        proposal: 已 approve 的 proposal dict
        record_kind: belief / history / reflection / relationship
        record_payload: 具体 payload(如 belief 文本、history 事件等)

    Returns:
        包含所有可追溯字段的 record dict
    """
    return {
        "kind": record_kind,
        "source_event_id": proposal.get("source_event_id")
            or proposal.get("id", ""),
        "timestamp": _now_iso(),
        "proposal_id": proposal.get("id") or proposal.get("proposal_id", ""),
        "evidence_ids": proposal.get("evidence_ids")
            or proposal.get("evidence", []),
        "user_id": proposal.get("user_id", ""),
        "reviewer_id": proposal.get("reviewer_id", ""),
        "decision": proposal.get("decision", "approved"),
        "payload": record_payload,
    }
